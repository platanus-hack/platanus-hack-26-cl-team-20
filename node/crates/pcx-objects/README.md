# pcx-objects · cascada de seguimiento de objetos (port de `seguidor_v4.py`)

Entrada: **foto limpia** (proyector apagado, marcador de sincronía ya borrado) rectificada al lienzo de la mesa con
`geom::Rectifier::new(homografía cámara→table_mm, Canvas)`. Salida: `ObjectState` + `ObjectEvent`
(`object_appeared/moved/removed`) con los campos del contrato v2 y extensiones opcionales que el schema admite:
`aff` (2×3 en mm, registro → ahora), `appearance` (PNG RGBA del recorte), `prompt`, `seq`, `level`.
API: `Tracker::new(canvas, Params::default())` `.with_detector/segmenter/llm/appearance_dir` · `update(&CleanFrame)
→ Report{events, unclaimed_zones, log}` · `register_all(&CleanFrame)` (nivel 5) · `add_object(_polygon_mm)` · `objects()`.

| Nivel | `seguidor_v4.py` (02:09) | Rust | Umbrales (iguales) |
|---|---|---|---|
| 0 | `absdiff(blur5(gris)) > umbral`, open 5, close 21 | `raster::{absdiff_gt, open_sq, close_sq}` | 24 · frac 0.2 · min_zona 500 |
| 1 | `matchTemplate(TM_CCOEFF_NORMED, mask)` | `ncc::masked_zncc` | igual 0.85 |
| 2 | `ORB_create(800,1.2,6,11,21)` + knn 0.8 + `estimateAffinePartial2D` | `orb` propio + `geom::ransac_similarity` | ≥10 inliers · 4 px · \|s−1\|≤0.15 |
| 3 | DINO 8805 + `MobileSAM.predict(caja, centro)` + hist HSV | traits `Detector`/`Segmenter`; `onnx::{GroundingDino, MobileSam}` (feature `onnx`) | box 0.22 · sim>0.6 · 0.5<ratio<2 · margen 0.25 |
| 4 | `juez_claude` (CLI) REF + 5 candidatos | `llm::ClaudeClient` (HTTP) / `MockLlm` | candidatos con sim>0.35 · effort low |
| 5 | `automatic_masks` 28×16 + juez nombra (formato `regiones`) | `models::automatic_masks` + `register_all` | min_area 200 · <25 % del área |

Diferencias: Claude por HTTP crudo (`/v1/messages`, `claude-opus-5-5` por defecto o `PCX_LLM_MODEL`, header
`anthropic-beta: server-side-fallback-2026-07-01` + `"fallbacks": "default"`, revisa `stop_reason == "refusal"` antes de
leer, salida con `output_config.format` JSON Schema, uso en `.jsonl` con modelo/seg/tokens). Imágenes rotuladas con
texto intercalado (REF/Candidato k) en vez de `putText`; `SentImage::box_to_original` reescala cajas. La afín es en mm
(conjugación exacta lienzo↔mm, sin el muestreo de `_A_proj`). Sin portar aún: `verificar_todo` (revisión periódica por
DINO) y el apagón con marcador (vive en captura/core: aquí entra la foto ya limpia).

**Nivel 2 sin OpenCV:** ORB propio (FAST-9 de `imageproc` + orientación por centroide + BRIEF rotado de 256 bits,
pirámide 6×1.2, ~250 líneas). `imageproc` no tiene descriptores invariantes a rotación (template matching exigiría
barrer ángulos); `akaze`/`cv` de rust-cv están sin mantenimiento y cuestan ~10× más. Determinista en las 3 plataformas.

**Modelos** (no se commitean): `models::ensure` los baja a la caché del SO (`~/Library/Caches/pcx/models`,
`%LOCALAPPDATA%\pcx\models`, `~/.cache/pcx/models`; o `PCX_MODELS_DIR`) y verifica sha256 fijo. DINO fp32 (719 MB):
las variantes int8 usan `ConvInteger`, que el EP de CPU de ORT no implementa (probado).

## Cifras (M1 Pro, `cargo bench -p pcx-objects`, lienzo 1280×720, netas de construir el seguidor ≈20 ms)
N0 escena quieta **≈16 ms** · N0+N1 (cambio de luz, «sigue igual») **≈46 ms** · N0+N1+N2 objeto movido **≈123 ms**
(ORB de imagen completa 58 ms). Python estimaba ~ms / ~ms / ~0.2 s. Nivel 3 (ONNX, test `#[ignore]`): ver abajo.
Niveles 4–5 reales: Sonnet ~20 s el registro (hallazgo de Daniel); aquí probados solo con mock (sin key).

NIVEL3_CIFRAS

## Tests
`cargo test -p pcx-objects`: escenas sintéticas (quieto, luz, traslación, rotación 30°, desaparece/reaparece,
intercambio, objeto nuevo sin dueño, arrugado → N3, ambiguo → N4 con mock, registro N5 con mock) + unidades.
`PCX_TEST_IMAGE=img.png cargo test -p pcx-objects --release --features onnx -- --ignored --nocapture` corre DINO+SAM.

## Propuesta: paralaje de objetos altos
La homografía es del plano de la mesa: un punto a altura *h* se ve corrido radialmente desde el nadir de la cámara
`C` por `h/(Hc−h)`, y el proyector (otro centro, `Hp`) lo pinta corrido por su propio factor. Al mover el objeto el
corrimiento cambia, por eso el efecto «se despega». Propuesta: (1) calibrar centro y altura de cámara y proyector
(R5: homografía a dos alturas, p. ej. la mesa y una caja de altura conocida); (2) altura por objeto con el escaneo de luz
estructurada (disparidad cámara↔proyector = *h*) o, sin escaneo, con el LLM del registro («alto/plano») como valor
aproximado; (3) en N2 ajustar la similitud sobre puntos corregidos `P = C + (P'−C)(Hc−h)/Hc` (huella real) y publicar
`polygon_mm` de la huella + `height_mm`; (4) el renderer proyecta sobre la tapa con `P_p = Cp + (P−Cp)·Hp/(Hp−h)`.
Los objetos planos (h≈0) quedan igual que hoy.
