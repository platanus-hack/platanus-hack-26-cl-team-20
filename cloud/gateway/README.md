# pcx gateway · Python 3.12 / uv
Desde `cloud/gateway` (sin hardware):
```sh
export PCX_USER_TOKEN="$(openssl rand -hex 32)" PCX_NODE_TOKEN="$(openssl rand -hex 32)"
uv sync --frozen
uv run python -m pcx_gateway                         # 0.0.0.0:${PORT:-9210}
# Otra terminal, con el mismo PCX_NODE_TOKEN:
uv run python tools/fake_node.py                    # --url wss://HOST/node
uv run pytest
```
`DATABASE_URL` acepta Postgres (asyncpg); sin variable usa `./gateway.db` (SQLite).
`REDIS_URL` activa pub/sub Redis/Valkey; sin variable usa memoria. Arranque: create_all idempotente.
Tokens SHA-256 en DB; no hay credenciales por defecto. El par de tokens aprovisiona usuario → espacio → nodo.
`PCX_USER_ID`, `PCX_SPACE_ID`, `PCX_NODE_ID` cambian esos IDs (defaults `demo-*`); no reasigna registros existentes.
Para otros espacios, aprovisiona otro par de IDs/tokens con `Store.provision(Settings(...))`; rotación explícita en DB.
`GET /health` es público; `GET /api/nodes` requiere Bearer de usuario y devuelve solo su nodo.

Render: crea un Blueprint apuntando a `cloud/render.yaml`, desde la raíz del repositorio.
Carga ambos tokens secretos en Render; incluye Postgres y Key Value en Virginia, web Starter.
**Una instancia y un worker**; los sockets y llamadas pendientes viven en ese proceso. No se desplegó automáticamente.
Referencia del Blueprint: https://render.com/docs/blueprint-spec

Clientes (define `PCX_USER_TOKEN` y reemplaza `HOST`):
```sh
claude mcp add --transport http pcx https://HOST/mcp --header "Authorization: Bearer $PCX_USER_TOKEN"
codex mcp add pcx --url https://HOST/mcp --bearer-token-env-var PCX_USER_TOKEN
```
OpenCode, en `opencode.json`:
```json
{"mcp":{"pcx":{"type":"remote","url":"https://HOST/mcp","headers":{"Authorization":"Bearer {env:PCX_USER_TOKEN}"},"oauth":false}}}
```
Nodo: `Hello` con token → `Welcome.last_cursor` durable → `StateSnapshot` completo → replay `EventBatch` posterior.
El snapshot NO adelanta el cursor durable del log. `seq` positivo, creciente, persistente por nodo; replay idempotente.
El espejo aplica eventos reportados; envía snapshots para cambios sin evento (manos/widgets). No anticipa ToolCall.
Heartbeat ~10 s en ambos sentidos; sin mensajes 35 s se cierra WS. Reconecta con backoff tras deploy.
`ToolResult.call_id` debe coincidir; respeta `timeout_s` e idempotencia `request_id`. No se reenvían acciones al reconectar.
Timeouts: project/remove/run_flow 15 s (ack), look 20 s, home 30 s, calibrate 180 s, ask plazo +5 s.
`events_wait` usa cursor por nodo, filtra tipos, pagina hasta 500; `cursor` avanza incluso sin coincidencias.
`home` o flujos con `requires_confirmation=true`: pendiente, auditado, sin ejecutar; no existe aún canal de confirmación.
MCP es Streamable HTTP stateless con Bearer estático; no implementa OAuth ni push. El fake produce datos sintéticos.
