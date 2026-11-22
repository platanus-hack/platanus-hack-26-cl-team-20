# Informe técnico: app del Quest, calibración Quest↔proyector, PC Windows y arquitectura distribuida

Alcance: `quest/` (WebXR), `quest-nativo/` (Kotlin), `playground/calib_quest_proyector.py`, `playground/lienzo_quest.py`,
`windows/`, `docs/SETUP-MESA-QUEST.md`, requests 005 y 009–013. Snapshot `sl-scanner` b8fecff. Evidencia como `ruta:línea`;
lo inferido va marcado como tal.

## 1. Propósito y funcionalidades

En el repo hay tres generaciones de «Quest + proyector», además del PC Windows y de seis requests de diseño.

**v1, WebXR (`quest/`, 5-nov).** El Quest Browser abre una sesión `immersive-ar`, renderiza la *vista del proyector*
(K + T_rig→proyector, en modo cabeza o fijo) y la transmite por WebRTC H.264 al navegador de un proyector Android
(`proyector.html`). La calibración se hace tocando cruces proyectadas con la punta del control, previamente calibrada por
pivote. Efectos: wireframe, aristas, mesa y un pozo anamórfico. Incluye un simulador con el escaneo del iPad. **No usa las
cámaras del casco**: WebXR no da acceso a passthrough (`quest/README.md:122-123`). Nunca se verificó en el casco
(`quest/README.md:124-126`).

**v2, app nativa `com.smartup.proyector` (`quest-nativo/`).** Usa Spatial SDK 0.13.2, MRUK y OpenCV 4.10
(`app/build.gradle.kts:44-56`). **Sí usa la cámara passthrough izquierda como sensor** (`ScanCamera.kt:137-148`, permiso
`HEADSET_CAMERA` en `AndroidManifest.xml:32`). El proyector sale por USB-C→DP→HDMI, como `Presentation` o como un panel
pegado al ojo izquierdo que se espeja (`SalidaProyector.kt:18-30`). **La calibración se hace desde el casco**:
- Gray code en lazo cerrado (`Calibrador.kt:22-37`).
- ArUco detectado con OpenCV para hacer PnP del proyector de la mesa (`ArucoMesa.kt:20-24`).
- Códigos QR seguidos por MRUK (`Marcadores.kt`).

Además expone profundidad por raycasts, body tracking y un **monitor HTTP con una escena 3D**
(`assets/monitor/3d.html`) servido desde el propio casco en el puerto 8787.

**v3, Quest fijo como cámara de la mesa (desde el 9-nov, `docs/SETUP-MESA-QUEST.md`).** El casco queda colgado junto al
proyector y solo actúa como servidor de sensores: cámara, pose y malla en :8787. Todo el cómputo corre en el Mac:
- Feed en :8798.
- Ventana del proyector en :8793, que llega al proyector por AirPlay.
- Calibración en `playground/calib_quest_proyector.py`, que proyecta Gray code por la ventana y lee los cuadros del Quest
  por HTTP.
- `lienzo_quest.py` arma el plano métrico de la mesa a 0,4 mm/px.

**PC Windows.** Es un nodo con GPU administrado por SSH sobre Tailscale (`windows/README.md:1-9`). Corre `hands_service`
(Brio + MediaPipe → WebSocket), `camserve`, un puente MIDI y el runtime :8790, que arranca solo gracias al autologin.

Respuestas directas a las preguntas del encargo:
- ¿Usa las cámaras passthrough del casco como sensor? v1 no; v2 y v3 sí, solo la izquierda.
- ¿Muestra un monitor 3D? Sí, en el navegador (`index.html` + `3d.html`, servidos por la app o por `monitor_adb.py`
  en :8810).
- ¿Calibra el proyector desde el casco con ArUco o gray code? Sí: Gray code dentro de la app para el proyector montado en
  el rig, y ArUco dentro de la app para el proyector de la mesa. La versión que terminó funcionando es la v3: el Gray code
  lo proyecta el Mac y la cámara del Quest lo lee.

## 2. Arquitectura interna

### 2.1 WebXR (JS, sin build)

| Módulo | Responsabilidad |
|---|---|
| `app.js` | Orquestador: configuración por URL (`:15-40`), máquina de calibración por toques (`:115-186`), toque promediado en 300 ms con rechazo si se movió más de 2 cm (`:272-284`), pivote (`:293-317`), bucle XR (`:515-598`), simulador y calibración sintética (`:606-724`) |
| `calib.js` | Solver puro: DLT de Hartley (`:195-212`), RQ (`:233-256`), LM con jacobiano numérico (`:323-379`), PnP con K fija, plano por Zhang (`:271-301`), `calibrar` con varios arranques (`:394-452`), pivote (`:479-496`) |
| `proyector-cam.js` | `ProyectorCam`: K→matriz de proyección three.js (`:10-19`), ajuste manual (yaw, pitch, roll, zoom, dx, dy, dcx, dcy). `PredictorPose`: extrapolación lineal y angular suavizada (`:169-203`) |
| `sala.js` | XRMesh/XRPlane → geometrías del casco y del proyector (`:226-263`), raycast con mallas solo cada 3 cuadros (`:275-296`), pozo anamórfico (`:322-364`) |
| `overlay.js`, `panel.js`, `log.js` | Cruces y errores en píxeles del proyector; panel XR dibujado en un canvas; logs agrupados por `POST /api/log` cada 400 ms |
| `transporte.js` | Fuente de video, señalización por WS o PeerJS, `Emisor`/`Receptor` WebRTC |
| `sim.js` | Proyector «verdadero» que proyecta el canvas sobre el escaneo con textura proyectiva y mapa de sombra |
| `server.py` | aiohttp: estático, `/ws`, `/api/log`, `/api/calib`, `/api/jev`, `adb reverse` |
| `calib_solver.py` | El mismo solver en numpy y OpenCV, para validar el JS |

