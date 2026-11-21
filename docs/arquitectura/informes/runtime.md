# Informe: núcleo del runtime y plano de control (`sl-scanner/playground`)

Los cuatro tests de mi área pasan: 21 aprobados y 1 omitido. Además medí en este Mac el costo del tick y de la codificación del cuadro (sección 8). En el texto marco **[V]** lo verificado (leído o ejecutado) y **[I]** lo inferido.

## 1. Propósito y funcionalidades

- **Runtime** (`playground/runtime.py`):
  - Tiene una escena viva de objetos con contorno y bbox, más audio, beat, controles MIDI, toques, manos y mirada.
  - Mantiene una galería de efectos como plugins con hot reload.
  - Compone el lienzo del proyector (numpy BGR) y expone una API HTTP: estado, comandos, SSE, PNG del cuadro y visor 3D.
  - Puede sacar el lienzo al proyector con SDL.
- **MCP stdio** (`mcp_server.py`): 25 tools `pg_*`. Es un proxy delgado sobre la API HTTP y además gestiona procesos (levanta el runtime y el cliente de audio).
- **Dashboard** (`dashboard.py`, `:8795`): lanza y cancela `playground.dale` (calibrar, escanear, pipeline y efectos), muestra el avance por SSE, hace de proxy de spawn, kill y param hacia el runtime, corre escenas del showcase y muestra una miniatura del cuadro.
- **Salida alternativa** (`ventana_proyector.py`, `:8793`): un servidor mínimo de presentación. Es una ventana normal con barra de título que muestra imágenes recibidas por HTTP. Nació después de varios kernel panics DCPEXT1 con ventanas sin bordes (`ventana_proyector.py:1-4`).
- **Visores web**:
  - Escena 3D: heightfield, objetos y el lienzo en vivo como textura (`web/`).
  - Escena métrica registrada (`registered.*`): render Three.js con warp óptico que se envía al runtime.
  - Visor de calibración (`calibration_web/`), servido por `slscan/calibration_viewer.py`.
- **Herramientas de escena concreta**:
  - `workbench.py`: segmentación clásica con OpenCV, proyectando cada paso.
  - `dualview.py`: tracking por template matching con dos cámaras.
  - `stage.py`: formas en px del proyector con fallback de scan4.
  - `showcase.py`: conjuntos de efectos.
  - `vnc.py`: cliente RFB propio para controlar el PC.

## 2. Arquitectura interna

**Módulos y responsabilidades [V]**

- `effects/base.py`
  - `Canvas` (`:37-81`): imagen `(h,w,3)` uint8 BGR, `project(x_mm,y_mm)` y `polygon()` con alfa limitado al bbox (`:63-78`).
  - `Effect` (`:84-127`): metadatos declarativos (`name`, `needs`, `targets`, `params`, `aliases`), ciclo `update(dt, scene)` / `draw(canvas)` / `set` / `kill`, y además `meta()` y `state()`.
  - Utilidades `bgr`, `pulse`, `offset_contour`. Hacia afuera los colores van en RGB y por dentro en BGR.
- `runtime.py`
  - `EffectRegistry` (`:56-121`): descubre `effects/*.py`, importa y recarga, y registra las subclases de `Effect` por `cls.name`.
  - `Runtime` (`:124-732`): escena, instancias, tick, eventos y suscriptores, comandos, dispositivos y audio.
  - `SCHEMA` (`:735-765`).
  - `build_app` (FastAPI, `:770-825`) y `serve` (`:828-869`).
- `scene.py`
  - Dataclasses `SceneObject`, `Person` y `Scene` (`:22-66`).
  - `find` por subcadena y alias, con la primera coincidencia (`:72-77`).
  - `nudge`, que persiste en `ajustes.json` (`:79-113`).
  - Constructores: `demo_scene` (mm con y hacia arriba), `scene_from_scan` (px del proyector con y hacia abajo, desde `segments.json`, `workbench.json`, `keys.json` y `ajustes.json`) y `scene_from_objects` (`world_mm`, `:267-293`).
- `requests_queue.py`: cola de pedidos de features en `requests/NNN-slug.md` con frontmatter.
- `web/scene3d.py`: `/api/scene3d` y la textura; `web/registered_api.py`: transporte del cuadro métrico.

**Dependencias que salen de mi alcance [V]**

