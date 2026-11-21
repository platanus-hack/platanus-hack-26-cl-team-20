# Informe técnico: calibración cámara-proyector, captura de patrones y flujo `dale` (sl-scanner)

Lo que dice «verificado» lo leí en el código o lo ejecuté. Lo que dice «inferido» es estimación mía. No abrí cámara, proyector ni puertos.

**Conclusión principal.** El runtime de efectos no usa homografía ni pose en mm. Usa un **LUT denso proyector→cámara por píxel** (`proj_to_cam.npz`) que sale del gray code. Es la única parte con uso físico real demostrado. La calibración métrica (intrínsecos, extrínsecos, rig en mm) tiene cinco variantes y ninguna quedó validada con hardware.

---

## 1. Propósito y funcionalidades

- **Captura de patrones** (`slscan/capture.py`):
  - ventana SDL sin bordes en el proyector;
  - cámaras AVFoundation, más cámaras remotas por HTTP;
  - medición de latencia y captura guiada de tablero.
- **Gray code** (`slscan/graycode.py`): genera los patrones y decodifica de forma diferencial. El resultado es un mapa denso cámara→proyector.
- **Calibración clásica** (`slscan/calib.py`): intrínsecos con ChArUco (Zhang), proyector por homografías locales, extrínsecos y bundle adjustment. Escribe `rig.json` (`slscan/rig.py`).
- **Calibración métrica con hoja** (`slscan/metric.py`, `slscan/joint.py`, `slscan/metric_session.py`): una A4 o carta física y un ChArUco proyectado. Ajuste conjunto rayo-plano, con poses de control reservadas.
- **Autocalibración sin tablero** (`slscan/autocalib.py`, `slscan/selfcalib.py`): bundle multivista sobre la escena. La escala viene de una medida externa.
- **Rig aproximado de una vista** (`slscan/approx_rig.py`): óptica del proyector supuesta, sin escala.
- **Validación y simulación** (`metric_validation.py`, `rigid_validation.py`, `rig_simulator.py`) y un visor web (`calibration_viewer.py`, puerto 8797).
- **Orquestación**:
  - `playground/dale.py`: un botón que hace todo el flujo;
  - `playground/recalib.py`: escena 2D (LUT y verificación ArUco);
  - `playground/radiometria.py`: barrido de color.
- **Otros**: `remote.py` controla un PC Windows por SSH (no tiene relación con la calibración).

## 2. Arquitectura interna

**Módulos y responsabilidades**

- **`graycode.py`**
  - `sequence()` y `pattern()` generan los patrones (`graycode.py:48-85`).
  - `decode()` produce el mapa (`graycode.py:176-291`).
  - `Decoded` es el contrato del mapa: `px`, `py` uint16 por píxel de cámara, `color`, `stats` y `valid` (`graycode.py:133-173`).
- **`capture.py`**
  - `PatternWindow` (`capture.py:111-316`), `Camera` (`:419-481`), `RemoteCamera` (`:518-575`).
  - `resolve_camera` (`:388-416`), `resolve_screen` (`:55-88`).
  - `capture_sequence` (`:763-778`), `measure_latency` (`:595-631`).
- **`recon.py`** (fuera de alcance; leí solo lo necesario)
  - `coalesce`: un match por código de proyector (`recon.py:31-62`).
  - `dense_proj_to_cam`: interpolación griddata + inpainting, que da el LUT (`recon.py:65-89`).
- **`rig.py`**: `Device` y `Rig` con save/load en los formatos `slscan-rig/1` y `/2`, importador de sltk y conversión `T_device_from_world`.
- **`calib.py`**: `CharucoBoard`, `corner_projector_coords`, `calibrate_projector`, `initial_extrinsics`, `bundle_adjust`, `calibrate_rig`.
- **`metric.py`**: `detect_paper`, `paper_scale`, `calibrate_from_paper` (legacy), `relative_to_metric` y la malla.
- **`joint.py`**: `PlaneView` y `calibrate` (ajuste conjunto rayo-plano).
- **`metric_session.py`**: protocolo de captura con poses train/holdout, `evaluate_reserved`, `write_json` atómico.
- **`selfcalib.py`**: `dense_tracks` y `fit` (bundle esparso con priors).
- **`autocalib.py`**: `capture` en vivo o replay, más `metric_scale`. Depende de `scene_tracks.rigid_tracks` (SIFT, fuera de alcance).
- **`cli.py`**: más de 20 subcomandos. Registra además `pipeline`, `objects`, `pbr` y `reconstruct` de otros módulos (`cli.py:514-521`, `1015-1026`).

