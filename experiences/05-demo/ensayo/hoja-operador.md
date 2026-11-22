# WUI · Hoja del operador · demo de 3 minutos (v2)

Estado: borrador para el ensayo; nada probado en la mesa.
Actualizado: domingo 22-nov-2026, 04:30.
Responsable: Matilda Roberts.


22-nov. **✅ = comando y mensajes leídos en el código** (`max/escenas-a-main`, 11d1dd9) · **⚠️ = solo se sabe en la mesa.** Nada se ejecutó: ni las escenas ni la mesa. Funciona **sin parches**. Fuentes: `revision.md`.

## 0 · Antes de empezar

- **Base:** la carpeta `escenas/` de `max/escenas-a-main` contra la consola local de `main` (v1). Sistema solar por el **camino A** (con `seguir`). No exportar `PCX_SIN_OBJETOS` ni `PCX_CAMARA`.
- **Consola (Chrome): ventana entera a la vista, nunca minimizada ni tapada.** Tapada, los toques no entran. No tocar cables ni cámaras después del último ensayo (salvo la cámara de lado, si es una sola: va de marca a marca).
- **5 terminales:** **T1** escritorio · **T2** solar `armar` · **T3** solar `seguir` · **T4** laboratorio · **T5** voz y respaldos (carpeta `sistema-solar`).
- En **cada** terminal: apretar «Copiar» en la consola y pegar esto (la dirección y el puerto salen solos; el token nunca se muestra): ⚠️

```sh
cd [CARPETA]/escenas/sistema-solar     # T1: …/escritorio   T4: …/laboratorio
export PCX_MCP_URL="$(pbpaste | grep -Eo 'https?://[^ "]*/mcp' | head -1)"
export PCX_USER_TOKEN="$(pbpaste | sed -n 's/.*Bearer \([^" ]*\).*/\1/p' | head -1)"
echo "url=$PCX_MCP_URL token=${#PCX_USER_TOKEN} caracteres"     # debe salir la URL y un número mayor que 0
```

## 1 · Preparación (mismo orden del ensayo: laboratorio, sistema solar, escritorio)

| # | Dónde | Comando | Está listo si dice… | Si falla |
|---|---|---|---|---|
| 1 ✅ | T2 | `uv run python conductor.py captura /tmp/demo` | `cámara=cam-N calibrada=True` | `False`: botón «Calibrar» de la consola y repetir |
| 2 ✅ | T2 | `uv run python conductor.py pestana Escritorio`, después `… pestana Laboratorio` y después `… pestana "Sistema solar"` | `pestaña activa: …` | Revisar el `echo` de arriba |
| 3 | — | T5: `python3 [RUTA]/cifras/cargar.py --crear` (copiar antes la carpeta `cifras` a este computador; con T3 y T4 detenidos). Debe terminar con `LISTO: todas las cifras están en su pestaña.` Ver `cifras/como-cargar.md` | — | Sin cifras la demo corre igual |
| 4 ⚠️ | T2 | `uv run python conductor.py --camara cam-3 captura /tmp/lat` y abrir `/tmp/lat/*-camara.jpg` | En la foto se ve la libreta (o el vaso) de cerca. Anotar **LIB = cam-__** (la que ve la libreta; tiene que ser `cam-3` o se cambia en `escena.json`, línea 10) y **VASO = cam-__** (la que ve el vaso). Si hay una sola cámara, son la misma y se mueve entre dos marcas de cinta: «libreta» y «vaso» | Probar `cam-1`, `cam-2`, `cam-4`. Si LIB no es `cam-3`: cambiarlo en `escritorio/escena.json`, línea 10 |
| 5 ✅ | T4 | `uv run python calibrar_vaso.py --camara-vaso VASO` · Enter con vaso vacío · Enter con 50 ml · Enter con 150 ml (Coca-Cola) | `Listo: …vaso.json` y `Ahora leo 150 ml` (aprox.) | `No pude calibrar`: repetir con líquido oscuro. No mover vaso ni cámara después |
| 6 ✅ | T4 | Pestaña Laboratorio activa, objetos puestos, manos fuera: `uv run python conductor.py --escena [RUTA]/laboratorio-demo-2-pasos.yaml --camara-vaso VASO --sin-contaminacion` | `pastillero: centro …` · `paso 1/2: Elige la muestra A2` · al tocar: `toque: lab-grilla A2 (dwell)` · al servir: `… ml → te_pasaste`. Salir: Ctrl-C | Si dice `uso la cámara del vaso simulada`: **parar**, falta `--camara-vaso` (los ml serían inventados) |
| 7 ✅ | T2 | `uv run python conductor.py pestana "Sistema solar"` y `uv run python visual/conductor_visual.py armar` | `Sol: engranaje en …` · `bola N: Saturno …` · `Láminas: 1` | `No veo bolas`: canasto entero bajo la cámara, a más de 15 cm del borde izquierdo |
| 8 ✅ | T3 | `uv run python visual/conductor_visual.py seguir` · mover el canasto · tocar Saturno · Ctrl-C | `siguiendo el canasto…` · `canasto:+…°` · `lámina: tocaron saturno` · `listo` | Nunca `limpiar` antes de `armar` |
| 9 ✅ | T1 | `uv run escritorio.py leer` | `"estado": "igual"` o `"nuevo"` | `sin_libreta` o error: la cámara LIB no ve la libreta (paso 4) |
| 10 ✅ | T1 | `uv run escritorio.py vivo --fijas` · escribir «¿endpoint cobros?» · tocar «Responde» (**a la izquierda** de la libreta) | `modelo listo en 0.0 s` · `escena andando` · `disparo: boton` · `leí «(respuesta fija)» → ['/v1/cobros', …]` | **Ctrl-C y relanzar después de cada prueba** (si no, la demo parte en la respuesta 2) |

