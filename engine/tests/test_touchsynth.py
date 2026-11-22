"""Mesa táctil musical sin hardware: escalas y notas, zonas del cocodrilo, sintetizador, dibujo, marimba y zona
libre, reparto de toques, comando touch del runtime (objeto, HTTP y el puente de playground.hands por HTTP real),
linterna (normales y sombreado), detección de dedos en la Osmo (sintética) y fusión Osmo + Brio."""

from __future__ import annotations

import json
import math
import socket
import threading
import time

import cv2
import numpy as np
import pytest

from playground.touchsynth import music
from playground.touchsynth.croc import CrocSynth, CrocZones
from playground.touchsynth.fusion import BrioContact, BrioObs, Fusion, FusionConfig
from playground.touchsynth.linterna import HeightField, Light, Linterna, normals, shade
from playground.touchsynth.marimba import Marimba
from playground.touchsynth.osmo_hands import OsmoConfig, OsmoHands, OsmoTip, PovMapper, ProjectionModel
from playground.touchsynth.registry import Registry, touch_state
from playground.touchsynth.scan import ScanData, fallback_croc, largest_rect
from playground.touchsynth.sketch import Sketch


@pytest.fixture(autouse=True)
def fake_midi():
    m = music.FakeMidi()
    music.set_midi(m)
    yield m
    music.set_midi(None)


def _ev(state, x, y, tid="t1", **kw):
    return {"id": tid, "state": state, "x": float(x), "y": float(y), **kw}


def _notes(m, kind="note_on"):
    return [msg for msg in m.messages if msg[0] == kind]


# ---------------------------------------------------------------------- música

def test_scales_and_names():
    assert music.scale_notes(48, "pentatonica_mayor", 7) == [48, 50, 52, 55, 57, 60, 62]
    assert music.scale_notes(57, "pentatonica_menor", 6) == [57, 60, 62, 64, 67, 69]
    assert music.scale_notes(60, [0, 7], 3) == [60, 67, 72]
    assert music.note_name(60) == "C4" and music.note_name(69) == "A4"
    assert music.velocity_of(None, 90) == 90 and music.velocity_of(1.0) == 127 and music.velocity_of(0.0) == 1
    with pytest.raises(ValueError):
        music.scale_notes(60, "no_existe", 3)


def test_notes_min_length_refcount_and_duration(fake_midi):
    n = music.Notes(fake_midi, min_len=0.1)
    n.on(60, 100, 1, now=0.0)
    n.off(60, 1, now=0.01)                        # tap: el off espera el largo mínimo
    n.tick(0.05)
    assert not _notes(fake_midi, "note_off")
    n.tick(0.11)
    assert _notes(fake_midi, "note_off") == [("note_off", 1, 60, 0)]
    n.on(62, 90, 2, now=1.0)                      # dos toques en la misma nota: suena hasta que se sueltan ambos
    n.on(62, 90, 2, now=1.0)
    n.off(62, 2, now=1.2); n.tick(1.3)
    assert ("note_off", 2, 62, 0) not in fake_midi.messages[-1:]
    n.off(62, 2, now=1.4); n.tick(1.5)
    assert fake_midi.messages[-1] == ("note_off", 2, 62, 0)
    n.on(64, 80, 2, now=2.0, dur=0.3)             # marimba: duración fija
    n.tick(2.2); assert n.sounding == [(2, 64)]
    n.tick(2.31); assert n.sounding == []
    n.on(65, 80, 1, now=3.0); n.panic()
    assert n.sounding == [] and fake_midi.messages[-1] == ("note_off", 1, 65, 0)


def test_midiout_null_mode_never_fails():
    m = music.MidiOut("null")
    m.note_on(60, 100, 1); m.cc(1, 64, 1); m.note_off(60, 1)
    st = m.status()
    assert st["mode"] == "nulo" and st["sent"] == 3 and st["port"] is None


# ---------------------------------------------------------------------- cocodrilo

def test_croc_zones_head_is_wider_end_and_ordered():
    z = CrocZones(fallback_croc(), n=10)                       # cola a la izquierda, cabeza (ancha) a la derecha
    assert z.n == 10 and "auto" in z.head_rule
    xs = [c[0] for c in z.centers]
    assert xs[0] > xs[-1] and all(a > b for a, b in zip(xs, xs[1:]))      # cabeza → cola
    for i, (cx, cy) in enumerate(z.centers):
        assert z.zone_at(cx, cy) == i
    assert z.zone_at(100, 100) is None
    # el margen cuenta: justo afuera del borde superior sigue tocando
    tx, top_y = fallback_croc()[int(np.argmin(fallback_croc()[:, 1]))]
    assert z.zone_at(tx, top_y - 8) is not None and z.zone_at(tx, top_y - 40) is None
    left = CrocZones(fallback_croc(), n=10, head="izquierda")
    assert left.centers[0][0] < left.centers[-1][0]


def test_croc_zones_split_by_area():
    z = CrocZones(fallback_croc(), n=8, split="area")
    areas = [int(((z.label == i) & z.mask).sum()) for i in range(8)]
    assert max(areas) / min(areas) < 1.3
    zl = CrocZones(fallback_croc(), n=8, split="largo")
    areas_l = [int(((zl.label == i) & zl.mask).sum()) for i in range(8)]
    assert max(areas_l) / min(areas_l) > 1.5                    # a lo largo: la cola (fina) tiene menos área