**Flujo de datos (verificado)**

1. **Captura en disco.** `PatternWindow.show(patrón)` → `sleep(settle)` → `cam.flush(n)` → `cam.read()` → un PNG por patrón en `<out>/<cam>/[AA-BB-CC_NN].png`.
2. **Mapa cámara→proyector.** `load_sequence` → `decode(threshold=5, skip_bits=2)` → `<cam>/map.npz`, que es el **LUT cámara→proyector**.
3. **Mapa proyector→cámara.** `recon.dense_proj_to_cam` → verificación ArUco con corrección global → `proj_to_cam.npz` (**LUT proyector→cámara**, 1920×1080) y `scene_pov.png`.
4. **Consumo en el runtime** (fuera de alcance). `playground/touchsynth/osmo_hands.py:3-6, 54-59` hace `cv2.remap` de cada cuadro de la cámara al espacio del proyector a 960×540. Así, «un dedo detectado ya está en coordenadas del proyector (sin homografías)».
   - También lo leen `firstlook.py`, `workbench.py`, `segment_pov.py`, `keys/quad.py` y `web/scene3d.py`, que lo cargan desde `SLSCAN_SCAN`.
5. **Rama 3D.** `map.npz` + `rig.json` → triangulación (`recon.triangulate_pair`) → nube y malla → pipeline P6 (fuera de alcance).

**Flujo `dale` paso a paso (verificado por lectura de `playground/dale.py`)**

0. Parseo de argumentos. Defaults: `--settle 1.2`, `--skip-bits 2`, `--rotate 0`, `--downscale 2`, `--cam-size 3840x2160`, `--views 1`, `--motion rigid`, `--port 8796`, `--escena-efectos halos` (`dale.py:170-190`). Vacía `dale.estado.json`. Sesión = `captures/dale-<fecha>`; salida = `reports/pipeline/<sesión>` (`dale.py:192-199`).
1. **Liberar hardware** (`dale.py:87-94`): `pkill -f` sobre `playground serve`, `touchsynth live`, `playground.dualview`, `playground.firstlook` y `playground.workbench`; espera 1,5 s; consulta `http://127.0.0.1:8790/api/state`. Si responde, solo registra el error y sigue.
2. **Guía en vivo** (`dale.py:204-208`): pide colocar el proyector quieto y espera confirmación hasta 600 s (voz, dashboard o Enter).
3. **Visor del pipeline**: `Popen` desprendido de `slscan pipeline serve <out> --port 8796` (`dale.py:212-213`). Nunca se cierra.
4. **Calibración** (`dale.py:220-256`):
   - comprueba `autocalib --help` y los flags soportados lanzando hasta 3 procesos Python (`dale.py:151-158`);
   - ejecuta `slscan autocalib <sesion> --out <sesion>/rig.json --display-confirmed --views N --settle max(settle,2.0) --rotate --downscale --cam-size --motion (camera si views==1) [--scale] [--scale-pixels] [--viewer-state]`;
   - reenvía eventos JSONL y, ante `move_camera`, narra el `msg` y escribe `continue` por stdin tras la confirmación;
   - dentro de autocalib: abre `PatternWindow` y `Camera('osmo')`, proyecta gris 180 y guarda `color.png`, captura 44 patrones con `settle` 2 s y `flush=5`, decodifica con `skip_bits=2`, guarda `view_00_map.npz` y borra los PNG (`autocalib.py:111-136`); luego `selfcalib.fit` y `metric_scale`;
   - si sale con código distinto de 0, narra «no convergió» y sigue sin rig.
5. **Scan** (`dale.py:265-267`): `slscan scan --camera osmo --cam-size 3840x2160 --downscale 2 --rotate R --name osmo --screen 1920x1080 --size 1920x1080 --settle 1.2 --skip-bits 2 --out <sesion>`. Captura de nuevo los 44 patrones en `<sesion>/osmo/`, guarda `color.png` con gris 160 y decodifica a `osmo/map.npz`. `dale` no le pasa `--rig`, así que no triangula.
6. **Radiometría** (`dale.py:272-276`): `playground.radiometria` proyecta negro/base, referencia y rampas R/G/B y gris (6 niveles), mediana de 3 cuadros por patrón; escribe `radiometry/*.png` y `radiometry_capture.json`.
7. **Escena 2D** (`dale.py:279-284`): `playground.recalib --scan <sesion> --skip-scan --no-workbench`. Hace `system_profiler` y displayplacer si el proyector está en espejo, resuelve la Osmo sondeando índices, calcula el LUT denso, proyecta la grilla ArUco 2 veces (2,0 s y 1,2 s de espera), corrige y guarda `proj_to_cam.npz` y `scene_pov.png`, y lanza `playground.segment_pov` (fuera de alcance).
8. **Pipeline P6** (`dale.py:287-296`): `slscan pipeline run <sesion> --out <out> [--rig]` (fuera de alcance).
9. **Escena 3D** (`dale.py:298-316`): usa `register_render/*/scene.json` si existe; si no, `slscan.registered build` con malla y rig.
10. **Runtime** (`dale.py:318-331`): copia `objects.json` a la sesión, lanza `playground serve --scan <sesion> --projector --port 8790` con `SLSCAN_SCAN` y `TOUCHSYNTH_MIDI=mac` (log en `~/.local/state/sl-scanner/serve.log`), espera 9 s fijos y ejecuta `playground.showcase --escena halos`.

