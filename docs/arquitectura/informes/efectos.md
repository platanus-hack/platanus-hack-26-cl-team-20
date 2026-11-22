# Informe: efectos proyectados, apariencia y superficies (sl-scanner b8fecff)

Leí completos todos los archivos de mi alcance y corrí los tests: 113 pasan y 4 se saltan. Además medí el costo de cada efecto con dos benchmarks propios, guardados fuera del repo. Lo que dice "verificado" lo leí en el código o lo ejecuté. Lo marcado como **[inferido]** es deducción mía.

## 1. Propósito y funcionalidades

- **Efectos** (`playground/effects/`): 46 archivos que definen 50 clases de efecto, contadas al cargarlas en el benchmark. Son plugins con hot reload que dibujan luz sobre objetos reales: halos, texturas reproyectadas, animaciones vectoriales, simulación de tela, juegos táctiles, instrumentos MIDI y HUDs.
- **Apariencia preparada offline** (`appearance_prepare.py`, `vectortrace.py`, `appearance.py`):
  - limpia la foto del escaneo (`scene_pov.png`);
  - segmenta con MobileSAM ONNX guiado por prompts;
  - vectoriza las regiones;
  - publica un catálogo `manifest.json` con hashes, que el runtime consume sin volver a segmentar.
- **Subsistema "superficie"** (`superficie*.py` + `catalogo_anim.py`): es un proceso aparte. Rectifica el plano de la mesa a un "lienzo" métrico y hace visión en tiempo real: hojas, teclado, mouse, manos, SAM más un agente VLM. Compone su propia salida al proyector. **Desde el 9‑nov es la mesa que realmente se usa**, y no pasa por los efectos del runtime (`docs/SETUP-MESA-QUEST.md:3-5`: «runtime viejo :8790 NO se usa»).

## 2. Arquitectura interna

**Núcleo**
- `effects/base.py`:
  - `Canvas` (l. 38‑81) envuelve `img` (BGR uint8), `px_per_mm`, `origin_px` y `flip_y`. Ofrece `project()` y `polygon()`, que mezcla con alfa solo dentro de la caja del polígono.
  - `Effect` (l. 84‑127) define los metadatos de clase, `update`, `draw`, `set`, `kill`, `meta` y `state`.
  - Helpers: `bgr`, `pulse` y `offset_contour`.
- El descubrimiento y orden de los efectos vive en `runtime.py` (fuera de mi alcance): `EffectRegistry` (l. 56‑118), `spawn` (l. 333), `layered` (l. 364) y `tick` (l. 404).

**Helpers por familia** (todos en `playground/`)
- `cuadros.py`: `Patch` = recorte del objeto con máscara sin los objetos que lo tapan, alfa suavizado, foto y mapas polares `rn`/`theta` normalizados al polígono. Lo usan `cuadro_portal`, `cuadro_vivo`, `marco_respira` y `parallax`.
- `ambiente.py`: `select()` (objetos por id, kind, alias o forma), `mask_small` (máscaras a baja resolución), `height01` (relieve), `palette_lut`, `to_canvas` (resize y suma) y `Pulse` (golpes de audio o de beat). Lo usan los cinco efectos de ambiente.
- `scan_geom.py`: `ScanData` cachea por mtime la foto, `height_proj.npy` y los json. Contiene detectores específicos de la escena (`buda_outline`, `papel_quad`, `ddj_layout`, `torre_faces`) y utilidades (`relief`, `texture`, `feather`, `blit` con modos add/over/max). `_memo` traga las excepciones (l. 79‑85).
- `fxdraw.py`: `Layers` = capa nítida más capa de brillo a ¼ de resolución, difuminada y sumada.
- `pixel_animation.py` / `vector_animation.py`: clases base que viven fuera de `effects/` para que no se registren solas.
- `catalogo_anim.py`: patrón de emojis más texto que corre a lo largo del contorno. Usa PIL y una caché de glifos.

**Dependencias fuera de mi alcance**
- `scene.py`, `stage.py` (formas fijas del estudio con un `FALLBACK` de scan4), `audio/features.py` y `audio/mapping.py`.
- `touchsynth/*` (`effectkit.py` con `InstrumentEffectMixin`/`SceneObjectsMixin`, `registry.touch_state`, instrumentos e `interactivo.py`), `pianohero/*`, `people/*` (DAViD).
- `slscan.contracts.digest`, `slscan.graycode`/`recon` para la preparación, `pad_checker`, `legibilidad`, `lienzo_quest`, `quest_cam`, `vision_mesa`, `teclado_grid`, `jev`, `vision_vlm`/`vision_jev` y `automatic_masks`.

