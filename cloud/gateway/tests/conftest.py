import asyncio
import os
import socket
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pcx_gateway.app import create_app
from pcx_gateway.config import Settings
from pcx_gateway.store import Store, metadata
from tools.fake_node import FakeNode


async def eventually(check, timeout=5):
    async with asyncio.timeout(timeout):
        while not await check():
            await asyncio.sleep(0.01)


@asynccontextmanager
async def live_server(settings):
    app = create_app(settings)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    assert not 8787 <= port <= 8799
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="warning", lifespan="on", timeout_graceful_shutdown=2)
    )
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:

        async def started():
            if task.done():
                task.result()
            return server.started

        await eventually(started)
        yield app, f"http://127.0.0.1:{port}", f"ws://127.0.0.1:{port}/node"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 8)
        sock.close()


@pytest.fixture
async def live(tmp_path):
    test_url = os.getenv("PCX_TEST_DATABASE_URL")
    if test_url:
        # Explicitly opt in with a disposable test database only.
        store = Store(test_url)
        try:
            async with store.engine.begin() as db:
                await db.run_sync(metadata.drop_all)
        finally:
            await store.close()
    settings = Settings(
        database_url=test_url or f"sqlite+aiosqlite:///{tmp_path}/gateway.db",
        user_token="test-user-secret",
        node_token="test-node-secret",
        console_token="test-console-secret",
        heartbeat_s=0.1,
        node_idle_s=2,
    )
    async with live_server(settings) as server:
        yield (*server, settings)


@asynccontextmanager
async def fake_connected(app, ws_url, settings):
    fake = FakeNode(ws_url, settings.node_id, settings.node_token)
    task = asyncio.create_task(fake.run_once())
    try:
        await asyncio.wait_for(fake.connected.wait(), 3)

        async def ready():
            conn = app.state.gateway.connections.get(settings.node_id)
            return conn is not None and conn.ready

        await eventually(ready)
        yield fake
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.fixture
async def node(live):
    app, _http_url, ws_url, settings = live
    async with fake_connected(app, ws_url, settings) as fake:
        yield fake


@pytest.fixture
async def http(live):
    async with httpx.AsyncClient(
        base_url=live[1], headers={"Authorization": "Bearer test-user-secret"}
    ) as client:
        yield client
