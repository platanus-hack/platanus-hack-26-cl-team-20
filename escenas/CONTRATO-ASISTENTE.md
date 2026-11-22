# Contrato del asistente: cualquier agente, cualquier preset, en tiempo real

Pedido de Max (21-nov, 18:50): las acciones y contratos de los agentes son **universales**. Cualquier preset o efecto
tiene parámetros configurables, cualquier agente puede consumir una foto de la mesa o de un área segmentada y el
catálogo de objetos, y con eso se arman asistentes en tiempo real que interactúan con cualquier creación.

La idea en una línea: **el agente no sabe de escenas; lee el manifiesto del preset activo y usa siempre las mismas
tools.** La voz de ElevenLabs (h24), Claude Code o Codex conducen el sistema solar, el laboratorio o el escritorio sin
código propio por escena.

Ejemplo completo: `escenas/sistema-solar/preset.json` (manifiesto; el conductor solar todavía no atiende los pedidos
de §3).

## 1. Las tools del asistente (las mismas para todo preset)

| Tool | Qué hace | Cómo se resuelve hoy con el MCP de la sesión |
|---|---|---|
| `mesa_objetos(prefijo?)` | El catálogo: cada objeto con `id`, `label`, `polygon_mm`, `center_mm`. Los del motor (`m01`…) y los de las escenas (`solar:saturno`, `lab:vaso-b`). | `scene` |
| `mesa_ver(objeto?, rect_mm?, limpia?)` | Foto de la mesa entera, o recortada a un objeto o a un área segmentada. `limpia` quita lo proyectado. | `look {clean, annotate:false}` + recorte por el `polygon_mm` del objeto, sobre la vista rectificada (1 px = 1 mm). |
| `mesa_eventos(cursor)` | Lo que pasa: toques, voz, eventos de las escenas (`canasto_movido`, `nivel`, `libreta_escrita`). | `events_wait` |
| `mesa_entender(preguntas, objeto?)` | Entender en vivo lo que no es geometría: qué señala la persona, si el vaso se ve rebalsado, si está escribiendo. Preguntas tipadas (`choice`, `score`, `noul`, `box`) sobre la foto de la mesa o de un área, más los objetos como estado. | JEV `decide()` (`engine/playground/jev.py` en `max/motor`), ver §1b. |
| `preset_leer(preset?)` | El manifiesto del preset (sus parámetros con su valor actual, sus acciones, sus eventos y su voz) más sus `datos` vivos (por ejemplo la pose del canasto o las dudas). | Lee `escenas/<preset>/preset.json` y el estado del conductor. |
| `preset_ajustar(param, valor)` | Cambia un parámetro en vivo: velocidad de las órbitas, ml objetivo, color de foco. | Evento `preset_ajustar` (ver §3). |
| `preset_accion(accion, args)` | Corre una acción declarada: `ir_a {planeta}`, `marcar_celda {celda}`, `responder {texto}`. | Evento `preset_accion` (ver §3). |
| Verbos comunes: `señalar(objeto)`, `nombrar(objetos)`, `explicar(texto, cerca_de)`, `medir(objeto, valor, unidad)`, `avisar(tono, texto)`, `callar()` | La gramática de la mesa (`MOTOR.md`) sobre **cualquier** objeto, del catálogo o de una escena, con el tema del preset activo. | `project`/`remove` con `anchor.object_id`, `base.css` + `tema.css`. Un preset puede tomar un verbo como suyo (en solar, `señalar` hace volar a Lumo). |

Un asistente nuevo es entonces: estas tools + la sección de voz del `escena.md` del preset + `preset_leer()` al
partir. Nada más.

**Tocar es uno solo** (nota de Max, 21-nov): el hand tracking de SAT en la consola (manos de MediaPipe) y el botón por
permanencia. En html, `data-press` sobre el elemento; al dejar el dedo quieto 400 ms llega `widget_pressed {widget_id,
value, point_mm}`. Ningún preset inventa otro toque. En solar, los blancos invisibles sobre los planetas impresos de la
lámina son `data-press` con `value` = el planeta.

