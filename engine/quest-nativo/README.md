# Proyector nativo (v2): projection mapping sin computador

Quest 3 + proyector Magcubic HY320 montados rígidos en un rig (de mano o trípode). El Quest es
sensor y cerebro: sabe dónde está (tracking 6DoF), conoce la sala (MRUK / profundidad), ve la
pared con su cámara passthrough y manda la imagen al proyector por USB-C → HDMI. El Mac solo
compila e instala. App: `com.smartup.proyector` (Kotlin, Meta Spatial SDK 0.13.2 + MRUK).

> Estado y resultados de pruebas: ver «Resultados» al final.

## Arquitectura

```
 cámara passthrough izq. (camera2, 1280x1280, 30 fps) ──┐
   ScanCamera: intrínsecos escalados al stream,          │  luma + timestamp (boottime)
   extrínseca cabeza←cámara (LENS_POSE_*)                ▼
 tracking (Spatial SDK getViewerPose) ──► HistorialPose ──► Calibrador ──► calib_proyector.json
   SistemaCabeza (ECS, cada cuadro)       (interpola,       Gray code → mapa cámara→proyector
                                           predice)          → raycast (MRUK env. / sala)
 MRUK: Space Setup (anclas, global mesh) ─┐                  → pares 3D(cabeza)↔2D(textura)
 Environment Raycaster (profundidad) ─────┤                  → RANSAC-DLT + LM (multi-vista)
                                          ▼                  → rejilla residual + lazo con puntos
                               GeometriaSala (triángulos)
                                          ▼
 RendererProyector (hilo GL + EGL propios, FBO 1920x1080)
   patrón (negro/blanco/Gray/carta) ó mapeo: malla vista desde P·T_cabeza←mundo(predicha|fija)
   → pasada final: ajuste manual 2D + rejilla residual
                                          ▼
 SalidaProyector ── (A) Presentation en el display "HDMI Screen" (display Android secundario)
                └── (B) panel de superficie fijo a la cabeza que llena el ojo izquierdo
                        (el espejo DP Alt Mode de Meta lo lleva al HDMI)
```

| Archivo | Qué hace |
|---|---|
| `MainActivity.kt` | permisos, MRUK, cámara, paneles (UI + salida B), `SistemaCabeza` (pose, panel fijo a la cabeza, botones del control), comandos por UI/control/adb, guardar/cargar, malla de profundidad, `camara_debug`, `autoprueba` |
| `ScanCamera.kt` | cámara passthrough izquierda (de Metraje) + intrínsecos al tamaño del stream + extrínseca + copia de luma + AE lock |
| `RendererProyector.kt` | GLES 3: shaders de patrón Gray (mismo cálculo que `slscan/graycode.py`), carta de prueba, malla con efectos (aristas, planos, rejilla sobre la mesa, blanco, profundidad), puntos de verificación, warp final (ajuste 2D + rejilla) |
| `SalidaProyector.kt` | elige y mantiene la salida A (Presentation) o B (panel), escucha conexión/desconexión del HDMI |
| `Calibrador.kt` | calibración en lazo cerrado (latencia, máscara, Gray, decodificación, raycast, ajuste, lazo con puntos) |
| `GrayCode.kt` | patrones y decodificador incremental + muestreo subpíxel (ajuste afín local) |
| `Proyeccion.kt` | modelo del proyector (P 3x4, K/R/C), DLT normalizado, RANSAC, Levenberg-Marquardt, caso plano restringido, matriz GL |
| `RejillaCorreccion.kt` | corrección densa residual (como `lazo_corr.npz` de la Osmo) |
| `GeometriaSala.kt` | MRUK → triángulos (global mesh, planos y volúmenes por etiqueta) y malla de profundidad por raycast |
| `HistorialPose.kt` | poses con tiempo: interpolación (pose de la cámara en el timestamp del cuadro) y predicción (latencia) |
| `CalibIO.kt` | JSON de calibración, imágenes de verificación, distorsión de camera2 |
| `Mat.kt` | álgebra mínima (Jacobi, RQ, Rodrigues...) sin dependencias |
| `app/src/test/.../SinteticoTest.kt` | pruebas sintéticas en la JVM (sin casco) |
| `tools/verificar_sintetico.py` | verificación cruzada de las mismas pruebas con `slscan.graycode` + OpenCV |