Duración estimada en vivo con `--views 1` (inferida, no medida): espera humana + unos 100 s de autocalib + 60-65 s de scan + 30-60 s de radiometría + 15-30 s de recalib + segment_pov + pipeline P6 (fuera de alcance) + 9 s de arranque del runtime. En total, unos 4 a 6 minutos más el pipeline.

## 3. Ejecución y concurrencia

- **Todo es secuencial y en procesos separados.**
  - `dale` lanza subprocesos uno tras otro y les lee stdout línea a línea (`dale.py:56-84`).
  - Hay un solo hilo extra: en `autocalib` lee stdin entre vistas mientras bombea eventos SDL (`autocalib.py:100-108`).
  - `calibration_viewer` sondea `state.json` cada 250 ms para emitir SSE (`calibration_viewer.py:228-235`). Su `Simulation` corre en un hilo daemon.
- **Propiedad del hardware: «un proceso a la vez».** `dale` mata el runtime y los trackers con `pkill -f` (`dale.py:87-94`).
  - Cada etapa vuelve a abrir la ventana y la cámara: autocalib, scan, radiometría y recalib.
  - Cada `Camera('osmo')` ejecuta `resolve_camera`, que **abre los índices 0 a 7 de AVFoundation para sondear** (`capture.py:401-407`).
- **Loop de captura:** un patrón cada `settle` + `flush` (unos 1,4 a 2,3 s). Además, `show()` nunca hace más de un flip por periodo de refresco (`capture.py:270-276`).
- **Qué vive en memoria** (medido en mi prueba sintética; detalle en §8):

  | Elemento | Tamaño | Notas |
  |---|---|---|
  | Pila de patrones | no se guarda en RAM | van a disco como PNG; `decode` carga de a pares (`graycode.py:219-223`) |
  | PNG de 1080×1920×3 | 6,2 MB sin comprimir | 44 por vista; por eso exigen 2 GiB libres (`autocalib.py:51`) |
  | `Decoded` a 1080×1920: `px`+`py` | 8,3 MB | uint16 |
  | `Decoded`: `color` | 24,9 MB | float32 |
  | `Decoded`: `bit_contrast` | unos 45 MB | uint8 por bit; solo con `retain_contrast` |
  | `proj_to_cam.npz` | 18,7 MB | `mapx`/`mapy` float32 más `valid` |
  | Cuadro 4K de la Osmo | 24,9 MB | se reduce con INTER_AREA y se rota en cada lectura (`capture.py:456-469`) |

## 4. Comunicación e interfaces

- **Hijos → `dale`**: JSONL por stdout con `{stage, step, progress, msg, ok, …}` (`autocalib.py:19-21`). **`dale` → `autocalib`**: `continue\n` por stdin al recibir `step=move_camera` (`dale.py:132-148`, `autocalib.py:97-110`).
- **Voz (secretaria)**:
  - `dale` agrega líneas a `~/.local/state/sl-scanner/secretaria/avances.jsonl` (`dale.py:45-53`);
  - para confirmar lee `feedback.jsonl` buscando las palabras «contin/listo/dale/ ya/sigue» (`dale.py:104-126`).
- **Dashboard**:
  - archivo testigo `~/.local/state/sl-scanner/dale.continue`;
  - estado en `dale.estado.json`, que en realidad es **JSONL** escrito en modo append (`dale.py:38-40`).
- **Puertos y endpoints**:
  - 8790: runtime, `/api/state`;
  - 8796: visor del pipeline;
  - 8797: visor de calibración;
  - 8798: escena registrada.
  - El visor de calibración es FastAPI, solo loopback (`calibration_viewer.py:192-202`), con `/api/state`, `/api/events` (SSE), `/images/*.jpg` y `POST /api/simulation/*`.
  - Descarga three.js desde un CDN la primera vez (`calibration_viewer.py:253-262`).
