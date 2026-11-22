# Paridad mesa → pcx (sáb 21-nov, ~06:30)

Qué hizo funcionar el equipo en la mesa física (rama `mesa`, engine de Max en Python) y qué le falta a pcx (rama `manu/plataforma`) para reemplazarla en la demo. Es un análisis de lectura: no se compiló ni se corrió nada, y las cifras vienen de los documentos y del código citados. El orquestador propuso el **sábado a las 16:00** como hora de corte para elegir con qué stack se presenta.

Fuentes: `origin/mesa` @ `f937dc6` (respaldo de las 04:19), `origin/main` @ `f859e54` (`docs/PLAN.md`, `docs/estado/engine-max.md`, `docs/pruebas/`, `docs/orquestador/`) y `manu/plataforma` @ `8163e4e`.

## 1. Estado de las últimas pruebas de Max y Daniel

Todo en el MacBook de Daniel, con el proyector T749 y una webcam USB cenital de 2560×1440 a 20 fps.

| Hora | Quién | Prueba | Resultado |
|---|---|---|---|
| 00:40 | Max (engine original, `dale`) | Escaneo gray code de 44 patrones (~5 min) | Funciona el mapa 2D: 11,3 % de px decodificados; 13 ArUco, mediana 1,75 px. **Falló** la autocalibración métrica («coordenadas fuera del raster»), así que no hay 3D |
| 00:45 | Max | Escena automática (MobileSAM + Grounding DINO) | 10 objetos; solo 3 con nombre fiable, 7 quedaron como «segmento N» |
| 00:55 | Daniel | Manos con MediaPipe y la LUT del gray code | ~24 fps, 21 puntos por mano en px del proyector |
| 01:10 | Daniel | 6 efectos nuevos y etiquetas | Corriendo con recarga en caliente, sin errores |
| 01:17–01:43 | Daniel | Anfitrión autónomo (Sonnet, 3 cámaras, carteles, voz) | 25 ciclos con público real; manos sobre el paño a las 01:36–01:41. Gastó la mayoría de los ciclos en desencimar carteles |
| 01:22 | Daniel | Micrófono por gesto 🤘 | Graba y transcribe en el whisper del mini, por ssh |
| 01:55 | Daniel | Reinicio limpio con `seguidor_v4` (cascada) | Vigente; v1–v3 quedan obsoletos |
| 02:10 | Daniel | Lienzo rectificado, marcador de sincronía, registro con Sonnet | Error mediano 0,2 px; registro en 15–25 s (~US$ 0,13) |
| 02:15 | Daniel | Escaneo rápido | 4,4 s de reloj; cobertura 84 %; mediana 2,0 px y p90 3,2 px; 3 de 3 corridas buenas |
| 04:19 | Daniel | `mesa kit` con paleta de colores por objeto | Último respaldo; sin cifras |

**Qué falló o quedó abierto**
- **No hay cifra de punta a punta de la cascada.** Por el código, v4 tarda unos 2,2 s o más desde que se suelta la mano: 0,4 s de calma, una ventana fija de 1,65 s en `limpio()` y ~0,2 s del nivel 2. El «< 1 s» de `ANFITRION.md` se escribió a la 01:2x, cuando corría el seguidor v1 (sin apagón).
- Los objetos altos (bowl, taza, rollo) quedan corridos al moverlos, por paralaje. El nivel 2 deja ~40 px de error en el Arduino. Un objeto que sale del área no siempre se oculta: un juez eligió el reflejo de la lámpara.
- No existe `demo_segura.sh`: el arranque es manual, con 5 procesos, y la cámara solo abre desde la sesión con permiso de macOS.
- Escaneo rápido: sesgo de −2 px en x, resolución 1280×720, sin control de calidad automático; sube a ~6 s si la cámara baja a 15 fps.
- Las cámaras lateral y FaceTime solo sirvieron de foto para el anfitrión. No hay nada medido sobre la libreta ni el nivel del líquido.
- La webcam no acepta exposición ni balance de blancos manuales.

**pcx en hardware** (T749 + C920 apuntando al techo): 60 fps y 215 ms de punta a punta; calibración precisa en 29,5 s con 0,8 px por plano; manos a 30 fps. La captura UVC no abre la C920 (bug B6). **No hay registro de que pcx haya corrido en la mesa de Daniel.**

