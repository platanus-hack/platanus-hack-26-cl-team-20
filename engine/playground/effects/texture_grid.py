"""Grilla según textura: dibuja sobre la superficie un patrón que depende de su material y geometría."""

from __future__ import annotations

import numpy as np
import cv2

from .base import Effect, Canvas, pulse, bgr

PATTERNS = {"madera": "lines", "vidrio": "scan", "mate": "dots", "tela": "weave", "piel": "cells", "brillante": "scan"}


class TextureGrid(Effect):
    name = "texture_grid"
    label = "Grilla por textura"
    description = "Elige el patrón por la textura del objeto (madera: vetas, vidrio: barrido, mate: puntos, piel: celdas) y lo recorta a su contorno."
    needs = ("textures", "geometry", "contours")
    targets = ("*",)
    params = {"color": [255, 180, 40], "pitch_mm": 40, "speed": 0.3, "pattern": "auto"}   # color en RGB

    def update(self, dt, scene):
        super().update(dt, scene)
        self.obj = self.object(scene)

    def draw(self, canvas: Canvas):
        if self.obj is None:
            return
        pat = self.p["pattern"] if self.p["pattern"] != "auto" else PATTERNS.get(self.obj.texture, "dots")
        mask = np.zeros(canvas.img.shape[:2], np.uint8)
        pts = np.array([canvas.project(x, y) for x, y in self.obj.contour], np.int32).reshape(-1, 1, 2)
        cv2.fillPoly(mask, [pts], 255)
        layer = np.zeros_like(canvas.img)
        b, g, r = bgr(self.p["color"])         # params en RGB; OpenCV en BGR
        x0, y0 = canvas.project(self.obj.bbox[0], self.obj.bbox[4])
        x1, y1 = canvas.project(self.obj.bbox[3], self.obj.bbox[1])
        pitch = max(4, int(self.p["pitch_mm"] * canvas.px_per_mm))
        shift = int((self.t * self.p["speed"] * pitch) % pitch)
        if pat == "lines":
            for x in range(x0 - pitch, x1 + pitch, pitch):
                cv2.line(layer, (x + shift, y0), (x + shift + pitch // 3, y1), (b, g, r), 2, cv2.LINE_AA)
        elif pat == "scan":
            y = y0 + int(((self.t * self.p["speed"] * 2) % 1.0) * max(1, y1 - y0))
            cv2.line(layer, (x0, y), (x1, y), (b, g, r), 6, cv2.LINE_AA)
        elif pat == "weave":
            for x in range(x0, x1, pitch):
                cv2.line(layer, (x, y0), (x, y1), (b, g, r), 1)
            for y in range(y0, y1, pitch):
                cv2.line(layer, (x0, y), (x1, y), (b, g, r), 1)
        elif pat == "cells":
            rad = max(3, pitch // 3)
            for yy, y in enumerate(range(y0, y1, pitch)):
                for x in range(x0 + (pitch // 2 if yy % 2 else 0), x1, pitch):
                    cv2.circle(layer, (x, y), int(rad * pulse(self.t + x * 0.01, 0.5)), (b, g, r), 2, cv2.LINE_AA)
        else:  # dots
            for y in range(y0, y1, pitch):
                for x in range(x0, x1, pitch):
                    cv2.circle(layer, (x + shift, y), 3, (b, g, r), -1, cv2.LINE_AA)
        layer[mask == 0] = 0
        canvas.img[:] = cv2.add(canvas.img, layer)