- **Variables de entorno**:
  - `SLSCAN_SCREEN` y `SLSCAN_CAMERA` (este último es un parche del 20-nov que fuerza una webcam por índice, `capture.py:393-395`);
  - `SLSCAN_SCAN`, `PLAYGROUND_STATE` y hints `SDL_*`.
- **Contratos en disco** (§6): `map.npz`, `proj_to_cam.npz`, `rig.json` y los reportes JSON auxiliares.

## 5. Algoritmos y modelos

**Gray code** (reimplementación de sltk; verificado)
- Patrones:
  - Gray binario reflejado, `bits_for(1920)=bits_for(1080)=11`;
  - cada bit lleva patrón e inverso, eje x y luego eje y, del MSB al LSB;
  - en total **44 imágenes** (`graycode.py:73-85`).
- Decodificación:
  - luma entera; `diff = patrón − inverso`; el bit vale 1 si `diff > 5`, 0 si `diff < −5`, e inválido en otro caso;
  - un bit inválido invalida el eje (`graycode.py:236-249`);
  - después XOR-shift de gray a binario.
- `skip_bits=2`:
  - descarta los 2 bits finos y asigna el centro del bloque de 4 px (`graycode.py:212`, `276-280`);
  - se justifica porque la cámara ve unos 3,7 px de proyector por px de cámara. En `reports/scan1_corners.json`, 1680 px de proyector caen en unos 450 px de cámara.
- No hay phase shift, umbral adaptativo ni corrección de errores.

**LUT proyector→cámara**
- `coalesce` promedia los píxeles de cámara de cada código: unos 129.600 matches con bloques de 4 px.
- Luego `griddata` lineal (Delaunay) sobre 1920×1080 e `inpaint` TELEA de agujeros de hasta 25 px (`recon.py:65-89`).

**Verificación ArUco en `recalib`**
- Proyecta 40 marcadores DICT_4X4_50 de 64 px sobre fondo negro, o sea 160 esquinas (`recalib.py:82-89`).
- Compara la esquina detectada con la predicción del LUT y reporta la mediana del error y cuántas quedan bajo 3 px.
- Corrige con un **desplazamiento global por mediana**, usando solo errores menores a 12 px (`recalib.py:104-111`).

**`calib.py` (estilo Moreno-Taubin)**
- Cada esquina del ChArUco pasa al proyector con una homografía RANSAC local de ±30 px. Exige datos en los 4 cuadrantes y, si no, usa la homografía del plano (`calib.py:158-201`).
- El proyector se calibra como cámara inversa con `K0` = `fx=1,3w`, `cy=0,9h` (lens shift; `calib.py:210-212`).
- Extrínsecos con PnP y promedio SVD de rotaciones; BA con `least_squares` TRF y pérdida Huber, intrínsecos fijos (`calib.py:242-323`).

**`joint.py`**
- Parámetros: Kc y Kp (focales, centro, Brown5), la pose relativa y la pose de cada hoja.
- Residuos:
  - esquinas físicas de la hoja reproyectadas en la cámara;
  - rayos del proyector intersectados con el plano de la hoja y reproyectados en la cámara (`joint.py:139-152`).
- Ajuste por etapas: focal y centro, luego k1, luego Brown5. Pérdida soft_l1, reinicios y bootstrap por vista.
- Diagnóstico de rango del Jacobiano (`joint.py:172-298`).

**`selfcalib.py`**
- Inicialización: F con RANSAC, E con focales a priori (`fc = max(size)·20/36`, `fp = 1,1·w`), `recoverPose` y triangulación (`selfcalib.py:94-111`).
- BA esparso con priors (`sigmas`, `:127`) y `jac_sparsity`.
- Rango por complemento de Schur, sin contar los priors.
- Rechaza si el rango es deficiente, la escena casi plana, hay profundidad negativa o la reproyección supera 1 px (`:182-187`).
- Modo «rigid»: compone un único T cámara→proyector con cada pose de cámara (`projector_at`, `:70-74`).

**`approx_rig.py`**
- Fija K del proyector con un barrido de throw (1,2 a 1,5, offset 100%).
- Obtiene la focal de la cámara igualando los valores singulares de E (`approx_rig.py:35-41`). Luego ajuste de perfil y BA.

**Escala** (`autocalib.py:147-173`)
- `huincha:MM`: distancia entre centros ópticos.
- `teclas:MM`: un segmento triangulado entre dos píxeles con gray válido.

**Precisión con evidencia**

