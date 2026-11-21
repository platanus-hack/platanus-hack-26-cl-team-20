"""Nodo sintético (contratos v2) con reconexión; nunca accede a dispositivos físicos ni cámaras."""

import argparse
import asyncio
import os
import time
from contextlib import suppress
from uuid import uuid4

import msgspec
from pcx_contracts.cloud import (
    CloudMessage,
    EventBatch,
    Heartbeat,
    Hello,
    HistoryGap,
    ImageContent,
    ReplayDone,
    StateSnapshot,
    TextContent,
    ToolCall,
    ToolResult,
    Welcome,
)
from pcx_contracts.devices import Device
from pcx_contracts.envelope import CONTRACT_VERSION, wrap
from pcx_contracts.events import (
    AskAnswered,
    ConfirmationRequested,
    ConfirmationResolved,
    FlowEmitted,
    HomeCallDone,
)
from pcx_contracts.geometry import PointMM, Surface
from pcx_contracts.home import ConfirmAction, HomeCallResult, HomePolicy, action_digest, decide
from pcx_contracts.tools import parse_args
from pcx_contracts.world import ObjectState, WorldState
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

# PNG 1x1 válido y sintético: sin captura de cámara.
PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII="


def square(x, y, side=60):
    return [PointMM(x, y), PointMM(x + side, y), PointMM(x + side, y + side), PointMM(x, y + side)]


