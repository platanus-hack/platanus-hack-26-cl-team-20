# Justificación del problema: de lo general a lo particular

Estado: en revision.
Actualizado: 2026-11-21, 20:40 (PISA 2025, estudio de Harvard y aviso sobre Funk 2015).
Responsable: Matilda; asistente como apoyo.
Decisión pendiente: qué cifras y qué persona por escena entran al pitch.
Base: las 3 escenas del plan del sábado ([docs/PLAN.md](../../docs/PLAN.md), 04:21).
Fuentes consultadas el 21-nov-2026; las 7 cifras principales se comprobaron dos veces. ✅ = cifra vista en su fuente · ⚠️ = por confirmar.

## La idea en una frase

**La ayuda digital vive en la pantalla, pero el trabajo y el aprendizaje pasan en la mesa, con las manos.** Superhuman Desk lleva la ayuda de los agentes de IA al lugar exacto donde está el problema, y solo en el momento en que hace falta.

## El embudo común (de macro a micro)

| Nivel | Qué decimos | Respaldo |
|---|---|---|
| **1. El mundo** | Los agentes de IA pasaron de curiosidad a herramienta diaria en un año. | ✅ Uso de agentes en el trabajo: 31 % (Stack Overflow 2025) → 59 % (encuesta de abril 2026, 1.100 personas). |
| **2. El límite** | Pero siguen encerrados en la pantalla. Lo que no está en la pantalla, no lo ven: tu libreta, los tubos de un laboratorio, la maqueta de un niño. | Observación del equipo; es el centro del track Physical World. |
| **3. El costo** | Cuando la ayuda está lejos de las manos, hay que ir y volver entre el manual o la pantalla y la tarea. Ahí se pierde tiempo y se cometen errores. | ⚠️ Funk 2015 (guía proyectada sobre la pieza, 15 trabajadores de un taller protegido): el resumen y el cuerpo del estudio dan cifras distintas (lo marcó Manu el 21-nov) y es un estudio antiguo. No usar sus cifras en el pitch. ✅ Una revisión de estudios de realidad aumentada en manufactura halla menos errores (efecto mediano) que con instrucciones clásicas (tesis TU Delft 2021). |
| **4. La lógica que faltaba** | Ayudar todo el tiempo también molesta: a los expertos, la guía proyectada los distrae. La clave es que la ayuda aparezca **solo cuando la cámara ve que algo va mal**. | ✅ Kosch y Funk 2017: a los novatos les sirve al aprender y después estorba; a los expertos los distrae. Proponen ayuda que se active al detectar un error. |
| **5. Por qué con proyector** | Un proyector cuesta como unos audífonos, no como unos lentes de realidad virtual, y toda la sala ve lo mismo sin ponerse nada. | ⚠️ Minoproyectores en tiendas chilenas: $42–73 mil. Lentes: US$500–3.700. |

Esto responde a las dos críticas del mentor. **Falta un dolor concreto:** cada escena tiene una persona y un momento. **Falta la lógica:** el sistema concluye "esto va mal" y recién ahí actúa.

---

## Escena 1 · Escritorio de programador

**Mundo.** Quienes más usan agentes ya manejan varios a la vez. ✅ Un tercio usa varios agentes (17 % especializados + 16 % coordinados), y ese grupo los usa casi todos los días (77–85 %) (Stack Overflow, abril 2026).

**El dolor.** Ahora el programador ya no escribe todo el código: **supervisa**. Los agentes se detienen a pedirle permiso, y la persona termina aprobando casi todo sin mirar. ✅ Se aprueba el 93 % de los permisos que pide Claude Code. Anthropic llama a esto "fatiga de aprobación". La fuente es interesada, porque Anthropic vende el modo que evita esos permisos.

**El costo en productividad.** ✅ Un estudio de Harvard con 720.542 programadores de 655 empresas encontró que, al adoptar agentes, se hacen 36 % más commits, pero el tiempo desde que el código se manda a revisión hasta que se aprueba sube 56 %, y el trabajo terminado sube mucho menos. El cuello de botella pasó a ser la revisión humana (Chen y Stratton, working paper, versión del 19-nov-2026). Es la cifra que usa el guion v3.

