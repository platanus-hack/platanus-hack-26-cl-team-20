---
name: pcx-flujos
description: "Crear modos e interfaces interactivas en la mesa de pcx con run_flow: una máquina de estados (estados, transiciones por evento o por tiempo, acciones project, update, remove, clear, emit, home_call) que corre dentro del nodo y responde al toque sin pasar por el modelo. Úsala siempre que pidan una guía paso a paso, un tutorial sobre objetos reales, un juego, un quiz, un menú, un modo, un temporizador o cualquier cosa que deba reaccionar sola a toques, manos u objetos que se mueven; también para decisiones sí o no junto al mouse (Vibecoders Desk), guías educativas sobre una maqueta (sistema solar) y protocolos de laboratorio con verificación. Incluye ejemplos completos y un validador contra los contratos."
license: MIT
compatibility: Requiere el servidor MCP de pcx conectado. El validador necesita el repo de pcx y uv.
metadata:
  author: pcx
  version: "0.1.0"
---

# pcx-flujos: modos con `run_flow`

Un toque debe sentirse inmediato y tu turno tarda segundos. `run_flow` resuelve eso: le entregas al nodo una máquina de estados y él la ejecuta en su reloj (20 Hz), reaccionando a toques y objetos en milisegundos. Tú solo la lanzas, escuchas lo que emite y decides lo que una máquina no puede decidir.

Regla para elegir:

| Necesito… | Usa |
|---|---|
| Una sola pregunta con botones | `ask` (ver `pcx-operar`) |
| Mostrar algo fijo | `project` |
| Varios pasos, tiempos, feedback inmediato o que siga funcionando sin mí | `run_flow` |

Antes de escribir un flujo llama `scene`: necesitas los `id` reales de los objetos y el tamaño de la superficie.

## Forma

```json
{"request_id": "<único por lanzamiento>",
 "flow": {
   "id": "mi-flujo", "initial": "inicio", "cleanup": true, "timeout_s": 300,
   "states": [
     {"id": "inicio",
      "on_enter": [ <acciones> ],
      "transitions": [
        {"on": {"event": "widget_pressed", "where": {"widget_id": "btn"}}, "to": "otro", "actions": [ <acciones> ]},
        {"after_s": 30, "to": "fin"}
      ]},
     {"id": "otro", "on_enter": [], "transitions": [{"after_s": 2, "to": "fin"}]},
     {"id": "fin", "final": true}
   ]}}
```

| Campo | Regla |
|---|---|
| `flow.id` | Identidad del flujo. Lanzar otro con el mismo `id` **reemplaza** al que corre (causa `restart`) |
| `initial` | Debe existir en `states` |
| `states` | Al menos uno; ids únicos |
| `cleanup` | `true` (default): al terminar se borran los widgets que el flujo proyectó |
| `timeout_s` | Vida máxima del flujo (≤ 86 400). Ponlo siempre |
| `final` | El estado corre su `on_enter` y el flujo termina. No puede tener transiciones |
| transición | **Exactamente uno** de `on` o `after_s` (≥ 0), más `to` y `actions` opcionales |

Respuesta: `{"flow_id", "state", "running", "cursor", "hint"}`.

## Acciones

Son los mismos comandos del nodo, sin `request_id` (el nodo lo deriva).

| `type` | Campos | Notas |
|---|---|---|
| `project` | `widgets` | Igual que la tool. Dentro de un flujo un `object_id` ausente **no** da error: el widget espera a que el objeto aparezca |
| `update` | `id`, `set` | Cambia campos de un widget existente (texto, color…). Solo existe dentro de flujos |
| `remove` | `ids` | Ids que no existen se ignoran |
| `clear` | — | Borra todo lo que proyectó este flujo |
| `emit` | `event`, `data` | Publica `flow_emitted{flow_id, event, data}`: tu canal hacia el agente |
| `home_call` | `domain`, `service`, `data` | Sujeta a la política del nodo: puede quedar pendiente de confirmación física (ver `pcx-domotica`) |

Una acción que falla no detiene el flujo: emite `flow_emitted{event: "action_error", data: {state, error}}`. Escúchalo mientras depuras.

## Transiciones por evento

`on: {"event": "<type>", "where": {<ruta>: <valor>}}`. Coincide si el `type` es igual y **cada** par de `where` es igual al campo del evento (rutas con punto). Solo igualdad: no hay rangos, zonas ni expresiones.

| Evento | Rutas útiles para `where` |
|---|---|
| `widget_pressed` | `widget_id`, `value`, `trigger` |
| `touch_down` / `touch_up` | `touch.widget_id`, `touch.object_id`, `touch.trigger` |
| `object_appeared` | `object.id`, `object.label` |
| `object_moved` | `object_id` |
| `object_removed` | `object_id`, `label` |
| `flow_emitted` | `event`, `flow_id`, `data.<clave>` |
| `home_call_done` | `status`, `request_id` |
| `hands_gone` | (sin filtro) |

