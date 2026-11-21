# Audio reactivo

El bajo (y lo que suene) mueve la escena: captura en el Mac → análisis a 50 Hz → `scene.audio` en el runtime → efectos.

## Correr

```
.venv/bin/python -m playground.audio devices                       # entradas en orden de preferencia, cuál se elige, cuáles son multicanal
.venv/bin/python -m playground.audio run                           # captura todos los canales y publica al runtime (PLAYGROUND_URL o :8790)
.venv/bin/python -m playground.audio run --device QX1222 --meter   # dispositivo a mano + medidor en la terminal
.venv/bin/python -m playground.audio run --no-post --seconds 10    # solo mirar niveles, sin runtime
```

Desde la secretaria: `pg_audio start` / `status` / `stop` / `devices` / `simulate '{"bass":0.9,"beat":1}'`.

Efectos: `bass_pulse` (halo del Buda y teclas con el bajo, flash en cada golpe), `bass_ripple` (ondas desde los platos
de la DDJ y el Circuit en cada golpe), `channel_react` (cada parte sigue a su canal, ver abajo); `contour_glow` y
`control_glow` aceptan `audio: "bass"` (apagado por defecto). Cualquier parámetro sigue al audio con
`pg_bind audio/bass effect:e3:width_mm lo 10 hi 90` (o `audio/ch2.rms`, `audio/bpm → beat:bpm`).

## Qué dispositivo

Orden automático: **mezcladora** (Behringer QX1222USB; macOS la suele mostrar como «USB Audio CODEC») → **loopback**
(BlackHole / Loopback, el master de Ableton; se salta si entrega silencio digital) → **micrófono** del Mac. Hoy (16-oct,
02:30) el Mac **no ve la QX1222USB**: no aparece en CoreAudio ni en el bus USB (solo la Osmo y el adaptador HDMI). Si
su USB está en la torre, hay que pasarlo al Mac. Mientras, se usa el micrófono interno (mono).

## Lo que publica

`sub` 25–60 Hz · `bass` 60–180 · `mid` 180–2k · `high` 2k–8k · `rms` · `beat` (1 en el golpe, cae en ~0,15 s) ·
`onsets` (contador) · `bpm` · `ch` = lo mismo por canal de entrada (`ch1`, `ch2`…; sin tempo). Todo 0..1 con **AGC
lento** por banda y por canal: no hay que tocar ganancia (micrófono lejano o línea caliente terminan en el mismo rango;
si baja 20 dB, se recupera en ~7 s). Golpes: subida de energía en 30–180 Hz con umbral adaptativo; un canal de solo
hi-hats no cuenta como golpe.

## Canales → escena (`mapping.json`, efecto `channel_react`)

Por defecto: canal 1 (L) → halo del Buda · canal 2 (R) → teclas · bajo de la mezcla → platos de la DDJ (anillos en
cada golpe) · agudos → cocodrilo · medios → Circuit Tracks. Se edita el JSON y se recarga solo (si queda mal escrito,
sigue el último bueno y el error sale en el estado del efecto). Un canal que el dispositivo no tiene cae a la mezcla.

## Más de 2 señales separadas (lo que hay que enrutar en la mesa)

El USB de la QX1222USB entrega **solo 2 canales: la mezcla principal L/R**. Para que cada instrumento llegue aparte:

1. **Paneo duro** (sin cables nuevos): PAN del bajo todo a la izquierda y del sinte/teclas todo a la derecha →
   `ch1` = bajo, `ch2` = teclas. Ojo: los parlantes también quedan separados L/R.
2. **ALT 3-4** (subgrupo): el botón MUTE/ALT 3-4 de un canal lo saca de la mezcla principal y lo manda a las salidas
   ALT 3-4. Esas salidas a **otra interfaz de audio** (o a las entradas de una con 4+ canales) = 2 señales más.
   Para seguir escuchándolo, CONTROL ROOM/PHONES con fuente ALT 3-4 o un retorno estéreo.
3. **AUX/FX send o INSERT**: el envío AUX de un canal (pre-fader) o un cable en el INSERT de un canal mono (en la
   primera muesca del plug, sin cortar la señal) da una salida directa de ese canal → a otra interfaz.
4. **Interfaz con 4+ entradas** (cada instrumento directo): la captura abre todos los canales sola.
5. **Ableton → BlackHole 16ch**: cada pista a su par de salidas (Audio To: Ext. Out 3/4…) con BlackHole 16ch como
   salida; `--device BlackHole` y `ch3`, `ch4`… son esas pistas. BlackHole se instala con permisos de admin (Max).

`python -m playground.audio devices` marca **MULTICANAL** las entradas con más de 2 canales; `pg_audio devices` las
lista en `multichannel`. Hoy en el Mac: ninguna.

## Latencia (medida 16-oct con el micrófono del Mac, música sonando en la sala)

| Tramo | ms |
|---|---|
| ADC → callback (marcas de tiempo de CoreAudio, primera muestra del bloque) | 21,5 |
| Espera del bloque de 20 ms (promedio) | 10 |
| Análisis (por cuadro, 1 canal) | 0,3 |
| POST al runtime (keep-alive, local) | 1,0–1,2 |
| Captura → runtime (última muestra del bloque → `scene.audio`) | 2,7 |
| Tick del runtime (26,8 fps proyectando: espera promedio) | ~18 |
| Ventana SDL al proyector (`show`) | 9 |

Golpe sintético → detectado: 1 cuadro (20 ms) después. Suma estimada sonido → luz: **~60 ms** más el retardo propio del
proyector. PortAudio declara 71 ms de latencia nominal para el micrófono interno; si esa cifra es la real, ~110 ms. Una
interfaz USB (la QX1222USB) suele bajar la entrada a 5–10 ms. Medición acústica pendiente:
`python -m playground.audio latency` toca golpes graves bajitos por el parlante y los detecta (no se corrió: había
música sonando).