- `devices/`: `DeviceRegistry`, fuentes MIDI y teclado.
- `audio.features`, `touchsynth.registry` (`TouchState` colgado de `scene.__dict__["_touch"]`) y `touchsynth.objetos`.
- `auto_scene.activate_runtime`.
- `slscan.registered.RegisteredScene`, `slscan.capture.PatternWindow` y `slscan.contracts`.

**Flujo de datos [V]**

1. Los productores externos (cliente de audio a ~50 Hz, puente de manos y toques, MIDI, MCP, secretaria, dashboard) envían `POST /api/command` a `Runtime.command`.
2. El comando muta la escena, las instancias o las colas (`_inputs`, `audio_in`, `TouchState`) y emite un evento hacia `events` y las colas de cada suscriptor SSE.
3. El hilo de tick hace `hot_reload`, anima la escena, drena los inputs, aplica audio y bindings, y por cada instancia en orden de `capa` llama `update` y `draw` sobre un `Canvas` nuevo. Luego aplica overlay y offset y deja el resultado en `rt.frame`.
4. `rt.frame` lo consumen el lazo de presentación SDL, `/api/frame.png` y `snapshot`.

## 3. Ejecución y concurrencia

**Procesos [V]**

- Runtime `python -m playground serve` (`:8790`).
- MCP stdio, que lanza el runtime y el cliente de audio desacoplados con `start_new_session` y pidfiles (`mcp_server.py:64-84`, `382-408`).
- Dashboard (`:8795`), que lanza dale y showcase.
- dale, que hace `pkill` de runtimes y lanza el visor del pipeline (`:8796`) y un runtime nuevo con `--projector` (`dale.py:87-94`, `325-329`).
- `ventana_proyector` (`:8793`).
- Clientes de audio, manos y touchsynth (fuera de alcance).

**Hilos del runtime [V]**

- **Tick**: hilo daemon con `rt.run` (`runtime.py:833`). Duerme `max(0, 1/fps − t)` (`:457`) con fps = 30 (`:125`); `serve` no tiene flag `--fps`. No salta cuadros: si un tick tarda, baja la tasa, y `dt` es real.
- **Presentación**: con `--projector`, el hilo principal corre SDL y uvicorn va a un hilo aparte (`:843-845`). El lazo hace `win.show(rt.frame)` y luego `sleep(1/fps)` (`:862`). `show()` ya espera un período de refresco (`capture.py:274-279`), así que la tasa de presentación queda por debajo de 30 Hz y no tiene fase con el tick [I]. Resultado: cuadros repetidos y saltados (judder).
- **HTTP**: los endpoints son `def`, así que corren en el pool de hilos de AnyIO (40 por defecto). Cada cliente SSE ocupa un hilo bloqueado en `q.get(timeout=15)` (`:814`) [I].
- **Fuentes de dispositivos**: un hilo por fuente (`devices/midi.py:215`, `keyboard.py:120`).
- **Preview opcional** con `cv2.imshow` en un hilo daemon (`:835-841`). En macOS la GUI fuera del hilo principal no es fiable [I].

**Sincronización [V]**

- `RLock` global (`:154`). El tick lo mantiene durante todos los `update`/`draw` (`:405-442`), de modo que spawn y set_param esperan hasta un tick completo.
- Muchas ramas de `command` mutan sin lock: `overlay`, `output_offset`, `nudge`, `reload_scene` (reemplaza `self.scene`, `:647`), `beat`, `audio_binds`.
- `set_audio` es lock-free por reemplazo de dict (`:266-289`).
- `emit` no toma lock y usa `Queue(maxsize=200)` por suscriptor; si la cola está llena, el evento se descarta (`:322-330`, `805`).

**Memoria [V]**

- `rt.frame` mide 1920×1080×3 uint8, unos 6,2 MB, y se asigna uno nuevo por tick (`:423`).
- Hay hasta 500 eventos en memoria (`:325`), un deque de 4000 inputs (`:158`) y un log de toques de 2000 (`registry.py:145`).
- En modo métrico se copian 6,2 MB por tick (`:408`).
- Caché de `scene3d` con un escaneo: `pov` de 6,2 MB, altura float32 de 8,3 MB y grids.

## 4. Comunicación e interfaces

**Runtime HTTP (`127.0.0.1:8790`, sin autenticación ni CORS) [V]**

