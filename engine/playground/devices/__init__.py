"""Dispositivos del playground: controladores MIDI (OHM RGB, Circuit Tracks, DDJ-SB2, Korg EK-50), audio y sus controles.

    fuente de eventos (SmartDeck WS · MIDI directo · puente UDP desde el PC · falsa para tests)
      → DeviceRegistry (dispositivos, controles con valor 0..1, último evento)
        → bindings control → parámetro de efecto / beat     → efectos
        → objeto de escena (skin con la posición de cada control)

Quién manda el puerto MIDI del OHM: SmartDeck si corre (ws://localhost:8090); si no, el playground lo abre
directo. Nunca dos procesos escribiendo los mismos LEDs (ver PLAN.md › Diseño del 002).
"""

from .midi import Control, Device, DeviceRegistry, FakeSource, DirectMidiSource, SmartDeckSource, list_midi_ports
from .ohm import ohm_device, ohm_scene_object, OHM_SKIN
from .keyboard import BridgeMidiSource, KeyboardSource, ek50_device, BRIDGE_PORT, DEFAULT_BRIDGE

__all__ = ["Control", "Device", "DeviceRegistry", "FakeSource", "DirectMidiSource", "SmartDeckSource",
           "list_midi_ports", "ohm_device", "ohm_scene_object", "OHM_SKIN", "BridgeMidiSource", "KeyboardSource",
           "ek50_device", "BRIDGE_PORT", "DEFAULT_BRIDGE"]
