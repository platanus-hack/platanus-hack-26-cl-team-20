"""Protocolo nodo ↔ nube (Render) por una WSS saliente del nodo.

Conexión y reconexión:
1. nodo → `hello{contract_versions, stream_id, log_first_cursor, log_last_cursor, retention}`.
2. nube → `welcome{contract_version, last_stream_id, last_cursor}` (la versión elegida está en ambas listas) o
   `hello_rejected{reason: "version", supported_versions}` y cierra.
3. nodo → `state_snapshot{stream_id, cursor, world}`: **estado recuperado** (el mundo actual, siempre completo).
4. **Historial**: el nodo reenvía como `event_batch{replay: true}` los eventos con `seq` > `last_cursor` que aún
   retiene. Si falta algo, manda antes `history_gap{stream_id, from, to, reason}` (seq perdidos, inclusive):
   - `reason: "retention"`: `last_cursor + 1 < log_first_cursor` (el log ya los descartó).
   - `reason: "stream_reset"`: `last_stream_id` ≠ `stream_id` (el nodo perdió su log). Se informa el hueco del
     stream viejo `{stream_id: last_stream_id, from: last_cursor + 1, to: null}` y, si `log_first_cursor > 1`, el
     del nuevo `{stream_id, from: 1, to: log_first_cursor - 1}`.
   Cierra con `replay_done{stream_id, cursor, history_complete}`; `history_complete=false` si hubo algún hueco.
5. Después, `event_batch{replay: false}` en vivo. La nube reenvía tools MCP como `tool_call`; el nodo responde
   `tool_result`. Ambos lados mandan `heartbeat` cada ~10 s. La consola de la nube resuelve confirmaciones con
   `confirm_action` (v2.1).

`stream_id` identifica el log de eventos del nodo: cambia si el nodo pierde su log y `seq` vuelve a empezar.
El estado físico manda en el nodo (reported); la nube solo pide cambios (desired) vía `tool_call`.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from msgspec import Meta, Struct, field

from .envelope import Envelope
from .home import ConfirmAction
from .world import WorldState


class CloudBase(Struct, kw_only=True, tag_field="type"):
    pass


class LogRetention(Struct, kw_only=True):
    """Cuánto historial retiene el nodo para replay (se descarta lo que exceda cualquiera de los dos)."""

    max_events: Annotated[int, Meta(ge=1)] = 10_000
    max_age_s: Annotated[float, Meta(gt=0)] = 3600.0


class Hello(CloudBase, kw_only=True, tag="hello"):
    node_id: str
    token: str
    capabilities: list[str] = []
    version: str
    """Versión del software del nodo (informativa)."""
    contract_versions: Annotated[list[int], Meta(min_length=1)]
    """Versiones de contrato que el nodo sabe hablar."""
    stream_id: str
    log_first_cursor: int
    """`seq` más antiguo que el nodo aún retiene (0 si el log está vacío)."""
    log_last_cursor: int
    retention: LogRetention = field(default_factory=LogRetention)


class Welcome(CloudBase, kw_only=True, tag="welcome"):
    session_id: str
    contract_version: int
    """Versión elegida por la nube; se usa en todo `Envelope.v` de la sesión."""
    last_stream_id: str | None = None
    last_cursor: int | None = None
    """Último `seq` que la nube ya tiene de `last_stream_id` (None = nada)."""


class HelloRejected(CloudBase, kw_only=True, tag="hello_rejected"):
    reason: Literal["version", "auth", "other"]
    supported_versions: list[int] = []
    detail: str | None = None


class StateSnapshot(CloudBase, kw_only=True, tag="state_snapshot"):
    """Estado recuperado. No implica historial completo (ver `replay_done`)."""

    stream_id: str
    cursor: int
    world: WorldState


class HistoryGap(CloudBase, kw_only=True, tag="history_gap"):
    """Eventos `seq` en [from, to] (inclusive) de `stream_id` que no se pueden reenviar."""

    stream_id: str
    from_: int = field(name="from")
    to: int | None
    """None = hasta el final (desconocido) de un stream que ya no existe."""
    reason: Literal["retention", "stream_reset"]


class EventBatch(CloudBase, kw_only=True, tag="event_batch"):
    stream_id: str
    cursor: int
    """`seq` del último evento del lote."""
    events: list[Envelope]
    replay: bool = False
    """True si son eventos históricos reenviados tras reconectar."""


class ReplayDone(CloudBase, kw_only=True, tag="replay_done"):
    stream_id: str
    cursor: int
    history_complete: bool
    """False si se mandó algún `history_gap` en esta reconexión."""


class ToolCall(CloudBase, kw_only=True, tag="tool_call"):
    call_id: str
    """Intento de transporte (lo genera el gateway). No es la clave de idempotencia: esa es `args.request_id`."""
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
    request_id: str | None = None
    """El `request_id` del comando (obligatorio si la tool es mutable; None en tools de solo lectura)."""
    ok: bool
    content: list[Content] = []
    structured: dict[str, Any] | None = None
    """Para `home`: un `HomeCallResult`; para `events_wait`: un `EventsWaitResult`."""


class Heartbeat(CloudBase, kw_only=True, tag="heartbeat"):
    t_ms: float
    cursor: int | None = None


class EventsWaitResult(Struct, kw_only=True):
    """`ToolResult.structured` de `events_wait`. `gap` avisa si el cursor pedido ya no está retenido."""

    stream_id: str
    cursor: int
    events: list[Envelope] = []
    gap: HistoryGap | None = None


CloudMessage = Union[Hello, Welcome, HelloRejected, StateSnapshot, HistoryGap, EventBatch, ReplayDone, ToolCall,
                     ToolResult, Heartbeat, ConfirmAction]
"""v2.1: `confirm_action` (nube → nodo) lo manda solo la consola de personas (token `console`), nunca un agente."""
