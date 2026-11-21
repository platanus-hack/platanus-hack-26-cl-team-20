import asyncio

import msgspec
import pytest
from pcx_contracts.cloud import EventBatch, StateSnapshot
from pcx_contracts.commands import Flow, FlowState, HomeCall, RunFlow, Transition
from pcx_contracts.envelope import wrap
from pcx_contracts.events import FlowEmitted

from pcx_gateway.config import Settings
from pcx_gateway.gateway import Gateway, needs_confirmation
from pcx_gateway.pubsub import MemoryBus
from pcx_gateway.store import Store, digest
from tools.fake_node import FakeNode


def test_home_confirmation_in_entry_and_transition():
    home = HomeCall(domain="lock", service="unlock")
    assert needs_confirmation(home)
    assert not needs_confirmation(HomeCall(domain="light", service="turn_on", requires_confirmation=False))
    for state in [
        FlowState(id="start", on_enter=[home]),
        FlowState(id="start", transitions=[Transition(to="start", after_s=1, actions=[home])]),
    ]:
        assert needs_confirmation(RunFlow(flow=Flow(id="demo", initial="start", states=[state])))


async def test_memory_bus_broadcast_isolation_and_cleanup():
    bus = MemoryBus()
    async with bus.subscribe("node") as a, bus.subscribe("node") as b, bus.subscribe("other") as other:
        await bus.publish("node")
        await bus.publish("node")  # Coalesce wakeups; event data is durable in the store.
        assert a.qsize() == b.qsize() == 1
        assert other.empty()
        await asyncio.wait_for(a.get(), 0.1)
    assert not bus.listeners
    await bus.close()


async def test_store_snapshot_log_cursor_separation_and_restart(tmp_path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/state.db", user_token="u", node_token="n"
    )
    store = Store(settings.database_url)
    fake = FakeNode(token="n")
    try:
        await store.start(settings)
        await store.start(settings)  # Startup migration + provisioning is idempotent.
        identity = await store.identity("u")
        assert identity.node_id == "demo-node" and await store.identity("bad") is None
        await store.snapshot("demo-node", StateSnapshot(cursor=20, world=fake.world))
        assert await store.cursor("demo-node") is None
        batch = EventBatch(
            cursor=1, events=[wrap(FlowEmitted(event="one"), seq=1, source="core", node_id="demo-node")]
        )
        await store.append("demo-node", batch)
        await store.append("demo-node", batch)
        assert await store.cursor("demo-node") == 1
        await store.audit("interrupted", identity, "project", {"widgets": []})
        row = await store.node("demo-node")
        assert row["token_hash"] == digest("n") and row["token_hash"] != "n"
    finally:
        await store.close()
    restarted = Store(settings.database_url)
    try:
        await restarted.start(settings)
        assert await restarted.cursor("demo-node") == 1
        from sqlalchemy import select

        from pcx_gateway.store import tool_calls

        async with restarted.engine.connect() as db:
            assert await db.scalar(select(tool_calls.c.status)) == "interrupted"
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
        await gateway.accept_snapshot(fake.node_id, StateSnapshot(cursor=0, world=fake.world))
        event = fake.record(FlowEmitted(event="one"))
        for batch in [
            EventBatch(cursor=2, events=[event]),
            EventBatch(cursor=2, events=[]),
            EventBatch(cursor=1, events=[event, event]),
            EventBatch(cursor=1, events=[msgspec.structs.replace(event, node_id="other")]),
            EventBatch(cursor=1, events=[msgspec.structs.replace(event, type="object_moved")]),
        ]:
            with pytest.raises(ValueError):
                await gateway.accept_events(fake.node_id, batch)
        assert await store.cursor(fake.node_id) is None
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
        await publisher.publish("node")  # Closing a subscriber removes its pump cleanly.
        assert queue.empty()
    finally:
        await subscriber.close()
        await publisher.close()
