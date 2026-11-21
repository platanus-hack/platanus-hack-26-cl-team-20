"""Tests sintéticos de calibración: sin hardware, con un rig conocido.

Cámaras y proyector se simulan proyectando el tablero (y, para el proyector, un mapa gray code
ideal) con modelos conocidos; se comprueba que la calibración recupera K, distorsión y poses.
"""

import numpy as np
import cv2
import pytest

from slscan import calib
from slscan.calib import CharucoBoard, Observation
from slscan.graycode import Decoded, INVALID
from slscan.rig import Device, Rig


BOARD = CharucoBoard(7, 5, 40.0, 30.0)
CAM_SIZE = (1280, 720)
K_TRUE = np.array([[1100.0, 0, 650.0], [0, 1100.0, 350.0], [0, 0, 1]])
DIST_TRUE = np.array([-0.12, 0.05, 0.0005, -0.0003, 0.0])


def board_poses(n: int, seed: int = 0, dist_mm=(500, 800)):
    """Poses tablero->cámara variadas (inclinación hasta ~35°, desplazamientos en el encuadre)."""
    rng = np.random.default_rng(seed)
    w, h = BOARD.size_mm
    out = []
    for _ in range(n):
        rvec = rng.uniform(-0.6, 0.6, 3) * np.array([1, 1, 0.5])
        R, _ = cv2.Rodrigues(rvec)
        z = rng.uniform(*dist_mm)
        center = np.array([rng.uniform(-0.25, 0.25) * z, rng.uniform(-0.15, 0.15) * z, z])
        t = center - R @ np.array([w / 2, h / 2, 0])     # centro del tablero en `center`
        out.append((R, t))
    return out


def project(K, dist, R, t, X):
    r, _ = cv2.Rodrigues(R)
    p, _ = cv2.projectPoints(X.reshape(-1, 1, 3), r, t.reshape(3, 1), K, dist)
    return p.reshape(-1, 2)


def render_board(K, R, t, size=CAM_SIZE, px_per_mm=4.0):
    """Imagen de cámara del tablero plano (sin distorsión): homografía mm -> px."""
    img = BOARD.image(px_per_mm, margin_mm=10.0)
    # píxel de la imagen del tablero -> mm (origen de las esquinas en el borde del tablero)
    m = 10.0
    src = np.array([[0, 0], [img.shape[1], 0], [img.shape[1], img.shape[0]], [0, img.shape[0]]], np.float64)
    mm = (src / px_per_mm) - m
    X = np.column_stack([mm, np.zeros(4)])
    dst = project(K, np.zeros(5), R, t, X)
    H, _ = cv2.findHomography(src, dst)
    out = cv2.warpPerspective(img, H, size, flags=cv2.INTER_AREA, borderValue=110)
    return cv2.cvtColor(out, cv2.COLOR_GRAY2BGR)


def test_board_detect_on_render():
    R, t = board_poses(1, seed=3)[0]
    d = BOARD.detect(render_board(K_TRUE, R, t))
    assert d is not None and len(d["img"]) >= 12
    exp = project(K_TRUE, np.zeros(5), R, t, d["obj"])
    assert np.abs(exp - d["img"]).max() < 0.6


def test_intrinsics_from_rendered_views():
    images = [render_board(K_TRUE, R, t) for R, t in board_poses(20, seed=1)]
    res = calib.calibrate_intrinsics(images, BOARD, drop_worse_than=None)
    assert res["rms"] < 0.5
    assert abs(res["K"][0, 0] / K_TRUE[0, 0] - 1) < 0.01
    assert abs(res["K"][0, 2] - K_TRUE[0, 2]) < 8 and abs(res["K"][1, 2] - K_TRUE[1, 2]) < 8