## 1b. Entender en vivo con JEV

JEV (TypeSafe, «System One») contesta preguntas tipadas en ~200 ms con estado de texto. Con imagen va por
`/decision-model` (`decide()`), que acepta estado multimodal. Úsalo para lo que la geometría no ve; lo que la
percepción ya mide (posición, pose, ml) no se le pregunta.

```python
from playground import jev   # engine/playground/jev.py, rama max/motor
r = jev.decide(
    [{"type": "json", "value": {"objetos": objetos_de_scene, "manos": manos}},   # mesa_objetos + include_hands
     jev.parte_imagen(jpg_del_area)],                                             # mesa_ver(objeto), 280 a 1568 px por lado
    {"senala": {"type": "choice", "instructions": "¿Qué planeta del canasto está señalando la niña?",
                "criteria": {"saturno": "la bola de Saturno", "jupiter": "la bola de Júpiter", "ninguno": "no señala una bola"}}},
)
# r["answers"]["senala"] → {"choice": "saturno", "confidence": 0.91, ...}; r es None si no hay llave o red: seguir igual
```

| Escena | Pregunta que suma | Tipo |
|---|---|---|
| Solar | ¿Qué planeta del canasto señala la niña? (cuando dice «este» sin tocar la lámina) | `choice` con los planetas en la mesa |
| Solar | Si la niña pinta las bolas: ¿dónde está la bola roja? (resuelve las dudas de tamaño) | `box` |
| Laboratorio | ¿El vaso se ve rebalsado o derramado? | `noul` |
| Escritorio | ¿La persona está escribiendo en la libreta ahora? | `noul` |

- **Llave por referencia:** `AI_GATEWAY_API_KEY` del entorno; si no está, `jev.py` la lee solo de `JEV_ENV_FILE` y
  la deja en la memoria del proceso. Nunca va en un archivo del repo, un log ni un prompt.
- **Imagen:** cada lado entre 280 y 1568 px (fuera de rango se rechaza); un área chica se agranda antes de mandarla.
- **Si falla** (sin llave, red, plazo), `decide()` devuelve `None` y el asistente sigue con lo que ya sabe.
- No medido por mí: la latencia con imagen. Los ~200 ms son con estado de texto.
- No va dentro del `ScenePerceptor`: el contrato del motor prohíbe red en los módulos de percepción.

## 2. Lo que declara cada preset: `preset.json`

```jsonc
{
  "id": "solar",                       // = prefijo de sus objetos = nombre del ScenePerceptor = scene { name }
  "nombre": "Sistema solar",
  "para_que": "Una frase: qué hace la escena y para quién.",
  "css": ["../../motor/sistema/base.css", "tema.css"],
  "voz": { "persona": "escena.md#la-voz-lumo", "idioma": "es-CL" },

  "objetos": [                         // lo que publica en el mundo (sirve para mesa_ver y para anclar)
    { "id": "solar:<planeta>", "label": "Planeta", "que_es": "Una bola del canasto con su planeta" }
  ],

  "params": {                          // lo configurable en vivo, con tipo y rango; el agente no adivina
    "giro_s": { "tipo": "numero", "min": 4, "max": 120, "unidad": "s", "valor": 26,
                "que_es": "Segundos que tarda una vuelta de órbita", "aplica": "config:ajuste.giro_s" },
    "color_foco": { "tipo": "color", "valor": "#ffc21a", "aplica": "css:--sd-foco" }
  },

  "acciones": {                        // lo que el agente puede pedir; descripción escrita para un LLM
    "ir_a": { "que_hace": "Lumo vuela al planeta, lo marca y abre su ficha",
              "args": { "planeta": { "tipo": "texto", "opciones_de": "objetos:solar:" } } }
  },

  "eventos": {                         // lo que la escena avisa, con sus datos
    "canasto_movido": { "que_es": "Alguien movió el canasto", "data": ["theta_deg", "t_mm"] }
  },

  "efectos": {                         // widgets del preset y sus parámetros propios (opcional)
    "solar-orbitas": { "params": ["giro_s", "orbita_opacidad"] }
  }
}
```

