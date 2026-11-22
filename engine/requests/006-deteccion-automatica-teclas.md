---
id: 006
title: detección automática de las teclas del teclado/piano
kind: tool
from: secretaria
status: open
created: 2026-10-15T19:25
priority: 5        # Max no la ordenó; propuesta: junto a 003 (misma técnica de visión), antes de 004; sube a 3 si se hace el 014
---
Palabras de Max: "el teclado quiero que detecte automático las teclas."

**Qué.** Que el playground encuentre solo, por visión, cada tecla del teclado (blancas y negras): contorno, posición en
mm sobre la mesa y nota que le corresponde. Hoy `pg_scene_objects` tiene el teclado como un solo objeto
(`teclado`: 61 teclas, primera nota 36, bbox 900 x 180 mm). El paso es pasar de un rectángulo a 61 objetos hijos.

**Para qué.** Proyectar sobre cada tecla (el piano con luces encima, que Max pidió al inicio) y saber qué tecla toca
la mano, sin calibrar a mano.

**Cómo.** Rectificar la imagen del teclado, separar teclas blancas por bordes verticales y negras por su contraste,
contar y numerar contra el rango (61 teclas desde la nota 36; confirmar si es el teclado real de Max), cruzar con
la geometría 3D (plano del teclado, z=60). Si el teclado manda MIDI, se contrasta con ello; si no, solo visión.

**Cómo se ve.** `pg_scene_objects teclado` devuelve cada tecla con id, nota, contorno y plano; un efecto puede
iluminar una tecla por nota o seguir a la tecla presionada; `pg_crop_object` recorta una tecla.

**Criterio de listo.**
- Con el teclado puesto en cualquier lugar de la mesa, detecta las 61 teclas (o el número real) sin intervención,
  con la nota correcta en cada una (verificado contra el teclado real tocando 5 teclas).
- Error de posición de cada tecla medido en mm contra una regla.
- Si el teclado se mueve, se re-detecta solo (robustez pedida a las 18:00).
- Toque de tecla (dedo bajando) como evento con nota y velocidad aproximada, aunque sea con latencia alta en
  una primera versión; si el teclado tiene MIDI, se informa la diferencia de tiempo entre ambos.

**Actualización 19:3x (Max):** "el teclado sí se puede conectar a midi al pc". El teclado **manda MIDI al PC**: hay verdad
de referencia (qué tecla suena, cuándo y con qué velocidad). Cambios:
- La detección por visión se **contrasta con el MIDI real**: se toca una nota y se comprueba que la tecla detectada
  es la correcta; así el mapeo tecla↔nota queda verificado y el error medido sin regla.
- El toque de tecla sale de MIDI (nota, velocidad, tiempo); la visión da la posición de cada tecla y qué dedo/mano
  está encima (anticipación).
- Criterio extra de listo: error de mapeo tecla↔nota = 0 sobre las 61 teclas (se recorre el teclado con MIDI y
  se compara con lo detectado); desfase tiempo MIDI↔visión medido.
- El MIDI llega al PC; Ableton y el sonido siguen en el Mac (PLAN). Cómo viaja el MIDI al Mac se define en 011.
- Alimenta el pedido 014 (piano roll proyectado sobre las teclas).

**Dudas / choques.**
- ~~¿Es un teclado MIDI USB?~~ Resuelto: sí, MIDI al PC. Falta el modelo y su rango real (¿61 teclas desde la nota 36?).
- Comparte técnica con la lectura de controles por visión (003) y el touch de la mesa (004).
