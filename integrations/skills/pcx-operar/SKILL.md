---
name: pcx-operar
description: "Ver y actuar sobre un espacio físico con pcx (mesa con cámaras y proyector) por MCP: leer la escena con scene, mirar por la cámara con look, proyectar y borrar widgets con project y remove (label, halo, arrow, panel, button, image, effect) anclados a objetos en mm, preguntar con botones proyectados usando ask y esperar toques u objetos con events_wait. Úsala siempre que haya que mostrar, señalar, resaltar o etiquetar algo sobre la mesa, saber qué objetos hay y dónde están, reaccionar a un toque o a un objeto que se mueve, aunque la persona no diga pcx."
license: MIT
compatibility: Requiere el servidor MCP de pcx conectado (nodo local con pcx serve o pcx mcp, o el gateway remoto).
metadata:
  author: pcx
  version: "0.1.0"
---

# pcx-operar: ver y actuar sobre el espacio

pcx es un nodo local (dueño del estado físico) con cámara cenital y proyector sobre una mesa. Tú ves con `scene` y `look`, y actúas proyectando luz con `project`. Todo se mide en **mm del marco `table_mm`**: origen arriba a la izquierda, x a la derecha, y hacia abajo. El tamaño de la superficie viene en `scene.surface` (por defecto 800×500 mm): léelo, no lo supongas.

Tools del nodo (10): `scene`, `look`, `project`, `remove`, `ask`, `events_wait`, `run_flow`, `calibrate`, `devices`, `home`. El gateway remoto agrega `history` (memoria del espacio). Tu cliente puede mostrarlas con prefijo (`mcp__pcx__scene`, `mcp__plugin_pcx_pcx__scene`…): es la misma tool.

Si no ves las tools, el MCP no está conectado: lee [references/conexion.md](references/conexion.md).

Skills hermanas: `pcx-flujos` (guías e interfaces que reaccionan al toque sin pasar por ti), `pcx-efectos` (shaders nuevos), `pcx-calibrar`, `pcx-domotica` (`home`), `pcx-extender`.

## Ciclo básico

1. **Mira**: `scene` (JSON, barato) o `look` (imagen, cara y sensible). Guarda `cursor`.
2. **Actúa**: `project` con widgets anclados a ids de objeto reales.
3. **Espera**: `ask` para una pregunta con botones, o `events_wait(cursor)` para toques y objetos.
4. **Limpia**: `remove` de todo lo que proyectaste (o `ttl_s` para que se borre solo).

```json
// scene → {}
{"rev": 4, "surface": {"width_mm": 800, "height_mm": 500},
 "objects": [{"id": "taza", "label": "taza", "confidence": 0.9, "center_mm": [110, 370],
              "polygon_mm": [[70,330],[150,330],[150,410],[70,410]], "bbox_mm": [70,330,80,80]}],
 "touches": [], "widgets": [], "transforms": [], "active_flows": [], "cursor": 12, "stream_id": "…"}
```

```json
// project
{"request_id": "resaltar-taza-1",
 "widgets": [
   {"type": "halo",  "id": "taza-halo",  "anchor": {"object_id": "taza"}, "color": "#00E5FF"},
   {"type": "label", "id": "taza-label", "anchor": {"object_id": "taza"}, "text": "Tu café", "size_mm": 14, "ttl_s": 30}
 ]}
// → {"ids": ["taza-halo", "taza-label"], "rev": 5}
```

```json
// remove
{"request_id": "resaltar-taza-1-fin", "ids": ["taza-halo", "taza-label"]}
```

`scene` acepta `include_hands` e `include_devices` (ambos `false` por defecto). `devices` lista cámaras, proyectores y demás (`kind`: `camera`, `projector`, `phone`, `home_assistant`, `midi`).

## `request_id`: idempotencia

Las tools que cambian algo (`project`, `remove`, `ask`, `run_flow`, `calibrate`, `home`) exigen `request_id` (1–128 caracteres). Es la identidad de la **operación lógica**: el nodo guarda el resultado 10 minutos (sobrevive reinicios) y, si llega otra llamada con el mismo `request_id`, devuelve el resultado guardado **sin ejecutar nada**.

