#!/bin/bash
# Relanza el servidor del feed cuando muere (p. ej. la Osmo se desconectó y la enumeración de cámaras quedó vieja).
cd /Volumes/Externo/sl-scanner
while true; do
  .venv/bin/python -m playground.superficie_feed "$@"
  echo "feed terminó rc=$? $(date +%H:%M:%S); relanzo en 3 s"
  sleep 3
done
