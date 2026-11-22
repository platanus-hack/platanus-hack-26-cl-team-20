"""Dibujar sobre la mesa con el dedo: trazos de luz que se desvanecen. Lógica en playground/touchsynth/sketch.py."""

from __future__ import annotations

from ..touchsynth.effectkit import InstrumentEffectMixin
from ..touchsynth.sketch import Sketch
from .base import Effect, Canvas


class TouchDraw(InstrumentEffectMixin, Effect):
    name = "touch_draw"
    label = "Dibujar en la mesa"
    description = ("El dedo pinta sobre la mesa: trazos con persistencia (hold_s) y desvanecimiento (fade_s; 0 = "
                   "para siempre); color por velocidad, por zona de la mesa o fijo. Borrar: set_param clear true, "
                   "pg_touch clear, o dos manos quietas sobre la mesa (o separándose rápido).")
    needs = ("touch",)
    targets = ("*",)
    params = {k: v for k, v in Sketch.DEFAULTS.items() if k != "clear"}

    def set(self, key, value):
        if key == "clear":
            self._clear_pending = bool(value)
            return
        super().set(key, value)

    def update(self, dt, scene):
        super().update(dt, scene)
        inst = self._run(scene, lambda: Sketch(params=self.p))
        if getattr(self, "_clear_pending", False):
            inst.clear(None, "parámetro")
            self._clear_pending = False

    def draw(self, canvas: Canvas):
        self._draw(canvas)
