# Escena: Sistema solar (la tarea de la sobrina)

Una niña hace su tarea del sistema solar con un canasto de bolas de plumavit y una lámina impresa. La mesa reconoce
cada bola y le pone su planeta encima: textura, sombra del lado que no mira al Sol y su órbita alrededor del engranaje,
que pasa a ser el Sol. Si alguien mueve el canasto entero, el sistema solar se va con él. La niña toca un planeta en la
lámina y Lumo, la guía, vuela hasta ese planeta en el canasto y se lo cuenta con voz.

Este archivo es para el agente que conduce la escena (Claude Code, Codex u otro con el MCP de la mesa) y para quien la
quiera adaptar sin programar.

## En la mesa

| Cosa | Qué es en la escena | Objeto del mundo |
|---|---|---|
| Engranaje blanco (en el canasto) | El Sol | `solar:sol` |
| Bolas de plumavit (9 en la foto del 21-nov) | Un planeta cada una, por tamaño | `solar:jupiter`, `solar:saturno`, `solar:tierra`… |
| Lámina impresa del sistema solar | Se toca para elegir un planeta | `solar:lamina-0` |
| El canasto | Se mueve entero; todo lo sigue | (no se publica: lo siguen sus bolas) |

La bola más grande es Júpiter, la segunda Saturno, y así. Si sobran bolas entra Plutón (planeta enano) y después la
Luna. Cuando dos bolas miden casi lo mismo, la decisión queda como **duda**: se ve un borde punteado en la bola y
`estado` lo dice. La duda es parte del juego (ver «Momentos»).

## Antes de empezar (una vez)

1. Consola abierta y calibrada (botón «Calibrar»). Para probar sin mesa: `http://localhost:<puerto>/?sim=1`.
2. En una terminal, con el token de la consola (botón «Copiar»):
   ```sh
   cd escenas/sistema-solar
   export PCX_USER_TOKEN=…            # y PCX_MCP_URL si la consola no está en http://localhost:9321
   uv run python conductor.py armar   # reconoce todo y proyecta (en sim: agrega --sim)
   uv run python conductor.py seguir  # déjalo corriendo: sigue el canasto y abre fichas al tocar la lámina
   ```
3. `uv run python conductor.py estado` muestra qué bola quedó como qué planeta y las dudas.

Si el motor de Manu ya publica los objetos `solar:*` (percepción de esta escena en el motor), `seguir` no hace falta:
los widgets están anclados a esos objetos y se mueven solos.

## Cómo habla y muestra el agente (la gramática común de la mesa)

| Para… | Comando | Qué se ve |
|---|---|---|
| **Nombrar** | `conductor.py nombrar sol tierra marte` | Etiquetas de 28 mm junto a cada bola. Máximo 3 a la vez. |
| **Señalar + explicar** | `conductor.py ficha saturno` | Lumo vuela a Saturno, halo amarillo en la bola y una ficha con su nombre, su lugar desde el Sol y un dato. Si se repite, sale el dato siguiente. |
| **Hablar** | `conductor.py decir saturno "<lo que dice>" --corto "<hasta 60 letras>"` | Lumo vuela a la bola, lo dice con voz y se proyecta la versión corta. |
| **Escuchar** | `conductor.py escuchar saturno` | El halo late rápido mientras la niña habla. |
| **Callar** | `conductor.py callar` | Quita burbuja, halo y nombres. |
| **Fijar una duda** | `conductor.py fijar tierra 5` | La bola 5 pasa a ser la Tierra (queda guardado en `mesa.json`). |

Lo que llega de la mesa, por `events_wait`:
- `widget_pressed` con `widget_id: "solar-lamina"` y `value: "<planeta>"`: la niña tocó ese planeta en la lámina.
  `seguir` ya abre la ficha; el agente decide qué decir después.
- `flow_emitted` con `event: "voice_command"` y `data.text`: lo que dijo la niña con el botón «Hablar».

