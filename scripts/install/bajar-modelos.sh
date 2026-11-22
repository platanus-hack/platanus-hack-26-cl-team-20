#!/usr/bin/env bash
# Baja los pesos de scripts/install/modelos.json a DEST y verifica el tamaño y el sha256 de cada archivo. Deja:
#   DEST/*.onnx · DEST/LICENSES/* · DEST/manifest.json (copia de modelos.json) · DEST/SHA256SUMS
# Lo usa release.yml (job «modelos») para meter models/ en cada paquete. Necesita bash, curl, jq y sha256sum o shasum.
#
#   scripts/install/bajar-modelos.sh DEST
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
SRC="$here/modelos.json"
DEST="${1:-}"
[ -n "$DEST" ] && [ $# -eq 1 ] || { echo "uso: $0 DEST" >&2; exit 2; }
for c in curl jq; do
  command -v "$c" >/dev/null 2>&1 || { echo "falta $c" >&2; exit 1; }
done

sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

mkdir -p "$DEST"
sums="$DEST/SHA256SUMS.tmp"
: > "$sums"
while IFS=$'\t' read -r nombre url want bytes; do
  case "$nombre" in /*|*..*|'') echo "::error::nombre inválido en modelos.json: '$nombre'"; exit 1 ;; esac
  case "$url" in https://*) ;; *) echo "::error::$nombre: la URL debe ser https"; exit 1 ;; esac
  dst="$DEST/$nombre"
  mkdir -p "$(dirname "$dst")"
  curl -fsSL --proto '=https' --retry 3 --retry-all-errors -o "$dst.part" "$url"
  got="$(sha256 "$dst.part")"
  size="$(wc -c < "$dst.part" | tr -d ' ')"
  if [ "$got" != "$want" ] || [ "$size" != "$bytes" ]; then
    rm -f "$dst.part"
    echo "::error::$nombre no coincide: sha256 $got (esperado $want), $size bytes (esperado $bytes)"
    exit 1
  fi
  mv "$dst.part" "$dst"
  printf '%s  %s\n' "$got" "$nombre" >> "$sums"
  echo "ok  $nombre  $size bytes  $got"
done < <(jq -r '(.modelos + .licencias)[] | [.nombre, .url, .sha256, (.bytes | tostring)] | @tsv' "$SRC")

# El motor busca cada modelo por nombre y sha256 propios (pcx-objects, pcx-hands). Si no calzan con este manifiesto,
# ignoraría el archivo del paquete y lo volvería a bajar: se avisa para alinear las dos listas.
while IFS=$'\t' read -r nombre want; do
  if ! grep -rqF "$want" "$repo/node/crates" 2>/dev/null; then
    echo "::warning::el sha256 de $nombre no aparece en node/crates: el motor no usará la copia del paquete"
  fi
done < <(jq -r '.modelos[] | [.nombre, .sha256] | @tsv' "$SRC")

cp "$SRC" "$DEST/manifest.json"
sort -k2 "$sums" > "$DEST/SHA256SUMS"
rm -f "$sums"
du -sh "$DEST"
