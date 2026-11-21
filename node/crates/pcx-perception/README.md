# pcx-perception

Percepción de pcx. Por ahora solo la **foto limpia** (`src/clean.rs`, tarea R1): la mesa sin proyección encima.
El binario y el worker de objetos llegan con R2.

```rust
let photo = capture_clean(&mut ring_reader, &mut control, Some(&mask), &CleanConfig::default())?;
// photo.metrics: tiempos, caída y dispersión entre cuadros; photo.into_rgb(): w × h × 3 para pcx-objects
```

- `FrameSource`: de dónde salen los cuadros; ya está implementado para `pcx_shm::RingReader`.
- `BlankControl`: `request_blank(ms) -> request_id` y `poll_blanked(request_id)` (sin bloquear).
- `Mask`: zona que ilumina el proyector en la imagen de cámara. Opcional, pero conviene pasarla.

## Cómo elige los cuadros

1. Pide `blank{ms: 300}` y lee cada cuadro nuevo. La referencia de luminancia es el último cuadro anterior a
   `min_latency_ms` (80 ms): lo que el contenido cambie hasta ahí no cuenta como apagón.
2. Apagón claro: caída ≥ `max(8 niveles, 25 % de la referencia)`. Usa la meseta más profunda (cuadros a 2 % de la
   caída del mínimo, contiguos o no), deja fuera los bordes y sale con 3. No espera `blanked`.
3. Caída menor: espera `blanked` y decide por su ventana + la latencia esperada (215–260 ms), solo si la caída
   coincide con esa ventana.
4. Sin caída: `BlackoutNotSeen`, salvo `assume_dark_when_flat`. Los errores llegan al vencer el plazo (800 ms).

Límites: una atenuación del contenido de más del 25 % entre los 80 ms y la llegada real de la oscuridad no se
distingue de un apagón: congela la escena antes de pedir la foto. Bloquea la exposición en captura; sin máscara y
con autoexposición la selección es más débil y la foto queda con la ganancia del apagón.

## Cifras (`cargo test -p pcx-perception -- --nocapture`, simulado a 30 fps)

- Latencia de 100 a 400 ms, 5 fases, ruido σ = 2: siempre cuadros del apagón; error medio ≤ 1,1 niveles.
- De `blank` a foto: latencia + ≤ 120 ms. Queda en ≤ 350 ms hasta ~225 ms de latencia; con 260 ms son 367 ms.
- Mediana de 3 cuadros de 1280×720×3 (`-- --ignored`): 8–12 ms sin optimizar.
