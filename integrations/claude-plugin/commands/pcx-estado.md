---
description: "Estado de pcx en una mirada: conexión, superficie, objetos, widgets, flujos, dispositivos y calibración"
argument-hint: "[mirar]"
allowed-tools: mcp__plugin_pcx_pcx__scene, mcp__plugin_pcx_pcx__devices, mcp__plugin_pcx_pcx__events_wait, mcp__plugin_pcx_pcx__look
---

Reporta el estado actual del espacio pcx usando sus tools MCP. Solo lectura: no proyectes, no borres y no lances nada.

Argumento recibido: `$ARGUMENTS`

1. Llama `scene` con `{"include_devices": true}`.
   - Si la tool no existe o falla la conexión, dilo y explica cómo conectar: variables `PCX_MCP_URL` (por defecto `http://127.0.0.1:9200/mcp`) y `PCX_MCP_TOKEN` en el entorno donde corre el agente, y un nodo arriba (`pcx serve`; `curl -s http://127.0.0.1:9200/health`). Un 401 es token ausente o distinto del del nodo. Termina ahí.
2. Llama `events_wait` con el `cursor` de `scene` menos 20 (mínimo 0), `timeout_s: 0` y `max_events: 20`, para ver la actividad reciente sin esperar.
3. Solo si el argumento es `mirar`, llama `look` y describe en una o dos frases lo que se ve. Sin ese argumento no pidas imágenes: salen del nodo.

Responde en español con este formato, corto y sin adornos:

```
pcx · <stream_id abreviado> · rev <rev> · cursor <cursor>
Superficie: <ancho>×<alto> mm
Calibración: <sin transformaciones | lista "from → to (kind, v<version>, residuo X mm)">
Dispositivos: <id (kind, status)…>
Objetos (<n>): <id «label» en (x, y) mm…>
Widgets (<n>): <id (type)…>
Flujos activos: <ids o ninguno>
Reciente: <últimos eventos relevantes: tipo y dato clave; omite touch_move y dwell>
```

Cierra con una línea de «Atención» solo si aplica alguno de estos casos:
- no hay transformaciones (sin calibrar: lo proyectado no va a calzar; ver la skill `pcx-calibrar`);
- hay un `calibration_drift` reciente;
- la única cámara es `sim` (dibujo sintético: no hay cámara real cableada);
- hay widgets con id `pcx:confirm:…` (una confirmación de domótica espera un toque);
- hay widgets proyectados y ningún flujo activo que los explique (posibles restos por limpiar).
