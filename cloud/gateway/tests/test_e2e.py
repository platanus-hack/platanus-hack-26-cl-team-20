import asyncio
import base64

import httpx
import msgspec
import pytest
from conftest import eventually, fake_connected, live_server
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pcx_contracts.cloud import CloudMessage, EventBatch, Heartbeat, Hello, Welcome
from pcx_contracts.events import FlowEmitted
from pcx_contracts.tools import mcp_tools
from sqlalchemy import func, select
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from pcx_gateway.store import events, tool_calls


def data(reply):
    assert not reply.is_error, reply
    return reply.structured_content


async def test_mcp_full_roundtrip(live, node, http):
    app, url, _ws, settings = live
    async with (
        httpx.AsyncClient(headers={"Authorization": f"Bearer {settings.user_token}"}) as transport,
        streamable_http_client(f"{url}/mcp", http_client=transport) as (read, write),
        ClientSession(read, write) as client,
    ):
        initialized = await client.initialize()
        assert "table_mm" in initialized.instructions
        listed = await client.list_tools()
        assert {t.name for t in listed.tools} == {t["name"] for t in mcp_tools()}
        for tool, expected in zip(listed.tools, mcp_tools()):
            assert tool.input_schema == expected["inputSchema"]
        scene = data(await client.call_tool("scene", {}))
        assert scene["objects"][0]["id"] == "cup"
        assert scene["cursor"] == 0 and "hands" not in scene
        projection = data(
            await client.call_tool(
                "project",
                {
                    "widgets": [
                        {
                            "type": "label",
                            "id": "hello",
                            "text": "Hola",
                            "anchor": {"object_id": "cup"},
                        }
                    ]
                },
            )
        )
        assert projection["ids"] == ["hello"]
        assert data(await client.call_tool("scene", {}))["widgets"][0]["id"] == "hello"
        answer = data(await client.call_tool("ask", {"question": "¿Té?", "options": ["Sí", "No"]}))
        assert answer["answer"] == "Sí"
        page = data(await client.call_tool("events_wait", {"cursor": 0, "timeout_s": 1}))
        assert [e["type"] for e in page["events"]] == ["flow_emitted", "ask_answered"]
        assert page["cursor"] == 2
        image = await client.call_tool("look", {})
        assert not image.is_error
        assert base64.b64decode(image.content[0].data).startswith(b"\x89PNG")
        assert data(await client.call_tool("devices", {}))["devices"][0]["kind"] == "camera"
        pending = data(await client.call_tool("home", {"domain": "lock", "service": "unlock"}))
        assert pending["status"] == "pending_confirmation"
        assert "home" not in [c.tool for c in node.calls]
        assert (await client.call_tool("ask", {"question": "Invalid", "options": []})).is_error
        await client.call_tool("remove", {"ids": ["hello"]})
        assert data(await client.call_tool("scene", {}))["widgets"] == []
    assert (await http.get("/api/nodes")).json()["nodes"][0]["connected"]
    async with app.state.gateway.store.engine.connect() as db:
        assert await db.scalar(select(func.count()).select_from(tool_calls)) >= 9
        assert (
            await db.execute(select(tool_calls.c.status).where(tool_calls.c.tool == "home"))
        ).scalar() == "pending_confirmation"


async def test_long_poll_pagination_and_filter(live, node):
    gateway = live[0].state.gateway
    identity = await gateway.store.identity("test-user-secret")
    pending = asyncio.create_task(
        gateway.execute(
            identity, "events_wait", {"cursor": 0, "types": ["flow_emitted"], "timeout_s": 1, "max_events": 1}
        )
    )
    await asyncio.sleep(0.03)
    assert not pending.done()
    await node.emit(FlowEmitted(event="first"))
    page = (await pending).structured
    assert page["events"][0]["payload"]["event"] == "first"
    for index in range(3):
        await node.emit(FlowEmitted(event=str(index)))

    async def all_events():
        return await gateway.store.cursor(node.node_id) == 4

    await eventually(all_events)
    page = (
        await gateway.execute(identity, "events_wait", {"cursor": 1, "timeout_s": 0, "max_events": 1})
    ).structured
    assert page["cursor"] == 2 and page["has_more"]
    page = (
        await gateway.execute(
            identity, "events_wait", {"cursor": 1, "types": ["ask_answered"], "timeout_s": 0.01}
        )
    ).structured
    assert page == {"events": [], "cursor": 4, "has_more": False}


