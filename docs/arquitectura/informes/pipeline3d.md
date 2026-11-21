# Informe técnico: pipeline 3D, reconstrucción, objetos, PBR y escena registrada (sl-scanner b8fecff)

**Convención:** [V] = verificado leyendo el código o ejecutándolo; [D] = lo afirma un documento del repo pero no está en el snapshot; [I] = inferencia mía.

## 1. Propósito y funcionalidades

El alcance completo salió de una sola tanda de agentes Codex del 17-oct (`docs/BRIEF-pipeline-3d.md:1-9`). El encargo era pasar del projection mapping 2D a una escena 3D métrica con error ≤2 mm. El objetivo de 2 mm era una exigencia del brief que nadie logró en datos reales; ningún reporte lo registra como cumplido. Los paquetes son:

- **P3, `metric_recon.py`:** correspondencias Gray → nube triangulada en mm → malla por grilla con UV y textura de apariencia.
- **P5, `objects.py` y `object_*`:** objetos genéricos sobre la malla. Se detectan con RANSAC de planos y componentes conexas; opcionalmente se suman máscaras SAM y etiquetas de un modelo visual local (Qwen2.5-VL) que corre en el PC.
- **P7, `pbr*.py` y `radiometry.py`:** materiales PBR (albedo, normal, roughness, F0) y posición de las lámparas de la casa a partir de tomas con cada lámpara encendida y apagada, más un barrido RGB/brillo.
- **P4, `registered.py` y `verify_registered.py`:** escena registrada en un visor Three.js. Renderiza desde el proyector calibrado con corrección de distorsión y verifica bordes físicos proyectando blanco con contornos negros.
- **P6, `pipeline*.py` y `contracts.py`:** orquestador offline con contratos JSON Schema, caché por hash, estados y un monitor web.
- **Ruta física real, `oficio_*` y `scripts/reconstruct_oficio_preview.py`:** captura en vivo de dos hojas oficio, ajuste de un rig aproximado y reconstrucción de vista previa.
- **Simuladores para autocalibración:** `scene_simulator`, `scene_viewer`, `replay_session` y `scene_tracks`.
- **Legado:** `recon.py` (coalesce, mapa denso proyector→cámara, triangulación compatible con sltk) y `viewer.py` (HTML autocontenido con el PLY embebido).

## 2. Arquitectura interna

**Módulos y responsabilidades [V]:**

- **`contracts.py`:** 15 tipos de artefacto en el diccionario `FIELDS` (`:70-147`) más 4 contratos NPZ en `ARRAYS` (`:275-291`). `validate()` combina jsonschema con chequeos numéricos: R en SO(3), T igual a [R|t], exactamente un proyector, índices de cara válidos y reglas de `physical_verified` (`:177-247`). Las escrituras son atómicas con fsync (`:260-272`). Los NPZ no usan pickle: la metadata va como JSON en un escalar Unicode (`:334-345`).
- **`pipeline.py`:** el DAG fijo de 9 etapas está en `DEPENDENCIES` (`:21-27`). Lo ejecutan `Runner`, `Context` y `Result`. Cada etapa produce una huella con este contenido: hashes de entradas, opciones, hash de todo `slscan/*.py` y fuente del adaptador (`:96-98`, `:110-119`, `:232-234`).
- **`pipeline_adapters.py`:** adaptadores sintéticos para todas las etapas. Para datos grabados solo hay `preflight`, `decode_recorded` y `validate_rig`. Las etapas `triangulate`, `mesh`, `register_render` y `verify` usan `import_product`, que solo acepta productos precalculados en `scan/products/<etapa>/` ligados por hash (`:297-360`).
- **`pipeline_web.py` y `pipeline_ui/`:** monitor FastAPI con SSE, de solo lectura. El 3D del monitor es un canvas 2D con proyección ortográfica propia, sin Three.js (`app.js:55-73`).
- **`metric_recon.py`:** P3. Flujo: `measured_matches` → `triangulate_measured` → `grid_mesh` → `export_reconstruction`, con el envoltorio `reconstruct_recorded`.
- **`object_geometry.py`:** RANSAC, adyacencia, componentes y un rasterizador por software. `objects.py` orquesta y escribe `objects.json` junto con las máscaras PNG. `object_semantics.py` tiene un vocabulario cerrado de 11 tipos y un catálogo de 8 rutas de efecto. `object_worker.py` corre en el PC con CUDA.
- **P7:** `pbr_model.py` es el renderer inverso en CPU, `pbr.py` el proceso y la exportación, `pbr_protocol.py` la guía de captura y los gates, `pbr_radiometry.py` la respuesta RGB, `pbr_synthetic.py` el banco sintético y `radiometry.py` el plan de 36 estados.
- **`registered.py`:** construye el bundle de la escena, calcula las matrices GL desde K y el warp óptico inverso. Su comando `serve` levanta el `Runtime` de playground. `verify_registered.py` hace la verificación offline.

