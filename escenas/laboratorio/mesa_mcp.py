"""Cliente mínimo del MCP de la mesa (Streamable HTTP sin estado, Bearer). Solo biblioteca estándar + numpy/OpenCV
para decodificar las fotos de `look`.

    PCX_MCP_URL   por defecto http://localhost:9322/mcp (el gateway local de esta escena)
    PCX_USER_TOKEN el token de la sesión (botón «Copiar» de la consola)
"""

from __future__ import annotations

import base64
import itertools
import json
import os
import re
import urllib.error
import urllib.request

import cv2
import numpy as np


class ErrorMesa(RuntimeError):
    pass


class Mesa:
    def __init__(self, url: str | None = None, token: str | None = None, timeout: float = 60):
        self.url = url or os.environ.get("PCX_MCP_URL", "http://localhost:9322/mcp")
        self.token = token or os.environ.get("PCX_USER_TOKEN", "")
        if not self.token:
            raise ErrorMesa("Falta PCX_USER_TOKEN: cópialo con el botón «Copiar» de la consola.")
        self.timeout = timeout
        self._ids = itertools.count(1)
        self._req = itertools.count(1)

    def _rpc(self, method: str, params: dict, timeout: float | None = None) -> dict:
        cuerpo = json.dumps({"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params}).encode()
        req = urllib.request.Request(
            self.url,
            data=cuerpo,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
        try:
            texto = urllib.request.urlopen(req, timeout=timeout or self.timeout).read().decode()
        except urllib.error.HTTPError as e:
            raise ErrorMesa(f"{method}: HTTP {e.code} {e.read().decode()[:300]}") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise ErrorMesa(f"{method}: sin conexión con el gateway ({e})") from e
        m = re.search(r"\{.*\}", texto, re.S)  # respuesta JSON directa o dentro de un evento SSE
        if not m:
            raise ErrorMesa(f"{method}: respuesta vacía")
        try:
            r = json.loads(m.group(0))
        except json.JSONDecodeError as e:
            raise ErrorMesa(f"{method}: respuesta que no es JSON: {texto[:200]!r}") from e
        if not isinstance(r, dict):
            raise ErrorMesa(f"{method}: respuesta inesperada: {texto[:200]!r}")
        if "error" in r:
            raise ErrorMesa(f"{method}: {r['error']}")
        if "result" not in r:
            raise ErrorMesa(f"{method}: respuesta sin result: {texto[:200]!r}")
        return r["result"]

    def tool(self, nombre: str, args: dict | None = None, timeout: float | None = None) -> tuple[dict, list[np.ndarray]]:
        """Llama una tool. Devuelve (datos estructurados, fotos decodificadas en BGR)."""
        args = dict(args or {})
        if nombre in ("project", "remove", "look", "ask", "calibrate"):
            args.setdefault("request_id", f"lab-{os.getpid()}-{next(self._req)}")
        r = self._rpc("tools/call", {"name": nombre, "arguments": args}, timeout)
        datos, fotos = {}, []
        for c in r.get("content", []):
            if c.get("type") == "image":
                buf = np.frombuffer(base64.b64decode(c["data"]), np.uint8)
                fotos.append(cv2.imdecode(buf, cv2.IMREAD_COLOR))
            elif c.get("type") == "text":
                try:
                    datos = json.loads(c["text"])
                except json.JSONDecodeError:
                    datos = {"texto": c["text"]}
        if r.get("structuredContent"):
            datos = r["structuredContent"]
        if r.get("isError") or (isinstance(datos, dict) and "error" in datos and len(datos) == 1):
            raise ErrorMesa(f"{nombre}: {datos.get('error', datos)}")
        return datos, fotos

    # Atajos
    def project(self, widgets: list[dict]) -> dict:
        return self.tool("project", {"widgets": widgets})[0]

    def remove(self, ids: list[str]) -> dict:
        return self.tool("remove", {"ids": list(ids)})[0]

    def look(self, camera_id: str | None = None, annotate: bool = False, clean: bool = False) -> tuple[dict, np.ndarray]:
        args = {"annotate": annotate}
        if camera_id:
            args["camera_id"] = camera_id
        if clean:
            args["clean"] = True
        datos, fotos = self.tool("look", args)
        if not fotos:
            raise ErrorMesa("look no devolvió foto")
        return datos, fotos[0]

    def eventos(self, cursor: int, tipos: list[str] | None = None, espera_s: float = 0.0) -> tuple[int, list[dict]]:
        datos, _ = self.tool("events_wait", {"cursor": cursor, "types": tipos or [], "timeout_s": espera_s}, timeout=espera_s + 15)
        return int(datos.get("cursor", cursor)), list(datos.get("events", []))

    def cursor_actual(self) -> int:
        """Cursor del final, para no procesar toques viejos al partir."""
        cur = 10**12
        cur, _ = self.eventos(cur, espera_s=0)
        return cur
