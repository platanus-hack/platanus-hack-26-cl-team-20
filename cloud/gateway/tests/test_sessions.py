"""Consola web: sesiones por visita (usuario + espacio + nodo propios) y archivos estáticos."""

from dataclasses import replace

import httpx
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
