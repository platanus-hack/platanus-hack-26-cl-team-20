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
# Chrome: http://localhost:9215/mesa → permitir cámara → Lanzar proyección → Copiar → pegar en una terminal
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

## Interfaz propia de la mesa (pedido de Daniel)

**Escenas (pestañas).** Una mesa tiene varias escenas con nombre; cada una con sus widgets (el modo manos es un widget,
así que también va por escena) y una sola activa en el proyector.

- Tool MCP nueva `scenes` (`action`: list, create, activate, duplicate, delete, rename). `project`, `remove` y `scene`
  aceptan `scene: "nombre"` para trabajar sobre una escena que no está a la vista; todas responden con `scene` (sobre
  cuál actuaron), `scene_active` y la lista. `scene` no se agregó a los esquemas de esas tres tools (son los del
  contrato y hay un test que lo exige): lo documenta la descripción de `scenes`.
- Viven en el nodo navegador y se guardan en la DB del gateway por sesión (tabla `node_state`, un JSON por nodo); al
  recargar la consola se recuperan. Máximo 8.
- Consola y visor: tira de pestañas, clic para activar (en la consola además crear, duplicar y borrar).
- Mesa: franja proyectada con las pestañas, visible en todas las escenas; se cambian dejando el dedo 400 ms. Para eso
  el seguimiento de manos queda prendido mientras la interfaz de la mesa esté activa y haya calibración. Casillas en
  la consola: mostrarla o no, y girarla 180°.
- Cambio de escena → evento `flow_emitted` con `event: "scene_changed"` y `data: {scene, by}` (no hay tipo de evento
  nuevo: el contrato no deja agregar uno sin tocar `contracts/`).
- Zona reservada: la franja y la esquina de voz. `project` devuelve `reserved` si un widget las pisa.

**Botón de voz.** Esquina reservada, visible en todas las escenas. Mientras una mano está sobre «Hablar» (o tras un
clic en la consola o en la ventana del proyector; otro clic manda), la consola escucha con `SpeechRecognition` del
navegador (es-CL, parciales) y el micrófono de ese computador. La mesa muestra el anillo que late y el texto parcial.
Al sacar la mano sale `flow_emitted` con `event: "voice_command"` y `data: {text, seconds, scene}`; también queda en el
registro de la consola y del visor. Sin `SpeechRecognition` graba el audio (queda como enlace en el registro de la
consola) y el evento sale con `text: null, audio_pending: true`. El permiso del micrófono se da una vez con «Activar
micrófono». El reconocimiento de Chrome usa el servicio de voz de Google: necesita internet.

**Objetos publicados por un cliente** (pedido del motor de Max, que entra como cliente MCP y no como nodo).
`scenes` con `action: "objects"` y `objects: [...]` reemplaza los objetos de los clientes MCP (forma del `ObjectState` del contrato:
`id`, `label`, `polygon_mm` de 3 a 64 puntos; `center_mm`, `bbox_mm` y `confidence` opcionales y se calculan si faltan;
máximo 50). Sube `rev`, manda snapshot y emite `object_appeared` / `object_moved` (más de 10 mm) / `object_removed`.
`scene` los trae y los widgets con `anchor.object_id` los siguen (también los html). Es por sesión, no por escena, no se
guarda en la DB. Cada objeto lleva `source` (`mcp` o `motor`): una fuente nunca borra ni pisa los objetos de otra
(un id que ya es del motor da error). Ver «Motor local».

**Foto limpia.** `look` con `clean: true` apaga lo proyectado (widgets, html e interfaz de la mesa), espera la demora
proyector→cámara medida por el último `calibrate` (350 ms si no hubo), toma el cuadro y repone. Vale con `annotate`
true o false; la respuesta trae `clean` y `blanked_ms`. No está en el esquema de `look` (es el del contrato): lo
documentan la descripción de `scenes` y las instrucciones del servidor MCP.

