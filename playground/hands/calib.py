"""Calibración Brio (webcam de pared, PC) → proyector, y del proyector → mm de la mesa.

Para el touch basta el plano de la mesa: una homografía brio_px → proj_px. Tres maneras de obtenerla, de mejor a
más simple:

1. **Gray code** (`from_graycode`): el Mac proyecta la secuencia gray code (la misma de `slscan scan`) y la Brio la
   ve; las imágenes quedan en una carpeta como las de slscan (`<scan>/brio/[AA-BB-CC_NN].png`) y se decodifican con
   `slscan.graycode.load_sequence/decode`. Con las correspondencias (un match por px de proyector, `recon.coalesce`)
   se ajusta la homografía con RANSAC: el plano dominante es la mesa, los objetos quedan como outliers (lo mismo que
   `playground/segment_pov.py:height_map`). `capture_graycode` hace la toma: ventana de patrones en el Mac
   (`slscan.capture.PatternWindow`) y cuadros sin pérdida de la Brio por HTTP (`GET /frame.png` del servicio).
2. **Puntos proyectados** (`capture_dots`): el proyector muestra una grilla de puntos de a uno y se ubican en la Brio
   por diferencia con un cuadro negro. Sirve sin la secuencia completa (≈ 10 cuadros).
3. **4 puntos a mano** (`from_points_file`): JSON {"brio": [[x, y] ×4+], "proj": [[x, y] ×4+], "mm": opcional}.

Métrica (opcional, para entregar toques en mm): homografía proj_px → mm de la mesa, desde
- la hoja carta de `segments.json` (`papel`: quad en px de proyector + "mm": [215.9, 279.4]), origen en su esquina;
- un `rig.json` métrico (rayos del proyector cortados con el plano z = const del mundo, que es la mesa);
- los "mm" del JSON de puntos.

Todo queda en un JSON (`captures/brio_calib.json` por defecto):
    {"format": "hands-calib/1", "brio_size": [1920, 1080], "proj_size": [1920, 1080], "projector": "proj0",
     "H_brio_to_proj": 3x3, "source": "graycode|dots|points", "stats": {...},
     "H_proj_to_mm": 3x3 | null, "mm_source": "...", "hand_ref": <palma en px de proyector apoyada> | null}
"""

from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

FORMAT = "hands-calib/1"
DEFAULT_PATH = Path("captures/brio_calib.json")


def _apply(H: np.ndarray, pts) -> np.ndarray:
    p = np.asarray(pts, np.float64).reshape(-1, 2)
    if len(p) == 0:
        return p
    return cv2.perspectiveTransform(p.reshape(-1, 1, 2), np.asarray(H, np.float64)).reshape(-1, 2)


