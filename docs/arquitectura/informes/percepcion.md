# Informe técnico: percepción y fuentes de cámara (`sl-scanner`, snapshot b8fecff)

Alcance: visión, segmentación, escena automática, manos, toque, personas, teclas y fuentes de cámara.
Evidencia citada como `ruta:línea`. «Verificado» = leído o ejecutado; «inferido» = deducido del código sin ejecutar.

## 1. Propósito y funcionalidades

El área tiene cinco subsistemas que se pueden separar:

- **Manos y mesa táctil** (`playground/hands/`, `windows/hands_service.py`). Una webcam de pared (Logitech Brio 100, conectada al PC Windows) da MediaPipe 21 landmarks por mano. Por WebSocket llegan al Mac, que convierte la punta del índice a px del proyector y a mm, y la transforma en toques `down/move/up` para el runtime.
- **Visión sobre el «lienzo»** (`vision_mesa.py`, `teclado_grid.py`). El lienzo es el plano de la mesa rectificado. El servicio `superficie_feed` (fuera de alcance) lo genera desde la Osmo o el Quest. Sobre él corren gestos y manos, el seguimiento de objetos chicos (CamShift), un mouse y un teclado modelados, y la tecla bajo cada dedo.
- **Escena automática** (`auto_scene.py`, `automatic_masks.py`, `grounded_labels.py`, `docs/ESCENA-AUTOMATICA.md`). Corre offline: toma `scene_pov.png`, pasa por MobileSAM y Grounding DINO, y produce objetos con máscara, nombre y efecto. Después hace una activación atómica en el runtime.
- **Agentes de visión que nombran objetos**. `vision_vlm.py` usa Sonnet a través del CLI `claude`. `vision_jev.py` y `jev.py` usan **Jev**: es el «modelo de decisión» de TypeSafe (`typesafe-ai/jev-1.14.0`), remoto, por el Vercel AI Gateway, y **no es un modelo local**. `jev.py` además elige emoji, ruta de efecto e intensidad por etiqueta.
- **Experimentos y herramientas de una escena concreta**:
  - DAViD para reiluminar personas (`people/`).
  - Detección tecla por tecla de un Korg de 61 teclas (`keys/`).
  - Ajuste del teclado ANSI (`teclado_*`) y del mousepad (`pad_*`).
  - Cocodrilo y Buda (`croc_*`, `align_object`, `segment_pov`, `firstlook`).
  - Cabeza 3D para anamorfosis (`cabeza.py`, `pozo.py`).
  - Profundidad monocular (`depth_mono.py`).
  - Legibilidad (`legibilidad.py`).
  - Cámara del Quest (`quest_cam.py`).

## 2. Arquitectura interna y flujo de datos

**Manos (PC → Mac → runtime)**

- `windows/hands_service.py` es autocontenido; solo depende de cv2, numpy, websockets y mediapipe.
  - `Capture`: hilo que guarda el último cuadro (`:274-356`).
  - `MediaPipeTasks`, `MediaPipeLegacy`, `FakeDetector` y `NoDetector` (`:143-248`).
  - `HandTracker`: IDs por vecino más cercano de la muñeca y pinch con histéresis (`:88-127`).
  - `Hub`: último JSON y difusión a los clientes (`:361-381`).
  - `Service`: hilos de captura, detección, WS y HTTP (`:384-586`).
- `hands/client.HandsClient` (hilo propio) guarda el último `HandsFrame` y alimenta `touch.TouchTracker` con la `calib.Calibration`. `bridge.TouchBridge` hace `POST /api/command {"cmd":"touch"}` al runtime en `:8790`.
- `model.py` define `Hand`, `HandsFrame`, `palm_size` y la mano sintética. La plantilla está **duplicada** en `hands_service.py:203-208`.
- `hands/__main__.py` es la CLI (`status`, `watch`, `calib-gray`, `calib-dots`, `calib-points`, `rest`, `run`) y por defecto apunta al PC 192.168.1.233 (`:25`).
- Dependencias fuera de alcance: `slscan.graycode`, `slscan.recon.coalesce` (`calib.py:164-170`), `slscan.rig.Rig` (`:218`), `slscan.capture.PatternWindow/capture_sequence` (`:284`) y el comando `touch` de `playground/runtime.py`.

**Lienzo (dentro del proceso `superficie_feed`)**

- El feed instancia `vm.Hands` (`superficie_feed.py:1675`), `vm.ObjectTracker` (`:1604`), `vm.KeyboardTracker` (`:1656`) y `vm.MouseTracker` (`:1707`) en un hilo de visión (`:1577`).
- El feed también llama a `vision_jev` (`:2421`), `vision_vlm` (`:2441`) y `jev` (`:59`) para el «catálogo refinado».
- `quest_cam.MjpegCam` es su fuente de cámara (`:958`).
- Las semillas de objetos salen de la última escena automática (`objects.json`), llevada del proyector al lienzo con `inv(H_lienzo_to_proj)` (`superficie_feed.py:1581-1600`).
- `teclado_grid.fit` arma la grilla ANSI (`teclado_grid.py:61-84`). `key_at` da la tecla bajo un punto (`:98-104`).

**Escena automática**

`auto_scene.prepare` (`:127-158`) sigue estos pasos:

1. `appearance_prepare.clean_scan` (fuera de alcance), o una copia de `scene_pov.png`.
2. `MobileSAM` (fuera de alcance) y `automatic_masks` con una grilla de 24×14 puntos.
3. `detect_labels`: subproceso `grounded_labels.py` con el Python del sistema.
4. `detected_proposals`: una caja → una máscara SAM.
5. `select_objects`.
6. `build_prepared`, que llama a `publish_catalog` (fuera de alcance).
7. Se escribe `prepared.json`.

