"""pcx_contracts: fuente única de verdad de los mensajes de pcx (nodo, nube, renderer, agentes)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import msgspec

from .cloud import CloudMessage
from .commands import Command, Flow
from .envelope import CONTRACT_VERSION, Envelope
from .events import Event
from .geometry import Transform
from .home import ConfirmAction, HomeCallResult, HomePolicy
from .render import FromRenderer, ToRenderer
from .widgets import Widget
from .world import WorldState

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "schema"
FIXTURES_DIR = ROOT / "fixtures"

# Tipos raíz que se exportan como JSON Schema (nombre de archivo -> tipo).
ROOTS: dict[str, Any] = {
    "widget": Widget,
    "command": Command,
    "event": Event,
    "envelope": Envelope,
    "to_renderer": ToRenderer,
    "from_renderer": FromRenderer,
    "cloud": CloudMessage,
    "world": WorldState,
    "flow": Flow,
    "transform": Transform,
    "home_policy": HomePolicy,
    "home_call_result": HomeCallResult,
    "confirm_action": ConfirmAction,
}

# Fixture -> tipo con el que se decodifica.
FIXTURES: dict[str, Any] = {
    "scene.json": ToRenderer,
    "scene_diff.json": ToRenderer,
    "flow_guia_3_pasos.json": Command,
    "tool_call.json": CloudMessage,
    "envelope_widget_pressed.json": Envelope,
    "blank.json": Command,
}

json_encoder = msgspec.json.Encoder()


def ns_to_str(data: Any) -> Any:
    """JSON hacia clientes TS: convierte a cadena decimal todo entero de un campo `*_ns` (TS: `BigInt(s)`).
    Python y Rust siguen usando enteros; solo el gateway/consola aplica esto (ver README, «Tiempos»)."""
    if isinstance(data, dict):
        return {k: (str(v) if k.endswith("_ns") and isinstance(v, int) and not isinstance(v, bool) else ns_to_str(v))
                for k, v in data.items()}
    if isinstance(data, list):
        return [ns_to_str(x) for x in data]
    return data


def decoder(tp: Any) -> msgspec.json.Decoder:
    return msgspec.json.Decoder(tp)


__all__ = ["CONTRACT_VERSION", "FIXTURES", "ROOTS", "decoder", "json_encoder", "ns_to_str"]
