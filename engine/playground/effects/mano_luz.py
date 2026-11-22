"""Mano luz: cada mano es una fuente de luz sobre el relieve escaneado (height_proj) y los objetos cercanos reaccionan
con un halo que crece con la cercanía; dos manos = dos luces (cálida y fría). Generaliza linterna, calculando la luz
solo en la ventana de cada mano. Lógica en playground/touchsynth/interactivo.py (ManoLuz)."""

from __future__ import annotations

import time

from ..touchsynth.effectkit import InstrumentEffectMixin, SceneObjectsMixin
from ..touchsynth.interactivo import ManoLuz
from ..touchsynth.registry import touch_state
from .base import Effect, Canvas


class ManoLuzEffect(SceneObjectsMixin, InstrumentEffectMixin, Effect):
    name = "mano_luz"
    label = "Mano de luz"
    description = ("Cada mano (o toque, si no llegan manos) es una luz que ilumina el relieve del escaneo, con caída "
                   "radial (radio) y altura según la mano; los objetos cercanos se encienden con un halo de su color "
                   "que crece al acercarse (alcance). Dos manos = dos luces (colores). demo=true: dos luces orbitan "
                   "solas. Más barato que linterna (sin sombras).")
    aliases = ("mano de luz", "manos de luz", "luz en la mano", "linterna", "ilumina con la mano", "dos luces", "antorcha")
    needs = ("geometry", "touch", "people", "contours")
    targets = ("*",)
    params = {**{k: v for k, v in ManoLuz.DEFAULTS.items()}, "objetos": "", "capa": 2}

    def update(self, dt, scene):
        super().update(dt, scene)
        if not self.p.get("scan") and getattr(scene, "scan_dir", None):
            self.p["scan"] = str(scene.scan_dir)                   # el relieve de la escena viva
        objs = self._scene_objs(scene)
        inst = self._run(scene, lambda: ManoLuz(params=self.p))
        if getattr(inst, "objs", None) is not objs:
            inst.set_objects(objs)
        ts = touch_state(scene)
        inst.update_sources(ts.hands, ts.touches, time.time())

    def draw(self, canvas: Canvas):
        self._draw(canvas)
