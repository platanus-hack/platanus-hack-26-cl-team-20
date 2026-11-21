"""Efectos de ambiente (pared_viva, plantas, libros, sombras_largas, lluvia_estrellas) sin hardware.

Escena sintética en px del proyector (como la del escaneo) con objetos genéricos por kind: una planta de contorno
irregular, dos libros rectangulares y una torre alta. Nada por nombre: los efectos eligen por forma/kind. Si existe
captures/scan5 (o SLSCAN_SCAN), también se corren sobre la escena real.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from playground.runtime import Runtime
from playground.scene import Scene, SceneObject

AMBIENTE = ("pared_viva", "plantas", "libros", "sombras_largas", "lluvia_estrellas")
W, H = 960, 540


def _star(cx, cy, r0, r1, n=7):
    pts = []
    for i in range(2 * n):
        a = math.pi * i / n
        r = r1 if i % 2 == 0 else r0
        pts.append([cx + r * math.cos(a), cy + r * math.sin(a)])
    return pts


def _rect(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _scene() -> Scene:
    s = Scene(table_mm=(float(W), float(H)))
    s.projector_px = True
    def add(oid, kind, poly, z1):
        xs, ys = [p[0] for p in poly], [p[1] for p in poly]
        s.objects[oid] = SceneObject(oid, oid, kind, [min(xs), min(ys), 0, max(xs), max(ys), z1], poly)
    add("table", "table", _rect(0, 0, W, H), 0)
    s.objects["table"].plane = [0, 0, 1, 0]
    add("planta", "planta", _star(220, 300, 40, 110), 200)
    add("libro_a", "libro", _rect(560, 380, 600, 500), 25)
    add("libro_b", "libro", _rect(620, 380, 655, 500), 25)
    add("torre", "prop", _rect(700, 120, 780, 260), 300)
    return s


def _rt(tmp_path, scene=None, size=(W, H)):
    rt = Runtime(scene=scene or _scene(), canvas_size=size, demo=False, requests_dir=tmp_path / "requests")
    rt.scene.objects.pop("ohm", None)            # el OHM que agrega el runtime es de la escena real, no de esta
    return rt


def _mask(poly, erode=0):
    m = np.zeros((H, W), np.uint8)
    cv2.fillPoly(m, [np.int32(poly).reshape(-1, 1, 2)], 1)
    if erode:
        m = cv2.erode(m, np.ones((2 * erode + 1, 2 * erode + 1), np.uint8))
    return m > 0


def _run(rt, n=30, dt=1 / 30):
    acc = np.zeros((rt.canvas_size[1], rt.canvas_size[0], 3), np.uint8)
    for _ in range(n):
        f = rt.tick(dt)
        np.maximum(acc, f, out=acc)
    return acc


def _errors(rt):
    return [e for e in rt.events if e["type"] == "effect_error"]


def test_gallery_meta_in_spanish_with_aliases(tmp_path):
    rt = _rt(tmp_path)
    gal = {g["name"]: g for g in rt.registry.gallery()}
    for name in AMBIENTE:
        assert name in gal, rt.registry.errors
        g = gal[name]
        assert g["label"] and len(g["description"]) > 40 and len(g["aliases"]) >= 3
        assert "capa" in g["params"]


@pytest.mark.parametrize("name", AMBIENTE)
def test_each_effect_draws_on_synthetic_scene(tmp_path, name):
    rt = _rt(tmp_path)
    rt.spawn(name)
    acc = _run(rt, 40)
    assert not _errors(rt), _errors(rt)
    assert acc.max() > 40, f"{name} no dibujó nada"
    st = rt.state()["effects"][0]
    assert st["alive"] and st["ms"] >= 0


@pytest.mark.parametrize("name", AMBIENTE)
def test_each_effect_runs_on_demo_scene_in_mm(tmp_path, name):
    rt = Runtime(canvas_size=(640, 320), requests_dir=tmp_path / "requests")     # demo: mm con y hacia arriba
    rt.spawn(name)
    acc = _run(rt, 15)
    assert not _errors(rt), _errors(rt)
    assert acc.max() > 0


def test_pared_viva_lights_walls_not_objects(tmp_path):
    rt = _rt(tmp_path)
    rt.spawn("pared_viva", None, {"respiracion": 0.0, "intensidad": 1.0})
    acc = _run(rt, 45)
    inside = acc[_mask(_rect(700, 120, 780, 260), erode=12)].max()
    outside = acc[~_mask(_rect(680, 100, 800, 280))].mean()
    assert inside < 10 and outside > 5


def test_pared_viva_fiesta_rings_on_beat(tmp_path):
    rt = _rt(tmp_path)
    st = rt.spawn("pared_viva", None, {"modo": "fiesta"})
    rt.scene.beat.update({"playing": True, "phase": 0.9})
    inst = rt.instances[st["id"]]
    rt.tick(1 / 30)
    rt.scene.beat["phase"] = 0.05                                 # vuelta de fase = golpe
    rt.tick(1 / 30)
    assert inst.rings, "un golpe del beat tiene que lanzar un anillo"
    rt.command("audio", {"features": {"bass": 0.9, "beat": 1.0, "onsets": 5}})
    rt.tick(1 / 30)
    rt.command("audio", {"features": {"bass": 0.9, "beat": 1.0, "onsets": 6}})
    rt.tick(1 / 30)
    assert len(inst.rings) >= 2 and inst.pulse.live


def test_plantas_picks_irregular_and_grows_from_base(tmp_path):
    rt = _rt(tmp_path)
    st = rt.spawn("plantas", None, {"crecer_s": 4.0, "viento": 0.0, "chispas": 2.0})
    inst = rt.instances[st["id"]]
    for _ in range(12):                                          # ~0,4 s: el frente va por abajo
        rt.tick(1 / 30)
    s = inst.state()
    assert s["objetos_vivos"] == ["planta"]                       # los libros y la torre son rectángulos
    m = _mask(_star(220, 300, 40, 110))
    ys = np.nonzero(m)[0]
    mid = (ys.min() + ys.max()) / 2
    lum = rt.frame.max(axis=2).astype(float)
    low = lum[m & (np.arange(H)[:, None] > mid)].mean()
    high = lum[m & (np.arange(H)[:, None] < mid - 30)].mean()
    assert low > high + 5, (low, high)
    _run(rt, 20)
    assert inst.state()["chispas_vivas"] > 0
    # por lista explícita también toma un rectángulo
    st2 = rt.spawn("plantas", None, {"objetos": "torre"})
    rt.tick(1 / 30)
    assert rt.instances[st2["id"]].state()["objetos_vivos"] == ["torre"]


def test_libros_walks_objects_with_titles_and_index(tmp_path):
    rt = _rt(tmp_path)
    st = rt.spawn("libros", None, {"objetos": "libro", "paso_s": 0.4, "titulos": "Rayuela|Ficciones"})
    inst = rt.instances[st["id"]]
    rt.tick(1 / 30)
    s = inst.state()
    assert s["orden"] == ["libro_a", "libro_b"] and s["actual"] == "libro_a"
    assert [it["title"] for it in inst.items] == ["Rayuela", "Ficciones"]
    seen = set()
    for _ in range(40):
        rt.tick(1 / 30)
        seen.add(inst.state()["actual"])
    assert seen == {"libro_a", "libro_b"}
    ax, ay = inst.anchor
    assert 0 <= ax < W and 0 <= ay < H
    assert not any(_mask(p)[int(ay), int(ax)] for p in (_rect(560, 380, 655, 500),))
    assert not _errors(rt)


def test_sombras_largas_cast_shadow_away_from_lamp(tmp_path):
    rt = _rt(tmp_path)
    rt.spawn("sombras_largas", None, {"azimut": 0.0, "lampara": False, "mancha": 0.0, "objetos": "torre"})
    f = _run(rt, 2).max(axis=2).astype(float)
    # lámpara hacia +x: la sombra de la torre cae a su izquierda (−x), el lado derecho queda iluminado
    left = f[150:230, 560:690].mean()
    right = f[150:230, 800:900].mean()
    assert right > 60 and left < right * 0.5, (left, right)


def test_lluvia_estrellas_hits_light_contours(tmp_path):
    rt = _rt(tmp_path)
    st = rt.spawn("lluvia_estrellas", None, {"densidad": 300, "velocidad": 600.0})
    inst = rt.instances[st["id"]]
    acc = _run(rt, 60)
    s = inst.state()
    assert s["choques"] > 0 and set(s["objetos"]) == {"planta", "libro_a", "libro_b", "torre"}
    ring = _mask(_rect(700, 120, 780, 260)) & ~_mask(_rect(700, 120, 780, 260), erode=4)
    assert acc[ring].max() > 0
    inside = _mask(_rect(700, 120, 780, 260), erode=16)          # (el destello del choque mide ~12 px)
    assert rt.frame[inside].max() < 30                           # las estrellas no entran a los objetos


def _real_scan():
    for c in (os.environ.get("SLSCAN_SCAN"), Path(__file__).parent.parent / "captures" / "scan5"):
        if c and (Path(c) / "segments.json").exists():
            return Path(c)
    return None


@pytest.mark.skipif(_real_scan() is None, reason="sin captures/scan5")
def test_real_scan5_all_ambient_effects(tmp_path):
    from playground.scene import scene_from_scan
    rt = Runtime(scene=scene_from_scan(_real_scan()), demo=False, requests_dir=tmp_path / "requests")
    for name in AMBIENTE:
        rt.spawn(name)
    acc = _run(rt, 6)
    assert not _errors(rt), _errors(rt)
    assert acc.mean() > 5