async def test_bad_tokens_offline_and_scope(live, http):
    app, _url, ws_url, settings = live
    assert (await http.get("/health")).status_code == 200
    assert (await http.get("/api/nodes", headers={"Authorization": "Bearer wrong"})).status_code == 401
    assert (await http.post("/mcp", headers={"Authorization": "Bearer wrong"}, json={})).status_code == 401
    async with connect(ws_url) as ws:
        await ws.send(
            msgspec.json.encode(Hello(node_id=settings.node_id, token="wrong", version="1")).decode()
        )
        with pytest.raises(ConnectionClosed) as error:
            await ws.recv()
        assert error.value.rcvd.code == 4401
    gateway = app.state.gateway
    identity = await gateway.store.identity(settings.user_token)
    reply = await gateway.execute(identity, "scene", {})
    assert not reply.ok and "no conectado" in reply.content[0].text
    other = type(settings)(
        database_url=settings.database_url,
        user_id="other-user",
        space_id="other-space",
        node_id="other-node",
        user_token="other-user-token",
        node_token="other-node-token",
    )
    await gateway.store.provision(other)
    other_identity = await gateway.store.identity(other.user_token)
    assert other_identity.node_id == other.node_id
    assert not await gateway.store.authenticate_node(settings.node_id, other.node_token)
    response = await http.get("/api/nodes", headers={"Authorization": "Bearer other-user-token"})
    assert [n["id"] for n in response.json()["nodes"]] == [other.node_id]


async def test_reconnect_cursor_replay_and_process_restart(live):
    app, _, ws_url, settings = live
    async with fake_connected(app, ws_url, settings) as node:
        assert node.welcome.last_cursor is None
        await node.emit(FlowEmitted(event="before-disconnect"))

        async def stored():
            return await app.state.gateway.store.cursor(node.node_id) == 1

        await eventually(stored)
    node.record(FlowEmitted(event="offline"))
    # Same DB, entirely new application/session manager: simulates a Render deploy.
    async with live_server(settings) as (second, _, second_ws):
        node.url = second_ws
        task = asyncio.create_task(node.run_once())
        try:
            await asyncio.wait_for(node.connected.wait(), 3)
            assert node.welcome.last_cursor == 1

            async def replayed():
                return await second.state.gateway.store.cursor(node.node_id) == 2

            await eventually(replayed)
            # Duplicate replay must not append a second copy.
            await node.send(EventBatch(cursor=2, events=node.events))
            async with second.state.gateway.store.engine.connect() as db:
                assert await db.scalar(select(func.count()).select_from(events)) == 2
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_heartbeat_and_protocol_rejection(live):
    _app, _, ws_url, settings = live
    async with connect(ws_url) as ws:
        await ws.send(
            msgspec.json.encode(
                Hello(node_id=settings.node_id, token=settings.node_token, version="1")
            ).decode()
        )
        assert isinstance(msgspec.json.decode(await ws.recv(), type=CloudMessage), Welcome)
        assert isinstance(msgspec.json.decode(await ws.recv(), type=CloudMessage), Heartbeat)
        # No StateSnapshot yet; event batch must be rejected.
        await ws.send(msgspec.json.encode(EventBatch(cursor=0, events=[])).decode())
        with pytest.raises(ConnectionClosed) as error:
            while True:
                await ws.recv()
        assert error.value.rcvd.code == 4400


async def test_disconnect_fails_pending_action(live, node):
    gateway = live[0].state.gateway
    identity = await gateway.store.identity("test-user-secret")
    node.ignore_tools.add("look")
    task = asyncio.create_task(gateway.execute(identity, "look", {}))

    async def sent():
        return bool(node.calls)

    await eventually(sent)
    await node.ws.close()
    reply = await asyncio.wait_for(task, 2)
    assert not reply.ok and "desconectado" in reply.content[0].text


