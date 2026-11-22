import asyncio
import base64
import dataclasses
import time

import httpx
import msgspec
import pytest
from conftest import eventually, fake_connected, live_server
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pcx_contracts.cloud import CloudMessage, EventBatch, Heartbeat, Hello, HelloRejected, Welcome
from pcx_contracts.events import FlowEmitted, ObjectAppeared
from pcx_contracts.geometry import PointMM
from pcx_contracts.tools import mcp_tools
from pcx_contracts.world import ObjectState
from sqlalchemy import func, select
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from pcx_gateway.store import events, tool_calls
from tools.fake_node import PNG, FakeNode, square


def data(reply):
    assert not reply.is_error, reply
    return reply.structured_content


def hello(settings, token=None, versions=(2,), stream="s1"):
    return msgspec.json.encode(
        Hello(
            node_id=settings.node_id,
            token=token or settings.node_token,
            version="t",
            contract_versions=list(versions),
            stream_id=stream,
            log_first_cursor=0,
            log_last_cursor=0,
        )
    ).decode()


async def confirmation(gateway, cid):
    async with gateway.store.engine.connect() as db:
        from pcx_gateway.store import confirmations

        return (await db.execute(select(confirmations).where(confirmations.c.id == cid))).mappings().first()


async def test_mcp_full_roundtrip(live, node, http):
    app, url, _ws, settings = live
    gateway = app.state.gateway
    async with (
        httpx.AsyncClient(headers={"Authorization": f"Bearer {settings.user_token}"}) as transport,
        streamable_http_client(f"{url}/mcp", http_client=transport) as (read, write),
        ClientSession(read, write) as client,
    ):
        initialized = await client.initialize()
        assert "table_mm" in initialized.instructions
        listed = await client.list_tools()
        assert {t.name for t in listed.tools} == {t["name"] for t in mcp_tools()} | {
            "history",
            "scenes",
            "present",
        }
        for tool, expected in zip(listed.tools, mcp_tools()):
            assert tool.input_schema == expected["inputSchema"]
        scene = data(await client.call_tool("scene", {}))
        assert scene["objects"][0]["id"] == "cup" and scene["stream_id"] == node.stream_id
        assert scene["cursor"] == 0 and "hands" not in scene
        widget = {"type": "label", "id": "hello", "text": "Hola", "anchor": {"object_id": "cup"}}
        assert (
            await client.call_tool("project", {"widgets": [widget]})
        ).is_error  # v2: request_id obligatorio
        projection = data(await client.call_tool("project", {"request_id": "p1", "widgets": [widget]}))
        assert projection["ids"] == ["hello"]
        assert data(await client.call_tool("scene", {}))["widgets"][0]["id"] == "hello"
        answer = data(
            await client.call_tool("ask", {"request_id": "a1", "question": "¿Té?", "options": ["Sí", "No"]})
        )
        assert answer["answer"] == "Sí"
        page = data(await client.call_tool("events_wait", {"cursor": 0, "timeout_s": 1}))
        assert [e["type"] for e in page["events"]] == ["flow_emitted", "ask_answered"]
        assert page["cursor"] == 2 and page["gap"] is None and page["stream_id"] == node.stream_id
        image = await client.call_tool("look", {})
        assert base64.b64decode(image.content[0].data).startswith(b"\x89PNG")
        assert data(await client.call_tool("devices", {}))["devices"][0]["kind"] == "camera"

        # Domótica: el agente no puede pedir saltarse la confirmación (campo v1 rechazado por el contrato).
        bypass = {"request_id": "h0", "domain": "lock", "service": "unlock", "requires_confirmation": False}
        assert (await client.call_tool("home", bypass)).is_error
        pending = data(
            await client.call_tool("home", {"request_id": "h1", "domain": "lock", "service": "unlock"})
        )
        assert pending["status"] == "pending" and pending["action_digest"]
        cid = pending["confirmation_id"]
        # El token MCP (el que tiene el agente) no puede confirmar; el de consola sí.
        body = {"approve": True, "action_digest": pending["action_digest"]}
        assert (await http.post(f"/api/confirmations/{cid}", json=body)).status_code == 403
        console = {"Authorization": f"Bearer {settings.console_token}"}
        bad = {**body, "action_digest": "0" * 64}
        assert (await http.post(f"/api/confirmations/{cid}", json=bad, headers=console)).status_code == 409
        assert (await http.post(f"/api/confirmations/{cid}", json=body, headers=console)).status_code == 202

        async def approved():
            row = await confirmation(gateway, cid)
            return row["status"] == "approved" and row["outcome"] == "done" and row["via"] == "console"

        await eventually(approved)
        listed = (await http.get("/api/confirmations", headers=console)).json()["confirmations"]
        assert listed[0]["id"] == cid and isinstance(listed[0]["expires_ns"], str)  # *_ns como string (TS)
        assert (await http.post(f"/api/confirmations/{cid}", json=body, headers=console)).status_code == 409
        light = data(
            await client.call_tool("home", {"request_id": "h2", "domain": "light", "service": "turn_on"})
        )
        assert light["status"] == "done"

        # Memoria: «¿dónde dejé las llaves?»
        now = time.time_ns()
        keys = ObjectState(
            id="k1",
            label="llaves de la casa",
            confidence=0.9,
            polygon_mm=square(400, 300),
            center_mm=PointMM(430, 330),
            since_ns=now,
            last_seen_ns=now,
        )
        await node.emit(ObjectAppeared(object=keys))
        upload = await http.put(
            "/api/node/objects/k1/thumbnail",
            content=base64.b64decode(PNG),
            headers={"Authorization": f"Bearer {settings.node_token}"},
        )
        assert upload.status_code == 204
        user_upload = await http.put("/api/node/objects/k1/thumbnail", content=base64.b64decode(PNG))
        assert user_upload.status_code == 401  # Solo el nodo sube recortes.

        async def remembered():
            found = data(await client.call_tool("history", {"query": "llaves", "since": "1h"}))
            return found["matches"] and found["matches"][0]["center_mm"] == [430, 330]

        await eventually(remembered)
        reply = await client.call_tool("history", {"query": "llaves"})
        assert reply.content[-1].type == "image"
        assert (await client.call_tool("history", {"since": "ayer"})).is_error
        await client.call_tool("remove", {"request_id": "r1", "ids": ["hello"]})
        assert data(await client.call_tool("scene", {}))["widgets"] == []
    rest = (await http.get("/api/history", params={"q": "llaves"})).json()
    assert rest["matches"][0]["thumbnail_url"] == "/api/objects/k1/thumbnail"
    assert isinstance(rest["events"][0]["t_pub_ns"], str)
    thumb = await http.get("/api/objects/k1/thumbnail")
    assert thumb.headers["content-type"] == "image/png"
    assert (
        await http.get("/api/objects/pcx:look/thumbnail")
    ).status_code == 404  # Fotos de look desactivadas por defecto.
    assert (await http.get("/api/nodes")).json()["nodes"][0]["connected"]
    async with gateway.store.engine.connect() as db:
        assert await db.scalar(select(func.count()).select_from(tool_calls)) >= 10
        statuses = (await db.execute(select(tool_calls.c.request_id, tool_calls.c.status))).all()
        assert ("h1", "pending_confirmation") in statuses


