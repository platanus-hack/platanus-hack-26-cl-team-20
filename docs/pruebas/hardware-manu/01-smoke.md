# QA smoke-01 · hardware real (21-nov-2026)

MacBook Pro M1 Pro, macOS 26, rama `manu/plataforma`. Proyector T749 (HDMI, 1920×1080 a 60 Hz) y webcam Logitech C920, ambos apuntando al techo, que tiene una viga.

| # | Prueba | Resultado | Cifra |
|---|---|---|---|
| 1 | Renderer en el T749 (7 widgets v2, tildes, un diff cada 250 ms) | OK | 60,00 fps; frame p50 16,7 ms, p95 19,4 ms; 140/140 `presented`; 0 ventanas fuera del T749 |
| 2 | Captura C920 con `pcx-capture uvc` | **Falla** | «Cannot fulfill request» (B6); nokhwa se cae (B7, B8) |
| 2 | Captura C920 con fuente AVFoundation de reemplazo → `pcx-shm` | OK | 720p 30,3 fps y 1080p 30 fps; retraso interno de AVFoundation p50 52–60 ms |
| 2b | Anillo `pcx-shm` | OK | escritura→lectura p50 0,23 ms, p95 0,68 ms; 0 saltos de `seq` |
| 2c | Exposición y balance de blancos | No se pueden fijar | B9 |
| 3 | Motion-to-photon (24 transiciones) | OK | p50 **215 ms**, p95 255, máx. 261 (Max: 313 ms); `t_present`→cámara p50 205 ms |
| 4 | ArUco DICT_4X4_100 | Parcial | Directo 0. Patrón − inverso (60+60 cuadros): 16/24 (~74 px), 24/40 (~45 px), 16/60 (~29 px). Homografía única 6–7 px; separando por la viga 2–4 px |
| 5 | Manos en vivo (mano del usuario, 30 s) | OK; toques falsos | 30 fps, proceso p50 15,6 ms; mano en el 95–97 % de los cuadros, score 0,99, sin cambios de ID. Los «toques» son falsos: una sola cámara cenital no mide contacto |

Contraste del montaje: 1,16:1 (luz fuerte en el encuadre y exposición no fijable). Con SNR ≈ 70 sigue siendo usable si se promedia y se resta.

## Bugs

- **B1 (crítico)**: `pcx-renderer --display T749` abre en otro monitor. winit 0.30.13 en macOS nombra «Monitor #<modelo>» y el fallback elige el primer monitor que no es el principal (`renderer/src/main.rs:205-213`). En arreglo (Codex, rama `codex/renderer-fixes`).
- **B2**: `t_present_ms` se toma en `present()` y no cuando la imagen se ve (luz +205 ms). Usar `addPresentedHandler` de Metal.
- **B3**: la primera escena se presenta entre 0,57 y 2,4 s tarde, porque el WebSocket conecta antes de que exista la ventana o la GPU.
- **B4**: `uvc:<n>` abre por índice. La cámara del iPhone (Continuity) entra y sale y cambia el orden. Abrir por uniqueID.
- **B6 (bloqueante)**: `uvc.rs:17-18` pide MJPEG. La C920 en AVFoundation solo ofrece 420v (30 fps) y yuvs (≤10 fps a 720p).
- **B7**: nokhwa 0.10.11 mezcla formato y rango de fps (`nokhwa-bindings-macos lib.rs:1040-1085`) y lanza NSInvalidArgumentException.
- **B8**: `set_control` de nokhwa usa el selector `exposureMode:` en lugar de `setExposureMode:`, y el proceso cae.
- **B9**: la exposición «locked» no tiene efecto y no hay modos de balance de blancos. Hacen falta controles UVC directos.
- **B10**: nokhwa clasifica 420v (NV12) como YUYV.

**Arreglo sugerido para la captura**: una fuente nativa por SO. En macOS, AVFoundation que abra por uniqueID, use 420v con `range.minFrameDuration`, reaplique el formato tras `startRunning` y entregue BGR (ver `scripts/c920cap.swift`, 30 fps a 720p y 1080p). En Windows, Media Foundation. En Linux, V4L2.

Evidencia y scripts: `qa-runs/smoke-01/{logs,evidencia,scripts}`.