def test_croc_synth_notes_hold_glide_cc_and_visual(fake_midi):
    c = CrocSynth(scan=ScanData.fallback("test"), params={"zones": 10, "root": 48, "scale": "pentatonica_mayor"})
    assert c.note_list[0] == 48                                 # cabeza = la más grave
    (x0, y0), (x3, y3) = c.zones.centers[0], c.zones.centers[3]
    c.handle(_ev("down", x0, y0, velocity=0.5), now=10.0)
    assert _notes(fake_midi)[-1] == ("note_on", 1, 48, 64) and c.held == {"t1": 0}
    assert any(m[0] == "control_change" for m in fake_midi.messages)
    c.step(10.5)
    assert c.glow[0] >= c.params["hold"] - 1e-6 and c.glow[5] == pytest.approx(c.params["base"])
    c.handle(_ev("move", x3, y3), now=11.0)                     # glissando a la zona 3
    c.step(11.2)
    assert ("note_off", 1, 48, 0) in fake_midi.messages and _notes(fake_midi)[-1][2] == c.note_list[3]
    c.handle(_ev("up", x3, y3), now=12.0); c.step(12.5)
    assert c.notes.sounding == [] and c.held == {}
    img = np.zeros((1080, 1920, 3), np.uint8)
    c.render(img, 1.0, 1.0, 12.5)
    bx, by, bw, bh = c.zones.bbox
    assert img[by:by + bh, bx:bx + bw].max() > 0 and img[:by - 100].max() == 0
    small = np.zeros((320, 640, 3), np.uint8)
    c.render(small, 640 / 1920, 320 / 1080, 12.5)             # otro tamaño de lienzo
    assert small.max() > 0


def test_croc_synth_ignores_touch_outside(fake_midi):
    c = CrocSynth(scan=ScanData.fallback("test"))
    c.handle(_ev("down", 50, 50), now=1.0)
    assert not _notes(fake_midi) and not c.held and not c.hit(50, 50)


# ---------------------------------------------------------------------- dibujo

def test_sketch_stroke_speed_color_fade_and_clear():
    s = Sketch({"fade_s": 2.0, "hold_s": 1.0})
    s.handle(_ev("down", 100, 100, t=0.0), now=0.0)
    for k in range(1, 11):
        s.handle(_ev("move", 100 + 20 * k, 100, t=0.05 * k), now=0.05 * k)     # 400 px/s
    s.handle(_ev("up", 300, 100), now=0.6)
    assert s.status()["trazos"] == 1 and s.status()["puntos"] == 11
    img = np.zeros((1080, 1920, 3), np.uint8)
    s.render(img, 1, 1, 0.7)
    assert img[100, 150:290].max() > 200                         # trazo a pleno
    dim = np.zeros_like(img); s.render(dim, 1, 1, 2.5)           # a mitad del desvanecimiento
    assert 0 < dim[100, 150:290].max() < img[100, 150:290].max()
    s.step(3.7)
    assert s.status()["puntos"] == 0                              # se apagó y se botó
    s.handle(_ev("down", 500, 500, t=4.0), now=4.0); s.handle(_ev("up", 500, 500), now=4.1)
    s.clear(4.2)
    assert s.status()["trazos"] == 0 and s.status()["borrados"] == 1


def test_sketch_color_modes():
    s = Sketch({"color_mode": "fijo", "color": [255, 0, 0]})
    s.handle(_ev("down", 10, 10), now=0)
    assert s.live["t1"].color == (0, 0, 255)                      # RGB rojo → BGR
    z = Sketch({"color_mode": "zona"})
    z.handle(_ev("down", 10, 10, tid="a"), now=0); z.handle(_ev("down", 1900, 10, tid="b"), now=0)
    assert z.live["a"].color != z.live["b"].color


def test_sketch_two_hands_gestures_clear():
    s = Sketch({"clear_hold_s": 1.0})
    s.handle(_ev("down", 900, 900, tid="x", t=0), now=0); s.handle(_ev("up", 900, 900, tid="x"), now=0.1)
    s.handle(_ev("down", 400, 500, tid="a", hand="1", t=1.0), now=1.0)
    s.handle(_ev("down", 1400, 500, tid="b", hand="2", t=1.0), now=1.0)
    s.step(1.5)
    assert s.clears == 0
    s.step(2.1)                                                   # quietas > 1 s → borra
    assert s.clears == 1 and s.status()["trazos"] == 0 and "quietas" in s.last_clear[1]
    s.step(3.5)
    assert s.clears == 1                                          # el mismo par no vuelve a borrar
    s2 = Sketch()
    s2.handle(_ev("down", 900, 500, tid="a", hand="1", t=0), now=0)
    s2.handle(_ev("down", 1000, 500, tid="b", hand="2", t=0), now=0)
    s2.handle(_ev("move", 600, 500, tid="a", hand="1", t=0.2), now=0.2)
    s2.handle(_ev("move", 1300, 500, tid="b", hand="2", t=0.3), now=0.3)          # se separan 600 px en 0,3 s
    assert s2.clears == 1 and "separan" in s2.last_clear[1]
    s3 = Sketch({"clear_hold_s": 1.0})                            # dos dedos de la misma mano: no borra
    s3.handle(_ev("down", 400, 500, tid="a", hand="1"), now=0); s3.handle(_ev("down", 450, 500, tid="b", hand="1"), now=0)
    s3.step(2.0)
    assert s3.clears == 0


def test_scan_croc_contour_comes_from_workbench(tmp_path):
    """El contorno del cocodrilo que vale es el de workbench.json (se afina ahí); segments.json solo de respaldo."""
    refined = [[650, 450], [1200, 450], [1200, 620], [650, 620]]
    coarse = [[700, 500], [900, 500], [900, 560], [700, 560]]
    (tmp_path / "segments.json").write_text(json.dumps({"cocodrilo": {"contour": coarse, "bbox": [700, 500, 200, 60]},
                                                       "papel": {"bbox": [1000, 100, 300, 400]}}))
    sd = ScanData.load(tmp_path)
    assert sd.croc.tolist() == coarse and sd.croc_source.startswith("segments.json")
    assert "papel" not in sd.objects and sd.height is None                 # la hoja es superficie libre
    (tmp_path / "workbench.json").write_text(json.dumps({"cocodrilo": {"contour": refined}, "torre": {"bbox": [100, 100, 200, 400]}}))
    sd = ScanData.load(tmp_path)
    assert sd.croc.tolist() == refined and sd.croc_source.startswith("workbench.json") and "torre" in sd.objects
    assert ScanData.load(tmp_path / "no_existe").source.startswith("sintético")