**Persona (hipótesis).** Una desarrolladora de una startup en Santiago corre 3 o 4 agentes en ventanas distintas. Uno se queda esperando su "sí" mientras ella mira otra ventana, y ese agente puede pasar minutos detenido sin que nadie lo note. *(Validar entrevistando hoy a programadores del hackathon.)*

**El costo de interrumpirse.** ✅ Según Gloria Mark (UC Irvine), una tarea interrumpida se retoma en promedio a los 23 minutos. Ojo: es el tiempo hasta volver a esa tarea, no el tiempo para recuperar la concentración.

**Qué cambia en la mesa.** El escritorio muestra qué agente te espera, sin sacarte de lo que haces. Dices "sí" con la mano junto al mouse, y lo que piensas en tu libreta de papel se vuelve una decisión para el agente.

**Qué medir en vivo.** Los segundos que pasan desde que un agente pide permiso hasta que la persona responde, con la mesa y sin ella.

**Frase posible.** "El problema ya no es escribir código: es no perder de vista qué agente te está esperando."

**No decir.** Que "todos manejan varios agentes": la mayoría usa uno solo. Tampoco que "escribir a mano es mejor": las réplicas no lo confirman.

---

## Escena 2 · Laboratorio

**Mundo.** Trabajar con precisión en un laboratorio es difícil, incluso para gente con formación. ✅ En un estudio con 108 personas de laboratorio, entre el 10 % y el 22 % no alcanzó la exactitud que exige la norma al pipetear. Los estudiantes fueron los más irregulares (J Appl Lab Med 2026). ✅ Más del 70 % de 1.576 investigadores no pudo repetir experimentos de otros (Nature 2016). Las causas son varias, no solo el pipeteo.

**Chile.** ✅ La educación media técnico-profesional tiene 164.935 estudiantes: un tercio de la educación media, en 980 liceos y 35 especialidades (MINEDUC, 2023). La cifra incluye a adultos que completan sus estudios, así que conviene decir "más de 160 mil estudiantes" y no "jóvenes".

**Persona (hipótesis).** Una estudiante de un liceo técnico, en su primera práctica de laboratorio, con un profesor para todo el curso. Tiene el protocolo en papel y los tubos en la mano.

**El momento de dolor.** Mide mal el líquido o se salta un paso, y nadie lo nota hasta el final. Para entonces el resultado no sirve y hay que repetir todo.

**Qué cambia en la mesa.** La mesa le muestra el siguiente paso sobre el tubo que corresponde. La cámara lateral ve el nivel del líquido y la ataja **antes** de que siga con el error. Cuando lo hace bien, la ayuda se retira (ver Kosch 2017).

**Qué medir en vivo.** Cuántos errores ataja la cámara y cuánto tarda en avisar desde que el error ocurre.

**Frase posible.** "Más de 160 mil estudiantes se forman en la educación técnica en Chile. Nuestra mesa mira sus manos y sus tubos, y les ataja el error antes de que pase."

**No decir.** Que la tecnología siempre ayuda. ✅ En un laboratorio virtual con 40 estudiantes hubo **menos** conductas de seguridad que en el real (48,8 % frente a 68,6 %). Por eso la mesa trabaja sobre objetos reales y no los reemplaza.

---

## Escena 3 · Educación (sistema solar)

**Mundo.** Hay ideas del espacio que casi nadie entiende bien, aunque haya estudiado. ✅ La mayoría de los estudiantes de astronomía introductoria cree que el verano llega porque la Tierra está más cerca del Sol (Harvard, estudio de Sadler, 2.567 respuestas).

**Chile.** ✅ En PISA 2025 (publicado el 8-sep-2026), Chile obtuvo 442 puntos en ciencias, 2 menos que en 2022, un cambio que la Agencia de Calidad considera estadísticamente estable. ✅ Cerca del 37 % de los estudiantes de 15 años queda bajo el Nivel 2 en ciencias, el mínimo para seguir aprendiendo (Agencia de Calidad de la Educación). Ya no usar la cifra de 444 (PISA 2022). ✅ Además, se proyecta que en 2026 falten 26 mil profesores (Elige Educar, citado por prensa; la cifra no está separada por asignatura).

