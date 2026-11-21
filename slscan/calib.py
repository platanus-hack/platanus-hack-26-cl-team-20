"""Calibración: intrínsecos (ChArUco), proyector (homografías locales), extrínsecos de todo el rig.

Flujo (F1):
1. Intrínsecos de cada cámara con 20–30 vistas del tablero ChArUco (`calibrate_intrinsics`).
2. Proyector: en cada pose del tablero se captura además una secuencia gray code; cada esquina
   detectada en la cámara se lleva a coordenadas del proyector con una homografía local
   (ventana de ±`half` px alrededor de la esquina, método de Brown que usa sltk). Con esas
   correspondencias tablero(mm) -> proyector(px) se calibra el proyector como si fuera una
   cámara (`calibrate_projector`).
3. Extrínsecos: pose inicial de cada dispositivo respecto a cam0 por las poses del tablero
   compartidas, y refinamiento conjunto por bundle adjustment con intrínsecos fijos
   (`bundle_adjust`). Salida: `Rig` en mm con el mundo en cam0.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .graycode import Decoded, INVALID
from .rig import Device, Rig


# ----------------------------------------------------------------------------- tablero

@dataclass
class CharucoBoard:
    cols: int = 7            # casillas a lo ancho
    rows: int = 5            # casillas a lo alto
    square_mm: float = 40.0
    marker_mm: float = 30.0
    dict_name: str = "DICT_5X5_100"

    def __post_init__(self):
        self.dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, self.dict_name))
        self.board = cv2.aruco.CharucoBoard((self.cols, self.rows), self.square_mm, self.marker_mm, self.dictionary)
        self.detector = cv2.aruco.CharucoDetector(self.board)

    @classmethod
    def from_args(cls, args) -> "CharucoBoard":
        c, r = (int(v) for v in args.board.lower().split("x"))
        return cls(c, r, args.square, args.marker, args.dict)

    @property
    def n_corners(self) -> int:
        return (self.cols - 1) * (self.rows - 1)

    @property
    def size_mm(self) -> tuple[float, float]:
        return (self.cols * self.square_mm, self.rows * self.square_mm)

    def corners_mm(self) -> np.ndarray:
        """Esquinas interiores (id -> xyz mm, z = 0), en el orden de ids de OpenCV."""
        return np.asarray(self.board.getChessboardCorners(), np.float64).reshape(-1, 3)

    def image(self, px_per_mm: float = 4.0, margin_mm: float = 10.0) -> np.ndarray:
        w, h = self.size_mm
        size = (int(round((w + 2 * margin_mm) * px_per_mm)), int(round((h + 2 * margin_mm) * px_per_mm)))
        return self.board.generateImage(size, marginSize=int(round(margin_mm * px_per_mm)))

    def detect(self, img: np.ndarray, min_corners: int = 6) -> dict | None:
        """-> {obj (n,3) mm, img (n,2) px, ids (n,), markers} o None si hay pocas esquinas."""
        gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        cc, ci, mc, mi = self.detector.detectBoard(gray)
        if cc is None or ci is None or len(cc) < min_corners:
            return None
        obj, pts = self.board.matchImagePoints(cc, ci)
        return {"obj": obj.reshape(-1, 3).astype(np.float64), "img": pts.reshape(-1, 2).astype(np.float64),
                "ids": ci.ravel().astype(int), "markers": 0 if mi is None else len(mi)}


# ----------------------------------------------------------------------------- intrínsecos

def _calib_flags(rational: bool, guess: bool = False) -> int:
    f = 0
    if rational:
        f |= cv2.CALIB_RATIONAL_MODEL
    if guess:
        f |= cv2.CALIB_USE_INTRINSIC_GUESS
    return f


def _per_view_rms(obj_list, img_list, K, dist, rvecs, tvecs) -> list[float]:
    out = []
    for o, i, r, t in zip(obj_list, img_list, rvecs, tvecs):
        p, _ = cv2.projectPoints(o.reshape(-1, 1, 3), r, t, K, dist)
        out.append(float(np.sqrt(np.mean(np.sum((p.reshape(-1, 2) - i) ** 2, axis=1)))))
    return out


def calibrate_points(obj_list: list[np.ndarray], img_list: list[np.ndarray], size: tuple[int, int],
                     rational: bool = False, K_guess: np.ndarray | None = None) -> dict:
    """calibrateCamera sobre listas de correspondencias 3D-2D (una por vista)."""
    obj = [o.astype(np.float32).reshape(-1, 1, 3) for o in obj_list]
    img = [i.astype(np.float32).reshape(-1, 1, 2) for i in img_list]
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 200, 1e-7)
    rms, K, dist, rvecs, tvecs = cv2.calibrateCamera(
        obj, img, size, K_guess, None, flags=_calib_flags(rational, K_guess is not None), criteria=crit)
    per_view = _per_view_rms(obj_list, img_list, K, dist, rvecs, tvecs)
    return {"K": K, "dist": dist.ravel(), "rms": float(rms), "per_view": per_view, "size": size,
            "rvecs": [r.ravel() for r in rvecs], "tvecs": [t.ravel() for t in tvecs], "used": len(obj)}


def calibrate_intrinsics(images: list[np.ndarray], board: CharucoBoard, rational: bool = False,
                         min_corners: int = 8, drop_worse_than: float | None = 1.0) -> dict:
    """Intrínsecos de una cámara desde imágenes del tablero. Descarta vistas con rms > drop_worse_than."""
    obj_list, img_list, used = [], [], []
    size = None
    for k, im in enumerate(images):
        if im is None:
            continue
        size = (im.shape[1], im.shape[0])
        d = board.detect(im, min_corners)
        if d is None:
            continue
        obj_list.append(d["obj"]); img_list.append(d["img"]); used.append(k)
    if len(obj_list) < 3:
        raise ValueError(f"solo {len(obj_list)} vistas con tablero; se necesitan >= 3")
    res = calibrate_points(obj_list, img_list, size, rational)
    if drop_worse_than is not None:
        keep = [i for i, e in enumerate(res["per_view"]) if e <= drop_worse_than]
        if 3 <= len(keep) < len(obj_list):
            res = calibrate_points([obj_list[i] for i in keep], [img_list[i] for i in keep], size, rational)
            used = [used[i] for i in keep]
    res["used_idx"] = used
    res["detections"] = {"obj": obj_list, "img": img_list}
    return res


# ----------------------------------------------------------------------------- proyector

def plane_homography(dec: Decoded, polygon: np.ndarray, ransac_px: float = 2.0, max_pts: int = 20000) -> np.ndarray | None:
    """Homografía global cámara->proyector del plano que contiene `polygon` (px de cámara), con todos los
    píxeles decodificados dentro del polígono ampliado. Sirve cuando el tablero no se puede decodificar
    (p.ej. un iPad mostrando el tablero apoyado en una cartulina: el gray code se lee alrededor)."""
    h, w = dec.px.shape
    poly = np.asarray(polygon, np.float32)
    c = poly.mean(axis=0)
    big = (c + (poly - c) * 2.5).astype(np.int32)        # el plano alrededor del tablero
    mask = np.zeros((h, w), np.uint8)
    cv2.fillPoly(mask, [big.reshape(-1, 1, 2)], 255)
    valid = (mask > 0) & (dec.px != INVALID) & (dec.py != INVALID)
    ys, xs = np.nonzero(valid)
    if len(xs) < 50:
        return None
    if len(xs) > max_pts:
        idx = np.random.default_rng(0).choice(len(xs), max_pts, replace=False); xs, ys = xs[idx], ys[idx]
    src = np.column_stack([xs, ys]).astype(np.float64)
    dst = np.column_stack([dec.px[ys, xs], dec.py[ys, xs]]).astype(np.float64) + 0.5
    H, m = cv2.findHomography(src, dst, cv2.RANSAC, ransac_px)
    return H if H is not None and m.sum() >= 50 else None


def corner_projector_coords(dec: Decoded, corners: np.ndarray, half: int = 30, min_pts: int = 12,
                            ransac_px: float = 2.0, fallback_plane: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """Esquinas de cámara -> coordenadas del proyector por homografía local (como sltk/Brown).

    Devuelve (proj (n,2), ok (n,) bool). Cada esquina usa los píxeles decodificados en
    una ventana de ±half px; con menos de min_pts queda ok=False. Si casi ninguna esquina tiene datos
    locales (tablero en una pantalla, sin decodificación encima) y fallback_plane, se usa la homografía
    global del plano alrededor del tablero.
    """
    h, w = dec.px.shape
    out = np.full((len(corners), 2), np.nan)
    ok = np.zeros(len(corners), bool)
    for k, (x, y) in enumerate(corners):
        x0, x1 = int(max(0, x - half)), int(min(w, x + half + 1))
        y0, y1 = int(max(0, y - half)), int(min(h, y + half + 1))
        if x1 - x0 < 4 or y1 - y0 < 4:
            continue
        sub_px, sub_py = dec.px[y0:y1, x0:x1], dec.py[y0:y1, x0:x1]
        valid = (sub_px != INVALID) & (sub_py != INVALID)
        if valid.sum() < min_pts:
            continue
        ys, xs = np.nonzero(valid)
        src = np.column_stack([xs + x0, ys + y0]).astype(np.float64)
        # la esquina debe estar rodeada de datos (los 4 cuadrantes), si no la homografía extrapola
        quad = [((src[:, 0] < x) & (src[:, 1] < y)).sum(), ((src[:, 0] >= x) & (src[:, 1] < y)).sum(),
                ((src[:, 0] < x) & (src[:, 1] >= y)).sum(), ((src[:, 0] >= x) & (src[:, 1] >= y)).sum()]
        if min(quad) < max(3, min_pts // 4):
            continue
        # el código decodificado k cubre [k, k+1): se usa el centro del píxel del proyector
        dst = np.column_stack([sub_px[valid], sub_py[valid]]).astype(np.float64) + 0.5
        H, mask = cv2.findHomography(src, dst, cv2.RANSAC, ransac_px)
        if H is None or mask.sum() < min_pts:
            continue
        q = H @ np.array([x, y, 1.0])
        out[k] = q[:2] / q[2]
        ok[k] = True
    if fallback_plane and ok.sum() < max(6, len(corners) // 2) and len(corners) >= 4:
        hull = cv2.convexHull(np.asarray(corners, np.float32)).reshape(-1, 2)
        Hg = plane_homography(dec, hull, ransac_px)
        if Hg is not None:
            for k, (x, y) in enumerate(corners):
                if not ok[k]:
                    q = Hg @ np.array([x, y, 1.0]); out[k] = q[:2] / q[2]; ok[k] = True
    return out, ok


def calibrate_projector(obj_list: list[np.ndarray], proj_list: list[np.ndarray], proj_size: tuple[int, int],
                        rational: bool = False) -> dict:
    """El proyector como cámara: tablero (mm) -> píxel de proyector. Necesita >= 3 poses."""
    if len(obj_list) < 3:
        raise ValueError("se necesitan >= 3 poses con esquinas llevadas al proyector")
    w, h = proj_size
    # guess: los proyectores suelen tener el centro óptico desplazado (lens shift); fx ~ 1.3 w
    K0 = np.array([[1.3 * w, 0, w / 2], [0, 1.3 * w, h * 0.9], [0, 0, 1]], np.float64)
    return calibrate_points(obj_list, proj_list, proj_size, rational, K_guess=K0)


# ----------------------------------------------------------------------------- extrínsecos

def _rt_to_mat(rvec, tvec):
    R, _ = cv2.Rodrigues(np.asarray(rvec, np.float64).reshape(3, 1))
    T = np.eye(4); T[:3, :3] = R; T[:3, 3] = np.asarray(tvec).ravel()
    return T


def _average_transforms(Ts: list[np.ndarray]) -> np.ndarray:
    """Media de poses: traslación media y rotación media por SVD de la suma de matrices."""
    R = sum(T[:3, :3] for T in Ts)
    U, _, Vt = np.linalg.svd(R)
    Rm = U @ Vt
    if np.linalg.det(Rm) < 0:
        U[:, -1] *= -1; Rm = U @ Vt
    T = np.eye(4); T[:3, :3] = Rm; T[:3, 3] = np.mean([T_[:3, 3] for T_ in Ts], axis=0)
    return T


@dataclass
class Observation:
    pose: int          # índice de pose del tablero
    device: str
    obj: np.ndarray    # (n,3) mm en el tablero
    img: np.ndarray    # (n,2) px en el dispositivo


def initial_extrinsics(devices: dict[str, Device], obs: list[Observation], world: str) -> tuple[dict, dict]:
    """Pose de cada dispositivo respecto al mundo y del tablero por pose, vía solvePnP.

    -> (dev_T: name -> 4x4 mundo->dispositivo, board_T: pose -> 4x4 tablero->mundo)
    """
    pnp: dict[tuple[int, str], np.ndarray] = {}
    for o in obs:
        d = devices[o.device]
        ok, r, t = cv2.solvePnP(o.obj.reshape(-1, 1, 3), o.img.reshape(-1, 1, 2), d.K, d.dist,
                                flags=cv2.SOLVEPNP_ITERATIVE)
        if ok:
            pnp[(o.pose, o.device)] = _rt_to_mat(r, t)       # tablero -> dispositivo
    board_T = {p: T for (p, n), T in pnp.items() if n == world}
    dev_T = {world: np.eye(4)}
    for name in devices:
        if name == world:
            continue
        rel = [pnp[(p, name)] @ np.linalg.inv(board_T[p]) for (p, n) in pnp if n == name and p in board_T]
        if not rel:
            raise ValueError(f"{name} no comparte ninguna pose del tablero con {world}")
        dev_T[name] = _average_transforms(rel)
    # tablero -> mundo para poses que cam0 no vio: por otro dispositivo
    for (p, n), T in pnp.items():
        if p not in board_T:
            board_T[p] = np.linalg.inv(dev_T[n]) @ T
    return dev_T, board_T


def bundle_adjust(devices: dict[str, Device], obs: list[Observation], world: str,
                  dev_T: dict, board_T: dict, iters: int = 50, log=None) -> tuple[dict, dict, dict]:
    """Refina poses de dispositivos y tablero minimizando la reproyección (intrínsecos fijos).

    -> (dev_T refinado, board_T refinado, rms por dispositivo en px)
    """
    from scipy.optimize import least_squares

    names = [n for n in devices if n != world]
    poses = sorted(board_T)
    pidx = {p: i for i, p in enumerate(poses)}

    def pack():
        x = []
        for n in names:
            T = dev_T[n]; r, _ = cv2.Rodrigues(T[:3, :3]); x += [*r.ravel(), *T[:3, 3]]
        for p in poses:
            T = board_T[p]; r, _ = cv2.Rodrigues(T[:3, :3]); x += [*r.ravel(), *T[:3, 3]]
        return np.array(x)

    def unpack(x):
        dT = {world: np.eye(4)}
        for i, n in enumerate(names):
            dT[n] = _rt_to_mat(x[6 * i:6 * i + 3], x[6 * i + 3:6 * i + 6])
        off = 6 * len(names)
        bT = {p: _rt_to_mat(x[off + 6 * i:off + 6 * i + 3], x[off + 6 * i + 3:off + 6 * i + 6]) for i, p in enumerate(poses)}
        return dT, bT

    def residuals(x):
        dT, bT = unpack(x)
        res = []
        for o in obs:
            T = dT[o.device] @ bT[o.pose]
            r, _ = cv2.Rodrigues(T[:3, :3])
            d = devices[o.device]
            p, _ = cv2.projectPoints(o.obj.reshape(-1, 1, 3), r, T[:3, 3].reshape(3, 1), d.K, d.dist)
            res.append((p.reshape(-1, 2) - o.img).ravel())
        return np.concatenate(res)

    x0 = pack()
    r0 = residuals(x0)
    sol = least_squares(residuals, x0, method="trf", loss="huber", f_scale=2.0, max_nfev=iters * 4, xtol=1e-10)
    dT, bT = unpack(sol.x)
    r1 = residuals(sol.x)
    if log:
        log(f"  bundle: rms {np.sqrt(np.mean(r0**2)):.3f} -> {np.sqrt(np.mean(r1**2)):.3f} px "
            f"({len(obs)} observaciones, {len(poses)} poses)")
    rms = {}
    off = 0
    for o in obs:
        n = 2 * len(o.obj)
        rms.setdefault(o.device, []).append(r1[off:off + n]); off += n
    rms = {k: float(np.sqrt(np.mean(np.concatenate(v) ** 2))) for k, v in rms.items()}
    return dT, bT, rms


# ----------------------------------------------------------------------------- rig completo

def calibrate_rig(captures: Path, board: CharucoBoard, proj_size: tuple[int, int] | None = None,
                  intrinsics_dir: Path | None = None, world: str | None = None, log=print) -> Rig:
    """Calibra todo desde una carpeta de capturas guiadas.

    Estructura: captures/pose_NNNN/<cam>/board.png y, para el proyector, en la misma carpeta
    las imágenes gray code `[AA-BB-CC_NN].png` (o una decodificación `map.npz`).
    """
    from . import graycode

    poses = sorted(p for p in captures.iterdir() if p.is_dir() and p.name.startswith("pose_"))
    if not poses:
        raise FileNotFoundError(f"sin pose_NNNN en {captures}")
    cam_names = sorted({c.name for p in poses for c in p.iterdir() if c.is_dir()})
    world = world or cam_names[0]
    log(f"{len(poses)} poses, cámaras {cam_names}, mundo {world}")

    # 1. detecciones e intrínsecos por cámara
    devices: dict[str, Device] = {}
    det: dict[tuple[int, str], dict] = {}
    for cam in cam_names:
        images, idx = [], []
        for i, p in enumerate(poses):
            f = p / cam / "board.png"
            if f.exists():
                im = cv2.imread(str(f))
                d = board.detect(im)
                if d is not None:
                    det[(i, cam)] = d; images.append(im); idx.append(i)
        jf = (intrinsics_dir / f"{cam}.json") if intrinsics_dir else None
        if jf and jf.exists():
            devices[cam] = Device.from_json(json.loads(jf.read_text()))
            log(f"  {cam}: intrínsecos de {jf.name} (rms {devices[cam].rms})")
        else:
            res = calibrate_intrinsics(images, board)
            devices[cam] = Device(cam, "camera", res["size"], res["K"], res["dist"], rms=res["rms"])
            log(f"  {cam}: intrínsecos desde {res['used']} vistas, rms {res['rms']:.3f} px")

    # 2. proyector por homografías locales
    obs: list[Observation] = [Observation(i, cam, d["obj"], d["img"]) for (i, cam), d in det.items()]
    pro_obj, pro_pts, pro_by_pose = [], [], {}
    for (i, cam), d in det.items():
        folder = poses[i] / cam
        dec = None
        if (folder / "map.npz").exists():
            dec = Decoded.load(folder / "map.npz")
        else:
            try:
                items = graycode.load_sequence(folder)
                dec = graycode.decode(items)
            except (FileNotFoundError, ValueError):
                dec = None
        if dec is None:
            continue
        q, ok = corner_projector_coords(dec, d["img"])
        if ok.sum() >= 6:
            pro_obj.append(d["obj"][ok]); pro_pts.append(q[ok])
            pro_by_pose.setdefault(i, []).append((d["obj"][ok], q[ok]))
    if pro_obj:
        if proj_size is None:
            raise ValueError("--proj-size es obligatorio cuando hay secuencias gray code")
        res = calibrate_projector(pro_obj, pro_pts, proj_size)
        devices["proj"] = Device("proj", "projector", proj_size, res["K"], res["dist"], rms=res["rms"])
        log(f"  proj: {len(pro_obj)} vistas, rms {res['rms']:.3f} px")
        for i, lst in pro_by_pose.items():
            o = np.concatenate([a for a, _ in lst]); q = np.concatenate([b for _, b in lst])
            obs.append(Observation(i, "proj", o, q))
    else:
        log("  sin gray code: rig sin proyector")

    # 3. extrínsecos + bundle
    dev_T, board_T = initial_extrinsics(devices, obs, world)
    dev_T, board_T, rms = bundle_adjust(devices, obs, world, dev_T, board_T, log=log)
    for n, d in devices.items():
        T = dev_T[n]; d.R, d.t = T[:3, :3], T[:3, 3]
        log(f"  {n}: rms {rms.get(n, float('nan')):.3f} px, centro {np.round(d.center_world(), 1)} mm, "
            f"FOV {d.fov_deg()[0]:.1f}°x{d.fov_deg()[1]:.1f}°")
    rig = Rig(devices, world=world, notes=f"calibrado desde {captures} con {len(poses)} poses")
    rig.extra["rms_px"] = rms
    return rig


def compare_rigs(a: Rig, b: Rig) -> dict:
    """Repetibilidad entre dos sesiones: diferencia de posición (mm) y orientación (°) por dispositivo."""
    out = {}
    for n in a.devices:
        if n not in b.devices:
            continue
        da, db = a[n], b[n]
        dR = da.R @ db.R.T
        ang = np.degrees(np.arccos(np.clip((np.trace(dR) - 1) / 2, -1, 1)))
        out[n] = {"pos_mm": float(np.linalg.norm(da.center_world() - db.center_world())), "rot_deg": float(ang)}
    return out
