#!/bin/bash
# Detiene la demo del techo: driver, renderer del T749, winguard y captura de la C920.
pkill -f "demo-techo-bin run"
pkill -f "target/release/pcx-renderer --display"
pkill -f "smoke-01/scripts/winguard"
pkill -TERM -f "c920cap --size"; sleep 1; pkill -f "qa-tool ringfeed"; sleep 0.3
pgrep -fl "demo-techo-bin|pcx-renderer --display|winguard|c920cap|ringfeed" || echo "demo detenida: no quedan procesos"