| Fuente | Datos | Resultado |
|---|---|---|
| Mi prueba sintética | homografía ideal, ambiente 30 DN, contraste ~100 DN | error del LUT: mediana **0,30 px de cámara**, p95 0,54 |
| `joint`, visor sintético | simulado | RMS ~0,15/0,20 px; máximo dimensional reservado ~1,04 mm (`docs/P2-VISOR.md:129-135`) |
| Autocalib sintético, 3 vistas | simulado | XYZ reservado RMS 6,78 mm, máximo 12,58 mm; **con 1 vista: 126,85 mm** (`docs/P2-AUTOCALIB.md:57-64`) |
| Modo rigid sintético | simulado | RMS XYZ 10,5 a 11,9 mm (`docs/P2-RIG-RIGIDO.md:111-122`) |
| Rig aproximado | **real** (17-oct) | RMS de selección ~1,06 px, máximo 6,47 px; escala desconocida (`docs/P2-RIG-APROXIMADO.md:46-58`) |
| «Oficio» | **real** | lados de la hoja con error máximo 26,09 mm y 4,00 mm, sobre referencias usadas en el propio ajuste (`docs/P2-OFICIO-REAL.md:16-19`) |

## 6. Persistencia

- **`<cam>/map.npz`** (`Decoded.save`, `graycode.py:161-165`): `px`, `py` (uint16, `0xFFFF` = inválido), `color`, `size` y `stats` (JSON en bytes, con `skip_bits` y `projector_size`). Es el **LUT cámara→proyector**.
- **`proj_to_cam.npz`** (`recalib.py:115`): `mapx`, `mapy` float32 de 1080×1920 y `valid` bool. Es el **LUT proyector→cámara** que consume el runtime.
- **Otros archivos de escena**: `scene_pov.png`, `recalib.json`, `camera_acquisition.json`, `radiometry_capture.json`.
- **`rig.json`**:
  - `slscan-rig/1|2` con `K`, `dist`, `R`, `t` (mm, `X_dev = R·X_world + t`), `T_device_from_world`, `size`, `rms`, `fov_deg` y `extra.*report`;
  - escritura atómica que rechaza NaN (`rig.py:108-134`);
  - con approx-rig, `units=projector_baseline`, que **no son mm** (`approx_rig.py:176`).
- **Reportes de autocalib y metric**: `autocalib-session.json`, `autocalib-report.json`, `pose_NN/observation.json`, `session.json`.
- **Rutas y supuestos hardcodeados**:
  - `~/.local/state/sl-scanner/...`;
  - UUIDs de displayplacer del Mac y del T749 (`recalib.py:37-38`), la cadena `"T749-fHD720"` y Sidecar de 1194×756 (`capture.py:46`);
  - **1920×1080** en todo el flujo (`recalib.py:24`, `radiometria.py:20`). `autocalib` aborta si el display no es 1920×1080 físico y lógico 1:1 (`autocalib.py:52-54`).
- **Cámara**: el nombre `'osmo'` está fijo (`autocalib.py:69`, `radiometria.py:33`, `recalib.py:91`). Se reconoce por cuadro vertical o por 25 fps (`capture.py:408-415`). Se piden 3840×2160 con reducción 2× porque la Osmo a 1080p entrega una imagen encajonada de 607×658 útiles (`capture.py:421-422`).
- **Sin carpeta `captures/`**: no está en este snapshot, así que no pude verificar las sesiones reales scan2, scan5 ni dale-20261017.

## 7. Estado real

- **Funciona con datos físicos** (evidencia indirecta: comentarios, docs y consumidores del runtime):
  - gray code → `map.npz` → `proj_to_cam.npz` → POV;
  - ejemplos: «visto en captures/scan2» (`metric.py:252`) y scan5 con 209.504 px válidos (`docs/P2-AUTOCALIB.md:214`);
  - el runtime de manos y teclas depende de ese archivo;
  - también hubo un segundo LUT de una cámara Brio (`keys/refine.py:637`).
- **Solo sintético**:
  - `joint` y `metric_session` («Sin toma real ni aprobación física de 2 mm», `docs/P2-RIG.md:214`);
  - autocalib de 3 vistas;
  - modo rigid.
- **Falló en real**: autocalib de una vista («falló por rango y reproyección; no publicó rig», `docs/P2-AUTOCALIB.md:74`).
- **Experimentos**: approx-rig y oficio (unidades relativas o errores de 26 mm), y `calib-projected` (medida manual).
- **Abandonado o legado**: `calib-procam`, `metric --legacy`, `remote.py`.

