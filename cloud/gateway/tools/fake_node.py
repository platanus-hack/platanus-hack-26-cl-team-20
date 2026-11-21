"""Synthetic node with automatic reconnect; never accesses physical devices."""

import argparse
import asyncio
import os
import time
from contextlib import suppress

import msgspec
from pcx_contracts.cloud import (
    CloudMessage,
    EventBatch,
    Heartbeat,
    Hello,
    ImageContent,
    StateSnapshot,
    TextContent,
    ToolCall,
    ToolResult,
    Welcome,
)
from pcx_contracts.devices import Device
from pcx_contracts.envelope import wrap
from pcx_contracts.events import AskAnswered, FlowEmitted
from pcx_contracts.geometry import PointMM, Surface
from pcx_contracts.tools import parse_args
from pcx_contracts.world import ObjectState, WorldState
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

# A valid 1x1 PNG: deliberately synthetic; no camera capture or image service.
PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII="


class FakeNode:
    def __init__(self, url="ws://127.0.0.1:9210/node", node_id="demo-node", token=None):
        self.url, self.node_id = url, node_id
        self.token = token or os.environ["PCX_NODE_TOKEN"]
        self.seq = 0
        self.events = []
        self.calls = []
        self.ignore_tools = set()
        self.connected = asyncio.Event()
        self.welcome = None
        self.ws = None
        self.lock = asyncio.Lock()
        now = time.time_ns()
        self.world = WorldState(
            rev=1,
            surface=Surface(width_mm=1000, height_mm=600),
            objects=[
                ObjectState(
                    id="cup",
                    label="taza sintética",
                    confidence=1,
                    polygon_mm=[PointMM(100, 100), PointMM(160, 100), PointMM(160, 160), PointMM(100, 160)],
                    center_mm=PointMM(130, 130),
                    since=now,
                    last_seen=now,
                )
            ],
            devices=[Device(id="synthetic-camera", kind="camera", adapter="fake", status="online")],
        )

    def record(self, payload):
        self.seq += 1
        event = wrap(payload, seq=self.seq, source="fake-core", node_id=self.node_id)
        self.events.append(event)
        return event

    async def send(self, message):
        async with self.lock:
            await self.ws.send(msgspec.json.encode(message).decode())

    async def snapshot(self):
        await self.send(StateSnapshot(cursor=self.seq, world=self.world))

    async def emit(self, payload):
        event = self.record(payload)
        await self.send(EventBatch(cursor=self.seq, events=[event]))

    async def handle(self, call):
        self.calls.append(call)
        if call.tool in self.ignore_tools:
            return
        args = parse_args(call.tool, call.args)
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
            answer = AskAnswered(request_id=args.request_id, answer=args.options[0], index=0)
            await self.emit(answer)
            data.update(answer=answer.answer, index=0, timed_out=False)
        elif call.tool == "look":
            content.append(ImageContent(data=PNG))
            data["summary"] = "Imagen sintética de prueba; taza cup en [130,130] mm"
        elif call.tool == "home" and args.requires_confirmation:
            await self.send(
                ToolResult(
                    call_id=call.call_id, ok=False, content=[TextContent(text="Pendiente de confirmación")]
                )
            )
            return
        content.append(TextContent(text=msgspec.json.encode(data).decode()))
        await self.send(ToolResult(call_id=call.call_id, ok=True, content=content, structured=data))

    async def run_once(self):
        tasks = set()
        try:
            async with connect(self.url, max_size=8 * 1024 * 1024) as ws:
                self.ws = ws
                await self.send(
                    Hello(
                        node_id=self.node_id, token=self.token, version="fake-1", capabilities=["synthetic"]
                    )
                )
                self.welcome = msgspec.json.decode(await ws.recv(), type=CloudMessage)
                if not isinstance(self.welcome, Welcome):
                    raise TypeError("Expected Welcome")
                await self.snapshot()
                replay = [event for event in self.events if event.seq > (self.welcome.last_cursor or 0)]
                if replay:
                    await self.send(EventBatch(cursor=replay[-1].seq, events=replay))
                self.connected.set()

                async def heartbeat():
                    while True:
                        await asyncio.sleep(10)
                        await self.send(Heartbeat(t_ms=time.time() * 1000, cursor=self.seq))

                tasks.add(asyncio.create_task(heartbeat()))
                async for raw in ws:
                    message = msgspec.json.decode(raw, type=CloudMessage)
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
