# pcx-core · core del nodo local (Rust)

Actor del mundo de escritor único + log durable (SQLite) + MCP + `/render` + flujos + ZeroMQ + nube. Binario: `pcx` (crate `pcxd`).
```bash
cargo run -p pcxd -- serve            # 127.0.0.1:9200 (PCX_PORT); PCX_TOKEN (default pcx-dev-token; "" = sin auth)
cargo run -p pcxd -- mcp              # MCP stdio: proxy al daemon si responde, si no core en proceso
cargo run -p pcxd -- up               # supervisor según node/pcx.toml (`pcx` = este binario)
cargo run -p pcxd -- replay <dir>     # reinyecta objetos/toques de otro <dir>/pcx.db por ZeroMQ (demo de respaldo)
cargo test -p pcx-core -p pcxd        # unidad + links (nube/ZeroMQ/supervisor) + e2e
```
Env: `PCX_NODE_ID`, `PCX_DATA_DIR` (default: dir. de datos del SO), `PCX_SURFACE_MM=800x500`, `PCX_INGEST`, `PCX_EVENTS`,
`PCX_ZMQ=0`, `PCX_CLOUD_URL` + `PCX_NODE_TOKEN`, `PCX_HOME_POLICY` (JSON `HomePolicy`; default: nada permitido).

## Interfaces para los otros frentes
| | |
|---|---|
| Ingesta in-process | `Core::start(settings, frames)` → `core.world: WorldHandle`: `ingest(env).await`, `ingest_nowait(env)`, `set_hands(..)`, `call(\|w\| ..)` |
| Ingesta ZeroMQ PULL | `PCX_INGEST` (Unix `ipc:///tmp/pcx/ingest.sock`, Windows `tcp://127.0.0.1:9206`). `Envelope` v2 msgpack (`to_vec_named`) o JSON: `object_*`, `touch_*`, `frame_meta`, `device_changed`, `calibration_drift`, `dwell`, `widget_pressed`, o **comandos** (`set_transform`, `blank`, `project`…). Manos: `{"type":"hands","hands":[HandState]}` (extensión local). El core re-secuencia con su `seq` |
| Eventos ZeroMQ PUB | `PCX_EVENTS` (`…/events.sock` / `:9207`): multipart `[type, Envelope msgpack]`; SUBSCRIBE por prefijo de tipo |
| `FrameSource` | `trait FrameSource { fn cameras() -> Vec<String>; fn latest(camera_id: Option<&str>, annotate: bool) -> Option<Frame> }` (`frames.rs`). Hoy `SyntheticFrameSource` dibuja el mundo; captura pasa la suya en `Core::start(.., Some(Arc::new(..)))` |
| WS `/render` | `scene` al conectar, luego `scene_diff{rev,base_rev}` y `blank{request_id,ms,reason}` (JSON de `pcx_contracts::ToRenderer`). Recibe `presented`, `stats`, `blanked` (→ evento `blanked` con `projector_id`). Si el renderer se atrasa recibe otro `scene` |
| `/mcp` | Streamable HTTP (rmcp), `Authorization: Bearer $PCX_TOKEN`. Las 10 tools de `contracts/schema/tools.json` |
| HTTP | `/health` (abierto) · `/state`, `/debug/touch {widget_id}\|{x_mm,y_mm,hold_ms?}`, `/debug/object`, `/debug/look.png`, `/console/confirm` (`ConfirmAction`), `/catalog` GET/POST (Bearer) |
| Calibración | manda `set_transform{request_id, transform}` por la ingesta: se versiona en SQLite (`calibrations`) y va al renderer en el próximo diff. `calibrate` (tool) queda encolada en `/state` |

## Persistencia (`<data_dir>/pcx.db`, migraciones por `user_version`)
`events` (log append-only, `seq` y `stream_id` durables: `events_wait`, replay a la nube, demos) · `world` (snapshot cada ≤250 ms y al
apagar; al arrancar se restauran objetos, widgets, transformaciones, dispositivos) · `catalog` (nombre, prompt, rutas de recortes) ·
`calibrations` (versionadas; LUT como archivos) · `flows` · `devices` · `audit` (tool calls: origen, call_id, request_id, ms) ·
`requests` (idempotencia por `request_id`, 10 min, sobrevive reinicios) · `confirmations` + `meta.home_policy`.

## Semántica
Hit-test en mm (tocables > `z` > botones). `dwell` llena `progress` y emite `widget_pressed` a los `dwell_ms`; `pinch`/`api`/`shadow`
al bajar. `ask` → panel + botones `ask-N-bI`. Flujos: acciones con `request_id` derivado `<rid>/<estado>/<i>`, el evento gana al
temporizador, máx. 8 inmediatas. `home_call`: la `HomePolicy` decide; `confirm` proyecta `pcx:confirm:<id>` (solo dwell/pinch) o
`/console/confirm`; vence → `expired`. Nube: hello → welcome → state_snapshot → [history_gap] → replay → replay_done → en vivo;
`tool_result.call_id` = el del `tool_call`; heartbeat 10 s; corte a los 35 s sin mensajes; backoff 0,5→30 s.

## Pendientes
Tipos de `proto.rs` → `pcx-contracts` · ejecutor real de Home Assistant (hoy `failed` stub) · reanudar flujos tras reinicio ·
deduplicar `ask` en curso con el mismo `request_id` · Windows: apagado con CTRL_BREAK en vez de `kill()`.
