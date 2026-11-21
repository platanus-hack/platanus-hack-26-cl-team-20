#!/usr/bin/env bash
# Genera contracts/ts/pcx.d.ts desde contracts/schema/pcx.ts.json (draft-07 con *_ns como string) (sin instalación global).
# Requiere haber corrido antes: (cd contracts && uv run python -m pcx_contracts.export)
set -euo pipefail
cd "$(dirname "$0")"
npx -y json-schema-to-typescript@15 \
  -i ../schema/pcx.ts.json \
  -o pcx.d.ts \
  --unreachableDefinitions \
  --bannerComment "/* Generado desde contracts/schema/pcx.ts.json. No editar: corre contracts/ts/generate.sh */"
echo "ok -> $(pwd)/pcx.d.ts"
