# Informe lector-historia: historia, decisiones y contexto humano de sl-scanner y del equipo

Convenciones: «medido» = cifra registrada en un archivo como resultado; «plan/estimado» = objetivo o cálculo sin medir; «inferido» = deducción mía. Rutas relativas a `sl-scanner/` salvo que diga «equipo» (`platanus-hack-26/docs/`).

## 1. Línea de tiempo del engine

**Quién hizo qué.**
- Max: pide.
- h1: orquestador Creativo. Escribió SPEC y retransmite decisiones (SPEC.md:3).
- h3: unidad Claude Fable que planifica y programa (PLAN.md:3). Son los 107 commits de autor «Max» con `Co-Authored-By: Claude Fable 5.1`. Sus subagentes firman «(agente)» y entran por merges `worktree-agent-*`.
- h4: secretaria por chat (Sonnet). Escribió los requests 001–014 (PLAN.md:194, 238).
- Secretaria de voz ElevenLabs «Playground»: escribió 015–044.
- Codex: 53 commits de autor «MAXMARDONES», en inglés y sin coautor, en las ramas `codex/p1-analisis`, `p2-rig`, `p3-malla`, `p7-pbr`, `vectortrace` y `bundle`. Unidades: h5 análisis, h6 autocalib, h10 objetos, h12/h13 tela y texturas (docs/ANALISIS-pipeline-3d.md:3, docs/P2-RIG.md:79, docs/P5-OBJETOS.md:3, docs/TELA-VIVA.md:1).
- h17: hizo DAViD (requests/046:37).
- El snapshot `b8fecff` lo coescribe Claude Opus 5.5.
- `git branch -a` muestra solo master y origin/master: todas las ramas se fusionaron. No hay trabajo fuera de master.

**Etapas.**
- **15-oct 15:26 · F0/F1 en software** (`899c903`, `f4725c8`): gray code, coalesce y triangulación reimplementados en Python/OpenCV 5; ChArUco, proyector por homografías locales y bundle; 17 tests sintéticos.
- **15-oct 17:53–18:33 · PC Windows por SSH** (`b3c45a0`…`8977689`): setup.ps1 y bootstrap.ps1, más un correo a Roberto, que es el dueño de la sesión del PC.
- **15-oct 18:47–20:28 · Live Mapping Playground** (`c2a0a5e`): runtime con hot reload, 3 efectos, `requests/`, MCP `pg_*` y skill de la secretaria. En hora y media se suman: OHM con 106 controles (`89c57c7`), BLE del foco (`e3acdf5`), cliente VNC propio (`a5f349f`), autologin (`6612fa2`) y los diseños 005–014 en PLAN.
- **15-oct 23:42 → 16-oct 04:30 · primera noche física**, la de más commits:
  - T749 y PJ588D extendidos (`28028c5`); primer escaneo con la Osmo (`a2620be`); primera visual (`fdf8a33`); lazo cerrado scan2 (`2e36982`); recalibración en un comando (`a2124be`).
  - Brio del PC por HTTP (`f420bf9`); calibración con hoja carta (`90e188d`); teclas una por una (`d4d657a`); voz (`52e3a5d`, `133247d`); scan5 (`afcc5c1`).
  - Touch synth, marimba y linterna (`9818aff`); cocodrilo vivo (`511c583`); `output_offset` (`77a4e5b`); Piano Hero con puente MIDI (`12d3622`); visuales v3 (`7732efe`).
  - Max dicta 30 requests por voz (015–044) entre 03:03 y 04:29.
- **17-oct 12:11–23:53 · «ola Codex» del pipeline 3D**, tras «no estoy satisfecho con el pipeline» (BRIEF-pipeline-3d.md:3-9):
  - Piezas P1 a P7: P1 análisis (`55d6b14`), P2 rig (`59ea4ec`), P3 malla (`12067f3`), P6 contratos (`5520af6`), visor (`53a0357`), P5 objetos SAM (`3dc6ae8`), P4 escena registrada (`67b65f0`), P7 PBR (`5883a2b`).
  - `dale` de un botón (`b5d7fef`), autocalib sin tablero (`4126514`), rig rígido y rig aproximado (`07df30f`, `7eb59ca`), dashboard (`589b86b`).
  - En paralelo: efectos de agentes de h3 (`80d6d4e`, `56a4ce8`, `85a19b0`) y vectores, tela y texturas de Codex (`7ff5f58`…`b12c869`).
