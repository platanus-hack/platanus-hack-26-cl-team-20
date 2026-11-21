"""Cocodrilo sintetizador (alias de objeto_synth sobre el cocodrilo): zonas de colores a lo largo del cocodrilo
(cabeza → cola); tocar una zona = nota MIDI y un pulso en el lugar; mantener = sostener. Sin cocodrilo en la escena
usa el contorno del escaneo (workbench.json). Lógica en playground/touchsynth/croc.py."""

from __future__ import annotations

from ..touchsynth.croc import CrocSynth
from .objeto_synth import ObjetoSynthEffect


class CrocSynthEffect(ObjetoSynthEffect):
    name = "croc_synth"
    label = "Cocodrilo sintetizador"
    description = ("El contorno exacto del cocodrilo (escaneo) en 8-12 zonas de color de la cabeza a la cola; cada "
                   "zona es una nota de la escala (MIDI canal 1, puerto 'sl-scanner touch' o IAC). Tocar = nota + "
                   "pulso; mantener = sostener; deslizar = glissando; a lo ancho del cuerpo = CC1. visual=false: solo sonido "
                   "(para usarlo con croc_alive). El sonido sale por la mesa (PC) si responde midi_play. Alias de "
                   "objeto_synth con objeto=croc.")
    aliases = ("cocodrilo sintetizador", "cocodrilo que suena")
    params = dict(CrocSynth.DEFAULTS)
    instrument_cls = CrocSynth
    objeto_default = "croc"
    auto_pick = False                          # sin cocodrilo en la escena: el del archivo del escaneo, como antes
