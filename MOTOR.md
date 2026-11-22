# WUI · sistema común de la mesa

Un proyector y una cámara, sin nada puesto en la cabeza: lo que ve uno lo ve toda la pieza. Este documento es la
referencia de **cómo se ve y cómo se usa** cualquier cosa que se proyecte, para que las escenas (escritorio, sistema
solar, laboratorio y las que vengan) sean un mismo producto con estilos distintos.

Está escrito para quien arma una escena, sepa o no programar.

| Qué | Dónde |
|---|---|
| Reglas y tokens | [`motor/sistema/base.css`](motor/sistema/base.css) (5 KB; `base.min.css`, 3 KB) |
| Los tres estilos, lado a lado | [`motor/sistema/galeria.html`](motor/sistema/galeria.html): se abre directo desde el repo |
| Temas de las escenas | `escenas/escritorio/tema.css`, `escenas/laboratorio/tema.css`, [`motor/sistema/temas/sistema-solar.css`](motor/sistema/temas/sistema-solar.css) |
| Percepción de referencia (Python) | [`motor/`](motor/), con [`motor/PARA-MANU.md`](motor/PARA-MANU.md) |

El motor que corre en la mesa es el proceso local en Rust (ver `docs/arquitectura/MOTOR-LOCAL.md`). `motor/` en Python
queda de referencia y de respaldo; su estado está al final.

## Apertura y señales WUI

Al abrir la salida `projector.html` de la consola, el logo animado se proyecta al centro una vez y termina fijo
en la esquina inferior de la barra de escenas. La calibración y la foto limpia ocultan la firma.
La guía usa Instrument Sans y luz monocromática. El selector lateral identifica la escena activa con un punto.
En el Sistema Solar, planetas, órbitas, cielo y Lumo aportan color. El trazo parte al lado del planeta y revela
la pista en un área libre a los 0,42 · 0,88 · 1,28 · 1,72 s. Queda un guion quieto a la izquierda del texto.
Si el planeta sale de vista, se retira la señal y el mismo espacio muestra el aviso de recuperación.

## 1. Cinco verbos

Todo lo que una escena le dice a una persona se arma con cinco piezas. Si algo no cabe en una de ellas, primero se
intenta decir con las que hay.

| Verbo | Qué ve la persona | Cómo se hace | Regla |
|---|---|---|---|
| **Señalar** | Un anillo alrededor de la cosa: «esta» | Widget `halo` con `anchor.object_id`; dentro de un html, `.sd-foco` | Un solo foco a la vez. Sigue a la cosa si se mueve. |
| **Nombrar** | Una etiqueta corta pegada a la cosa | Widget `label`, o `.sd-etiqueta` | Tres palabras como máximo. |
| **Explicar** | Un panel con el paso de ahora | `.sd-panel` (con `.sd-paso` para «2 de 5») | En zona libre, nunca encima de un objeto. Un panel a la vez. |
| **Medir** | Un número grande con su unidad | `.sd-dato` | Cambia en vivo sin saltar de ancho. |
| **Tocar** | Dejar el dedo quieto: avanza el trazo del botón y dispara | `.sd-boton` con `data-press="nombre"` | 72 mm de alto o más. Siempre se ve que disparó. |

Dos cosas cruzan a las cinco:

- **Tono.** `data-tono="ok" | "alerta" | "peligro"` en cualquier pieza. Va en el color, en la forma (el borde engruesa)
  y en las palabras. Nunca solo en el color: «Te pasaste: saca 4 ml», no un número rojo.
- **Voz.** El botón de voz de la esquina es de la mesa. Lo que la mesa dice en voz alta también queda escrito.

Y una preferencia: **que confirme el mundo antes que un botón.** Si el paso es «pon la taza aquí», el paso se cumple
cuando la taza llega, no cuando alguien toca «listo».

## 2. Las reglas de la luz

Valen para todo tema, porque son del proyector y no del gusto.

1. **Texto de 28 mm o más.** Una sola regla de tamaño. 1 px de CSS es 1 mm de mesa, y bajo 28 mm no se lee de pie.
2. **Negro es no proyectar.** El fondo de todo es transparente. Lo que lleva texto va sobre una placa oscura. Una
   superficie blanca grande encandila y lava los objetos.
