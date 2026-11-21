# Dolores candidatos — borrador para Pablo y Matilda

21-nov-2026. Base revisada: `c32a31b`, seis conversaciones del 20-nov, hasta la de 23:46, y `docs/investigacion/`. No hay elección del equipo ni validación física registrada en este documento. Actualizar al recibir nuevas transcripciones del orquestador; el resumen de decisiones compartidas sigue en `../docs/PLAN.md`.

## Qué estamos construyendo y qué falta elegir

El producto es un sistema que integra percepción del espacio, calibración, proyección e interacción, con una capa de agentes que puede controlarlo y generar comportamientos. Una guía de armado, un escritorio o una experiencia creativa son aplicaciones posibles del sistema.

Elegir una tarea para la demo permite mostrar por qué sirve esa infraestructura. No implica decidir todavía que el producto será exclusivamente industrial, educativo o de escritorio.

## Qué dicen las conversaciones

| Conversación | Qué aporta | Cómo interpretarla |
|---|---|---|
| [17:55, antes del evento](../docs/transcripciones/2026-11-20-1755-equipo-antes-del-evento.md) | Motivación de tocar la mesa para grabar o controlar; Superhuman Desk y origami como ejemplos | Motivación interna y posibilidades; no validación de demanda externa |
| [22:20, idea](../docs/transcripciones/2026-11-20-2220-reunion-equipo-idea.md) | Engine general y experiencias encima; escritorio, guía sobre objetos, handheld; separación entre percepción y tracking | Dirección técnica y de producto propuesta; no prueba del montaje actual |
| [23:08, mentor](../docs/transcripciones/2026-11-20-2308-mentor.md) | Falta un dolor concreto, una conclusión útil a partir de lo observado y foco; objeción de brillo | Criterios para elegir la experiencia; el mentor no eligió un mercado |
| [23:34, rincón](../docs/transcripciones/2026-11-20-2334-rincon-oscuro.md) | Mesa larga en zona oscura, con posibilidad de que pasen personas e interactúen | Condición propuesta para ensayar |
| [23:39, reparto](../docs/transcripciones/2026-11-20-2339-reparto.md) | Primera prioridad: montar y lograr la primera proyección; Pablo y Matilda con Max | Dependencia inmediata antes de prometer capacidades |
| [23:46, ideas](../docs/transcripciones/2026-11-20-2346-ideas-domotica-observador.md) | Domótica y observación como extensiones | Ideas adicionales; no se registra que sustituyan las prioridades anteriores |

Las transcripciones son automáticas: no atribuir frases a personas basándose solo en los números de hablante.

## Cuatro candidatos

### A. Persona que arma por primera vez un kit

**Dolor:** «Veo el siguiente paso en el manual, pero no sé qué pieza de esta mesa corresponde ni dónde va; necesito que alguien me corrija».

