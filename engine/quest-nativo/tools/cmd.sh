#!/usr/bin/env bash
# Manda un comando a la app del casco y muestra la respuesta del log.
#   tools/cmd.sh estado
#   tools/cmd.sh calibrar
#   tools/cmd.sh "ajuste 2 0 0 1"
#   tools/cmd.sh traer            # baja calib_proyector.json y las carpetas cal_* a ./descargas/
set -euo pipefail
ADB=${ADB:-/Users/max/Library/Android/sdk/platform-tools/adb}
export ANDROID_SERIAL=${ANDROID_SERIAL:-192.168.4.161:5555}
if ! "$ADB" get-state >/dev/null 2>&1; then
  "$ADB" connect "$ANDROID_SERIAL" >/dev/null 2>&1 || true
fi
APP=com.smartup.proyector
DIR=/sdcard/Android/data/$APP/files
AQUI="$(cd "$(dirname "$0")/.." && pwd)"

if [[ "${1:-}" == "traer" ]]; then
  mkdir -p "$AQUI/descargas"
  "$ADB" pull "$DIR" "$AQUI/descargas/" >/dev/null
  echo "en $AQUI/descargas/files"
  exit 0
fi

"$ADB" logcat -c
"$ADB" shell am broadcast -a $APP.CMD -p $APP --es c "'$*'" >/dev/null
sleep 0.6
"$ADB" logcat -d -s Proyector:* | grep -F "[cmd]" | tail -3
