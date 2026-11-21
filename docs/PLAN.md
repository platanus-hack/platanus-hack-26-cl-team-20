# Platanus Hack 26 — plan vivo

Estado (sáb 00:15): track **Physical World**. Proyector + cámara que entiende el espacio y lo vuelve interactivo, controlado por agentes. El engine de Max ya está en `engine/`. Falta elegir el dolor y la historia de la demo.

## El proyecto
Un **engine** que, con hardware barato (proyector de ~50 lucas, webcam o teléfono), mapea una superficie, sabe hacia dónde proyecta y qué objetos hay, y proyecta interfaces interactivas encima. «Point and shoot»: sin dibujar el espacio a mano.

Casos de uso, de más a menos seguro:
1. **Smart desk** (demo principal): widgets proyectados sobre el escritorio alrededor del teclado (notas, sliders, torpedo de reunión, estado de agentes), que se tocan con la mano. Dinámico: el público pasa objetos de su mochila y el sistema los reconoce y les pone un widget.
2. **Guía sobre objetos reales:** «explícame cómo cambiar esta pieza» y la luz apunta la pieza y el movimiento. Manuales industriales, equipos médicos, educación.
3. **Proyector handheld** (lo más difícil, opcional): escanear un lugar y que el proyector sepa a dónde apunta al moverlo. Servo para autofoco.

Nombre de trabajo de la demo: **Superhuman Desk**, una mesa de trabajo épica con «modos» que cambian todo el escritorio (ejemplos conversados: modo stocks/tracker, tutorial de origami con los cortes proyectados sobre el papel, pizarra). Mismo gesto que el «haz un panel hacker» de Herdr: se le pide un modo y la mesa se arma sola.

Motivación de Max: que los agentes salgan de la pantalla; tocar la mesa para grabar audio o hacer scroll al lado del mouse. Pensado para que cualquiera lo arme en casa (proyector de 50 lucas + teléfono Android): smart desk, smart wall.

Técnica según Max: la IA (segmentación + un modelo de visión que nombra y da bounding box) se usa para prearmar la escena; el tracking y el mapeo en vivo son deterministas, por latencia. Más cámaras = más precisión (por ejemplo una frontal para detectar el toque). Max ya armó un sistema de posicionamiento visual que triangula varias cámaras (iPad con LiDAR, Quest, webcams) en un mismo espacio 3D; sobre eso se proyecta un canvas, se segmenta y se trackea, y un modelo de visión dice qué es cada objeto y qué efecto ponerle.

**Hardware disponible:** dos proyectores, ~10 cámaras, teléfonos, dashcam (cámara delantera y trasera con micrófono; solo graba), ESP32 con pantalla táctil de 7", servo para foco, badge Plataboy, impresoras 3D de la sede.

## Feedback del mentor (vie 23:08) y decisión pendiente
Objeciones:
- **No hay un dolor concreto.** «Qué me va a mostrar que me cambie algo». Mucha tecnología wow que al usarla no resuelve nada.
- **Demasiado amplio.** Tip que le pidieron transmitir: los proyectos que ganan hacen una cosa muy bien; el generalista abarca poco. El ganador del año pasado atacaba un dolor muy concreto.
- **Límite físico:** un proyector barato con luz ambiente ilumina poco.
- Falta la capa de lógica: qué conclusiones saca el sistema de lo que ve y qué hace con eso.

Postura del equipo en la conversación: ambición pesa 20 % y el factor wow manda (Max); meta de hoy = engine andando con unos 4 casos y mañana elegir los reales; alguien propuso probar tres casos tres horas cada uno. División conversada: (1) engine y (2) dashboard o herramientas para que una persona controle lo que se proyecta; Max propuso a Matilda y Pablo para buscar los casos de uso. Propuesta de valor dicha al mentor: «projection mapping dinámico», democratizar el mapping que hoy exige calibrar a mano en el lugar. Dolores que salieron: educación (pupitre, textos escolares, instituto técnico con maquinaria), AR sin lentes y compartida, agentes que necesitan salir al mundo físico, salas de reuniones, discapacidad visual, retail, mascota-agente.

**Decisión pendiente del equipo:** un dolor y un usuario para la historia de la demo. Recomendación del orquestador: mantener el engine, contar UNA historia —guía de armado/reparación proyectada sobre el objeto real («las instrucciones viven lejos de las manos»)— con una prueba medible en vivo, y dejar el smart desk y los demás casos como cierre de 30 segundos («y el mismo engine hace esto»).