def test_croc_reloads_when_workbench_changes(tmp_path, fake_midi):
    import os
    (tmp_path / "workbench.json").write_text(json.dumps({"cocodrilo": {"contour": fallback_croc().tolist()}}))
    c = CrocSynth(params={"scan": str(tmp_path)})
    bbox0 = c.zones.bbox
    c.step(100.0)
    moved = (fallback_croc() + (0, 200)).tolist()                        # alguien afina/mueve el contorno
    (tmp_path / "workbench.json").write_text(json.dumps({"cocodrilo": {"contour": moved}}))
    t = time.time() + 10
    os.utime(tmp_path / "workbench.json", (t, t))
    c.step(101.0)                                                        # menos de 2 s: no mira todavía
    assert c.zones.bbox == bbox0
    c.step(103.0)
    assert c.zones.bbox[1] == bbox0[1] + 200 and c.status()["recargas"] == 1
    assert c.status()["contorno"].startswith("workbench.json")


def test_mcp_pg_touch_builds_commands(monkeypatch):
    from playground import mcp_server
    calls = []
    monkeypatch.setattr(mcp_server, "_cmd", lambda cmd, **a: calls.append((cmd, a)) or {"ok": True})
    mcp_server.pg_touch("tap", target="croc:3", velocity=0.8)
    mcp_server.pg_touch("down", x=100, y=200, id="a")
    mcp_server.pg_touch("clear")
    mcp_server.pg_touch("list")
    assert calls[0] == ("touch", {"state": "tap", "source": "mcp", "target": "croc:3", "velocity": 0.8})
    assert calls[1] == ("touch", {"state": "down", "source": "mcp", "x": 100, "y": 200, "id": "a"})
    assert calls[2] == ("touchsynth", {"action": "clear"}) and calls[3] == ("touches", {})
    assert "error" in mcp_server.pg_touch("tap")


# ---------------------------------------------------------------------- marimba y zona libre

def test_largest_rect_on_synthetic_mask():
    m = np.zeros((1080, 1920), bool)
    m[100:400, 1000:1700] = True                                  # 700x300 libre
    m[600:700, 100:300] = True                                    # chica
    x, y, w, h = largest_rect(m, cell=16)
    assert 1000 <= x and x + w <= 1700 and 100 <= y and y + h <= 400 and w * h >= 0.8 * 700 * 300
    assert largest_rect(np.zeros((1080, 1920), bool)) is None
    m2 = m.copy(); m2[250, 1300] = False                          # una manchita: con frac < 1 se tolera
    assert largest_rect(m2, frac=0.95)[2] >= 600


def test_free_rect_avoids_objects():
    sd = ScanData.fallback("test")
    rect, why = sd.free_rect(margin=30)
    x, y, w, h = rect
    obj = sd.object_mask(30)
    assert obj[y:y + h, x:x + w].mean() < 0.05 and w >= 240 and h >= 90 and why


def test_marimba_layout_strike_roll_and_duration(fake_midi):
    m = Marimba(params={"rect": [1000, 100, 600, 200], "bars": 6, "root": 60, "dur_s": 0.3})
    assert len(m.bars) == 6 and m.rect_source == "a mano"
    lens = [float(b[:, 1].max() - b[:, 1].min()) for b in m.bars]
    assert lens[0] > lens[-1]                                     # graves más largas
    for b in m.bars:
        assert b[:, 0].min() >= 1000 and b[:, 0].max() <= 1600
    c0, c2 = m.bars[0].mean(axis=0), m.bars[2].mean(axis=0)
    m.handle(_ev("down", *c0, velocity=1.0), now=1.0)
    assert _notes(fake_midi)[-1] == ("note_on", 2, 60, 127)
    m.handle(_ev("move", *c2), now=1.05)                          # redoble: pasa por la barra 2
    assert _notes(fake_midi)[-1][2] == m.note_list[2]
    m.step(1.2); assert len(m.notes.sounding) == 2
    m.step(1.4); assert m.notes.sounding == []                    # duración fija, sin esperar el up
    m.handle(_ev("up", *c2), now=1.5)
    assert m.hit(1100, 150) and not m.hit(100, 100)
    img = np.zeros((1080, 1920, 3), np.uint8); m.render(img, 1, 1, 1.5)
    assert img[100:300, 1000:1600].max() > 0
    assert m.resolve(2) == pytest.approx(tuple(c2))


def test_marimba_picks_free_zone_from_scan():
    m = Marimba(scan=ScanData.fallback("test"))
    assert m.rect is not None and "respaldo" not in m.rect_source
    x, y, w, h = m.rect
    assert not ScanData.fallback().object_mask(0)[y:y + h, x:x + w].any()


# ---------------------------------------------------------------------- reparto de toques

def test_registry_routes_by_priority_and_resolves(fake_midi):
    reg = Registry()
    croc = reg.get("e1", lambda: CrocSynth(scan=ScanData.fallback("t")))
    mar = reg.get("e2", lambda: Marimba(params={"rect": [1300, 120, 520, 220]}))
    sk = reg.get("e3", lambda: Sketch())
    cx, cy = croc.zones.centers[2]
    mx, my = mar.bars[1].mean(axis=0)
    log = [dict(_ev("down", cx, cy, "a"), seq=1), dict(_ev("down", mx, my, "b"), seq=2),
           dict(_ev("down", 300, 950, "c"), seq=3), dict(_ev("move", 320, 960, "c"), seq=4),
           dict(_ev("move", cx + 300, cy, "a"), seq=5), dict(_ev("up", 330, 960, "c"), seq=6)]
    for iid in ("e1", "e2", "e3"):
        reg.pump(iid, log, now=1.0)
    assert croc.hits == 1 and mar.hits == 1
    assert sk.status()["trazos"] == 1 and "a" not in sk.live          # el dibujo no se quedó con el toque del cocodrilo
    assert reg.claims["a"] == "e1" and reg.claims["b"] == "e2" and reg.claims["c"] == "e3"
    assert reg.pump("e1", log, now=1.1) == 0                           # no repite eventos
    assert reg.resolve("croc:2") == pytest.approx(croc.zones.centers[2])
    assert reg.resolve("marimba:1") == pytest.approx(tuple(mar.bars[1].mean(axis=0)))
    assert reg.resolve("nada:1") is None
    assert reg.command("clear")["borrados"] == 1 and sk.status()["trazos"] == 0
    reg.command("panic")
    assert croc.notes.sounding == []
    reg.drop("e1")
    assert "e1" not in reg.instruments


