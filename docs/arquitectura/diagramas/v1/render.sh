#!/bin/sh
# Renderiza los diagramas UML (fuente: SVG escrito a mano) a PNG 2x y a un PDF combinado.
set -e
cd "$(dirname "$0")"
for f in 1-sistema 2-nodo; do
  rsvg-convert -z 2 "$f.svg" -o "$f.png"
done
rsvg-convert -f pdf -o arquitectura-pcx.pdf 1-sistema.svg 2-nodo.svg
