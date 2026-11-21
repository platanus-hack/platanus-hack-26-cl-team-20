"""Sobre común de todo evento o comando en el bus local y hacia la nube.

Validaciones al decodificar: `v` es obligatorio y debe estar en `SUPPORTED_VERSIONS`; `type` debe ser igual a
`payload.type`. Ambas fallan con `msgspec.ValidationError`.
"""

from __future__ import annotations

import time

from msgspec import Struct

from .commands import Command
from .events import Event

CONTRACT_VERSION = 2
SUPPORTED_VERSIONS: tuple[int, ...] = (2,)
"""Versiones que este paquete sabe leer. `Hello.contract_versions` las anuncia; `Welcome.contract_version` elige."""


def tag_of(msg: Struct) -> str:
    return type(msg).__struct_config__.tag  # type: ignore[return-value]


class Envelope(Struct, kw_only=True):
    type: str
    """Igual al `type` del payload (permite filtrar sin decodificarlo). Se valida."""
    v: int
    """Versión del contrato (obligatoria). Versiones desconocidas se rechazan."""
    seq: int
    """Monótono por `stream_id` del nodo; es el cursor de `events_wait` y de la reconexión."""
    t_capture_ns: int | None = None
    """ns de época del cuadro que originó el evento (None si no viene de una cámara)."""
    t_pub_ns: int
    source: str
    """Proceso o adaptador que lo publica, p. ej. `perception-hands`, `core`, `agent`."""
    node_id: str
    payload: Command | Event

    def __post_init__(self) -> None:
        if self.v not in SUPPORTED_VERSIONS:
            raise ValueError(f"versión de contrato desconocida: {self.v} (soportadas: {list(SUPPORTED_VERSIONS)})")
        if self.type != tag_of(self.payload):
            raise ValueError(f"type {self.type!r} no coincide con payload.type {tag_of(self.payload)!r}")


def wrap(payload: Command | Event, *, seq: int, source: str, node_id: str,
         t_capture_ns: int | None = None) -> Envelope:
    return Envelope(type=tag_of(payload), v=CONTRACT_VERSION, seq=seq, t_capture_ns=t_capture_ns,
                    t_pub_ns=time.time_ns(), source=source, node_id=node_id, payload=payload)
