# Mesa táctil musical (touchsynth)

Cocodrilo sintetizador y cocodrilo vivo, dibujo sobre la mesa, marimba y mano linterna, con toques que vienen de la
Osmo cenital, de la Brio del PC o de las dos fusionadas. Todo en px del proyector (1920x1080, T749) y con el escaneo
vigente **`captures/scan5`**; con la escena del escaneo en el runtime (`scene_from_scan`) el contorno del cocodrilo
es el vivo de la escena (sigue a `nudge` y `reload_scene`).

```
Osmo (Mac) ─ vista del proyector ─ OsmoHands ─┐
Brio (PC) ─ MediaPipe ─ BrioContact ──────────┴─ Fusion ─ HTTP: comandos "touch" / "hands" del runtime (:8790)
                                                            └─ croc_synth · croc_alive · touch_draw · marimba · linterna
                                                                 ├─ proyector (lo maneja el runtime: serve --projector)
                                                                 └─ sonido: la mesa (PC) › el MacBook › Ableton
```

## En vivo

**Nada de esto abre ventanas en el proyector**: el runtime (`python -m playground serve --projector …`) es el único
dueño de la salida. Los efectos se spawnean por su API y las pruebas cortas van con el comando `overlay`.

```sh
cd ~/hardware/sl-scanner
.venv/bin/python -m playground.touchsynth live --modes croc,vivo,draw --ver -v     # Ctrl+C para salir
.venv/bin/python -m playground.touchsynth live --modes linterna                     # mano linterna
```

- `live` es un cliente del runtime: spawnea los efectos de `--modes` (y los mata al salir; `--keep` los deja),
  mide el aporte del proyector mostrando 4 tableros con `overlay` (~5 s: **sin manos en la mesa**), baja lo que el
  runtime proyecta (`/api/frame.png`, ~8 Hz) para el modelo de la Osmo y manda los toques (`touch`) y las manos
  (`hands`). `--ver` agrega `touch_marks` (anillo en cada punta, verde = toca) y `--luz-base 18` un gris tenue para
  que la Osmo vea la mano aunque la visual sea negra.
- `pg_touch list` da toques, manos, el estado de cada instrumento y el de la fusión (`en_vivo.fusion.modo`:
  fusion | solo-osmo | solo-brio, `degradado`).
- Brio: si corre `windows/hands_service.py` en el PC (`--brio-ws ws://192.168.1.233:8793`, por defecto) y existe
  `captures/brio_calib.json` (`python -m playground.hands calib-gray …`), la fusión usa la Osmo para x,y y la Brio
  para el apoyo. Sin servicio: solo Osmo (degradado, lo dice el estado). `--brio-ws none` la apaga.
- Sin cámaras: `pg_touch tap target croc:3` / `marimba:5` / `x y`, o `python -m playground.hands run --runtime …`.
- Comprobar sin proyector: `python -m playground.touchsynth zones` (zonas y notas) y `preview` (PNG del lienzo con
  toques sintéticos en `reports/`).

## Sonido: por la mesa primero

Las notas las genera el runtime (los instrumentos viven ahí). Salida `auto`:

1. **La mesa (PC)**: el receptor `windows/midi_play.py` las toca con el "Microsoft GS Wavetable Synth" y el audio
   sale por el dispositivo predeterminado de Windows. Para que sea la mesa: en el PC, Configuración > Sistema >
   Sonido > Salida > **"Altavoces (USB Audio CODEC)"** (así aparece la QX1222USB; o clic derecho en el parlante de la
   barra de tareas > Configuración de sonido). El runtime lo busca solo: primero por UDP 8797 (necesita una regla de
   firewall, ver el docstring de `midi_play.py`; hoy no está) y si no, **lo lanza por ssh** y le escribe por la sesión
   (no abre puertos ni deja nada corriendo en el PC: muere con el runtime). Programas GM: cocodrilo pad cálido (89),
   marimba (12), dibujo cristal (98).
2. **El MacBook**: mientras la mesa no contesta suena un sintetizador simple del Mac (sounddevice); se cambia solo a
   la mesa cuando aparece.
3. **Ableton**: el puerto virtual "sl-scanner touch" (o IAC) queda siempre abierto en paralelo.

