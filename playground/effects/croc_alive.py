"""Cocodrilo vivo (pedido 018; alias de objeto_vivo sobre el cocodrilo): patas con rigging que aletean, respiración
al beat/audio y escamas que se encienden por zona al tocar. Lógica en playground/touchsynth/alive.py. Para que además
suene: croc_synth con visual=false."""

from __future__ import annotations

from ..touchsynth.alive import CrocAlive
from .objeto_vivo import ObjetoVivoEffect


class CrocAliveEffect(ObjetoVivoEffect):
    name = "croc_alive"
    label = "Cocodrilo vivo"
    description = ("Patas con rigging (detectadas en el contorno) que se mecen lento y aletean al tocar el cocodrilo; "
                   "respira ±2 % con el audio (bajo), el beat o lento; escamas hexagonales que se encienden por zona "
                   "con una onda desde el dedo. set_param aletear true = aletea sin tocar. Sonido: croc_synth "
                   "con visual=false. Alias de objeto_vivo con objeto=croc.")
    aliases = ("cocodrilo vivo", "que camine el cocodrilo")
    params = dict(CrocAlive.DEFAULTS)
    instrument_cls = CrocAlive
    objeto_default = "croc"
    auto_pick = False