Flujo: `getViewerPose` → `PredictorPose` → `ProyectorCam.actualizar` → segundo `WebGLRenderer` a 1280×720 →
`VideoFrame` → `MediaStreamTrackGenerator` → RTCPeerConnection → `<video>` en el proyector.

### 2.2 Nativa (Kotlin, unas 6.400 líneas)

- **`MainActivity`** (2.150 líneas) concentra casi todo: permisos, MRUK, paneles, sistemas ECS, más de 40 comandos de
  texto (`:629-754`), bucle ArUco, profundidad, JSON del monitor y guardar/cargar. Es un *god object*.
- **`ScanCamera`**: camera2 con el YUV más grande disponible. Convierte intrínsecos y extrínseca de `LENS_POSE_*` a la
  convención de Spatial SDK (`:172-194`). Entrega tres salidas: copia de luma, NV21 a la mitad para el monitor y
  `onCuadro` para el calibrador.
- **`HistorialPose`**: buffer circular de 512 poses con interpolación slerp en un timestamp dado (`:46-55`) y predicción
  con ventana de 30 ms acotada a 200 ms (`:62-85`).
- **`Calibrador`**: hilo propio que corre latencia → Gray → decodificación → raycast → ajuste → lazo.
- **`GrayCode`** (decodificador incremental), **`Proyeccion`** (`ModeloProyector` con P 3×4 y `AjusteProyector`
  RANSAC+LM), **`RejillaCorreccion`**, **`GeometriaSala`** (MRUK → triángulos con baricéntricas para dibujar aristas),
  **`ArucoMesa`** (incluye la vista `VistaAruco`), **`Marcadores`** (QR con ZXing), **`CalibIO`** (JSON, imágenes,
  distorsión de camera2), **`Mat`** (álgebra sin dependencias).
- **`RendererProyector`**: EGL y GLES3 propios en un HandlerThread. Primera pasada a un FBO de 1920×1080 (patrones,
  carta, mapeo de la malla, puntos, líneas 2D); segunda pasada de warp (ajuste 2D + rejilla) hacia la Surface de destino
  (`:406-434`, `:529-621`). Guarda el instante en que se presentó cada contenido, que el calibrador usa para medir
  latencia (`:610-614`).
- **`SalidaProyector`**: política AUTO, PRESENTACION, PANEL o NINGUNA (`:81-93`).
- **`ServidorMonitor`**: servidor HTTP/1.1 hecho a mano, solo GET, una petición por conexión.

Flujo del modo mapeo: ECS → `HistorialPose` → `vistaRenderer()` (pose predicha o fija, `MainActivity.kt:538-552`) →
`P·T_cabeza←mundo` → malla → FBO → warp → HDMI.

Flujo de calibración: patrón → swap con su marca de tiempo → cuadro de la cámara a +latencia → decodificar → raycast →
ajuste → rejilla → lazo.

### 2.3 Mac, Python

`calib_quest_proyector.py` (2.485 líneas) tiene siete pasos (`captura|decodificar|geometria|resolver|pixel|lazo|exportar`)
más `bundle` y `verificar`. Cada paso lee y escribe JSON y NPZ en `captures/quest-cal-*`. Clases principales: `Sesion`,
`Proyector` (HTTP a la ventana), `CamaraQuest`, `Capturador`, `Cam`, `Malla`, `Sim`, `Modelo`, `VigiaDCPEXT`.

`lienzo_quest.py` arma las homografías cámara→lienzo→contenido y el remap denso; `AlturaMalla` calcula la altura sobre la
mesa con la malla MRUK estática.

`tools/monitor_adb.py` (puerto 8810) hace de proxy de :8787 y cae a adb (logcat, dumpsys, pull de `files/`) cuando la app
no responde.

### 2.4 Windows

- `setup.ps1` y `bootstrap.ps1`: sshd, firewall limitado a LAN y tailnet, llave del Mac, uv, venv y energía.
- `autologin.ps1`: Sysinternals Autologon y tarea programada del runtime :8790.
- `doctor.py`: chequeo de GPU, cámaras, displays y puertos serie.
- `camserve.py` y `hands_service.py`: servidores de cuadros y de manos.
- Desde el Mac se administra con `slscan/remote.py`: comandos por SSH y sincronización del código con `tar` por el mismo
  canal SSH.

## 3. Ejecución y concurrencia

**Hilos de la app nativa** (inventario verificado en el código):
- Main: UI, comandos y **todos los raycasts de MRUK**, por tandas de 300 (`MainActivity.kt:1097-1125`,
  `Calibrador.kt:408-444`).