async def test_timeout_cleanup_and_late_result(live, node, monkeypatch):
    from pcx_contracts.cloud import TextContent, ToolResult

    from pcx_gateway.gateway import TOOL_TIMEOUTS

    gateway = live[0].state.gateway
    identity = await gateway.store.identity("test-user-secret")
    monkeypatch.setitem(TOOL_TIMEOUTS, "look", 0.03)
    node.ignore_tools.add("look")
    reply = await gateway.execute(identity, "look", {})
    assert not reply.ok and "Timeout" in reply.content[0].text
    assert not gateway.connections[node.node_id].pending
    assert node.calls[-1].timeout_s == 0.03
    await node.send(ToolResult(call_id=node.calls[-1].call_id, ok=True, content=[TextContent(text="late")]))
    assert (await gateway.execute(identity, "scene", {})).ok
    async with gateway.store.engine.connect() as db:
        assert await db.scalar(select(tool_calls.c.status).where(tool_calls.c.tool == "look")) == "timeout"


async def test_binary_frames_and_idle_timeout(live, node):
    _, _, ws_url, settings = live
    async with connect(ws_url) as ws:
        await ws.send(
            msgspec.json.encode(Hello(node_id=settings.node_id, token=settings.node_token, version="1"))
        )
        assert isinstance(msgspec.json.decode(await ws.recv(), type=CloudMessage), Welcome)
        with pytest.raises(ConnectionClosed) as error:
            async with asyncio.timeout(3):
                while True:
                    await ws.recv()
        assert error.value.rcvd.code == 4400


async def test_mirror_uses_reported_events_and_fences_replay(live, node):
    from pcx_contracts.cloud import StateSnapshot
    from pcx_contracts.events import ObjectMoved
    from pcx_contracts.geometry import PointMM

    gateway = live[0].state.gateway
    identity = await gateway.store.identity("test-user-secret")
    await node.emit(ObjectMoved(object_id="cup", center_mm=PointMM(200, 200)))

    async def moved():
        return await gateway.store.cursor(node.node_id) == 1

    await eventually(moved)
    scene = (await gateway.execute(identity, "scene", {})).structured
    assert scene["objects"][0]["center_mm"] == [200, 200]
    assert scene["reported_cursor"] == 1 and scene["snapshot_cursor"] == 0
    with pytest.raises(ValueError, match="atrasado"):
        await gateway.accept_snapshot(node.node_id, StateSnapshot(cursor=0, world=node.world))
    # Replay is immutable: a reused seq with different data is rejected atomically.
    event = msgspec.structs.replace(
        node.events[0], payload=ObjectMoved(object_id="cup", center_mm=PointMM(0, 0))
    )
    with pytest.raises(ValueError, match="contenido"):
        await gateway.accept_events(node.node_id, EventBatch(cursor=1, events=[event]))
    scene = (await gateway.execute(identity, "scene", {})).structured
    assert scene["objects"][0]["center_mm"] == [200, 200]


async def test_flows_confirmation_and_action_routing(live, node):
    gateway = live[0].state.gateway
    identity = await gateway.store.identity("test-user-secret")
    flow = {"id": "demo", "initial": "start", "states": [{"id": "start", "final": True}]}
    assert (await gateway.execute(identity, "run_flow", {"flow": flow})).ok
    assert (await gateway.execute(identity, "calibrate", {"mode": "verify"})).ok
    assert (
        await gateway.execute(
            identity, "home", {"domain": "light", "service": "turn_on", "requires_confirmation": False}
        )
    ).ok
    assert [call.tool for call in node.calls] == ["run_flow", "calibrate", "home"]
    flow["states"][0]["on_enter"] = [{"type": "home_call", "domain": "lock", "service": "unlock"}]
    reply = await gateway.execute(identity, "run_flow", {"flow": flow})
    assert reply.structured["status"] == "pending_confirmation"
    assert len(node.calls) == 3  # Entire flow is held; no partial execution before approval.
    flow["initial"] = "missing"
    assert not (await gateway.execute(identity, "run_flow", {"flow": flow})).ok