**Flujo de datos en el runtime**
1. La escena tiene los objetos (contornos en px del proyector, o en mm en la demo), `audio`, `beat`, `controls`, `inputs` y `gaze`. Los toques cuelgan de `touch_state(scene)`.
2. `tick` recorre `layered()`, ordenado por `(capa, n.º de spawn)`.
3. Cada instancia llama a `update(dt, scene)` y luego a `draw(canvas)`, que muta el lienzo compartido.
4. Se aplica un `warpAffine` de `output_offset` y sale el cuadro.

**Flujo en superficie_feed**
1. Cámara (Osmo o Quest MJPEG).
2. `remap` o homografía hacia el lienzo: 2051×1156 a 0,4 mm/px.
3. Hilo de visión.
4. Composición en `rojo_loop`.
5. Homografía `H_lienzo_to_proj`, luego `remap` del lazo cerrado, luego `POST /imagen` JPEG a `ventana_proyector.py:8793`. En modo Osmo, en cambio, escribe un PNG/JPG a disco y manda el comando `overlay` al runtime.

## 3. Ejecución y concurrencia

**Runtime**
- Un hilo a `fps=30` (`runtime.py:444-456`).
- Todo `tick` corre con `self.lock` tomado (l. 405‑442): el `hot_reload` (un `stat` de cada .py en cada tick), la escena, el audio y todos los `update`/`draw`.
- Los comandos HTTP (`spawn`, `set_param`) usan el mismo lock, así que esperan a que termine el cuadro.
- La preparación pesada ocurre dentro de `update` en el primer cuadro y bloquea el render. Medido en el primer cuadro:

| Efecto | Primer cuadro |
|---|---|
| `objeto_vivo` | 200 ms |
| `scene_texture` | 152 ms (incluye sha256 del PNG) |
| `marimba` | 115 ms |
| `mascara_viva` | 52 ms |

**Qué vive en memoria (runtime)**

| Dato | Tamaño y formato |
|---|---|
| Lienzo nuevo por tick | `np.zeros` 1920×1080×3 uint8 = 6,2 MB |
| `ScanData` por escaneo | `pov` 6,2 MB + `height` float32 8,3 MB |
| Máscaras del catálogo (lru 32) | bool 1920×1080, 2 MB c/u |
| `Patch` de un ROI de 600×440 | ≈5 MB: mask, alpha f32, alpha8×3, tex8, rn, theta |
| Vectores | `rank` int32 + `fill` + `sketch` + capa acumulada, del tamaño del bbox |
| `tela_viva` | malla ≤65×65×3 f32 + texturas del ROI |
| Ambiente | rejillas a 1/4 (`pared_viva`), 1/8 (`sombras_largas`) y 1/2 (`lluvia_estrellas`) |

**superficie_feed** usa al menos 9 hilos daemon que comparten dicts sin lock (salvo `paper.lock`, `tk_lock`, `seg["lock"]` y las conditions):
- `CamReader`: lectura de la cámara.
- `loop`: warp al lienzo y codificación de MJPEG solo para los clientes que miran (o cada 0,5 s). Además lanza una búsqueda de hoja en un hilo nuevo cada 0,2 s.
- `vision_loop`, a ≤15 Hz:
  - tracker del teclado cada 0,3 s;
  - mouse en cada vuelta;
  - Hough de moledores cada 0,5 s;
  - flujo óptico Lucas‑Kanade de los segmentos SAM;
  - manos con MediaPipe;
  - alturas sobre la malla MRUK a 1 Hz;
  - CamShift de objetos.
- `rojo_loop`: composición a 1/0,15 s con la Osmo, o ≥0,2 s (≤5 Hz) con el Quest.
- `seg_loop`: SAM periódico.
- `rebase_loop`: SAM base cada 45 s más refinado con un agente (Sonnet por CLI ~20 s, o Jev ~2 s).
- `jev_hilo`, `cam2_loop` (cámara lateral del iPad) e hilos que arman el catálogo.

