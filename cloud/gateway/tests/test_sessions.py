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
        assert page.status_code == 200 and "Copiar para tu agente" in page.text
        mesa = await http.get("/mesa")
        assert mesa.status_code == 200 and "WUI · mesa" in mesa.text
        old = await http.get("/?motor=ws://127.0.0.1:9230&sim=1")  # la consola vivía en /
        assert old.status_code == 307 and old.headers["location"] == "/mesa?motor=ws://127.0.0.1:9230&sim=1"
        agent = await http.get("/agente")
        assert agent.headers["content-type"].startswith("text/plain")
        assert f"{url}/api/sessions" in agent.text and "PENDIENTE" in agent.text and "/mesa#s=" in agent.text
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
            # Quien presenta pide solo estado (video: false): recibe las láminas, nunca cuadros.
            assert (await http.get(f"/presentar/{view}")).status_code == 200
            async with websockets.connect(view_url) as presenter:
                await presenter.send(json.dumps({"token": view, "video": False}))
                assert json.loads(await asyncio.wait_for(presenter.recv(), 3))["kind"] == "status"
                await asyncio.sleep(0.1)
                await fake.ws.send(jpeg)
                await fake.ws.send('{"type":"view","kind":"deck","slides":[{"title":"Uno"}],"index":0}')
                assert json.loads(await asyncio.wait_for(presenter.recv(), 3))["slides"] == [{"title": "Uno"}]
                # Un nodo que no es navegador ignora el pedido de avanzar.
                await presenter.send(json.dumps({"type": "slide", "to": "next"}))
                # El otro, que sí quiere video, recibe las dos cosas (el estado sale antes que el cuadro).
                kinds = []
                while isinstance(got := await asyncio.wait_for(viewer.recv(), 3), str):
                    kinds.append(json.loads(got)["kind"])
                assert got == jpeg and "deck" in kinds
            # El nodo sigue hablando el contrato normal después de mandar cuadros.
            listed = await http.get("/api/nodes", headers=user)
            assert listed.json()["nodes"][0]["connected"] is True


async def test_agent_page_points_to_the_installer_when_published(tmp_path):
    cfg = settings(tmp_path, install_url="https://ejemplo.test/install.sh")
    async with live_server(cfg) as (_app, url, _ws), httpx.AsyncClient(base_url=url) as http:
        text = (await http.get("/agente")).text
        assert 'curl -fsSL "https://ejemplo.test/install.sh" | sh -s -- --yes --skills --start' in text
        assert "PENDIENTE" not in text and "motor_token" in text


async def test_phone_camera_link_only_opens_the_cam_socket(tmp_path):
    cfg = settings(tmp_path, open_sessions=True)
    async with live_server(cfg) as (_app, url, ws), httpx.AsyncClient(base_url=url) as http:
        ses = (await http.post("/api/sessions")).json()
        user = {"Authorization": f"Bearer {ses['user_token']}"}
        cam = (await http.post("/api/cam-token", headers=user)).json()["cam_token"]
        phone = {"Authorization": f"Bearer {cam}"}
        # El token del celular no abre nada más: ni la API, ni el MCP, ni mirar, ni pedir otros tokens.
        assert (await http.get("/api/nodes", headers=phone)).status_code == 401
        assert (await http.post("/api/cam-token", headers=phone)).status_code == 401
        assert (await http.post("/mcp", headers=phone, json={})).status_code == 401
        assert (await http.get(f"/cam/{cam}")).status_code == 200
        async with websockets.connect(ws.replace("/node", "/view")) as bad:
            await bad.send(json.dumps({"token": cam}))
            await asyncio.wait_for(bad.wait_closed(), 3)
            assert bad.close_code == 4401
        cam_url = ws.replace("/node", "/cam")
        async with websockets.connect(cam_url) as bad:
            await bad.send(json.dumps({"token": ses["user_token"]}))
            await asyncio.wait_for(bad.wait_closed(), 3)
            assert bad.close_code == 4401
        # Cada celular toma el primer puesto libre (máximo 4); sin la mesa abierta, los cuadros se descartan.
        async with websockets.connect(cam_url) as one, websockets.connect(cam_url) as two:
            await one.send(json.dumps({"token": cam, "label": "iPhone"}))
            assert json.loads(await asyncio.wait_for(one.recv(), 3)) == {"slot": 1, "mesa": False}
            await two.send(json.dumps({"token": cam}))
            assert json.loads(await asyncio.wait_for(two.recv(), 3))["slot"] == 2
            await one.send(b"\xff\xd8" + b"x" * 100)
            await one.send("no es un cuadro")
            assert (await http.get("/health")).json()["ok"] is True
