---
id: 013
title: pipeline handheld "viewfinder": proyector en la mano con iPad pegado, SAM + depth monocular en la GPU del PC
kind: visual
from: secretaria
status: open
created: 2026-10-15T19:45
priority: 4        # paralelo al pipeline de luz estructurada; no lo reemplaza
---
Palabras de Max: "quiero hacer un pipeline de visión paralelo un poco distinto en donde no se convierte a 3d con luz
estructurada sino que segment anything + un algoritmo por diseñar me permite usar el proyector handheld y va
iluminando cosas etc, es una especie de foco/luz redonda estilo viewfinder que abre una visión a una nueva
realidad. entiendo que puedo dejar la cámara del ipad junto a ese proyector que es handheld y usar también
acelerómetro, giroscopio etc para saber la posición exacta del proyector en el espacio. aunque igual desde el video
feed de la cámara que va en el segundo proyector, ya sea ipad con lidar o..., deberíamos usar ese algoritmo nuevo que
hay para estimar depth sobre un frame y todo debería correr sobre la gpu del pc para real time"

**Qué.** Un segundo pipeline, **paralelo al de luz estructurada y sin reemplazarlo**: Max lleva un proyector en la mano con
el iPad pegado; el sistema sabe dónde apunta, entiende lo que hay delante y proyecta un **círculo de luz tipo
linterna/viewfinder** que, dentro del círculo, "abre otra realidad" sobre lo que se ilumina (por ejemplo, el objeto
segmentado se ve con otra textura, otra luz, una capa distinta).

**Para qué.** Una forma de jugar en vivo que no depende de reconstruir antes toda la escena en 3D ni de que el
proyector esté fijo: se descubre la escena con la linterna.

**Cadena.**
1. **Pose del proyector (6DoF) en vivo.** Fuente principal: el iPad (ARKit ya fusiona cámara + IMU: acelerómetro y
   giroscopio, con LiDAR, y da pose por cuadro; sirve más que integrar el IMU crudo, que se desvía). Se complementa con
   visión (relocalizar contra el mapa de la mesa de 009, para corregir deriva). Como el iPad está pegado rígido al
   proyector, se calibra una vez la transformación iPad→proyector.
2. **Segmentación 2D:** Segment Anything sobre el cuadro del iPad (o el de otra cámara): máscaras de los objetos
   que la linterna toca. Sobre la GPU del PC, tiempo real; usar una variante rápida si hace falta.
