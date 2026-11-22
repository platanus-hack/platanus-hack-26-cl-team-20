# Remoto de la presentación (POV de Matilda + control de Daniel)

## Levantar

```bash
bash remoto/levantar.sh     # idempotente: reinicia solo este servidor (remoto/servidor.pid), log en remoto/servidor.log
```

Después **recargar la presentación** (`http://127.0.0.1:9300/presentacion.html`) para que cargue `remoto/presentacion-remoto.js`.
QR y enlaces para escanear, desde este Mac: `http://127.0.0.1:9330/`

Apagar: `kill $(cat remoto/servidor.pid)` y, si se quiere, `tailscale serve --https=8443 off`.

## URL para los celulares

**Vía A — Tailscale (certificado válido, sin advertencias). El celular debe estar en el tailnet con Tailscale encendido.**

- Matilda (cámara): `https://macbook-pro-de-daniel.taildb4fed.ts.net:8443/pov`
- Daniel (control): `https://macbook-pro-de-daniel.taildb4fed.ts.net:8443/control`

**Vía B — misma wifi, certificado autofirmado (para un celular que no está en el tailnet).**
Safari avisa «La conexión no es privada»: tocar «Mostrar detalles» → «visitar este sitio web». La IP y la clave salen de `levantar.sh` (si el Mac cambia de red, volver a correrlo).

- Matilda (cámara): `https://<ip-del-mac>:9331/pov?k=<clave>`
- Daniel (control): `https://<ip-del-mac>:9331/control?k=<clave>`

En `/pov`: tocar «Empezar a transmitir» y permitir la cámara. Indicador rojo «Transmitiendo» = llegó a la presentación.

## Piezas

- `servidor.py`: Python estándar. `127.0.0.1:9330` (HTTP; lo usan la presentación y `tailscale serve`) y `0.0.0.0:9331` (HTTPS autofirmado, exige `?k=`). Señalización por long-polling (`/api/poll`, `/api/enviar`, `/api/estado`), CORS `*`.
- `presentacion-remoto.js`: dentro de la presentación. Responde la oferta WebRTC → `WUI.ponerPov(stream)`, ejecuta comandos → `WUI.*`, publica `WUI.estado()` cada 1 s. Si el servidor no está, reintenta en silencio.
- `pov.html`, `control.html`: páginas móviles. `prueba.html`: presentación simulada para probar (`/prueba`, sala aparte `prueba`, no toca la real).
- Video: WebRTC directo celular → Mac, solo STUN de Google, **sin TURN**.

## Qué se probó (22-nov, sin celular)

- `curl`: `/pov`, `/control`, `/salud` responden 200 por HTTPS de Tailscale con certificado verificado (`ssl_verify_result=0`) y por HTTPS de red local (`-k`); sin clave en red local da 403.
- Chrome headless aislado con cámara falsa, contra `/prueba` (carga el mismo `presentacion-remoto.js`), por las tres vías (HTTP local, Tailscale, red local): el video llega y avanza; los 7 comandos llegan a `WUI` (`siguiente`, `anterior`, `pausa`, `ctl fin/preparar/dale`); el estado se publica; reconecta solo tras recargar la presentación y tras cerrar la conexión del lado del celular.

## Qué NO se probó

- Un iPhone real: permiso de cámara en Safari, cámara trasera, Wake Lock, y el video real por la red.
- La presentación real (`presentacion.html`) con el remoto: no se recargó para no interrumpir la demo. Solo se probó con la presentación simulada.
- La página `/control` en un celular (los comandos se probaron con las mismas peticiones HTTP que hace la página).
- Si Matilda no está en la misma wifi ni en el tailnet, o la wifi aísla clientes entre sí, el video no conectará (no hay TURN).
