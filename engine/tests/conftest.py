"""Configuración común de los tests: nada sale por MIDI ni por audio de verdad.

Los instrumentos de la mesa táctil abren su salida al crearse (`MidiOut.prepare`): en modo "auto" eso abriría el
puerto virtual, el audio del Mac y lanzaría el receptor del PC por ssh. En los tests la salida es "null" salvo que
un test pida otra explícitamente (los de sonido usan receptores locales y destinos falsos).
"""

import os

import pytest

os.environ["TOUCHSYNTH_MIDI"] = "null"


@pytest.fixture(autouse=True)
def _midi_null(monkeypatch):
    monkeypatch.setenv("TOUCHSYNTH_MIDI", "null")
    from playground.touchsynth import music
    if not isinstance(music._MIDI, music.FakeMidi):
        music.set_midi(None)
    yield
