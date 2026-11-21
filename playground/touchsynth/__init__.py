"""Mesa táctil musical: cocodrilo sintetizador, dibujo sobre la mesa, marimba, linterna y fusión de cámaras.

    toques (Brio/MediaPipe, Osmo por diferencia, o ambas fusionadas)  →  runtime: comando "touch" (px de proyector)
      →  efectos croc_synth · touch_draw · marimba · linterna  →  instrumentos (este paquete)  →  MIDI + lienzo

Módulos: `music` (escalas, MIDI, notas), `scan` (objetos, cocodrilo, altura y zona libre del escaneo), `croc`,
`sketch`, `marimba`, `linterna` (instrumentos), `registry` (estado por escena y reparto de toques), `osmo_hands`
(dedos desde la Osmo en px del proyector), `fusion` (Osmo da x,y; Brio da si está apoyado), `live` (lazo en vivo).
CLI: `python -m playground.touchsynth --help`. Guía: playground/touchsynth/README.md.
"""