## Decisiones técnicas

- **Spatial SDK 0.13.2** (igual que Metraje y los samples locales): compila y ya probamos
  cámara + MRUK con esta versión. Existe 0.15.0 (Maven, 3-nov-2026) pero no hace falta nada nuevo
  de ella y subir arriesga romper APIs de paneles; se puede probar después cambiando
  `spatialsdk` en `gradle/libs.versions.toml`.
- **Salida al proyector**: con el adaptador conectado, `adb shell dumpsys display` muestra un
  display Android real: `"HDMI Screen"` 1920x1080@60, `displayId 20`, `FLAG_PRESENTATION`
  (Quest 3 v209, 5-nov-2026). Por eso la salida por defecto (AUTO) es **A: `Presentation`** con
  una SurfaceView 1920x1080 donde dibuja nuestro renderer: sin compositor, sin timewarp, pixel a
  pixel. Si el sistema sigue pintando el espejo del ojo izquierdo encima (la cámara no ve nuestros
  patrones), la app cae sola a **B: panel** (`VideoSurfacePanelRegistration`, layer zIndex 10)
  que se reubica cada cuadro en `pose_cabeza · (dx=−3,2 cm, 0, 1 m)` y cubre el ojo izquierdo; en
  B se apaga el passthrough y se oculta la UI para que no se re-proyecten. El recorte/escala fijo
  del espejo y la homografía panel→ojo quedan absorbidos porque P es una 3x4 general (11 gdl) y la
  calibración es de punta a punta (textura → pared).
- **Pose de la cámara**: extrínseca de camera2 `LENS_POSE_TRANSLATION/ROTATION`, convertida igual
  que el showcase oficial de Meta para Spatial SDK (`q = (r3,−r0,−r1,r2)⁻¹ · Euler(180°,0,0)`,
  `t = (t0, t1, −t2)`), compuesta con la pose de la cabeza interpolada en el timestamp del cuadro.
  Rayo de un píxel con la convención Android de los intrínsecos (v desde arriba):
  `((u−cx)/fx, (cy−v)/fy, 1)`. Comprobación independiente: comando `camara_debug` (puntos de
  profundidad lanzados desde el centro de la cabeza, proyectados en la imagen de la cámara con la
  extrínseca: deben calzar con los bordes reales).
- **Modelo del proyector respecto de la cabeza**: P (3x4) que lleva puntos del marco cabeza
  (Spatial SDK, metros, zurdo: x der, y arriba, z adelante) a píxeles de la textura. DLT de
  Hartley + RANSAC + LM con parámetros físicos (fx, fy, cx, cy, skew, rotación, centro). Si los
  puntos son casi coplanares (una sola pared) el DLT es degenerado: se usa un modelo restringido
  (skew 0, fx = fy, cx centrado) y la UI pide **otra vista**: como el proyector va rígido a la
  cabeza, capturar desde 2–3 poses del rig da puntos no coplanares en el marco cabeza y el DLT
  completo queda bien condicionado (probado en sintético: 0,5 mm de error de centro).
- **Lazo cerrado**: después del ajuste se proyectan ~80 puntos 3D por la cadena completa (modelo
  + pose + rejilla), la cámara los mide y el error (px de cámara → px de proyector con el
  jacobiano local del mapa Gray) corrige la rejilla, hasta 3 iteraciones o < 0,8 px.
- **Sin OpenCV en el casco**: todo en Kotlin puro (≈1.500 líneas), verificado contra OpenCV y
  slscan en el Mac. Nada pesado que instalar.

## Compilar e instalar (Mac)