Cuidado con los tipos en `where`: `2` y `2.0` no son iguales. Usa cadenas y booleanos en `data`.

Para «toca este objeto» usa `touch_down` con `touch.object_id`. Para «toma este objeto» combina `object_moved` y `object_removed` (al levantarlo desaparece de la mesa). Para una zona, proyecta un widget tocable y escucha `widget_pressed`.

## Precedencia y tiempos

- Con un evento se prueban las transiciones `on` en orden de lista; gana la primera que coincide.
- Si un evento y un temporizador caen en el mismo tick, **gana el evento**. Entre temporizadores gana el menor `after_s`.
- `after_s` cuenta desde que se entró al estado; volver a entrar reinicia la cuenta.
- `after_s: 0` es inmediata. Máximo 8 seguidas; un ciclo hecho solo de inmediatas se rechaza.
- Resolución del reloj: 50 ms.

## Seguir un flujo desde el agente

```json
// 1. scene → guarda cursor (antes de lanzar, para no perder nada)
// 2. run_flow
// 3. events_wait en bucle
{"cursor": 128, "types": ["flow_emitted", "flow_state_changed"], "timeout_s": 25}
```

- `flow_state_changed{flow_id, from_state, to_state, cause}`: `cause` es `start`, el tipo del evento que disparó, o `after_s`.
- El flujo terminó cuando `to_state` es `null`. Causas: `final`, `timeout`, `restart`, `immediate_loop`, `stop`.
- `flow_emitted` lleva lo que tú definiste con `emit`. Diseña los flujos para que **emitan decisiones**, no para que tengas que deducirlas de los cambios de estado.

## Terminar o cancelar

No hay tool para detener un flujo. Diseña la salida:

1. Un botón «Salir» con transición a un estado `final`.
2. `timeout_s` siempre.
3. Desde el agente: lanza con el **mismo `flow.id`** (y `request_id` nuevo) un flujo mínimo cuyo estado inicial sea `final`. Reemplaza al anterior, limpia sus widgets y termina.

```json
{"request_id": "cancelar-mi-flujo-1", "flow": {"id": "mi-flujo", "initial": "fin", "states": [{"id": "fin", "final": true}]}}
```

## Mandarle una señal a un flujo que corre

Un flujo no puede mirar por la cámara ni razonar; tú sí. Patrón de verificación:

1. El flujo entra a un estado `verifica` que hace `emit` de `verificar` y espera `flow_emitted` con `event: "paso_verificado"`.
2. Tú recibes `verificar` por `events_wait`, miras (`look`, `scene`) y decides.
3. Respondes lanzando un **flujo señal** de un solo estado final que emite el evento (ver `assets/senal.json`). Usa otro `flow.id`: si usas el del flujo principal, lo reemplazas.
4. El flujo principal toma la transición. Dale siempre un `after_s` de respaldo por si tú no respondes.

## Ejemplos completos (validados contra los contratos)

Están en `assets/`. Son los argumentos de `run_flow` tal cual. **Cambia los `object_id` por los de `scene`** y ajusta los `rect_mm` a tu superficie.

| Archivo | Escena | Qué muestra |
|---|---|---|
| [assets/vibecoders-desk.json](assets/vibecoders-desk.json) | Vibecoders Desk | Un agente pide decidir sí o no: botones anclados al `mouse` que lo siguen; se confirma dejando la mano 1 s; emite `decision` |
| [assets/edu-sistema-solar.json](assets/edu-sistema-solar.json) | Edu | Guía sobre la maqueta: «agarra Júpiter»; si toman Marte corrige y vuelve; pista por tiempo; emite `reto_superado` |
| [assets/lab-guia.json](assets/lab-guia.json) | Lab | Tres pasos con verificación: el flujo pide `verificar`, el agente mira la cámara lateral y responde con `assets/senal.json` |
| [assets/senal.json](assets/senal.json) | — | Flujo señal de un estado: emite `paso_verificado` |

### Vibecoders Desk: decisiones con la mano junto al mouse

Los botones «Sí» y «No» llevan `anchor.object_id: "mouse"` con `offset_mm` vertical: el nodo los pone al lado del mouse y los mueve con él. `triggers: ["dwell", "api"]` con `dwell_ms: 1000` exige dejar la mano un segundo; el anillo del botón muestra el avance. No agregues `rest` a una decisión: dispara apenas se apoya la mano, y junto al mouse la mano se apoya todo el tiempo.

Lanza un flujo por decisión (cambia `title`, `body` y `data.pregunta_id`), espera `flow_emitted` con `event: "decision"` y lee `data.respuesta`: `si`, `no` o `sin_respuesta` (60 s). Para una pregunta suelta sin feedback, `ask` con `anchor: {"object_id": "mouse"}` hace lo mismo en una llamada.

