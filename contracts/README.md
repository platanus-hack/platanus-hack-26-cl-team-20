# contracts · fuente única de verdad de los mensajes de pcx

Python (`msgspec.Struct`) define todo; de ahí salen JSON Schema, tools MCP, tipos TS y fixtures canónicos que Rust valida.

| Módulo (`src/pcx_contracts/`) | Qué define |
|---|---|
| `geometry` · `devices` · `world` | `PointMM`, `RectMM`, `Transform`, `Surface` · `Device` · `ObjectState`, `HandState`, `Touch`, `WorldState` |
| `widgets` | Unión por `type`: label, halo, arrow, panel, button, image, effect (+ `anchor`, `z`, `ttl_s`, `on_press`) |
| `commands` · `events` · `envelope` | Comandos (incl. `RunFlow` con `Flow`, `check_flow()`) · eventos · sobre `{type,v,seq,t_capture_ns,t_pub_ns,source,node_id,payload}` |
| `render` | core ↔ renderer: `scene`, `scene_diff`, `presented`, `stats`; `to_legacy()` → PROTOCOLO-ESCENA v0 |
| `cloud` | nodo ↔ Render (WSS): `hello`, `welcome`, `state_snapshot`, `event_batch`, `tool_call`, `tool_result`, `heartbeat` |
| `tools` | 10 tools MCP con `inputSchema` plano (sin `$ref`), `mcp_tools()` y `parse_args(tool, args)` |

## Uso y regeneración
```bash
cd contracts
uv run pytest                                # ida y vuelta json/msgpack, schema, canónicos, flujo, tools
uv run python -m pcx_contracts.export        # schema/*.json, schema/tools/*.input.json, fixtures/canonical/
./ts/generate.sh                             # ts/pcx.d.ts vía npx json-schema-to-typescript (sin instalar global)
cd rust/pcx-contracts && cargo test          # Rust re-serializa los fixtures == fixtures/canonical/
```
Python: `msgspec.json.decode(raw, type=ToRenderer)`; otros proyectos: `uv add --editable ../contracts`.
Rust: `pcx-contracts = { path = "../contracts/rust/pcx-contracts" }` (serde a mano: geometry, widgets, render).
Al cambiar un contrato: edita Python → `export` → `generate.sh` → ajusta Rust si toca render/widgets → tests.

## Convenciones
- **Marco canónico `table_mm`**: x a la derecha, y hacia abajo, origen arriba a la izquierda, en mm. Otros marcos:
  `cam:<id>` y `proj:<id>` (px), unidos por `Transform{from_frame,to_frame,kind,matrix|lut_ref,version,residual_mm}`.
- Puntos `[x, y]`, rectángulos `[x, y, w, h]`, polígonos `[[x, y], ...]`; colores `#RRGGBB[AA]`.
- Tiempos: `*_ns` = ns de época (int, envelope y mundo); `*_ms` = ms de época (float, protocolo de render).
- Uniones discriminadas por `type` (snake_case). Opcionales viajan como `null`; los defaults son iguales en Python y Rust.
- Versión: `Envelope.v` = `CONTRACT_VERSION` (1). Cambios incompatibles suben la versión; agregar campos opcionales no.
- `seq` del envelope es el cursor de `events_wait` y de `state_snapshot`/`event_batch` (reanudación tras reconectar).
- Tool `events.wait` se llama `events_wait`: Claude y OpenAI no aceptan puntos en nombres de tools.

## Cambios respecto de PROTOCOLO-ESCENA v0
`homography` → `transforms[]` (se mantiene `homography` opcional como legado); nuevo `scene_diff{rev, base_rev}`;
label `anchor:{x_mm,y_mm}` → `anchor.point_mm:[x,y]`; widgets nuevos `image` y `effect`; campos `z`, `ttl_s`, `on_press`.
Hacia el renderer la geometría llega resuelta en mm absolutos (el core resuelve `anchor.object_id`).
