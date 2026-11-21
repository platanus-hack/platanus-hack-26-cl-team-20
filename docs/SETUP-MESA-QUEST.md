# Mesa con el Quest 3 como cámara (desde el 9-nov-2026)

El Quest 3 cuelga fijo junto al proyector de la mesa y es la **cámara de la mesa** (la webcam USB y la Osmo ya no
se usan). Todo el pipeline (hojas, teclado, mouse, manos, SAM + agente, catálogo animado, capas) corre con su video, y
la salida al proyector va **solo** por la ventana normal de `playground/ventana_proyector.py`.

```bash
scripts/arranque_mesa.sh             # levanta lo que falte, en orden, sin tocar lo que ya corre
scripts/arranque_mesa.sh --estado    # qué corre y qué no
scripts/arranque_mesa.sh --parar-feed   # libera la ventana para calibrar
```

Página de control: `http://127.0.0.1:8798/pg` (cámara, área proyectable con overlay, SAM, hoja).

## Componentes y puertos

```
 Quest 3 (fijo, app com.smartup.proyector)                        Mac
 ┌──────────────────────────────┐   USB: adb forward 8787    ┌───────────────────────────────────────────────┐
 │ HTTP :8787                   │ ─────────────────────────▶ │ feed  playground.superficie_feed  :8798        │
 │  /cam.mjpg  864², ~12,7 fps  │   (Wi-Fi 192.168.4.161 de   │  cámara → lienzo (0,4 mm/px) → visión:        │
 │  /cam_full.jpg 1728² luma    │    respaldo automático)     │  hojas · teclado · mouse · manos MediaPipe ·  │
 │  /cam_pose.json /depth.json  │                             │  SAM + agente (Sonnet) · Jev · catálogo       │
 │  /malla.json /estado.json    │                             │  → compone el contenido 1900x1040 (H + lazo)  │
 └──────────────────────────────┘                             │  → POST /imagen ≤5 Hz, JPEG q90               │
                                                              └──────────────────────┬────────────────────────┘
                                                                                     ▼
                                       ventana_proyector.py :8793 (ventana NORMAL 1900x1040 en el display (0,1080))
                                                                                     ▼
                                                     AirPlay → proyector Projector-6325 → la mesa → la ve el Quest
 Otros: monitor del casco :8810 (quest-nativo/tools/monitor_adb.py) · quest/server.py --https :8800/:8801 ·
        vigilante logs/quest_watchdog.sh (app al frente + cámara abierta) · runtime viejo :8790 NO se usa.
```

| Puerto | Qué | Quién lo lanza |
|---|---|---|
| 8787 (Quest; en el Mac por `adb forward`) | app del casco: cámara, pose, malla | la app; `arranque_mesa.sh` hace el forward |
| 8793 | ventana del proyector (`/mostrar`, `/gris`, `/negro`, `/blanco`, `POST /imagen`, `/estado`) | `arranque_mesa.sh` (si no corre y está el display) |
| 8798 | feed de la mesa (`/pg`, `/status`, `/vision.json`, `/recalibrar`, …) | `scripts/feed_loop.sh` (relanza si muere) |
| 8810 | monitor del casco | `arranque_mesa.sh` |
| 8800/8801 | `quest/server.py` (WebXR, señalización, `/api/jev`) | `arranque_mesa.sh` |
| 8791/8792 | secretaria (otro proceso): no usar | — |

## Espacios y archivos de calibración

| Espacio | Qué es |
|---|---|
| cámara | cuadro completo de la cámara izquierda del Quest, 1728x1728 (`/cam.mjpg` llega a la mitad por decimación: x₈₆₄ = x₁₇₂₈/2) |
| mesa | ancla MRUK TABLE en mm (origen en su centro); en la cámara x va a la derecha, y hacia abajo |
| lienzo | plano de la mesa rectificado: 2051x1156 px a **0,4 mm/px** = 820x462 mm, la caja de la huella del proyector. Max queda **arriba** (rot 180 para leer derecho, como con la Osmo) |
| contenido | la ventana del proyector, **1900x1040** (no el display 1920x1080 ni el runtime) |