```bash
cd /Volumes/Externo/sl-scanner/quest-nativo
export GRADLE_USER_HOME=/Volumes/Externo/cache/gradle JAVA_HOME=/opt/homebrew/opt/openjdk@17
./gradlew :app:assembleDebug                 # APK: app/build/outputs/apk/debug/app-debug.apk
./gradlew :app:testDebugUnitTest             # pruebas sintéticas (gray code, DLT/PnP, rejilla)
../.venv/bin/python tools/verificar_sintetico.py   # verificación cruzada con slscan + OpenCV

ADB=/Users/max/Library/Android/sdk/platform-tools/adb
# el único USB-C del Quest lo usa el video: adb por Wi-Fi
$ADB connect 192.168.4.161:5555 && export ANDROID_SERIAL=192.168.4.161:5555
$ADB install -r -g app/build/outputs/apk/debug/app-debug.apk
$ADB shell pm grant com.smartup.proyector horizonos.permission.HEADSET_CAMERA
$ADB shell pm grant com.smartup.proyector android.permission.CAMERA
$ADB shell pm grant com.smartup.proyector com.oculus.permission.USE_SCENE
$ADB shell am start -n com.smartup.proyector/.MainActivity
$ADB logcat -s Proyector:*
```

## Pasos para Max

1. **Cadena de video** (la probada por Meta): USB-C del Quest → adaptador USB-C a DisplayPort
   (Alt Mode) → adaptador **activo** DP→HDMI → HDMI del HY320. El HY320 a 1080p, sin keystone ni
   zoom digital (todo lo corrige la calibración, y el keystone del proyector rompe el modelo si se
   cambia después de calibrar). Foco nítido.
2. **adb por Wi-Fi** (el USB-C queda ocupado): `adb connect 192.168.4.161:5555`.
3. **Space Setup** de la sala (recomendado; si no, se usa solo la profundidad): en el casco,
   Configuración → Entorno físico → Configuración del espacio, o botón SPACE SETUP de la app.
4. **Montaje**: Quest y proyector rígidos entre sí (nada de juego). La cámara izquierda del Quest
   tiene que ver la zona que ilumina el proyector. Si nadie lleva el casco puesto:
   `adb shell am broadcast -a com.oculus.vrpowermanager.prox_close` (o cinta sobre el sensor de
   proximidad, entre los lentes). `prox_open` lo revierte.
5. **Permisos**: la primera vez el sistema pide cámara y escena; con `adb install -g` + los
   `pm grant` de arriba no hace falta ponerse el casco. Si `USE_SCENE` no queda concedido por adb,
   hay que aceptar el diálogo en el casco una vez.
6. **Calibrar** (sala oscura, el proyector a full brillo): botón **A** del control, botón
   CALIBRAR VISTA o `cmd calibrar`. Dura ~15–20 s: negro/blanco (latencia), 40 patrones Gray,
   raycast, ajuste y lazo. El rig quieto durante la captura (si se mueve > 1 cm o 0,5° aborta).
   **Captura 2–3 vistas** moviendo/rotando el rig 10–20° entre una y otra (cada `calibrar`
   suma una vista y re-ajusta con todas). Con una sola pared plana es obligatorio; con mesa +
   objetos + pared basta una.
7. **Verificar**: `ver mapeo` + efecto `aristas` o `blanco`: las aristas proyectadas deben caer
   sobre los bordes reales. Imágenes en `/sdcard/Android/data/com.smartup.proyector/files/cal_<fecha>/`
   (`blanco.jpg`, `mascara.png`, `mapa_u.png`, `mapa_v.png`, `muestras.jpg` con el residuo por
   punto, `verif_N.jpg` esperado verde / medido rojo ×5, `pares.csv`).
8. **Ajuste fino a mano** (respaldo): stick derecho mueve ±2 px; con el gatillo apretado,
   izquierda/derecha rota ±0,2° y arriba/abajo escala ±0,2 %. **Y** guarda. Por adb:
   `cmd "ajuste 3 -2 0 1"` (dx dy rot escala, relativo), `cmd "ajuste reset"`.
9. **Modo**: **B** alterna CABEZA (de mano: pose predicha cada cuadro) / FIJO (trípode: pose del
   proyector constante en la sala, no depende del tracking cuadro a cuadro; se guarda relativa al
   ancla de la sala para que sirva en la sesión siguiente).
10. **Efecto**: **X** recorre aristas → planos → rejilla (sobre la mesa, con barrido) → blanco →
    profundidad.