## Motor local (protocolo motor v0.1)

Decisión de la reunión de las 17:16: cámara → **motor Rust local** (segmentación + coordenadas) → esta consola →
ventana del proyector. Protocolo: `docs/arquitectura/PROTOCOLO-MOTOR.md` en la rama `feat/motor-local` (v0 y la
sección v0.1). Código: `console/motor.js`.

- **Conexión.** `ws://127.0.0.1:9230/v1/ws` por defecto; se cambia con `?motor=` (sin ruta se agrega `/v1/ws`) o en
  «Motor local» (paso Proyector). El token llega con `?motor_token=` (el enlace que imprime `pcx up`), se guarda solo
  en `sessionStorage` y ambos parámetros se sacan de la barra de direcciones. Se conecta sola **solo si hay
  emparejamiento** (token o `?motor=`): una visita sin motor no dispara el permiso de red local de Chrome. Primer
  mensaje `hello {v: 0, token, session_id, app}`; reconexión con backoff (0,5 s → 10 s). Si el motor rechaza el token
  u origen (`error` auth, cierre 4401/4403/1008) no reintenta sola.
- **Indicador** en la cabecera: «Motor local: conectado · último ciclo X ms» o «Motor local: sin motor». El panel
  muestra cámara, modelos, estado, etapas y objetos; botones «Percibir ahora» (`perceive`) y «Olvidar objetos»
  (`reset`, también quita del mundo los objetos del motor).
- **`calib`** al recibir `welcome` y cada vez que cambia la calibración o la superficie: `camera_px` = `image_px` de la
  calibración, `matrix_px_to_mm` (9, por filas), `lens` o null, `surface_mm`.
- **`blank`/`unblank`.** La ventana del proyector pone un `div` negro con z-index máximo, encima del canvas, del html y
  de las manos; responde `blanked {id, t_ns}` después de dos `requestAnimationFrame` **en esa ventana** (t_ns =
  `Date.now()` en ns, reloj del sistema). `unblank` quita el negro (la escena sigue dibujándose debajo: vuelve exacta)
  y se responde `unblanked`. Tope de seguridad doble: la ventana se desapaga sola a los `max_ms` (3 s si no viene,
  máximo 10 s) y la consola a los `max_ms` + 250 ms; en ambos casos avisa `unblanked`. Sin ventana del proyector no
  se confirma `blanked` (con `?sim=1` confirma la vista previa, que es lo que ve la cámara simulada). Si se corta el
  motor, nada queda a oscuras.
- **`hands`.** La presencia de manos de MediaPipe (`hands.js`) se avisa solo al cambiar; la ausencia se confirma a los
  120 ms (con el `t_ns` del primer cuadro sin manos). Con el motor conectado y una cenital, el seguimiento de manos
  queda prendido.
- **`objects`** (incremental): se fusionan con `source: "motor"` (`mergeObjects` en `node.js`); los inválidos se
  saltan y se anotan en el registro. Mismos eventos y umbral de 10 mm que antes.
- **`look`.** Con el motor conectado y con cámara, `look` (y `look clean`) piden `look_frame`; la respuesta trae
  `via: "motor"`. Si la foto tiene la proporción de la calibración se trata como la cámara calibrada (grilla y
  `point_px` valen); si no, va como `motor:<id>` sin calibrar. Si el motor falla o tarda (6 s, 12 s limpia), sigue con
  la cámara de la consola y `motor_note`.
- **`need_frame`** (motor sin cámara): se responde `frame` con la cenital a 1280 px.
- **v0.1 · escenas.** Si el `welcome` trae `scenes` o `cameras` (motor v0.1), la consola manda `scene {name}` al
  conectar y cada vez que cambia la pestaña activa. La pestaña se llama igual que el módulo (`lab`, `solar`, `desk`;
  sin distinguir mayúsculas); una pestaña sin módulo manda `null`. A un motor v0 no se le manda.
