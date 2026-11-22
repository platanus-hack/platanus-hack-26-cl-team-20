"""Cliente MCP asíncrono de la mesa (Streamable HTTP sin estado, Bearer de la sesión).

El token de la sesión vive en el servidor local: la página nunca lo ve (el gateway además no tiene CORS).
"""

from __future__ import annotations

import itertools
import json
import os
import re

import httpx


class ErrorMesa(RuntimeError):
    pass


class Mesa:
    def __init__(self, gateway: str, token: str, http: httpx.AsyncClient | None = None):
        if not token:
            raise ErrorMesa("falta el token de la sesión de la mesa (PCX_USER_TOKEN o `sdvoz sesion`)")
        self.url = gateway.rstrip("/") + "/mcp"
        self._headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                         "Accept": "application/json, text/event-stream"}
        self.http = http or httpx.AsyncClient(timeout=70)
        self._ids = itertools.count(1)
        self._req = itertools.count(1)

    async def rpc(self, method: str, params: dict, timeout: float = 30) -> dict:
        body = {"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params}
        try:
            r = await self.http.post(self.url, json=body, headers=self._headers, timeout=timeout)
        except httpx.HTTPError as e:
            raise ErrorMesa(f"sin conexión con la mesa ({type(e).__name__})") from e
        if r.status_code >= 400:
            raise ErrorMesa(f"{method}: HTTP {r.status_code} {r.text[:200]}")
        m = re.search(r"\{.*\}", r.text, re.S)  # JSON directo o dentro de un evento SSE
        if not m:
            raise ErrorMesa(f"{method}: respuesta vacía")
        data = json.loads(m.group(0))
        if "error" in data:
            raise ErrorMesa(f"{method}: {data['error'].get('message') if isinstance(data['error'], dict) else data['error']}")
        return data["result"]

    async def tool(self, name: str, args: dict | None = None, timeout: float = 30) -> dict:
        """Llama una tool del gateway. Devuelve el resultado estructurado (sin imágenes)."""
        args = dict(args or {})
        if name in ("project", "remove", "look", "ask", "calibrate", "run_flow"):
            args.setdefault("request_id", f"voz-{os.getpid()}-{next(self._req)}")
        res = await self.rpc("tools/call", {"name": name, "arguments": args}, timeout=timeout)
        data: dict = {}
        if isinstance(res.get("structuredContent"), dict):
            data = res["structuredContent"]
        else:
            for c in res.get("content", []):
                if c.get("type") == "text":
                    try:
                        data = json.loads(c["text"])
                    except (json.JSONDecodeError, TypeError):
                        data = {"texto": c.get("text")}
        if res.get("isError"):
            msg = data.get("error") or data.get("message") or data.get("texto") or data
            raise ErrorMesa(f"{name}: {msg}")
        return data

    async def look_crudo(self, args: dict, timeout: float = 25) -> dict:
        """`look` con su contenido tal cual (texto + imagen base64), para quien necesita la foto."""
        args = {**args, "request_id": f"voz-{os.getpid()}-{next(self._req)}"}
        return await self.rpc("tools/call", {"name": "look", "arguments": args}, timeout=timeout)

    async def tools(self) -> list[str]:
        res = await self.rpc("tools/list", {})
        return [t["name"] for t in res.get("tools", [])]

    async def aclose(self):
        await self.http.aclose()