Memoria de superficie_feed:
- Lienzo: 7,1 MB. Contenido de salida 1900×1040: 5,9 MB. Remap del lazo: 2×f32 = 15,8 MB. Cuadro completo del Quest 1728²: 9 MB.
- Capa del catálogo: BGRA de tamaño lienzo, más mosaicos por ítem y glifos en 60 ángulos.

## 4. Comunicación e interfaces: el contrato exacto de un efecto

**Metadatos de clase**

| Campo | Qué hace |
|---|---|
| `name` | Id único en la galería |
| `label`, `description` | Texto para la galería y para la voz |
| `aliases` | Frases de voz que la secretaria busca |
| `params` | Defaults sin tipo; la instancia los mezcla con los que recibe (`base.py:96`) |
| `targets` | **Se valida** en `spawn`: el kind del objeto debe estar en `targets` o ser `"*"` (`runtime.py:345`) |
| `needs` | **Solo informativo**: fuera de `effects/` nadie lo lee, salvo `meta()` (verificado con grep) |
| `default_target` | Convención que cada efecto resuelve por su cuenta; no está en la base |
| `capa` | Param que fija el orden z |

**Ciclo de vida**
- **Constructor** `__init__(target, params, instance_id)`: `target` es un `SceneObject` o `None`.
- **`update(dt, scene)`** recibe la escena entera y mutable. Ahí cada efecto lee lo que necesita:
  - `objects` (`contour`, `bbox`, `kind`, `extra.outline`/`quad`/`keys`/`controls`/`faces`/`ajuste`/`vector_bundle`);
  - `audio` (helpers `audio_level`/`audio_mode`), `beat`, `controls`, `inputs`, `gaze`, `camera_frame`, `scan_dir`, `projector_px`, `coordinate_space`;
  - toques y manos con `touch_state(scene)`.
- **Hay efectos que escriben en la escena**: `install_scene_regions` reemplaza `scene.objects` (`appearance.py:149`) y `mirada` escribe `scene.gaze["elegido"]`.
- **`draw(canvas)`** no devuelve nada: muta `canvas.img` en el lugar. No hay alfa ni búfer por capa. Cada efecto elige su propia mezcla:
  - suma `cv2.add`, la mayoría (es la luz sobre negro);
  - reemplazo `cv2.copyTo` por máscara (texturas Minto, `tela_viva`, `mascara_viva`);
  - `addWeighted` (alfa de `Canvas.polygon`, `keys_map`);
  - `max` (títulos de `libros`);
  - el modo `over` de `scan_geom.blit`.
- **`set(key, value)`** se sobreescribe para comandos: `pianohero` (`cmd`), `touch_draw` (`clear`), `objeto_vivo` (`aletear`).
- **`state()`** entrega JSON para la API.
- **Errores**: si un efecto lanza una excepción, el runtime borra la instancia y emite `effect_error` (`runtime.py:429-432`). Muchos efectos validan "fail‑closed" con `ValueError`.
- **Hot reload**: rehace la instancia con los mismos `p` y `t` (l. 394‑395). El estado que debe sobrevivir se guarda en módulos: `pianohero` usa `ph.LIVE` (`pianohero.py:140-152`) y los instrumentos usan el registro de touchsynth.
- **Reloj**: `self.t` acumula `dt`, pero `cam_view`, `control_glow`, `pianohero` y los instrumentos usan `time.time()`. Hay relojes mezclados.

**Espacios de coordenadas**

| Espacio | Cómo se usa |
|---|---|
| Demo en mm | `flip_y=True`, `px_per_mm≈1,2` |
| "Legacy" en px del proyector | `projector_px=True`, 1 unidad = 1 px, y hacia abajo. **`table_mm` guarda el tamaño del proyector** (`appearance.py:107`, `tela_viva.py:188`) |
| `world_mm` | Calibrado (P4). **Desactiva todos los efectos**: `spawn` lanza error (`runtime.py:336`) y `tick` no itera (l. 425) |

- Los efectos de píxeles, vectores, texturas y tela **exigen** el lienzo nativo, sin transformaciones (por ejemplo `pixel_animation.py:75`).
- `stage()`, `effectkit.PROJ_SIZE` y `touch_marks` (l. 41) suponen 1920×1080.
- superficie_feed define tres espacios más: cámara (1728²), lienzo (0,4 mm/px) y contenido (1900×1040).

**Catálogo completo**

Costo = mediana de update+draw en 30 cuadros a 1920×1080 tras 5 de calentamiento, en esta Mac, con escena sintética. Entre paréntesis va el primer cuadro (preparación). "Estudio" significa atado a la mesa de Max.