- ECS: `SistemaCabeza` en cada cuadro guarda la pose y lee los botones cada 20 ms (`:555-612`). `SistemaMarcadores` corre
  a 20 Hz.
- `ProyectorGL`: Choreographer.
- `ProyectorCam`: ImageReader con `maxImages=3` y `acquireLatestImage`, más un executor de la sesión de cámara.
- `ProyectorCal`.
- `Aruco`: como máximo unos 8 Hz, porque duerme hasta completar 120 ms (`:1092`).
- `Depth`: como máximo 10 Hz (`:1545-1562`).
- `MonitorAccept` más un pool sin límite de hilos, con tope de 24 conexiones.

Casi todo el estado compartido son campos `@Volatile` y algunos `synchronized`; no hay un modelo de propiedad de datos
claro.

**Frecuencias:**
- Cámara a 30 fps.
- Cuadro del monitor como máximo cada 70 ms (`ScanCamera.kt:215`), medido en **12,7 fps** (`SETUP-MESA-QUEST.md:77`).
- Copia de luma cada 150 ms.
- La página `3d.html` pide `/estado.json` cada 200 ms (`3d.html:553`) y `render.jpg` a 5 Hz (`:555`).
- El estado de la UI se refresca cada 500 ms y se escribe en el log cada 10 s.

**Memoria (calculada sobre cuadros de 1728²):**
- Una luma pesa 3 MB.
- El decodificador Gray reserva 5 `IntArray(w·h)`, unos 60 MB, más 24 MB para los mapas u y v (`GrayCode.kt:64-66`).
  El pico total supera los 100 MB contando el acumulador de `promedio` y `Blobs`.
- FBO RGBA8 de 1920×1080: 8,3 MB más el buffer de profundidad.
- Rejilla de 49×28 RG32F.
- Malla como triángulos sueltos de 13 floats por vértice (`RendererProyector.kt:46`).
- `HistorialPose`: 512 poses, unos 6 s a la cadencia del ECS (inferido).

**WebXR:** un solo `setAnimationLoop`. El render del proyector y el envío se limitan a `fps` (`app.js:429-434`). Usa dos
contextos WebGL con `preserveDrawingBuffer` (`:51`). El panel se redibuja cada 300 ms, la UI cada 400 ms y las
estadísticas cada 1 s.

**PC, `hands_service`:** hilo de captura y hilo de detección (≤30 Hz, `:404-433`), un hilo por cliente WS que lee de un
*hub* de «último mensaje» con `Condition` (`hands_service.py:361-381`, `:436-478`), y HTTP con hilos.

**Calibración en el Mac:** es secuencial y espera al menos 1,5 s por patrón (`calib_quest_proyector.py:740`).

## 4. Comunicación e interfaces

```
                  LAN 192.168.4.x (Quest .161, Mac .182) · PC 192.168.1.233 · Tailscale 100.64/10
┌─ Quest 3 fijo (app :8787, sin auth, CORS *) ─┐  adb forward USB / Wi-Fi    ┌──────────── Mac ────────────────┐
│ /cam.mjpg 864² ≤12,7 fps · /cam_full.jpg     │◀── HTTP GET ────────────────│ superficie_feed :8798            │
│ (luma 1728², X-Ts-Ns) · /cam_pose.json?ts=   │                             │ calib_quest_proyector (offline)  │
│ /estado.json /malla.json /depth.* /cmd?c=    │◀── /cmd o am broadcast ─────│ monitor_adb :8810 (proxy + adb)  │
│ files/calib_mesa.json ◀──────────────────────┼── adb push + run-as ────────│ quest/server.py :8800/:8801      │
└──────────────────────────────────────────────┘                             │ ventana_proyector :8793 POST /imagen ≤5 Hz
                                                                              └───────────┬─────────────────────┘
                                                              AirPlay (~313 ms extremo a extremo) ▼
                                                          proyector → mesa → lo ve la cámara del Quest
┌─ PC Windows (RTX 4070) ─────────────────────────────────────────────────────────────────────┐
│ hands_service WS :8793 (JSON 15–30 Hz) / HTTP :8794 (/frame.jpg|png, /hands.json) ──▶ Mac   │
│ midi_bridge ── UDP :8796 JSON ──▶ Mac · midi_play ◀── ssh stdin · runtime :8790 · sshd :22  │
│ VNC :5900 ◀── playground/vnc.py · slscan remote (SSH + tar)                                 │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
v1: Quest Browser ── WebRTC H.264 (LAN, mDNS) ──▶ proyector.html · señalización /ws de server.py o PeerJS cloud
```

**Señalización WebRTC** (`server.py:80-120`, `transporte.js:98-129`). Mensajes:
- `hello{rol,sala}` → `welcome{id,peers}`
- `join` / `leave`
- `signal{to,data}`, donde `data` es `{sdp,n}`, `{ice,n}` o `{pedir:1}`. El contador `n` descarta respuestas a ofertas
  viejas.

Heartbeat de 20 s. El receptor vuelve a pedir oferta cada 4 s (`:216-220`). Para el bitrate se usan los parámetros
`x-google-*` en el SDP (`:74-77`), `maintain-framerate` (`:79-89`) y `playoutDelayHint=0` (`:91-94`). Sin STUN en la LAN
salvo `?stun=1`; con PeerJS se usa STUN de Google.