## 2. Matriz de paridad

Niveles de la cascada: N0 diferencia entre fotos limpias, N1 «sigue igual», N2 movimiento rígido, N3 DINO + SAM, N4 un LLM desempata, N5 registro global.

| # | Capacidad | Origen en la mesa | pcx hoy | | Destino en pcx |
|---|---|---|---|---|---|
| 1 | Foto limpia con marcador de sincronía | `seguidor_v4.py::limpio` | Existen `blank` y `blanked{t_start, t_end}` en core y renderer, pero nadie toma la foto. Sin marcador: va por tiempos, y `t_present` se adelanta 205 ms a la luz (B2) | 🟡 | `pcx-perception::clean` (crate nuevo) |
| 2 | Escaneo rápido de 4,4 s | `escaneo_rapido.py` | `pcx-calib` precise: 3,8 s en simulación y 29,5 s en el techo real. El `calibrate` del MCP solo encola y el core no reenvía `pattern` | 🟡 | `pcx-calib` + `pcxd calibrate` |
| 3 | Lienzo rectificado de 0,2 px | `seguidor_v4.py::Mapa` (`lienzo_map.npz`) | `pcx-calib` genera el polinomio (`Poly2`, 0,03 px simulado), pero `pcx-objects::Rectifier` solo acepta homografía | 🟡 | `pcx-objects::geom` |
| 4 | Cascada v4, N0–N2 | `seguidor_v4.py` | Portada con los mismos umbrales: 16, 46 y 123 ms; 19 tests sintéticos | ✅ | `pcx-objects` |
| 5 | Cascada N3 (DINO + SAM) | `detector_dino.py` (0,5–1,5 s) | ONNX tras un feature; el modelo (719 MB) no está bajado y no hay cifras (el README conserva el marcador `NIVEL3_CIFRAS`) | 🟡 | `pcx-objects::onnx` |
| 6 | N4 y registro con SAM + Sonnet (~20 s) | `juez_claude`, `registrar_todo` | `register_all` y `ClaudeClient` por HTTP, probados solo con mock (falta la API key) | 🟡 | `pcx-objects::llm` |
| 7 | Retomar catálogo y verificación periódica | `seguidor_v4.py::run`, `verificar_todo` | Sin portar. La tabla `catalog` guarda solo nombre, prompt y recortes | ❌ | `pcx-objects` + `pcx-core::store` |
| 8 | Bucle en vivo: episodio de manos, cada 45 s, manual, pausa por escaneo | `seguidor_v4.py::run` | No existe: `pcx-perception` figura apagado en `node/pcx.toml` y no tiene binario | ❌ | `pcx-perception` |
| 9 | Contrato objeto → efectos (contorno, afín, seq, oculto, apariencia) | `seguimiento.py`, `pose_mesa.py` | `ObjectState` con `aff`, `appearance` y `seq`; los widgets con `anchor.object_id` se recalculan en cada diff. Falta el fundido de entrada | ✅ | `pcx-core` |
| 10 | Manos como sensor y episodios | `manos_mesa.py` | `pcx-hands`: 30 fps en vivo y `hands_gone` a los 0,4 s. Falta el worker (frente de cableado, en curso). Con una sola cámara los toques son falsos: usar permanencia | 🟡 | `pcx-hands` |
| 11 | Cuadro compartido entre procesos | memmap en `/tmp/mesa` | `pcx-shm` (0,23 ms). La fuente UVC está rota en macOS | 🟡 | `pcx-capture` |
| 12 | Micrófono por gesto | `mesa/microfono.py` | Nada: ni gesto, ni audio, ni transcripción | ❌ | `pcx-voice` (nuevo) |
| 13 | Anfitrión autónomo con 3 cámaras y voz | `mesa/anfitrion.sh`, `ANFITRION.md`, `mesa/mesa` | El MCP da `look`, `project` e `history`, pero no hay agente (`pcx-agent` sin implementar), ni voz, ni segunda cámara cableada | ❌ | `modes/anfitrion` |
| 14 | Paleta de colores por objeto | `mesa/mesa kit` | Nada automático | ❌ | opción `kit` del modo |
| 15 | Espacio libre para paneles | `mesa_libre.py` | El agente elige `rect_mm` a ciegas | ❌ | `pcx-core::geom` |
| 16 | Control por agentes | `mesa/mesa` (spawn, set, kill, msg) | MCP con 10 tools, flujos locales, idempotencia y nube | ✅ | supera a la mesa |

