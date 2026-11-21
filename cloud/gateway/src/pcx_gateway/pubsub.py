"""Wakeups only: the database remains the durable source for every event page."""

import asyncio
from collections import defaultdict
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol

from redis.asyncio import Redis


class Bus(Protocol):
    def subscribe(self, node_id: str) -> AbstractAsyncContextManager[asyncio.Queue]: ...
    async def publish(self, node_id: str) -> None: ...
    async def close(self) -> None: ...


class MemoryBus:
    def __init__(self):
        self.listeners = defaultdict(set)

    @asynccontextmanager
    async def subscribe(self, node_id):
        queue = asyncio.Queue(maxsize=1)
        self.listeners[node_id].add(queue)
        try:
            yield queue
        finally:
            self.listeners[node_id].discard(queue)
            if not self.listeners[node_id]:
                del self.listeners[node_id]

    async def publish(self, node_id):
        for queue in self.listeners.get(node_id, ()):
            if queue.empty():
                queue.put_nowait(None)

    async def close(self):
        self.listeners.clear()


class RedisBus:
    def __init__(self, url):
        self.redis = Redis.from_url(url)

    @asynccontextmanager
    async def subscribe(self, node_id):
        queue = asyncio.Queue(maxsize=1)
        async with self.redis.pubsub() as pubsub:
            await pubsub.subscribe(f"pcx:events:{node_id}")
            # subscribe() writes the command; wait for the server acknowledgement
            # before the caller reads its first page, otherwise a wakeup can be lost.
            async with asyncio.timeout(5):
                while True:
                    message = await pubsub.get_message(ignore_subscribe_messages=False, timeout=1)
                    if message and message["type"] == "subscribe":
                        break

            async def pump():
                async for message in pubsub.listen():
                    if message["type"] == "message" and queue.empty():
                        queue.put_nowait(None)

            task = asyncio.create_task(pump())
            try:
                yield queue
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def publish(self, node_id):
        await self.redis.publish(f"pcx:events:{node_id}", "changed")

    async def close(self):
        await self.redis.aclose()