- `GET /api/state`: devuelve scene, effects, gallery, errors, requests, devices, audio, stats, canvas, px_per_mm, touch y projector. Serializa dos veces (`:778-780`). En cada llamada lista los puertos MIDI con mido (`devices/midi.py:62-67`, `151-155`) y parsea todos los `.md` de pedidos.
- `GET /api/schema` y `GET /api/effects`.
- `POST /api/command {cmd, args}`: responde `{ok:true, result}`. Solo `KeyError` y `ValueError` se convierten en 400 `{ok:false, error}` (`:790-796`); cualquier otra excepción es un 500 sin JSON.
- `GET /api/frame.png`: PNG del `rt.frame` actual, sin lock (`:798-801`).
- `GET /api/events` (SSE): `data: {type, t, …}`, sin campos `event:` ni `id:`. Al conectar repite los últimos 20 eventos y manda keepalive cada 15 s (`:803-820`).
- `GET /api/scene3d?nx&parts`, `/api/scene3d/texture.jpg` y archivos estáticos en `/web` (`scene3d.py:367-392`).
- Modo métrico (`registered_api.py`): `GET /api/registered`, `/api/registered/warp.bin`, `/api/registered/assets/{rel}`, `POST /api/registered/frame?scene_sha256&seconds` (PNG exacto de 1920×1080, máximo 12 MB, vida de 0 a 10 s) y `POST /api/registered/stop`.

**Los 33 comandos de `command` (`runtime.py:574-732`)**

En `SCHEMA` están documentados 29:

- `list_effects`
- `spawn{effect, target?, params?}`
- `kill{id?}`
- `set_param{id, key, value}`
- `scene`
- `find{query}`
- `request{title, body, kind, from}`
- `requests{status?}`
- `set_status{id, status, note?}`
- `reload`
- `snapshot{path?}`
- `beat{bpm?, playing?}`
- `schema`
- `devices{controls?}`
- `controls{device, type?}`
- `connect_devices{prefer}`
- `connect_keyboard{prefer, bridge?}`
- `pianohero{action, level?, song?, practice?, offset_ms?, speed?, keys?, demo?, prefer?}`
- `simulate{device?, control, value, state?}`
- `audio{features?, meta?, hold?, stop?}`
- `bind{control, target, lo?, hi?}`
- `unbind{control, target?}`
- `touch{state: down|move|up|tap, x, y | target, id?, projector?, source?, t?, velocity?, …}`
- `touches`
- `hands{hands:[{id, x, y, …}], source?}`
- `gaze{x, y | objeto | off}`
- `touchsynth{action, salida?, status?}`
- `activate_prepared{scan, prepared}`
- `registered_scene{path}`

Otros 4 existen pero **no están en `SCHEMA`**: `overlay{path, seconds? | off}`, `output_offset{dx?, dy?}`, `nudge{object, dx, dy}` y `reload_scene{scan?}`.

**Los 16 tipos de evento SSE [V]**

- `spawn{id, effect, target}`
- `kill{id}`
- `param{id, key, value}`
- `reload{file, effects}`
- `reload_error{file, error}`
- `effect_error{id, effect, error}`
- `control{device, control, ctype, value, state}`
- `touch{id, state, x, y, seq, projector, source, …}`
- `hands{ids, source}`
- `gaze{…}`
- `output_offset{dx, dy}`
- `nudge{object, dx, dy, ajuste}`
- `scene_reloaded{scan}`
- `request{id, title, kind}`
- `bind{control, target}`
- `prepared_scene_activated` (`auto_scene.py:206`)

**Las 25 tools MCP**

Las listé importando el servidor [V]. Todas llaman a `_cmd`, que antes ejecuta `_ensure()`: si el runtime no responde, lo levanta (`mcp_server.py:113-130`).