- **18-oct 15:10–15:32 · bundle sanitizado para compartir** (`e2b2ae2`, docs/BUNDLE.md) y último fix (`f7abd9b`).
- **18-oct → 20-nov · sin commits.** Todo entra en `b8fecff`: 137 archivos y +152 672 líneas, de las cuales unas 109 000 son dos copias de `three.module.js` vendorizado. Fechas que se leen adentro:
  - Feed de superficie con la Osmo, 2-nov (playground/superficie_feed.py:1-2).
  - Quest WebXR v1 y app nativa Kotlin v2, resultados del 5-nov (quest/README.md:176, quest-nativo/README.md:223).
  - Jev, 8-nov (playground/jev.py:1-2).
  - Mesa con el Quest 3 como cámara, desde el 9-nov (docs/SETUP-MESA-QUEST.md:1).
  - DAViD, 17-nov.
  - El mensaje del snapshot dice que incluye «cambios sin commitear del 20-nov (dale, runtime, autocalib, capture con SLSCAN_CAMERA para webcam)».
- Consecuencia: el último mes no tiene historia granular; hay que reconstruir su lógica desde el código y los README.

## 2. Qué se intentó, qué funcionó y qué se descartó

**Funcionó.**
- **Gray code y mapa denso.** Validado contra sltk (PLAN.md:86). El port a Kotlin da «100 % de bloques idénticos» (tabla de quest-nativo/README.md). El auto-skip de bits nació de un hallazgo: la cámara ve ~3,4 px de proyector por px (PLAN.md:609-610).
- **Mapping 2D en el espacio del proyector** (scene_pov, segmentos, efectos en px de proyector). Fue la demo de octubre, pero «no una reconstrucción 3D» (BRIEF:13).
- **Runtime de plugins.** Hot reload, y un plugin roto no tumba nada (PLAN.md:204-207). Desde `4e80b1c` el reemplazo es atómico.
- **Mesa Quest con lazo cerrado (9-nov).** Residuo 0,52 px, lazo a 0,1 mm de mediana, gray vs geometría 0,51 mm (SETUP-MESA-QUEST.md:57-66). Es el resultado de alineación más sólido, aunque en el plano de la mesa.
- **Voz + cola de requests.** Así operó Max la noche del 16-oct (requests 015–044).
- **DAViD.** Corre en el Mac a ~10 fps (requests/046:43).

**A medias o fallido.**
- **Rig métrico.** Nunca se corrió uno real hasta el 17-oct (BRIEF:21-24).
  - El autocalib sintético da XYZ RMS 6,78 mm, máximo 12,58 mm: «no cumple ≤2 mm», y la captura física de una vista «falló por rango y reproyección» (docs/P2-AUTOCALIB.md:58-77).
  - En el rig aproximado real, todos los throw supuestos convergen: «el barrido no mide el throw real» (docs/P2-RIG-APROXIMADO.md:41-58).
  - h1 lo autorizó para el show «sin verificar».
- **Parches de alineación.** Hubo `nudge`/`ajustes.json`, `output_offset` global y refine de teclas.
  - El refine en vivo llevó el IoU de 0,24 a 0,33 y se revirtió (PLAN.md:670-671).
  - El offset global dejó a Max viendo «todo el output desfasado unos 4 cm» (requests/038).
  - Max los prohibió como solución (BRIEF:72-74).
  - El valor es inconsistente: (−1, +20) px en PLAN.md:681 y `77a4e5b`, pero (−21, +21) en BRIEF:40.
- **Segmentación por reglas por objeto** (cocodrilo por tono, teclado por periodicidad, platos por elipse). «No generaliza» (BRIEF:19-20). Se pasó a P5 (SAM + etiquetas locales) y después a SAM + agente Sonnet/Jev en la mesa (SETUP:127).
- **Manos del cocodrilo (16-oct).** No respondía (040), usaba la cámara equivocada (042) y no aparecía el feed (044).
- **MIDI.** El EK-50 no manda notas al PC (PLAN.md:658-659); el Circuit Tracks no está segmentado; el Mac no ve la QX1222USB (PLAN.md:648).
- **Pantalla completa en macOS.** Tras cuatro iteraciones (Spaces, fullscreen clásico, NOFRAME en `53229eb`) terminó en «Nada de pantalla completa… tres kernel panics DCPEXT (8 y 9-nov)» (SETUP:107-108).
- **VNC como servicio.** Lo bloqueó el clasificador de permisos con «Unauthorized Persistence» (PLAN.md:434-439); se escribió un cliente propio.

