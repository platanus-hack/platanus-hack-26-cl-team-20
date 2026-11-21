"""Interactividad genérica sin hardware: objeto_synth/objeto_vivo sobre cualquier objeto, toque_onda, mano_luz,
juego_memoria, gancho de mirada (comando gaze + efecto mirada) y rendimiento, con toques sintéticos sobre una
escena inventada (cuadro, sofá, planta con hojas, libro) en px del proyector. Nada por nombre."""

from __future__ import annotations

import os
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from playground.scene import Scene, SceneObject
from playground.touchsynth import music
from playground.touchsynth.interactivo import Memoria, Mirada, Onda, pick_hud
from playground.touchsynth.objetos import (ObjetoVivo, object_color, pick_object, scene_objects_px, to_objpx)
from playground.touchsynth.registry import registry_of, touch_state

ROOT = Path(__file__).resolve().parent.parent
NEW = ("objeto_synth", "objeto_vivo", "toque_onda", "mano_luz", "juego_memoria", "mirada")


@pytest.fixture(autouse=True)
def fake_midi():
    m = music.FakeMidi()
    music.set_midi(m)
    yield m
    music.set_midi(None)


def _notes(m, ch=None):
    return [e for e in m.messages if e[0] == "note_on" and (ch is None or e[1] == ch)]


def _rect(x, y, w, h):
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def planta() -> list[list[float]]:
    """Maceta alargada con tres hojas que sobresalen hacia arriba (salientes = patas)."""
    m = np.zeros((1080, 1920), np.uint8)
    cv2.ellipse(m, (1450, 760), (260, 50), 0, 0, 360, 255, -1)
    cv2.ellipse(m, (1690, 760), (60, 55), 0, 0, 360, 255, -1)
    for x in (1340, 1520):
        cv2.rectangle(m, (x - 16, 650), (x + 16, 730), 255, -1)
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return max(cnts, key=cv2.contourArea).reshape(-1, 2).astype(float).tolist()


def sala() -> Scene:
    s = Scene(table_mm=(1920.0, 1080.0))
    s.projector_px = True

    def add(oid, name, kind, poly, **extra):
        xs, ys = [p[0] for p in poly], [p[1] for p in poly]
        s.objects[oid] = SceneObject(oid, name, kind, [min(xs), min(ys), 0, max(xs), max(ys), 50], poly,
                                     extra={"aliases": [name], **extra})

    add("table", "mesa", "table", _rect(0, 0, 1920, 1080))
    add("cuadro1", "cuadro grande", "frame", _rect(200, 80, 520, 360), quad=_rect(200, 80, 520, 360))
    add("sofa", "sofá", "prop", [[150, 650], [1050, 640], [1080, 900], [700, 960], [160, 930]])
    add("planta", "planta", "prop", planta())
    add("libro", "libro", "prop", _rect(1300, 120, 220, 150), quad=_rect(1300, 120, 220, 150))
    s.objects["fantasma"] = SceneObject("fantasma", "OHM", "instrument", [0, 0, 0, 10, 10, 1], _rect(1150, 150, 400, 250),
                                        extra={"placement": "provisional: sin fit"})
    return s


def _rt(tmp_path, scene=None):
    from playground.runtime import Runtime
    return Runtime(scene=scene or sala(), canvas_size=(1920, 1080), demo=False, requests_dir=tmp_path / "req")


def _istatus(rt, iid):
    return rt.command("touches")["touchsynth"]["instrumentos"][iid]


# ---------------------------------------------------------------------- objetos genéricos

def test_scene_objects_are_generic_and_skip_table_and_provisional():
    s = sala()
    ids = sorted(o.id for o in scene_objects_px(s))
    assert ids == ["cuadro1", "libro", "planta", "sofa"]
    assert object_color("sofa") == object_color("sofa") and object_color("sofa") != object_color("libro")
    o, c = pick_object(s, None, "planta")
    assert o.id == "planta" and c.shape[1] == 2
    o, _ = pick_object(s, None, "")                       # vacío = el más grande
    assert o.id == "sofa"
    o, _ = pick_object(s, s.objects["libro"], "planta")   # el target del spawn manda
    assert o.id == "libro"
    assert pick_object(Scene(), None, "nada")[0] is None   # escena demo en mm: sin contornos en px
    assert pick_hud(scene_objects_px(s)).id == "libro"    # el plano más chico


def test_gallery_has_new_effects_with_spanish_meta_and_capa(tmp_path):
    rt = _rt(tmp_path)
    gal = {g["name"]: g for g in rt.command("list_effects")["gallery"]}
    for name in NEW + ("croc_synth", "croc_alive"):
        g = gal[name]
        assert g["label"] and g["description"] and g["aliases"], name
    for name in NEW:
        assert "capa" in gal[name]["params"], name
    assert gal["croc_synth"]["params"].get("objeto") is None  # alias: mismos params de siempre


