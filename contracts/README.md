# contracts · fuente única de verdad de los mensajes de pcx

Python (`msgspec.Struct`) define todo; de ahí salen JSON Schema, tools MCP, tipos TS y fixtures canónicos que Rust valida.

| Módulo (`src/pcx_contracts/`) | Qué define |
|---|---|
| `geometry` · `devices` · `world` | `PointMM`, `RectMM`, `Transform` (homography\|lut), `select_projector_transform()` · `Device` · `WorldState` |
| `home` | `HomePolicy` (en el nodo), `decide()`, `action_digest()`, `HomeCallResult`, `ConfirmAction` (consola → nodo) |
| `widgets` | Unión por `type`: label, halo, arrow, panel, button, image, effect (+ `anchor`, `z`, `ttl_s`, `on_press`) |
| `commands` · `events` · `envelope` | Comandos (incl. `RunFlow` con `Flow`, `check_flow()`) · eventos · sobre `{type,v,seq,t_capture_ns,t_pub_ns,source,node_id,payload}` |
| `render` | core ↔ renderer: `scene`, `scene_diff`, `blank`, `pattern`, `pattern_clear` → ; ← `presented`, `stats`, `blanked`, `pattern_shown`, `resync`; `to_legacy()` → v0 |
| `cloud` | nodo ↔ Render (WSS): `hello`, `welcome`, `hello_rejected`, `state_snapshot`, `history_gap`, `event_batch`, `replay_done`, `tool_call`, `tool_result`, `heartbeat`, `confirm_action` (nube → nodo) |
| `tools` | 10 tools MCP con `inputSchema` plano (sin `$ref`), `mcp_tools()` y `parse_args(tool, args)` |

## Uso y regeneración
```bash
cd contracts
uv run pytest                                # ida y vuelta json/msgpack, schema, canónicos, flujo, tools
uv run python -m pcx_contracts.export        # schema/*.json, schema/tools/*.input.json, fixtures/canonical/
./ts/generate.sh                             # ts/pcx.d.ts vía npx json-schema-to-typescript (sin instalar global)
cargo test -p pcx-contracts                 # Rust re-serializa los fixtures == fixtures/canonical/ (tests/fixtures.rs, tests/proto.rs)
```
Python: `msgspec.json.decode(raw, type=ToRenderer)`; otros proyectos: `uv add --editable ../contracts`.
Rust: `pcx-contracts = { path = "../contracts/rust/pcx-contracts" }` (serde a mano: geometry, widgets, render, world, events,
commands, envelope, home, cloud; `pcx-core::proto` solo re-exporta y agrega msgpack y args de tools).
Al cambiar un contrato: edita Python → `export` → `generate.sh` → ajusta Rust (`contracts/rust/pcx-contracts`) → tests.

## Convenciones
- **Marco canónico `table_mm`**: x a la derecha, y hacia abajo, origen arriba a la izquierda, en mm. Otros marcos:
  `cam:<id>` y `proj:<id>` (px), unidos por `Transform` = `homography{matrix[9]}` | `lut{lut: LutDescriptor, approx_matrix?}`.
  El renderer elige con `select_projector_transform(transforms, projector_id)`: `table_mm → proj:<id>`, LUT > homografía,
  mayor `version`, empate = la primera. `projector_matrix()` da la homografía (de la LUT su `approx_matrix`, si no fallback).
- Puntos `[x, y]`, rectángulos `[x, y, w, h]`, polígonos `[[x, y], ...]`; colores `#RRGGBB[AA]`.
- Tiempos: `*_ns` = ns de época (int en Python, Rust y el cable nodo↔nube); `*_ms` = ms de época (float, render).
  **JSON hacia clientes TS**: los `*_ns` viajan como **cadena decimal** (`ns_to_str()` en el gateway; TS: `BigInt(s)`).
  Elegido sobre `*_ms` float porque es exacto (un float de ms pierde los ns y un `number` de ns pierde ±64 ns), no
  duplica campos y Python/Rust no cambian. `ts/pcx.d.ts` sale de `schema/pcx.ts.json` (draft-07 + `*_ns: string`).
- Uniones discriminadas por `type` (snake_case). Opcionales viajan como `null`; los defaults son iguales en Python y Rust.
- Versión: `Envelope.v` = `CONTRACT_VERSION` (2), obligatorio; se rechaza si no está en `SUPPORTED_VERSIONS` o si
  `type` ≠ `payload.type`. Se negocia en `hello.contract_versions` → `welcome.contract_version` (o `hello_rejected`).
- Reconexión: `seq` es monótono por `stream_id` y **lo renumera el core** (el `seq` de captura/manos/objetos solo vale en su proceso). `state_snapshot` = estado recuperado; el historial se reenvía como
  `event_batch{replay:true}` desde `last_cursor`, con `history_gap{from,to}` si falta algo y `replay_done{history_complete}`.
  Retención del log: `hello.retention{max_events, max_age_s}` (por defecto 10 000 eventos / 1 h).