| Efecto | Qué hace | Datos | Ámbito | ms/cuadro |
|---|---|---|---|---|
| contour_glow | Halo concéntrico que late (beat/audio) | contorno, beat, audio | genérico | 1,3 |
| texture_grid | Patrón según `texture` recortado al objeto | bbox, contorno | genérico, **roto en px de proyector** | 25,8 (dibuja 0 px) |
| frame_feed | Cámara o carta de ajuste rectificada a 4 esquinas | contour[:4], camera_frame | genérico | 24,7 |
| keys_map | Polígono y nombre de cada tecla; activas por MIDI | extra.keys, controls | Korg | <0,1 |
| control_glow | Pads, perillas y faders según MIDI | extra.controls, controls | OHM | ~0 |
| bass_pulse | Halo del Buda y teclas con el bajo, flash en el golpe | audio, `stage()` | estudio (FALLBACK scan4) | 2,0 |
| bass_ripple | Anillos desde platos y Circuit en cada golpe | audio/beat, `stage()` | estudio | 0,6 |
| channel_react | Parte ↔ canal de la mezcladora | audio por canal, mapping.json, `stage()` | estudio | 2,2 |
| buda_breath | Foto que respira ±1,5 %, Canny que sube, halo | foto, contorno, croc | Buda (genérico sin foto) | 1,3 (11,8) |
| cuadro_portal | Túnel, iris y estrellas con la forma del objeto; el toque abre o cierra | Patch, toques, beat | genérico | 1,1 (25) |
| cuadro_vivo | Remap ondulante de zonas lisas, luz angular, partículas | Patch, foto | genérico | 1,6 (22,8) |
| marco_respira | Banda interior con gradiente que recorre el perímetro | Patch | genérico | 0,6 (19,7) |
| parallax | Desplazamiento por profundidad hacia cabeza o mano | Patch, height, puntero | genérico | 0,9 (15,4) |
| papel_canvas | Texto, imagen o estado rectificado por homografía | extra.quad / papel_quad | "papel" (genérico con quad) | 0,6 |
| torre_faces | Patrón por cara (gradiente, ondas, scan) | torre_caras.json, relieve | torre/scan5 | 0,6 |
| ddj_skin | Platos que giran, pads secuenciador, faders | ddj_layout (foto), MIDI | DDJ | 1,1 |
| scene_texture | Foto realzada reproyectada con viñeta por objeto | apariencia, máscaras | escaneo | 1,2 (152) / 0,5 por objeto |
| scene_regions | Provider: instala objetos del catálogo; no emite luz | manifest | genérico | ~0,2 |
| lluvia_estrellas | Estrellas que chocan y encienden contornos | contornos | genérico | 2,5 |
| pared_viva | Ondas desde objetos sobre el fondo, lluvia de luz, respiración | contornos, relieve | genérico | 1,1 |
| plantas | Frente de luz que crece de la base a la punta, chispas | contornos, relieve | genérico | 0,8 |
| libros | Recorre objetos: lomo, título, índice con línea | contornos | genérico | 1,2 |
| sombras_largas | Lámpara virtual con sombras por marcha de alturas | relieve/bbox | genérico (ilumina 100 %) | 1,3 |
| touch_marks | Anillos en los dedos, luz base | touch_state | genérico | ~0 |
| toque_onda | Onda desde el toque que rebota en contornos, nota MIDI | toques, contornos | genérico | ~0 sin toques |
| touch_draw | Trazos de luz con el dedo | toques | genérico | ~0 |
| objeto_vivo / croc_alive | Celdas hexagonales, respiración, patas con rigging | contorno, toques, audio | genérico / cocodrilo | 1,0 (200) |
| objeto_synth / croc_synth | Zonas del objeto → notas MIDI | contorno, toques | genérico / cocodrilo | no medido |
| marimba | Barras en la zona libre; el golpe es una nota | escena, toques | genérico | 0,5 (115) |
| juego_memoria | Simón con objetos y HUD | contornos, toques | genérico | 0,6 |
| mirada | Foco en el punto mirado; dwell → elegido | scene.gaze | genérico | 0,6 |
| linterna / mano_luz | La mano es una luz sobre el relieve | height_proj, manos | requiere escaneo | no medido |
| pianohero | Notas en carriles de las teclas reales, juego y HUD | keys, inputs ek50 | Korg EK‑50 | 0,9 |
| cam_view | Recuadro (PIP) de una carpeta o URL JPEG | archivos/HTTP | depuración | ~0 |
| relight_people | DAViD: normales y recorte; dedos = focos | video o cámara, modelo, calib | experimental | no medido |
| leopardo_fucsia | Rosetas en el cuerpo menos las orejas | máscaras, ids de orejas fijos | Minto | 0,6 (41) |
| tigre_animado | Rayas por el eje de la oreja | máscara | Minto (genérico de hecho) | 0,2 |
| mascara_viva | Bordes, Canny y ojos detectados que parpadean | máscara, foto | máscara azteca | 2,1 (52) |
| surface_lines / contour_breathe / rabbit_outline | Ondas guiadas por la luz / borde que respira / conejo | máscara (+foto) | gen./gen./conejo | 1,8 / 0,5 / 0,6 |
| vector_drawing / vector_fill / vector_pencil | Trazo progresivo / relleno / boceto | bundle vectorial | genérico, default "cuadro" | 0,4 / 0,6 / 0,6 (8‑30) |
| vector_blobs / mask_pulse | Regiones grandes con hue y bloom al bajo | bundle | genérico / máscara | 4,7 / 3,3 |
| tela_viva | Tela PBD ≤65×65 empujada por un cuerpo oculto; UV inverso | bundle, máscara, foto | cuadro | 3,2 (36) |