Si la cámara "no ve la proyección": revisar foco/brillo, que la cámara izquierda apunte a la
zona proyectada y la luz ambiente; con `ver prueba` el proyector muestra la carta (esquina
roja arriba-izquierda, verde arriba-derecha, azul abajo-izquierda, blanca abajo-derecha, borde
magenta): si en la pared se ve la vista del casco en vez de la carta, el HDMI está en espejo y
hay que usar `salida panel` (la app lo hace sola en la calibración).

### Comandos (`cmd` = `adb shell am broadcast -a com.smartup.proyector.CMD -p com.smartup.proyector --es c`)

| comando | qué hace |
|---|---|
| `estado` | estado completo al logcat |
| `calibrar` / `cancelar` / `verificar` / `reiniciar` | captura una vista y ajusta / aborta / repite solo el lazo / borra vistas |
| `modo cabeza` · `modo fijo` · `modo fijar` | pose del render: cabeza predicha · pose de la calibración · pose actual |
| `efecto aristas\|planos\|rejilla\|blanco\|profundidad\|siguiente` | efecto del mapeo |
| `ver mapeo\|negro\|blanco\|prueba` · `ver gray 0 9 0` | qué se proyecta (carta de prueba, patrón Gray eje/bit/inverso) |
| `salida auto\|presentacion\|panel\|ninguna` | salida A/B |
| `panel <dist> <dx> <dy> <sx> <sy>` | geometría del panel fijo a la cabeza (salida B) |
| `guardar` · `cargar` | `files/calib_proyector.json` (se carga solo al iniciar) |
| `ajuste dx dy rot esc` · `ajuste reset` | ajuste manual 2D |
| `latencia <ms>` · `prediccion on\|off` | predicción de la pose (modo cabeza) |
| `profundidad [nx ny]` · `geometria mruk\|profundidad\|ambas` | malla de profundidad por el frustum del proyector · qué malla se dibuja |
| `sala` · `recargar_sala` | Space Setup del sistema · recargar salas |
| `raycast env\|sala` | la calibración usa la profundidad del entorno (default) o la sala MRUK |
| `umbral N` · `bits_omitidos N` · `promedio N` · `latencia_min ms` · `lazo_iter N` · `rejilla on\|off` | parámetros de calibración |
| `camara_debug` | `files/camara_debug.jpg`: profundidad proyectada sobre la cámara (chequeo de la extrínseca) |
| `autoprueba` · `captura_salida <nombre>` | guarda lo que va al proyector como `files/prueba_*.png` |
| `ui on\|off\|alternar` · `panelproy on\|off` · `passthrough on\|off` | operador |
| `escanear` (= `sala`) | Space Setup del sistema (MRUK `requestSceneCapture`); al volver recarga malla y anclas. Botón ESCANEAR PIEZA |
| `camara` | reabre la cámara (se reintenta sola al recuperar el foco) |
| `marcadores [ver\|grid C F [ladoMax]\|detectar on\|off\|calibrar\|off]` | grilla de QR proyectada (default 4x3, QR de ~320 px) + tracking de QR de MRUK; `calibrar` suma una vista al Calibrador. Layout en `files/marcadores.json` |
| `aruco [on\|off\|voltear\|pnp\|reset\|grilla]` | ArUco DICT_4X4_50 (OpenCV, luma completa) sobre la cámara passthrough → esquinas 3D en la malla MRUK, acumuladas 2 s por id (EMA) con rechazo de marcadores que no calzan con la homografía → plano, panel AR en el plano y PnP del proyector de la mesa (`files/calib_mesa.json`, marco de la sala, con `P_sala` re-anclable). Grilla: `files/aruco_grid.json` (o la de assets) |
| `aruco params ajustado\|fabrica` · `aruco aruco3 on\|off` · `aruco perimetro r` · `aruco paso n` · `aruco ventana s` | detector (ajustado = umbral 3..53 paso 4, perímetro 0,01, aprox. 0,05, corrección 0,8, subpíxel) y acumulación |
| `aruco ev EV` · `aruco ev auto` · `aruco exp ms [iso]` · `ae_lock on\|off [comp]` | exposición. OJO: la cámara passthrough del Quest 3 no ofrece AE lock, ni compensación (rango [0,0]), ni MANUAL_SENSOR: los comandos quedan, pero la exposición es siempre automática |
| `aruco grid C F [tam]` (en el monitor :8810) · `tools/aruco_grid.py C F tam` | genera la grilla en el Mac (cuadros blancos con ≥20 px de negro al borde), la proyecta por el runtime :8790 (`overlay`) y empuja el JSON a la app |
| `proyector mesa\|rig\|auto` · `proyector K fx [cx cy]` | qué proyector se renderiza (mesa = pose fija del PnP ArUco, con la grilla esperada en cian y las esquinas detectadas como puntos); K conocida del proyector de la mesa (con un solo plano la focal no es observable: sin K el ajuste restringido puede errar el centro en >1 m) |
| `depth N M [maxM]` | profundidad en vivo: rejilla N×M de raycasts del Environment Raycaster por los píxeles de la cámara izquierda (Spatial SDK 0.13.2 no expone el mapa de la Depth API) |