**Persona (hipótesis).** Un niño de 5° básico en un colegio con pocos recursos. Su profesora tiene un curso grande y no puede revisar la maqueta de cada uno.

**El momento de dolor.** El niño arma su maqueta de plumavit, pero la maqueta no le dice si se equivocó: puede poner Marte donde va Júpiter y nadie lo corrige a tiempo.

**Qué cambia en la mesa.** La maqueta reacciona: ve qué planeta toma el niño y le responde al instante ("ese es Marte"). La profesora gana un ayudante para cada mesa.

**Qué medir en vivo.** Cuántos intentos necesita un voluntario del público para ordenar los planetas, y cuánto tarda en corregir un error con la ayuda de la mesa.

**Frase posible.** "Hasta en la universidad la gente cree que el verano llega porque la Tierra está más cerca del Sol. En nuestra mesa, el niño mueve el planeta y ve al instante qué pasa."

**No decir.** Que la tecnología enseña mejor por sí sola. ✅ En un experimento con 172 universitarios sobre las fases de la Luna, la realidad virtual, la simulación en pantalla y la maqueta física enseñaron lo mismo, aunque el 78 % prefirió la realidad virtual. Nuestro argumento es otro: **corregir al instante a cada niño cuando la profesora no alcanza**, y hacerlo con algo barato.

---

## Qué falta para que el argumento quede sólido

1. **Testimonios reales hoy.** Preguntar a 3–5 programadores del hackathon cuántos agentes usan a la vez y cuántas veces uno quedó esperando su aprobación sin que se dieran cuenta. Es la evidencia más fácil de conseguir y la que más convence.
2. Si alguien del equipo conoce a una profesora o a un docente de un liceo técnico, pedirle una frase sobre su día a día.
3. Las cifras ⚠️ se confirman o se sacan del pitch. La de PISA ya se actualizó a PISA 2025 (21-nov, 20:40).

## Fuentes principales

- Stack Overflow Developer Survey 2025: https://survey.stackoverflow.co/2025/ai
- Stack Overflow, abril 2026: https://stackoverflow.blog/2026/05/27/agents-on-a-leash-agentic-ai-remains-mostly-monitored-at-work/
- Anthropic, auto mode: https://www.anthropic.com/engineering/claude-code-auto-mode
- Chen y Stratton, Harvard (working paper, versión 19-nov-2026): https://fion.ac/jellyfish.pdf
- Gloria Mark (Gallup): https://news.gallup.com/businessjournal/23146/too-many-interruptions-work.aspx
- METR 2025: https://arxiv.org/abs/2507.09089
- Funk, Mayer y Schmidt 2015: https://www.medien.ifi.lmu.de/pubdb/publications/pub/funk2015usinginsitu/funk2015usinginsitu.pdf
- Kosch, Funk et al. 2017: https://www.unibw.de/usable-security-and-privacy/publikationen/pdf/kosch2017ubicomp.pdf/@@download/file/kosch2017ubicomp.pdf
- Tesis TU Delft 2021: https://repository.tudelft.nl/record/uuid:b9943231-dec3-4637-a42a-5734bafacb8a
- Pipeteo, 108 personas: https://pure.amsterdamumc.nl/en/publications/pipetting-performance-across-laboratory-personnel-a-comparative-s/
- Nature 2016, reproducibilidad: https://www.nature.com/articles/533452a
- MINEDUC, educación TP: https://www.mineduc.cl/?p=38230
- Laboratorio virtual vs real: https://pmc.ncbi.nlm.nih.gov/articles/PMC8294259
- Agencia de Calidad, PISA 2025: https://www.agenciaeducacion.cl/caida-en-los-resultados-pisa-2025-ratifica-necesidad-de-poner-el-foco-en-el-logro-de-los-aprendizajes/
- Sadler, Harvard: https://www.gse.harvard.edu/ideas/news/99/04/research-finds-revolutionary-discoveries-cognitive-sciences-are-not-reflected-high
- Fases de la Luna, 172 estudiantes: https://ar5iv.labs.arxiv.org/html/1807.11179
- Déficit docente: https://www.cnnchile.com/pais/elige-educar-deficit-profesores/