**Efectos** (`engine/playground/effects/` en `mesa`)

| Efecto | Qué hace | pcx hoy | Destino |
|---|---|---|---|
| `etiqueta_rolling` | Nombre en marquesina alrededor del contorno, más un velo | 🟡 `halo` + `label` anclados, sin texto en curva ni velo | Alcanza para la demo; el velo sería `effects/velo.wgsl` |
| `mensaje_mesa` | Cartel que se escribe solo, con flecha a un objeto y `rot 180` | 🟡 `panel` + `arrow`, sin rotación del texto | Orientar `table_mm` hacia el público |
| `seguimiento` | Aplica las poses y funde la entrada | ✅ anclas; ❌ fundido | `renderer` |
| `mano_esqueleto` | 21 puntos proyectados sobre la mano | ❌ el renderer no recibe manos | Manos como uniform en `effects_prelude.wgsl` |
| `cabritas`, `cucharas_tesla` | Partículas y rayos que reaccionan a la mano | ❌ por lo mismo | No portar: no aparecen en las 3 escenas |
| `mantel_disco`, `taza_buk` | Sacan la grilla y las letras de la apariencia del objeto | ❌ `appearance` se publica y nadie la usa | No portar |
| `arduino_red` | Cables con pulsos hacia cada objeto y panel de pasos | 🟡 `arrow` + `panel` + `run_flow` | `effects/cable.wgsl` si sobra tiempo |
| `credencial_linkedin` | Tarjetas que rotan junto al objeto | ✅ `panel` anclado y `run_flow` con `after_s` | — |
| `marca_equipo` | Tarjetas que rebotan con «esta mesa usa cámaras» | 🟡 `label` fijo | El aviso de cámaras es obligatorio |

## 3. Las 3 escenas como modos de pcx

Un **modo** sería una carpeta `modes/<nombre>/` con `flow.json` (lo ejecuta `run_flow`), `catalog.json` (objetos pre-escaneados), `mode.toml` (workers y cámaras) y `prompt.md` (instrucciones del agente). Un worker puede avisar algo propio sin tocar el contrato: manda `emit{event, data}` por la ingesta y los flujos transicionan con `flow_emitted`.

| Escena | Ya existe | Falta | Modo |
|---|---|---|---|
| **1 · Vibecoders Desk** | `ask` anclado con permanencia; `project` y `events_wait` por MCP (prototipo probado con Claude Code y Codex; el gateway remoto sigue sin desplegar); `panel` y `label` | Fuente de actividad de los agentes; catálogo (teclado, mouse, libreta, juguete); lector de libreta; ubicación automática de paneles | `desk`. Flujo: reposo ⇄ decisión (`ask` SÍ/NO anclado al mouse). Widgets: panel de agentes junto al teclado y panel de decisiones bajo la libreta. Workers: objetos, manos, `libreta`. Prompt: informa con `project`, decide con `ask`, escucha `libreta_texto` |
| **2 · Edu (sistema solar)** | `halo` y `label` anclados; `touch_down` trae `object_id`; flujos; `image` | Catálogo de 8 esferas parecidas (distinguir por color y tamaño); paralaje (son altas); shader de universo; voz; imagen del personaje | `edu`. Flujo: un estado por planeta pedido; tocar el correcto avanza y tocar otro responde «ese es Marte». Widgets: `effect universo` de fondo, halos de órbita, personaje. Workers: objetos, manos, voz. Prompt: arma el quiz desde el catálogo |
| **3 · Lab** | `contracts/fixtures/flow_guia_3_pasos.json` ya es esta escena; halo, flecha, panel y botón | Cámara lateral cableada; worker de nivel; objetos transparentes y delgados, difíciles para N0 y SAM | `lab`. Flujo: 5 pasos verificados por `flow_emitted{event: nivel}` o por botón, con estado de error «te pasaste». Workers: objetos, manos, `nivel`. Prompt: explica cada paso a un novato. Usar la gradilla naranja impresa como objeto y ubicar los tubos con `offset_mm` |

## 4. Plan de port priorizado

