#!/bin/bash
# Lanza el runtime del proyector de la mesa SOLO cuando existe el display del proyector y lo relanza si muere.
# Mientras el proyector no está, no abre ninguna ventana (así nunca cae sobre los monitores de Max).
# El display del proyector se detecta solo: un display 1920x1080 que NO sea uno de los dos monitores de trabajo
# (orígenes (0,0) y (1920,0)); sirve para AirPlay (28,1080) o HDMI (el origen que macOS le dé).
# Se puede forzar con SLSCAN_SCREEN="1920x1080@X,Y".
#   nohup scripts/runtime_loop.sh captures/dale-20261102-1511 > logs/runtime_loop.log 2>&1 &
cd /Volumes/Externo/sl-scanner || exit 1
SCAN=${1:-captures/dale-20261102-1511}
export SLSCAN_SCAN="$SCAN" TOUCHSYNTH_MIDI=mac
detectar_proyector() {
  .venv/bin/python - <<'EOF'
import os, sys, Quartz
forzado = os.environ.get("SLSCAN_SCREEN")
_, ids, _ = Quartz.CGGetActiveDisplayList(16, None, None)
disp = [(int(Quartz.CGDisplayBounds(i).origin.x), int(Quartz.CGDisplayBounds(i).origin.y),
         int(Quartz.CGDisplayBounds(i).size.width), int(Quartz.CGDisplayBounds(i).size.height)) for i in ids]
if forzado:
    x, y = (int(v) for v in forzado.split("@")[1].split(","))
    ok = any((d[0], d[1]) == (x, y) for d in disp)
    print(forzado if ok else ""); sys.exit(0)
trabajo = {(0, 0), (1920, 0)}
cand = [d for d in disp if (d[0], d[1]) not in trabajo]
# cualquier tamaño: si el proyector llega a otra resolución (AirPlay a veces negocia 1600x1200) igual se usa; la
# calibración métrica exige 1920x1080, así que el runtime lo anota y hay que corregir el modo del display aparte
print(f"{cand[0][2]}x{cand[0][3]}@{cand[0][0]},{cand[0][1]}" if len(cand) == 1 else "")
EOF
}
while true; do
  SCREEN=$(detectar_proyector)
  if [ -n "$SCREEN" ]; then
    echo "$(date +%T) proyector presente en $SCREEN: lanzo runtime"
    SLSCAN_SCREEN="$SCREEN" .venv/bin/python -m playground serve --scan "$SCAN" --projector --port 8790
    echo "$(date +%T) runtime terminó rc=$?"
    sleep 3
  else
    sleep 5
  fi
done
