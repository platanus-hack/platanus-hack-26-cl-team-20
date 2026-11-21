# 01 · El engine de Max hoy (sl-scanner @ b8fecff)

Síntesis de la lectura completa del engine (unas 65K líneas de Python, 7K de Kotlin y JS propio) hecha por 8 lectores en paralelo el sáb 21-nov a la 01:00. El detalle, con citas `ruta:línea`, está en [`informes/`](informes/). Las rutas de abajo son relativas a `engine/`, que es el mismo commit.

## 1. Qué es, en una frase

Un **taller de projection mapping con IA**: un proyector y una o más cámaras se calibran entre sí, el sistema reconoce los objetos de la escena, y un runtime proyecta efectos animados sobre ellos. Todo se controla por API HTTP, MCP, dashboard y una secretaria por voz. Lo construyeron Max y sus agentes entre el 15-oct y el 20-nov.

## 2. Mapa de funcionalidades

| Área | Qué hace | Dónde | Estado real |
|---|---|---|---|
| Calibración por gray code | 44 patrones; decodifica el mapa denso cámara↔proyector (LUT `proj_to_cam.npz`) | `slscan/graycode.py`, `capture.py`, `recon.py`, `playground/recalib.py` | **Funciona con hardware** (scan2, scan5) y está validado contra sltk |
| Calibración métrica (mm, intrínsecos/extrínsecos) | 5 variantes: ChArUco, hoja, autocalib multivista, rig aproximado, oficio | `slscan/calib.py`, `joint.py`, `autocalib.py`, `selfcalib.py`, `approx_rig.py` | **Ninguna validada con hardware**: autocalib de 1 vista falló; el oficio dio errores de 4–26 mm |
| Mesa Quest (nov) | El Quest 3 queda fijo como cámara; plano métrico de la mesa a 0,4 mm/px; lazo cerrado | `playground/calib_quest_proyector.py`, `lienzo_quest.py`, `superficie_feed.py` | **El mejor resultado físico**: residuo 0,52 px, lazo a 0,1 mm de mediana (en el plano) |
| Runtime de efectos | Plugins con hot reload, lienzo numpy 1920×1080 a 30 Hz, API HTTP + SSE | `playground/runtime.py`, `effects/` | Funciona (lo levanté: ~25 fps con 6 efectos), pero solo con CPU |
| Efectos | 50 clases: halos, texturas, vectores, tela, juegos táctiles, instrumentos | `playground/effects/` | La mayoría cuesta 0,5–2,5 ms por cuadro; un tercio está atado al estudio de Max (Buda, DDJ, cocodrilo) |
| Escena automática | MobileSAM + Grounding DINO offline → objetos con máscara y nombre; activación atómica | `playground/auto_scene.py`, `automatic_masks.py` | Funciona offline; DINO tiene un bug de etiquetado |
| Nombres por VLM | Sonnet por el CLI `claude`, o «Jev» (modelo remoto de TypeSafe vía Vercel AI Gateway) | `vision_vlm.py`, `vision_jev.py`, `jev.py` | Funciona; el contrato `{objetos:[{nombre,caja,centro,confianza}]}` es exactamente lo que necesita un agente |
| Manos | MediaPipe en el PC Windows (Brio) → WebSocket → toque por pinch o tamaño de palma | `windows/hands_service.py`, `playground/hands/` | **Nunca probado con la Brio real** |
| Toque con 1 cámara | Predice lo que la cámara debería ver según lo proyectado; contacto = la punta del dedo y su sombra se juntan | `playground/touchsynth/osmo_hands.py`, `fusion.py` | Algoritmo original y valioso, **nunca probado con una mano real** |
| Instrumentos y audio | Zonas que suenan por MIDI, audio reactivo (bandas, BPM), bindings MIDI → parámetro | `touchsynth/`, `audio/`, `devices/`, `pianohero/` | Funciona con fakes; varios bugs (OHM directo roto) |
| Secretaria por voz | ElevenLabs Conversational AI + Claude Sonnet, 20 herramientas sobre el runtime, avisos de progreso | `playground/secretaria/` | Funcionó en la noche del 16-oct; **no percibe toques, manos ni cámara** |
| MCP | 25 tools `pg_*`, proxy delgado sobre la API HTTP | `playground/mcp_server.py` | Funciona; ninguna tool devuelve imágenes; varias son stubs |
| Pipeline 3D | Nube, malla, objetos 3D, PBR, escena registrada en Three.js | `slscan/metric_recon.py`, `objects.py`, `pbr*.py`, `registered.py` | **Casi todo sintético**; el modo 3D apaga los efectos del runtime |
| App del Quest | WebXR (nunca en el casco), nativa Kotlin (calibra desde el casco), y Quest como servidor de sensores | `quest/`, `quest-nativo/` | En uso solo como servidor de cámara y pose (:8787) |
| PC Windows | Nodo con GPU por SSH: manos, cámara, MIDI | `windows/` | Satélite; es la sesión personal de un tercero y muchas veces estaba apagado |

