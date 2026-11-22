# 02 · Casos de uso y capacidades del engine

El equipo todavía no elige el dolor de la demo (lo están trabajando Pablo y Matilda). La arquitectura no depende de esa elección: los casos de uso son **aplicaciones** que se arman encima de un conjunto fijo de **capacidades** del engine. Este documento separa ambas cosas.

## 1. Capacidades del engine (comunes a todos los casos)

| # | Capacidad | Qué significa para un agente | Base en el código de Max |
|---|---|---|---|
| C1 | **Calibrar solo** | Saber dónde cae cada píxel del proyector sobre la mesa, en mm, sin dibujar a mano. Modo rápido (ArUco, ~3 s), modo preciso (gray code) y verificación continua («el rig se movió») | `graycode.py`, `recalib.py`, `calib_quest_proyector.py` |
| C2 | **Ver la mesa** | Lista de objetos con nombre, contorno, posición en mm y confianza; foto de la cámara para razonar con visión | `auto_scene.py`, `vision_vlm.py`, `objects.json` |
| C3 | **Seguir lo que se mueve** | Objetos y manos con ID estable mientras se mueven | `vision_mesa.py` (trackers), `hands_service.py` |
| C4 | **Proyectar interfaces** | Widgets anclados a objetos o a puntos de la mesa: etiqueta, halo, flecha, panel, botón, progreso, imagen | `papel_canvas`, `contour_glow`, `libros`, `marco_respira` |
| C5 | **Sentir el toque** | Eventos de toque, permanencia (dwell) y pinch sobre widgets o zonas | `hands/touch.py`, `touchsynth/osmo_hands.py` |
| C6 | **Conectar agentes** | MCP local y remoto, eventos empujados a la conversación, herramientas que devuelven imágenes | `mcp_server.py`, `secretaria/` |
| C7 | **Sumar cámaras en caliente** | Un celular escanea un QR proyectado, abre una página y queda registrado como cámara | `capture.RemoteCamera`, `quest/transporte.js` |
| C8 | **Observarse** | Latencia de punta a punta, fps por etapa, errores, grabación y repetición de sesiones | `measure_latency`, eventos JSONL |
| C9 | **Modos** | Una configuración de widgets + reglas (máquina de estados) que el agente arma y cambia: «modo guía», «modo escritorio» | `showcase.py`, `juego_memoria` |

## 2. Casos de uso candidatos

| Caso | Dolor | Capacidades que usa | Riesgo técnico | Encaje con la rúbrica |
|---|---|---|---|---|
| **A. Guía de armado o reparación que verifica cada paso** (recomendado por la investigación) | Las instrucciones viven lejos de las manos; −80 % de errores con guía proyectada (Funk 2015, OPS) | C1–C5, C9 | Medio: el reconocimiento de piezas y la verificación de cada paso son lo difícil | Impacto alto (industria, minería, mantención); prueba medible en vivo (contador de errores) |
| **B. Escritorio aumentado («Superhuman Desk»)** | Los agentes viven dentro de la pantalla; tocar la mesa para grabar, aprobar o ver estado | C1, C2, C4–C6, C9 | Bajo-medio | Wow alto; dolor menos concreto (objeción del mentor) |
| **C. Tu agente sale de la pantalla** (Claude Code, etc. proyecta su estado y te pide confirmación tocando la mesa) | Aprobaciones y notificaciones de agentes que interrumpen el trabajo | C4–C6 | Bajo | Originalidad alta; se puede mostrar con el MCP remoto |
| **D. Objetos del público** | Un objeto cualquiera recibe una interfaz o guía generada en segundos | C2, C4, C6 | Medio: VLM + segmentación en vivo | Originalidad y ambición; buen cierre de la demo |
| **E. Cámaras «enchufar y listo»** | Agregar sensores hoy exige calibrar a mano | C1, C7 | Medio | Técnico (25 %); un momento visible en la demo |
| **F. Proyector de mano** | El proyector sabe a dónde apunta al moverlo | C1, C3 + pose 6DoF | Alto | Ambición; mejor dejarlo como visión futura |
| **G. Arte y mapping creativo** (lo de Max) | Projection mapping que hoy exige calibrar a mano | C1, C2, efectos | Bajo (ya existe) | Wow; poco dolor concreto |

**Recomendación de arquitectura**: diseñar para C1–C9 y dejar los casos A–G como «modos» encima. Así la demo puede contar una historia (A, por ejemplo) y cerrar con 30 s de otros modos (B, D, E) sin cambiar el engine. Eso también responde a la objeción del mentor (foco) sin perder el argumento de ambición (engine general).

## 3. Contrato mínimo que cualquier caso necesita

1. `scene()` → objetos `{id, label, polygon_mm, center_mm, confidence, since}`, manos y toques actuales.
2. `look(camera?)` → imagen de la cámara (y opcionalmente lo proyectado) para que el agente razone con visión.
3. `project(widget)` / `update` / `remove` → widgets anclados a `object_id` o a un punto en mm, con capas y TTL.
4. Eventos: `object.appeared/moved/removed`, `touch.down/up`, `widget.pressed`, `dwell`, `calibration.drift`.
5. `ask(pregunta, opciones)` → proyecta botones y espera el toque (o el tiempo límite).