`activate_runtime` corre dentro del runtime con el comando `activate_prepared` (`runtime.py:630-631`). Toca directamente sus internos: `runtime.lock`, `registry`, `instances`, `_n`, `frame`, `overlay_until` (`auto_scene.py:176-207`).

**Personas**

`live.PeopleLayer` levanta un proceso hijo con `spawn`. El hijo corre `david.David` (CoreML) y `hands_mp.Hands`, y publica por `mp.Queue`. Lo consume `effects/relight_people.py` con `relight.*` (`effects/relight_people.py:14-15`).

**Agentes de visión**

- `vision_vlm.describir` → subproceso `claude`.
- `vision_jev.describir` → `jev.decide` (HTTPS) + catálogo SAM base (`catalog.json` en disco).
- `jev.consultar_catalogo` → `decidir_objeto` (cascada de emoji + ruta + intensidad) con caché en disco por etiqueta. Depende opcionalmente de `slscan.object_semantics` (`jev.py:487`).
- `jev_prueba` y `vision_jev_bench` son CLIs de prueba y benchmark.

**Herramientas que proyectan y miden**

`keys/refine`, `keys/quad`, `croc_*`, `align_object`, `verify_outline`, `pad_*`, `teclado_manual`, `legibilidad` y `pozo` siguen el mismo patrón:

1. Escriben un PNG en disco.
2. Llaman a `POST /api/command {"cmd":"overlay","args":{"path":…}}` en `:8790`.
3. Esperan de 1 a 2 s.
4. Leen la Osmo con `slscan.capture.Camera` o el feed por HTTP.
5. Pasan de cámara a proyector con la LUT `proj_to_cam.npz`.

Si el runtime no está, abren una `PatternWindow` propia.

**Teclas (`keys/`)**

- `__main__` elige el método: `bordes` (`detect.py`), `modelo` (`fit.py`, ECC) o `auto`. El rectángulo de entrada sale de `workbench.json`, `segments.json` o se estima.
- `verify.py` dibuja las imágenes de verificación y proyección.
- `quad.py` saca el cuadrilátero blanco por aristas y mide el corrimiento global por correlación de fase.
- `refine.py` afina tecla por tecla con capturas.

## 3. Ejecución y concurrencia

| Componente | Procesos e hilos | Frecuencia | En memoria |
|---|---|---|---|
| `hands_service` (PC) | 1 proceso, 4 hilos: captura (bloquea en `cap.read`), detección, servidor WS de websockets sync (+1 hilo receptor por cliente, `:449-465`), HTTP `ThreadingHTTPServer` | captura a 30 fps; detección con tope `--hz 30` y sleep del resto (`:431-433`) | último BGR 1920×1080 (~6,2 MB) + reducción a 960×540 (~1,5 MB) por detección; último JSON (~1–2 KB con 2 manos) |
| `HandsClient` (Mac) | 1 hilo, `recv(timeout=0.25)`, ping cada 2 s | lo que mande el PC | último `HandsFrame`; deques de 60 llegadas y 20 pongs |
| `run_loop` + `TouchTracker` | hilo del llamador; `client.wait(0.3)` | por mensaje | estado por mano (`_HandState`), deque de 300 tamaños de palma |
| `TouchBridge` | 1 hilo, `Queue(256)`; si se llena bota `move`, nunca `down`/`up` (`bridge.py:92-102`) | por evento | — |
| `PeopleLayer` | proceso hijo `spawn` + hilo bomba; `Queue(maxsize=2)` que descarta el más viejo (`live.py:145-156`); se apaga tras 15 s sin consumidores (`:218-221`) | 8–12 fps en vivo (README) | por paquete: cuadro 640×360 BGR (0,7 MB) + normal f16 (1,4 MB) + alfa f16 (0,46 MB) [+ profundidad f32 0,9 MB], **todo pickleado** por la cola en cada cuadro (~25–35 MB/s) |
| `vision_mesa` | dentro del hilo de visión del feed | por cuadro del lienzo; manos a escala 0,5 | lienzo ~1920×1126; plantillas y histogramas |
| `cabeza.py` | 1 hilo de lazo + HTTP en 127.0.0.1:8803 | 8 Hz por defecto, sondea `/raw_hd.jpg` | último debug JPEG |
| `pozo.py` | 1 lazo | 10 Hz; re-render si el ojo se movió > 3 mm | PNG 1920×1080 en disco por render |
| `MjpegCam` | 1 hilo, `Condition`; `read()` bloquea hasta un cuadro nuevo | 864×864 a ~12,7 fps (tope de la app, `superficie_feed.py:27`) | último BGR + JPEG |
| `keys/refine` | `Grabber` por cámara (hilo que lee sin parar para vaciar el búfer de AVFoundation, `refine.py:295-338`) | 1 captura por segundo (`settle`) | mapas `mapx/mapy/valid` recortados a la ROI |
| `auto_scene` | proceso CLI + subproceso DINO (timeout 240 s) | offline | **todas** las máscaras bool a resolución completa de los candidatos que pasan el umbral (hasta 336 × ~2 MB) antes de deduplicar (`automatic_masks.py:30-36`) |
| `vision_jev` | `ThreadPoolExecutor(8)` para la 2.ª ronda y los recortes (`vision_jev.py:317`) | por pedido | imágenes JPEG en base64 |

`HandsClient.status()` ordena deques que otro hilo está modificando (`client.py:123-126`), sin lock. Es una carrera posible: `RuntimeError: deque mutated during iteration` (inferido).

## 4. Comunicación e interfaces

