# 03 · Arquitectura propuesta (borrador para validar)

Estado: **propuesta**. Cada decisión (D1…D10) tiene opciones, una recomendación y su estado. Nada se implementa en grande hasta que Manu valide. Base: [01-ENGINE-ACTUAL.md](01-ENGINE-ACTUAL.md) y [02-CASOS-DE-USO.md](02-CASOS-DE-USO.md).

## 0. Principios (derivados de lo que falló y funcionó en el engine)

1. **El lazo en tiempo real es local.** Cámara → percepción → render → proyector nunca pasa por la red pública. Render.com no tiene GPU ni región en Sudamérica (~120–150 ms de ida y vuelta desde Santiago), y la demo no puede depender del Wi-Fi del evento.
2. **La IA prepara; el tracking es determinista.** Los modelos lentos (VLM, SAM) corren por evento y en segundo plano; el lazo caliente usa trackers baratos. Lo dijo Max (transcripción 2220) y lo confirma el código.
3. **Lo último gana.** Cuadros y señales de alta frecuencia se leen como «último valor», no por colas: latencia acotada sin backpressure. Las colas solo para lo que no se puede perder (comandos, `down`/`up`).
4. **Un dueño por recurso.** Un proceso es dueño del proyector, uno de cada cámara, uno del estado del mundo. Nadie mata a otro con `pkill`.
5. **Un marco canónico: la mesa en mm.** Todo lo demás (px de cámara, px de proyector) es una transformación con nombre y versión.
6. **Contratos primero.** Una sola definición tipada de mensajes y comandos genera validación, esquemas JSON, tipos TypeScript y las tools MCP.
7. **Medible.** Cada evento lleva la hora de captura; la latencia de punta a punta se mide y se muestra en vivo.
8. **Ningún proceso ejecuta código de un agente.** Lo que el agente cree (widgets, efectos) corre aislado en el navegador, no en Python.

## 1. Vista general

```
                         ┌──────────────────────── NUBE (Render) ───────────────────────────┐
  Claude Code / Desktop  │  gateway (Web Service, FastAPI)                                   │
  u otro agente ──MCP───▶│   · MCP remoto (Streamable HTTP) · REST · WSS de estaciones       │
                         │   · señalización WebRTC para celulares fuera de la LAN           │
  Consola web ──HTTPS───▶│  agent-worker (Background Worker): bucle de Claude con tools     │
                         │  Key Value (Valkey): pub/sub, sesiones · Postgres: historial      │
                         └──────────────────────────────▲───────────────────────────────────┘
                                                        │ 1 WSS saliente (sin abrir puertos)
 ┌──────────────────────────── ESTACIÓN (Mac conectado al proyector) ──────┴─────────────────┐
 │                                                                                           │
 │  capture ──(memoria compartida: anillo de cuadros)──▶ perception-hands ─┐                │
 │   UVC / celular / Quest / replay                    ▶ perception-objects ┤  eventos       │
 │                                                     ▶ calibration (job) ─┤  (bus local)   │
 │                                                                          ▼                │
 │                                          core  (dueño del estado del mundo)               │
 │                                          · escena: objetos, manos, toques, widgets        │
 │                                          · comandos tipados, eventos con seq, log         │
 │                                          · gateway local: WS + REST + MCP local           │
 │                                                  │ WS (diffs de estado, ~60 Hz)           │
 │                                                  ▼                                        │
 │                          renderer (navegador en la pantalla del proyector, WebGL)         │
 │                          widgets en mm → homografía/LUT en GPU → proyector (60 Hz)        │
 │                                                                                           │
 │  supervisor: arranca, revisa salud y reinicia cada proceso                                │
 └───────────────────────────────────────────────────────────────────────────────────────────┘
        ▲ celular: página web → cámara por WebRTC/WS en la LAN (QR proyectado)
```

## 2. Decisiones

### D1 · Topología: el «cerebro» en Render, nodos locales solo donde la latencia lo exige
- **Decisión de Manu (sáb 01:30)**: el producto es **contexto físico para agentes** en general, no solo la mesa con proyector. Incluye cámaras de la casa, domótica, personas y objetos. La mayor parte se despliega en Render; lo que deba ser local, se hace local.
- **Criterio para decidir dónde corre cada cosa: niveles de latencia** (como un sistema nervioso: reflejos en la médula, razonamiento en el cerebro).

