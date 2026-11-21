# Manos y mesa táctil (pedido 004, webcam de pared del 009)

La webcam del PC (Logitech Brio 100, en la pared mirando el escritorio) ve las manos; MediaPipe saca 21 puntos por
mano; en el Mac la punta del índice pasa al espacio del proyector con la homografía del plano de la mesa y se
convierte en toques `down / move / up` que llegan al runtime del playground.

```
PC NEW-WHITE-4K                                   Mac
 Brio (DSHOW, 1920x1080 MJPG 30 fps)               client.HandsClient   (reconecta solo, desfase de relojes)
  → windows/hands_service.py                        → calib.Calibration (brio px → proj px → mm)
     MediaPipe HandLandmarker (CPU, 960 px)         → touch.TouchTracker (histéresis, EMA, 3 px, zonas)
     WS  :8793  manos en JSON, 15–30 Hz  ────LAN──▶ → bridge.TouchBridge → POST /api/command {"cmd": "touch"}
     HTTP :8794 /health /frame.jpg /frame.png /hands.json
```

| Archivo | Qué hace |
|---|---|
| `windows/hands_service.py` | servicio del PC (autocontenido: cv2, numpy, websockets, mediapipe). `--fake` sin cámara ni modelo |
| `playground/hands/model.py` | mano de 21 puntos, mensaje, tamaño de palma, mano sintética |
| `playground/hands/client.py` | cliente WS: último estado, reconexión, latencia captura→Mac |
| `playground/hands/calib.py` | Brio→proyector por gray code, puntos proyectados o 4 puntos; proyector→mm |
| `playground/hands/touch.py` | lógica táctil y zonas excluidas |
| `playground/hands/bridge.py` | toques → runtime (formato propuesto del comando `touch` en su docstring) |
| `playground/hands/__main__.py` | CLI `python -m playground.hands …` |

## Instalar y lanzar en el PC (desde el Mac, por SSH)

Todo con `slscan remote` (cwd `C:\sl-scanner`, venv en el PATH). En la tailnet sirve el nombre del PC en vez de la IP.

```sh
cd ~/hardware/sl-scanner

# 1. código (copia slscan/, playground/, windows/, tests/)
.venv/bin/python -m slscan remote sync --host 192.168.1.233 --user "Roberto Mardones"

# 2. mediapipe SIN tocar OpenCV: la 1.1.1 declara opencv-contrib-python, que pondría un segundo cv2 encima del
#    opencv-python 5.0 del venv. Con --no-deps y sus dependencias reales (verificado con --dry-run el 16-oct: agrega
#    12 paquetes, no cambia numpy ni OpenCV):
.venv/bin/python -m slscan remote run --host 192.168.1.233 --user "Roberto Mardones" \
  "uv pip install --python .venv/Scripts/python.exe --no-deps mediapipe==1.1.1; uv pip install --python .venv/Scripts/python.exe 'absl-py~=2.3' 'flatbuffers~=25.9' 'sounddevice~=0.5' matplotlib"

# 3. prueba sin cámara (cuadros y mano sintéticos) y luego con la Brio. Vive mientras dure la sesión SSH (Ctrl+C)
.venv/bin/python -m slscan remote run --host 192.168.1.233 --user "Roberto Mardones" "python windows/hands_service.py --fake"
.venv/bin/python -m slscan remote run --host 192.168.1.233 --user "Roberto Mardones" "python windows/hands_service.py"
```

- El modelo `hand_landmarker.task` (~7,8 MB) se baja solo a `C:\sl-scanner\models\` la primera vez (de
  `storage.googleapis.com/mediapipe-models/...`, ver `MODEL_URL` en el servicio). `models/` no lo pisa `remote sync`.
- Opciones útiles: `--camera 1` (otro índice DirectShow), `--hz 20`, `--det-width 640` (más rápido),
  `--num-hands 2`, `--handed-as-is` (no invertir izquierda/derecha), `--detector none` (solo cuadros, para calibrar).
- Si mediapipe no carga, el servicio igual arranca y sirve cuadros; `/health` muestra el error en `detector.error`.
- **Una sola dueña de la Brio**: DirectShow no comparte la cámara. Si corre `windows/camserve.py` (servidor de
  cuadros de la Brio, mismo puerto 8794), hay que pararlo antes. `hands_service` sirve las mismas rutas, las mismas
  claves de `/health` (`camera_open`, `frame`, `seq`, `fps`, `mean`, `age_s`, `hint`) y la cabecera `X-Frame-Seq`,
  así que `slscan.capture.RemoteCamera` y `playground.dualview` siguen funcionando contra él.
- **Firewall** (una vez, PowerShell de administrador en el PC; igual que la regla del runtime en `autologin.ps1`):

  ```powershell
  New-NetFirewallRule -Name sl-scanner-hands -DisplayName "manos 8793-8794 solo LAN y tailnet" -Direction Inbound `
    -Protocol TCP -LocalPort 8793,8794 -Action Allow -Profile Any -RemoteAddress @("LocalSubnet","100.64.0.0/10")
  ```

  El servicio además rechaza por su cuenta cualquier IP que no sea loopback, privada o de la tailnet.