- **PC → Mac, manos (WS `:8793`)**. Mensaje `{"type":"hands","t","seq","size","fps","det_ms","detector","hands":[{id,handed,score,landmarks[21][3],tip_index,pinch,pinch_ratio}]}`, con landmarks en px de la Brio; `z` es la de MediaPipe, relativa a la muñeca (`hands_service.py:11-17`).
  - El cliente manda `{"type":"ping","t0"}` y recibe un pong con `t` del PC. El desfase de relojes sale de la muestra con menor ida y vuelta, como NTP (`client.py:144-149`, `:110-114`). Con eso se mide la latencia captura en el PC → llegada al Mac.
  - Política «el más nuevo gana»: `Hub.wait` y cada cliente manda solo el último (`:378-381`, `:468-473`).
  - Reconexión con espera de 0,5 a 5 s (`client.py:194-196`).
- **PC → Mac, cuadros (HTTP `:8794`)**. Rutas `/health`, `/frame.jpg?q&w`, `/frame.png?fresh=N&gray=1` (espera N cuadros nuevos, para calibrar) y `/hands.json`, con cabeceras `X-Frame-Seq` y `X-Frame-Time` (`:526-550`).
  - Acceso: solo LAN, tailnet 100.64/10 o `--allow`, **sin autenticación** (`:69-78`).
  - `camserve.py` ocupa el mismo puerto pero **ignora `fresh` y `gray`** (`camserve.py:95-107`).
  - DirectShow no comparte la cámara: solo uno de los dos servicios puede abrir la Brio (README `:52-55`).
- **Mac → runtime**. `POST http://127.0.0.1:8790/api/command` con `touch`, `overlay`, `nudge`, `reload_scene`, `output_offset` o `activate_prepared`. También se usan `GET /api/state` y `/api/schema`.
  - Formato de `touch`: `{"id","state","x","y","space":"proj","projector","mm","hand","handed","trigger","t","source","reason?"}` (`bridge.py:5-16`, `touch.py:126-132`).
  - `OverlayWindow.estado` detecta si el comando existe mandando un overlay a un archivo inexistente (`keys/refine.py:253-274`).
- **Quest → Mac**. App `com.smartup.proyector` en `:8787`.
  - `/cam.mjpg?w&fps&q`: MJPEG con `Content-Length` por parte, 864² por decimación.
  - `/cam_full.jpg`: 1728² en luma, con `X-Ts-Ns`, ~0,2 s por USB.
  - `/estado.json` para comprobar si volvió el USB.
  - Transporte por `adb forward` (USB) o Wi-Fi en 192.168.4.161, con respaldo automático (`quest_cam.py:8-15`, `:40-139`).
- **Feed (`superficie_feed`)**: puerto por defecto 8791 (`superficie_feed.py:887`), pero `cabeza`, `pozo` y `teclado_manual` suponen **8798**. Rutas usadas: `/raw_hd.jpg`, `/raw.jpg`, `/limpio.mjpg`, `/papel.json`, `/rojo?on=`, `/status`, `/teclado/recargar`. `teclado_manual` trae su propio lector de partes MJPEG (`teclado_manual.py:212-238`).
- **`cabeza.py` → `pozo.py`**: sondeo HTTP de `/cabeza.json` en `:8803`, con `{ojo_mm, mirada, ok, fuente, hz…}`; también `/debug.jpg` y `/status`.
- **Jev**: HTTPS `POST https://ai-gateway.vercel.sh/v4/ai/decision-model` con `stateParts` (JSON + imagen JPEG en base64 de 280–1568 px por lado) y preguntas `noul|choice|score|box` (`jev.py:260-318`). Hay además `/evaluation-model` para texto (`:47`) y una ruta directa a `api.typesafe.ai/v1/systemone` (`:48`). Un solo `httpx.Client` keep-alive (`:156-161`).
- **Sonnet**: `subprocess` con `claude -p … --allowedTools Read --max-turns 3`. El modelo lee la imagen del disco (`vision_vlm.py:41-43`).
- **DINO**: subproceso cuya salida JSON va por stdout (`auto_scene.py:84-95`).
- **DAViD**: `multiprocessing` `spawn`, una `Queue` de resultados y otra de control (`live.py:175-181`).
- **Choques de puertos**: `8793` es el WS de manos en el PC y también la «ventana del proyector» en el Mac (`quest_cam.py:248`, `superficie_feed.py:30`).

## 5. Algoritmos y modelos (entrada → modelo → salida)