**Planificado y no hecho** (inferido: no hay módulos que lo cierren y los requests siguen `open`):
- Cabezales móviles, Sony FX3, DJI SDR y YoloBox (005).
- Foco SmallRig por DMX/BLE más allá de `scan/dump/learn` (PLAN.md:275-292, 481-518).
- iPad LiDAR (010), segundo proyector con blending (012), viewfinder handheld con VDA (013), monitores con piano roll (014), portales por observador (045).
- Smart Vision quedó excluido por ser código de cliente (PLAN.md:157-160).
- El sucesor práctico del 013 es el Quest como fuente de pose 6DoF (quest-nativo, inferido).
- Ojo: los status de 015–044 nunca se actualizaron; todos dicen `open`, incluso el 023, que confirma un arreglo. No sirven como tracker.

**Hallazgo crítico para la reescritura: hay dos pilas.**
- Runtime viejo `playground serve` (:8790, plugins de efectos, MCP `pg_*`).
- Pila de la mesa Quest: feed `superficie_feed` (:8798) + `ventana_proyector.py` (:8793).
- SETUP-MESA-QUEST.md dice «runtime viejo :8790 NO se usa» (:32) y «No lanzar el runtime viejo… el feed ya no le habla» (:110). La tabla marca «Toques/manos al runtime: apagado (no hay runtime)» (:130).
- El PLAN del equipo dice que el engine «ya tiene API HTTP y servidor MCP para que un agente prenda efectos» (equipo PLAN.md:51). Esa API es la del runtime que la pila más reciente dejó de usar.

## 3. Mediciones reales registradas

| Qué | Valor | Fuente |
|---|---|---|
| F0 vs sltk | 104 830 vs 104 809 puntos, mediana 0,018 mm (0,00006 mm en `--mode sltk`) | PLAN.md:14, 86 |
| Primer escaneo real (Osmo) | 44 patrones a 1 s; ~3,4 px proy/px cám; 123 181 px decodificados con skip-bits 3 | PLAN.md:608-612 |
| Esquinas vs homografía densa | diferencias 8,2 / 6,6 / 1,9 / 20,6 px; 2589 inliers | reports/scan1_corners.json |
| Cobertura scene_pov | 97,6 % de px de proyector | PLAN.md:622 |
| Shaders firstlook | 14,8 fps en CPU a 960×540 | PLAN.md:630 |
| Lazo cerrado scan2 | mediana 0,9 px de cámara | `2e36982` |
| scan5 | ArUco sueltos 1,05 px mediana; 209 k px decodificados | PLAN.md:643, BRIEF:16 |
| Osmo a 1080p | 607×658 px útiles (encajonada) → se pide 4K y se reduce 2× | PLAN.md:642 |
| Teclas | bordes 0,34; ECC 0,83; sintético < 0,4 px; refine sintético 0,42→0,74 con 111 capturas; refine en vivo 0,24→0,33, rechazado | PLAN.md:660-671 |
| Corrimiento global | (+0,8, −19,9) px, respuesta 0,45; IoU de teclas 0,62→0,70; luego 0–4 px en Buda, DDJ, torre y hoja | PLAN.md:677-680, 694 |
| Render keys_map | 120 ms → 2 ms por cuadro | PLAN.md:693 |
| MIDI PC→Mac (`--fake`) | ida y vuelta 2–5 ms, 0 pérdidas | PLAN.md:656-657 |
| Latencia proyector→Osmo | 5 intentos, 1 válido: 1337 ms; cámara a 25 fps; settle recomendado 1417 ms | reports/latency_osmo_proj1.json. **No confiable**: PLAN.md:569-570 dice que no se pudo medir porque la Osmo estaba en movimiento |
| align_test | varianza de bordes 30,2 (gris) vs 33,3 (invertida); mejor corrimiento (−3, −3), varianza 31,7 | reports/align_test.json (inferido: prueba de textura invertida de `96b663c`) |
| osmo_watch.log | una sola línea, «23:56:17 osmo=2»; sin valor de medición | reports/osmo_watch.log |
| PC | RTX 4070 12 GB, torch 2.14.1+cu126, Windows 11 26300, Python 3.12.15, 105/999 GB libres, display 1024×768 por SSH, una cámara 640×480 DSHOW, sin puertos serie, cv2 5.0.0, open3d 0.20.0 | reports/pc_doctor.json |
| Autocalib sintético (3 vistas) | RMS 0,288 px cámara / 0,128 px proyector; XYZ RMS 6,78 mm, p95 10,63, máx. 12,58; con una vista, máx. 126,85 mm | docs/P2-AUTOCALIB.md:58-64 |
| Rig aproximado real (17-oct) | RMS 1,06 px; auditoría: mediana 0,365, p95 2,13, máx. 6,48 px | docs/P2-RIG-APROXIMADO.md:41-58 |
| Cámara Quest | 12,7 fps (dt mediana 80 ms, p95 ~125 ms); `cam_full` 145–295 ms | SETUP-MESA-QUEST.md:73-80 |
| **Motion-to-photon (mesa Quest)** | escalón negro→blanco ~313 ms (AirPlay + proyector + exposición); cámara sola ~40 ms | SETUP-MESA-QUEST.md:82-85 |
| Calibración mesa Quest | residuo 0,52 px; lazo 0,1 mm de mediana tras 3 pasadas; gray vs geometría 0,51 mm (p90 1,1); 6 % > 3 mm por paralaje | SETUP-MESA-QUEST.md:57-66 |
| Verificación `/recalibrar` | 12 cuadrados de 36 mm, ~3 s; ajusta si la mediana pasa 1,5 mm; umbral de verificar 3 px / 2 mm | SETUP-MESA-QUEST.md:89-94 |
| Salida de la mesa | ≤ 5 Hz, JPEG q90; lienzo de 0,4 mm/px (820×462 mm) | SETUP-MESA-QUEST.md:25, 50, 118 |
| Quest nativo (sintético) | gray 0,067 px de mediana; cadena completa: centro 1,1 mm, 0,17 px | quest-nativo/README.md (Resultados 5-nov) |
| WebXR v1 (sintético) | ruido de toque de 1 cm → 5,6–9 px | quest/README.md:176-184 |
| DAViD en Mac M4 | normal sola 40–57 ms a 384 px; en vivo 8–12 fps | playground/people/README.md:27-41 |

