"""Cliente mínimo del MCP de la mesa (gateway de pcx), solo con la biblioteca estándar.

La URL y el token salen del botón «Copiar» de la consola: `PCX_MCP_URL` (por defecto el gateway local de esta escena,
puerto 9323) y `PCX_USER_TOKEN`.
"""

from __future__ import annotations

import base64
import itertools
import json
import os
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field

URL_LOCAL = "http://localhost:9323/mcp"


class ErrorMesa(RuntimeError):
    pass


@dataclass
class Resultado:
    datos: dict
    imagenes: list[bytes] = field(default_factory=list)


class Mesa:
    def __init__(self, url: str | None = None, token: str | None = None):
        self.url = url or os.environ.get("PCX_MCP_URL", URL_LOCAL)
        self.token = token or os.environ.get("PCX_USER_TOKEN") or self._token_de_archivo()
        if not self.token:
            raise ErrorMesa("Falta PCX_USER_TOKEN: cópialo del botón «Copiar» de la consola (va después de «Bearer»).")
        self._ids = itertools.count(1)

    @staticmethod
    def _token_de_archivo() -> str | None:
        """La sesión de la consola guardada en salida/sesion.json (ensayos locales; la carpeta no se sube al repo)."""
        ruta = os.environ.get("PCX_SESION_JSON", os.path.join(os.path.dirname(__file__), "salida", "sesion.json"))
        try:
            with open(ruta) as f:
                return json.load(f).get("user_token")
        except (OSError, json.JSONDecodeError):
            return None

    def _rpc(self, metodo: str, params: dict, timeout: float) -> dict:
        cuerpo = json.dumps({"jsonrpc": "2.0", "id": next(self._ids), "method": metodo, "params": params}).encode()
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
            texto = urllib.request.urlopen(req, timeout=timeout).read().decode()
        except urllib.error.HTTPError as e:
            raise ErrorMesa(f"{metodo}: HTTP {e.code} {e.read()[:300]!r}") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            # sin red, timeout de lectura o conexión cortada: todo es «no hay mesa», y quien llama reintenta
            raise ErrorMesa(f"No hay gateway en {self.url}: {getattr(e, 'reason', e)}") from e
        # Streamable HTTP puede contestar JSON o SSE («data: {...}»)
        datos = [ln[5:].strip() for ln in texto.splitlines() if ln.startswith("data:")]
        try:
            r = json.loads(datos[-1] if datos else texto)
        except json.JSONDecodeError as e:
            raise ErrorMesa(f"{metodo}: respuesta que no es JSON: {texto[:200]!r}") from e
        if not isinstance(r, dict):
            raise ErrorMesa(f"{metodo}: respuesta inesperada: {texto[:200]!r}")
        if "error" in r:
            raise ErrorMesa(f"{metodo}: {r['error']}")
        if "result" not in r:
            raise ErrorMesa(f"{metodo}: respuesta sin result: {texto[:200]!r}")
        return r["result"]

    def tool(self, nombre: str, args: dict | None = None, *, timeout: float = 60) -> Resultado:
        r = self._rpc("tools/call", {"name": nombre, "arguments": args or {}}, timeout)
        datos = r.get("structuredContent")
        imagenes = []
        for c in r.get("content", []):
            if c.get("type") == "image":
                imagenes.append(base64.b64decode(c["data"]))
            elif datos is None and c.get("type") == "text":
                try:
                    datos = json.loads(c["text"])
                except json.JSONDecodeError:
                    datos = {"texto": c["text"]}
        datos = datos or {}
        if r.get("isError"):
            raise ErrorMesa(f"{nombre}: {datos.get('error', datos)}")
        return Resultado(datos, imagenes)

    # atajos con nombre de lo que hacen
    def mirar(self, camara: str | None = None, **extra) -> Resultado:
        args = {"annotate": False, **extra}
        if camara:
            args["camera_id"] = camara
        return self.tool("look", args, timeout=30)

    def proyectar(self, widgets: list[dict], escena: str | None = None) -> dict:
        args = {"widgets": widgets, "request_id": uuid.uuid4().hex}
        if escena:
            args["scene"] = escena
        return self.tool("project", args).datos

    def quitar(self, ids: list[str], escena: str | None = None) -> dict:
        args = {"ids": ids, "request_id": uuid.uuid4().hex}
        if escena:
            args["scene"] = escena
        return self.tool("remove", args).datos

    def esperar_eventos(self, cursor: int, tipos=(), timeout_s: float = 2.0) -> dict:
        return self.tool("events_wait", {"cursor": cursor, "types": list(tipos), "timeout_s": timeout_s}, timeout=timeout_s + 15).datos

    def escenas(self, accion: str = "list", **args) -> dict:
        return self.tool("scenes", {"action": accion, **args}).datos

    def mundo(self, escena: str | None = None) -> dict:
        return self.tool("scene", {"scene": escena} if escena else {}).datos