| Pipeline | Entrada | Modelo o algoritmo | Salida | Dónde corre | Latencia (evidencia) | ¿Vivo a 30 fps? |
|---|---|---|---|---|---|---|
| Manos de la Brio | BGR 1080p MJPG reducido a 960 px | MediaPipe HandLandmarker float16, modo VIDEO, `num_hands=2`, conf. 0,5 (`hands_service.py:146-154`); respaldo `mp.solutions.hands` | 21 landmarks en px, mano, pinch | PC, CPU | estimado 15–25 ms; **nunca medido con la Brio** (README `:179-183`) | sí, plausible |
| Toque | landmarks | homografía, EMA 0,5, zona muerta de 3 px, pinch 0,30/0,42 o «apoyo» por razón de palma 1,06/1,12, 2 cuadros, mano perdida 0,3 s (`touch.py:92-108`, `:179-256`) | `TouchEvent` en px del proyector y mm | Mac | µs | sí |
| Manos del lienzo | lienzo × 0,5 | MediaPipe **GestureRecognizer** + One Euro (min_cutoff 1,2, beta 0,02) | landmarks, gesto, toque por pinch 0,22/0,28 o reposo (punta quieta < 6 px durante > 0,45 s con `Pointing_Up`/`None`), malla 2.5D, `tip_proj` | Mac, CPU | sin medir | sí; la cámara del Quest da ~12,7 fps |
| Objetos chicos | lienzo × 0,5 | CamShift sobre histograma HS + `matchTemplate` de rescate (> 0,55) | caja, centro, mm | Mac | barato | sí |
| Mouse | lienzo | máscara blanca (V > 222, S < 20, textura baja) + `minAreaRect` filtrado por las medidas de un G203 (116,6 × 62,2 mm) | centro, dirección, zonas de clic | Mac | barato | sí |
| Teclado | lienzo × 0,25 | `matchTemplate` + ECC euclidiano (`vision_mesa.py:356-390`) | cuadrilátero → grilla ANSI | Mac | barato | sí |
| Personas | BGR 640–960 | DAViD ViT-B/16, normal, recorte y profundidad, ONNX MIT → CoreML fp16 (`david.py:39-42`, `:82-105`); multitarea ViT-L opcional | normal, alfa y profundidad relativa | Mac, Neural Engine | normal a 384 px: 40–57 ms; normal + recorte: 100–160 ms; las tres: 190–245 ms; en vivo 8–12 fps; ORT en CPU ~1 s (README `:27-47`, medido el 17-nov). RTX 4070: solo estimado | solo la normal (~25 fps) |
| Reiluminación | normal, alfa, profundidad, manos | Lambert envolvente + Blinn, cono, caída, a media resolución (`relight.py:81-131`) | imagen reiluminada / luz para el proyector | Mac, numpy | ~25 ms a 960×540 con 2 focos (`relight.py:13-14`) | no a 30 fps junto con DAViD |
| Escena automática | `scene_pov` 1920×1080 | MobileSAM ONNX con grilla de 24×14 = 336 decodificaciones (score ≥ 0,72, estabilidad ≥ 0,82, IoU de deduplicación 0,85) + Grounding DINO tiny en CPU con 3 hilos (umbral 0,30/0,22) + SAM por caja (≥ 0,7) | ≤ 10 objetos disjuntos con máscara PNG, nombre «tipo N» e ID por hash | Mac, CPU | sin medir; DINO tiene timeout de 240 s | **no**, offline |
| Jev con imagen | lienzo ≤ 960 px | 1 llamada con 24 clases × (noul + box), 2.ª ronda tapando lo ya aceptado, recortes SAM sin explicar (≤ 8 en paralelo) y ajuste de la caja a la unión de máscaras SAM | `{objetos:[{nombre,caja,centro,confianza}]}` | remoto | ~200 ms por llamada de texto (docstring); imagen sin dato; IoU contra Sonnet 0,83 → 0,87 con el ajuste SAM (`vision_jev.py:26`); precisión 73 → 76 % con el umbral de la 2.ª ronda (`:224`) | no; eventual |
| Jev con texto | etiqueta + tamaño en mm + posición + z + manos cerca | 3 llamadas Choice (grupo → subgrupo → emoji) sobre 1.914 emoji; ruta e intensidad en la 1.ª (`jev.py:391-440`) | emoji, ruta de efecto, intensidad 0..1 | remoto, con caché por etiqueta | ~200 ms por llamada (docstring) | no |
| Sonnet | lienzo JPG | Claude Sonnet por CLI | mismo contrato | remoto | timeout de 90 s, «cada ~30 s» | no |
| Teclas del Korg | `scene_pov` + altura | rectificación iterativa por punto de fuga, plegado por octava y FFT contra una plantilla de 2+3 negras, bordes con ajuste gaussiano de 3 puntos y proyectiva 1D (`detect.py:7-28`, `:383-399`); alternativa ECC sobre una plantilla con 81 arranques (`fit.py:128-143`) | 61 polígonos con nota MIDI, estado y confianza | Mac | segundos | no |
| Cuadrilátero del teclado | `scene_pov` | máscara HSV + rectas Huber + subpíxel por perfiles de gradiente (`quad.py:65-180`) | 4 esquinas → 61 teclas por homografía | Mac | segundos | no |
| Afinado de teclas | proyector + Osmo + Brio | barrido proyectado ±12 px y por tecla, IoU contra una silueta de reflectancia W−D (`refine.py:474-581`) | desplazamiento por tecla | Mac | 1 captura/s, varios minutos | no |
| Cabeza 3D | `/raw_hd.jpg` de la Osmo | FaceLandmarker (iris 468/473) + profundidad por distancia interpupilar (63 mm) corregida por giro; respaldos PoseLandmarker y mancha de piel; pose de la mesa por `solvePnP` IPPE con K tomada del FOV (`cabeza.py:104-119`, `:219-241`) | ojo en mm del marco de la mesa, mirada | Mac | 8 Hz | no |
| Pozo (anamorfosis) | ojo en mm + hoja | proyección de vértices 3D al plano z = 0 por el rayo ojo→punto (`pozo.py:137-141`) | imagen en lienzo → proyector | Mac | `render_ms` se imprime en `--dry`; sin dato guardado | 10 Hz por overlay |
| Segmentación heurística | `scene_pov` + mapa | umbrales HSV, Sobel, FFT, HoughCircles, GrabCut por objeto con nombre fijo (`segment_pov.py:63-214`) | `segments.json`, `height_proj.npy` | Mac | segundos | no |
| Profundidad | foto de la Osmo | Depth Anything V2 Metric Indoor (small/base/large) en MPS | `depth_m.npy`, PLY | Mac, MPS | `infer_s` se escribe en el reporte; no hay reporte en el repo | no |
| Cocodrilo / alineación | Osmo con luz blanca | silueta por tono HSV; barrido de desplazamientos con IoU o fracción de bordes (`croc_refine.py:60-79`, `align_object.py:78-87`) | `nudge` en el runtime | Mac | ~1,1–1,5 s por desplazamiento | no |
| Verificación de calibración | foto de la Osmo | ArUco 4×4 en grilla de 8×5 + ajuste de traslación, similitud, afín y homografía (`verify_outline.py:62-98`) | residuo por marcador y por objeto | Mac | una foto | no |
| Mousepad | `osmo/color.png` + gray code | máscara roja, RANSAC secuencial de rectas, homografía cámara→proyector por RANSAC dentro del pad, proporción por metrología de una vista (`pad_checker.py:82-223`) | `pad_quad.json`, damero pre-deformado | Mac | segundos | no |