**Dependencias fuera del alcance [V]:** `graycode` (`Decoded`, `decode`, `load_sequence`), `rig` (`Device`, `Rig`), `metric` (`Mesh`, `write_mesh_ply`, `paper_scale`), `joint`, `selfcalib`, `calibration_viewer.Journal`, `metric_session`, `capture.Camera`, `rig_simulator` y `autocalib`. En playground: `runtime.Runtime`, `scene.scene_from_objects`, `web/registered_api.py`, `web/registered.js` y `dale.py`.

**Flujo de datos [V]:**

1. Cuadros PNG del Gray code → `Decoded`: px/py en uint16 por píxel de cámara, más `bit_contrast`.
2. Agrupación por celda del proyector (paso 2^skip_bits) → `MeasuredMatches`.
3. Con `rig.json`: triangulación filtrada → `CloudQuality` → `TexturedMesh`.
4. → `mesh.json`, `mesh.npz` y `mesh.ply`.
5. → `objects.json`: caras, bbox en mm, plano y máscara PNG en px del proyector.
6. → `scene.json` registrada → el navegador renderiza desde el proyector y hace POST del PNG al runtime.

P7 cuelga de la malla y produce `materials.json`, `lights.json` y `radiometry.json`.

**Conexión con el runtime de efectos [V]:** el runtime de efectos 2D no consume nada del 3D. Hay dos puntos de contacto:

- `scene_from_scan` prioriza `objects.json` cuando existe (`playground/scene.py:178-179`) y crea una escena con `coordinate_space="world_mm"` (`:267-293`). Con esa escena el tick no ejecuta ningún efecto (`runtime.py:425`), y los offsets y nudges quedan prohibidos.
- El comando `registered_scene` activa `metric_mode` (`runtime.py:576-587`). Desde ahí la salida es solo el último PNG que manda el navegador; si no llega ninguno, la salida queda negra (`runtime.py:406-412`, `registered_api.py:13-33`).

En la práctica, el modo 3D apaga los efectos. Lo que se proyectaba de verdad era la escena 2D de `playground.recalib`; `dale.py` lo dice explícitamente: «es lo que el runtime proyecta esta noche aunque el pipeline métrico quede bloqueado» (`dale.py:278-285`).

## 3. Ejecución y concurrencia

- **Pipeline [V]:** un proceso, un hilo, etapas en serie aunque exista el DAG (`pipeline.py:227`). La cancelación usa un `threading.Event`. Hay un lock `fcntl` exclusivo por carpeta de salida (`:197-201`), lo que lo limita a POSIX. Cada `ctx.progress()` reescribe y valida todo `pipeline.json` con fsync (`:76-80`, `:140-144`).
- **Monitor [V]:** `pipeline serve` corre como proceso uvicorn aparte. Por cada cliente relee `pipeline.json` cada 200 ms y emite un snapshot completo solo si cambió (`pipeline_web.py:36-54`).
- **Escena registrada [V]:** `registered serve` levanta un hilo con `Runtime.run` y uvicorn (`registered.py:267-278`). El navegador hace el render en GPU, codifica PNG y lo envía cada 250 ms más el tiempo de codificación, con vida de 3 s (`registered.js:116`). Eso da como máximo ~4 fps, lejos del tiempo real.
- **Worker de objetos [V]:** corre en el PC con CUDA, de forma secuencial: SAM vit_b, libera la VRAM, luego Qwen2.5-VL en fp16 (`object_worker.py:42-57`, `:71-94`). Procesa hasta 128 máscaras por corrida. El traslado del bundle al Mac es manual.

