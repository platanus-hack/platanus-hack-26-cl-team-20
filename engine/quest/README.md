# Quest → proyector · projection mapping sin computador (prototipo v1, WebXR)

El Meta Quest 3 sabe dónde está (6DoF), conoce la sala (malla de Space Setup, planos, hit-test) y renderiza **la vista
desde el proyector**. Esa vista sale por WebRTC (H.264) al navegador del proyector Android, que la muestra a pantalla
completa. El Mac solo se usa para desarrollar y servir las páginas; la señalización también puede ir por PeerJS cloud.

```
Quest Browser (quest.html, WebXR immersive-ar)                     Proyector Android (proyector.html)
  pose casco + malla/planos ─► cámara proyector (K, T_rig→proy) ─►   <video> pantalla completa, fondo negro
  canvas 1280x720 ─► VideoFrame → MediaStreamTrackGenerator ─► WebRTC H.264 (LAN, directo casco→proyector)
                                    ▲ señalización: server.py (/ws) o PeerJS cloud
```

## Arrancar

1. **Mac** (sirve páginas + señalización; logs del casco salen en esta terminal):
   ```bash
   cd /Volumes/Externo/sl-scanner
   .venv/bin/python quest/server.py --https          # http :8800 y https :8801 (autofirmado)
   ```
   Si hay casco por adb (USB o Wi-Fi) hace `adb reverse tcp:8800 tcp:8800` solo. `--abrir-casco` además abre la página.
2. **Casco**, una de dos:
   - Con adb (contexto seguro vía localhost):
     ```bash
     ADB=~/Library/Android/sdk/platform-tools/adb
     $ADB connect 192.168.4.161:5555                       # si va por Wi-Fi (requiere `adb tcpip 5555` previo por USB)
     $ADB -s 192.168.4.161:5555 reverse tcp:8800 tcp:8800
     $ADB -s 192.168.4.161:5555 shell am start -a android.intent.action.VIEW -d "http://localhost:8800/quest.html" com.oculus.browser
     ```
   - Sin cable: en el navegador del casco abrir `https://192.168.4.182:8801/quest.html`, «Avanzado › Continuar» en la
     advertencia del certificado (una vez). WebXR exige https o localhost; http://192.168.4.182:8800 **no** sirve en el casco.
3. **Proyector**: en su navegador `http://192.168.4.182:8800/proyector.html` → botón «Pantalla completa» (tiene foco:
   el OK del control remoto lo aprieta). Teclas: `F` pantalla completa, `P` patrón local, `D` estadísticas.
4. En el casco: **Entrar AR** (acepta el permiso de datos espaciales). El panel aparece frente a ti.

## Montar el rig

- **Cabeza** (modo `cabeza`): proyector rígido sobre la correa del casco, ~8–10 cm sobre los ojos, mirando al frente e
  inclinado ~10–15° hacia abajo (mesa a un brazo de distancia). Cualquier holgura del soporte rompe la calibración.
- **Trípode** (modo `fijo`): 1.5–1.8 m de alto, a 2–3 m de la mesa, inclinado 25–35° hacia abajo.
- En el proyector: **keystone apagado (manual y automático), zoom digital 100 %**, enfoque a la distancia de uso. Usa
  «Patrón» (en el panel o `P` en proyector.html) y verifica que se vean los 4 bordes: sin overscan ni recorte.
- Throw ratio por defecto 1.2 (a 1.5 m la imagen mide ~1.25 m). Si sabes el del proyector: `?throw=1.4`.

## Calibrar (paso a paso)

1. Space Setup hecho en el casco (si no hay malla, el panel lo dice; botón «Space Setup»).
2. **Punta (pivote)**, una vez: apoya la punta del control derecho en un punto fijo (esquina de la mesa) y gíralo en todas
   direcciones 4 s. Guarda el offset de la punta (la esfera roja virtual debe quedar en la punta física).
