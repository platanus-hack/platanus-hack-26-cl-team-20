"""Livid OHM RGB: skin, controles y mapa MIDI, tomados de ~/ohmrgb (solo lectura).

Geometría de `~/ohmrgb/ui/src/editor/constants.js` (viewBox 700x442): grid 8x8 desde (215,15) paso 39, pads
de 30; 16 knobs; 8 faders; crossfader; 17 botones (64..80). El mapa CC del hardware lo consulta el driver por
SysEx (`ohmrgb.py::_query_mappings`); mientras no haya equipo, se usa el orden del driver (knob N = CC N+?)
como etiqueta y se corrige en vivo con el primer evento (`learn`).
"""

from __future__ import annotations

from pathlib import Path

from .midi import Control, Device
from ..scene import SceneObject

OHMRGB_DIR = Path.home() / "ohmrgb"

OHM_SKIN = {
    "viewbox": [700, 442],
    "svg": str(OHMRGB_DIR / "ui" / "public" / "faceplate_OHMRGB.svg"),
    "license": "skin propia de Max (~/ohmrgb); referencia, no se copia al repo",
    "grid": {"x0": 215, "y0": 15, "step": 39, "pad": 30},
    "knobs": {0: [18, 18], 1: [66, 18], 2: [114, 18], 3: [162, 18], 4: [18, 66], 5: [66, 66], 6: [114, 66], 7: [162, 66],
              8: [18, 114], 9: [66, 114], 10: [114, 114], 11: [162, 114],
              12: [536, 114], 13: [584, 114], 14: [632, 114], 15: [680, 114]},
    "faders": {0: [18, 160], 1: [66, 160], 2: [114, 160], 3: [162, 160], 4: [536, 160], 5: [584, 160], 6: [632, 160], 7: [680, 160]},
    "fader_len": 150,
    "crossfader": [354, 336],
    "buttons": {64: [234, 336], 65: [475, 336], 66: [21, 358], 67: [69, 358], 68: [117, 358], 69: [165, 358],
                70: [539, 358], 71: [587, 358], 72: [635, 358], 73: [683, 358],
                74: [540, 6], 75: [588, 6], 76: [636, 6], 77: [540, 40], 78: [588, 40], 79: [636, 40], 80: [683, 24]},
    "button_roles": {64: "page_prev", 65: "page_next", 80: "voice", **{b: "channel" for b in range(66, 74)},
                     **{b: "section" for b in range(74, 80)}},
}

# tamaño físico aproximado del OHM RGB (mm) para ubicarlo en la mesa mientras no haya fit (003)
OHM_SIZE_MM = (310.0, 196.0)


def ohm_controls() -> dict[str, Control]:
    s = OHM_SKIN
    out: dict[str, Control] = {}
    g = s["grid"]
    for r in range(8):
        for c in range(8):
            out[f"pad.{r}.{c}"] = Control(f"pad.{r}.{c}", "pad", [g["x0"] + c * g["step"] + g["pad"] / 2, g["y0"] + r * g["step"] + g["pad"] / 2],
                                          [g["pad"], g["pad"]], midi={"note": r * 8 + c})
    for i, pos in s["knobs"].items():
        out[f"knob{i}"] = Control(f"knob{i}", "knob", list(pos), [30, 30], midi={"cc": None})
    for i, pos in s["faders"].items():
        out[f"fader{i}"] = Control(f"fader{i}", "fader", [pos[0], pos[1] + s["fader_len"] / 2], [20, s["fader_len"]], midi={"cc": None})
    out["xfader"] = Control("xfader", "crossfader", list(s["crossfader"]), [200, 20], midi={"cc": None})
    for b, pos in s["buttons"].items():
        out[f"btn{b}"] = Control(f"btn{b}", "button", list(pos), [28, 20], midi={"note": b, "role": s["button_roles"].get(b, "")})
    return out


def ohm_device() -> Device:
    return Device("ohm", "Livid OHM RGB", "midi", port_match=["OhmRGB", "Ohm RGB", "OHM"], has_midi=True,
                  skin={k: OHM_SKIN[k] for k in ("viewbox", "svg", "license")}, controls=ohm_controls(),
                  note="dueño del puerto: SmartDeck si corre (ws 8090); si no, el playground directo")


def skin_to_mm(pos_skin: list[float], origin_mm: tuple[float, float], size_mm=OHM_SIZE_MM) -> list[float]:
    """Coordenadas de la skin -> mm de la mesa, con el OHM apoyado sin rotar (hasta que 003 dé el fit real)."""
    w, h = OHM_SKIN["viewbox"]
    return [origin_mm[0] + pos_skin[0] / w * size_mm[0], origin_mm[1] + (h - pos_skin[1]) / h * size_mm[1]]


def ohm_scene_object(dev: Device, origin_mm: tuple[float, float] = (1150.0, 150.0)) -> SceneObject:
    """El OHM como objeto de la escena (instrumento) con sus controles hijos en mm (colocación provisional)."""
    x0, y0 = origin_mm
    w, h = OHM_SIZE_MM
    controls = {cid: {"type": c.type, "pos_mm": skin_to_mm(c.pos, origin_mm), "size_mm": [c.size[0] / 700 * w, c.size[1] / 442 * h]}
                for cid, c in dev.controls.items()}
    return SceneObject("ohm", "OHM RGB", "instrument", [x0, y0, 0, x0 + w, y0 + h, 25],
                       [[x0, y0], [x0 + w, y0], [x0 + w, y0 + h], [x0, y0 + h]], plane=[0, 0, 1, 25], texture="mate",
                       albedo=[0.1, 0.1, 0.1], tracked=False,
                       extra={"device": "ohm", "controls": controls, "placement": "provisional: sin fit (pedido 003)"})
