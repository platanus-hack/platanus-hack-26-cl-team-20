# SL Scanner · plan técnico

15-oct-2026 · unidad h3 (Fable), orquestador Creativo. Manda `SPEC.md` (con las adiciones de Max del 15-oct); lo que
diga Max después manda sobre los dos. v2: incorpora el PC Windows, los dos proyectores en cabezal móvil, las luces, la
segmentación 3D, personas, playground y raytracing.

## Resumen

- **Stack: Python 3.12/3.13 + OpenCV 5 (wheels, sin compilar), torch CUDA en el PC Windows.** sltk solo como
  referencia de algoritmos; no se porta el C++.
- **Dos máquinas.** El **PC Windows con GPU es el motor** (captura 4K, calibración, reconstrucción, tracking, render a
  los proyectores). El **Mac es el control**: SSH por Tailscale para administrar y, desde F5, un backend `slserver`
  (FastAPI + WebSocket) en el PC que el Mac y el playground web consumen. Nada pesado en el Mac (1 GB libre).
- **F0 hecha** (15-oct): el `sampledata` de sltk procesado de punta a punta; nube igual a la de sltk (104 830 vs
  104 809 puntos, mediana 0,018 mm). `reports/f0_*.png`, `reports/f0_viewer.html`.
- **F1 lista en software** (17 tests sintéticos). **Setup del PC Windows listo para que Max lo corra**
  (`windows/setup.ps1`, `windows/README.md`); h3 no instala nada remoto hasta que Max confirme.
- Nada se compra, publica ni sube sin Max.

## Evaluación de sltk

Clonado en `./sltk` (`--depth 1`, 103 MB, ignorado por git). CLI en C++ de 2016–2018:

| Herramienta | Qué hace | Estado |
|---|---|---|
| `sldisp` | patrones gray code, senoidales y monocromos por OSC, SDL2 fullscreen | el protocolo OSC sobra en un solo proceso |
| `slcapture.py` | Python 2, MJPEG de `mjpg-streamer`, guarda JPG | inservible: Python 2 y JPEG rompe los patrones finos |
| `slcrunch` | decodifica gray code → mapa cámara→proyector, coalesce, triangula, PLY | ~300 líneas útiles; OpenCV 2.4 y `exiftool` |
| `slcalibrate` | `calibrateCamera` cámara y proyector, `stereoCalibrate`, `stereoRectify` | API 2.4, chessboard, una cámara |
| `plyalign/merge/trim` | ICP y limpieza de nubes | PCL 1.8 + VTK 7 + Boost: imposible en 4 GB de disco |

Veredicto: **no portar**. Lo que copiamos (ya reimplementado y validado contra su salida): codificación gray code x/y
con inverso y umbral 5, coalesce por píxel de proyector, calibración del proyector por homografías locales alrededor de
cada esquina (método de Brown), triangulación en el par rectificado. Hallazgo: el sampledata de sltk se generó con
coalesce sin ponderar; el código actual pondera por luminancia y bota el 30 % de los puntos oscuros. Ofrecemos ambos.

## Hardware (según Max, 15-oct)

| Dispositivo | Rol | Cómo entra al sistema |
|---|---|---|
| PC Windows, GPU NVIDIA, buena RAM | motor | SSH por Tailscale; `slserver` |
| 2 proyectores en cabezal móvil (360°) | luz estructurada y projection mapping | un display cada uno; pose del cabezal calibrada por orientación |
| 1 Osmo por proyector, pegada al lente | alinear proyector ↔ escena; par cámara-proyector de base fija | UVC por USB (o HDMI + capturadora) |
| Sony FX3 | vista general 4K de la escena, tracking de objetos y personas | HDMI + capturadora UVC (o USB streaming) |
| Webcam cenital | mapa de la mesa, tracking desde arriba | UVC |
| Foco DMX USB-C (RGB + temperatura) | luz controlada para PBR | interfaz USB-DMX (FTDI) + `pyserial` |
| Barra RGB con temperatura, manual | segunda luz para PBR | Max la mueve; se trackea por cámara (marcadores ArUco pegados) |
| 3 monitores del escritorio | control, playground | — |
| Mac | control, visores, decisiones | SSH → PC |

Supuestos reversibles: las Osmo y la FX3 entregan 4K sin compresión visible (HDMI o UVC YUY2/NV12; **no** MJPEG) y con
exposición, foco y balance fijos; los cabezales móviles o tienen DMX (los movemos nosotros) o son manuales (Max los
mueve y el sistema recalibra la orientación por escaneo).

## Arquitectura

```
Mac (control)                         PC Windows (motor, C:\sl-scanner)
 slscan CLI ── SSH/Tailscale ───────▶  slscan (mismo paquete) + slserver (FastAPI/WS)
 playground web (Three.js) ── WS ───▶    captura: Osmo×2, FX3, webcam, DMX, displays×2
 visores HTML, reports/                  calib: rig.json (mm, mundo = mesa)
                                          recon: gray code + phase shift, fusión, malla (Open3D)
                                          scene: segmentación → objetos (mallas) → tracking 6DoF
                                          people: pose/armature, caras, ropa (librerías abiertas)
                                          render: moderngl por proyector con su K y distorsión; RT en GPU
```

Paquete único `slscan/` (hoy: `rig`, `graycode`, `recon`, `calib`, `capture`, `viewer`, `remote`, `cli`; se suman
`heads` (cabezales), `lights` (DMX y tracking de luces), `phaseshift`, `fusion`, `scene`, `people`, `server`,
`render`). Reglas transversales:

- **Coordenadas**: mm, mundo fijo en la mesa (origen en el tablero de calibración); cada dispositivo `R|t` respecto a
  eso; un cabezal móvil tiene base fija + ángulos (pan, tilt) → pose del proyector por cinemática.
- **Robustez al movimiento** (pedido de Max): el mapa 3D de la escena y las poses de los dispositivos viven en el
  marco de la mesa, no en el de ninguna cámara. Si una cámara o un proyector apuntan a otra parte, su pose se
  re-estima contra el mapa (features 3D anclados + PnP, o el ángulo del cabezal); si un objeto se mueve, su malla se
  trackea en 6DoF y la proyección sigue a la malla, no a la imagen.
- **Captura sin compresión** (PNG 8/16 bits), exposición fija verificada antes de cada escaneo, latencia medida.
- **Gamma del proyector** y **respuesta de color de cada luz** calibradas y guardadas en `rig.json`.
- **Reproducible por CLI**, cada hito deja PNG/HTML en `reports/`; datos crudos en el PC.

## Fases e hitos

### F0 · Base (hecha, 15-oct)

Pipeline `slscan decode` sobre `sltk/sampledata`: 104 830 puntos vs 104 809 de sltk, mediana 0,018 mm (`--mode sltk`
baja a 0,00006 mm; el resto es el decodificador JPEG). Capturas `reports/f0_scan_0020.png`, `f0_scan_0021.png`; visor
`reports/f0_viewer.html` verificado en Chrome.

### F1 · Rig y calibración (software listo; falta hardware)

- **Intrínsecos** de Osmo×2, FX3 y webcam con ChArUco (20–30 vistas guiadas). **Proyectores** por homografías locales
  sobre gray code en 10–15 poses del tablero. **Extrínsecos** de todo en el marco de la mesa con bundle adjustment
  (hecho: `calib.py`, 17 tests).
- **Cabezales móviles** (nuevo, `heads.py`): modelo base→pan→tilt→proyector con ejes y offsets desconocidos. Se
  calibra escaneando el tablero en 6–10 orientaciones; con DMX se mueven solos, sin DMX Max los gira y el sistema
  detecta la orientación nueva. Resultado: dado (pan, tilt) sabemos el frustum del proyector; **"a dónde apunta"** =
  intersección del frustum con la malla de la escena (F2) → lista de objetos iluminados (F5).
- **Osmo pegada al lente**: par rígido proyector-Osmo; se calibra una vez y sirve para re-alinear el proyector cuando
  gira (la Osmo ve el mapa, PnP, y de ahí la pose del proyector).
- **Hecho cuando**: reproyección < 0,5 px cámaras, < 1 px proyectores; cabezal: error de apuntado < 5 mm a 2 m entre
  orientaciones; dos sesiones difieren < 2 mm y < 0,2°.
- **Se muestra**: visor del rig con frustums y las orientaciones del cabezal; mapas de distorsión.
- **De Max**: correr `windows/setup.ps1`, enchufar Osmo/FX3/webcam/proyectores al PC, tablero ChArUco (q4), decir si
  los cabezales tienen DMX, 20 minutos de captura guiada.