3. **Calibrar**: aparece la cruz 1 proyectada. Toca su centro con la punta y aprieta el **gatillo** (o A). El toque es el
   promedio de los 300 ms previos al gatillo; si el control se movió > 2 cm lo rechaza.
   - Modo cabeza: la cruz va pegada a tu cabeza, quédate quieto en cada toque. Mover la cabeza **entre** toques ayuda.
   - Las cruces deben caer en superficies a **distinta profundidad** (mesa + muro + piso). Si todo cae en un plano (típico
     del trípode mirando una mesa) se activa **Doble toque** solo: cada cruz se toca otra vez sobre una cartulina blanca
     sostenida 20–40 cm más cerca del proyector.
   - Cruz inalcanzable: «Saltar cruz». Error: B o «Rehacer último».
   - Desde 6 puntos resuelve en vivo: círculos verdes = tocados, rojo = error de reproyección.
4. Al terminar aplica y guarda (navegador + `quest/calib/calib.json` en el Mac). «Exportar» también descarga el JSON.
5. **Verifica a ojo** con efecto «mesa» o «aristas»: los bordes proyectados deben caer sobre los bordes reales.
   Afina a mano: **X** (control izq.) cambia el modo de ajuste giro → rollzoom → mover → centro; el **stick izquierdo**
   ajusta, con **grip** más fino. Se guarda solo.
6. **Latencia**: «Latencia ±10» (o `?latencia-ms=40`). Mueve la cabeza de lado mirando un borde de mesa y sube hasta que
   lo proyectado deje de ir atrasado; si se adelanta, baja. Extrapola la pose con velocidad lineal y angular suavizadas.

Precisión esperada: el toque manda. 1 cm de error de toque a ~1.3 m son ~16 px del proyector; con 12 cruces el modelo
queda a ~5–10 px (ver pruebas). Por eso el paso 5 (ajuste a mano) es parte del flujo, no un parche.

### Controles en el casco

| Control | Acción |
|---|---|
| Gatillo der. | botón del panel (si apuntas al panel) · tocar cruz · poner pozo (efecto pozo) |
| A | tocar cruz · fuera de calibración: pozo donde apunta el rayo |
| B | rehacer último toque · fuera de calibración: mostrar/ocultar panel |
| X (izq.) | modo de ajuste manual · stick izq. ajusta · grip = fino |
| Y (izq.) | traer el panel frente a ti |
| Manos | pellizco = gatillo |

### Parámetros de URL (quest.html)

| Parámetro | Default | Qué hace |
|---|---|---|
| `latencia-ms` | 0 | predicción de pose N ms al futuro (también ± en el panel) |
| `modo` | cabeza | `cabeza` o `fijo` (cada uno guarda su calibración) |
| `efecto` | wireframe | `wireframe`, `aristas`, `mesa`, `pozo`, `negro` |
| `puntos` | 12 | cruces de calibración (≥ 6; 20 da más margen) |
| `res` | 720 | resolución del stream: `540`, `720`, `1080` |
| `fps`, `kbps` | 30, 12000 | cuadros y bitrate máximo del video |
| `w`, `h` | 1920, 1080 | resolución nativa del proyector (píxeles de calibración) |
| `throw` | 1.2 | throw ratio para la K inicial / de respaldo |
| `senal` | ws | `ws` (server.py), `peerjs` (PeerJS cloud), `no` |
| `sala` | sala | nombre de la sala de señalización (mismo en ambas páginas) |
| `fuente` | auto | `generador` (VideoFrame) o `captura` (canvas.captureStream) |
| `hint` | motion | contentHint de la pista (`detail` prioriza nitidez) |
| `cast` | 0 | respaldo cast nativo (ver abajo) |
| `doble` | 0 | doble toque desde el inicio |
| `sim` | 0 | simulador sin casco (ver pruebas) |

## Sin Mac (alojado)

