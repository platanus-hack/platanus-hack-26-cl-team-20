import asyncio
import dataclasses
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


async def test_look_storage_is_opt_in_and_thumbnails_expire(tmp_path):
    import base64
    from dataclasses import replace

    from pcx_contracts.cloud import ImageContent, ToolResult
    from sqlalchemy import update

    from pcx_gateway.store import objects

    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/photos.db", user_token="u", node_token="n"
    )
    store = Store(settings.database_url)
    bus = MemoryBus()
    try:
        await store.start(settings)
        identity = await store.identity("u")
        gateway = Gateway(store, bus, settings)
        answer = ToolResult(
            call_id="look",
            ok=True,
            content=[ImageContent(data=base64.b64encode(b"photo").decode(), mime_type="image/png")],
        )
        await gateway.keep_look(identity, answer)
        assert await store.thumbnail(identity.space_id, "pcx:look") is None
        gateway.settings = replace(settings, keep_look=True)
        await gateway.keep_look(identity, answer)
        assert (await store.thumbnail(identity.space_id, "pcx:look"))[0] == b"photo"
        async with store.engine.begin() as db:
            await db.execute(
                update(objects).where(objects.c.object_id == "pcx:look").values(thumb_at=time.time() - 86401)
            )
        assert await store.thumbnail(identity.space_id, "pcx:look") is None
        await store.purge(30)
        async with store.engine.connect() as db:
            assert await db.scalar(select(objects.c.thumb).where(objects.c.object_id == "pcx:look")) is None
    finally:
        await store.close()
        await bus.close()


async def test_provision_env_is_authoritative(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path}/rotate.db"
    first = Settings(database_url=url, user_token="u1", node_token="n1", console_token="c1")
    store = Store(url)
    try:
        await store.start(first)
        rotated = dataclasses.replace(first, user_token="u2", node_token="n2", console_token="c2")
        await store.start(rotated)  # No falla: el entorno manda.
        assert await store.identity("u1") is None and await store.identity("c1") is None
        assert (await store.identity("u2")).scope == "mcp"
        assert (await store.identity("c2")).scope == "console"
        assert not await store.authenticate_node("demo-node", "n1")
        assert await store.authenticate_node("demo-node", "n2")
        # Intercambiar user y consola no deja un token con dos scopes.
        swapped = dataclasses.replace(rotated, user_token="c2", console_token="u2")
        await store.start(swapped)
        assert (await store.identity("c2")).scope == "mcp" and (await store.identity("u2")).scope == "console"
        # Sin PCX_CONSOLE_TOKEN no queda acceso de consola.
        await store.start(dataclasses.replace(swapped, console_token=None))
        assert await store.identity("u2") is None and await store.identity("c2") is not None
        # Los dueños no se reasignan desde el env.
        with pytest.raises(ValueError, match="otro dueño"):
            await store.provision(dataclasses.replace(first, user_id="intruso", user_token="x1"))
        with pytest.raises(ValueError, match="otro espacio"):
            await store.provision(dataclasses.replace(first, space_id="otro", user_token="x2"))
    finally:
        await store.close()


async def test_publish_failure_is_logged_not_raised(tmp_path, caplog):
    class BrokenBus(MemoryBus):
        async def publish(self, node_id):
            raise ConnectionError("Key Value caído")

    store = Store(f"sqlite+aiosqlite:///{tmp_path}/bus.db")
    settings = Settings(user_token="u", node_token="n")
    gateway = Gateway(store, BrokenBus(), settings)
    try:
        await store.start(settings)
        fake = FakeNode(token="n")
        conn = Connection(None, stream_id=fake.stream_id)
        await gateway.accept_snapshot(
            conn, fake.node_id, StateSnapshot(stream_id=fake.stream_id, cursor=0, world=fake.world)
        )
        event = fake.record(FlowEmitted(event="one"))
        await gateway.accept_events(
            conn, fake.node_id, EventBatch(stream_id=fake.stream_id, cursor=1, events=[event])
        )
        assert await store.cursor(fake.node_id) == 1
        assert "bus.publish falló" in caplog.text
    finally:
        await store.close()


def test_memory_bus_without_redis_url(monkeypatch, tmp_path):
    from pcx_gateway.app import create_app

    monkeypatch.setenv("REDIS_URL", "")
    assert Settings.from_env().redis_url is None
    app = create_app(Settings(database_url=f"sqlite+aiosqlite:///{tmp_path}/m.db"))
    assert isinstance(app.state.gateway.bus, MemoryBus)