- Idempotencia: todo comando lleva `request_id` (estable entre reintentos; el nodo deduplica 10 min). `ToolCall.call_id`
  es el intento de transporte; `tool_result` lleva ambos. `ask_answered`, `home_call_done`, `blanked`… repiten el `request_id`.
- Domótica: la `HomePolicy` del nodo decide allow/confirm/deny; nada del que llama la salta. `confirm` → `pending{confirmation_id,
  action_digest}`; se resuelve solo por toque físico en el botón reservado `pcx:confirm:<id>` o `ConfirmAction` de la consola.
- `run_flow`: cada transición tiene `on` **o** `after_s ≥ 0`; el evento gana al temporizador en el mismo tick; máx.
  8 transiciones inmediatas (`after_s: 0`) seguidas; `check_flow` corre en todo decode (y en `parse_args`).
- Restricciones iguales en Python, Rust y los dos schemas; `fixtures/invalid/cases.json` las prueba en los cuatro.
- Tool `events.wait` se llama `events_wait`: Claude y OpenAI no aceptan puntos en nombres de tools.

## Cambios respecto de PROTOCOLO-ESCENA v0
`homography` → `transforms[]` (se mantiene `homography` opcional como legado); nuevo `scene_diff{rev, base_rev}`;
label `anchor:{x_mm,y_mm}` → `anchor.point_mm:[x,y]`; widgets nuevos `image` y `effect`; campos `z`, `ttl_s`, `on_press`.
Hacia el renderer la geometría llega resuelta en mm absolutos (el core resuelve `anchor.object_id`).

## Cambios v1→v2
Todos rompen compatibilidad salvo los marcados (+).
- `Envelope.v` obligatorio y `= 2`; `type` debe coincidir con `payload.type`. `hello` exige `contract_versions`, `stream_id`,
  `log_first_cursor`, `log_last_cursor`; `welcome` exige `contract_version`; `state_snapshot`/`event_batch` exigen `stream_id`.
- `request_id` obligatorio en todos los comandos (tools mutables y `ask`); `ask_answered.request_id` y `flow_state_changed.request_id` obligatorios.
- `home_call`: se quitó `requires_confirmation` (ahora se rechaza). Resultado = `HomeCallResult` (`done|pending|denied|failed`).
- `Transform`: unión por `kind`. `homography` exige `matrix[9]`; `lut` exige `lut{format,width,height,units,sampling,grid_step,path,sha256}`
  (+ `approx_matrix` opcional). Se quitó `lut_ref`. Rust: `Transform` es `enum`; `projector_matrix(ts, projector_id)` pide proyector.
- Validación estricta: puntos exactamente `[x,y]`, rects `[x,y,w,h]`, polígonos ≥ 3, colores `#RRGGBB[AA]`, progress/opacity en [0,1],
  `homography` legado de 9. Rust ahora rechaza lo que antes aceptaba.
- `run_flow` rechaza flujos vacíos, `initial` inexistente, tiempos negativos, `on`+`after_s` juntos y ciclos inmediatos; tool `destructiveHint: true`.
- `ObjectState.since/last_seen` → `since_ns/last_seen_ns`. TS: todo `*_ns` es `string`.
- (+) Nuevos: `blank`/`blanked` (comando, render y evento), `history_gap`, `replay_done`, `hello_rejected`, `confirmation_requested/resolved`,
  `home_call_done`, `HomePolicy`, `ConfirmAction`, `EventsWaitResult`, `ToolResult.request_id`. inputSchema MCP conserva `null`.

## Cambios v2→v2.1
Aditivos (`Envelope.v` sigue en 2) salvo el marcado (!). Rust: todos los tipos viven ya en `pcx-contracts`.
- Render: `pattern{pattern_id, image:{path|data_uri}, until_ms?}` (pantalla completa, px nativos, sin warp ni widgets) y
  `pattern_clear{}` →; ← `pattern_shown{pattern_id, t_present_ms}` y `resync{have_rev}` (el core responde con `scene`).
- Eventos: `hands_gone{t_capture_ns, episode_start_ns?, last_hand_ns?}`; `Trigger` suma `rest` (apoyo).
- `FrameMeta` suma `segment` (anillo `pcx-shm`), `format` (`bgr8`|`rgb8`|`gray8`, def. `bgr8`) y `stride` (def. `w*canales`).
- (!) `ConfirmAction` es `CloudMessage` (nube → nodo): `{confirmation_id, approved, by: console|touch, action_digest, operator?}`;
  `approve` → `approved`. El digest sigue obligatorio. El API HTTP de consola del gateway no cambia.
- `Envelope.seq`: documentado que el core lo renumera al ingerir.
