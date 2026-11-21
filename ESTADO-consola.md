# Estado de la consola (sáb 21-nov)

Rama `daniel/consola`, nacida de `manu/plataforma` @ 8e7d213.

## Qué expone hoy el gateway

- **MCP remoto** en `/mcp` (Streamable HTTP sin estado, Bearer estático). 11 tools: `scene`, `look`, `project`, `remove`,
  `ask`, `events_wait`, `run_flow`, `calibrate`, `devices`, `home` (del contrato) y `history` (propia del gateway).
  - Las responde el gateway solo, desde su espejo en DB: `scene`, `devices`, `events_wait`, `history`.
  - Las reenvía al nodo como `tool_call` y espera `tool_result`: `look` (20 s), `project`/`remove`/`run_flow` (15 s),
    `ask` (plazo + 5 s), `calibrate` (180 s), `home` (30 s).
- **WSS de nodos** en `/node`. El token va **dentro** del `hello` (no en un header), así que un navegador puede conectarse.
  Secuencia: `hello` → `welcome` → `state_snapshot` → `history_gap`* → `event_batch{replay}` → `replay_done`; después
  `event_batch` en vivo, `tool_call`/`tool_result` y `heartbeat` cada ~10 s (sin mensajes 35 s, el gateway corta).
- **HTTP**: `/health` público; con Bearer `/api/nodes`, `/api/history`, `/api/objects/{id}/thumbnail`,
  `/api/confirmations`.
- **Identidad**: un solo trío usuario → espacio → nodo (`demo-*`), creado desde tres variables de entorno.

## Qué faltaba para que un navegador haga de nodo

1. **Sesiones por visita.** Con un trío fijo, dos personas se pisan el nodo y habría que pegar el token global en la página.
   Falta un `POST /api/sessions` que cree usuario + espacio + nodo con tokens propios.
2. **Servir la consola.** El gateway no sirve archivos y no tiene CORS. Lo más corto: que el mismo gateway sirva la consola
   (mismo origen, un solo servicio en Render, sin CORS).
3. **El nodo en JavaScript.** No existe: hay que hablar el protocolo de arriba y resolver `look` (cuadro de la cámara),
   `project`/`remove`/`ask` (dibujar en la ventana del proyector) y responder con claridad lo que no se puede todavía.
4. **Lo que el navegador no puede hoy** (queda en el nodo Rust): manos, objetos, calibración automática (ArUco, gray code),
   `run_flow`, domótica. El espejo del gateway aguanta un mundo sin objetos ni transformaciones.

## ¿Ya existe una consola en algún PR?

No. #6 (plugin y skills), #7 (modos), #10 (instaladores), #11 (pcx-agent) y #12 (catálogo) no traen interfaz web. #9
(seguridad) toca `app.py`, `config.py`, `store.py` y `gateway.py` del gateway: mis cambios ahí son chicos y aditivos para que
el merge sea fácil. `web/` es solo la landing; de ahí saco colores y tipografías.

## Sobre el camino propuesto

Lo sigo, con dos ajustes:
- **La consola la sirve el gateway** (`/`), no Vercel: un solo servicio, mismo origen, y el `render.yaml` casi no cambia.
  Los archivos son estáticos y sin build; se pueden mover a un static site después.
- **Detección del nodo Rust local**: queda para después del recorrido de punta a punta. El nodo Rust hoy entra al gateway
  con el trío `demo-*`; para que la consola lo use hay que pasarle las credenciales de la sesión (un comando más que copiar).

## Hecho (13:15)

Recorrido de punta a punta funcionando en local, probado con Chrome (cámara de prueba) y un cliente MCP real:
sesión → cámara con feed → ventana del proyector → `look` devuelve el cuadro → `project` dibuja → toque → evento.

- **Gateway**: `POST /api/sessions` (con `PCX_OPEN_SESSIONS=1`) y la consola servida en `/`. 27 tests en verde, ruff limpio.
- **Consola** (`cloud/gateway/src/pcx_gateway/console/`, estática y sin build):
  - Cámaras: se detectan solas, feed de cada una, se elige la principal; enchufar o sacar una se refleja al tiro.
  - Proyector: lista monitores (Chrome/Edge), abre la ventana en el elegido y pide pantalla completa; patrón de prueba
    y vista previa de lo proyectado en la misma página.
  - Agente: botón «Copiar» con el comando para Claude Code, Codex u otro cliente MCP, con el token de la sesión; lista
    en vivo de lo que hace el agente, con la foto que vio en cada `look`.