- `pg_runtime(action)`: gestión local de procesos (start, stop, restart, status). La vida del runtime se prueba con `GET /api/schema`.
- `pg_state()`: `GET /api/state`, resumido.
- `pg_scene_objects(kind?)`: comando `scene`.
- `pg_find(query)` y `pg_geometry(object_id)`: comando `find`. `pg_geometry` habla de «mm» aunque en las escenas de escaneo las unidades son px.
- `pg_list_effects`, `pg_spawn(effect, target?, params?: JSON str)`, `pg_kill(instance_id?)`, `pg_set_param(id, key, value: JSON str)`, `pg_snapshot(path?)` y `pg_beat(bpm?, playing?)`: el comando homónimo. `pg_snapshot` devuelve una **ruta, no la imagen**.
- `pg_request_feature(title, body, kind?, sender?)`: escribe el archivo localmente **y además** envía el comando `request`, así que el pedido queda **duplicado** (`:259-260`).
- `pg_requests(status?)`: lee los archivos localmente.
- `pg_cameras()`: `slscan.capture.list_cameras` local.
- `pg_crop_object(id, camera?)`: stub que llama a `find`.
- `pg_gestures()`: `/api/state`.
- `pg_touch(action, x, y, target, id, velocity, salida)`: `touches`, `touchsynth` o `touch`.
- `pg_midi(action, device, control, value, state, prefer)`: `devices`, `connect_devices` o `simulate`.
- `pg_audio(action, device, channel, features)`: proceso aparte o comando `audio`.
- `pg_controls(device, type)`, `pg_bind(control, target, lo, hi, remove)` y `pg_pianohero(…)`: el comando homónimo.
- `pg_light(...)`: BLE directo desde el proceso MCP.
- `pg_dmx` y `pg_ableton`: stubs estáticos.

**Dashboard (`:8795`) [V]**

- Rutas: `GET /`, `/ui/{app.js|style.css}`, `POST/GET /api/dale`, `POST /api/cancelar`, `POST /api/continuar`, `GET /api/dale/events` (SSE con eventos `reset`, `dale` y `status`), `GET /api/pipeline`, `GET /api/escena`, `POST /api/spawn|kill|param`, `GET|POST /api/showcase` y `GET /api/frame.png`.
- Orquestación de procesos:
  - `Popen(start_new_session)` y lectura del stdout línea a línea como JSON `{paso, …}` (`dashboard.py:96-117`).
  - Cancelación con `killpg` SIGTERM y, si a los 5 s sigue vivo, SIGKILL (`:119-135`).
  - Antes de lanzar dale, `pkill -f "slscan pipeline serve"` (`:190-192`).
  - Showcase con `subprocess.run(timeout=60)` (`:395-400`).
  - Sigue `dale.estado.json` desde un offset (`:149-173`) y crea el archivo `dale.continue` (`:314-319`).
- Las opciones de dale se validan con regex (`:195-232`).
- Quien mata los runtimes es dale, con `pkill -f` de 5 patrones (`dale.py:87-94`).

**`ventana_proyector` (`:8793`) [V]**: `GET /estado|/mostrar?path=|/gris?v=|/negro|/blanco` y `POST /imagen`.

## 5. Algoritmos y técnicas

- **Hot reload [V]** (`runtime.py:97-118`, `382-400`):
  - En cada tick hace `stat` de los ~48 archivos de efectos, lo que mide 0,23 ms.
  - Un archivo cambiado se recarga con `importlib.reload` y las instancias vivas se reconstruyen con `cls(target, p, iid)`, copiando solo `t`; el resto del estado interno se pierde.
  - Un error de import queda en `errors` y la instancia vieja sigue viva.
  - Un error en `update` o `draw` mata la instancia y emite `effect_error` (`:425-432`). Lo cubren los tests.
- **Capas**: se ordena por el param `capa` y después por número de spawn (`:364-373`).
- **Alfa por ROI** en `Canvas.polygon`: mezcla solo el bbox. Según el comentario del código, 61 teclas bajan de ~120 ms a pocos ms (`base.py:63-64`).
- **Audio**: las features se retienen `hold` segundos. Después decaen ×0,8 por tick, y el beat retenido decae ×0,75. El lag se estima por EMA (`:291-311`), y los bindings mapean `lo..hi`.
- **`output_offset`**: `warpAffine` del cuadro completo por tick cuando está activo (`:438-440`).
- **`scene3d`**:
  - Altura por paralaje (no métrica), recortada a percentiles 0,5–99,5 y con mediana 5×5.
  - `z_scale` deja el relieve en un 6 % del ancho.
  - Por objeto, p10 y p90 dentro de una máscara recortada a su bbox.
  - Grid con `INTER_AREA` de 192×108; el JSON resultante pesa menos de 2 MB (lo verifica un test).
