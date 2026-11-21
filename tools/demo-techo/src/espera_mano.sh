#!/bin/bash
# Espera manos (filas nuevas en latencia_mano.csv) hasta 180 s y saca 4 fotos de evidencia de la fase (c).
D=${PCX_DEMO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}
Q=$D/../smoke-01/scripts/qa-tool/target/release/qa-tool
PY=$D/../smoke-01/.venv/bin/python
cd "$D"
n0=$(wc -l < logs/latencia_mano.csv); k=0
for i in $(seq 1 360); do
  n=$(wc -l < logs/latencia_mano.csv)
  if [ "$n" -gt $((n0+5)) ]; then
    k=$((k+1)); "$Q" snap --ring c920 --avg 3 --out16 /tmp/qa_c$k.png >/dev/null
    "$PY" -I src/foto.py /tmp/qa_c$k.png calib/ref_negro16.png evidencia/c_mano_$k.jpg 2>/dev/null
    "$Q" snap --ring c920 --out /tmp/qa_c${k}_raw.png >/dev/null
    "$PY" -I -c "import cv2; cv2.imwrite('evidencia/c_mano_${k}_cruda.jpg', cv2.imread('/tmp/qa_c${k}_raw.png'), [cv2.IMWRITE_JPEG_QUALITY, 85])" 2>/dev/null
    n0=$n; [ $k -ge 4 ] && break; sleep 2
  fi
  sleep 0.5
done
echo "fotos con mano: $k · filas de latencia: $(($(wc -l < logs/latencia_mano.csv)-1))"
rm -f /tmp/qa_c*.png
