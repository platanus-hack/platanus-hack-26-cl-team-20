#!/bin/bash
# Arranque de la mesa con el Quest 3 como cámara (9-nov-2026). Idempotente: levanta SOLO lo que no está corriendo,
# en orden, y nunca reinicia ni mata lo que ya anda (la ventana del proyector, el monitor, el server del Quest).
#
#   scripts/arranque_mesa.sh              # levanta lo que falte
#   scripts/arranque_mesa.sh --estado     # qué corre y qué no (no toca nada)
#   scripts/arranque_mesa.sh --parar-feed # para el feed y su lazo (para calibrar con la ventana libre)
#
# Orden: 1) adb forward del Quest por USB (:8787 → 127.0.0.1:8787; si no hay USB, el feed usa la Wi-Fi)
#        2) ventana del proyector (playground/ventana_proyector.py :8793, ventana NORMAL; solo si el display (0,1080) existe)
#        3) feed de la mesa (scripts/feed_loop.sh → playground.superficie_feed :8798, --calib captures/quest-cal-fija)
#        4) monitor del casco (quest-nativo/tools/monitor_adb.py :8810)
#        5) server del Quest (quest/server.py --https :8800/:8801)
#        6) vigilante de la app (logs/quest_watchdog.sh)
# Variables: MESA_SCAN (captures/mesa-quest), MESA_CALIB (captures/quest-cal-fija), FEED_PORT (8798), FEED_ARGS (extra).
# Nada de pantalla completa ni del runtime viejo :8790. Ver docs/SETUP-MESA-QUEST.md.

cd /Volumes/Externo/sl-scanner || exit 1
PY=.venv/bin/python
ADB=${ADB:-/Users/max/Library/Android/sdk/platform-tools/adb}
SERIAL_USB=2G0YC5ZGCM043L
QUEST_IP=192.168.4.161
SCAN=${MESA_SCAN:-captures/mesa-quest}
CALIB=${MESA_CALIB:-captures/quest-cal-fija}
FEED_PORT=${FEED_PORT:-8798}
VENTANA_PORT=8793
CAM_Q="w=864&fps=15&q=80"          # la app entrega 864x864 a ~12,7 fps como máximo (cuadro del monitor cada 70 ms)
mkdir -p logs

escucha() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }
http_ok() { [ "$(curl -s -m "${2:-2}" -o /dev/null -w '%{http_code}' "$1")" = "200" ]; }
usb_quest() { "$ADB" devices 2>/dev/null | grep -q "^$SERIAL_USB[[:space:]]*device"; }
display_proyector() {   # ¿existe el display del proyector en (0,1080)? (sin él, la ventana caería en los monitores de Max)
  "$PY" - <<'EOF' 2>/dev/null
import Quartz
_, ids, _ = Quartz.CGGetActiveDisplayList(16, None, None)
print("si" if any((int(Quartz.CGDisplayBounds(i).origin.x), int(Quartz.CGDisplayBounds(i).origin.y)) == (0, 1080) for i in ids) else "no")
EOF
}
linea() { printf '  %-34s %s\n' "$1" "$2"; }

estado() {
  echo "mesa con el Quest · $(date '+%Y-%m-%d %H:%M:%S')"
  if usb_quest; then linea "Quest por USB ($SERIAL_USB)" "sí"; else linea "Quest por USB ($SERIAL_USB)" "NO"; fi
  if "$ADB" forward --list 2>/dev/null | grep -q "tcp:8787 tcp:8787"; then linea "adb forward :8787" "sí"; else linea "adb forward :8787" "NO"; fi
  for b in "http://127.0.0.1:8787" "http://$QUEST_IP:8787"; do
    j=$(curl -s -m 2 "$b/estado.json" | "$PY" -c "import json,sys; j=json.load(sys.stdin); c=j.get('camara',{}); print(f\"app foco={j['app']['foco']} cámara={c.get('abierta')} {c.get('fps',0):.0f} fps\")" 2>/dev/null)
    linea "app del Quest $b" "${j:-NO responde}"
  done
  linea "display del proyector (0,1080)" "$(display_proyector)"
  if escucha $VENTANA_PORT; then linea "ventana proyector :$VENTANA_PORT" "$(curl -s -m 2 http://127.0.0.1:$VENTANA_PORT/estado)"; else linea "ventana proyector :$VENTANA_PORT" "NO"; fi
  if escucha "$FEED_PORT"; then
    s=$(curl -s -m 3 "http://127.0.0.1:$FEED_PORT/status" | "$PY" -c "import json,sys; d=json.load(sys.stdin); c=d.get('camara',{}); sa=d.get('salida',{}); print(f\"{d.get('modo')} · feed {d['fps']} fps · cámara {c.get('fps')} fps {c.get('size')} · lienzo {d['lienzo']} {d.get('mm_por_px')} mm/px · salida {'on' if d['rojo']['on'] else 'off'} ({d['rojo']['enviados']} cuadros, hace {sa.get('hace_s')} s)\")" 2>/dev/null)
    linea "feed :$FEED_PORT" "${s:-responde raro}"
  else linea "feed :$FEED_PORT" "NO"; fi
  if pgrep -f "scripts/feed_loop.sh" >/dev/null; then linea "feed_loop.sh (relanzador)" "sí"; else linea "feed_loop.sh (relanzador)" "NO"; fi
  if escucha 8810; then linea "monitor del casco :8810" "sí"; else linea "monitor del casco :8810" "NO"; fi
  if escucha 8800; then linea "quest/server.py :8800/:8801" "sí"; else linea "quest/server.py :8800/:8801" "NO"; fi
  if pgrep -f "quest_watchdog.sh" >/dev/null; then linea "vigilante logs/quest_watchdog.sh" "sí"; else linea "vigilante logs/quest_watchdog.sh" "NO"; fi
  if escucha 8790; then linea "runtime viejo :8790" "CORRIENDO (no se usa: la salida va por la ventana)"; fi
}

