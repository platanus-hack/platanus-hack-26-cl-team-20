#!/bin/bash
# Demo del techo en bucle: cámara C920 → anillo pcx.cam.c920, renderer SOLO en el T749 (con winguard) y driver.
D=${PCX_DEMO_DIR:-$(cd "$(dirname "$0")" && pwd)}
QA=${PCX_QA_DIR:?define PCX_QA_DIR con los scripts de captura (c920cap, winguard)}
pgrep -f "c920cap --size" >/dev/null || "$QA/scripts/start_ring.sh" 1280x720 demo
if ! pgrep -f "target/release/pcx-renderer --display" >/dev/null; then
  "$QA/scripts/run_renderer.sh" 9242 demo || exit 3
  sleep 3
fi
# El driver carga pcx-hands (onnxruntime); a veces queda colgado en la compilación de CoreML (MLLoader):
# si en 20 s no está esperando al renderer, se mata y se reintenta (máx. 6 veces).
for intento in 1 2 3 4 5 6; do
  pkill -9 -f "demo-techo-bin run" 2>/dev/null; sleep 1
  nohup "$D/demo-techo-bin" run --port 9242 --assets "$D/assets" --logs "$D/logs" > "$D/logs/demo.out" 2> "$D/logs/demo.err" &
  PID=$!
  for _ in $(seq 1 40); do grep -q "esperando al renderer" "$D/logs/demo.err" 2>/dev/null && break; sleep 0.5; done
  if grep -q "esperando al renderer" "$D/logs/demo.err"; then
    echo "demo corriendo (pid $PID, intento $intento). Para detenerla: $D/stop.sh"; exit 0
  fi
  echo "intento $intento: el driver no arrancó (onnxruntime/CoreML colgado); reintento"
done
echo "ERROR: el driver no arrancó"; exit 1
