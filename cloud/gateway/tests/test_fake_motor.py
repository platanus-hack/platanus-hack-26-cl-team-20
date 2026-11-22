"""El motor falso habla el protocolo motor v0 (lo usan la consola en local y test_console_js.py)."""

import asyncio
import base64
import json
import socket
import time
from contextlib import asynccontextmanager

import httpx
import pytest
import uvicorn
from conftest import eventually
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from tools.fake_motor import FAKE_JPEG, MotorConfig, create_app

TOKEN = "motor-secreto"
ORIGIN = "http://localhost:9215"
CALIB = {
    "type": "calib",
    "camera_px": [1280, 720],
    "matrix_px_to_mm": [1, 0, 0, 0, 1, 0, 0, 0, 1],
    "lens": None,
    "surface_mm": [1000, 562],
}


@asynccontextmanager
async def fake_motor(**kw):
    cfg = MotorConfig(token=TOKEN, cycle_s=0, log=lambda *_: None, **kw)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(16)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(cfg), log_level="warning", lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:

        async def started():
            if task.done():
                task.result()
            return server.started

        await eventually(started)
        yield cfg, f"http://127.0.0.1:{port}", f"ws://127.0.0.1:{port}/v1/ws"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 8)
        sock.close()


async def recv(ws, kind, timeout=3):
    """Siguiente mensaje de tipo `kind` (salta los demás, p. ej. status)."""
    async with asyncio.timeout(timeout):
        while True:
            msg = json.loads(await ws.recv())
            if msg["type"] == kind:
                return msg


async def hello(ws, token=TOKEN):
    await ws.send(json.dumps({"type": "hello", "v": 0, "token": token, "session_id": "s1", "app": "test"}))


async def test_health_cors_y_red_local():
    async with fake_motor() as (_, base, _ws), httpx.AsyncClient(base_url=base) as http:
        ok = await http.get("/v1/health", headers={"Origin": "https://pcx-gateway.onrender.com"})
        assert ok.status_code == 200 and ok.json()["ok"] is True
        assert ok.headers["access-control-allow-origin"] == "https://pcx-gateway.onrender.com"
        assert ok.json()["camera"]["px"] == [160, 90]
        pre = await http.options(
            "/v1/health",
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Private-Network": "true",
            },
        )
        assert pre.status_code == 204
        assert pre.headers["access-control-allow-private-network"] == "true"
        assert (await http.get("/v1/health", headers={"Origin": "https://otro.example"})).status_code == 403


async def test_ws_rechaza_origen_y_token():
    async with fake_motor() as (_, _base, url):
        with pytest.raises(InvalidStatus) as bad_origin:
            async with connect(url, origin="https://otro.example"):
                pass
        assert bad_origin.value.response.status_code == 403
        async with connect(url, origin=ORIGIN) as ws:
            await hello(ws, token="otro")
            assert (await recv(ws, "error"))["code"] == "auth"
            with pytest.raises(ConnectionClosed) as closed:
                await ws.recv()
            assert closed.value.rcvd.code == 4401