- **v0.1 · `event {scene, event, data}`** → `flow_emitted {event, data}` al gateway (el agente lo ve con
  `events_wait`, como `voice_command`) y una línea en el registro de la consola.
- **v0.1 · `welcome`**: el panel muestra las cámaras del motor (cenital y lateral) y sus escenas. El primer `objects`
  después de `welcome` trae todos los objetos vigentes y **reemplaza** los del motor que habían quedado (motor
  reiniciado o consola recargada); los siguientes son incrementales. Con un motor v0 solo se fusiona.
- **Sin probar con el motor Rust real ni con proyector real.** Probado con `tools/fake_motor.py`: tests de Python
  (`tests/test_fake_motor.py`), tests JS con `node --test` (`tests/console/motor.test.mjs`, los corre
  `tests/test_console_js.py`, uno de punta a punta contra el motor falso) y una prueba headless (sin cámara) de la
  consola con `?sim=1` + ventana del proyector.

Probar en local:
```sh
cd cloud/gateway && PORT=9215 PCX_OPEN_SESSIONS=1 uv run python -m pcx_gateway
cd cloud/gateway && uv run python -m tools.fake_motor --token secreto   # --sin-camara, --jpeg foto.jpg, --ciclo-s 0
# http://localhost:9215/mesa?sim=1&motor=ws://127.0.0.1:9230&motor_token=secreto → Lanzar → «Percibir ahora»
# una pestaña llamada «lab» activa el módulo lab del motor falso: manda eventos «nivel» (flow_emitted)
```

Riesgos y pendientes:
- **Local Network Access (Chrome 142+).** Desde `https://pcx-gateway.onrender.com`, conectarse a 127.0.0.1 pide
  permiso («apps y servicios de este dispositivo»). Si se niega, el WebSocket falla igual que «sin motor»: el panel lo
  sugiere tras dos intentos. Por eso no se conecta sola sin emparejar.
- **Contenido mixto.** Desde https, `ws://127.0.0.1` y `ws://localhost` valen (son de confianza en Chrome, Edge y
  Firefox); `ws://` a otra IP (un motor remoto) lo bloquea el navegador: la consola no lo intenta y pide `wss://`.
  Safari no probado.
- **Origen.** El motor acepta solo `https://pcx-gateway.onrender.com` y `http://localhost|127.0.0.1:*`: otra URL de
  Render o la consola abierta por IP de la LAN quedan fuera.
- **Reconexión y objetos.** Con v0.1 la consola se pone al día con el `objects` completo después de `welcome`; con un
  motor v0, si la consola se recarga no ve los objetos que el motor ya tenía hasta que cambien. Al cortarse la
  conexión, los objetos del motor quedan en el mundo («Olvidar objetos» los quita).

## Modelo de lente (pedido de Max)

Método medido por el orquestador en `tools/mesa-agente/lente.py`; resultado de referencia en
`data/lente/2026-11-21/lente-modelo.json`. En el nodo navegador:

- La calibración puede llevar `lens: {f, cx, cy, k1, k2}` (radial de OpenCV, focal nominal) y entonces `matrix` lleva
  píxeles **ya corregidos** a mm. `pxToMm`/`mmToPx` de `calib.js` son el único camino: los usan `look` (grilla, que se
  dibuja curva), `anchor.point_px`, `polygon_px`, `from_px`/`to_px`, las manos y `calibrate verify`.
- `calibrate` con `mode: "precise"` (o el botón «Calibrar con lente»): corre quick y después proyecta 144 puntos verdes
  (16×9 cada 60 mm), 3 pares encendido/apagado, componentes conexas, empareja con la calibración vigente (22 px) y
  ajusta k1 y k2 por búsqueda con la homografía por mínimos cuadrados adentro, descartando por rondas los puntos sobre
  objetos altos. Devuelve f nominal, k1, k2, error en px y mm, puntos usados y descartados. Solo guarda el modelo si
  mejora a la homografía sola.