| Nivel | Presupuesto | Ejemplos | Dónde |
|---|---|---|---|
| Reflejo | < 50 ms | Toque → respuesta proyectada, warp del proyector, seguimiento de la mano | **Nodo local** (obligatorio: red + falta de GPU en Render) |
| Percepción | 50 ms – 1 s | Detectar y seguir objetos y personas, reglas simples («si entra alguien, enciende la luz») | Nodo local (modelos en el Mac); eventos hacia la nube |
| Razonamiento | 1 – 10 s | Agente que decide, VLM que nombra, generar una guía o un widget | **Render** (agente) + API de Claude |
| Memoria | minutos o más | Qué pasó, dónde quedó cada objeto, rutinas de la persona, resúmenes | **Render** (Postgres + Key Value) |

- **Arquitectura resultante**: un **hub de contexto en Render** (modelo del mundo agregado de todos los espacios y dispositivos, memoria, agente, MCP remoto, consola) y **nodos de borde** (un Mac, un celular o una Raspberry) con adaptadores de dispositivos. Un nodo hace localmente los niveles «reflejo» y «percepción», y sube **eventos semánticos y cuadros clave**, no video continuo. Cada nodo abre una conexión WSS saliente hacia Render: sin túneles ni puertos abiertos.
- **Adaptadores de dispositivos** (puertos y adaptadores):
  - proyector + cámara (la mesa de Max);
  - celulares por la web;
  - cámaras IP de la casa (RTSP/ONVIF) leídas por el nodo local;
  - domótica a través de **Home Assistant** (API WebSocket/REST y servidor MCP propio): no conviene escribir integraciones con HomeKit, Google Home o Nest una por una.
- Estado: **validado en dirección**; falta acordar el alcance para la demo.

### D2 · Procesos en la estación
- **Recomendación**: `core` (asyncio, solo I/O y estado), `capture`, `perception-hands`, `perception-objects`, `calibration` (job a pedido) y `renderer` (navegador), todos bajo un supervisor (`process-compose` con health checks, o uno propio en Python).
- Por qué procesos y no hilos: GIL y aislamiento de fallas. Hay evidencia en el engine (DAViD en un hilo bajó el runtime de 25 a 17 fps) y un efecto o modelo que se cae no puede congelar la proyección (bug actual).
- Estado: **por validar**.

### D3 · Transporte dentro de la estación
| Qué viaja | Propuesta | Por qué |
|---|---|---|
| Cuadros de cámara (MB, 30–60 Hz) | **Memoria compartida**: un anillo de 4 slots por cámara con encabezado `{seq, t_captura_ns, w, h, formato}`; los lectores toman el último | Cero copias y cero serialización. Hoy se copian, picklean o codifican como PNG |
| Eventos y comandos (KB, ≤1 kHz) | **ZeroMQ** (`ipc://`), sin broker: los workers empujan a `core` (PUSH/PULL) y `core` publica (PUB/SUB). Mensajes msgpack tipados | Decenas de µs, probado en industria, trivial en Python, sin servidor extra |
| Estado hacia el navegador | **WebSocket** desde `core`: snapshot al conectar + diffs con `rev` | El navegador no habla ZeroMQ |
| Alternativas evaluadas | NATS (broker, sirve navegador y nube, más piezas), Zenoh o dora-rs (robótica, memoria compartida nativa, más riesgo en 30 h) | Se pueden adoptar después sin cambiar los contratos |
- Estado: **por validar**.

### D4 · Renderer: navegador con WebGL en la pantalla del proyector
- **Opciones**: (a) seguir con numpy/OpenCV en CPU; (b) Python con GPU (moderngl/pygame-GL); (c) Rust con wgpu; (d) **navegador (TypeScript + WebGL2: PixiJS o three.js)**.
- **Recomendación: (d).**
  - GPU y vsync a 60 Hz (hoy son 30 Hz en CPU, con judder).
  - Texto Unicode, HTML/SVG nítido para interfaces.
  - La homografía es una matriz (CSS `matrix3d` o shader); el modo preciso usa la LUT del gray code como textura.
  - Hot reload nativo (Vite).
  - Los agentes escriben muy bien HTML/SVG/JS, y ese código corre aislado (iframe + CSP), no dentro de Python.
  - La misma app sirve de vista previa remota (gemelo digital en la consola) y se testea con capturas headless (Playwright).
- **Riesgo**: los kernel panics DCPEXT de macOS con pantalla completa. Mitigación: ventana de navegador posicionada en la pantalla del proyector **sin** pantalla completa nativa (como hizo Max con la ventana normal). Es el **spike 1**, a probar en el Mac de la demo antes de comprometerse.
- Estado: **por validar**.

