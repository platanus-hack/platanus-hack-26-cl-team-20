"""Reconstrucción: de mapas cámara->proyector a nube de puntos.

- `coalesce`: para cada píxel del proyector, promedio (ponderado por luminancia) de los
  píxeles de cámara que lo vieron. Baja el ruido y deja un punto por píxel de proyector.
- `triangulate_pair`: rayos de cámara y proyector (ambos sin distorsión) -> punto 3D en mm.
  Modo "sltk": reproduce bit a bit lo que hace slcrunch (par rectificado R1/P1, R2/P2,
  LS lineal de Hartley-Sturm, de vuelta al marco de la cámara, y = -y, z = -z).
  Modo "world": triangulación con las poses del rig, salida en el marco del mundo.
- PLY binario con color, compatible con el de sltk.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .graycode import Decoded
from .rig import Device, Rig, stereo_rectify


@dataclass
class Matches:
    cam: np.ndarray    # (n,2) float32, píxel de cámara
    pro: np.ndarray    # (n,2) float32, píxel de proyector
    color: np.ndarray  # (n,3) float32 BGR


def coalesce(dec: Decoded, weight: str = "none", min_weight: float = 0.5) -> Matches:
    """Un match por píxel de proyector: media de los píxeles de cámara que lo vieron.

    Igual que slcrunch -c. weight="none": media simple (así se generó el sampledata de sltk,
    verificado: 104830 vs 104809 puntos). weight="luma": pondera por (B*2 + G*4 + R) / (7*255)
    como el código actual de slcrunch, que descarta píxeles oscuros. Se exige suma de pesos
    > min_weight. Orden de salida: filas del proyector, luego columnas.
    """
    cam, pro, col = dec.points()
    if len(cam) == 0:
        z = np.zeros((0, 2), np.float32)
        return Matches(z, z.copy(), np.zeros((0, 3), np.float32))
    if weight == "luma":
        w_ = np.clip((col[:, 0] * 2 + col[:, 1] * 4 + col[:, 2]) / (7.0 * 255.0), 0, 1).astype(np.float64)
    elif weight == "none":
        w_ = np.ones(len(cam), np.float64)
    else:
        raise ValueError(weight)
    pw, ph = int(pro[:, 0].max()) + 1, int(pro[:, 1].max()) + 1
    key = pro[:, 1].astype(np.int64) * pw + pro[:, 0]
    n = pw * ph
    sx = np.bincount(key, weights=cam[:, 0] * w_, minlength=n)
    sy = np.bincount(key, weights=cam[:, 1] * w_, minlength=n)
    sw = np.bincount(key, weights=w_, minlength=n)
    cnt = np.bincount(key, minlength=n).astype(np.float64)
    sc = np.stack([np.bincount(key, weights=col[:, i].astype(np.float64), minlength=n) for i in range(3)], axis=1)
    ok = sw > min_weight
    idx = np.nonzero(ok)[0]
    pro_xy = np.stack([idx % pw, idx // pw], axis=1).astype(np.float32)
    cam_xy = np.stack([sx[idx] / sw[idx], sy[idx] / sw[idx]], axis=1).astype(np.float32)
    color = (sc[idx] / cnt[idx, None]).astype(np.float32)
    return Matches(cam_xy, pro_xy, color)


def dense_proj_to_cam(dec: Decoded, width: int = 1920, height: int = 1080, hole_px: int = 25) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Mapa denso proyector -> cámara (mapx, mapy float32 de (height, width), valid bool) interpolando las
    correspondencias del gray code (lo que usa recalib para el POV y los trackers de dualview).
    hole_px: diámetro máximo de agujero que se rellena (0 = no rellenar)."""
    from scipy.interpolate import griddata
    m = coalesce(dec)
    gx, gy = np.meshgrid(np.arange(width), np.arange(height))
    mapx = griddata(m.pro, m.cam[:, 0], (gx, gy), method="linear")
    mapy = griddata(m.pro, m.cam[:, 1], (gx, gy), method="linear")
    valid = np.isfinite(mapx) & np.isfinite(mapy)
    mapx = np.nan_to_num(mapx, nan=-1).astype(np.float32); mapy = np.nan_to_num(mapy, nan=-1).astype(np.float32)
    # agujeros chicos (objetos oscuros o brillantes donde el gray code no decodificó): se rellenan por inpainting
    # desde los bordes del agujero, así el POV y las máscaras no quedan con puntos negros ("gaps en los píxeles")
    import cv2
    v8 = valid.astype(np.uint8)
    closed = cv2.morphologyEx(v8, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (hole_px, hole_px)))
    holes = (closed > 0) & ~valid
    if holes.any():
        mapx = cv2.inpaint(mapx, holes.astype(np.uint8), 3, cv2.INPAINT_TELEA)
        mapy = cv2.inpaint(mapy, holes.astype(np.uint8), 3, cv2.INPAINT_TELEA)
        valid = valid | holes
    return mapx, mapy, valid


