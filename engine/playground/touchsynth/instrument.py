"""Base de los instrumentos de la mesa táctil: reciben toques (px del proyector), suenan por MIDI y dibujan.

Un instrumento no sabe del runtime: `handle(ev, now)` con eventos del comando "touch" ({id, state, x, y, …}),
`step(now)` por cuadro, `render(img, sx, sy, now)` sobre el lienzo (sx, sy = lienzo / proyector), `status()`,
`hit(x, y)` (¿un toque que empieza aquí es mío?) y `close()` (apaga sus notas). Los efectos de
`playground/effects/` (croc_synth, touch_draw, marimba, linterna) los envuelven; el estado vive en el registro
de la escena (`registry.py`), así que un hot reload del efecto no corta notas ni borra trazos.
"""

from __future__ import annotations

import colorsys
import math
from dataclasses import dataclass

import cv2
import numpy as np

from .music import Notes


class Instrument:
    kind = "instrumento"
    priority = 0                 # quién se queda con un toque que empieza donde se pisan dos instrumentos
    claims_touch = True

    def __init__(self):
        self.last_seq = 0
        self.notes = Notes()
        self.params: dict = {}

    def configure(self, params: dict) -> None:
        self.params = dict(params)

    def _ensure_scan(self, req: str, need_height: bool = True) -> None:
        """Carga el escaneo solo si cambió el pedido (`scan` en los params; "" = el de por defecto)."""
        from .scan import ScanData
        req = str(req or "")
        if getattr(self, "scan", None) is None or (req and req != getattr(self, "_scan_req", None)):
            self.scan = ScanData.load(req or None, need_height)
        self._scan_req = req

    def hit(self, x: float, y: float) -> bool:
        return False

    def handle(self, ev: dict, now: float) -> None:
        pass

    def step(self, now: float) -> None:
        self.notes.tick(now)

    def render(self, img: np.ndarray, sx: float, sy: float, now: float) -> None:
        pass

    def resolve(self, index: int | None) -> tuple[float, float] | None:
        """Centro de la zona `index` (para tocar por nombre desde el MCP)."""
        return None

    def status(self) -> dict:
        return {"kind": self.kind}

    def close(self) -> None:
        self.notes.panic()


# ---------------------------------------------------------------------- utilidades de dibujo

def hsv_bgr(h: float, s: float = 1.0, v: float = 1.0) -> tuple[int, int, int]:
    """h, s, v en 0..1 → tupla BGR 0..255."""
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, max(0.0, min(1.0, s)), max(0.0, min(1.0, v)))
    return int(b * 255), int(g * 255), int(r * 255)


def scale_bgr(c, k: float) -> tuple[int, int, int]:
    k = max(0.0, k)
    return tuple(int(max(0, min(255, v * k))) for v in c)


PALETTES = {
    "arcoiris": lambda t: hsv_bgr(0.0 + 0.78 * t, 0.9, 1.0),
    "fuego": lambda t: hsv_bgr(0.0 + 0.16 * t, 1.0, 1.0),
    "hielo": lambda t: hsv_bgr(0.48 + 0.2 * t, 0.85, 1.0),
    "selva": lambda t: hsv_bgr(0.22 + 0.25 * t, 0.9, 1.0),
}


def palette(name: str, n: int) -> list[tuple[int, int, int]]:
    f = PALETTES.get(name, PALETTES["arcoiris"])
    return [f(i / max(1, n - 1)) for i in range(n)]


def to_canvas(pts, sx: float, sy: float) -> np.ndarray:
    p = np.asarray(pts, np.float64).reshape(-1, 2) * (sx, sy)
    return np.round(p).astype(np.int32).reshape(-1, 1, 2)


@dataclass
class Pulse:
    """Visual de un golpe: anillo que se expande y se apaga."""
    x: float
    y: float
    t0: float
    color: tuple[int, int, int]
    life: float = 0.8
    r0: float = 10.0
    r1: float = 90.0

    def alive(self, now: float) -> bool:
        return now - self.t0 < self.life

    def draw(self, img: np.ndarray, sx: float, sy: float, now: float) -> None:
        a = (now - self.t0) / self.life
        if a < 0 or a >= 1:
            return
        k = (1 - a) ** 1.5
        r = (self.r0 + (self.r1 - self.r0) * math.sqrt(a)) * (sx + sy) / 2
        c = (int(round(self.x * sx)), int(round(self.y * sy)))
        cv2.circle(img, c, max(1, int(r)), scale_bgr(self.color, k), max(1, int(4 * (sx + sy) / 2 * k + 1)), cv2.LINE_AA)
        cv2.circle(img, c, max(1, int(r * 0.35)), scale_bgr(self.color, 0.6 * k), -1, cv2.LINE_AA)


def add_layer(img: np.ndarray, layer: np.ndarray, x0: int = 0, y0: int = 0) -> None:
    """Suma luz (proyección): img[y0:, x0:] += layer, recortando a los bordes."""
    h, w = layer.shape[:2]
    H, W = img.shape[:2]
    xa, ya, xb, yb = max(0, x0), max(0, y0), min(W, x0 + w), min(H, y0 + h)
    if xb <= xa or yb <= ya:
        return
    img[ya:yb, xa:xb] = cv2.add(img[ya:yb, xa:xb], np.ascontiguousarray(layer[ya - y0:yb - y0, xa - x0:xb - x0]))