### D5 · Lenguajes (incluida la pregunta por Rust)
- **Python 3.12** para la estación (core, captura, percepción, calibración): ahí está el ecosistema (OpenCV, MediaPipe, Ultralytics, CoreML) y todo el código valioso de Max se porta, no se reescribe. Con tipos estrictos (msgspec o pydantic, pyright) y un workspace `uv`.
- **TypeScript** para renderer, consola y página del celular.
- **Python** también en la nube (FastAPI + SDK de Anthropic + SDK de MCP), para compartir el paquete de contratos.
- **Rust: no para nuestro código en esta hackathon.** Los cuellos de botella medidos no son del lenguaje: son arquitectura (render en CPU, PNG por HTTP, copias, GIL, AirPlay). Las partes calientes ya corren en código nativo (OpenCV, MediaPipe, numpy, Metal/WebGL). Rust se justificaría para un compositor nativo o IPC zero-copy, y eso ya lo resuelven la GPU del navegador y la memoria compartida. Queda abierto portar una pieza puntual (decode, detección de toque) con PyO3 si el profiling lo pide.
- Estado: **por validar**.

### D6 · Modelo del mundo y contratos
- `core` es un **actor de escritor único**: aplica comandos en orden, sube `rev` y publica diffs. Sin locks globales: el render nunca espera a un comando.
- **CQRS liviano**: comandos (validados e idempotentes con `request_id`), consultas (snapshot con `rev`) y eventos (con `seq`, reanudables desde un `seq`).
- **Log de eventos** en anillo en memoria + archivo NDJSON por sesión: permite reproducir una sesión, depurar y tener la demo de respaldo grabada.
- **Marcos**: `table_mm` canónico; registro de transformaciones `cam:<id>→table_mm` y `table_mm→proj:<id>` (homografía o LUT), con versión y residuo en mm.
- **Widgets declarativos** (propuesta del lector de efectos): `{id, type: label|halo|outline|arrow|callout|button|progress|panel|image|html|effect, anchor: {object_id | point_mm | zone}, style, z, ttl, on_touch}`. El hit-test del toque contra widgets se hace en `core`, en mm.
- Un paquete `contracts` (Python) es la fuente única: genera JSON Schema → tipos TS y las tools MCP.
- Estado: **por validar**.

### D7 · Calibración: rápida por defecto, precisa como modo avanzado
- **Rápida (demo)**: grilla ArUco proyectada → homografía cámara↔proyector; un tablero o marcador de tamaño conocido define `table_mm`. Unos 3 s. El código base existe (`recalib.py:82-111`).
- **Precisa**: gray code de Max (decoder validado), con sincronía adaptativa (aceptar el patrón cuando la cámara ve el cambio, no con `sleep`) y solo los bits útiles (36 en vez de 44). Sirve para objetos con altura (paralaje).
- **Verificación continua**: proyectar 12 cuadrados, medir el residuo en mm y avisar «rig movido» (idea de la mesa Quest).
- **Cámara nueva (celular)**: ArUco 1–2 s → su homografía; las otras cámaras no se tocan.
- El renderer es el dueño permanente de la ventana del proyector; la calibración le pide patrones por comando, sin matar procesos.
- Estado: **por validar**.

### D8 · Percepción
- **Manos y toque** (30 Hz, local): MediaPipe Hand Landmarker en el Mac (en un proceso aparte) → máquina de estados de Max (`hands/touch.py`) con One Euro. El toque para la demo es por **permanencia o pinch** (robusto); el **toque por sombra** de Max queda como modo avanzado, si se valida con mano real. Opcional: segunda cámara lateral (celular) para el contacto.
- **Objetos** (10–15 Hz): YOLO-seg o YOLOE con ByteTrack en MPS para cajas, máscaras e IDs estables; **Claude con visión solo para nombrar** objetos nuevos o quietos, de forma asíncrona.
- Estado: **por validar** (depende de los spikes 3 y 4: velocidad real de MediaPipe y YOLO en el Mac de la demo).

### D9 · Agente
- **Herramientas tipadas, menos y más ricas** que las 25 `pg_*` actuales: `scene`, `look` (devuelve imagen), `project`, `update`, `remove`, `highlight`, `ask` (botones proyectados que esperan un toque), `wait_for`, `events_since`, `calibrate`, `status`.
- **Dos formas de conectarse** sobre las mismas herramientas:
  1. MCP local o remoto para agentes externos (Claude Code, Claude Desktop): la demo «conecto mi Claude a mi mesa con una URL».
  2. Un agente propio en la nube con la API de mensajes de Anthropic: modelo `claude-opus-5-5` para el agente y `claude-haiku-5-5` para nombrar objetos (rápido y barato), con prompt caching del system prompt y las tools.