- **Registered**: render Three.js con la cámara a partir de K y T, un post-pass con la LUT `warp` (RG float32 de w×h) y máscaras por objeto. Se envía un PNG cada ~250 ms más el tiempo de encode (`registered.js:116`), o sea unos 3–4 Hz [I].
- **`workbench`**: Canny, densidad de Sobel para el teclado, FFT para el paso entre teclas, altura + color + GrabCut para el cocodrilo, y caras de la torre por perfil de Sobel.
- **`dualview`**: `matchTemplate` con TM_CCOEFF_NORMED sobre `equalizeHist`, y descuento de la deriva de cámara por mediana.
- **`vnc`**: DES en Python puro para la autenticación RFB y encoding Raw.

## 6. Persistencia y supuestos de entorno [V]

**Archivos que escribe**

- `requests/NNN-slug.md`
- `<scan>/salida.json` (offset) y `<scan>/ajustes.json` (nudges)
- `reports/playground_snapshot.png`
- En `~/.local/state/sl-scanner/`: `playground-<port>.{pid,log}`, `audio-<port>.{pid,log}`, `dale.continue`, `dale.estado.json`, `serve.log` y `pattern.log`
- `workbench` escribe `<SCAN>/workbench.json` y `wb_*.png`; `dualview` escribe `reports/dual/`.

**Archivos que lee**

- `captures/scanN/{segments, workbench, keys, objects, mesh}.json`, `scene_pov.png`, `height_proj.npy`, `proj_to_cam.npz` y `captures/rig.json`.

**Valores por defecto inconsistentes**

- El runtime usa `captures/scan5` (`__main__.py:19`).
- `scene3d` usa `captures/scan2` (`scene3d.py:39-43`).
- `stage` busca scan5, luego 4 y luego 2.

En este checkout **no existe `captures/`**, así que todo corre con la escena demo.

**Supuestos hardcodeados**

- Rutas: `/Volumes/Externo/sl-scanner` (`runtime_loop.sh:8`, `arranque_mesa.sh:18`, `feed_loop.sh:3`), adb en `/Users/max/...` y el serial del Quest.
- IPs: `192.168.1.233` (VNC 5900, Brio 8794, puente MIDI 8796/UDP) y `192.168.4.161` (Quest).
- Monitores de trabajo en el origen (0,0) y (1920,0), proyector en (0,1080).
- Cuenta del llavero `max`, cámara `osmo` 3840×2160 rotada 270.
- Resolución 1920×1080 en todas partes; el modo métrico la exige exacta.
- Solo macOS: pygame/SDL, Quartz, AppKit (pyobjc) y `security`.

## 7. Estado real

**Funciona, con evidencia**

- Galería y spawn/draw, validación de targets, hot reload con plugin roto y archivo reemplazado entre glob y stat, cola de pedidos, API HTTP y colores RGB→BGR: `tests/test_playground.py`, 8 aprobados.
- Estáticos y `/api/scene3d` con escaneo sintético, demo y rig: `tests/test_web.py`, 5 aprobados y 1 omitido porque falta `captures/scan2`.
- Proxy del dashboard, dale falso con espera de «Continuar», SSE y deduplicación: `tests/test_dashboard.py`, 7 aprobados.
- El MCP por stdio levanta un runtime en `:8781`, lista tools y lo apaga: `tests/test_mcp_stdio.py`, 1 aprobado.

**Stubs**: `pg_dmx`, `pg_ableton` y `pg_crop_object` (la máscara figura como «pendiente»); `pg_cameras` solo enumera. `Scene.people`, `lights` y `projectors` son decorativos.

**Específico de una escena**: `scene_from_scan`, con ids `buda`, `croc`, `keys`, `ddj`, `ohm`, `torre` y `papel`; `stage.FALLBACK` (scan4); `workbench`; `dualview`; `showcase.ESCENAS`; `pianohero`. Todo está pensado para el estudio de Max.

**Contradicción de flujo [I, según el código]**: dale copia `objects.json` a la sesión.

- Si también hay `segments.json`, `scene_from_scan` toma la rama de `objects.json` y la escena queda en `world_mm`. Ahí `spawn` lanza `ValueError` (`runtime.py:335-336`) y el tick no dibuja efectos (`:425`).
- Si no hay `segments.json`, `__main__` ni siquiera carga el escaneo (`:43-45`) y queda la **demo**, con el cocodrilo falso girando.

