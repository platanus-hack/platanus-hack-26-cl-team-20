"""Playground: galería, spawn/draw, hot reload, plugin roto, cola de pedidos y API HTTP."""

import time
from pathlib import Path

import numpy as np

from playground.runtime import Runtime, build_app

GLOW = Path(__file__).parent.parent / "playground" / "effects" / "contour_glow.py"


def _rt(tmp_path):
    return Runtime(canvas_size=(640, 320), requests_dir=tmp_path / "requests")


def test_gallery_and_spawn_draws(tmp_path):
    rt = _rt(tmp_path)
    names = {g["name"] for g in rt.registry.gallery()}
    assert {"contour_glow", "texture_grid", "frame_feed"} <= names
    rt.spawn("contour_glow", "cocodrilo"); rt.spawn("texture_grid", "mesa"); rt.spawn("frame_feed", "cuadro")
    frame = rt.tick(0.03)
    assert frame.shape == (320, 640, 3) and frame.max() > 0
    assert len(rt.state()["effects"]) == 3


def test_spawn_validates_targets(tmp_path):
    rt = _rt(tmp_path)
    try:
        rt.spawn("frame_feed", "cocodrilo")
        assert False, "frame_feed no aplica a instrumentos"
    except ValueError:
        pass
    try:
        rt.spawn("no_existe")
        assert False
    except KeyError:
        pass


def test_hot_reload_keeps_instances_and_survives_errors(tmp_path):
    src = GLOW.read_text()
    rt = _rt(tmp_path)
    rt.spawn("contour_glow", "cocodrilo", {"color": [1, 2, 3]}); rt.tick(0.03)
    try:
        GLOW.write_text(src.replace('label = "Halo de contorno"', 'label = "Halo v2"')); time.sleep(0.02); rt.tick(0.03)
        assert rt.registry.classes["contour_glow"].label == "Halo v2"
        assert [i.p["color"] for i in rt.instances.values()] == [[1, 2, 3]]
        GLOW.write_text(src + "\nesto no es python\n"); time.sleep(0.02); rt.tick(0.03)
        assert "contour_glow" in rt.registry.errors and rt.events[-1]["type"] == "reload_error"
        assert len(rt.instances) == 1                       # la instancia vieja sigue viva
        GLOW.write_text(src.replace("def draw(self, canvas: Canvas):", "def draw(self, canvas: Canvas):\n        raise RuntimeError('boom')"))
        time.sleep(0.02); rt.tick(0.03)
        assert rt.events[-1]["type"] == "effect_error" and len(rt.instances) == 0
    finally:
        GLOW.write_text(src)
    time.sleep(0.02); rt.tick(0.03)
    assert rt.registry.errors == {} and rt.registry.classes["contour_glow"].label == "Halo de contorno"


def test_request_queue_roundtrip(tmp_path):
    rt = _rt(tmp_path)
    r = rt.command("request", {"title": "Halo que siga al cocodrilo", "body": "al levantarlo", "kind": "sound", "from": "h4"})
    assert r["id"] == "001" and Path(r["path"]).name == "001-halo-que-siga-al-cocodrilo.md"
    assert rt.command("requests", {"status": "open"})["requests"][0]["kind"] == "sound"
    rt.command("set_status", {"id": "001", "status": "done", "note": "listo"})
    assert rt.requests.summary()["done"] == 1
    assert "listo" in Path(r["path"]).read_text()


def test_http_api(tmp_path):
    from fastapi.testclient import TestClient
    rt = _rt(tmp_path)
    c = TestClient(build_app(rt))
    assert c.get("/api/schema").json()["spawn"]["effect"] == "str"
    r = c.post("/api/command", json={"cmd": "spawn", "args": {"effect": "contour_glow", "target": "cocodrilo"}}).json()
    assert r["ok"] and r["result"]["effect"] == "contour_glow"
    rt.tick(0.03)
    assert c.get("/api/frame.png").headers["content-type"] == "image/png"
    assert c.post("/api/command", json={"cmd": "nada"}).status_code == 400
    assert c.get("/api/state").json()["stats"]["ticks"] == 1


def test_colors_are_rgb_outside_bgr_inside(tmp_path):
    """Un halo turquesa [0,255,200] (RGB) tiene que pintar píxeles BGR (200,255,0), no (0,255,200)."""
    rt = _rt(tmp_path)
    rt.spawn("contour_glow", "cocodrilo", {"color": [0, 255, 200], "layers": 1, "width_mm": 1})
    frame = rt.tick(0.03)
    lit = frame[(frame.sum(axis=2) > 0)]
    assert len(lit) > 0
    # el color dominante del contorno es la tupla BGR del RGB pedido
    vals, counts = np.unique(lit, axis=0, return_counts=True)
    top = tuple(int(v) for v in vals[counts.argmax()])
    assert top == (200, 255, 0), top
    rt.kill(); rt.spawn("texture_grid", "mesa", {"color": [255, 0, 0], "pattern": "lines"})
    frame = rt.tick(0.03)
    lit = frame[(frame.sum(axis=2) > 0)]
    vals, counts = np.unique(lit, axis=0, return_counts=True)
    assert tuple(int(v) for v in vals[counts.argmax()]) == (0, 0, 255)      # rojo RGB -> BGR (0,0,255)


def test_demo_scene_starts_with_no_effects(tmp_path):
    rt = _rt(tmp_path)
    assert rt.state()["effects"] == []


def test_hot_reload_tolerates_file_replaced_between_glob_and_stat(tmp_path, monkeypatch):
    rt = _rt(tmp_path)
    rt.spawn('contour_glow','cocodrilo')
    rt.tick(.03)
    original = Path.stat
    def transient(path,*args,**kwargs):
        if path == GLOW:
            raise FileNotFoundError(str(path))
        return original(path,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path,'stat',transient)
        frame=rt.tick(.03)
        assert frame.any() and len(rt.instances)==1
    # The next frame discovers the file again; rendering never died.
    assert rt.tick(.03).any() and not rt.registry.errors