- Sube la carpeta `quest/` tal cual a cualquier hosting estático con **https** (GitHub Pages, Cloudflare Pages, S3):
  three.js va en `vendor/`, no hay build. Sin `server.py`, los logs y la subida de calib se apagan solos; la calibración
  queda en el navegador del casco y en «Exportar».
- Señalización: `quest.html?senal=peerjs&sala=XYZ` y `proyector.html?senal=peerjs&sala=XYZ`. PeerJS cloud
  (0.peerjs.com) solo intercambia SDP/ICE; el video va directo por la LAN (STUN de Google para los candidatos).
  Probado en headless: video en ~7–17 s, H.264.
- Para no depender de PeerJS cloud: un `peerjs-server` propio o un WebSocket mínimo (Cloudflare Worker + Durable Object)
  con el mismo protocolo de `server.py`: `hello{rol,sala}` → `welcome{id,peers}`, `join`, `leave`, `signal{to,data}`.
- Si el proyector se aloja con https, abre proyector.html también con https (mezcla ws/http no permitida).

## Respaldo: cast nativo del Quest

Si el proyector no tiene navegador o WebRTC no conecta: `quest.html?cast=1&senal=no`. La vista proyector va como un
cuadro fijo a la cabeza **solo en el ojo izquierdo** (el ojo que transmite el cast; el derecho sigue con passthrough).
Castea el Quest al proyector (Chromecast integrado / Google Cast desde Ajustes del casco o la app Meta Horizon) y
calibra en ese modo: la escala y el recorte del cast los absorbe K. Ajusta el tamaño con `?castAncho=1.2` (m a 1 m).
Latencias esperadas (no medidas): cast nativo del Quest ~200–500 ms; WebRTC en LAN ~50–100 ms (render + encode + red +
decode + pantalla del proyector). El Quest no hace AirPlay ni Miracast.

## Limitaciones conocidas

- **Sin lazo cerrado**: WebXR no da acceso a las cámaras passthrough, así que no se puede ver lo proyectado y
  corregir solo (como hicimos con la Osmo). La calibración es por toques + ajuste a mano.
- **No verificado en el casco**: el casco no estuvo disponible para entrar a la sesión. Lo que no se pudo probar:
  si Quest Browser sigue entregando VideoFrame/WebRTC durante la sesión inmersiva (si se congela, prueba
  `?fuente=captura` o `?cast=1`), permisos de malla/planos, posición real del origen del rayo del control, rendimiento.
- `mesh-detection`/`plane-detection` requieren Space Setup y el permiso de datos espaciales.
- `depth-sensing`: three r170 lo usa solo para oclusión en el casco y solo con WebXR Layers; si llega sin Layers se
  ignora (parche en app.js). No se usa para el contenido proyectado en v1.
- **Contenido denso = más latencia**: en el Mac, 720p con efecto «mesa» usa ~2 Mbps y ~10–20 ms de jitter buffer;
  «wireframe» denso satura (~9 Mbps, baja a 540p, ~100 ms de jitter buffer). Para mapping conviene contenido ralo.
- Modo fijo: el marco es la malla global de Space Setup (estable entre sesiones). Si se rehace Space Setup, recalibrar.
- WebRTC usa candidatos mDNS (.local). Si casco y proyector no conectan en la misma red, revisa que el router no aísle
  clientes Wi-Fi; en el proyector, `proyector.html?debug=1` muestra el estado.

## Plan v2 nativo

- App Meta Spatial SDK (base: `/Volumes/Externo/quest-scanner-work/metraje`: MRUK, `ScanCamera.kt`, `LiveMesh.kt`).
- **Lazo cerrado con Passthrough Camera API** (`horizonos.permission.HEADSET_CAMERA`): proyectar gray code / marcadores,
  capturar con la cámara RGB del casco (intrínsecos y pose lente→cabeza desde camera2, como `ScanCamera.kt`),
  decodificar correspondencias píxel proyector ↔ píxel cámara, triangular con la profundidad / raycast de MRUK y
  calibrar con miles de puntos (como `calibrar_lazo` con la Osmo). Recalibración continua para la holgura del soporte.
