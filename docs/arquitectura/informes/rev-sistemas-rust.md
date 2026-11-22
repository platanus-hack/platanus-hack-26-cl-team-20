# Revisión: Rust, sistemas, multiplataforma y distribución

Revisor independiente · sáb 21-nov-2026 · objeto: `docs/arquitectura/ARQUITECTURA-ACTUAL.md` y el código en curso (rama
`manu/plataforma` y worktrees `.claude/worktrees/agent-*`). Solo lectura. La única «compilación» fue un `cargo tree` sobre
una copia en `/tmp` que junta todos los crates del nodo, para ver cómo se resuelven las dependencias (sin compilar).

Contexto que pesa en las prioridades: quedan unas 30 h hasta la demo (dom 09:00) y la demo corre en **un** Mac. Por eso
cada objeción separa «para la demo» de «para que sea instalable en los 3 SO».

---

## Resumen: las 10 objeciones por impacto

| # | Objeción | Impacto | Costo del arreglo |
|---|---|---|---|
| 1 | No está definido qué corre en qué proceso: ONNX (C++) en el mismo proceso que el core = un segfault tumba MCP, nube y estado | Alto (demo) | 3–5 h |
| 2 | `pcx up` sin las primitivas por SO: hijos huérfanos, sin instancia única, salud = «HTTP 200» | Alto (demo y 3 SO) | 4–6 h |
| 3 | Frontera async/sync: SQLite, ONNX y `reqwest::blocking` dentro de tokio; actor en un task sin aislamiento de panics | Alto | 2–3 h |
| 4 | `ort`: dos versiones pedidas, RC eterno, matriz de binarios sin Mac Intel, EPs y peso del binario | Alto (3 SO) | 1–2 h + decisión |
| 5 | Renderer en Windows/Linux: Wayland ignora posición y «siempre encima», DPI mixto, FXC de DX12 con vello | Medio-alto (3 SO) | 1–2 h |
| 6 | Superficie local: ZeroMQ en dos versiones con puertos que ya no calzan, puertos fijos, `/tmp/pcx`, MCP sin validar `Origin` | Medio-alto | 2–3 h |
| 7 | `pcx-shm` en Windows no se recupera si el escritor muere con lectores vivos; seqlock con UB formal | Medio | 1–2 h |
| 8 | Distribución: firma/notarización, TCC de cámara, SmartScreen, glibc; `dist` sirve pero no cubre todo | Medio (demo) / alto (producto) | 0,5 día |
| 9 | Modelos: dos cachés distintas, URLs mutables (`main`), un sha vacío, sin reanudar ni candado; DINO pesa 204–719 MB | Medio | 2–3 h |
| 10 | Límites de crates e higiene del workspace: `pcx-core` es un «god crate», contratos copiados a mano, dependencias duplicadas, C toolchain escondido | Medio (deuda que crece por hora) | 1–2 h |

---

## 1. Topología de procesos indefinida: ONNX en el proceso del core

**Problema.** El documento dice «un binario `pcx` + `pcx-renderer`» y lista `pcx-hands`/`pcx-objects` como crates, con
«canales en proceso y ZeroMQ para workers externos». No dice cuáles van en proceso. El camino de menor resistencia
(y el que sugiere el texto) es enlazar `pcx-hands` y `pcx-objects` dentro de `pcx`. Entonces ONNX Runtime (C++), el
execution provider de CoreML/DirectML y `tokenizers`/`onig` (C) comparten espacio de direcciones con el actor del mundo,
el MCP, el enlace a la nube y SQLite. Un `SIGSEGV`/`abort()` en C++ no es un `panic!` de Rust: `catch_unwind` no lo
atrapa y el proceso entero muere. Además, la propuesta anterior (`03-ARQUITECTURA-PROPUESTA.md`, D2, principio 4 «un
dueño por recurso») sí separaba procesos; la versión actual lo perdió sin decirlo.

**Evidencia.**
- `pcx-hands/Cargo.toml`: `ort = "=2.0.0-rc.15"` con `download-binaries`; `ort-sys` enlaza
  `cargo:rustc-link-lib=static=onnxruntime` (`ort-sys-2.0.0-rc.15/build/main.rs:183`): ORT va **dentro** de cualquier
  binario que dependa de estos crates. El `.a` de ORT para aarch64-apple-darwin pesa 80 MB en la caché local
  (`~/Library/Caches/ort.pyke.io`).
- `pcx-core/src/world.rs:183-185`: el actor es un `tokio::spawn` sobre un `mpsc::unbounded_channel`. Si un job hace
  `panic`, el task muere en silencio y desde ahí todo `WorldHandle::call` hace `panic!("el actor del mundo terminó")`
  (`world.rs:871-875`). El proceso sigue «vivo» para un health check por HTTP, pero ya está muerto.
