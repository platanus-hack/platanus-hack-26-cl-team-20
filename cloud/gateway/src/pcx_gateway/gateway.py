import asyncio
import base64
import hmac
import json
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import uuid4

import msgspec
from msgspec import Meta, Struct
from pcx_contracts.cloud import HistoryGap, ImageContent, StateSnapshot, TextContent, ToolCall, ToolResult
from pcx_contracts.envelope import SUPPORTED_VERSIONS
from pcx_contracts.events import ConfirmationRequested, ConfirmationResolved, HomeCallDone
from pcx_contracts.home import ConfirmAction, HomeCallResult, action_digest
from pcx_contracts.tools import parse_args
from pcx_contracts.world import WorldState

from .config import Settings
from .pubsub import Bus
from .store import Identity, Store

log = logging.getLogger(__name__)
TOOL_TIMEOUTS = {"look": 20, "project": 15, "remove": 15, "run_flow": 15, "calibrate": 180, "home": 30}
LOOK_PHOTO_ID = "pcx:look"
MAX_LOOK_BYTES = 2 * 1024 * 1024
MAX_BATCH_EVENTS = 1000


class NodeOffline(RuntimeError):
    pass


class Rejected(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class HistoryArgs(Struct, kw_only=True, forbid_unknown_fields=True):
    query: str | None = None
    """Texto a buscar en la etiqueta o id de los objetos (p. ej. «llaves»)."""
    since: str | None = None
    """ISO 8601 o relativo («2h», «30m», «7d»)."""
    until: str | None = None
    types: list[str] = []
    limit: Annotated[int, Meta(ge=1, le=200)] = 50


# Tool del gateway (no está en contracts/tools.py: ver REVISION.md). Esquema plano, como las demás.
HISTORY_TOOL = {
    "name": "history",
    "description": "Memoria del espacio: busca objetos por etiqueta (p. ej. «llaves») y los eventos entre since y "
    "until. Devuelve dónde se vio por última vez cada objeto (center_mm en table_mm), si sigue en la mesa, sus "
    "eventos y la miniatura más reciente. since/until: ISO 8601 o relativo («2h», «30m», «7d»).",
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {"type": ["string", "null"], "default": None},
            "since": {"type": ["string", "null"], "default": None},
            "until": {"type": ["string", "null"], "default": None},
            "types": {"type": "array", "items": {"type": "string"}, "default": []},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
        },
    },
    "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
}
RELATIVE = re.compile(r"^(\d+(?:\.\d+)?)\s*([smhdw])$")
UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


def parse_time(value: str | None) -> int | None:
    """ns de época desde ISO 8601, segundos de época o relativo hacia atrás («2h»)."""
    if value is None or value == "":
        return None
    value = value.strip()
    if m := RELATIVE.match(value.lower()):
        moment = datetime.now(UTC) - timedelta(**{UNITS[m[2]]: float(m[1])})
    elif re.fullmatch(r"\d+(\.\d+)?", value):
        return int(float(value) * 1e9)
    else:
        moment = datetime.fromisoformat(value)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
    return int(moment.timestamp() * 1e9)


def iso(ns):
    return None if ns is None else datetime.fromtimestamp(ns / 1e9, UTC).isoformat(timespec="seconds")


@dataclass(eq=False)
class Connection:
    websocket: object
    session_id: str = field(default_factory=lambda: str(uuid4()))
    contract_version: int = max(SUPPORTED_VERSIONS)
    stream_id: str | None = None
    ready: bool = False
    replaying: bool = True
    browser: bool = False
    """Nodo navegador (capability `browser`): entiende el aviso `viewers`, que está fuera del contrato."""
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending: dict[str, asyncio.Future] = field(default_factory=dict)

    async def send(self, message):
        async with self.lock:
            await self.websocket.send_text(msgspec.json.encode(message).decode())

    def fail_pending(self):
        for future in self.pending.values():
            if not future.done():
                future.set_exception(
                    NodeOffline(
                        "Nodo desconectado durante la llamada; no se reintentó. Si reintentas, usa el mismo "
                        "request_id (el nodo deduplica)."
                    )
                )


def result(call_id, data, *, ok=True, request_id=None, images=()):
    return ToolResult(
        call_id=call_id,
        request_id=request_id,
        ok=ok,
        structured=data,
        content=[TextContent(text=json.dumps(data, ensure_ascii=False)), *images],
    )


MAX_VIEWERS = 6
MAX_VIEW_FRAME = 700 * 1024  # un JPEG de 1280 px; se guarda uno solo por nodo, compartido entre quienes miran
VIEW_KIND = re.compile(r'^\{"type":"view","kind":"([a-z]+)"')


