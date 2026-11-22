---
name: pcx-extender
description: "Extender pcx por dentro: agregar un worker de percepción (detector de objetos, manos, lo que vea una cámara), un adaptador de cámara o dispositivo nuevo, o una tool MCP nueva. Cubre el sobre Envelope de los contratos, el bus ZeroMQ de ingesta y eventos, el lector del anillo de memoria compartida pcx-shm, el trait FrameSource, el supervisor pcx.toml, dónde va cada cosa en el repo, cómo regenerar contratos (Python a JSON Schema a Rust y TS) y cómo testear. Úsala siempre que haya que tocar node/crates, contracts, cloud/gateway o renderer para sumar una capacidad a pcx, conectar un sensor o cámara, publicar eventos al nodo desde otro proceso, o cuando una tool, evento o comando todavía no exista."
license: MIT
compatibility: Requiere el repo de pcx clonado, Rust (cargo) y uv.
metadata:
  author: pcx
  version: "0.1.0"
---

# pcx-extender: sumar capacidades a pcx

Antes de tocar nada, ubica tu cambio. Casi todo cabe en una de tres formas, de menor a mayor costo:

| Quiero… | Forma | Toca contratos |
|---|---|---|
| Que el nodo sepa algo nuevo del mundo (objetos, toques, manos) | **Worker de percepción**: proceso aparte que publica eventos existentes | No |
| Una cámara u otro origen de cuadros | **Adaptador** en `pcx-capture` | No |
| Que los agentes puedan pedir algo nuevo | **Tool MCP** | Sí |

Si lo que necesitas es un look visual, es `pcx-efectos`; si es un comportamiento interactivo, es `pcx-flujos`. Extiende el nodo solo cuando ninguna de las dos alcanza.

## Mapa del repo

| Ruta | Qué es |
|---|---|
| `contracts/src/pcx_contracts/` | **Fuente única** de mensajes (Python, `msgspec`): `widgets`, `commands`, `events`, `envelope`, `tools`, `home`, `render`, `cloud`, `world`, `devices`, `geometry` |
| `contracts/schema/`, `contracts/fixtures/` | Generados: JSON Schema, `tools.json`, fixtures canónicos |
| `contracts/rust/pcx-contracts/` | Los mismos tipos en Rust (serde a mano) |
| `contracts/ts/pcx.d.ts` | Tipos TS generados |
| `node/crates/pcxd` | Binario `pcx`: `serve`, `mcp`, `up`, `replay` |
| `node/crates/pcx-core` | Actor del mundo, tools MCP (`tools.rs`), HTTP (`server.rs`), bus (`bus.rs`), flujos, SQLite, nube |
| `node/crates/pcx-shm` | Anillo de memoria compartida para cuadros |
| `node/crates/pcx-capture` | Fuentes de cuadros → anillo + `frame_meta` |
| `node/crates/pcx-hands`, `pcx-objects`, `pcx-calib` | Percepción y calibración (bibliotecas) |
| `renderer/` | Compositor del proyector y efectos WGSL |
| `cloud/gateway/` | MCP remoto, WSS de nodos, tool `history` |
| `node/pcx.toml` | Procesos que lanza y vigila `pcx up` |
| `integrations/` | Este plugin y las skills |

Un solo workspace Rust en la raíz (`Cargo.toml`, `target/`). `main` siempre debe correr: ramas cortas.

## El sobre (`Envelope`)

Todo evento o comando que cruza un proceso viaja así (`contracts/src/pcx_contracts/envelope.py`):

```json
{"type": "object_moved", "v": 2, "seq": 17, "t_capture_ns": 1795231660101000000, "t_pub_ns": 1795231660119000000,
 "source": "perception-objetos", "node_id": "mesa-1",
 "payload": {"type": "object_moved", "object_id": "taza", "center_mm": [150, 370]}}
```

