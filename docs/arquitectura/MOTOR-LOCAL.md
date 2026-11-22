# Motor local (`pcx motor`) — decisión del sáb 21-nov, 17:16

Decisión del equipo (transcripción `docs/transcripciones/2026-11-21-1716-arquitectura-camara-proyector.md`, rama
`daniel/consola`): el cómputo corre en el computador de la persona, en un proceso Rust que levanta un servidor
local; la web app se conecta a él por una URL configurable (hoy local, mañana remota vía proxy).

```
cámara ──► pcx motor (Rust, 127.0.0.1:9230) ──WS: coordenadas──► web app (consola) ──► 2.ª pantalla ──► proyector
              foto limpia + Perceptor            ◄── calib, hands, blanked ──
```

| Pieza | Dónde | Qué hace |
|---|---|---|
| Servidor | `pcxd/src/motor/server.rs` | `GET /v1/health`, WS `/v1/ws`; token, `Host`, `Origin`, CORS y Private Network Access |
| Orquestador | `pcxd/src/motor/engine.rs` | Actor: disparos (manos fuera + 250 ms, cada 45 s, `perceive`), tabla de objetos con `rev`, `status` |
| Foto limpia | `pcxd/src/motor/clean.rs` | `BlankControl` sobre el WS + compuerta: solo cuadros posteriores a `blanked`; usa `pcx_perception::capture_clean` |
| Cuadros | `pcxd/src/motor/frames.rs` | Cámara propia con `pcx-capture` (AVFoundation en macOS) o `need_frame` → `frame` de la web app |
| Percepción | `pcx-perception/src/pipeline_api.rs` | Trait `Perceptor`; hoy `DiffOnly` (respaldo), después `Pipeline` (cascada + SAM) |

- **Unidades:** los `*_mm` son los del mundo de la web app (`matrix_px_to_mm` de su calibración, con su modelo de
  lente `k1/k2`).
- **Arranque:** `pcx up` (proceso `motor` de `node/pcx.toml`) o `pcx motor`. Genera `~/.pcx/motor.token` (0600) e
  imprime la URL de emparejamiento `https://pcx-gateway.onrender.com/?motor=ws://127.0.0.1:9230&motor_token=…`.
- **Relación con el core:** el motor no depende de `pcx serve`; las manos las detecta la web app (MediaPipe) y le
  avisa con `hands`. El core, el renderer nativo y los workers siguen disponibles para el montaje anterior.
- **Garantía de la foto limpia:** sin `blanked` no hay foto (`blanked_timeout`), y todo apagón termina con `unblank`.

Uso, decisiones de detalle del protocolo, seguridad y tests: `node/crates/pcxd/README-motor.md`.

## Pendiente para la mesa real
1. Probar con la C920 y el T749: latencia real proyector → cámara con el navegador (hoy se asume 215–260 ms) y
   exposición fija (`--lock-exposure`).
2. Cámara compartida: el navegador (MediaPipe) y el motor abren la misma cámara; en macOS suele poder, en Windows no
   (ahí queda el respaldo `needs_frames`).
3. Chrome: permiso de red local desde `https://pcx-gateway.onrender.com` hacia `ws://127.0.0.1:9230`.
4. Enchufar el `Pipeline` real en `motor::default_perceptor()`.
5. Motor remoto vía proxy: hoy `Host` debe ser loopback.
