# pcx-objects · cascada de seguimiento de objetos (port de `seguidor_v4.py`)

Entrada: **foto limpia** (proyector apagado, marcador de sincronía ya borrado) rectificada al lienzo de la mesa con
`geom::Rectifier` (homografía cámara→table_mm, o con lente: `with_radial`/`from_poly`, ver R5). Salida: `ObjectState` + `ObjectEvent`
(`object_appeared/moved/removed`) con los campos del contrato v2 y extensiones opcionales que el schema admite:
`aff` (2×3 en mm, registro → ahora), `appearance` (PNG RGBA del recorte), `prompt`, `seq`, `level`.
API: `Tracker::new(canvas, Params::default())` `.with_detector/segmenter/llm/appearance_dir` · `update(&CleanFrame)
→ Report{events, unclaimed_zones, ambiguous, n2b_ms, log}` · `register_all(&CleanFrame)` (nivel 5) · `add_object(_polygon_mm)` · `objects()`.

| Nivel | `seguidor_v4.py` (02:09) | Rust | Umbrales (iguales) |
|---|---|---|---|
| 0 | `absdiff(blur5(gris)) > umbral`, open 5, close 21 | `raster::{absdiff_gt, open_sq, close_sq}` | 24 · frac 0.2 · min_zona 500 |
| 1 | `matchTemplate(TM_CCOEFF_NORMED, mask)` | `ncc::masked_zncc` | igual 0.85 |
| 2 | `ORB_create(800,1.2,6,11,21)` + knn 0.8 + `estimateAffinePartial2D` | `orb` propio + `geom::ransac_similarity` | ≥10 inliers · 4 px · \|s−1\|≤0.15 |
| 2b | (nivel por color de `seguidor_objetos.py`/`seguidor_v2.py`: hist HSV 18×8 + `compareHist` + área) | `Tracker::n2b_candidates` + `raster::{blobs, BinLut, open_sq_fast, fill_largest_fast}` | `n2b_*`: sim>0.6 · 0.4<ratio<2 · margen 0.25 · dentro del área ≥0.9 |
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

**Nivel 2b (R4), re-ubicar sin modelos:** si N2 no calza (arrugado/doblado, o color liso sin puntos para ORB), cada
zona cambiada sin dueño es un candidato: su contorno (apertura 5 + componente mayor rellena) es la **máscara nueva**
y se compara con la referencia por histograma HSV (`HistMetric::Correl` como el original, o `Bhattacharyya`), razón
de área y posición (dentro del área útil; `n2b_max_dist_mm` opcional). Las zonas pegadas al lugar anterior se unen
con la máscara vieja y se les quita lo que ahora muestra la mesa, así sirve también si giró o se arrugó en el lugar.
Un solo candidato bueno (o con ventaja > `n2b_margin`) → `level: "N2b hist (sim 0.98)"`; la afín lleva el centroide
y, si la forma se conservó y es alargada, el giro del eje principal (**módulo 180°**: sin textura no se distingue).
Dos lugares de color parecido → **no asigna**: `Report.ambiguous` + bitácora, y los candidatos pasan a N3/N4 (con
juez decide el LLM; sin juez el objeto queda oculto). `Report.n2b_ms` mide el nivel; `n2b_enabled = false` deja la
cascada original. Límites conocidos: un objeto retirado y otro del mismo color y tamaño puesto en la misma revisión
se confunden (un solo candidato); y el nivel 0 compara grises, así que un cambio de color con el mismo gris no se ve.
De paso, N2 ya no corre el ORB de la foto completa si la referencia no tiene textura (< 10 puntos).

**Rectificador con lente (R5):** `geom::Rectifier` tiene tres modelos, todos con muestreo bilineal:
`new(homografía)` (como antes; `.precompute((w, h))` le arma tabla), `with_radial(homografía, Radial{centro, focal,
k1, k2})` y `from_poly(&Poly2, canvas, (w, h))`. `Poly2::fit(lienzo_px, cámara_px, grado)` es el «polinomio robusto
ajustado al escaneo» de `seguidor_v4.Mapa` (el `lienzo_map.npz` de Daniel; su generador no está en `origin/mesa`):
mínimos cuadrados reponderados (Tukey) que dejan fuera los bultos de los objetos. Los dos últimos precalculan una
**tabla de remapeo** (índice + pesos en 1/256 por píxel, 8 bytes) y `rectify` la aplica por franjas en paralelo;
`valid_area()` da el área útil para `Tracker::with_area`. Ojo con el nombre: `Poly2` es polinomio en 2 variables;
el **grado 2 no alcanza** para una lente con k1 (la radial es cúbica), hay que usar grado ≥ 4 (cifras abajo).

**Modelos** (no se commitean): `models::ensure` los baja a la caché del SO (`~/Library/Caches/pcx/models`,
`%LOCALAPPDATA%\pcx\models`, `~/.cache/pcx/models`; o `PCX_MODELS_DIR`) y verifica sha256 fijo. DINO fp32 (719 MB):
las variantes int8 usan `ConvInteger`, que el EP de CPU de ORT no implementa (probado).

## Cifras (M1 Pro, `cargo bench -p pcx-objects`, lienzo 1280×720, netas de construir el seguidor ≈20 ms)
N0 escena quieta **≈16 ms** · N0+N1 (cambio de luz, «sigue igual») **≈46 ms** · N0+N1+N2 objeto movido **≈123 ms**
(ORB de imagen completa 58 ms). Python estimaba ~ms / ~ms / ~0.2 s. Nivel 3 (ONNX, test `#[ignore]`): ver abajo.
Niveles 4–5 reales: Sonnet ~20 s el registro (hallazgo de Daniel); aquí probados solo con mock (sin key).

**N2b y rectificador (medidos en debug con `cargo test -p pcx-objects -- --nocapture`, M1 Pro, 1280×720; no hay
corrida en release: será más rápido, falta medirlo con `cargo bench`):** N2b «arrugado» **≈7 ms** · «girado sin
textura» **≈8 ms** · girado en el lugar **≈5 ms** (solo el nivel, `Report.n2b_ms`; mejor de 3; presupuesto 30 ms).
Rectificar 1920×1080 → 1280×720 con tabla: **4–7 ms** (8 hilos; presupuesto 10 ms), armar la tabla ≈70–80 ms una vez.
Lente simulada k1 = −0.08 (f = 1400 px, escaneo 48×27 con ruido ±0.1 px y ~6 % de bultos), error contra la verdad:
solo homografía rms 8.28 / máx 29.7 px · `Poly2` grado 2: 3.18 / 14.3 · grado 3: 0.13 / 1.15 · **grado 4: 0.009 /
0.055 px** · grado 5 igual. `with_radial` usa el modelo exacto (error de foto 0.8/255 en el test).

NIVEL3_CIFRAS

## Tests
`cargo test -p pcx-objects`: escenas sintéticas (quieto, luz, traslación, rotación 30°, desaparece/reaparece,
intercambio, objeto nuevo sin dueño, arrugado → N3 (con `n2b_enabled = false`: con N2b lo resuelve antes), ambiguo →
N4 con mock, registro N5 con mock) + unidades. `tests/n2b.rs` (1280×720, sin modelos): arrugado → N2b, girado sin
textura (movido 40° y en el lugar 60°), dos objetos de color parecido → ambiguo sin asignar (y con juez: N4 + N2b),
colores distintos intercambiados, N2b apagado. `geom`: lente k1 con `Poly2` y tiempo/exactitud de la tabla.
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
