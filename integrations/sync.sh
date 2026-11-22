#!/usr/bin/env bash
# Copia las skills desde la fuente única (integrations/skills) al plugin de Claude Code
# (integrations/claude-plugin/skills). Un plugin instalado se copia a una caché y no puede
# leer fuera de su carpeta, por eso el plugin lleva una copia.
#
#   integrations/sync.sh           copia (borra en el destino lo que ya no existe en la fuente)
#   integrations/sync.sh --check   no escribe; sale con 1 si la copia está desactualizada (para CI)
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
src="$here/skills"
dst="$here/claude-plugin/skills"

if [ ! -d "$src" ]; then
  echo "sync: no existe $src" >&2
  exit 2
fi

if [ "${1:-}" = "--check" ]; then
  if diff -rq -x .DS_Store -x __pycache__ "$src" "$dst" >/dev/null 2>&1; then
    echo "sync: el plugin está al día con integrations/skills"
    exit 0
  fi
  echo "sync: integrations/claude-plugin/skills difiere de integrations/skills. Corre integrations/sync.sh" >&2
  diff -rq -x .DS_Store -x __pycache__ "$src" "$dst" >&2 || true
  exit 1
fi

rm -rf "$dst"
mkdir -p "$dst"
# cp -R portable (sin rsync): copia cada skill completa
for skill in "$src"/*/; do
  name="$(basename "$skill")"
  if [ ! -f "$skill/SKILL.md" ]; then
    echo "sync: $name no tiene SKILL.md; se omite" >&2
    continue
  fi
  cp -R "$skill" "$dst/$name"
done
find "$dst" -name .DS_Store -delete 2>/dev/null || true
find "$dst" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
echo "sync: $(find "$dst" -name SKILL.md | wc -l | tr -d ' ') skills copiadas a integrations/claude-plugin/skills"