**Presupuesto para menos de 1 s.** Desde `hands_gone`: el negro llega a la cámara en 215–260 ms, más 2–3 cuadros (70–100 ms), rectificar (≤ 10 ms), N0–N2 (123 ms) y el diff al renderer (~20 ms). Son ~0,5 s hasta el evento y ~0,7 s hasta la luz. Sumando la calma, ~1,1 s desde que se suelta la mano con 0,4 s y ~0,95 s con 0,25 s. Meta medible: `hands_gone` → `object_moved` en menos de 600 ms.

Cada tarea es de 30–45 min para un agente. «Cableado» es el frente en curso (captura nativa, worker de manos, `pcx up`).

**R · Re-segmentar en menos de 1 s**

| # | Tarea | Listo cuando | Depende de |
|---|---|---|---|
| R1 | Foto limpia en `pcx-perception/src/clean.rs`: manda `blank{ms: 300}`, espera `blanked`, toma del anillo los cuadros oscuros (caída de luminancia en el área proyectada; tiempos como respaldo), usa la mediana de los interiores y sale apenas tiene 3 | Con cuadros sintéticos y latencia variable de 100 a 400 ms siempre elige cuadros del apagón; de `blank` a foto en ≤ 350 ms a 30 fps | — |
| R2 | Worker de objetos (binario `pcx-perception`, activado en `node/pcx.toml`): `hands_gone` → R1 → `Rectifier` → `Tracker::update` → `object_*` por la ingesta; revisión cada 45 s; pausa mientras se calibra | Con `replay:` y el core real, mover un objeto produce `object_moved` y el halo anclado cambia en `/state` | R1, cableado |
| R3 | Telemetría por etapa en JSONL (calma, blank → foto, rectificar, N0, N1, N2, publicar) y `calm_s` configurable (0,25) | p50 de `hands_gone` → `object_moved` bajo 600 ms en replay; tabla en `docs/pruebas/` | R2 |
| R4 | N2b barato antes de DINO, en `pcx-objects/src/tracker.rs`: zona cambiada sin dueño + histograma HSV + razón de área → máscara nueva (como v1 y v2) | Los casos «arrugado» y «girado sin textura» se resuelven en N2b en menos de 30 ms; los 19 tests siguen verdes | — |
| R5 | `Rectifier::from_poly(Poly2)` con tabla de remapeo precalculada, en `pcx-objects/src/geom.rs` | Con lente simulada (k1 = −0,08) el residuo baja de 0,3 px; rectificar 1280×720 toma menos de 10 ms | — |
| R6 | Solo si R1 falla en hardware: marcador blanco durante el apagón (`blank.marker_px`: contratos v2.2 → core → renderer) | Golden del renderer; R1 usa el marcador cuando viene | R1 |

**S · Setup de ~3 s con catálogo pre-escaneado**

| # | Tarea | Listo cuando | Depende de |
|---|---|---|---|
| S1 | Catálogo v2: migración de `catalog` (suma `polygon_mm`, `height_mm`, `hist`, `color`, `mode`), `Tracker::{export,import}_catalog` y `pcx catalog save\|load` | Ida y vuelta en un test; el archivo trae N objetos con sus recortes | — |
| S2 | `Tracker::locate_from_catalog(frame)`: con una foto limpia, por objeto prueba N1 en su última pose, N2 en toda la imagen y N2b. Sin LLM | 6 objetos barajados se ubican en menos de 500 ms; el que falta queda oculto | S1, R4 |
| S3 | Arranque rápido `pcx up --mode <m>`: restaura la calibración de SQLite, corre `verify` (0,4 s), hace `quick` solo si hay deriva y después S2. Imprime «listo en X s» | En replay y con los procesos calientes, todos los `object_appeared` llegan en ≤ 3 s | S2, R2 |
| S4 | `pcx calibrate quick\|precise\|verify` real (hoy el core solo encola): renderer + anillo → `set_transform` + lienzo. Parte de `tools/demo-techo/src/calib.rs`, que ya calibró en hardware | En la mesa: precise en ≤ 6 s con residuo ≤ 1 px; verify detecta que se movió el proyector | cableado, hardware |
| S5 | Pre-escaneo de la utilería en la mañana: `register_all` con Claude real, renombrar o fusionar a mano y guardar un catálogo por escena; medir segundos y tokens | `modes/*/catalog.json` de las 3 escenas; registro en ≤ 25 s | R2, S1, hardware, API key |

