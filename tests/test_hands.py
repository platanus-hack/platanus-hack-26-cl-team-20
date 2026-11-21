"""Manos y mesa táctil sin hardware: cliente WS (servidor falso), calibración (4 puntos, gray code sintético, puntos
proyectados, métrica), lógica táctil con landmarks sintéticos (down/move/up), zonas excluidas, puente al runtime y
el servicio del PC en modo --fake."""

from __future__ import annotations

import importlib.util
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np
import pytest

from playground.hands import calib
from playground.hands.bridge import TouchBridge, run_loop
from playground.hands.client import HandsClient, ws_url
from playground.hands.model import Hand, HandsFrame, palm_size, synthetic_hand
from playground.hands.touch import TouchConfig, TouchTracker, Zones

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------- utilidades

def _hand_msg(seq: int, t: float, tip=(500.0, 400.0), pinch=False) -> dict:
    lm = synthetic_hand(*tip, scale=80, pinch=pinch)
    return {"type": "hands", "t": t, "seq": seq, "size": [1920, 1080], "detector": "fake",
            "hands": [{"id": 1, "handed": "Right", "score": 0.9, "landmarks": lm.tolist(),
                       "tip_index": list(tip), "pinch": pinch}]}


class FakeWS:
    """Servidor WS falso en un puerto libre. `script(ws, n_conexion)` decide qué manda."""

    def __init__(self, script):
        from websockets.sync.server import serve
        self.connections = 0
        self.script = script
        self.server = serve(self._handler, "127.0.0.1", 0)
        self.port = self.server.socket.getsockname()[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def _handler(self, ws):
        self.connections += 1
        self.script(ws, self.connections)

    @property
    def url(self):
        return f"ws://127.0.0.1:{self.port}/"

    def close(self):
        self.server.shutdown()


def _hand(tip, scale=80.0, pinch=False, hid=1, handed="Right") -> Hand:
    lm = synthetic_hand(tip[0], tip[1], scale=scale, pinch=pinch)
    return Hand(hid, handed, lm, (float(tip[0]), float(tip[1])), pinch, 0.9)


# brio → proj: escala 0,5 y desplazamiento (la mano de 80 px en la Brio mide 40 px en el proyector)
H_TEST = np.array([[0.5, 0.0, 100.0], [0.0, 0.5, 50.0], [0.0, 0.0, 1.0]])


def _cal(**kw) -> calib.Calibration:
    ref = palm_size(synthetic_hand(0, 0, scale=80)) * 0.5          # palma apoyada en px de proyector
    return calib.Calibration(H_TEST.copy(), hand_ref=ref, **kw)


# ---------------------------------------------------------------------- cliente

def test_ws_url():
    assert ws_url("192.168.1.233") == "ws://192.168.1.233:8793/"
    assert ws_url("pc:9000") == "ws://pc:9000/"
    assert ws_url("ws://x:1/") == "ws://x:1/"


def test_client_receives_latest_and_clock_offset():
    offset = 5.0                                                      # el reloj del "PC" va 5 s adelantado

    def script(ws, n):
        for i in range(1, 4):
            ws.send(json.dumps(_hand_msg(i, time.time() + offset - 0.02, tip=(500 + i, 400))))
            time.sleep(0.02)
        t_end = time.time() + 3
        while time.time() < t_end:                                    # contesta pings como el servicio
            try:
                m = json.loads(ws.recv(timeout=0.2))
            except TimeoutError:
                continue
            except Exception:  # noqa: BLE001
                return
            if m.get("type") == "ping":
                ws.send(json.dumps({"type": "pong", "t0": m["t0"], "t": time.time() + offset}))
                ws.send(json.dumps(_hand_msg(10, time.time() + offset - 0.02)))

    srv = FakeWS(script)
    c = HandsClient(srv.url, ping_every=0.1).start()
    try:
        assert c.wait(timeout=3, after_seq=None) is not None
        t_end = time.time() + 3
        while (c.latest is None or c.latest.seq != 10) and time.time() < t_end:
            time.sleep(0.02)
        f = c.latest
        assert f.seq == 10 and len(f.hands) == 1
        h = f.hands[0]
        assert h.landmarks.shape == (21, 3) and h.handed == "Right" and not h.pinch
        assert c.clock_offset == pytest.approx(offset, abs=0.05)
        st = c.status()
        assert st["connected"] and st["latency_ms"] is not None and 0 <= st["latency_ms"] < 200
        assert len(c.hands()) == 1
    finally:
        c.close(); srv.close()


def test_client_reconnects_by_itself():
    def script(ws, n):
        ws.send(json.dumps(_hand_msg(n, time.time())))
        if n == 1:
            ws.close()                                                # el "PC" corta la primera conexión
            return
        time.sleep(1.5)

    srv = FakeWS(script)
    c = HandsClient(srv.url, ping_every=0, backoff=(0.05, 0.2)).start()
    try:
        t_end = time.time() + 5
        while (c.latest is None or c.latest.seq < 2) and time.time() < t_end:
            time.sleep(0.02)
        assert c.latest is not None and c.latest.seq >= 2
        assert c.reconnects >= 1 and srv.connections >= 2
    finally:
        c.close(); srv.close()


def test_client_survives_server_down():
    c = HandsClient("ws://127.0.0.1:9/", ping_every=0, open_timeout=0.2, backoff=(0.05, 0.1)).start()
    try:
        time.sleep(0.3)
        st = c.status()
        assert not st["connected"] and st["error"] and c.hands() == []
    finally:
        c.close()


# ---------------------------------------------------------------------- calibración

def test_homography_four_points_and_roundtrip(tmp_path):
    H = np.array([[1.1, 0.05, 30.0], [-0.02, 0.95, 12.0], [1e-5, 2e-5, 1.0]])
    brio = np.array([[100, 80], [1800, 120], [1750, 1000], [150, 950]], float)
    proj = cv2.perspectiveTransform(brio.reshape(-1, 1, 2), H).reshape(-1, 2)
    cal = calib.from_points(brio, proj)
    test = np.array([[960, 540], [300, 700]], float)
    want = cv2.perspectiveTransform(test.reshape(-1, 1, 2), H).reshape(-1, 2)
    assert np.allclose(cal.brio_to_proj(test), want, atol=1e-6)
    assert np.allclose(cal.proj_to_brio(want), test, atol=1e-6)
    assert cal.stats["err_px_max"] < 1e-6
    p = cal.save(tmp_path / "c.json")
    back = calib.Calibration.load(p)
    assert np.allclose(back.H_brio_to_proj, cal.H_brio_to_proj) and back.source == "points"
    # respaldo: archivo de 4 puntos con mm
    pts = {"brio": brio.tolist(), "proj": proj.tolist(), "mm": [[0, 0], [500, 0], [500, 300], [0, 300]]}
    (tmp_path / "pts.json").write_text(json.dumps(pts))
    cal2 = calib.Calibration.load(tmp_path / "pts.json")
    assert cal2.metric and np.allclose(cal2.brio_to_mm(brio[2:3]), [[500, 300]], atol=1e-6)
    with pytest.raises(ValueError):
        calib.from_points(brio[:3], proj[:3])


def test_from_graycode_synthetic(tmp_path):
    from slscan import graycode
    proj_size, cam_size = (128, 64), (320, 200)
    # proyector → cámara: la mesa vista en ángulo
    src = np.float32([[0, 0], [127, 0], [127, 63], [0, 63]])
    dst = np.float32([[30, 40], [290, 25], [300, 180], [20, 170]])
    H_pc = cv2.getPerspectiveTransform(src, dst)
    folder = tmp_path / "scan" / "brio"
    folder.mkdir(parents=True)
    for spec in graycode.sequence(proj_size):
        pat = graycode.pattern(proj_size, spec.axis, spec.bit, spec.inverse)
        img = cv2.warpPerspective(pat, H_pc, cam_size, flags=cv2.INTER_NEAREST)
        img = np.maximum(img, 10).astype(np.uint8)                   # algo de luz ambiente
        cv2.imwrite(str(folder / f"{spec.tag}.png"), img)
    cal = calib.from_graycode(folder, proj_size=proj_size, ransac_px=2.0)
    assert (folder / "map.npz").exists()
    assert cal.stats["n"] > 4000 and cal.stats["inlier_frac"] > 0.9
    test = np.array([[100, 80], [200, 120], [250, 60]], float)
    want = cv2.perspectiveTransform(test.reshape(-1, 1, 2), np.linalg.inv(H_pc)).reshape(-1, 2)
    assert np.abs(cal.brio_to_proj(test) - want).max() < 0.7
    assert cal.brio_size == cam_size and cal.source == "graycode"


def test_find_dot_and_grid():
    dark = np.full((240, 320), 20, np.uint8)
    yy, xx = np.mgrid[0:240, 0:320]
    blob = 200 * np.exp(-((xx - 123.4) ** 2 + (yy - 77.8) ** 2) / (2 * 3.0 ** 2))
    lit = np.clip(dark + blob, 0, 255).astype(np.uint8)
    c = calib.find_dot(dark, lit)
    assert c is not None and abs(c[0] - 123.4) < 0.3 and abs(c[1] - 77.8) < 0.3
    assert calib.find_dot(dark, dark) is None
    g = calib.dot_grid((1920, 1080), (3, 3), 160)
    assert g.shape == (9, 2) and tuple(g[0]) == (160, 160) and tuple(g[-1]) == (1759, 919)


def test_metric_from_paper_quad(tmp_path):
    # hoja carta vertical en px de proyector (como en segments.json), esquinas en orden arbitrario
    quad = [[1649.1, 550.5], [1364.5, 541.6], [1374.2, 230.1], [1658.8, 239.0]]
    H = calib.mm_from_quad(quad)
    q = calib.order_quad(quad)
    mm = cv2.perspectiveTransform(q.reshape(-1, 1, 2), H).reshape(-1, 2)
    assert np.allclose(mm, [[0, 0], [215.9, 0], [215.9, 279.4], [0, 279.4]], atol=1e-3)
    (tmp_path / "segments.json").write_text(json.dumps({"papel": {"quad": quad, "mm": [215.9, 279.4]}}))
    assert np.allclose(calib.mm_from_segments(tmp_path / "segments.json"), H)


def test_metric_from_rig(tmp_path):
    from slscan.rig import Device, Rig
    K = np.array([[1500.0, 0, 960], [0, 1500.0, 540], [0, 0, 1]])
    # proyector a 1 m sobre la mesa (z = 0), mirando hacia abajo: mundo → proyector = rotación de 180° en x
    R = np.diag([1.0, -1.0, -1.0])
    c = np.array([0.0, 0.0, 1000.0])
    proj = Device("proj0", "projector", (1920, 1080), K, np.zeros(5), R, -R @ c)
    Rig({"proj0": proj}, world="mesa").save(tmp_path / "rig.json")
    H = calib.mm_from_rig(tmp_path / "rig.json")
    mm = cv2.perspectiveTransform(np.array([[[960.0, 540.0]], [[960.0 + 150, 540.0]]]), H).reshape(-1, 2)
    assert np.allclose(mm[0], [0, 0], atol=1e-3)
    assert np.allclose(mm[1], [100.0, 0.0], atol=1e-3)              # 150 px a f = 1500 y 1 m → 100 mm


# ---------------------------------------------------------------------- lógica táctil

def _run(tr, frames, t0=100.0, dt=1 / 30):
    evs = []
    for i, hands in enumerate(frames):
        evs += tr.update(hands, t0 + i * dt)
    return evs


def test_touch_apoyo_down_move_up_with_jitter():
    tr = TouchTracker(_cal(), TouchConfig(mode="apoyo", ema_alpha=1.0))
    tip = np.array([600.0, 500.0])
    frames = [[_hand(tip, scale=100)]] * 6                            # flotando: palma 25 % más grande
    frames += [[_hand(tip, scale=80)]] * 3                            # apoyada
    frames += [[_hand(tip + (dx, 0), scale=80)] for dx in (10, 20, 30, 40)]          # arrastre (5 px de proyector por paso)
    frames += [[_hand(tip + (40 + j, j), scale=80)] for j in (1, -1, 1, -1, 1)]     # temblor de 1 px de Brio
    frames += [[_hand(tip + (40, 0), scale=100)]] * 3                 # se levanta
    evs = _run(tr, frames)
    states = [e.state for e in evs]
    assert states == ["down", "move", "move", "move", "move", "up"]
    down, up = evs[0], evs[-1]
    assert (down.x, down.y) == pytest.approx((100 + 600 * 0.5, 50 + 500 * 0.5))
    assert down.trigger == "apoyo" and down.ratio == pytest.approx(1.0, abs=1e-6)
    assert (up.x, up.y) == pytest.approx((100 + 640 * 0.5, 300.0))
    assert down.id == up.id == "t1" and down.mm is None
    assert evs[0].handed == "Right" and evs[0].brio == (600.0, 500.0)


def test_touch_hysteresis_band_keeps_state():
    tr = TouchTracker(_cal(), TouchConfig(mode="apoyo", frames_down=1, frames_up=1))
    tip = (600.0, 500.0)
    evs = _run(tr, [[_hand(tip, scale=80 * 1.09)]] * 3)                # entre ratio_down y ratio_up: no baja
    assert evs == []
    evs = _run(tr, [[_hand(tip, scale=80)]], t0=101) + _run(tr, [[_hand(tip, scale=80 * 1.09)]] * 3, t0=102)
    assert [e.state for e in evs] == ["down"]                          # en la banda: sigue apoyada
    evs = _run(tr, [[_hand(tip, scale=80 * 1.2)]], t0=103)
    assert [e.state for e in evs] == ["up"]


def test_touch_pinch_mode_and_metric():
    cal = _cal(H_proj_to_mm=np.diag([0.8, 0.8, 1.0]))
    tr = TouchTracker(cal, TouchConfig(mode="pinch", ema_alpha=1.0))
    tip = (700.0, 300.0)
    frames = [[_hand(tip, scale=110)]] * 2 + [[_hand(tip, scale=110, pinch=True)]] * 3 + [[_hand(tip, scale=110)]] * 3
    evs = _run(tr, frames)
    assert [e.state for e in evs] == ["down", "up"]
    assert evs[0].trigger == "pinch" and evs[0].mm == pytest.approx((450 * 0.8, 200 * 0.8))
    args = evs[0].to_args("proj0")
    assert args["state"] == "down" and args["mm"] == [360.0, 160.0] and args["projector"] == "proj0"


def test_touch_pinch_from_landmarks():
    tr = TouchTracker(_cal(), TouchConfig(mode="pinch", pinch_source="landmarks", frames_down=1, frames_up=1))
    h_on = _hand((700, 300), pinch=True)
    h_on.pinch = False                                                 # el servicio no lo marcó: se recalcula
    evs = _run(tr, [[h_on], [_hand((700, 300))]])
    assert [e.state for e in evs] == ["down", "up"]


def test_touch_lost_hand_releases():
    tr = TouchTracker(_cal(), TouchConfig(mode="apoyo", lost_s=0.2))
    evs = _run(tr, [[_hand((600, 500))]] * 3)
    assert [e.state for e in evs] == ["down"]
    assert tr.update([], 100.2) == []                                  # perdida hace poco: espera
    evs = tr.update([], 100.5)
    assert [e.state for e in evs] == ["up"] and evs[0].reason == "mano perdida" and tr.active() == {}


def test_touch_auto_reference():
    cal = _cal()
    cal.hand_ref = None
    tr = TouchTracker(cal, TouchConfig(mode="apoyo", auto_ref_min=10))
    _run(tr, [[_hand((600, 500), scale=s)] for s in [100, 120, 80, 81, 79, 110, 90, 80, 125, 80, 80, 80]])
    assert tr.hand_ref == pytest.approx(palm_size(synthetic_hand(0, 0, scale=80)) * 0.5, rel=0.02)


def test_two_hands_two_touches():
    tr = TouchTracker(_cal(), TouchConfig(mode="apoyo"))
    frames = [[_hand((400, 400), hid=1), _hand((1200, 600), hid=2, handed="Left")]] * 3
    evs = _run(tr, frames)
    assert sorted((e.state, e.hand) for e in evs) == [("down", "1"), ("down", "2")]
    assert len(tr.active()) == 2 and evs[0].id != evs[1].id


# ---------------------------------------------------------------------- zonas excluidas

def _workbench(tmp_path) -> Path:
    data = {"mesa": {"bbox": [0, 0, 1920, 1080], "area_px": 1},
            "cocodrilo": {"bbox": [380, 280, 100, 60], "contour": [[380, 280], [480, 280], [480, 340], [380, 340]]},
            "buda": {"quad": [[700, 100], [800, 100], [800, 200], [700, 200]], "bbox": [700, 100, 100, 100]},
            "torre": {"bbox": [100, 600, 50, 300]},
            "teclado": {"bbox": [828, 709, 877, 81], "pitch_px": 33.7, "teclas_blancas": [{"i": 0}]}}
    p = tmp_path / "workbench.json"
    p.write_text(json.dumps(data))
    return p


def test_zones_from_file(tmp_path):
    z = Zones.from_file(_workbench(tmp_path), margin=0)
    assert sorted(z.polys) == ["buda", "cocodrilo", "teclado", "torre"]        # la mesa no se excluye
    assert z.contains((430, 310)) == "cocodrilo" and z.contains((750, 150)) == "buda"
    assert z.contains((120, 700)) == "torre" and z.contains((1000, 400)) is None
    z2 = Zones.from_file(_workbench(tmp_path), names=["buda"], margin=10)
    assert sorted(z2.polys) == ["buda"] and z2.contains((805, 150)) == "buda" and z2.contains((430, 310)) is None


def test_touch_excluded_zone(tmp_path):
    zones = Zones.from_file(_workbench(tmp_path), margin=0)
    tr = TouchTracker(_cal(), TouchConfig(mode="apoyo", ema_alpha=1.0), zones)
    # proj (430, 310) ⇔ brio ((430-100)/0.5, (310-50)/0.5) = (660, 520): la mano apoyada en el cocodrilo
    on_croc = (660.0, 520.0)
    frames = [[_hand(on_croc)]] * 4 + [[_hand((700.0, 520.0))]] * 3 + [[_hand(on_croc, scale=110)]] * 3
    assert _run(tr, frames) == []                                     # ni down, ni move, ni up
    # en la mesa sí, y un arrastre que entra al cocodrilo sigue
    tr2 = TouchTracker(_cal(), TouchConfig(mode="apoyo", ema_alpha=1.0), zones)
    frames = [[_hand((900.0, 520.0))]] * 3 + [[_hand(on_croc)]] + [[_hand(on_croc, scale=110)]] * 3
    assert [e.state for e in _run(tr2, frames)] == ["down", "move", "up"]


# ---------------------------------------------------------------------- puente al runtime

class _FakeRT:
    def __init__(self, has_touch: bool):
        self.has_touch = has_touch
        self.commands, self.events = [], []

    def command(self, cmd, args):
        if not self.has_touch:
            raise KeyError(f"comando desconocido: {cmd}")
        self.commands.append((cmd, args))
        return {"ok": True}

    def emit(self, type_, **data):
        self.events.append((type_, data))


def _events():
    tr = TouchTracker(_cal(), TouchConfig(mode="apoyo"))
    return _run(tr, [[_hand((600, 500))]] * 3 + [[_hand((600, 500), scale=110)]] * 3)


def test_bridge_object_with_and_without_touch_command():
    evs = _events()
    rt = _FakeRT(has_touch=True)
    b = TouchBridge(rt, projector="proj0")
    for e in evs:
        b.send(e)
    assert [a["state"] for c, a in rt.commands] == ["down", "up"] and rt.commands[0][0] == "touch"
    rt2 = _FakeRT(has_touch=False)
    b2 = TouchBridge(rt2)
    for e in evs:
        b2.send(e)
    assert [d["state"] for t, d in rt2.events] == ["down", "up"] and b2.unsupported and b2.fallback_emit == 2


def test_bridge_with_real_runtime(tmp_path):
    from playground.runtime import Runtime
    rt = Runtime(canvas_size=(640, 320), requests_dir=tmp_path / "requests")
    b = TouchBridge(rt)
    for e in _events():
        b.send(e)
    touches = [ev for ev in rt.events if ev["type"] == "touch"]
    assert [t["state"] for t in touches] == ["down", "up"] and touches[0]["x"] == pytest.approx(400.0)


def test_bridge_http(tmp_path):
    got = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            got.append(body)
            if len(got) == 1:                                         # primero: runtime sin el comando
                out, code = {"ok": False, "error": "'comando desconocido: touch'"}, 400
            else:
                out, code = {"ok": True, "result": {}}, 200
            data = json.dumps(out).encode()
            self.send_response(code); self.send_header("Content-Length", str(len(data))); self.end_headers()
            self.wfile.write(data)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        b = TouchBridge(f"http://127.0.0.1:{srv.server_address[1]}/", projector="proj1")
        evs = _events()
        b.send(evs[0])
        b.flush()
        assert b.unsupported and b.errors == 1
        b.send(evs[1])
        b.close()
        assert [g["cmd"] for g in got] == ["touch", "touch"]
        assert got[0]["args"]["state"] == "down" and got[0]["args"]["projector"] == "proj1"
        assert {"id", "state", "x", "y", "mm", "hand", "t"} <= set(got[0]["args"])
        assert b.sent == 1 and not b.unsupported
    finally:
        srv.shutdown()


class _ScriptClient:
    """Cliente falso para run_loop: entrega cuadros preparados."""
    clock_offset = 0.0

    def __init__(self, frames):
        self.frames = list(frames)

    def wait(self, timeout=1.0, after_seq=None):
        if not self.frames:
            time.sleep(0.01)
            return None
        return self.frames.pop(0)

    def to_local_time(self, t):
        return t

    def status(self):
        return {"fake": True}


def test_run_loop_end_to_end():
    now = time.time()
    frames = [HandsFrame(now - 0.03, i, [_hand((600, 500), scale=80 if 2 <= i < 8 else 110)], received=now + i / 30)
              for i in range(12)]
    rt = _FakeRT(has_touch=True)
    stats = run_loop(_ScriptClient(frames), TouchTracker(_cal(), TouchConfig(mode="apoyo")), TouchBridge(rt),
                     seconds=0.3)
    assert stats["events"]["down"] == 1 and stats["events"]["up"] == 1 and stats["frames"] == 12
    assert stats["latency_ms_median"] is not None and stats["latency_ms_median"] >= 25
    assert [a["state"] for _, a in rt.commands] == ["down", "up"]


# ---------------------------------------------------------------------- servicio del PC (modo --fake)

def _service_module():
    spec = importlib.util.spec_from_file_location("hands_service", ROOT / "windows" / "hands_service.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_service_helpers():
    hs = _service_module()
    assert hs.is_lan("192.168.1.40") and hs.is_lan("10.0.0.3") and hs.is_lan("127.0.0.1") and hs.is_lan("::1")
    assert hs.is_lan("100.101.102.103") and hs.is_lan("::ffff:192.168.1.9")
    assert not hs.is_lan("8.8.8.8") and not hs.is_lan("nada")
    tr = hs.HandTracker()
    a = synthetic_hand(500, 500, scale=80)
    b = synthetic_hand(1400, 500, scale=80)
    out = tr.update([(a, "Right", 0.9), (b, "Left", 0.9)], (1920, 1080), 1.0)
    ids = {o["handed"]: o["id"] for o in out}
    out2 = tr.update([(b + (5, 0, 0), "Left", 0.9), (a + (8, 3, 0), "Right", 0.9)], (1920, 1080), 1.03)
    assert {o["handed"]: o["id"] for o in out2} == ids                # mismas ids aunque cambie el orden
    assert len(out2[0]["landmarks"]) == 21 and out2[0]["tip_index"] == [round(1400 + 5, 1), 500.0]
    p = tr.update([(synthetic_hand(500, 500, pinch=True), "Right", 0.9)], (1920, 1080), 1.06)
    assert p[0]["pinch"] and p[0]["id"] == ids["Right"]


def test_service_fake_end_to_end():
    hs = _service_module()
    a = hs.build_parser().parse_args(["--fake", "--host", "127.0.0.1", "--ws-port", "0", "--http-port", "0"])
    svc = hs.Service(a).start()
    c = HandsClient(f"ws://127.0.0.1:{svc.ws_port}/", ping_every=0.2)
    try:
        c.start()
        f = c.wait(timeout=5)
        assert f is not None and f.detector == "fake" and f.size == (1920, 1080)
        assert len(f.hands) == 1 and f.hands[0].landmarks.shape == (21, 3) and f.hands[0].handed == "Right"
        base = f"http://127.0.0.1:{svc.http_port}"
        health = json.loads(urllib.request.urlopen(f"{base}/health", timeout=5).read())
        assert health["ok"] and health["camera"]["status"] == "ok" and health["detector"]["name"] == "fake"
        assert health["camera_open"] and health["frame"] == [1920, 1080]      # contrato de windows/camserve.py
        r = urllib.request.urlopen(f"{base}/frame.jpg?w=960", timeout=5)
        assert int(r.headers["X-Frame-Seq"]) > 0
        jpg = cv2.imdecode(np.frombuffer(r.read(), np.uint8), cv2.IMREAD_COLOR)
        assert jpg.shape == (540, 960, 3)
        bad = urllib.request.Request(f"{base}/frame.jpg?q=nada")
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(bad, timeout=5)
        assert e.value.code == 400
        cam = calib.RemoteCamera(base, fresh=1)
        png = cam.read()
        assert png.shape == (1080, 1920)                              # gris y sin pérdida para calibrar
        hands = json.loads(urllib.request.urlopen(f"{base}/hands.json", timeout=5).read())
        assert hands["type"] == "hands"
        t_end = time.time() + 3
        while c.clock_offset is None and time.time() < t_end:
            time.sleep(0.05)
        assert c.clock_offset is not None and abs(c.clock_offset) < 0.05     # mismo reloj
    finally:
        c.close()
        svc.stop()