- `v` es obligatorio y vale 2. `type` debe ser igual a `payload.type`. Si no, se rechaza.
- `seq`: pon el tuyo; **el core lo renumera** al ingerir. El de `events_wait` es el del core.
- `t_capture_ns`: ns de época del cuadro que originó el evento. Pásalo siempre que venga de una cámara: es lo que permite medir latencia y elegir fotos limpias. `null` si no aplica.
- `source`: nombre de tu proceso (`perception-manos`, `capture-cam0`).
- Tiempos `*_ns` son enteros de ns de época; `*_ms` son float de ms (solo render).
- Coordenadas en `table_mm`. Un worker que detecta en px de cámara debe convertir con el `Transform` `cam:<id> → table_mm` de `scene.transforms`.

## 1. Worker de percepción

Proceso aparte, en cualquier lenguaje, que **lee cuadros** del anillo y **publica eventos** por ZeroMQ. No toca el core.

### Publicar: ingesta (PUSH → PULL del core)

| | Unix | Windows |
|---|---|---|
| `PCX_INGEST` | `ipc:///tmp/pcx/ingest.sock` | `tcp://127.0.0.1:9206` |
| `PCX_EVENTS` | `ipc:///tmp/pcx/events.sock` | `tcp://127.0.0.1:9207` |

Conecta un socket PUSH a `PCX_INGEST` y manda un `Envelope` por mensaje, en **msgpack con nombres de campo** (`rmp_serde::to_vec_named`, `msgspec.msgpack`) o en JSON. El core acepta:

- Eventos: `object_appeared`, `object_moved`, `object_removed`, `touch_down`, `touch_move`, `touch_up`, `widget_pressed`, `dwell`, `calibration_drift`, `hands_gone`, `blanked`, `device_changed`, `frame_meta`.
- Comandos: `set_transform` (así entrega su resultado la calibración), `blank`, `project`, etc.
- Manos (extensión local, sin sobre): `{"type": "hands", "hands": [HandState, …]}`.

Otros tipos se ignoran en silencio. Un mensaje inválido solo deja un `warn` en el log del core (`ingesta: mensaje inválido`): mira ese log mientras desarrollas.

Lee siempre `PCX_INGEST` del entorno en vez de fijar el default: el README de `pcx-capture` documenta otro puerto para Windows (9225) que el del core (9206).

### Escuchar: eventos (SUB ← PUB del core)

Conecta un SUB a `PCX_EVENTS`. Cada mensaje es multipart `[tipo, Envelope msgpack]`; suscríbete por prefijo de tipo (`object_`, `blanked`, `""` para todo). PUB descarta si vas lento: no es una cola durable (para eso está el log de `events_wait`).

Plantilla que funciona tal cual: [assets/worker_ejemplo.py](assets/worker_ejemplo.py) (`uv run --with pyzmq python worker_ejemplo.py`).

### Leer cuadros: anillo `pcx-shm`

La captura escribe cada cámara en un segmento de memoria compartida `pcx.cam.<id>` (un escritor, muchos lectores, el último cuadro gana, seqlock por slot). La captura manda al core un `frame_meta{camera_id, seq, t_capture_ns, w, h, segment, format, stride}` por cuadro, pero el core **no lo republica**: lo guarda como último cuadro de esa cámara y no sale por el PUB de eventos ni por `events_wait`. Un worker no se entera de cuadros nuevos por el bus: espera en el anillo con `wait_newer`.

```rust
use std::time::Duration;
use pcx_shm::RingReader;

let mut ring = RingReader::open("cam0", Duration::from_secs(5))?;
let (mut buf, mut last) = (Vec::new(), 0u64);
loop {
    if let Some(meta) = ring.wait_newer(last, Duration::from_millis(500), &mut buf)? {
        last = meta.seq;                       // meta: seq, t_capture_ns, w, h, stride, format (bgr8 por defecto)
        procesar(&buf[..meta.nbytes()], &meta); // publica con t_capture_ns = meta.t_capture_ns
    }
    if ring.closed() { break; }
}
```

