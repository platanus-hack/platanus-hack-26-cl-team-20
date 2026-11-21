# Superhuman Desk para la reunión con Matilda

**Dirección.** Un producto sobre la misma mesa, con tres modos preparados: Dev Mode, Lab y Educación. El usuario puede cambiar de modo durante la demo. Los tres se configuran antes del pitch. La creación de modos propios queda como dirección futura.

**Propósito.** Unir el trabajo digital con lo que tenemos físicamente delante. Darle cuerpo a la IA significa que puede percibir objetos y acciones de la persona y responder en ese mismo espacio. La libreta puede extenderse sobre la mesa. Una herramienta digital puede devolver una respuesta al lugar físico donde se necesita.

**Pitch.** Superhuman Desk permite usar herramientas digitales y agentes sobre las cosas reales con las que trabajas. Pones una libreta, un componente o una maqueta sobre la mesa. Lo que haces con ese objeto cambia la respuesta que ves ahí mismo.

## Los tres modos

| Modo | Qué se ve | Valor a comprobar |
|---|---|---|
| **Dev Mode** | Seleccionas un dibujo de la libreta. Aparece junto a la tarea del agente que lo está usando y puedes revisar un resultado vinculado al fragmento. | Pasar un insumo físico a un trabajo digital que puedes usar y revisar, conservando la referencia al original. Agenda, tickets y operaciones son otras posibilidades del modo. |
| **Lab** | Pones un módulo en la zona de trabajo. Si es el equivocado, la mesa señala qué falta y sostiene el paso. Si lo sustituyes por el correcto, avanza. | Detectar una selección equivocada antes de continuar. Un módulo Arduino o utilería impresa sirve para ensayar con objetos grandes. La escena comprueba una selección en la mesa. |
| **Educación** | Mueves una Luna física alrededor de la Tierra y la proyección actualiza qué fase vería una persona desde nuestro planeta. | Usar la manipulación de un modelo para hacer y comprobar una predicción. |

**El momento principal propuesto es Lab.** El jurado ve una acción, una respuesta y una corrección sin que haya que explicar una interfaz entera. Dev muestra el diálogo entre libreta y agentes. Educación muestra que la misma mesa sirve para explorar una relación física de otra manera.

## Guion de cinco minutos

| Tiempo | Acción en la mesa | Qué se tiene que entender |
|---|---|---|
| **0:00–0:25 · Apertura** | Se ve la libreta, la mesa y la primera capa proyectada. | Los agentes pueden responder sobre los objetos con los que estás trabajando. |
| **0:25–1:30 · Dev** | Seleccionas un boceto real. Aparece en la proyección junto a una tarea y se muestra su resultado asociado. | Un dibujo en papel se convierte en un insumo que una herramienta digital puede usar. |
| **1:30–2:55 · Lab** | Colocas el módulo equivocado, lo cambias y la indicación avanza tras detectar el correcto. | La guía responde al estado de la mesa. |
| **2:55–3:50 · Educación** | Desplazas la Luna y comparas la fase proyectada en dos posiciones. | Mover un objeto permite comprobar una predicción. |
| **3:50–4:35 · Cierre** | Se ven los tres modos de Superhuman Desk. | Es la misma mesa. Cambian los objetos y la tarea. La mesa percibe lo que haces y responde en tu espacio. |
| **4:35–5:00 · Margen** | Tiempo para transiciones o recuperar un gesto. | Ensayar que se pueda terminar con calma. |

Los modos precargados permiten que la entrada física y la respuesta sean reales durante la presentación. Para cada escena hay que acordar qué detecta la cámara, qué estado cambia y qué proyecta el sistema. Si alguna parte se muestra con una grabación o un resultado preparado, identificarla al presentarla.

## Qué mostramos según la rúbrica

| Factor | Evidencia en la demo |
|---|---|
| **Aspecto técnico · 25 %** | En cada modo, una entrada física cambia la salida. Lab permite mostrar la detección y el avance con claridad. |
| **Ambición · 20 %** | Los tres modos corren sobre el mismo montaje. Cada uno usa la capacidad de percibir y proyectar en una tarea diferente. |
| **Ejecución · 20 %** | Tres interacciones breves, transiciones ensayadas y margen dentro de los cinco minutos. |
| **Impacto · 20 %** | Una selección corregida, un boceto que entra a un trabajo digital y una predicción que se puede comprobar. Ahorros y resultados de aprendizaje quedan por medir. |
| **Originalidad · 15 %** | Una persona puede pasar del papel al trabajo del agente y recibir su resultado físicamente. El cambio de modo muestra la capacidad compartida. |

Referencia: [brief de Physical World y rúbrica](../../docs/TRACKS.md).

## Las ocho áreas para coordinar

| Área | Qué llevamos | Qué hay que resolver |
|---|---|---|
| **1. Propósito y visión** | Unión física y digital. La IA puede percibir y responder en la mesa. | Alinear una frase común para el pitch. |
| **2. Usuarios y problemas** | Hipótesis por modo: persona que desarrolla con libreta y agentes, aprendiz que ejecuta una tarea de banco, estudiante que explora un modelo. | Elegir a quién representamos en cada escena. El usuario inicial del producto sigue abierto. |
| **3. Casos de uso** | Dev Mode, Lab y Educación como modos preparados. | Elegir objetos, área de acción y respuesta que se pueda repetir. |
| **4. Propuesta de valor** | Pasar un boceto a trabajo digital, detectar una selección equivocada y comprobar una predicción. | Identificar el beneficio observable de cada escena. |
| **5. Experiencia e interacción** | Seleccionar un fragmento, poner una pieza o mover un objeto. | Comprobar precisión, sombras y claridad de los cambios proyectados. |
| **6. Nombre, marca e identidad** | Superhuman Desk y tres nombres de modo. | Dar a la proyección un tratamiento común y legible desde el público. |
| **7. Demo y pitch** | Guion de cinco minutos con Lab como momento principal propuesto. | Asignar voz, operación, cámara y respaldo. |
| **8. Escenas y pruebas** | Tres acciones comprobables con la cámara y la proyección. | Ensayar el recorrido completo y verificar si el público entiende por qué cambia la respuesta. |

## Para coordinar con el equipo técnico

- El [plan del equipo](../../docs/PLAN.md#plataforma-t%C3%A9cnica-de-manu-pcx-s%C3%A1b-0202) registra el prototipo de pcx con Claude Code y Codex y la rama `mesa` como respaldo. Falta comprobar los tres recorridos en el montaje que se presentará. El [registro de pruebas](../../docs/pruebas/sl-scanner-original.md) documenta avances y límites de calibración.
- Acordar primero la entrada física y la salida visibles de cada modo. Para Dev, el engine ya trae [proyección ajustada a una hoja](../../engine/playground/README.md#efectos-plugins-con-hot-reload). Mostrar el trabajo del agente requiere validar el vínculo entre la nota, la tarea y su resultado. [Contrato del engine y los agentes](../../docs/CONTRATO.md).
- Las impresoras 3D pueden servir para un soporte de libreta o utilería de la escena. Su función es facilitar el montaje y la lectura de la demo.