**App nativa:** HTTP sin keep-alive (`Connection: close`) y MJPEG `multipart/x-mixed-replace` (`ServidorMonitor.kt:156-177`).
Endpoints principales (`MainActivity.kt:2040-2105`): `/estado.json`, `/malla.json`, `/profundidad.json`,
`/depth.jpg|json|mjpg`, `/cam.jpg|mjpg`, `/cam_full.png|jpg` (con cabeceras `X-Ts-Ns`, `X-Frame-Index`, `X-Exposure`),
`/cam_pose.json?ts=`, `/render.jpg|mjpg`, `/cuerpo.json`, `/log`, `/cmd?c=` y el estático. Los comandos llegan como texto
libre, por `/cmd?c=` o por `am broadcast` (`MainActivity.kt:615-627`).

**Otros canales:**
- `quest/server.py`: `/api/log`, `/api/calib` (GET y POST) y `/api/jev`, que usa la llave del AI Gateway del Mac, solo
  acepta IPs no globales y tiene tope de 120 llamadas por minuto (`server.py:191-246`).
- Mac → ventana del proyector: `POST /imagen` en JPEG q90 a 5 Hz como máximo (`SETUP-MESA-QUEST.md:25`); `/mostrar?path=`
  para los patrones de calibración.
- `aruco_grid.py` → runtime `:8790/api/command` con `overlay` (`tools/aruco_grid.py:53-57`).
- PC → Mac: WS de manos, UDP 8796 para MIDI y ssh stdin para `midi_play` (`windows/README.md:52-57`, `:71-73`).

**PC→Mac:** mensajes JSON por WS con `t` (época del PC) y `seq`. El cliente manda `ping{t0}` y el PC responde `pong{t0,t}`
(`hands_service.py:18-19`).

**Sincronización de relojes:**
- **Mac↔Quest: no se sincroniza.** Se evita el problema pidiendo la pose *en el reloj del casco*: `/cam_full` devuelve
  `X-Ts-Ns` y luego `/cam_pose.json?ts=` interpola la pose en el `HistorialPose` (`MainActivity.kt:1505-1541`). Dentro del
  casco todo usa boottime, y la base del timestamp de la cámara se detecta con el primer cuadro (`ScanCamera.kt:315-327`).
- **Mac↔PC:** desfase estilo NTP tomado de la muestra con menor ida y vuelta (`playground/hands/client.py:110-148`).
- **Mac↔ventana↔Quest:** no hay sincronía de cuadros; el Mac detecta el cambio en la imagen y espera a que se estabilice
  (`calib_quest_proyector.py:735-759`).

**Fusión de marcos:** el mundo común es el marco de la sala de MRUK (Spatial SDK, zurdo, metros). La pose de la cámara se
calcula como pose de la cabeza × extrínseca de la lente. Las calibraciones se guardan como `P_sala = P_mundo·T_mundo←sala`
y se re-anclan al cargar (`MainActivity.kt:1356-1400`). En el Mac se convierte a un marco diestro con `FZ=(1,1,-1)` y `FY`
(`calib_quest_proyector.py:80`, `:1598`). El `bundle` acumula sesiones en el **marco de la cámara del Quest**,
aprovechando que el montaje es rígido (`:1603-1681`), y después lo lleva al mundo actual con la pose de ahora
(`:1684-1731`). La mesa tiene su marco en mm (ancla TABLE) y encima va el lienzo. **No hay fusión multicámara**: la Brio
del PC se registra aparte (inferido; su calibración la analiza otro agente).

## 5. Algoritmos

**v1, toques.** DLT seguido de LM (fx=fy) o PnP con K fija. Si los toques caen todos en un plano se activa el «doble
toque» (`app.js:134-137`). Resultados en sintético (los reproduje): con 1 cm de ruido y 12 cruces, el modelo queda a
~5,6 px de mediana a 2 m. Con 0,5 cm, ~2,8 px. El pivote queda en 0,49 mm. Si es una sola pared con K fija, el error a 2 m
sube a ~12,7 px.

**v2, lazo cerrado en el casco** (`Calibrador.kt:203-353`):
1. Latencia con negro→blanco tres veces y máscara de la zona iluminada.
2. Gray de 10+10 bits con su inverso: 40 patrones.
3. **Descarte automático de bits finos** cuando la fracción válida baja de 0,6 (`GrayCode.kt:98-104`).
4. Muestras subpíxel por ajuste afín local 7×7 con jacobiano (`:155-218`).
5. Rayos desde la pose de la cámara a mitad de la captura, contra el Environment Raycaster o, como respaldo, la sala MRUK.
   Si el rig se movió más de 1 cm o 0,5°, la captura se aborta (`Calibrador.kt:285`).
6. RANSAC-DLT (8 puntos, 400 iteraciones, 6 px) y LM con 11 parámetros físicos y corrección de quiralidad
   (`Proyeccion.kt:205-349`). Si los puntos son coplanares, cae a un modelo restringido con barrido de f y cy (`:356-431`).
   Varias vistas se acumulan y se ajustan juntas.
