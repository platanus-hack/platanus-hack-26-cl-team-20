"""Cocodrilo vivo y sonido por la mesa, sin hardware: rigging de patas desde el contorno, aleteo, respiración,
escamas por zona, efecto en el runtime (contorno vivo de la escena, reload_scene), marcas de toques, salida de
notas por UDP al PC (receptor windows/midi_play.py), sintetizador del Mac y cliente HTTP del lazo en vivo."""

from __future__ import annotations

import importlib.util
import json
import math
import socket
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from playground.touchsynth import music, sound
from playground.touchsynth.alive import CrocAlive, CrocRig
from playground.touchsynth.registry import registry_of, touch_state
from playground.touchsynth.scan import ScanData, fallback_croc

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def fake_midi():
    m = music.FakeMidi()
    music.set_midi(m)
    yield m
    music.set_midi(None)


def legged_croc() -> np.ndarray:
    """Cuerpo alargado (cabeza ancha a la derecha) con 4 patas: 2 abajo y 2 arriba."""
    m = np.zeros((1080, 1920), np.uint8)
    cv2.ellipse(m, (950, 550), (300, 45), 0, 0, 360, 255, -1)
    cv2.ellipse(m, (1200, 550), (70, 60), 0, 0, 360, 255, -1)                 # cabeza
    for x, y0, y1 in ((870, 580, 640), (1080, 580, 640), (880, 460, 520), (1090, 460, 520)):
        cv2.rectangle(m, (x - 14, y0), (x + 14, y1), 255, -1)
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    return max(cnts, key=cv2.contourArea).reshape(-1, 2).astype(np.float32)


# ---------------------------------------------------------------------- rigging

def test_rig_finds_four_legs_with_roots_on_the_body():
    rig = CrocRig(legged_croc(), n_zones=10)
    assert len(rig.legs) == 4
    assert sorted(g.side for g in rig.legs) == [-1, -1, 1, 1]
    for g in rig.legs:
        rx, ry = g.root[0] + rig.x0, g.root[1] + rig.y0
        assert min(abs(rx - x) for x in (870, 880, 1080, 1090)) < 25           # en el tramo de la pata
        assert 480 < ry < 620                                                  # sobre el borde del cuerpo
        assert 400 < g.area < 3000 and not (g.mask & rig.body).any()
    assert rig.n_cells > 50 and (rig.label >= 0).sum() == rig.mask.sum()
    xs = [c[0] for c in rig.z.centers]
    assert xs[0] > xs[-1]                                                     # zona 0 = cabeza (derecha)


def test_rig_without_legs_still_has_scales():
    rig = CrocRig(fallback_croc())
    assert rig.legs == [] and rig.n_cells > 30 and rig.body.sum() == rig.mask.sum()


def test_flap_breath_and_scales(fake_midi):
    a = CrocAlive(params={"scan": "/no/existe"})
    a.set_contour(legged_croc())
    assert len(a.rig.legs) == 4
    idle = [abs(a.leg_angle(i, 10.0 + k * 0.1)) for i in range(4) for k in range(30)]
    assert max(idle) <= a.params["idle_deg"] + 1e-6
    x, y = a.resolve(1)
    zone = a.rig.legs[1].zone
    a.trigger(x, y, zone, 100.0)
    peak = [max(abs(a.leg_angle(i, 100.0 + k * 0.01)) for k in range(40)) for i in range(4)]
    assert peak[1] > 18 and peak[1] > max(peak[j] for j in (0, 2, 3))         # la más cercana aletea más
    assert max(abs(a.leg_angle(1, 108.0 + k * 0.1)) for k in range(10)) <= a.params["idle_deg"] + 0.5   # se apaga
    a.step(100.0)
    assert a.glow[zone] == pytest.approx(1.0) and a.glow.sum() == pytest.approx(1.0)
    b = a._cell_brightness(100.0)
    lit = b[a.rig.cell_zone == zone].mean()
    assert lit > b[a.rig.cell_zone != zone].mean() + 0.3                      # la zona tocada brilla
    a.step(105.0)
    assert a.glow[zone] < 0.02

    class S:
        audio = {"live": True, "bass": 1.0}
        beat = {"playing": False}
    for _ in range(40):
        a.update_breath(S, 1.0)
    assert a.breath_now == pytest.approx(1.02, abs=0.002) and a.breath_src_used == "audio"
    S.audio = {"live": False}
    S.beat = {"playing": True, "phase": 0.5}
    assert a.update_breath(S, 1.0) == pytest.approx(0.98) and a.breath_src_used == "beat"
    S.beat = {"playing": False}
    vals = [a.update_breath(S, t) for t in np.linspace(0, 5, 50)]
    assert 0.979 < min(vals) and max(vals) < 1.021 and a.breath_src_used == "lento"