### F2 · Escaneo 3D de la escena completa

- Gray code + phase shift (4 pasos, 2 frecuencias) desde cada proyector en varias orientaciones del cabezal; decodifica
  cada par proyector-cámara; triangula; fusiona todo en el marco de la mesa con pesos por ángulo y confianza; malla por
  Poisson (Open3D en el PC) y limpieza. Cobertura 360° = unión de orientaciones del cabezal.
- **Hecho cuando**: < 1 mm a 1 m sobre un objeto medido; planitud del tablero RMS < 0,5 mm; sin costuras visibles entre
  orientaciones (< 1 mm en los traslapes).
- **Se muestra**: malla con color en el visor web; tabla de error por par y fusionada.

### F3 · Luces y materiales PBR

- **Foco DMX** (`lights.py`): control por USB-DMX; **calibración con recorrido**: para cada canal (R, G, B, CCT) y
  nivel, la cámara mide la respuesta con un ColorChecker y una esfera difusa → curva de intensidad y color por valor
  DMX; posición 3D del foco por esferas cromadas o por triangulación de su cuerpo desde ≥ 2 cámaras. El recorrido de
  posiciones lo guía la pantalla (Max mueve el foco; el sistema confirma la pose antes de cada toma).
- **Barra manual**: marcadores ArUco pegados en la barra → pose 6DoF por cámara en cada toma; su emisión se modela como
  línea de luz (integral sobre la barra) con la misma calibración de color.
- **Materiales**: estéreo fotométrico de luz cercana sobre la malla de F2 → normales finas; albedo; separación
  difuso/especular; rugosidad y F0 por ajuste GGX por texel; UV con xatlas; glTF. **Referencia**: el paper de PBR con
  fuentes de luz y una cámara que Max menciona (link pendiente; mientras tanto, candidatos: *High-Gloss SVBRDF Capture
  Using Bounce Light*, CGF 2026; *Self-calibrated Near-Light Photometric Stereo Using a Geometric Proxy*, 2025).
- **Hecho cuando**: re-render en Blender bajo una luz no usada en el ajuste vs foto: error medio < 5 % en luminancia.
- **Se muestra**: foto vs render lado a lado; albedo/normal/rugosidad.
- **De Max**: modelo del foco DMX y de la interfaz USB-DMX, el link del paper, mover las luces, compras opcionales
  (polarizadores, esferas cromadas, ColorChecker, ArUco impresos).

### F4 · Projection mapping base

Render desde cada proyector con su K, distorsión y la pose del cabezal (moderngl), contenido pegado a la malla, con
compensación de albedo. Hecho cuando: < 2 px de error proyectado medido por cámara. Se muestra: video.

### F5 · Segmentación de la escena y tracking de objetos (lo más importante para Max)

- **Segmentación**: SAM 2 (y SAM 3 si está disponible) sobre las imágenes 4K de FX3, Osmo y cenital → máscaras 2D;
  cada máscara se levanta a la malla por la calibración (píxel → triángulo) y se votan etiquetas entre cámaras; se
  cruza con la geometría (planos de mesa y pantallas por RANSAC, componentes conexas, curvatura) para cortar la malla en
  objetos: mesa, pantallas, cada cosa. Referencia de redes de segmentación de nubes: OpenPCSeg
  (github.com/BAI-Yeqi/OpenPCSeg); está entrenada en LiDAR exterior, así que sirve como arquitectura o para fine-tune con
  nuestras escenas, no tal cual. **Todo termina en malla por objeto** (recorte + cierre de huecos + UV propias).
- **Tracking 6DoF**: por objeto, features 3D (SIFT/SuperPoint) anclados en su malla texturizada + PnP RANSAC por cámara,
  fusión entre cámaras, Kalman; re-registro ICP con un gray code corto cuando se pierde. Sobrevive a que el objeto se
  mueva y a que cámaras/proyectores apunten a otra parte (todo está en el marco de la mesa).
- **Hecho cuando**: cada objeto segmentado coincide con el real (IoU 3D > 0,9 a mano alzada); tracking < 2 mm, < 100 ms;
  la proyección sigue al objeto movido sin re-escanear.
- **Se muestra**: escena coloreada por objeto en el visor; video de un objeto moviéndose con la proyección pegada.

### F6 · Personas

Pose 2D/3D con librerías abiertas (RTMPose/MMPose o YOLO-pose por cámara, triangulación multicámara → armature 3D en
tiempo real), identidad por cara (InsightFace) y atributos de ropa (CLIP) para seguir a varias personas entre cámaras;
proyección sobre el cuerpo con predicción de latencia. **Nota**: Max mencionó el código de Smart Vision (AB InBev) como
ayuda para cara, cuerpo y ropa; h1 lo frenó porque es un proyecto de cliente del orquestador de Trabajo. Lo que se
buscaba ahí: el flujo de detección de personas y re-identificación entre cámaras y atributos de ropa; se hace con las
librerías abiertas anteriores salvo que Max decida otra cosa.

### F7 · Playground y GPU

Backend `slserver` con escena en vivo (mallas, objetos, personas, luces, proyectores) por WebSocket; UI web (Three.js)
para crear experimentos: shaders por objeto, triggers por interacción (tocar la mesa, acercar un objeto), tracking en
vivo. Raytracing en tiempo real en la GPU: primero rasterizado con sombras y reflejos en pantalla (moderngl), luego
path tracing (OptiX vía `pyoptix`/CUDA o Vulkan RT) para efectos sobre la malla; Blender Cycles para offline.

## Riesgos y decisiones pendientes

- **Compresión y latencia de las cámaras**: si la FX3 u Osmo entran por USB con MJPEG, no sirven para patrones finos;
  por HDMI con capturadora sí. Decidir cuando se enchufen.
- **Cabezales sin DMX**: la calibración depende de que Max los deje quietos entre orientaciones.
- **Disco del Mac** (~1 GB): todo pesado en el PC.
- **Smart Vision**: fuera hasta que Max lo decida con h1.
- **Paper PBR**: pendiente el link.
- **q4**: tablero ChArUco (`reports/board_7x5_40mm.png` o uno comprado rígido).

## Cómo se ejecuta

```
cd ~/hardware/sl-scanner
.venv/bin/python -m slscan decode sltk/sampledata/scan_0020 --rig sltk/sampledata/procam_2016_12_09.yaml --out reports/f0 --png
.venv/bin/python -m slscan compare reports/f0/scan_0020_cloud.ply sltk/sampledata/scan_0020_cloud.ply --flip-yz
.venv/bin/python -m slscan view reports/f0/scan_0020_cloud.ply --rig sltk/sampledata/procam_2016_12_09.yaml --out reports/f0_viewer.html
.venv/bin/python -m pytest tests -q
.venv/bin/python -m slscan remote doctor --host <pc-windows>      # cuando Max corra windows/setup.ps1
```

## Live Mapping Playground (v3, 15-oct 18:28–19:10)

Alcance en `SPEC.md › Live Mapping Playground`. Decisiones de Max: **sonido en el Mac** (Ableton, Circuit Tracks,
MCP `ableton`), **visuales en el PC** (NEW-WHITE-4K, RTX 4070, 128 GB RAM, Python 3.12 + torch CUDA 12.6 + Open3D
instalados el 15-oct, SSH con llave), **puente por LAN**. Secretaria por chat hoy (h4, Sonnet), por voz después.

### Arquitectura

```
Max ──voz (ElevenLabs)──▶ Secretaria ──tools (MCP playground, ableton)──▶ runtime (Mac: sonido · PC: visuales)
  │                           │ pedidos de features                              ▲ hot reload
  └──chat (Herdr)──▶ h4 (secretaria por chat) ──▶ requests/NNN-slug.md ──▶ h3 / instancias que programan en vivo
```

- **Runtime** (`playground/`, hecho como MVP): escena viva (objetos con geometría, contorno, textura, plano, pose;
  personas; luces; proyectores), galería de efectos como plugins con **hot reload** (guardar el archivo basta; un
  plugin roto no tumba nada), lienzo por proyector, API HTTP + SSE (`/api/command`, `/api/state`, `/api/events`,
  `/api/frame.png`). Mismo contrato que BIOME. En el PC el lienzo se reemplaza por el render calibrado (moderngl).
- **Capa de tools** (`playground/mcp_server.py`, MCP `playground` por stdio): `pg_state`, objetos y geometría,
  galería/spawn/kill/params, snapshot, beat, pedidos, y stubs honestos de cámaras, crop/segmentación, gestos, MIDI
  del Circuit Tracks, foco DMX; Ableton vía su MCP existente. Se van llenando por fase (F5 crop, F6 gestos, F3 DMX).