7. Rejilla residual con celdas de 40 px y pesos gaussianos (`RejillaCorreccion.kt:47-57`).
8. Lazo con hasta 84 puntos (12×7) detectados como blobs. El error de cámara se lleva a píxeles del proyector con el
   jacobiano. Hasta 3 iteraciones o hasta bajar de 0,8 px (`Calibrador.kt:447-519`).

Resultados solo en sintético: centro a 1,1 mm y 0,17 px en la pared (`quest-nativo/README.md:238`). La cámara es solo de
30 fps, con exposición siempre automática (`README.md:173`).

**ArUco de la mesa** (`MainActivity.kt:1129-1297`). Esquinas → raycast → EMA de 2 s → plano y homografía con rechazo de
marcadores cuyo residuo supera max(8 mm, 3×mediana). El PnP es Zhang si se conoce K; si no, un barrido con *prior* de f
entre 500 y 8000 y de cy (`ArucoMesa.kt:230-260`). La ambigüedad de espejo se resuelve por el lado de la cámara. Con la
«imagen por signo» de un par patrón/invertido se cancela el albedo de la mesa (`ArucoMesa.kt:98-105`). Si hay una pose
fija, el ArUco solo verifica, y tres verificaciones malas seguidas levantan «RIG MOVIDO» (`MainActivity.kt:1209-1219`).

**QR de MRUK.** Se usan los centros; las esquinas son poco fiables con keystone (hasta 10 cm, `MainActivity.kt:1687-1690`).

**v3, Mac** (`calib_quest_proyector.py`):
1. Gray de 11+11 bits (44 patrones) más grillas ArUco normal e invertida.
2. Umbral relativo por píxel. Se omiten los bits con menos de 72 % de legibilidad (`:945-990`).
3. Centroides por bloque y afín local en una grilla de 24 px del proyector: unos 3.000 pares (`:1004-1063`).
4. Rayos ∩ anclas y malla MRUK (Möller–Trumbore vectorizado, `:534-565`, `:1068-1093`).
5. `least_squares` con pérdida soft_l1 o Huber sobre f, cx, cy, k1 y pose (`:1176-1209`).
6. **Restricción epipolar con puntos de paralaje** (objetos sobre la mesa) para fijar el centro cuando los datos son
   coplanares (`:1160-1165`, `:1421-1460`).
7. Prior blando: el proyector unos 10±5 cm sobre el Quest y 20±10 cm al lado (`:1353`).
8. Variantes A/B/C elegidas por reglas (`:1482-1527`) y covarianza para obtener σ.
9. Lazo con remap suavizado (`griddata` y gaussiana de σ=25 px, `:2095-2109`).
10. Exportación de P con la convención de bordes de píxel y de `H_proj_to_mesa`.
11. Referencia métrica con el Pixel 9 mostrando ArUco/ChArUco desde `charuco.html` (`:1883-2075`).

`lienzo_quest.py` calcula `H_cam→mesa` intersecando rayos con el plano de la mesa (falla si la cámara no es pinhole pura,
`:52-64`) y, en modo denso, agrega una corrección residual del gray code.

Números reales según la documentación (no reproducibles, porque `captures/` no está en el snapshot): residuo de 0,52 px;
lazo con 0,1 mm de mediana; Gray code frente a geometría con 0,51 mm de mediana y p90 de 1,1 mm
(`SETUP-MESA-QUEST.md:57-66`). La verificación rápida «recalibrar» proyecta 12 cuadrados de 36 mm en unos 3 s y avisa si
la mediana pasa de 1,5 mm (`SETUP-MESA-QUEST.md:89-92`).

## 6. Persistencia

**Archivos:**
- `quest/calib/calib.json` y su versión con fecha, `quest/logs/casco-*.jsonl` y `localStorage` con prefijo
  `quest-proyector:`.
- En `files/` de la app:
  - `calib_proyector.json` (modelo, rejilla y bloque `app`), escrito de forma atómica con un `.tmp`
    (`CalibIO.kt:59-94`).
  - `cal_<fecha>/`, con jpg, png y `pares.csv`.
  - `calib_mesa.json`, que viene del Mac con `pose_fija`.
  - `calib_mesa_aruco.json`, `aruco_grid.json`, `proyector_mesa_K.json` y `marcadores.json`.
- En el Mac: `captures/quest-cal-fija/` con `bundle.json`, `calib_*`, `H_proj_to_mesa.json`, `lazo_corr.npz`,
  `lienzo_quest.json/.npz` y `verificaciones.jsonl`.

**Convenciones explícitas:** centros de píxel enteros frente a bordes, con la corrección `T05` (`:2249-2251`). Contenido
de la ventana de 1900×1040 frente al display de 1920×1080. Varios JSON llevan un campo `convencion` o `nota` que describe
el marco.

**Valores hardcodeados:**
- IPs: `192.168.4.161` (Quest), `192.168.4.1` (`server.py:54`), `192.168.1.233` (PC, VNC y :8794).
- Serial adb `2G0YC5ZGCM043L` y `/Users/max/Library/Android/sdk/...`.
- Rutas `/Volumes/Externo/...` y `C:\sl-scanner`.
- Display del proyector en `(0,1080,1920,1080)`.
- Pose por defecto de la cámara (`CAM_DEF`) y prior de 10/20 cm.
- Grillas ArUco en `captures/dale-20261102-1511`.
- Llave pública `max@macbook` en `bootstrap.ps1:4`.
- `AI_GATEWAY_API_KEY` en el entorno del Mac.
- Entrada de llavero `sl-scanner-vnc`.

