---
id: 007
title: plantas como clase en la segmentación del point cloud
kind: tool
from: secretaria
status: open
created: 2026-10-15T19:25
priority: 6        # Max no la ordenó; propuesta: con la segmentación general, después de lo de controles
---
Palabras de Max: "quiero también en la segmentación del pointcloud que detecte plantas."

**Qué.** Que la segmentación 3D (SAM sobre las imágenes, llevada al point cloud y a malla, según PLAN) tenga la clase
`planta`, además de mesa, pantalla, objetos y personas. Cada planta sale como objeto en `pg_scene_objects` con
bbox, malla y contorno.

**Para qué.** Efectos que reaccionan a las plantas (luz, sombras, brillo en las hojas, un halo) y que no las trate
como "objeto genérico".

**Cómo (a decidir por h3).** Detección por texto en la imagen (modelo abierto de vocabulario libre sobre SAM,
con la etiqueta "planta") o por color y forma como apoyo. Luego proyección de la máscara al point cloud y malla
por planta. Ojo: las hojas son geometría fina, brillante y semitransparente; la luz estructurada y el
point cloud ahí salen ruidosos.

**Cómo se ve.** `pg_find planta` devuelve la lista; `pg_spawn contour_glow planta1` pone un halo; `pg_crop_object` da la
máscara.

**Criterio de listo.**
- Con una o varias plantas en la escena, cada una sale como objeto con clase `planta`, bbox y malla.
- Acierto contado en una escena real de prueba (por ejemplo 3 plantas, 2 objetos verdes que no son plantas):
  cuántas se detectaron y cuántos falsos positivos.
- Sigue a la planta si se mueve o se cambia de lugar.
- La clase queda como una etiqueta general en el sistema de clases para sumar otras (por ejemplo "taza", "libro").

**Dudas / choques.**
- El cocodrilo de hule es verde ("piel"); puede confundirse con planta. Anotar que el cocodrilo es `instrument` y no
  se pisa.
- Depende de que la segmentación 3D general (PLAN, sección de segmentación) esté en pie.
