#!/usr/bin/env bash
# Smoke del gateway. Dos modos (PCX_SMOKE_MODE):
#   readonly (por defecto): /health, auth, MCP tools/list, /api/nodes y handshake WSS hello→welcome
#     sin snapshot ni eventos. No escribe objetos, eventos ni historial; solo actualiza last_seen del nodo.
#     Si el nodo real está conectado, omite el handshake para no desplazarlo. Seguro contra producción.
#   full: nodo sintético con snapshot (objeto «cup») + round trip `look`. Persiste estado sintético,
#     por eso solo se permite contra un gateway local (127.0.0.1, localhost o ::1).
set -euo pipefail
: "${PCX_GATEWAY_URL:?Set PCX_GATEWAY_URL (https://HOST or http://127.0.0.1:9215)}"
: "${PCX_USER_TOKEN:?Set PCX_USER_TOKEN}"
: "${PCX_NODE_TOKEN:?Set PCX_NODE_TOKEN}"
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root/cloud/gateway"
exec uv run --frozen python - <<'PY'
import asyncio
import os
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx
import msgspec
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pcx_contracts.cloud import CloudMessage, Hello, HelloRejected, Welcome
from pcx_contracts.envelope import CONTRACT_VERSION
from tools.fake_node import FakeNode
from websockets.asyncio.client import connect

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def hello(node_id, token):
    return msgspec.json.encode(
        Hello(
            node_id=node_id,
            token=token,
            version="smoke",
            contract_versions=[CONTRACT_VERSION],
            stream_id=f"smoke-{uuid4()}",
            log_first_cursor=0,
            log_last_cursor=0,
        )
    ).decode()


async def handshake(ws_url, node_id, token):
    async with asyncio.timeout(10), connect(ws_url) as ws:
        await ws.send(hello(node_id, token))
        return msgspec.json.decode(await ws.recv(), type=CloudMessage)


async def main():
    url = os.environ["PCX_GATEWAY_URL"].rstrip("/")
    parsed = urlsplit(url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path):
        raise ValueError("PCX_GATEWAY_URL must be an HTTP(S) origin without credentials/path/query")
    mode = os.getenv("PCX_SMOKE_MODE", "readonly")
    if mode not in {"readonly", "full"}:
        raise ValueError("PCX_SMOKE_MODE must be readonly or full")
    if mode == "full" and parsed.hostname not in LOCAL_HOSTS:
        raise ValueError("PCX_SMOKE_MODE=full writes synthetic state; only allowed against a local gateway")
    ws_url = urlunsplit(("wss" if parsed.scheme == "https" else "ws", parsed.netloc, "/node", "", ""))
    node_id = os.getenv("PCX_NODE_ID", "demo-node")
    headers = {"Authorization": f"Bearer {os.environ['PCX_USER_TOKEN']}"}
    print(f"MODE {mode}", flush=True)
    async with httpx.AsyncClient(headers=headers, timeout=10) as http:
        health = await http.get(f"{url}/health")
        health.raise_for_status()
        if health.json().get("ok") is not True:
            raise RuntimeError("Unhealthy gateway")
        print("PASS /health", flush=True)
        bad = await http.get(f"{url}/api/nodes", headers={"Authorization": "Bearer smoke-invalid"})
        if bad.status_code != 401:
            raise RuntimeError(f"Invalid bearer should be 401, got {bad.status_code}")
        print("PASS invalid bearer -> 401", flush=True)

        async def node_status():
            response = await http.get(f"{url}/api/nodes")
            response.raise_for_status()
            return next(n for n in response.json()["nodes"] if n["id"] == node_id)

        async with (
            streamable_http_client(f"{url}/mcp", http_client=http) as (read, write),
            ClientSession(read, write) as client,
        ):
            await client.initialize()
            listed = await client.list_tools()
            names = {tool.name for tool in listed.tools}
            if not {"scene", "look", "history"} <= names:
                raise RuntimeError("Missing expected MCP tools")
            print(f"PASS MCP tools/list ({len(names)} tools)", flush=True)

            rejected = await handshake(ws_url, node_id, "smoke-invalid")
            if not isinstance(rejected, HelloRejected) or rejected.reason != "auth":
                raise RuntimeError("Invalid node token should get hello_rejected{auth}")
            print("PASS WSS /node rejects invalid node token", flush=True)

            if mode == "readonly":
                if (await node_status())["connected"]:
                    print("SKIP WSS hello/welcome: the real node is connected (not displacing it)", flush=True)
                else:
                    welcome = await handshake(ws_url, node_id, os.environ["PCX_NODE_TOKEN"])
                    if not isinstance(welcome, Welcome):
                        raise RuntimeError(f"Expected welcome, got {type(welcome).__name__}")
                    print("PASS WSS hello/welcome with PCX_NODE_TOKEN (no snapshot, no events)", flush=True)
                    async with asyncio.timeout(10):
                        while (await node_status())["connected"]:
                            await asyncio.sleep(0.1)
                print("SMOKE OK (readonly)", flush=True)
                return

            if (await node_status())["connected"]:
                raise RuntimeError("Node already connected; stop it before running the synthetic smoke")
            fake = FakeNode(ws_url, node_id, os.environ["PCX_NODE_TOKEN"])
            task = asyncio.create_task(fake.run_once())
            try:
                async with asyncio.timeout(10):
                    while not fake.connected.is_set() or not (await node_status())["connected"]:
                        if task.done():
                            task.result()
                            raise RuntimeError("Fake node disconnected before handshake")
                        await asyncio.sleep(0.1)
                    while True:
                        scene = await client.call_tool("scene", {})
                        state = scene.structured_content or {}
                        if (not scene.is_error and state.get("stream_id") == fake.stream_id
                                and any(obj["id"] == "cup" for obj in state.get("objects", []))):
                            break
                        await asyncio.sleep(0.1)
                print("PASS fake node hello/welcome + snapshot + /api/nodes connected", flush=True)
                result = await client.call_tool("look", {})
                if result.is_error or not (result.structured_content or {}).get("synthetic"):
                    raise RuntimeError("Synthetic tool round trip failed")
                print("PASS MCP look round trip via fake node", flush=True)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            async with asyncio.timeout(10):
                while (await node_status())["connected"]:
                    await asyncio.sleep(0.1)
            print("PASS fake node disconnected (no background process)", flush=True)
    print("SMOKE OK (full)", flush=True)


async def bounded():
    async with asyncio.timeout(45):
        await main()


asyncio.run(bounded())
PY