## 7. Estado real

| Pieza | Estado | Evidencia |
|---|---|---|
| WebXR v1 | Probado en headless y simulador, **no en el casco** | `quest/README.md:124`, `resultados/headless.json` |
| Nativa: salida HDMI y calibración del rig en la cabeza | Compila; **solo sintético**; abandonada en la práctica | `quest-nativo/README.md:244-254` («Pendiente») |
| Nativa como servidor de sensores (:8787) | **En uso** desde el 9-nov | `SETUP-MESA-QUEST.md` |
| Calibración fija del proyector de la mesa vía Mac | **Funciona** (los números de la §5) | `SETUP-MESA-QUEST.md:53-66` |
| ArUco/QR en la app | Experimental (comandos de ajuste, verificación de «rig movido») | `MainActivity.kt:1209-1219` |
| Body tracking | Inútil con el casco sin nadie puesto (lo dice el propio código) | `MainActivity.kt:291` |
| `hands_service`, `camserve` | Implementados, con clientes en el Mac | `playground/hands/*` |
| `slserver` (FastAPI en el PC) | **No existe** | Solo en `requirements-gpu.txt:15` y `README.md:6` |
| Requests 005, 009–013 | Todos `status: open` | Encabezados de cada request |

**Qué proponía cada request y qué se implementó:**
- **005** (streaming de la FX3 y del lienzo al YoloBox Encoder): no se implementó.
- **009** (tres cámaras en un marco común): no se implementó. La FX3 y la Osmo fueron reemplazadas por el Quest.
- **010** (iPad con LiDAR como sensor RGB-D en vivo vía Record3D o ARKit): no se implementó. Solo se usó el escaneo
  estático `sala.glb`.
- **011** (reparto Mac/PC, canales de eventos y video, reloj común): parcial. El PC solo hace manos, MIDI y runtime; el
  estado vive en el Mac; el reloj común existe solo en el cliente de manos.
- **012** (segundo proyector con zonas o blending): no se implementó. La app solo distingue proyector «mesa» de proyector
  «rig».
- **013** (viewfinder de mano con iPad, SAM y Video Depth Anything en el PC): no se implementó. Lo más cercano es el modo
  cabeza de la v2 con predicción; `depth_mono.py` usa Depth Anything V2 sobre una foto, offline.

## 8. Rendimiento

- **AirPlay:** el escalón negro→blanco tarda **unos 313 ms** de punta a punta, y la cámara aporta unos 40 ms
  (`SETUP-MESA-QUEST.md:82-85`). Por eso cada patrón espera al menos 1,5 s, más la detección de cambio y de estabilidad:
  una captura de unos 50 patrones toma minutos (inferido).
- **MJPEG del Quest:** cada cliente codifica su propio JPEG. Con `w<864` hace JPEG q95 → decodificar → escalar →
  recodificar (`MainActivity.kt:1919-1927`). El índice compartido `ultimoJpegCamIndice` obliga a tener un solo cliente
  pesado (`:1931`, documentado en `SETUP-MESA-QUEST.md:112`). Por USB rinde 12,7 fps a unos 480 kB/s.
- **Cuadro completo:** 145–295 ms por pedido (`SETUP-MESA-QUEST.md:80`).
- **Raycasts de MRUK** serializados en el hilo principal (2–10 s de espera por tanda), compitiendo con la UI, ArUco, la
  profundidad y el monitor.
- **`/estado.json`** se arma completo, con los arrays de ArUco, cinco veces por segundo por cada visor, y abre una conexión
  TCP y un hilo por petición.
- **Calibración nativa:** unos 15–20 s por vista (`quest-nativo/README.md:123-124`).
- **WebRTC (headless, Mac→Mac):** con el efecto «mesa», 720p a 1,8 Mbps, jitter buffer de 8–10 ms y encode de 3,2 ms. Con
  «wireframe» satura: 8,3 Mbps, baja a 540p, el jitter buffer sube a 76 ms y el encode a 9,8 ms. PeerJS cloud tarda 5,8 s
  hasta el primer cuadro y llega a 136 ms de jitter. La latencia del cast nativo del Quest (~200–500 ms) es una estimación
  sin medir (`quest/README.md:117`).

## 9. Deuda técnica, bugs y riesgos

**Seguridad:**
- El servidor :8787 del Quest no tiene autenticación, responde con `Access-Control-Allow-Origin: *` y **permite Private
  Network Access** (`ServidorMonitor.kt:98-101`, `:125-126`). Cualquier página web abierta en un navegador de la LAN puede
  leer las cámaras del casco (`/cam_full.jpg`) y mandar `/cmd`.
- `/log` ejecuta `logcat` (`MainActivity.kt:2023-2029`).
- `server.py` sirve todo `quest/` como estático (`:341`), incluida la **llave privada** `quest/certs/dev-*.key` (`:278`),
  además de los logs y las calibraciones. `/api/calib` y `/api/log` escriben sin autenticación.
- PeerJS cloud usa un id fijo, `smartup-proyector-<sala>` (`transporte.js:278`), que cualquiera que conozca la sala puede
  suplantar.