En ambos casos el showcase final de dale no proyecta nada sobre lo real.

**Camino legado**: `arranque_mesa.sh:16` dice textualmente: «Nada … del runtime viejo :8790». La mesa con el Quest proyecta a través de `ventana_proyector`, alimentada por `superficie_feed` a ≤5 Hz.

**Modo métrico**: después de `registered_scene` o `stop`, el runtime queda en negro de forma permanente y no hay comando para salir (`registered_api.py:73-79`).

## 8. Rendimiento

Medí con la escena demo a 1920×1080, `dt=1/30` y mido sustituido [V]:

| Medición | Resultado |
|---|---|
| Init + carga de 50 clases de efectos | 64 ms |
| Tick sin efectos | 0,3 ms |
| Tick con `contour_glow` | 1,1 ms |
| Tick con `contour_glow` + `texture_grid` | 10,9 ms |
| Tick con los tres anteriores + `frame_feed` | 25,8 ms (p95 26,6) |
| Encode PNG del cuadro | 10,6 ms (633 KB, cuadro casi negro) |
| Encode JPEG q80 | 3,7 ms (190 KB) |
| `state()` serializado | 0,76 ms y 56 KB (sin pedidos ni mido) |

Con tres efectos simples ya se está cerca de los 33 ms de presupuesto de un tick a 30 Hz.

**Cuellos de botella evidentes**

- Todo corre en un solo proceso Python con GIL: tick, presentación, servidor HTTP, 50 POST/s del audio y ~30 Hz de toques [I].
- **Copias del cuadro**: lienzo nuevo por tick, `warpAffine` opcional, BGR→RGB contiguo, `make_surface` con swapaxes, blit y subida de la textura con SCALED. Son 3–4 copias completas de 6 MB por cuadro presentado (`capture.py:261-283`).
- **Vistas previas**: cada `/api/frame.png` cuesta un PNG completo, y el visor 3D lo pide a 10 Hz (`web/app.js:16`).
- **`/api/state` hace I/O**: enumera MIDI por CoreMIDI y parsea los pedidos del disco. Lo consultan el dashboard y el visor cada 2 s.
- Los métodos `update`/`draw` corren bajo el lock: la latencia de un comando es de hasta un tick completo.
- En modo métrico se copian 6 MB por tick y la entrada llega a 3–4 Hz vía PNG.
- El hot reload ejecuta `import` dentro del hilo de render, así que un import pesado congela la proyección.

## 9. Deuda técnica, bugs y riesgos

**Bugs verificados**

1. Recargar `base.py` rompe el registro de plugins. `runtime.py:42` importa `Effect` una sola vez; después de `importlib.reload(base)` (`:106`), `issubclass(nueva, Effect viejo)` da `False` y `load()` devuelve `[]` sin error. Lo reproduje en un proceso aparte. Además las clases eliminadas nunca se quitan de la galería.
2. El hilo de render puede morir sin aviso:
   - `hot_reload` reconstruye instancias sin `try` (`:390-394`).
   - `tick` no protege `animate_demo`, `_tick_audio`, overlay ni `inst.kill()`.
   - `run()` no tiene `try` (`:445-457`).

   El resultado es un cuadro congelado con la API viva; el propio `docs/PERFORMANCE-EFECTOS.md:36-37` lo advierte. Además, al reconstruir no se llama `kill()` a la instancia vieja, que se filtra si tenía recursos.
3. `pg_request_feature` crea dos archivos de pedido (`mcp_server.py:259-260`). Encima, `next_id` tiene una carrera entre procesos (`requests_queue.py:77-89`).
4. `set_param` no valida nada: un valor de tipo erróneo provoca una excepción en `draw` y **mata** el efecto (`:375-380`, `:428-432`). `kill` con un id inexistente responde como si hubiera tenido éxito.

**Riesgos [I]**

- SSE: `json.dumps(ev)` sin `default` (`:811`, `:815`). Un `float32` emitido desde un puente en el mismo proceso tumba el stream de ese cliente. Los clientes desconectados ocupan su hilo hasta 15 s, y con 40 SSE el pool queda sin hilos para los endpoints síncronos.
- `serve(window=True)` usa imshow fuera del hilo principal.
- Con `metric_mode`, si el display no mide exactamente 1920×1080 se lanza `ValueError` y se apaga todo el runtime (`:850-851`).