def linear_ls_triangulate(u0: np.ndarray, P0: np.ndarray, u1: np.ndarray, P1: np.ndarray) -> np.ndarray:
    """Hartley-Sturm lineal (A X = B, 4x3) vectorizado, como `LinearLSTriangulation` de slcrunch."""
    n = len(u0)
    A = np.empty((n, 4, 3)); B = np.empty((n, 4))
    for row, (u, P) in enumerate(((u0, P0), (u1, P1))):
        for k in range(2):     # k=0 usa u.x con fila 0; k=1 usa u.y con fila 1
            A[:, 2 * row + k, :] = u[:, k:k + 1] * P[2, :3] - P[k, :3]
            B[:, 2 * row + k] = -(u[:, k] * P[2, 3] - P[k, 3])
    # mínimos cuadrados por lotes vía pseudoinversa (SVD por lotes, como DECOMP_SVD)
    return np.einsum("nij,nj->ni", np.linalg.pinv(A), B).reshape(n, 3)


_UNDIST_CRIT = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 40, 1e-10)


def _undistort(pts: np.ndarray, dev: Device, R=None, P=None) -> np.ndarray:
    """undistortPoints con más iteraciones que las 5 por defecto (precisión < 0.001 px)."""
    pts = np.ascontiguousarray(pts, np.float64).reshape(-1, 1, 2)
    if hasattr(cv2, "undistortPointsIter"):          # OpenCV 4
        return cv2.undistortPointsIter(pts, dev.K, dev.dist, R, P, _UNDIST_CRIT).reshape(-1, 2)
    return cv2.undistortPoints(pts, dev.K, dev.dist, R=R, P=P, criteria=_UNDIST_CRIT).reshape(-1, 2)


def triangulate_pair(m: Matches, cam: Device, pro: Device, rect: dict | None = None,
                     mode: str = "sltk") -> np.ndarray:
    """Puntos 3D (n,3) float32 en mm para matches entre una cámara y el proyector."""
    if mode == "sltk":
        if rect is None or not all(k in rect for k in ("R1", "R2", "P1", "P2")):
            rect = stereo_rectify(cam, pro)
        R1, R2, P1, P2 = (np.asarray(rect[k], float) for k in ("R1", "R2", "P1", "P2"))
        cu = _undistort(m.cam, cam, R1, P1)
        pu = _undistort(m.pro, pro, R2, P2)
        X = linear_ls_triangulate(cu, P1, pu, P2)
        X = X @ R1            # vuelve del marco rectificado al de la cámara (R1^T X)
        X[:, 1] *= -1; X[:, 2] *= -1
        return X.astype(np.float32)
    if mode == "world":
        cu = _undistort(m.cam, cam)
        pu = _undistort(m.pro, pro)
        Pc = np.hstack([cam.R, cam.t.reshape(3, 1)])
        Pp = np.hstack([pro.R, pro.t.reshape(3, 1)])
        X = cv2.triangulatePoints(Pc, Pp, cu.T, pu.T)
        return (X[:3] / X[3]).T.astype(np.float32)
    raise ValueError(mode)