- Video: MediaCodec H.264 de baja latencia → RTP/UDP o WebRTC nativo, y una APK receptora mínima en el proyector
  (MediaCodec a Surface): menos latencia que un navegador.
- Predicción con el tiempo de display predicho del runtime + offset medido glass-to-glass.

## Archivos

| Archivo | Qué es |
|---|---|
| `quest.html`, `js/app.js` | app del casco (WebXR, panel, calibración, efectos, envío) y simulador |
| `js/calib.js` | solver puro: DLT normalizado, RQ, LM (fx=fy, cx, cy, pose), PnP con K fija, pivote de la punta |
| `js/proyector-cam.js` | `ProyectorCam` (K + T_rig→proyector, modos cabeza/fijo, ajuste manual) y `PredictorPose` |
| `js/sala.js` | mallas/planos XR → vistas del casco y del proyector; efectos wireframe/aristas/mesa/pozo |
| `js/overlay.js` | cruces numeradas, errores de reproyección y patrón en píxeles del proyector |
| `js/transporte.js` | fuente de video, WebRTC (H.264 primero, bitrate, baja latencia), señalización ws y PeerJS |
| `js/panel.js`, `js/log.js`, `js/sim.js` | panel en el casco · logs al Mac · simulador con el escaneo |
| `proyector.html` | receptor, JS conservador para navegadores viejos de proyectores |
| `server.py` | http(s) estático + señalización + `/api/log` + `/api/calib` + `adb reverse` |
| `calib_solver.py` | el mismo solver en numpy/OpenCV, para validar el JS o re-resolver `calib/calib.json` |
| `pruebas/` | `test_solver.mjs` (node), `headless.py` (Chrome headless), `xr-mock.js` (WebXR falso), resultados y capturas |
| `vendor/three-0.170.0/` | three.js r170 + GLTFLoader + líneas gordas (MIT), sin CDN |

## Pruebas sin casco

```bash
/opt/homebrew/bin/node quest/pruebas/test_solver.mjs                                   # solver JS, 200 casos por fila
.venv/bin/python quest/calib_solver.py quest/pruebas/resultados/sintetico.json          # JS vs numpy/OpenCV
.venv/bin/python quest/pruebas/headless.py smoke sim xr e2e peerjs                      # Chrome headless (server.py corriendo)
```
Simulador interactivo: `http://localhost:8800/quest.html?sim=1` (WASD/RF mover, arrastrar = mirar, C calibrar,
clic = tocar la cruz donde la ves, T calibración sintética, 1–5 efectos, P pozo, M ajuste con flechas, V modo).
El escaneo de Scaniverse hace de mundo real y un proyector «verdadero» oculto proyecta el canvas sobre él (textura
proyectiva con sombra): se ve lo mismo que vería Max, desalineado antes de calibrar y alineado después.

Resultados (5 nov 2026):
- Solver sin ruido: error 5e-13 px (node) y 0.005 px en el simulador con el escaneo. JS = OpenCV `calibrateCamera`
  a 1e-4 px; DLT JS = DLT numpy a 1e-11.
- Ruido de toque 1 cm rms, 12 cruces: error del modelo a 2 m ~5.6 px mediana (node), ~9 px mediana en el simulador
  (cabeza a ~1.3 m de la mesa), ~5 px en trípode con doble toque. 0.5 cm de ruido → ~2.8 px.
- Pivote de la punta: 0.5 mm de error con 1 mm de ruido.
- WebXR falso: sesión, malla/planos, clic en el panel, 12 toques con el control → recupera la calibración (0.005 px),
  pozo con A, ajuste con stick, cierre de sesión; sin errores de consola.
- WebRTC Mac→Mac: H.264 720p30, 0 pérdidas; con PeerJS cloud también.
