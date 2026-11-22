#!/bin/bash
# Levanta el servidor remoto (idempotente). Uso: bash remoto/levantar.sh
cd "$(dirname "$0")" || exit 1
PUERTO=9330; PUERTO_LAN=9331; PUERTO_TS=8443
IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)
CLAVE=$(python3 -c "import json;print(json.load(open('config.json')).get('clave',''))" 2>/dev/null)
[ -z "$CLAVE" ] && CLAVE=$(openssl rand -hex 3)
TSDNS=$(tailscale status --json 2>/dev/null | python3 -c "import json,sys;print(json.load(sys.stdin)['Self']['DNSName'].rstrip('.'))" 2>/dev/null)

# Certificado autofirmado para la IP local actual (se regenera si cambió la IP)
if [ -n "$IP" ] && { [ ! -f cert.pem ] || ! openssl x509 -in cert.pem -noout -text 2>/dev/null | grep -q "IP Address:$IP"; }; then
  openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 30 -subj "/CN=wui-remoto" \
    -addext "subjectAltName=IP:$IP,IP:127.0.0.1" >/dev/null 2>&1
fi

URL_TS=""; [ -n "$TSDNS" ] && URL_TS="https://$TSDNS:$PUERTO_TS"
URL_LAN=""; [ -n "$IP" ] && URL_LAN="https://$IP:$PUERTO_LAN"
cat > config.json <<EOF
{"puerto": $PUERTO, "puerto_lan": $PUERTO_LAN, "clave": "$CLAVE", "url_tailscale": "$URL_TS", "url_lan": "$URL_LAN"}
EOF

# Solo se detiene NUESTRO servidor (por pid guardado)
if [ -f servidor.pid ] && kill -0 "$(cat servidor.pid)" 2>/dev/null; then kill "$(cat servidor.pid)"; sleep 0.5; fi
nohup python3 servidor.py >> servidor.log 2>&1 &
echo $! > servidor.pid
sleep 1

# HTTPS con certificado válido dentro del tailnet (no toca lo que ya haya en el puerto 443)
[ -n "$TSDNS" ] && tailscale serve --bg --https=$PUERTO_TS http://127.0.0.1:$PUERTO >/dev/null 2>&1

echo "Servidor: pid $(cat servidor.pid)  ·  QR y enlaces: http://127.0.0.1:$PUERTO/"
[ -n "$URL_TS" ] && echo "Tailscale:  $URL_TS/pov   ·   $URL_TS/control"
[ -n "$URL_LAN" ] && echo "Red local:  $URL_LAN/pov?k=$CLAVE   ·   $URL_LAN/control?k=$CLAVE"
