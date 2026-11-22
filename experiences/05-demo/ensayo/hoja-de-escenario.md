# Hoja de escenario · WUI · demo de 3 minutos

Estado: borrador para el ensayo; nada probado en la mesa.
Actualizado: domingo 22-nov-2026, 04:30.
Responsable: Matilda Roberts.


Estado: versión corregida para el ensayo de las 06:00 (dom 22-nov-2026). Reemplaza a `experiences/05-demo/hoja-de-escenario.md`, que tiene el botón en el lado equivocado, el nombre antiguo y las frases antiguas.
Para qué: que todos sepan **qué objeto va dónde, quién lo cambia y con qué frase de Max**.
Base: `guion-3min-wui.md` (esta carpeta) y el código de las escenas de Max (commit `1b853ec`). Cada posición dice **[verificado]** con su fuente, o **[confirmar en el ensayo]**.
Medidas: mm de la mesa, desde la esquina de arriba a la izquierda (la zona proyectada mide 1000 × 563).

## Roles

| Quién | Qué hace | Dónde está |
|---|---|---|
| **Max** | Narra con micrófono | De pie, a un costado de la mesa |
| **Matilda** | Actúa en la mesa, sin hablar: escribe, toca y cambia los objetos | Sentada al escritorio |
| **Vale** (sobrina) | Deja el dedo quieto sobre Saturno en la lámina | Entra en el cambio 1, sale en el cambio 2 |
| **Operador** (por definir) | Cambia de pestaña, lanza la voz de Lumo y vigila que la mesa responda | En un computador al lado |
| **Ayudante** (por definir) | Pasa y recibe los objetos de cada escena | Junto a una mesa auxiliar |

## Antes de empezar

- [ ] **Calibrar** cámara y proyector justo antes. [confirmar en el ensayo: es un consejo de Manu]
- [ ] **Mesa limpia:** ningún celular ni objeto ajeno. El laboratorio detiene el protocolo si ve uno. [verificado: `laboratorio/escena.md:33-34`]
- [ ] **Pestañas «Inicio» y «Cierre» y las 4 cifras** cargadas (`cifras/cifras-final.json`).
- [ ] **Escritorio** lanzado con `--fijas`. [verificado que la opción existe: `escritorio/escritorio.py:526`]
- [ ] **Sistema solar:** `armar` (T2) y `seguir` (T3) probados en el ensayo y **escritos sin Enter**: se lanzan en el cambio 1; el comando `decir saturno` escrito en una terminal de un Mac con parlante. [verificado en código; sin probar en la mesa]
- [ ] **Laboratorio** probado y **escrito sin Enter** en T4 (se lanza en el cambio 2), con el protocolo de 2 pasos y la cámara real del vaso (`--camara-vaso <id>`). Sin esa opción usa un vaso **simulado**. [verificado: `laboratorio/conductor.py:708-710`]
- [ ] **Vaso calibrado** con `calibrar_vaso.py` (vacío, 50 y 150 ml). Sin calibración, el laboratorio no parte. [verificado: `conductor.py:715`]
- [ ] **Los tres juegos de objetos** en la mesa auxiliar, en orden. Coca-Cola sin gas (la espuma confunde la lectura; opinión, sin probar).
- [ ] **Videos de respaldo** de cada escena. Hoy no existen.

## Zonas que hay que dejar libres

- **Franja izquierda, 150 mm:** pestañas de la consola. [verificado: `escritorio/escena.json:59`; `sistema-solar/CORRER-EN-MESA.md:49`]
- **Esquina de arriba a la derecha, 130 mm:** botón de voz. [verificado: `laboratorio/escena.md:56-57`]
- El `escena.md` del laboratorio habla además de una franja de 52 mm arriba para pestañas. [confirmar en el ensayo dónde están las pestañas en la consola del día]

## Escena por escena

### Inicio y escritorio · 0:00–0:58