**11 ⚠️ · Voz de Lumo (T5, una sola línea; después del paso 8).** Solo suena en un Mac, por su parlante: Lumo vuela, se oye la voz y queda la frase corta. Sin sonido: Max dice la frase.

```sh
uv run python conductor.py decir saturno "Los anillos de Saturno son millones de pedacitos de hielo y de roca que dan vueltas a su alrededor. Cuando les llega la luz del Sol, brillan." --corto "Sus anillos son de hielo y roca"
```

Con el corte 1, dejar escrita esta en vez de la anterior: `uv run python conductor.py decir saturno "Los anillos de Saturno son millones de pedacitos de hielo y de roca que dan vueltas a su alrededor." --corto "Sus anillos son de hielo y roca"`

**Dejar así justo antes de la demo:** libreta en página limpia · T1 `vivo --fijas` recién relanzado · después, pestaña **«Inicio»** activa (T1 dice `escena activa: Inicio`); si «Inicio» no existe, se parte en «Escritorio» · T2 con `armar` escrito, sin Enter · T3 con `seguir` escrito, sin Enter · T4 con el comando del paso 6 escrito, sin Enter · T5 con la voz escrita, sin Enter. · **Pestaña «Laboratorio» limpia:** después de cada pasada completa, con el pastillero todavía en la mesa: clic en «Laboratorio» → T4 ↑ Enter → esperar `paso 1/2` → T5 `uv run python conductor.py pedir paso_siguiente` → esperar `paso 2/2` → Ctrl-C en T4. Mirar que la pestaña quede solo con «1 de cada 5»

## 2 · Durante la demo

Cambiar de pestaña = **un clic en la pestaña, en la consola** ⚠️. Respaldo ✅ (T5): `uv run python conductor.py pestana "Nombre"`.

