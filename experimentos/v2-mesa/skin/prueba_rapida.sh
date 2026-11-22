#!/bin/bash
# Prueba de velocidad: modelo rápido de imagen de Google, 3 estilos, en HD.
cd "$(dirname "$0")"; PY=~/code/platanus-hack-26/sl-scanner/.venv/bin/python
for cfg in "cyber2 gemini-3.1-flash-image 1K" "cyber2 gemini-3.1-flash-image 2K" "coral gemini-3.1-flash-image 2K" "templo gemini-3.1-flash-image 2K"; do
  set -- $cfg; t=$(python3 -c "import time;print(time.time())")
  out=$(IMG_SIZE=$3 $PY gen_google.py $1 $2 entrada-vivo.png 2>&1 | grep -E "cruda|ok|error|Error|HTTP" | tr '\n' ' ')
  d=$(python3 -c "import time;print('%.1f'%(time.time()-$t))"); cp salidas/google-$1.png salidas/rapido-$1-$3.png 2>/dev/null
  echo "$1 $2 $3 -> $d s · $out"
done
echo FIN