También `latest()`, `latest_into(&mut buf)` y `with_latest(|meta, bytes| …)` (cero copias, validado por seqlock). Desde otro lenguaje, el layout binario está documentado en la cabecera de `node/crates/pcx-shm/src/lib.rs` (cabecera global de 64 bytes con magic `PCXRING1`, 64 bytes por slot, datos alineados a 4096; todo `u64` little-endian). `pcx-capture peek --id cam0` muestra fps, tamaño y edad del último cuadro.

### Fotos limpias

La percepción de objetos necesita cuadros sin luz proyectada. Manda el comando `blank{request_id, ms, reason}` por la ingesta (1–2000 ms; ~300 basta): el renderer apaga la salida y el core publica `blanked{request_id, t_start_ms, t_end_ms}`. Usa cuadros con `t_capture` dentro del intervalo, descontando la latencia proyector→cámara (~205 ms medidos). `hands_gone` avisa de que no hay manos tapando.

### Dentro del proceso del core

Si tu percepción es una biblioteca Rust, puedes saltarte ZeroMQ: `Core::start(settings, frames)` devuelve `core.world` (`WorldHandle`) con `ingest(env).await`, `ingest_nowait(env)` y `set_hands(..)`. Así se cablean `pcx-hands` (`HandsEngine::process` → `HandsEvent::to_contract()`) y `pcx-objects` (`Tracker::update(&CleanFrame)` devuelve un `Report`; sus `report.events` son `ObjectEvent`, y cada uno se convierte con `.to_contract()`).

### Registrarlo en el supervisor

```toml
# node/pcx.toml
[process.mi-worker]
cmd = ["mi-worker", "--camera", "cam0"]
restart = "always"          # o "never"
enabled = true
# health = "http://127.0.0.1:9230/health"   # opcional: URL que debe responder 200
```

`pcx up` lanza y vigila lo que esté en `node/pcx.toml`. Las variables de `[env]` llegan a todos los procesos.

## 2. Adaptador de cámara o dispositivo

### Cámara (fuente de cuadros)

En `node/crates/pcx-capture/src/sources/`. La regla está en el propio `mod.rs`: *una función `spawn(…, tx)` y una rama en `open_source`*.

1. Nuevo módulo `sources/<nombre>.rs` con `pub fn spawn(…, tx: LatestTx)`: corre en su hilo o tarea y hace `tx.put(Frame { img, t_capture_ns })` por cuadro. Termina cuando el receptor se suelta.
2. Variante en `enum SourceSpec`, su texto en `FromStr` (`<nombre>:<arg>`) y en `adapter()`.
3. Rama en `open_source`.
4. Si arrastra dependencias nativas, ponlo detrás de una feature (como `uvc`).

La bomba de `pcx-capture` hace el resto: redimensiona, escribe el anillo `pcx.cam.<id>` en `bgr8`, y emite `device_changed` (online/offline) y `frame_meta`.

Contexto real: `uvc` (nokhwa) no abre la Logitech C920 en macOS (bug B6 de `docs/pruebas/hardware-manu/01-smoke.md`). El arreglo pendiente es justo un adaptador nativo por SO (AVFoundation, Media Foundation, V4L2).

### Que `look` vea tu cámara: `FrameSource`

```rust
// node/crates/pcx-core/src/frames.rs
pub trait FrameSource: Send + Sync {
    fn cameras(&self) -> Vec<String>;
    fn latest(&self, camera_id: Option<&str>, annotate: bool) -> Option<Frame>;   // None = la cenital principal
}
```

`Frame { camera_id, data (PNG o JPEG ya codificado), mime_type, w, h, t_capture_ns, annotated }`. Debe responder en pocos ms: lee el último cuadro del anillo, no captures ahí. Se entrega en `Core::start(settings, Some(Arc::new(MiFuente)))`; con `None` el core usa `SyntheticFrameSource`, que dibuja el estado del mundo (cámara `sim`).

