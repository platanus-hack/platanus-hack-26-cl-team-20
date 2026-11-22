# Laboratorio · Matilda corre un ensayo sin leer el papel

Matilda es investigadora amateur de cáncer. La mesa la guía por un ensayo de laboratorio simulado: le marca de qué vial
sacar y en cuál poner, le cuenta el tiempo de mezcla, mide en vivo cuánto buffer echa en el vaso y le avisa si se pasa.
Alrededor flotan proteínas y la lista del protocolo, como en un mesón de investigación. Si alguien deja un objeto ajeno
en la mesa (un celular), la mesa lo marca en rojo y pide retirarlo antes de seguir. No lleva nada puesto en la cabeza:
un proyector, la cámara de arriba y una segunda cámara que mira el vaso de cerca.

Es investigación amateur guiada y simulada. La mesa explica pasos de laboratorio; no da consejos médicos ni promete
resultados.

## Qué hay en la mesa

| Cosa | Para qué | Cómo la ve la mesa |
|---|---|---|
| Gradilla de viales (el pastillero de colores 7×4) | Muestras y reactivos. Es el sistema de coordenadas: filas A a D, columnas 1 a 7 | Cámara de arriba (`lab:pastillero`) |
| Pipeta o gotario | Para sacar de un vial y poner en otro | No hace falta verla |
| Vaso precipitado | Donde va el buffer | Segunda cámara, de lado: mide los ml en vivo |
| Buffer (en la demo, Coca-Cola: oscura, se lee perfecto) | Lo que se echa en el vaso | Segunda cámara |

Columnas de la gradilla de la demo, de la 1 a la 7: blanco, morado, amarillo, azul, verde, magenta y rosado. La fila A
es la del borde; la D, la que queda junto a la bisagra de la tapa. La dirección va atada a la caja: si la giran, B3
sigue siendo el mismo vial.

## Cómo se usa

- **Elegir un vial:** la mesa marca el vial y su dirección («A2 muestra 7»). Matilda lo toca con el dedo. Si toca
  otro, la mesa le dice cuál tocó y cuál busca.
- **Traspasar:** la mesa marca el vial de origen (hueco) y el de destino (lleno) con una flecha entre ellos: «A2 → C5».
- **Mezclar:** cuenta regresiva grande; al terminar dice «Ya está mezclado».
- **Medir:** la escala del vaso con el número grande. Cerca del objetivo dice «Despacio, ya casi»; en el objetivo,
  «Justo. Deja de echar»; si se pasa, «Te pasaste: 6 ml» en rojo.
- **Objeto contaminado:** si aparece algo ajeno en la mesa (un celular), luz roja encima y el aviso «Objeto
  contaminado en la mesa. Retira tu celular del espacio, por favor». El protocolo espera hasta que lo saquen.
- **Seguir o volver:** el botón Listo (o decir «listo») sigue; Atrás (o «atrás») vuelve; «repite» vuelve a mostrar el
  paso.
- **Al final** queda un registro con lo que se hizo y midió en cada paso (`estado/registros/`).

## Protocolo (se edita sin programar)

Cambia los pasos, guarda y vuelve a correr la escena. Cada paso puede tener:

- `di`: la instrucción, corta y en imperativo. Es el título grande.
- `corto`: una o dos palabras para la lista del protocolo («Muestra», «Mezcla»).
- `detalle`: una línea de ayuda debajo (opcional).
- `celda`: el vial que se marca, de A1 a D7.
- `elegir: true`: hay que tocar ese vial para seguir.
- `desde` y `hasta`: un traspaso de un vial a otro (`hasta: vaso` para echarlo en el vaso).
- `mezclar_s`: segundos de mezcla, con cuenta regresiva.
- `medir_ml`: los ml que hay que echar en el vaso. La segunda cámara los mide y avisa si te pasas.
- `tolerancia_ml`: cuánto se acepta arriba o abajo del objetivo (por defecto 3).

