"""Una sola app Starlette en un solo proceso: /node es dueño de los sockets, /mcp usa las tools del contrato."""

import asyncio
import logging
import time
from contextlib import asynccontextmanager, suppress

import msgspec
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp_types import CallToolResult, ImageContent, ListToolsResult, TextContent, Tool
from pcx_contracts import ns_to_str
from pcx_contracts.cloud import (
    CloudMessage,
    EventBatch,
    Heartbeat,
    Hello,
    HelloRejected,
    HistoryGap,
    ReplayDone,
    StateSnapshot,
    ToolResult,
    Welcome,
)
from pcx_contracts.envelope import SUPPORTED_VERSIONS
from pcx_contracts.tools import mcp_tools
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from .config import Settings
from .gateway import HISTORY_TOOL, Connection, Gateway, Rejected, iso, parse_time
from .pubsub import MemoryBus, RedisBus
from .store import Store

log = logging.getLogger(__name__)
INSTRUCTIONS = """pcx da contexto físico de un espacio real. El nodo local es dueño del estado reportado.
Empieza por scene o look. Coordenadas table_mm: mm, origen arriba izquierda, x derecha, y abajo.
Usa anchor.object_id para que los widgets sigan objetos; usa textos cortos y legibles (12–20 mm).
Toda tool que cambia algo exige request_id: elígelo tú y repítelo igual si reintentas (el nodo deduplica).
project crea/reemplaza widgets por id; remove limpia tus widgets al terminar. ask proyecta opciones y
espera el toque. events_wait(cursor) hace long-poll; conserva el cursor devuelto aunque no haya eventos; si
trae gap, faltan eventos (reconexión del nodo): vuelve a leer scene. run_flow corre en el nodo sin latencia.
history(query, since, until) es la memoria del espacio: «¿dónde dejé las llaves?» → history(query="llaves").
home: la política del nodo decide; si responde pending, una PERSONA confirma con un toque en la mesa o en la
consola. Tú no puedes confirmar ni debes pedir que se omita. Espera home_call_done con events_wait.
No presupongas que una tool produjo un efecto: espera su resultado y el estado reportado. Un timeout puede
dejar un efecto en el nodo: consulta scene antes de reintentar.
"""
IMAGE_MAGIC = {b"\x89PNG": "image/png", b"\xff\xd8\xff": "image/jpeg", b"RIFF": "image/webp"}