**Seguridad**

- No hay autenticación ni validación del Host en `:8790`, `:8795` ni `:8793` (el visor de calibración sí la tiene: `calibration_viewer.py:191-201`), y frente a DNS rebinding no hay defensa.
- Primitivas sobre archivos locales vía HTTP: `snapshot{path}` escribe en cualquier ruta, `overlay{path}` y `/mostrar?path=` leen cualquier archivo, `registered_scene{path}` y `reload_scene{scan}` también aceptan rutas arbitrarias.
- `POST /imagen` no tiene límite de tamaño.
- Hot reload equivale a ejecutar cualquier `.py` que se deje en `effects/`, en el mismo proceso, sin timeout: un bucle infinito en `draw` cuelga la proyección.
- La gestión por `pkill -f` de patrones mata cualquier proceso que coincida.
- `_ensure()` puede levantar un runtime duplicado o con la escena equivocada si `/api/schema` tarda más de 2 s.

**Diseño**

- Tres sistemas de coordenadas conviven (mm con y hacia arriba, px con y hacia abajo, `world_mm`) con nombres engañosos (`width_mm`, `bbox_mm`, `size_mm` que en realidad son px).
- Estado global de módulo: cachés de `stage` y `scene3d`, y `TouchState` en `scene.__dict__`.
- `command` es un if-chain de 160 líneas, con 4 comandos fuera del schema y un schema escrito como strings libres en vez de tipos.
- Coexisten dos caminos de salida (PatternWindow sin bordes y la ventana normal) por un problema real de firmware.

## 10. Relevancia para el producto «agente con contexto físico»

**Esencial (el concepto ya está y está probado)**:

- El ciclo plugin → escena → lienzo → proyector.
- El protocolo `{cmd, args}` con estado más SSE.
- El MCP como proxy delgado.
- El `TouchState` con `seq` para que los efectos consuman los toques sin perder ninguno.
- La política de vsync y de nunca proyectar sobre los monitores de trabajo (`display_present`/`hide`).
- El servidor de presentación mínimo de `ventana_proyector`.

**Reutilizable con cambios**:

- El contrato `Effect`, con metadatos `needs`/`targets`/`params`, muy útil para que un agente descubra capacidades.
- La API de `Canvas`.
- La cola de pedidos.
- `scene3d` como contexto 3D para el agente.
- El transporte métrico validado por SHA (`registered_api.accept_frame`).
- El patrón de orquestación del dashboard (proceso hijo con eventos JSON por stdout y archivo de «continuar»).

**Prescindible para el MVP**: `pianohero`, devices/OHM/EK-50, `pg_light`, `pg_dmx`, `pg_ableton`, `vnc`, `dualview`, `workbench`, `stage.FALLBACK`, `showcase.ESCENAS`, `scripts/arranque_mesa.sh`, `runtime_loop.sh` y `build_bundle.py` (una exportación sanitizada para compartir el repo).

**Lo que falta para que un agente opere de forma robusta**:

- Percibir imágenes: tools MCP que devuelvan el cuadro de cámara y el proyectado como contenido de imagen.
- Coordenadas consistentes con transformaciones explícitas.
- Primitivas declarativas: texto, forma, imagen o UI anclados a un objeto o región, con TTL y capas.
- Zonas táctiles con eventos.
- Errores tipados y versionado del estado (`rev`) con SSE reanudable.
- Watchdog del render y un supervisor de procesos.
- Autenticación.

## 11. Recomendaciones para la reescritura

**Conservar casi tal cual**:

- Los metadatos de `Effect` y el ciclo `update`/`draw`.
- La idea de `SCHEMA`, convertida en tipos reales.
- `TouchState` con `seq`.
- La política de pantalla de `PatternWindow` (vsync, display presente, nivel de ventana) y la ventana normal como modo seguro.
- La validación estricta de `accept_frame`.
- La captura de errores por efecto con su evento.

**Rediseñar**:

1. **Procesos.**
   - Un **render/presenter** dedicado que haga tick y present en el mismo lazo, sincronizado con vsync (como ya hace `pianohero/__main__.py:113-118`), idealmente en GPU (moderngl o wgpu) con shaders.
   - Un **control-plane** aparte (FastAPI asíncrono más MCP) que hable con el renderer por IPC: cola de comandos y snapshots de estado (ZeroMQ o un socket UDS con msgpack), y frames de vista previa en memoria compartida codificados en JPEG o WebP a demanda.
   - Los productores de alta frecuencia (audio, manos) por UDP u otro canal binario, no por HTTP JSON.
