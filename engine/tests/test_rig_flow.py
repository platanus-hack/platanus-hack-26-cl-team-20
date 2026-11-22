"""Flujo completo `calibrate_rig` sobre una carpeta de capturas sintéticas (2 cámaras + proyector)."""

import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import test_calib as T                       # noqa: E402
from slscan import calib                     # noqa: E402
from slscan.rig import Device, Rig           # noqa: E402
from slscan.viewer import write_html         # noqa: E402


def test_calibrate_rig_end_to_end(tmp_path):
    cam0 = Device("cam0", "camera", T.CAM_SIZE, T.K_TRUE, np.zeros(5))
    R1, _ = cv2.Rodrigues(np.array([0.0, -0.45, 0.0]))     # a la izquierda, mirando hacia +x
    c1 = np.array([-350.0, 0.0, 60.0])
    cam1 = Device("cam1", "camera", T.CAM_SIZE, T.K_TRUE, np.zeros(5), R1, -(R1 @ c1))
    pro = T._proj_device()
    caps = tmp_path / "captures"
    n_ok = 0
    for i, (R, t) in enumerate(T.board_poses(16, seed=21, dist_mm=(650, 800))):
        for cam in (cam0, cam1):
            Rc, tc = cam.R @ R, cam.R @ t + cam.t          # tablero -> cámara
            img = T.render_board(cam.K, Rc, tc)
            if T.BOARD.detect(img) is None:
                continue
            folder = caps / f"pose_{i:04d}" / cam.name
            folder.mkdir(parents=True)
            cv2.imwrite(str(folder / "board.png"), img)
            # el proyector expresado en el marco de esta cámara (synthetic_decoded trabaja ahí)
            Rrel = pro.R @ cam.R.T
            pro_rel = Device("proj", "projector", pro.size, pro.K, pro.dist, Rrel, pro.t - Rrel @ cam.t)
            T.synthetic_decoded(cam, pro_rel, Rc, tc).save(folder / "map.npz")
            n_ok += 1
    assert n_ok >= 16
    # intrínsecos de una sesión dedicada (en el flujo real: calib-intrinsics con 25-30 vistas)
    intr = tmp_path / "intr"; intr.mkdir()
    for cam in (cam0, cam1):
        (intr / f"{cam.name}.json").write_text(json.dumps(Device(cam.name, "camera", cam.size, cam.K, cam.dist).to_json()))
    log = []
    rig = calib.calibrate_rig(caps, T.BOARD, proj_size=pro.size, intrinsics_dir=intr, log=log.append)
    assert set(rig.devices) == {"cam0", "cam1", "proj"} and rig.world == "cam0"
    for name, truth in (("cam1", cam1), ("proj", pro)):
        d = rig[name]
        assert np.linalg.norm(d.center_world() - truth.center_world()) < 3.0, (name, log)
        dR = d.R @ truth.R.T
        ang = np.degrees(np.arccos(np.clip((np.trace(dR) - 1) / 2, -1, 1)))
        assert ang < 0.2, (name, ang)
        assert rig.extra["rms_px"][name] < 0.6, (name, rig.extra["rms_px"])
    assert abs(rig["proj"].K[0, 0] / pro.K[0, 0] - 1) < 0.03
    # formato y visor
    rig.save(tmp_path / "rig.json")
    back = Rig.load(tmp_path / "rig.json")
    assert np.allclose(back["proj"].K, rig["proj"].K)
    html = write_html(tmp_path / "rig.html", [], back, title="rig")
    assert html.stat().st_size > 1000
    r = calib.compare_rigs(rig, back)
    assert all(v["pos_mm"] < 1e-6 for v in r.values())