def test_intrinsics_with_distortion_from_points():
    corners = BOARD.corners_mm()
    rng = np.random.default_rng(5)
    obj, img = [], []
    for R, t in board_poses(25, seed=2):
        p = project(K_TRUE, DIST_TRUE, R, t, corners) + rng.normal(0, 0.15, (len(corners), 2))
        obj.append(corners); img.append(p)
    res = calib.calibrate_points(obj, img, CAM_SIZE)
    assert res["rms"] < 0.3
    assert np.abs(res["K"] - K_TRUE).max() < 3.0
    assert abs(res["dist"][0] - DIST_TRUE[0]) < 0.01
    # k2/k3 se compensan entre sí: lo que importa es el campo de distorsión donde hubo tablero
    allp = np.concatenate(img)                      # solo donde hubo tablero
    (x0, y0), (x1, y1) = np.percentile(allp, 10, axis=0), np.percentile(allp, 90, axis=0)
    ys, xs = np.mgrid[y0:y1:20j, x0:x1:30j]
    grid = np.column_stack([xs.ravel(), ys.ravel()]).astype(np.float64).reshape(-1, 1, 2)
    u_true = cv2.undistortPoints(grid, K_TRUE, DIST_TRUE, P=K_TRUE).reshape(-1, 2)
    u_est = cv2.undistortPoints(grid, res["K"], res["dist"], P=K_TRUE).reshape(-1, 2)
    assert np.abs(u_true - u_est).max() < 1.0      # cy y p2 se compensan con ruido de 0.15 px


def _proj_device():
    Kp = np.array([[1700.0, 0, 640.0], [0, 1700.0, 520.0], [0, 0, 1]])
    Rp, _ = cv2.Rodrigues(np.array([0.0, -0.35, 0.0]))     # mira hacia +x (hacia el centro de la escena)
    center = np.array([-250.0, 0.0, -50.0])                  # a la izquierda de la cámara, en su marco
    return Device("proj", "projector", (1280, 800), Kp, np.zeros(5), Rp, -(Rp @ center))


def synthetic_decoded(cam: Device, pro: Device, R, t) -> Decoded:
    """Mapa cámara->proyector ideal para un tablero plano en pose (R,t) respecto a la cámara."""
    w, h = cam.size
    ys, xs = np.mgrid[0:h, 0:w]
    pts = np.column_stack([xs.ravel(), ys.ravel()]).astype(np.float64)
    rays = cv2.undistortPoints(pts.reshape(-1, 1, 2), cam.K, cam.dist).reshape(-1, 2)
    rays = np.column_stack([rays, np.ones(len(rays))])
    n = R[:, 2]                                   # normal del plano del tablero en la cámara
    denom = rays @ n
    lam = (n @ t) / denom
    X = rays * lam[:, None]
    ok = (lam > 0) & np.isfinite(lam)
    # dentro del tablero
    Xb = (R.T @ (X - t).T).T
    bw, bh = BOARD.size_mm
    ok &= (Xb[:, 0] >= 0) & (Xb[:, 0] <= bw) & (Xb[:, 1] >= 0) & (Xb[:, 1] <= bh)
    q = project(pro.K, pro.dist, pro.R, pro.t, X)
    ok &= (q[:, 0] >= 0) & (q[:, 0] < pro.size[0]) & (q[:, 1] >= 0) & (q[:, 1] < pro.size[1])
    px = np.full(h * w, INVALID, np.uint16); py = px.copy()
    px[ok] = np.floor(q[ok, 0]).astype(np.uint16); py[ok] = np.floor(q[ok, 1]).astype(np.uint16)
    return Decoded(cam.size, px.reshape(h, w), py.reshape(h, w), np.zeros((h, w, 3), np.float32), {})


def test_projector_corner_mapping_and_calibration():
    cam = Device("cam0", "camera", CAM_SIZE, K_TRUE, DIST_TRUE)
    pro = _proj_device()
    corners = BOARD.corners_mm()
    obj_list, pro_list = [], []
    for R, t in board_poses(8, seed=7, dist_mm=(600, 800)):
        dec = synthetic_decoded(cam, pro, R, t)
        cpx = project(cam.K, cam.dist, R, t, corners)
        q, ok = calib.corner_projector_coords(dec, cpx, half=25)
        assert ok.sum() >= 12
        truth = project(pro.K, pro.dist, pro.R @ R, pro.R @ t + pro.t, corners)
        assert np.abs(q[ok] - truth[ok]).max() < 0.3
        obj_list.append(corners[ok]); pro_list.append(q[ok])
    res = calib.calibrate_projector(obj_list, pro_list, pro.size)
    assert res["rms"] < 0.6
    assert abs(res["K"][0, 0] / pro.K[0, 0] - 1) < 0.02
    assert abs(res["K"][1, 2] - pro.K[1, 2]) < 15