**Solo plan o estimación, sin medir.**
- VDA en la 4070 a 50–60 fps (PLAN.md:451-454).
- Presupuesto del handheld de 70–100 ms (PLAN.md:381-383).
- DAViD a ~22–30 fps en la 4070 (people/README.md:44).

**Objetivos nunca verificados.**
- F1: < 0,5 px cámara y < 1 px proyector (PLAN.md:101).
- F5: < 2 mm y < 100 ms (PLAN.md:149).
- ≤ 2 mm de Max (BRIEF:66).
- Red < 50 ms, Osmo→proyector < 60 ms y réplica < 20 ms (PLAN.md:341, 548).

docs/PERFORMANCE-EFECTOS.md define el método de benchmark de efectos (1920×1080, dt 1/30, 30 cuadros). Sus cifras están en `reports/scene_regions/...`, que es privado y no viene en el snapshot.

## 4. Hardware real y supuestos de entorno

- **Mac.** MacBook Air M4 con el disco casi lleno: 4,2 GB libres en SPEC.md:48, ~1 GB en PLAN.md:13, ~5 GB en BRIEF:89. Usa un disco externo para caché.
  - Fue la máquina real de todo: proyector, Osmo, voz, MCP y, en noviembre, el feed de la mesa con SAM.
- **PC NEW-WHITE-4K.** RTX 4070 12 GB con 128 GB de RAM (PLAN.md:193); 4 salidas de video, 3 DP y 1 HDMI (PLAN.md:361).
  - Es la sesión personal de Roberto: VNC, Avast y autologin pendiente (PLAN.md:464-479).
  - Por SSH solo ve 1024×768.
  - Estaba apagado el 17-oct (BRIEF:42) y el 17-nov (people/README.md:44).
  - En la práctica fue satélite: Brio por `camserve` :8794, puente MIDI UDP 8796, `midi_play` por SSH con la salida de audio de la QX1222 (windows/README.md:50-75).
  - La «torre Linux» es un PC de 2013 que no sirve.
- **Proyectores.**
  - T749-fHD720 1920×1080 (PLAN.md:553).
  - PJ588D 1024×768 (`28028c5`).
  - Projector-6325 por AirPlay (SETUP:30).
  - Magcubic HY320 Android con el Quest (quest-nativo/README.md:3).
- **Cámaras.**
  - Osmo Pocket 3: UVC hasta 4K a 25 fps, vertical, comprimida y sin exposición fija (PLAN.md:556-558, BRIEF:124).
  - Logitech Brio en el PC.
  - Quest 3 desde el 9-nov, como cámara fija de la mesa; reemplazó a la webcam y a la Osmo (SETUP:3-5).
  - Pixel como cámara IP (`91833e3`).
  - iPad usado como Sidecar y para mostrar el ChArUco.
  - La FX3 nunca aparece conectada (inferido).