async def test_touch_confirmation_is_tracked(live, node):
    gateway = live[0].state.gateway
    identity = await gateway.store.identity(live[3].user_token)
    pending = await gateway.execute(
        identity, "home", {"request_id": "t1", "domain": "lock", "service": "lock"}
    )
    cid = pending.structured["confirmation_id"]
    assert (await confirmation(gateway, cid))["status"] == "pending"
    assert await node.touch_confirm(cid)

    async def touched():
        row = await confirmation(gateway, cid)
        return row["status"] == "approved" and row["via"] == "touch" and row["outcome"] == "done"

    await eventually(touched)
    page = await gateway.execute(
        identity, "events_wait", {"cursor": 0, "types": ["home_call_done"], "timeout_s": 0}
    )
    assert page.structured["events"][0]["payload"]["confirmation_id"] == cid


async def test_long_poll_pagination_and_filter(live, node):
    gateway = live[0].state.gateway
    identity = await gateway.store.identity("test-user-secret")
    args = {"cursor": 0, "types": ["flow_emitted"], "timeout_s": 1, "max_events": 1}
    pending = asyncio.create_task(gateway.execute(identity, "events_wait", args))
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
    assert page["events"] == [] and page["cursor"] == 4 and not page["has_more"]
    page = (await gateway.execute(identity, "events_wait", {"cursor": 999, "timeout_s": 0})).structured
    assert page["cursor"] == 4  # Un cursor inventado se reubica al final, no deja al agente ciego.


