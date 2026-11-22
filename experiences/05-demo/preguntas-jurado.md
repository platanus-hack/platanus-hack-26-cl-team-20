# Preguntas probables del jurado (2 minutos)

Estado: borrador para el ensayo de las 06:00.
Actualizado: domingo 22-nov-2026, 01:00.
Para qué: llegar con respuestas cortas (10–15 segundos cada una) y saber **quién responde qué**.
Base: [guion de 3 minutos](guion-3min.md), [justificación del problema](../01-investigacion/justificacion-del-problema.md), `docs/PLAN.md` y `docs/RUBRICA.md`.
Rúbrica: técnico 25 % · ambición 20 % · ejecución 20 % · impacto 20 % · originalidad 15 %.

✅ = dato visto en su fuente · ⚠️ = falta que alguien del equipo lo confirme antes de decirlo.

## Regla para responder

1. Una idea por respuesta, y un dato si lo hay.
2. Si no lo medimos, se dice: «eso todavía no lo medimos». Es mejor que inventar.
3. Lo técnico lo responden Manu, Max o Daniel. El problema y las cifras, Matilda o Pablo.

## Técnico (25 %)

| Pregunta | Respuesta corta | Estado |
|---|---|---|
| **¿Qué es lo difícil de esto?** | «Tres cosas: saber dónde está cada objeto en milímetros, proyectar justo encima, y ver la mesa sin confundirse con nuestra propia luz. Para eso apagamos el proyector un instante y sacamos una foto limpia.» | ✅ Mesa recalibrada a 0,7 mm y foto limpia 5 de 5 en el checkpoint v1 (Daniel, sáb 23:32). ⚠️ Repetir la medición el domingo antes de decirla |
| **¿Corre en la nube?** | «El motor corre local, en el computador de la mesa: es un programa en Rust con el modelo de segmentación incluido. A la nube solo viaja lo que el agente necesita.» | ✅ Decisión de arquitectura del sáb 17:16 (`docs/PLAN.md`). ⚠️ La voz y el agente sí usan internet: no decir «todo local» |
| **¿Cómo se conecta un agente?** | «Por MCP, el estándar abierto. Cualquier agente (Claude Code, Codex) ve la mesa, proyecta y espera un toque.» | ✅ `docs/PLAN.md` (prototipo medido con Claude Code y Codex) |
| **¿Qué tan rápido responde?** | Que la responda Manu con el número medido en la mesa. | ⚠️ En el plan hay metas («objetos en menos de 1 s») y una medición antigua (215 ms de punta a punta, prototipo del sábado). Falta el número de la versión de la demo |
| **¿Reconoce cualquier objeto o solo los de la demo?** | Que la responda Max. | ⚠️ Las tres escenas son presets preparados. Falta confirmar qué reconoce el motor fuera de ellos |
| **¿Qué fue en vivo y qué preparado?** | Decir la verdad escena por escena. Si se usó un video, ya se avisó en el momento. | ⚠️ Se define después del ensayo |
| **¿Funciona con luz de día?** | «Con un proyector barato necesita luz baja. Con uno más potente, mejora. Es el límite del hardware, no del motor.» | ✅ Límite que marcó el mentor (`docs/PLAN.md`). ⚠️ La segunda frase es razonable, pero no la probamos |
| **¿Qué hicieron en estas 36 horas?** | El equipo tiene que acordar esta respuesta antes de la demo. | ⚠️ **Importante:** el plan dice que Max traía parte del motor hecho y que faltaba confirmar si las bases permiten código previo. Si preguntan, hay que decir con claridad qué existía y qué se hizo aquí |

## Impacto (20 %)

| Pregunta | Respuesta corta | Estado |
|---|---|---|
| **¿A quién le sirve primero?** | «A quien aprende con las manos y tiene un profesor para todo el curso: en Chile hay más de 160 mil estudiantes de educación técnica.» | ✅ MINEDUC 2023: 164.935 (incluye adultos; decir «estudiantes», no «jóvenes») |
| **¿De dónde salen las cifras?** | «Todas son de 2023 en adelante y están en esta lámina, con su fuente.» Mostrar la slide de fuentes. | ✅ `slide-fuentes.html` |
| **El estudio de Harvard, ¿está publicado?** | «Es un working paper de noviembre de 2026, con 720 mil programadores. Todavía no pasa por una revista.» | ✅ Chen y Stratton, versión 19-nov-2026 |
| **¿Lo probaron con usuarios?** | «Todavía no. Las personas de la demo son hipótesis; el siguiente paso es probarlo en un liceo técnico.» | ✅ No hubo pruebas ni entrevistas |
| **¿Esto mejora el aprendizaje o evita errores?** | «Eso no lo medimos. Lo que mostramos es que la mesa avisa el error en el momento en que ocurre, y no al final.» | ✅ Acordado: no decir que sube puntajes ni que evita accidentes |
| **¿Por qué importa en un taller o fábrica?** | «La manufactura tiene la tasa de accidentes laborales más alta de Chile: 3,5 por cada 100 trabajadores.» Sin decir que la mesa los evita. | ✅ SUSESO 2024 (cifra de reserva) |
| **¿Quién paga por esto?** | Que la responda Pablo o Max. | ⚠️ El equipo no ha definido un modelo de negocio |

## Originalidad y ambición (35 %)

| Pregunta | Respuesta corta | Estado |
|---|---|---|
| **¿Por qué no lentes de realidad aumentada?** | «Porque aquí todos ven lo mismo y nadie se pone nada en la cabeza.» | ✅ Argumento del guion |
| **¿Por qué no una tablet?** | «La tablet te hace mirar a otro lado. Aquí la ayuda aparece sobre el objeto que tienes en la mano.» | ✅ Argumento de la justificación |
| **Proyectar sobre objetos ya existe. ¿Qué es lo nuevo?** | «Lo nuevo no es proyectar: es que un agente de IA ve la mesa, decide y actúa sobre los objetos reales, y que cualquier agente se puede conectar.» | ✅ No decir «autocalibración» ni «democratizar el mapping»: otros ya lo hicieron (Lightform, RoomAlive) |
| **¿Cuánto cuesta armarlo?** | «Un proyector y una cámara web comunes; no necesita equipo especial.» Sin dar un número. | ⚠️ No tenemos el precio exacto. No es prioridad |
| **¿La cámara graba a las personas?** | «El motor procesa la imagen en el computador y entrega posiciones de objetos, no video. Cuando el agente pide mirar, recibe una foto que no se guarda.» | ✅ `docs/PLAN.md` (contrato del motor: coordenadas y objetos en mm) y `docs/arquitectura/SEGURIDAD.md` (la foto no se guarda por defecto; las miniaturas caducan a las 24 h). ⚠️ Confirmar con Daniel que la versión de la demo funciona así |
| **¿Qué sigue después del hackathon?** | Que la responda Max. | ⚠️ Sin definir |

## Lo que el equipo tiene que cerrar antes de las 08:30

1. **Código previo:** qué se dice si preguntan qué se hizo en las 36 horas.
2. **Un número de velocidad** medido en la mesa (Manu).
3. **Quién responde** cada tipo de pregunta.