- **MIDI y audio.** OHM RGB (el puerto y los LEDs son de SmartDeck: PLAN.md:256-262), DDJ-SB2, Circuit Tracks, Korg EK-50 de 61 teclas y Behringer QX1222USB (solo audio).
- **Luces.** SmallRig RC 100C (BLE Mesh; DMX sin adaptador a la venta) y barra RGB manual. Nada operativo: no conviene planificar DMX/BLE.
- **En la hackathon** (equipo PLAN.md:19, 49, 52):
  - Dos proyectores, ~10 cámaras, teléfonos, dashcam, ESP32 con pantalla táctil de 7", servo para foco, badge, impresoras 3D.
  - Un rincón oscuro con mesa larga.
  - El engine original corre en el MacBook de Daniel con proyector.
- **Sin confirmar en mis fuentes:** si el Quest 3, el iPad o una GPU NVIDIA estarán en la sede. Max sí nombra «iPad con LiDAR, Quest, webcams» como piezas de su sistema (equipo PLAN.md:17; transcripción 1755:41).

## 5. Patrones en el feedback de Max (lo que no se puede romper)

1. **Alineación impecable y verificable.** «superalineado» (015), «tecla por tecla… hasta asegurar que quede perfecto» (021), «ultradetalle… que la geometría se adapte perfecta a absolutamente todo» (030), «2 cm más abajo y 2 cm más a la izquierda» (034). Umbral duro ≤ 2 mm (BRIEF:56, 66).
2. **Itera a ojo, pero quiere la causa en la calibración.**
   - Dicta corrimientos: «un poquito más abajo» = 4 px con `pg_mover` (PLAN.md:646); «dos ticks más» (028).
   - Propone A/B: checkers de colores y «tú mismo elige los mejores píxeles» (020), versiones en colores para votar (027), imagen invertida o bordes (034).
   - Pero exige: «ese finetuning por objeto no debería ocurrir si el setup está bien calibrado; tal vez el FOV o una distancia errónea…» (BRIEF:57-59). Prohíbe los corrimientos como solución (BRIEF:72-74).
   - Detecta cuando una corrección global estropea lo que ya estaba bien (038).
3. **Nunca un proyector oscuro u ocioso.** «No me gusta estar viendo siempre una pantalla apagada… Es urgente: estamos todos mirando» (017); «ahora mismo estoy viendo oscuridad» (029). Quiere ver en vivo las ediciones y calibraciones.
4. **Observabilidad y auto-verificación del agente.** Comparar capturas con las dos cámaras (015, 016, 021). Ver el feed de la cámara del tracking (041). «que el orquestador audite todo en tiempo real» (016). Ver el pipeline mientras corre (BRIEF:113-115).
5. **Verificación estándar:** todo blanco con los contornos en negro, para que la foto muestre los objetos; nunca líneas blancas sobre negro (BRIEF:59-60, 75).
6. **No bloquearle pantallas ni flujo.** iPad en negro con el proyector encendido (022, resuelto en 023). Quiere seguir hablando en la terminal y que los agentes se turnen el proyector (022). De ahí la regla: todo lo proyectado pasa por el runtime u `overlay` y los agentes no abren ventanas propias (PLAN.md:645).
7. **Interactividad por encima de la decoración.** Tocar el cocodrilo con la mano es «lo más importante para que sea interactivo» (031). Prueba en el acto y reporta fallas concretas (040, 044).
8. **Criterio estético propio y por capas.**
   - Le gusta la foto real del objeto como shader (033, 034), la «estética del globo» (020) y las escamas.
   - Rechaza el halo cuadrado amarillo (033). El orden de capas importa: el Buda va bajo el cocodrilo.
   - Quiere usar cada superficie: torre, DDJ, hoja (028, 036).
9. **Generalidad.** «La gracia es que un agente analice los objetos». Nada específico del cocodrilo fijo en el código (BRIEF:7-8, 49-50).
10. **Pragmatismo de hardware y recursos.** La cámara que tiene sentido (042). No instalar en el PC si el Mac basta (043). Ableton Link sin abrir Ableton (025). Sonido por la mesa y si no por el Mac (026).
11. **Ritmo.** «que se apuren», «comiteen», «showcase final» (020, 030).
12. **Gobernanza.**
    - Nada se compra, publica ni sube sin Max (PLAN.md:18, SPEC.md:74).
    - Capturas privadas y no tocar ventanas personales (PLAN.md:464-469).
    - Llaves por referencia.
    - Sin código de clientes (SPEC.md:131-132).
13. **Robustez al movimiento** de objetos, cámaras y proyectores, con el mapa en el marco de la mesa (SPEC.md:136-138, PLAN.md:74-77).