2. **Modelo de estado.**
   - Un único sistema de coordenadas canónico más transformaciones explícitas (proyector px ↔ mundo mm ↔ cámara), con unidades en los nombres.
   - Escena con `rev` monotónico; comandos idempotentes con `request_id`.
   - Eventos con `id` y reanudación (`Last-Event-ID`) y un ring buffer.
3. **Plugins.**
   - Registrar por protocolo o duck typing, no por `issubclass` contra una clase recargable.
   - Validar `params` con pydantic.
   - Construir las nuevas instancias dentro de `try`, con rollback a la instancia vieja.
   - Medir tiempo por efecto y desactivarlo si excede el presupuesto.
   - Para el código que escriba el agente, opcionalmente un sandbox en subproceso.
4. **API para el agente.**
   - Primitivas de alto nivel: `show_ui`, `highlight(object)`, `place_text`, `define_touch_zone`, `look(camera) → image` y `projected() → image`.
   - Tools MCP que devuelvan contenido de imagen; menos tools y mejor tipadas.
5. **Operación.**
   - Supervisor con pidfiles y healthchecks en lugar de `pkill -f`.
   - Token y validación del Host; rutas de archivo restringidas a una lista blanca.
   - Configuración en un archivo (puertos, displays, cámaras) en vez de constantes y rutas fijas.

**Descartar**: los caminos de escena específicos (`scene_from_scan` con ids fijos, FALLBACK), los stubs de hardware, el cliente VNC y la duplicación de visores 3D con defaults de escaneo distintos.

## Archivos leídos y tests

**Leídos completos**:

- `playground/`: `__init__.py`, `__main__.py`, `runtime.py`, `scene.py`, `stage.py`, `ventana_proyector.py`, `dashboard.py`, `mcp_server.py`, `requests_queue.py`, `skill/SKILL.md`, `workbench.py`, `showcase.py`, `dualview.py`, `vnc.py`, `effects/__init__.py`, `effects/base.py`, `README.md`.
- `playground/dashboard_ui/`: `index.html`, `app.js`, `style.css`, `README.md`.
- `playground/web/`: `__init__.py`, `registered_api.py`, `scene3d.py`, `app.js`, `index.html`, `style.css`, `registered.html`, `registered.js`, `registered-math.mjs`, `registered.css`.
- `playground/calibration_web/`: `index.html`, `app.js`.
- `docs/PERFORMANCE-EFECTOS.md` y `docs/BUNDLE.md`.
- `scripts/`: `demo.py`, `runtime_loop.sh`, `feed_loop.sh`, `arranque_mesa.sh`, `prueba_vsync.py`, `build_bundle.py`.
- `examples/demo/README.md`.

**Lectura parcial**:

- `calibration_web/style.css`: un CSS minificado de 8 KB que solo revisé por encima.
- `examples/demo/scene.json`: leí el principio y el final y validé su estructura con un script; es `demo_scene()` serializada.

**Como contexto, fuera de alcance**: `effects/contour_glow.py`, `slscan/capture.py:55-370`, `slscan/calibration_viewer.py:184-250`, `devices/midi.py:62-160`, `touchsynth/registry.py:135-185`, `auto_scene.py:161-206`, fragmentos de `dale.py` y `pianohero/__main__.py`, y los cuatro archivos de tests.

**Tests**: 21 aprobados y 1 omitido. Las mediciones de la sección 8 y la reproducción del bug de `base.py` las hice en un proceso aparte en `/tmp`, ya borrado, sin abrir dispositivos ni puertos.

| Archivo | Resultado |
|---|---|
| `test_playground.py` | 8 aprobados (3,7 s) |
| `test_web.py` | 5 aprobados y 1 omitido (falta `captures/scan2`) |
| `test_dashboard.py` | 7 aprobados |
| `test_mcp_stdio.py` | 1 aprobado (11,6 s) |

`test_playground` reescribe temporalmente `playground/effects/contour_glow.py` y lo restaura. El contenido quedó intacto, pero su mtime cambió.
