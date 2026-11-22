"""Toque onda: cada toque, en cualquier parte, lanza una onda que recorre la escena; al alcanzar un objeto ilumina el
tramo de su contorno que cruza el frente (color del objeto), el objeto destella y rebota una onda hija desde el punto
de contacto. Lógica en playground/touchsynth/interactivo.py (Onda)."""

from __future__ import annotations

from ..touchsynth.effectkit import InstrumentEffectMixin, SceneObjectsMixin
from ..touchsynth.interactivo import Onda
from .base import Effect, Canvas


class ToqueOnda(SceneObjectsMixin, InstrumentEffectMixin, Effect):
    name = "toque_onda"
    label = "Onda al tocar"
    description = ("Cada toque lanza una onda de luz que se propaga por toda la escena; al llegar a un objeto ilumina "
                   "su borde con el color del objeto, lo hace destellar, suena una nota suave (canal 3) y rebota una "
                   "onda hija desde el punto de contacto. No le quita los toques a los instrumentos. Params: "
                   "velocidad (px/s), alcance, banda, rebote, sonido, objetos (vacío = todos), arrastre_px (> 0: "
                   "también al deslizar).")
    aliases = ("onda", "ondas", "ondas al tocar", "eco", "sonar", "radar", "gota", "que rebote")
    needs = ("contours", "touch")
    targets = ("*",)
    params = {**Onda.DEFAULTS, "objetos": "", "capa": 5}

    def update(self, dt, scene):
        super().update(dt, scene)
        objs = self._scene_objs(scene)
        inst = self._run(scene, lambda: Onda(params=self.p))
        if objs is not getattr(self, "_pushed", None) or getattr(inst, "objs", None) is not objs:
            inst.set_objects(objs)
            self._pushed = objs

    def draw(self, canvas: Canvas):
        self._draw(canvas)
