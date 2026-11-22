#!/bin/sh
# capturar.sh <pagina.html> <t en segundos> <salida.png> [escala]   (Chrome headless, 1340×710, animaciones congeladas en t)
D="$(cd "$(dirname "$0")" && pwd)"
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --hide-scrollbars --force-device-scale-factor=${4:-1} --screenshot="$D/$3" --window-size=1340,710 --virtual-time-budget=6000 "file://$D/$1?t=$2" 2>/dev/null