async def test_ciclo_cuando_se_van_las_manos():
    async with fake_motor() as (cfg, _base, url), connect(url, origin=ORIGIN) as ws:
        await hello(ws)
        welcome = await recv(ws, "welcome")
        assert welcome["v"] == 0 and welcome["camera"]["px"] == [160, 90] and welcome["needs_frames"] is False
        assert welcome["scenes"] == ["lab", "solar", "desk"]
        assert [c["role"] for c in welcome["cameras"]] == ["cenital", "lateral"]
        assert (await recv(ws, "objects")) == {"type": "objects", "rev": 0, "upsert": [], "remove": []}
        await ws.send(json.dumps(CALIB))
        await ws.send(json.dumps({"type": "calib", "camera_px": [1, 2]}))
        assert (await recv(ws, "error"))["code"] == "bad_calib"
        await ws.send(json.dumps({"type": "hands", "present": False, "t_ns": time.time_ns()}))
        assert (await recv(ws, "status"))["state"] == "calm"
        blank = await recv(ws, "blank")
        assert blank["max_ms"] > 0
        await ws.send(json.dumps({"type": "blanked", "id": blank["id"], "t_ns": time.time_ns()}))
        assert (await recv(ws, "unblank"))["id"] == blank["id"]
        await ws.send(json.dumps({"type": "unblanked", "id": blank["id"]}))
        objects = await recv(ws, "objects")
        assert objects["rev"] == 1 and objects["remove"] == []
        assert {o["id"] for o in objects["upsert"]} == {"taza", "libro"}
        taza = next(o for o in objects["upsert"] if o["id"] == "taza")
        assert taza["center_mm"] == [300, 252.9] and 3 <= len(taza["polygon_mm"]) <= 64
        idle = await recv(ws, "status")
        assert idle["state"] == "idle" and idle["last_cycle_ms"] > 0 and "blank" in idle["stages_ms"]
        assert cfg.sessions[0].calib == CALIB
        # Segundo ciclo (perceive): solo lo que cambió.
        await ws.send(json.dumps({"type": "perceive"}))
        blank = await recv(ws, "blank")
        await ws.send(json.dumps({"type": "blanked", "id": blank["id"], "t_ns": time.time_ns()}))
        await recv(ws, "unblank")
        moved = await recv(ws, "objects")
        assert moved["rev"] == 2 and [o["id"] for o in moved["upsert"]] == ["taza"]
    # v0.1: al reconectar, todos los objetos vigentes justo después de welcome.
    async with fake_motor() as (cfg, _base, url):
        cfg.objects.update({"taza": {"id": "taza"}, "libro": {"id": "libro"}})
        cfg.rev = 7
        async with connect(url, origin=ORIGIN) as ws:
            await hello(ws)
            await recv(ws, "welcome")
            full = await recv(ws, "objects")
            assert full["rev"] == 7 and {o["id"] for o in full["upsert"]} == {"taza", "libro"}


async def test_escena_y_eventos():
    async with fake_motor() as (cfg, _base, url), connect(url, origin=ORIGIN) as ws:
        await hello(ws)
        await recv(ws, "welcome")
        await ws.send(json.dumps({"type": "scene", "name": "otra"}))
        assert (await recv(ws, "error"))["code"] == "unknown_scene"
        await ws.send(json.dumps({"type": "scene", "name": "lab"}))
        event = await recv(ws, "event")
        assert event["scene"] == "lab" and event["event"] == "nivel" and event["data"]["objetivo_ml"] == 200
        await ws.send(json.dumps({"type": "scene", "name": None}))
        await eventually(lambda: _async(cfg.sessions[0].scene is None))


async def _async(value):
    return value


async def test_sin_blanked_no_hay_ciclo():
    async with fake_motor(blank_ms=200) as (_, _base, url), connect(url, origin=ORIGIN) as ws:
        await hello(ws)
        await recv(ws, "welcome")
        await ws.send(json.dumps({"type": "perceive"}))
        blank = await recv(ws, "blank")
        assert (await recv(ws, "unblank"))["id"] == blank["id"]  # por las dudas, igual vuelve a la escena
        assert (await recv(ws, "error"))["code"] == "blank_timeout"


async def test_look_y_motor_sin_camara():
    async with fake_motor() as (_, _base, url), connect(url, origin=ORIGIN) as ws:
        await hello(ws)
        await recv(ws, "welcome")
        await ws.send(json.dumps({"type": "look", "id": "l1", "clean": False}))
        frame = await recv(ws, "look_frame")
        assert frame["id"] == "l1" and (frame["w"], frame["h"]) == (160, 90)
        assert base64.b64decode(frame["jpeg_b64"]) == FAKE_JPEG
    async with fake_motor(camera=False) as (_, _base, url), connect(url, origin=ORIGIN) as ws:
        await hello(ws)
        welcome = await recv(ws, "welcome")
        assert welcome["camera"] is None and welcome["needs_frames"] is True
        assert [c["role"] for c in welcome["cameras"]] == ["lateral"]
        await recv(ws, "objects")  # los vigentes (ninguno todavía)
        await ws.send(json.dumps({"type": "perceive"}))
        blank = await recv(ws, "blank")
        blanked_ns = time.time_ns()
        await ws.send(json.dumps({"type": "blanked", "id": blank["id"], "t_ns": blanked_ns}))
        for _ in range(3):
            need = await recv(ws, "need_frame")
            jpeg_b64 = base64.b64encode(FAKE_JPEG).decode()
            reply = {
                "type": "frame",
                "id": need["id"],
                "jpeg_b64": jpeg_b64,
                "w": 160,
                "h": 90,
                "t_ns": blanked_ns + 1,
            }
            await ws.send(json.dumps(reply))
        await recv(ws, "unblank")
        assert {o["id"] for o in (await recv(ws, "objects"))["upsert"]} == {"taza", "libro"}
