---
id: 011
title: arquitectura distribuida Mac ↔ PC Windows: qué corre dónde y cómo se comunican
kind: tool
from: secretaria
status: open
created: 2026-10-15T19:45
priority: 1        # base de 012 y 013; propuesta: junto a 002
---
Palabras de Max: "y todo tiene que correr en el windows y aquí y comunicarse."

**Qué.** Definir y dejar andando qué proceso corre en el Mac, cuál en el PC Windows y cómo hablan entre sí, de modo
que el playground sea una sola escena viva aunque esté repartida en dos máquinas.

**Punto de partida (PLAN.md, sección Live Mapping Playground).** Decisión previa de Max: **sonido en el Mac**
(Ableton, Circuit Tracks, MCP `ableton`), **visuales en el PC** (RTX 4070, 128 GB de RAM, torch CUDA 12.6 y Open3D
instalados), puente por **LAN**; SSH por Tailscale para administrar. Hoy el runtime del playground y las tools
`pg_*` corren en el Mac (HTTP + SSE en `127.0.0.1:8790`). Lo que dice Max ahora, "todo en el Windows y aquí", es
coherente con eso: confirmar que es esa división y no otra.

**Qué hay que decidir y entregar.**
- Reparto: propuesta de partida. **PC:** captura de cámaras (FX3, webcam, Osmo, iPad), calibración, reconstrucción,
  segmentación (SAM), depth monocular, tracking, render a los proyectores (moderngl), todo lo de GPU.
  **Mac:** Ableton y MIDI de los controladores, secretaria (chat/voz), MCP, UI de control y visores, Herdr.
  El **estado de la escena** vive en un solo lugar (propuesta: el runtime en el PC, que es el motor) y el Mac lo
  consulta y le manda comandos.
- Protocolo: HTTP + SSE para comandos y estado (ya existe), WebSocket o similar para eventos de baja latencia
  (MIDI, gestos, beat) y un canal aparte para video (JPEG o H.264 de baja latencia, o NDI/WebRTC) cuando el Mac
  necesite ver las cámaras. Elegir con números, no a mano.
- Reloj común: sincronizar los relojes del Mac y el PC (estampas de tiempo en cada evento y cuadro) para fusionar
  sonido, MIDI y visión; medir el desfase.
- Orden de arranque y caídas: si el PC o el Mac se cae, el otro lo dice y espera, sin quedar en estado raro; `pg_runtime`
  muestra a quién está conectado.
- Seguridad: solo LAN y Tailscale, sin abrir puertos a internet; claves por referencia.

**Cómo se ve.** Max dice "sube el tempo" en el Mac y el efecto en el PC late distinto; mueve una perilla en el
OHM (Mac) y cambia un efecto proyectado desde el PC. `pg_state` dice dónde corre cada pieza, el estado de cada enlace y la
latencia.

**Criterio de listo.**
- Un diagrama simple (qué proceso, dónde, puerto, protocolo) en el repo.
- `pg_state` y `pg_runtime` informan el estado de los dos lados y del enlace.
- Latencia Mac→PC→proyector medida para un evento (por ejemplo MIDI → efecto visible) y anotada; objetivo de
  partida a validar: menos de 50 ms en el tramo de red (a confirmar según lo que mida h3).
- Caída de un lado o desconexión de red: el otro sigue y reconecta solo.
- Hot reload de efectos funciona con el runtime en el PC (guardar el archivo basta).

**Dudas / choques.**
- **El PC por SSH reporta un display de 1024x768** y el render corre como tarea en la sesión interactiva de Roberto
  (PLAN, "siguiente"): el render a proyectores requiere sesión gráfica abierta en el PC. Hay que resolver cómo arranca
  el motor con una sesión con pantalla (inicio de sesión automático, o servicio con acceso a la GPU).
- Ableton y los controladores quedan en el Mac; la visión de los controles (003) corre en el PC: sus valores viajan por el
  enlace. Eso agrega latencia al camino "perilla → sonido": si la perilla gobierna sonido, conviene que el MIDI se
  maneje directo en el Mac y solo copie el valor al PC.
- 1 GB libre en el Mac: nada pesado ahí.