## 6. Decisiones de arquitectura ya pensadas o pedidas

- **011 · distribución Mac↔PC** (requests/011; PLAN.md:321-351, 520-549).
  - El estado vive en un solo motor.
  - HTTP+SSE para comandos y estado; WebSocket para eventos < 10 ms; video solo si hace falta y a la resolución del proyector.
  - Relojes por ping-pong; reconexión sola; `links` en `pg_state`.
  - v2: el Mac renderiza el proyector 1 desde una réplica de la escena y el PC conserva la GPU. Por la red viajan «resultados (poses, máscaras comprimidas, puntos), no video».
  - Status `open`, sin las mediciones de cierre. Para la reescritura sirve el principio: una fuente de verdad, réplicas que renderizan sin esperar la red, resultados en vez de video.
- **012 · varios proyectores.**
  - Cada uno con intrínsecos, lienzo, ventana y zona propios.
  - Pose fija, de cabezal o «en vivo».
  - Cada efecto declara `projector: proj0 | proj1 | best`; blending en los solapes.
  - `open`. El modelo de proyector con pose en vivo es justo el que pide el handheld.
- **013 · handheld viewfinder.** Pose 6DoF del dispositivo XR pegado + SAM + profundidad monocular: Metric-VDA-Small en streaming, Apache-2.0; Base y Large son CC-BY-NC. Lleva predicción de pose; el riesgo principal es la latencia (PLAN.md:367-387, 441-460). `open`; su sucesor práctico es quest-nativo.
- **009 · topología multicámara.** Todas las cámaras con intrínsecos y pose en el marco de la mesa, con fusión, y anotando qué cámara sirve para qué: las comprimidas no sirven para escanear. Solo se hizo `dualview` con deriva entre dos cámaras (`c1a17a5`).
- **005 · streaming.** El YoloBox solo transmite; no sirve para tracking, con ~100–200 ms de latencia estimada (PLAN.md:389-398).
- **010, 014, 045.** iPad como RGB-D, monitores como objetos `screen` con lienzo propio, portales con render off-axis por observador. Todo `open`. Del 045 queda un principio: dos observadores no pueden ver bien la misma superficie, así que hay que elegir cómo degradar.
- **046 · DAViD:** `done` (capa `people/live.py`, efecto `relight_people`).
- **Ola Codex.**
  - Contratos versionados P6 con etiquetas de evidencia (`evidence=synthetic`, `metric_status=unverified`).
  - Eventos JSONL (`stage`, `step`, `progress`, `msg`, `ok`) en los pasos largos.
  - `dale` como flujo de un botón narrado (docs/P2-AUTOCALIB.md:137-157; `b5d7fef`).
- **Runtime.**
  - Plugins que declaran `needs` y `targets` (PLAN.md:220).
  - API con el mismo contrato que BIOME (PLAN.md:207).
  - MCP `pg_*`.
  - Como mucho 2 instancias programando a la vez, cada una en su rama o worktree (PLAN.md:215-219).
- **Coordenadas.**
  - Milímetros, con el mundo fijo en la mesa (PLAN.md:72).
  - En la pila Quest: ancla MRUK de la mesa en mm, lienzo de 0,4 mm/px y cadenas de homografías nombradas para la entrada y la salida (SETUP:44-68).
- **IA fuera del lazo caliente.**
  - Max: la IA «prearma» y el tracking es determinista por latencia (transcripción 2220:31; equipo PLAN.md:17).
  - Jev recibe solo texto, ~200 ms (jev.py:4-5).
  - El SAM de la mesa se rehace cada 45 s (SETUP:127).

## 7. Del lado del equipo de la hackathon

Advertencia: las transcripciones son automáticas y sin nombres de hablante; las atribuciones son inferidas por contenido y cruzadas con el PLAN del equipo.

- **Max** (SPEAKER_04 en 1755, SPEAKER_00 en 2220 y en la del mentor).
  - Quiere que los agentes «salgan» de la pantalla: «sentía que mis agentes necesitaban como expandirse» (1755:22).
  - Hardware barato: «proyector de 50 lucas de mercado libre… Y un teléfono Android» (1755:28).
  - Propone un engine generalista y luego tracks de casos de uso: smart desk, guía sobre objetos y handheld con servo de foco (2220:9-22).
  - Lleva 10 años en mapping (mentor:12).
  - Defiende ambición y wow: «ambición tenía un porcentaje súper grande en la rúbrica» (mentor:103), «la ponderación de Wow Factor aplica más que cualquiera» (mentor:111).
  - Meta del viernes: engine andando con unos 4 casos; propuso a Matilda y Pablo para los casos de uso (mentor:113).