# ---------------------------------------------------------------------- objeto_synth / objeto_vivo

def test_objeto_synth_any_object_by_zones_plays_midi(tmp_path, fake_midi):
    rt = _rt(tmp_path)
    iid = rt.spawn("objeto_synth", "sofa", {"zones": 6, "scale": "dorica", "root": 50})["id"]
    rt.tick(0.03)
    st = _istatus(rt, iid)
    assert st["objeto"] == "sofa" and st["zones"] == 6 and len(st["notes"]) == 6
    rt.command("touch", {"state": "down", "target": "sofa:0", "id": "a"})
    rt.tick(0.03)
    assert _istatus(rt, iid)["held"] == {"a": 0}
    assert _notes(fake_midi, 1)[-1][2] == 50                       # zona 0 = la nota más grave
    rt.command("touch", {"state": "move", "target": "sofa:5", "id": "a"})   # glissando
    rt.command("touch", {"state": "up", "target": "sofa:5", "id": "a"})
    rt.tick(0.03)
    assert len(_notes(fake_midi, 1)) == 2
    # por param objeto (alias) en vez de target
    i2 = rt.spawn("objeto_synth", None, {"objeto": "planta", "zones": 4})["id"]
    rt.tick(0.03)
    assert _istatus(rt, i2)["objeto"] == "planta"


def test_objeto_vivo_wakes_on_touch_and_finds_legs(tmp_path):
    rt = _rt(tmp_path)
    iid = rt.spawn("objeto_vivo", "planta", {"res": 0.5})["id"]
    frame = rt.tick(0.03).copy()
    st = _istatus(rt, iid)
    assert st["objeto"] == "planta" and len(st["patas"]) >= 1 and st["escamas"] > 20
    x0, y0, w, h = st["bbox"]
    assert frame[y0:y0 + h, x0:x0 + w].max() > 0 and frame[:300, :].max() == 0     # dibuja solo sobre la planta
    inst = registry_of(rt.scene).instruments[iid]
    inst.energia = 0.1
    rt.command("touch", {"state": "tap", "target": "planta"})
    rt.tick(0.03)
    assert _istatus(rt, iid)["energia"] > 0.9 and inst.glow.max() > 0.5
    rt.set_param(iid, "aletear", True)
    rt.tick(0.03)
    vivo = registry_of(rt.scene).instruments[iid]
    assert isinstance(vivo, ObjetoVivo) and max(t0 for t0, _ in vivo.flaps) > time.time() - 2


def test_croc_aliases_still_work_without_a_croc_in_the_scene(tmp_path, fake_midi):
    rt = _rt(tmp_path)
    nada = str(tmp_path / "sin_escaneo")
    e1 = rt.spawn("croc_synth", None, {"scan": nada})["id"]
    e2 = rt.spawn("croc_alive", None, {"scan": nada})["id"]
    assert rt.tick(0.03).max() > 0
    rt.command("touch", {"state": "tap", "target": "croc:0"})
    rt.tick(0.03)
    assert _notes(fake_midi, 1) and _istatus(rt, e1)["kind"] == "cocodrilo" and _istatus(rt, e2)["kind"] == "cocodrilo_vivo"


# ---------------------------------------------------------------------- toque_onda

def test_onda_reaches_objects_bounces_and_sounds(fake_midi):
    s = sala()
    o = Onda()
    o.set_objects(scene_objects_px(s))
    o.handle({"id": "t", "state": "down", "x": 1100, "y": 500}, 10.0)
    assert len(o.waves) == 1
    o.step(10.05)
    assert o.hits == 0                                              # aún no llega a nadie
    for k in range(1, 50):                                          # 700 px/s: en 2,5 s alcanzó todo lo cercano
        o.step(10.05 + k * 0.05)
    assert o.hits >= 3 and o.bounces >= 2                           # rebotes desde los puntos de contacto
    assert set(o.flash) >= {"sofa", "planta"} and o.flash["sofa"] > 0.1
    assert _notes(fake_midi, 3)
    img = np.zeros((1080, 1920, 3), np.uint8)
    o.handle({"id": "u", "state": "down", "x": 1100, "y": 500}, 12.45)
    o.step(12.5)
    o.render(img, 1.0, 1.0, 12.5)
    x, y, w, h = cv2.boundingRect(np.asarray(s.objects["sofa"].contour, np.float32))
    assert img[y:y + h, x:x + w].max() > 0


