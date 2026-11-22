# Hoja de escenario: demo de 3 minutos

Estado: borrador para el ensayo de las 06:00.
Actualizado: domingo 22-nov-2026, 00:50 (revisado contra el código de los presets).
Los roles de operador, ayudante y pantalla grande, y quién cambia los objetos, son **propuestas**: el equipo no los ha definido.
Para qué: que todos sepan **qué objeto va dónde, quién lo cambia y cuándo**, para no perder tiempo en los cambios de escena.
Base: [guion de 3 minutos](guion-3min.md) y los presets de Max (PR #28, `max/escenas-a-main`).

## Roles

| Quién | Qué hace | Dónde está |
|---|---|---|
| **Max** | Narra con micrófono | De pie, a un costado de la mesa |
| **Matilda** | Actúa en la mesa, sin hablar: escribe, toca y cambia los objetos | Sentada al escritorio |
| **Operador** (por definir) | Cambia de escena en la consola y vigila que el agente responda | En un computador al lado, fuera de cámara |
| **Ayudante** (por definir) | Pasa y recibe los objetos de cada escena | Junto a una mesa auxiliar, fuera de cámara |
| **Sobrina** (Vale, amiga del equipo; hay que pedírselo) | Toca Saturno en la lámina | Entra solo en el sistema solar |
| **Pantalla grande** (por definir) | Muestra la mesa desde arriba con el enlace «ver» de la consola | Computador de la sala |

## Antes de empezar (checklist)

- [ ] **Recalibrar** cámara y proyector justo antes (la cámara se corre con el tiempo, lo advirtió Manu).
- [ ] **Mesa limpia:** ningún celular ni objeto ajeno. El laboratorio de Max detiene el protocolo si ve uno.
- [ ] **Internet** funcionando: la voz depende de servicios en línea.
- [ ] **El agente de cada escena** corriendo y respondiendo (la mesa no reacciona sola).
- [ ] **Los tres juegos de objetos** listos en la mesa auxiliar, en orden.
- [ ] **El enlace «ver»** abierto en la pantalla grande.
- [ ] **Laboratorio lanzado con la cámara real del vaso** (`--camara-vaso <id>`). Sin esa opción, el conductor de Max usa una cámara del vaso **simulada** y los ml no serían reales (`conductor.py`, líneas 708–710).
- [ ] **Vaso calibrado** antes de la demo con `calibrar_vaso.py` (niveles de 50 y 150 ml). Sin calibración, el laboratorio no parte (`conductor.py`, línea 715).
- [ ] **Videos de respaldo** de cada escena abiertos, por si algo falla. Hoy **no existen**: hay que grabarlos.

## Escena por escena

### Escritorio · 0:38–1:15

**En la mesa desde el inicio:** laptop, teclado, mouse y la libreta (A4 o cuaderno universitario: con letra de 28 mm caben unos 13 caracteres por línea). Lápiz.

**Dónde:** la libreta, a la derecha del centro (el preset la espera en x 600–750 mm, y 262–472 mm si el motor no la encuentra). El panel de agentes sale a su izquierda y el botón «Responde» a su derecha. Nada en la franja izquierda de 150 mm: ahí va la barra de pestañas de la consola (los presets de Max la descuentan, según lo que dijo Daniel a las 18:58).

**Cámara secundaria (C920):** apuntando a la libreta, de cerca.

**Señal para Matilda:** cuando Max dice «Escribe una pregunta en su libreta…», escribe y toca «Responde».

### Cambio 1 · escritorio → sistema solar

| Sale | Entra | Quién |
|---|---|---|
| Laptop, teclado, mouse, libreta | Canasto con las 9 bolas y el engranaje (Sol) · lámina impresa del sistema solar | Matilda saca y pone; el ayudante recibe y pasa |

**Señal:** Max dice «En la tarde llega su sobrina…». El operador cambia a la escena del sistema solar.

### Sistema solar · 1:15–1:55

**En la mesa:** el canasto al centro y la lámina al lado, sin taparse.

**Señales para Matilda:**
- «Matilda mueve el canasto…» → desliza y gira el canasto, lento.
- «La sobrina toca Saturno…» → la sobrina deja el dedo quieto sobre Saturno en la lámina.

### Cambio 2 · sistema solar → laboratorio

| Sale | Entra | Quién |
|---|---|---|
| Canasto y lámina | Pastillero de colores 7×4 (gradilla de viales) · vaso precipitado · Coca-Cola (el «buffer») · gotario o pipeta | Matilda saca y pone; el ayudante recibe y pasa |

**Pastillero:** columnas 1 a 7 de colores (blanco, morado, amarillo, azul, verde, magenta, rosado). La fila A es la del borde y la D la de la bisagra de la tapa.

**Vaso:** donde lo vea la segunda cámara, de lado (el preset lo espera cerca de x 525, y 470 mm).

**Cámara secundaria (C920):** ⚠️ ver «Riesgos»: en el escritorio miraba la libreta y aquí tiene que mirar el vaso de lado.

**Señal:** Max dice «En la noche, Matilda investiga cáncer…». El operador cambia a la escena del laboratorio.

### Laboratorio · 1:55–2:30

**Señales para Matilda:**
- «La IA le ilumina la celda que toca» → toca el vial iluminado (A2).
- «Una segunda cámara mide los mililitros…» → echa la Coca-Cola en el vaso, despacio.
- «…y si se pasa» → **se pasa a propósito** de 50 ml. El aviso «Te pasaste» aparece al pasar de 54 ml (50 + 3 de tolerancia + 1 de margen) y mantenerse ahí un instante (2 lecturas seguidas), así que conviene echar claramente de más, unos 60 ml.

### Cierre · 2:30–3:00

**Matilda:** se aparta y deja la mesa despejada. **El operador:** pantalla final (tres franjas, cifra y frase).

## Riesgos que hay que resolver en el ensayo

| Riesgo | Por qué | Qué hacer |
|---|---|---|
| **¿Una sola cámara secundaria para dos escenas?** | El escritorio usa por defecto `cam-3` para la libreta; el laboratorio recibe la cámara del vaso como opción al lanzarlo. Max dijo que la C920 es la lateral. Si es la misma cámara, hay que moverla en el cambio 2 y recalibrar el vaso | Confirmar con Max: ¿se mueve la cámara o hay dos? |
| **Laboratorio con ml simulados** | Si se lanza sin `--camara-vaso`, los ml los inventa una simulación | Revisar la línea de lanzamiento en el checklist |
| **Objeto ajeno en el laboratorio** | Un celular u otro objeto en la mesa detiene el protocolo | Mesa limpia, o lanzar el laboratorio con `--sin-contaminacion` |
| **Cambios de escena lentos** | Son 2 cambios con varios objetos cada uno | Ensayar los cambios con reloj; el guion deja unos 45 s de margen en total |
| **La mesa no reacciona sola** | Cada escena necesita su agente atento | El operador vigila que responda; si no, plan B |
| **Algo no funciona en vivo** | El motor y las escenas no se han probado juntos en la mesa | Videos de respaldo por escena, y decir que es grabado |

## Por definir en el ensayo

- Quién es el operador, el ayudante y quién maneja la pantalla grande.
- Confirmar con Vale que hace de sobrina y explicarle su señal (tocar Saturno).
- Si la cámara secundaria se mueve entre escenas.
- Cronometrar los 3 minutos con los cambios de objetos incluidos.
