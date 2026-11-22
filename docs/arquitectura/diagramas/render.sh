#!/bin/sh
# Diagramas de arquitectura de pcx. Fuente: generar.py -> SVG -> PNG + PDF combinados.
#   vista simple (v2):  1-vista-general … 5-percepcion          -> arquitectura-pcx.pdf
#   láminas técnicas:   tecnico-1-sistema, tecnico-2-computador -> arquitectura-pcx-tecnico.pdf
#   lámina para PPT:    slide-arquitectura (16:9, PNG a 3x = 3840×2160) -> slide-arquitectura.pdf
# Los de la primera versión están en v1/.
set -e
cd "$(dirname "$0")"
python3 generar.py
SIMPLE="1-vista-general 2-computador 3-agentes 4-instalacion 5-percepcion"
TECNICO="tecnico-1-sistema tecnico-2-computador"
for f in $SIMPLE $TECNICO; do
  rsvg-convert -z 2 "$f.svg" -o "$f.png"
done
rsvg-convert -z 3 slide-arquitectura.svg -o slide-arquitectura.png
rsvg-convert -f pdf -o arquitectura-pcx.pdf $(for f in $SIMPLE; do printf '%s.svg ' "$f"; done)
rsvg-convert -f pdf -o arquitectura-pcx-tecnico.pdf $(for f in $TECNICO; do printf '%s.svg ' "$f"; done)
rsvg-convert -f pdf -o slide-arquitectura.pdf slide-arquitectura.svg