**Mapeo de coordenadas (tres sistemas que conviven)**

- **Brio → proyector**: una sola homografía del plano de la mesa. Se obtiene por gray code + `coalesce` + RANSAC de 4 px (error esperado < 2 px), por puntos proyectados o por 4 puntos a mano (`calib.py:161-179`). La captura gray code tarda ~40 s; los puntos, ~10 s (README `:88-100`).
- **Proyector → mm**: homografía desde la hoja carta (`mm_from_quad`) o cortando rayos del rig con el plano z = 0 (`mm_from_rig`, `:212-234`).
- **Osmo**: LUT densa `proj_to_cam.npz` con `mapx`, `mapy` y `valid` por píxel del proyector. Se usa con `cv2.remap` (`refine.py:126-144`, `croc_resegment.py:94-95`). La deriva de la cámara se compensa con `matchTemplate` sobre una zona estática (`croc_resegment.py:29-36`).
- **Lienzo**: `H_cam_to_lienzo` sale de un damero, y `H_lienzo_to_proj` + `lazo_corr.npz` es un remap en lazo cerrado. Los mm salen de `mm_por_px`.
- **Mesa 3D (solo `cabeza.py`)**: pose del plano por `solvePnP` con las esquinas del lienzo y K aproximada por FOV.
- La escena automática y las teclas viven en px del proyector. `vision_mesa` vive en px del lienzo y entrega además `tip_proj`.
- **Lo que no se modela**: la paralaje de objetos con altura. Es una sola homografía de la mesa; el propio código lo reconoce (`teclado_manual.py:49-51`, people README `:71-73`).

**Detección de toque (cómo decide que un dedo toca)**

- `hands/touch.py`:
  - **pinch**: pulgar–índice / palma < 0,30, se suelta a > 0,42.
  - **apoyo**: tamaño de palma (puntos rígidos) llevado al plano de la mesa contra una referencia apoyada; baja si la razón < 1,06 y sube si > 1,12, con 2 cuadros de antirrebote. La referencia sale de `rest` o se aprende sola (percentil 10 de las últimas 300 muestras).
  - **auto**: cualquiera de los dos.
  - `z_weight` permite sumar la z de MediaPipe, pero vale 0 por defecto.
  - Un toque que empezaría dentro de una zona excluida (polígonos de objetos) no se emite; un arrastre que empezó en la mesa sigue aunque cruce un objeto.
- `vision_mesa.Hands`: pinch con otros umbrales, o **reposo** (dwell).
- **No hay** altura medida, sombra, multicámara ni profundidad. El README admite que el toque «rozando contra apoyado» no se distingue (`README.md:184-188`).

**Seguimiento temporal**

- `hands_service`: IDs por vecino más cercano de la muñeca, con salto máximo del 20 % de la diagonal, penalización si cambia la mano y vida de 0,5 s.
- `touch`: EMA y zona muerta. Los IDs de toque son `t{n}` y nacen en cada `down`.
- `vision_mesa`: One Euro por mano, pero la clave es `f"{handed}{i}"`, que depende del orden en que MediaPipe devuelve las manos.
- `hands_mp`: EMA 0,5 por mano.
- `people/live`: EMA 0,35 de la normal y el alfa entre cuadros; el alfa se recalcula cada 3 cuadros.
- `cabeza`: EMA 0,35 que se reinicia ante saltos de más de 400 mm.
- `MouseTracker`: α 0,4.
- `KeyboardTracker`: promedio 0,5.
- `firstlook`: trackers CSRT de OpenCV.
- **No hay Kalman ni un tracker multiobjeto genérico**, y los objetos de la escena son estáticos.

**Cómo se arma la escena automática**

1. Apariencia limpia (`clean_scan`) o `scene_pov.png`; máscara de soporte `supported.png`.
2. `automatic_masks`: grilla de 24×14 puntos sobre la zona válida; por punto, una decodificación de SAM; filtros por score, estabilidad y área (700 px a 80 % del cuadro); deduplicación por IoU > 0,85 sobre miniaturas de 240×135 (`automatic_masks.py:8-39`).
3. `grounded_labels` en un subproceso offline: vocabulario de ~35 frases, cajas con su tipo y resolución de ambigüedad entre etiquetas anidadas con score parecido (`grounded_labels.py:24-42`).
4. Cada caja de DINO es solo un prompt: la forma final sale de SAM (`auto_scene.py:67-81`).
5. `select_objects`: primero los conocidos (fondos al final), después los desconocidos por área; máscaras disjuntas; cada una debe conservar ≥ 35 % de su área original; máximo 10 (`:47-64`).
6. ID = `obj_` + sha256(hash de la escena + ruta del scan + máscara empaquetada)[:16]; nombre «tipo N»; efecto preestablecido por tipo (`effect_for`, `:38-44`).
7. `prepared.json` + `objects.json` + vistas previas, sin tocar el catálogo activo.
8. Activación: valida hashes, rutas, scan actual, tamaño nativo del proyector y ausencia de overlay. Bajo `runtime.lock` construye la `Scene` nueva y las instancias de efecto, las dibuja una vez y cambia todo de golpe. Si falla, restaura el manifiesto. Es idempotente por firma (`:161-207`).

## 6. Persistencia y supuestos de entorno

**Archivos que se escriben**

