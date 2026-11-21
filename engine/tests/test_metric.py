"""Tests sintéticos de la escala métrica con hoja carta (slscan.metric), sin hardware.

Escena: una cartulina con una hoja carta pegada, en varias poses frente a una cámara tipo Osmo (vertical) y un
proyector conocidos (el de test_calib). Se renderizan la foto con luz blanca, la foto con el ChArUco proyectado y
el mapa gray code ideal (en bloques de 4 px, como el escaneo real con --skip-bits 2), y se comprueba que la
calibración recupera los intrínsecos y la pose del proyector; además la malla y la detección de la hoja.
"""

import numpy as np
import cv2
import pytest

from slscan import metric
from slscan.calib import CharucoBoard
from slscan.graycode import Decoded, INVALID
from slscan.rig import Device, Rig

from test_calib import _proj_device, project

CAM = Device("osmo", "camera", (720, 1280), np.array([[950.0, 0, 366.0], [0, 950.0, 628.0], [0, 0, 1]]), np.zeros(5))
PRO = _proj_device()                                  # 1280x800, f 1700, centro (640, 520), a 250 mm a la izquierda
BOARD = CharucoBoard(7, 5, 40.0, 30.0)
CARD = (-150.0, -110.0, 366.0, 390.0)                 # cartulina ~52x50 cm (x0, y0, x1, y1) en mm del plano de la hoja
PAPER_CENTER = np.array([metric.PAPER_MM[0] / 2, metric.PAPER_MM[1] / 2, 0.0])

# poses del plano (rx, ry, rz en rad; centro de la hoja en mm, marco de la cámara)
POSES = [((0.38, 0.10, 0.04), (25.0, -55.0, 760.0)),
         ((-0.30, 0.30, -0.06), (10.0, -70.0, 720.0)),
         ((0.12, -0.42, 0.10), (40.0, -40.0, 800.0)),
         ((-0.20, -0.25, -0.12), (0.0, -60.0, 690.0))]


def plane_pose(rot, center):
    R, _ = cv2.Rodrigues(np.array(rot, np.float64))
    return R, np.array(center, np.float64) - R @ PAPER_CENTER


def H_plane_to(dev: Device, R, t) -> np.ndarray:
    """mm del plano (x, y) -> px del dispositivo, para el plano en pose (R, t) respecto a la cámara (mundo)."""
    Rd, td = dev.R @ R, dev.R @ t + dev.t
    H = dev.K @ np.column_stack([Rd[:, 0], Rd[:, 1], td])
    return H / H[2, 2]


def _apply(H, x, y):
    d = H[2, 0] * x + H[2, 1] * y + H[2, 2]
    return (H[0, 0] * x + H[0, 1] * y + H[0, 2]) / d, (H[1, 0] * x + H[1, 1] * y + H[1, 2]) / d


