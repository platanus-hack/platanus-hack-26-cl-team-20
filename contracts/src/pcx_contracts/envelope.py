"""Sobre común de todo evento o comando en el bus local y hacia la nube."""

from __future__ import annotations

import time

from msgspec import Struct

from .commands import Command
from .events import Event

CONTRACT_VERSION = 1


class Envelope(Struct, kw_only=True):
    type: str
    """Igual al `type` del payload (permite filtrar sin decodificarlo)."""
    v: int = CONTRACT_VERSION
    seq: int
    """Monótono por nodo; es el cursor de `events_wait`."""
    t_capture_ns: int | None = None
    """ns de época del cuadro que originó el evento (None si no viene de una cámara)."""
    t_pub_ns: int
    source: str
    """Proceso o adaptador que lo publica, p. ej. `perception-hands`, `core`, `agent`."""
    node_id: str
    payload: Command | Event


def tag_of(msg: Struct) -> str:
    return type(msg).__struct_config__.tag  # type: ignore[return-value]


def wrap(payload: Command | Event, *, seq: int, source: str, node_id: str,
         t_capture_ns: int | None = None) -> Envelope:
    return Envelope(type=tag_of(payload), seq=seq, t_capture_ns=t_capture_ns, t_pub_ns=time.time_ns(),
                    source=source, node_id=node_id, payload=payload)
