"""Mirada: enfoca lo que se mira. Lee scene.gaze (comando gaze {x, y} o {objeto}; hoy simulado, mañana la cámara de
ojos): foco de luz suave en el punto, el objeto mirado se enciende con su color y, si la mirada se queda dwell_s,
queda elegido (anillo de progreso + halo). Lógica en playground/touchsynth/interactivo.py (Mirada)."""

from __future__ import annotations

import time

from ..touchsynth.effectkit import InstrumentEffectMixin, SceneObjectsMixin
from ..touchsynth.interactivo import Mirada, gaze_of
from .base import Effect, Canvas


class MiradaEffect(SceneObjectsMixin, InstrumentEffectMixin, Effect):
    name = "mirada"
    label = "Mirada"
    description = ("Lo que miras se enciende: foco suave donde está la mirada (comando gaze x, y en px del proyector, "
                   "u objeto), el objeto mirado se ilumina con su color y si la mirada se queda dwell_s queda "
                   "elegido (scene.gaze['elegido']). Sin mirada no dibuja nada; demo=true pasea la mirada por los "
                   "objetos. Gancho para la cámara de ojos.")
    aliases = ("mirada", "lo que miro", "gaze", "ojos", "enfoca lo que miro", "foco")
    needs = ("contours", "people")
    targets = ("*",)
    params = {**Mirada.DEFAULTS, "objetos": "", "capa": 6}

    def update(self, dt, scene):
        super().update(dt, scene)
        objs = self._scene_objs(scene)
        inst = self._run(scene, lambda: Mirada(params=self.p))
        if getattr(inst, "objs", None) is not objs:
            inst.set_objects(objs)
        inst.update_gaze(gaze_of(scene), time.time())
        g = getattr(scene, "gaze", None)
        if isinstance(g, dict) and g:
            g["mirando"] = inst.focus
            if inst.chosen:
                g["elegido"] = inst.chosen

    def draw(self, canvas: Canvas):
        self._draw(canvas)
