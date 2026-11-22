"""Halo de contorno: sigue el borde del objeto (y su movimiento) con un halo que late con el beat.
Con `audio` (bass, sub, low, kick, mid, high, rms, beat) el tamaño y la intensidad siguen ese rasgo del audio.

Los defaults hablan WUI: Contorno, color `SEÑAL_GUIA`, ancho de línea del wordmark y ritmo de tinta.
El operador puede sobreescribir cualquier `params` desde el dashboard. Para efectos coloridos de
actividad se pasa un `color` explícito y queda fuera del sistema de señal."""

from __future__ import annotations

from .base import Effect, Canvas, pulse, offset_contour, bgr
from .wui_preset import amplitud_estado, aplicar_wui
from .wui_tokens import SEÑAL_POR_ESTADO
from ..audio.features import audio_level, audio_mode
from ..audio.hooks import fire as fire_cue


class ContourGlow(Effect):
    name = "contour_glow"
    label = "Halo de contorno"
    description = ("Capas concéntricas alrededor del contorno del objeto; late con el beat y se estira en la dirección del "
                   "movimiento. audio='bass' (o sub, low, kick, mid, high, rms) hace que tamaño e intensidad sigan el audio. "
                   "Defaults en lenguaje WUI (Contorno · señal de guía); se puede devolver al color saturado pasando `color`.")
    needs = ("contours", "beat")
    targets = ("prop", "instrument", "frame", "screen", "person")
    params = aplicar_wui(
        {"color": list(SEÑAL_POR_ESTADO["guia"]), "width_mm": 28, "layers": 4, "speed": 1.0,
         "beat_sync": False, "audio": "", "audio_gain": 1.0, "wui_estado": "apareciendo"},
        forma="contorno",
    )

    def update(self, dt, scene):
        super().update(dt, scene)
        self.phase = scene.beat["phase"] if self.p["beat_sync"] and scene.beat["playing"] else pulse(self.t, self.p["speed"])
        self.obj = self.object(scene)
        mode = audio_mode(self.p.get("audio"))
        self.lvl = None if mode is None else audio_level(getattr(scene, "audio", None), mode, self.p.get("audio_gain", 1.0))
        self.env = amplitud_estado(self.t, self.p.get("wui_estado", "apareciendo"))
        # Hook sonoro: Guía · la pista se asienta (envelope cruza 0,5 por primera vez en apareciendo).
        if (self.p.get("wui_estado") == "apareciendo" and self.env >= 0.5
                and not getattr(self, "_guia_fired", False)):
            fire_cue("guia", f"contour_glow:{self.instance_id}:guia")
            self._guia_fired = True

    def draw(self, canvas: Canvas):
        if self.obj is None:
            return
        color = bgr(self.p["color"])          # params en RGB; OpenCV en BGR
        lvl = getattr(self, "lvl", None)
        env = getattr(self, "env", 1.0)
        for k in range(int(self.p["layers"]), 0, -1):
            if lvl is None:
                spread = self.p["width_mm"] * k / self.p["layers"] * (0.6 + 0.4 * (1 - self.phase)) * env
                alpha = 0.25 * (1 - (k - 1) / self.p["layers"]) * env
            else:                                 # audio: tamaño e intensidad siguen el rasgo elegido
                spread = self.p["width_mm"] * k / self.p["layers"] * (0.3 + 0.9 * lvl)
                alpha = min(1.0, (0.06 + 0.5 * lvl) * (1 - (k - 1) / self.p["layers"]))
            canvas.polygon(offset_contour(self.obj.contour, spread), color, thickness=3, alpha=alpha)
        canvas.polygon(self.obj.contour, color, thickness=2)