- **Secretaria**: misma estructura que `~/.local/share/secretaria` (agente ElevenLabs + `agente/herramientas.json`
  como webhooks a un puente local + hook `Stop` de Claude + pane en Herdr), replicada en este repo como
  `playground/secretaria/` (siguiente paso): el puente expone las tools `pg_*` como webhook tools del agente y la
  skill `playground/skill/SKILL.md` es su guion. Hoy el guion lo usa h4 por chat con el MCP.
- **Instancias que programan en vivo**: un pane orquestador del playground en Herdr (unidad de este repo, no el
  orquestador de SAT) toma `requests/` en orden, lanza como máximo **2 instancias simultáneas** (Sonnet para efectos
  y tools acotados; Opus/Fable solo para cambios de runtime), cada una en su rama/worktree, con hot reload para
  probar en vivo y commit al cerrar el pedido. Crear el agente de ElevenLabs o lanzar instancias pasa por
  `orq-creativo ask`. Llaves por referencia (llavero/Infisical), nunca pegadas.
- **Galería de efectos**: plugins que declaran qué leen (`needs`) y sobre qué objetos van (`targets`). Hechos:
  `contour_glow` (contorno + beat), `texture_grid` (patrón por material), `frame_feed` (cuadro plano con feed de
  cámara rectificado). Siguientes: `shadow_cast` (sombras proyectadas por una luz virtual sobre la malla), `keys_light`
  (teclas del piano por MIDI), `croc_voice` (gesto/pose del cocodrilo → nota/CC), `person_skin` (SMPL-X), `pbr_relight`.
- **Body tracking**: escaneo de Max (F2) + ajuste de un modelo SMPL-X a su malla (parámetros de forma) y pose en
  tiempo real desde las cámaras (RTMPose multi-vista → SMPL-X fit por cuadro); proyección del modelo en 3D con el
  render calibrado. Librerías abiertas.
- **Mundo PBR vivo**: la escena del playground es la malla segmentada de F5 con materiales de F3 y poses de tracking;
  dimensiones y posiciones reales en mm.

### Estado y siguientes pasos

- Hecho 15-oct: runtime + 3 efectos + cola `requests/` + MCP + skill + tests (`tests/test_playground.py`);
  captura `reports/playground_demo.png`. PC con torch CUDA listo (`reports/pc_doctor.json`).
- Siguiente: puente `playground/secretaria/` (webhooks ElevenLabs → tools), render calibrado en el PC (necesita la
  sesión interactiva: por SSH el PC reporta un display 1024x768; el render corre como tarea en la sesión de Roberto),
  puente de tempo Ableton → `pg_beat`, MIDI del Circuit Tracks (`mido`), y los efectos siguientes vía `requests/`.

## Pedidos de Max vía h4 (15-oct 19:09) · orden: 002, 003, 001, 004, 005

| # | Pedido | Depende de | Estado |
|---|---|---|---|
| 002 | Reusar `~/ohmrgb`: mapeo MIDI, tracking de perillas con OpenCV, skin del OHM | nada (software); cámara para el tracking | **doing** (19:15) |
| 003 | Skins SVG de cada controlador ajustadas a la cámara + segmentación de cada control | 002, cámaras y proyector calibrados (F1) | open |
| 001 | DDJ-SB2, Circuit Tracks, OHM RGB como dispositivos MIDI; QX1222USB solo audio | 002 (mismo registro de dispositivos); equipos enchufados | open |
| 004 | La mesa como superficie touch | F1 (cámara-proyector-mesa), 003, pose de manos (F6) | open |
| 005 | Streaming hacia el equipo de Max | modelo del equipo (h4 lo confirma con Max) | open |

Cámaras según Max (19:1x): FX3 con angular **cenital sobre la mesa** (cámara principal), Osmo Pocket por USB en modo
webcam pegada al proyector, dos DJI SDR transmission hacia el equipo de streaming.

### Diseño del 002 (dispositivos y controles)

- `~/ohmrgb` es referencia y librería, **no se edita**. Se usan: la geometría de la skin (`ui/src/editor/constants.js`:
  viewBox 700x442, grid 8x8 desde (215,15) paso 39, 16 knobs, 8 faders, crossfader, 17 botones), `ohmrgb.py`
  (driver rtmidi, importado por ruta) y `occlusion.py` (pad tapado = oscurecimiento o cambio radical de color, nunca
  más brillo).
- **Quién manda el puerto MIDI y los LEDs**: **SmartDeck** (`session_controller.py`), si está corriendo. El playground
  no abre el puerto: se suscribe a su WebSocket (`ws://localhost:8090`, mensajes `midi` con knob/fader/pad/crossfader y
  `state` con la grilla) y, para escribir LEDs, usa sus comandos `set_grid_direct` / `editor_release` (toma y suelta
  la grilla, nunca dos procesos escribiendo a la vez). Si SmartDeck **no** corre, el playground abre el OHM directo
  (`DirectMidiSource`, python-rtmidi/mido) y escribe LEDs con el driver. Lo decide la fuente que se conecta primero;
  `pg_midi` dice en qué modo está.
- **Modelo**: `playground/devices/`: `Device` (nombre, puertos MIDI, estado), `Control` (id, tipo knob|fader|pad|
  button|crossfader, posición en la skin, valor 0..1, último evento), fuentes de eventos (SmartDeck WS, MIDI directo,
  falso para tests), **bindings** control → parámetro de efecto o del beat (`pg_bind`), y el OHM como objeto de la
  escena (`kind=instrument`, controles hijos con posición en mm cuando el fit de 003 lo ubique en la mesa; mientras,
  en coordenadas de la skin).
- **Tracking óptico** (segunda parte del 002): `occlusion.py` sobre la cámara cenital con la calibración del OHM;
  entra cuando la FX3 cenital esté enchufada (hoy no hay cámara que vea el OHM).
- Hallazgo de seguridad: `~/ohmrgb/ui/src/editor/constants.js` tiene una llave de ElevenLabs en texto plano (línea 85);
  no se copió. Conviene rotarla (lo decide Max).

### Foco SmallRig RC 100 (prioridad de Max, 19:15)

- **Corrección (19:25)**: el USB-C del RC 100 es **alimentación PD (hasta ~110 W) y DMX**, no USB de datos: el DMX
  entra por ese conector con un adaptador USB-C→DMX de SmallRig (por eso el Mac no lo ve como dispositivo USB ni serie
  aunque esté enchufado). Según el manual del RC 100B (manuals.plus) y la página de SmallRig: control por app
  **SmallGoGo** por Bluetooth (pareo: con el foco encendido, mantener el botón Bluetooth/Mute hasta que el ícono
  parpadee rápido; el sufijo del nombre Bluetooth sale en la pantalla de inicio) y DMX con adaptador aparte. El único
  adaptador DMX de SmallRig a la venta es el **4390 (USD 39,99)**, y SmallRig lo da por compatible solo con RC 220
  Pro/350/450; para el RC 100B no hay SKU confirmado. Además hace falta una interfaz USB-DMX en el PC (Enttec Open DMX
  USB ~USD 70 o DMX USB Pro ~USD 180).
- Escaneo BLE desde el Mac (19:24, Bluetooth encendido por h1): el foco no se anuncia con nombre. Apareció un
  **"Mesh Device"** (servicio 0x1827, Mesh Provisioning, fabricante 0x03F6) a -47 dBm que es el candidato: SmallGoGo
  usa Bluetooth Mesh, y un nodo sin aprovisionar solo expone PB-GATT. Si es eso, controlarlo sin el app exige
  implementar aprovisionamiento y capa de malla (días), o capturar el tráfico del app; DMX es el camino seguro.
  Pendiente (q13): elegir camino y confirmar modelo (RC 100B bicolor o RC 100C RGB).
