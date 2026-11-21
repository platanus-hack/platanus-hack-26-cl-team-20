import numpy as np
import cv2

from slscan import recon
from slscan.rig import Device, Rig, stereo_rectify


def _rig():
    K = np.array([[1500.0, 0, 640], [0, 1500.0, 360], [0, 0, 1]])
    cam = Device("cam0", "camera", (1280, 720), K, np.array([-0.1, 0.05, 0.001, -0.001, 0.0]))
    R, _ = cv2.Rodrigues(np.array([0.05, -0.4, 0.02]))
    pro = Device("proj", "projector", (1280, 800), np.array([[1700.0, 0, 640], [0, 1700.0, 400], [0, 0, 1]]),
                 np.array([-0.05, 0.1, 0.0, 0.0, 0.0]), R, np.array([-250.0, 10.0, 60.0]))
    return Rig({"cam0": cam, "proj": pro})


def _project(dev, X):
    rvec, _ = cv2.Rodrigues(dev.R)
    p, _ = cv2.projectPoints(X.reshape(-1, 1, 3), rvec, dev.t.reshape(3, 1), dev.K, dev.dist)
    return p.reshape(-1, 2)


def test_triangulate_world_recovers_points():
    rig = _rig(); cam, pro = rig["cam0"], rig.projector()
    rng = np.random.default_rng(1)
    X = np.column_stack([rng.uniform(-150, 150, 500), rng.uniform(-100, 100, 500), rng.uniform(700, 900, 500)])
    m = recon.Matches(_project(cam, X).astype(np.float32), _project(pro, X).astype(np.float32),
                      np.zeros((500, 3), np.float32))
    Y = recon.triangulate_pair(m, cam, pro, mode="world")
    assert np.abs(Y - X).max() < 0.05


def test_triangulate_sltk_mode_equals_world_up_to_axis_flip():
    rig = _rig(); cam, pro = rig["cam0"], rig.projector()
    rng = np.random.default_rng(2)
    X = np.column_stack([rng.uniform(-150, 150, 200), rng.uniform(-100, 100, 200), rng.uniform(700, 900, 200)])
    m = recon.Matches(_project(cam, X).astype(np.float32), _project(pro, X).astype(np.float32),
                      np.zeros((200, 3), np.float32))
    Y = recon.triangulate_pair(m, cam, pro, rect=stereo_rectify(cam, pro), mode="sltk")
    Y[:, 1] *= -1; Y[:, 2] *= -1
    assert np.abs(Y - X).max() < 0.05


def test_coalesce_and_ply_roundtrip(tmp_path):
    from slscan.graycode import Decoded, INVALID
    px = np.full((4, 6), INVALID, np.uint16); py = px.copy()
    px[1, 1:3] = 5; py[1, 1:3] = 2          # dos píxeles de cámara ven el mismo píxel de proyector (5,2)
    px[2, 4] = 0; py[2, 4] = 0
    color = np.zeros((4, 6, 3), np.float32); color[1, 1] = 100; color[1, 2] = 200; color[2, 4] = 50
    dec = Decoded((6, 4), px, py, color, {})
    m = recon.coalesce(dec)
    assert len(m.cam) == 2
    i = np.where((m.pro == [5, 2]).all(axis=1))[0][0]
    assert np.allclose(m.cam[i], [1.5, 1.0]) and np.allclose(m.color[i], 150)
    xyz = np.random.default_rng(0).normal(size=(2, 3)).astype(np.float32)
    recon.write_ply(tmp_path / "a.ply", xyz, m.color)
    back, rgb = recon.read_ply(tmp_path / "a.ply")
    assert np.allclose(back, xyz) and rgb.shape == (2, 3)