- `autologin.ps1` deja la sesión de Windows abierta para cualquiera con acceso físico y **pasa la contraseña en texto plano
  como argumento** a Autologon (`:14-17`), visible en la lista de procesos mientras corre.
- `setup.ps1` solo apaga `PasswordAuthentication` (`:61-69`), mientras que `bootstrap.ps1` también apaga KbdInteractive
  (`:22-23`), que en Windows acepta contraseñas. Los dos scripts no son coherentes.
- La regla de firewall `100.64.0.0/10` aplica con `-Profile Any` a todo el rango CGNAT, no solo a la interfaz de
  Tailscale.
- VNC usa autenticación DES con contraseña de 8 caracteres y RFB sin cifrar (`playground/vnc.py:1-11`).
- El receptor de broadcast está registrado como `RECEIVER_EXPORTED` (`MainActivity.kt:314`).

**Bugs probables:**
- `ScanCamera.onDisconnected` no marca `failed` (`:273-276`), así que `iniciarCamara` no la reabre
  (`MainActivity.kt:372-376`). Existe un vigilante externo como parche (`logs/quest_watchdog.sh`).
- El offset monotonic/boottime se calcula una sola vez; tras una suspensión del casco cambia (inferido).
- La pose de `getViewerPose` se estampa con la hora de la consulta, no con la hora a la que es válida (inferido). Con el
  rig fijo no importa; en mano, sí.
- Los `latch.await` con timeout devuelven resultados parciales sin avisar (`Calibrador.kt:438`).
- `README.md` describe la cámara como 1280² y la documentación de la mesa como 1728²; el código toma el tamaño máximo
  disponible (`ScanCamera.kt:152`).
- En el lazo de la v3, un emparejamiento de blobs equivocado deja un atípico grande (en mi corrida sintética, unos 63 px
  con mediana de 0,03 px).

**Problemas de arquitectura:**
- `MainActivity` como *god object*; más de 40 comandos de texto sin tipado.
- **El canal de logs se usa como canal de datos:** la geometría del Pixel viaja por `/api/log` y luego se lee desde
  `quest/logs` (`calib_quest_proyector.py:1885-1899`).
- La calibración se empuja con `adb run-as` (`:2347-2354`).
- Puertos con significados distintos según la máquina: 8793 es la ventana en el Mac y el WS de manos en el PC; 8794 son
  los cuadros del PC y la página de la secretaria; 8790 es el runtime en las dos máquinas.
- La pantalla completa en macOS provocó tres kernel panics DCPEXT (`SETUP-MESA-QUEST.md:107`).
- `matplotlib` no está en el venv, pero `exportar` lo necesita.

**Dependencias frágiles del Quest:** la cámara solo funciona con la app al frente (`CAMERA_DISABLED`), hace falta engañar
al sensor de proximidad, adb va por Wi-Fi porque el USB-C lo ocupa el video, no hay mapa de la Depth API en el SDK 0.13.2,
y no hay AE lock ni exposición manual.

## 10. Relevancia para el producto «agente con contexto físico»

**Esencial (reusar la idea, reescribir el código):**
- Cadena de calibración proyector↔cámara: Gray con descarte automático de bits y afín subpíxel → PnP con caso plano,
  prior y paralaje → corrección por lazo (remap o rejilla en el espacio del proyector) → verificación rápida
  («recalibrar»: 12 cuadrados en unos 3 s) y alarma de «rig movido» por residuo.
- El **lienzo métrico de la mesa** (mm/px, `H_cam→lienzo`, `H_lienzo→proj`): es exactamente la API espacial que necesita
  un agente sobre una mesa.
- La consulta de pose por timestamp en el origen (`HistorialPose` + `/cam_pose.json?ts=`).
- Las convenciones de marcos documentadas dentro de cada JSON.
- Los *harness* sintéticos (simulador de escena, `xr-mock`, tests en la JVM verificados contra OpenCV).

**Reutilizable con cambios:**
- La señalización WS y el emisor/receptor WebRTC: sirven para que **celulares desde el navegador** actúen como cámaras
  extra, sin instalar app. Hay que invertir los roles (el celular emite) y sumar autenticación. `charuco.html` ya es un
  antecedente de «página en el celular que participa en la calibración».
- El hub de «último mensaje» con `Condition` y el offset de reloj estilo NTP.
- La página 3D de observabilidad: frustums, malla y superficie proyectada.
- Los shaders de warp y rejilla.

**Prescindible:**
- WebXR v1, la salida por HDMI desde el casco, la calibración por toques y pivote, el body tracking y los QR de MRUK.
- PeerJS cloud y AirPlay.
- El autologin, VNC y la sincronización por SSH+tar.
- Los planes de iPad, FX3 y YoloBox.

El Quest como cámara solo vale la pena si se necesita su malla de la sala o su tracking 6DoF. Para una mesa basta un
plano, y una cámara UVC de 60 fps es más simple y tiene menos latencia.

## 11. Recomendaciones para la reescritura