**Datos en memoria (1080×1920 cámara, 1920×1080 proyector).** Los tamaños son de lectura, salvo donde indico [D] o [I]:

| Dato | Formato | Tamaño |
|---|---|---|
| `Decoded.px` / `py` | uint16 | 4,1 MB cada uno |
| `Decoded.color` | float32 BGR | 24,9 MB |
| `bit_contrast` (18 bits) | uint8 | 37,3 MB [D] |
| 44 cuadros Gray crudos | uint8 | ~274 MB [D] |
| `MeasuredMatches` (~90 k celdas) | varios | pocos MB, más `source_match` int32 de 8,3 MB y 6 máscaras bool de 2 MB |
| Rasterizado (`object_geometry.py:141-163`) | rayos float64 + KD-tree + owner int32 + depth float64 | ~33 MB + KD-tree + 8,3 MB + 16,6 MB por dispositivo |
| Warp óptico del proyector | `ideal` float64 / `uv` float32 | 33 MB / 16,6 MB |
| Payload de `/api/registered` | JSON con listas de vértices, caras y UV | >10 MB para ~80 k vértices [I] |

## 4. Comunicación e interfaces

- **Puertos y endpoints [V]:**
  - `pipeline serve`: 8796, con `/api/pipeline`, `/api/events` (SSE), `/api/preview` y `/api/artifacts/<ruta>`, que solo sirve archivos cuyo hash coincide con el reporte actual (`pipeline_web.py:23-33`).
  - `registered serve`: 8798, con `/api/registered`, `/api/registered/warp.bin`, `/api/registered/assets/...` y `POST /api/registered/frame?scene_sha256=` (PNG de exactamente 1920×1080, máximo 12 MB).
  - `oficio_capture` habla con el runtime en `127.0.0.1:8790/api/command` (`overlay`, `output_offset`) (`oficio_capture.py:66-73`).
  - El visor de calibración está en 8797 [D].
- **Contrato común [V]:** cada artefacto lleva `schema_version="1.0"`, `artifact`, `evidence` (`synthetic`, `recorded` o `live`), `units` y `input_hashes`. Los assets se referencian como `{path, sha256}` con ruta relativa y sin traversal (`contracts.py:53-56`, `:373-395`). La convención de píxel es `opencv_integer_centers`, `X_dev = R·X_world + t` y la UV tiene origen arriba a la izquierda.
- **Debilidad del esquema [V]:** todos los esquemas de primer nivel usan `additionalProperties=True` (`:149-152`), así que los campos extra no se validan.
- **Estados [V]:** `pending`, `running`, `passed`, `failed`, `blocked` y `unverified`.
  - `blocked` = falta una dependencia o un producto, no es un fallo de proceso.
  - `unverified` = el artefacto está completo pero no acredita nada físico.
  - `physical_verified` solo puede salir de un `validation.json` que cumpla el protocolo: 3 poses reservadas, 3 repeticiones, máximo más incertidumbre ≤2 mm y cobertura 1 (`contracts.py:227-244`, `pipeline.py:300-304`).