## 3. El hallazgo más importante: hay dos versiones que se separaron

1. **Versión de octubre**: runtime `playground serve` en :8790 con plugins, API, SSE, MCP y secretaria. Es la que el `docs/CONTRATO.md` del equipo da por base, y la que corre la demo sin hardware (`python scripts/demo.py`).
2. **Versión de noviembre («mesa Quest»)**: `superficie_feed.py` (3188 líneas en un solo `main`, 9 hilos) compone su propia imagen y la manda como JPEG por HTTP a `ventana_proyector.py` (:8793), a ≤5 Hz, y de ahí al proyector por AirPlay. `docs/SETUP-MESA-QUEST.md` dice: «runtime viejo :8790 NO se usa».

Consecuencias:
- **La API y el MCP que el equipo da por listos pertenecen a la versión que Max dejó de usar.** No hay hoy un camino agente → mesa real de noviembre.
- **La mejor calibración (noviembre) y la mejor interfaz de control (octubre) no se hablan.** La reescritura tiene que unirlas.

## 4. Mediciones reales (las únicas con evidencia)

| Qué | Valor | Fuente |
|---|---|---|
| Motion-to-photon (escalón negro→blanco), mesa Quest + AirPlay | **~313 ms** | `docs/SETUP-MESA-QUEST.md:82-85` |
| Latencia de la Osmo a 4K (1 de 5 intentos válido) | 1337 ms | `reports/latency_osmo_proj1.json` |
| Cámara del Quest | 12,7 fps a 864² | `docs/SETUP-MESA-QUEST.md:77` |
| Residuo de calibración de la mesa Quest | 0,52 px; lazo a 0,1 mm de mediana | `docs/SETUP-MESA-QUEST.md:57-66` |
| Tick del runtime con 3 efectos simples | 25,8 ms (el presupuesto a 30 Hz es 33 ms) | medido por el lector de runtime |
| Codificar `/api/frame.png` | 10–43 ms por cuadro (se pide a 8–10 Hz) | medido por los lectores de runtime e interacción |
| Detección de toque por sombra (sintético) | p50 19 ms, p90 23 ms | medido por el lector de interacción |
| Primer cuadro de un efecto (preparación dentro del lock) | 20–200 ms: congela la proyección | medido por el lector de efectos |
| Decode gray code + LUT (sintético) | 1,2 s + 2,4 s (griddata) | medido por el lector de calibración |
| DAViD (personas) en M4 | 8–12 fps | `playground/people/README.md` |

## 5. Deudas técnicas que condicionan la nueva arquitectura

**Rendimiento**
- Todo el render es CPU (numpy y OpenCV) en un hilo Python con un lock global: los comandos esperan un tick completo y la preparación de un efecto congela la proyección.
- Se hacen 3–4 copias de 6 MB por cuadro presentado, y la presentación no está sincronizada con el tick (judder).
- IPC por PNG/JPEG: HTTP por cada evento de toque, PNG a disco + comando `overlay`, JPEG a 5 Hz a la ventana del proyector. Esto explica buena parte de los 313 ms.
- GIL: DAViD en un hilo bajó el runtime de ~25 a ~17 fps; por eso se movió a otro proceso (`playground/people/live.py:4-5`). Es evidencia directa de que la percepción tiene que ir en procesos separados.

**Robustez**
- El hilo de render puede morir sin aviso: un error al reconstruir efectos en hot reload deja la proyección congelada con la API viva. Además, recargar `base.py` vacía la galería en silencio.
- Gestión de procesos con `pkill -f` por patrones, `sleep(9)` en vez de health checks, y pidfiles compartidos.
- `dale` (el flujo de un botón) por defecto **nunca produce rig**: no pasa escala y autocalib sale con código 2. Además captura el gray code dos veces (~100 s perdidos).

**Modelo de datos**
- Tres sistemas de coordenadas mezclados: mm con y hacia arriba, px del proyector con y hacia abajo, y `world_mm`; con nombres engañosos (`width_mm` que son px). En noviembre se suman tres más: cámara, lienzo y contenido.
- Supuestos fijos: 1920×1080, cámara `osmo`, IPs `192.168.1.233` y `192.168.4.161`, rutas `/Volumes/Externo` y `/Users/max`.
- Esquemas como strings libres; `params` sin tipos; 4 comandos fuera del schema.