## 5. Algoritmos y técnicas

Todo es CPU, NumPy y OpenCV en uint8 BGR. PIL solo se usa para texto y emojis. **No hay GPU en ninguna parte** (grep negativo; el único acierto fue "antorcha"). Técnicas recurrentes:
- **Preparar una vez y animar por LUT:** la fase se guarda en uint8 y la suma "da la vuelta sola", más `cv2.LUT` (`pared_viva`, `marco_respira`, `cuadro_portal`).
- **Trabajo a resolución baja, después `resize` y un solo `cv2.add`** (`ambiente.to_canvas`, `fxdraw.Layers`, tope de 384 px en `PixelAnimation`, 512 px en tela y blobs).
- **`cv2.remap` sobre mallas finas** para deformar texturas (ondas, parallax, UV inverso de la tela).
- Simulación PBD vectorizada con aristas disjuntas, paso fijo de 1/60 y tope de 2 subpasos (`tela_viva.py:202-211`).
- Marcha de rayos con desplazamientos enteros (`sombras_largas`).
- Distancia con etiquetas para parametrizar el perímetro por longitud de arco (`marco_respira`).
- K‑means determinista y jerarquía de contornos (`vectortrace`).
- Consenso local con kd‑tree y remuestreo con Delaunay (`appearance_prepare.clean_scan`).

Sobre las mediciones: `docs/PERFORMANCE-EFECTOS.md` solo describe el método. Sus resultados están en `reports/scene_regions/...` y en `benchmark_effects.py`, y ninguno de los dos existe en el snapshot (verificado). Las cifras de la tabla son mías. Un tick vacío (lienzo más `warpAffine`) cuesta 1,07 ms.

## 6. Persistencia

**Lee:**
- `captures/<scan>/`: `scene_pov.png`, `height_proj.npy`, `segments.json`, `workbench.json`, `keys.json`, `proj_to_cam.npz`, `osmo/map.npz`, `osmo/color.png`.
- `reports/scene_regions/<scan>/manifest.json`: schema 1.0, `coordinate_space=projector_px`, hashes de entradas y assets. La ruta se deriva como `scan/../../reports` (`appearance.py:18-20`).
- Bundles `trace.json`/`mask.png`. Fallback hardcodeado: `reports/vectortrace/<scan>/<id>/trace.json` (`vector_animation.py:151`, `tela_viva.py:177`).
- `playground/torre_caras.json` (solo scan5), `playground/audio/mapping.json`.
- superficie: `superficie.json`, `pad_quad.json`, `superficie_feed.json`, `lienzo_remap_webcam.npz`, `lazo_corr.npz`, `teclado.json`/`teclado_ref.jpg`, `cam2_calib.json`, `captures/quest-cal-fija/lienzo_quest.json`+`.npz`.

