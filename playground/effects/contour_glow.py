"""Halo de contorno: sigue el borde del objeto (y su movimiento) con un halo que late con el beat.
Con `audio` (bass, sub, low, kick, mid, high, rms, beat) el tamaño y la intensidad siguen ese rasgo del audio."""

from __future__ import annotations

from .base import Effect, Canvas, pulse, offset_contour, bgr
from ..audio.features import audio_level, audio_mode


class ContourGlow(Effect):
    name = "contour_glow"
    label = "Halo de contorno"
    description = ("Capas concéntricas alrededor del contorno del objeto; late con el beat y se estira en la dirección del "
                   "movimiento. audio='bass' (o sub, low, kick, mid, high, rms) hace que tamaño e intensidad sigan el audio.")
    needs = ("contours", "beat")
    targets = ("prop", "instrument", "frame", "screen", "person")
    params = {"color": [0, 255, 200], "width_mm": 30, "layers": 4, "speed": 1.0, "beat_sync": True,   # color en RGB
              "audio": "", "audio_gain": 1.0}                 # audio "" = apagado; "bass" = late con el bajo

    def update(self, dt, scene):
        super().update(dt, scene)
        self.phase = scene.beat["phase"] if self.p["beat_sync"] and scene.beat["playing"] else pulse(self.t, self.p["speed"])
        self.obj = self.object(scene)
        mode = audio_mode(self.p.get("audio"))
        self.lvl = None if mode is None else audio_level(getattr(scene, "audio", None), mode, self.p.get("audio_gain", 1.0))

    def draw(self, canvas: Canvas):
        if self.obj is None:
            return
        color = bgr(self.p["color"])          # params en RGB; OpenCV en BGR
        lvl = getattr(self, "lvl", None)
        for k in range(int(self.p["layers"]), 0, -1):
            if lvl is None:
                spread = self.p["width_mm"] * k / self.p["layers"] * (0.6 + 0.4 * (1 - self.phase))
                alpha = 0.25 * (1 - (k - 1) / self.p["layers"])
            else:                                 # audio: tamaño e intensidad siguen el rasgo elegido
                spread = self.p["width_mm"] * k / self.p["layers"] * (0.3 + 0.9 * lvl)
                alpha = min(1.0, (0.06 + 0.5 * lvl) * (1 - (k - 1) / self.p["layers"]))
            canvas.polygon(offset_contour(self.obj.contour, spread), color, thickness=3, alpha=alpha)
        canvas.polygon(self.obj.contour, color, thickness=2)
