# Superhuman Desk con personas: respuesta a la propuesta de tres modos

Estado: referencia.
Actualizado: 2026-11-21.
Responsable: Matilda.
Nota: escrito antes del plan del sábado (04:21), que eligió Escritorio, Educación y Laboratorio. Se conserva por la idea de «persona con dolor reconocible», que retoma la [justificación del problema](../01-investigacion/justificacion-del-problema.md).

**Dirección.** Se mantiene la mesa de Superhuman Desk, pero cada modo responde a **una persona con un dolor que el jurado reconoce al tiro**. Hay dos modos preparados: **Taller** (principal) y **Mesa familiar**. La rehabilitación tras un ACV queda como alternativa del segundo modo. Lab, Dev y Educación se integran o pasan al cierre.

**Propósito.** Que la IA vea lo que pasa en la mesa y ayude justo ahí, a quien más lo necesita: al estudiante sin maestro al lado y al abuelo que no sigue la conversación.

**Pitch.** Tu agente ve la mesa, te muestra dónde va cada pieza y te ataja el error antes de que pase. Y en la mesa de la casa, le muestra al abuelo lo que no alcanzó a oír ni a ver.

## Los dos modos

| Modo | Persona y dolor | Qué se ve | Valor a comprobar |
|---|---|---|---|
| **Taller** (adapta Lab) | Estudiante de un liceo técnico de bajos recursos: un profesor para 30, pocas máquinas y un manual en papel. Se equivoca y nadie lo ve a tiempo. | **Del manual a la luz en un minuto:** el agente lee el manual o una foto, ilumina qué pieza va y dónde, y si toma la equivocada la marca en rojo y no avanza. | Menos errores en novatos que con papel. Medirlo en vivo con personas reales (papel vs. luz). |
| **Mesa familiar** | El tata que oye poco y ve poco, en el almuerzo del domingo. Asiente sin entender y termina callado. | El agente escucha la conversación y **solo cuando le hablan al tata** muestra frente a él **una palabra clave gigante**, de alto contraste y nítida (por ejemplo, **SAL**), mientras se ilumina el salero. | Que el tata responda a la primera, sin que le repitan. |
| *Alternativa: Rehab* | Persona con la mano afectada tras un ACV, con una hoja fotocopiada de ejercicios. | Pasar tapitas de una zona a otra: la mesa cuenta, nota si usa la mano sana y le avisa al kinesiólogo. | Más repeticiones en casa, y medidas. |

**Momento principal propuesto: Taller.** Es la continuación del Lab de Pablo, tiene la evidencia más fuerte y el jurado participa. **Mesa familiar es el cierre emotivo:** «SAL» aparece y la luz cae en el salero.

## Guion de cinco minutos

| Tiempo | Acción en la mesa | Qué se tiene que entender |
|---|---|---|
| **0:00–0:30 · Dolor** | «¿Quién aprendió algo con un manual que no entendía?» Foto de un taller de liceo: 1 profesor, 30 estudiantes. | Quien aprende se equivoca y nadie lo ve a tiempo. |
| **0:30–1:15 · El agente ve** | Se vacían las piezas y se muestra el manual. Cada pieza queda nombrada y aparece el paso 1. | Del manual a la luz en un minuto, sin programar nada. |
| **1:15–2:40 · Taller** | Un jurado arma sin leer. Toma la pieza equivocada: rojo, la voz explica y la mesa no avanza hasta que la cambia. | La mesa ataja el error antes de que pase. |
| **2:40–3:50 · Mesa familiar** | Tres personas conversan; el «tata» es un jurado con lentes de simulación y tapones. Alguien dice «tata, ¿me pasa la sal?». | Aparece **SAL** gigante frente a él y se ilumina el salero. Responde a la primera. |
| **3:50–4:35 · Cierre** | «La misma mesa también…»: Rehab, Dev y la Luna en tres imágenes. | Es una sola mesa; cambian la persona y la tarea. |
| **4:35–5:00 · Margen** | Transiciones o repetir un gesto. | Terminar con calma. |

Si algo se muestra grabado, se avisa al presentarlo.

## Qué mostramos según la rúbrica

| Factor | Evidencia en la demo |
|---|---|
| **Técnico · 25 %** | Lectura del manual + detección de la pieza + error atajado en vivo; voz → palabra clave → objeto iluminado. |
| **Ambición · 20 %** | La misma mesa sirve a dos personas muy distintas, sin programar cada guía. |
| **Ejecución · 20 %** | Dos escenas cortas, ensayadas y con respaldo. |
| **Impacto · 20 %** | Personas con nombre: el estudiante sin maestro al lado y el tata que se queda fuera de la conversación. |
| **Originalidad · 15 %** | La guía no la programa nadie; la mesa entiende a quién le hablan y traduce la conversación a una palabra y a una luz. |

## Datos para el pitch
- Guía proyectada vs. papel: hasta **−80 % de errores** ⚠️ (cifra del proveedor, no confirmada) y hasta **−50 %** ✅ (Funk 2015, n=15). Ayuda a novatos, no a expertos ✅.
- Más de **160 mil estudiantes** en educación técnico-profesional en Chile ✅ (164.935, incluye adultos; Mineduc 2023).
- En Chile, el **45 %** de los mayores de 65 dice oír mal y solo el **18 %** usa audífono ⚠️ (ENS 2016-17, por verificar).
- Sobre los 80, **más de 1 de cada 5** ve mal y oye mal a la vez ⚠️ (por verificar).

## Para coordinar con el equipo técnico
- **Taller:** reutilizar el Lab de Pablo y el juego de memoria del engine (enciende en secuencia, compara y marca el error en rojo). Usar un kit de piezas grandes y de colores, porque la webcam no ve diferencias de menos de 1 mm.
- **Mesa familiar:** voz a texto + el agente decide si le hablan al tata y extrae UNA palabra clave + se ilumina el objeto si está en la mesa. Fijar los asientos o usar un teléfono por asiento como micrófono. Prueba de 15 min: ¿cuánto tarda en aparecer la palabra?
- Estilo de la proyección: fondo negro, una palabra blanca o amarilla gigante y halos de contorno (no rellenos).

## Por decidir con Pablo
1. ¿Taller como principal y Mesa familiar como cierre, o al revés?
2. ¿El segundo modo es Mesa familiar o Rehab tras un ACV?
3. ¿Dev y la Luna entran al cierre o se guardan?
