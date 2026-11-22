---
id: 012
title: segundo proyector: calibración y blending o asignación por zonas
kind: tool
from: secretaria
status: open
created: 2026-10-15T19:45
priority: 3        # el equipo llega después (Max: "luego llegará"); propuesta: después de 011
---
Palabras de Max: "luego llegará un segundo proyector también."

**Qué.** Que el playground soporte dos (o más) proyectores a la vez, cada uno con su lienzo, su calibración y su zona,
y que la imagen total se componga bien donde ambos alcanzan.

**Quién es el segundo.** Por el resto del mensaje de Max (el que lleva el iPad pegado y se maneja en la mano, ver
013) el segundo proyector parece ser el **handheld**. Hoy el sistema tiene un solo proyector, `proj0` (cabezal
móvil, "sin calibrar"), y el SPEC/PLAN hablaban de dos proyectores en cabezal móvil. **Duda para Max:** el segundo
proyector que llega, ¿es el handheld, o es otro fijo/en cabezal y el handheld es un tercero?

**Calibración.** Para cada proyector: intrínsecos y distorsión de lente, y pose en el marco de la mesa.
- Proyector fijo o en cabezal móvil (como `proj0`): pose por cinemática (base fija + pan/tilt), calibrado una vez
  con la Osmo pegada al lente (PLAN).
- Proyector handheld: **no tiene pose fija**; sus intrínsecos se calibran una vez, y su pose cambia cada cuadro (la da el
  pipeline 013). Si es el caso, se calibra el par cámara-proyector rígido (iPad pegado) y la pose de ese par se
  estima en vivo.

**Reparto de la imagen (a decidir con h3 y Max).**
- **Asignación por zonas:** cada proyector tiene su región de la escena (por ejemplo, uno la mesa, otro la pared o lo
  que ilumina a mano); un objeto se proyecta desde el que lo ve mejor y evita sombras de manos.
- **Blending:** en la zona donde se solapan, se mezclan los dos lienzos con una rampa suave y corrección de brillo y
  color, para que no se note la junta.
- Un efecto puede pedir "proyéctalo desde el proyector X" o "desde el que lo vea mejor".

**Cómo se ve.** `pg_state` lista `proj0` y `proj1` con tamaño, pose, zona y estado (`sin calibrar`, `calibrado`);
`pg_spawn` sobre un objeto en el solape se ve continuo, sin junta ni doble brillo; el render usa el lienzo de cada
uno.

**Criterio de listo.**
- Dos proyectores calibrados, con error de reproyección en mm medido para cada uno.
- Un objeto en la zona de solape se ve sin junta visible (foto de verificación, `pg_snapshot` por proyector).
- Un efecto puede dirigirse a un proyector específico o a "el mejor".
- Cada proyector tiene su salida de video y su ventana en el PC (según monitores y salidas de GPU disponibles).
- Si un proyector se apaga o desconecta, el otro sigue y se avisa.

**Dudas / choques.**
- **Salidas de video del PC:** la RTX 4070 tiene un número limitado de salidas; falta confirmar monitores
  actuales + 2 proyectores (PLAN habla de 3 monitores y `displays×2`). Si el handheld va por cable, el cable
  limita la movilidad; por inalámbrico habría latencia (ver 013).
- Depende de 011 (dónde corre el render) y de 009 (cámaras calibradas).
- Nada se compra: el proyector lo trae Max.