**C · Multicámara (cenital + lateral)**

| # | Tarea | Listo cuando | Depende de |
|---|---|---|---|
| C1 | Segunda cámara: `[process.capture-lateral]`, abrir por uniqueID y `FrameSource` del core sobre N anillos | Con dos fuentes de replay, `look(camera_id)` devuelve imágenes distintas y `devices` lista 2 cámaras | cableado |
| C2 | Worker `nivel`: zona por tubo en la lateral, umbral HSV del colorante, fracción → ml con 2 puntos, histéresis; manda `emit{event: "nivel", data: {tubo, ml, banda}}` | 5 imágenes de prueba dan la banda correcta; un flujo transiciona con `data.banda` | C1 |
| C3 | Worker `libreta`: al cerrar un episodio de manos sobre la libreta, recorte limpio → Claude con visión → `emit{event: "libreta_texto"}` | Test con mock; con API key, una página manuscrita se lee en menos de 5 s | C1, R1 |

**M · Modos de las 3 escenas**

| # | Tarea | Listo cuando | Depende de |
|---|---|---|---|
| M1 | Formato y cargador: `pcx mode <n>` carga el catálogo, detiene el flujo anterior y lanza `run_flow` | `pcx mode lab` deja flujo y widgets en `/state`; cambiar de modo limpia los anteriores | — |
| M2 | Modo `lab`: 5 pasos, verificación y error | Un test e2e con eventos simulados recorre todos los estados | M1 (C2 para lo real) |
| M3 | Modo `edu` + `effects/universo.wgsl` | `/debug/touch` sobre el planeta correcto y uno incorrecto mueve el flujo; golden del shader | M1 |
| M4 | Modo `desk` + hook `pcx-notify` para Claude Code | Agente falso → `ask` → `/debug/touch` → la respuesta llega al agente en menos de 1 s | M1 |
| M5 | `free_rect` en `pcx-core::geom` (port de `mesa_libre.py`): un panel o `ask` con ancla y sin `rect_mm` cae en el hueco libre más cercano | Tests; un `ask` junto a un objeto rodeado no tapa nada | — |
| M6 | Voz (`pcx-voice`): `flow_emitted{event: "say"}` → `say -v Paulina`; gesto 🤘 de 1 s → graba → transcribe → `emit{event: "voz_texto"}` | Test del clasificador con 21 puntos; `say` suena en macOS | cableado |
| M7 | Kit con paleta: al aparecer un objeto, halo y etiqueta con su color (opción `kit` del modo) | 10 objetos reciben 10 colores, estables entre reinicios | M1 |
| M8 | Anfitrión: `modes/anfitrion/prompt.md` (port de `ANFITRION.md` a las tools del MCP) y bucle `claude -p` | 3 ciclos seguidos sin intervención, con el diario en `history` | C1, M6 |

**Orden.** Sin hardware y en paralelo desde ya: R1, R4, R5, S1, M1 y M5. Después R2 → R3 y S2 → S3. Con hardware en la mañana: S4 y S5. Luego C1 → C2 y M2 → M3 → M4. M6–M8 solo si sobra tiempo.

## 5. Riesgos y decisiones

- **Corte de las 16:00.** Si a esa hora R2 y S3 no corren en la mesa real, se presenta con `mesa` y pcx se muestra como arquitectura. La rama `mesa` no se toca: es el respaldo.
- **Paralaje sin resolver en ambos stacks.** Afecta a Edu (esferas) y a Lab (tubos). Mitigación para la demo: objetos altos cerca del centro, halos con 20 mm de margen y etiquetas al lado. La corrección real es la altura por objeto (`height_mm` de S1 más la propuesta del README de `pcx-objects`), después del bloque S.
- **No depender de N3 en la demo.** DINO en Rust pesa 719 MB (el disco está justo) y no tiene cifras. Catálogo cerrado + N2b cubre las 3 escenas.
- **API key de Anthropic.** Sin ella no hay registro (S5) ni lector de libreta (C3). Sigue pendiente.
- **Toque.** Con una sola cámara cenital no se mide contacto: todos los modos usan permanencia (`dwell`) o `touch_down` sobre un objeto.
- **Objetos transparentes** (pipeta, tubos de vidrio): N0 y SAM los ven mal. Usar colorante y la gradilla impresa como referencia fija.
