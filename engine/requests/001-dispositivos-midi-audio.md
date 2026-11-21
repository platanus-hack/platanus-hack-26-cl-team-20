---
id: 001
title: los 4 equipos de Max como dispositivos MIDI y de audio del playground
kind: tool
from: secretaria
status: open
created: 2026-10-15T19:20
priority: 3        # orden de Max: b, c, a, d
---
Palabras de Max: "tengo una DDJ SB2, una herinhrt kalrk tenink xentx qx122usb, el circuit tracks y un ohm rgb."

**Qué.** Registrar en el playground, como dispositivos con nombre, los 4 equipos: Pioneer DDJ-SB2, Behringer Xenyx
QX1222USB (efectos Klark Teknik), Novation Circuit Tracks y Livid OHM RGB. `pg_midi` los lista, lee sus eventos y
escribe hacia los que aceptan salida (LEDs, notas, parámetros). Mismo mecanismo de dispositivos que usa el pedido del
Circuit Tracks del SPEC.

**Para qué.** Que la secretaria y los efectos puedan leer perillas, faders y pads (como fuente de parámetros y de
triggers) y mandar notas o colores, sin que Max configure nada a mano.

**Qué es cada uno (verificado por búsqueda web; ninguno está enchufado hoy, `system_profiler` no vio nada):**
- DDJ-SB2: controlador DJ USB, MIDI + audio. Entra como MIDI y como interfaz de audio.
- Circuit Tracks: MIDI por USB (notas, CC, parámetros) y audio USB.
- OHM RGB: MIDI por USB; ya lo maneja `~/ohmrgb/ohmrgb.py` (pads RGB con 8 colores, 17 botones, 8 faders, 8 knobs, crossfader).
- **QX1222USB: no tiene MIDI.** Su USB es interfaz de audio estéreo 2 in / 2 out; los efectos Klark Teknik (24 bit,
  32 presets, tap tempo) se manejan a mano en el panel. Ninguna de las fichas que vi menciona MIDI. En el playground es
  solo un dispositivo de audio (entrada/salida), no un controlador. Si Max quiere moverle los efectos desde el
  playground, hace falta otra vía (no existe hoy); queda fuera de este pedido.

**Criterio de listo.**
- `pg_midi` devuelve los 3 dispositivos MIDI (DDJ-SB2, Circuit Tracks, OHM RGB) con nombre, puertos y estado
  conectado/desconectado, y un evento de prueba por cada uno (mover una perilla se ve en la respuesta).
- La lista de audio incluye DDJ-SB2, Circuit Tracks y QX1222USB como entrada/salida, marcando que la QX1222USB no tiene MIDI.
- Dispositivo ausente = estado "sin conectar", sin error ni caída del runtime.
- Un efecto puede pedir un parámetro "knob/fader de <dispositivo>" y `pg_set_param` lo puede enlazar.

**Actualización 19:20 (Max):** "me da lo mismo que no tenga midi, con la visión se puede detectar la posición de cada
perilla y fader etc." La QX1222USB entra entonces también como dispositivo **de visión**: sin MIDI, pero con cada
perilla y fader leído por cámara (ver 003). Para el playground es un "controlador virtual": los valores salen de la
posición vista, no de eventos MIDI. Criterio extra de listo: `pg_midi` (o la tool que h3 defina) lista la QX1222USB
con sus controles y valor leído por visión, marcando origen `visión` (los demás, origen `midi`). Los efectos y
`pg_set_param` los tratan igual. Latencia y ruido de esa lectura quedan medidos (en 003).

**Dudas / choques.**
- El OHM ya tiene dueño: `~/ohmrgb/session_controller.py` (SmartDeck) usa el MIDI y los LEDs para sesiones de Claude
  Code. Si el playground también escribe pads, se pisan. Falta decidir quién manda los LEDs (ver 002).
- Los tres controladores de DJ/groovebox pueden chocar con Ableton si los dos abren el mismo puerto MIDI; hay que ver
  qué se comparte y qué queda exclusivo.
