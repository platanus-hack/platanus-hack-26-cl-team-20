#!/usr/bin/env bash
# Run before connecting a real node: the fake persists a synthetic snapshot.
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

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from tools.fake_node import FakeNode


async def main():
    url = os.environ["PCX_GATEWAY_URL"].rstrip("/")
    parsed = urlsplit(url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path):
        raise ValueError("PCX_GATEWAY_URL must be an HTTP(S) origin without credentials/path/query")
    ws_url = urlunsplit(("wss" if parsed.scheme == "https" else "ws", parsed.netloc, "/node", "", ""))
    node_id = os.getenv("PCX_NODE_ID", "demo-node")
    headers = {"Authorization": f"Bearer {os.environ['PCX_USER_TOKEN']}"}
    async with httpx.AsyncClient(headers=headers, timeout=10) as http:
        health = await http.get(f"{url}/health")
        health.raise_for_status()
        if health.json().get("ok") is not True:
            raise RuntimeError("Unhealthy gateway")
        print("PASS /health", flush=True)

        async def node_status():
            response = await http.get(f"{url}/api/nodes")
            response.raise_for_status()
            return next(n for n in response.json()["nodes"] if n["id"] == node_id)

        if (await node_status())["connected"]:
            raise RuntimeError("Node already connected; stop it before running the synthetic smoke")
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
    print("SMOKE OK", flush=True)


async def bounded():
    async with asyncio.timeout(45):
        await main()


asyncio.run(bounded())
PY
