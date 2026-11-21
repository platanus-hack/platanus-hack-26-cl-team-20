"""Efectos de cuadros (cuadro_portal, cuadro_vivo, marco_respira, parallax) sin hardware.

Escena sintética en px del proyector con nombres cualquiera (una lámina, un muro, un sofá y una planta delante de la
lámina): los efectos caen dentro de su objeto, sirven para cualquier tipo, eligen solo el cuadro más grande, no pintan
sobre lo que está delante, el portal abre/cierra por parámetro y por toque, el audio y la cabeza se notan. Si existe
captures/scan5 (o SLSCAN_SCAN) se renderizan sobre la escena real, se mide el tiempo por cuadro y se prueba el CLI.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from playground import cuadros as cq
from playground.effects.base import Canvas
from playground.runtime import Runtime
from playground.scene import Scene, SceneObject, demo_scene, scene_from_scan
from playground.touchsynth.registry import touch_state

EFFECTS = ("cuadro_portal", "cuadro_vivo", "marco_respira", "parallax")
LAMINA = [[300, 150], [800, 120], [820, 520], [320, 540]]
MURO = [[1200, 200], [1600, 200], [1600, 500], [1200, 500]]
SOFA = [[900, 700], [1500, 700], [1500, 950], [900, 950]]
PLANTA = [[690, 380], [780, 380], [780, 470], [690, 470]]
DT = 1 / 30


def _real_scan() -> Path | None:
    for c in (os.environ.get("SLSCAN_SCAN"), Path(__file__).parent.parent / "captures" / "scan5",
              Path(__file__).resolve().parents[1].parents[2] / "captures" / "scan5"):
        if c and (Path(c) / "scene_pov.png").exists() and (Path(c) / "segments.json").exists():
            return Path(c)
    return None


def _obj(oid, kind, poly, **extra):
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    return SceneObject(oid, oid, kind, [min(xs), min(ys), 0, max(xs), max(ys), 10], [list(map(float, p)) for p in poly],
                       extra={"aliases": [], **extra})


@pytest.fixture(scope="module")
def synth_dir(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("cuadros")
    pov = np.full((1080, 1920, 3), (50, 50, 50), np.uint8)
    pts = np.asarray(LAMINA, np.int32).reshape(-1, 1, 2)
    cv2.fillPoly(pov, [pts], (140, 90, 40))
    cv2.circle(pov, (560, 260), 70, (230, 240, 250), -1)              # una luna clara (fuente de partículas)
    cv2.rectangle(pov, (380, 380), (640, 500), (40, 120, 200), -1)     # un campo con detalle
    for x in range(390, 640, 14):
        cv2.line(pov, (x, 385), (x + 6, 495), (20, 60, 120), 2)
    cv2.fillPoly(pov, [np.asarray(MURO, np.int32).reshape(-1, 1, 2)], (200, 200, 205))
    cv2.fillPoly(pov, [np.asarray(SOFA, np.int32).reshape(-1, 1, 2)], (60, 70, 160))
    cv2.fillPoly(pov, [np.asarray(PLANTA, np.int32).reshape(-1, 1, 2)], (40, 160, 40))
    cv2.imwrite(str(d / "scene_pov.png"), pov)
    h = np.zeros((1080, 1920), np.float32)
    cv2.fillPoly(h, [np.asarray(SOFA, np.int32).reshape(-1, 1, 2)], 1.0)
    h = cv2.GaussianBlur(h, (0, 0), 40) * 300                          # el sofá tiene relieve de verdad
    np.save(d / "height_proj.npy", h)
    return d


def make_scene(scan_dir: Path | None) -> Scene:
    s = Scene(table_mm=(1920.0, 1080.0))
    s.projector_px = True
    s.scan_dir = str(scan_dir) if scan_dir else None
    s.objects["mesa"] = _obj("mesa", "table", [[0, 0], [1920, 0], [1920, 1080], [0, 1080]])
    s.objects["lamina"] = _obj("lamina", "frame", LAMINA)
    s.objects["muro"] = _obj("muro", "plane", MURO)
    s.objects["sofa"] = _obj("sofa", "prop", SOFA)
    s.objects["planta"] = _obj("planta", "prop", PLANTA)
    return s


def _rt(scene) -> Runtime:
    rt = Runtime(scene=scene, demo=False)
    rt.scene.objects.pop("ohm", None)                                  # el OHM provisional no es parte de esta escena
    return rt


def _run(rt, n=8):
    for _ in range(n):
        rt.tick(DT)
    return rt.frame


def _box(img, poly, inset=0):
    p = np.asarray(poly, int)
    x0, y0 = p.min(axis=0) + inset
    x1, y1 = p.max(axis=0) - inset
    return img[y0:y1, x0:x1]


def test_gallery_metadata():
    rt = Runtime(scene=make_scene(None), demo=False)
    for name in EFFECTS:
        cls = rt.registry.classes[name]
        m = cls.meta()
        assert m["label"] and len(m["description"]) > 60 and m["aliases"], name
        assert "capa" in m["params"]
        assert name not in rt.registry.errors
    words = {a for n in EFFECTS for a in rt.registry.classes[n].aliases}
    assert {"portal", "cuadro vivo", "marco", "parallax"} <= words


@pytest.mark.parametrize("name", EFFECTS)
@pytest.mark.parametrize("target", ["lamina", "muro", "sofa"])
def test_renders_inside_any_object(synth_dir, name, target):
    rt = _rt(make_scene(synth_dir))
    st = rt.spawn(name, target)
    img = _run(rt)
    assert st["id"] in rt.instances, "el efecto se cayó"
    poly = rt.scene.objects[target].contour
    assert _box(img, poly, 12).mean() > 3 or name == "marco_respira"
    if name == "marco_respira":                                         # solo la banda: borde encendido, centro apagado
        cx, cy = np.asarray(poly).mean(axis=0).astype(int)
        assert img[cy - 20:cy + 20, cx - 20:cx + 20].max() == 0
        assert img.sum() > 0
    far = img[1000:1080, 0:200] if target != "sofa" else img[0:80, 1700:1920]
    assert far.max() == 0                                               # nada fuera de su lugar


def test_default_target_is_largest_frame(synth_dir):
    for name in EFFECTS:
        rt = _rt(make_scene(synth_dir))
        rt.spawn(name)
        img = _run(rt, 4)
        assert img.sum() > 0, name
        assert _box(img, LAMINA, 30).mean() > _box(img, MURO, 10).mean() or name == "marco_respira", name
        assert _box(img, MURO, 10).max() == 0 and _box(img, SOFA, 10).max() == 0, name


def test_does_not_paint_what_is_in_front(synth_dir):
    for name in ("parallax", "cuadro_portal"):
        rt = _rt(make_scene(synth_dir))
        rt.spawn(name, "lamina", {"estrellas": 0} if name == "cuadro_portal" else None)
        img = _run(rt)
        planta = _box(img, PLANTA, 10).mean()
        cerca = img[300:360, 520:620].mean()
        assert planta < 0.2 * cerca, (name, planta, cerca)


def test_portal_iris_param_and_touch(synth_dir):
    rt = _rt(make_scene(synth_dir))
    st = rt.spawn("cuadro_portal", "lamina", {"abierto": 0.0, "velocidad_iris": 6.0, "estrellas": 0})
    inst = rt.instances[st["id"]]
    cx, cy = np.asarray(LAMINA).mean(axis=0).astype(int)
    closed = _run(rt, 10)[cy - 60:cy + 60, cx - 60:cx + 60].mean()
    assert inst.aper == 0.0
    rt.set_param(st["id"], "abierto", 1.0)
    opened = _run(rt, 12)[cy - 60:cy + 60, cx - 60:cx + 60].mean()
    assert inst.aper == pytest.approx(1.0) and opened > closed + 5
    rt.touch({"state": "tap", "x": float(cx), "y": float(cy)})       # tocar el cuadro lo cierra
    _run(rt, 1)
    assert inst.p["abierto"] == 0.0
    rt.touch({"state": "tap", "x": 1800.0, "y": 1000.0})             # tocar fuera no hace nada
    _run(rt, 1)
    assert inst.p["abierto"] == 0.0


def test_audio_bass_pumps(synth_dir):
    def render(bass):
        s = make_scene(synth_dir)
        rt = _rt(s)
        st = rt.spawn("cuadro_portal", "lamina", {"audio": "bass", "estrellas": 0})
        inst = rt.instances[st["id"]]
        c = Canvas(np.zeros((1080, 1920, 3), np.uint8), 1.0, flip_y=False)
        for _ in range(5):
            s.audio = {**s.audio, "bass": bass}
            inst.update(DT, s)
        inst.draw(c)
        return inst, c.img
    i0, a = render(0.0)
    i1, b = render(1.0)
    assert i0.lvl == 0.0 and i1.lvl == 1.0
    assert b.astype(np.int64).sum() > a.astype(np.int64).sum() * 1.05


def test_parallax_follows_head_and_hand(synth_dir):
    imgs = []
    for head in ([1.0, 0.0], [-1.0, 0.0]):
        rt = _rt(make_scene(synth_dir))
        st = rt.spawn("parallax", "lamina", {"cabeza": head, "suavizado": 50.0})
        imgs.append(_run(rt, 6))
        assert rt.instances[st["id"]].state()["fuente"] == "param"
    assert np.abs(imgs[0].astype(int) - imgs[1].astype(int)).mean() > 0.3
    rt = _rt(make_scene(synth_dir))
    st = rt.spawn("parallax", "sofa")
    rt.set_hands({"hands": [{"id": "m1", "x": 1450, "y": 720}]})
    _run(rt, 10)
    s = rt.instances[st["id"]].state()
    assert s["fuente"] == "mano" and s["cabeza"][0] > 0.3 and s["profundidad"] == "relieve"
    rt.set_hands({"hands": []})
    _run(rt, 2)
    assert rt.instances[st["id"]].state()["fuente"] == "vaiven"


def test_follows_nudge(synth_dir, tmp_path):
    import shutil
    d = tmp_path / "scan"
    shutil.copytree(synth_dir, d)
    rt = _rt(make_scene(d))
    rt.spawn("marco_respira", "lamina", {"brillo_exterior": 0})
    a = _run(rt, 2).copy()
    rt.scene.nudge("lamina", 40, 0)
    b = _run(rt, 2)
    xa = np.nonzero(a.sum(axis=(0, 2)))[0]
    xb = np.nonzero(b.sum(axis=(0, 2)))[0]
    assert xb.min() - xa.min() == pytest.approx(40, abs=3)


def test_demo_scene_in_mm():
    rt = Runtime(scene=demo_scene(), demo=True)
    ids = [rt.spawn(n, "frame")["id"] for n in EFFECTS]
    _run(rt, 4)
    assert all(i in rt.instances for i in ids)
    assert rt.frame.sum() > 0


def test_no_frames_draws_nothing():
    s = Scene(table_mm=(1920.0, 1080.0))
    s.projector_px = True
    s.objects["mesa"] = _obj("mesa", "table", [[0, 0], [1920, 0], [1920, 1080], [0, 1080]])
    rt = _rt(s)
    for n in EFFECTS:
        rt.spawn(n)
    img = _run(rt, 3)
    assert len(rt.instances) == 4 and img.max() == 0


def test_poly_sources():
    o = _obj("x", "frame", [[0, 0], [10, 0], [10, 10]])
    o.contour = []
    o.extra["projector_contour_px"] = [[1, 1], [20, 1], [20, 20], [1, 20]]
    assert cq.poly_of(o)[1] == [20.0, 1.0]
    o.extra["outline"] = [[2, 2], [30, 2], [30, 30]]
    assert cq.poly_of(o)[1] == [30.0, 2.0]


# ---------------------------------------------------------------------- escena real

@pytest.mark.skipif(_real_scan() is None, reason="sin captures/scan5")
def test_real_scan_render_and_time():
    scan = _real_scan()
    s = scene_from_scan(scan)
    rt = Runtime(scene=s, demo=False)
    frame = s.find("frame")                                             # el cuadro de la escena, por tipo
    assert frame is not None
    for name in EFFECTS:
        st = rt.spawn(name, frame.id)
        inst = rt.instances[st["id"]]
        c = Canvas(np.zeros((1080, 1920, 3), np.uint8), 1.0, flip_y=False)
        inst.update(DT, s); inst.draw(c)                               # preparación
        ts = []
        for _ in range(15):
            c.img[:] = 0
            t0 = time.thread_time()
            inst.update(DT, s); inst.draw(c)
            ts.append((time.thread_time() - t0) * 1000)
        assert _box(c.img, frame.contour, 30).mean() > 3 or name == "marco_respira"
        assert c.img.sum() > 0
        med = float(np.median(ts))
        print(f"{name}: {med:.2f} ms/cuadro (CPU) sobre {scan.name}")
        assert med < 25, (name, med)                                    # tope holgado: la máquina puede estar cargada
        rt.kill(st["id"])


@pytest.mark.skipif(_real_scan() is None, reason="sin captures/scan5")
def test_snapshot_cli(tmp_path, monkeypatch):
    from playground.__main__ import main
    monkeypatch.setenv("SLSCAN_SCAN", str(_real_scan()))
    monkeypatch.setattr(Runtime, "run", lambda self, seconds=None: [self.tick(DT) for _ in range(6)] and None)
    out = tmp_path / "x.png"
    assert main(["snapshot", "--spawn", "cuadro_portal:cuadro", "--spawn", "marco_respira", "--seconds", "0.2",
                 "--out", str(out)]) == 0
    img = cv2.imread(str(out))
    assert img is not None and img.sum() > 0
