# Encargo: soporte de escritorio para Waveshare ESP32-S3-Touch-LCD-7 (Rev 1.2)

Eres ayudante del líder 3D (pane wJ:p3, tab «3D») en la hackathon Platanus Hack 26. Corres en el mini de Daniel. Español chileno, mensajes cortos.

## Objetivo
Un soporte de escritorio inclinado (~15–20° desde la vertical, que se lea bien sentado) para la pantalla táctil de 7" Waveshare ESP32-S3-Touch-LCD-7 Rev 1.2, imprimible sin soportes en Bambu A1 (cama 256×256 mm, boquilla 0,4, PLA). Se atornilla a las 4 pestañas M3 de atrás. Debe dejar libres y accesibles los dos USB-C, la microSD, el conector de batería PH2.0, los botones RESET/BOOT y los bornes. Estética: limpia, minimalista, que combine con un case de consola portátil (concepto «Superhuman Desk»). Opcional si sobra tiempo: bolsillo para batería LiPo atrás.

## Medidas (fuentes oficiales, ya descargadas en `3d/waveshare-stand/ref/`)
- `details-size-2-touch-rev1.2.png` (plano Rev 1.2, con touch): vidrio 192,96 × 110,76; área visible 154,88 × 86,72, a 19,04 del borde izquierdo y 13,04 de arriba / 11,00 de abajo (vista frontal). Atrás: chasis metálico 165,72 × 97,60; 4× M3 en patrón **126,20 × 65,65**, a 19,76 del borde izquierdo del chasis (vista trasera) y 18,31 de arriba / 13,64 de abajo. 4× M2,5 de la PCB en patrón 58,00 × 49,00.
- `esp32-s3-touch-lcd-7.pdf` (plano mecánico, revisión anterior de la PCB): vidrio 192,96±0,1 × 110,76±0,1, esquinas R7,88; espesor TP+LCD = 5,00; espesor total ~8,50; M3 a 31,85 del borde del vidrio (vista trasera). OJO: la PCB de este PDF es otra revisión (conectores a otro lado); usa el PDF solo para vidrio/espesores y el PNG Rev1.2 para PCB y conectores.
- `esp32-s3-touch-lcd-7.stp` (STEP oficial, 26 MB): fuente para alturas de componentes, posición exacta de USB-C y cuánto sobresalen los insertos M3. Si puedes sacar medidas de ahí (p. ej. venv en el scratchpad con `pip install cadquery-ocp` en python3.12 del MacBook, o parsear el STEP), hazlo; si no, anota qué cota supusiste.
- En Rev 1.2 los dos USB-C (UART y USB) están en el borde de la PCB que en vista trasera queda a la IZQUIERDA (apuntan hacia el borde izquierdo trasero). El encargo original decía «abajo»: manda el plano.
- **No inventes medidas.** Lo que falte, anótalo y márcalo «borrador sin verificar».

## Herramientas
- OpenSCAD 2021.01 está en el MacBook: `ssh macbook '/opt/homebrew/bin/openscad -o x.stl x.scad'` y PNG `--imgsize=1200,900 --camera=...`. Copia los .scad con `scp`. Mira los PNG (Read) antes de dar algo por bueno.
- Slicer: `ssh macbook "'/Applications/Original Prusa Drivers/PrusaSlicer.app/Contents/MacOS/PrusaSlicer' --help"` para validar que lamina (exporta G-code a un temporal y lee tiempo/filamento estimados). El líder se encarga de perfiles Bambu.
- Si `ssh macbook` falla, reintenta en un rato.

## Calidad
- Paramétrico: todas las medidas arriba del .scad, tolerancia como variable (0,3 mm por lado).
- Paredes ≥ 1,6 mm; sin soportes; tornillos M3 (indica largo necesario).
- Si el soporte no cabe de una pieza en 256×256, divídelo con uniones limpias.
- Haz primero una **pieza de prueba chica** (placa con el patrón 126,20 × 65,65 recortada al mínimo, o solo una esquina con dos agujeros, < 15 min) para validar agujeros y holguras.

## Entregables
En `~/code/platanus-hack-26/3d/waveshare-stand/`: `.scad`, STL, PNG de vistas (frente, atrás, lado, con un fantasma de la pantalla), y `NOTAS.md` con medidas usadas + fuente, tiempo/filamento estimados, tornillería, qué falta verificar. Copia STL y PNG finales al MacBook en `~/Downloads/platanus-3d/waveshare-stand/` (crea la carpeta; no toques nada más de Downloads).

## Límites
Nada de SmartUp, nada destructivo, no gastes plata, no publiques, no escribas a nadie. No edites `3d/ESTADO.md` (lo lleva el líder). Cuando termines, deja un resumen de 3 líneas y quédate esperando.