def test_bundle_adjust_recovers_rig():
    cam0 = Device("cam0", "camera", CAM_SIZE, K_TRUE, DIST_TRUE)
    R1, _ = cv2.Rodrigues(np.array([0.05, 0.5, 0.0]))
    cam1 = Device("cam1", "camera", CAM_SIZE, K_TRUE * np.array([[1.05, 1, 1], [1, 1.05, 1], [1, 1, 1]]),
                  np.zeros(5), R1, np.array([-400.0, 20.0, 120.0]))
    pro = _proj_device()
    devs = {"cam0": cam0, "cam1": cam1, "proj": pro}
    corners = BOARD.corners_mm()
    rng = np.random.default_rng(11)
    obs = []
    for i, (R, t) in enumerate(board_poses(12, seed=9, dist_mm=(700, 900))):
        for name, d in devs.items():
            Rd, td = d.R @ R, d.R @ t + d.t
            p = project(d.K, d.dist, Rd, td, corners)
            inside = (p[:, 0] > 0) & (p[:, 0] < d.size[0]) & (p[:, 1] > 0) & (p[:, 1] < d.size[1])
            if inside.sum() < 8:
                continue
            obs.append(Observation(i, name, corners[inside], p[inside] + rng.normal(0, 0.2, (inside.sum(), 2))))
    # poses iniciales perturbadas
    truth = {n: np.vstack([np.hstack([d.R, d.t.reshape(3, 1)]), [0, 0, 0, 1]]) for n, d in devs.items()}
    guess = {}
    for n, T in truth.items():
        if n == "cam0":
            guess[n] = np.eye(4); continue
        dR, _ = cv2.Rodrigues(rng.normal(0, 0.03, 3))
        G = T.copy(); G[:3, :3] = dR @ T[:3, :3]; G[:3, 3] += rng.normal(0, 15, 3); guess[n] = G
    dev_T0, board_T0 = calib.initial_extrinsics(devs, obs, "cam0")
    dev_T, board_T, rms = calib.bundle_adjust(devs, obs, "cam0", dev_T0, board_T0)
    for n in ("cam1", "proj"):
        dR = dev_T[n][:3, :3] @ truth[n][:3, :3].T
        ang = np.degrees(np.arccos(np.clip((np.trace(dR) - 1) / 2, -1, 1)))
        assert ang < 0.1, n
        assert np.linalg.norm(dev_T[n][:3, 3] - truth[n][:3, 3]) < 1.5, n
        assert rms[n] < 0.35


def test_compare_rigs():
    a = Rig({"cam0": Device("cam0", "camera", CAM_SIZE, K_TRUE, DIST_TRUE)})
    b = Rig({"cam0": Device("cam0", "camera", CAM_SIZE, K_TRUE, DIST_TRUE, t=np.array([1.0, 0, 0]))})
    r = calib.compare_rigs(a, b)
    assert r["cam0"]["pos_mm"] == pytest.approx(1.0) and r["cam0"]["rot_deg"] == pytest.approx(0.0, abs=1e-6)


def test_plane_homography_fallback_when_board_not_decoded():
    """Tablero sobre una pantalla: sin gray code encima de las esquinas, la homografía global del plano las ubica."""
    cam = Device("cam0", "camera", CAM_SIZE, K_TRUE, np.zeros(5))
    pro = _proj_device()
    corners = BOARD.corners_mm()
    R, t = board_poses(1, seed=8, dist_mm=(650, 750))[0]
    dec = synthetic_decoded(cam, pro, R, t)
    cpx = project(cam.K, cam.dist, R, t, corners)
    # borrar la decodificación encima del tablero (la pantalla no refleja el gray code)
    hull = cv2.convexHull(cpx.astype(np.float32)).astype(np.int32).reshape(-1, 1, 2)
    kill = np.zeros(dec.px.shape, np.uint8); cv2.fillPoly(kill, [hull], 255)
    dec.px[kill > 0] = INVALID; dec.py[kill > 0] = INVALID
    q_local, ok_local = calib.corner_projector_coords(dec, cpx, fallback_plane=False)
    assert ok_local.sum() < 6
    q, ok = calib.corner_projector_coords(dec, cpx, fallback_plane=True)
    truth = project(pro.K, pro.dist, pro.R @ R, pro.R @ t + pro.t, corners)
    assert ok.all() and np.abs(q - truth).max() < 0.5