- Operación nueva → `request_id` nuevo. Reusar uno para otra cosa hace que la llamada parezca exitosa y no cambie nada: es el error más común.
- Reintento tras un timeout o corte → el mismo `request_id`, así no se duplica.
- Usa ids legibles con sufijo único: `guia-cafe-paso2-7f3a`.

## Widgets

`project` crea o **reemplaza por `id`**. Colores `#RRGGBB` o `#RRGGBBAA`. Campos comunes: `id`, `anchor`, `z`, `ttl_s`, `on_press`.

| type | Campos propios (defaults) | Geometría mínima |
|---|---|---|
| `label` | `text`, `size_mm` (12), `color` (#FFFFFF), `bg` | `anchor.point_mm` o `anchor.object_id` |
| `halo` | `polygon_mm`, `color` (#00E5FF), `width_mm` (4), `pulse_hz` (1) | `polygon_mm` (≥ 3 puntos) o `anchor.object_id` |
| `arrow` | `from_mm`, `to_mm`, `color` (#FFD400), `width_mm` (5) | `from_mm` + `to_mm`, o un ancla |
| `panel` | `rect_mm`, `title`, `body`, `color` | `rect_mm` o un ancla |
| `button` | `rect_mm`, `label`, `progress` (0..1), `color` (#00E676) | `rect_mm` o un ancla |
| `image` | `rect_mm`, `src`, `opacity` | `rect_mm` o un ancla |
| `effect` | `shader`, `params`, `rect_mm`, `polygon_mm` | `rect_mm`, `polygon_mm` o `anchor.object_id` |

Puntos `[x, y]`, rectángulos `[x, y, w, h]`, polígonos `[[x, y], …]`. Detalle de cada widget, tamaños por defecto y `image.src` en [references/widgets.md](references/widgets.md).

## Anclar a objetos (lo que hace útil a pcx)

`anchor: {"object_id": "<id de scene>"}` hace que el widget **siga al objeto** cuando se mueve, sin que tú hagas nada. El nodo resuelve la geometría:

- `label`: centrado sobre el objeto, justo arriba de su caja.
- `halo`: el contorno del objeto agrandado 8 mm.
- `arrow`: 70 mm, apunta al objeto desde arriba (desde abajo si el objeto está pegado al borde superior).
- `panel`, `button`, `image`: a la derecha del objeto con 20 mm de aire (a la izquierda si no cabe).
- `effect`: usa el polígono del objeto.

`offset_mm: [dx, dy]` desplaza el resultado. `anchor.point_mm` ancla a un punto fijo.

Reglas que conviene saber:

- En `project`, un `object_id` que no existe da error (`objeto desconocido … (usa scene)`). Los ids los decide la percepción: sácalos siempre de `scene`, no los inventes a partir del `label`.
- Si el objeto desaparece, el widget anclado deja de dibujarse pero sigue existiendo; vuelve cuando el objeto reaparece. Bórralo tú si ya no aplica.
- Con `rect_mm` explícito el widget queda fijo en la mesa aunque tenga ancla.

## Capas (`z`) y vida (`ttl_s`)

- `z` (entero, 0 por defecto) ordena el dibujo: mayor = encima. Con el mismo `z`, los `effect` van sobre los vectores. Pon fondos y efectos de ambiente en `z` negativo y los botones arriba.
- Para los toques gana primero lo tocable, luego el mayor `z`, luego los botones.
- El nodo usa `z` 10–11 para `ask` y 50–51 para su confirmación de domótica. Mantén tus widgets bajo 50.
- `ttl_s` borra el widget solo al vencer. Úsalo en todo lo efímero (avisos, resaltados): si tu sesión se corta, la mesa no queda sucia.
- Si reemplazas un widget que tenía `ttl_s`, vuelve a mandar `ttl_s`: sin él, el vencimiento anterior sigue corriendo.

## Hacer algo tocable

`on_press: {"value": "ok", "triggers": ["dwell", "pinch", "api"], "dwell_ms": 800}` (esos son los defaults) hace tocable a cualquier widget; los `button` lo son siempre. Al confirmarse el toque llega el evento `widget_pressed{widget_id, value, trigger, point_mm}`.

- `dwell`: permanencia; el botón dibuja el anillo de `progress` mientras se llena.
- `pinch`: pellizco. `rest`: mano apoyada (dispara al apoyar, sin espera; solo si lo incluyes en `triggers`). `api`: toque inyectado por software.
- Botones cómodos: al menos 110×45 mm, separados 15 mm o más (el área tocable del botón se extiende 5 mm por lado).

## Preguntar: `ask`

```json
{"request_id": "ask-leche-1", "question": "¿Le pongo leche?", "options": ["Sí", "No"],
 "timeout_s": 40, "anchor": {"object_id": "taza"}}
// → {"status": "answered", "answer": "Sí", "index": 0, "ask_id": "ask-3", "request_id": "ask-leche-1"}
// → {"status": "timeout", "answer": null, "index": null, …} si nadie tocó
```

Proyecta un panel con la pregunta y un botón por opción (1 a 6; permanencia de 800 ms), **bloquea** hasta el toque o el plazo y limpia sus widgets. Sin `anchor` va abajo al centro; con `anchor.object_id` va al lado del objeto. `timeout_s` de 1 a 600 (60 por defecto).

Tu cliente MCP tiene su propio límite por llamada (Codex: 60 s por defecto). Usa `timeout_s` ≤ 45 salvo que sepas que tu cliente aguanta más; si necesitas esperar más, usa botones propios y `events_wait`. Opciones cortas: el botón mide 110 mm.

## Esperar eventos: `events_wait`

Long-poll con cursor. Devuelve en cuanto hay eventos o al vencer `timeout_s` (0–50, 25 por defecto).

```json
{"cursor": 12, "types": ["widget_pressed", "object_moved"], "timeout_s": 25}
// → {"stream_id": "…", "cursor": 15, "gap": null,
//    "events": [{"type": "widget_pressed", "seq": 15, "source": "core", "t_pub_ns": …,
//                "payload": {"type": "widget_pressed", "widget_id": "ok", "value": "ok", "trigger": "dwell", "point_mm": [525, 362]}}]}
```

- Parte del `cursor` de `scene` tomado **antes** de proyectar: así no pierdes un toque rápido.
- Pasa siempre el `cursor` de la respuesta anterior, también cuando `events` viene vacío.
- Filtra con `types`: sin filtro llegan `touch_move` y `dwell`, que son muchos.
- `gap` distinto de `null` significa que se perdieron eventos (retención): vuelve a leer `scene`.
- Un `cursor` mayor que el último `seq` (por ejemplo, de otro `stream_id`) se trata como 0 y reproduce el historial: si cambia `stream_id`, vuelve a `scene`.
- Con `cursor: 0` recibes historia vieja. Casi nunca es lo que quieres.

Tipos y campos de cada evento en [references/eventos.md](references/eventos.md).

## `look`: imagen + texto

`look` devuelve una imagen de la cámara y un resumen en texto (mesa, objetos con id y centro, widgets). Args: `camera_id` (omitido = la cenital principal; ids en `devices`) y `annotate` (`true` por defecto: grilla en mm y luz proyectada).

Úsalo cuando el JSON no alcanza: verificar que lo proyectado cae donde debe, leer algo escrito, juzgar un estado (nivel de un líquido, una pieza mal puesta). Si responde `Cámara sim`, es el dibujo sintético del estado: no hay cámara real cableada en ese nodo.

## Latencias esperadas

| Tramo | Cifra |
|---|---|
| Comando → luz en la mesa (hardware real, punta a punta con cámara) | ~215 ms p50, 255 ms p95 |
| Renderer | 60 fps; 1,4–8 ms por cuadro |
| Reloj del nodo (permanencia, `ttl_s`, temporizadores) | 20 Hz → resolución de 50 ms |
| Permanencia para confirmar un toque | 800 ms por defecto (`dwell_ms`) |
| Seguimiento de un objeto movido | ~120 ms; registrar un objeto nuevo con LLM, ~20 s |
| Por el gateway remoto | suma la red; plazos: `project`/`remove`/`run_flow` 15 s, `look` 20 s, `home` 30 s, `calibrate` 180 s |
| Tu propio turno (LLM) | segundos |

Conclusión práctica: una interfaz que debe responder al toque al instante no puede depender de tu turno. Para eso existe `run_flow` (skill `pcx-flujos`).

## Privacidad

Los cuadros de cámara viven en memoria compartida dentro del nodo y **no salen de ahí salvo cuando llamas `look`**: esa imagen viaja hasta ti y, por lo tanto, hasta el proveedor de tu modelo. `scene` y los eventos solo llevan geometría y etiquetas.

- Prefiere `scene`. Llama `look` cuando de verdad necesites ver, no en bucle ni «por si acaso».
- Avisa a la persona cuando vas a mirar por una cámara que no es la de la mesa.
- No guardes ni reenvíes las imágenes a otros servicios.
- Fuera de tu control: si el nodo tiene activo el registro de objetos con LLM (`pcx-objects`, niveles 4–5), manda recortes de los objetos a la API de Claude.

## Qué no hacer

- **No tapes ni imites la confirmación del nodo.** Para acciones de domótica el nodo proyecta arriba a la derecha (los últimos 250 mm de ancho, primeros 150 mm de alto) un panel amarillo «¿Confirmas?» y el botón `pcx:confirm:<id>`. Es la prueba de que una persona aprobó: solo se resuelve con una mano real sobre el botón (permanencia o pellizco) o desde la consola humana, que usa un token de consola que tú no tienes. Mientras está pendiente, el nodo oculta tus widgets. No intentes aprobarla por ninguna otra vía (ver `pcx-domotica`). No proyectes nada en esa zona mientras haya una confirmación pendiente, ni un botón que diga «Confirmar» o un panel «¿Confirmas?» en amarillo `#FFD400`, ni widgets con `z` ≥ 50. Los ids que empiezan con `pcx:` son del nodo y se rechazan.
- No inundes la mesa de luz: un rectángulo blanco grande deslumbra a la cámara y empeora la percepción. La luz es lo que no es negro; proyecta poco y con contraste.
- No dejes widgets huérfanos: limpia al terminar o usa `ttl_s`.
- No uses texto chico: `size_mm` entre 10 y 20; frases cortas.
- No hagas sondeo rápido con `scene` para detectar cambios: usa `events_wait`.
- No supongas que una tool tuvo efecto tras un timeout: mira `scene` antes de reintentar (con el mismo `request_id`).

## Errores comunes

| Síntoma | Causa | Arreglo |
|---|---|---|
| `project` responde bien pero no cambia nada | `request_id` reutilizado | Uno nuevo por operación |
| `objeto desconocido "x" (usa scene)` | id inventado o el objeto ya no está | Leer `scene` y usar su `id` |
| `label necesita anchor.point_mm o anchor.object_id` | Falta geometría | Dar ancla o `rect_mm`/`polygon_mm` |
| `ids reservados del nodo (pcx:*)` | id con prefijo `pcx:` | Cambiar el id |
| El widget anclado no se ve | El objeto salió de la mesa | Esperar `object_appeared` o borrar |
| El efecto no se ve y no hay error | El `shader` no existe en el catálogo del renderer | Ver `pcx-efectos` |
| `events_wait` devuelve cientos de eventos viejos | `cursor` en 0 o de otro `stream_id` | Tomar `cursor` de `scene` |
| `ask` corta con error del cliente | `timeout_s` mayor que el límite del cliente | `timeout_s` ≤ 45 |
| Lo proyectado cae corrido respecto del objeto | Sin calibrar o rig movido | Ver `pcx-calibrar` |
