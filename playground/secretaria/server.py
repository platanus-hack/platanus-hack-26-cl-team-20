"""Puente HTTP del agente de voz «Playground»: ElevenLabs (nube) → túnel propio → este Mac → runtime del playground.

Dos servidores, los dos solo en 127.0.0.1:

  :8793  webhooks (lo publica el túnel propio, `tunel.py`; nunca el de la secretaria)
         POST /playground/h/<herramienta>   herramientas webhook del agente (catálogo en tools.py)
         GET  /playground/salud             vivo o no, sin datos
         Todo POST exige X-Playground-Token = el token del puente (ElevenLabs lo guarda como secreto del workspace
         y lo agrega en cada llamada). Sin token o con otro: 401 y queda en rechazos.jsonl. También acepta las
         rutas sin el prefijo /playground (por si el túnel lo recorta).
  :8794  página local para hablar con el agente desde el Mac (`python -m playground.secretaria hablar`)
         GET /app · GET /app/cliente.js · POST /app/sesion (token de conversación de ElevenLabs; la llave nunca
         llega al navegador). Rechaza otro Host u Origin (rebinding de DNS). Nunca va por el túnel.
"""

from __future__ import annotations

import hmac
import json
import threading
import time
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from . import config as cfgmod
from .tools import Toolbox

AQUI = Path(__file__).parent
MAX_BODY = 200_000


def token_ok(header: str, expected: str | None) -> bool:
    """Comparación en tiempo constante; sin token configurado no pasa nadie."""
    if not expected or not header:
        return False
    return hmac.compare_digest(header.encode("utf-8"), expected.encode("utf-8"))


def build_app(toolbox: Toolbox | None = None, token_fn: Callable[[], str | None] | None = None) -> FastAPI:
    app = FastAPI(title="playground · puente de voz", docs_url=None, redoc_url=None, openapi_url=None)
    holder: dict = {"tb": toolbox}
    get_token = token_fn or (lambda: cfgmod.token())

    def tb() -> Toolbox:
        if holder["tb"] is None:
            holder["tb"] = Toolbox()
        return holder["tb"]

    async def salud():
        return {"ok": True}

    async def herramienta(name: str, request: Request):
        if not token_ok(request.headers.get("x-playground-token", ""), get_token()):
            cfgmod.log("rechazos", {"herramienta": name[:64], "motivo": "token"})
            return JSONResponse({"error": "token"}, status_code=401)
        if name not in tb().tools:
            return JSONResponse({"error": f"no existe la herramienta {name[:64]}"}, status_code=404)
        body = await request.body()
        if len(body) > MAX_BODY:
            return JSONResponse({"error": "cuerpo demasiado grande"}, status_code=413)
        try:
            args = json.loads(body or b"{}")
        except ValueError:
            return JSONResponse({"error": "json"}, status_code=400)
        t0 = time.perf_counter()
        try:
            out = await run_in_threadpool(tb().call, name, args if isinstance(args, dict) else {})
        except Exception as e:  # noqa: BLE001  (la voz tiene que poder decir «falló», no quedarse colgada)
            out = {"error": f"falló en el Mac: {type(e).__name__}"}
        cfgmod.log("llamadas", {"herramienta": name, "conv": request.headers.get("x-conversacion", "")[:64],
                                "ok": "error" not in out, "ms": round((time.perf_counter() - t0) * 1000),
                                "args": json.dumps(args, ensure_ascii=False)[:300],
                                **({"error": str(out.get("error"))[:200]} if "error" in out else {})})
        return JSONResponse(out)

    for prefix in (cfgmod.RUTA, ""):
        app.add_api_route(prefix + "/salud", salud, methods=["GET"])
        app.add_api_route(prefix + "/h/{name}", herramienta, methods=["POST"])
    return app


# ---------------------------------------------------------------------------- página local para hablar

def build_local_app(cfg: dict | None = None, session_fn: Callable[[], dict] | None = None,
                    port: int | None = None) -> FastAPI:
    cfg = cfg or cfgmod.load()
    port = int(port or cfg.get("puerto_app") or cfgmod.PUERTO_APP)
    app = FastAPI(title="playground · hablar", docs_url=None, redoc_url=None, openapi_url=None)
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    origins = {None, f"http://127.0.0.1:{port}", f"http://localhost:{port}"}

    def local(request: Request) -> bool:
        return request.headers.get("host") in hosts and request.headers.get("origin") in origins

    def session() -> dict:
        if session_fn:
            return session_fn()
        from .agent import conversation_token
        c = cfgmod.load()
        return {"token": conversation_token(c), "agente": c.get("nombre_agente")}

    @app.get("/app")
    async def page(request: Request):
        if not local(request):
            return JSONResponse({"error": "solo local"}, status_code=403)
        return HTMLResponse((AQUI / "app" / "index.html").read_text(encoding="utf-8"),
                            headers={"Cache-Control": "no-store", "Permissions-Policy": "microphone=(self)"})

    @app.get("/app/cliente.js")
    async def client_js(request: Request):
        if not local(request):
            return JSONResponse({"error": "solo local"}, status_code=403)
        path = Path(cfg.get("cliente_js") or "")
        if not path.is_file():
            return JSONResponse({"error": "falta el SDK web de ElevenLabs (cliente_js en la config)"}, status_code=404)
        return FileResponse(path, media_type="text/javascript")

    @app.post("/app/sesion")
    async def sesion(request: Request):
        if not local(request):
            return JSONResponse({"error": "solo local"}, status_code=403)
        try:
            return await run_in_threadpool(session)
        except Exception as e:  # noqa: BLE001
            return JSONResponse({"error": f"no pude abrir la sesión: {str(e)[:200]}"}, status_code=502)

    return app


def serve(port: int | None = None, app_port: int | None = None) -> None:
    import uvicorn
    cfg = cfgmod.load()
    port = port or int(cfg.get("puerto") or cfgmod.PUERTO)
    app_port = app_port or int(cfg.get("puerto_app") or cfgmod.PUERTO_APP)
    cfgmod.token(create=True)
    local = uvicorn.Server(uvicorn.Config(build_local_app(cfg, port=app_port), host="127.0.0.1", port=app_port, log_level="warning"))
    threading.Thread(target=local.run, name="playground-hablar", daemon=True).start()
    cfgmod.log("http", {"linea": f"escuchando en 127.0.0.1:{port} (webhooks) y :{app_port} (página local)"})
    uvicorn.run(build_app(), host="127.0.0.1", port=port, log_level="warning", access_log=False)