- **Caché y reanudación [V]:** una etapa se reutiliza si la fuente y la huella coinciden, ninguna dependencia cambió y las salidas siguen íntegras por hash (`:242-247`). Cada ejecución publica en `artifacts/<etapa>/<uuid>/` como generación inmutable, renombrando desde staging (`:253-273`). Los códigos de salida del CLI son 0, 1, 2 y 130 (`:368`).
- **Quién llama a quién [V]:** `playground/dale.py` encadena barrido radiométrico → recalib 2D → `slscan pipeline run` → `slscan.registered build` (si hay malla y rig) → copia `objects.json` a la sesión → levanta el runtime en 8790 (`dale.py:288-330`).

## 5. Algoritmos y modelos

- **`recon.coalesce` [V]:** media por código usando bincount (`recon.py:31-62`). `dense_proj_to_cam` usa `griddata` lineal más inpainting TELEA (`:65-86`); lo consume la ruta 2D y no sirve como medida.
- **Triangulación [V]:** `cv2.triangulatePoints` (DLT) sobre coordenadas normalizadas sin distorsión (`metric_recon.py:210-217`). El modo `sltk` reproduce la triangulación de slcrunch: rectificación más Hartley-Sturm por pseudoinversa (`recon.py:89-124`).
- **Agrupación P3 [V]:** una celda Gray da un candidato. El centro es la mediana de los píxeles de cámara. Se rechaza el grupo si el radio supera 6 px o si no es 8-conexo. La incertidumbre se fija en σ_cam = 0,25 px y σ_proj = extensión/√12, sin dividir por √n (`:91-165`). La agrupación es un bucle Python por celda con `ndimage.label`.
- **Filtros por defecto [V]:** contraste ≥0,02; reproyección ≤1 px; ángulo ≥1°; gap entre rayos ≤2 mm; profundidad entre 50 y 5000 mm; σ ≤2 mm (`:181-188`). σ se calcula por diferencias centrales con 8 triangulaciones extra: √λmax(JΣJᵀ) (`:283-293`). Los rechazos se guardan como bitmask (`:168-177`).
- **Malla [V]:** quads de celdas vecinas del proyector, sin Delaunay ni Poisson. Se cortan aristas de más de 50 mm, saltos de más de 20 mm en cualquier dispositivo y caras vecinas a más de 60°. El winding mira a la cámara. UV = (cam+0,5)/tamaño. `texture_valid` aplica un z-test por texel (`:327-386`).
- **P5 [V]:**
  - RANSAC sobre centroides de cara: 100 hipótesis, muestra de 4096 caras, umbral `noise_mm·plane_sigma` = 1,5 mm, normal dentro de 30°, hasta 16 planos (`object_geometry.py:76-111`).
  - Aristas compartidas y bordes de color RGB con distancia ≤50 → componentes conexas.
  - El rasterizador hace un bucle por cara: consulta un KD-tree de rayos sin distorsión, aplica baricéntricas y profundidad con corrección de perspectiva (`:133-191`).
  - Asignación de máscaras SAM: cubrir ≥60% de los píxeles visibles de la cara, y gana la máscara más pequeña (`objects.py:52-64`).
  - Contornos con `findContours RETR_CCOMP` conservando islas y huecos.
- **Semántica [V]:** el prompt pide JSON con `type`, `material`, `surface`, `confidence` y `suggested_effects` (`object_worker.py:83-94`). `describe()` rechaza valores fuera del vocabulario; bajo 0,6 de confianza la etiqueta queda en `unknown` (`object_semantics.py:28-49`). Las rutas cruzan la semántica con las capacidades `mesh`, `normals`, `planar` y `textured` (`:52-71`).
- **P7 [V]:**
  - `isolate`: encendido menos la media de las bases antes y después, rechazando deriva y saturación (`pbr_protocol.py:73-86`).
  - `locate_light`: profundidad sobre el rayo del emisor más potencia RGB sobre parches mate de referencia. `least_squares` con soft_l1 y 4 inicios; descarta la luz si el condicionamiento supera 1e5 o σ/d >10% (`pbr_model.py:85-135`).
  - `fit_texels`: 7 parámetros por texel (albedo RGB, 2 pendientes de normal, roughness, F0) con BRDF Lambert más GGX/Smith/Schlick, 2 inicializaciones, bucle Python por texel (`:138-188`).
  - Respuesta del proyector `y = b + A·p^γ` validada con mezclas reservadas; compensación con `lsq_linear` (`pbr_radiometry.py`).
  - Temperatura de color McCamy, solo con color calibrado.