**Topología.** Un solo **hub** en la máquina conectada al proyector por HDMI directo; mejor Linux o Windows, donde la
pantalla completa no es un riesgo. El hub es dueño del estado de la escena, de la salida al proyector (ventana nativa a
pantalla completa con vsync), de la captura de la cámara principal (UVC por USB, con timestamps del driver), de la visión
en GPU y de la API/MCP del agente. Los celulares son cámaras satélite. La inferencia pesada (SAM, profundidad) puede ir en
otra máquina con GPU, pero como *worker* sin estado.

**Video:**
- Dentro del hub, sin re-encodes: memoria compartida o colas de un solo slot con el «último cuadro».
- Hacia los celulares, WebRTC (`getUserMedia` → H.264), estampando el tiempo de captura de cada cuadro
  (`requestVideoFrameCallback`).
- MJPEG solo para vistas de depuración.
- Nada de AirPlay en el camino crítico.

**Eventos y control:**
- Un WebSocket con mensajes tipados (JSON o msgpack): `seq`, `t_src` y `t_hub`.
- HTTP para *snapshots* y consultas, como `pose@ts` o «píxel del lienzo → píxel del proyector».
- MCP encima de esa misma API.
- Comandos tipados con validación, no texto libre.

**Relojes:** el hub es el reloj maestro. Cada cliente estima su offset con ping/pong de mínima ida y vuelta (el patrón de
`hands/client.py`) y se publica la latencia medida, por ejemplo con la prueba del escalón negro→blanco, que ya existe.

**Descubrimiento:** mDNS/zeroconf (`_mesa._tcp`) y, para los celulares, **un QR proyectado sobre la mesa** con la URL y un
token de sesión. Después, la autocalibración del celular: el hub proyecta Gray o ArUco y el celular queda registrado con
una homografía al lienzo.

**Seguridad:** token por sesión, servicios atados a la interfaz de la LAN, sin CORS `*` ni PNA, y nada servido desde
directorios que contengan llaves o logs.

## Archivos leídos completos

- `quest/`: `server.py`, `calib_solver.py`, `quest.html`, `proyector.html`, `charuco.html`, `README.md`, `package.json` y
  todos los `js/*.js` (`app`, `calib`, `log`, `overlay`, `panel`, `proyector-cam`, `sala`, `sim`, `transporte`).
- `quest/pruebas/`: `headless.py`, `test_solver.mjs`, `xr-mock.js`. De `resultados/`: `calib_solver.txt` y
  `test_solver.txt` completos; `headless.json` solo en resumen.
- `quest-nativo/`:
  - Los 15 archivos `.kt` de main y los 5 de test.
  - `AndroidManifest.xml`, `res/layout/*`, `res/values/*`, `build.gradle.kts` (raíz y `app/`), `settings.gradle.kts`,
    `libs.versions.toml`.
  - `assets/monitor/index.html` y `3d.html`.
  - `tools/*`: `monitor_adb.py`, `aruco_grid.py`, `medir_aruco.py`, los tres `verificar_*.py`, `cmd.sh`,
    `probar_aruco.sh`, `probar_en_casco.sh`.
  - `README.md`.
- `playground/calib_quest_proyector.py` (las 2.485 líneas) y `playground/lienzo_quest.py`.
- `windows/`: `README.md`, `bootstrap.ps1`, `setup.ps1`, `autologin.ps1`, `doctor.py`, ambos `requirements-*.txt`,
  `hands_service.py`, `camserve.py`.
- `docs/SETUP-MESA-QUEST.md` y los requests 005, 009, 010, 011, 012 y 013.

**Solo por encima** (para contrastar): `assets/aruco_grid.json` (estructura: 24 marcadores), `playground/quest_cam.py`
(encabezado y grep), `scripts/arranque_mesa.sh` (líneas 1–40), cabeceras de `playground/vnc.py`, `secretaria/tunel.py` y
`slscan/remote.py`, y un grep de `hands/client.py`.

**No leídos:** `quest/vendor/`, `three.module.js` y `addons/` (excluidos por el encargo); el wrapper de Gradle, `proguard`,
`gradle.properties` y `LICENSE`; `windows/mail-roberto*.txt` y `midi_*.py` (fuera de alcance). El snapshot no incluye
`captures/`, así que no hay datos reales de calibración para revisar.

## Pruebas corridas

- **`node test_solver.mjs`** (copiado a `/tmp` para no escribir en el repo): 200 de 200 casos bien en cada fila, con los
  mismos números guardados en el repo; pivote con 0,49 mm de error.
- **`calib_solver.py`** sobre la salida anterior: «JS == OpenCV: OK», código de salida 0.
- **`calib_quest_proyector.py todo --dry --sesion /tmp/qcal-dry`**: corre entero hasta el lazo, en unos 13 s.
  - Contra la verdad sintética: Δf de −4,3 px (sobre unos 3.100), Δcentro de 1,6 mm, ΔR de 0,03°.
  - Lazo con 0,03 px de mediana, pero un atípico de unos 63 px deja el rms en 10,9 px.
  - El Pixel sintético queda a 9,6 mm de la verdad.
  - **Falla en `exportar` porque falta `matplotlib` en el venv.**
  - Esta corrida creó `quest/__pycache__/` en el repo; ya la borré.
- **No corridos:** los tests de Kotlin (requieren compilar con Gradle/Android, prohibido por el encargo) y `headless.py`
  (necesita Chrome y el servidor, escribe en el repo y usa PeerJS cloud).