@dataclass(eq=False)
class Viewer:
    """Alguien mirando en vivo. No acumula: guarda el último cuadro y el último mensaje de cada tipo; si va
    lento, simplemente se salta cuadros (el plan chico de Render tiene 512 MB)."""

    frame: bytes | None = None
    sent: int = 0
    dropped: int = 0
    state: dict[str, str] = field(default_factory=dict)
    log: deque = field(default_factory=lambda: deque(maxlen=40))
    wake: asyncio.Event = field(default_factory=asyncio.Event)


class Gateway:
    def __init__(self, store: Store, bus: Bus, settings: Settings):
        self.store, self.bus, self.settings = store, bus, settings
        self.connections: dict[str, Connection] = {}
        self.viewers: dict[str, set[Viewer]] = {}
        self.slow_checked: dict[str, float] = {}
        self.tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ mirar en vivo
    def relay(self, node_id, message: bytes | str):
        """Nodo → los que miran. `bytes` = cuadro JPEG; `str` = mensaje `{"type":"view","kind":...}` tal cual."""
        viewers = self.viewers.get(node_id)
        if not viewers:
            return
        if isinstance(message, bytes):
            if len(message) > MAX_VIEW_FRAME:
                return
            for v in viewers:
                if v.frame is not None:
                    v.dropped += 1  # no alcanzó a salir el anterior: este lo reemplaza
                v.frame = message
                v.wake.set()
            self.watch_slow(node_id, viewers)
            return
        kind = VIEW_KIND.match(message)
        for v in viewers:
            if kind and kind[1] == "activity":
                v.log.append(message)
            else:
                v.state[kind[1] if kind else "?"] = message
            v.wake.set()

    def watch_slow(self, node_id, viewers):
        """Cada 2 s: si alguien pierde más de un tercio de los cuadros, se le pide al nodo bajar la calidad."""
        now = time.monotonic()
        if now - self.slow_checked.get(node_id, 0) < 2:
            return
        self.slow_checked[node_id] = now
        slow = any(v.dropped > 3 and v.dropped > (v.dropped + v.sent) / 3 for v in viewers)
        for v in viewers:
            v.sent = v.dropped = 0
        if slow:
            task = asyncio.create_task(
                self.to_browser(node_id, {"type": "viewers", "count": len(viewers), "slow": True})
            )
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    def view_status(self, node_id):
        connected = self.connected(node_id) is not None
        self.relay(node_id, json.dumps({"type": "view", "kind": "status", "connected": connected}))

    async def to_browser(self, node_id, message: dict):
        """Mensaje fuera del contrato hacia un nodo navegador (los demás nodos no lo reciben)."""
        conn = self.connected(node_id)
        if conn is None or not conn.browser:
            return
        try:
            async with conn.lock:
                await conn.websocket.send_text(json.dumps(message))
        except Exception:
            log.debug("no se pudo avisar a %s", node_id, exc_info=True)

    async def tell_viewers(self, node_id):
        """Avisa al nodo cuántos miran: solo manda video y estado mientras haya alguien."""
        await self.to_browser(node_id, {"type": "viewers", "count": len(self.viewers.get(node_id, ()))})

    async def wake(self, node_id):
        """Despierta a events_wait. Los datos ya son durables: si el bus falla, se registra y el WSS sigue."""
        try:
            await self.bus.publish(node_id)
        except Exception:
            log.warning("bus.publish falló para %s; events_wait lo verá al vencer", node_id, exc_info=True)

    def connected(self, node_id) -> Connection | None:
        conn = self.connections.get(node_id)
        return conn if conn is not None and conn.ready else None

    async def execute(self, identity: Identity, tool: str, raw: dict):
        call_id = str(uuid4())
        try:
            if tool == "history":
                args = msgspec.convert(raw, HistoryArgs)
                since, until = parse_time(args.since), parse_time(args.until)
            else:
                args = parse_args(tool, raw)
        except (KeyError, ValueError, TypeError, msgspec.ValidationError) as exc:
            return result(call_id, {"error": f"Argumentos inválidos: {exc}"}, ok=False)
        if tool == "history":
            return await self.history(call_id, identity, args, since, until)
        await self.store.audit(call_id, identity, tool, raw)
        status, answer = "failed", None
        try:
            answer = await self.dispatch(call_id, identity, tool, args, raw)
            status = "completed" if answer.ok else "failed"
            if tool == "home" and answer.ok and (answer.structured or {}).get("status") == "pending":
                status = "pending_confirmation"
            return answer
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except (NodeOffline, TimeoutError, OSError, RuntimeError) as exc:
            status = "timeout" if isinstance(exc, TimeoutError) else "failed"
            message = str(exc) or "Timeout esperando tool_result del nodo; consulta scene antes de reintentar"
            return result(call_id, {"error": message}, ok=False, request_id=getattr(args, "request_id", None))
        finally:
            await self.store.finish(call_id, status, answer.ok if answer else False)

    async def history(self, call_id, identity, args, since, until):
        data = await self.store.history(
            identity.space_id,
            query=args.query,
            since_ns=since,
            until_ns=until,
            types=args.types,
            limit=args.limit,
        )
        images = []
        for match in data["matches"]:
            match["last_seen"] = iso(match["last_seen_ns"])
            if match["has_thumbnail"] and not images:
                thumb = await self.store.thumbnail(identity.space_id, match["object_id"])
                images.append(
                    ImageContent(data=base64.b64encode(thumb.thumb).decode(), mime_type=thumb.thumb_mime)
                )
        for event in data["events"]:
            event["t"] = iso(event["t_pub_ns"])
        return result(call_id, data, images=images)

    async def dispatch(self, call_id, identity, tool, args, raw):
        node_id = identity.node_id
        conn = self.connected(node_id)
        if tool == "events_wait":
            # Sale de la DB: funciona aunque el nodo esté reconectando (p. ej. durante un deploy).
            return result(call_id, await self.wait_events(node_id, args))
        if tool in ("scene", "devices"):
            row = await self.store.node(node_id)
            if row["world"] is None:
                raise NodeOffline(
                    f"Nodo {node_id} nunca mandó state_snapshot; vuelve a intentar cuando esté online"
                )
            world = msgspec.json.decode(row["world"], type=WorldState)
            if tool == "devices":
                devices = [
                    msgspec.to_builtins(d) for d in world.devices if args.kind is None or d.kind == args.kind
                ]
                return result(call_id, {"devices": devices, "connected": conn is not None})
            data = msgspec.to_builtins(world)
            if not args.include_hands:
                data.pop("hands")
            if not args.include_devices:
                data.pop("devices")
            data.update(
                cursor=await self.store.cursor(node_id),
                stream_id=row["stream_id"],
                snapshot_cursor=row["snapshot_cursor"],
                reported_cursor=row["reported_cursor"],
                history_complete=row["history_complete"],
                node_id=node_id,
                connected=conn is not None,
                last_seen=row["last_seen"],
            )
            return result(call_id, data)
        if conn is None:
            raise NodeOffline(
                f"Nodo {node_id} no conectado o sincronizando; vuelve a intentar cuando esté online"
            )
        if len(conn.pending) >= self.settings.max_pending_calls:
            raise NodeOffline("Demasiadas llamadas en curso hacia el nodo; espera y reintenta")
        request_id = getattr(args, "request_id", None)
        timeout = args.timeout_s + 5 if tool == "ask" else TOOL_TIMEOUTS[tool]
        future = asyncio.get_running_loop().create_future()
        conn.pending[call_id] = future
        try:
            async with asyncio.timeout(timeout):
                await conn.send(ToolCall(call_id=call_id, tool=tool, args=raw, timeout_s=timeout))
                answer: ToolResult = await future
        finally:
            conn.pending.pop(call_id, None)
            if not future.done():
                future.cancel()
        if request_id is not None and answer.request_id not in (None, request_id):
            return result(
                call_id, {"error": "tool_result con request_id distinto"}, ok=False, request_id=request_id
            )
        if tool == "home" and answer.ok:
            return await self.home_result(call_id, identity, args, answer)
        if tool == "look":
            await self.keep_look(identity, answer)
        return answer

    async def home_result(self, call_id, identity, args, answer):
        """El nodo decide (HomePolicy). Si queda `pending`, se registra para la consola, verificando el digest."""
        try:
            res = msgspec.convert(answer.structured or {}, HomeCallResult)
        except msgspec.ValidationError as exc:
            return result(call_id, {"error": f"HomeCallResult inválido del nodo: {exc}"}, ok=False)
        if res.status == "pending":
            expected = action_digest(args)
            if not res.confirmation_id or not hmac.compare_digest(res.action_digest or "", expected):
                return result(
                    call_id,
                    {"error": "confirmación sin id o con action_digest que no calza"},
                    ok=False,
                    request_id=args.request_id,
                )
            await self.store.confirmation_upsert(
                identity.node_id,
                identity.space_id,
                id=res.confirmation_id,
                request_id=args.request_id,
                action_digest=expected,
                summary=f"{args.domain}.{args.service}",
                expires_ns=res.expires_ns,
            )
        return answer

    async def keep_look(self, identity, answer):
        for item in answer.content:
            if isinstance(item, ImageContent):
                try:
                    data = base64.b64decode(item.data, validate=True)
                except ValueError:
                    return
                if len(data) <= MAX_LOOK_BYTES:
                    await self.store.put_thumbnail(
                        identity.space_id, LOOK_PHOTO_ID, data, item.mime_type, "última foto"
                    )
                return

    async def wait_events(self, node_id, args):
        deadline = asyncio.get_running_loop().time() + args.timeout_s
        # Suscribirse antes de leer: un commit entre la lectura y la espera no se pierde.
        async with self.bus.subscribe(node_id) as queue:
            while True:
                page = await self.store.page(node_id, args.cursor, args.types, args.max_events)
                if page["events"] or page["gap"]:
                    return page
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return page
                try:
                    await asyncio.wait_for(queue.get(), remaining)
                except TimeoutError:
                    return await self.store.page(node_id, args.cursor, args.types, args.max_events)

    # ------------------------------------------------------------------ protocolo del nodo
    async def accept_snapshot(self, conn: Connection, node_id, snapshot: StateSnapshot):
        if snapshot.cursor < 0:
            raise ValueError("state_snapshot.cursor debe ser >= 0")
        if conn.stream_id is not None and snapshot.stream_id != conn.stream_id:
            raise ValueError("state_snapshot.stream_id distinto del anunciado en hello")
        await self.store.snapshot(node_id, snapshot)

    async def accept_gap(self, conn: Connection, node_id, gap: HistoryGap):
        if not conn.replaying:
            raise ValueError("history_gap solo antes de replay_done")
        if gap.from_ < 1 or (gap.to is not None and gap.to < gap.from_):
            raise ValueError("history_gap con rango inválido")
        await self.store.gap(node_id, gap)
        await self.wake(node_id)

    async def accept_events(self, conn: Connection, node_id, batch):
        if batch.stream_id != conn.stream_id:
            raise ValueError("event_batch de otro stream_id")
        if len(batch.events) > MAX_BATCH_EVENTS:
            raise ValueError(f"event_batch con más de {MAX_BATCH_EVENTS} eventos")
        if batch.replay and not conn.replaying:
            raise ValueError("event_batch{replay:true} después de replay_done")
        seqs = [event.seq for event in batch.events]
        if seqs and (seqs != sorted(set(seqs)) or seqs[0] < 1 or batch.cursor != seqs[-1]):
            raise ValueError("event_batch: seqs positivos crecientes y cursor == último seq")
        if not seqs:
            return
        for event in batch.events:
            if event.node_id != node_id or event.v != conn.contract_version:
                raise ValueError("event_batch: node_id o versión inválidos")
        fresh = await self.store.append(node_id, batch)
        await self.track_confirmations(node_id, fresh)
        await self.wake(node_id)

    async def track_confirmations(self, node_id, fresh):
        row = None
        for event in fresh:
            p = event.payload
            if isinstance(p, ConfirmationRequested):
                row = row or await self.store.node(node_id)
                await self.store.confirmation_upsert(
                    node_id,
                    row["space_id"],
                    id=p.confirmation_id,
                    request_id=p.request_id,
                    action_digest=p.action_digest,
                    summary=p.summary,
                    expires_ns=p.expires_ns,
                )
            elif isinstance(p, ConfirmationResolved):
                status = "expired" if p.via == "timeout" else ("approved" if p.approved else "rejected")
                await self.store.confirmation_update(
                    node_id, p.confirmation_id, status=status, via=p.via, resolved_at=time.time()
                )
            elif isinstance(p, HomeCallDone) and p.confirmation_id:
                await self.store.confirmation_update(node_id, p.confirmation_id, outcome=p.status)

    async def confirm(self, identity: Identity, cid: str, approve: bool, digest_: str, operator: str):
        """Consola (token de personas) → nodo. El nodo verifica de nuevo id + digest y publica el desenlace."""
        if identity.scope != "console":
            raise Rejected(403, "Solo un token de consola puede confirmar; el token MCP de agentes no")
        row = await self.store.confirmation(identity.space_id, cid)
        if row is None:
            raise Rejected(404, "confirmation_id desconocido en este espacio")
        if row["status"] not in ("pending", "sent"):
            raise Rejected(409, f"La confirmación ya está {row['status']}")
        if row["expires_ns"] and time.time_ns() > row["expires_ns"]:
            await self.store.confirmation_update(
                row["node_id"], cid, status="expired", resolved_at=time.time()
            )
            raise Rejected(410, "La confirmación venció")
        if not hmac.compare_digest(digest_, row["action_digest"]):
            raise Rejected(409, "action_digest no coincide con la acción pendiente")
        conn = self.connected(row["node_id"])
        if conn is None:
            raise Rejected(503, "Nodo desconectado; confirma con un toque en la mesa o reintenta")
        message = ConfirmAction(  # v2.1: viaja como CloudMessage
            confirmation_id=cid, approved=approve, by="console", action_digest=digest_, operator=operator
        )
        await conn.send(message)
        await self.store.confirmation_update(row["node_id"], cid, status="sent", operator=operator)
        return {"confirmation_id": cid, "status": "sent", "approve": approve}

    def pick_version(self, offered: list[int]) -> int | None:
        common = set(offered) & set(SUPPORTED_VERSIONS)
        return max(common) if common else None
