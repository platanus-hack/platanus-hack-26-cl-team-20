"""Single-process Starlette application; /node owns connections, /mcp uses contract tools."""

import asyncio
import logging
import time
from contextlib import asynccontextmanager

import msgspec
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp_types import CallToolResult, ImageContent, ListToolsResult, TextContent, Tool
from pcx_contracts.cloud import CloudMessage, EventBatch, Heartbeat, Hello, StateSnapshot, ToolResult, Welcome
from pcx_contracts.tools import mcp_tools
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from .config import Settings
from .gateway import Connection, Gateway
from .pubsub import MemoryBus, RedisBus
from .store import Store

log = logging.getLogger(__name__)
INSTRUCTIONS = """pcx da contexto físico de un espacio real. El nodo local es dueño del estado reportado.
Empieza por scene o look. Coordenadas table_mm: mm, origen arriba izquierda, x derecha, y abajo.
Usa anchor.object_id para que los widgets sigan objetos; usa textos cortos y legibles (12–20 mm).
project crea/reemplaza widgets por id; remove limpia tus widgets al terminar. ask proyecta opciones y
espera el toque de la persona. events_wait(cursor) hace long-poll; conserva el cursor devuelto, incluso
cuando no haya eventos. No hay push MCP. run_flow ejecuta transiciones localmente sin latencia de nube.
scene refleja StateSnapshot + eventos reportados; snapshot_cursor y reported_cursor indican su frescura. Si necesitas imagen usa look.
No presupongas que un ToolCall produjo un efecto: espera ToolResult y estado reportado del nodo.
home y flujos con HomeCall requieren confirmación cuando requires_confirmation=true: quedan pendientes
sin ejecutar. No omitas esa protección para eludir la aprobación de una persona. Un timeout de una acción
puede dejar un efecto en el nodo: consulta scene antes de reintentar; conserva request_id al reintentar.
"""


async def receive_cloud(ws, timeout):
    frame = await asyncio.wait_for(ws.receive(), timeout)
    if frame["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(frame.get("code", 1000))
    raw = frame.get("text") if frame.get("text") is not None else frame.get("bytes")
    if raw is None:
        raise ValueError("Se requiere JSON en un frame text o binary")
    return msgspec.json.decode(raw, type=CloudMessage)


class BearerAuth:
    def __init__(self, app, store):
        self.app, self.store = app, store

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and (
            scope["path"].startswith("/mcp") or scope["path"].startswith("/api/")
        ):
            request = Request(scope)
            scheme, _, token = request.headers.get("authorization", "").partition(" ")
            identity = await self.store.identity(token) if scheme.lower() == "bearer" and token else None
            if identity is None:
                await JSONResponse(
                    {"error": "Bearer de usuario inválido"},
                    status_code=401,
                    headers={"WWW-Authenticate": 'Bearer realm="pcx"'},
                )(scope, receive, send)
                return
            scope.setdefault("state", {})["identity"] = identity
        await self.app(scope, receive, send)


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
    tools = [Tool.model_validate(tool) for tool in mcp_tools()]

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
        version="0.1.0",
        instructions=INSTRUCTIONS,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        get_tool_input_schema=lambda name: next((t.input_schema for t in tools if t.name == name), None),
    )
    manager = StreamableHTTPSessionManager(server, stateless=True, json_response=True)

    @asynccontextmanager
    async def lifespan(app):
        try:
            await store.start(settings)
            async with manager.run():
                yield
        finally:
            for conn in list(gateway.connections.values()):
                conn.fail_pending()
                await conn.websocket.close(code=1012)
            await bus.close()
            await store.close()

    async def health(request):
        return JSONResponse(
            {"ok": True, "service": "pcx-gateway", "connected_nodes": len(gateway.connections)}
        )

    async def api_nodes(request):
        identity = request.state.identity
        node = dict(await store.node(identity.node_id))
        node.pop("token_hash")
        node.pop("world")
        node.update(
            connected=bool((conn := gateway.connections.get(identity.node_id)) and conn.ready),
            cursor=await store.cursor(identity.node_id),
        )
        return JSONResponse({"nodes": [node]})

    async def node_socket(ws: WebSocket):
        await ws.accept()
        conn = None
        heartbeat = None
        node_id = None
        try:
            hello = await receive_cloud(ws, 10)
            if not isinstance(hello, Hello) or not await store.authenticate_node(hello.node_id, hello.token):
                await ws.close(code=4401, reason="Hello/token de nodo inválido")
                return
            node_id = hello.node_id
            conn = Connection(ws)
            old = gateway.connections.get(node_id)
            if old:
                old.fail_pending()
                await old.websocket.close(code=4001, reason="Nueva conexión del nodo")
            gateway.connections[node_id] = conn
            await conn.send(Welcome(session_id=conn.session_id, last_cursor=await store.cursor(node_id)))
            await store.seen(node_id)

            async def send_heartbeats():
                while True:
                    await asyncio.sleep(settings.heartbeat_s)
                    await conn.send(Heartbeat(t_ms=time.time() * 1000, cursor=await store.cursor(node_id)))

            heartbeat = asyncio.create_task(send_heartbeats())
            while True:
                message = await receive_cloud(ws, settings.node_idle_s)
                if gateway.connections.get(node_id) is not conn:
                    break
                if isinstance(message, StateSnapshot):
                    await gateway.accept_snapshot(node_id, message)
                    conn.ready = True
                elif isinstance(message, EventBatch) and conn.ready:
                    await gateway.accept_events(node_id, message)
                elif isinstance(message, ToolResult) and conn.ready:
                    future = conn.pending.get(message.call_id)
                    if future and not future.done():
                        future.set_result(message)
                elif isinstance(message, Heartbeat):
                    await store.seen(node_id)
                else:
                    raise ValueError("Se requiere StateSnapshot después de Welcome; mensaje no esperado")
        except WebSocketDisconnect:
            pass
        except (msgspec.DecodeError, ValueError, TimeoutError):
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
                await bus.publish(node_id)

    app = Starlette(
        lifespan=lifespan,
        routes=[
            Route("/health", health),
            Route("/api/nodes", api_nodes),
            WebSocketRoute("/node", node_socket),
            Route("/mcp", MCPRoute(manager), methods=["GET", "POST", "DELETE"]),
            Route("/mcp/", MCPRoute(manager), methods=["GET", "POST", "DELETE"]),
        ],
    )
    app.state.gateway = gateway
    app.add_middleware(BearerAuth, store=store)
    return app