- **P4 [V]:** matriz de proyección GL asimétrica desde K, con cambio de base B = diag(1,−1,−1) (`registered.py:30-45`). El warp inverso por píxel exige ida y vuelta <0,01 px y un overscan acotado (`:48-82`).
- **Verificación [V]:** perfiles a lo largo de normales prefijadas. El borde físico sale del gradiente de la foto blanca y la línea proyectada del oscurecimiento. Ambos se intersectan rayo–plano y se mide la distancia bidireccional entre polilíneas. Traslación, similitud, afín y homografía se calculan solo como diagnóstico (`verify_registered.py:51-157`).
- **Ajuste oficio [V]:** `least_squares` sobre focal de cámara, focal del proyector, cy del proyector, R/t del par y las poses de las dos hojas. Distorsión cero, punto principal fijo y priors en log-focales (`oficio_fit.py:20-108`).
- **`scene_tracks` [V]:** SIFT con 8000 features, ratio mutuo 0,75 y matriz fundamental por RANSAC, más un ajuste afín local de códigos Gray (`scene_tracks.py:10-77`). **No es tracking de objetos.** Son tracks de features entre 2–3 posiciones estáticas de un rig rígido, para autocalibración (`autocalib.py:208-211`). En todo el alcance no hay tracking de objetos en movimiento ni medida de latencia de objetos.

**Precisión medida:**

- **Solo sintético [D]:** hoja carta reconstruida con error <1e-6 mm (`P3-MALLA.md:144-146`); proyección JS contra OpenCV con 9,38e-13 px (`P4-ESCENA.md:133-135`); P7 con luces a 0,073%, normales a 0,10° y roughness con MAE 0,028 (`P7-PBR.md:166-176`). En P7 el generador usa el mismo modelo que el solver, así que esas cifras no miden robustez.
- **Física [D]:** rig oficio con baseline 335 mm. Error máximo de lados sobre las propias hojas usadas en el ajuste: 26,09 y 4,00 mm. Las variantes de ponderación dieron entre 31 y 129 mm. Resultado: 112.513 puntos y 84.240 caras (`P2-OFICIO-REAL.md:11-38`).

## 6. Persistencia

- **Formatos [V]:** JSON de contratos, NPZ comprimido sin pickle, PLY binario little-endian (nube con XYZ+RGB; malla con XYZ, normal, s/t y RGB), máscaras PNG 0/255 y mapas PBR en PNG de 16 bits (albedo sRGB, el resto lineal) (`pbr.py:90-99`).
- **Particularidad [V]:** `appearance.png` es un hardlink a `color.png`; si las rutas están en sistemas de archivos distintos, falla (`metric_recon.py:500-505`).
- **Rutas y supuestos hardcodeados [V]:**
  - Cámara por defecto `"osmo"` y proyector 1920×1080 (`pipeline.py:130`); rig por defecto `<scan>/rig.json` y salida `reports/pipeline/<scan>` (`:128`, `:348`).
  - Overlay del runtime en `127.0.0.1:8790` (`oficio_capture.py:67`); hojas de 216×330 mm, ArUco 2 y 1 con un corrimiento de +8 px hecho a mano (`:25`, `:31`, `:49`); `Camera('osmo', size='3840x2160', rotate=270, downscale=2)` (`:90`).
  - `rotate 270` y filtros relajados en el script de reconstrucción (`scripts/reconstruct_oficio_preview.py:25-27`, `:37-38`).
  - Three.js 0.160.0 desde CDN (`viewer.py:102`).
  - `fcntl` y hardlinks: supone macOS o Linux.