(Ojo: `pcx-hands` define otro trait con el mismo nombre, `FrameSource::next_frame()`, para alimentar su motor. No son intercambiables.)

### Otros dispositivos

Un dispositivo se anuncia con el evento `device_changed{device{id, kind, adapter, caps, status, meta}}`; queda en `scene` (con `include_devices`) y en la tool `devices`. `kind` hoy admite `camera`, `projector`, `phone`, `home_assistant`, `midi`: uno nuevo es un cambio de contrato.

## 3. Tool MCP nueva

Es un cambio de contrato: se propaga a Python, schema, Rust, TS y gateway. Orden:

1. **`contracts/src/pcx_contracts/tools.py`**: struct de argumentos (`msgspec.Struct, kw_only=True`) y un `ToolDef` en `TOOLS` con `name`, `description`, `args`, `read_only`/`destructive`. Si la tool cambia el mundo, modela un `Command` en `commands.py` con `request_id` obligatorio.
   - Nombres solo `[a-z_]`: Claude y OpenAI rechazan puntos (por eso `events_wait`).
   - Esquemas planos: nada de uniones complejas en los argumentos; Codex soporta un subconjunto de JSON Schema.
2. **Regenerar**:
   ```bash
   cd contracts
   uv run python -m pcx_contracts.export     # schema/*.json, schema/tools.json, schema/tools/<tool>.input.json, fixtures/canonical/
   ./ts/generate.sh                          # ts/pcx.d.ts
   ```
3. **Rust, contratos**: si agregaste comandos, eventos o widgets, replica el tipo en `contracts/rust/pcx-contracts/src/` (serde a mano; mismas restricciones en `validate.rs`).
4. **Rust, core**: `node/crates/pcx-core/src/tools.rs`. La lista de tools sale sola de `contracts/schema/tools.json` (`include_str!`), pero el despacho no: agrega la rama en `Tools::dispatch`. Tools de lectura parsean sus args con un struct en `proto.rs`; las que mutan pasan por `command(tag, args)` y se aplican en `World::apply` (`world.rs`), dentro del actor. Actualiza `INSTRUCTIONS` si el agente debe saber de ella.
5. **Gateway**: `cloud/gateway/src/pcx_gateway/gateway.py`. Las tools que mutan se reenvían al nodo: agrega su plazo en `TOOL_TIMEOUTS`. Las de lectura no se reenvían solas: `scene`, `devices` y `events_wait` se responden desde el espejo y la base del gateway en ramas propias de `Gateway.dispatch`, así que una tool de lectura nueva necesita su rama ahí (o reenviarse explícitamente al nodo con su plazo). Una tool que vive solo en la nube se define ahí, como `HISTORY_TOOL`.
6. **Tests** (abajo) y **docs**: `contracts/README.md` y la skill `pcx-operar` (`integrations/skills/`, luego `integrations/sync.sh`).

El test `test_tool_schemas_are_flat_objects` (`contracts/tests/test_contracts.py`) fija la lista exacta de nombres: actualízalo.

Reglas del actor: todo cambio de estado pasa por `WorldHandle::call` (un solo escritor, sin locks). No bloquees dentro del actor; lo que espera (como `ask`) devuelve `CmdOut::Wait` y se espera afuera. Cada llamada queda en la tabla `audit`.

Lo mismo aplica a eventos, comandos y widgets nuevos: Python primero, `export`, Rust, tests. Las restricciones deben ser iguales en Python, Rust y los schemas; `contracts/fixtures/invalid/cases.json` las prueba en todos.

## Testear

```bash
cd contracts && uv run pytest                       # ida y vuelta json/msgpack, schema, canónicos, flujos, tools
cargo test -p pcx-contracts                        # Rust re-serializa los fixtures == fixtures/canonical/
cargo test -p pcx-core -p pcxd                     # actor, enlaces (nube, ZeroMQ, supervisor) y e2e con cliente MCP real
cargo test -p pcx-shm -p pcx-capture               # anillo y fuentes
cargo test -p pcx-renderer --lib --test protocol   # renderer sin GPU
cd cloud/gateway && uv run pytest                  # gateway (SQLite)
cargo test --workspace                             # todo
```

