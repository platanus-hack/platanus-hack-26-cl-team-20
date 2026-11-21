"""Marcas de la mesa táctil: anillo en cada mano/dedo detectado (verde = toca, ámbar = sobre la mesa) y, si se pide,
una luz base tenue en todo el lienzo para que la Osmo vea las manos aunque la visual sea negra. Lo spawnea
`python -m playground.touchsynth live --ver` (y lo mata al salir)."""

from __future__ import annotations

import time

import cv2
import numpy as np

from ..touchsynth.registry import touch_state
from .base import Effect, Canvas, bgr


class TouchMarks(Effect):
    name = "touch_marks"
    label = "Marcas de toques"
    description = ("Anillo en cada dedo detectado por las cámaras (verde = toca, ámbar = flota) y luz base tenue "
                   "(luz_base 0-60) para que la Osmo vea las manos.")
    needs = ("touch",)
    targets = ("*",)
    params = {"luz_base": 0, "rings": True, "touch_color": [0, 255, 0], "hover_color": [255, 200, 0],
              "radius": 16, "stale_s": 0.6}

    def update(self, dt, scene):
        super().update(dt, scene)
        ts = touch_state(scene)
        now = time.time()
        self.marks = [(h.get("tip") or [h["x"], h["y"]], bool(h.get("touching"))) for h in ts.hands.values()
                      if now - float(h.get("t", now)) <= float(self.p["stale_s"])]
        self.marks += [([t["x"], t["y"]], True) for t in ts.touches.values()]

    def draw(self, canvas: Canvas):
        img = canvas.img
        lb = int(self.p["luz_base"])
        if lb > 0:
            np.maximum(img, lb, out=img)
        if not self.p["rings"]:
            return
        sx, sy = img.shape[1] / 1920.0, img.shape[0] / 1080.0
        r = max(2, int(float(self.p["radius"]) * (sx + sy) / 2))
        for (x, y), touching in self.marks:
            col = bgr(self.p["touch_color"] if touching else self.p["hover_color"])
            cv2.circle(img, (int(x * sx), int(y * sy)), r, col, 2, cv2.LINE_AA)
