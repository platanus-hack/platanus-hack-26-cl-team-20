# 02 · Cableado (`pcx up` + captura nativa + worker de manos) — sáb 21-nov

**Estado: software probado, hardware pendiente.** El T749 y la C920 se desconectaron antes de la prueba (`system_profiler` solo ve la FaceTime y el iPhone, que no se tocan). `pcx cameras` las lista sin abrirlas.

## Sin hardware (`cargo test -p pcxd --test cableado`, debug, M1 Pro)
`pcx up` lanza core + `pcx worker capture` (replay de las 9 fotos de `pcx-hands/testdata`, 640x480 a 15 fps) + `pcx worker hands`:
- MCP `project` → el cliente de `/render` recibe el `scene_diff` con el widget; `/state.hands` se llena desde el worker (otro proceso).
- Carga de modelos del worker (CPU, debug): 1,67–1,79 s en 10 arranques seguidos, 0 cuelgues.
- SIGKILL al worker: el supervisor lo relanza en 1,0 s (backoff) y vuelven las manos; el core conserva su pid.
- Segundo `pcx up`: falla con «ya hay un `pcx up` corriendo (pid …)». SIGKILL a `pcx up`: core, captura, worker y un hijo ajeno (`sleep` bajo `pcx guard`) terminan en 2,2 s (kqueue), sin `pkill`.
- Workspace: 129 tests en verde; `clippy --workspace --all-targets` sin avisos.

## CoreML
El binario de ORT que baja `ort` para macOS es el `+coreml` y enlaza `CoreML.framework` aunque se pida CPU. El EP de CoreML solo se registra con `--backend coreml` y `pcxd --features coreml`; si la carga se cuelga igual, el worker sale con código 3 a los `--init-timeout-s` (30 s) y el supervisor lo relanza; el core no se entera. Para eliminar CoreML del todo: `ort` con `load-dynamic` y una `libonnxruntime` solo CPU.

## Prueba en hardware (lista para correr al reconectar)
```bash
R=/Users/manuelblanco/Projects/platanus_hack_26; QA=$R/qa-runs/smoke-01/scripts
$R/qa-runs/demo-techo/stop.sh                              # 1. libera cámara y proyector
cargo build --release -p pcxd -p pcx-renderer              # target compartido
./target/release/pcx cameras                               # 2. debe aparecer 0x1120000046d0892 (C920); no abre nada
./target/release/pcx-renderer --list-displays              #    T749: anotar bounds x y w h
./target/release/pcx up --config node/pcx.mac-c920.toml 2> /tmp/pcx-up.log &   # 3.
$QA/winguard $(jq .procs.renderer.pid /tmp/pcx/pcx-up.json) <x> <y> <w> <h> &  #    ventana solo en el T749
./target/release/pcx-capture peek --id c920 --secs 10      # 4. fps reales (esperado ≈ 30 a 720p)
grep "worker hands\]" /tmp/pcx-up.log | tail -3            # 5. fps procesados, % con manos, ms p50/p95
PCX_TOKEN=pcx-dev-token ./target/release/pcx mcp           # 6. desde Claude Code: project label «Hola techo»
kill -9 $(jq .procs.hands.pid /tmp/pcx/pcx-up.json)        # 7. medir tiempo hasta «hands: iniciado» y manos nuevas en /state
kill %1                                                    # 8. SIGTERM a pcx up: todo se cierra
PCX_QA_DIR=$QA $R/qa-runs/demo-techo/start.sh              # 9. relanzar la demo del techo
```
Anotar: fps de captura (peek), `min_dur` del log de `cam` (0,0333 s), retraso PTS→llegada, fps y ms del worker, tiempo de reinicio, ventanas fuera del T749 (debe ser 0) y si la exposición fija tuvo efecto (B9).