- **Datos ausentes [V]:** este snapshot no trae `captures/` ni los reportes `p2-oficio-live-2122`, `p3`, `p4`, `p5` o `p7`. `reports/` solo tiene 23 archivos de first-look. Ningún resultado físico es reproducible desde aquí.

## 7. Estado real

**Funciona con datos físicos:**

- [D] Decode Gray de scan5: 208.937 correspondencias válidas, 567 fuera del proyector, 2,78 s (`PIPELINE.md:160-164`).
- [D] Una única reconstrucción real, la del oficio del 17-oct, con su rig aproximado de errores de 4 a 26 mm sobre sus propias referencias. Nunca se verificó físicamente.
- [V] Esa reconstrucción no pasa por el pipeline P6 ni por P4. `oficio_fit` guarda el rig sin `pixel_convention`, y `Rig.save` lo escribe entonces como `slscan-rig/1` (`rig.py:110-115`, `oficio_fit.py:148-151`), formato que `contracts.read_json(..,'rig')` rechaza. Por eso se publicó con `oficio_preview.py` en el visor de calibración.

**Solo sintético [V+D]:**

- P4 completo (incluido el render en Chrome headless).
- P5 geométrico. SAM y el VLM nunca se ejecutaron (`P5-OBJETOS.md:161-164`).
- P7 completo. Ningún código produce un `pbr/session.json` real ni un `frames.npz` lineal en atlas UV (grep sin productores fuera de `pbr_synthetic`).
- Los simuladores de sala.

**Cascarón [V]:** con un scan real, el pipeline solo hace `preflight` y `decode`, y se bloquea en `validate_rig`. Aunque haya rig, `triangulate`, `mesh` y `register_render` exigen productos importados: P3 no está conectado al Runner (`pipeline_adapters.py:358-360`; `P3-MALLA.md:67-72`).

**Legado o abandonado [V]:** el modo `sltk` de `triangulate_pair`, `viewer.py` (HTML con PLY embebido) y `dense_proj_to_cam` (todavía lo usa la ruta 2D).

## 8. Rendimiento

**Microbenchmark propio [V]:** script en `/tmp/slscan_bench/bench3d.py`, fuera del repo. Rig sintético parecido al real (baseline 335 mm, plano a ~2 m), 90.180 celdas de 4 px:

| Rutina | Tiempo | Resultado |
|---|---|---|
| `triangulate_measured` | 0,67 s | — |
| `grid_mesh` | 0,20 s | 155.062 caras |
| RANSAC + componentes | 0,23 s | 96 grupos |
| `rasterize` (1 dispositivo, 1920×1080) | **5,9 s** | — |

El rasterizado es el cuello de botella: un bucle Python por cara. P5 con SAM rasteriza cámara y proyector, y `sample_camera_to_uv` lo vuelve a hacer.

**Hallazgo [V]:** con los filtros por defecto se rechazan los 90.180 puntos por `UNCERTAINTY`. La σ mediana de correspondencia es 5,6 mm, por encima de `max_sigma_mm = 2`. La cuantización de 4 px con esta geometría no da 2 mm. Por eso el script del oficio relajó σ a 120 mm, el gap a 40 mm y la reproyección a 2,5 px.

**Otros datos:**

- [D] Decode más agrupación de scan5: 6,0 s (`P3-MALLA.md:162`).
- [I] `fit_texels` hace 2 `least_squares` por texel, en Python: el sintético de 96 texels tarda segundos. Un atlas de 1024² tardaría horas.
- [V] `test_replay_session` tarda 86 s por el raycaster sintético.
- [V] El transporte P4 llega a unos 4 fps en PNG.
- [D] La latencia cámara–proyector medida: un solo ensayo válido de 1337 ms.
- [V] La huella de caché incluye el hash de todo `slscan/*.py`, así que cualquier edición invalida todas las etapas. `_intact` vuelve a hashear todas las salidas en cada corrida.

## 9. Deuda técnica, bugs y riesgos

