"""Pulso del bajo: el halo del Buda y el brillo de las teclas siguen `audio.bass`; cada golpe es un flash corto.

Dibuja directo en px del proyector sobre lo real (formas del escaneo, `playground/stage.py`). Si se spawnea sobre
un objeto de la escena, el halo va alrededor de ese objeto en vez del Buda. Sin audio queda un contorno tenue.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from .base import Effect, Canvas, bgr, offset_contour
from ..audio.features import audio_level, audio_source, parse_key
from ..stage import stage


def _scaled(color_bgr, k: float) -> tuple[int, int, int]:
    k = max(0.0, min(1.0, k))
    return tuple(int(c * k) for c in color_bgr)


def _mix(a, b, t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return tuple(int(x * (1 - t) + y * t) for x, y in zip(a, b))


class BassPulse(Effect):
    name = "bass_pulse"
    label = "Pulso del bajo"
    description = ("El halo del Buda y las teclas laten con el bajo (audio.bass); cada golpe (audio.beat) es un flash "
                   "corto hacia flash_color. band elige el rasgo (bass, sub, low, kick, mid, high, rms) y la fuente "
                   "(ch1.bass, R.rms… = un canal de la mezcladora).")
    needs = ("audio", "geometry")
    targets = ("*",)
    params = {"color": [255, 170, 40], "flash_color": [255, 255, 255], "key_color": [0, 200, 255],   # RGB
              "band": "bass", "gain": 1.0, "flash": 0.8, "width_px": 70, "layers": 4, "halo": True,
              "keys": True, "idle": 0.12, "soft": True, "scan": ""}

    def update(self, dt, scene):
        super().update(dt, scene)
        a = getattr(scene, "audio", None) or {}
        self.lvl = audio_level(a, self.p["band"], self.p["gain"])
        src = (parse_key(self.p["band"]) or ("mix", ""))[0]             # band 'ch2.bass': golpes de ese canal
        self.beat = float(audio_source(a, src).get("beat", 0.0)) * float(self.p["flash"])
        self.obj = self.object(scene)

    def _halo_poly(self, canvas: Canvas, st: dict):
        if self.obj is not None and self.obj.kind != "table" and len(self.obj.contour) >= 3:
            return np.array([canvas.project(x, y) for x, y in self.obj.contour], float)
        return np.asarray(st["buda"], float) if st.get("buda") else None

    def draw(self, canvas: Canvas):
        st = stage(self.p["scan"] or None, canvas.size)
        h, w = canvas.img.shape[:2]
        base, flash = bgr(self.p["color"]), bgr(self.p["flash_color"])
        e = max(float(self.p["idle"]), self.lvl)
        col = _mix(base, flash, self.beat)

        if self.p["halo"]:
            poly = self._halo_poly(canvas, st)
            if poly is not None:
                s = 4 if self.p["soft"] else 1                       # halo suave: se dibuja a 1/4 y se difumina
                sh, sw = -(-h // s), -(-w // s)
                small = np.zeros((sh, sw, 3), np.uint8)
                n = max(1, int(self.p["layers"]))
                reach = float(self.p["width_px"]) * (0.3 + 0.9 * e + 0.5 * self.beat)
                for k in range(n, 0, -1):
                    pts = np.asarray(offset_contour(poly, reach * k / n), float) / s
                    inten = (0.25 + 0.75 * e + self.beat) * (1 - (k - 1) / n)
                    cv2.polylines(small, [pts.astype(np.int32).reshape(-1, 1, 2)], True, _scaled(col, inten),
                                  max(1, int(10 / s + 14 / s * e)), cv2.LINE_AA)
                if s > 1:
                    small = cv2.GaussianBlur(small, (0, 0), 3)
                    small = cv2.convertScaleAbs(small, alpha=2.0)     # compensa lo que baja el difuminado
                    layer = cv2.resize(small, (sw * s, sh * s), interpolation=cv2.INTER_LINEAR)[:h, :w]
                else:
                    layer = small
                cv2.add(canvas.img, layer, dst=canvas.img)
                cv2.polylines(canvas.img, [poly.astype(np.int32).reshape(-1, 1, 2)], True,
                              _scaled(col, 0.35 + 0.65 * e + self.beat), 2, cv2.LINE_AA)

        if self.p["keys"] and st.get("keys"):
            kc, n = bgr(self.p["key_color"]), len(st["keys"])
            kcol = _mix(kc, flash, self.beat)
            layer = np.zeros_like(canvas.img)
            for i, key in enumerate(st["keys"]):
                u = i / max(1, n - 1)
                wave = 0.8 + 0.2 * math.sin(2 * math.pi * (1.5 * u - 0.6 * self.t))   # leve ola para que respire
                inten = max(float(self.p["idle"]) * 0.6, self.lvl * wave) + self.beat
                cv2.fillConvexPoly(layer, np.asarray(key, float).astype(np.int32), _scaled(kcol, inten), cv2.LINE_AA)
            cv2.add(canvas.img, layer, dst=canvas.img)
