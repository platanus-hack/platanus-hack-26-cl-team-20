# pcx-hands · manos y toque en Rust (ort + MediaPipe ONNX del opencv_zoo)

Cuadro `&[u8]` RGB/BGR + `FrameMeta` → palma (192², anchors SSD, NMS) → ROI rotado → 21 landmarks (224²) →
seguimiento estilo MediaPipe (ROI del cuadro anterior; palma solo si faltan manos) → IDs estables → One Euro →
toque (pinch con histéresis, apoyo por tamaño de palma, dwell, zonas excluidas) → `HandState` + eventos con `t_capture_ns`.

**API**: `HandsEngine::from_cache(EngineConfig)` · `.process(&Frame, &FrameMeta) -> HandsOutput{hands, tracked, events}` ·
`.run(&mut impl FrameSource, |out| …)` (el core implementa `FrameSource` con su lector de memoria compartida) ·
`set_homography(cam→table_mm)`. Piezas sueltas: `HandDetector` (`detect_palms`, `landmarks(roi)`), `HandTracker`,
`TouchTracker::update(&[TouchHand], t_ns)`. Eventos `HandsEvent`: `TouchDown/Move/Up`, `Dwell{progress}`, `HandsGone`
(sin manos `calm_s`=0,4 s → foto limpia); `.to_contract()` da `pcx_contracts::Event` v2.1 (toques y dwell requieren `point_mm`).
- Contrato: `HandState`, `Touch`, `Trigger`, `FrameMeta`, `Event` vienen de `pcx-contracts` (v2.1). `HandsGone` →
  `hands_gone` y el «apoyo» se emite con `trigger: rest`.

**Modelos** (Apache-2.0, no se commitean): `cargo run -p pcx-hands --example fetch_models` → caché del SO
(`~/Library/Caches/ai.pcx.pcx/models`, o `$PCX_MODELS_DIR`) con sha256. Features: `download` (def.), `coreml`.

**Paridad** (`cargo test -p pcx-hands --release -- --nocapture`): 9 imágenes de `mediapipe-assets` (Apache-2.0, 600 KB,
`testdata/`), referencia = Python del zoo con OpenCV 5.1 (`testdata/make_reference.py`, venv `uv` fuera del repo).
Mismo n.º de palmas en las 9; 13 manos; **error medio 1,09 px, peor 3,13 px (≤ 1 % del alto de la mano)**; palma
≤ 0,5 px; presencia y lateralidad iguales (±0,02). Diferencias: un solo warp afín bilineal con supermuestreo en vez de
crop→rotar→`INTER_AREA`, NMS con IoU real (el zoo pasa xyxy como xywh) y decodificación JPEG distinta.

**Benchmark** (M1 Pro, `--example bench`, 2 manos, `threads=4`; Mac compartido con otros builds → ±20 %):

| backend | resolución | seguimiento 2 manos | palma | landmarks/mano | redetección (palma + 2) |
|---|---|---|---|---|---|
| CPU | 640×480 | 6,0 ms (≈170 fps) | 19 ms | 3,6 ms | ≈ 26 ms |
| CPU | 1280×720 | 11,4 ms (≈ 88 fps) | 14 ms | 5,4 ms | ≈ 25 ms |
| CoreML | 640×480 | 5,8 ms | 13 ms | 2,8 ms | ≈ 19 ms |
| CoreML | 1280×720 | 11,0 ms | 16 ms | 6,1 ms | ≈ 28 ms |

En régimen (manos seguidas) solo corren landmarks: ≥ 88 fps a 720p vs ~25 fps de MediaPipe Python en la mesa. CoreML
casi no mejora (probable partición del grafo); CPU es el default. A 1280×720 el muestreo pesa (supermuestreo ≤ 3×3).

**Pendiente**: lateralidad con la cámara cenital (`flip_handedness`), afinar `hand_ref` del apoyo con la mesa real,
validar CoreML con paridad, NMS ponderado de MediaPipe.