`pg_touch sonido` muestra el estado; `pg_touch sonido salida mac|pc|pc-ssh|virtual|null|auto` lo cambia en caliente
(`TOUCHSYNTH_MIDI` al lanzar el runtime hace lo mismo). Probar el PC a mano:
`ssh "Roberto Mardones@192.168.1.233"` y `python windows\midi_play.py --test` (3 notas) o `--list` (puertos).

## Modos (efectos de la galería, se spawnean como cualquier otro)

| efecto | qué hace | params útiles |
|---|---|---|
| `croc_synth` | contorno del cocodrilo en 10 zonas de color, cabeza (grave) → cola; tocar = nota + pulso, mantener = sostener, deslizar = glissando, a lo ancho = CC1. MIDI canal 1 | `zones` 8-12, `scale` (pentatonica_mayor, pentatonica_menor, dorica, blues, hirajoshi…), `root` 48, `head` auto/izquierda/derecha, `split` largo/area, `invert`, `visual` false = solo sonido |
| `croc_alive` | cocodrilo vivo (pedido 018): patas detectadas en el contorno con rigging que se mecen lento y aletean al tocar; respira ±2 % con el audio (bajo), el beat o lento; escamas hexagonales que se encienden por zona con una onda desde el dedo | `flap_deg`, `flap_hz`, `flap_s`, `idle_deg`, `breath`, `breath_src` auto/audio/beat/lento, `cell_px`, `palette`, `foto` (textura de la foto del escaneo); `set_param <id> aletear true` aletea sin tocar |
| `touch_draw` | el dedo pinta; trazos que se apagan | `fade_s` (0 = para siempre), `hold_s`, `color_mode` velocidad/zona/fijo, `width`; borrar: `set_param <id> clear true`, `pg_touch clear` o dos manos quietas 1,2 s (o separándose rápido) |
| `marimba` | barras en la zona libre más grande (mesa menos objetos del escaneo); golpe = nota corta, arrastre = redoble. MIDI canal 2 | `bars`, `scale`, `root` 60, `dur_s`, `rect` [x,y,w,h] para ponerla a mano |
| `linterna` | la mano es una luz: relieve del escaneo con normales, caída radial, cono y sombras | `radio`, `intensidad`, `color`, `altura`, `relieve`, `cono`, `inclinacion`, `sombras`, `ambiente`, `demo` |
| `touch_marks` | anillos en los dedos detectados y luz base (lo pone `live --ver`) | `luz_base`, `rings` |

### Interactividad genérica (cualquier objeto de la escena, nada por nombre)

Lógica en `objetos.py` (helpers + `ObjetoSynth`/`ObjetoVivo`) e `interactivo.py`. Objetos = los de la escena con
contorno en px del proyector (escaneo, o `extra.projector_contour_px` de objects.json), menos la mesa y lo provisional;
cada uno con un color estable por id. `croc_synth`/`croc_alive` quedan como alias (objeto=croc, mismo comportamiento).

| efecto | qué hace | params útiles |
|---|---|---|
| `objeto_synth` | cualquier objeto (target del spawn o `objeto`; vacío = el más grande) en N zonas a lo largo de su eje mayor; MIDI canal 1. Tocar por nombre: `target '<id>:3'` | `zones`, `scale`, `root`, `objeto`, `visual` |
| `objeto_vivo` | el objeto cobra vida: celdas, respiración, "patas" donde el contorno tiene salientes; despierta al tocarlo y se adormece | `res` (0.5: se calcula a media resolución), `despierto_s`, `dormido`, `color` auto, `aletear` |
| `toque_onda` | cada toque lanza una onda; ilumina el borde de los objetos que alcanza (su color), destello, nota (canal 3) y rebote | `velocidad`, `alcance`, `banda`, `rebote`, `sonido`, `objetos`, `arrastre_px` |
| `mano_luz` | cada mano (o toque) es una luz sobre el relieve (height_proj), solo en su ventana; halo en los objetos cercanos; 2 manos = 2 luces | `radio`, `alcance`, `colores`, `escala`, `demo` |
| `juego_memoria` | Simón con los objetos; puntaje en el HUD (el plano más chico o `hud`); canal 4 | `inicio`, `paso_s`, `hud`, `objetos`, `semilla` |
| `mirada` | foco donde se mira (`scene.gaze`, comando `gaze {x, y}` o `{objeto}`), el objeto mirado se enciende; `dwell_s` = elegido | `radio`, `dwell_s`, `margen_px`, `demo` |