**Seguridad**
- Sin autenticación en ningún puerto (:8790, :8793, :8795, :8787 del Quest con CORS `*` y Private Network Access).
- Comandos que leen o escriben rutas arbitrarias (`snapshot`, `overlay`, `/mostrar?path=`).
- Hot reload = ejecutar cualquier `.py` en el proceso de render.
- Una herramienta de la secretaria ejecuta un CLI con texto del LLM; secretos leídos del `.env` de otro proyecto.

**Plataforma**
- macOS + pantalla completa o ventana sin bordes en el proyector provocó **kernel panics DCPEXT** (tres veces). La solución fue una ventana normal con barra de título.

## 6. Bugs verificados (para no portarlos)

| Bug | Dónde |
|---|---|
| Recargar `base.py` deja la galería vacía sin error | `playground/runtime.py:42,106` |
| Excepción en hot reload mata el hilo de render | `playground/runtime.py:390-394,445-457` |
| `pg_request_feature` duplica cada pedido | `playground/mcp_server.py:259-260` |
| `dale` sin `--scale` nunca produce rig | `playground/dale.py`, `slscan/autocalib.py:148,243` |
| `slscan grab` y `slscan latency` dan `NameError: open_camera` | `slscan/cli.py:119,423` |
| `texture_grid` no dibuja nada en px del proyector y cuesta 26 ms | `playground/effects/texture_grid.py:34-35` |
| Efectos de audio dibujan en posiciones fijas del estudio de Max | `playground/stage.py:24-34` |
| `grounded_labels` deja «mousepad» y «tablet» como `unknown` | `playground/grounded_labels.py:67-68` |
| `firstlook.py` no importa (`os` sin importar) | `playground/firstlook.py:23` |
| `register_scene` rechaza cualquier rig físico (`pose_estimated`) | `slscan/registered.py:112-115` |
| Filtros P3 por defecto rechazan el 100 % de los puntos reales | `slscan/metric_recon.py:181-188` |
| OHM por MIDI directo descarta todos los eventos | `playground/devices/midi.py:313-320` |
| Bindings de audio pisan parámetros en cada tick | `playground/runtime.py:305-311` |
| `camserve.py` fija FOURCC después del tamaño (la Brio cae a ~5 fps) | `windows/camserve.py:51-52` |
| Tests de `selfcalib` desactualizados (2 fallan) | `tests/test_selfcalib.py` |

Tests: de ~600 tests de las áreas leídas, todos pasan salvo esos 2. Todos corren con fakes o datos sintéticos (el snapshot no trae `captures/`).

## 7. Qué conservar, qué rediseñar, qué dejar

**Conservar (portar el algoritmo, con tests)**
- `slscan/graycode.py` (decoder validado, auto-skip de bits) y `recon.coalesce`.
- La verificación por ArUco y el lazo cerrado de la mesa Quest (`calib_quest_proyector.py`): residuo en mm y alarma de «rig movido».
- El toque por sombra (`touchsynth/osmo_hands.py` + `fusion.py`): es original y técnico, pero hay que validarlo con mano real antes de apostar la demo a él.
- `hands/calib.py`, `hands/touch.py` (máquina de estados de toque) y el patrón de `hands_service.py` (último cuadro gana, detector intercambiable, `/health`, reloj por ping/pong).
- El modelo de objeto (`objects.json`: id, etiqueta, máscara, bbox, plano) y el patrón «preparar offline → activar atómico» de `auto_scene`.
- El contrato de metadatos de `Effect` (`name`, `params`, `targets`, `aliases`): ya es un tool schema.
- El patrón de la secretaria para empujar eventos a la conversación sin interrumpir (`can_speak` + contextual update).
- La regla del puente de toques: bajo presión se botan los `move`, nunca los `down`/`up`.
- Las lecciones de macOS: vsync obligatorio, nunca presentar en el display interno, ventana normal como modo seguro.

**Rediseñar**
- Render (CPU → GPU), IPC (PNG/HTTP → memoria compartida + bus), coordenadas (un marco canónico), comandos (strings → tipos), procesos (pkill → supervisor), agente (proxy ciego → agente que ve).

**Dejar fuera del MVP**
- PBR/radiometría, pipeline P6 con generaciones inmutables, simuladores de sala, WebXR, Kotlin nativo (salvo como servidor de cámara si se usa el Quest), VNC, autologin, Piano Hero, efectos del estudio, luces BLE/DMX.

## 8. Lo que corre hoy en este computador

```sh
cd ~/Projects/platanus_hack_26/sl-scanner
.venv/bin/python scripts/demo.py --no-browser   # runtime :8790 + dashboard :8795
```

Se probó el flujo agente → runtime por la API: `spawn` de `contour_glow`, `texture_grid` y otros sobre la escena demo. `/api/frame.png` devuelve lo que dibujaría el proyector.