def test_legs_move_in_the_render_body_stays():
    a = CrocAlive(params={"scan": "/no/existe", "idle_deg": 0.0, "shimmer": 0.0, "ripple": False})
    a.set_contour(legged_croc())
    a.breath_now = 1.0
    still = a.compose(10.0)
    a.trigger(*a.resolve(0), a.rig.legs[0].zone, 10.0)
    a.glow[:] = 0
    moved = a.compose(10.08)                                                  # cerca del máximo del aleteo
    diff = np.abs(moved - still).max(axis=2) > 0.05
    assert (diff & a.rig.legs[0].mask).sum() > 0.2 * a.rig.legs[0].mask.sum()
    body_core = cv2.erode(a.rig.body.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
    assert (diff & body_core).sum() < 0.02 * body_core.sum()
    img = np.zeros((1080, 1920, 3), np.uint8)
    a.render(img, 1, 1, 10.0)
    assert img[450:660, 600:1300].max() > 0 and img[:300].max() == 0


# ---------------------------------------------------------------------- en el runtime

def _scan_dir(tmp_path: Path) -> Path:
    d = tmp_path / "scan"
    d.mkdir()
    (d / "workbench.json").write_text(json.dumps({"cocodrilo": {"contour": legged_croc().tolist()}}))
    (d / "segments.json").write_text(json.dumps({}))
    return d


def test_croc_alive_in_runtime_follows_scene_and_observes_touches(tmp_path, fake_midi):
    from playground.runtime import Runtime
    from playground.scene import scene_from_scan
    d = _scan_dir(tmp_path)
    rt = Runtime(scene=scene_from_scan(d), canvas_size=(1920, 1080), demo=False, requests_dir=tmp_path / "req")
    e_syn = rt.spawn("croc_synth", None, {"visual": False})["id"]
    e_alive = rt.spawn("croc_alive")["id"]
    rt.tick(0.03)
    reg = registry_of(rt.scene)
    alive, synth = reg.instruments[e_alive], reg.instruments[e_syn]
    assert len(alive.rig.legs) == 4 and alive.rig.x0 < 700 < alive.rig.x0 + alive.rig.w
    rt.command("touch", {"state": "down", "target": "croc:2", "id": "a"})
    rt.tick(0.03)
    assert synth.held == {"a": 2} and fake_midi.messages[-1][0] in ("note_on", "control_change")   # suena
    rt.command("touch", {"state": "up", "target": "croc:2", "id": "a"})
    assert alive.glow.max() > 0.5 and max(t0 for t0, _ in alive.flaps) > 0                          # y se mueve
    frame = rt.tick(0.03)
    assert frame.max() > 0                                                    # croc_synth sin visual, alive dibuja
    x0 = alive.rig.x0
    rt.scene.nudge("croc", 40, 0)                                             # 'un poquito a la derecha'
    rt.tick(0.03)
    assert alive.rig.x0 == pytest.approx(x0 + 40, abs=2)
    rt.set_param(e_alive, "aletear", True)
    rt.tick(0.03)
    assert alive.status()["patas"] and alive.status()["respiracion"]["fuente"] in ("lento", "beat", "audio")
    rt.command("touch", {"state": "down", "target": "croc:4", "id": "b"})     # nota sostenida…
    rt.tick(0.03)
    rt.command("reload_scene", {})                                            # …escena nueva, mismos instrumentos
    rt.tick(0.03)
    assert registry_of(rt.scene) is reg and synth.held == {"b": 4} and "b" in touch_state(rt.scene).touches


def test_touch_marks_draws_rings_and_base_light(tmp_path):
    from playground.runtime import Runtime
    rt = Runtime(canvas_size=(1920, 1080), demo=False, requests_dir=tmp_path / "req")
    rt.spawn("touch_marks", None, {"luz_base": 20})
    rt.command("hands", {"hands": [{"id": "h1", "x": 500, "y": 400, "tip": [520, 380], "touching": True}]})
    f = rt.tick(0.03)
    assert (f[100, 100] == 20).all() and f[..., 1].min() >= 20 and tuple(f[380, 520 + 16]) == (0, 255, 0)


# ---------------------------------------------------------------------- sonido por la mesa

def _midi_play():
    spec = importlib.util.spec_from_file_location("midi_play", ROOT / "windows" / "midi_play.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakePort:
    def __init__(self):
        self.msgs = []

    def send(self, m):
        self.msgs.append(m)

    def close(self):
        pass


def test_midi_play_receiver_logic():
    mp = _midi_play()
    assert mp.is_lan("192.168.1.10") and mp.is_lan("100.101.5.6") and mp.is_lan("127.0.0.1")
    assert not mp.is_lan("8.8.8.8")
    assert mp.parse_programs("1:89,2:12") == {0: 89, 1: 12}
    port = FakePort()
    pl = mp.Player(port, "Microsoft GS Wavetable Synth", {0: 89, 1: 12})
    assert [(m.type, m.channel, m.program) for m in port.msgs] == [("program_change", 0, 89), ("program_change", 1, 12)]
    data = json.dumps({"v": 1, "m": [[0x90, 60, 100], [0x81, 64, 0], [0xB0, 1, 70], [0xF0, 1, 2]]}).encode()
    assert pl.handle(data, ("192.168.1.20", 5000)) is None
    played = port.msgs[2:]
    assert [(m.type, m.channel) for m in played] == [("note_on", 0), ("note_off", 1), ("control_change", 0)]
    assert pl.handle(data, ("8.8.8.8", 5000)) is None and pl.rejected == 1 and pl.played == 3
    reply = json.loads(pl.handle(json.dumps({"v": 1, "ping": "abc"}).encode(), ("10.0.0.5", 1)))
    assert reply["pong"] == "abc" and reply["out"].startswith("Microsoft") and reply["n"] == 3


def test_notes_travel_by_udp_to_the_pc_receiver():
    mp = _midi_play()
    port = FakePort()
    pl = mp.Player(port, "GS fake", {})
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.bind(("127.0.0.1", 0))
    srv.settimeout(0.2)
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            try:
                data, addr = srv.recvfrom(65536)
            except socket.timeout:
                continue
            r = pl.handle(data, addr)
            if r:
                srv.sendto(r, addr)

    th = threading.Thread(target=loop, daemon=True)
    th.start()
    try:
        out = music.MidiOut("pc", pc=f"127.0.0.1:{srv.getsockname()[1]}")
        out.note_on(60, 100, 1); out.cc(1, 64, 1); out.note_off(60, 1)
        t_end = time.time() + 2
        while len(port.msgs) < 3 and time.time() < t_end:
            time.sleep(0.01)
        assert [m.type for m in port.msgs] == ["note_on", "control_change", "note_off"]
        st = out.status()
        assert st["mode"] == "pc" and st["destinos"][0]["pong"]["out"] == "GS fake"
        out.close()
    finally:
        stop.set(); th.join(1); srv.close()


def test_auto_falls_back_to_the_mac_when_the_pc_does_not_answer(monkeypatch):
    class StubSynth:
        name = "MacBook (stub)"
        def __init__(self, *a, **k): self.got = []
        def send(self, *a): self.got.append(a)
        def close(self): pass
        def status(self): return {"sink": self.name}

    class StubPort:
        name = "sl-scanner touch"
        def __init__(self): self.got = []
        def send(self, *a): self.got.append(a)
        def close(self): pass
        def status(self): return {"sink": self.name}

    monkeypatch.setattr(sound, "MacSynth", StubSynth)
    monkeypatch.setattr(music.MidiOut, "_midi_port", lambda self, which: StubPort())
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:            # un puerto donde nadie contesta
        s.bind(("127.0.0.1", 0))
        dead = s.getsockname()[1]
    import sys
    out = music.MidiOut("auto", pc=f"127.0.0.1:{dead}", reprobe_s=3600,
                        ssh_cmd=[sys.executable, "-c", "import sys; sys.exit(1)"])     # ssh que falla
    out.note_on(62, 90, 2)
    st = out.status()
    assert st["mode"] == "puerto+mac" and "MacBook" in st["port"]
    assert out.sinks[1].got == [("note_on", 1, 62, 90)]
    t_end = time.time() + 5
    while not (out.search or "").startswith("sin mesa") and time.time() < t_end:
        time.sleep(0.05)
    assert out.search.startswith("sin mesa") and out.status()["mode"] == "puerto+mac"
    out.close()


def _local_receiver_cmd():
    import sys
    return [sys.executable, "-u", str(ROOT / "windows" / "midi_play.py"), "--stdin", "--out", "fake", "--programs", ""]


def test_ssh_transport_with_the_receiver_on_stdin():
    """El mismo receptor del PC, corrido local en vez de por ssh: líneas JSON por stdin, pong por stdout."""
    s = sound.SshMidi(_local_receiver_cmd(), name="receptor local")
    assert s.start(timeout=20) and s.ready["listo"] == "fake"
    s.send("note_on", 0, 60, 100)
    s.send("note_off", 0, 60, 0)
    pong = s.ping(timeout=10)
    assert pong is not None and pong["n"] == 2 and pong["out"] == "fake"
    assert any("note_on" in m for m in s.played_lines) and s.alive
    s.close()
    assert s.proc.poll() is not None and not s.alive


def test_auto_switches_from_the_mac_to_the_table_by_ssh(monkeypatch):
    class StubSynth:
        name = "MacBook (stub)"
        closed = False
        def __init__(self, *a, **k): pass
        def send(self, *a): pass
        def close(self): StubSynth.closed = True
        def status(self): return {"sink": self.name}

    class StubPort:
        name = "sl-scanner touch"
        def send(self, *a): pass
        def close(self): pass
        def status(self): return {"sink": self.name}

    monkeypatch.setattr(sound, "MacSynth", StubSynth)
    monkeypatch.setattr(music.MidiOut, "_midi_port", lambda self, which: StubPort())
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        dead = s.getsockname()[1]
    out = music.MidiOut("auto", pc=f"127.0.0.1:{dead}", ssh_cmd=_local_receiver_cmd())
    out.prepare()
    t_end = time.time() + 25
    while "pc-ssh" not in out.status()["mode"] and time.time() < t_end:
        time.sleep(0.05)
    assert out.status()["mode"] == "puerto+pc-ssh" and StubSynth.closed and out.search == "mesa por ssh"
    out.note_on(64, 100, 1)
    ssh = out.sinks[-1]
    assert ssh.ping(timeout=10)["n"] == 1
    out.close()


def test_mac_synth_renders_offline():
    syn = sound.MacSynth(start=False)
    syn.send("note_on", 0, 60, 100)
    syn.send("note_on", 1, 72, 110)
    a = syn.render(4410)
    assert np.abs(a).max() > 0.05 and np.isfinite(a).all() and np.abs(a).max() <= 1.0
    syn.send("note_off", 0, 60, 0)
    for _ in range(35):                                                     # 3,5 s: el pad suelta, la marimba se apaga
        b = syn.render(4410)
    assert np.abs(b).max() < 0.01 and syn.voices == {}


def test_sound_action_switches_output(tmp_path):
    from playground.runtime import Runtime
    rt = Runtime(canvas_size=(640, 360), demo=False, requests_dir=tmp_path / "req")
    st = rt.command("touchsynth", {"action": "sonido", "salida": "null"})
    assert st["mode"] == "nulo" and isinstance(music.get_midi(), music.MidiOut)
    assert rt.command("touchsynth", {"action": "sonido"})["mode"] == "nulo"


# ---------------------------------------------------------------------- lazo en vivo: cliente del runtime

def test_live_client_spawns_overlays_and_reads_frames(tmp_path):
    import uvicorn
    from playground.runtime import Runtime, build_app
    from playground.touchsynth.live import RuntimeClient, checkerboards
    boards = checkerboards((1920, 1080), 48)
    assert len(boards) == 4 and np.array_equal(boards[0], 255 - boards[1])
    assert (np.maximum.reduce(boards) == 255).all() and (np.minimum.reduce(boards) == 0).all()
    rt = Runtime(canvas_size=(1920, 1080), demo=False, requests_dir=tmp_path / "req")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(build_app(rt), host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        c = RuntimeClient(f"http://127.0.0.1:{port}")
        assert c.alive()
        iid = c.cmd("spawn", effect="touch_marks", params={"luz_base": 10})["id"]
        path = tmp_path / "tablero.png"
        cv2.imwrite(str(path), boards[2])
        c.cmd("overlay", path=str(path), seconds=5)
        rt.tick(0.03)
        f = c.frame()
        assert f is not None and np.array_equal(f[..., 0], boards[2])
        c.cmd("overlay", off=True)
        r = c.cmd("touchsynth", action="en_vivo", status={"fusion": {"modo": "solo-osmo"}})
        assert r == {"recalibrar": False}
        c.cmd("touchsynth", action="recalibrar")
        assert c.cmd("touchsynth", action="en_vivo", status={})["recalibrar"] is True
        assert c.cmd("kill", id=iid)["killed"] == [iid]
        with pytest.raises(RuntimeError):
            c.cmd("no_existe")
    finally:
        server.should_exit = True
        th.join(timeout=5)