def test_toque_onda_in_runtime_does_not_steal_touches(tmp_path, fake_midi):
    rt = _rt(tmp_path)
    syn = rt.spawn("objeto_synth", "sofa", {"zones": 5})["id"]
    onda = rt.spawn("toque_onda", None, {"velocidad": 1e6, "alcance": 1e7})["id"]
    rt.tick(0.03)
    rt.command("touch", {"state": "tap", "target": "sofa:2"})
    rt.tick(0.03)
    rt.tick(0.03)
    st = _istatus(rt, onda)
    assert st["alcances"] >= 3 and _istatus(rt, syn)["hits"] == 1      # suena el sofá y la onda igual


# ---------------------------------------------------------------------- mano_luz

def test_mano_luz_two_hands_two_lights_and_halo(tmp_path):
    rt = _rt(tmp_path)
    iid = rt.spawn("mano_luz", None, {"scan": str(tmp_path / "sin_escaneo")})["id"]
    for _ in range(4):                                             # las manos llegan cada cuadro (como desde live)
        rt.command("hands", {"hands": [{"id": 0, "x": 460, "y": 260}, {"id": 1, "x": 1410, "y": 195, "height": 0.2}]})
        frame = rt.tick(0.03)
    st = _istatus(rt, iid)
    assert len(st["luces"]) == 2 and st["fuente"] == "manos"
    assert st["cerca"]["cuadro1"] > 0.5 and st["cerca"]["libro"] > 0.5 and st["cerca"].get("sofa", 0) < st["cerca"]["cuadro1"]
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    assert g[260, 460] > 60 and g[1000, 1800] == 0                 # luz bajo la mano, oscuro lejos
    rt.command("hands", {"hands": []})
    rt.command("touch", {"state": "down", "x": 900, "y": 800, "id": "q"})
    rt.tick(0.03)
    assert _istatus(rt, iid)["fuente"] == "toques"


# ---------------------------------------------------------------------- juego_memoria

def _memoria():
    s = sala()
    objs = scene_objects_px(s)
    hud = pick_hud(objs)
    m = Memoria(params={"semilla": 1, "inicio": 2})
    m.set_objects([o for o in objs if o.id != hud.id], hud)
    return m


def test_memoria_sequence_score_and_error(fake_midi):
    m = _memoria()
    assert m.hud.id == "libro" and "libro" not in [o.id for o in m.objs]
    m.step(100.0)
    assert m.state == "mostrando" and len(m.seq) == 2
    shown = []
    for k in range(60):
        now = 100.0 + k * 0.05
        m.step(now)
        cur = m.current(now)
        if cur and (not shown or shown[-1] != cur):
            shown.append(cur)
    assert shown == m.seq and m.state == "esperando"
    for oid in m.seq:
        x, y = m.by_id(oid).center
        m.handle({"id": "t", "state": "down", "x": x, "y": y}, 104.0)
    assert m.state == "bien" and m.score == 2
    m.step(105.5)
    assert m.state == "mostrando" and len(m.seq) == 3
    m.step(110.0)
    assert m.state == "esperando"
    wrong = next(o for o in m.objs if o.id != m.seq[0])
    m.handle({"id": "t", "state": "down", "x": wrong.center[0], "y": wrong.center[1]}, 110.1)
    assert m.state == "error" and m.best == 2
    img = np.zeros((1080, 1920, 3), np.uint8)
    m.render(img, 1.0, 1.0, 110.2)
    assert img[120:270, 1300:1520].max() > 0                         # puntaje en el HUD (libro)
    m.step(113.0)
    assert m.state == "mostrando" and m.score == 0 and len(m.seq) == 2


def test_juego_memoria_in_runtime_plays_by_touch_command(tmp_path, fake_midi):
    rt = _rt(tmp_path)
    iid = rt.spawn("juego_memoria", None, {"semilla": 4, "inicio": 1, "paso_s": 0.05, "pausa_s": 0.0})["id"]
    rt.tick(0.03)
    st = _istatus(rt, iid)
    assert st["hud"] == "libro" and st["estado"] == "mostrando" and len(st["secuencia"]) == 1
    time.sleep(0.25)
    rt.tick(0.03)
    assert _istatus(rt, iid)["estado"] == "esperando"
    rt.command("touch", {"state": "tap", "target": "memoria"})       # toca el que corresponde
    rt.tick(0.03)
    assert _istatus(rt, iid)["puntaje"] == 1 and _notes(fake_midi, 4)
    i2 = rt.spawn("juego_memoria", None, {"hud": "cuadro1"})["id"]
    rt.tick(0.03)
    assert _istatus(rt, i2)["hud"] == "cuadro1"