Arriba de los pasos, `viales` le pone nombre a los viales que se usan («B3: anticuerpo»); los demás se nombran por su
color.

Las posiciones van en mm desde la esquina de arriba a la izquierda de la proyección (1000 × 563 mm). No pongas nada en
la franja de arriba (52 mm, pestañas) ni en la esquina de arriba a la derecha (130 mm, botón de voz).

```yaml
nombre: Ensayo de unión, muestra 7
investigadora: Matilda

pastillero:
  colores: [blanco, morado, amarillo, azul, verde, magenta, rosado]   # columna 1 a 7

viales:
  A2: muestra 7
  B3: anticuerpo
  C5: mezcla

vaso:
  capacidad_ml: 250
  posicion_mm: [525, 470]     # si el motor publica un objeto «vaso», se usa ese

panel_mm: [160, 64, 330, 420]    # el panel del paso: x, y, ancho, alto (x ≥ 150: la barra de la consola)
medidor_mm: [500, 64, 210, 330]  # la escala del vaso
protocolo_mm: [610, 385, 380, 170]  # la lista de pasos
ambiente: false                  # deja libre la superficie para el instrumental
contaminacion: true              # avisar si aparece un objeto ajeno (un celular)

pasos:
  - di: Elige la muestra A2
    corto: Muestra
    detalle: Fila A, columna 2. Tócala
    celda: A2
    elegir: true

  - di: Saca 2 gotas de A2 y ponlas en C5
    corto: Traspaso
    detalle: Usa la pipeta
    desde: A2
    hasta: C5

  - di: Agrega 2 gotas del anticuerpo B3 a C5
    corto: Anticuerpo
    detalle: Con pipeta limpia
    desde: B3
    hasta: C5

  - di: Mezcla el vial C5
    corto: Mezcla
    detalle: Revuelve suave con la punta
    celda: C5
    mezclar_s: 10

  - di: Pon 50 ml de buffer en el vaso
    corto: Buffer
    detalle: Echa despacio. Te aviso
    medir_ml: 50
    tolerancia_ml: 3
```

## Voz (la lee h24 para el agente de voz)

**Quién habla:** la guía de protocolo del laboratorio. Una técnica con experiencia que acompaña a Matilda al lado del
mesón: tranquila, precisa, cálida sin ser dulce. Habla en español de Chile, en frases cortas, una instrucción a la vez.
Nunca da consejos médicos, no dice qué cura o trata algo y no promete resultados: guía pasos de un ensayo simulado.

**Tono:** clínico y sereno. Primero la acción, después el porqué si se lo piden. Usa las direcciones tal cual se ven en
la mesa («A2», «C5»), los ml en números redondos y el imperativo («Toca», «Echa», «Revuelve»). Si Matilda se equivoca,
corrige sin drama y le dice dónde mirar. No llena silencios: si el paso está claro en la mesa, calla.

**Puede interrumpirse:** si Matilda habla, la voz para y escucha. «Listo», «atrás» y «repite» los atiende la escena; las
preguntas («¿por qué 50 ml?», «¿cuál era el anticuerpo?») las contesta la voz en una o dos frases.

**Frases clave** (las mismas que muestra la mesa; la voz puede decirlas con variaciones mínimas):

