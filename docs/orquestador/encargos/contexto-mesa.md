# Contexto para el agente de la mesa (MacBook) — del orquestador de la hackathon

Léelo entero antes de seguir. Es el contexto que te falta; no cambia lo que Daniel te pidió, lo ordena.

## Dónde estamos
- **Platanus Hack 26**, track **Physical World**. Hacking hasta el **domingo 08:30–09:00**; presentaciones desde las 09:30; finales 12:00. Ahora es sábado de madrugada: quedan ~30 horas.
- **La demo de 5 minutos es lo que más pesa.** Nadie revisa el código. Rúbrica (0–5): técnico 25 %, ambición 20 %, ejecución 20 %, impacto 20 %, originalidad 15 %.
- Puesto fijo: el rincón oscuro con mesa larga de la sede, con el Mac de Daniel, el proyector T749 y la webcam cenital.

## Qué queremos lograr
Un **agente que ve tu mesa y te arma la interfaz que necesitas**, con un proyector barato y cámaras comunes. Lo distintivo no es el mapping (Lightform ya lo vendió y cerró), sino: objetos cualesquiera sin marcadores + agente que compone lo que se proyecta + interacción con las manos.

Feedback del mentor (vie 23:08): falta **un dolor concreto** y foco; mucha tecnología wow no resuelve nada. Pablo y Matilda están eligiendo el dolor y el guion. Tu trabajo tiene que poder contar esa historia, sea cual sea.

## El equipo y quién hace qué
| Quién | Qué |
|---|---|
| Daniel | Dirige la mesa contigo |
| Max | Autor del engine (sl-scanner). El original no se toca |
| Manu | Ordena el engine en el repo del equipo y conecta agentes a su API/MCP |
| Pablo y Matilda | Dolor, casos de uso, guion de la demo, nombre |
| Orquestador (mini, tab «Orquestador» del Herdr del mini) | Plan, repo, WhatsApp del grupo, respaldos |

## Repo del equipo
`github.com/satelerd/platanus-hack-26` (privado). En el mini: `~/code/platanus-hack-26/repo/`.
- `engine/` = copia del sl-scanner de Max (lo ordena Manu).
- **Rama `mesa`**: el orquestador respaldó ahí, a las 01:5x, todo lo tuyo que estaba sin commitear en `hack/daniel-mesa` (efectos, manos, DINO, seguidores v1–v4, MESA-PIPELINE.md, `mesa/`). Lo va a re-respaldar periódicamente. **Tú no necesitas hacer git**: sigue trabajando en el checkout del MacBook.
- Hallazgos de pruebas: `docs/pruebas/` (se publican al grupo). Sigue anotando avances en `~/code/platanus-hack-26/max/ESTADO.md` del MacBook: el orquestador lo lee y manda updates al grupo.

## Prioridades (del orquestador; Daniel puede cambiarlas)
1. **Que la demo no falle.** Una mesa que funcione igual 10 veces seguidas vale más que una cascada perfecta que falla 1 de 3. Antes de seguir afinando el seguidor, deja una versión «demo segura» que se pueda levantar con un solo comando y que se recupere sola si algo se cae.
2. **Un guion reproducible.** Una secuencia de 5–6 momentos que siempre salen (ver `docs/investigacion/04-demo.md` en el repo del equipo).
3. **Grabar un video de respaldo** de la mesa funcionando, por si en vivo algo falla.
4. Recién después: seguidor v4, objetos nuevos, anfitrión.
- Evita sumar versiones paralelas (`seguidor_v2..v4`): deja una sola vigente y anota cuál en ESTADO.md.

## Cuidados
- Vas en 60 % de contexto. Antes de llegar a ~75 %, escribe en ESTADO.md un traspaso (qué corre, cómo levantar todo, qué estás haciendo, qué falta) para que una sesión nueva siga sin perder nada.
- Costos: anota en ESTADO.md cuánto llevan las llamadas a modelos (ya las registras en `/tmp/mesa/llm_uso.jsonl`).
- El anfitrión graba y le habla a gente que no es del equipo: pon un cartel visible de que la mesa usa cámaras.
- Secretos nunca en archivos. Nada destructivo. No escribas al WhatsApp: los updates los manda el orquestador.

## Al terminar el paso actual
Responde en 5 líneas: qué funciona hoy de punta a punta, qué es frágil, qué te falta para tener la «demo segura», y si necesitas algo de otra persona del equipo.