**Escribe** (superficie, en la carpeta del scan):
- `items/<fecha>/` con máscaras PNG, recortes RGBA, SVG, `catalog.json`/`catalog_refinado.json`, `agente.json`, `jev.json`;
- `lazo.json`, PNG/JPG temporales para el `overlay` del runtime, `recalibraciones.jsonl`.

Las publicaciones del catálogo son atómicas (`tmp.replace`).

**Rutas y supuestos de entorno hardcodeados:**
- Modelos SAM en `~/hardware/models/slscan-segmentation` (`superficie_feed.py:1968`).
- Fuentes `/System/Library/Fonts/Apple Color Emoji.ttc` y `Helvetica.ttc` (`catalogo_anim.py:25-26`).
- Captura de pantalla con Quartz (l. 1271) y `displayplacer`.
- En `scripts/arranque_mesa.sh:17-20`: `/Volumes/Externo/sl-scanner`, `/Users/max/...adb`, número de serie del Quest e IP `192.168.4.161`.
- Videos y calibraciones por defecto en `relight_people`.
- Solo macOS.

## 7. Estado real

- **Funciona, con tests verdes:** cuadros, ambiente, visuals v3, apariencia/vectores, texturas Minto, tela, cocodrilo vivo e instrumentos. En mi benchmark las 50 clases se registran, y 29 efectos sin escaneo más 14 con catálogo sintético corren sin errores.
- **Los 4 tests saltados** necesitan `captures/scan5`, que no está en el snapshot. Sin escaneos ni catálogos reales, todo lo atado a escena solo está validado en sintético.
- **Atado a una escena:** `bass_*`, `channel_react`, `buda_breath`, `ddj_skin`, `torre_faces`, `keys_map`, `control_glow`, `pianohero`, `croc_*`, Minto (orejas con ids fijos en `pixel_animation.py:54` y `leopardo_fucsia.py:46-50`) y `mascara_viva`.
- **Experimental o de depuración:** `relight_people`, `cam_view`, `frame_feed`, `texture_grid`, y las calibraciones Osmo de superficie (reemplazadas por las del Quest).
- **Código muerto:** `papel_canvas` en modo "estado" lee `scene.hud` (l. 83), pero nadie lo escribe (grep). `superficie_feed.py:390` tiene `if prev is not None or True`.
- **Sin ningún test:** `superficie*.py` y `catalogo_anim.py`.

## 8. Rendimiento

- Con la escena sintética casi todo cuesta 0,5–2,5 ms. Las excepciones son **`texture_grid` y `frame_feed` (~25 ms cada uno)**: reservan máscara y capa del cuadro completo y mezclan en float o con indexación booleana.
- **El cuello real son los primeros cuadros:** de 20 a 200 ms de preparación dentro del lock del render. Hay que moverla a un worker y hacer el swap de forma atómica.
- **Trabajo redundante:**
  - un lienzo nuevo de 6,2 MB por tick;
  - `stat` de todos los plugins por tick;
  - `install_scene_regions`/`texture_path` en el `update` de cada efecto, cada cuadro;
  - `Canvas.project` con bucle Python por vértice (ya existe la versión vectorizada `ambiente.project_pts`).
- **En superficie_feed:**
  - la salida es JPEG q90 por POST a ≤5 Hz, o PNG/JPG a disco más `overlay` del runtime. Eso es latencia de IPC más codificación;
  - el remap del lazo se aplica a cada cuadro en CPU;
  - SAM en CPU tarda unos 20 s.
- **Oportunidades de GPU:** casi todos los efectos ya son "consultas a textura más LUT" y pasan directo a fragment shaders: portal, `pared_viva`, `cuadro_vivo`, `parallax`, `marco_respira`, blobs/bloom, la deformación de la tela y `sombras_largas` (marcha de rayos). La cadena de salida (homografía → remap del lazo) sería **una sola pasada de shader** con el remap como textura float.

## 9. Deuda técnica, bugs y riesgos