- **Dejarlo corriendo** sin una sesión SSH abierta: tarea al iniciar sesión, como `sl-scanner-playground`. Cambia el
  PC de Roberto (persistencia): se corre solo con el OK de Max.

  ```powershell
  $py = "C:\sl-scanner\.venv\Scripts\python.exe"; $u = "$env:USERDOMAIN\$env:USERNAME"
  $a = New-ScheduledTaskAction -Execute $py -Argument "windows\hands_service.py" -WorkingDirectory "C:\sl-scanner"
  $s = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
  Register-ScheduledTask -TaskName "sl-scanner-hands" -Action $a -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $u) -Settings $s -User $u -RunLevel Limited -Force
  Start-ScheduledTask -TaskName "sl-scanner-hands"
  ```

Desde el Mac:

```sh
.venv/bin/python -m playground.hands status --pc 192.168.1.233            # /health
.venv/bin/python -m playground.hands watch  --pc 192.168.1.233 --seconds 10   # manos, fps, latencia captura→Mac
open http://192.168.1.233:8794/frame.jpg                                   # lo que ve la Brio
```

## Calibrar (Brio → proyector del Mac, `proj0` = T749 en el display 1)

La Brio tiene que ver la zona que ilumina el proyector y no moverse después. Lo que sale queda en
`captures/brio_calib.json` (fuera de git, como el resto de `captures/`).

1. **Gray code** (lo mejor): el Mac proyecta la secuencia de `slscan scan` y baja cada cuadro de la Brio sin pérdida
   (`GET /frame.png?fresh=3&gray=1`). Unos 40 s; no tocar la mesa.

   ```sh
   .venv/bin/python -m playground.hands calib-gray --pc 192.168.1.233 --screen 1 --mm-segments captures/scan2/segments.json
   .venv/bin/python -m playground.hands calib-gray --decode-only --skip-bits 3     # recalcular sin volver a capturar
   ```

   Imágenes en `captures/brio_scan/brio/` (44 PNG en grises, decenas de MB: borrarlas si falta disco; `map.npz`
   queda al lado). Se aceptan `stats.inlier_frac` alto (la mesa domina; los objetos son outliers) y
   `stats.err_px_median` < 2 px de proyector. `--skip-bits` (2 por defecto) ignora los bits que la Brio no
   resuelve, como `--skip-bits 3` en el escaneo de la Osmo.
2. **Puntos proyectados** (respaldo rápido, ~10 s): `calib-dots --pc 192.168.1.233 --screen 1 --grid 4x3`.
3. **4 puntos a mano** (sin proyector a mano): un JSON y `calib-points puntos.json`.

   ```json
   {"brio": [[412, 300], [1530, 288], [1585, 905], [360, 920]],
    "proj": [[160, 160], [1759, 160], [1759, 919], [160, 919]],
    "mm":   [[0, 0], [1150, 0], [1150, 545], [0, 545]]}
   ```

   `brio`: px en `/frame.jpg` (tamaño completo); `proj`: px del proyector de las mismas marcas; `mm` opcional.
   `Calibration.load` también lee este archivo directo.

**Métrica (mm de la mesa)**, opcional en cualquiera de los tres: `--mm-segments captures/scan2/segments.json` (hoja
carta `papel`: origen en su esquina, vale si la hoja está sobre la mesa), `--mm-rig captures/rig.json [--plane-z 0]`
cuando el rig de F1 esté calibrado con el mundo en la mesa, o el campo `mm` del JSON de puntos.

**Referencia de mano apoyada** (para el modo `apoyo`): mano plana sobre la mesa, quieta, 3 s.

```sh
.venv/bin/python -m playground.hands rest --pc 192.168.1.233
```

## Touch

```sh
.venv/bin/python -m playground.hands run --pc 192.168.1.233 --runtime http://127.0.0.1:8790 \
    --exclude captures/scan2/workbench.json [--exclude-names cocodrilo,torre,buda] [--mode auto|pinch|apoyo]
```

Imprime cada evento con px, mm, gatillo y latencia captura→evento, y al final las estadísticas. Sin `--runtime`
solo imprime.