3. **Líneas de 3 mm o más.** Más finas se pierden en la textura de la mesa.
4. **Un solo color de acento por escena** (`--sd-foco`). Si todo brilla, nada señala.
5. **Nada tapa lo que la persona tiene en la mano.** La luz acompaña a la cosa desde el borde.
6. **Los primeros 150 mm de la izquierda son de la mesa.** Ahí va la barra de pestañas (hasta 40 escenas, paginadas).
   Ninguna escena pone nada en `x < 150`: todo `rect_mm` parte en 150 o más, y un widget anclado a un objeto que entra
   a la barra se corre hacia adentro. La esquina de voz también es de la mesa. `project` avisa con `reserved` si un
   widget pisa alguna de las dos.
7. **Lo que es de todas las escenas va en la capa global.** `scene: "*"` en `project`, `remove` y `scene` dibuja un
   widget en todas las pestañas (una marca, un aviso de la mesa). No es una escena: no aparece en la barra. Si un id
   existe en la escena y en la capa global, se ve el de la escena. Una escena no la usa para su propia interfaz, y la
   regla de los 150 mm vale igual ahí.

## 3. Tokens

Un tema es un archivo que redefine estos valores y nada más. Los que no toca quedan con el valor base.

| Token | Para qué | Base |
|---|---|---|
| `--sd-tinta` | Texto y trazos | `#f4f1ea` |
| `--sd-tenue` | Texto secundario, guías | `#9a968c` |
| `--sd-foco` | El acento: lo señalado, lo que se toca | `#ffd400` |
| `--sd-ok` · `--sd-alerta` · `--sd-peligro` | Los tres tonos | verde · ámbar · rojo |
| `--sd-placa` | Fondo de lo que lleva texto | negro al 62 % |
| `--sd-fuente` · `--sd-fuente-dato` | Letra de texto y de números | del sistema · monoespaciada |
| `--sd-texto` · `--sd-titulo` · `--sd-dato` | Tamaños (mm) | 28 · 44 · 72 |
| `--sd-trazo` · `--sd-radio` · `--sd-toque` · `--sd-aire` | Grosor, esquinas, alto mínimo de un botón, separación | 4 · 14 · 72 · 16 |
| `--sd-barra` | Ancho de la barra de pestañas de la mesa. No es de las escenas: ningún tema lo cambia | 150 |
| `--sd-rapido` · `--sd-permanencia` · `--sd-curva` | Tiempos y curva de movimiento | 160 ms · 400 ms |

Un tema puede subir `--sd-texto`, nunca bajarlo de 28.

Las fuentes tienen que estar en el computador de la mesa: el html proyectado no tiene red. Por eso cada tema lista
varias y termina en una genérica.

## 4. Tres estilos, un producto

| Escena | Estilo | Acento | Letra | Forma y movimiento |
|---|---|---|---|---|
| **Escritorio** | Tinta y papel, editorial | Rojo de lápiz de editor | Serif de libro; mono para horas y cifras | Esquinas rectas como una hoja. La respuesta se escribe a mano, a velocidad de mano. |
| **Sistema solar** | Óleo nocturno, para jugar | Amarillo cadmio | Redonda y más grande (32 mm) | Muy redondo. Todo llega con un resorte suave. |
| **Laboratorio** | Clínico y exacto | Verde agua de instrumento | Sans neutra; mono para ml | Esquinas casi rectas. Llega rápido y se asienta, sin rebote. |

Lo que una escena agrega fuera de los cinco verbos (la letra a mano del escritorio, las órbitas, la regla del vaso)
lleva su propio prefijo (`desk-`, `solar-`, `lab-`) y usa los mismos tokens. Así cambiar el tema lo cambia todo.

Para ver los tres juntos, abre `motor/sistema/galeria.html` en un navegador.

## 5. Armar un widget

Un widget html de cualquier escena:

```json
{ "type": "effect", "shader": "html", "id": "paso", "rect_mm": [620, 60, 340, 300],
  "params": {
    "css": "<base.min.css><tema.css de la escena>",
    "html": "<div class='sd-panel'><div class='sd-paso'>PASO 3 DE 5</div><h1>Líquido A al vaso B</h1><div class='sd-dato' data-tono='ok'>48<small>ml</small></div><div class='sd-boton' data-press='listo'><span>Listo</span></div></div>"
  } }
```