async def test_bad_tokens_versions_offline_and_scope(live, http):
    app, _url, ws_url, settings = live
    assert (await http.get("/health")).json()["ok"]
    assert (await http.get("/api/nodes", headers={"Authorization": "Bearer wrong"})).status_code == 401
    assert (await http.post("/mcp", headers={"Authorization": "Bearer wrong"}, json={})).status_code == 401
    console = {"Authorization": f"Bearer {settings.console_token}"}
    assert (await http.post("/mcp", headers=console, json={})).status_code == 401  # Consola no es MCP.
    assert (await http.post("/api/confirmations/x", content=b"x" * (2 * 1024 * 1024))).status_code == 413
    for token, versions, reason, code in ((None, (1,), "version", 4426), ("wrong", (2,), "auth", 4401)):
        async with connect(ws_url) as ws:
            await ws.send(hello(settings, token, versions))
            rejected = msgspec.json.decode(await ws.recv(), type=CloudMessage)
            assert isinstance(rejected, HelloRejected) and rejected.reason == reason
            with pytest.raises(ConnectionClosed) as error:
                await ws.recv()
            assert error.value.rcvd.code == code
    gateway = app.state.gateway
    identity = await gateway.store.identity(settings.user_token)
    reply = await gateway.execute(identity, "scene", {})
    assert not reply.ok and "state_snapshot" in reply.content[0].text
    other = type(settings)(
        database_url=settings.database_url,
        user_id="other-user",
        space_id="other-space",
        node_id="other-node",
        user_token="other-user-token",
        node_token="other-node-token",
    )
    await gateway.store.provision(other)
    assert (await gateway.store.identity(other.user_token)).node_id == other.node_id
    assert not await gateway.store.authenticate_node(settings.node_id, other.node_token)
    response = await http.get("/api/nodes", headers={"Authorization": "Bearer other-user-token"})
    assert [n["id"] for n in response.json()["nodes"]] == [other.node_id]


async def test_reconnect_replay_gaps_and_process_restart(live):
    app, _, ws_url, settings = live
    async with fake_connected(app, ws_url, settings) as node:
        assert node.welcome.last_cursor is None and node.welcome.contract_version == 2
        await node.emit(FlowEmitted(event="before-disconnect"))

        async def stored():
            return await app.state.gateway.store.cursor(node.node_id) == 1

        await eventually(stored)
    node.record(FlowEmitted(event="offline"))
    # Misma DB, app completamente nueva: simula un deploy de Render.
    async with live_server(settings) as (second, _, second_ws):
        store = second.state.gateway.store
        node.url = second_ws
        task = asyncio.create_task(node.run_once())
        try:
            await asyncio.wait_for(node.connected.wait(), 3)
            assert (node.welcome.last_stream_id, node.welcome.last_cursor) == (node.stream_id, 1)

            async def replayed():
                return (
                    await store.cursor(node.node_id) == 2
                    and (await store.node(node.node_id))["history_complete"]
                )

            await eventually(replayed)
            await node.send(EventBatch(stream_id=node.stream_id, cursor=2, events=node.events, replay=False))
            async with store.engine.connect() as db:
                assert await db.scalar(select(func.count()).select_from(events)) == 2  # Sin duplicados.
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        # Offline: retención local descarta 3–4 y luego el nodo pierde su log (stream nuevo).
        for name in ("lost-3", "lost-4", "kept-5"):
            node.record(FlowEmitted(event=name))
        node.forget(4)
        task = asyncio.create_task(node.run_once())
        try:
            await asyncio.wait_for(node.connected.wait(), 3)

            async def retention_gap():
                return await store.cursor(node.node_id) == 4  # hueco + evento 5

            await eventually(retention_gap)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        old_stream = node.stream_id
        node.reset_stream()
        node.record(FlowEmitted(event="new-stream-1"))
        task = asyncio.create_task(node.run_once())
        try:
            await asyncio.wait_for(node.connected.wait(), 3)
            assert node.welcome.last_stream_id == old_stream and node.welcome.last_cursor == 5
            identity = await store.identity(settings.user_token)

            async def reset_seen():
                return (await store.node(node.node_id))["stream_id"] == node.stream_id and await store.cursor(
                    node.node_id
                ) == 6

            await eventually(reset_seen)
            page = (
                await second.state.gateway.execute(identity, "events_wait", {"cursor": 2, "timeout_s": 0})
            ).structured
            assert [g["reason"] for g in page["gaps"]] == ["retention", "stream_reset"]
            assert page["gap"]["from"] == 3 and page["gap"]["to"] == 4
            assert [e["payload"]["event"] for e in page["events"]] == ["kept-5", "new-stream-1"]
            assert page["stream_id"] == node.stream_id
            assert not (await store.node(node.node_id))["history_complete"]
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_heartbeat_and_protocol_rejection(live):
    _app, _, ws_url, settings = live
    async with connect(ws_url) as ws:
        await ws.send(hello(settings))
        assert isinstance(msgspec.json.decode(await ws.recv(), type=CloudMessage), Welcome)
        assert isinstance(msgspec.json.decode(await ws.recv(), type=CloudMessage), Heartbeat)
        # Sin state_snapshot todavía: el lote se rechaza.
        await ws.send(msgspec.json.encode(EventBatch(stream_id="s1", cursor=0, events=[])).decode())
        with pytest.raises(ConnectionClosed) as error:
            while True:
                await ws.recv()
        assert error.value.rcvd.code == 4400