## Rúbrica del jurado (0–5 por dimensión)
| Dimensión | Peso | Cómo lo ganamos |
|---|---|---|
| Aspecto técnico | 25 % | Calibración proyector-cámara y tracking funcionando en vivo |
| Ambición | 20 % | Engine general, no un solo truco: 2 casos de uso en la misma demo |
| Ejecución | 20 % | Smart desk estable; respaldo con escenas prehechas |
| Impacto | 20 % | Caso industrial/manuales con hardware de 50 lucas |
| Originalidad | 15 % | Objetos del público reconocidos en vivo |

## Reparto (actualizado vie 23:39)
| Frente | Quién | Qué entrega |
|---|---|---|
| Montaje físico y primera demo: colgar proyector y cámara, cables, proyección inicial | Max con Matilda y Pablo | **Lo primero de todo:** una primera demo proyectando en el rincón. «Teniendo eso cambia todo» |
| Engine funcional con lo que Max ya tiene, y puente para que agentes lo controlen | Daniel y Manu | Engine corriendo en el repo, y un agente moviendo una capa (`docs/CONTRATO.md`) |
| Casos de uso, pitch, nombre comercial | Pablo y Matilda (después del montaje) | Un dolor y un usuario para la historia; guion de 5 min |
| Rig y piezas 3D | agentes de 3D | Soporte de proyector y cámara cuando haya medidas |

**Puesto fijo:** la sede tiene un rincón oscuro con una mesa larga. Idea: dejar ahí un computador con cámara y proyector montados todo el fin de semana; se trabaja al lado y la gente que pasa puede mover cosas e interactuar. Resuelve el problema de brillo que marcó el mentor y sirve de prueba con público real antes del pitch.

**Engine de Max (vie 23:59):** `github.com/MAXMARDONES/sl-scanner`, snapshot `b8fecff`. Trae calibración cámara-proyector (gray code y autocalib), playground de efectos con recarga en caliente, segmentación y visión, manos y personas, app del Quest, y **ya tiene API HTTP y servidor MCP** para que un agente prenda efectos sobre objetos de la escena.
- **Original**, sin tocar: corre en el MacBook de Daniel con el proyector. Lo instala el agente `max-engine` (tab «Max»), donde Max puede jugar.
- **Copia del equipo:** `engine/` del repo `satelerd/platanus-hack-26` (subtree del mismo commit). Ahí Manu ordena y extiende. Contrato engine↔agente actualizado en `docs/CONTRATO.md`.
- Invitados al repo del equipo: Manu (`MBlancoC`), Max (`MAXMARDONES`), Pablo (`salvatierrapm`). Falta Matilda.

Repo privado del equipo: `github.com/satelerd/platanus-hack-26` (local: `~/code/platanus-hack-26/repo/`). Carpetas `engine/ agent/ experiences/ hardware/ docs/`.

Créditos disponibles según Daniel: Vercel, Render, Anthropic. Anthropic se canjea en `hack.platan.us/26-cl/hacker/anthropic` (Matilda, sáb 00:36). Montos sin verificar.

Seguimiento de uso de LLM («token maxxing») del evento: por persona en `hack.platan.us/26-cl/hacker/llm`, general en `hack.platan.us/26-cl/llm` (Matilda, sáb 02:58). Cada uno se conecta con su sesión.

## Demo y pitch
- **La demo de 5 minutos es lo que más pesa:** nadie revisa el código; se busca factor wow en cada paso. La categoría importa menos que la presentación.
- Scope ambicioso con «room to grow»: si algo no queda perfecto (por ejemplo el tracking), igual se muestra.
- Idea de Daniel que sigue viva: que el público pueda usar algo después (QR). Ver cómo calza sin desviar del núcleo.
- 5 minutos. La sala se ve mal desde atrás: una persona en la mesa y otra grabando de cerca, y eso va a la pantalla grande.
- Arco: partir simple (proyector barato) → ir sumando capacidades → cerrar con objetos del público.
- Lo que no alcance a correr en tiempo real se muestra con escenas preparadas.
- Sáb 20:00: charla «cómo hacer un buen demo».

## Horarios
| Cuándo | Qué |
|---|---|
| Vie 23:00 | Inicio del hacking |
| Vie ~00:00 | Meta de la noche: idea cerrada, primer prototipo andando y lista de desafíos. Después, a dormir a casa |
| Sáb mañana | Desayuno y día completo en la sede |
| Sáb 13:30 | Almuerzo — meta: proyección alineada |
| Sáb 20:00 | Charla de demo — meta: smart desk interactivo |
| Sáb noche | Segundo caso de uso, pitch, ensayo. Se decide ahí si se pasa de largo o se duerme |
| Dom 08:30–09:00 | **Fin del hacking** (otra fuente decía 09:30–10:15: verificar en dashboard) |
| Dom 09:30 | Presentaciones generales |
| Dom 12:00 | Presentaciones finales |
| Dom 14:30 | Premiación |