if [ "${1:-}" = "--estado" ]; then estado; exit 0; fi
if [ "${1:-}" = "--parar-feed" ]; then
  pkill -f "scripts/feed_loop.sh" && echo "feed_loop parado"
  pkill -f "playground.superficie_feed $SCAN" && echo "feed parado"
  exit 0
fi

# 1) Quest por USB: adb forward (el feed lee 127.0.0.1:8787 y no carga la Wi-Fi, que comparte el AirPlay)
CAM="http://127.0.0.1:8787/cam.mjpg?$CAM_Q"
if usb_quest; then
  "$ADB" -s "$SERIAL_USB" forward tcp:8787 tcp:8787 >/dev/null && echo "1) adb forward :8787 por USB"
  http_ok "http://127.0.0.1:8787/estado.json" 3 || echo "   la app del Quest no responde por USB (¿abierta y con foco?)"
elif http_ok "http://$QUEST_IP:8787/estado.json" 3; then
  CAM="http://$QUEST_IP:8787/cam.mjpg?$CAM_Q"; echo "1) sin USB: el feed lee el Quest por Wi-Fi ($QUEST_IP)"
else
  echo "1) el Quest no está ni por USB ni por Wi-Fi: el feed igual se lanza y espera (se relanza solo)"
fi

# 2) ventana del proyector: solo si no corre y si el display del proyector existe (nunca en los monitores de Max)
if escucha $VENTANA_PORT; then
  echo "2) ventana del proyector :$VENTANA_PORT ya corre"
elif [ "$(display_proyector)" = "si" ]; then
  nohup "$PY" -m playground.ventana_proyector --display 0,1080 --size 1900x1040 --port $VENTANA_PORT >> logs/ventana_proyector.log 2>&1 &
  sleep 2; echo "2) ventana del proyector lanzada (:$VENTANA_PORT, ventana normal 1900x1040 en (0,1080))"
else
  echo "2) NO lanzo la ventana: no está el display del proyector en (0,1080) (¿AirPlay conectado?)"
fi

# 3) feed de la mesa con el Quest (relanzador feed_loop.sh)
if escucha "$FEED_PORT" || pgrep -f "scripts/feed_loop.sh $SCAN" >/dev/null; then
  echo "3) feed :$FEED_PORT ya corre"
else
  # shellcheck disable=SC2086
  nohup scripts/feed_loop.sh "$SCAN" --calib "$CALIB" --camera "$CAM" --port "$FEED_PORT" --rojo ${FEED_ARGS:-} >> logs/feed_quest.log 2>&1 &
  echo "3) feed lanzado: $SCAN --calib $CALIB --camera $CAM --port $FEED_PORT --rojo (log logs/feed_quest.log)"
fi

# 4) monitor del casco (:8810)
if escucha 8810; then echo "4) monitor del casco :8810 ya corre"
else
  (cd quest-nativo && nohup ../.venv/bin/python tools/monitor_adb.py >> ../logs/monitor_adb.log 2>&1 &)
  echo "4) monitor del casco lanzado (http://127.0.0.1:8810/)"
fi

# 5) server del Quest (WebXR / señalización)
if escucha 8800; then echo "5) quest/server.py :8800 ya corre"
else
  nohup "$PY" quest/server.py --https >> logs/quest_server.log 2>&1 &
  echo "5) quest/server.py --https lanzado (:8800/:8801)"
fi

# 6) vigilante de la app del casco (la trae al frente y reabre la cámara si pierde el foco)
if pgrep -f "quest_watchdog.sh" >/dev/null; then echo "6) vigilante ya corre"
else
  nohup bash logs/quest_watchdog.sh >> logs/quest_watchdog.log 2>&1 &
  echo "6) vigilante lanzado (logs/quest_watchdog.log)"
fi

sleep 3
echo
estado