async def test_oversized_ws_frame_closes(live):
    _app, _, ws_url, settings = live
    async with connect(ws_url, max_size=None) as ws:
        await ws.send(hello(settings))
        await ws.recv()
        with pytest.raises(ConnectionClosed) as error:
            await ws.send("x" * (9 * 1024 * 1024))
            while True:
                await ws.recv()
        assert error.value.rcvd is None or error.value.rcvd.code in (1009, 4400)


async def test_disconnect_fails_pending_action(live, node):
    gateway = live[0].state.gateway
    identity = await gateway.store.identity("test-user-secret")
    node.ignore_tools.add("ask")
    task = asyncio.create_task(
        gateway.execute(identity, "ask", {"request_id": "q1", "question": "?", "options": ["a"]})
    )

    async def sent():
        return bool(node.calls)

    await eventually(sent)
    await node.ws.close()
    reply = await asyncio.wait_for(task, 2)
    assert not reply.ok and "desconectado" in reply.content[0].text and reply.request_id == "q1"
    # Con el nodo caído, la memoria y events_wait siguen respondiendo desde la DB.
    assert (await gateway.execute(identity, "events_wait", {"cursor": 0, "timeout_s": 0})).ok
    assert (await gateway.execute(identity, "scene", {})).structured["connected"] is False


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
        await ws.send(hello(settings).encode())
        assert isinstance(msgspec.json.decode(await ws.recv(), type=CloudMessage), Welcome)
        with pytest.raises(ConnectionClosed) as error:
            async with asyncio.timeout(3):
                while True:
                    await ws.recv()
        assert error.value.rcvd.code == 4400


async def test_mirror_uses_reported_events_and_fences_replay(live, node):
    from pcx_contracts.cloud import StateSnapshot
    from pcx_contracts.events import ObjectMoved

    gateway = live[0].state.gateway
    conn = gateway.connections[node.node_id]
    identity = await gateway.store.identity("test-user-secret")
    await node.emit(ObjectMoved(object_id="cup", center_mm=PointMM(200, 200)))

    async def moved():
        return await gateway.store.cursor(node.node_id) == 1

    await eventually(moved)
    scene = (await gateway.execute(identity, "scene", {})).structured
    assert scene["objects"][0]["center_mm"] == [200, 200]
    assert scene["reported_cursor"] == 1 and scene["snapshot_cursor"] == 0
    with pytest.raises(ValueError, match="atrasado"):
        await gateway.accept_snapshot(
            conn, node.node_id, StateSnapshot(stream_id=node.stream_id, cursor=0, world=node.world)
        )
    event = msgspec.structs.replace(
        node.events[0], payload=ObjectMoved(object_id="cup", center_mm=PointMM(0, 0))
    )
    with pytest.raises(ValueError, match="contenido"):
        await gateway.accept_events(
            conn, node.node_id, EventBatch(stream_id=node.stream_id, cursor=1, events=[event])
        )
    scene = (await gateway.execute(identity, "scene", {})).structured
    assert scene["objects"][0]["center_mm"] == [200, 200]