class FakeNode:
    def __init__(self, url="ws://127.0.0.1:9210/node", node_id="demo-node", token=None):
        self.url, self.node_id = url, node_id
        self.token = token or os.environ["PCX_NODE_TOKEN"]
        self.stream_id = str(uuid4())
        self.contract_versions = [CONTRACT_VERSION]
        self.seq = 0
        self.events = []
        self.log_first = 1  # primer seq retenido (simula retención con `forget`)
        self.calls = []
        self.ignore_tools = set()
        self.connected = asyncio.Event()
        self.welcome = None
        self.ws = None
        self.lock = asyncio.Lock()
        self.policy = HomePolicy(allow=["light.*", "lock.*"], confirm=["lock.*"])
        self.pending_confirmations = {}  # confirmation_id -> (request_id, digest)
        now = time.time_ns()
        self.world = WorldState(
            rev=1,
            surface=Surface(width_mm=1000, height_mm=600),
            objects=[
                ObjectState(
                    id="cup",
                    label="taza sintética",
                    confidence=1,
                    polygon_mm=square(100, 100),
                    center_mm=PointMM(130, 130),
                    since_ns=now,
                    last_seen_ns=now,
                )
            ],
            devices=[Device(id="synthetic-camera", kind="camera", adapter="fake", status="online")],
        )

    def record(self, payload):
        self.seq += 1
        event = wrap(payload, seq=self.seq, source="fake-core", node_id=self.node_id)
        self.events.append(event)
        return event

    def forget(self, up_to):
        """Simula retención: el log local descarta seq <= up_to."""
        self.events = [e for e in self.events if e.seq > up_to]
        self.log_first = up_to + 1

    def reset_stream(self):
        """Simula que el nodo perdió su log: stream nuevo, seq desde 1."""
        self.stream_id, self.seq, self.events, self.log_first = str(uuid4()), 0, [], 1

    async def send(self, message):
        async with self.lock:
            await self.ws.send(msgspec.json.encode(message).decode())

    async def snapshot(self):
        await self.send(StateSnapshot(stream_id=self.stream_id, cursor=self.seq, world=self.world))

    async def emit(self, payload):
        event = self.record(payload)
        await self.send(EventBatch(stream_id=self.stream_id, cursor=self.seq, events=[event]))
        return event

    async def handle(self, call: ToolCall):
        self.calls.append(call)
        if call.tool in self.ignore_tools:
            return
        args = parse_args(call.tool, call.args)
        request_id = getattr(args, "request_id", None)
        data = {"synthetic": True, "tool": call.tool}
        content = []
        if call.tool == "project":
            widgets = {w.id: w for w in self.world.widgets}
            widgets.update({w.id: w for w in args.widgets})
            self.world.widgets = list(widgets.values())
            self.world.rev += 1
            await self.snapshot()
            await self.emit(FlowEmitted(event="projected", data={"ids": list(widgets)}))
            data["ids"] = [w.id for w in args.widgets]
        elif call.tool == "remove":
            self.world.widgets = [w for w in self.world.widgets if w.id not in args.ids]
            self.world.rev += 1
            await self.snapshot()
            data["removed"] = args.ids
        elif call.tool == "ask":
            await asyncio.sleep(0.02)
            answer = AskAnswered(request_id=request_id, answer=args.options[0], index=0)
            await self.emit(answer)
            data.update(answer=answer.answer, index=0, timed_out=False)
        elif call.tool == "look":
            content.append(ImageContent(data=PNG))
            data["summary"] = "Imagen sintética de prueba; taza cup en [130,130] mm"
        elif call.tool == "home":
            data = await self.home(args)
        content.append(TextContent(text=msgspec.json.encode(data).decode()))
        await self.send(
            ToolResult(call_id=call.call_id, request_id=request_id, ok=True, content=content, structured=data)
        )

    async def home(self, call):
        """Política local (HomePolicy): el agente no la ve ni la puede saltar."""
        decision = decide(self.policy, call)
        if decision == "deny":
            return msgspec.to_builtins(HomeCallResult(request_id=call.request_id, status="denied"))
        if decision == "allow":
            await self.emit(HomeCallDone(request_id=call.request_id, status="done"))
            return msgspec.to_builtins(HomeCallResult(request_id=call.request_id, status="done"))
        cid, digest = str(uuid4()), action_digest(call)
        expires = time.time_ns() + int(self.policy.confirm_timeout_s * 1e9)
        self.pending_confirmations[cid] = (call.request_id, digest)
        await self.emit(
            ConfirmationRequested(
                confirmation_id=cid,
                request_id=call.request_id,
                action_digest=digest,
                summary=f"{call.domain}.{call.service}",
                via=self.policy.confirm_via,
                expires_ns=expires,
            )
        )
        result = HomeCallResult(
            request_id=call.request_id,
            status="pending",
            confirmation_id=cid,
            action_digest=digest,
            expires_ns=expires,
        )
        return msgspec.to_builtins(result)

    async def resolve(self, cid, approved, via, digest=None):
        request_id, expected = self.pending_confirmations.get(cid, (None, None))
        if request_id is None or (digest is not None and digest != expected):
            return False
        del self.pending_confirmations[cid]
        await self.emit(
            ConfirmationResolved(
                confirmation_id=cid, request_id=request_id, action_digest=expected, approved=approved, via=via
            )
        )
        status = "done" if approved else "rejected"
        await self.emit(HomeCallDone(request_id=request_id, confirmation_id=cid, status=status))
        return True

    async def touch_confirm(self, cid):
        """Toque físico (dwell) sobre `pcx:confirm:<cid>`."""
        return await self.resolve(cid, True, "touch")

    async def on_frame(self, raw):
        kind = msgspec.json.decode(raw).get("type")
        if kind == "confirm_action":  # Consola vía nube (no es CloudMessage: ver REVISION.md).
            action = msgspec.json.decode(raw, type=ConfirmAction)
            await self.resolve(action.confirmation_id, action.approve, "console", action.action_digest)
            return None
        return msgspec.json.decode(raw, type=CloudMessage)

    async def resume(self):
        """Reconexión v2: snapshot → history_gap(s) → replay → replay_done."""
        welcome = self.welcome
        await self.snapshot()
        gaps = []
        last = welcome.last_cursor or 0
        if welcome.last_stream_id not in (None, self.stream_id):
            gaps.append(
                HistoryGap(stream_id=welcome.last_stream_id, from_=last + 1, to=None, reason="stream_reset")
            )
            last = 0
            if self.log_first > 1:
                gaps.append(
                    HistoryGap(
                        stream_id=self.stream_id, from_=1, to=self.log_first - 1, reason="stream_reset"
                    )
                )
        elif last + 1 < self.log_first:
            gaps.append(
                HistoryGap(
                    stream_id=self.stream_id, from_=last + 1, to=self.log_first - 1, reason="retention"
                )
            )
        for gap in gaps:
            await self.send(gap)
        replay = [event for event in self.events if event.seq > last]
        if replay:
            await self.send(
                EventBatch(stream_id=self.stream_id, cursor=replay[-1].seq, events=replay, replay=True)
            )
        await self.send(ReplayDone(stream_id=self.stream_id, cursor=self.seq, history_complete=not gaps))

    async def run_once(self):
        tasks = set()
        try:
            async with connect(self.url, max_size=8 * 1024 * 1024) as ws:
                self.ws = ws
                await self.send(
                    Hello(
                        node_id=self.node_id,
                        token=self.token,
                        version="fake-2",
                        capabilities=["synthetic"],
                        contract_versions=self.contract_versions,
                        stream_id=self.stream_id,
                        log_first_cursor=self.log_first if self.events else 0,
                        log_last_cursor=self.seq,
                    )
                )
                self.welcome = msgspec.json.decode(await ws.recv(), type=CloudMessage)
                if not isinstance(self.welcome, Welcome):
                    raise TypeError(f"Se esperaba welcome: {self.welcome}")
                await self.resume()
                self.connected.set()

                async def heartbeat():
                    while True:
                        await asyncio.sleep(10)
                        await self.send(Heartbeat(t_ms=time.time() * 1000, cursor=self.seq))

                tasks.add(asyncio.create_task(heartbeat()))
                async for raw in ws:
                    message = await self.on_frame(raw)
                    if isinstance(message, ToolCall):
                        task = asyncio.create_task(self.handle(message))
                        tasks.add(task)
        finally:
            self.connected.clear()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.ws = None

    async def run(self):
        delay = 0.5
        while True:
            try:
                await self.run_once()
                delay = 0.5
            except (ConnectionClosed, OSError, TimeoutError):
                pass
            await asyncio.sleep(delay)
            delay = min(delay * 2, 10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="ws://127.0.0.1:9210/node")
    parser.add_argument("--node-id", default=os.getenv("PCX_NODE_ID", "demo-node"))
    args = parser.parse_args()
    with suppress(KeyboardInterrupt):
        asyncio.run(FakeNode(args.url, args.node_id).run())


if __name__ == "__main__":
    main()