- **Daniel.**
  - La presentación es lo que más importa; un QR para que el público lo pruebe (equipo PLAN.md:61-63; 1755:21, 29-30).
  - Superhuman Desk y pitch de menos a más.
  - Cámaras «enchufar y listo» con el celular (equipo PLAN.md:99-112).
  - Domótica y agente observador (2346:4).
  - «lo que me preocupa es que el engine funcione» (2339:3).
- **Manu.** Con Daniel, engine funcional y puente agente↔engine (2339:7; equipo PLAN.md:45, 112). En mis fuentes no hay frases que pueda atribuirle.
- **Pablo y Matilda.** Montaje con Max primero y después casos de uso, pitch y nombre (equipo PLAN.md:44-46). Un hablante sin identificar (SPEAKER_04 en la del mentor) es escéptico: «se vuelve un producto muy técnico… Yo no sé si la gente quiere eso» (mentor:30-32).
- **Mentor** (SPEAKER_01, inferido; «Básicamente dirijo un grupo ingeniero», mentor:124), en sus palabras:
  - «¿Cuál es el dolor que ustedes vieron…? No me queda claro hasta dónde acompaña» (:11).
  - «nos falta el juego, como qué es lo que me va a mostrar… que me va a cambiar» (:23).
  - «a menos que tenga uno bien potente como que igual no está iluminando» (:36).
  - «lo importante igual es como el dolor detrás y eso es lo que va a generar impacto» (:42).
  - «mucho mejor si se enfocan en una cosa… Cuando tratáis de abarcar mucho, abarca poco» (:72).
  - «Tiene que doler. El año pasado ganó un sistema automatizado de exploración y auditoría de instalaciones públicas» (:99).
  - Construir de forma incremental (:112).
  - «la lógica que cómo vas a transformar este mundo… qué le vas a presentar» (:116).
  - «mucha tecnología que es muy guau, pero después uno lo empieza usar…» (:118).
- **Casos y dolores barajados** (equipo PLAN.md:9-13, 28): smart desk, guía de armado y reparación (industria, equipos médicos, minería), handheld (auto o calle), educación (pupitre, textos, liceo técnico), AR sin lentes y compartida, agentes en el mundo físico, salas de reunión, discapacidad visual, retail, mascota-agente, origami, modo stocks, pizarra.
- **Lo que recomienda la investigación.**
  - Guía de armado con prueba medible en vivo (02-dolor.md:54-59).
  - Pitch: «un agente que ve tu mesa y te arma la interfaz» (01-arte-previo.md:49).
  - No vender «democratizar el mapping»: fue el pitch de Lightform, que cerró (01:43-44). Max usó esa idea con el mentor (mentor:18).
- **Restricciones físicas.**
  - Brillo: proyectores de menos de USD 100 dan ~40–274 ANSI. Sobre A3 son ~400–2200 lux y un contraste de ~2:1 a 6:1 con luz de oficina: solo un área chica es viable, y hay que medir el proyector real (02-dolor.md:44).
  - Respuesta acordada: el rincón oscuro (transcripción 2334; equipo PLAN.md:49).
  - Latencia medida de la cadena Quest + AirPlay: ~313 ms.
  - Contraste: UI optimizada para la luz (2220:26).
- **Tiempo.** Fin del hacking el domingo 08:30–09:00, a verificar (equipo PLAN.md:78). Hitos del sábado: proyección alineada a las 13:30 y smart desk interactivo a las 20:00.
- **Abierto.** El dolor y el usuario (equipo PLAN.md:30), si se permite código previo (:93), el repo de entrega, el jurado, Riemann, el acceso al dashboard, invitar a Matilda y el brillo real del proyector.

## 8. Lecciones para la reescritura