async def test_flows_and_home_routing(live, node):
    gateway = live[0].state.gateway
    identity = await gateway.store.identity("test-user-secret")
    flow = {"id": "demo", "initial": "start", "states": [{"id": "start", "final": True}]}
    assert (await gateway.execute(identity, "run_flow", {"request_id": "f1", "flow": flow})).ok
    assert (await gateway.execute(identity, "calibrate", {"request_id": "c1", "mode": "verify"})).ok
    denied = await gateway.execute(
        identity, "home", {"request_id": "h1", "domain": "alarm", "service": "disarm"}
    )
    assert denied.structured["status"] == "denied"
    # Un flujo con home_call se envía al nodo: su HomePolicy decide (el gateway no puede aprobar nada).
    flow["states"][0]["on_enter"] = [{"type": "home_call", "domain": "lock", "service": "unlock"}]
    assert (await gateway.execute(identity, "run_flow", {"request_id": "f2", "flow": flow})).ok
    flow["states"][0]["on_enter"][0]["requires_confirmation"] = False
    assert not (await gateway.execute(identity, "run_flow", {"request_id": "f3", "flow": flow})).ok
    assert [call.tool for call in node.calls] == ["run_flow", "calibrate", "home", "run_flow"]
    flow["initial"] = "missing"
    assert not (await gateway.execute(identity, "run_flow", {"request_id": "f4", "flow": flow})).ok


async def test_fake_node_class_is_v2():
    fake = FakeNode(token="x")
    assert fake.contract_versions == [2] and fake.stream_id


async def test_token_rotation_on_redeploy(live):
    """Cambiar los tokens en el env y redesplegar: arranca, el viejo da 401 y el nuevo 200."""
    _app, _, _ws_url, old = live
    rotated = dataclasses.replace(
        old, user_token="rotated-user", node_token="rotated-node", console_token="rotated-console"
    )
    async with live_server(rotated) as (_second, url, second_ws):
        async with httpx.AsyncClient(base_url=url) as client:
            for token, status in (
                (old.user_token, 401),
                (old.console_token, 401),
                (rotated.user_token, 200),
                (rotated.console_token, 200),
            ):
                response = await client.get("/api/nodes", headers={"Authorization": f"Bearer {token}"})
                assert response.status_code == status, token
        async with connect(second_ws) as ws:
            await ws.send(hello(rotated, old.node_token))
            rejected = msgspec.json.decode(await ws.recv(), type=CloudMessage)
            assert isinstance(rejected, HelloRejected) and rejected.reason == "auth"
        async with connect(second_ws) as ws:
            await ws.send(hello(rotated))
            assert isinstance(msgspec.json.decode(await ws.recv(), type=CloudMessage), Welcome)


async def test_bus_failure_keeps_node_socket(live, node, caplog):
    """Si Key Value falla, el evento queda en la DB, se registra y el WSS del nodo sigue vivo."""
    app, _, _, settings = live
    gateway = app.state.gateway

    class BrokenBus:
        async def publish(self, node_id):
            raise ConnectionError("Key Value caído")

    gateway.bus = BrokenBus()
    conn = gateway.connections[settings.node_id]
    await node.emit(FlowEmitted(event="sin-bus"))

    async def stored():
        return await gateway.store.cursor(settings.node_id) == 1

    await eventually(stored)
    await node.emit(FlowEmitted(event="sigue-conectado"))

    async def stored_again():
        return await gateway.store.cursor(settings.node_id) == 2

    await eventually(stored_again)
    assert gateway.connections.get(settings.node_id) is conn and conn.ready
    assert "bus.publish falló" in caplog.text