- Cargar un modelo ya medido: pegar el JSON en «Modelo de lente (pegar JSON)» o `calibrate` con el campo extra `model`.
- Resolución: todo se guarda en píxeles de `image_px` y se usa normalizado (manos y video en 0..1; los píxeles de `look`
  se escalan si la foto llega a otro tamaño), así que no hay que reescalar parámetros.
- Visor: el shader quita la distorsión además de rectificar, y muestra «Lente: k1, k2, error mm».

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

## Marca WUI, portada y `/agente` (pedido de Daniel, 21-nov noche)

- **Nombre visible: WUI (World User Interface).** Cambió en títulos, cabeceras, prompt del agente e instrucciones del
  servidor MCP. Los identificadores internos (`pcx_*`, `PCX_*`, servidor MCP `pcx`, `localStorage pcx.*`) no se tocaron.
- **`/` es la portada**: una frase, el botón «Copiar para tu agente» (copia un mensaje corto con el enlace a `/agente`)
  y el enlace «Abrir la mesa sin instalar nada». Si el navegador ya tiene una sesión, dice «Volver a tu mesa».
- **La consola vive en `/mesa`.** `/?cualquier=parámetro` redirige a `/mesa?…` (307), así que los enlaces viejos
  (`/?sim=1`, `/?motor=…&motor_token=…` del instalador de Manu) siguen funcionando. `/` sin parámetros ahora es la
  portada. `/ver/<token>` sigue igual.
- **`/agente`** devuelve texto plano para un agente: motor local, crear sesión (`POST /api/sessions`), abrir
  `/mesa#s=<session_id>.<node_token>.<user_token>` (la mesa adopta esa sesión y borra el fragmento), pedir cámaras,
  pantalla del proyector y micrófono, conectar el MCP con el `user_token` y comprobar.
- **El paso del motor local sale marcado PENDIENTE**: el instalador del PR #13 (`feat/setup-agente`) no está
  publicado (PR sin mergear, repo privado, sin release, dominio provisorio). Cuando haya URL pública, basta definir
  `PCX_INSTALL_URL=<url del install.sh>` en el servicio: `/agente` pasa a mostrar el comando real del `setup.md` de Manu
  (`curl -fsSL "<url>" | sh -s -- --yes --skills --start`) y la URL de la mesa con `motor=` y `motor_token=`.
- Probado en local con Chrome headless: portada (escritorio y 390 px), redirección, sesión adoptada por fragmento y
  nodo conectado. No probado: un agente real siguiendo `/agente` de punta a punta.

## Modo presentación (pedido de Daniel, 21-nov noche)

- **`/presentar/<token>`** (el mismo token de solo lectura de `/ver`): láminas a pantalla completa intercaladas con
  la mesa en vivo. Avanza con flechas, espacio o clic (clic en el cuarto izquierdo retrocede), `F` es pantalla
  completa, Inicio/Fin saltan a los extremos.
- **Láminas:** `{title?, text?, image?, html?}`. `text` acepta párrafos, líneas `- ` y `**negrita**`; `image` es una
  URL o un `data:image`; `html` es un diagrama (svg/html) que va en un iframe sin scripts.
- **En vivo:** `{live: "visor"}` es el visor embebido (cenital rectificada + lo proyectado; sin calibrar muestra la
  cámara tal cual) y `{live: "camara", camera: "cam-2"}` una cámara sola. Pueden llevar `title` (rótulo arriba).
- **Quién las define:** el agente con la tool `present` (`set` con `slides` o `markdown`, `go` con número, `next` o
  `prev`, `get`, `clear`; la respuesta trae `url`) o la persona pegando Markdown o JSON en el paso 4 de la mesa. En
  Markdown: `---` separa, `# ` titula, `![](url)` es la imagen, `@vivo` y `@camara cam-2` son las vistas en vivo.
