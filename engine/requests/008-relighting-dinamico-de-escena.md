---
id: 008
title: relighting dinámico de la escena (superar dynamic_scene_relighting)
kind: visual
from: secretaria
status: open
created: 2026-10-15T19:25
priority: 7        # Max no la ordenó; propuesta: después de la base de cámaras (009) y la calibración
---
Palabras de Max: "also aquí encontré un repo amateur para lo que quiero lograr hacer pero quiero mejores resultados
https://github.com/gizatt/dynamic_scene_relighting"

**Qué.** Iluminar de nuevo la escena en vivo con el proyector: simular luces virtuales (posición, color, tipo) sobre
la geometría real y proyectar esa luz de vuelta para que la escena se vea iluminada como si esas luces existieran.
Debe ser mejor que el repo de referencia.

**El repo de referencia (lo leí en su README; no lo corrí).** "Dynamic Scene Relighting with Projection Mapping"
(MIT Projx, 2020-21): una cámara RGBD y un proyector pegado a una cámara fotográfica; de la nube de puntos
estima normales, calcula la luz de varias luces simuladas con OpenGL desde el punto de vista del proyector y la
proyecta de vuelta. Calibración proyector-cámara con AprilTags proyectados y optimización con Drake; su propio autor
dice que es "rough around the edges", que a veces la retoca a mano y que el modelo pinhole sin distorsión no basta.
Limita: una sola cámara RGBD, escena de repisa con muñecos, sin personas ni objetos que se muevan, sin PBR ni
sombras reales.

**Qué esperamos mejor (propuesta; lo concreta h3).**
- Calibración proyector-cámaras con distorsión de lente, medida y verificable (no retoque a mano), con las 3 cámaras.
- Geometría mejor: luz estructurada con el proyector + fusión multicámara (ver 009), no un solo RGBD.
- Materiales PBR estimados (albedo, rugosidad) para que la luz virtual se comporte bien en madera, vidrio, mate y piel.
- Sombras y reflejos correctos con la geometría viva (primero rasterizado; después path tracing por GPU, según F7).
- Objetos, plantas y personas que se mueven: el relighting sigue y no se descuadra (robustez pedida a las 18:00).
- Un efecto de la galería (`relight`) con luces virtuales como parámetros (`pg_set_param`) y controlables por MIDI,
  gestos y el beat.

**Cómo se ve.** `pg_spawn relight` sobre la escena o sobre un objeto; mover la luz virtual cambia la iluminación
visible sobre la mesa y los objetos, con sombras coherentes.

**Criterio de listo (primera etapa).**
- Una luz virtual movible sobre la mesa y 2 objetos, con sombras coherentes a su geometría.
- Error de alineación proyección-geometría medido (mm) y menor que el del repo de referencia en la misma escena (se
  puede correr su código como línea base si Max lo autoriza; ver duda).
- Latencia y fps anotados.
- Nada de proyectar luz fuerte a la cara de personas sin aviso (seguridad visual): el proyector no ilumina ojos con
  luz intensa; Max decide los límites.

**Dudas / choques.**
- Depende de calibración (hoy `proj0` "sin calibrar"), de las cámaras (009) y de luz estructurada.
- Licencia del repo de referencia: sin verificar; se usa como referencia de enfoque, no se copia código sin revisar.
- Los focos controlables (Small Rig RC 100) los toma h3 aparte; son luz real que se suma al relighting, no reemplazan
  al proyector.