1. **[V, bug] `register_scene` rechaza cualquier rig físico.** Exige `pose_estimated=True` en todos los dispositivos (`registered.py:112-115`), pero todos los productores marcan `False` en el dispositivo que define el mundo (`rig.py:123`, `autocalib.py:269`). P4 solo puede funcionar en sintético.
2. **[V] El rig del oficio no cumple el contrato v2** (punto 7).
3. **[V] Los filtros P3 por defecto son físicamente inalcanzables** para esta cámara y proyector (§8).
4. **[V] P3 y P4 no están conectados al Runner.** El modo grabado es casi solo validación de hashes.
5. **[V] El modo métrico apaga los efectos** (`runtime.py:425`). No existe composición de efectos en 3D.
6. **[V] Bucles Python por cara y por texel;** etapas en serie; reescritura completa del reporte con fsync en cada `progress`; jsonschema construye el validador en cada llamada.
7. **[V] `/api/preview` solo manda caras si hay ≤5000 vértices** (`pipeline_web.py:116-120`): con una malla real, el monitor muestra solo puntos.
8. **[V] Los IDs de objeto son el hash de la malla más las caras** (`objects.py:146`). No hay identidad entre escaneos ni tracking.
9. **[V] La verificación exige controles prefijados a mano** (`samples_px`, `normals_px`, plano local) y ningún código los genera.
10. **[V] Menores:**
    - Mezcla de BGR y RGB entre `m.color` y `color_rgb`.
    - `except BaseException: raise` sin efecto (`registered.py:163-165`).
    - `skip_bits` por defecto 2 en el pipeline y 0 en `reconstruct`.
    - `Context.artifact` busca por nombre de archivo y puede ser ambiguo.
    - Estilo de una línea muy denso en `oficio_*` y `scene_*`.
    - Mucha política de dominio escrita en strings y mensajes.
11. **[I] Riesgo de diseño:** la arquitectura está optimizada para auditar precisión (fail-closed, procedencia), no para producto. Mucho texto defensivo en el código y poca funcionalidad conectada de punta a punta.

## 10. Relevancia para el producto «agente con contexto físico»

**Lo honesto:** para una demo de 5 minutos con proyector, cámara y agente sobre una mesa, casi todo este alcance sobra. Nada de esto corre en tiempo real, el 3D físico tiene errores de centímetros y el runtime no compone efectos en modo 3D.

**Esencial o reutilizable:**

- **Decode Gray y agrupación** (`metric_recon.measured_matches`). Da el mapa cámara↔proyector, que es la base para traducir lo que ve la cámara a píxeles del proyector. En una mesa plana basta una homografía y el Gray code la puede afinar.
- **Modelo de objeto de `objects.json`:** id, etiqueta y semántica, máscara en px del proyector, regiones con huecos, bbox en mm, plano y capacidades. Es casi exactamente el «contexto físico» que conviene exponerle al agente.
- **Patrón SAM y modelo visual por máscara**, con vocabulario cerrado y validación de la respuesta. Conviene reemplazar Qwen local por Claude con visión sobre el recorte. Ojo: enviar fotos de la escena fuera de la máquina es una decisión de privacidad que en el repo original estaba restringida.
- **`registered.camera_matrices` y `optical_warp`.** Son correctos y probados: sirven para renderizar desde el proyector calibrado sobre superficies no planas si se calibra un rig.
- **`verify_registered.extract_paired_controls`.** Idea útil para que el agente se autoverifique: proyectar un contorno, fotografiarlo y medir el desfase.

**Prescindible:** P7 completo (PBR, lámparas, radiometría), el pipeline con generaciones y hashes, el monitor, el oficio, los simuladores, `viewer.py`, el modo `sltk` y `scene_tracks`.

**Lo que suma puntos técnicos con poco riesgo:** escaneo Gray de la mesa en unos 30 s; RANSAC del plano de la mesa más objetos por altura (si hay rig) o por SAM; objetos con máscara en px del proyector entregados al agente; y la verificación automática de alineación.