# ---------------------------------------------------------------------- runtime: comando touch

def _rt(tmp_path, **kw):
    from playground.runtime import Runtime
    return Runtime(canvas_size=(640, 360), requests_dir=tmp_path / "requests", demo=False, **kw)


def test_runtime_touch_command(tmp_path):
    rt = _rt(tmp_path)
    r = rt.command("touch", {"id": "t9", "state": "down", "x": 812.4, "y": 433, "source": "brio"})
    assert r == {"touches": 1, "seq": 1, "id": "t9"}
    rt.command("touch", {"id": "t9", "state": "move", "x": 820, "y": 440})
    ts = touch_state(rt.scene)
    assert ts.touches["t9"]["x"] == 820.0
    rt.command("touch", {"id": "t9", "state": "up", "x": 820, "y": 440})
    assert ts.touches == {} and [e["state"] for e in ts.log] == ["down", "move", "up"]
    evs = [e for e in rt.events if e["type"] == "touch"]
    assert [e["state"] for e in evs] == ["down", "move", "up"] and evs[0]["source"] == "brio"
    rt.command("touch", {"state": "tap", "x": 10, "y": 10})
    assert [e["state"] for e in ts.log[-2:]] == ["down", "up"] and ts.touches == {}
    with pytest.raises(ValueError):
        rt.command("touch", {"state": "boom", "x": 1, "y": 1})
    with pytest.raises(ValueError):
        rt.command("touch", {"state": "down"})
    with pytest.raises(KeyError):
        rt.command("touch", {"state": "down", "target": "croc:1"})       # sin cocodrilo spawneado
    assert rt.command("hands", {"hands": [{"id": 1, "x": 5, "y": 6}]}) == {"hands": 1}
    assert rt.command("touches")["hands"]["1"]["x"] == 5.0
    assert "touch" in rt.command("schema")


def test_runtime_effects_play_and_survive_hot_reload(tmp_path, fake_midi):
    rt = _rt(tmp_path)
    nada = str(tmp_path / "sin_escaneo")                                  # → escena sintética
    e1 = rt.spawn("croc_synth", None, {"scan": nada})["id"]
    e2 = rt.spawn("touch_draw")["id"]
    e3 = rt.spawn("marimba", None, {"scan": nada})["id"]
    e4 = rt.spawn("linterna", None, {"scan": nada})["id"]
    frame = rt.tick(0.03)
    assert frame.max() > 0                                                # zonas y barras a la vista
    rt.command("touch", {"state": "down", "target": "croc:0", "id": "z"})
    rt.command("touch", {"state": "down", "x": 200, "y": 1000, "id": "d"})
    rt.command("touch", {"state": "move", "x": 300, "y": 1010, "id": "d"})
    rt.tick(0.03)
    st = rt.command("touches")["touchsynth"]["instrumentos"]
    assert st[e1]["held"] == {"z": 0} and st[e2]["en_curso"] == 1 and st[e4]["fuente"] == "toques"
    assert _notes(fake_midi)[-1][1:3] == (1, 48)
    # hot reload: el runtime rehace la instancia con el mismo id; la nota sostenida y el trazo siguen
    cls = type(rt.instances[e1])
    rt.instances[e1] = cls(rt.instances[e1].target, rt.instances[e1].p, e1)
    rt.tick(0.03)
    assert rt.command("touches")["touchsynth"]["instrumentos"][e1]["held"] == {"z": 0}
    rt.command("touch", {"state": "up", "target": "croc:0", "id": "z"})
    rt.set_param(e2, "clear", True)
    rt.tick(0.03)
    assert rt.command("touches")["touchsynth"]["instrumentos"][e2]["trazos"] == 0
    rt.command("touch", {"state": "tap", "target": "marimba:2", "velocity": 0.5})
    rt.tick(0.03)
    assert rt.command("touches")["touchsynth"]["instrumentos"][e3]["hits"] == 1
    assert rt.command("touchsynth", {"action": "recalibrar"})["recalibrar"]
    rt.kill(e1)
    assert e1 not in rt.command("touches")["touchsynth"]["instrumentos"]
    json.dumps(rt.state(), default=str)                                   # el estado se serializa


def test_runtime_touch_over_http_and_hands_bridge(tmp_path):
    """El puente de playground.hands manda toques por HTTP real y el runtime ya no responde 400."""
    import uvicorn
    from fastapi.testclient import TestClient
    from playground.hands.bridge import TouchBridge
    from playground.runtime import build_app
    rt = _rt(tmp_path)
    c = TestClient(build_app(rt))
    r = c.post("/api/command", json={"cmd": "touch", "args": {"state": "down", "x": 1, "y": 2, "id": "q"}})
    assert r.status_code == 200 and r.json()["ok"]
    assert c.post("/api/command", json={"cmd": "touch", "args": {"state": "zzz"}}).status_code == 400
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
        b = TouchBridge(f"http://127.0.0.1:{port}", projector="proj0", source="brio")
        for st, x in (("down", 600), ("move", 640), ("up", 640)):
            b.send({"id": "h1", "state": st, "x": x, "y": 500, "space": "proj", "projector": "proj0", "mm": None,
                    "hand": "1", "handed": "Right", "trigger": "pinch", "t": time.time(), "source": "brio"})
        b.close()
        assert b.sent == 3 and b.errors == 0 and not b.unsupported
        assert [e["state"] for e in touch_state(rt.scene).log if e["id"] == "h1"] == ["down", "move", "up"]
    finally:
        server.should_exit = True
        th.join(timeout=5)