# ----------------------------------------------------------------------------- PLY

def write_ply(path: str | Path, xyz: np.ndarray, color_bgr: np.ndarray | None = None,
              normals: np.ndarray | None = None, comment: str = "slscan") -> None:
    xyz = np.asarray(xyz, np.float32)
    n = len(xyz)
    header = ["ply", "format binary_little_endian 1.0", f"comment {comment}", f"element vertex {n}",
              "property float x", "property float y", "property float z"]
    fields = [("x", "<f4"), ("y", "<f4"), ("z", "<f4")]
    if normals is not None:
        header += ["property float nx", "property float ny", "property float nz"]
        fields += [("nx", "<f4"), ("ny", "<f4"), ("nz", "<f4")]
    if color_bgr is not None:
        header += ["property uchar red", "property uchar green", "property uchar blue"]
        fields += [("red", "u1"), ("green", "u1"), ("blue", "u1")]
    header.append("end_header")
    rec = np.empty(n, dtype=fields)
    rec["x"], rec["y"], rec["z"] = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    if normals is not None:
        rec["nx"], rec["ny"], rec["nz"] = normals[:, 0], normals[:, 1], normals[:, 2]
    if color_bgr is not None:
        c = np.clip(np.asarray(color_bgr), 0, 255).astype(np.uint8)
        rec["red"], rec["green"], rec["blue"] = c[:, 2], c[:, 1], c[:, 0]
    with open(path, "wb") as f:
        f.write(("\n".join(header) + "\n").encode("ascii"))
        f.write(rec.tobytes())


def read_ply(path: str | Path):
    """Lee PLY binario little-endian o ASCII con x y z [nx ny nz] [red green blue]. -> (xyz, rgb|None)."""
    with open(path, "rb") as f:
        header = []
        while True:
            line = f.readline().decode("ascii", "replace").strip()
            header.append(line)
            if line == "end_header":
                break
        fmt = next(l for l in header if l.startswith("format")).split()[1]
        n = int(next(l for l in header if l.startswith("element vertex")).split()[2])
        props = [l.split() for l in header if l.startswith("property")]
        tmap = {"float": "f4", "float32": "f4", "double": "f8", "uchar": "u1", "uint8": "u1",
                "int": "i4", "uint": "u4", "short": "i2", "ushort": "u2", "char": "i1"}
        if fmt == "ascii":
            data = np.loadtxt(f, max_rows=n)
            names = [p[2] for p in props]
            rec = {nm: data[:, i] for i, nm in enumerate(names)}
        else:
            endian = "<" if "little" in fmt else ">"
            dt = np.dtype([(p[2], endian + tmap[p[1]]) for p in props])
            rec = np.frombuffer(f.read(n * dt.itemsize), dtype=dt, count=n)
    xyz = np.stack([rec["x"], rec["y"], rec["z"]], axis=1).astype(np.float32)
    rgb = None
    if all(k in (rec.dtype.names if hasattr(rec, "dtype") else rec) for k in ("red", "green", "blue")):
        rgb = np.stack([rec["red"], rec["green"], rec["blue"]], axis=1).astype(np.uint8)
    return xyz, rgb


# ----------------------------------------------------------------------------- comparación

def compare_clouds(a: np.ndarray, b: np.ndarray) -> dict:
    """Distancia de cada punto de a a su vecino más cercano en b (mm). Para validar contra sltk."""
    from scipy.spatial import cKDTree
    d, _ = cKDTree(b).query(a, k=1)
    return {"n_a": int(len(a)), "n_b": int(len(b)), "median_mm": float(np.median(d)),
            "p95_mm": float(np.percentile(d, 95)), "max_mm": float(d.max()), "mean_mm": float(d.mean())}
