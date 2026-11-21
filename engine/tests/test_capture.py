"""Captura guiada sin hardware: una cámara falsa que muestra el tablero en poses sucesivas."""

import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import test_calib as T                       # noqa: E402
from slscan import capture                   # noqa: E402


class FakeCamera:
    """Entrega la misma imagen durante `hold` lecturas (tablero quieto) y luego cambia de pose."""

    def __init__(self, poses, hold=12, name="cam0"):
        # como una foto real: el blanco del papel no satura (0.8 * 255)
        self.frames = [(T.render_board(T.K_TRUE, R, t) * 0.8).astype(np.uint8) for R, t in poses]
        self.hold = hold; self.k = 0; self.name = name; self.index = 0

    @property
    def size(self):
        return (self.frames[0].shape[1], self.frames[0].shape[0])

    def read(self):
        f = self.frames[min(self.k // self.hold, len(self.frames) - 1)]
        self.k += 1
        return f

    def flush(self, n=3):
        pass

    def close(self):
        pass


def test_guided_capture_saves_views(tmp_path, monkeypatch):
    calls = {"imshow": 0}
    monkeypatch.setattr(cv2, "namedWindow", lambda *a, **k: None)
    monkeypatch.setattr(cv2, "destroyWindow", lambda *a, **k: None)
    monkeypatch.setattr(cv2, "imshow", lambda *a, **k: calls.__setitem__("imshow", calls["imshow"] + 1))
    monkeypatch.setattr(cv2, "waitKey", lambda ms=0: 255 if calls["imshow"] < 400 else ord("q"))
    monkeypatch.setattr(capture.time, "time", lambda: calls["imshow"] * 0.1)   # 10 "fps" simulados
    poses = T.board_poses(8, seed=4, dist_mm=(450, 650))
    cam = FakeCamera(poses, hold=12)
    n = capture.guided_board_capture([cam], T.BOARD, tmp_path, target=6, stable_s=0.5)
    assert n >= 4
    views = sorted(tmp_path.glob("view_*/cam0.png"))
    assert len(views) == n
    img = cv2.imread(str(views[0]))
    assert T.BOARD.detect(img) is not None


def test_coverage_grid():
    g = capture.CoverageGrid((1200, 800), cells=(4, 2))
    pts = np.array([[10.0, 10.0], [1190.0, 790.0]])
    assert g.new_cells(pts) == 2
    g.add(pts)
    assert g.new_cells(pts) == 0 and abs(g.fraction() - 2 / 8) < 1e-9


def test_displays_listed():
    d = capture.list_displays()
    assert d and d[0]["w"] > 0