- Los RC de `ort` vienen corrigiendo «varios bugs de unsoundness tras pruebas con Miri» (notas de rc.13:
  https://newreleases.io/project/github/pykeio/ort/release/v2.0.0-rc.13).

**Alternativa.** Un **binario multi-llamada** con tres clases de proceso:
- `pcx serve`: core (actor, MCP, `/render`, nube, SQLite). **Sin ORT ni cámaras.** Es lo que tiene que sobrevivir.
- `pcx worker capture|hands|objects`: síncrono, un hilo por lazo, lee `pcx-shm` y empuja eventos al core. `panic =
  "abort"` en el perfil (o `std::process::abort` en el hook de panic): si algo falla, muere entero y el supervisor lo
  reinicia en ~1 s. `objects` (DINO + SAM, ~1–2 GB de RAM en pico) en su propio proceso, lanzado a pedido.
- `pcx-renderer` (ya es un proceso aparte, bien).

Un solo ejecutable también simplifica la firma y la identidad TCC de la cámara (objeción 8). ORT queda enlazado en el
mismo archivo, pero solo se inicializa en los workers.

En el actor: un panic en un job no debe matar el mundo. O se envuelve cada job en `catch_unwind(AssertUnwindSafe(..))`
y se registra (aceptable porque el estado se reconstruye del log), o se hace `abort` y el supervisor reinicia el core
(el log de eventos en SQLite ya permite reanudar). No vale el estado intermedio actual, donde el proceso queda zombi.

**Costo.** 3–5 h, porque ya existen `pcx-shm` y la ingesta ZeroMQ/canal. Para la demo basta con: workers en
procesos aparte, actor con `catch_unwind` o `abort`, y que la salud del core verifique el actor (ver 2).

---

## 2. `pcx up` sin las primitivas de cada SO

**Problema.** El supervisor (hoy en Python, `node/core/src/pcx_core/supervisor.py` en el worktree `abb29f6`, que el
crate `pcx-core` dice que va a portar) usa grupos de procesos y SIGTERM en Unix, y `terminate()/kill()` en Windows. Faltan
las piezas que hacen robusto un supervisor de escritorio:
- **Hijos huérfanos.** Si el supervisor muere con SIGKILL, se cae o la persona cierra la terminal en Windows, los hijos
  siguen vivos y retienen la cámara (AVFoundation/MSMF dan acceso exclusivo), la ventana del proyector, los puertos y el
  segmento compartido. El siguiente `pcx up` falla por «cámara ocupada / puerto en uso / `ShmError::Exists`».
  `TerminateProcess` en Windows mata solo al hijo directo, no al árbol.
- **Sin instancia única.** Nada impide dos `pcx up` (p. ej., uno de Claude Desktop vía `pcx mcp` y otro manual).
- **Salud superficial.** `health: str` es una URL. Un core con el actor muerto (objeción 1) responde 200.
- **Sin cortacircuito.** `restart="always"` con backoff, pero sin «N caídas en M segundos → parar y avisar». Un modelo
  que no descarga o una cámara ausente queda en un bucle eterno.

**Evidencia.** `supervisor.py:1-5` («en Windows `terminate()`/`kill()` de subprocess»), `ProcSpec.health: str | None`,
`restart: str = "always"`. En Rust todavía no existe (`pcxd/src/main.rs` tiene 1 línea).

**Alternativa (Rust, por SO).**
- Árbol de procesos: Windows, **Job Object** con `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` (si muere `pcx`, el SO mata el
  árbol). Linux, `prctl(PR_SET_PDEATHSIG, SIGTERM)` en el hijo + grupo de procesos. macOS no tiene pdeathsig: el hijo
  vigila un pipe heredado (EOF en stdin = el padre murió → salir). Los crates `process-wrap`/`command-group` resuelven
  grupos y Job Objects. La lectura de EOF se escribe a mano (~20 líneas).
- Instancia única: `flock` sobre `<runtime_dir>/pcx.lock` (Unix) o un mutex con nombre `Local\pcx-<uid>` (Windows), con
  el PID y los puertos escritos en `<runtime_dir>/pcx.json`. `pcx mcp` (stdio) lee ese archivo para encontrar el daemon,
  o lo levanta si no existe.
- Salud real: el core expone `/healthz`, que hace un `call` al actor con timeout de 500 ms. Los workers publican un
  heartbeat (`t_last_frame` en el encabezado del anillo o un evento cada 1 s). El supervisor reinicia por falta de
  heartbeat, no solo por código de salida.
- Cortacircuito: 5 caídas en 60 s → estado `failed`, visible en `pcx status` y en la consola, sin seguir reintentando.
- Apagado: SIGTERM/`CTRL_BREAK_EVENT` → 3 s → kill. En Windows, `GenerateConsoleCtrlEvent` necesita
  `CREATE_NEW_PROCESS_GROUP`.
- Arranque al iniciar sesión (después de la demo): LaunchAgent, `systemd --user` y entrada en `Run` o Programador de
  tareas. Cuidado con TCC en macOS (objeción 8).

**Costo.** 4–6 h para los 3 SO. Para la demo (macOS): pipe-EOF + lock + `/healthz` que toque el actor + cortacircuito,
~2 h.

---

## 3. Frontera async/sync: bloqueos y panics dentro de tokio

**Problema.** Se mezclan llamadas bloqueantes con el runtime de tokio:
- `WorldHandle::command` (async) llama a `self.store.request_get/put`, que son SQLite síncrono detrás de un
  `std::sync::Mutex` (`store.rs:57-63`), en el hilo del worker de tokio. Por su parte, `World::apply` escribe el log de
  eventos dentro del task del actor. Con WAL y `synchronous=NORMAL` suele bastar, pero un checkpoint de WAL, un disco
  lento o un antivirus en Windows (Defender escanea los `.db` y `-wal`) bloquean un hilo del runtime decenas de ms.
- El canal del actor es **unbounded** y `post()` se usa para ingesta de alta frecuencia (`set_hands`, `ingest_nowait`):
  si el actor se atrasa, la memoria crece sin límite en vez de descartar cuadros viejos.
- `pcx-objects/src/llm.rs:87,101` usa `reqwest::blocking::Client`. Si alguien llama a `ask_json` desde un contexto
  tokio (lo natural en el core), `reqwest::blocking` hace panic («Cannot drop a runtime in a context where blocking is
  not allowed»). Además, con timeout de 180 s ocuparía un hilo del runtime durante minutos.
- La inferencia ONNX (5–30 ms por cuadro en CPU para palma y landmarks; cientos de ms o segundos para DINO) no puede ir
  en un task. `spawn_blocking` tampoco sirve para lazos permanentes (es un pool para trabajos que terminan).
- ORT, por defecto, deja sus hilos intra-op **girando** (`allow_spinning`) entre inferencias. A 30 Hz eso consume núcleos
  que necesitan el renderer y tokio. `pcx-hands` fija `intra_threads: 2`, pero no desactiva el giro.

**Alternativa.**
- El actor en un `std::thread` dedicado, dueño de la `Connection` de SQLite (sin `Mutex`) y con un canal **acotado**.
  Para la ingesta de manos y objetos, `tokio::sync::watch` o un «último gana» (`try_send` que descarta). El estado sale por
  `watch` (ya existe). Las llamadas async solo hacen `send` + `oneshot`.
- Los crates de percepción exponen API **síncrona y pura** (`fn step(&mut self, frame: &Frame) -> Vec<Event>`), sin
  tokio ni HTTP. Cada worker tiene un hilo por lazo y `rayon` para el preprocesamiento.
- La llamada al LLM sale de `pcx-objects`: el core la hace con `reqwest` async, o mejor vía Render, para no repartir
  `ANTHROPIC_API_KEY` en cada computador (también es un tema de distribución).
- En la sesión de ORT: `with_intra_op_spinning(false)` (o `session.intra_op.allow_spinning=0`) y `inter_threads=1`.

**Costo.** 2–3 h. Mover el actor a un hilo es mecánico, porque `WorldHandle` ya encapsula el canal.

---

## 4. `ort`: versión, matriz de binarios, EPs y peso

**Problema y evidencia.**
- **Dos versiones pedidas.** `pcx-objects` pide `ort = "2.0.0-rc.10"` (con features por defecto) y `pcx-hands` pide
  `=2.0.0-rc.15` (`default-features = false`). Como en semver `^2.0.0-rc.10` acepta rc.15, Cargo unifica en silencio a
  rc.15 (verificado con `cargo tree -i ort-sys`). `pcx-objects` se escribió contra la API de rc.10 y compilará (o no)
  contra rc.15 sin que nadie lo haya elegido. Entre rc.10 y rc.15 hubo cambios incompatibles (rc.13 rompió la API de
  operadores, compuertas de EP por feature y pasó de CUDA 12 a 13; ver las notas de rc.13 arriba).
- **RC perpetuo.** `ort` 2.0 lleva 16 RC desde febrero de 2024 (rc.0 2024-02-06 … rc.15 2026-10-19, crates.io). Está
  bien mantenido (último push 2026-11-18, 4 issues abiertos), pero cada RC puede romper la API: hay que fijarlo con `=`.
- **Matriz de binarios precompilados** (`ort-sys-2.0.0-rc.15/build/download/dist.tsv`, ORT 1.31.0):
  - macOS: solo `aarch64-apple-darwin+coreml` (y `+coreml,webgpu`). **No hay `x86_64-apple-darwin`**: en Macs Intel,
    `download-binaries` falla y hay que compilar ORT desde el código fuente o usar `load-dynamic`.
  - Windows x64: solo variantes con `directml`, `webgpu`, `openvino` o CUDA; no hay un CPU «puro». Pedir CPU trae el
    build con DirectML, y `DirectML.dll` hay que distribuirlo al lado. DirectML está en «sustained engineering» (sin
    funciones nuevas; Microsoft empuja Windows ML):
    https://learn.microsoft.com/windows/ai/directml y https://github.com/microsoft/onnxruntime/pull/25901.
  - Linux: x64 y arm64 «none» y CUDA 13. Glibc mínima no documentada en el TSV: hay que probarla en la distro más vieja
    que se soporte.
- **Arrastre de dependencias.** Las features por defecto de `ort` (por `pcx-objects`) meten `ureq` con `native-tls`
  como dependencia de build de `ort-sys`, y eso trae `openssl-sys` (`cargo tree -i openssl-sys`). En Linux, compilar
  exige `libssl-dev` + `pkg-config`. Contradice «dependencias que compilen solas en los 3 SO».
- **Peso.** ORT estático suma decenas de MB a cada ejecutable que lo enlace (el `.a` pesa 80 MB antes de enlazar y
  quitar símbolos; el `pcx-renderer` release sin ORT pesa 11,9 MB). Si `pcx`, `pcx-renderer` y otros binarios lo
  enlazan, se paga varias veces.

**Alternativa.**
- `[workspace.dependencies] ort = { version = "=2.0.0-rc.15", default-features = false, features = ["std", "ndarray"?,
  "download-binaries", "tls-rustls", "api-22"] }` y, en cada crate, `ort.workspace = true`. Las EPs se activan con
  features del binario (`pcx/coreml`, `pcx/directml`), no de las librerías.
- Para la demo: macOS arm64 + CoreML. Para producto: evaluar `load-dynamic` con la `libonnxruntime` (y `DirectML.dll`)
  dentro del archivo del release. Así el ejecutable no carga ORT si no se usa percepción, las EPs se cambian sin
  recompilar y se soporta el Mac Intel con un ORT oficial de Microsoft (que sí publica `osx-x86_64`). Según la propia
  documentación de `ort`, con `load-dynamic` el ejecutable «no falla al arrancar si falta la librería»
  (https://raw.githubusercontent.com/pykeio/ort/main/docs/content/setup/linking.mdx).
- CoreML: fijar formas estáticas (DINO con longitud de texto fija) y activar la caché del modelo compilado
  (`ModelCacheDirectory`). Si no, cada arranque recompila y unas formas dinámicas parten el grafo entre CoreML y CPU, con
  copias que se comen la ganancia.

**Costo.** Fijar la versión y las features: 30 min. Decidir `load-dynamic` y empaquetarlo: 2–3 h, después de la demo.

---

## 5. Renderer fuera de macOS

**Problema.** El renderer (`renderer/src/main.rs:199-251`) está pensado y probado para el Mac del montaje. En los otros
SO:
- **Wayland** (lo que usan por defecto Ubuntu y Fedora con GNOME): `with_position` se ignora («Android / Wayland:
  Unsupported», https://docs.rs/winit/0.30.13/winit/window/struct.Window.html) y `WindowLevel::AlwaysOnTop` tampoco se
  aplica. La ventana sin bordes cae en el monitor que elija el compositor, normalmente el principal, no el proyector.
  La única forma portable de elegir monitor en Wayland es `Fullscreen::Borderless(Some(monitor))`.
- **Windows con DPI mixto** (laptop al 150 %, proyector al 100 %): la posición se convierte a lógica con el factor del
  monitor destino (`mon.position().to_logical(sf)`), pero en Windows winit interpreta la posición lógica de creación
  con el factor del monitor donde nace la ventana. Resultado: la ventana queda desplazada o con el tamaño equivocado.
  Hay que usar `PhysicalPosition`/`PhysicalSize` directamente.
- **DX12 + vello**: `Backends::PRIMARY` en Windows elige DX12. Si wgpu cae al compilador FXC (cuando no hay DXC
  estático o `dxcompiler.dll`), compilar los shaders de cómputo de vello tarda segundos y congela el hilo principal
  (FXC: «más de 10 s» en shaders simples, https://bugzilla.mozilla.org/show_bug.cgi?id=1885217; con vello, 3–5 s al
  iniciar en Graphite). El enum `Dx12Compiler` documenta el orden de respaldo:
  https://docs.rs/wgpu/latest/wgpu/enum.Dx12Compiler.html.
- **Laptops con GPU híbrida**: `PowerPreference::HighPerformance` elige la GPU discreta, pero la salida HDMI del
  proyector puede estar cableada a la integrada. Eso agrega una copia entre adaptadores y latencia por cuadro.
- **ESC cierra el proyector** (`main.rs:301-304`). En la demo, una tecla perdida apaga la proyección. Mejor ESC solo con
  `--dev`.
- Hay `expect`/`panic` en cada falla de inicialización. Está bien porque es otro proceso, pero entonces el supervisor
  tiene que reiniciarlo, y el reinicio necesita la escena completa (ya existe `resync`).

**Alternativa.** En `#[cfg(not(target_os = "macos"))]`, usar `with_fullscreen(Some(Fullscreen::Borderless(Some(mon))))`.
El kernel panic era de la pantalla completa **nativa** de macOS, no de la borderless de Windows o Linux. Posición y
tamaño en físicos. En Windows, `Backends::VULKAN | DX12` con la feature `static-dxc` de wgpu (o Vulkan primero).
Preferir el adaptador que reporta el monitor elegido. Mantener el camino macOS tal cual, porque ya está probado.

**Costo.** 1–2 h y una prueba en una máquina Windows y una Linux con Wayland. Sin esa prueba, «instalable en los 3 SO»
no se puede afirmar en el pitch.

---

## 6. Superficie local: ZeroMQ, puertos, rutas y MCP sin `Origin`

**Problema y evidencia.**
- **Dos versiones de `zeromq`**: `pcx-capture` pide `0.4` y `pcx-core` pide `0.7` (las dos en el lockfile:
  `cargo tree -d` → `zeromq v0.4.1`, `zeromq v0.7.0`). El README del crate dice que es una implementación «basic» que
  «does not implement all of ZeroMQ's feature set» (https://github.com/zeromq/zmq.rs). El código Python del capturador
  usa opciones de libzmq (`IMMEDIATE`, `NOBLOCK`, HWM) que conviene verificar en zmq.rs, y la interoperabilidad con
  pyzmq solo está «tested against the reference implementation».
- **Los puertos ya divergieron**: el core Python usa `tcp://127.0.0.1:9206/9207` en Windows
  (`pcx_core/settings.py:16`), mientras el capturador documenta `tcp://127.0.0.1:9225` para el mismo `ingest`
  (`node/capture/README.md:31`). En Windows, la captura no llegaría al core.
- **Puertos fijos**: 9200 (`/render`) es el puerto por defecto de Elasticsearch/OpenSearch, muy común en máquinas de
  desarrolladores. A eso se suman 9206, 9207, 9220 y 9225, todos fijos.
- **`/tmp/pcx/*.sock`**: `/tmp` es compartido entre usuarios. Otro usuario puede crear `/tmp/pcx` antes (DoS o
  suplantación del socket). En macOS además existe el límite de 104 bytes de `sun_path`.
- **MCP local**: `rmcp` 3.11 valida `Host` (allowed_hosts por defecto) pero la validación de `Origin` viene
  **desactivada** por defecto («Defaults to an empty list, which disables Origin validation»,
  `rmcp-3.11.0/src/transport/streamable_http_server/tower.rs:252-258`). El WS `/render` (axum propio) y la página del
  celular (`0.0.0.0:9220`) no tienen autenticación. Una página web cualquiera abierta en el mismo computador puede
  hablarle a `localhost`. Para un nodo que controla un proyector y, después, la domótica, ese es el vector a cerrar.
- **Firewall**: abrir en `0.0.0.0` (celular) dispara el diálogo del firewall de macOS para binarios no firmados y el de
  Windows Defender Firewall (que en redes «públicas» pide permisos de administrador).

**Alternativa.**
- Si todo el nodo es Rust, ZeroMQ solo hace falta como puente con los workers **Python** de la rama `mesa`. Entre
  procesos Rust basta `interprocess` (sockets locales: UDS + named pipes de Windows, crate activo, 2.4.9 del 18-nov-2026)
  con msgpack con prefijo de largo, o un WS local que ya existe. Si se mantiene ZeroMQ, una sola versión en
  `[workspace.dependencies]` y una prueba de interoperabilidad con pyzmq en CI.
- Los endpoints se definen **en `pcx-contracts`** (una constante y una función por SO), no en cada README.
- Puertos: bind a `127.0.0.1:0` (puerto efímero) y publicar los elegidos en `<runtime_dir>/pcx.json` (ver objeción 2).
  Ruta: `$XDG_RUNTIME_DIR/pcx` (Linux), `$TMPDIR/pcx` (macOS, ya es por usuario), `%LOCALAPPDATA%\pcx\run` (Windows),
  con permisos 0700.
- MCP: `allowed_origins` explícito y un token local (generado por `pcx up`, guardado en `pcx.json`, modo 0600) para el
  HTTP del MCP y para `/render`. La página del celular, con un código de un solo uso del QR.

**Costo.** 2–3 h. El token y `Origin` son 30 min y conviene hacerlos antes de la demo, porque «domótica controlada por
agentes» va a generar la pregunta de seguridad.

---

## 7. `pcx-shm`: recuperación en Windows y seqlock

**Problema.** El diseño (seqlock, último gana, layout compatible con Python) es bueno, pero:
- **Windows no se recupera de la caída del escritor.** El file mapping vive mientras alguien tenga un handle. Si el
  capturador muere con un lector abierto (manos, objetos), `closed` nunca se marca y
  `pid_alive` devuelve `true` siempre en Windows (`seg.rs` → `pub fn pid_alive(_pid: u32) -> bool { true }`). El lector
  espera cuadros que no llegan y el capturador reiniciado choca con `ERROR_ALREADY_EXISTS` → `ShmError::Exists`
  (`lib.rs:219-221`, `if cfg!(windows) { return Err(ShmError::Exists(name)); }`). Si el supervisor reinicia el
  capturador, este entra en un bucle de caídas hasta que todos los lectores se reinicien.
- **Reúso de PID en POSIX**: el huérfano se detecta con `kill(pid, 0)`. Tras un reinicio del equipo, el PID viejo puede
  pertenecer a otro proceso vivo → `Exists` falso. Riesgo bajo pero real con `pcx up` al iniciar sesión.
- **Seqlock y modelo de memoria de Rust**: el lector arma `&[u8]` (`from_raw_parts`, `lib.rs` en `read_latest`) sobre
  memoria que el escritor puede estar escribiendo. Esa carrera de datos es **UB formal** en Rust aunque el `gen` se valide
  después. `with_latest` además entrega bytes posiblemente rotos a un closure del usuario, que puede ejecutarse más de
  una vez (con efectos laterales repetidos). Es el problema conocido de los seqlocks en Rust, que motivó
  https://github.com/rust-lang/rfcs/pull/3301. En la práctica, el compilador no lo rompe hoy. Hay que documentarlo y
  acotarlo.
- `wait_newer` sondea cada 200 µs: 5 000 despertares por segundo por lector.

**Alternativa.**
- Agregar al encabezado `t_heartbeat_ns` (el escritor lo actualiza en cada `write` y con un tick de 250 ms aunque no haya
  cuadros). El lector que lo ve viejo (>1 s) devuelve `Closed` y suelta el mapping.
- En Windows: nombre con época, `pcx.cam.<id>.<n>`, con el `n` vigente publicado en `FrameMeta` o en `pcx.json`. Así un
  escritor nuevo nunca choca con un mapping viejo. Agregar un `pid_alive` real con
  `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` + `GetExitCodeProcess`.
- En POSIX, guardar también la hora de inicio del proceso junto al PID, o usar la época del punto anterior.
- Para el UB: copiar con `ptr::copy_nonoverlapping` a un búfer propio solo dentro del lector (o con el crate
  `atomic-memcpy`) y quitar `with_latest` de la API pública, o marcarlo `unsafe`.
- Esperar con back-off (spin corto → `sleep(1 ms)`) o con una notificación (evento con nombre en Windows, `futex` en Linux,
  o simplemente el `FrameMeta` por el socket como señal).

**Costo.** 1–2 h. Para la demo en macOS el riesgo es bajo. Para Windows es bloqueante.

---

## 8. Distribución: firma, TCC, SmartScreen, glibc

**Estado de las herramientas (verificado).**
- `dist` (antes `cargo-dist`) está vivo: v0.34.0 del 2026-11-02 y push del 2026-11-21
  (https://github.com/axodotdev/cargo-dist, API de GitHub). Genera el workflow de GitHub Actions, los archivos por
  target, `install.sh`/`install.ps1` y publica en Releases. Firma Windows **solo con SSL.com eSigner o Azure Artifact
  Signing, y solo x86_64** (https://axodotdev.github.io/cargo-dist/book/supplychain-security/signing/windows.html). En
  macOS firma con `codesign` usando `CODESIGN_IDENTITY`/`CODESIGN_CERTIFICATE`
  (https://docs.rs/crate/cargo-dist/0.32.0/source/src/sign/macos.rs). No encontré soporte de **notarización**.

**Problemas.**
- **TCC (cámara en macOS).** Si `pcx up` se corre desde Terminal, el «proceso responsable» es Terminal: el permiso se
  pide y se guarda a nombre de Terminal, iTerm o VS Code, no de `pcx`
  (https://www.qt.io/blog/the-curious-case-of-the-responsible-process,
  https://developer.apple.com/forums/thread/111100). Funciona en la demo, pero con dos consecuencias: cada terminal
  distinta vuelve a pedir el permiso, y lanzado como LaunchAgent (al iniciar sesión) `pcx` pasa a ser responsable y
  necesita un `Info.plist` **embebido** con `NSCameraUsageDescription` (`-sectcreate __TEXT __info_plist`). Sin eso,
  macOS no muestra el diálogo y deniega
  (https://developer.apple.com/forums/thread/700714). Con firma ad hoc, la identidad cambia en cada build y el permiso
  se pierde en cada actualización.
- **Gatekeeper.** `curl | sh` no pone el atributo `com.apple.quarantine`, así que un binario sin notarizar corre. Eso
  salva la demo, pero si alguien baja el `.tar.gz` desde el navegador, Gatekeeper lo bloquea. Un ejecutable suelto no
  admite «staple»: la notarización se verifica en línea.
- **Windows.** `irm | iex` tampoco marca Mark-of-the-Web, así que SmartScreen no salta. Pero un `.exe` sin firma, nuevo,
  que abre la cámara, abre sockets y escribe en `%LOCALAPPDATA%` es candidato a una detección heurística de Defender.
  Firmar con Azure Artifact Signing (el camino barato que soporta `dist`) toma días de verificación de identidad: no
  alcanza para el hackathon.
- **Linux.** Hay que compilar con una glibc vieja (runner `ubuntu-22.04` o `cargo zigbuild` con `.2.31`), si no el binario
  no corre en distros LTS. `nokhwa` en Linux usa `v4l2-sys-mit` con **bindgen** → libclang en el build
  (`cargo tree -i bindgen`).
- **Tamaño.** `pcx` con ORT estático + wgpu/vello + rusqlite + rustls anda por 60–100 MB sin comprimir (estimación a
  partir de los 80 MB del `.a` de ORT y los 11,9 MB del renderer). Con dos ejecutables que enlacen ORT, el doble. Los
  modelos van aparte (objeción 9).
- **La API key en el nodo.** `pcx-objects` lee `ANTHROPIC_API_KEY` del entorno del nodo (`llm.rs:94`). Para «instalable
  por una persona» la llamada debe pasar por Render con el token del nodo.

**Alternativa.**
- Para la demo: nada de esto bloquea, porque se corre desde Terminal en el Mac del montaje. Que no haya sorpresas con
  TCC al lanzar como LaunchAgent: no hacerlo en la demo.
- Para el producto: `dist` para la matriz, los instaladores y Releases (ahorra un día de YAML), más un job propio de
  macOS con `rcodesign` (firma y notarización desde Linux, sin Mac en CI) o `notarytool`, con Developer ID. Un solo
  ejecutable multi-llamada (objeción 1) = una firma, una identidad TCC. Builds: `aarch64-apple-darwin`,
  `x86_64-pc-windows-msvc`, `x86_64-unknown-linux-gnu` (y Mac Intel solo si se resuelve ORT, objeción 4).
- Probar `install.ps1` en una VM Windows limpia antes de mostrar el comando en el pitch.

**Costo.** `dist init` + matriz: 2 h. Firma y notarización: medio día más, y el Developer ID ($99/año) si no existe.

---

## 9. Modelos: descarga y caché

**Problema y evidencia.**
- **Dos cachés distintas.** `pcx-hands` usa `directories::ProjectDirs::from("ai","pcx","pcx").cache_dir()/models`
  (en macOS, `~/Library/Caches/ai.pcx.pcx/models`). `pcx-objects` usa `dirs::cache_dir()/pcx/models` (en macOS,
  `~/Library/Caches/pcx/models`), con respaldo a `temp_dir` (`models.rs:166-171`). Son dos crates para lo mismo y dos
  rutas.
- **URLs mutables.** Manos: `media.githubusercontent.com/.../opencv_zoo/main/...` (Git LFS de una rama `main`). Objetos:
  `huggingface.co/.../resolve/main/...`. Si el autor actualiza el archivo, el sha256 deja de calzar y **la instalación se
  rompe** para todos. GitHub LFS tiene cuota de ancho de banda por repo.
- **Un sha vacío**: `sha256: ""` en una entrada de `pcx-objects/src/models.rs:150` (el `.tokenizer.json` de DINO o el
  siguiente): ese archivo no se verifica.
- **Tamaño**: Grounding DINO tiny pesa 204 MB en int8 y 719 MB en fp32 (`models.rs:135,141`), más MobileSAM y el
  tokenizador. Sin reanudar descargas (`Range`), sin progreso visible en `pcx up` y sin candado: dos procesos (manos y
  objetos, o dos reinicios seguidos) pueden bajar el mismo archivo a la vez. La escritura atómica con rename evita la
  corrupción, pero no el doble consumo.
- `tokenizers` con `onig` compila Oniguruma en C (`onig_sys`, verificado con `cargo tree`).

**Alternativa.**
- Un crate o módulo `pcx-models` (en `pcx-contracts` no, porque hace IO): manifiesto único (`models.toml`: nombre, URL,
  sha256, tamaño, licencia), una caché (`<cache>/pcx/models/<sha256>`), candado por archivo (`fd-lock`), reanudación con
  `Range`, progreso por evento hacia la consola, `pcx models pull` explícito y modo sin red.
- Espejo propio: los archivos como assets de un GitHub Release del repo (o Hugging Face con **revision = commit**, no
  `main`).
- DINO bajo demanda (nivel 3 de la cascada) y nunca en el camino de `pcx up`: la demo no puede depender de 200 MB por el
  Wi-Fi del recinto. Precargar los modelos en el Mac de la demo.
- `tokenizers` con `default-features = false, features = ["fancy-regex"]` (Rust puro) si el tokenizador de DINO (BERT
  WordPiece) no necesita Oniguruma. Es probable que no.

**Costo.** 2–3 h para el módulo común. El espejo y los sha: 30 min.

---

## 10. Límites de crates e higiene del workspace

**Problema y evidencia.**
- **`pcx-core` es un «god crate».** Su descripción: «actor del mundo, log de eventos (SQLite), MCP, /render, flujos,
  ingesta ZeroMQ, enlace a la nube y supervisor». Depende de axum, rmcp, rusqlite, tokio-tungstenite, zeromq y reqwest.
  El supervisor es asunto del binario (`pcxd`), no de la librería del core.
- **Contratos copiados a mano**: `pcx-core/src/proto.rs` son 1 062 líneas que reflejan a mano los modelos Python («TODO:
  mover este módulo a `pcx-contracts`»). Si `pcx-hands` o `pcx-capture` necesitan emitir `Envelope`/eventos, tienen dos
  opciones malas: depender de `pcx-core` (todo el stack HTTP y SQLite, y un **ciclo** si el core enlaza percepción en
  proceso) o duplicar los tipos. El proceso de contratos (msgspec → JSON Schema → Rust) existe y se está saltando.
- **`pcx-capture` es un binario disfrazado de librería**: depende de `clap`, `axum`, `tokio` y `cpu-time`, y hoy su
  `lib.rs` está vacío.
- **Sin `[workspace.dependencies]`**, y ya hay duplicados reales en el lockfile combinado (`cargo tree -d`):
  `reqwest 0.12/0.13`, `zeromq 0.4/0.7`, `tokio-tungstenite 0.29/0.30`, `thiserror 1/2`, `nalgebra 0.32/0.33`, `rand`
  0.8/0.9/0.10 y `sha2 0.10/0.11`. También se mezclan `dirs` con `directories`, `ureq` con `reqwest`, y ediciones 2021
  (workspace) con 2024 (renderer).
- **«Compila solo en los 3 SO» no se cumple**: bindgen/libclang (nokhwa en Linux), `onig_sys` (C), `openssl-sys` en
  Linux (vía las features por defecto de `ort`). Además `rusqlite/bundled` necesita un compilador de C, cosa aceptable.
- **Sin `rust-toolchain.toml`, `[workspace.lints]`, `deny.toml` ni CI.** `anyhow` aparece en una librería (`pcx-core`),
  que debería usar `thiserror` y dejar `anyhow` para `pcxd`. El renderer registra con `eprintln!` y no con `tracing`.

**Alternativa (capas, sin ciclos).**

```
pcx-contracts      tipos y constantes (endpoints, nombres de shm), serde puro, sin IO
   ↑
pcx-shm · pcx-models · pcx-calib · pcx-hands · pcx-objects · pcx-capture(lib)
   librerías síncronas; sin tokio; errores con thiserror; features para EPs y backends
   ↑
pcx-core           async: actor, MCP, /render, enlace a la nube (cloud podría ser pcx-cloud)
   ↑
pcxd (bin `pcx`)   CLI, supervisor, `pcx worker …` (usa las libs de percepción), tracing-subscriber, anyhow
pcx-renderer (bin) depende solo de pcx-contracts
```

- `[workspace.dependencies]` con **todas** las versiones fijadas (`ort`, `rmcp`, `zeromq`, `wgpu`/`vello` con `=`).
  `[workspace.lints]` con `unsafe_op_in_unsafe_fn = "deny"`, `clippy::unwrap_used = "warn"` en las librerías.
  `rust-toolchain.toml` (1.9x estable, la que necesitan la edición 2024 y wgpu 30). Edición única.
- `cargo deny check licenses bans` (los modelos y ORT tienen licencias que hay que listar en la landing).
- CI mínimo de 3 SO: `cargo check --workspace --all-features` en `macos-14`, `windows-latest` y `ubuntu-22.04`. Es lo más
  barato que existe para respaldar «instalable en los 3 SO» y habría detectado los puntos 4, 5 y 10 de una vez.

**Costo.** 1–2 h para el workspace y CI. Mover `proto.rs` a `pcx-contracts`: 1–2 h, y hacerlo **antes** de que los
workers empiecen a emitir eventos.

---

## Otros riesgos (fuera del top 10)

- **`nokhwa`** (0.10.11, del 2026-05-15; 67 issues abiertos; el repo se movió a `l1npengfih/nokhwa`). Hay un reporte de
  que en macOS `stop_stream()` no suelta la cámara a nivel de SO (issue #224 para 0.10.10). Combinado con el reinicio por
  el supervisor, eso significa «cámara ocupada». La macOS usa el `objc` 0.2 antiguo, y en Windows MSMF entrega
  MJPEG/NV12 con conversión en CPU. Para la demo sirve; mantenerlo detrás de la feature `uvc` como ya está, y tener
  `mjpeg`/`replay` como respaldo. Probar desconectar y reconectar la webcam USB con el sistema corriendo.
- **`rmcp`**: es el SDK oficial y está muy activo (3.11.0 del 2026-11-17), pero ha subido versiones mayores muy rápido:
  fijarlo con `=` y no actualizarlo durante el hackathon. En `pcx mcp` (stdio), **todo** el logging debe ir a stderr: un
  `println!` rompe el protocolo.
- **`broadcast::channel(256)`** hacia `/render`: el `Lagged` ya pide la escena completa (bien). Verificar lo mismo en el
  canal de eventos (4096) hacia MCP `events_wait` y la nube.
- **`tokio = { features = ["full"] }`** en librerías: aceptable, pero en `pcx-core` basta `rt-multi-thread, net, sync,
  time, macros, signal`.
- **WSS saliente a Render** con `rustls-tls-webpki-roots`: en redes corporativas con inspección TLS fallará (no confía
  en el almacén del SO). `rustls-platform-verifier` lo resuelve.

---

## Mantener (decisiones correctas)

- **Rust para el nodo y un `pcx-renderer` en proceso aparte.** El renderer ya está aislado, con reconexión, `resync` y
  «última escena» mientras no hay core. Es el patrón correcto para que nada congele la proyección.
- **wgpu + vello + parley con homografía/LUT en el shader**, con 1,4–7,8 ms por cuadro medidos y golden images en las
  pruebas. Hot reload de WGSL **validado con naga** y respaldo al shader anterior.
- **Nunca la pantalla completa nativa en macOS** (kernel panics), con `NSStatusWindowLevel`, todos los Spaces y
  `ActivationPolicy::Accessory`. Es la decisión correcta y está bien encapsulada en `pin_window`.
- **`pcx-shm`**: un escritor, último gana, seqlock, layout compatible con Python, límite de nombre de 30 caracteres para
  macOS, `O_EXCL` + detección de huérfanos en POSIX y pruebas entre procesos y con Python. Solo faltan los arreglos del
  punto 7.
- **Actor de escritor único con log de eventos en SQLite** (WAL, migraciones con `user_version`, idempotencia por
  `request_id`) y `watch` para el estado. Es el modelo correcto; solo hay que llevarlo a un hilo propio (punto 3).
- **`rusqlite` con `bundled`**, `rustls` en vez de OpenSSL donde ya se eligió, y descarga de modelos con escritura
  atómica + sha256 (`pcx-hands`).
- **Features opcionales** para lo pesado (`pcx-objects/onnx`, `pcx-capture/uvc`, `pcx-hands/coreml`). Es la
  dirección correcta. Solo falta llevar las EPs al binario (punto 4).
- **`rmcp` oficial** con Streamable HTTP + stdio y un proxy `pcx mcp` → daemon. Es el SDK con más tracción en Rust.
- **`dist` para releases**: está mantenido y ahorra el YAML. Complementarlo con la firma y notarización de macOS, no
  reemplazarlo por un workflow propio desde cero.
- **Un enlace WSS saliente** al cerebro (sin puertos abiertos), con `last_cursor`, replay y `HistoryGap`.