@dataclass
class Calibration:
    H_brio_to_proj: np.ndarray
    brio_size: tuple[int, int] = (1920, 1080)
    proj_size: tuple[int, int] = (1920, 1080)
    projector: str = "proj0"
    source: str = "points"
    stats: dict = field(default_factory=dict)
    H_proj_to_mm: np.ndarray | None = None
    mm_source: str | None = None
    hand_ref: float | None = None          # tamaño de palma apoyada, en px de proyector (ver touch.py)

    # ------------------------------------------------------------------ mapeos
    def brio_to_proj(self, pts) -> np.ndarray:
        return _apply(self.H_brio_to_proj, pts)

    def proj_to_brio(self, pts) -> np.ndarray:
        return _apply(np.linalg.inv(self.H_brio_to_proj), pts)

    @property
    def metric(self) -> bool:
        return self.H_proj_to_mm is not None

    def proj_to_mm(self, pts) -> np.ndarray | None:
        return None if self.H_proj_to_mm is None else _apply(self.H_proj_to_mm, pts)

    def brio_to_mm(self, pts) -> np.ndarray | None:
        return None if self.H_proj_to_mm is None else _apply(self.H_proj_to_mm @ self.H_brio_to_proj, pts)

    # ------------------------------------------------------------------ io
    def to_json(self) -> dict:
        return {"format": FORMAT, "brio_size": list(self.brio_size), "proj_size": list(self.proj_size),
                "projector": self.projector, "source": self.source, "stats": self.stats,
                "H_brio_to_proj": np.asarray(self.H_brio_to_proj).tolist(),
                "H_proj_to_mm": None if self.H_proj_to_mm is None else np.asarray(self.H_proj_to_mm).tolist(),
                "mm_source": self.mm_source, "hand_ref": self.hand_ref, "saved": time.strftime("%Y-%m-%dT%H:%M:%S")}

    def save(self, path: str | Path = DEFAULT_PATH) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_json(), indent=1))
        return path

    @classmethod
    def from_json(cls, d: dict) -> "Calibration":
        if "H_brio_to_proj" not in d:
            raise ValueError("JSON sin H_brio_to_proj (¿es un archivo de puntos? usa from_points_file)")
        hm = d.get("H_proj_to_mm")
        return cls(np.array(d["H_brio_to_proj"], np.float64), tuple(d.get("brio_size", (1920, 1080))),
                   tuple(d.get("proj_size", (1920, 1080))), d.get("projector", "proj0"), d.get("source", "points"),
                   d.get("stats", {}), None if hm is None else np.array(hm, np.float64), d.get("mm_source"),
                   d.get("hand_ref"))

    @classmethod
    def load(cls, path: str | Path = DEFAULT_PATH) -> "Calibration":
        """Lee un brio_calib.json o, si es un archivo de puntos ({"brio", "proj"}), arma la homografía al vuelo."""
        d = json.loads(Path(path).read_text())
        if "H_brio_to_proj" not in d and "brio" in d:
            return from_points_file(path)
        return cls.from_json(d)


# ---------------------------------------------------------------------- homografías