**Riesgos y trampas ya vividos.**
1. **Dos pilas divergentes** (runtime :8790 con MCP vs. feed de la mesa :8798 + ventana :8793). Hay que elegir la base antes de tocar código, y no prometer que el MCP mueve la mesa sin cablearlo.
2. **macOS y los displays.** Kernel panics con fullscreen o borderless. No usar `displayplacer` mientras algo presenta. Un solo dueño de la ventana. No calibrar mientras el feed manda cuadros. No abrir más de un cliente pesado de `/cam.mjpg` (SETUP:105-112).
3. **Parches que tapan la calibración** (nudges, offsets globales), además con valores inconsistentes. Max los rechaza.
4. **Precisión declarada vs. medida.** El ≤ 2 mm 3D nunca se certificó; lo único sólido es el plano de la mesa con lazo cerrado.
5. **Cámaras comprimidas, con auto-exposición o lentas** (Osmo en modo webcam, Quest a 12,7 fps). Los bits útiles del gray code dependen de la relación de px entre cámara y proyector.
6. **Dependencias frágiles.** Un PC ajeno y a menudo apagado, la sesión de otra persona, Avast, un MIDI que no llega.
7. **Sobreplanificación** de hardware ausente (FX3, cabezales, DMX): horas de PLAN sin uso.
8. **Heurísticas por objeto** que no sobreviven a una escena nueva.
9. **Licencias.**
   - sltk es MIT: mantener el aviso de THIRD-PARTY-NOTICES.txt.
   - VDA Base y Large son no comerciales; DAViD es MIT; three.js va vendorizado.
   - Hubo una llave de ElevenLabs en texto plano en `~/ohmrgb` (PLAN.md:270-271).
10. **Historia opaca de noviembre:** un solo commit.
11. **Requests sin mantener:** los status no reflejan el estado real.

**Conservar sí o sí.**
- El decoder de gray code validado, con su auto-skip de bits.
- `/recalibrar`, que devuelve el residuo en mm con umbrales (3 px / 2 mm), y el lazo cerrado.
- Milímetros en el marco de la mesa, con cadenas de transformaciones nombradas.
- Runtime de plugins con hot reload aislado, `needs`/`targets` y reemplazo atómico.
- Eventos JSONL y etiquetas de evidencia (sintético o físico; verificado o no).
- Proyector siempre vivo, con algo andando.
- Observabilidad: feed y pipeline visibles.
- Un solo proceso dueño del proyector, con turnos por overlay.
- La IA fuera del lazo caliente.
- Privacidad de capturas y llaves en memoria.
- Secretaria por voz + cola de pedidos como forma de trabajo de Max.

**Preguntas abiertas para Max o el equipo.**
1. ¿La base es el runtime con MCP (:8790) o la mesa Quest (:8798/:8793)? ¿Hay que unirlas?
2. ¿Qué cámara y qué proyector habrá en la sede (Quest 3, Osmo, webcam, celular; T749, HY320, uno de 50 lucas)? ¿HDMI o AirPlay?
3. ¿Habrá una GPU NVIDIA en la sede o todo corre en el Mac?
4. ¿Qué latencia es aceptable para las interacciones de la demo? Hoy son ~313 ms.
5. ¿El criterio de ≤ 2 mm aplica a la hackathon, o basta el plano de la mesa con lazo cerrado?
6. ¿Jev (Vercel AI Gateway) y SAM siguen siendo el camino de etiquetado? ¿Con qué llaves y créditos?
7. ¿Cuál es el `output_offset` correcto, y debe seguir existiendo?
8. ¿Qué efectos de octubre (cocodrilo, Piano Hero, visuales v3) se conservan y cuáles se archivan?
9. ¿Se permite traer código previo a la hackathon (equipo PLAN.md:93)?

## Archivos leídos

**Completos, en sl-scanner:** PLAN.md, SPEC.md, requests/README.md y los 46 requests, docs/BUNDLE.md, docs/INDICE.md, docs/SETUP-MESA-QUEST.md, docs/BRIEF-pipeline-3d.md, docs/PERFORMANCE-EFECTOS.md, windows/README.md, examples/demo/README.md, reports/latency_osmo_proj1.json, pc_doctor.json, align_test.json, osmo_watch.log, scan1_corners.json, THIRD-PARTY-NOTICES.txt. Del historial git: log completo con cuerpos, resumen `--stat` por commit, stat del snapshot y `git branch -a`.

**Completos, en platanus-hack-26:** las 6 transcripciones y su README, docs/investigacion/01-arte-previo.md, 02-dolor.md, docs/PLAN.md.

**En parte:** docs/P2-AUTOCALIB.md (resultados), docs/P2-RIG-APROXIMADO.md (ensayo real), docs/P2-RIG.md (inicio), quest/README.md y quest-nativo/README.md (encabezado y resultados), playground/people/README.md (velocidades), cabeceras de playground/jev.py y playground/superficie_feed.py.

**No leídos:** imágenes y HTML de reports/, incluido `_crit/final.jpg`; diffs de git (por diseño); docs/ANALISIS-pipeline-3d.md, P3/P4/P5/P7, PIPELINE.md, GUIA-ARTISTAS.md; docs/CONTRATO.md del equipo.