## Monitor en el browser

- **En vivo desde la app**: `http://192.168.4.161:8787/` (dashboard) y `/3d.html` (escena 3D). Endpoints:
  `/estado.json` (cabeza, cámara con pose y frustum, controles/manos, anclas MRUK, salida y displays,
  calibración, marcadores QR, ArUco y PnP de la mesa), `/cam.jpg` `/cam.mjpg` (passthrough izq., color, mitad de
  resolución), `/render.jpg` `/render.mjpg` (FBO del proyector antes del warp), `/profundidad.json` (rejilla de
  raycasts desde la cabeza), `/malla.json` (malla MRUK + profundidad, posiciones y color por triángulo), `/log`,
  `/cmd?c=...`, `/depth.jpg` `/depth.mjpg` `/depth.json` (profundidad en vivo, 5 fps), `/cam_full.png` `/cam_full.jpg`
  (cuadro de luma 1728x1728 sin reescalar, cabeceras `X-Ts-Ns`, `X-Frame-Index`, `X-Exposure`), `/cam_pose.json?ts=`
  (pose de la cámara y la cabeza en la sala para ese timestamp, intrínsecos, distorsión, extrínseca cámara↔cabeza).
  `/malla.json` trae también `anclas` (planos y volúmenes MRUK con etiqueta, pose y tamaño). Streams por defecto:
  `cam.mjpg` 864 px a 8 fps (calidad 65; `?fps=&w=&q=`), `render.mjpg` y `depth.mjpg` a 5 fps; las páginas los cortan
  con la pestaña oculta y solo piden `/estado.json` cada 2 s. Solo responde con la app al frente (en segundo plano
  Horizon OS no deja conectar).
- **En el Mac**: `.venv/bin/python tools/monitor_adb.py` → `http://127.0.0.1:8810/` (y `/3d`). Si `:8787` responde
  usa esos feeds y suma lo de adb (logcat con otros tags, dumpsys de foco/displays, batería, CPU); si no, cae a
  adb puro (manda `estado`, `camara_debug`, `captura_salida`, `profundidad` y baja `files/`). `/3d` usa
  `sala.glb` del iPad como respaldo si no hay malla MRUK.
- `tools/verificar_marcadores.py`: decodifica la grilla QR (sintética o `prueba_marcadores.png`) con OpenCV.

## Medir la latencia glass-to-wall

1. **Lazo render → cámara**: la calibración lo mide (negro→blanco, 3 ciclos) y lo deja en
   `calib_proyector.json` → `latencia_lazo_ms`. Incluye la exposición/lectura de la cámara
   (~20–40 ms), así que la latencia hasta la pared es algo menor.
2. **A ojo (la que importa)**: modo CABEZA, efecto `blanco` sobre una caja; mover el rig de lado a
   lado a velocidad constante y subir/bajar `cmd "latencia N"` hasta que el borde iluminado deje de
   adelantarse/atrasarse respecto de la caja. Ese N va al JSON con `guardar`.
