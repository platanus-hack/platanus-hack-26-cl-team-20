"""Objeto vivo: cualquier objeto cobra vida al tocarlo: escamas/celdas que se encienden por zona con una onda desde
el dedo, respiración (audio, beat o lenta) y "patas" con rigging donde el contorno tiene salientes. Generaliza
croc_alive (que queda como alias para el cocodrilo). Lógica en playground/touchsynth/objetos.py (ObjetoVivo)."""

from __future__ import annotations

import time

from ..touchsynth.effectkit import InstrumentEffectMixin
from ..touchsynth.objetos import ObjetoVivo, object_color, pick_object
from .base import Effect, Canvas


class ObjetoVivoEffect(InstrumentEffectMixin, Effect):
    name = "objeto_vivo"
    label = "Objeto vivo"
    description = ("El objeto (target o param objeto; vacío = el más grande) cobra vida: celdas hexagonales que se "
                   "encienden por zona con una onda desde el dedo, respira con el audio/beat (o lento) y, si el "
                   "contorno tiene salientes, son patas que aletean al tocarlo. Despierta al tocarlo y se adormece "
                   "en despierto_s. color auto = color del objeto. set_param aletear true = lo despierta sin tocar. "
                   "Sonido: objeto_synth con visual=false sobre el mismo objeto.")
    aliases = ("dale vida", "que respire", "objeto vivo", "despierta el objeto", "escamas", "que cobre vida")
    needs = ("contours", "touch", "beat", "audio")
    targets = ("*",)
    params = {**{k: v for k, v in ObjetoVivo.DEFAULTS.items() if k != "scan"}, "objeto": "", "capa": 0}
    instrument_cls = ObjetoVivo
    objeto_default = ""
    auto_pick = True

    def set(self, key, value):
        if key == "aletear":
            self._flap_pending = True
            return
        super().set(key, value)

    def _resolve(self, scene):
        return pick_object(scene, self.object(scene), self.p.get("objeto") or self.objeto_default, self.auto_pick)

    def _make(self, o, c):
        if self.instrument_cls is ObjetoVivo:
            oid = o.id if o is not None else None
            return ObjetoVivo(params=self.p, contour=c, obj_id=oid, auto_color=object_color(oid or "objeto"))
        return self.instrument_cls(params=self.p)

    def update(self, dt, scene):
        super().update(dt, scene)
        o, c = self._resolve(scene)
        inst = self._run(scene, lambda: self._make(o, c))
        if c is not None:
            if isinstance(inst, ObjetoVivo) and o is not None and inst.obj_id != o.id:
                inst.obj_id, inst.auto_color = o.id, object_color(o.id)
            inst.set_contour(c)
        now = time.time()
        inst.update_breath(scene, now)
        if getattr(self, "_flap_pending", False) and inst.rig is not None:
            self._flap_pending = False
            if isinstance(inst, ObjetoVivo):
                inst.poke(now)
            else:
                x, y = inst.resolve(None)
                zone = inst.rig.z.zone_at(x, y)
                inst.trigger(x, y, 0 if zone is None else zone, now)

    def draw(self, canvas: Canvas):
        self._draw(canvas)