- **Nodo navegador**: `look`, `project` (label, halo, arrow, panel, button, image; effect como pulso de luz), `remove`,
  `ask`; clic sobre un widget = toque (`widget_pressed`). `scene`, `devices`, `events_wait` e `history` las contesta el gateway.

## Cómo probar

```sh
cd cloud/gateway && PORT=9215 PCX_OPEN_SESSIONS=1 uv run python -m pcx_gateway
# Chrome: http://localhost:9215/ → permitir cámara → Lanzar proyección → Copiar → pegar en una terminal
```

## Agregado después (tarde del 21-nov)

Desplegado en `https://pcx-gateway.onrender.com` desde esta rama (cada push despliega). Todo vive en el nodo navegador:

- **`calibrate`** (quick, precise, verify): proyecta marcas una a una, las busca en la cámara cenital por diferencia de
  cuadros y guarda la homografía píxeles de `look` → mm. `look` trae grilla cada 100 mm; `project` acepta
  `anchor.point_px`, `polygon_px` (halo) y `from_px`/`to_px` (arrow). Sin calibrar rige la **manual de 4 puntos**,
  editable en la consola.
- **Manos** (`effect` con `shader: "hands"`, o la casilla «Manos»): MediaPipe Hands en la pestaña de la consola; la
  ventana del proyector dibuja esqueleto, estela del índice y resalta lo que hay bajo el dedo; 400 ms quieto =
  `widget_pressed{trigger: "dwell"}`. `scene` con `include_hands` trae las manos (2 Hz).
- **UI html** (`effect` con `shader: "html"`, `rect_mm`, `params: {html, css, rotate_deg}`): iframe aislado (sin scripts
  del agente, sin red salvo imágenes https), 1 px de CSS = 1 mm; `data-press="nombre"` emite `widget_pressed` con ese valor.
- Halo con `radius_mm`, cenital externa por defecto, label de 28 mm y escala de texto en la consola.
- `?sim=1` abre la consola con una cámara simulada que ve la proyección girada 180° (para probar sin equipo).

Probado con cámara simulada, manos sintéticas y cliente MCP real. **No probado por mí con mano y proyector reales.**
Los campos nuevos (`point_px`, `radius_mm`, shaders `hands`/`html`) no están en `contracts/`: el gateway los deja pasar
porque el contrato no prohíbe campos extra, pero no salen en el esquema de `tools/list` ni los entiende el nodo Rust.

## Enlace para ver en vivo

«Copiar enlace para ver» (paso Agente) entrega `/ver/<token>`. Quien lo abre ve la cámara cenital, lo proyectado
(widgets, html y manos, con el mismo `render.js`) y el registro del agente. Solo mirar.

- El token es de scope `view` (`POST /api/view-token` con el token de usuario): solo abre el WebSocket `/view`; da 401
  en `/mcp` y `/api`, y no sirve en `/node`.
- El gateway le avisa al nodo navegador cuánta gente mira (`viewers`); solo entonces el nodo manda JPEG de la cenital
  (640 px, ~9 por segundo) y mensajes `view` (mundo, manos a 15 Hz, registro) por su mismo WSS. El gateway los reenvía
  sin decodificar ni guardar.
- Memoria: por cada persona se guarda **un** cuadro (el último) y el último mensaje de cada tipo; si va lenta, se salta
  cuadros. Máximo 6 mirando por mesa y 256 KiB por cuadro.
- **Vista combinada**: el visor deforma el video a la superficie proyectable con la homografía (WebGL, en el visor) y
  dibuja encima lo proyectado; deslizador de opacidad (100 % solo proyección, 0 % solo cámara) e interruptor para ver la
  cámara sin rectificar. Sin calibración muestra cámara y proyección por separado. Botón «Descargar foto» (resolución
  nativa de la cámara).