**En la mesa desde el inicio:** laptop, teclado, mouse, libreta y lápiz.

| Objeto o proyección | Dónde | Estado |
|---|---|---|
| Libreta | x 600–750, y 262–472 (150 × 210 mm, tamaño A5), a la derecha del centro | [verificado: `escritorio/escena.json:8`]. Si el motor reconoce la libreta, usa su posición real. Otra libreta más grande: [confirmar en el ensayo] |
| Panel «Tus agentes» | x 170–410, y 280–455: a la izquierda | [verificado: `escena.json:60`] |
| **Botón «Responde»** | **A la IZQUIERDA de la libreta**, pegado a su borde, arriba: x 560–594, y 274–288 aprox. Mide 34 × 14 mm | [verificado: `escena.json:18` y `escritorio/dibujo.py:115-141`]. El `escena.md` de Max dice otro lado; manda el código |
| Laptop, teclado y mouse | Fuera del panel (x 170–410) y de la libreta | [confirmar en el ensayo]: el código no les fija lugar |
| Cifra «+56 %» | Sobre el panel | [confirmar en el ensayo: depende del archivo de cifras] |

**Cámara secundaria:** `cam-3` tiene que mirar la libreta. Con `--fijas` la mesa no interpreta la letra, pero igual necesita ver tinta nueva para contestar. [verificado: `escena.json:10`, `escritorio/escritorio.py:276-278`, `pluma.py:139-160`]. Lápiz oscuro y grueso. [opinión]

**Señales:** «En la mañana» → el operador cambia a «Escritorio». «Escribe una pregunta» → Matilda escribe «¿endpoint cobros?» de corrido, sin sacar la mano de la libreta; apenas termina toca «Responde» (dedo quieto) y saca la mano antes de 4 s.

**Ojo, la libreta también se lee sola:** con tinta nueva y la mano fuera unos 0,8 s, la mesa contesta sin botón. [verificado: `escena.json:21-28`, `escritorio.py:394-411`]. Si Matilda levanta la mano a media pregunta, salen dos respuestas (la segunda, «parte por el 422 / y después el retry»). Si toca el botón cuando ya leyó sola, sale el aviso «nada nuevo que leer» y la respuesta queda. [verificado: `escritorio.py:276-278`]. El escritorio se lanza justo antes de la demo, para que la primera respuesta sea «/v1/cobros…». [verificado: `pluma.py:139-160`]

### Cambio 1 · escritorio → sistema solar · 0:58–1:18

| Sale | Entra | Quién |
|---|---|---|
| Laptop, teclado, mouse, libreta | Canasto con las bolas y el engranaje (Sol) · lámina impresa del sistema solar | Matilda saca y pone; el ayudante recibe y pasa |

**Señal:** «En la tarde». El operador cambia a «Sistema solar». Max sigue hablando.

### Sistema solar · 1:18–1:43

| Objeto | Dónde | Estado |
|---|---|---|
| Canasto (9 bolas y el engranaje) | Entero bajo la cámara y a más de 150 mm del borde izquierdo. Ponerlo al centro es la propuesta | [verificado el requisito: `CORRER-EN-MESA.md:48-50`; 9 bolas según `escena.md:16`] |
| Lámina | Al lado del canasto, sin taparse | [confirmar en el ensayo]: el código no fija lugar |
| Ficha de Saturno (360 × 262 mm) | Sale en el espacio libre más cercano a la lámina: dejar un hueco | [verificado: `sistema-solar/mesa.json`, `ficha`] |

**Señales:** «Matilda mueve el canasto» → desliza y gira, lento. «La sobrina toca Saturno» → Vale deja el dedo quieto sobre Saturno en la lámina. «…a contárselo» → el operador da Enter en T5 (`decir saturno`) (el toque no hace hablar a Lumo). [verificado: `conductor.py:1113-1117`]

### Cambio 2 · sistema solar → laboratorio · 1:43–2:08