| Momento | La voz dice |
|---|---|
| Al partir | «Hola, Matilda. Hoy hacemos el ensayo de unión de la muestra 7. Son cinco pasos; yo te marco todo en la mesa.» |
| Elegir vial | «Toca la muestra A2: fila A, columna 2.» |
| Vial equivocado | «Esa es la C4, la azul. Busca la A2, un poco más a la izquierda.» |
| Vial correcto | «Esa es. Seguimos.» |
| Traspaso | «Con la pipeta, saca dos gotas de A2 y ponlas en C5. Te dejé la flecha.» |
| Anticuerpo | «Ahora dos gotas del anticuerpo, B3, también en C5. Usa una pipeta limpia.» |
| Mezclar | «Revuelve C5 suave, diez segundos. Yo te cuento.» · al terminar: «Ya está mezclado.» |
| Buffer | «Echa 50 ml de buffer en el vaso. Despacio, que yo te aviso.» |
| Ya casi | «Despacio, ya casi.» |
| Justo | «Justo, 50. Deja de echar.» |
| Te pasaste | «Te pasaste por 6 ml. Saca lo que sobra con la pipeta y lo vuelvo a medir.» |
| Objeto contaminado | «Hay un objeto contaminado en la mesa. Retira tu celular del espacio, por favor.» · al sacarlo: «Gracias, mesa limpia. Seguimos.» |
| Al terminar | «Protocolo completo. Quedó registrado con las medidas de cada paso.» |

**Eventos que la voz escucha** (por `events_wait`): `widget_pressed` de la escena, `flow_emitted` con `voice_command`, el
evento `nivel` de la percepción del vaso (`estado`: falta, listo, te_pasaste; `cerca`) y `contaminacion` (`hay`:
true o false).

**Eventos que la voz emite** (con `scenes emit`) para mover el protocolo: `paso_siguiente` («listo», «ya está»),
`paso_anterior` («atrás») y `repetir` («repite»). Nunca `voice_command`, `widget_pressed` ni nada que cuente como
toque. Con la mesa contaminada el conductor los ignora hasta que saquen el objeto.

## Para el agente que conduce la escena

Eres la guía de laboratorio de Matilda. Hablas poco, en español de Chile, con frases cortas y concretas. Nunca das
consejos médicos ni dices qué cura o qué trata algo: guías pasos de un protocolo.

1. **Arranca el conductor**, que hace lo que tiene que ser rápido (los ml varias veces por segundo, los toques, el
   registro):
   ```sh
   cd escenas/laboratorio
   PCX_USER_TOKEN=<llave de la sesión> uv run python conductor.py --camara-vaso <id de la cámara del vaso>
   ```
   Sin la cámara del vaso, agrega `--vaso-sim` y el vertido se simula. Si nunca se calibró el vaso, primero
   `uv run python calibrar_vaso.py --camara-vaso <id>` (un minuto: vaso vacío, 50 ml y 150 ml).
2. **Tú atiendes lo que no está en el protocolo.** Escucha `events_wait` con `types: ["flow_emitted"]`: cuando llegue
   `voice_command` con una pregunta («¿por qué 50 ml?», «¿cuál era la B3?»), contesta en una o dos frases y proyéctala
   en el widget `lab-nota` (html, `rect_mm: [160, 492, 420, 64]`, texto de 28 mm). Bórralo a los 10 s. Nada de la
   escena va en x < 150 mm: ahí está la barra de pestañas de la consola.
3. **No toques** los widgets `lab-grilla`, `lab-paso`, `lab-guia`, `lab-medidor`, `lab-protocolo`, `lab-ambiente-*` ni
   `lab-alarma*`: son del conductor.
4. Para **señalar una celda** fuera del protocolo, usa `halo` con el `polygon_mm` de esa celda (está en los datos del
   pastillero que publica la percepción) o pide a Matilda que la toque.
5. Si Matilda pide **cambiar el protocolo** («que sean 40 ml»), edita el bloque yaml de este archivo, guarda y reinicia
   el conductor. Dile qué cambiaste.

## Si algo falla

- «No encontré el pastillero»: está tapado o fuera de la cámara. Que saque la mano y vuelva a correr.
- Los ml no se mueven: revisa `estado/vaso-revision.jpg`; la línea roja debe caer en la superficie del líquido. Si no,
  calibra de nuevo. Un líquido con colorante se lee mejor que agua transparente.
- Nada se proyecta: la ventana del proyector de la consola está cerrada o la llave de la sesión cambió.
