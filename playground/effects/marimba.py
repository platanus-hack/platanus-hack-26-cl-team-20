"""Marimba proyectada en la zona libre más grande de la mesa: golpe = nota. Lógica en playground/touchsynth/marimba.py."""

from __future__ import annotations

from ..touchsynth.effectkit import InstrumentEffectMixin
from ..touchsynth.marimba import Marimba
from .base import Effect, Canvas


class MarimbaEffect(InstrumentEffectMixin, Effect):
    name = "marimba"
    label = "Marimba en la mesa"
    description = ("Barras de madera proyectadas en la zona libre más grande de la mesa (mesa menos objetos del "
                   "escaneo, o rect=[x,y,w,h]); golpe = nota corta (MIDI canal 2) con la fuerza del toque; "
                   "arrastrar = redoble.")
    needs = ("geometry", "touch")
    targets = ("*",)
    params = dict(Marimba.DEFAULTS)

    def update(self, dt, scene):
        super().update(dt, scene)
        self._run(scene, lambda: Marimba(params=self.p))

    def draw(self, canvas: Canvas):
        self._draw(canvas)