## 8. Rendimiento

- **Latencia proyector→Osmo: 1.337 ms** (`reports/latency_osmo_proj1.json`). Solo 1 de 5 intentos fue válido. Coincide con lo que anota `03-tecnica.md` sobre webcams en 4K en macOS. Por eso se usa `settle` de 1,2 a 2 s.
- **Captura (inferido de los parámetros)**:
  - scan ≈ 44 × (1,2 s + flush) ≈ **60-65 s**;
  - autocalib ≈ 44 × (2,0 + 0,2) ≈ **100 s por vista**;
  - radiometría ~30-60 s; recalib ~10-20 s más segment_pov.
- **Cómputo (medido en mi prueba sintética de 1920×1080)**:
  - `decode` de 44 cuadros en memoria: 1,18 s, más la lectura de PNG;
  - `coalesce`: 0,03 s;
  - `dense_proj_to_cam`: **2,40 s** (griddata).
- **Cuellos de botella**: la latencia de la cámara (×44); que el gray code se capture **dos veces** en `dale` (§9); los 8 patrones finos que se capturan y se descartan; y las reaperturas de la cámara con sondeo de 8 índices.

## 9. Deuda técnica, bugs y riesgos

1. **Bug verificado.** `slscan grab` y `slscan latency` usan `open_camera` sin importarlo, lo que da `NameError` (`cli.py:119`, `cli.py:423`). Lo reproduje: `name 'open_camera' is not defined`, antes de abrir cualquier dispositivo.
2. **Bug de flujo (verificado por lectura).** `dale` usa `--views 1` por defecto y no pasa `--scale`.
   - `autocalib` siempre llama `metric_scale`, que lanza «falta escala externa» (`autocalib.py:243`, `:148`), y sale con código 2.
   - `dale` lo narra como «no convergió» (`dale.py:248-249`), así que **el `dale` por defecto nunca produce rig**.
   - Además, una vista con óptica libre ya falló físicamente.
3. **Gray code duplicado.** `dale` captura los 44 patrones en autocalib (`view_00`) y otra vez en `scan`; se pierden unos 100 s.
4. **Patrones innecesarios.** `sequence()` ignora `skip_bits`: se capturan 44 cuando bastan 36, y `decode` descarta los demás (`graycode.py:212`).
5. **Sin sincronía real.** Solo hay `sleep(settle)` fijo, sin verificar que el cuadro muestre el patrón esperado (`capture.py:768-772`). Con 1,2 s de settle frente a 1,34 s de latencia medida, el margen es nulo (inferido). Un bit equivocado da coordenadas falsas que no se marcan como inválidas.
6. **Umbral fijo de 5 DN.** No hay referencia blanco/negro por píxel ni chequeo de consistencia entre códigos vecinos.
   - El par complementario cancela la luz ambiente (es la principal fuente de robustez), y cada patrón tiene ~50% de blanco, lo que mantiene estable la autoexposición.
   - Con proyector débil fallan primero los bits finos, y superficies oscuras o especulares quedan inválidas y se rellenan (≤25 px).
   - La exposición manual no se aplica en AVFoundation y queda marcada `lock_status: unverified` (`capture.py:436-454`).
7. **Corrección global por mediana en `recalib`** (`recalib.py:110-111`). Es un «nudge» que la propia documentación P2 prohíbe y que tapa errores sistemáticos.
8. **Fragilidad del entorno macOS**:
   - `pkill -f` por patrones;
   - si 8790 sigue ocupado, solo registra el problema y continúa (`dale.py:92`);
   - el visor 8796 nunca se cierra;
   - el runtime se arranca con `sleep(9)` en lugar de un health-check (`dale.py:328`);
   - displayplacer con IDs fijos.
9. **Cinco calibradores coexistiendo** con convenciones de píxel distintas: centro +0,5 (legacy) frente a centros enteros. Hay parches de −0,5 repartidos (`autocalib.py:158`, `selfcalib.py:58`) y antes hubo un bug de «la mitad de los bloques corrida 1 px» (`metric.py:250-253`).
10. **Tests desincronizados.** En `test_selfcalib.py`, el fixture `SimpleNamespace(views=3, settle=0)` no trae `cam_size`, `rotate` ni `downscale` (ver resultado de tests).
11. **Lección a conservar.** Usar vsync **obligatorio**: presentar sin vsync por HDMI provocó dos kernel panics (`capture.py:132-137`).
12. **Estilo.** One-liners densos y `build_parser` redefinido por monkeypatch (`cli.py:1012-1026`). `dale` lanza tres procesos `--help` solo para detectar qué flags existen (`dale.py:151-158`).