- `captures/brio_calib.json` (`hands-calib/1`), `captures/brio_scan/brio/*.png` + `map.npz`.
- Por escaneo: `<scan>/keys.json`, `keys_refine.json`, `keys_quad.json`, `keys_antes_*.json`, `ajustes.json`, `segments.json`, `workbench.json`, `height_proj.npy`, `pad_quad.json`, `pad_checker.png`, `teclado.json`, `teclado_ref.jpg`, `ajustes_objetos.json`, `hand_rig.json`, `jev_cache.json`, `refine_*.json`, `align_*.json`, `verify_outline.json`, `corrimiento.json`, `legibilidad/`.
- Del catálogo: `items/<fecha>/jev.json`.
- Escena automática: `reports/scene_regions/<scan>/auto-*/{prepared.json,objects.json,masks/…}`.
- Herramientas de calibración: `reports/_crit`, `reports/_keys_refine`, borradas al terminar salvo `--guardar`.
- DAViD: `<task>-<size>.mlpackage` junto a los ONNX.
- Escrituras atómicas con `.tmp` + `os.replace` en `jev.Cache`, `teclado_manual` y `pozo`; el resto escribe directo.

**Rutas fijas**

- `/Volumes/Externo/{hf-cache/david, models/mediapipe, cache/jev-ref, venvs/david}`.
- `~/hardware/models/slscan-segmentation`.
- `/Users/max/Desktop/coding_projects/remotion-live/.env`: **lee secretos de otro proyecto** (`jev.py:51`).
- `/Users/max/Library/Android/sdk/...adb` (`quest_cam.py:162`).
- `C:\sl-scanner\models`.

**Red**: PC en 192.168.1.233, usuario «Roberto Mardones»; Quest en 192.168.4.161 con serial `2G0YC5ZGCM043L`.

**Supuestos de hardware**

- Proyector de 1920×1080 fijo (`W, H = 1920, 1080` en casi todos los archivos).
- Osmo a `3840x2160`, `rotate=270`, `downscale=2`, `hfov` de 84°.
- Brio por DirectShow en el índice 0.
- Escenas `scan2`/`scan5`/`dale-20261102-1511`.
- La persona sentada con rotación de 180°.
- Monitor del Mac para las ventanitas de ajuste.

**Entornos Python fragmentados**: hay al menos cuatro.

- `.venv` del Mac: **no trae mediapipe, onnxruntime ni torch** (verificado), pero sí coremltools 9.0, websockets 16.1.1 y httpx 0.28.1.
- `/Volumes/Externo/venvs/david`.
- Python 3.14 del sistema con torch, para DINO y Depth Anything.
- El venv del PC con mediapipe 1.1.1 instalado con `--no-deps`.

## 7. Estado real

**Funciona con evidencia**

- Tests sin hardware en verde (ver el final).
- El servicio de manos en `--fake` corrió en el PC el 16-oct (README `:179-181`).
- DAViD tiene mediciones en el M4 (README `:27-41`).
- La escena automática está integrada en el runtime (`runtime.py:630`).
- `superficie_feed` importa `vision_mesa`, `vision_jev`, `jev`, `quest_cam` y `teclado_grid`, así que es código vivo.

**Sin probar con hardware**: la Brio con MediaPipe, el toque «apoyo» y la luz del proyector sobre la mano (README `:179-190`). La salida a proyector de DAViD también está «sin probar con proyector ni persona real» (people README `:71-73`). Las cifras de la RTX 4070 son estimaciones.

**Específico de una escena**

- Todo `keys/` (Korg EK-50, 61 teclas): ~2.540 líneas.
- `segment_pov` (Buda, torre, papel, DDJ, OHM, Circuit, cocodrilo, con HSV fijos).
- `croc_*`, `align_object`, `verify_outline` (nombres fijos en `:49`), `firstlook`.
- `teclado_*` (teclado NIBIO ANSI 104, rotado 180°), `pad_*` (borde rojo del mousepad), `MouseTracker` (G203).

**Experimentos**: `legibilidad`, `pozo`, `cabeza`, `depth_mono`, `people/*`, la cascada de emoji de Jev y `vision_jev_bench`.

**Roto**: `firstlook.py` usa `os` sin importarlo (`:23`). Verificado: `NameError` al importar.

## 8. Rendimiento

- **Copias e IPC de cuadros**: `PeopleLayer` picklea ~2,5–3,5 MB por cuadro a través de `mp.Queue`. La fuente `url:` abre **una conexión HTTP nueva por cuadro** (`live.py:73-75`).
- **IPC por disco**: las herramientas que proyectan escriben un PNG de 1920×1080 y el runtime lo vuelve a leer. `pozo` lo hace a 10 Hz (`pozo.py:344-346`).
- **Escrituras en el lazo de visión**: `HandRig.observe` reescribe `hand_rig.json` en **cada** cuadro con la palma abierta una vez que acumuló 15 muestras. El recorte a 30 mantiene el contador sobre 15 (`vision_mesa.py:71-76`).
- **GIL**: DAViD se movió a otro proceso porque en un hilo el runtime caía de ~25 a ~17 fps (`live.py:4-5`). Todo `vision_mesa` corre en hilos dentro del feed y compite con su render y su codificación MJPEG.
- **Serialización**: los landmarks viajan como JSON con floats redondeados; con 2 manos son ~1–2 KB por mensaje a 30 Hz, despreciable. `Hub.publish` serializa una vez y reparte la misma cadena a todos los clientes.
- **Escena automática**: 336 llamadas secuenciales al decodificador de SAM en CPU, con máscaras completas en memoria. `activate_runtime` instancia y dibuja todos los efectos **mientras tiene `runtime.lock`**, así que congela el render durante esa ventana (`auto_scene.py:176-198`).
- **Latencias de cámara**:
  - Overlay → cámara ~0,27 s, y la Osmo tarda ~0,5 s en ajustar la exposición (`refine.py:343-344`).
  - El Quest entrega como máximo 12,7 fps a 864².
  - La latencia del AirPlay se mide con `quest_cam medir --latencia`; no hay resultados en el repo.
