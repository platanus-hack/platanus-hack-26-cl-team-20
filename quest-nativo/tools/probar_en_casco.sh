#!/usr/bin/env bash
# Prueba completa en el casco, sin ponérselo: instala, da permisos, lanza, revisa el log
# (cámara, MRUK, displays/salida, GL), corre la autoprueba del render, camara_debug y una captura.
#
#   tools/probar_en_casco.sh            # si alguien tiene el casco puesto
#   tools/probar_en_casco.sh --prox     # casco sobre la mesa: simula "puesto" y lo revierte al final
set -uo pipefail
ADB=${ADB:-/Users/max/Library/Android/sdk/platform-tools/adb}
export ANDROID_SERIAL=${ANDROID_SERIAL:-192.168.4.161:5555}
APP=com.smartup.proyector
AQUI="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$AQUI/descargas/prueba_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUT"
PROX=0; [[ "${1:-}" == "--prox" ]] && PROX=1

"$ADB" connect "$ANDROID_SERIAL" >/dev/null 2>&1 || true
"$ADB" get-state >/dev/null || { echo "casco no responde por adb ($ANDROID_SERIAL)"; exit 2; }
if [[ $PROX == 1 ]]; then
  "$ADB" shell am broadcast -a com.oculus.vrpowermanager.prox_close >/dev/null
  trap '"$ADB" shell am broadcast -a com.oculus.vrpowermanager.prox_open >/dev/null; echo "prox_open (revertido)"' EXIT
fi

echo "== instalar"
"$ADB" install -r -g "$AQUI/app/build/outputs/apk/debug/app-debug.apk" | tail -1
for p in horizonos.permission.HEADSET_CAMERA android.permission.CAMERA com.oculus.permission.USE_SCENE; do
  "$ADB" shell pm grant $APP $p 2>&1 | sed "s/^/  grant $p: /"
done
"$ADB" shell dumpsys package $APP | grep -E "HEADSET_CAMERA|USE_SCENE|permission.CAMERA" | sed 's/^ */  /'
"$ADB" shell dumpsys display | grep -E 'DisplayDeviceInfo\{"HDMI' | cut -c1-120 > "$OUT/hdmi.txt"; echo "  HDMI: $(cat "$OUT/hdmi.txt" | cut -c1-100)"

echo "== lanzar"
"$ADB" logcat -c
"$ADB" shell am force-stop $APP
"$ADB" shell am start -n $APP/.MainActivity >/dev/null
sleep 12
cmd() { "$ADB" shell am broadcast -a $APP.CMD -p $APP --es c "'$*'" >/dev/null; }
cmd estado; sleep 1
cmd autoprueba; sleep 5
cmd camara_debug; sleep 3
cmd profundidad; sleep 4
cmd ver prueba; sleep 2
"$ADB" shell screencap -p /sdcard/proyector_cap.png && "$ADB" pull /sdcard/proyector_cap.png "$OUT/screencap.png" >/dev/null
cmd estado; sleep 1
"$ADB" logcat -d -s Proyector:* AndroidRuntime:E > "$OUT/logcat.txt"
"$ADB" pull /sdcard/Android/data/$APP/files "$OUT/" >/dev/null 2>&1
"$ADB" shell dumpsys SurfaceFlinger --display-id > "$OUT/sf_displays.txt" 2>/dev/null
"$ADB" shell dumpsys SurfaceFlinger | grep -E "Display [0-9]+|layerStack|com.smartup.proyector|Presentation|SurfaceView" > "$OUT/sf_capas.txt" 2>/dev/null

echo "== resumen del log"
grep -E "\[cam\] (primer cuadro|intrínsecos|eje óptico|sesión|base de timestamp)|\[sala\]|environmentRaycaster|\[salida\]|\[gl\]|\[prueba\]|\[debug\]|\[prof\]|FATAL|Exception" "$OUT/logcat.txt" | cut -c1-220 | head -60
echo "== render vs slscan"
"$AQUI/../.venv/bin/python" "$AQUI/tools/verificar_salida.py" "$OUT/files" || true
echo "salida en $OUT"