- Los cambios hechos por quien presenta llegan al agente como `flow_emitted` con `event: "slide_changed"`.
- Las láminas viven en la mesa y se guardan con las escenas (por sesión, máx. 1,2 MB y 80 láminas): sobreviven a
  recargar la consola. La consola de la mesa tiene que estar abierta para presentar.
- Límites conocidos: la cámara que se transmite es una sola por mesa, así que una lámina `camara` cambia también lo
  que ven en `/ver`; la página de presentación cuenta como una persona mirando (más otra por el visor embebido).
- Probado en local con Chrome headless y cámara simulada: tool, teclado, clic, Markdown desde la consola, recarga.
  No probado: con cámara y calibración reales (la vista rectificada es el mismo código del visor), ni en Render.

## Celular como cámara (pedido de Daniel, 21-nov noche)

- En el paso «Cámaras» de la mesa, **«Sumar un celular»** muestra un QR (y el enlace) a **`/cam/<token>`**. El
  teléfono abre la página, acepta el permiso y manda su cámara trasera; en la mesa aparece como una cámara más con id
  **`cel-1`…`cel-4`** (máximo 4 por mesa). Sirve para `look` (`camera_id: "cel-1"`), `devices`, el selector de cámara
  de `/ver` y las láminas `{live: "camara", camera: "cel-1"}` de la presentación.
- **Cómo viaja:** el teléfono manda JPEG (1280 px, calidad 0,72, ~7 cuadros/s) por el WebSocket `/cam`; el gateway
  guarda solo el último cuadro por celular y se lo pasa a la consola por el WebSocket del nodo (binario, con un byte
  adelante que dice de qué celular viene). Es el mismo esquema del visor: nunca se encola, si algo va lento se saltan
  cuadros. Sin la consola abierta, los cuadros se descartan.
- **Token propio (`scope cam`)**, creado con `POST /api/cam-token`: solo abre `/cam`. No sirve para MCP, `/api`,
  `/view` ni `/node` (hay test). Dura lo que la sesión; el mismo QR sirve para varios teléfonos.
- Un celular **nunca se elige solo como cenital** (llega y se va, y cambiar la cenital rompe la calibración); la
  persona puede marcarlo con «Usar como cenital». La calibración y las manos siguen a la cenital.
- El teléfono pide que la pantalla no se apague (Wake Lock) y trae «Cambiar de cámara» (trasera/frontal).
- La librería del QR (`console/vendor/qrcode.js`, MIT, Kazuhiko Arase) va incluida: no depende de un CDN.
- Probado en local con Chrome headless (cámara falsa en una pestaña con tamaño de teléfono): aparece `cel-1`,
  `look` trae su imagen, desaparece al cerrar la pestaña. **No probado con un teléfono real** (iPhone/Android, permiso,
  Wake Lock, red móvil) ni en Render; necesita https, así que el QR de una consola en `localhost` no sirve.

## Botón de voz: sin disparos falsos (21-nov noche)

Con gente conversando alrededor, una mano que pasaba por la esquina «Hablar» grababa y mandaba fragmentos. Ahora,
para la voz con la mano (el clic no cambia):

- hay que dejar la mano quieta **600 ms** sobre el botón, con un anillo de avance y el texto «Mantén»;
- cuenta solo la **punta del índice o el centro de la palma**, dentro de un círculo al **80 %** del dibujado;
- si lo dicho tiene **menos de 3 palabras** o duró **menos de 1,5 s**, no se emite `voice_command`: la mesa muestra
  «No alcancé a escucharte» y el registro dice qué se descartó.

Los umbrales están en la constante `VOICE` de `console/app.js` (`dwellMs`, `zone`, `graceMs`, `minWords`,
`minSeconds`). Ojo: una orden de una o dos palabras («siguiente») dicha con la mano también se descarta; por clic sí
pasa. Probado con manos sintéticas y reconocimiento simulado (roce, borde del dibujo, toque corto, mano quieta 3 s);
no con micrófono ni manos reales.