## 11. Recomendaciones para la reescritura

- **Conservar** (portando y simplificando): decode y agrupación Gray, triangulación con filtros parametrizables (calibrados según la σ real), `grid_mesh`, `plane_fit` y RANSAC, el contrato de objeto y las matemáticas de K→GL y del warp.
- **Rediseñar:**
  - **Contratos:** pocos tipos (pydantic o msgspec) con un campo `evidence`, sin hash de todo el código fuente, y con caché por contenido solo donde haya cómputo caro.
  - **Proceso de escena:** un proceso dueño de la escena en memoria (cámara, mapa, objetos). Los frames por memoria compartida o un ring buffer, no PNG por HTTP. El agente recibe una API pequeña por WS/MCP con eventos (objeto entra o sale, toque).
  - **Rasterizado:** a GPU (moderngl o wgpu), o z-buffer con `cv2.fillPoly` vectorizado.
  - **Render del proyector:** dentro del runtime, no en un navegador con un POST cada 250 ms.
  - **Tracking en línea:** de máscaras 2D (SAM2, FastSAM o YOLO-seg) mapeadas al proyector. Hoy no existe.
- **Descartar:** P7/PBR, el bloqueo dogmático por 2 mm (dejarlo como métrica), `import_product` y las generaciones inmutables, el monitor SSE de archivos, los simuladores y el oficio.
- **Si se reutiliza código de P4,** corregir primero el bug de `pose_estimated` y el formato del rig.

---

## Archivos leídos y tests

**Archivos leídos completos:** los 26 de `slscan/` del alcance (`recon.py`, `metric_recon.py`, `pipeline.py`, `pipeline_adapters.py`, `pipeline_web.py`, `contracts.py`, `objects.py`, `object_geometry.py`, `object_semantics.py`, `object_worker.py`, `pbr.py`, `pbr_model.py`, `pbr_protocol.py`, `pbr_radiometry.py`, `pbr_synthetic.py`, `radiometry.py`, `registered.py`, `verify_registered.py`, `scene_simulator.py`, `scene_tracks.py`, `scene_viewer.py`, `viewer.py`, `oficio_capture.py`, `oficio_fit.py`, `oficio_preview.py`, `replay_session.py`), los 3 de `pipeline_ui/` (`index.html`, `style.css`, `app.js`), `scripts/reconstruct_oficio_preview.py` y los 7 documentos (`BRIEF-pipeline-3d.md`, `ANALISIS-pipeline-3d.md`, `PIPELINE.md`, `P3-MALLA.md`, `P4-ESCENA.md`, `P5-OBJETOS.md`, `P7-PBR.md`).

Fuera del alcance revisé solo los puntos de integración: `playground/runtime.py` (líneas 138-171, 400-445 y 570-650), `playground/scene.py` (170-185 y 265-293), `playground/web/registered_api.py` completo, `playground/dale.py` (270-340), `slscan/rig.py` (100-135), `slscan/autocalib.py` (255-280), `docs/P2-OFICIO-REAL.md` (1-45) y un grep de `playground/web/registered.js`. No quedó nada del alcance sin leer.

**Tests** (`.venv`, `-q -x`): 111 passed y 1 skipped.

| Test | Resultado |
|---|---|
| `test_recon` | 3 passed |
| `test_metric_recon` | 15 passed |
| `test_metric_recon_sample_color` | 1 passed |
| `test_pipeline` | 26 passed (17 s) |
| `test_objects` | 23 passed |
| `test_pbr` | 20 passed (9 s) |
| `test_radiometry` | 2 passed |
| `test_registered` | 15 passed |
| `test_replay_session` | 4 passed (87 s) |
| `test_oficio` | 2 passed |
| `test_registered_browser` | skip (requiere Chrome y `SLSCAN_THREE_VENDOR` con Three.js 0.160.0 local, `tests/test_registered_browser.py:29-32`) |

No faltó ninguna dependencia. No toqué el repo ni ningún dispositivo.