async def receive_cloud(ws, timeout, max_size):
    frame = await asyncio.wait_for(ws.receive(), timeout)
    if frame["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(frame.get("code", 1000))
    raw = frame.get("text") if frame.get("text") is not None else frame.get("bytes")
    if raw is None:
        raise ValueError("Se requiere JSON en un frame text o binary")
    if len(raw) > max_size:
        raise ValueError("Frame demasiado grande")
    return msgspec.json.decode(raw, type=CloudMessage)


def bearer(scope_or_request) -> str | None:
    request = scope_or_request if isinstance(scope_or_request, Request) else Request(scope_or_request)
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


class BodyTooLarge(Exception):
    pass


class Guard:
    """Bearer de usuario (/mcp: solo scope mcp; /api: mcp o consola) y límite de tamaño de cuerpos HTTP."""

    def __init__(self, app, store, max_body):
        self.app, self.store, self.max_body = app, store, max_body

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        length = Request(scope).headers.get("content-length")
        if length and (not length.isdigit() or int(length) > self.max_body):
            return await JSONResponse({"error": "Cuerpo demasiado grande"}, status_code=413)(
                scope, receive, send
            )
        if path.startswith(("/mcp", "/api/")) and not path.startswith("/api/node/"):
            token = bearer(scope)
            identity = await self.store.identity(token) if token else None
            if identity is None or (path.startswith("/mcp") and identity.scope != "mcp"):
                return await JSONResponse(
                    {"error": "Bearer inválido para esta ruta"},
                    status_code=401,
                    headers={"WWW-Authenticate": 'Bearer realm="pcx"'},
                )(scope, receive, send)
            scope.setdefault("state", {})["identity"] = identity
        seen = 0

        async def limited():
            nonlocal seen
            message = await receive()
            seen += len(message.get("body", b""))
            if seen > self.max_body:
                raise BodyTooLarge
            return message

        await self.app(scope, limited, send)


class MCPRoute:
    def __init__(self, manager):
        self.manager = manager

    async def __call__(self, scope, receive, send):
        await self.manager.asgi_app(scope, receive, send)


def create_app(settings: Settings | None = None):
    settings = settings or Settings.from_env()
    store = Store(settings.database_url)
    bus = RedisBus(settings.redis_url) if settings.redis_url else MemoryBus()
    gateway = Gateway(store, bus, settings)
    tools = [Tool.model_validate(tool) for tool in [*mcp_tools(), HISTORY_TOOL]]

    async def list_tools(ctx, params):
        return ListToolsResult(tools=tools)

    async def call_tool(ctx, params):
        identity = ctx.request.state.identity
        reply = await gateway.execute(identity, params.name, params.arguments or {})
        content = [
            TextContent(text=c.text)
            if hasattr(c, "text")
            else ImageContent(data=c.data, mime_type=c.mime_type)
            for c in reply.content
        ]
        return CallToolResult(content=content, structured_content=reply.structured, is_error=not reply.ok)

    server = Server(
        "pcx",
        version="0.2.0",
        instructions=INSTRUCTIONS,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        get_tool_input_schema=lambda name: next((t.input_schema for t in tools if t.name == name), None),
    )
    manager = StreamableHTTPSessionManager(server, stateless=True, json_response=True)

    async def retention():
        while True:
            try:
                await store.purge(settings.retention_days)
            except Exception:
                log.warning("retención falló", exc_info=True)
            await asyncio.sleep(3600)

    @asynccontextmanager
    async def lifespan(app):
        purge = None
        try:
            await store.start(settings)
            purge = asyncio.create_task(retention())
            async with manager.run():
                yield
        finally:
            if purge:
                purge.cancel()
                await asyncio.gather(purge, return_exceptions=True)
            for conn in list(gateway.connections.values()):
                conn.fail_pending()
                with suppress(Exception):
                    await conn.websocket.close(code=1012)  # «service restart»: el nodo reconecta con backoff
            await bus.close()
            await store.close()

    async def health(request):
        try:
            await asyncio.wait_for(store.ping(), 3)
        except Exception:
            log.warning("health: la DB no responde", exc_info=True)
            return JSONResponse({"ok": False, "db": False}, status_code=503)
        return JSONResponse(
            {"ok": True, "service": "pcx-gateway", "connected_nodes": len(gateway.connections)}
        )

    async def api_nodes(request):
        identity = request.state.identity
        node = dict(await store.node(identity.node_id))
        node.pop("token_hash")
        node.pop("world")
        node.update(
            connected=gateway.connected(identity.node_id) is not None, cursor=await store.cursor(node["id"])
        )
        return JSONResponse({"nodes": [node]})

    async def api_history(request):
        identity = request.state.identity
        q = request.query_params
        types = [t for t in q.get("types", "").split(",") if t]
        try:
            since, until = parse_time(q.get("since")), parse_time(q.get("until"))
            limit = max(1, min(int(q.get("limit", 50)), 200))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        data = await store.history(
            identity.space_id, query=q.get("q"), since_ns=since, until_ns=until, types=types, limit=limit
        )
        for match in data["matches"]:
            match["last_seen"] = iso(match["last_seen_ns"])
            if match["has_thumbnail"]:
                match["thumbnail_url"] = f"/api/objects/{match['object_id']}/thumbnail"
        return JSONResponse(ns_to_str(data))

    async def api_thumbnail(request):
        identity = request.state.identity
        row = await store.thumbnail(identity.space_id, request.path_params["object_id"])
        if row is None or row.thumb is None:
            return JSONResponse({"error": "Sin miniatura"}, status_code=404)
        return Response(row.thumb, media_type=row.thumb_mime, headers={"Cache-Control": "private, max-age=5"})

    async def api_node_thumbnail(request):
        """Nodo → nube: último recorte de un objeto. Auth con el token del nodo (no el de usuario)."""
        token = bearer(request)
        node = await store.node_by_token(token) if token else None
        if node is None:
            return JSONResponse({"error": "Token de nodo inválido"}, status_code=401)
        body = await request.body()
        mime = next((m for magic, m in IMAGE_MAGIC.items() if body.startswith(magic)), None)
        if mime is None or len(body) > settings.max_thumbnail:
            return JSONResponse({"error": "Se espera PNG, JPEG o WebP de 512 KiB o menos"}, status_code=415)
        label = request.query_params.get("label")
        await store.put_thumbnail(node["space_id"], request.path_params["object_id"], body, mime, label)
        return Response(status_code=204)

    async def api_confirmations(request):
        identity = request.state.identity
        rows = await store.confirmations(identity.space_id, request.query_params.get("status"))
        return JSONResponse(ns_to_str({"confirmations": rows}))

    async def api_confirm(request):
        """Consola → nodo. Solo token de consola; el nodo vuelve a verificar id + digest (ConfirmAction)."""
        identity = request.state.identity
        try:
            body = await request.json()
            approve, digest_ = body["approve"], body["action_digest"]
            operator = str(body.get("operator") or "console")[:64]
            if not isinstance(approve, bool) or not isinstance(digest_, str):
                raise TypeError
        except (ValueError, KeyError, TypeError):
            return JSONResponse(
                {"error": "Se espera {approve: bool, action_digest: str, operator?}"}, status_code=400
            )
        try:
            data = await gateway.confirm(identity, request.path_params["cid"], approve, digest_, operator)
        except Rejected as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status)
        return JSONResponse(data, status_code=202)

    async def node_socket(ws: WebSocket):
        await ws.accept()
        conn = None
        heartbeat = None
        node_id = None
        try:
            hello = await receive_cloud(ws, 10, settings.max_ws_frame)
            if not isinstance(hello, Hello):
                raise ValueError("Se espera hello")  # noqa: TRY004 (error de protocolo, no de tipo)
            version = gateway.pick_version(hello.contract_versions)
            if version is None:
                rejected = HelloRejected(reason="version", supported_versions=list(SUPPORTED_VERSIONS))
                await ws.send_text(msgspec.json.encode(rejected).decode())
                await ws.close(code=4426, reason="Versión de contrato no soportada")
                return
            if not await store.authenticate_node(hello.node_id, hello.token):
                await ws.send_text(msgspec.json.encode(HelloRejected(reason="auth")).decode())
                await ws.close(code=4401, reason="Token de nodo inválido")
                return
            node_id = hello.node_id
            conn = Connection(ws, contract_version=version, stream_id=hello.stream_id)
            old = gateway.connections.get(node_id)
            gateway.connections[node_id] = conn
            if old:
                old.fail_pending()
                with suppress(Exception):
                    await old.websocket.close(code=4001, reason="Nueva conexión del nodo")
            last_stream_id, last_cursor = await store.resume_point(node_id)
            await conn.send(
                Welcome(
                    session_id=conn.session_id,
                    contract_version=version,
                    last_stream_id=last_stream_id,
                    last_cursor=last_cursor,
                )
            )
            await store.seen(node_id)

            async def send_heartbeats():
                while True:
                    await asyncio.sleep(settings.heartbeat_s)
                    await conn.send(Heartbeat(t_ms=time.time() * 1000, cursor=await store.cursor(node_id)))

            heartbeat = asyncio.create_task(send_heartbeats())
            while True:
                message = await receive_cloud(ws, settings.node_idle_s, settings.max_ws_frame)
                if gateway.connections.get(node_id) is not conn:
                    break
                if isinstance(message, StateSnapshot):
                    await gateway.accept_snapshot(conn, node_id, message)
                    conn.ready = True
                elif isinstance(message, Heartbeat):
                    await store.seen(node_id)
                elif not conn.ready:
                    raise ValueError("Se requiere state_snapshot después de welcome")
                elif isinstance(message, HistoryGap):
                    await gateway.accept_gap(conn, node_id, message)
                elif isinstance(message, EventBatch):
                    await gateway.accept_events(conn, node_id, message)
                elif isinstance(message, ReplayDone):
                    conn.replaying = False
                    await store.set_history_complete(node_id, message.history_complete)
                elif isinstance(message, ToolResult):
                    future = conn.pending.get(message.call_id)
                    if future and not future.done():
                        future.set_result(message)
                else:
                    raise ValueError(f"Mensaje no esperado del nodo: {type(message).__name__}")
        except WebSocketDisconnect:
            pass
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError, TimeoutError) as exc:
            log.info("nodo %s: cierre por protocolo: %s", node_id, exc)
            with suppress(Exception):
                await ws.close(code=4400, reason="Protocolo inválido o heartbeat vencido")
        except OSError:
            pass
        except RuntimeError:
            log.debug("WebSocket cerrado", exc_info=True)
        finally:
            if heartbeat:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
            if conn:
                conn.fail_pending()
            if node_id and gateway.connections.get(node_id) is conn:
                del gateway.connections[node_id]
                with suppress(Exception):
                    await bus.publish(node_id)

    app = Starlette(
        lifespan=lifespan,
        routes=[
            Route("/health", health),
            Route("/api/nodes", api_nodes),
            Route("/api/history", api_history),
            Route("/api/objects/{object_id}/thumbnail", api_thumbnail),
            Route("/api/node/objects/{object_id}/thumbnail", api_node_thumbnail, methods=["PUT"]),
            Route("/api/confirmations", api_confirmations),
            Route("/api/confirmations/{cid}", api_confirm, methods=["POST"]),
            WebSocketRoute("/node", node_socket),
            Route("/mcp", MCPRoute(manager), methods=["GET", "POST", "DELETE"]),
            Route("/mcp/", MCPRoute(manager), methods=["GET", "POST", "DELETE"]),
        ],
    )
    app.state.gateway = gateway
    app.add_middleware(Guard, store=store, max_body=settings.max_http_body)
    return app
