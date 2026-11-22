---
id: 003
title: fit de skins SVG abiertas de cada controlador sobre la cámara y segmentación de cada control
kind: tool
from: secretaria
status: open
created: 2026-10-15T19:20
priority: 2        # orden de Max: b, c, a, d
---
Palabras de Max: "hay skins opensource svgs etc para hacer fit de eso, igual luego segmentar la posición de todo"

**Qué.** Para cada controlador (OHM RGB, DDJ-SB2, Circuit Tracks) tomar una skin SVG abierta (o la propia, en el
caso del OHM, que ya está en `~/ohmrgb/ui`), ajustarla (fit) a lo que ve la cámara y obtener la posición de cada
control: perilla, fader, pad, botón, jog. Resultado: un mapa "control → posición en la imagen y en mm de la mesa".

**Para qué.** Proyectar sobre cada control, saber dónde está la mano respecto a cada uno y asociar cada control físico
con su evento MIDI (ver 001 y 002).

**Cómo.**
1. Skin: SVG con coordenadas de cada control (OHM: ya existe; DDJ-SB2 y Circuit Tracks: buscar skins abiertas con
   licencia que permita usarlas; si no hay buena, queda anotado y se pide a Max).
2. Fit: homografía/similitud entre las esquinas de la skin y el equipo en la imagen (marcadores o detección de
   contorno; mismo enfoque que `calibrate.py` del OHM).
3. Segmentación: cada control recibe una máscara/centro en la imagen y su posición en mm en la mesa, usando el mapa 3D.
4. Verificación: spawnear un efecto en cada control y mirar que cae donde corresponde.

**Cómo se ve.** `pg_scene_objects` lista cada controlador con sus controles hijos; `pg_crop_object` devuelve la
máscara de un control; `contour_glow` o un efecto nuevo puede iluminar un control específico.

**Criterio de listo.**
- Al menos el OHM y uno de los otros dos con skin ajustada a la cámara y error de posición medido (mm o px) en una
  captura real.
- Cada control tiene id, tipo, posición, y su evento MIDI cuando existe.
- Si el equipo se mueve, el fit se corrige solo o se avisa que hay que recalibrar (robustez que pidió Max a las 18:00).
- Licencias de las skins anotadas en el repo.

**Actualización 19:20 (Max):** "me da lo mismo que no tenga midi, con la visión se puede detectar la posición de cada
perilla y fader etc." Se suma **lectura por visión del valor de cada control**, para los 4 equipos y en especial la
Behringer QX1222USB (sin MIDI): ángulo de cada perilla (marca o indicador), posición del cursor de cada fader, estado
de cada botón y de los pads/leds encendidos.
- Qué: sobre cada control segmentado, estimar su valor 0–1 (perilla: ángulo del indicador contra su rango; fader:
  posición del cursor sobre su riel; botón: apretado o no cuando se ve) y publicarlo en vivo con el id del control.
- Cómo se ve: la perilla física se gira y su valor cambia en `pg_state`; un efecto enlazado reacciona. Se puede
  proyectar un anillo o barra de valor sobre cada control.
- Criterio de listo extra: en la Behringer, error de valor medido (por ejemplo ±5% de recorrido) en una prueba real
  con un fader y una perilla; actualización mínima de 10 Hz; dice "sin lectura" cuando la mano tapa el control en vez
  de inventar un valor; con el OHM se contrasta contra su MIDI real (verdad de referencia) para medir el error.
- Duda: el OHM, el DDJ y el Circuit Tracks sí tienen MIDI; la visión ahí sirve para validar y para controles sin
  MIDI (por ejemplo la Behringer). Conviene dejar la fuente MIDI como principal donde exista.
- Dependencia: cámara con resolución suficiente sobre la superficie (ver 009 para qué cámara ve cada equipo).

**Dudas / choques.**
- Depende de que las cámaras y el proyector estén calibrados (hoy `proj0` figura "sin calibrar" en `pg_state`).
- Orden de Max: este va segundo, después de 002.