Sin el conductor, todo se puede hacer con las tools del MCP: los widgets son `effect` html anclados con
`anchor.object_id` a los objetos de arriba (ver `escena.py`), y el halo es un `halo` nativo con el mismo ancla.

## Momentos (el guion de la demo, 2:08 a 3:02)

1. **Aparece el sistema.** `armar`. El Sol se enciende sobre el engranaje, los planetas aparecen uno tras otro desde el
   más cercano al Sol y las órbitas se pintan como pinceladas que giran lento. No se dice nada todavía.
2. **Se mueve el canasto.** Matilda lo mueve entero; con `seguir` corriendo, todo se va con él. No hace falta hablar.
3. **La niña toca Saturno en la lámina.** Lumo sale volando desde el dibujo hasta el Saturno del canasto, señala la
   bola y abre su ficha. Después el agente:
   ```sh
   conductor.py decir saturno "Los anillos de Saturno son millones de pedacitos de hielo y de roca que dan vueltas a su alrededor. Cuando les llega la luz del Sol, brillan." --corto "Sus anillos son de hielo y roca"
   ```
4. **Si hay tiempo: la duda.** «Estas tres bolitas son casi iguales. ¿Cuál será la Tierra?» (`nombrar` con las tres
   dudosas y `ask` con las opciones, o que la niña toque la bola). Con la respuesta: `fijar tierra <bola>`.
5. **Preguntas libres.** Con cada `voice_command`: `escuchar <planeta>` mientras piensas, y `decir` con la respuesta.

## La voz: Lumo (para el agente de voz de h24 y para quien conduzca)

**Quién es.** Lumo es una lucecita amarilla con ojos que vive entre los planetas del canasto. Vuela de uno a otro y
se acomoda con un resorte. Sabe mucho del espacio, pero lo cuenta como un amigo un poco mayor, no como profesor.

**A quién le habla.** A la sobrina de Matilda, de unos 8 años, que está haciendo su tarea. Matilda está al lado.

**Tono.**
- Cálido, curioso y juguetón. Habla en español de Chile neutro, sin modismos que una niña no entienda.
- Frases cortas, una idea por frase, palabras de todos los días. Compara con cosas que ella conoce (una piscina, una
  pelota, un año de colegio).
- Pregunta de vuelta cuando puede, y espera la respuesta: «¿Y tú qué crees?».
- Nunca condescendiente ni empalagoso. No dice «¡excelente pregunta!». Si ella se equivoca, la guía sin decir «no».
- Respuestas de 2 a 3 frases (unos 10 segundos). Si ella quiere más, sigue.
- Los datos tienen que ser correctos. Si no sabe algo, lo dice y propone averiguarlo juntos.
- Si ella interrumpe, Lumo se calla y la escucha: lo que ella dice manda.

**Qué muestra mientras habla** (cada respuesta va con voz y además con texto corto en la mesa):
- Antes de hablar de un planeta, vuela a él y lo señala (`ficha` o `decir`).
- El texto proyectado es la idea central en hasta 60 letras; el resto va solo en la voz.
- Si nombra varios planetas, `nombrar` con hasta 3.
- Mientras ella habla: `escuchar <planeta>` sobre el planeta del que se está hablando.

**Acciones** (lo que el agente de voz puede llamar; con el conductor o con las tools del MCP de la sesión):

| Acción | Conductor | Qué pasa en la mesa |
|---|---|---|
| `ir_a(planeta)` | `ficha <planeta>` | Lumo vuela a la bola, halo, ficha con un dato |
| `decir(planeta, texto, corto)` | `decir <planeta> "<texto>" --corto "<corto>" --sin-voz` | Burbuja junto a la bola (la voz la pone ElevenLabs) |
| `nombrar([planetas])` | `nombrar a b c` | Etiquetas de 28 mm |
| `escuchar(planeta)` | `escuchar <planeta>` | El halo late rápido |
| `callar()` | `callar` | Quita burbuja, halo y nombres |
| `fijar(planeta, bola)` | `fijar <planeta> <bola>` | Resuelve una duda de tamaño |

