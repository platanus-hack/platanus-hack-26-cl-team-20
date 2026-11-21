"""Linterna: la mano ilumina el relieve escaneado (normales del height map, luz puntual con cono y sombras).
Lógica en playground/touchsynth/linterna.py."""

from __future__ import annotations

import time

from ..touchsynth.effectkit import InstrumentEffectMixin
from ..touchsynth.linterna import Linterna
from ..touchsynth.registry import touch_state
from .base import Effect, Canvas


class LinternaEffect(InstrumentEffectMixin, Effect):
    name = "linterna"
    label = "Mano linterna"
    description = ("La mano es una luz: ilumina el mundo 3D reconstruido (relieve del escaneo con normales y "
                   "sombras), con caída radial (radio), cono tipo linterna, color, intensidad y altura de la luz. "
                   "Sin mano queda casi oscuro (ambiente). demo=true hace orbitar una luz sola.")
    needs = ("geometry", "touch", "people")
    targets = ("*",)
    params = dict(Linterna.DEFAULTS)

    def update(self, dt, scene):
        super().update(dt, scene)
        inst = self._run(scene, lambda: Linterna(params=self.p))
        ts = touch_state(scene)
        inst.update_sources(ts.hands, ts.touches, time.time())

    def draw(self, canvas: Canvas):
        self._draw(canvas)
