"""Vector hierarchy, real-mask clipping, stale-source guards and runtime plugins."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import cv2
import numpy as np
import pytest

from playground.vectortrace import Trace, prefix, trace_image, write_bundle
from playground.scene import Scene, SceneObject
from playground.runtime import Runtime
from playground.effects.base import Canvas


def test_nested_holes_palette_and_svg():
    image = np.zeros((100, 100, 3), np.uint8)
    image[:] = [0, 0, 255]
    image[20:80, 20:80] = [0, 255, 0]
    image[40:60, 40:60] = [0, 0, 255]
    mask = np.ones((100, 100), np.uint8)
    mask[5:10, 5:10] = 0
    trace = trace_image(image, mask, colors=2, min_area=0, simplify=0)
    assert [255, 0, 0] in trace.palette and [0, 255, 0] in trace.palette
    assert any(r.depth == 2 and r.parent is not None for r in trace.rings)
    assert any(r.holes for r in trace.regions)
    assert Trace.from_json(trace.to_json()).to_json() == trace.to_json()
    ET.fromstring(trace.svg())
    assert 'fill-rule="evenodd"' in trace.svg()
    assert trace_image(image, mask, colors=2, min_area=0, simplify=0).to_json() == trace.to_json()


def test_prefix_uses_arc_length_not_vertex_count():
    pts = [[0, 0], [90, 0], [100, 0]]
    np.testing.assert_allclose(prefix(pts, .5, closed=False), [[0, 0], [50, 0]])
    assert prefix(pts, 0).shape == (0, 2)
    np.testing.assert_allclose(prefix(pts, 1)[-1], pts[0])


@pytest.fixture
def bundle(tmp_path):
    scan = tmp_path / "scan"; scan.mkdir()
    image = np.full((160, 240, 3), [0, 0, 220], np.uint8)
    image[50:110, 90:150] = [0, 230, 0]
    image[70:90, 110:130] = [240, 0, 0]
    mask = np.zeros(image.shape[:2], np.uint8)
    cv2.ellipse(mask, (120, 80), (90, 65), 15, 0, 360, 255, -1)
    mask[20:40, 100:115] = 0  # actual occlusion hole, unrelated to the object quad
    cv2.imwrite(str(scan / "scene_pov.png"), image)
    cv2.imwrite(str(tmp_path / "mask.png"), mask)
    path = write_bundle(scan / "scene_pov.png", tmp_path / "mask.png", tmp_path / "bundle", target_id="painting")
    scene = Scene(table_mm=(240, 160), projector_px=True, scan_dir=str(scan))
    scene.objects["painting"] = SceneObject("painting", "painting", "frame", [0, 0, 0, 2, 2, 0],
                                             [[0, 0], [2, 0], [0, 2]])
    rt = Runtime(scene=scene, demo=False, canvas_size=(240, 160), requests_dir=tmp_path / "requests")
    return path, rt, mask


@pytest.mark.parametrize("name", ["vector_drawing", "vector_fill", "vector_pencil"])
def test_runtime_clips_mask_ignores_wrong_quad_and_exposes_params(bundle, name):
    path, rt, mask = bundle
    st = rt.spawn(name, "painting", dict(bundle=str(path), progress=0))
    assert rt.tick(.01).max() == 0
    rt.set_param(st["id"], "progress", 1)
    image = rt.tick(.01).copy()
    assert image.any() and not image[mask == 0].any()
    assert st["id"] in rt.instances
    assert not [e for e in rt.events if e["type"] == "effect_error"]
    assert rt.instances[st["id"]].state()["regions"] > 0
    assert {"speed", "width", "color", "order", "progress", "audio"} <= st["params"].keys()
    before = rt.instances[st["id"]].prepared
    rt.tick(.01)
    assert rt.instances[st["id"]].prepared is before


def test_fill_holes_rgb_order_and_changes(bundle):
    path, rt, mask = bundle
    st = rt.spawn("vector_fill", "painting", dict(bundle=str(path), progress=1))
    image = rt.tick(.01).copy()
    assert image[80, 120, 0] > 200  # blue in BGR, inside a nested region
    assert image[50, 60, 2] > 180  # red in BGR
    rt.set_param(st["id"], "progress", .3)
    a = rt.tick(.01).copy()
    rt.set_param(st["id"], "order", "reverse")
    b = rt.tick(.01)
    assert not np.array_equal(a, b)
    rt.set_param(st["id"], "progress", 1)
    rt.set_param(st["id"], "source_colors", False)
    rt.set_param(st["id"], "color", [255, 0, 0])
    image = rt.tick(.01)
    assert image[:, :, 2].any() and not image[:, :, :2].any()


def test_audio_changes_line_width_and_no_loop_finishes(bundle):
    path, rt, mask = bundle
    st = rt.spawn("vector_drawing", "painting", dict(bundle=str(path), progress=-1, loop=False, speed=1, audio="bass"))
    a = rt.tick(2).copy()
    rt.scene.audio["bass"] = 1
    inst = rt.instances[st["id"]]
    inst.update(.01, rt.scene)
    canvas = Canvas(np.zeros_like(a), 1, flip_y=False)
    inst.draw(canvas)
    assert canvas.img.sum() > a.sum()
    assert not canvas.img[mask == 0].any()


@pytest.mark.parametrize("change", ["source", "mask", "target", "size"])
def test_stale_or_misaligned_bundle_fails_closed(bundle, change):
    path, rt, _ = bundle
    st = rt.spawn("vector_fill", "painting", dict(bundle=str(path), progress=1))
    rt.tick(.01)
    if change == "source":
        cv2.imwrite(str(Path(rt.scene.scan_dir) / "scene_pov.png"), np.zeros((160, 240, 3), np.uint8))
    elif change == "mask":
        cv2.imwrite(str(path.parent / "mask.png"), np.zeros((130, 180), np.uint8))
    else:
        data = json.loads(path.read_text())
        data["target_id" if change == "target" else "projector_size"] = "another" if change == "target" else [100, 100]
        path.write_text(json.dumps(data))
    assert rt.tick(.01).max() == 0
    assert st["id"] not in rt.instances
    assert any(e["type"] == "effect_error" for e in rt.events)


def test_reject_non_native_canvas_and_wrong_scene(bundle):
    path, rt, _ = bundle
    st = rt.spawn("vector_fill", "painting", dict(bundle=str(path), progress=1))
    rt.tick(.01)
    inst = rt.instances[st["id"]]
    with pytest.raises(ValueError, match="native"):
        inst.draw(Canvas(np.zeros((160, 240, 3), np.uint8), 1, flip_y=True))
    rt.scene.projector_px = False
    with pytest.raises(ValueError, match="legacy"):
        inst.update(.01, rt.scene)


def test_empty_and_bounded_trace():
    image = np.zeros((20, 20, 3), np.uint8)
    assert not trace_image(image, np.zeros((20, 20))).regions
    with pytest.raises(ValueError):
        trace_image(image, colors=100)
    with pytest.raises(ValueError):
        trace_image(image, np.ones((10, 10)))
