"""pcx_contracts: fuente única de verdad de los mensajes de pcx (nodo, nube, renderer, agentes)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import msgspec

from .cloud import CloudMessage
from .commands import Command, Flow
from .envelope import CONTRACT_VERSION, Envelope
from .events import Event
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
}

# Fixture -> tipo con el que se decodifica.
FIXTURES: dict[str, Any] = {
    "scene.json": ToRenderer,
    "scene_diff.json": ToRenderer,
    "flow_guia_3_pasos.json": Command,
    "tool_call.json": CloudMessage,
    "envelope_widget_pressed.json": Envelope,
}

json_encoder = msgspec.json.Encoder()


def decoder(tp: Any) -> msgspec.json.Decoder:
    return msgspec.json.Decoder(tp)


__all__ = ["CONTRACT_VERSION", "FIXTURES", "ROOTS", "decoder", "json_encoder"]
