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

Créditos disponibles según Daniel: Vercel, Render, Anthropic. Montos y activación sin verificar.

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
