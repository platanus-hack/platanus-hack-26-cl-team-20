"""Consola web: sesiones por visita (usuario + espacio + nodo propios) y archivos estáticos."""

import asyncio
import json
from dataclasses import replace

import httpx
import websockets
from conftest import fake_connected, live_server

from pcx_gateway.config import Settings


def settings(tmp_path, **extra):
    return Settings(database_url=f"sqlite+aiosqlite:///{tmp_path}/gateway.db", heartbeat_s=0.1, **extra)


async def test_sessions_closed_by_default(tmp_path):
    async with live_server(settings(tmp_path)) as (_app, url, _ws), httpx.AsyncClient(base_url=url) as http:
        assert (await http.post("/api/sessions")).status_code == 403


async def test_session_is_an_isolated_node(tmp_path):
    cfg = settings(tmp_path, open_sessions=True, sessions_per_hour=2)
    async with live_server(cfg) as (app, url, ws), httpx.AsyncClient(base_url=url) as http:
        a, b = [(await http.post("/api/sessions")).json() for _ in range(2)]
        assert (await http.post("/api/sessions")).status_code == 429
        assert a["node_id"] != b["node_id"] and a["user_token"] != b["user_token"]
        node_cfg = replace(cfg, node_id=a["node_id"], node_token=a["node_token"])
        async with fake_connected(app, ws, node_cfg):
            mine = await http.get("/api/nodes", headers={"Authorization": f"Bearer {a['user_token']}"})
            other = await http.get("/api/nodes", headers={"Authorization": f"Bearer {b['user_token']}"})
        assert mine.json()["nodes"][0] | {"id": a["node_id"], "connected": True} == mine.json()["nodes"][0]
        assert other.json()["nodes"][0] | {"id": b["node_id"], "connected": False} == other.json()["nodes"][0]
        # El token de nodo no sirve como token de usuario.
        bad = await http.get("/api/nodes", headers={"Authorization": f"Bearer {a['node_token']}"})
        assert bad.status_code == 401


async def test_console_is_served(tmp_path):
    async with live_server(settings(tmp_path)) as (_app, url, _ws), httpx.AsyncClient(base_url=url) as http:
        page = await http.get("/")
        assert page.status_code == 200 and "pcx · consola" in page.text
        assert (await http.get("/projector.html")).status_code == 200
        assert (await http.get("/health")).json()["ok"] is True


async def test_view_link_is_read_only_and_relays(tmp_path):
    cfg = settings(tmp_path, open_sessions=True)
    async with live_server(cfg) as (app, url, ws), httpx.AsyncClient(base_url=url) as http:
        ses = (await http.post("/api/sessions")).json()
        user = {"Authorization": f"Bearer {ses['user_token']}"}
        view = (await http.post("/api/view-token", headers=user)).json()["view_token"]
        watcher = {"Authorization": f"Bearer {view}"}
        # El token de mirar no abre nada más.
        assert (await http.get("/api/nodes", headers=watcher)).status_code == 401
        assert (await http.post("/api/view-token", headers=watcher)).status_code == 401
        assert (await http.post("/mcp", headers=watcher, json={})).status_code == 401
        assert (await http.get(f"/ver/{view}")).status_code == 200
        view_url = ws.replace("/node", "/view")
        async with websockets.connect(view_url) as bad:
            await bad.send(json.dumps({"token": ses["user_token"]}))
            await asyncio.wait_for(bad.wait_closed(), 3)
            assert bad.close_code == 4401
        node_cfg = replace(cfg, node_id=ses["node_id"], node_token=ses["node_token"])
        async with fake_connected(app, ws, node_cfg) as fake, websockets.connect(view_url) as viewer:
            await viewer.send(json.dumps({"token": view}))
            assert json.loads(await asyncio.wait_for(viewer.recv(), 3)) == {
                "type": "view",
                "kind": "status",
                "connected": True,
            }
            await asyncio.sleep(0.1)
            await fake.ws.send('{"type":"view","kind":"world","world":{"rev":7}}')
            assert json.loads(await asyncio.wait_for(viewer.recv(), 3))["world"] == {"rev": 7}
            jpeg = b"\xff\xd8" + b"x" * 100
            await fake.ws.send(jpeg)
            assert await asyncio.wait_for(viewer.recv(), 3) == jpeg
            # El nodo sigue hablando el contrato normal después de mandar cuadros.
            listed = await http.get("/api/nodes", headers=user)
            assert listed.json()["nodes"][0]["connected"] is True