## 10. Relevancia para el producto (proyector + cámara + agente sobre una mesa)

- **Esencial.** Una correspondencia cámara↔proyector por cámara, en ambas direcciones:
  - **cam→proj**: llevar a coordenadas del proyector los toques y objetos detectados;
  - **proj→cam**: llevar el cuadro a la «vista del proyector», para que agente y detector razonen en el espacio donde se dibuja.
  - El enfoque LUT/POV del repo es exactamente eso, y tiene una ventaja sobre la homografía: **ya incluye parallax y distorsión** para objetos con altura.
- **Reutilizable con cambios**:
  - `graycode.py` (casi tal cual);
  - `coalesce` y `dense_proj_to_cam`;
  - la grilla ArUco de `recalib` (como modo rápido y como health-check);
  - `PatternWindow` (sus lecciones de macOS);
  - `RemoteCamera` (celular por HTTP);
  - `calib.py` y `joint.py` para un modo métrico avanzado;
  - el formato `rig.json /2`.
- **Prescindible para el MVP**: autocalib, selfcalib, approx_rig y oficio (complejos y sin resultado físico aceptado), `rig_simulator`, `calibration_viewer`, `metric_session`, `radiometria`, `remote.py` y la orquestación por `pkill` de `dale`. El rig en mm solo hace falta para razonar en 3D; para «mm sobre la mesa» basta un factor de escala del plano (por ejemplo, un marcador de tamaño conocido).

**Comparación con ArUco + `findHomography`** (`03-tecnica.md` §1)

- **Ventajas de ArUco**:
  - 1 cuadro en lugar de 44: unos 2-3 s incluso con la latencia de la Osmo;
  - detección binaria robusta, tolerante a JPEG y a cámaras remotas;
  - fácil de repetir como verificación.
- **Desventajas de ArUco**:
  - solo vale para el plano de la mesa. Un objeto de altura h produce un error de aproximadamente h·baseline/distancia; por ejemplo, 30 cm de baseline a 1 m y 5 cm de altura dan **~15 mm** (inferido);
  - no modela la distorsión de webcams baratas, con varios px de error en los bordes (inferido);
  - con fondo negro, la autoexposición puede subir y saturar.
- **Límite físico común a ambos**: la densidad de muestreo de la cámara (~3,7 px de proyector por px de cámara). La cámara debe encuadrar ajustado el área proyectada.

**Recomendación: camino híbrido.** Usar ArUco y homografía para la demo (la UI está sobre la mesa) y dejar el gray code como modo «preciso» para efectos sobre objetos con altura. El código base del modo rápido ya existe en `recalib.py:82-111`.

**Registrar una cámara nueva (celular) en caliente.** El proyector es el marco común, así que cada cámara tiene su propio mapa independiente y no hace falta tocar las demás. Hay tres opciones:

- (a) **Proyectar la grilla ArUco 1-2 s** y calcular la homografía (con TPS opcional sobre las 160 esquinas).
- (b) **Gray code de 36 patrones solo para esa cámara.** Con una cámara de baja latencia serían unos 7 s (inferido). `capture-scan` ya captura con varias cámaras en una sola pasada (`cli.py:360-394`).
- (c) **Sin proyectar nada** (propuesta inferida): emparejar SIFT entre el cuadro nuevo y `scene_pov.png`, que ya es la escena en coordenadas del proyector.

Para celulares, `RemoteCamera` sirve vía IP Webcam o DroidCam (`capture.py:518-575`). Pero `flush` no hace nada y los JPEG degradan un umbral de 5 DN: conviene ArUco o la opción (c).

## 11. Recomendaciones para la reescritura

- **Servicio de calibración con contrato claro.** Producir un `CameraProjectorMap` versionado:

  ```
  {camera_id, kind: homography|lut, H?, lut_ref (npz o memoria compartida),
   valid, cam_size, proj_size, block_px, residual_px {median, p95} (ArUco),
   created_at, scene_signature}
  ```

  - Exponer `cam_to_proj(points)` y `proj_to_cam`.
  - El render consume el LUT o la H; el agente lo lee por API.