Manda `captures/quest-cal-fija/` (pose fija proyector↔Quest, `calib_quest_proyector.py bundle`):

| Archivo | Para qué |
|---|---|
| `calib_proyector_mesa.json`, `calib_mesa.json`, `bundle.json` | K y pose del proyector (residuo 0,52 px); `calib_mesa.json` es el que se empuja a la app |
| `H_proj_to_mesa.json` | contenido → mesa (mm) |
| `lazo_corr.npz` | corrección de lazo cerrado en el contenido (remap 1900x1040; 0,1 mm de mediana tras 3 pasadas) |
| `lienzo_quest.json` + `.npz` | **lo que carga el feed**: `H_lienzo_to_proj` (lienzo → contenido), `H_cam_to_lienzo` (cámara 1728 → lienzo), remap denso cámara→lienzo, huella, `mm_por_px`. Lo arma `playground/lienzo_quest.py construir` |
| `cam_pose.json`, `geometria.json`, `malla.json` | pose de la cámara, plano de la mesa y malla MRUK (alturas de `/vision.json`) |

Cadena de la salida: lienzo → `H_lienzo_to_proj` → contenido → `lazo_corr.npz` → `POST /imagen`.
Cadena de la entrada: cuadro del Quest → remap cámara→lienzo (geometría: pose de la cámara ∩ plano de la mesa).
El gray code (`captures/quest-cal-20261109-real2/mapa_cam_proj.npz`) se usa para verificar: coincide con la
geometría en **0,51 mm de mediana** (p90 1,1 mm); el 6 % que difiere > 3 mm es paralaje de objetos (teclado, cables).
El remap por defecto es geométrico porque el lazo cerrado ya alineó el proyector con esa misma geometría; `--modo denso`
lo arma con la corrección residual del gray code (para usar sin `lazo_corr`).

La carpeta de trabajo del feed es `captures/mesa-quest/` (items/, teclado.json, jev_cache.json, y un `superficie.json`
generado para que `teclado_fit`, `teclado_manual` y compañía trabajen en el lienzo nuevo).

## Cámara: fps y latencia (medido el 9-nov, `python -m playground.quest_cam medir`)

| Fuente | fps | Notas |
|---|---|---|
| `/cam.mjpg?w=864&fps=15` por USB (adb forward) | **12,7** (dt mediana 80 ms, p95 ~125 ms) | ~480 kB/s. **Por defecto**: no carga la Wi-Fi del AirPlay |
| `/cam.mjpg` por Wi-Fi | 12,6 | igual de rápido; queda de respaldo automático |
| `/cam.mjpg?w=1296` | 12,7 a 864 px | la app no da más: el cuadro del monitor es la mitad del sensor y sale cada ≥70 ms (`ScanCamera.kt`) |
| `/cam_full.jpg` (1728 luma) | 145-295 ms por cuadro (USB), 175-260 ms (Wi-Fi) | solo para segmentación base y `/recalibrar` |

Latencia: escalón negro→blanco por la ventana visto por la cámara = **~313 ms** de punta a punta (AirPlay + proyector
+ exposición; igual por USB, Wi-Fi o `/cam.jpg`). El stream agrega ~5-8 ms sobre un `/cam.jpg` sondeado: la latencia
propia de la cámara es ~40 ms. El stream trae croma (color); `/cam_full.jpg` es solo luma: la segmentación base combina
la luma de 1728 con la croma del stream.

## Recalibrar y medir