1. **`texture_grid` no dibuja nada en escenas en px del proyector** pero cuesta ~26 ms. El rango en y queda invertido (`texture_grid.py:34-35`). Verificado: 0 px encendidos.
2. **`bass_pulse`, `bass_ripple` y `channel_react` iluminan coordenadas fijas del FALLBACK de scan4** (`stage.py:24-34`) aunque la escena no tenga esos objetos. En la escena sintética, `bass_pulse` encendió el 14 % del cuadro.
3. **Hay dos sistemas de render paralelos y excluyentes:** los efectos del runtime y la composición de superficie_feed. El `overlay` tapa los efectos (`runtime.py:433-437`). En `world_mm` no hay efectos.
4. **Coordenadas ambiguas:** `table_mm` sirve de tamaño del proyector. Hay supuestos fijos de 1920×1080 en `scan_geom`, `stage`, `effectkit` y `touch_marks`, mientras la salida real es de 1900×1040 **[inferido: los efectos no funcionarían bien si se movieran a la ventana]**.
5. `needs` no se valida y `params` no tiene tipos ni rangos. Para un agente, el esquema es pobre.
6. **Contrato de composición implícito:** cada efecto decide si suma, reemplaza o mezcla con alfa. Sin búfer por capa no se puede hacer fade ni opacidad global.
7. **Hot reload frágil en helpers:** se recargan con `importlib.reload` dentro de los plugins (`vector_trace.py`, `vector_blobs.py`, `pixel_surfaces.py`, `scene_regions.py`, `relight_people.py`). `tigre_animado`, `leopardo_fucsia` y `mascara_viva` importan `PixelAnimation` sin reload, así que pueden quedar dos clases base distintas **[inferido]**.
8. `scan_geom._memo` traga excepciones y cachea `None` para siempre en ese stamp.
9. **superficie_feed (3188 líneas, todo dentro de `main`):**
   - estado compartido sin lock entre 9 hilos;
   - `SEG_COLORS` definido dos veces (l. 1608 y 1963);
   - `rojo_loop` usa `vis` antes de que exista (lo tapa con un `except NameError`);
   - `W` y `H` globales que muta `main`;
   - API HTTP construida a mano con query strings.
10. **El texto con Hershey de cv2 no tiene tildes:** `pianohero` las quita con `ascii_` (l. 36‑37). Solo `catalogo_anim` y `render_catalogo` usan PIL.
11. Las orejas faltantes lanzan `ValueError` y matan la instancia, por diseño fail‑closed. No avisa a quien la lanzó salvo con un evento.

## 10. Relevancia para el producto «agente con contexto físico»

**Esencial o casi directo:**
- `papel_canvas` y `Papers.to_projector`/`POST /papel/pintar`: renderizar un panel (texto o imagen) en un rectángulo de 2 px/mm y llevarlo a la hoja real con una homografía. Es el primitivo "pantalla sobre papel".
- `Sticky` (poses que solo saltan con cambios grandes) y el overlay con borde difuminado.
- `cuadros.prepare`: máscara del objeto sin oclusores, feather y coordenadas polares para resaltar cualquier objeto.
- La cadena lienzo métrico → `H_lienzo_to_proj` → lazo cerrado.

**Reutilizable con cambios, como widgets de UI:**
- `marco_respira`: "este objeto", un resaltado de borde animado y barato.
- `contour_glow`: halo.
- `libros`: lista indexada, línea guía hacia el objeto, punto que viaja y texto a lo largo de un eje. Es casi un callout con flecha.
- `mirada`: dwell con anillo de progreso, o sea un botón por permanencia.
- `juego_memoria`: secuencia de pasos con toque, una guía paso a paso.
- `toque_onda` y `touch_marks`: feedback de toque.
- `fxdraw.Layers`: brillo barato.
- `catalogo_anim`: texto que sigue un contorno y caché de glifos.
- El manifest con hashes y validación fail‑closed.
- `vectortrace` para producir contornos SVG.
- El catálogo SAM con etiquetas del agente (superficie_feed), que ya es "contexto físico para un agente".

**Prescindible para el producto:** todo lo audio‑reactivo y lo atado al estudio (Buda, DDJ, torre, piano, cocodrilo, Minto, `tela_viva`), `relight_people`, `cam2`, rotación de monitor, moledores, Jev/emojis y `frame_feed`/`texture_grid`.

## 11. Recomendaciones para la reescritura

- **Conservar casi tal cual:**
  - el modelo declarativo de metadatos (`name`, `description`, `aliases`, `params`, `targets`): es un tool schema;
  - el patrón "preparar offline con hashes, consumir en runtime";
  - los algoritmos de `cuadros.prepare`, `marco_respira`, `Papers`/`Sticky`, `vectortrace` y `clean_scan`.
