"""Objeto sintetizador: cualquier objeto de la escena (por id, kind o alias; o el target del spawn) se vuelve un
instrumento por zonas a lo largo de su eje mayor. Generaliza croc_synth (que queda como alias para el cocodrilo).
Lógica en playground/touchsynth/objetos.py (ObjetoSynth) y croc.py (zonas)."""

from __future__ import annotations

from ..touchsynth.croc import CrocSynth
from ..touchsynth.effectkit import InstrumentEffectMixin
from ..touchsynth.objetos import ObjetoSynth, pick_object
from .base import Effect, Canvas


class ObjetoSynthEffect(InstrumentEffectMixin, Effect):
    name = "objeto_synth"
    label = "Objeto sintetizador"
    description = ("Cualquier objeto de la escena (target del spawn o param objeto: id, tipo o alias; vacío = el más "
                   "grande) se divide en N zonas de color a lo largo de su eje mayor; cada zona es una nota de la "
                   "escala (MIDI canal 1, puerto 'sl-scanner touch'). Tocar = nota + pulso; mantener = sostener; "
                   "deslizar = glissando; a lo ancho = CC1. visual=false: solo sonido (con objeto_vivo encima). "
                   "Tocar por nombre: pg_touch target '<objeto>:3'.")
    aliases = ("objeto que suena", "instrumento", "toca el objeto", "sintetizador", "hazlo sonar", "que suene el cuadro",
               "objeto instrumento")
    needs = ("contours", "touch")
    targets = ("*",)
    params = {**{k: v for k, v in CrocSynth.DEFAULTS.items() if k != "scan"}, "objeto": "", "capa": 0}
    instrument_cls = ObjetoSynth
    objeto_default = ""
    auto_pick = True                           # sin target ni objeto: el objeto más grande

    def _resolve(self, scene):
        return pick_object(scene, self.object(scene), self.p.get("objeto") or self.objeto_default, self.auto_pick)

    def _make(self, o, c):
        if self.instrument_cls is ObjetoSynth:
            return ObjetoSynth(params=self.p, contour=c, obj_id=o.id if o is not None else None)
        return self.instrument_cls(params=self.p)

    def update(self, dt, scene):
        super().update(dt, scene)
        o, c = self._resolve(scene)
        inst = self._run(scene, lambda: self._make(o, c))
        if c is not None:
            inst.set_contour(c)
            inst.obj_id = o.id if o is not None else None

    def draw(self, canvas: Canvas):
        self._draw(canvas)