Sede: Roger de Flor 2725, Torre 3, piso 15 (MUT, Buk). Equipo: Daniel, Max Mardones, Matilda Roberts, Pablo Moraga, Manuel Blanco.

## Agentes en wJ (tope 3–4 vivos)
- Orquestador — wJ:p2
- lider-3d (Opus) — tab «3D», wJ:p3 — cases cerrados; en espera del proyector y la cámara para el rig.
- riemann (Opus) — tab «3D», wJ:p5 — juguete a control remoto del evento que se maneja desde el dashboard; gana quien más lo controle. Encargo en encargos/riemann.md. Bloqueado hasta tener el link y el navegador.
- investigador (Opus) — tab «Investigación», wJ:pB — arte previo, dolor, técnica, momentos de demo.
- max-engine (Opus) — tab «Max», wJ:pD — instala y corre el sl-scanner original en el MacBook con proyector y cámara; Max juega ahí.

## Riesgos y dudas abiertas
- **Código previo:** Max dice que el engine «ya está más o menos trabajado». No he visto las bases: hay que confirmar que se permite traer código propio previo.
- **Riemann:** falta el link exacto y saber qué cuenta como «controlar» y si se permite automatizar.
- **Dashboard** (`hack.platan.us/hacker`): Daniel autorizó entrar con su GitHub desde el mini, pero el Chrome del mini no responde a la extensión.
- Repo de entrega y dónde se despliega: sin verificar. Créditos según Daniel: Vercel, Render, Anthropic; Pablo compartió `hack.platan.us/26-cl/llm` (probable página de créditos de LLM).
- Quiénes son el jurado.

## Plataforma técnica de Manu: pcx (sáb 02:02)
Manu quedó a cargo de la plataforma. Leyó el engine de Max completo: lo más sólido es la calibración 2D; el 3D y el toque casi no se probaron con hardware. Hay dos versiones separadas en el engine (runtime de octubre con API/MCP y mesa Quest de noviembre).
- **pcx** («contexto físico para agentes»): nodo local (Mac + proyector + cámaras) con percepción, estado del mundo y renderer en Rust (~100 fps, ~5 ms); cerebro en Render con MCP remoto, agente y memoria. Cualquier agente se conecta por MCP con un comando.
- Prototipo medido: Claude Code y Codex ven la mesa, proyectan sobre objetos y esperan un toque.
- Rama `manu/plataforma`. Próximo: portar el seguidor v4, manos y calibración de la mesa de Daniel.
- **La rama `mesa` es la demo de respaldo** mientras pcx madura.
- Manu necesita: el caso de la demo (define los modos) y cuentas de Render y API key de Anthropic.

**Riesgo (orquestador):** son dos stacks en paralelo a ~30 h del cierre. Hace falta una hora de corte para decidir con cuál se presenta. Propuesta: sábado 16:00. Si pcx no corre la demo completa en la mesa a esa hora, se presenta con `mesa` y pcx se muestra como arquitectura.

## Cámaras «enchufar y listo» (idea de Daniel, sáb 00:20)
Agregar cámaras debería ser trivial: cada cámara nueva se ubica sola en el mundo 3D con el escaneo de luz estructurada, y el sistema sabe dónde está respecto del proyector. El celular de cualquiera debería poder sumarse como cámara, también como contexto para los agentes («mira, así está apuntando el proyector»).

Lo que ya trae el engine de Max:
- Calibración cámara-proyector por gray code y autocalib multivista (`engine/docs/P2-AUTOCALIB.md`, `--motion rigid --views 3`).
- Varias fuentes de cámara: Osmo por USB, Quest 3 por HTTP (`playground/quest_cam.py`, puerto 8787) y webcam remota servida por HTTP (`windows/camserve.py`, puerto 8794).
- No hay todavía: celular como cámara, ni registrar una cámara nueva «en caliente» sin repetir todo el escaneo.

Caminos para el celular, de más simple a más ambicioso:
1. iPhone como webcam del Mac por Continuity Camera: sin código; aparece como cámara más.
2. Página web desde el dashboard que use la cámara del celular y mande cuadros al Mac, imitando `camserve`. Sirve para contexto del agente y para cualquier teléfono.
3. Registrar esa cámara en el mundo 3D proyectando patrones mientras el celular mira: es el núcleo de «enchufar y listo» y un buen punto técnico para la rúbrica (25 %).