| Cuando Max dice… | El operador hace | Debe verse | Si no pasa en 5 s |
|---|---|---|---|
| «**En la mañana**, su escritorio…» | Clic en «Escritorio» | T1: `escena activa: Escritorio`. Panel «Tus agentes» y marco de la libreta | T1 cerrado: `uv run escritorio.py vivo --fijas` |
| «**Escribe una pregunta**…» | Mirar T1 | `disparo: boton` y `leí «(respuesta fija)»` (si leyó sola, solo sale `leí…`: también está bien). La respuesta se escribe en el papel | T5: `uv run python conductor.py pedir paso_siguiente` (T1: `disparo: agente`). Si la mesa dice «nada nuevo que leer», ya contestó |
| «**En la tarde** llega su sobrina…» | Clic en «Sistema solar». Con canasto y lámina puestos y **manos fuera**: T2 Enter. Al terminar: T3 Enter | T2: `Sol: engranaje en …`. T3: `siguiendo el canasto…` | Al entrar se ven los planetas del ensayo: es normal. `No veo bolas`: T2 ↑ Enter una vez; si no, quedan fijos |
| «Matilda **mueve el canasto**…» | Mirar T3 | `canasto:+…°` y los planetas lo siguen | Nada: Max salta la frase |
| «…toca Saturno, y Lumo vuela **a contárselo**» | Ver en T3 `lámina: tocaron saturno` y dar **Enter en T5** | Ficha de Saturno; Lumo vuela; voz y frase corta | A los 3 s sin ese mensaje: Enter en T5 igual. Sin sonido: Max dice la frase |
| «**En la noche**, Matilda investiga…» | **0.** (solo con una cámara de lado) el ayudante la pasa de la marca «libreta» a la marca «vaso». **1.** T3 Ctrl-C (`listo`), antes de que saquen el canasto. **2.** Clic en «Laboratorio». **3.** Con pastillero y vaso puestos y manos fuera: T4 Enter | T4: `pastillero: centro …` · `paso 1/2: Elige la muestra A2`. Celda A2 marcada | `No encontré el pastillero`: manos fuera, T4 ↑ Enter **una** vez. Si no: video |
| (Matilda toca el vial A2) | Mirar T4 | `toque: lab-grilla A2 (dwell)` · `paso 2/2: Pon 50 ml…` | A los 3 s, **solo si T4 no dice `paso 2/2`**: T5 `uv run python conductor.py pedir paso_siguiente`, una vez. Si T4 dice `protocolo terminado` (se avanzó dos veces): T5 `uv run python conductor.py pedir paso_anterior` |
| «Una cámara de lado **mide**…» | Mirar T4 | `… ml → listo` y luego `… ml → te_pasaste`. En la mesa, «Te pasaste» en rojo | No hacer nada: Max usa su plan B |
| «**Te acompaña en lo personal**…» (con el corte 2: «**Hagamos que el mundo**…») | **1.** Clic en «Cierre». **2.** Apenas se vea «+160 mil»: Ctrl-C en T4 (si T4 sigue vivo, dibuja su medidor encima del cierre). Nunca al revés | «+160 mil» | Queda el laboratorio: está bien |

## 3 · Si algo se cae

- **T1 repite `sin mesa, reintento:`**: no hacer nada, se recupera solo cuando vuelve la mesa (y vuelve a dibujar el panel). **Solo si T1 volvió al `$`** (pasa únicamente al arrancar, si la mesa no contesta): relanzar `uv run escritorio.py vivo --fijas` **solo si estamos en el escritorio**; al partir se roba la pestaña.
- **T4 volvió al `$`:** ↑ Enter (parte de nuevo en A2). `Falta la calibración del vaso`: video.
- **Planetas encima del laboratorio:** Ctrl-C en T3. Si quedan: T5 `uv run python conductor.py limpiar`.
- **Se cortó la mesa:** T1 (`sin mesa, reintento:`), T3 (`error en el ciclo … sigo` o `evento: …`) y T4 (`sin mesa, reintento cada 2 s`, o `error en la guía, sigo en 2 s`) se recuperan solos. Mesa negra: recargar la consola y relanzar la ventana del proyector. ⚠️

**Parches (ninguno hace falta para esta hoja):** 01, 03 y 05 ya los hizo Max · 02c (resto del 02b), al lanzar `seguir` no se abren fichas de toques viejos · 04, comando `oir` para oír el toque sin seguir el canasto (canasto fijo o camino B).
**Si Max deja el solar por el camino B** (motor ya corriendo): pestaña `solar` (minúscula), T2 lleva `export PCX_SIN_OBJETOS=1`, **no** se usa T3, y nadie oye el toque: al tocar Vale, en T5 `uv run python conductor.py ficha saturno` y luego la voz. ⚠️
