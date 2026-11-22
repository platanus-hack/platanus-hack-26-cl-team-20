"""El hilo de eventos de la escena no muere si un evento falla (revisión del PR #28, P1 2)."""

from __future__ import annotations

import threading

import conductor as C


class MesaFalsa:
    def __init__(self, parar: threading.Event):
        self.parar = parar
        self.vueltas = 0

    def eventos(self, cursor, tipos, timeout):
        self.vueltas += 1
        if self.vueltas == 1:
            return {"cursor": 0}
        if self.vueltas == 2:
            return {
                "cursor": "no-es-numero",
                "events": [
                    {"payload": {"type": "flow_emitted", "event": "voice_command", "data": {"text": "hola"}}},
                    {"payload": {"type": "flow_emitted", "event": "repetir", "data": {}}},
                ],
            }
        self.parar.set()
        return {"cursor": 3, "events": []}


def test_un_evento_que_falla_no_mata_el_hilo(monkeypatch):
    parar = threading.Event()
    atendidos = []
    monkeypatch.setattr(C, "leer_json", lambda p, defecto=None: {"elegido": "tierra"})

    def escuchar(mesa, pid):
        raise RuntimeError("la mesa no contestó el project")

    monkeypatch.setattr(C, "escuchar", escuchar)
    monkeypatch.setattr(C, "atender", lambda mesa, ev, data: atendidos.append(ev))
    mesa = MesaFalsa(parar)
    C.escuchar_eventos(mesa, parar)
    assert atendidos == ["repetir"]  # después de la falla de la voz, el pedido siguiente se atendió
    assert mesa.vueltas == 3  # y el hilo siguió escuchando hasta que lo pararon
