import asyncio
import json
from dataclasses import dataclass, field
from uuid import uuid4

import msgspec
from pcx_contracts.cloud import StateSnapshot, TextContent, ToolCall, ToolResult
from pcx_contracts.commands import HomeCall, RunFlow, check_flow
from pcx_contracts.envelope import CONTRACT_VERSION
from pcx_contracts.tools import parse_args
from pcx_contracts.world import WorldState

from .config import Settings
from .pubsub import Bus
from .store import Identity, Store

TOOL_TIMEOUTS = {"look": 20, "project": 15, "remove": 15, "run_flow": 15, "calibrate": 180, "home": 30}


class NodeOffline(RuntimeError):
    pass


@dataclass(eq=False)
class Connection:
    websocket: object
    session_id: str = field(default_factory=lambda: str(uuid4()))
    ready: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending: dict[str, asyncio.Future] = field(default_factory=dict)

    async def send(self, message):
        async with self.lock:
            await self.websocket.send_text(msgspec.json.encode(message).decode())

    def fail_pending(self):
        for future in self.pending.values():
            if not future.done():
                future.set_exception(
                    NodeOffline("Nodo desconectado durante la llamada; no se reintentó la acción")
                )


def result(call_id, data, *, ok=True):
    return ToolResult(
        call_id=call_id,
        ok=ok,
        structured=data,
        content=[TextContent(text=json.dumps(data, ensure_ascii=False))],
    )


def needs_confirmation(args):
    if isinstance(args, HomeCall):
        return args.requires_confirmation
    if isinstance(args, RunFlow):
        return any(
            isinstance(action, HomeCall) and action.requires_confirmation
            for state in args.flow.states
            for action in [*state.on_enter, *(a for t in state.transitions for a in t.actions)]
        )
    return False


class Gateway:
    def __init__(self, store: Store, bus: Bus, settings: Settings):
        self.store, self.bus, self.settings = store, bus, settings
        self.connections: dict[str, Connection] = {}

    async def execute(self, identity: Identity, tool: str, raw: dict):
        call_id = str(uuid4())
        try:
            args = parse_args(tool, raw)
            if isinstance(args, RunFlow) and (errors := check_flow(args.flow)):
                raise ValueError("; ".join(errors))
        except (KeyError, ValueError, msgspec.ValidationError) as exc:
            return result(call_id, {"error": f"Argumentos inválidos: {exc}"}, ok=False)
        await self.store.audit(call_id, identity, tool, raw)
        status = "failed"
        answer = None
        try:
            if needs_confirmation(args):
                status = "pending_confirmation"
                answer = result(
                    call_id, {"status": status, "message": "Pendiente de confirmación; no ejecutado"}
                )
            else:
                answer = await self.dispatch(call_id, identity, tool, args, raw)
                status = "completed" if answer.ok else "failed"
            return answer
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except (NodeOffline, TimeoutError, OSError, RuntimeError) as exc:
            status = "timeout" if isinstance(exc, TimeoutError) else "failed"
            return result(call_id, {"error": str(exc) or "Timeout esperando ToolResult del nodo"}, ok=False)
        finally:
            await self.store.finish(call_id, status, answer.ok if answer else False)

    async def dispatch(self, call_id, identity, tool, args, raw):
        node_id = identity.node_id
        conn = self.connections.get(node_id)
        if conn is None or not conn.ready:
            raise NodeOffline(
                f"Nodo {node_id} no conectado o sincronizando; vuelve a intentar cuando esté online"
            )
        if tool == "events_wait":
            return result(call_id, await self.wait_events(node_id, args))
        if tool in ("scene", "devices"):
            row = await self.store.node(node_id)
            world = msgspec.json.decode(row["world"], type=WorldState)
            if tool == "devices":
                return result(
                    call_id,
                    {
                        "devices": [
                            msgspec.to_builtins(d)
                            for d in world.devices
                            if args.kind is None or d.kind == args.kind
                        ]
                    },
                )
            data = msgspec.to_builtins(world)
            if not args.include_hands:
                data.pop("hands")
            if not args.include_devices:
                data.pop("devices")
            data.update(
                cursor=await self.store.cursor(node_id) or 0,
                snapshot_cursor=row["snapshot_cursor"],
                reported_cursor=row["reported_cursor"],
                node_id=node_id,
                connected=True,
            )
            return result(call_id, data)
        timeout = args.timeout_s + 5 if tool == "ask" else TOOL_TIMEOUTS[tool]
        future = asyncio.get_running_loop().create_future()
        conn.pending[call_id] = future
        try:
            async with asyncio.timeout(timeout):
                await conn.send(ToolCall(call_id=call_id, tool=tool, args=raw, timeout_s=timeout))
                return await future
        finally:
            conn.pending.pop(call_id, None)
            if not future.done():
                future.cancel()

    async def wait_events(self, node_id, args):
        cursor = args.cursor
        deadline = asyncio.get_running_loop().time() + args.timeout_s
        # Subscribe before reading, so a commit between read and wait cannot be missed.
        async with self.bus.subscribe(node_id) as queue:
            while True:
                events, cursor, more = await self.store.page(node_id, cursor, args.types, args.max_events)
                if events:
                    return {"events": events, "cursor": cursor, "has_more": more}
                conn = self.connections.get(node_id)
                if conn is None or not conn.ready:
                    raise NodeOffline("Nodo desconectado durante events_wait")
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return {"events": [], "cursor": cursor, "has_more": False}
                try:
                    await asyncio.wait_for(queue.get(), remaining)
                except TimeoutError:
                    return {"events": [], "cursor": cursor, "has_more": False}

    async def accept_snapshot(self, node_id, snapshot: StateSnapshot):
        if snapshot.cursor < 0:
            raise ValueError("snapshot cursor debe ser >= 0")
        await self.store.snapshot(node_id, snapshot)

    async def accept_events(self, node_id, batch):
        seqs = [event.seq for event in batch.events]
        if seqs and (seqs != sorted(set(seqs)) or seqs[0] < 1 or batch.cursor != seqs[-1]):
            raise ValueError("event_batch: seqs positivos crecientes y cursor == último seq")
        if not seqs and batch.cursor != (await self.store.cursor(node_id) or 0):
            raise ValueError("un lote vacío no puede avanzar el cursor")
        for event in batch.events:
            if (
                event.node_id != node_id
                or event.v != CONTRACT_VERSION
                or event.type != type(event.payload).__struct_config__.tag
            ):
                raise ValueError("event_batch: node_id, versión o type inválido")
        await self.store.append(node_id, batch)
        await self.bus.publish(node_id)