| Sale | Entra | Quién |
|---|---|---|
| Canasto y lámina | Pastillero de colores 7×4 · vaso · Coca-Cola (el «buffer») | Matilda saca y pone; el ayudante recibe y pasa |

**Señal:** «En la noche», cuando Lumo terminó. El operador cambia a «Laboratorio». Max sigue hablando.

### Laboratorio · 2:08–2:32

| Objeto o proyección | Dónde | Estado |
|---|---|---|
| Pastillero | Columnas 1 a 7: blanco, morado, amarillo, azul, verde, magenta, rosado. Fila A: la del borde. Fila D: la de la bisagra. El A2 es el **morado de la fila del borde** | [verificado: `laboratorio/escena.md:21-23`]. La mesa lo busca donde esté; su lugar exacto: [confirmar en el ensayo] |
| Vaso | Cerca de x 525, y 470 (abajo, al centro), donde lo vea la cámara de lado | [verificado: `conductor.py:98`] |
| Panel del paso | x 160–490, y 64–484 | [verificado: `laboratorio-demo-2-pasos.yaml`, `panel_mm`] |
| Escala del vaso | x 500–710, y 64–394 | [verificado: mismo archivo, `medidor_mm`] |
| Lista del protocolo | x 650–990, y 385–555 | [verificado: mismo archivo, `protocolo_mm`] |
| Cámara de lado | Mirando el vaso | [confirmar en el ensayo]: si es la misma `cam-3` del escritorio, alguien la mueve en el cambio 2 |

**Señales:** «WUI le marca qué vial tocar» → Matilda toca el A2, dedo quieto. «Una cámara de lado» → echa la Coca-Cola. «y si se pasa» → llega a unos 60 ml y para.

**Por qué 60 ml:** «Te pasaste» sale al pasar de 54 ml (50 + 3 de tolerancia + 1 de margen) en 2 lecturas seguidas. [verificado: `conductor.py:357`, `percepcion/vaso.py:250-287`]

### Cierre · 2:32–2:51

«Tres momentos» → Matilda deja la botella y se queda quieta; no despeja. «Te acompaña en lo personal» → el operador cambia a «Cierre».

## Riesgos que hay que resolver en el ensayo

| Riesgo | Por qué | Qué hacer |
|---|---|---|
| **La libreta contesta dos veces o a destiempo** | Lee sola si la mano sale de la libreta unos 0,8 s | Escribir de corrido y tocar «Responde» de inmediato. En el ensayo, probar a tocar con la otra mano dejando la que escribe sobre la libreta |
| **Lumo no habla** | El toque abre la ficha, pero la voz sale con un comando aparte y solo en un Mac | Operador con `decir saturno` listo; si falla, Max dice la frase |
| **El toque en Saturno no abre nada** | Solo se atiende si corre `seguir` (`conductor.py:1138`) | Confirmar cómo se lanza el sistema solar; el comando `decir` sirve igual |
| **Una sola cámara secundaria para dos escenas** | El escritorio usa `cam-3`; el laboratorio recibe la suya al lanzarlo | Definir si se mueve y quién |
| **Laboratorio con ml simulados** | Sin `--camara-vaso`, los ml los inventa una simulación | Revisar la línea de lanzamiento |
| **Objeto ajeno en el laboratorio** | Detiene el protocolo | Mesa limpia, o lanzar con `--sin-contaminacion` (`conductor.py:692`) |
| **Cambios de escena lentos** | 20 s y 25 s son estimados, sin cronometrar | Ensayar con reloj; Max espera a ver la mesa |
| **Algo no funciona en vivo** | Las escenas no se han probado juntas en la mesa | Videos de respaldo, y decir que es grabado |

## Por definir en el ensayo

- Quién es el operador y quién el ayudante.
- Explicarle a Vale su señal.
- Dónde quedan el laptop, la lámina y el pastillero para que nada proyectado caiga encima.
- Cronometrar los 3 minutos con los cambios incluidos.