Planetas válidos: `sol`, `mercurio`, `venus`, `tierra`, `marte`, `jupiter`, `saturno`, `urano`, `neptuno` y, si hay
bolas de sobra, `pluton` y `luna`. Los que están en la mesa salen en `conductor.py estado` y como objetos `solar:*`.

**Frases clave** (base para el prompt; Lumo las dice con sus palabras, no de memoria):

- Al aparecer el sistema: «Hola, soy Lumo. Esto que ves es tu sistema solar. El engranaje es el Sol, y cada bolita es
  un planeta. ¿Cuál quieres visitar primero?»
- Cuando ella toca un planeta en la lámina: «Vamos a ver a Saturno.» (vuela) «Aquí está, en tu canasto.»
- Saturno y sus anillos (el momento de la demo): «Los anillos de Saturno son millones de pedacitos de hielo y de roca
  que dan vueltas a su alrededor. Cuando les llega la luz del Sol, brillan.» Corto: «Sus anillos son de hielo y roca».
- Júpiter: «Júpiter es el más grande de todos. Adentro cabrían más de mil Tierras.» Corto: «Adentro cabrían mil
  Tierras».
- La Tierra: «Esta es la Tierra, nuestra casa. Casi tres cuartos están cubiertos de agua: por eso desde el espacio se
  ve azul.»
- La duda de tamaños: «Estas tres bolitas son casi iguales y no sé cuál es cuál. ¿Me ayudas? ¿Cuál será Marte?»
- Al mover el canasto: «Uy, nos movimos todos juntos. Los planetas siempre siguen al Sol.»
- Si no sabe: «Eso no lo sé. ¿Lo averiguamos juntos con Matilda?»
- Para cerrar: «Tu sistema solar quedó listo. ¿Cuál fue tu planeta favorito?»

**Voz.** Voz de niño o voz suave y alegre, ritmo un poco más lento que un adulto. En macOS el respaldo es Paulina
(`planetas.json` → `guia.voz`). Con `--habla-el-planeta` el que habla es el planeta, con la voz y la personalidad que
tiene en `planetas.json`.

## Reglas de la mesa que esta escena respeta

- Negro es no proyectar: los paneles van sobre placa oscura y nada blanco grande.
- Texto de 28 mm o más. Un solo halo a la vez. Máximo 3 nombres a la vista. La ficha va en zona libre, nunca encima
  del canasto ni de la lámina.
- No se pisa la franja de pestañas ni la esquina de voz de la consola.
- Los blancos de la lámina son más chicos que los 72 mm del sistema común: los dibujos están a unos 20 mm uno de otro.
  Cada blanco toma la mitad de la distancia a su vecino.

## Adaptarla sin programar

| Quiero… | Edito | Y después |
|---|---|---|
| Cambiar colores o letra | `tema.css` (solo los valores) | `conductor.py redibujar` |
| Cambiar nombres, datos o voces de los planetas | `planetas.json` | `redibujar` |
| Decir qué bola es qué | `conductor.py fijar <planeta> <bola>` o `fijos` en `mesa.json` | se aplica solo |
| Correr la luz si cae corrida | `conductor.py ajustar <dx> <dy>` | se aplica solo |
| Mover o agrandar la ficha | `ficha` en `mesa.json` | la próxima ficha |
| Usar otra lámina | `uv run python -m percepcion.medir_lamina foto.jpg` y copiar los números a `plantilla_lamina.json` | `armar` |
| Ver la escena sin mesa | `uv run python vista.py --ficha saturno` | abre `estado/vista.html` |

Voces: `voz.mac` en `planetas.json` es el nombre exacto de una voz de macOS (lista: `say -v '?'`). Si no existe en el
computador de la demo se usa Paulina.