## Pestañas a la izquierda y paginadas (22-nov)

- La franja de pestañas de la mesa pasó de arriba a una **barra de 150 mm en el lado izquierdo**, con las pestañas
  apiladas; «Girar esa interfaz 180°» la lleva al lado opuesto. El botón de voz sigue en su esquina.
- Si no caben todas (10 puestos en una mesa de 562 mm de alto), la barra se pagina: 8 pestañas por página, con
  **«▲ subir»** arriba y **«▼ bajar»** abajo. Dedo quieto 1,2 s encima (`PAGE_DWELL_MS` en `app.js`) y la barra se
  desliza a la otra página; si el dedo sigue quieto, sigue pasando. También funcionan con clic.
- Al cambiar la escena activa (agente, consola o visor) la barra salta a la página donde está.
- **Máximo 40 escenas** (antes 8). En la consola y el visor las pestañas siguen horizontales, con scroll.
- La zona reservada que informa `project` (`reserved`) ahora es la barra izquierda `[0, 0, 150, alto]` y la esquina
  de voz. Medidas en `UI` de `render.js`.
- Probado con dedo sintético en simulación (23 y 40 escenas). No probado en la mesa real.

## Capa global (22-nov)

`scene: "*"` en `project`, `remove` y `scene` es una capa cuyos widgets se dibujan **en todas las escenas** (marcas de
agua, avisos). No es una pestaña: no aparece en la barra y no se activa, duplica ni borra. Si un id existe en la
escena activa y en la capa global, se ve el de la escena. `scene` sin parámetro trae los widgets de la escena y,
aparte, `global_layer`; `scenes` informa `global_widgets` (cuántos hay). `remove` sin `scene` solo toca la escena
activa y avisa si el id está en la capa global. Se guarda por sesión junto con las escenas. Probado por MCP en
simulación (incluida la recarga de la consola).

## Juegos con la mano: html interactivo (22-nov)

- Un widget `{type: "effect", shader: "html"}` con **`params.js`** corre ese script dentro de su iframe: sandbox
  `allow-scripts` sin `allow-same-origin` (origen opaco), CSP con nonce y **sin red de ningún tipo** (con script
  tampoco se permiten imágenes https). Los `<script>` dentro de `params.html` siguen sin correr.
- **Protocolo** (postMessage, envuelto en el objeto `wui` dentro del iframe):
  - `wui.onHands((hands) => …)`: llega al ritmo del seguimiento de manos (~30 por segundo) con
    `[{tip, palm, pinch, inside, handed, points}]`, en mm relativos al rect del widget (= px de CSS, rotación deshecha);
    `points` son los 21 puntos.
  - `wui.emit(valor)`: llega al agente como `widget_pressed{widget_id, value}` (texto o JSON, máx. 500 caracteres y
    10 por segundo por widget; `EMIT_PER_S` en `html-layer.js`).
  - `wui.size`: `[ancho, alto]` en mm.
  - El ejemplo mínimo (una pelota que sigue el dedo) va en la nota `effects` de la respuesta de `project`.
- Cada pantalla (proyector, vista previa de la consola, visor) corre **su propia copia** del script; los eventos de
  `wui.emit` valen solo desde la ventana del proyector si está abierta, y si no desde la vista previa.
- **Un bucle infinito no congela la ventana del proyector**: Chrome corre los iframes aislados en otro proceso
  (medido con Chrome 157). Pero todos los iframes de la página comparten ese proceso, así que uno colgado congela a
  los demás widgets html. Por eso hay vigilancia: si un script no da señales de vida en 4 s (`WATCHDOG_MS`), se
  detiene el que cargó último y los demás se vuelven a montar en un proceso nuevo (sus scripts parten de cero); el
  agente recibe `flow_emitted` con `event: "widget_killed"`. Los errores del script llegan como `widget_error`.
