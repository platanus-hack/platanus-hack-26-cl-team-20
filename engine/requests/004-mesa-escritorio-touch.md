---
id: 004
title: la mesa del escritorio como superficie touch
kind: tool
from: secretaria
status: open
created: 2026-10-15T19:20
priority: 4        # orden de Max: b, c, a, d
---
Palabras de Max: "igual luego segmentar la posición de todo y hacer que la mesa de escritorio sea touch"

**Qué.** Detectar toques y arrastres de dedos o manos sobre la mesa (objeto `mesa`, plano z=0) y entregarlos como
eventos con posición en mm de la mesa.

**Para qué.** Que Max toque la mesa para disparar efectos, mover cosas proyectadas, tocar el teclado proyectado
(piano) o pulsar botones proyectados, sin mouse.

**Cómo (sugerido; lo decide h3).** Cámara cenital o de ángulo + mapa 3D: dedo cerca del plano de la mesa (altura bajo
un umbral) = toque; apoyado = down, levantado = up. Pose de manos con librerías abiertas (ya está en el plan para
gestos). Excluir los objetos segmentados (cocodrilo, controladores, teclado) para que apoyar un objeto no cuente como toque.

**Cómo se ve.** Al tocar la mesa aparece un efecto en ese punto (por ejemplo una onda); arrastrar mueve un objeto
proyectado. `pg_gestures` incluye eventos `touch` con x, y en mm y estado (down, move, up).

**Criterio de listo.**
- Un toque con el dedo sobre la mesa entrega un evento con posición y error medido (mm) contra un punto marcado.
- Una mano flotando a más de unos cm no dispara toque; apoyar un objeto tampoco.
- Funciona con la proyección encendida (sin que los efectos proyectados lo confundan).
- Latencia medida y anotada.

**Dudas / choques.**
- Depende de la calibración cámara-proyector-mesa (hoy `proj0` "sin calibrar") y de la segmentación de objetos (003).
- Orden de Max: es el último (b, c, a, d).