- **Calidad**: parte en 1280 px y JPEG 0,8 con una persona mirando; baja a 960 y 640 si el gateway ve que alguien pierde
  más de un tercio de los cuadros o si la subida del nodo se atrasa, y vuelve a subir tras 15 s sin atrasos.
- Pendiente: WebRTC directo entre navegadores (el gateway solo de señalización) para video fluido y sin pasar por Render.

## Diseño (no implementado): elegir dónde corre el procesamiento pesado

Pedido de Max: que SAM, DINO y lo demás puedan correr en el computador de la mesa o en otro (un PC con GPU en la casa),
dejando en la mesa un cliente liviano tipo Raspberry Pi.

**Idea: separar el nodo en dos roles que comparten un espacio.** Hoy un espacio tiene un solo nodo que hace todo. Pasaría a
tener un nodo de **mesa** (cámara, proyector, manos, toques: lo que exige baja latencia) y, opcional, uno o más nodos de
**cómputo** (sin cámara ni proyector) que anuncian en su `hello` las capacidades que ofrecen: `segment`, `detect`, `embed`.

- **Gateway**: `nodes.space_id` deja de ser único y cada nodo guarda sus `capabilities`. Al llegar una tool, el gateway
  elige el nodo por capacidad: `look`, `project`, `calibrate` → mesa; `segment`, `detect` → cómputo si hay uno conectado,
  si no la mesa (si puede), si no un error claro. La preferencia se guarda por espacio y se cambia desde la consola
  («Procesar en: esta máquina / PC de la casa / automático»).
- **Cuadros**: el nodo de cómputo necesita la imagen. Camino simple: el gateway le pide un cuadro a la mesa (un `look`
  interno, sin anotar) y se lo pasa al de cómputo dentro del `tool_call`; sirve para tools sueltas (segmentar una foto
  tarda 1–2 s igual). Para tracking continuo conviene un canal directo mesa→cómputo (WebRTC o WSS dentro de la tailnet),
  con el gateway solo de señalización: es el mismo mecanismo que el WebRTC del enlace para ver.
- **Resultados**: el nodo de cómputo devuelve geometría en píxeles de la foto (polígonos, cajas, etiquetas); el nodo de
  mesa, que tiene la homografía, la pasa a mm y actualiza `objects` del mundo. El agente no nota la diferencia.
- **Mesa liviana**: una Raspberry Pi con Chromium en modo kiosco abriendo esta consola ya hace de nodo de mesa (cámara,
  ventana del proyector, enlace). MediaPipe Hands en una Pi 5 anda lento (5–10 cuadros/s): las manos también se podrían
  declarar como capacidad y mandarse al nodo de cómputo, pagando ~50–100 ms de red local.
- **Nodo de cómputo**: el mismo binario `pcx` con `pcx serve --role compute` (los modelos ONNX de `pcx-objects`) o un
  script Python con el protocolo de `/node`; se enlaza a la sesión pegando un comando desde la consola, igual que el agente.
- **Orden sugerido**: 1) capacidades por nodo y ruteo en el gateway; 2) tool `segment` con cuadro reenviado por el
  gateway; 3) canal directo para tracking; 4) selector en la consola.

## Falta

- **Probar con cámara y proyector reales** y con Claude Code de verdad (acá se probó con cámara simulada y cliente MCP).
- **Pantalla completa**: el navegador exige un clic en la ventana del proyector si no la abre ya completa.
- **Escala real**: la superficie mide 1000 unidades de ancho; son mm solo si la proyección mide 1 m.
- **Sin soporte en el navegador**: `run_flow`, `home`, detección y tracking de objetos. Responden un error claro.
- **Manos**: la pestaña de la consola debe quedar a la vista; tapada, el navegador la frena a ~1 cuadro/s.
- **Nodo Rust local**: que la consola lo detecte y le pase la sesión.
- **Deploy** (no hecho, falta OK): `render.yaml` ya trae `PCX_OPEN_SESSIONS=1`, pero el Blueprint despliega
  `manu/plataforma`; hay que mergear esta rama ahí o apuntar el servicio a `daniel/consola`.
- **Limpieza**: las sesiones viejas no se borran de la DB; `request_id` todavía no deduplica reintentos en el navegador.