- En la consola, bajo la vista previa, aparece «Widgets con script» con un botón **«Matar <id>»** por cada uno (lo
  detiene también en la ventana del proyector); detenido, el botón pasa a «quitar».
- Probado en simulación con manos sintéticas: la pelota, el tope de 10 eventos, un error de script, un bucle infinito
  (se detiene solo ese widget y la pelota sigue) y el botón. **No probado** con la ventana del proyector abierta (el
  camino del proyector a la consola es el mismo de los toques html) ni con manos reales.

## Eventos de flujo desde un cliente MCP y órdenes cortas de voz (22-nov, pedido del motor de Max)

- **`scenes {action: "emit", event, data}`** publica un `flow_emitted{event, data}` que los demás clientes leen con
  `events_wait`. `data.by = "agent"` y `data.scene` (la escena activa) se ponen al final y no se pueden
  sobreescribir: nunca se confunde con algo que hizo una persona en la mesa.
  - Nombres reservados (se rechazan): `voice_command`, `scene_changed`, `slide_changed`, `widget_killed`,
    `widget_error`, `home_call_done`, `dwell`, `press`, `pressed` y todo lo que empiece con `widget_`, `object_`,
    `hand_`, `touch`, `confirm`, `home_`, `voice_`, `pcx` o `wui`.
  - El nombre va en minúsculas, números y `_ . : -` (hasta 40); `data` es un objeto de hasta 2000 caracteres; máximo
    5 eventos por segundo. Los toques y confirmaciones físicas son otros *tipos* de evento (`widget_pressed`,
    `confirmation_resolved`), que esta acción no puede producir.
- **`scenes {action: "voice_shortcuts", words: [...]}`** fija las órdenes cortas que el botón de voz sí manda aunque
  tengan una o dos palabras y duren poco (hasta 30, de hasta 3 palabras cada una); sin `words` devuelve la lista. Por
  defecto: listo, atrás, repite, siguiente, anterior. Se compara la frase completa, sin mayúsculas, tildes ni
  puntuación; el `voice_command` lleva `shortcut: true`. La permanencia de 600 ms de la mano sigue siendo obligatoria.
  La lista se guarda por sesión.
- Probado por MCP y con manos sintéticas y reconocimiento simulado; no con micrófono real.

## Versión de la consola y caché (22-nov)

Un `params.js` «no corría» en la mesa aunque el código desplegado estaba bien (comprobado en Render con un
navegador limpio): la consola abierta seguía con módulos anteriores al deploy. Para que no vuelva a pasar:

- Todos los archivos de la consola se sirven con `Cache-Control: no-cache` (el navegador revalida siempre con ETag).
- `/health` trae `console_version` (hash de los archivos de `console/`). La mesa lo revisa cada 30 s: si cambió,
  muestra un aviso naranja para recargar y cada respuesta de tool lleva `console_outdated` con la explicación.

## Audio para los widgets (22-nov)

- Dentro de un widget con script, **`wui.onAudio(({freq, level, hz_per_band, t}) => …)`** entrega ~30 veces por
  segundo el espectro del micrófono del computador: `freq` es un `Uint8Array` de 128 bandas (0..255, de graves a
  agudos, ~188 Hz cada una con 48 kHz) y `level` el volumen de 0 a 1. El ejemplo (barras de espectro) está en la
  nota `effects` de `project`.
- El iframe aislado no puede abrir el micrófono: lo abre la consola (`console/audio.js`, un `AnalyserNode`) y solo
  pasa el espectro, **nunca audio crudo**. Se abre solo mientras algún widget esté suscrito y se cierra al quitarlo.
  Va de la consola a la ventana del proyector; no pasa por el gateway ni llega al visor.
- La consola muestra «Micrófono abierto: N widgets reciben el espectro» mientras esté prendido, o el motivo si falta
  el permiso. Parámetros en `AUDIO` de `audio.js`.
- Probado en la vista previa con el micrófono falso de Chrome. No probado con micrófono real ni con la ventana del
  proyector abierta.