def homography(src, dst, ransac_px: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    """src → dst con ≥ 4 pares. Con `ransac_px`, RANSAC y reajuste por mínimos cuadrados sobre los inliers."""
    src = np.asarray(src, np.float64).reshape(-1, 2)
    dst = np.asarray(dst, np.float64).reshape(-1, 2)
    if len(src) != len(dst) or len(src) < 4:
        raise ValueError(f"se necesitan ≥ 4 pares de puntos (hay {len(src)} y {len(dst)})")
    if len(src) == 4 and ransac_px is None:
        H = cv2.getPerspectiveTransform(src.astype(np.float32), dst.astype(np.float32))
        return H.astype(np.float64), np.ones(4, bool)
    if ransac_px is None:
        H, _ = cv2.findHomography(src, dst, 0)
        inl = np.ones(len(src), bool)
    else:
        H, mask = cv2.findHomography(src, dst, cv2.RANSAC, ransac_px, maxIters=5000, confidence=0.999)
        if H is None:
            raise ValueError("RANSAC no encontró un plano")
        inl = mask.ravel().astype(bool)
        H2, _ = cv2.findHomography(src[inl], dst[inl], 0)
        H = H2 if H2 is not None else H
    if H is None:
        raise ValueError("homografía degenerada (¿puntos alineados?)")
    return H / H[2, 2], inl


def _errors(H, src, dst) -> np.ndarray:
    return np.linalg.norm(_apply(H, src) - np.asarray(dst, np.float64).reshape(-1, 2), axis=1)


def from_points(brio_pts, proj_pts, mm_pts=None, brio_size=(1920, 1080), proj_size=(1920, 1080),
                projector: str = "proj0", source: str = "points") -> Calibration:
    H, inl = homography(brio_pts, proj_pts)
    err = _errors(H, brio_pts, proj_pts)
    cal = Calibration(H, tuple(brio_size), tuple(proj_size), projector, source,
                      {"n": int(len(err)), "err_px_median": round(float(np.median(err)), 3),
                       "err_px_max": round(float(err.max()), 3)})
    if mm_pts is not None:
        cal.H_proj_to_mm, _ = homography(proj_pts, mm_pts)
        cal.mm_source = f"{source} (mm del archivo)"
    return cal


def from_points_file(path: str | Path) -> Calibration:
    """Respaldo sin secuencia: {"brio": [[x,y]...], "proj": [[x,y]...], "mm": [[x,y]...]?, "brio_size"?, "proj_size"?}."""
    d = json.loads(Path(path).read_text())
    return from_points(d["brio"], d["proj"], d.get("mm"), d.get("brio_size", (1920, 1080)),
                       d.get("proj_size", (1920, 1080)), d.get("projector", "proj0"), "points")


def from_graycode(scan_dir: str | Path, threshold: int = 5, skip_bits: int = 0, ransac_px: float = 4.0,
                  proj_size=(1920, 1080), projector: str = "proj0", save_map: bool = True) -> Calibration:
    """Homografía de la mesa desde una secuencia gray code vista por la Brio (`scan_dir` = carpeta con los PNG)."""
    from slscan import graycode, recon
    scan_dir = Path(scan_dir)
    items = graycode.load_sequence(scan_dir)
    dec = graycode.decode(items, threshold=threshold, skip_bits=skip_bits)
    if save_map:
        dec.save(scan_dir / "map.npz")
    m = recon.coalesce(dec)
    if len(m.cam) < 8:
        raise ValueError(f"muy pocas correspondencias ({len(m.cam)}): ¿la Brio ve la proyección? ¿exposición?")
    H, inl = homography(m.cam, m.pro, ransac_px=ransac_px)
    err = _errors(H, m.cam[inl], m.pro[inl])
    stats = {"n": int(len(m.cam)), "inliers": int(inl.sum()), "inlier_frac": round(float(inl.mean()), 3),
             "err_px_median": round(float(np.median(err)), 3), "err_px_p90": round(float(np.percentile(err, 90)), 3),
             "valid_cam_px": int(dec.stats.get("valid", 0)), "skip_bits": skip_bits, "threshold": threshold,
             "ransac_px": ransac_px, "scan": str(scan_dir)}
    return Calibration(H, tuple(dec.size), tuple(proj_size), projector, "graycode", stats)


# ---------------------------------------------------------------------- métrica (proj px → mm de la mesa)

def order_quad(quad) -> np.ndarray:
    """4 esquinas en orden: arriba-izq, arriba-der, abajo-der, abajo-izq (en coordenadas de imagen)."""
    q = np.asarray(quad, np.float64).reshape(4, 2)
    c = q.mean(axis=0)
    q = q[np.argsort(np.arctan2(q[:, 1] - c[1], q[:, 0] - c[0]))]      # sentido horario en imagen (y hacia abajo)
    start = int(np.argmin(q.sum(axis=1)))
    return np.roll(q, -start, axis=0)


def mm_from_quad(quad_proj, size_mm=(215.9, 279.4)) -> np.ndarray:
    """Homografía proj px → mm desde un rectángulo de medida conocida (la hoja carta). El lado corto del quad toma
    la medida corta. Origen en la esquina arriba-izquierda del quad; x a lo largo de su borde superior."""
    q = order_quad(quad_proj)
    top, side = np.linalg.norm(q[1] - q[0]), np.linalg.norm(q[3] - q[0])
    short, long_ = sorted(float(v) for v in size_mm)
    w, h = (short, long_) if top <= side else (long_, short)
    H, _ = homography(q, [[0, 0], [w, 0], [w, h], [0, h]])
    return H


def mm_from_segments(path: str | Path, name: str = "papel") -> np.ndarray:
    seg = json.loads(Path(path).read_text())
    obj = seg.get(name)
    if not obj or "quad" not in obj:
        raise KeyError(f"{path} no tiene '{name}' con quad")
    return mm_from_quad(obj["quad"], obj.get("mm", (215.9, 279.4)))


def mm_from_rig(rig_path: str | Path, projector: str | None = None, plane_z: float = 0.0, grid: int = 9) -> np.ndarray:
    """Homografía proj px → (X, Y) mm sobre el plano z = plane_z del mundo del rig (la mesa, si el mundo es la mesa).

    Corta los rayos del proyector (sin distorsión) con el plano en una grilla y ajusta la homografía por mínimos
    cuadrados: absorbe casi toda la distorsión del lente del proyector.
    """
    from slscan.rig import Rig
    rig = Rig.load(rig_path)
    dev = rig[projector] if projector else rig.projector()
    w, h = dev.size
    gx, gy = np.meshgrid(np.linspace(0, w - 1, grid), np.linspace(0, h - 1, grid))
    px = np.stack([gx.ravel(), gy.ravel()], axis=1)
    rays = cv2.undistortPoints(px.reshape(-1, 1, 2), dev.K, dev.dist).reshape(-1, 2)
    d_cam = np.column_stack([rays, np.ones(len(rays))])
    R, c = dev.R, dev.center_world()
    d_w = d_cam @ R                                        # R^T d por fila: dirección en el mundo
    lam = (plane_z - c[2]) / d_w[:, 2]
    ok = np.isfinite(lam) & (lam > 0)
    if ok.sum() < 4:
        raise ValueError("el plano de la mesa no está delante del proyector en este rig")
    X = c + lam[ok, None] * d_w[ok]
    H, _ = homography(px[ok], X[:, :2])
    return H


# ---------------------------------------------------------------------- captura desde el Mac

class RemoteCamera:
    """La Brio del PC vista como una `slscan.capture.Camera`: read() trae un PNG sin pérdida del servicio de manos."""

    def __init__(self, base_url: str, name: str = "brio", fresh: int = 3, gray: bool = True, timeout: float = 10.0):
        self.base = base_url.rstrip("/")
        if not self.base.startswith("http"):
            self.base = f"http://{self.base}"
        self.name = name
        self.fresh = fresh
        self.gray = gray
        self.timeout = timeout

    def flush(self, n: int = 3) -> None:
        self.fresh = max(self.fresh, n)                    # el servicio espera cuadros nuevos: no hay buffer local

    def read(self) -> np.ndarray:
        url = f"{self.base}/frame.png?fresh={self.fresh}&gray={int(self.gray)}"
        with urllib.request.urlopen(url, timeout=self.timeout) as r:
            data = np.frombuffer(r.read(), np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
        if img is None:
            raise RuntimeError(f"cuadro ilegible desde {url}")
        return img

    @property
    def size(self) -> tuple[int, int]:
        h, w = self.read().shape[:2]
        return w, h

    def close(self) -> None:
        pass


def http_base(pc: str, port: int = 8794) -> str:
    """'192.168.1.233' → 'http://192.168.1.233:8794'. Una URL http:// completa (con su puerto) pasa tal cual."""
    if pc.startswith(("http://", "https://")):
        return pc.rstrip("/")
    host = pc.split("://")[-1].split("/")[0].split(":")[0]
    return f"http://{host}:{port}"


def capture_graycode(pc: str, out: str | Path = "captures/brio_scan", screen=1, proj_size=(1920, 1080),
                     settle: float = 0.6, fresh: int = 3, http_port: int = 8794, log=print) -> Path:
    """Proyecta la secuencia gray code desde el Mac y guarda lo que ve la Brio en <out>/brio/[..].png."""
    from slscan import graycode
    from slscan.capture import PatternWindow, capture_sequence
    out = Path(out)
    cam = RemoteCamera(http_base(pc, http_port), "brio", fresh=fresh)
    specs = graycode.sequence(tuple(proj_size))
    log(f"{len(specs)} patrones a {settle:.1f} s (~{len(specs) * (settle + 0.25) / 60:.1f} min). No muevas nada.")
    win = PatternWindow(tuple(proj_size), screen=screen)
    try:
        capture_sequence(win, [cam], specs, lambda s: graycode.pattern(tuple(proj_size), s.axis, s.bit, s.inverse),
                         out, settle, flush=fresh, log=log)
    finally:
        win.close()
    return out / cam.name


def find_dot(dark: np.ndarray, lit: np.ndarray, min_area: int = 6, min_contrast: float = 25.0):
    """Centro (x, y) del punto proyectado: la mancha más grande de la diferencia lit − dark, ponderada por brillo."""
    g0 = dark if dark.ndim == 2 else cv2.cvtColor(dark, cv2.COLOR_BGR2GRAY)
    g1 = lit if lit.ndim == 2 else cv2.cvtColor(lit, cv2.COLOR_BGR2GRAY)
    diff = cv2.GaussianBlur(cv2.subtract(g1, g0), (5, 5), 0).astype(np.float32)
    peak = float(diff.max())
    if peak < min_contrast:
        return None
    mask = (diff > peak * 0.5).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(mask)
    if n < 2:
        return None
    k = int(np.argmax(st[1:, cv2.CC_STAT_AREA]) + 1)
    if st[k, cv2.CC_STAT_AREA] < min_area:
        return None
    ys, xs = np.nonzero(lab == k)
    w = diff[ys, xs]
    return float((xs * w).sum() / w.sum()), float((ys * w).sum() / w.sum())


def dot_grid(proj_size=(1920, 1080), grid=(3, 3), inset: int = 160) -> np.ndarray:
    w, h = proj_size
    xs = np.linspace(inset, w - 1 - inset, grid[0])
    ys = np.linspace(inset, h - 1 - inset, grid[1])
    return np.array([(x, y) for y in ys for x in xs], np.float64)


def capture_dots(pc: str, screen=1, proj_size=(1920, 1080), grid=(3, 3), inset: int = 160, radius: int = 14,
                 settle: float = 0.5, fresh: int = 3, http_port: int = 8794, log=print) -> Calibration:
    """Calibración por puntos proyectados de a uno (respaldo rápido sin secuencia gray code)."""
    from slscan.capture import PatternWindow
    cam = RemoteCamera(http_base(pc, http_port), "brio", fresh=fresh)
    pts = dot_grid(proj_size, grid, inset)
    win = PatternWindow(tuple(proj_size), screen=screen)
    brio, proj = [], []
    try:
        black = np.zeros((proj_size[1], proj_size[0]), np.uint8)
        win.show(black); time.sleep(settle); dark = cam.read()
        for x, y in pts:
            img = black.copy()
            cv2.circle(img, (int(round(x)), int(round(y))), radius, 255, -1, cv2.LINE_AA)
            win.show(img); time.sleep(settle)
            c = find_dot(dark, cam.read())
            log(f"  punto ({x:.0f}, {y:.0f}) → {'no visto' if c is None else f'({c[0]:.1f}, {c[1]:.1f})'}")
            if c is not None:
                brio.append(c); proj.append((x, y))
        win.show(black)
    finally:
        win.close()
    if len(brio) < 4:
        raise RuntimeError(f"la Brio vio solo {len(brio)} de {len(pts)} puntos: ¿apunta a la proyección?")
    h, w = dark.shape[:2]
    return from_points(brio, proj, brio_size=(w, h), proj_size=proj_size, source="dots")


def hand_ref_from_frames(frames, cal: Calibration, min_samples: int = 10) -> float:
    """Tamaño de palma apoyada (px de proyector) = mediana sobre cuadros con la mano plana sobre la mesa."""
    from .model import palm_size
    sizes = [palm_size(cal.brio_to_proj(h.landmarks[:, :2])) for f in frames for h in f.hands]
    if len(sizes) < min_samples:
        raise RuntimeError(f"pocas muestras de mano ({len(sizes)} < {min_samples}): ¿se ve la mano?")
    return float(np.median(sizes))
