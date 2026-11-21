#!/usr/bin/env bash
# Instala el APK actual, relanza la app y verifica en vivo: ArUco (antes/después), PnP de la mesa,
# depth, cam_full/cam_pose, malla con anclas y streams. Usa adb por Wi-Fi o, si no, el USB.
#   tools/probar_aruco.sh                 # casco puesto: sin prox_close
#   tools/probar_aruco.sh --prox          # casco sobre la mesa: prox_close antes de instalar
#   tools/probar_aruco.sh --sin-instalar
set -uo pipefail
ADB=${ADB:-/Users/max/Library/Android/sdk/platform-tools/adb}
WIFI=${WIFI:-192.168.4.161:5555}
USB=${USB:-2G0YC5ZGCM043L}
APP=com.smartup.proyector
AQUI="$(cd "$(dirname "$0")/.." && pwd)"
PY="$AQUI/../.venv/bin/python"
H="http://${WIFI%%:*}:8787"
GRILLA=${GRILLA:-/Volumes/Externo/sl-scanner/captures/dale-20261102-1511/aruco_grid.json}
PROX=0; INSTALAR=1
for a in "$@"; do [[ $a == --prox ]] && PROX=1; [[ $a == --sin-instalar ]] && INSTALAR=0; done

"$ADB" connect "$WIFI" >/dev/null 2>&1 || true
if "$ADB" -s "$WIFI" get-state >/dev/null 2>&1; then S=$WIFI
elif "$ADB" -s "$USB" get-state >/dev/null 2>&1; then S=$USB
else echo "casco no responde por adb (ni $WIFI ni USB $USB)"; exit 2; fi
echo "== adb por $S"
[[ $PROX == 1 ]] && "$ADB" -s "$S" shell am broadcast -a com.oculus.vrpowermanager.prox_close >/dev/null && echo "prox_close enviado"
if [[ $INSTALAR == 1 ]]; then
  "$ADB" -s "$S" install -r -g "$AQUI/app/build/outputs/apk/debug/app-debug.apk" | tail -1
  for p in horizonos.permission.HEADSET_CAMERA android.permission.CAMERA com.oculus.permission.USE_SCENE; do "$ADB" -s "$S" shell pm grant $APP $p 2>/dev/null; done
fi
[[ -f "$GRILLA" ]] && "$ADB" -s "$S" push "$GRILLA" /sdcard/Android/data/$APP/files/aruco_grid.json >/dev/null
"$ADB" -s "$S" shell am start -n $APP/.MainActivity --activity-brought-to-front >/dev/null
[[ $S == "$USB" ]] && { "$ADB" -s "$S" tcpip 5555 >/dev/null 2>&1; sleep 3; "$ADB" connect "$WIFI" >/dev/null 2>&1; }
for i in $(seq 1 20); do sleep 2; curl -s -m 2 -o /dev/null "$H/estado.json" && break; done
curl -s -m 3 "$H/cmd?c=aruco%20on"; echo
T=$(mktemp -d); echo "== endpoints"
for r in estado.json cam.jpg render.jpg depth.jpg depth.json cam_full.jpg cam_full.png cam_pose.json malla.json "log?n=5"; do
  curl -s -m 15 -o "$T/p" -D "$T/h" -w "$r %{http_code} %{size_download}B %{time_total}s" "$H/$r"; echo " $(grep -i "^X-" "$T/h" | tr -d '\r' | tr '\n' ' ')"
done
"$PY" - "$H" <<'EOF'
import json, sys, urllib.request
h = sys.argv[1]
m = json.load(urllib.request.urlopen(h + "/malla.json", timeout=20))
print("malla.json anclas:", len(m.get("anclas", [])), sorted({a["etiqueta"] for a in m.get("anclas", [])}))
p = json.load(urllib.request.urlopen(h + "/cam_pose.json", timeout=5))
print("cam_pose.json:", {k: p[k] for k in ("ts_ns",) if k in p}, "t", p.get("camara", {}).get("t"), "fx", p.get("intrinsecos", {}).get("fx"))
d = json.load(urllib.request.urlopen(h + "/estado.json", timeout=5)).get("depth")
print("depth:", d)
print("ae_lock:", json.load(urllib.request.urlopen(h + "/cmd?c=ae_lock%20on%20-1", timeout=5)).get("r"))
EOF
echo "== ArUco por cuadro (fábrica vs ajustado) y captura con marcas"
"$PY" "$AQUI/tools/medir_aruco.py" --app "$H" --comparar --segundos 12
echo "monitor: http://127.0.0.1:8810/ · app: $H/ · 3D: $H/3d.html"
