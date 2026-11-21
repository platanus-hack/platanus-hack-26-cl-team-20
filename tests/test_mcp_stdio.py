"""Arranca el MCP del playground por stdio (como lo hace Claude Code) y lista sus tools."""

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent


PORT = 8781      # puerto de prueba: no toca el runtime real (8795 es el dashboard)


def _session_run(fn, tmp_state: Path):
    import os
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    async def go():
        env = {**os.environ, "PLAYGROUND_URL": f"http://127.0.0.1:{PORT}", "PLAYGROUND_STATE": str(tmp_state)}
        params = StdioServerParameters(command=sys.executable, args=["-m", "playground", "mcp"], cwd=str(ROOT), env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await fn(session)
    return asyncio.run(asyncio.wait_for(go(), timeout=90))


def _text(res) -> str:
    return "".join(getattr(c, "text", "") for c in res.content)


def test_mcp_server_starts_runtime_and_lists_tools(tmp_path):
    async def fn(session):
        tools = [t.name for t in (await session.list_tools()).tools]
        st = await session.call_tool("pg_state", {})            # runtime apagado: lo levanta solo
        songs = await session.call_tool("pg_pianohero", {"action": "songs"})
        assert '"oda"' in _text(songs) and '"Dificil"' in _text(songs)
        status = await session.call_tool("pg_runtime", {"action": "status"})
        stop = await session.call_tool("pg_runtime", {"action": "stop"})
        after = await session.call_tool("pg_runtime", {"action": "status"})
        return tools, st, status, stop, after
    tools, st, status, stop, after = _session_run(fn, tmp_path)
    for n in ("pg_state", "pg_spawn", "pg_request_feature", "pg_requests", "pg_list_effects", "pg_cameras", "pg_runtime",
              "pg_audio", "pg_pianohero"):
        assert n in tools, tools
    assert not getattr(st, "is_error", False) and '"objects"' in _text(st) and "error" not in _text(st)[:40]
    assert '"running"' in _text(status) and f"playground-{PORT}.log" in _text(status)
    assert '"stopped"' in _text(stop) and '"stopped"' in _text(after)
    assert (tmp_path / f"playground-{PORT}.log").exists()