- **Lo que sí está bien hecho**: hilos de captura que guardan solo el último cuadro, detección con tope de frecuencia, difusión «el más nuevo gana», la cola del bridge que bota `move` y nunca `down`/`up`, y la reducción a 960 px antes de MediaPipe.

## 9. Deuda técnica, bugs y riesgos

1. **Etiquetado por subcadena roto** en `grounded_labels.py:67-68`. «mouse pad» da `{mouse, mousepad}` y «tablet» da `{table, tablet}`, así que ambos quedan como `unknown`. Verificado ejecutando la lógica.
2. `firstlook.py:23`: falta `import os` (verificado).
3. `camserve.py:51-52` fija FOURCC **después** del tamaño; el propio servicio advierte que así la Brio cae a ~5 fps en YUY2 (`hands_service.py:298`). Además ignora `fresh` y `gray`, así que una calibración gray code contra camserve leería cuadros viejos (inferido).
4. `vision_jev.main` declara `--ajustar-sam` como `store_true`, con valor por defecto False. Eso anula el valor por defecto True de `describir` (`:383-385`). Además `describir` modifica el global `jv.MODELO_VISION` (`:282-283`), lo que afecta a todo el proceso y no es seguro entre hilos.
5. **Dos implementaciones incompatibles de pinch**: 0,30/0,42 sobre la palma promedio (`hands_service.py:91`, `touch.py:95-96`) contra 0,22/0,28 sobre muñeca → nudillo medio (`vision_mesa.py:150-153`). También hay tres reglas de mano izquierda/derecha distintas: invertida en el servicio (`hands_service.py:424-425`), sin corregir en `vision_mesa` y en `hands_mp`.
6. `vision_mesa.widths_px:88`: `a == 0 or b == 17 and a == 0` es una condición redundante (precedencia de operadores).
7. **IDs inestables en `vision_mesa`**: la clave `f"{handed}{i}"` cambia si cambia el orden de detección, y entonces el estado del One Euro y del toque pasa a la otra mano (inferido).
8. **El toque es físicamente débil**: una sola cámara, sin altura. Los tres criterios son «apoyo» por tamaño de palma (~2 % por cada 3 cm, «cerca del ruido»), reposo o pinch. **No hay sombra, multicámara ni profundidad.** La fusión prometida está en `touchsynth/fusion.py`, fuera de alcance. La referencia aprendida sola (percentil 10) da toques falsos si la mano nunca se apoya.
9. **Seguridad**: los servicios de LAN no tienen autenticación; `jev.py` lee la llave del `.env` de otro repo; `vision_vlm` usa la sesión personal del CLI `claude`; `cabeza.py` escucha solo en 127.0.0.1 (correcto).
10. **Acoplamiento**: `auto_scene` usa atributos privados del runtime. Las calibraciones dependen de un JSON por escena con claves que cambian según el archivo (`workbench.json` contra `segments.json`).
11. **Código muerto**: `legibilidad.py` (`SIZES`, `CONTRASTS`, `max_to_pad`, `random`, `seed` sin usar) y `align_object.py:27` (importa `camera_drift` y no lo usa; por eso no compensa la deriva de la cámara). `RemoteCamera` está duplicada (`hands/calib.py:239` y `slscan/capture.py:518`). `json.dump(seg, open(...))` deja el archivo sin cerrar (`segment_pov.py:215`).
12. **Documentación inconsistente**: people README `:9` dice que la luz cuesta ~6 ms y `relight.py:13-14` dice ~25 ms. El docstring de `superficie_feed` dice puerto 8791 y los consumidores usan 8798.
13. **Memoria**: `automatic_masks` acumula máscaras completas antes de deduplicar; con muchas propuestas válidas puede llegar a cientos de MB (inferido).
14. **Puertos superpuestos** entre máquinas (`8793`), y el rango 8787–8799 concentra Quest, runtime, ventana, manos y feed.

## 10. Relevancia para el producto «agente con contexto físico»

**Esencial y reutilizable casi tal cual**

- `hands/calib.py`: modelo de calibración limpio y testeado (homografía de la mesa + métrica + tres métodos de obtención).
- `hands_service.py`: patrón de servicio de percepción con captura «el más nuevo gana», detector intercambiable, modo fake, `/health`, sincronización de relojes y filtro de LAN.
- `touch.py` + `bridge.py`: máquina de estados de toque, zonas de exclusión y esquema `TouchEvent`.
- El contrato `{objetos:[{nombre,caja,centro,confianza}]}` de `vision_vlm`/`vision_jev`: es exactamente la entrada de mundo de un agente.
- El patrón «preparar → activar atómico» con IDs por hash y procedencia de `auto_scene`.
- `MjpegCam`: lector robusto con respaldo.

**Reutilizable con cambios**

- Los trackers de `vision_mesa`, quitando lo del G203 y Max.
- `cabeza.py`, para interfaces con perspectiva corregida.
- `verify_outline`: medir el residuo de la calibración con ArUco y clasificarlo en traslación, similitud, afín u homografía es una idea generalizable.
- `automatic_masks` y la parte de Grounding DINO (corrigiendo el etiquetado por subcadena).
- `teclado_grid.key_at`, como ejemplo de un «objeto con layout conocido».
- `jev.describir_item`: una descripción textual de cada objeto (tamaño en mm, posición en la mesa, capa z, manos cerca) es útil como estado para un agente, con cualquier modelo.

