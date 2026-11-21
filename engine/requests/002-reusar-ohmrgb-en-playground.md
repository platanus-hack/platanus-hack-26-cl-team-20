---
id: 002
title: reusar ~/ohmrgb en el playground (mapeo MIDI, tracking de perillas con OpenCV, skin)
kind: tool
from: secretaria
status: doing
created: 2026-10-15T19:20
priority: 1        # orden de Max: b, c, a, d
---
Palabras de Max: "fíjate un proyecto tengo ohm rgb, ya hay mucho para poder mapear eventos midi y también trackear con
open cv etc, obvio cada una de las perillas etc. en el proyecto de ohm rgb está la skin del ohm"

**Qué.** Aprovechar lo que ya hay en `~/ohmrgb` en vez de rehacerlo. Es de lectura para h3; no se edita ese repo sin Max.
Lo que hay (visto en su CLAUDE.md y carpeta):
- `ohmrgb.py`: driver del OHM (set_pad, flush, callbacks de pads, botones 64–80, 8 faders, 8 knobs, crossfader).
- `controller_spec.json`: spec declarativa del controlador, con el mapa de botones por coordenadas del faceplate.
- `calibrate.py`, `calibrate_live.py`, `occlusion.py`: calibración de la cámara cenital (C920, 270° + mirrorV),
  seguimiento por flujo óptico y detección por pad de qué está tapado.
- `ui/src/editor/components/Faceplate.jsx` y `constants.js`: la skin SVG del OHM con coordenadas de cada control.
- `session_controller.py` (daemon, WS en 8090) y `server.py` (8081/8082).

**Qué pasa.** En el playground, cada perilla, fader, pad y botón del OHM es un "control" con posición en la escena
(sobre la imagen de la cámara) y valor en vivo:
1. Mapeo MIDI: evento del OHM → parámetro de un efecto o del beat (con `pg_set_param`, enlazable).
2. Tracking óptico: la cámara ve el controlador y confirma/lee perillas y pads (dedo o mano encima, pad tapado), con
   OpenCV, reusando `occlusion.py` y la calibración.
3. Skin: el playground puede dibujar la skin del OHM como objeto (con sus controles) y proyectar sobre él (por ejemplo
   un halo en el pad que suena).

**Cómo se ve.** El OHM aparece en `pg_scene_objects` como objeto `instrument` con sus controles; un efecto tipo
`contour_glow` puede seguir su contorno; mover una perilla real cambia algo proyectado.

**Criterio de listo.**
- `pg_scene_objects` lista el OHM con bbox y los 8 knobs, 8 faders, crossfader, 64 pads y 17 botones con posición.
- Girar una perilla física cambia el valor visible en `pg_state` o el parámetro de un efecto enlazado.
- La cámara detecta un pad tapado (reglas de Max: solo oscurecimiento o cambio radical de color, no más brillo; ver
  su memoria `ohmrgb-occlusion-corrections.md`).
- Convivencia definida con SmartDeck: o el playground toma los LEDs mientras corre, o se reparten los pads. No se
  dejan dos procesos escribiendo los mismos LEDs.

**Dudas / choques.**
- SmartDeck (`session_controller.py`) ya es dueño del OHM para las sesiones de Claude. Si ambos corren, hay que
  decidir quién manda los LEDs. Propuesta: el playground pide el modo "cedido" mientras está activo (lo decide Max).
- La cámara de `ohmrgb` es una C920 cenital calibrada para el OHM; el playground usa otras cámaras (Osmo, FX3,
  webcam). La calibración no se traspasa sola: hay que cruzarla con el mapa 3D del playground (pedido 003).
- Orientación del OHM ya confirmada (270° + mirrorV): no se cambia sin re-verificar con capturas.

> 2026-10-15 19:15 · doing: h3 lo toma. Diseño: SmartDeck sigue dueño del puerto MIDI y de los LEDs; el playground lee los eventos por su WebSocket (ws://localhost:8090, mensajes `midi`) y, si SmartDeck no corre, abre el OHM directo con python-rtmidi usando ~/ohmrgb/ohmrgb.py como librería (sin editarlo). LEDs desde el playground solo por `set_grid_direct`/`editor_release` de SmartDeck o en modo directo.