- **Un solo espacio canónico:** el plano de la mesa en mm (el lienzo). Las anclas serían `object_id`, `sheet_id` o un punto en mm. Una sola cadena hacia el proyector, con homografía o malla más el remap del lazo. Eliminar el modo legacy en px del proyector, el `FALLBACK` y las constantes 1920×1080.
- **Widgets en modo retenido** que el agente compone por diffs:
  - `{id, type: label|halo|outline|arrow|callout|button|progress|panel|path|image, anchor, style, z, ttl, on_touch}`;
  - cada widget tiene geometría propia en mm, lo que da hit‑testing gratis contra los toques del mismo espacio;
  - el estado de interacción (dwell, pasos) vive en el renderer;
  - esquema JSON tipado con rangos (lo que hoy falta en `params`).
- **Render en GPU, separado de la percepción:**
  - un proceso renderer a 60 Hz con shaders, por ejemplo una ventana WebGL/Canvas: los agentes generan bien HTML/SVG, y una homografía es un `matrix3d` de CSS más una pasada WebGL para el remap del lazo. Otra opción es wgpu o ModernGL;
  - la percepción (cámara, manos, SAM, VLM) va en otro proceso;
  - IPC con memoria compartida o anillo de cuadros, y un bus de eventos para toques y objetos. Nada de JPEG por POST ni PNG a disco;
  - comandos sin lock global contra el render: cola de mensajes aplicada al inicio del cuadro;
  - la preparación pesada en workers, con swap atómico.
- **Texto Unicode de verdad** (FreeType/Skia/HTML) y legibilidad para quien está del otro lado de la mesa (el `rot=180` de hoy).
- **Composición explícita por capa:** búfer RGBA premultiplicado y modo add/over por widget, más opacidad global. Ya existe un prototipo en `componer_premult` (`superficie_feed.py:232-246`).
- **Descartar** los efectos del estudio y lo audio‑reactivo para el MVP. Se pueden portar después como shaders "decorativos" si hay tiempo.

## Archivos leídos completos

**Efectos:** los 47 archivos de `playground/effects/`: `__init__.py`, `base.py` y los 45 plugins (bass_pulse, bass_ripple, buda_breath, cam_view, channel_react, contour_glow, control_glow, croc_alive, croc_synth, cuadro_portal, cuadro_vivo, ddj_skin, frame_feed, juego_memoria, keys_map, leopardo_fucsia, libros, linterna, lluvia_estrellas, mano_luz, marco_respira, marimba, mascara_viva, mirada, objeto_synth, objeto_vivo, papel_canvas, parallax, pared_viva, pianohero, pixel_surfaces, plantas, relight_people, scene_regions, scene_texture, sombras_largas, tela_viva, texture_grid, tigre_animado, toque_onda, torre_faces, touch_draw, touch_marks, vector_blobs, vector_trace).

**Helpers y subsistema:** `appearance.py`, `appearance_prepare.py`, `vector_animation.py`, `pixel_animation.py`, `vectortrace.py`, `fxdraw.py`, `catalogo_anim.py`, `scan_geom.py`, `cuadros.py`, `ambiente.py`, `superficie.py`, `superficie_aspecto.py`, `superficie_captura.py`, `superficie_feed.py` (las 3188 líneas), `superficie_manual.py`, `torre_caras.json`, y `data/emoji_tree.json` (solo estructura: árbol de 3 niveles, 9 raíces y 1914 hojas emoji con etiquetas es/en; lo usa `jev.py`).

**Docs:** los 5 pedidos.

**Contexto fuera de alcance, leído parcialmente:** `runtime.py` (registro, spawn, tick, run), `scene.py` (cabecera), `stage.py`, `touchsynth/effectkit.py`, `audio/mapping.py`, `scripts/arranque_mesa.sh` y `docs/SETUP-MESA-QUEST.md`.

**No alcanzado:** nada de mi alcance quedó sin leer.

## Tests y benchmarks

- `pytest` de los 8 archivos pedidos: **113 passed, 4 skipped en 21,4 s**. Los 4 skips dicen «sin captures/scan5» (`test_effects_ambiente.py:206`, `test_effects_cuadros.py:247` y `:273`, `test_visuals_v3.py:255`). No faltó ninguna dependencia.
- Benchmarks propios, de solo lectura y sin dispositivos (MIDI reemplazado por `FakeMidi`): `/tmp/lector_efectos_bench/bench.py` y `bench2.py`. El segundo crea un catálogo sintético en `/tmp/lector_efectos_bench/run`.
