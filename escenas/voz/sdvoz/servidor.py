"""Servidor local de la voz: la página React, el token de conversación y las client tools hacia la mesa.

    uv run python -m sdvoz servir [--puerto 9325] [--voz]

- `POST /api/sesion/<escena>` {modo: texto|voz} → URL firmada (texto, websocket) o token de conversación (voz,
  WebRTC). La llave de ElevenLabs se queda aquí. El modo voz solo con `--voz` (el micrófono necesita el OK de Max).
- `POST /api/tool/<escena>/<nombre>` → ejecuta la client tool contra el MCP de la sesión de la mesa. El token de la
  sesión tampoco sale de aquí (y el gateway no tiene CORS).
- `GET /api/eventos/<escena>` (SSE) → lo que pasa en la mesa, ya traducido: `hablar` (la voz debe reaccionar),
  `contexto` (lo sabe sin hablar) y `escena` (cambió la pestaña). Fuentes: `events_wait` del gateway y los
  `eventos.jsonl` que escribe el motor (ahí quedan hoy `nivel`, `libreta_nueva`, `canasto_movido` de los
  ScenePerceptor, porque el motor todavía no los publica al gateway).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import agentes, config as cfgmod
from .escenas import ESCENAS, escena as get_escena
from .herramientas import CATALOGO, Contexto, describir_evento, ejecutar
from .mesa import ErrorMesa, Mesa
from .presets import preset_de

AQUI = Path(__file__).resolve().parent
DIST = AQUI.parent / "web" / "dist"


def _plano(ev: dict) -> dict:
    """Un evento de events_wait o del motor, en forma plana: {type, event?, data?, ...}."""
    if isinstance(ev.get("payload"), dict):
        ev = {**ev["payload"], "seq": ev.get("seq")}
    elif isinstance(ev.get("event"), dict):
        ev = ev["event"]
    if "type" not in ev and "event" in ev:      # los del motor: {"event": "nivel", "data": {...}}
        ev = {"type": "flow_emitted", **ev}
    return ev


CARPETA_A_PRESET = {"laboratorio": "lab", "sistema-solar": "solar", "escritorio": "desk"}

ESTABLE_S = float(os.environ.get("SD_OBJETOS_ESTABLE_S", "5"))


class Cambios:
    """Altas y bajas de objetos con histéresis: la voz solo se entera de lo que se mantiene `estable_s` segundos.

    Cuenta por nombre (no por id): cuando el motor re-arma la escena, «se fue Sol» y «apareció Sol» (aunque cambie el
    id) se anulan. Un objeto que sale y vuelve antes de `estable_s` no se avisa. Lo que madura junto (con `junta_s`
    de diferencia) va en un solo aviso, como el catálogo que llega de una vez.
    """

    def __init__(self, estable_s: float = ESTABLE_S, junta_s: float = 1.5):
        self.estable_s, self.junta_s = estable_s, junta_s
        self.actual: dict[str, int] = {}       # cuántos hay por nombre, según los eventos
        self.contado: dict[str, int] = {}      # cuántos le dijimos a la voz
        self.cambio: dict[str, float] = {}     # nombre → último cambio sin avisar
        self.ids: dict[str, str] = {}          # id → nombre de los presentes
        self.ignorados = 0                     # cambios que volvieron atrás antes de madurar

    def alta(self, oid: str | None, nombre: str, ahora: float):
        if oid and oid in self.ids:
            return                             # alta repetida del mismo id
        if oid:
            self.ids[oid] = nombre
        self.actual[nombre] = self.actual.get(nombre, 0) + 1
        self.cambio[nombre] = ahora

    def baja(self, oid: str | None, nombre: str | None, ahora: float):
        nombre = (self.ids.pop(oid, None) if oid else None) or nombre or oid or "algo"
        self.actual[nombre] = self.actual.get(nombre, 0) - 1
        self.cambio[nombre] = ahora

    def maduros(self, ahora: float) -> tuple[list[str], list[str]]:
        """(altas, bajas) que ya se mantuvieron; vacío si nada maduró."""
        edades = {n: ahora - t for n, t in self.cambio.items()}
        if not any(e >= self.estable_s for e in edades.values()):
            return [], []
        casi = any(self.estable_s - self.junta_s <= e < self.estable_s for e in edades.values())
        if casi and max(edades.values()) < self.estable_s + self.junta_s:
            return [], []                      # espera a los que están por madurar para avisar todo junto
        altas: list[str] = []
        bajas: list[str] = []
        for nombre, e in edades.items():
            if e < self.estable_s:
                continue
            del self.cambio[nombre]
            d = self.actual.get(nombre, 0) - self.contado.get(nombre, 0)
            self.contado[nombre] = self.actual.get(nombre, 0)
            if d == 0:
                self.ignorados += 1
            altas += [nombre] * max(d, 0)
            bajas += [nombre] * max(-d, 0)
        return altas, bajas


class Oido:
    """Escucha la mesa y reparte lo que pasa entre las páginas conectadas."""

    def __init__(self, mesa: Mesa | None, contextos: dict[str, Contexto]):
        self.mesa, self.contextos = mesa, contextos
        self.eventos: deque = deque(maxlen=60)
        self.subs: set[tuple[str, asyncio.Queue]] = set()
        self.ultimo_nivel: dict[str, tuple] = {}
        self.ultimo_contexto_nivel = 0.0
        self.estado = {"gateway": "sin conectar", "motor": []}
        self.cambios = Cambios()
        self.tarea_cambios: asyncio.Task | None = None

    # -- reparto
    def publicar(self, msg: dict, preset: str | None = None):
        preset = CARPETA_A_PRESET.get(preset, preset) if preset else None
        for clave, q in list(self.subs):
            if preset and msg["tipo"] == "hablar" and get_escena(clave).preset_id != preset:
                continue
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(msg)

    def recibir(self, crudo: dict, fuente: str):
        ev = _plano(crudo)
        self.eventos.append(ev)
        for c in self.contextos.values():
            c.eventos = list(self.eventos)
        t, e, d = ev.get("type"), ev.get("event"), ev.get("data") or {}
        if t == "flow_emitted" and e == "preset_hecho" and d.get("id"):
            for c in self.contextos.values():
                c.respuestas[d["id"]] = d
            return
        if t == "flow_emitted" and (d.get("de") == "voz" or str(d.get("id") or "").startswith("voz-")):
            return                                   # nuestro propio pedido (paso, preset_accion, preset_ajustar)
        frase = describir_evento(ev)
        if not frase:
            return
        preset = d.get("escena") or d.get("preset")
        if isinstance(preset, str) and ":" in preset:
            preset = preset.split(":")[0]
        texto = f"[mesa] {frase}"
        if t == "widget_pressed":
            if str(ev.get("widget_id") or "").startswith("pcx:"):
                return                               # la interfaz propia de la mesa (pestañas, voz)
            wid = str(ev.get("widget_id") or "")
            preset = wid.split("-")[0].split(":")[0] if ("-" in wid or ":" in wid) else preset
            self.publicar({"tipo": "hablar", "texto": texto}, preset)
        elif t in ("object_appeared", "object_removed"):
            if t == "object_appeared":
                o = ev.get("object") or d
                oid = o.get("id") or o.get("object_id")
                self.cambios.alta(oid, o.get("label") or oid or "algo", time.monotonic())
            else:
                self.cambios.baja(ev.get("object_id") or d.get("object_id"), ev.get("label") or d.get("label"),
                                  time.monotonic())
            if self.tarea_cambios is None or self.tarea_cambios.done():
                with contextlib.suppress(RuntimeError):      # sin loop corriendo (tests): avisar_cambios a mano
                    self.tarea_cambios = asyncio.get_running_loop().create_task(self._avisar_cambios())
            return
        elif t == "flow_emitted" and e == "scene_changed":
            self.publicar({"tipo": "escena", "pestana": d.get("scene"), "texto": texto})
        elif t == "flow_emitted" and e == "nivel":
            firma = (d.get("estado"), d.get("cerca"))
            vaso = str(d.get("vaso") or "vaso")
            if firma != self.ultimo_nivel.get(vaso) and (d.get("estado") or d.get("cerca")):
                self.ultimo_nivel[vaso] = firma
                urgente = str(d.get("estado") or "").startswith(("te_pas", "exceso", "sobre", "pasado"))
                self.publicar({"tipo": "hablar", "texto": texto, "urgente": urgente}, preset or "lab")
            elif time.monotonic() - self.ultimo_contexto_nivel > 1.0:
                self.ultimo_contexto_nivel = time.monotonic()
                self.publicar({"tipo": "contexto", "texto": texto})
        elif t == "flow_emitted" and e in ("libreta_escrita", "libreta_nueva", "solar_armado", "voice_command",
                                           "contaminacion", "lamina_tocada"):
            if e == "contaminacion" and not d.get("hay"):
                return
            self.publicar({"tipo": "hablar", "texto": texto}, preset)
        else:
            self.publicar({"tipo": "contexto", "texto": texto})
        cfgmod.log("evento", {"fuente": fuente, "texto": texto[:200]})

    async def _avisar_cambios(self):
        """Mientras haya altas o bajas sin madurar, revisa cada medio segundo y avisa solo las que se mantuvieron."""
        while self.cambios.cambio:
            await asyncio.sleep(0.5)
            self.avisar_cambios(time.monotonic())

    def avisar_cambios(self, ahora: float):
        """Altas: hasta 3 juntas son algo que hizo la persona (la voz reacciona); más es la escena armándose
        (contexto, sin turno). Bajas: siempre contexto."""
        antes = self.cambios.ignorados
        altas, bajas = self.cambios.maduros(ahora)
        if self.cambios.ignorados > antes:
            cfgmod.log("evento", {"fuente": "objetos", "ignorados": self.cambios.ignorados - antes})
        if altas:
            texto = "[mesa] apareció " + altas[0] if len(altas) == 1 else \
                f"[mesa] aparecieron {len(altas)} cosas: " + ", ".join(altas[:12])
            self.publicar({"tipo": "hablar" if len(altas) <= 3 else "contexto", "texto": texto})
            cfgmod.log("evento", {"fuente": "objetos", "texto": texto[:200]})
        if bajas:
            texto = "[mesa] se fue " + bajas[0] if len(bajas) == 1 else \
                f"[mesa] se fueron {len(bajas)} cosas: " + ", ".join(bajas[:12])
            self.publicar({"tipo": "contexto", "texto": texto})
            cfgmod.log("evento", {"fuente": "objetos", "texto": texto[:200]})

    # -- fuentes
    async def gateway(self):
        if not self.mesa:
            return
        cursor = None
        while True:
            try:
                if cursor is None:
                    r = await self.mesa.tool("events_wait", {"cursor": 10**12, "timeout_s": 0}, timeout=15)
                    cursor = int(r.get("cursor") or 0)
                    self.estado["gateway"] = "escuchando"
                r = await self.mesa.tool("events_wait", {"cursor": cursor, "timeout_s": 20, "max_events": 100},
                                         timeout=40)
                cursor = int(r.get("cursor", cursor))
                for ev in r.get("events", []):
                    self.recibir(ev, "gateway")
                self.estado["gateway"] = "escuchando"
            except ErrorMesa as e:
                self.estado["gateway"] = f"error: {str(e)[:80]}"
                await asyncio.sleep(3)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 (el oído no se cae por un evento raro)
                self.estado["gateway"] = f"error: {type(e).__name__}"
                await asyncio.sleep(3)

    async def motor(self):
        """Sigue los eventos.jsonl del motor (desde el final, como `tail -f`)."""
        fijo = os.environ.get("SD_MOTOR_EVENTOS")
        raiz = Path(os.environ.get("PCX_MOTOR_STATE", Path.home() / ".pcx" / "motor"))
        pos: dict[Path, int] = {}
        while True:
            try:
                archivos = [Path(fijo)] if fijo else list(raiz.glob("**/eventos.jsonl"))
                for p in archivos:
                    if not p.exists():
                        continue
                    tam = p.stat().st_size
                    if p not in pos:
                        pos[p] = tam                 # parte al final: lo viejo no se cuenta
                        continue
                    if tam < pos[p]:
                        pos[p] = 0                   # rotó
                    if tam > pos[p]:
                        with p.open("rb") as f:
                            f.seek(pos[p])
                            nuevo = f.read()
                        pos[p] = tam
                        for linea in nuevo.decode("utf-8", "ignore").splitlines():
                            with contextlib.suppress(ValueError):
                                self.recibir(json.loads(linea), f"motor:{p.parent.name}")
                self.estado["motor"] = [str(p) for p in pos]
                await asyncio.sleep(0.25)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                await asyncio.sleep(1)


def _remoto(request: Request) -> bool:
    """¿Llegó por la tailnet (tailscale serve) y no desde este mismo computador?"""
    h = request.headers
    return bool(h.get("tailscale-user-login") or h.get("x-forwarded-for")) or \
        (request.client is not None and request.client.host not in ("127.0.0.1", "::1"))


def _origen_ok(request: Request) -> bool:
    """Las tools solo se llaman desde la página de este mismo servidor. Un cliente sin navegador (curl, `probar`) no
    manda Origin y pasa. Una página de otro sitio abierta en este Mac no pasa, ni con DNS rebinding: además de ser el
    mismo origen, el host tiene que ser local o venir por tailscale serve."""
    origen = request.headers.get("origin")
    if origen is None:
        return True
    u = urlsplit(origen)
    if u.netloc != request.headers.get("host", ""):
        return False
    return u.hostname in ("127.0.0.1", "localhost", "::1") or bool(request.headers.get("tailscale-user-login"))


def _quien(request: Request) -> str:
    return request.headers.get("tailscale-user-login") or ("local" if not _remoto(request) else "tailnet")


def agente_publico(clave: str) -> str | None:
    """agent_id para conectarse sin llave: `SD_AGENTE_<ESCENA>` (p. ej. el Lumo público de la cuenta de Max) o, si no
    hay llave en este computador, el de la config."""
    env = os.environ.get(f"SD_AGENTE_{clave.upper()}") or (os.environ.get("SD_VOZ_AGENT_ID") if clave == "solar" else None)
    if env:
        return env.strip()
    if not cfgmod.api_key():
        return (cfgmod.load().get("agentes") or {}).get(clave)
    return None


def crear_app(permitir_voz: bool = False) -> Starlette:
    cfg = cfgmod.load()
    token = cfgmod.session_token(cfg)
    mesa = Mesa(cfgmod.gateway_url(cfg), token) if token else None
    contextos = {c: Contexto(mesa, e) for c, e in ESCENAS.items()} if mesa else {}
    oido = Oido(mesa, contextos)
    tareas: list[asyncio.Task] = []

    @contextlib.asynccontextmanager
    async def vida(app):
        tareas.extend([asyncio.create_task(oido.gateway()), asyncio.create_task(oido.motor())])
        yield
        for t in tareas:
            t.cancel()
        if mesa:
            await mesa.aclose()

    async def estado(request: Request):
        c = cfgmod.load()
        return JSONResponse({
            "escenas": [{"clave": k, "agente": e.agente, "pestana": e.pestana,
                         "publicado": bool((c.get("agentes") or {}).get(k) or agente_publico(k)),
                         "preset": bool(preset_de(e.preset_id, e))} for k, e in ESCENAS.items()],
            "mesa": {"gateway": cfgmod.gateway_url(c), "sesion": bool(token), "oido": oido.estado["gateway"],
                     "motor": oido.estado["motor"]},
            "voz_permitida": permitir_voz,
            "herramientas": [h.nombre for h in CATALOGO],
        })

    async def sesion(request: Request):
        clave = request.path_params["escena"]
        try:
            e = get_escena(clave)
        except KeyError as ex:
            return JSONResponse({"error": str(ex)}, status_code=404)
        modo = (await request.json()).get("modo", "texto") if await request.body() else "texto"
        publico = agente_publico(e.clave)
        if publico:
            # Agente público (sin autenticación): la página se conecta con el agent_id; no hace falta llave.
            if modo == "voz" and not permitir_voz:
                return JSONResponse({"error": "El modo voz usa el micrófono: corre `servir --voz`."}, status_code=403)
            cfgmod.log("sesion", {"escena": e.clave, "modo": modo, "publico": True, "quien": _quien(request)})
            return JSONResponse({"agentId": publico, "escena": e.clave, "agente": e.agente})
        try:
            if modo == "voz":
                if not permitir_voz:
                    return JSONResponse({"error": "El modo voz usa el micrófono: falta el OK de Max (servir --voz)."},
                                        status_code=403)
                out = {"conversationToken": await asyncio.to_thread(agentes.conversation_token, e.clave)}
            else:
                out = {"signedUrl": await asyncio.to_thread(agentes.signed_url, e.clave)}
        except agentes.ElevenLabsError as ex:
            return JSONResponse({"error": str(ex)}, status_code=502)
        cfgmod.log("sesion", {"escena": e.clave, "modo": modo, "quien": _quien(request)})
        return JSONResponse({**out, "escena": e.clave, "agente": e.agente})

    async def tool(request: Request):
        clave, nombre = request.path_params["escena"], request.path_params["nombre"]
        if not _origen_ok(request):
            return JSONResponse({"error": "Origen no permitido: abre la página desde este mismo servidor."}, status_code=403)
        if "application/json" not in request.headers.get("content-type", ""):
            return JSONResponse({"error": "Se espera Content-Type: application/json."}, status_code=415)
        if clave not in contextos:
            return JSONResponse({"resultado": "La voz no tiene sesión de mesa (falta el token de la sesión)."})
        args = await request.json() if await request.body() else {}
        t0 = time.monotonic()
        res = await ejecutar(contextos[clave], nombre, args if isinstance(args, dict) else {})
        cfgmod.log("tool", {"escena": clave, "nombre": nombre, "args": args, "resultado": res[:300], "quien": _quien(request),
                            "ms": round((time.monotonic() - t0) * 1000)})
        return JSONResponse({"resultado": res})

    async def eventos(request: Request):
        clave = request.path_params["escena"]
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        sub = (clave, q)
        oido.subs.add(sub)

        async def flujo():
            try:
                yield "retry: 2000\n\n"
                while True:
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=15)
                        yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": latido\n\n"
            finally:
                oido.subs.discard(sub)

        return StreamingResponse(flujo(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    async def simular(request: Request):
        """Solo para probar desde este Mac: inyecta un evento como si viniera del motor (p. ej. un nivel del vaso)."""
        if _remoto(request):
            return JSONResponse({"error": "solo desde este computador"}, status_code=403)
        ev = await request.json()
        oido.recibir(ev, "simulado")
        return JSONResponse({"ok": True})

    async def indice(request: Request):
        if (DIST / "index.html").exists():
            return FileResponse(DIST / "index.html")
        return JSONResponse({"error": "falta compilar la página: cd web && npm run build"}, status_code=503)

    rutas = [
        Route("/", indice),
        Route("/api/estado", estado),
        Route("/api/sesion/{escena}", sesion, methods=["POST"]),
        Route("/api/tool/{escena}/{nombre}", tool, methods=["POST"]),
        Route("/api/eventos/{escena}", eventos),
        Route("/api/simular", simular, methods=["POST"]),
    ]
    if (DIST / "assets").exists():
        rutas.append(Mount("/assets", StaticFiles(directory=DIST / "assets")))
    app = Starlette(routes=rutas, lifespan=vida)
    app.state.oido, app.state.contextos = oido, contextos
    return app