### Edu: guía sobre la maqueta del sistema solar

Cada planeta es un objeto de `scene` (`sol`, `jupiter`, `marte`). El estado `busca_jupiter` escucha tres formas de «agarrar» (`object_moved`, `object_removed`, `touch_down` sobre el objeto) para el planeta correcto y para el distractor. El error lleva a `ese_es_marte`, que corrige con un halo de 4 s y vuelve solo. Sin respuesta en 25 s, `pista_jupiter` hace brillar el correcto. Para más retos, repite el trío `busca_X` / `ese_es_Y` / `X_ok`.

### Lab: guía paso a paso con verificación

Dos tipos de verificación: la que el nodo puede hacer solo (`object_moved` de la pipeta en el paso 1) y la que necesita ojos (nivel del líquido en el paso 2). Para la segunda el flujo emite `verificar{paso, criterio, camara}`; el agente llama `look` con la cámara lateral (`camera_id` de `devices`), juzga y lanza `senal.json` con `ok: true` o `false`. Si el agente no responde en 40 s, `sin_verificar2` deja que la persona confirme. «Salir» pide 1,2 s de permanencia para que no se toque sin querer.

## Validar antes de lanzar

Desde la raíz del repo de pcx:

```bash
uv run --project contracts python integrations/skills/pcx-flujos/scripts/validar_flujo.py mi-flujo.json
```

(Instalada como skill, el script está en `scripts/validar_flujo.py` de esta carpeta.) Usa `parse_args("run_flow", …)` de los contratos, que es la misma validación del nodo, más el JSON Schema de la tool. Además avisa de estados inalcanzables, estados sin salida, filtros `where` con campos que el evento no tiene y `update` de widgets que el flujo nunca proyecta. Sin el repo a mano, el nodo valida igual al recibir `run_flow` y responde `flujo inválido: …`.

## Probar sin hardware

Solo en un nodo de desarrollo lanzado con `pcx serve --debug` (las rutas `/debug/*` están apagadas por defecto y usan `PCX_DEBUG_TOKEN`, que no es el token del MCP). Es para que quien desarrolla pruebe sus flujos; no es parte de operar la mesa y no sirve para confirmar domótica:

```bash
# crear o mover un objeto
curl -s -X POST localhost:9200/debug/object -H "Authorization: Bearer $PCX_DEBUG_TOKEN" -H 'Content-Type: application/json' \
  -d '{"id": "mouse", "label": "mouse", "center_mm": [500, 300], "size_mm": [60, 100]}'
# tocar un widget (toque `api`: tus botones deben incluir `api` en `triggers` para probarse así)
curl -s -X POST localhost:9200/debug/touch -H "Authorization: Bearer $PCX_DEBUG_TOKEN" -H 'Content-Type: application/json' \
  -d '{"widget_id": "vd-si"}'
```

## Buenas prácticas

- Prefija los ids de widgets con el flujo (`lab-…`): dos flujos comparten la mesa y un `project` con el mismo `id` pisa al otro.
- Un panel que se actualiza (`update`) parpadea menos que borrar y volver a proyectar.
- Todo estado no final necesita una salida que no dependa de la persona: `after_s` o el `timeout_s` del flujo.
- Emite al terminar (`guia_terminada`, `decision`): el agente no debería adivinar.
- Deja libre la esquina superior derecha: ahí proyecta el nodo sus confirmaciones.
- Un flujo con `home_call` puede actuar sobre el mundo físico: léelo dos veces y revisa `pcx-domotica`.

## Errores comunes

| Síntoma | Causa | Arreglo |
|---|---|---|
| `run_flow` responde bien pero no pasa nada nuevo | Mismo `request_id` de un lanzamiento anterior: devuelve el resultado guardado | `request_id` nuevo por lanzamiento |
| `transición … con on y after_s a la vez` | Ambos en la misma transición | Sepáralas en dos |
| `ciclo de transiciones inmediatas` | Estados unidos solo por `after_s: 0` | Pon un evento o un tiempo > 0 |
| El flujo termina con `cause: immediate_loop` | Más de 8 inmediatas seguidas | Igual |
| La transición nunca dispara | `where` con una ruta que no existe (`object_id` en `object_appeared` es `object.id`) | Ver la tabla de rutas; correr el validador |
| `action_error … widget desconocido` | `update` de un widget que ya se borró o nunca se proyectó | Proyectarlo antes o no borrarlo |
| El widget anclado no aparece | El `object_id` no existe en `scene` | Usar el id real |
| Lanzar la señal reinicia el flujo principal | La señal usa el mismo `flow.id` | Otro `flow.id` para la señal |
| La mesa queda con widgets tras terminar | `cleanup: false`, o widgets proyectados fuera del flujo | `cleanup: true`; lo de fuera, `remove` |