- **pinch**: pulgar con índice. Explícito y robusto; no exige tocar la mesa.
- **apoyo**: la mano sobre la mesa, por el tamaño de la palma llevada al plano de la mesa contra la referencia
  (`ratio` < 1,06 baja, > 1,12 sube, 2 cuadros seguidos). Sin `rest` la referencia se aprende sola (percentil 10 de
  lo visto): funciona si la mano se apoya de vez en cuando.
- **auto** (por defecto): cualquiera de los dos.
- Suavizado EMA (`--ema 0.5`) y zona muerta de 3 px (`--deadband`): el temblor no genera `move`.
- Zonas: los polígonos de `workbench.json` o `segments.json` (px de proyector; todos menos `mesa` y `papel`, o los
  de `--exclude-names`). Un toque que empezaría encima de un objeto no se emite; un arrastre que empezó en la mesa
  sigue aunque cruce uno.

En el runtime: el comando `touch` existe (16-oct) con el formato de `bridge.py`: deja los toques en
`scene.touches` y `scene.touch_log`, los publica en `/api/events` y los consumen los instrumentos de
`playground/touchsynth` (cocodrilo, dibujo, marimba, linterna). `pg_touch` y `pg_gestures` los muestran. Con un
runtime viejo sin el comando el puente sigue funcionando como antes (anota `runtime_sin_touch` y sigue).

## Mensajes

WS `:8793`, un JSON por cuadro procesado:

```json
{"type": "hands", "t": 1792125835.257, "seq": 1089, "size": [1920, 1080], "fps": 29.9, "det_ms": 14.2,
 "detector": "mediapipe-tasks",
 "hands": [{"id": 3, "handed": "Right", "score": 0.97, "landmarks": [[x, y, z], "...21"],
            "tip_index": [x, y], "pinch": false, "pinch_ratio": 0.83}]}
```

`landmarks` en px de la Brio; `z` es la de MediaPipe (relativa a la muñeca, en la escala de x; negativo = más cerca
de la cámara). `t` es la hora de captura en el reloj del PC: el cliente manda `{"type": "ping", "t0"}`, recibe
`{"type": "pong", "t0", "t"}` y corrige el desfase (muestra de menor ida y vuelta).

HTTP `:8794`: `GET /health`, `GET /frame.jpg?q=80&w=960`, `GET /frame.png?fresh=3&gray=1` (sin pérdida, espera N
cuadros nuevos: para calibrar), `GET /hands.json`.

## Tests

```sh
.venv/bin/python -m pytest tests/test_hands.py -q
```

Sin hardware: cliente contra un servidor WS falso (desfase de relojes, reconexión, servidor caído), homografía de 4
puntos y archivo de respaldo, gray code sintético (patrones de `slscan.graycode` deformados por una homografía
conocida, error < 0,7 px), puntos proyectados, métrica por hoja y por rig, lógica táctil con landmarks sintéticos
(down/move/up, temblor, histéresis, pinch, mano perdida, dos manos, referencia aprendida), zonas excluidas, puente
(objeto, `Runtime` real y HTTP) y el servicio del PC en modo `--fake` de punta a punta.

## Límites y pendientes

- **No probado todavía con la Brio ni con MediaPipe**. Probado: `--fake` en el PC (Python 3.12, 16-oct 01:50) y la
  cadena servicio → cliente → toques → runtime por HTTP en el Mac; el `--dry-run` de la instalación en el PC. Con la
  Brio no se pudo: a esa hora la tenía `camserve.py`. Falta: instalar mediapipe, abrir el firewall, medir fps y
  `det_ms` reales y la latencia de punta a punta (exposición + MJPG + MediaPipe ~15–25 ms en CPU a 960 px + LAN);
  `watch` y `run` la informan.
- **Una sola cámara en ángulo no mide altura fina**: con la Brio a ~1,5 m, 3 cm cambian el tamaño de la palma ~2 %,
  cerca del ruido. `apoyo` distingue apoyada de levantada a varios cm, no "rozando" de "tocando". Para el criterio
  del 004 (error en mm contra un punto marcado y mano flotando que no dispara) hace falta otra vista: triangular la
  punta con la Osmo o la FX3 cuando el 009 calibre las cámaras, la sombra del dedo bajo el proyector, o profundidad
  (VDA métrico). Mientras, `pinch` es el gatillo confiable.
- Con la proyección encendida, la luz sobre la mano puede bajar la detección de MediaPipe: medirlo (criterio del 004).
- La calibración y las zonas son del proyector del Mac (`proj0`). Para el proyector del PC se repite con ese display.
- Las zonas salen del escaneo (estáticas): si se mueve un objeto, hay que volver a segmentar.
- Fusión con la Osmo (posición precisa en el proyector + apoyo de la Brio): `playground/touchsynth/fusion.py`.