- **El render es dueño permanente de la ventana del proyector.** La calibración le pide mostrar patrones por IPC, sin matar procesos. Hay que conservar vsync, ventana SDL sin bordes, nunca usar el display interno y subir la ventana sobre la barra de menú.
- **Sincronía adaptativa.** Cada patrón se acepta cuando un parche testigo del cuadro cambia, reutilizando la lógica de `measure_latency`, en vez de un `sleep` fijo.
- **Capturar solo los bits que se usan** y elegir `skip_bits` según los px de proyector por px de cámara. Añadir umbral relativo con referencia blanco/negro, chequeo de vecinos y reportar el porcentaje válido.
- **ArUco como health-check periódico** o a pedido: si la mediana supera X px, recalibrar. Nada de «nudges» globales silenciosos.
- **Conservar casi tal cual**: `graycode.decode` y `Decoded`, `coalesce`, el modelo `Device`/`Rig` y `calib.corner_projector_coords` y `bundle_adjust` (para un modo métrico con ChArUco impreso).
- **Rediseñar**:
  - reemplazar `griddata` por interpolación por bloques o afín por partes (hoy cuesta 2,4 s);
  - abrir la cámara una sola vez como servicio de cuadros;
  - parametrizar resolución de cámara y proyector, sin `'osmo'` ni 1920×1080 fijos.
- **Descartar** del MVP autocalib, selfcalib, approx y oficio, y evitar la Osmo a 4K (1,3 s de latencia): usar una webcam a 720p, como propone `03-tecnica.md`.

---

## Archivos leídos

**Leídos completos (27 del alcance + 1 del equipo).** `slscan/__init__.py`, `slscan/__main__.py`, `slscan/cli.py`, `slscan/capture.py`, `slscan/graycode.py`, `slscan/calib.py`, `slscan/autocalib.py`, `slscan/selfcalib.py`, `slscan/approx_rig.py`, `slscan/rig.py`, `slscan/joint.py`, `slscan/metric.py`, `slscan/metric_session.py`, `slscan/metric_validation.py`, `slscan/rigid_validation.py`, `slscan/rig_simulator.py`, `slscan/remote.py`, `slscan/calibration_viewer.py`, `playground/dale.py`, `playground/recalib.py`, `playground/radiometria.py`, `docs/P2-RIG.md`, `docs/P2-AUTOCALIB.md`, `docs/P2-RIG-RIGIDO.md`, `docs/P2-RIG-APROXIMADO.md`, `docs/P2-VISOR.md`, `docs/P2-OFICIO-REAL.md`, además de `platanus-hack-26/docs/investigacion/03-tecnica.md`.

**Sin leer del alcance:** ninguno.

**Fuera de alcance, solo fragmentos:** `slscan/recon.py:25-112`, `playground/touchsynth/osmo_hands.py:1-75`, `playground/web/scene3d.py:130-145`, fragmentos de `tests/test_selfcalib.py`, `tests/test_capture.py` y `tests/test_dale.py`, y `reports/latency_osmo_proj1.json`, `reports/scan1_corners.json`, `reports/align_test.json`.

## Resultado de los tests

Comando: `.venv/bin/python -m pytest tests/<archivo> -q -x -p no:cacheprovider` (test_selfcalib repetido sin `-x`). **68 pasan y 2 fallan.**

| Archivo | Resultado |
|---|---|
| test_graycode | 4 pasan |
| test_calib | 7 pasan |
| test_capture | 3 pasan |
| test_selfcalib | **8 pasan, 2 fallan** |
| test_rigid_selfcalib | 5 pasan |
| test_approx_rig | 2 pasan |
| test_joint | 5 pasan |
| test_metric | 13 pasan |
| test_metric_session | 8 pasan |
| test_metric_validation | 3 pasan |
| test_rig_flow | 1 pasa |
| test_dale | 2 pasan |
| test_calibration_viewer | 7 pasan |

Los dos fallos son `test_live_adapter_uses_requested_devices_and_closes_on_cancel[continue\ncontinue\n-True]` y `[-False]`, con `AttributeError: 'types.SimpleNamespace' object has no attribute 'cam_size'` en `slscan/autocalib.py:58`. Es un fixture desactualizado frente a los flags `--cam-size/--rotate/--downscale`, no un error del estimador.

`test_dale` solo prueba el reenvío de JSONL y el `continue` con un script falso; no cubre el flujo completo. `test_capture` simula las ventanas y solo lista displays con CoreGraphics; no abre hardware.

La prueba sintética que ejecuté (NameError de `grab` y tiempos de decode y LUT) está en `/tmp/calib_probe/probe.py`, fuera del repo. Salida: `cmd_grab NameError: name 'open_camera' is not defined`; decode 1,18 s; coalesce 0,03 s con 129.600 matches; `dense_proj_to_cam` 2,40 s; error del LUT mediana 0,30 px y p95 0,54 px de cámara; `px+py` 8.294.400 bytes, `color` 24.883.200 bytes, `mapx+mapy+valid` 18.662.400 bytes.