- **Verificar** (cada vez que se mueva algo): `curl 'http://127.0.0.1:8798/recalibrar'` proyecta 12 cuadrados de 36 mm
  (par patrón/invertido, binaria por signo, ~3 s) y responde el residuo en mm entre donde se dibujó y donde la cámara
  lo ve. `?ajustar=1` corrige H cámara→lienzo si la mediana pasa 1,5 mm (respaldo en `lienzo_quest.prev.json`).
  Imagen de control: `captures/quest-cal-fija/recalibrar_quest.jpg`; historial: `recalibraciones.jsonl`.
- **Si se movió el proyector o el Quest**: `scripts/arranque_mesa.sh --parar-feed`, luego
  `.venv/bin/python playground/calib_quest_proyector.py verificar` (umbral 3 px / 2 mm) y, si no pasa, captura nueva +
  `bundle --push` + `lazo --sesion captures/quest-cal-fija`; después `.venv/bin/python -m playground.lienzo_quest
  construir --scan captures/mesa-quest` y relanzar con `scripts/arranque_mesa.sh`. Mientras se proyectan patrones:
  `log stream --predicate 'composedMessage CONTAINS "DCPEXT"'` y parar si aparece algo.
- **Ver el lienzo**: `.venv/bin/python -m playground.lienzo_quest ver` (lienzo de un cuadro completo con la huella y una
  rejilla de 10 cm) o `/frame.jpg` del feed.
- **Medir la cámara**: `.venv/bin/python -m playground.quest_cam medir [--latencia]` (la latencia proyecta 4 escalones).
- **Teclado** (sin proyectar): `/seg/base?agente=1` → máscara del teclado en `items/<fecha>/` →
  `.venv/bin/python -m playground.teclado_fit captures/mesa-quest --lienzo items/<fecha>/lienzo.jpg --mask
  items/<fecha>/<n>_teclado_mask.png --save` → el feed lo recarga solo (mira el mtime de `teclado.json`).

## Qué NO hacer

- Nada de pantalla completa, ventanas sin bordes ni presentación directa en el proyector: tres kernel panics DCPEXT
  (8 y 9-nov). La salida es la ventana normal de `ventana_proyector.py`; no se reinicia esa ventana.
- No usar `displayplacer` mientras algo presenta en el proyector.
- No lanzar el runtime viejo `:8790` (`playground serve --projector`, `scripts/runtime_loop.sh`): el feed ya no le habla.
- No calibrar con el feed mandando cuadros: la ventana es una sola; `--parar-feed` o `/rojo?on=0` antes.
- No abrir más de un cliente pesado de `/cam.mjpg`: la app comparte el índice del último cuadro entre clientes.

## Estado de cada capa (9-nov)

| Capa / función | Estado con el Quest |
|---|---|
| Salida por la ventana (`POST /imagen`, ≤5 Hz, JPEG q90, lazo aplicado) | ✔ |
| Fondo negro (luz 0, sin autoluz) | ✔ por defecto |
| Catálogo animado (brillo 0,5) | ✔ por defecto, sobre la última carpeta refinada de `captures/mesa-quest/items/` |
| Manos MediaPipe | ✔ (el stream trae color; con cuadros grises igual corre) |
| Piel por color (etiquetas de SAM) | se apaga sola si la cámara entrega gris |
| Mouse | ✔ con umbrales relativos (croma corrida del Quest); capa apagada por defecto |
| Hojas | detector por brillo (sin cambios); capa apagada por defecto |
| Teclado | grilla por `teclado_fit` sobre la máscara SAM del lienzo nuevo; capa apagada por defecto |
| Moledor (Hough) | da falsos positivos con lazos de cable; capa apagada por defecto |
| SAM + agente (`/seg/base`, rebase cada 45 s) | ✔ con cuadro completo 1728 coloreado; agente sonnet (q31 pendiente para pasar a jev) |
| Alturas 3D en `/vision.json` (`alturas`) | rayo cámara→malla MRUK: estática (Space Setup), no ve manos ni objetos nuevos |
| Pantalla virtual sobre la hoja (`/vd`) | sin cambios (va dentro del cuadro de la ventana) |
| Toques/manos al runtime | apagado (no hay runtime) |
