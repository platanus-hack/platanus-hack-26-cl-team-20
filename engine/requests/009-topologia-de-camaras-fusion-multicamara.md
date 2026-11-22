---
id: 009
title: topología de cámaras (webcam en pared, FX3 cenital, Osmo en el proyector) y fusión para tracking y reconstrucción
kind: tool
from: secretaria
status: open
created: 2026-10-15T19:25
priority: 2        # Max no la ordenó; es base de 003, 006, 007 y 008. Propuesta: junto a 002 y 003
---
Palabras de Max: "la webcam estará cenital también pero apuntando desde la pared hacia el escritorio, entonces nos verá
las caras, las manos etc. se deben usar los feeds de las 3 cámaras para tener un tracking perfecto y reconstruir la
geometría con la luz estructurada además de los focos que puedes controlar."

**Topología (con lo que Max dijo antes en 005).**
1. **Sony FX3, gran angular, cenital sobre la mesa.** Ve la mesa desde arriba. Su señal sale por DJI SDR Transmission
   al YC Onion (005).
2. **Webcam, en la pared, en alto, mirando el escritorio.** Ve la mesa en ángulo y ve caras y manos. Max la llamó
   "cenital" pero la describe en ángulo: es una vista oblicua desde la pared, no vertical. No lo tomo como cambio de
   plan: la dejo anotado como "webcam de pared".
3. **Osmo Pocket por USB (modo webcam), pegada al proyector.** Es el par rígido para alinear proyector y escena.

**Qué.** Registrar las 3 cámaras con intrínsecos y poses en un mismo marco 3D (el de la mesa), y fusionar sus feeds para:
- tracking de objetos, manos, caras y personas (más robusto con 2 o 3 vistas que con una);
- reconstrucción de la geometría con luz estructurada: el proyector manda patrones, las cámaras los leen y se
  triangula, con la webcam de pared dando el punto de vista oblicuo que a la FX3 le falta;
- identidad de personas entre vistas (cara con la webcam, cuerpo con la FX3 desde arriba).

**Para qué.** Que el mapa 3D (mesa, objetos, plantas, controles, teclas, manos) sea exacto y siga vivo, y alimentar
tracking de gestos, relighting (008) y los pedidos 003, 006, 007.

**Cómo se ve.** `pg_cameras` lista las 3 cámaras con rol, estado, fps, resolución real y pose; `pg_snapshot` o una
vista combinada muestra lo que ve cada una; un objeto aparece en `pg_scene_objects` con su pose aunque una cámara lo
tape (lo ve otra).

**Criterio de listo.**
- `pg_cameras` muestra las 3 con intrínsecos y pose en el marco de la mesa, y el error de reproyección medido.
- Un objeto trackeado se ve en las 3 vistas y su pose coincide (error en mm medido).
- Si una cámara se tapa o falla, el tracking sigue con las otras y se avisa.
- Luz estructurada con el proyector reconstruye la mesa y 2 objetos con un error medido contra una medida real.
- Cada cámara con su resolución real y compresión anotada (la FX3 viaja por un enlace DJI; la Osmo Pocket en webcam
  no da 4K sin compresión; la webcam tampoco): se dice qué cámara sirve para qué (patrones finos vs tracking).

**Dudas / choques con SPEC y PLAN (no toqué SPEC.md).**
- Se suma a lo anotado en 005: FX3 en cenital, no vista general; una sola Osmo Pocket, no dos.
- Webcam: de SPEC l.118 ("webcam cenital") pasa a ser de pared en ángulo.
- **El PC tiene que recibir la FX3.** Max pidió "usar los feeds de las 3 cámaras" para tracking y reconstrucción, y en 005
  la FX3 va por DJI SDR al Onion. Hay que definir cómo llega también al PC (segundo receptor DJI, splitter
  HDMI, o por el Onion). Sin eso la FX3 no entra al tracking. Pregunta a Max.
- **La luz estructurada la hace el proyector, no los focos.** Los focos (Small Rig RC 100, que toma h3) suman luz
  controlable a la escena pero no proyectan patrones; las dos cosas se usan juntas, cada una en su papel.
- La webcam ve caras: la identidad de personas se hace con librerías abiertas (PLAN); lo que se guarde de personas lo
  decide Max.