3. **Con video**: grabar a 240 fps la pared y un objeto que se mueva junto al rig, contar cuadros
   entre el movimiento y el de la imagen proyectada.

## Limitaciones y siguientes pasos

- La exactitud 3D depende de la profundidad del Quest (Environment Raycaster, del orden del cm) y
  de la extrínseca de fábrica de la cámara. El lazo corrige lo que se ve desde la pose de
  calibración; para el modo cabeza conviene calibrar con 2–3 vistas.
- La cámara passthrough es de 30 fps y con exposición automática; si el HY320 es tenue, oscurecer
  la sala (o `umbral 4`, `promedio 3`). AE lock se usa si la cámara lo ofrece.
- El HY320 tiene lag de entrada propio (procesamiento, posible keystone digital): medirlo y
  ponerlo en `latencia`.
- Salida B: el espejo reescala el ojo izquierdo (pérdida de nitidez) y pasa por el compositor.
- Siguientes: streaming Wi-Fi al HY320 (es Android: APK receptor que reciba H.264/WebRTC del Quest)
  para no depender del cable; usar también la cámara derecha (estéreo, más puntos y menos
  sombras); medir y compensar latencia con predicción de mayor orden; auto-exposición por patrón.

## Resultados (5-nov-2026)

**Compilación**: `./gradlew :app:assembleDebug` OK (APK debug 78 MB, Spatial SDK 0.13.2, JDK 17,
caché Gradle en `/Volumes/Externo/cache/gradle`).

**Pruebas sintéticas** (`./gradlew :app:testDebugUnitTest`, todas pasan; datos en `app/build/sintetico/`):

| prueba | resultado |
|---|---|
| Gray code 1920x1080 → cámara 640x480 (homografía, ~3 px proy/px cám, albedo con textura, ruido ±3) | 268.060 px válidos, 0 mal decodificados, error de bloque 0,79 px; muestras subpíxel: **0,067 px** mediana (p90 0,12) |
| idem con `slscan.graycode.decode` (Python, mismas imágenes) | **100 % de bloques idénticos**, mismos válidos |
| proyector, sala (pared + mesa + caja), 1 vista, ruido 0,5 px, 3 % outliers | residuo 0,59 px (óptimo para σ=0,5), centro 0,1 mm, generalización 0,035 px; OpenCV `solvePnP` 0,588 px |
| solo pared, 3 vistas del rig | DLT completo: 0,60 px, centro 0,5 mm, generalización 0,075 px; OpenCV 0,597 px |
| solo pared, 1 vista | modelo restringido: 0,60 px, centro 1,6 mm, generalización 0,39 px |
| rejilla residual con distorsión no proyectiva (radial + ondulación) | 1,52 px → **0,47 px** |
| **cadena completa** (sala analítica + proyector y cámara en el rig, convenciones del casco) | auto-descarte de 3 bits finos por eje (≈7 px proy/px cám), 424 pares, centro **1,1 mm**, reproyección en la pared **0,17 px** (p90 0,45) |

La simulación de cadena completa encontró que con cámara y proyector juntos los bits finos del
Gray no se resuelven: por eso el decodificador elige solo cuántos bits descartar por eje (fracción
válida por bit ≥ 0,6), y la precisión subpíxel sale del ajuste afín local.

**En el casco**: ver «Pendiente» (el casco quedó sin red durante esta sesión).

### Pendiente para Max

1. Con el casco despierto y en la Wi-Fi: `tools/probar_en_casco.sh --prox` (instala, permisos,
   lanza, autoprueba del render vs slscan, `camara_debug`, malla de profundidad, captura). Deja
   todo en `descargas/prueba_<fecha>/`.
2. Mirar `camara_debug.jpg`: los puntos de profundidad deben calzar con los bordes reales.
3. Ver si la salida A funciona: `tools/cmd.sh "ver prueba"` → en el proyector/monitor debe verse
   la carta (esquina roja arriba-izquierda). Si se ve la vista del casco, es espejo: `tools/cmd.sh "salida panel"`.
4. Calibración real con el rig (pasos 6–9 de arriba) y medir latencia.
