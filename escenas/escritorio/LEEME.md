# Escritorio del vibe coder

Escena de WUI («El mundo es tu interfaz»). Matilda, CTO y vibe coder, trabaja en su escritorio con una libreta al lado del teclado.
Escribe una idea o una pregunta a mano, y el agente le contesta en la misma libreta, con luz, al lado de lo que escribió.
A un costado, la mesa muestra lo que hacen sus agentes: la orquestación sale de la pantalla y se queda en el escritorio.

## El preset (para Pablo y Matilda)

- **`escena.md`**: la escena en palabras, para el agente que la conduce: qué importa en la mesa, qué proyectar al partir
  y qué hacer en cada paso, con los widgets listos para copiar.
- **`tema.css`**: el estilo tinta y papel sobre el sistema común (`MOTOR.md` + `motor/sistema/base.css`). Cada widget
  html lleva `base.css` + `tema.css` en `params.css`. Texto de 28 mm o más.

Con esos dos archivos y un agente conectado a la mesa por MCP, la escena corre. Lo de abajo (director, percepción) es
para correrla sola y para el motor.

## Qué se ve

- **Marcas de corte** en las cuatro esquinas de donde va la libreta, como en una imprenta, y un botón «Responde».
- Cuando Matilda escribe y saca la mano, aparece un **corchete de lápiz rojo** junto a lo que escribió («esto leí») y la
  palabra «leyendo».
- Unos segundos después, la **respuesta se escribe a mano con luz** en un hueco libre de la página, línea por línea.
- El panel **«Tus agentes»**: nombre, qué hace y desde cuándo. Un cursor que titila es un agente trabajando, un «?» rojo
  es uno que espera algo de ti y un «✓» es uno que terminó. La última fila es este mismo agente, en vivo.

Estilo: tinta y papel, editorial. Serif para leer, mono para horas y cifras, letra a mano para lo que escribe el agente.
Una sola tinta de acento, el lápiz rojo del editor.

## Cambiarla sin programar

Todo está en archivos de texto de esta carpeta:

| Quiero cambiar | Archivo |
|---|---|
| Dónde va la libreta, el panel o el botón | `escena.json` (medidas en mm de la mesa) |
| Cómo contesta el agente (tono, largo, qué hace con un nombre o una idea) | `voz.md` |
| Los agentes del panel | `agentes.json` (se relee solo cada pocos segundos) |
| Colores | `escena.json` → `colores` |
| Textos y forma de cada pieza | `plantillas/*.html` y `plantillas/estilo.css` |
| Si lee solo, con el botón o con la voz | `escena.json` → `disparo` |
| Respuestas para ensayar sin modelo | `escena.json` → `respuesta.fijas` (y `--fijas` al correr) |

Después de cambiar algo, vuelve a correr `montar` (o reinicia `vivo`).

## Correrla

```sh
cd escenas/escritorio
uv run escritorio.py vivo                       # con la cámara secundaria de verdad (escena.json → libreta.camara)
uv run escritorio.py vivo --libreta-sim salida/sim --fijas   # ensayo sin cámara ni modelo
uv run escritorio.py escribir "¿cómo le pongo a este endpoint?"   # «escribe» en la libreta sintética del ensayo
uv run escritorio.py vista                      # todo dibujado en salida/vista.html, sin mesa
uv run escritorio.py diagnostico --libreta-sim foto.jpg   # qué ve la percepción en una foto real (salida/diagnostico.jpg)
uv run pytest                                   # 39 tests
```

En el primer ensayo con la mesa: saca una foto con la cámara secundaria y corre `diagnostico`. La esquina «1» tiene que
quedar arriba a la izquierda tal como lee Matilda; si no, cambia `libreta.girar` en `escena.json` (1, 2 o 3 cuartos de
vuelta). Después ajusta `libreta.esquinas_mm` a donde quedó la libreta en la mesa (o deja que el motor la publique como
objeto «libreta»).

La conexión a la mesa sale del botón «Copiar» de la consola: `PCX_USER_TOKEN` (y `PCX_MCP_URL` si no es
`http://localhost:9323/mcp`). Para que un agente conduzca la escena a mano, ver `AGENTE.md`.

El modelo que lee la letra es un proceso de Claude Code que queda abierto (`pluma.py`): usa la sesión de quien lo corre,
sin herramientas, sin MCP, sin hooks y sin los CLAUDE.md de la máquina. Arrancarlo toma 12 a 20 s y se hace una vez al
partir `vivo`; después cada lectura toma lo que tarda el modelo.

**Requisito:** el CLI `claude` instalado y con sesión iniciada en el Mac donde corre `vivo` (probarlo antes con
`claude -p "hola"`). Si no está o no contesta al partir, `vivo` no se cae: sigue con las respuestas fijas de
`respuesta.fijas` (`PlumaFija`) y lo dice en el log con «AVISO: no pude abrir el modelo». Para ensayar sin modelo a
propósito: `--fijas`.

## Percepción (para el nodo de cómputo)

Funciones puras en `percepcion/`, imagen BGR de entrada y diccionarios de salida. Las coordenadas de la libreta van de 0
a 1 (u a lo ancho, v a lo alto) y son el marco común entre la cámara secundaria, la cenital y la mesa en mm.

| Función | Entra | Sale |
|---|---|---|
| `ubicar_libreta(foto, cerca_de=None, girar=0)` | foto de cualquier cámara | `quad_px` (4 esquinas en orden de lectura), `aspecto`, `paginas` (1 o 2, por el lomo) |
| `rectificar(foto, quad, tamano)` | foto y esquinas | la libreta vista de frente |
| `mascara_tinta(pagina)` | página rectificada | máscara de tinta; ignora renglones, margen rojo y la luz proyectada |
| `lo_nuevo(antes, ahora)` | dos máscaras | `hay_nuevo`, `caja` y `renglones` normalizados, corrimiento de la página |
| `espacio_libre(tinta, tamano, cerca_de, ocupado)` | máscara y tamaño pedido | hueco libre `[x, y, w, h]`, de preferencia debajo de lo escrito |
| `hay_mano(foto, quad)`, `movimiento(a, b)` | fotos | si hay una mano encima, cuánto se movió la escena |
| `Lector` | fotos sucesivas | junta todo y recuerda la última lectura (`mirar`, `confirmar`, `hueco`) |
| `px_a_mm(calib, puntos)`, `caja_rotada(quad_mm)` | calibración de `look` | mm de la mesa, como `pxToMm` de la consola; rect girado para un widget html |

Probado con libretas sintéticas (perspectiva, luz despareja, ruido, doble página, mano encima, luz proyectada encima) y
con la foto real de la mesa con una libreta sintética pegada. **No probado con fotos reales de la libreta**: no hay
todavía capturas de la cámara secundaria.