- Control por **Bluetooth LE** con `bleak` desde el Mac (q11: Mac). Protocolo no público: `playground/lights/ble.py` trae `scan` → `dump` (GATT, solo lectura) → `learn`
  (característica de escritura + comandos en `~/.local/state/sl-scanner/lights.json`) → `set`. Tool `pg_light`.
  Los comandos se descubren con el foco a la vista (escribir en la característica writable y mirar) o capturando el
  app SmallGoGo; no se inventan bytes. Referencias de método: [Wokwi](https://blog.wokwi.com/reverse-engineering-a-bluetooth-lightbulb/),
  [whizzy.org](https://www.whizzy.org/2023-12-14-bricked-xmas/).

## Pedidos 006–009 (h4, 19:17) · entran después del foco y del 002

| # | Pedido | Depende de | Orden propuesto |
|---|---|---|---|
| 009 | Topología de 3 cámaras (webcam en la pared mirando el escritorio: caras y manos; FX3 cenital; Osmo en el proyector) y fusión para tracking y reconstrucción | F1 (calibración con el PC) | junto a 002/003: es base de 003, 006, 007, 008 |
| 006 | Detección automática de las teclas del teclado (blancas y negras) por visión | 009, F1 | con 003 (misma técnica) |
| 007 | Plantas como clase en la segmentación 3D | F5 | con la segmentación general |
| 008 | Relighting dinámico de la escena; piso a superar: github.com/gizatt/dynamic_scene_relighting | 009, F1, F2/F3 | después de la base de cámaras |

Aclaración (h4): los focos no proyectan patrones; la luz estructurada la hace el proyector. Los focos sirven para la
fotometría (F3) y el relighting (008). Actualizaciones de 001 y 003: la Behringer QX1222USB entra como dispositivo
**de visión** (sin MIDI) y se lee el valor de cada perilla/fader por cámara (ángulo del indicador, posición del cursor).

### Streaming y cámaras (para q12 de h1, 19:30)

- **FX3 por USB**: desde el firmware 5.0 la FX3 hace *USB streaming* (UVC/UAC, sin capturadora) a 1080p por USB-C
  3.2; latencia típica de UVC 50–120 ms. Sirve para tracking y para el escaneo si se puede fijar exposición (hay que
  probarlo: el modo UVC puede comprimir).
- **FX3 por HDMI + capturadora UVC** (4K): ~60–100 ms, sin compresión visible; es el camino para la cenital 4K.
- **FX3 por Wi-Fi**: solo *live view* para el app Monitor & Control / Creators' App (baja resolución, > 300 ms, no es
  un flujo capturable) y transferencia de archivos; **no** sirve como feed en vivo ni hace RTMP sola.
- **YC Onion**: no encontré ningún equipo de captura o streaming con ese nombre (la marca vende sliders motorizados).
  Si Max se refiere a un switcher tipo YoloBox, esos tienen entradas HDMI y algunos salida USB-C como webcam, pero no
  son capturadora hacia el PC a 4K. Falta el modelo (h4 lo pregunta).

## Pedidos 011–013 (h4, 19:31) · 011 primero porque ordena el resto

### 011 · Arquitectura distribuida Mac ↔ PC (Max: "todo tiene que correr en el windows y aquí y comunicarse")

Confirmo la división anterior y la cierro así:

```
MAC (control y sonido)                                   PC NEW-WHITE-4K (motor; estado de la escena vive aquí)
 Ableton + MIDI de controladores (OHM, Circuit, DDJ)      runtime playground :8790 (escena, efectos, hot reload)
 secretaria (chat/voz) + MCP playground (tools pg_*) ──▶  HTTP+SSE /api (comandos, estado, eventos) ◀── UI web, visores
 bridge-mac :8791: MIDI→eventos, beat de Live,            WS :8792 eventos de baja latencia (MIDI, gestos, beat, pose)
   secretaria, visores                                     captura: FX3 (USB/HDMI), webcam, Osmo, iPad (010)
 Herdr, requests/, git                                     GPU: calibración, reconstrucción, SAM, depth, tracking
                                                           render: una ventana por proyector (moderngl) en la sesión gráfica
```

- **Dónde vive el estado**: en el runtime del PC (único motor). El Mac corre un `bridge-mac` pequeño: lee MIDI y el
  beat de Live, los manda por WS al PC, y expone al MCP lo local (Ableton, secretaria). `PLAYGROUND_URL` del MCP
  apunta al PC; las tools `pg_*` no cambian. Si la perilla gobierna sonido, el MIDI se aplica en el Mac y solo se
  copia el valor al PC (sin ida y vuelta).
- **Protocolo**: HTTP+SSE (ya existe) para comandos y estado; **WebSocket** para eventos < 10 ms (MIDI, gestos, beat,
  pose del handheld); **video** solo cuando el Mac necesite ver cámaras: MJPEG por HTTP (simple, ~1 cuadro de retraso)
  y, si hace falta más, WebRTC. Se elige midiendo: objetivo de partida < 50 ms en el tramo de red para MIDI → efecto.
- **Reloj**: estampas en cada evento y cuadro; desfase medido con ping-pong WS (como NTP) y corregido en el PC.
- **Caídas**: cada lado reconecta solo; `pg_runtime` y `pg_state` muestran `links` (mac↔pc, cámaras, proyectores) con
  latencia y último latido.
- **Sesión gráfica en el PC**: por SSH el PC ve 1024x768 sin GPU para ventanas. El render corre en la **sesión
  interactiva**: inicio de sesión automático del usuario (Roberto) y una tarea programada "al iniciar sesión" que
  levanta `playground serve` con las ventanas de proyector; el PC ya no suspende. Lo configuro por SSH cuando Max
  dé el OK (cambia el inicio de sesión del PC: `ask`).
- **Seguridad**: solo LAN y Tailscale; sin puertos a internet; sin claves en el repo.
- **Hecho cuando**: diagrama en el repo (este); `pg_state` con `links`; latencia MIDI→efecto medida; reconexión
  probada apagando el Wi-Fi; hot reload funcionando con el runtime en el PC.

### 012 · Segundo proyector (probablemente el handheld)

- Planifico para **handheld** y dejo abierta la opción de fijo: el modelo de proyector en `rig.json` ya admite pose
  fija, cabezal (base + pan/tilt) o **pose en vivo** (la da 013). Cada proyector: intrínsecos propios, lienzo propio,
  ventana propia en el PC, zona.
- **Reparto**: asignación por zonas por defecto (cada objeto se proyecta desde el proyector que lo ve con mejor
  ángulo y sin oclusión de manos); **blending** con rampa y corrección de brillo/color donde se solapan. Un efecto
  puede pedir un proyector (`projector: proj1 | best`).
- **Salidas del PC**: la RTX 4070 tiene 4 salidas (3 DisplayPort + 1 HDMI). Con 3 monitores + 2 proyectores faltan
  salidas: o un monitor menos durante los shows, o un hub DisplayPort MST. Para el handheld por cable: HDMI largo;
  inalámbrico (transmisor HDMI) suma 60–150 ms y pega directo al riesgo de 013.
- Hecho cuando: dos proyectores calibrados con error en mm; solape sin junta en foto; efecto dirigible; si uno cae,
  el otro sigue.

### 013 · Pipeline handheld "viewfinder" (paralelo a la luz estructurada)

- **Pose 6DoF**: ARKit del iPad (cámara + IMU + LiDAR fusionados) → por Wi-Fi/USB al PC a 30–60 Hz; iPad→proyector
  calibrado una vez (par rígido, ChArUco visto por ambos: el proyector "ve" vía gray code). Relocalización contra el
  mapa de la mesa (009) para matar la deriva. **Predicción** de pose a +latencia (filtro de velocidad angular) para
  que el círculo no arrastre.
- **Segmentación**: SAM 2 (variante small/tiny en TensorRT) sobre el cuadro del iPad, solo dentro del círculo.
- **Profundidad monocular en la GPU** (lo elijo yo, a validar con fps medidos en la RTX 4070): candidatos 2026 con
  escala métrica: **Depth Anything V2/V3 (metric, indoor)** por velocidad (small/base > 30 fps a 720p con TensorRT),
  **UniDepthV2** y **MoGe-2** por precisión métrica, **Depth Pro** solo offline (lento). Decisión por prototipo: el
  más rápido que dé < 5 % de error métrico contra el LiDAR del iPad; la escala se recalibra con el LiDAR.
- **Algoritmo del círculo** (el "por diseñar"): máscara SAM ∩ círculo → parche de profundidad → malla local (o
  homografía si es plano) → el contenido se deforma a esa superficie y se renderiza desde la pose predicha del
  proyector con borde suave (`feather`). Primera versión: homografía por plano local; segunda: malla.
- **Riesgo principal**: latencia con el proyector en movimiento. Presupuesto: captura 16 ms + red 5 ms + SAM/depth
  20 ms + render 8 ms + proyector 16–50 ms ≈ 70–100 ms → sin predicción se ve arrastre a velocidades normales de la
  mano; con predicción de pose y círculo grande (feather) es aceptable. Se mide extremo a extremo (mover → se mueve).
- **Video al handheld**: cable HDMI (mejor latencia) o transmisor inalámbrico (+60–150 ms). Falta el modelo del
  proyector (q de h4).
- Hecho cuando: pose con error medido; círculo quieto sobre un punto marcado al moverse despacio; efecto solo dentro
  de la máscara; fps y latencia anotados (> 20 fps, objetivo a validar); convive con la luz estructurada.

### YoloBox (Max, 19:3x: el "YC Onion" es un YoloBox Encoder de YoloLiv) · para q12 y 005

- Línea YoloBox: **Ultra** (4 HDMI in + USB in, salida HDMI y **USB-C UVC**, RTMP/RTMPS/SRT/NDI, 4K30 / 1080p60);
  **Pro** (3 HDMI in, segundo USB-C se ve como **webcam UVC** en un computador, RTMP, 1080p); **Extreme** (8 HDMI,
  4K60, SRT/RTMP; sin confirmar UVC). Falta cuál es el de Max.
- Como **capturadora hacia el PC**: sí, si es Ultra o Pro: la salida USB-C UVC entrega la mezcla (programa) a
  1080p (Ultra hasta 4K30) con la latencia de un encoder de switcher (~100–200 ms estimado; sin cifra del fabricante),
  más que una capturadora UVC simple (~60–100 ms). Sirve para streaming y monitoreo, **no** para tracking ni
  escaneo (latencia y compresión). Recomendación: FX3 → PC por USB-C directo o HDMI + capturadora para el playground,
  y el YoloBox aparte para el streaming, recibiendo el lienzo del playground por HDMI (loop) o por SRT/RTMP en LAN.

### q12 resuelta (19:36): FX3 → HDMI → TX DJI → RX DJI → USB-C → PC · YoloBox solo para streaming

Pasos para Max (lo físico lo hace él):
1. **Firmware del TX y del RX** a ≥ V01.02.07.05 (dic-2024, trae "PC streaming"): app **DJI Assistant 2 (Ronin
   Series)** en un computador, TX y RX conectados por USB-C de a uno, encendidos; en la app elegir el equipo →
   Firmware Update → instalar. (Alternativa: app DJI Ronin en el celular, menú del dispositivo → firmware.)
2. **Activar "PC streaming" en el RX**: en el menú del receptor (pantalla/botones del RX o desde la app Ronin
   conectada al RX): Settings → **PC Streaming** → ON. Conectar el RX al PC por el USB-C de **datos** ("Firmware Update
   and Video Output", no el de power) con un cable USB-C↔USB-A o C↔C al PC.
3. Enchufar: FX3 HDMI → TX; emparejar TX/RX (vienen emparejados en el combo); RX → PC.
4. Cuando esté: `slscan remote run --host 192.168.1.233 --user "Roberto Mardones" "python windows/doctor.py"` tiene que
   listar una cámara nueva (DJI, 1920x1080) y mido la latencia real con un parpadeo de pantalla visto por la FX3
   (comando `slscan latency` en el PC). Límite conocido: 1080p60 H.264 a 20 Mbps; la prueba de patrones finos con esa
   compresión queda para el escaneo.

### 014 · Los 3 monitores como superficies + piano roll que cae a las teclas (h4, 19:55) y 006 actualizado

- **006**: el teclado manda MIDI al PC → verdad de referencia para el mapeo tecla↔nota (61 teclas, error 0) y para el
  desfase MIDI↔visión; la visión sigue dando posición y contorno de cada tecla y qué mano la toca.
- **014**: 2 monitores sobre el teclado + 1 sobre la DDJ-SB2, en fullscreen, como superficies del playground. Cada
  monitor = objeto `screen` con pose 3D calibrada (ChArUco mostrado en la pantalla y visto por las cámaras) y un
  lienzo propio. Efecto `piano_roll` (galería): notas desde un clip de Ableton (`get_clip_notes`), un MIDI o lo que
  se toca; una sola línea de tiempo en mm/s para pantalla + bisel + aire + tecla (la nota desaparece detrás del bisel
  y reaparece en la tecla a la hora exacta); sobre la tecla la pinta el proyector con el contorno de 006 y marca
  acierto/error con el MIDI. Monitor de la DDJ: ondas, fase, valores de perillas (003) y efectos de galería.
- **Salidas de video**: 3 monitores + 2 proyectores = 5 > 4 salidas de la RTX 4070. Opciones (decide Max): el monitor
  de la DDJ al Mac (ahí están la DDJ, Ableton y el MIDI), gráficos integrados de la placa si los hay, adaptador
  USB (más latencia) o handheld inalámbrico. Y confirmar la geometría exacta de los monitores sobre el teclado.
- **Sincronía** entre salidas: medir el retraso de cada pantalla y proyector con cámara y compensar en la línea de
  tiempo. Depende de 006, 003/001, 012, 011 y 009.

### VNC al PC (pedido de Max, 19:35) · estado

- Contraseña generada y guardada en el llavero del Mac (`sl-scanner-vnc`, cuenta `max`), nunca escrita en archivos.
- **Bloqueado por permisos de este agente**: la instalación de TightVNC como servicio en el PC y la modificación de la
  skill `vnc-desktop` (hoy solo conecta a la VM QEMU sin contraseña; TightVNC exige autenticación DES) fueron
  rechazadas por el clasificador de permisos ("Unauthorized Persistence"). Lo que falta, para que lo haga Max o se
  me permita: en el PC, `winget install GlavSoft.TightVNC` con `--override` (ADDLOCAL=Server, autenticación VNC
  con la contraseña del llavero, sin HTTP), firewall 5900 solo LocalSubnet + 100.64.0.0/10, y en la skill leer
  `VNC_HOST`/`VNC_PASSWORD` del entorno con auth tipo 2. Ver nota de h3 a h1.

### Profundidad para el handheld (013) y por cámara: Video Depth Anything (Max, 19:40) · evaluación

- **Qué es**: Video Depth Anything (ByteDance, CVPR 2025; github.com/DepthAnything/Video-Depth-Anything), Depth
  Anything V2 con atención temporal: profundidad de video **consistente en el tiempo** (sin parpadeo), videos de largo
  arbitrario. Variantes Small 28,4 M (Apache-2.0), Base 113,1 M y Large 381,8 M (CC-BY-NC: ojo para uso comercial).
  **Hay checkpoints métricos** (Metric-VDA-S/B/L, entrenados en Virtual KITTI + IRS; KITTI δ1 0,877/0,887/0,910,
  NYUv2 0,850/0,883/0,908), justo lo que necesita la proyección (escala real).
- **Modo streaming** (experimental, `run_streaming.py`, un cuadro a la vez con caché de estados de la atención
  temporal; desde sep-2025 también para los métricos): es el que sirve en vivo. Costo: baja la precisión frente al
  modo offline de 32 cuadros (ScanNet δ1 0,926 → 0,836).
- **Velocidad** (dato oficial, A100, 518×518): Small 7,5 ms/cuadro fp16 (6,8 GB VRAM en lote de 32), Large 14 ms
  (23,6 GB). **Estimación RTX 4070** (≈ 0,4× A100 en fp16, 12 GB): Small ≈ 15–20 ms → **50–60 fps** a 518 px;
  Base ≈ 30–40 ms → 25–30 fps; Large no cabe en lote (en streaming cuadro a cuadro sí, ~60 ms → ~15 fps). Cifras
  a medir en el PC (ya tiene torch CUDA): script de benchmark con 300 cuadros de la webcam, fp16, 518 y 756 px.
- **Alternativa de referencia**: FlashDepth (arXiv 2504.07093) es streaming nativo a 2044×1148 a 24 fps (GPU no
  dicha); dice que VDA hace 24 fps a 924×518 pero en lotes de 32. Para 2K en vivo es el candidato si VDA-S queda corto.
- **Decisión propuesta**: **Metric-Video-Depth-Anything-Small en modo streaming, fp16, 518 px** para el handheld (013)
  y para la profundidad por cámara en vivo (009); **Base** offline cuando importe la calidad (escaneo de apoyo, 008);
  escala y deriva corregidas con el LiDAR del iPad y con la luz estructurada donde haya. Entra a `requests/013` como
  elección de h3; se confirma con el benchmark en la 4070 (fps, VRAM, error vs LiDAR).

### Reglas de uso del VNC al PC (h1, 19:47)

- Las capturas del PC son privadas: viven en `reports/` fuera de git, se borran al terminar de usarlas y nunca se
  suben ni se pegan en notas. (Las dos de la prueba ya se borraron.)
- No se leen, transcriben ni tocan ventanas personales de la sesión (correo, chats, navegador de Roberto). Solo
  ventanas propias: playground, displays, configuración.
- Avast puede poner en cuarentena scripts o el runtime: se avisa a h1 y no se desactiva.
- Refuerza q15: una cuenta `playground` aparte evita trabajar sobre la sesión personal de Roberto (decide Max).

### q15 resuelta (19:48): misma cuenta (Roberto) · estado

- Hecho por SSH: tarea programada `sl-scanner-playground` (al iniciar sesión de Roberto, `python -m playground serve
  --host 0.0.0.0 --port 8790 --window`, reintentos), firewall 8790 solo LAN y tailnet, runtime corriendo en la
  sesión gráfica (ventana "playground" visible por VNC; `http://192.168.1.233:8790/api/schema` responde desde el Mac).
  El playground y sus dependencias están sincronizados e instalados en `C:\sl-scanner`.
- Pendiente de Max/Roberto (necesita la contraseña de Windows, que yo no manejo): el inicio de sesión automático.
  `windows/autologin.ps1` instala Sysinternals Autologon, pide la contraseña en pantalla una vez (queda cifrada en
  LSA, no en texto plano) y deja también la tarea y el firewall por si se corre en un PC nuevo.

### Foco SmallRig RC 100C: ingeniería inversa del USB-C (DMX) y de la app (Max, 19:55)

**Qué se sabe del puerto.** El USB-C del RC 100C es "Power/DMX input": entra PD (hasta ~110 W) y DMX con un adaptador
USB-C→XLR que SmallRig vende aparte; para el RC 100C **no hay adaptador a la venta** (FAQ de SmallRig: "no DMX
adapter for RC COB 100C currently"; el 4390 de USD 39,99 es para RC 220 Pro/350/450). No existe pinout público ni
desarme del adaptador. DMX512 es RS-485 diferencial (par A/B + común) a 250 kbps, 8N2, con break ≥ 88 µs y MAB ≥ 8 µs.
Los adaptadores de este tipo (amaran USB-C→5-pin DMX, Godox) son pasivos: llevan el par RS-485 por pines del USB-C que
no son de potencia. Candidatos, de más a menos probable: **D+/D− (USB 2.0)** como A/B con GND, o **SBU1/SBU2**, o CC
reusado. Hay que medirlo; no se adivina.

**Medición (Max, con multímetro o analizador lógico; nada se conecta a VBUS/PD):**
1. Breakout USB-C de **enchufe** (plug) con todos los pines (Adafruit USB Type-C Plug Breakout, ~USD 3–7) enchufado al
   foco encendido y en modo DMX (menú del RC 100C: DMX → ON, dirección 1). **No** conectar nada a VBUS.
2. Multímetro DC entre GND y cada pin (D+, D−, SBU1, SBU2, CC1, CC2): un receptor RS-485 en reposo muestra ~2–3 V en
   A/B o una diferencia fija A−B de ~0,2–0,4 V; los pines CC muestran los pull-down de 5,1 kΩ del sumidero (ignorar).
3. Mejor: analizador lógico (Saleae/clon, ~USD 15) a 1 MS/s en D+, D−, SBU1, SBU2 mientras **otro** controlador DMX
   (o nuestro FTDI) manda tramas: el par que muestre la trama de 250 kbps es el DMX. Si el foco solo *recibe*, el
   par se identifica por las resistencias de terminación/bias (~120 Ω entre A y B con el foco apagado).
4. Anotar: pin A, pin B, GND; si A/B invertidos el foco ignora las tramas (probar las dos polaridades).

**Camino barato para controlarlo sin el adaptador oficial** (lo elige Max; nada se compra sin él):
- **Opción A · RS-485 directo (~USD 35–45)**: breakout USB-C plug (~USD 3–7) + cable **FTDI USB-RS485-WE-1800-BT**
  (~USD 30, Digi-Key/Farnell) → el PC o el Mac lo ven como puerto serie; nuestro `lights/dmx.py` manda tramas DMX
  (break por `serial.send_break` o pyftdi, 250 kbps 8N2, 40 Hz) y mapea canales (intensidad, CCT, HSI, efectos: el
  mapa de canales del RC 100C sale del manual o se descubre barriendo canales con el foco a la vista). Cableado:
  A→pin A, B→pin B, GND→GND del USB-C; **nada a VBUS**. Sirve también para la barra DMX.
- **Opción B · Interfaz DMX comercial (~USD 70–75)**: **Enttec Open DMX USB** (~USD 67–70; FTDI + RS-485 en caja,
  con XLR 5 pines) + el mismo breakout USB-C soldado a un XLR. Más robusto y reutilizable con cualquier luz DMX;
  mismo software (es FTDI). **Enttec DMX USB Pro** (~USD 162–170) solo si se quiere aislamiento y timing en hardware.
- **Opción C · Bluetooth (gratis, tiempo de Max)**: en el Pixel 9, Ajustes › Opciones para desarrolladores › "Registro
  de Bluetooth HCI" ON; abrir SmallGoGo, parear el RC 100C y mover intensidad, CCT y color unas 10 veces cada uno;
  luego `adb bugreport` (o compartir el bugreport desde el teléfono) → dentro va `btsnoop_hci.log` → Wireshark.
  Si es Bluetooth Mesh (lo que sugiere el "Mesh Device" del escaneo), el tráfico va cifrado con claves de red y la
  captura solo sirve para confirmar el modelo; controlarlo exige aprovisionar nuestra propia red mesh (días). Si es
  GATT simple, con 2–3 capturas saco los bytes y `pg_light set` queda andando en una tarde.

**Recomendación**: A (medir primero, ~USD 35–45) y C en paralelo porque es gratis; B si Max prefiere algo de caja y
sirve para más luces. Riesgo controlado: con el breakout nunca se toca VBUS/PD, y RS-485 tolera polaridad invertida.

### 011 v2 (Max, 20:0x): el Mac sale al proyector 1 con la Osmo; la torre (PC) al proyector 2 con la GPU

Palabras de Max: "este compu, el mac, lo usaremos para salir al proyector y tener como webcam input la dji osmo, y la
sony nos ayuda con visión extra igual que la webcam… el otro proyector va a ir directo a la torre, pero el compute de
la torre lo tenemos que usar. el proyector no es 4k, así que podemos optimizar el streaming lan de la señal de la
cámara". Entiendo "la torre" = PC NEW-WHITE-4K (si fuera otra máquina, se pregunta).

```
MAC (render proyector 1 + sonido + control)              PC NEW-WHITE-4K "la torre" (render proyector 2 + GPU)
 proyector 1 ◀── render local (moderngl/cv2 fullscreen)     proyector 2 ◀── render local (tarea al iniciar sesión)
 Osmo (UVC por USB) ── cuadros ──▶ runtime Mac ──LAN──▶     captura: FX3 (DJI RX por USB-C), webcam de pared
 Ableton + MIDI + secretaria + MCP                          GPU: calibración, SAM, depth (VDA), tracking, escaneo
 escena compartida: el PC es la fuente de verdad; el Mac mantiene una réplica para renderizar sin esperar a la red
```

- **Escena**: una sola, con el estado maestro en el PC (geometría, objetos, poses, personas). El Mac guarda una
  **réplica** que se actualiza por WebSocket (deltas de poses a 30–60 Hz) y renderiza el proyector 1 desde ella: si
  la red se cae, el Mac sigue proyectando lo último. Los efectos corren en los dos runtimes con el mismo código
  (hot reload en ambos: `slscan remote sync` + recarga automática); cada efecto declara `projector: proj0 | proj1 |
  best` (012).
- **Cámaras**: Osmo en el Mac (entrada local del proyector 1: alineación y tracking cercano); FX3 y webcam en el PC.
  **Streaming LAN de cámaras** solo cuando el otro lado las necesite, y **a la resolución útil del proyector**
  (1920×1080 o la del proyector real, no 4K): MJPEG a 15–30 fps (~3–8 MB/s, latencia de un cuadro) para
  visualizar; para tracking viajan **resultados** (poses, máscaras comprimidas, puntos), no video.
- **Reparto del cómputo**: el PC hace todo lo de GPU (SAM, Video Depth Anything, tracking, escaneo) con los feeds
  del PC y, si hace falta, con el de la Osmo bajado por LAN; devuelve poses y máscaras. El Mac no corre modelos.
- **Puertos**: PC runtime :8790 (HTTP+SSE), :8792 (WS eventos/réplica); Mac runtime :8790 local, :8791 bridge
  (MIDI, beat de Live, Osmo MJPEG). Solo LAN y tailnet.
- **Pendiente para cerrar 011**: medir latencia Osmo→Mac→proyector 1 (objetivo < 60 ms) y PC→Mac (réplica, < 20 ms
  en LAN cableada); confirmar qué máquina es "la torre".

### Hardware detectado en el Mac (15-oct 20:22)

- **Proyector**: display `T749-fHD720`, 1920×1080, conectado en **espejo** (Mirror: On, duplica la pantalla del Mac).
  Para renderizar el proyector 1 desde el playground conviene **extender** en vez de duplicar (Ajustes › Pantallas ›
  Usar como pantalla extendida): así la ventana fullscreen va solo al proyector y el Mac queda libre. Lo cambia Max.
- **Osmo Pocket 3**: aparece como cámara UVC (VendorID 0x2CA3 DJI) en el índice 0 de AVFoundation; entrega hasta
  3840×2160 (25 fps) y 1920×1080; está en **vertical** (cuadro nativo 1080×1920): hay que ponerla horizontal o
  rotar el cuadro en el pipeline. Modo webcam: comprimido (no sirve para patrones finos; sí para alinear y trackear).
- Prueba hecha: patrón de prueba fullscreen (mirror → proyector) y cuadro de la Osmo con el patrón sobre la mesa de
  vidrio y el piso: `reports/osmo_con_patron.png` (privado, fuera de git). Los objetos y posiciones son provisionales.

### Proyector 1 + Osmo: primera toma guiada lista (20:35)

- **Pantallas**: el T749 pasó de espejo a **extendido** con `displayplacer` (Mac 1470x956 principal, proyector 1920x1080
  a la derecha = índice 1, iPad Sidecar a la izquierda = índice 2). Volver atrás:
  `captures/displayplacer_rollback_mirror.txt` (el comando exacto que daba `displayplacer list` antes del cambio).
- **Osmo Pocket 3 en modo webcam**: manda su cuadro vertical centrado con bandas negras dentro de 1920x1080 (o
  3840x2160). Con la Osmo horizontal se llena el cuadro; `--rotate` queda por si acaso.
- **Latencia proyector→Osmo**: no se pudo medir (la Osmo no apuntaba a la proyección: la estaban moviendo). Se mide
  con `slscan latency --camera 0 --screen 1` cuando esté fija; mientras, `--settle 0.5`.

**Pasos cortos para Max (calibración del proyector 1 con la Osmo):**
1. Osmo horizontal, fija (trípode o apoyada), mirando la zona que ilumina el proyector. No la muevas durante las tomas.
2. **Intrínsecos de la Osmo con el tablero en el iPad**: en el Mac, `slscan show-board --screen 2` (lo muestra en
   el iPad por Sidecar a pantalla completa; el comando dice cuántos px mide la casilla: mídela con regla en el iPad y
   anota los mm). Luego `slscan capture-calib --cameras 0 --names osmo --views 25 --square <mm medidos> --out
   captures/osmo_intr`: mueve el iPad frente a la Osmo (inclinado, cerca, lejos, en las esquinas); se captura solo
   cuando está quieto y bien enfocado. Después `slscan calib-intrinsics captures/osmo_intr --glob "view_*/osmo.png"
   --name osmo --square <mm> --out captures/osmo.json --png reports/osmo_distorsion.png`.
3. **Superficie plana y mate** para proyector-cámara (la mesa es de vidrio, no sirve): la más simple es una **cartulina
   blanca o cartón forrado (A3 o más)** apoyado sobre la mesa o contra la pared, con el **tablero impreso** encima
   (`reports/board_7x5_40mm.png` al 100 %; en A4 queda a ~28 mm por casilla: anota el valor real). La pared también
   sirve si el proyector la ilumina y la Osmo la ve.
4. `slscan calib-procam --camera 0 --name osmo --screen 1 --size 1920x1080 --poses 10 --square <mm> --out
   captures/procam`: la ventana guía dice cuántas esquinas ve; ESPACIO captura el tablero y luego la secuencia gray
   code (no tocar nada ~40 s); cambia inclinación y posición entre poses (10 poses).
5. `slscan calib-rig captures/procam --intrinsics captures --proj-size 1920x1080 --out captures/rig.json --html
   reports/rig.html`: reproyección < 0,5 px cámara y < 1 px proyector = calibrado. `reports/rig.html` muestra el rig.

### Noche del 15/16-oct: primera escaneada (estado 00:05)

- Pantallas: T749 extendido a 1920x1080 (índice 1 ahora que el PJ588D se desconectó); Mac y Sidecar en espejo entre
  ellos (no se tocan). Ventana de patrones rehecha con **SDL/pygame**: sin bordes, tamaño y posición exactos del
  proyector, no se cierra sola, log en `~/.local/state/sl-scanner/pattern.log`. Verificado con `screencapture -D 2`.
- Osmo Pocket 3 en modo webcam: se elige por nombre (`--camera osmo`: cuadro vertical, o 25 fps si está horizontal).
  Vertical entrega 9:16 con bandas (pierde 2/3 de resolución): Max la gira a horizontal.
- Primer ChArUco proyectado sobre el escritorio, visto por la Osmo (00:02). Sobre el escritorio no se detectan
  esquinas (no es plano); hace falta la superficie plana blanca.
- **Calibración gruesa con patrón proyectado** (`slscan calib-projected`): por pose, el proyector muestra el ChArUco,
  la Osmo lo ve, Max mide el ancho proyectado (mm) → escala; 3 poses (plana, inclinada a cada lado) → intrínsecos de
  cámara y proyector + pose relativa → `captures/rig.json` + `reports/rig.html`. Supone casillas uniformes (error con
  keystone, aceptable para la primera nube). Después: `slscan scan --rig captures/rig.json --rotate 0`.
- Pendiente de refinar: intrínsecos de la Osmo con tablero físico (Pixel o impreso), latencia real (auto-exposición
  en modo webcam), phase shift.

### Primer escaneo real (16-oct 00:1x) · resultado

- `slscan scan` con la Osmo (vertical, rotada 270 por software) y el T749: 44 patrones gray code a 1 s. Hallazgo: la
  cámara ve ~3,4 px de proyector por px de cámara, así que los 2–3 bits más finos no se resuelven; `--skip-bits 3`
  (resolución efectiva de proyector 240x135 bloques) decodifica **123 181 px** (toda la zona proyectada: cuadro,
  teclado, DDJ, cocodrilo). Panel color + mapa x + mapa y: `reports/scan1_maps.png` (privado). Datos:
  `captures/scan1/osmo/` (44 PNG + `map.npz`).
- Puntos blancos en las 4 esquinas del proyector (inset 120 px) trackeados con OpenCV en la Osmo: los 4 detectados;
  homografía proyector→cámara en `reports/scan1_corners.json`. Contra la homografía del gray code (RANSAC sobre 4000
  correspondencias, 65 % inliers porque la escena no es un plano) difieren 2–21 px: coherente.
- **Sin rig todavía no hay nube métrica**: falta la calibración (3 medidas con regla con `calib-projected`, o el
  tablero físico). Con la homografía ya se puede hacer projection mapping **plano** sobre el escritorio.

### Primera visual generativa sobre la escena (16-oct 00:3x)

- **Vista del proyector** (`captures/scan1/scene_pov.png`): la escena remapeada al espacio del proyector con el
  mapa denso del gray code (interpolación de los bloques); 97,6 % de los píxeles del proyector con correspondencia.
- **Nube relativa**: paralaje de cada correspondencia respecto al plano dominante (homografía RANSAC) = altura
  relativa (`captures/scan1/height_proj.npy`, `scan1_relative.ply`, `reports/scan1_relative_viewer.html`): la torre
  sobresale clara; sin calibración no es métrica.
- **Segmentación heurística en el espacio del proyector** (`captures/scan1/segments.json`): Buda (tonos cálidos →
  cuadrilátero), torre (blanco + altura, tercio izquierdo), hoja carta (blanco, aspecto ~1,1–1,3, a la derecha del
  Buda), teclado (banda con bordes verticales densos: 23 teclas blancas y 8 negras detectadas), controladores (oscuro,
  abajo). Siguiente nivel: SAM en el PC.
- **Shaders** (`playground/firstlook.py`, 14,8 fps en CPU a 960x540): halo dorado pulsante + anillos sobre el Buda
  (con su propia textura), scanlines de color en la torre (brillo por altura), ruido de TV en la hoja, grilla de puntos
  en los controladores, onda arcoíris por tecla blanca y pulso azul en las negras, fondo plasma con los contornos del
  relieve (gradiente de la altura). Capturas de la Osmo: `reports/firstlook_osmo_2.png`, `reports/buda_lit_osmo.png`
  (el Buda iluminado solo en blanco), `reports/buda_pov.png` (la pintura desde el proyector). Privadas.
- **Hoja carta como regla**: queda en el plano de la pantalla; con sus 4 esquinas (215,9×279,4 mm) la escala en ese
  plano es ~0,8 mm por px de proyector. Para `calib-projected` se puede pegar la hoja a la cartulina y usarla como
  medida automática en cada pose (sin regla): pendiente de implementar la detección del cuadrilátero en la Osmo.


## 16-oct (madrugada) · runtime con escena real, overlay, secretaria por voz

- **Osmo**: a 1920x1080 entrega el cuadro encajonado (607x658 px útiles); se pide 3840x2160 y `downscale=2`. Causa de la mala calidad del escaneo 3.
- **Escaneo vigente: `captures/scan5`** (luz apagada). Verificación con marcadores ArUco sueltos (el ChArUco entero no se detecta sobre objetos): mediana 1,05 px. Mapa denso con relleno de agujeros. `captures/scan5_brio`: gray code desde la Brio del PC (camserve :8794) → tracking a dos cámaras (`playground/dualview.py`, con deriva de cámara).
- **Runtime con escena real**: `scene_from_scan` (1 px = 1 px del proyector, y hacia abajo), `python -m playground serve --scan captures/scan5 --projector`. Comandos nuevos: `nudge` (ajustes finos, persistidos en `<scan>/ajustes.json`), `reload_scene`, `overlay` (imagen de calibración N s). La demo (mesa 1600x800 mm) era la causa del "nada calza".
- **Regla de proyección**: con el runtime en el proyector, macOS deja su ventana encima de cualquier otra. Todo lo que se proyecta es un efecto del runtime o un `overlay`; los agentes no abren ventanas propias. PatternWindow: fullscreen clásico (`SDL_VIDEO_MAC_FULLSCREEN_SPACES=0`, `SDL_VIDEO_MINIMIZE_ON_FOCUS_LOSS=0`): el fullscreen con Spaces dejaba el iPad en negro y la ventana se minimizaba al perder foco. Si el proyector muestra su pantalla azul "Smart Projector" (pasó al desconectar el Sidecar), re-aplicar el modo con displayplacer renegocia el HDMI.
- **Secretaria por voz** (`playground/secretaria`, agente ElevenLabs "Playground", nativa en el Mac, sin túnel): narra avances (`avances.jsonl`), recibe feedback (`feedback.jsonl`, `tail -F` desde h3), `pg_mover` corre objetos por voz ("un poquito más abajo" = 4 px). Reiniciar desde master: `voz --parar` y `voz --fondo --sin-saludo`; `publicar` actualiza las tools.
- **Segmentación scan5**: cocodrilo por tono verde-cian + GrabCut acotado (franja de 290 px sobre las teclas del EK-50); platos de la DDJ en todo el ancho con par geométrico; OHM a la izquierda; hoja carta con tinte cian (sat<60, val>190, relleno>0,6). `croc_refine.py` (checker + búsqueda por IoU) y `croc_resegment.py` (foto nueva, con guardias: nunca aplica algo peor).
- **Pendientes**: Circuit Tracks no segmentado; EK-50 no manda MIDI al PC (aparece como "Generic USB MIDI 0"); QX1222USB no la ve el Mac (audio reactive con micrófono interno); sonido por la mesa del PC; showcase final (`playground/showcase.py`) cuando lleguen buda_breath/ddj_skin/torre_faces/papel_canvas y pianohero como efecto.
### Piano Hero proyectado (Max, 16-oct 02:2x) · estado 03:xx

- `playground/pianohero/` + efecto `pianohero` + `pg_pianohero` + CLI `python -m playground.pianohero`. Las notas bajan
  por el carril de cada tecla real (polígono de keys.json prolongado hacia atrás). Hay 3 niveles y modo práctica, 3
  melodías incluidas más una escala para calibrar, y se puede cargar cualquier `.mid`. Detalle en
  `playground/pianohero/README.md`.
- **MIDI**: `windows/midi_bridge.py` (PC, UDP 8796) → `playground/devices/keyboard.py` (Mac). La fuente es auto:
  local si el Korg está en el Mac, si no el puente. Con `--fake` PC → Mac, ida y vuelta de 2 a 5 ms y 0 paquetes
  perdidos.
- **El EK-50 no manda notas al PC**: el puerto abre, pero no llega nada. El PC tiene Windows 11 26300 con MIDI
  Services. El diagnóstico está en el README.
- **Teclas de scan5**: el detector borde a borde da 0,34 (perspectiva fuerte). Se agregó un ajuste por modelo,
  `playground/keys/fit.py`: homografía del teclado entero con ECC. En scan5 da correlación 0,83; contra un teclado
  sintético con la misma perspectiva el error es < 0,4 px. `python -m playground.keys` (método auto) lo usa cuando
  los bordes dan < 0,6. Verificado con la Osmo (7 s de proyección): la luz cae sobre las 61 teclas del EK-50.
- **Afinado tecla por tecla con cámaras** (Max, 03:16): `python -m playground.keys.refine captures/scan5`. Proyecta por
  el overlay del runtime el relleno de cada tecla corrido y captura con la Osmo y la Brio a la vez, con fondo checker
  de contraste. Mide IoU contra la silueta real de cada tecla (llevada al proyector con el mapa de cada cámara) y hace
  primero un corrimiento global 7x7 y después dx por negra. Las blancas siguen a sus negras vecinas. Escribe
  `keys.json` con `ajuste` por tecla y `keys_refine.json` (IoU antes/después). En sintético pasa de 0,42 a 0,74 con
  111 capturas.
  - Primera corrida en vivo (03:39): no sirvió. IoU 0,24 → 0,33 y el global quedó en el borde de la grilla (+6,+6).
    Se revirtió keys.json; la corrida quedó guardada en `keys_refine_rechazado.json`. Ahora la grilla global es
    ±12 px (gruesa con paso 4, luego fina), solo escribe si el resultado es concluyente (óptimo fuera del borde,
    IoU ≥ 0,5, las dos cámaras de acuerdo) y `--debug` guarda imágenes de siluetas y de lo iluminado para revisar
    antes de otra corrida.
- **Corrimiento global del proyector (16-oct, 03:5x)**: `python -m playground.keys.quad captures/scan5 --offset`
  proyecta un patrón aleatorio y su negativo por overlay, los lleva al proyector con el mapa de la Osmo y los compara
  por correlación de fase. Midió que la luz cae (+0,8, −19,9) px respecto del mapa de scan5, en toda la escena
  (respuesta 0,45). La cámara no se movió: calza con scene_pov a 0,2 px. Con el relleno de las 61 teclas corrido
  (−0,8, +20), el IoU pasa de 0,62 a 0,70. El cuadrilátero de las teclas blancas en scene_pov (`quad.py`: aristas con
  Huber y subpíxel) calza con keys.json a ≤1 px en el frente. Conclusión: está corrida la salida del proyector, no
  las teclas. Se corrige con un corrimiento global de la salida (−1, +20) o reescaneando el mapa; reescribir
  keys.json no lo arregla. Lo decide el coordinador.
### Visuales v3 como efectos del runtime (16-oct 03:3x–04:3x)

- Feedback de Max: el Buda desalineado y sin su foto, la torre y la DDJ sin usar su superficie, la hoja sin usar.
  Causa: `segments.json` traía rectángulos de área mínima (el "quad" del Buda se comía el teclado; la "hoja" era la
  pantalla entera; los platos eran círculos corridos 30-50 px; la "cara de arriba" de la torre caía en la pared).
- `playground/scan_geom.py`: geometría fina desde `scene_pov.png` (tapiz por colores saturados sin el cocodrilo; hoja
  por Otsu; platos por correlación con una plantilla elíptica; pads y perillas por zonas claras; relieve robusto).
  Caras reales del PC en `playground/torre_caras.json` (marcadas con la Osmo y llevadas al proyector con
  `proj_to_cam`). `scene_from_scan` las deja en la escena; los efectos usan la escena viva (con ajustes).
- Efectos: `buda_breath`, `ddj_skin`, `torre_faces`, `papel_canvas`, `scene_texture` (aliases por voz). Runtime:
  param `capa`. `Canvas.polygon` con alfa mezcla solo la caja del polígono (keys_map 120 ms → 2 ms por cuadro).
- Verificado con la Osmo por overlay: con el `output_offset` (−1, +20) de la salida, lo proyectado cae a 0-4 px de lo
  real en Buda, DDJ, torre y hoja. Tests: `tests/test_visuals_v3.py` (escaneo sintético + scan5 si está).

