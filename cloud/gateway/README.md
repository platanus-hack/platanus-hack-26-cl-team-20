# pcx gateway · Python 3.12 / uv
Desde `cloud/gateway` (sin hardware):
```sh
export PCX_USER_TOKEN="$(openssl rand -hex 32)" PCX_NODE_TOKEN="$(openssl rand -hex 32)" \
       PCX_CONSOLE_TOKEN="$(openssl rand -hex 32)"   # opcional: aprobar domótica desde la consola
uv sync --frozen
uv run python -m pcx_gateway                         # 0.0.0.0:${PORT:-9210}
# Otra terminal, con el mismo PCX_NODE_TOKEN:
uv run python tools/fake_node.py                    # --url wss://HOST/node
uv run pytest                                       # SQLite
PCX_TEST_DATABASE_URL=postgresql://USER@127.0.0.1:9211/pcx_test uv run pytest   # Postgres desechable
```
Postgres verificado: los 19 tests pasan contra Postgres 14 local (`initdb` temporal en el puerto 9211).
`DATABASE_URL` acepta Postgres (asyncpg); sin variable usa `./gateway.db` (SQLite).
`REDIS_URL` activa pub/sub Redis/Valkey; sin variable usa memoria. Arranque: create_all idempotente.
Tokens SHA-256 en DB; no hay credenciales por defecto. El par de tokens aprovisiona usuario → espacio → nodo.
`PCX_USER_ID`, `PCX_SPACE_ID`, `PCX_NODE_ID` cambian esos IDs (defaults `demo-*`); no reasigna registros existentes.
Para otros espacios, aprovisiona otro par de IDs/tokens con `Store.provision(Settings(...))`; rotación explícita en DB.
`GET /health` es público (503 si la DB no responde). Con Bearer de usuario (MCP o consola): `GET /api/nodes`,
`GET /api/history?q=llaves&since=2h&until=&types=`, `GET /api/objects/{id}/thumbnail`, `GET /api/confirmations`.
Solo consola: `POST /api/confirmations/{id}` `{approve, action_digest, operator}` → `confirm_action` al nodo.
Solo nodo (token de nodo): `PUT /api/node/objects/{id}/thumbnail?label=` con PNG/JPEG/WebP ≤ 512 KiB.
JSON hacia la consola: `*_ns` como cadena (`ns_to_str`). Ver `REVISION.md`.

Render: Blueprint `render.yaml` en la raíz del repositorio; pasos, costos y smoke en [DEPLOY.md](../../docs/DEPLOY.md).
Carga los tres tokens secretos en Render; incluye Postgres Basic 256 MB (1 GB de disco), Key Value 256 MB y web Starter en Virginia.
**Una instancia y un worker**; los sockets y llamadas pendientes viven en ese proceso. No se desplegó automáticamente.
Referencia del Blueprint: https://render.com/docs/blueprint-spec

Clientes (define `PCX_USER_TOKEN` y reemplaza `HOST`):
```sh
claude mcp add --transport http pcx https://HOST/mcp --header "Authorization: Bearer $PCX_USER_TOKEN"
codex mcp add pcx --url https://HOST/mcp --bearer-token-env-var PCX_USER_TOKEN
```
OpenCode v2, en `opencode.json` (v1: `pcx` directamente bajo `mcp`):
```json
{"mcp":{"servers":{"pcx":{"type":"remote","url":"https://HOST/mcp","headers":{"Authorization":"Bearer {env:PCX_USER_TOKEN}"},"oauth":false}}}}
```
Nodo (v2): `hello{contract_versions, stream_id, …}` → `welcome{contract_version, last_stream_id, last_cursor}` o
`hello_rejected` → `state_snapshot` → `history_gap`* → `event_batch{replay}` → `replay_done`. Replay idempotente.
`events_wait.cursor` es la posición global del gateway (sobrevive a `stream_reset`); huecos en `gap`/`gaps`.
El espejo aplica eventos reportados; envía snapshots para cambios sin evento (manos/widgets). No anticipa ToolCall.
Heartbeat ~10 s en ambos sentidos; sin mensajes 35 s se cierra WS. Reconecta con backoff tras deploy.
`ToolResult.call_id` debe coincidir; respeta `timeout_s` e idempotencia `request_id`. No se reenvían acciones al reconectar.
Timeouts: project/remove/run_flow 15 s (ack), look 20 s, home 30 s, calibrate 180 s, ask plazo +5 s.
`events_wait` filtra tipos, pagina hasta 500 y responde desde la DB aunque el nodo esté offline.
`home`: decide la `HomePolicy` del nodo; `pending` se registra con su `action_digest` y se resuelve con un toque
(`confirmation_resolved{via:touch}`) o la consola. Tool extra `history(query, since, until)`: memoria del espacio.
MCP es Streamable HTTP stateless con Bearer estático; no implementa OAuth ni push. El fake produce datos sintéticos.

## Portada (`/`), consola web (`/mesa`) e instrucciones para agentes (`/agente`)
El gateway sirve la consola estática de `src/pcx_gateway/console/` (sin build). Con `PCX_OPEN_SESSIONS=1`,
`POST /api/sessions` (público, tope de 30 por IP y hora) crea usuario + espacio + nodo con tokens propios y el
navegador entra como nodo por `/node`: `look` devuelve un cuadro de la cámara, `project`/`remove`/`ask` dibujan en la
ventana del proyector. Local: `PORT=9215 PCX_OPEN_SESSIONS=1 uv run python -m pcx_gateway` y abrir
`http://localhost:9215/mesa` en Chrome. `/` es la portada de WUI (nombre visible del producto; los identificadores
`pcx_*` no cambian) y `/agente` devuelve texto plano con los pasos para un agente; `PCX_INSTALL_URL` es la URL pública
del `install.sh` del motor local (sin ella ese paso sale marcado como pendiente). Detalle y pendientes: `ESTADO-consola.md` en la raíz.
Mirar en vivo: `POST /api/view-token` (Bearer de usuario) → token de solo lectura; página `/ver/<token>` y WebSocket
`/view` (primer mensaje `{token}`). El nodo navegador manda JPEG y mensajes `{"type":"view",…}` que el gateway reenvía
sin decodificar; guarda un solo cuadro por persona mirando. Máximo 6 por nodo.

Privacidad: `look` no se guarda por defecto. `PCX_KEEP_LOOK=1` conserva la última foto; las miniaturas dejan de servirse a las 24 h y `purge` borra sus bytes.