# ---------------------------------------------------------------------- linterna

def test_normals_of_plane_and_bump():
    yy, xx = np.mgrid[0:50, 0:60].astype(np.float32)
    nx, ny, nz = normals(0.5 * xx)                                  # sube hacia +x: la normal mira a −x
    k = 1 / math.sqrt(1.25)
    assert np.allclose(nx[5:-5, 5:-5], -0.5 * k, atol=1e-4) and np.allclose(ny, 0, atol=1e-5)
    assert np.allclose(nz[5:-5, 5:-5], k, atol=1e-4)
    bump = 20 * np.exp(-((xx - 30) ** 2 + (yy - 25) ** 2) / 50)
    nx, ny, nz = normals(bump)
    assert nx[25, 22] < -0.1 and nx[25, 38] > 0.1 and ny[18, 30] < -0.1 and ny[32, 30] > 0.1   # hacia afuera
    assert nz[25, 30] == pytest.approx(1.0, abs=1e-3)
    assert np.allclose(nx ** 2 + ny ** 2 + nz ** 2, 1, atol=1e-5)


def _flat(w=1920, h=1080):
    return np.zeros((h, w), np.float32)


def test_shade_point_light_falloff_and_ambient():
    hf = HeightField(_flat(), scale=8, smooth=0, relief=1.0)
    dark = shade(hf, [], ambient=0.05, ambient_color=(1, 1, 1))
    assert np.allclose(dark, 0.05)
    L = Light(960, 540, z=200, color=(1, 1, 1), intensity=1.0, radius=400, cone_deg=90)
    img = shade(hf, [L], ambient=0.0, shadows=False)[..., 0]
    iy, ix = np.unravel_index(np.argmax(img), img.shape)
    assert abs(ix * 8 + 4 - 960) <= 8 and abs(iy * 8 + 4 - 540) <= 8      # lo más claro bajo la mano
    assert img[540 // 8, (960 + 200) // 8] < img[540 // 8, (960 + 80) // 8]  # cae con la distancia
    assert img[540 // 8, (960 + 420) // 8] == 0                              # fuera del radio: nada
    assert img[540 // 8, (960 - 150) // 8] == pytest.approx(img[540 // 8, (960 + 150) // 8], rel=0.05)


def test_shade_slopes_facing_light_and_cast_shadow():
    yy, xx = np.mgrid[0:1080, 0:1920].astype(np.float32)
    ridge = np.where(np.abs(xx - 960) < 40, 80.0, 0.0).astype(np.float32)     # muro vertical alto en x = 960
    hf = HeightField(ridge, scale=4, smooth=1.0, relief=1.0, clip_pct=(0, 100))
    L = Light(760, 540, z=60, color=(1, 1, 1), intensity=1.0, radius=900, cone_deg=90)   # luz rasante a la izquierda
    lit = shade(hf, [L], ambient=0.0, shadows=True)[..., 0]
    noshadow = shade(hf, [L], ambient=0.0, shadows=False)[..., 0]
    row = 540 // 4
    face = lit[row, (960 - 44) // 4]                                          # cara del muro que mira a la luz
    flat_near = lit[row, 880 // 4]
    behind = lit[row, 1100 // 4]                                              # detrás del muro
    assert face > flat_near                                                   # la cara iluminada resalta (volumen)
    assert behind < 0.2 * noshadow[row, 1100 // 4]                            # sombra proyectada detrás
    cone = shade(hf, [Light(760, 540, 60, (1, 1, 1), 1.0, 900, dir=(0.0, 1.0), cone_deg=25, tilt_deg=40)], 0.0,
                 shadows=False)[..., 0]
    assert cone[(540 + 60) // 4, 760 // 4] > cone[(540 - 60) // 4, 760 // 4]   # el cono apunta hacia donde apunta la mano


def test_linterna_sources_hands_touches_demo():
    lt = Linterna(scan=ScanData.fallback("t"), params={"escala": 8})
    now = 100.0
    lt.update_sources({"1": {"x": 500, "y": 400, "height": 0.0, "t": now}}, {}, now)
    assert lt.source == "manos" and list(lt.lights) == ["m1"]
    z_low = lt.lights["m1"].z
    lt.update_sources({"1": {"x": 500, "y": 400, "height": 1.0, "t": now}}, {}, now)
    assert lt.lights["m1"].z > z_low                                          # mano más alta, luz más alta
    lt.update_sources({"1": {"x": 500, "y": 400, "t": now - 5}}, {"a": {"x": 900, "y": 300}}, now)
    assert lt.source == "toques"                                              # mano vieja → toques
    lt.update_sources({}, {}, now)
    assert lt.source == "ninguna" and lt.lights == {}
    img = np.zeros((1080, 1920, 3), np.uint8); lt.render(img, 1, 1, now)
    assert img.max() <= int(0.03 * 255) + 1                                   # sin manos: casi oscuro
    lt.configure({**lt.params, "demo": True}); lt.update_sources({}, {}, now)
    assert lt.source == "demo"
    img = np.zeros((1080, 1920, 3), np.uint8); lt.render(img, 1, 1, now)
    assert img.max() > 100


# ---------------------------------------------------------------------- Osmo (sintético)

def _osmo_world(seed=0, scale=2):
    rng = np.random.default_rng(seed)
    W, H = 1920, 1080
    gy, gx = np.mgrid[0:H, 0:W].astype(np.float32)
    mapper = PovMapper(gx, gy, np.ones((H, W), bool), scale=scale)          # cámara = proyector (mapa identidad)
    w, h = mapper.size
    tex = cv2.GaussianBlur(rng.uniform(0, 1, (h, w, 3)).astype(np.float32), (0, 0), 3)
    B, Wt = 25 + 20 * tex, 170 + 50 * tex
    model = ProjectionModel(B, Wt, B + (Wt - B) * 0.5)
    return rng, mapper, model


def _push(det, p):
    """Reloj sintético: un cuadro proyectado por segundo; la cámara lo ve 0,1 s después."""
    det._tk = getattr(det, "_tk", 1000.0) + 1.0
    det.push_projected(p, t=det._tk)


def _det(det, cam):
    return det.detect_pov(cam, t=getattr(det, "_tk", 1000.0) + 0.1)


def _pattern(rng, w, h):
    p = np.zeros((h, w, 3), np.uint8)
    for _ in range(12):
        x, y = int(rng.integers(0, w - 100)), int(rng.integers(0, h - 80))
        cv2.rectangle(p, (x, y), (x + int(rng.integers(30, 200)), y + int(rng.integers(30, 150))),
                      [int(v) for v in rng.integers(0, 255, 3)], -1)
    return cv2.add(p, np.full_like(p, 100))                                  # luz base (como --luz-base en vivo)


def _cam(rng, model, p, tip=None, gap=(0, 0)):
    h, w = p.shape[:2]
    cam = model.predict(p)
    if tip is not None:
        tx, ty = tip
        arm = np.zeros((h, w), np.uint8)
        cv2.line(arm, (tx, ty + 120), (tx, h + 50), 1, 60)
        cv2.circle(arm, (tx, ty + 60), 40, 1, -1)
        cv2.line(arm, (tx, ty), (tx, ty + 60), 1, 14)
        sh = cv2.warpAffine(arm, np.float32([[1, 0, gap[0]], [0, 1, gap[1]]]), (w, h)) > 0
        only = sh & (arm == 0)
        cam[only] = model.B[only]                                           # sombra: solo luz ambiente
        skin = model.B + model.A * np.array([0.5, 0.6, 0.9], np.float32) * p.astype(np.float32) / 255
        cam[arm > 0] = skin[arm > 0]
    cam += rng.normal(0, 3, cam.shape).astype(np.float32)
    return np.clip(cam, 0, 255).astype(np.uint8)


def test_osmo_projection_model_ignores_changing_visuals():
    rng, mapper, model = _osmo_world()
    w, h = mapper.size
    det = OsmoHands(mapper, OsmoConfig(model="proyeccion"), model)
    for k in range(3):                                                       # la proyección cambia, no hay mano
        p = _pattern(rng, w, h)
        _push(det, p)
        assert _det(det, _cam(rng, model, p)) == []
    g = ProjectionModel(model.B, model.B + model.A, model.B + model.A * 0.25)   # gris al 25 % → gamma 2
    assert g.gamma == pytest.approx(2.0, abs=0.05)


def test_projection_model_capture_with_auto_exposure_and_gamma_fit():
    """La Osmo tiene auto-exposición: con tableros complementarios (mitad blanca) la ganancia no cambia entre tomas
    y B, W salen bien; la gamma se recupera en vivo."""
    rng, mapper, true = _osmo_world(3)
    w, h = mapper.size
    true.set_gamma(1.8)
    shown = {}

    def show(img):
        shown["p"] = mapper.proj_to_pov(img)

    def read_pov():
        p = shown["p"]
        ae = 0.5 / max(float(p.mean()) / 255.0, 0.05)                 # la cámara compensa el brillo medio
        return np.clip(true.predict(p) * ae, 0, 255).astype(np.uint8)

    m = ProjectionModel.capture(show, read_pov, settle=0.0, frames=1, size=(1920, 1080), block=48)
    inner = (slice(20, -20), slice(20, -20))
    assert np.median(np.abs(m.A - true.A)[inner]) < 6 and np.median(np.abs(m.B - true.B)[inner]) < 4
    p = _pattern(rng, w, h)
    cam = np.clip(true.predict(p), 0, 255).astype(np.uint8)
    assert m.fit_gamma(cam, [p], mapper.valid) == pytest.approx(1.8, abs=0.35)


def test_osmo_compares_with_what_was_projected_during_the_latency():
    rng, mapper, model = _osmo_world(7)
    w, h = mapper.size
    det = OsmoHands(mapper, OsmoConfig(model="proyeccion", gamma_every=0), model)
    a, b, c = (_pattern(rng, w, h) for _ in range(3))
    det.push_projected(a, t=10.0)
    det.push_projected(b, t=10.3)
    det.push_projected(c, t=10.49)
    got = det._projected_for(10.5, a)
    assert any(g is det.proj_hist[1][1] for g in got) and not any(g is det.proj_hist[0][1] for g in got)
    assert det.detect_pov(_cam(rng, model, b), t=10.5) == []           # la cámara todavía ve b (latencia)


def test_osmo_ignores_blobs_stuck_to_the_border():
    rng, mapper, model = _osmo_world(4)
    w, h = mapper.size
    det = OsmoHands(mapper, OsmoConfig(model="proyeccion", gamma_every=0), model)
    p = _pattern(rng, w, h)
    _push(det, p)
    cam = _cam(rng, model, p).astype(np.float32)
    cam[200:240, 0:12] = (40, 200, 40)                                 # algo verde pegado al borde izquierdo
    assert _det(det, cam.astype(np.uint8)) == []


def test_osmo_fingertip_and_shadow_gap():
    rng, mapper, model = _osmo_world(1)
    w, h = mapper.size
    det = OsmoHands(mapper, OsmoConfig(model="proyeccion"), model)
    p = _pattern(rng, w, h)
    _push(det, p)
    tips = _det(det, _cam(rng, model, p, (480, 250), (0, 0)))           # tocando: la sombra queda tapada
    assert len(tips) == 1
    t = tips[0]
    assert abs(t.x - 960) < 12 and abs(t.y - 490) < 16                      # px del proyector (POV a la mitad)
    assert t.dir[1] < -0.9 and t.hand_y > t.y and t.gap is None
    for gap in ((10, -8), (20, -16)):
        tp = _det(det, _cam(rng, model, p, (480, 250), gap))[0]
        true_gap = 2 * math.hypot(*gap)
        assert tp.gap is not None and abs(tp.gap - true_gap) < 0.3 * true_gap


def test_osmo_hybrid_learns_scene_and_survives_auto_exposure():
    """Modelo por defecto en vivo: fondo aprendido + aporte conocido del proyector. Un objeto que no estaba al
    calibrar se aprende, las visuales que cambian y un salto de exposición no son manos, y la mano sí."""
    rng, mapper, model = _osmo_world(5)
    w, h = mapper.size
    det = OsmoHands(mapper, OsmoConfig(), model)
    assert det.cfg.model == "hibrido"

    def scene(p, tip=None, ae=1.0):
        cam = _cam(rng, model, p, tip).astype(np.float32)
        cam[50:120, 300:400] += 35                                          # objeto nuevo sobre la mesa
        return np.clip(cam * ae, 0, 255).astype(np.uint8)

    for _ in range(det.cfg.warmup + 3):
        p = _pattern(rng, w, h)
        _push(det, p)
        assert _det(det, scene(p)) == []
    for ae in (1.1, 1.2, 1.3, 1.4, 1.4):                                   # la Osmo sube la exposición (rampa)
        p = _pattern(rng, w, h)
        _push(det, p)
        assert _det(det, scene(p, ae=ae)) == []
    assert det.stats["exposicion"] == pytest.approx(1.4, abs=0.1)
    p = _pattern(rng, w, h)
    _push(det, p)
    tips = _det(det, scene(p, (400, 260), ae=1.4))
    assert len(tips) == 1 and abs(tips[0].x - 800) < 16 and abs(tips[0].y - 510) < 24     # ~2 cm


def test_osmo_hybrid_learns_projector_gain_from_changes():
    """En vivo el proyector aporta menos (o más) que al calibrar: la ganancia sale de los cambios entre cuadros y
    las visuales que se mueven dejan de ser primer plano."""
    rng, mapper, model = _osmo_world(8)
    w, h = mapper.size
    real = ProjectionModel(model.B, model.B + 0.6 * model.A)              # la luz real es el 60 % de la medida
    det = OsmoHands(mapper, OsmoConfig(), model)
    fg = []
    for k in range(40):
        p = _pattern(rng, w, h)
        _push(det, p)
        _det(det, _cam(rng, real, p))
        fg.append(det.stats["fg_frac"])
    assert det.stats["gain"] == pytest.approx(0.6, abs=0.12)
    assert max(fg[-5:]) < 0.02


def test_osmo_relearns_a_bad_background_and_pause():
    """Si el fondo quedó mal aprendido (p.ej. con un tablero de calibración todavía en cámara) el primer plano tapa
    media mesa: después de `reset_frames` cuadros se reaprende solo. `pause` no detecta por un rato."""
    rng, mapper, model = _osmo_world(6)
    w, h = mapper.size
    det = OsmoHands(mapper, OsmoConfig(reset_frames=4), model)
    p = _pattern(rng, w, h)
    yy, xx = np.mgrid[0:h, 0:w]
    checker = np.repeat(((((xx // 16) + (yy // 16)) % 2) * 255).astype(np.uint8)[..., None], 3, axis=2)
    for _ in range(det.cfg.warmup):                                        # la Osmo todavía ve el tablero
        _push(det, p)
        _det(det, _cam(rng, model, checker))
    for _ in range(4):
        _push(det, p)
        assert _det(det, _cam(rng, model, p)) == []                   # gigante: pegado al borde, sin puntas
    assert det.stats.get("reinicios") == 1 and det.bg is None
    for _ in range(det.cfg.warmup + 2):
        _push(det, p)
        assert _det(det, _cam(rng, model, p)) == []
    assert det.stats["fg_frac"] < 0.01
    det.pause(10)
    _push(det, p)
    assert _det(det, _cam(rng, model, p, (400, 260))) == [] and det.bg is None


def test_osmo_background_mode():
    rng, mapper, model = _osmo_world(2)
    w, h = mapper.size
    p = np.full((h, w, 3), 200, np.uint8)
    det = OsmoHands(mapper, OsmoConfig(model="fondo", warmup=3))
    for _ in range(3):
        assert _det(det, _cam(rng, model, p)) == []
    tips = _det(det, _cam(rng, model, p, (300, 200)))
    assert len(tips) == 1 and abs(tips[0].x - 600) < 14 and abs(tips[0].y - 390) < 18


# ---------------------------------------------------------------------- fusión

def _tip(x, y, gap=None, t=0.0):
    return OsmoTip(x, y, x, y + 70, (0.0, -1.0), 3000.0, gap, 120.0, t)


def _run(fu, frames):
    """frames: lista de (t, osmo | None, brio | None)."""
    evs = []
    for t, osmo, brio in frames:
        if osmo is not None:
            fu.update_osmo(osmo, t)
        if brio is not None:
            fu.update_brio(brio, t, calibrated=True)
        evs += fu.step(t)
    return evs


def test_fusion_osmo_position_brio_contact():
    fu = Fusion()
    frames = []
    for k in range(10):
        t = 1 + k * 0.05
        contact = 2 <= k < 7
        frames.append((t, [_tip(800 + 2 * k, 400)], [BrioObs("3", 830.0, 430.0, contact, 0.0 if contact else 0.5, t=t)]))
    evs = _run(fu, frames)
    states = [e["state"] for e in evs]
    assert states[0] == "down" and states[-1] == "up" and set(states[1:-1]) <= {"move"}
    d = evs[0]
    assert d["source"] == "fusion" and abs(d["x"] - 806) < 6 and d["y"] == pytest.approx(400, abs=1)   # x,y de la Osmo
    assert d["hand"] == "3" and 0.2 <= d["velocity"] <= 1 and fu.status(1.45)["modo"] == "fusion"
    assert not fu.status(1.45)["degradado"]


def test_fusion_solo_osmo_by_shadow_and_presence():
    fu = Fusion(FusionConfig(gap_touch=12))
    evs = _run(fu, [(1.0, [_tip(500, 500, 40)], None), (1.05, [_tip(500, 500, 30)], None),
                    (1.1, [_tip(500, 500, 6)], None), (1.15, [_tip(500, 500, 4)], None),
                    (1.2, [_tip(500, 500, None)], None),                    # la sombra se juntó con el dedo
                    (1.25, [_tip(500, 500, None)], None),
                    (1.3, [_tip(500, 500, 35)], None), (1.35, [_tip(500, 500, 40)], None)])
    assert [e["state"] for e in evs] == ["down", "up"] and evs[0]["trigger"] == "sombra"
    assert evs[0]["source"] == "osmo" and evs[0]["velocity"] > 0.35       # bajó rápido
    st = fu.status(1.35)
    assert st["modo"] == "solo-osmo" and st["degradado"] and "sombra" in st["detalle"]
    fp = Fusion()                                                         # nunca hubo sombra: dedo quieto = toque
    passing = [(1.0 + 0.05 * k, [_tip(300 + 40 * k, 300)], None) for k in range(10)]   # algo que pasa: no toca
    assert _run(fp, passing) == []
    still = [(2.0 + 0.05 * k, [_tip(800 + (k % 2), 300)], None) for k in range(10)]   # quieto 0,45 s: toca
    evs = _run(fp, still)
    assert [e["state"] for e in evs][:1] == ["down"] and evs[0]["trigger"] == "presencia"
    draw = [(2.5 + 0.05 * k, [_tip(800 + 30 * k, 300)], None) for k in range(1, 6)]   # ya tocando: dibuja
    evs = _run(fp, draw + [(2.8, [], None), (2.9, [], None), (3.2, [], None)])
    states = [e["state"] for e in evs]
    assert states[0] == "move" and states[-1] == "up" and "down" not in states
    assert "quieto" in fp.status(3.2)["detalle"]


def test_fusion_solo_brio_and_uncalibrated_and_stale():
    fb = Fusion()
    evs = _run(fb, [(t, None, [BrioObs("1", 700.0, 300.0, True, t=t)]) for t in (1.0, 1.05, 1.1)])
    assert evs[0]["state"] == "down" and evs[0]["source"] == "brio" and fb.status(1.1)["modo"] == "solo-brio"
    fu = Fusion()                                                         # Brio sin calibrar: 1 a 1
    frames = [(t, [_tip(900, 600)], [BrioObs("2", None, None, True, t=t)]) for t in (1.0, 1.05, 1.1)]
    for t, o, b in frames:
        fu.update_osmo(o, t); fu.update_brio(b, t, calibrated=False)
    evs = fu.step(1.0) + fu.step(1.05)
    assert evs and evs[0]["source"] == "fusion" and evs[0]["x"] == pytest.approx(900)
    fs = Fusion(FusionConfig(stale_s=0.3))
    fs.update_osmo([_tip(100, 100, 50)], 1.0); fs.update_brio([], 0.0, True)
    assert fs.mode(1.0) == "solo-osmo" and fs.mode(2.0) == "sin-camaras"


def test_fusion_lost_hand_releases_and_hands_list():
    fu = Fusion(FusionConfig(lost_s=0.2, osmo_contact="presencia", dwell_s=0.0))
    evs = _run(fu, [(1.0, [_tip(400, 400)], None), (1.05, [_tip(400, 400)], None)])
    assert evs[-1]["state"] == "down"
    hands = fu.hands(1.05)
    assert len(hands) == 1 and hands[0]["touching"] and hands[0]["y"] == pytest.approx(470, abs=2)
    fu.update_osmo([], 1.1)
    evs = fu.step(1.1) + fu.step(1.4)
    assert evs and evs[-1]["state"] == "up" and evs[-1].get("reason") == "mano perdida"
    assert fu.hands(1.4) == []


def test_brio_contact_from_landmarks():
    from playground.hands.calib import from_points
    from playground.hands.model import Hand, synthetic_hand
    cal = from_points([[0, 0], [1920, 0], [1920, 1080], [0, 1080]], [[0, 0], [1920, 0], [1920, 1080], [0, 1080]])
    from playground.hands.model import palm_size
    flat = Hand(1, "Right", synthetic_hand(600, 500, scale=80), (600.0, 500.0))
    high = Hand(1, "Right", synthetic_hand(600, 500, scale=110), (600.0, 500.0))     # más cerca de la cámara
    bc = BrioContact(cal, mode="apoyo", hand_ref=palm_size(flat.landmarks))
    o = bc.observe([flat], 1.0)[0]
    assert o.contact and o.x == pytest.approx(600) and o.height == pytest.approx(0, abs=0.05)
    o2 = bc.observe([high], 1.1)[0]
    assert not o2.contact and o2.height > 0.5
    nocal = BrioContact(None).observe([Hand(2, "Left", synthetic_hand(10, 10), (10.0, 10.0), pinch=True)], 1.0)[0]
    assert nocal.x is None and nocal.contact is True


def test_fusion_into_runtime_plays_croc(tmp_path, fake_midi):
    rt = _rt(tmp_path)
    rt.spawn("croc_synth", None, {"scan": str(tmp_path / "nada")})
    rt.tick(0.03)
    from playground.touchsynth.registry import registry_of
    reg = registry_of(rt.scene)
    zx, zy = reg.resolve("croc:5")
    fu = Fusion(emit=lambda ev: rt.command("touch", ev))
    _run(fu, [(t, [_tip(zx, zy)], [BrioObs("1", zx + 10, zy + 10, True, t=t)]) for t in (1.0, 1.05, 1.1)])
    rt.tick(0.03)
    st = rt.command("touches")["touchsynth"]["instrumentos"]["e1"]
    assert list(st["held"].values()) == [5]
    assert _notes(fake_midi)[-1][1:3] == (1, reg.instruments["e1"].note_list[5])
