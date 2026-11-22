# Protocolo de trabajo — experiencias

Estado: vigente para organizar el trabajo; no implica elección de producto.
Fecha: 2026-11-21. Responsables: Pablo Moraga y Matilda Roberts.

## Objetivo de esta sesión

Preparar una exploración informada de propósito, valor e ICP para una demo de hackathon. Horizonte indicado por Pablo: aproximadamente 30 horas restantes; no es una verificación del horario oficial. El éxito inmediato es una demo convincente y comprobable según la rúbrica, con una historia de impacto comprensible. Propósito, valor, ICP y caso principal siguen abiertos.

## Dónde vive cada cosa

| Ruta | Contenido |
|---|---|
| `00-coordinacion/` | Este protocolo, estado de trabajo, decisiones y preguntas abiertas |
| `01-investigacion/` | Síntesis, mapa de antecedentes, comunicación, oportunidades, fuentes y límites |
| `02-estrategia/` | Hipótesis de propósito, propuesta de valor e ICP |
| `03-casos-de-uso/` | Una ficha por candidato y resultados de sus pruebas |
| `04-marca/` | Territorios de marca, nombres, tono e identidad; después de explorar la estrategia |
| `05-demo/` | Historia, guion de cinco minutos, escenas, roles, ensayo y respaldo |
| `06-conversaciones/` | Notas de exploración, distinciones, acuerdos y preguntas posteriores |
| `07-recursos/` | Imágenes, referencias y material audiovisual con origen y condiciones de uso |

El plan compartido sigue en `docs/PLAN.md`; la rúbrica del equipo en `docs/RUBRICA.md`; el contrato técnico en `docs/CONTRATO.md`. La investigación previa de `docs/investigacion/` y el borrador `experiences/dolores.md` se conservan como antecedentes. La nueva síntesis debe advertir diferencias o afirmaciones que no pudo corroborar. No mantener dos planes compartidos en paralelo.

## Nombres

- Carpetas: minúsculas, español sin tildes, palabras separadas por guiones. Los números anteriores ordenan frentes, no prioridades ni puntuaciones.
- Documentos vivos: nombre estable y descriptivo, sin fecha ni versión: `proposito.md`, `propuesta-de-valor.md`, `icp.md`, `guion.md`.
- Registros fechados: `AAAA-MM-DD-tipo-tema.md`; ejemplo: `2026-11-21-exploracion-proposito.md`. Si hay dos del mismo tipo y tema ese día, agregar `-HHMM` en hora de Santiago.
- Candidatos: `CU-01-guia-de-armado.md`; nombres `NM-01-...` solo cuando realmente evaluemos nombres. El ID se conserva aunque cambie el título. No asignar IDs a cada idea casual.
- Recursos: `AAAA-MM-DD-origen-descripcion.ext`. Anotar enlace y autor en el documento que lo usa. No copiar material ajeno como si fuera propio.
- No usar `final`, `final-final`, `nuevo`, `v2` para documentos vivos. Git registra cambios. Una entrega congelada, si hace falta, lleva fecha y hora.
- Los archivos anteriores mantienen sus nombres para preservar referencias. Cuando un documento nuevo sustituya uno anterior, dejar un enlace claro; mover solo tras comprobar referencias.

## Estados y evidencia

Encabezado de trabajo: `Estado`, `Actualizado`, `Responsable` y, si aplica, `Decisión pendiente`. Estados: `borrador`, `en revision`, `acordado`, `descartado`, `referencia`.

Distinguir siempre:

- **Fuente:** qué dice un tercero, con URL, fecha de consulta y tipo de evidencia.
- **Hipótesis:** lo que inferimos y aún debemos probar, incluyendo usuario y comprador posibles.
- **Comprobación propia:** qué se observó, con montaje, condiciones y resultado.
- **Decisión:** quién la acordó y cuándo. Una recomendación del asistente no es un acuerdo del equipo.

No usar una demostración de laboratorio, un caso de proveedor o una anécdota como validación general de demanda. No afirmar originalidad universal por ausencia de resultados. Separar visión, comportamiento implementado y lo comprobado físicamente.

## Forma de avanzar

1. Investigación documental → conversación exploratoria → 2–3 hipótesis comparables → prueba de mesa → elección con el equipo → guion y marca → ensayo.
2. Trabajar una pregunta principal por bloque. Registrar lo que cambia y lo que sigue abierto; no intentar resolver estrategia y marca en un único prompt.
3. Llevar al equipo propuestas concretas: usuario, situación, cambio observable, momento de demo y dependencia técnica.
4. Comparar alternativas con la rúbrica indicada por Pablo: originalidad 15 %, ambición 20 %, ejecución 20 %, aspecto técnico 25 %, impacto 20 %. Puntuar solo con evidencia disponible; evitar precisión ficticia.
5. Tras cada prueba o conversación, actualizar el estado y registrar decisiones reales. Los acuerdos compartidos se trasladan al plan del equipo coordinadamente.
6. Sin publicación, despliegue, PR, envío ni contacto con terceros en este encargo. Guardar investigación y borradores locales está autorizado.

## Qué debe resolver el equipo técnico

Antes de cerrar el guion, comprobar con Max, Manu y Daniel: montaje operativo, interacción disponible, fiabilidad, latencia, capacidades de agentes, recuperación de fallos y qué forma parte del trabajo realizado durante el evento. Esta investigación no certifica esas capacidades.