**Prescindible para el producto**: `keys/*`, `teclado_manual`, `teclado_fit`, `pad_*`, `legibilidad`, `croc_*`, `align_object`, `segment_pov`, `firstlook`, `pozo`, `people/*` (vistoso, no central), `depth_mono` y la cascada de emoji y rutas de Jev.

## 11. Recomendaciones para la reescritura

1. **Un servicio de percepción con un contrato de eventos versionado**, desacoplado del render:
   - Temas: `frame.meta`, `hands` (landmarks en px de cámara, proyector y mm, con ID estable), `touch` (`down/move/up` con `trigger` y `confidence`), `objects` (ID, etiqueta, polígono y máscara en px del proyector, caja, z, confianza, versión de escena), `scene.snapshot`.
   - Transporte: WebSocket o ZeroMQ entre máquinas; **memoria compartida (anillo de cuadros) dentro de la misma máquina**, en vez de pickle o PNG en disco. Mantener el desfase por ping/pong y la marca de tiempo de captura en cada evento.
2. **Un solo modelo de coordenadas**: marco «mesa mm» canónico + px del proyector + un registro de cámaras. Cada cámara declara su transformación a la mesa (homografía o LUT densa). Eliminar la triple convivencia de proyector, lienzo y `scene_pov`.
3. **Toque**: tratar pinch y reposo como gestos explícitos y separar «contacto con la mesa». Para el contacto: una segunda vista calibrada (triangular la punta), la sombra del dedo bajo el proyector o profundidad. Un solo módulo de manos, con umbrales configurables y One Euro.
4. **Separar vivo y eventual**:
   - Vivo a 30 fps: manos, toque y trackers baratos.
   - Por evento, cuando la escena cambia: segmentación (SAM/SAM2 en la GPU del PC), etiquetado (DINO, OWL o un VLM) y nombres por Claude con visión por API (no por CLI). Para cada objeto, usar seguimiento por propagación de máscara en lugar de re-segmentar.
5. **API de salida para el agente**: capas o superficies con nombre en lugar de «escribir PNG + `overlay`». Las herramientas de calibración también las usarían.
6. **Configuración y secretos**: un archivo de configuración con hosts, puertos, cámaras y rutas de modelos; secretos solo desde el entorno; un solo entorno Python por máquina con las dependencias declaradas.
7. **Corregir antes de portar**: los bugs del §9 (1–7) y la escritura de `hand_rig.json` en el lazo. Dejar `activate` fuera del lock del render: construir afuera y cambiar una referencia.
8. **Descartar** las herramientas de una sola escena (teclas del Korg, cocodrilo, Buda, mousepad rojo, legibilidad, pozo) y conservar solo sus ideas generales: barrido proyectado con IoU para afinar un objeto, verificación con ArUco y modelo de layout conocido + homografía.

---

## Archivos leídos completos (46 de 46)

- `playground/hands/`: `README.md`, `__init__.py`, `__main__.py`, `model.py`, `client.py`, `calib.py`, `touch.py`, `bridge.py`.
- `playground/people/`: `README.md`, `__init__.py`, `__main__.py`, `david.py`, `live.py`, `hands_mp.py`, `relight.py`.
- `playground/keys/`: `__init__.py`, `__main__.py`, `detect.py` (1.028 líneas), `fit.py`, `quad.py`, `refine.py`, `verify.py`.
- `playground/`: `vision_jev.py`, `vision_jev_bench.py`, `jev.py`, `jev_prueba.py`, `vision_mesa.py`, `vision_vlm.py`, `automatic_masks.py`, `auto_scene.py`, `grounded_labels.py`, `segment_pov.py`, `depth_mono.py`, `croc_refine.py`, `croc_resegment.py`, `align_object.py`, `verify_outline.py`, `legibilidad.py`, `teclado_fit.py`, `teclado_grid.py`, `teclado_manual.py`, `pad_adjust.py`, `pad_checker.py`, `cabeza.py`, `pozo.py`, `firstlook.py`, `quest_cam.py`.
- `windows/`: `hands_service.py`, `camserve.py`.
- `docs/ESCENA-AUTOMATICA.md`.

No quedó ninguno sin leer. Fuera de alcance solo hice búsquedas puntuales en `playground/superficie_feed.py` (encabezado, rutas HTTP, puerto y uso de `vision_mesa`), `playground/runtime.py` (`activate_prepared`), `playground/effects/relight_people.py` (imports) y `slscan/capture.py` (`RemoteCamera`).

## Resultado de los tests

Ejecutados con `.venv/bin/python -m pytest tests/<archivo> -q -x`, después de `EXIT 0` en el log de instalación:

| Archivo | Resultado | Tiempo |
|---|---|---|
| `test_hands` | 24 passed | 2,8 s |
| `test_people` | 9 passed | 0,5 s |
| `test_keys` | 11 passed, 1 skipped | 11,4 s |
| `test_keys_refine` | 7 passed | 14,3 s |
| `test_auto_scene` | 12 passed | 0,7 s |

- El test omitido es `test_keys.py:241` (`test_escaneo_real`), porque no existe `captures/scan2`; el repo no trae `captures/`.
- Los tests no ejercitan MediaPipe, DAViD ni SAM reales: el `.venv` no tiene mediapipe, onnxruntime ni torch, y usan fakes o datos sintéticos.
  - `test_hands`: cliente contra un WS falso, homografías, gray code sintético, lógica de toque, puente y el servicio en modo fake de punta a punta.
  - `test_people`: solo `relight` y `prepare`/`unpad`.
  - `test_keys` y `test_keys_refine`: teclados sintéticos.
  - `test_auto_scene`: modelos simulados.
- Verificaciones extra ejecutadas:
  - `import playground.firstlook` da `NameError: name 'os' is not defined`.
  - La lógica de `grounded_labels` deja «mouse pad» y «tablet» como `unknown`.