- `motor ui <escena>` imprime el `css` ya armado (base + tema) para pegarlo o cargarlo desde un script.
- `data-press="listo"` hace que dejar el dedo 400 ms emita `widget_pressed` con `value: "listo"`.
- Hoy el `css` viaja en cada widget (3 KB). **Propuesta para la consola:** que la capa html traiga `base.min.css` por
  defecto, y así una escena manda solo su tema. Es agregar ese texto a `BASE_CSS` de `html-layer.js`; mientras no
  esté, se manda completo.

## 6. Crear una escena

Una escena es una carpeta en `escenas/<nombre>/`:

| Archivo | Qué es | Quién lo escribe |
|---|---|---|
| `escena.md` | Qué pasa en la mesa, en palabras: qué objetos importan, qué muestra y qué dice el agente en cada paso | Cualquiera |
| `tema.css` | El estilo: solo tokens | Cualquiera, copiando uno de los tres |
| `percepcion/` | Opcional: lo que esta escena necesita ver y que el motor no trae (un nivel en ml, una grilla) | Quien programe |

El agente (Claude Code, Codex u otro con MCP) lee `escena.md`, mira la mesa con `look` y `scene`, y proyecta con
`project`. Los objetos le llegan reconocidos y seguidos, con nombre: ancla un halo a `anchor.object_id` y el halo va
con la cosa.

Lista corta para revisar una escena antes de mostrarla:

- [ ] ¿Todo el texto mide 28 mm o más?
- [ ] ¿Hay un solo foco a la vez y un solo panel a la vez?
- [ ] ¿Cada botón mide 72 mm de alto o más y se nota cuando dispara?
- [ ] ¿Cada aviso se entiende sin el color?
- [ ] ¿Algo proyectado tapa un objeto o la mano?
- [ ] ¿Algún `rect_mm` parte antes de `x = 150`? Esa franja es de la barra de pestañas.
- [ ] ¿Cambia entero al cambiar `tema.css`, sin colores escritos a mano en el html?

## 7. Percepción de referencia (`motor/`, Python)

Queda como referencia y como respaldo. Lo que hace y lo que está medido:

- **Cadena completa sin hardware:** `cd motor && uv run motor up --sim` levanta el gateway, una mesa simulada hecha con
  la foto real del 21-nov y el motor. Un agente por MCP ve los objetos con nombre, ancla un halo a la taza, la taza se
  mueve y llega `object_moved` a 4 mm de la verdad (`motor/docs/evidencia/sim-halo-sigue-taza.jpg`).
- **Cascada de decisión** (`cascada.py`): sobre las fixtures, error de 0,7 mm o menos al mover, girar, sacar y volver,
  con 35 a 70 ms de CPU por foto cuando no hace falta SAM.
- **Calibración por luz estructurada** (`luz.py`): gray code que entrega el mismo modelo de lente de la consola, a
  1 mm de la verdad en el sim.
- **Deriva del montaje:** detecta que la cámara se corrió (de golpe o de a poco) y corrige o recalibra.
- **Las tres escenas enchufadas:** Sol y planetas del canasto seguidos como grupo, lámina con cada planeta impreso,
  pastillero 7×4, nivel del vaso y libreta.
- **Con la mesa real:** `motor conectar` entra a una sesión de la consola como cliente MCP (`look {clean: true}` y
  `scenes {action: "objects"}`).

Lo que **no** está hecho o no está probado:

- Nada corrió en la mesa física. Todo lo medido es simulación con la foto real, y 23 tests.
- Los tiempos son de un Mac saturado por otros procesos. El encoder de SAM tarda cerca de 0,4 s en una máquina libre
  y acá llegó a varios segundos.
- La galería y los temas no se revisaron en un navegador ni en un proyector: el Mac no tenía memoria para abrir uno.
- Track-Anything no se evaluó desde acá (lo lleva otra unidad).
- La segunda cámara como sensor de altura de la mano (paralaje) está en el sim, sin usar en un toque real.
