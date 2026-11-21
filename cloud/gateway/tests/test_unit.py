import asyncio
import time

import msgspec
import pytest
from pcx_contracts.cloud import EventBatch, StateSnapshot
from pcx_contracts.events import FlowEmitted, ObjectAppeared, ObjectMoved, ObjectRemoved
from pcx_contracts.geometry import PointMM
from pcx_contracts.world import ObjectState
from sqlalchemy import select

from pcx_gateway.config import Settings
from pcx_gateway.gateway import Connection, Gateway, parse_time
from pcx_gateway.pubsub import MemoryBus
from pcx_gateway.store import Store, digest, tool_calls
from tools.fake_node import FakeNode, square


def test_parse_time_formats():
    now = time.time_ns()
    assert abs(parse_time("2h") - (now - 2 * 3600 * 10**9)) < 10**9
    assert parse_time("1700000000") == 1700000000 * 10**9
    assert parse_time("2026-11-21T00:00:00Z") == parse_time("2026-11-21T00:00:00")
    assert parse_time(None) is None
    with pytest.raises(ValueError):
        parse_time("ayer")


async def test_memory_bus_broadcast_isolation_and_cleanup():
    bus = MemoryBus()
    async with bus.subscribe("node") as a, bus.subscribe("node") as b, bus.subscribe("other") as other:
        await bus.publish("node")
        await bus.publish("node")  # Se coalescen: los datos durables están en la DB.
        assert a.qsize() == b.qsize() == 1
        assert other.empty()
        await asyncio.wait_for(a.get(), 0.1)
    assert not bus.listeners
    await bus.close()


async def test_store_restart_migration_tokens_and_memory(tmp_path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/state.db",
        user_token="u",
        node_token="n",
        console_token="c",
    )
    store = Store(settings.database_url)
    fake = FakeNode(token="n")
    try:
        await store.start(settings)
        await store.start(settings)  # Migración + aprovisionamiento idempotentes.
        identity = await store.identity("u")
        assert identity.node_id == "demo-node" and identity.scope == "mcp"
        assert (await store.identity("c")).scope == "console"
        assert await store.identity("bad") is None
        assert await store.authenticate_node("demo-node", "n") and not await store.authenticate_node(
            "demo-node", "u"
        )
        await store.snapshot("demo-node", StateSnapshot(stream_id=fake.stream_id, cursor=0, world=fake.world))
        now = time.time_ns()
        keys = ObjectState(
            id="k1",
            label="llaves",
            confidence=0.9,
            polygon_mm=square(400, 300),
            center_mm=PointMM(430, 330),
            since_ns=now,
            last_seen_ns=now,
        )
        for payload in (
            ObjectAppeared(object=keys),
            ObjectMoved(object_id="k1", center_mm=PointMM(800, 500)),
            ObjectRemoved(object_id="k1"),
        ):
            fake.record(payload)
        batch = EventBatch(stream_id=fake.stream_id, cursor=3, events=fake.events)
        assert len(await store.append("demo-node", batch)) == 3
        assert await store.append("demo-node", batch) == []  # Replay idempotente.
        found = await store.history(
            "demo-space", query="LLAV", since_ns=None, until_ns=None, types=[], limit=10
        )
        assert found["matches"][0]["center_mm"] == [800, 500] and not found["matches"][0]["present"]
        assert [e["type"] for e in found["events"]] == ["object_removed", "object_moved", "object_appeared"]
        assert all(e["label"] == "llaves" for e in found["events"])
        none = await store.history("demo-space", query="%", since_ns=None, until_ns=None, types=[], limit=10)
        assert none["matches"] == []  # LIKE escapado: «%» no es comodín.
        await store.audit("interrupted", identity, "project", {"widgets": [], "request_id": "r1"})
        row = await store.node("demo-node")
        assert row["token_hash"] == digest("n") and row["token_hash"] != "n"
    finally:
        await store.close()
    restarted = Store(settings.database_url)
    try:
        await restarted.start(settings)
        assert await restarted.resume_point("demo-node") == (fake.stream_id, 3)
        async with restarted.engine.connect() as db:
            assert await db.scalar(select(tool_calls.c.status)) == "interrupted"
        await restarted.purge(retention_days=0)
        assert await restarted.cursor("demo-node") == 0
        assert (
            await restarted.history(
                "demo-space", query="llaves", since_ns=None, until_ns=None, types=[], limit=5
            )
        )["matches"]  # La memoria de objetos se conserva.
    finally:
        await restarted.close()


async def test_invalid_batches_rejected(tmp_path):
    store = Store(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    settings = Settings(user_token="u", node_token="n")
    bus = MemoryBus()
    gateway = Gateway(store, bus, settings)
    try:
        await store.start(settings)
        fake = FakeNode(token="n")
        conn = Connection(None, stream_id=fake.stream_id)
        await gateway.accept_snapshot(
            conn, fake.node_id, StateSnapshot(stream_id=fake.stream_id, cursor=0, world=fake.world)
        )
        event = fake.record(FlowEmitted(event="one"))
        s = fake.stream_id
        for batch in [
            EventBatch(stream_id=s, cursor=2, events=[event]),
            EventBatch(stream_id=s, cursor=1, events=[event, event]),
            EventBatch(stream_id="other-stream", cursor=1, events=[event]),
            EventBatch(stream_id=s, cursor=1, events=[msgspec.structs.replace(event, node_id="other")]),
        ]:
            with pytest.raises(ValueError):
                await gateway.accept_events(conn, fake.node_id, batch)
        with pytest.raises(msgspec.ValidationError):  # v2: type ≠ payload.type y v desconocida.
            msgspec.convert({**msgspec.to_builtins(event), "type": "object_moved"}, type(event))
        with pytest.raises(msgspec.ValidationError):
            msgspec.convert({**msgspec.to_builtins(event), "v": 1}, type(event))
        assert await store.cursor(fake.node_id) == 0
        conn.replaying = False
        with pytest.raises(ValueError, match="replay"):
            await gateway.accept_events(
                conn, fake.node_id, EventBatch(stream_id=s, cursor=1, events=[event], replay=True)
            )
    finally:
        await store.close()
        await bus.close()


async def test_redis_adapter_subscription_ack_and_broadcast():
    from fakeredis import FakeServer
    from fakeredis.aioredis import FakeRedis

    from pcx_gateway.pubsub import RedisBus

    server = FakeServer()
    subscriber = RedisBus("redis://unused")
    publisher = RedisBus("redis://unused")
    subscriber.redis = FakeRedis(server=server)
    publisher.redis = FakeRedis(server=server)
    try:
        async with subscriber.subscribe("node") as queue, subscriber.subscribe("other") as other:
            await publisher.publish("node")
            await asyncio.wait_for(queue.get(), 1)
            assert other.empty()
        await publisher.publish("node")
        assert queue.empty()
    finally:
        await subscriber.close()
        await publisher.close()


def test_distinct_tokens_required():
    with pytest.raises(ValueError, match="distintos"):
        asyncio.run(Store("sqlite+aiosqlite://").provision(Settings(user_token="x", node_token="x")))