- **Usuario y situación:** persona novata ante un kit de piezas distinguibles; tarea corta de selección y colocación. Comprador posible: responsable de taller o capacitación, por validar.
- **Qué hace hoy:** alterna entre instrucciones, piezas y una persona que le ayuda.
- **Costo a medir:** dudas, selecciones equivocadas, retrabajo y minutos de asistencia. No tenemos un costo monetario propio. Estimación futura: minutos de asistencia × costo por minuto + retrabajo observado.
- **Experiencia propuesta:** el sistema señala una pieza y su destino; observa la colocación y responde. La explicación puede venir del agente; la regla de acierto debe ser comprobable.
- **Por qué proyección:** vincula una indicación a una pieza o posición física concreta. Si un papel con flechas resuelve igual, el beneficio adicional no está demostrado.
- **Evidencia:** un estudio comparó instrucciones proyectadas y pictóricas con 15 trabajadores con discapacidad cognitiva en un taller protegido. Apoya estudiar ese contexto, sin demostrar resultados para nuestros usuarios o sistema. [Funk et al., ASSETS 2015](https://www.medien.ifi.lmu.de/pubdb/publications/pub/funk2015usinginsitu/funk2015usinginsitu.pdf).
- **Prueba de mesa:** cuatro pasos con piezas grandes y colores diferentes; introducir una selección equivocada y observar si la detecta antes de avanzar. Verificar pieza y zona, sin equiparar eso a verificar un ensamble completo.
- **Dependencia crítica:** detección fiable de esos estados en la iluminación real. Si el avance es manual, presentarlo como guía proyectada con avance manual.

### B. Instructor de un taller práctico

**Dolor:** «Repito a cada persona dónde actuar y pierdo tiempo señalando mientras las demás esperan».

- **Usuario y situación:** instructor que enseña una secuencia sobre un objeto de práctica. Comprador posible: institución o responsable del taller, sin validar.
- **Qué hace hoy:** demuestra, apunta con la mano, usa fotos o vuelve a explicar a cada mesa.
- **Costo a medir:** repeticiones, tiempo de asistencia por alumno y esperas. Sin cifra propia; no inferir mejora del aprendizaje a partir de una demo.
- **Experiencia propuesta:** el instructor define una secuencia; el sistema ilumina el lugar correspondiente y permite repetir o avanzar.
- **Por qué proyección:** conserva una referencia espacial compartida entre quien enseña y quien ejecuta.
- **Evidencia:** aparece como posibilidad en las conversaciones; no hay entrevista a un instructor ni observación de una clase. El estudio del candidato A no valida automáticamente este segmento.
- **Prueba de mesa:** una persona explica cuatro pasos y otra intenta seguirlos; registrar cuándo necesita señalar de nuevo.
- **Dependencia crítica:** contenido correcto y referencias espaciales claras. La cámara puede apoyar el seguimiento, pero no dar por hecha la evaluación automática del aprendizaje.

### C. Creador que quiere prototipar una interacción física

**Dolor:** «Quiero que tocar o mover este objeto produzca una respuesta aquí mismo, pero tengo que conectar geometría, detección, eventos y visuales antes de probar la idea».

- **Usuario y situación:** creador de instalaciones o prototipos; quiere pasar de un comportamiento imaginado a uno que otra persona pueda usar. Usuario y comprador podrían coincidir; por validar.
- **Qué hace hoy:** combina herramientas de mapping, visión y programación, y prepara contenido para una escena. Es una hipótesis de flujo apoyada en el relato del equipo; observar una sesión real para precisar el cuello de botella.
- **Costo a medir:** minutos hasta la primera interacción y hasta una modificación del comportamiento; intervenciones técnicas necesarias. Sin monto ni ahorro observado.
- **Experiencia propuesta:** en una escena calibrada pide «al tocar esta pieza, ilumina aquella»; el sistema enlaza detección, evento y respuesta. Después cambia la regla usando la misma escena.
- **Por qué proyección:** la salida y la acción pertenecen a los objetos físicos que forman la experiencia.
- **Evidencia:** experiencia interna de mapping y deseo de facilitar interacciones en las conversaciones de 22:20 y 23:08. Falta validar con un creador externo.
- **Prueba de mesa:** dos comportamientos distintos sobre la misma escena, registrando preparación, archivos editados y ayuda del equipo. Declarar cualquier efecto preexistente que se reutilice.
- **Dependencia crítica:** demostrar creación o modificación de la interacción, además de encender un efecto existente. «Mapping más fácil» por sí solo no diferencia esta propuesta.

### D. Persona que trabaja con las manos y necesita un control auxiliar

**Dolor:** «Para ejecutar una acción breve tengo que interrumpir la tarea, buscar una ventana y volver a lo que estaba haciendo».

- **Usuario y situación:** alguien realizando una tarea manual que necesita un temporizador, una nota o grabar audio. El usuario concreto sigue por identificar.
- **Qué hace hoy:** usa teléfono, teclado, pantalla o un control físico.
- **Costo a medir:** interrupciones y tiempo para ejecutar una acción concreta; sin ahorro demostrado ni comprador identificado.
- **Experiencia propuesta:** un control grande junto a la tarea, asociado a una acción real y con confirmación visible.
- **Por qué proyección:** hipótesis de colocar el control donde se necesita; compararlo con un botón físico, un atajo o el teléfono.
- **Evidencia:** motivación interna en 17:55. Desktopography ya investigó interfaces proyectadas que conviven con objetos sobre un escritorio; esa convivencia no sería novedad propia. [Página de los autores](https://chrisharrison.net/index.php/Research/Desktopography).
- **Prueba de mesa:** una sola acción durante una tarea manual, comparada con su alternativa más simple.
- **Dependencia crítica:** que la colocación ayude y el control responda con fiabilidad. Un panel con muchos widgets no demuestra beneficio.

## Cómo elegir sin cerrar prematuramente el sistema

| Candidato | Claridad del dolor | Qué permitiría demostrar | Duda principal |
|---|---|---|---|
| A. Kit | Tarea y error fáciles de observar | Percepción → decisión → guía → respuesta | ¿Podemos verificar el estado real? |
| B. Instructor | Requiere observar a un instructor | Referencias físicas compartidas y secuencia reutilizable | ¿Ahorra asistencia y conserva claridad? |
| C. Creador | Claro para un público creador; requiere explicar el trabajo previo | Sistema reutilizable y modificación de comportamientos | ¿Simplifica la creación frente al flujo actual? |
| D. Control auxiliar | Depende mucho de la tarea elegida | Interacción breve sobre el espacio de trabajo | ¿Mejora un botón, un atajo o un teléfono? |

**Propuesta para contrastar con el equipo:** ensayar A y C en la misma escena antes de elegir. A permite comprobar una consecuencia para quien realiza una tarea; C permite comprobar el valor del sistema para quien construye experiencias. Si solo funciona uno, usar ese resultado para decidir. La recomendación previa de investigación favorece A; todavía no es una decisión del equipo.

## Arte previo y afirmaciones que podemos sostener

- Lightform ya ofrecía escaneo con luz estructurada y herramientas para aplicar contenido a la escena. La facilidad de mapping tiene antecedentes. [Lightform Creator](https://lightform.com/creator).
- Guiar y verificar trabajo físico mediante proyección también tiene antecedentes comerciales: [LightGuide](https://www.lightguidesys.com/) y [Arkite](https://arkite.com/). Por eso el candidato A no basta como argumento de originalidad.
- Arkite describe generación de borradores de trabajo mediante IA. Añadir IA al contenido tampoco establece por sí solo una diferencia. [Arkite AI](https://arkite.com/platform/ai/).
- La diferenciación por comprobar sería reducir la preparación necesaria para crear y adaptar interacciones sobre una escena observada, con una infraestructura reutilizable. Comparar qué configuramos a mano y qué resuelve el sistema; no afirmar que nadie lo hace.
- El presupuesto total debe incluir computador, cámara, proyector y montaje. El precio mencionado de un proyector no es el costo verificado del sistema.

## Validación breve antes del guion y la marca

1. **Montaje:** lograr una indicación legible y alineada sobre una zona pequeña. Registrar superficie, iluminación, equipos y qué puede moverse sin perder el ajuste. A3 es un punto de partida sugerido, no una capacidad medida.
2. **Dolor:** pedir a tres personas pertinentes que cuenten la última vez que hicieron esa tarea, cómo la resolvieron y dónde necesitaron ayuda. No basta preguntar si les gusta la idea.
3. **Ensayo A:** cuatro pasos, registrando selecciones, errores no detectados, ayudas y tiempo. Definir antes qué cuenta como error. Si el error es actuado, etiquetarlo como demostración del comportamiento.
4. **Ensayo C:** cambiar una regla en la misma escena, registrando tiempo, preparación y ayuda. Comprobar qué se reutiliza y qué se genera.
5. **Elección:** Pablo, Matilda y el equipo acuerdan usuario, tarea y evidencia que mostrarán. Con eso se escribe `guion.md` y después `marca.md`.

Una comparación pequeña durante la hackathon sirve para aprender y describir lo observado. No permite anunciar porcentajes generales de mejora. Un respaldo grabado o preconfigurado debe identificarse como tal.

## Límites del borrador técnico

El [README del engine](../engine/README.md) diferencia demo sintética, escena fija y reconocimiento que requiere modelos adicionales; no certifica tracking al mover equipos. No hemos ejecutado ni validado aquí el montaje físico.

El [contrato vigente](../docs/CONTRATO.md) usa `/api/state`, `/api/command` y `/api/events`. Las rutas `/layers`, `/scene` o `/mode` que aparecen en el borrador anterior de momentos de demo no son el contrato vigente y requieren traducción por el equipo técnico.

Este documento no da por implementada la verificación de pasos, el reconocimiento de objetos arbitrarios ni la adaptación automática al moverlos. Cada promesa del guion debe corresponder a un comportamiento comprobado o quedar presentada como siguiente paso.
