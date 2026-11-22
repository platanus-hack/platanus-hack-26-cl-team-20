# pcx-calib · calibración cámara ↔ proyector ↔ mesa (R5)

Rust puro (nalgebra, sha2, serde): no usa OpenCV y compila en macOS, Windows y Linux. Implementa `calibrate{mode}` del contrato:
- **quick**: grilla de 40 marcadores `pcx6` propios (6x6 celdas, 64 ids con Hamming ≥ 4 en todas las rotaciones). Se proyecta el par patrón/inverso y se binariza por signo, lo que cancela la luz ambiente y el albedo. Las esquinas son subpíxel. Luego DLT normalizado + RANSAC para cámara→proyector, y el marco `table_mm` sale de marcadores impresos (`MetricRef::PrintedMarkers`, mejor si están repartidos) o de `MmPerProjPx`.
- **precise**: gray code de Max con el método de Daniel:
  - solo los bits útiles (franja ≥ 2,5 px de cámara);
  - umbral relativo `0,22 × contraste` y auto-skip;
  - refinado sub-bloque con vecinos coherentes;
  - streaming a ~2,5 cuadros por patrón, con 2 parches de sincronía que codifican `índice mod 4`. Se usa el cuadro limpio del centro de cada racha y se re-proyecta solo lo que falta. `SyncMode::Change` acepta el cuadro cuando la cámara ve el cambio y se estabiliza.
  Salidas: LUT proj→cam por bloques (sin `griddata`, sin mezclar a través de saltos de profundidad), `.npy` f32 H×W×2 + `LutDescriptor` (sha256), homografía aproximada (RANSAC) y **lienzo rectificado**. El lienzo es un polinomio cúbico robusto proj→cam, como el `lienzo_map` de Daniel: corrige la lente e ignora los bultos.
- **verify**: 12 cuadrados de 36 mm, cada uno con su propio par patrón/inverso para evitar confundir identidades tras una deriva. Se exige cobertura ≥10/12. El centro de cada uno sale de la intersección de diagonales y se lleva a mm con la calibración guardada. Si la mediana supera el umbral (2 mm), se emite `CalibrationDrift`.

**API**: `CalibRun::new(Mode, CalibConfig, Option<Calibration>)`. Se avanza con `.step(&mut dyn PatternSink, &mut dyn FrameGrab) -> Step` (QuickGrid → Metric → ChooseBits → GrayCapture → Decode → BuildLut, o VerifySquares) o se corre entero con `.run()`, que devuelve `CalibOutcome {calibration, transforms: Vec<Transform>, drift, lut, approx_h_pc, canvas, report}`.
- `PatternSink { size(); show(&Pattern) -> t_ns; release() }` (patrón a pantalla completa en px nativos) y `FrameGrab { grab(not_before_ns) -> Frame }` (siguiente cuadro nuevo a partir de un tiempo).
- `Calibration` (serde) guarda `h_cam_proj`, `h_table_proj` y `residual_mm`.

## Cifras (simulador, `cargo test -p pcx-calib --release -- --nocapture`, M-series)
Escenario: proyector de 1024x768 a 1,15 m, cámara de 640x480 con k1 = −0,08, ruido σ 2, ambiente 40 DN, latencia de 120 ± 8 ms a 30 fps, caja de 90 mm y 0,4 px de cámara por px de proyector. «Adquisición» se mide con el reloj de la cámara; «cómputo» con el de pared.

| Modo / medida (contra la verdad sintética) | p50 | p95 | Tiempo |
|---|---|---|---|
| quick: `table_mm→proj` en la mesa (35/40 marcadores, 4 impresos) | 1,08 px proy · 0,78 mm | 1,78 px · 1,31 mm | 0,40 s adq + 5 ms |
| precise, LUT en la mesa | 0,25 px cám · 0,45 mm | 0,49 px · 0,88 mm | 3,57 s adq + 0,19 s (28 patrones, 0 reintentos) |
| precise, LUT **sobre la caja** (paralaje) | 0,31 px · 0,50 mm | 2,35 px · 3,82 mm | (p95 = borde de la caja) |
| precise, homografía aprox. sobre la caja | 8,47 px · **13,8 mm** | 9,15 px · 14,9 mm | la LUT corrige ~27× |
| precise, lienzo (polinomio) en la mesa / homografía | 0,03 / 0,39 px cám | 0,06 / 0,95 px | lente corregida |
| precise con `SyncMode::Change` | — | — | 6,03 s adq + 0,17 s |
| verify quieto (12 cuadrados) | 0,87 mm · 1,23 px proy | 10,3 mm (cuadrado sobre la caja) | 0,40 s + 1 ms |
| verify tras mover el proyector 13 mm | 13,9 mm → `calibration_drift` | 18,4 mm | |

Cobertura de la LUT: 99,5 %. Control independiente (LUT contra esquinas de marcadores): 0,26 / 0,53 px. Los tiempos son de punta a punta sin el arranque: quick ≈ 0,4 s, precise ≈ 3,8 s (Daniel: 4,4 s con 320 ms de latencia real).

## Prueba en hardware (Mac del montaje; NO se ejecutó)
Requiere `scripts/manos_mesa.py` (memoria compartida `/tmp/mesa/cenital_*.npy`) y el runtime en `:8790` (comando `overlay`). No abre cámara ni ventanas.
```bash
cargo run --release -p pcx-calib --example calib_hw -- quick      # o --marker 60:X:Y:LADO (print-marker 60 m.bmp)
cargo run --release -p pcx-calib --example calib_hw -- precise
cargo run --release -p pcx-calib --example calib_hw -- verify     # quieto: ok; luego mover el proyector: drift
```
Reportar de `/tmp/mesa/pcx-calib/report_*.json`:
- quick: `quick.markers_seen` y `residual_mm` (p50/p95);
- precise: `precise.stream` (`latency_ms`, `frame_period_ms`, `retried`), `decode.valid/lit`, `lut_coverage` y `check_cam_px`;
- verify: `verify.err_mm`; todos: `acquisition_ms`, `compute_ms` y `wall_s`. Revisar también la LUT `.npy`.

## Propuestas de cambio de contrato (definidas aquí, sin tocar `contracts/`)
1. **Patrón de calibración**: render `pattern{request_id, width, height, path|shm, format: "gray8"}`, a pantalla completa en px nativos encima de la escena, con respuesta `pattern_presented{request_id, t_present_ms}` y `pattern_end{}`. Hoy son `io::Pattern` y `PatternSink`.
2. **Eventos en Rust** (hecho en v2.1): `calib::CalibrationDrift::to_event()` da `pcx_contracts::Event::CalibrationDrift`; `Pattern::to_render()` da el `pattern` v2.1.
3. **`calibration_done{mode, versions, residual_mm_p50, residual_mm_p95, coverage, acquisition_ms, compute_ms}`**: hoy no hay evento de fin.
4. **Semántica de la LUT**: precise publica `Lut proj:<id> → cam:<id>` para la percepción (POV sin paralaje); el renderer sigue con `table_mm → proj` (homografía). Proponer un marco `plane:<proj>` para el lienzo (plano de la mesa sin bultos) y residuos p50/p95 en vez de un solo `residual_mm`.
5. **`Calibrate`**: agregar `metric_ref` opcional (marcadores impresos con esquinas en mm) en vez de solo configuración del nodo.