# ---------------------------------------------------------------------- mirada

def test_gaze_command_hook(tmp_path):
    rt = _rt(tmp_path)
    assert rt.command("gaze", {"x": 400, "y": 300})["gaze"]["x"] == 400.0
    assert rt.scene.gaze["source"] == "api"
    g = rt.command("gaze", {"objeto": "sofá"})["gaze"]
    assert g["objeto"] == "sofa" and 150 < g["x"] < 1080
    assert rt.command("gaze", {})["gaze"]["objeto"] == "sofa"
    assert any(e["type"] == "gaze" for e in rt.events)
    with pytest.raises(KeyError):
        rt.command("gaze", {"objeto": "no-existe"})
    with pytest.raises(ValueError):
        rt.command("gaze", {"x": "a", "y": 1})
    assert rt.command("gaze", {"off": True}) == {"gaze": {}} and rt.scene.gaze == {}
    assert "gaze" in rt.command("schema") and "gaze" in rt.scene.to_json()


def test_mirada_focuses_dwell_and_chooses():
    s = sala()
    m = Mirada(params={"dwell_s": 0.5, "suave": 1.0})
    m.set_objects(scene_objects_px(s))
    m.update_gaze(None, 1.0)
    assert m.pt is None and m.focus is None
    for k in range(8):
        m.update_gaze({"x": 460, "y": 260, "t": 1.0 + k * 0.1}, 1.0 + k * 0.1)
    assert m.focus == "cuadro1" and m.chosen == "cuadro1" and m.focus_k["cuadro1"] > 0.5
    img = np.zeros((1080, 1920, 3), np.uint8)
    m.render(img, 1.0, 1.0, 2.0)
    assert img[260, 460].max() > 0 and img[800, 1800].max() == 0
    m.update_gaze({"x": 460, "y": 260, "t": 0.0}, 10.0)                # mirada vieja (stale): se apaga
    assert m.pt is None


def test_mirada_effect_follows_gaze_command(tmp_path):
    rt = _rt(tmp_path)
    iid = rt.spawn("mirada", None, {"dwell_s": 0.0})["id"]
    rt.command("gaze", {"objeto": "planta"})
    rt.tick(0.03)
    rt.tick(0.03)
    assert _istatus(rt, iid)["foco"] == "planta" and rt.scene.gaze.get("mirando") == "planta"


# ---------------------------------------------------------------------- rendimiento y snapshot

def test_new_effects_frame_budget(tmp_path):
    """Mediana de update+draw por cuadro a 1920x1080 con toques y manos (presupuesto de diseño: < 4 ms; aquí con
    holgura para máquinas cargadas)."""
    from playground.effects.base import Canvas
    rt = _rt(tmp_path)
    nada = str(tmp_path / "sin_escaneo")
    rt.command("hands", {"hands": [{"id": 0, "x": 460, "y": 260}, {"id": 1, "x": 1410, "y": 700}]})
    rt.command("gaze", {"objeto": "sofa"})
    out = {}
    for name, tgt, params in (("objeto_synth", "sofa", {}), ("objeto_vivo", "cuadro1", {}), ("toque_onda", None, {}),
                              ("mano_luz", None, {"scan": nada}), ("juego_memoria", None, {}), ("mirada", None, {})):
        inst = rt.instances[rt.spawn(name, tgt, params)["id"]]
        times = []
        for f in range(25):
            if f % 5 == 0:
                rt.command("touch", {"state": "tap", "x": 300 + 50 * f, "y": 700})
            c = Canvas(np.zeros((1080, 1920, 3), np.uint8), 1.0, flip_y=False)
            t0 = time.perf_counter()
            inst.update(1 / 30, rt.scene)
            inst.draw(c)
            times.append(1000 * (time.perf_counter() - t0))
        out[name] = float(np.median(times[5:]))
    assert all(v < 15.0 for v in out.values()), out


def _scan5():
    for p in (os.environ.get("SLSCAN_SCAN"), ROOT / "captures" / "scan5"):
        if p and (Path(p) / "segments.json").exists():
            return Path(p)
    return None


@pytest.mark.skipif(_scan5() is None, reason="sin captures/scan5")
def test_snapshot_cli_objeto_vivo_on_scan5(tmp_path, monkeypatch):
    from playground.__main__ import main
    monkeypatch.setenv("SLSCAN_SCAN", str(_scan5()))
    out = tmp_path / "x.png"
    assert main(["snapshot", "--spawn", "objeto_vivo:buda", "--spawn", "toque_onda", "--seconds", "0.4",
                 "--out", str(out)]) == 0
    img = cv2.imread(str(out))
    assert img is not None and img.max() > 0
