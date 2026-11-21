#!/bin/sh
# Renderiza los diagramas UML a PNG, SVG y un PDF combinado.
set -e
cd "$(dirname "$0")"
for f in 1-sistema 2-nodo; do
  dot -Tpng -Gdpi=170 "$f.dot" -o "$f.png"
  dot -Tsvg "$f.dot" -o "$f.svg"
  rsvg-convert -f pdf -o "$f.pdf" "$f.svg"
done
rsvg-convert -f pdf -o arquitectura-pcx.pdf 1-sistema.svg 2-nodo.svg