Reglas:
- `aplica` dice dónde vive el valor: `config:<ruta>` (el json de ajustes de la escena), `css:<--token>` (el tema, en
  vivo) o `accion:<nombre>` (lo resuelve una acción). Así cualquier parámetro se cambia sin programar.
- Los tipos son pocos: `numero` (con `min`, `max`, `unidad`), `texto`, `bool`, `color`, `opcion` (con `opciones`),
  `objeto` (un id del catálogo, con `prefijo`), `punto_mm`, `rect_mm`, `lista`.
- `opciones_de: "objetos:<prefijo>"` dice que el valor sale del catálogo vivo (los planetas que hay en la mesa).
- Todo texto de `que_es` y `que_hace` está escrito para que un LLM lo use sin más contexto.

## 3. Cómo viaja un pedido (con las tools que ya existen)

```
agente ──run_flow{emit preset_accion}──▶ gateway ──flow_emitted──▶ conductor del preset (events_wait)
                                                                         │ hace la acción (project, voz…)
agente ◀──────────── flow_emitted preset_hecho {id, ok, resultado} ◀──────┘
```

- Pedido: `flow_emitted` con `event: "preset_accion"` y `data: {preset, accion, args, id}`, o `event:
  "preset_ajustar"` y `data: {preset, param, valor, id}`. Hoy se emite con `run_flow` de un estado final con una
  acción `emit`. Funciona igual desde la voz, desde Claude Code o desde un botón proyectado (`data-press`).
- Respuesta: `preset_hecho {id, ok, resultado | error}`. El agente sabe si se hizo y qué quedó.
- El conductor de cada preset escucha solo los pedidos con su `preset`. Si dos presets corren a la vez, no chocan.

Pendiente para simplificar (no bloquea): una tool `emit` directa en el gateway (hoy existe el comando `Emit` en
`pcx_contracts`, pero no está en `TOOLS`), y que `scene { name }` del motor active a la vez el `ScenePerceptor` y el
conductor del preset.

## 4. Lo mismo en las tres escenas

| | Solar | Laboratorio | Escritorio |
|---|---|---|---|
| `mesa_ver(objeto)` | `solar:lamina-0`: qué está tocando la niña | `lab:vaso-b`: cuánto líquido hay | `desk:libreta`: qué escribió Matilda |
| `preset_ajustar` | `giro_s`, `escala_planetas`, `color_foco` | `objetivo_ml`, `tolerancia_ml` | `max_lineas`, `color_tinta` |
| `preset_accion` | `ir_a`, `decir`, `nombrar`, `fijar` | `marcar_celda`, `siguiente_paso`, `repetir` | `responder`, `leer_libreta` |
| Eventos | `canasto_movido`, `solar_armado` | `nivel`, `contaminacion` | `libreta_escrita` |

Los nombres de laboratorio y escritorio son ejemplos: los fija quien lleva cada escena en su `preset.json`.

## 5. Quién hace qué

- **Cada preset** (los arma Matilda desde el 21-nov, 18:54): su `preset.json` y que su conductor atienda
  `preset_accion` y `preset_ajustar`. Solar trae el manifiesto de ejemplo; atender los pedidos en su conductor queda
  pendiente.
- **Voz (h24)**: las tools de §1 como client tools del agente de ElevenLabs, genéricas, leyendo el manifiesto del
  preset activo.
- **Motor (h19, Manu)**: `scene { name }` como interruptor único del preset; esta sección cabe en `MOTOR.md`.