def render(R, t, proj_img=None, ss=2, cam=CAM, pro=PRO, paper=True):
    """Foto de la cámara: albedo (hoja 0,92, cartulina 0,5) x (ambiente + luz del proyector), con supermuestreo.

    proj_img None = proyector en blanco. El px de proyector del modelo usa la convención de bordes (el píxel k
    cubre [k, k+1)), por eso se muestrea la imagen del proyector en q - 0,5.
    """
    w, h = cam.size
    Hpc, Hpp = H_plane_to(cam, R, t), H_plane_to(pro, R, t)
    Hcp, Hcm = Hpp @ np.linalg.inv(Hpc), np.linalg.inv(Hpc)
    ys, xs = np.mgrid[0:h * ss, 0:w * ss].astype(np.float64)
    cx, cy = (xs + 0.5) / ss - 0.5, (ys + 0.5) / ss - 0.5
    u, v = _apply(Hcm, cx, cy)
    x0, y0, x1, y1 = CARD
    albedo = np.where((u >= x0) & (u <= x1) & (v >= y0) & (v <= y1), 0.5, 0.0)
    if paper:
        pw, ph = metric.PAPER_MM
        albedo = np.where((u >= 0) & (u <= pw) & (v >= 0) & (v <= ph), 0.92, albedo)
    qx, qy = _apply(Hcp, cx, cy)
    inside = (qx >= 0) & (qx < pro.size[0]) & (qy >= 0) & (qy < pro.size[1])
    if proj_img is None:
        light = inside.astype(np.float64)
    else:
        light = cv2.remap(proj_img.astype(np.float32), (qx - 0.5).astype(np.float32), (qy - 0.5).astype(np.float32),
                          cv2.INTER_LINEAR, borderValue=0) / 255.0 * inside
    img = 255 * albedo * (0.08 + 0.92 * light) + 6
    img = img.reshape(h, ss, w, ss).mean(axis=(1, 3))
    return cv2.cvtColor(np.clip(img, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)


def decoded(R, t, skip_bits=2, cam=CAM, pro=PRO, color=None) -> Decoded:
    """Mapa cámara -> proyector ideal sobre la cartulina, cuantizado como `graycode.decode` con skip_bits."""
    w, h = cam.size
    Hpc, Hpp = H_plane_to(cam, R, t), H_plane_to(pro, R, t)
    Hcp, Hcm = Hpp @ np.linalg.inv(Hpc), np.linalg.inv(Hpc)
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float64)
    u, v = _apply(Hcm, xs, ys)
    qx, qy = _apply(Hcp, xs, ys)
    x0, y0, x1, y1 = CARD
    ok = (u >= x0) & (u <= x1) & (v >= y0) & (v <= y1)
    ok &= (qx >= 0) & (qx < pro.size[0]) & (qy >= 0) & (qy < pro.size[1])
    kx, ky = np.floor(np.where(ok, qx, 0)).astype(np.int64), np.floor(np.where(ok, qy, 0)).astype(np.int64)
    if skip_bits:
        # como graycode.decode (ya corregido): centro del bloque de 2^skip_bits px
        b = 1 << skip_bits
        kx = (kx // b) * b + b // 2
        ky = (ky // b) * b + b // 2
    px = np.where(ok, kx, INVALID).astype(np.uint16)
    py = np.where(ok, ky, INVALID).astype(np.uint16)
    col = np.zeros((h, w, 3), np.float32) if color is None else color.astype(np.float32)
    return Decoded(cam.size, px, py, col, {"skip_bits": skip_bits})


def synthetic_pose(rot, center, name=""):
    """PaperPose como la arma `metric calib` desde una carpeta: hoja en la foto blanca, mapa, ChArUco proyectado."""
    R, t = plane_pose(rot, center)
    canvas, corners_px = metric.projected_charuco(BOARD, PRO.size)
    white = render(R, t)
    board_img = render(R, t, canvas)
    dec = decoded(R, t)
    quad = metric.detect_paper(white)
    assert quad is not None, name
    plane = metric.paper_scale(quad, dec)
    ch = metric.charuco_on_plane(board_img, BOARD, corners_px, plane)
    return metric.PaperPose(plane, ch, name), (R, t)


def _rot_deg(Ra, Rb):
    dR = Ra @ Rb.T
    return float(np.degrees(np.arccos(np.clip((np.trace(dR) - 1) / 2, -1, 1))))


# ----------------------------------------------------------------------------- hoja

def test_detect_paper_on_synthetic_photo():
    R, t = plane_pose(*POSES[0])
    img = render(R, t)
    quad = metric.detect_paper(img)
    assert quad is not None
    truth = np.array(_apply(H_plane_to(CAM, R, t), *metric.paper_corners_mm().T)).T
    # mismo conjunto de esquinas (el orden lo fija paper_scale)
    d = np.linalg.norm(quad[:, None, :] - truth[None, :, :], axis=2).min(axis=1)
    assert d.max() < 0.35, d


def test_detect_paper_rejects_empty_and_finds_on_clutter():
    assert metric.detect_paper(np.full((400, 300, 3), 20, np.uint8)) is None
    img = np.full((600, 800, 3), 40, np.uint8)
    cv2.rectangle(img, (50, 50), (300, 120), (230, 230, 230), -1)              # banda blanca (proporción 3,6)
    cv2.circle(img, (690, 520), 60, (240, 240, 240), -1)                       # disco blanco
    pts = np.array([[420, 160], [600, 175], [590, 410], [405, 395]], np.int32)  # hoja en perspectiva
    cv2.fillPoly(img, [pts.reshape(-1, 1, 2)], (235, 235, 235))
    quad = metric.detect_paper(img)
    assert quad is not None
    d = np.linalg.norm(quad[:, None, :] - pts[None, :, :], axis=2).min(axis=1)
    assert d.max() < 2.0


def test_paper_scale_homographies():
    R, t = plane_pose(*POSES[1])
    plane = metric.paper_scale(metric.detect_paper(render(R, t)), decoded(R, t))
    mm = metric.paper_corners_mm()
    # el lado 0-1 es el corto: las esquinas mapean a las verdaderas (salvo el giro de 180° de un rectángulo)
    best = None
    for flip in (mm, mm[[2, 3, 0, 1]]):
        tc = np.array(_apply(H_plane_to(CAM, R, t), *flip.T)).T
        tp = np.array(_apply(H_plane_to(PRO, R, t), *flip.T)).T
        e = (np.abs(plane.quad_cam - tc).max(), np.abs(plane.quad_proj - tp).max())
        best = e if best is None or sum(e) < sum(best) else best
    assert best[0] < 0.35 and best[1] < 1.5, best
    # escala: mm por px de cámara en el centro de la hoja, contra la verdad
    ctr = metric.PAPER_MM[0] / 2, metric.PAPER_MM[1] / 2
    true_mm_cam = 1 / metric._local_scale(H_plane_to(CAM, R, t), ctr)
    assert plane.mm_per_px_cam == pytest.approx(true_mm_cam, rel=0.01)
    assert plane.fit["inliers"] > 1000 and plane.fit["rms_proj_px"] < 2.0


# ----------------------------------------------------------------------------- calibración

@pytest.fixture(scope="module")
def synthetic_poses():
    return [synthetic_pose(rot, c, f"pose_{i:02d}") for i, (rot, c) in enumerate(POSES)]


def test_charuco_adds_points(synthetic_poses):
    for pose, _ in synthetic_poses:
        obj, _, _ = pose.points()
        assert len(obj) >= 4 + 12, pose.name                     # hoja + ChArUco: 16+ puntos por pose
        assert np.abs(obj[:, 2]).max() == 0


def _check_rig(rig, f_tol=0.02, rot_tol=1.0, pos_tol=5.0):
    cam, pro = rig["osmo"], rig.projector()
    assert abs(cam.K[0, 0] / CAM.K[0, 0] - 1) < f_tol, cam.K
    assert abs(pro.K[0, 0] / PRO.K[0, 0] - 1) < f_tol, pro.K
    assert _rot_deg(pro.R, PRO.R) < rot_tol
    assert np.linalg.norm(pro.center_world() - PRO.center_world()) < pos_tol


def test_calibrate_from_paper_recovers_rig(synthetic_poses):
    poses = [p for p, _ in synthetic_poses]
    rig = metric.calibrate_from_paper(poses, CAM.size, PRO.size, cam_name="osmo", repeat=5)
    _check_rig(rig)
    rep = rig.extra["metric_report"]
    assert rep["rms_px"]["cam"] < 0.5 and rep["rms_px"]["proj"] < 2.5
    # la hoja medida con el rig calibrado: lados dentro de 1 mm
    for r in rep["paper_check"]:
        assert max(abs(e) for e in r["err_mm"]) < 1.0, r
    assert "leave_one_out_max" in rep["repeatability"] and "noise_std" in rep["repeatability"]


def test_calibrate_from_paper_three_poses(synthetic_poses):
    """El mínimo que pide el flujo de Max: 3 poses (plana-ish, inclinada a cada lado)."""
    poses = [p for p, _ in synthetic_poses[:3]]
    rig = metric.calibrate_from_paper(poses, CAM.size, PRO.size, cam_name="osmo", repeat=0)
    _check_rig(rig)


def test_calibration_fixes_paper_turned_90(synthetic_poses):
    """Con inclinación fuerte la hoja puede parecer más ancha que alta: si una pose entra con el lado largo
    como si fuera el corto, la K calibrada lo detecta (Zhang) y se recalibra."""
    poses = [p for p, _ in synthetic_poses]
    good = metric.calibrate_from_paper(poses, CAM.size, PRO.size, cam_name="osmo", repeat=0)
    assert good.extra["metric_report"]["reoriented"] == []
    poses[1] = metric.turn_pose(poses[1])
    rig = metric.calibrate_from_paper(poses, CAM.size, PRO.size, cam_name="osmo", repeat=0)
    assert rig.extra["metric_report"]["reoriented"] == [poses[1].name]
    _check_rig(rig)


def test_rig_roundtrip_json(tmp_path, synthetic_poses):
    rig = metric.calibrate_from_paper([p for p, _ in synthetic_poses], CAM.size, PRO.size, cam_name="osmo", repeat=2)
    rig.save(tmp_path / "rig.json")
    back = Rig.load(tmp_path / "rig.json")
    assert np.allclose(back.projector().K, rig.projector().K)
    assert back.extra["metric_report"]["poses"] == len(synthetic_poses)


# ----------------------------------------------------------------------------- malla y métrico

def heightfield_grid(nx=60, ny=40, step=5.0, z0=700.0):
    xs, ys = np.meshgrid(np.arange(nx) * step - nx * step / 2, np.arange(ny) * step - ny * step / 2)
    z = z0 - 40 * np.exp(-((xs - 20) ** 2 + ys ** 2) / (2 * 35.0 ** 2))       # domo de 40 mm hacia la cámara
    G = np.dstack([xs, ys, z])
    G[:3, :3] = np.nan                                                        # un hueco
    C = np.dstack([np.full_like(xs, 50), (ys - ys.min()) / np.ptp(ys) * 255, np.full_like(xs, 200)])
    return G, C


def test_grid_mesh_from_heightfield(tmp_path):
    G, C = heightfield_grid()
    mesh = metric.grid_mesh(G, C)
    assert len(mesh.faces) > 0 and len(mesh.faces) <= 2 * 59 * 39
    # normales hacia la cámara (origen): componente z negativa en el marco de la cámara
    assert (mesh.normals[:, 2] < 0).mean() > 0.99
    out = metric.write_mesh_ply(tmp_path / "m.ply", mesh)
    back = metric.read_mesh_ply(out)
    assert len(back.faces) == len(mesh.faces) and np.allclose(back.vertices, mesh.vertices)
    assert np.array_equal(back.colors, mesh.colors)
    png = tmp_path / "m.png"
    metric.render_mesh_png(mesh, png, title="test")
    assert png.exists()


def test_cloud_to_mesh_fallbacks():
    G, C = heightfield_grid()
    ok = np.isfinite(G).all(axis=2)
    xyz, col = G[ok], C[ok]
    jj, ii = np.nonzero(ok)
    pro = np.column_stack([ii * 4 + 2, jj * 4 + 2]) + (ii % 2)[:, None]       # bloques de 4 (bits bajos 2 o 3)
    m_grid = metric.cloud_to_mesh(xyz, col, pro_xy=pro, method="grid")
    assert m_grid.method == "grid" and len(m_grid.faces) > 0
    m_del = metric.cloud_to_mesh(xyz, col, method="delaunay")
    assert m_del.method == "delaunay" and len(m_del.faces) > 0
    m_auto = metric.cloud_to_mesh(xyz, col, pro_xy=pro)
    assert len(m_auto.faces) > 0 and m_auto.method in ("grid", "open3d-poisson")
    # una discontinuidad grande no se une con caras
    G2 = G.copy(); G2[:, 30:, 2] += 300
    m2 = metric.grid_mesh(G2)
    assert len(m2.faces) < len(metric.grid_mesh(G).faces) - 30


def test_relative_to_metric_with_true_rig():
    """Con el rig verdadero, las correspondencias de un plano con un domo se triangulan en mm."""
    rig = Rig({"osmo": CAM, "proj": PRO}, world="osmo")
    assert metric.relative_to_metric(np.zeros((800, 1280)), None) is None
    # escena: plano z = 760 con un domo de 50 mm hacia la cámara; rayos desde el proyector cada 8 px
    W, H = PRO.size
    step = 8
    ys, xs = np.mgrid[0:H:step, 0:W:step].astype(np.float64)
    rays = np.linalg.inv(PRO.K) @ np.vstack([xs.ravel() + 0.5, ys.ravel() + 0.5, np.ones(xs.size)])
    d = PRO.R.T @ rays
    C = PRO.center_world()
    lam = (760 - C[2]) / d[2]
    for _ in range(30):                                                       # punto fijo sobre la superficie
        X = C[:, None] + lam * d
        z = 760 - 50 * np.exp(-((X[0] - 30) ** 2 + (X[1] + 40) ** 2) / (2 * 60.0 ** 2))
        lam = (z - C[2]) / d[2]
    X = C[:, None] + lam * d
    cam_px = project(CAM.K, CAM.dist, np.eye(3), np.zeros(3), X.T)
    mapx = np.full((H, W), np.nan, np.float32); mapy = mapx.copy(); valid = np.zeros((H, W), bool)
    mapx[ys.astype(int), xs.astype(int)] = cam_px[:, 0].reshape(xs.shape)
    mapy[ys.astype(int), xs.astype(int)] = cam_px[:, 1].reshape(xs.shape)
    valid[ys.astype(int), xs.astype(int)] = True
    hrel = np.zeros((H, W), np.float32)
    hrel[ys.astype(int), xs.astype(int)] = (760 - X[2]).reshape(xs.shape) * 0.37   # "altura relativa" en otra unidad
    res = metric.relative_to_metric(hrel, rig, proj_to_cam={"mapx": mapx, "mapy": mapy, "valid": valid},
                                    step=step, offset=0.5)
    assert len(res["xyz"]) == X.shape[1]
    assert np.linalg.norm(res["xyz"] - X.T, axis=1).max() < 0.05
    assert np.nanmax(res["height_mm"]) == pytest.approx(50, abs=1.0)
    assert res["mm_per_unit"] == pytest.approx(1 / 0.37, rel=0.02)
    mesh = metric.cloud_to_mesh(res["xyz"], None, pro_xy=res["pro"], method="grid")
    assert len(mesh.faces) > 0


# ----------------------------------------------------------------------------- CLI de punta a punta

def _write_pose_folders(root, poses=POSES):
    canvas, _ = metric.projected_charuco(BOARD, PRO.size)
    for i, (rot, c) in enumerate(poses):
        R, t = plane_pose(rot, c)
        d = root / f"pose_{i:02d}" / "osmo"
        d.mkdir(parents=True)
        white = render(R, t)
        cv2.imwrite(str(d / "white.png"), white)
        cv2.imwrite(str(d / "board.png"), render(R, t, canvas))
        decoded(R, t, color=white).save(d / "map.npz")


def test_cli_metric_calib_and_mesh(tmp_path):
    from slscan import cli
    root = tmp_path / "paper_calib"
    _write_pose_folders(root)
    rig_json, rep_json, html = tmp_path / "rig.json", tmp_path / "rep.json", tmp_path / "rig.html"
    assert cli.main(["metric", "calib", str(root), "--solver", "legacy", "--proj-size", "1280x800", "--out", str(rig_json),
                     "--report", str(rep_json), "--html", str(html), "--repeat", "2"]) == 0
    rig = Rig.load(rig_json)
    _check_rig(rig)
    assert html.exists() and rep_json.exists()
    # `metric paper` sobre una pose, con su mapa
    pose0 = root / "pose_00" / "osmo"
    assert cli.main(["metric", "paper", str(pose0 / "white.png"), "--map", str(pose0 / "map.npz"),
                     "--json", str(tmp_path / "paper.json"), "--png", str(tmp_path / "paper.png")]) == 0
    # `metric mesh`: la pose 1 como escaneo (un plano inclinado) -> malla métrica sobre el plano verdadero
    scan = tmp_path / "scan"
    (scan / "osmo").mkdir(parents=True)
    (scan / "osmo" / "map.npz").write_bytes((root / "pose_01" / "osmo" / "map.npz").read_bytes())
    ply, png = tmp_path / "mesh.ply", tmp_path / "mesh.png"
    assert cli.main(["metric", "mesh", str(scan), "--rig", str(rig_json), "--out", str(ply), "--png", str(png),
                     "--method", "grid", "--html", str(tmp_path / "mesh.html")]) == 0
    mesh = metric.read_mesh_ply(ply)
    assert len(mesh.faces) > 1000 and png.exists()
    R, t = plane_pose(*POSES[1])
    dist = (mesh.vertices.astype(np.float64) - t) @ R[:, 2]                  # distancia al plano verdadero
    # sesgo = error de la calibración (~0,1 % de la profundidad); ruido = bloques de 4 px del gray code
    assert abs(dist.mean()) < 2.0 and dist.std() < 1.0, (dist.mean(), dist.std())


def test_cli_metric_capture_with_simulated_hardware(tmp_path, monkeypatch):
    """`metric capture` sin hardware: la ventana guarda lo que se proyecta y la "cámara" renderiza la cartulina con
    eso. Recorre captura -> gray code decodificado de verdad -> map.npz -> load_pose (hoja + ChArUco)."""
    from slscan import capture, cli
    R, t = plane_pose(*POSES[0])

    class FakeWindow:
        def __init__(self, size, **_):
            self.size, self.img = size, np.zeros((size[1], size[0]), np.uint8)
        def show(self, img):
            self.img = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        def solid(self, level):
            self.show(np.full((self.size[1], self.size[0]), level, np.uint8))
        def close(self):
            pass

    win = {}

    def make_window(size, **kw):
        win["w"] = FakeWindow(size, **kw)
        return win["w"]

    class FakeCamera:
        def __init__(self, index, size=None, name=None, rotate=0, downscale=1):
            self.name = name or "cam0"
        def flush(self, n=3):
            pass
        def read(self):
            return render(R, t, win["w"].img, ss=1)
        def close(self):
            pass

    monkeypatch.setattr(capture, "PatternWindow", make_window)
    monkeypatch.setattr(capture, "Camera", FakeCamera)
    monkeypatch.setattr(capture.time, "sleep", lambda s: None)
    monkeypatch.setattr("builtins.input", lambda *_: "")
    out = tmp_path / "cap"
    assert cli.main(["metric", "capture", "--legacy", "--paper-mm", "215.9x279.4", "--poses", "1", "--size", "1280x800", "--settle", "0", "--out", str(out)]) == 0
    folder = out / "pose_00" / "osmo"
    assert (folder / "map.npz").exists() and (folder / "white.png").exists() and (folder / "board.png").exists()
    assert not list(folder.glob("[[]*.png"))                                  # imágenes gray code borradas
    dec = Decoded.load(folder / "map.npz")
    truth = decoded(R, t)
    both = dec.valid & truth.valid
    assert both.sum() > 20000
    assert np.median(np.abs(dec.px[both].astype(int) - truth.px[both].astype(int))) == 0
    corners_px = np.array(__import__("json").loads((out / "projected_board.json").read_text())["corners_px"])
    pose = metric.load_pose(folder, BOARD, corners_px)
    assert pose.charuco is not None and len(pose.charuco["cam"]) >= 12
    assert pose.plane.fit["inliers"] > 1000