Prueba de humo a mano, sin cámara ni proyector:

```bash
# Nodo de desarrollo: --debug habilita /debug/* (apagado por defecto), con su propio token
PCX_DATA_DIR=/tmp/pcx-dev PCX_TOKEN=dev-mcp PCX_DEBUG_TOKEN=dev-debug cargo run -p pcxd -- serve --debug
curl -s localhost:9200/health
curl -s localhost:9200/state -H 'Authorization: Bearer dev-mcp'        # mundo + auditoría de las últimas tools
curl -s -X POST localhost:9200/debug/object -H 'Authorization: Bearer dev-debug' -H 'Content-Type: application/json' \
  -d '{"id": "taza", "center_mm": [110, 370], "size_mm": [80, 80]}'
curl -s -X POST localhost:9200/debug/touch -H 'Authorization: Bearer dev-debug' -H 'Content-Type: application/json' \
  -d '{"x_mm": 110, "y_mm": 370}'
curl -s localhost:9200/debug/look.png -H 'Authorization: Bearer dev-debug' -o /tmp/look.png
```

`/debug/*` es una herramienta de quien desarrolla el nodo, no del agente que lo opera. Con el modelo de seguridad del PR #9 (`codex/seguridad`): sin `--debug` esas rutas responden 404; usan `PCX_DEBUG_TOKEN`, distinto de `PCX_TOKEN` (MCP) y de `PCX_CONSOLE_TOKEN` (consola); sus toques son siempre `api` y **no confirman domótica**. Antes de ese PR, `/debug` compartía el token del MCP y no requería flag: no lo uses fuera de un nodo de pruebas.

Usa siempre `PCX_DATA_DIR` propio al probar: por defecto el nodo escribe en el directorio de datos del SO y restaura widgets y objetos de la corrida anterior. Los puertos 8787–8799 están reservados.

## Convenciones que evitan retrabajo

- Sin OpenCV del sistema ni dependencias que no compilen solas en macOS, Windows y Linux.
- Lo específico de un SO va detrás de `#[cfg(target_os = …)]`.
- Modelos y binarios grandes no se commitean: caché del SO con sha256 (ver `pcx-hands`, `pcx-objects`).
- No agregues un evento nuevo si uno existente sirve: un worker que publica `object_*` funciona hoy con flujos, widgets anclados y `history`, sin tocar nada más.
- No abras cámaras ni ventanas en tests: `replay:` como fuente y `--headless` en el renderer.

## Errores comunes

| Síntoma | Causa | Arreglo |
|---|---|---|
| El core no reacciona a mis mensajes | `v` ausente o distinto de 2, `type` ≠ `payload.type`, msgpack posicional | Sobre completo; msgpack **con nombres** |
| `object_moved` no hace nada | El objeto no existe: falta `object_appeared` antes | Emitir `object_appeared` primero |
| Mi worker no recibe eventos | SUB sin `SUBSCRIBE`, o conectó después de publicarse | Suscribir (aunque sea `""`); no depender de eventos pasados |
| La tool aparece en `tools/list` pero falla con `tool desconocida` | Falta la rama en `Tools::dispatch` | Agregarla |
| `cargo test -p pcx-contracts` falla tras cambiar Python | Fixtures canónicos regenerados y Rust sin actualizar | Replicar el cambio en `contracts/rust/pcx-contracts` |
| Funciona local y no por el gateway | Falta el plazo en `TOOL_TIMEOUTS` (tool que muta) o la rama en `Gateway.dispatch` (tool de lectura) | Agregarlo |
| `look` sigue mostrando el dibujo sintético | No se pasó un `FrameSource` a `Core::start` | Cablearlo |
| El nodo arranca con widgets viejos | Estado restaurado de `pcx.db` | `PCX_DATA_DIR` limpio |