## Pantalla del computador en la mesa (22-nov)

- Widget **`{type: "effect", shader: "screen", rect_mm}`**: muestra en vivo la pantalla o ventana que la persona
  comparte (`params.fit`: `contain` por defecto o `cover`; `params.opacity`). El agente lo ubica y mueve como
  cualquier widget.
- En la consola, bajo la vista previa, el botón **«Compartir pantalla»** abre el selector del navegador
  (`getDisplayMedia`, exige el clic de la persona). Mientras nadie comparte, el widget muestra un aviso y `project`
  responde con `screen: "Nadie está compartiendo…"`. Al dejar de compartir (botón o barra del navegador) vuelve el
  aviso. El agente recibe `flow_emitted` con `event: "screen_share"` y `active: true/false`.
- **Queda local**: el video va de la consola a la ventana del proyector entregando el `MediaStream` entre ventanas
  del mismo origen; no viaja al gateway ni al visor (en `/ver` el recuadro dice que solo se ve en la mesa). La UI lo
  dice junto al botón. `look` la ve solo a través de la cámara.
- Si la consola se recarga hay que volver a compartir (el navegador no deja reanudar una captura sola). En macOS el
  sistema pide una vez el permiso de grabación de pantalla para Chrome.
- Probado con una fuente simulada en lugar del selector de pantalla (headless no tiene pantalla que capturar): vista
  previa, ventana del proyector, aviso al dejar de compartir y eventos. No probado con una pantalla real.

## Preferencias que sobreviven a la recarga (22-nov)

- **Micrófono:** si el permiso ya está concedido, la consola queda en «Micrófono listo» sola y esconde el botón.
- **Patrón de prueba:** se recuerda por sesión; si se apagó (a mano o porque el agente proyectó), sigue apagado.
- **Proyector:** «Lanzar proyección» cierra la ventana anterior y abre una nueva (reutilizar una abierta hacía que el
  navegador ignorara la pantalla completa), y la pantalla elegida para el proyector se recuerda por nombre.
- Probado en headless: patrón, micrófono y relanzamiento (queda una sola ventana). **No pude comprobar** que la
  ventana nueva entre en pantalla completa: eso depende del permiso de ventanas y de un monitor real.

## Foto limpia y calibración ya no reinician los scripts (22-nov)

`look clean: true` y `calibrate` escondían los widgets html desmontando sus iframes, así que un script interactivo
(`params.js`) partía de cero después de cada foto limpia. Ahora la capa html se esconde (`HtmlLayer.hide`,
`visibility: hidden`) sin desmontar nada: el script sigue corriendo con su estado, en la consola, el proyector y
el visor. Probado en simulación con la ventana del proyector abierta: tras `look clean` y `calibrate` el script
arrancó una sola vez.

## Falta

- **Probar con cámara y proyector reales** y con Claude Code de verdad (acá se probó con cámara simulada y cliente MCP).
- **Pantalla completa**: el navegador exige un clic en la ventana del proyector si no la abre ya completa.
- **Escala real**: la superficie mide 1000 unidades de ancho; son mm solo si la proyección mide 1 m.
- **Sin soporte en el navegador**: `run_flow`, `home`, detección y tracking de objetos. Responden un error claro.
- **Manos**: la pestaña de la consola debe quedar a la vista; tapada, el navegador la frena a ~1 cuadro/s.
- **Motor Rust local**: conectado por el protocolo motor v0 (ver «Motor local»); falta probarlo con el motor real.
- **Deploy** (no hecho, falta OK): `render.yaml` ya trae `PCX_OPEN_SESSIONS=1`, pero el Blueprint despliega
  `manu/plataforma`; hay que mergear esta rama ahí o apuntar el servicio a `daniel/consola`.
- **Limpieza**: las sesiones viejas no se borran de la DB; `request_id` todavía no deduplica reintentos en el navegador.