Frente: Manu y Daniel (puente agente ↔ engine), con Max para el paso 3.

## Ideas de Daniel para más adelante (vie 23:46, sin prioridad sobre engine y demo)
- **Domótica:** integrar el sistema con Apple HomeKit, Google Home, Nest y televisores, para que el agente también actúe sobre la casa y lo muestre.
- **Agente observador:** una cámara apuntando al equipo y otras en la hackathon, analizando audio y video de forma continua para decidir qué puede aportar o qué necesita la gente. Ojo: grabar a otros asistentes requiere su consentimiento y el de la organización.

## Ideas descartadas o en reserva
Agent Wrapped, panel de decisiones, Meet Proxy Maxing, Token Maxing. El «Superhuman Desk» de Daniel quedó absorbido por el smart desk.

## Próximos pasos
1. Equipo confirma roles (tabla de arriba).
2. Max comparte el repo o el estado del engine para saber qué existe.
3. 3D: medir proyector y cámara y diseñar el rig.
4. Resolver dashboard y bases (código previo, entregables, hora de corte).

### Update Manu sáb 03:23 (pcx)
Nodo local en Rust (cámaras, manos, objetos, calibración, renderer GPU) + cerebro en Render (MCP, memoria). 8 módulos, 112 tests verdes; seguidor v4 portado a Rust; LUT corrige paralaje en simulación. Hardware real: renderer 60 fps, latencia punta a punta 215 ms (original 313 ms). Próximo: `pcx up` y prueba completa calibrar → Claude proyecta por MCP → cámara verifica. **Sigue pidiendo el caso de demo.**

## Plan sábado 21 (cierre de la noche 04:01)

Fuente: `notas/2026-11-21-0401-cierre-noche.md`. Enviado al grupo a las 04:21.

🗺️ *Plan para el sábado* (cierre de la noche, 04:01)

*Casos de uso: 3 escenas de ~1 min cada una*
1. *Vibecoders Desk*: escritorio normal (teclado, mouse, libreta, un juguete). Aparece la actividad de tus agentes; si piden decidir sí o no, pones la mano al lado del mouse; en la libreta física escribes y abajo te proyecta las decisiones
2. *Edu*: maqueta del sistema solar de plumavit que se vuelve universo, con un personaje que le explica al niño («agarra Júpiter… no, ese es Marte»). Alternativas: anatomía (cráneo) o caligrafía sobre papel. Se decide mañana
3. *Lab*: pipeta, jeringa y tubos; la IA guía a alguien sin experiencia paso a paso, y una cámara lateral ve el nivel del líquido

*Prioridades técnicas*
1. 🔴 *Re-segmentar en menos de 1 s* cuando se mueve un objeto (lo más importante)
2. 🟠 *Setup de ~3 s*: dejar los objetos pre-escaneados en memoria para no escanear en la demo
3. 🟡 *Multi-cámara*: cenital + lateral (libreta y nivel del líquido)
4. 🟡 *Montaje físico* que se pueda llevar y armar en el escenario

📌 *Pendiente*
• Pitch: guion de video de 5 min, etapa por etapa (quedó para esta noche)
• Marca: ni Jarvis ni el morado típico de IA; idea fuerza «los agentes ya tienen ojos y manos, ahora les damos pinceles»
• Métricas de impacto dentro del relato (tiempo o plata ahorrada)
• Manu: plataforma pcx calibrando el techo (29 s, 0,8 px) y la landing en propuesta. *Necesita:* el caso de demo (↑ estas 3 escenas), el nombre del producto y saber si el proyector de Max es el T749
• Mesa de Daniel = demo de respaldo

🎒 *Qué traer mañana desde la casa*
• Trípode grande para el proyector (un papá tendría uno) o algo para colgarlo
• Mesa plegable liviana + mantel blanco
• Escritorio: teclado, mouse, libreta y un juguete
• Edu: bolas de plumavit (sistema solar) y/o un cráneo
• Lab: pipeta, jeringa, tubos de ensayo, vaso precipitado y colorante
• Extensiones, zapatillas y cables HDMI y USB largos
• Cámaras extra (webcam o celular) con su soporte

🖨️ *Impresión 3D (A2L, solo filamento naranjo)*
¿Qué imprimimos? Propuesta: soportes de cámara y proyector, gradilla para tubos, pipeta y jeringa de utilería, planetas o anillos de Saturno, y fichas o marcadores para la mesa. El naranjo puede ser parte de la marca. Cada uno tire ideas y las imprimimos mañana temprano

