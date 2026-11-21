"""Protocolo nodo ↔ nube (Render) por una WSS saliente del nodo.

Secuencia: nodo → `hello`; nube → `welcome{last_cursor}`; nodo → `state_snapshot{cursor, world}` y luego
`event_batch` con lo posterior a ese cursor. La nube reenvía tools MCP como `tool_call`; el nodo responde
`tool_result`. Ambos lados mandan `heartbeat` cada ~10 s. El estado físico manda en el nodo (reported);
la nube solo pide cambios (desired) vía `tool_call`.
"""

from __future__ import annotations

from typing import Any, Union

from msgspec import Struct

from .envelope import Envelope
from .world import WorldState


class CloudBase(Struct, kw_only=True, tag_field="type"):
    pass


class Hello(CloudBase, kw_only=True, tag="hello"):
    node_id: str
    token: str
    capabilities: list[str] = []
    version: str


class Welcome(CloudBase, kw_only=True, tag="welcome"):
    session_id: str
    last_cursor: int | None = None
    """Último `seq` que la nube ya tiene de este nodo (None = nada)."""


class StateSnapshot(CloudBase, kw_only=True, tag="state_snapshot"):
    cursor: int
    world: WorldState


class EventBatch(CloudBase, kw_only=True, tag="event_batch"):
    cursor: int
    """`seq` del último evento del lote."""
    events: list[Envelope]


class ToolCall(CloudBase, kw_only=True, tag="tool_call"):
    call_id: str
    tool: str
    args: dict[str, Any] = {}
    timeout_s: float | None = None


class TextContent(Struct, kw_only=True, tag_field="type", tag="text"):
    text: str


class ImageContent(Struct, kw_only=True, tag_field="type", tag="image"):
    data: str
    """base64."""
    mime_type: str = "image/png"


Content = Union[TextContent, ImageContent]


class ToolResult(CloudBase, kw_only=True, tag="tool_result"):
    call_id: str
    ok: bool
    content: list[Content] = []
    structured: dict[str, Any] | None = None


class Heartbeat(CloudBase, kw_only=True, tag="heartbeat"):
    t_ms: float
    cursor: int | None = None


CloudMessage = Union[Hello, Welcome, StateSnapshot, EventBatch, ToolCall, ToolResult, Heartbeat]