3. **Profundidad monocular:** modelo de estimación de profundidad sobre el video de la cámara, para tener forma
   3D sin luz estructurada. **Max aclaró (19:4x) cuál tenía en mente: Video Depth Anything**
   (https://videodepthanything.github.io/, ByteDance, CVPR 2025, arXiv 2501.12375): profundidad **consistente en el tiempo**
   para videos de cualquier largo, basada en Depth Anything V2. **h3 lo evalúa primero**; si no sirve, elige otro
   (alternativas de referencia que conozco, sin verificar su vigencia: Depth Anything V2/V3, Apple Depth Pro,
   Metric3D v2, UniDepth, MoGe). Lo que leí en su página y en el README del repo (`DepthAnything/Video-Depth-Anything`):
   - Consistencia temporal: lo que importa aquí, porque un modelo cuadro a cuadro parpadea y la luz proyectada
     "vibraría" sobre las superficies.
   - **Modo streaming**: existe, pero **experimental y sin entrenar** (julio 2025): guarda estados de atención
     temporal entre cuadros y procesa un cuadro a la vez. El propio README dice que **pierde calidad** frente al modo
     offline (en su ejemplo de ScanNet, δ1 baja de 0,926 a 0,836). Desde septiembre 2025 también lo soporta
     para los modelos de profundidad métrica. Para el viewfinder es la variante a probar primero, y hay que
     medir si la caída de calidad es aceptable.
   - **Modelos:** Small (28 M parámetros), Base y Large (382 M), versión de profundidad relativa y versión **métrica**.
     La métrica (Small/Base/Large) entrega escala real, que es lo que necesitamos; de lo contrario se calibra la
     escala y el desplazamiento con el LiDAR del iPad (010) o con el mapa de la mesa. La página dice que su demo
     alinea escala y desplazamiento corriendo MoGe sobre el primer cuadro.
   - **Velocidad (lo que publica el README, medido en una A100 con entrada 1 x 32 x 518 x 518, no en nuestra RTX 4070):**
     Small 7,5 ms y Large 14 ms en FP16 (y 9,1 / 67 ms en FP32). El número no es directamente el fps del streaming
     ni el de nuestra GPU: **h3 tiene que medirlo en la RTX 4070 con el video real del iPad.**
   - **Licencia: ojo.** El README dice que el modelo **Small es Apache-2.0** y que **Base y Large son CC-BY-NC-4.0
     (solo uso no comercial)**. Si el playground llega a usarse en algo comercial de SmartUp o de un cliente, hay que
     revisar o usar el Small. Lo decide Max; hasta entonces h3 evalúa pero no empotra Base/Large sin avisar.
   - Importa que dé **escala métrica** o que se calibre su escala con el LiDAR del iPad (010), porque una profundidad
     relativa deforma la proyección.
4. **Algoritmo por diseñar (lo que Max llama "un algoritmo por diseñar"):** combina máscara SAM + profundidad + pose
   para saber qué superficie ilumina el círculo, deformar el contenido proyectado para que calce con esa
   superficie (rectificación por homografía o por malla local) y aplicar el efecto solo dentro del círculo y de
   la máscara. Queda a decisión de h3 con prototipos.
5. **Render:** moderngl en el PC, lienzo del proyector handheld (012), con el círculo y su borde suave.

**Cómo se ve.** Max apunta el proyector a la mesa: aparece un círculo de luz; sobre el cocodrilo se ve con otra piel, sobre
la planta (007) brillan las hojas, sobre el teclado se iluminan las teclas; al mover la mano el círculo sigue,
sin arrastre visible. `pg_state` muestra `proj1` con pose en vivo y `pg_spawn viewfinder` crea el efecto con
`radius_mm`, `feather`, `effect` (la "otra realidad") y `target` (todo o una clase).

**Criterio de listo (primera etapa).**
- El iPad entrega cuadro y pose al PC (opción de captura de 010; con la pose de ARKit) y el sistema conoce la pose
  del proyector con error medido contra un punto marcado.
- Con el proyector en la mano, un círculo de luz se queda sobre un punto marcado en la mesa con error (mm) anotado
  mientras Max se mueve despacio.
- Dentro del círculo, un efecto se aplica solo sobre el objeto segmentado (máscara SAM), no sobre el fondo.
- Profundidad monocular en la GPU: Video Depth Anything evaluado primero (Small/Base/Large, modo streaming y offline por ventanas, relativo vs métrico) y se anota cuál se eligió; tiempo por cuadro, fps y calidad medidos en la RTX 4070, con el parpadeo temporal comparado contra un modelo cuadro a cuadro; objetivo de partida a validar:
  superar 20 fps de extremo a extremo (a confirmar; depende del modelo).
- Latencia de extremo a extremo medida (mover la mano → el círculo se mueve) y anotada; con predicción de pose si
  hace falta.
- Convive con el pipeline de luz estructurada (el otro proyector y 009 siguen funcionando).

**Dudas / choques.**
- **Latencia con un proyector en movimiento:** es el riesgo principal. El círculo se mueve rápido; cualquier retraso
  entre captura, cómputo, render y proyector se ve como arrastre. Medir y prever (predicción de pose y cuadro).
- **Cómo llega el video al proyector handheld:** por cable limita el movimiento; inalámbrico agrega retraso. Falta
  que Max diga qué proyector es y qué entradas tiene.
- El LiDAR (iPad) cubre pocos metros y baja resolución (010); sirve para escala y verdad de referencia más que para
  detalle.
- Luz en la cara: un proyector en la mano puede dar luz intensa a los ojos de personas; límite de brillo y aviso
  los decide Max (mismo punto que 008).
- iPad: modelo y app de captura sin definir (010). Si es ARKit propia, ese pedido la incluye.
- Piezas que conviene reusar: SAM del PLAN (F5), pose ya en el marco de la mesa (009), calibración con la Osmo/Charuco.

> 2026-10-15 19:42 · h3: profundidad elegida (a confirmar con benchmark en la RTX 4070): **Metric-Video-Depth-Anything-Small, modo streaming, fp16, 518 px** (Apache-2.0, 28,4 M, métrica; A100: 7,5 ms/cuadro → estimado 50–60 fps en la 4070; en streaming pierde precisión: ScanNet δ1 0,926→0,836). Base offline para calidad (CC-BY-NC). FlashDepth como alternativa 2K streaming. Escala con el LiDAR del iPad. Detalle en PLAN › Profundidad para el handheld.