Sin cámaras: `pg_touch tap target buda` (cualquier objeto: su centro), `memoria` (el que toca ahora en el juego),
`gaze {"objeto": "buda"}` o `gaze {"x": 700, "y": 300}`; `gaze {"off": true}` la borra.

Cocodrilo que suena y se mueve: `croc_synth` con `visual: false` + `croc_alive` (este mira los toques sin
quitárselos al sintetizador). Sin escena del escaneo, el contorno sale de `workbench.json` (`segments.json` de
respaldo). Los toques se reparten: cocodrilo > marimba > dibujo (un toque que empieza en el cocodrilo no dibuja).
Ableton: pista MIDI con "MIDI From: sl-scanner touch" (activar el puerto en Preferencias › MIDI) y el canal.

## Fusión de cámaras (`fusion.py`, `osmo_hands.py`)

- **Osmo**: cada cuadro se remapea a la vista del proyector con `proj_to_cam.npz` (a 1/3: 640x360). Modelo
  "híbrido": fondo aprendido en vivo (absorbe la luz ambiente, objetos nuevos y la auto-exposición de la Osmo) + el
  aporte conocido del proyector (medido con 4 tableros complementarios; su ganancia sale de los cambios entre
  cuadros) comparado con lo que se proyectó 30–400 ms antes (latencia), así las visuales que cambian no son manos
  (en vivo con la linterna moviéndose: 0,2-0,4 % del cuadro como primer plano). Si más de un tercio del cuadro sale
  como primer plano (alguien inclinado sobre la mesa, fondo mal aprendido) no reporta manos y a los 20 cuadros
  reaprende el fondo. Punta = punto más lejano a donde el brazo entra por el borde. Contacto por la sombra del dedo:
  el proyector la tira justo en el px del dedo, así que punta ↔ sombra ≈ 0 al tocar.
- Rendimiento: 9 ms por cuadro solo (antes, con todo en un proceso, 40-60 ms); ahora `live` corre aparte del
  runtime, así que la detección ya no compite con el dibujo.
- **Brio**: contacto por pinch o apoyo (tamaño de la palma en el plano de la mesa) y altura relativa.
- **Fusión**: posición de la Osmo, contacto de la Brio. Con una sola cámara funciona degradado: solo Osmo = sombra
  o, si no hay sombra visible, dedo quieto 0,3 s = toque; solo Brio = posición por homografía.

## Qué falta

- **Brio en la mesa**: hoy apunta al piso y el servicio de manos (MediaPipe) no está instalado en el PC: la fusión
  real Osmo + Brio no se probó en vivo (sí con tests). Falta: apuntarla, `calib-gray` y correr el servicio.
- **Toques con la mano real**: probado en vivo el lazo completo (Osmo, modelo, proyector, toques por HTTP, MIDI,
  zonas sobre el cocodrilo) en tandas de menos de 20 s, pero sin una mano tocando a propósito; falta afinar `thr`,
  la sombra (`gap_touch`) y la latencia con Max en la mesa, con la luz apagada. La Osmo en modo webcam tiene
  auto-exposición: si se puede bloquear en la Osmo (AE lock), mejor.
- Marimba: en scan5 la zona libre que encuentra es chica (256x256 px arriba a la derecha); `rect` para ponerla donde
  Max quiera.
- La linterna usa la altura relativa del escaneo (sin rig métrico); `relieve` la escala a ojo.
- **Sonido por la mesa**: probado desde el Mac por ssh (el receptor arrancó en 0,6 s, tocó en el GS Wavetable y
  el micrófono del Mac midió el acorde en la sala: RMS ×3,4). Falta que Max confirme que sale por la QX1222USB
  (dispositivo predeterminado de Windows) y el OK para una regla de firewall UDP 8797 o una tarea programada, si
  prefiere el receptor siempre prendido en vez de lanzado por ssh. El GS Wavetable tiene latencia propia (~50-100 ms).
- **Cocodrilo vivo**: en scan5 aparecen las 2 patas visibles (las del otro costado quedan tapadas); el aleteo
  proyectado se probó por `overlay` (luz sobre el cuerpo real, bien alineada), falta verlo andar en sala oscura con
  Max tocando.
- Hot reload: los efectos se recargan solos; los módulos de `touchsynth/` no (reiniciar el runtime).
