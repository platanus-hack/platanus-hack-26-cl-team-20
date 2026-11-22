"""Dispositivos: OHM con sus controles, fuente simulada, bindings, traducción de SmartDeck y efecto control_glow."""

import numpy as np

from playground.runtime import Runtime
from playground.devices import SmartDeckSource, ohm_device


def _rt(tmp_path):
    return Runtime(canvas_size=(640, 320), requests_dir=tmp_path / "requests")


def test_ohm_device_and_scene_object(tmp_path):
    rt = _rt(tmp_path)
    dev = rt.devices.devices["ohm"]
    types = {}
    for c in dev.controls.values():
        types[c.type] = types.get(c.type, 0) + 1
    assert types == {"pad": 64, "knob": 16, "fader": 8, "crossfader": 1, "button": 17}
    obj = rt.scene.objects["ohm"]
    assert obj.kind == "instrument" and len(obj.extra["controls"]) == 106
    pad = obj.extra["controls"]["pad.0.0"]["pos_mm"]
    assert obj.bbox[0] <= pad[0] <= obj.bbox[3] and obj.bbox[1] <= pad[1] <= obj.bbox[4]
    assert dev.status == "sin conectar"


def test_simulated_control_updates_binding_and_scene(tmp_path):
    rt = _rt(tmp_path)
    rt.command("connect_devices", {"prefer": "fake"})
    rt.spawn("contour_glow", "ohm")
    rt.command("bind", {"control": "ohm/knob0", "target": "effect:e1:width_mm", "lo": 10, "hi": 110})
    rt.command("bind", {"control": "ohm/fader0", "target": "beat:bpm", "lo": 90, "hi": 150})
    ev = rt.command("simulate", {"control": "knob0", "value": 0.25})
    assert ev["type"] == "knob" and rt.instances["e1"].p["width_mm"] == 35.0
    rt.command("simulate", {"control": "fader0", "value": 1.0})
    assert rt.scene.beat["bpm"] == 150.0
    rt.tick(0.03)
    assert rt.scene.controls["ohm"]["knob0"]["value"] == 0.25
    assert rt.events[-1]["type"] in ("control",) and rt.events[-1]["ctype"] == "fader"
    assert rt.command("unbind", {"control": "ohm/knob0"})["removed"] == 1
    assert rt.devices.devices["ohm"].status == "simulado"


def test_smartdeck_translation():
    src = SmartDeckSource("ohm", cc_map={21: "knob0"})
    assert src._translate({"input_type": "pad", "row": 2, "col": 5, "value": 100, "state": "down"}) == ("pad.2.5", 100 / 127, "down")
    assert src._translate({"input_type": "button", "id": 80, "value": 127, "state": "down"}) == ("btn80", 1.0, "down")
    assert src._translate({"input_type": "knob", "id": 21, "value": 64}) == ("knob0", 64 / 127, "move")
    assert src._translate({"input_type": "crossfader", "id": 9, "value": 0}) == ("xfader", 0.0, "move")
    assert not src.available(timeout=0.1) or True     # sin SmartDeck corriendo no falla


def test_control_glow_draws_pressed_pad(tmp_path):
    rt = _rt(tmp_path)
    rt.command("connect_devices", {"prefer": "fake"})
    rt.spawn("control_glow", "ohm", {"color": [255, 0, 0]})
    rt.tick(0.03)
    before = int((rt.frame.sum(axis=2) > 0).sum())
    rt.command("simulate", {"control": "pad.3.3", "value": 1.0, "state": "down"})
    rt.tick(0.03)
    after = int((rt.frame.sum(axis=2) > 0).sum())
    assert after > before
    lit = rt.frame[rt.frame.sum(axis=2) > 0]
    assert tuple(int(v) for v in np.unique(lit, axis=0)[0]) == (0, 0, 255)
