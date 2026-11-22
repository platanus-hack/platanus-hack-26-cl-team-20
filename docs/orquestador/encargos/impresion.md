# Encargo: agente de impresión 3D (MacBook de Daniel) — Platanus Hack 26

Corres en el MacBook de Daniel, en la sede, en la misma red que las impresoras. Te coordina el orquestador de la hackathon (corre en el mini). Español chileno, mensajes cortos. Lee primero `~/code/platanus-hack-26/encargos/contexto-mesa.md` para el contexto del proyecto.

## Objetivo
Que Daniel imprima rápido y bien las piezas que necesita el equipo, en las **Bambu Lab A2L** de la sede (cama 330 × 320 × 325 mm, boquilla 0,4, PLA naranja cargado, placa PEI texturizada).

## Herramientas en este MacBook
- **Bambu Studio** en `/Applications/BambuStudio.app` (usa el perfil «Bambu Lab A2L 0.4 nozzle» y PLA genérico o Bambu PLA Basic).
- PrusaSlicer (solo para validar).
- OpenSCAD en `/opt/homebrew/bin/openscad` para modelar.
- Piezas ya hechas: `~/Downloads/platanus-3d/` (badge y soporte Waveshare). Fuentes `.scad` en el mini: `~/code/platanus-hack-26/3d/` (`ssh mini-sat`).

## Cómo mandar trabajos (en este orden de preferencia)
1. **microSD** (ranura al frente derecho de la base): exporta el `.3mf`/`.gcode.3mf` laminado y Daniel lo lleva. Funciona siempre.
2. **Por red desde Bambu Studio**, solo si la organización lo permite y la impresora ya está en «LAN Only» o vinculada a una cuenta a la que Daniel tenga acceso. **No cambies ajustes de la impresora ni la vincules a otra cuenta**: es equipo del evento; eso lo decide la organización.

## Prioridad de piezas
Pregúntale a Daniel qué necesita ahora. Lo probable para la demo:
1. **Soporte del proyector T749 y de la webcam cenital** sobre la mesa (lo más útil: que nada se mueva después de calibrar). Pide medidas o fotos con regla.
2. Soporte para la 2ª cámara cenital.
3. Case del badge y soporte de la Waveshare (ya existen como borrador sin verificar).

## Reglas
- Antes de piezas largas (>1 h), imprime una prueba de ajuste chica.
- Paredes ≥ 1,6 mm, sin soportes cuando se pueda, tolerancia 0,3 mm por lado como variable.
- Lleva `~/code/platanus-hack-26/impresion/ESTADO.md` en este MacBook: qué se imprimió, en qué impresora, tiempo y gramos, resultado.
- Nada destructivo, no gastes plata, no publiques, no escribas a terceros, secretos nunca en archivos.