- **Eventos físicos hacia la conversación**: los eventos relevantes (toque en un botón, objeto nuevo) entran como mensajes de sistema a mitad de conversación (no invalidan el caché), con el arbitraje de turnos de la secretaria de Max.
- **Alternativa evaluada**: Managed Agents de Anthropic, que aloja el bucle. Está en beta y cobra tiempo de sesión; nuestras herramientas viven en la estación y el bucle necesita reaccionar a eventos, así que conviene un bucle propio.
- Estado: **por validar**.

### D10 · Nube en Render
| Servicio Render | Para qué | ¿Cabe en Render? |
|---|---|---|
| Web Service `gateway` (FastAPI) | MCP remoto, REST, WSS de estaciones y de la consola, señalización WebRTC | Sí (WebSockets soportados; plan pago para que no se duerma) |
| Background Worker `agent-worker` | Bucle de Claude, nombrado de objetos, tareas largas | Sí |
| Key Value (Valkey) | pub/sub entre instancias, sesiones, colas | Sí |
| Postgres | Estaciones, sesiones, historial de eventos, modos y guías | Sí |
| Static Site (o Vercel) | Consola web: gemelo digital, chat con el agente, métricas | Sí |
| Lazo en tiempo real, cámaras, proyector, GPU | — | **No**: hardware local, sin GPU, latencia de red |

Región: Virginia (o Ohio). Todo se declara en un `render.yaml` (Blueprint). Estado: **por validar**.

## 3. Qué vive en memoria (estación)

| Dato | Dónde | Tamaño | Política |
|---|---|---|---|
| Cuadros de cámara | Memoria compartida, 4 slots por cámara | 1280×720×3 ≈ 2,8 MB por slot | Último gana |
| Mapas de calibración (H y LUT) | Memoria compartida de solo lectura, versionada | H: 72 B; LUT 1920×1080 float32: ~16 MB | Se reemplazan de forma atómica |
| Estado del mundo | `core` | KB (≤50 objetos, ≤4 manos, ≤200 widgets) | `rev` monótono + diffs |
| Log de eventos | `core`: anillo de 10K + NDJSON en disco | MB | Reproducible |
| Modelos | Cada worker de percepción | MediaPipe ~10 MB, YOLO 10–50 MB (MPS) | Cargados al arrancar |
| Cuadro proyectado (solo para el toque por sombra) | Lectura reducida del renderer, 640×360 | 0,7 MB | Último gana |

## 4. Presupuesto de latencia (objetivo)

| Etapa | Hoy (medido o inferido) | Objetivo |
|---|---|---|
| Cámara | 40 ms (Quest) a 1337 ms (Osmo 4K) | UVC 720p a 60 fps: ~30–50 ms |
| Percepción de manos | 15–25 ms (estimado) | ~15 ms |
| Transporte | HTTP por evento, PNG/JPEG | <1 ms (ZeroMQ + memoria compartida) |
| Render | 30 Hz en CPU + JPEG a ≤5 Hz | 60 Hz en GPU: ≤16 ms |
| Salida al proyector | AirPlay (~200+ ms) | HDMI directo: 30–45 ms |
| **Total** | **~313 ms** | **~100–130 ms**, medido en vivo y mostrado en la consola |

## 5. Seguridad mínima
- Token por estación y por sesión; `core` escucha solo en localhost, salvo la página del celular (LAN, con token del QR).
- Sin rutas de archivo en la API; CORS estricto; nada servido desde carpetas con llaves o logs.
- El código que crea el agente corre en el navegador (iframe con CSP), con un watchdog que detecta cuadros congelados.
- Secretos solo por entorno (`.env` local, variables de Render).

## 6. Pruebas y observabilidad
- **Fuente de cámara «replay»** (video o cuadros grabados): todo el pipeline se prueba sin hardware y en CI.
- Tests de contratos; capturas headless del renderer (Playwright); prueba de latencia por escalón negro→blanco (ya existe en el engine).
- Métricas por etapa (fps, latencia p50/p95) y la hora de captura propagada hasta el cuadro proyectado. La consola muestra el grafo de procesos en vivo: es parte del pitch técnico.

## 7. Spikes (1 hora máx. cada uno, antes de comprometer)
1. **Ventana del proyector** en el Mac de la demo: navegador en la segunda pantalla sin pantalla completa nativa, vsync, sin kernel panic; medir el escalón negro→blanco.
2. **Cámara**: webcam UVC a 720p60 en macOS (latencia, exposición fija) + anillo de memoria compartida.
3. **MediaPipe Hands** en Python 3.12 arm64: disponibilidad del wheel y velocidad en el Mac.
4. **YOLO-seg / YOLOE en MPS**: velocidad y calidad con objetos de escritorio.
