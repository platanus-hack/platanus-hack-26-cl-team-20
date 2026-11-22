# Escena 2 · Sistema solar en vivo

Escena lista. Capturas: `capturas/solar-0606-4k.jpg` (alguien toca Júpiter: se enciende su aro, la libreta escribe su
ficha, se marca en la lámina y Lumo está escuchando) y `capturas/solar-0606-platos.jpg`.

## Antes de encender

1. Mesa calibrada (`calibrar.py`) y consola `/mesa` como pestaña activa de su ventana.
2. Control corriendo: `doppler run -p platanus-hack-26 -c dev -- ../sl-scanner/.venv/bin/python mesa_ctl.py`.
3. La consola local necesita el cambio de `consola-local.patch` (permite al widget vivo leer `127.0.0.1:9302`); se
   aplica sobre `cloud/gateway/src/pcx_gateway/console/` del gateway local y se recarga la ventana del proyector.
4. El motor Rust detenido (apaga la proyección para su foto limpia).

Tres platos con bolas de plumavit y un engranaje (el Sol). La mesa sigue los platos en tiempo real: al moverlos o
girarlos, los planetas proyectados van con ellos.

- **Encender:** tecla **S** en el dashboard (o `curl "http://127.0.0.1:9301/start?escena=solar"`). **X** apaga.
- **Cómo sigue los platos:** no detecta cada bola (blanco sobre blanco es frágil). Detecta el círculo de cada plato y su
  punto negro, que da el giro. Cada bola tiene una posición fija dentro de su plato (`solar-modelo.json`).
- **Si cambia el montaje** (otras bolas u otro orden): con la mesa sin proyección, `solar_vivo.py --modelo`. Las bolas
  están anotadas en `ANOT` dentro de `solar_vivo.py`.
- **Tocar un planeta físico** (dedo quieto 0,3 s): se marca en la lámina impresa, la libreta muestra sus datos y Lumo,
  que vive en la libreta, los cuenta en voz alta (ElevenLabs; sin llave usa la voz del Mac).
- **Sol reactivo al sonido:** su brillo y sus rayos siguen el nivel del micrófono de la consola.
- **Plátano:** se blinda, le salen alas y enciende el propulsor (ciclo de 16 s).
- **Señal filtrada:** un plato quieto queda clavado; solo se actualiza si se mueve más de 3 mm o gira más de 3°.
- **Etiquetas:** el nombre de cada planeta va sobre la mesa, fuera del plato, unido por una línea.
- **Qué plato es cuál:** al encender se identifican por su aspecto; si hay duda, el del engranaje es el de más textura.
- **Logo:** el logotipo animado de Pablo (`marca/wui-logo.webp`, de sus videos de apertura y loop).

El widget vivo lee las poses desde `http://127.0.0.1:9302/poses`. Para eso la consola local permite a los widgets con
script conectarse solo a esa dirección (cambio en `html-layer.js` del gateway local, no está en `main`).

Datos y colores de los planetas: `planetas.json`, de la escena de Max.

## Hablar con Lumo

Lumo es un agente de voz de ElevenLabs propio de esta mesa: «WUI · Lumo (mesa v2.1)». Es una copia del Lumo de Max
(misma voz y modelo) con el prompt y las herramientas de este montaje; el de Max no se toca. Se crea o actualiza con
`doppler run -p platanus-hack-26 -c dev -- python lumo_agente.py` (el id queda en `out/lumo-agente.json`).

- **Partir:** mano sobre el botón «Hablar con Lumo» junto a la libreta; la conversación parte al tiro. El mismo botón corta.
- **Página:** `http://127.0.0.1:9325/` la sirve `solar_vivo.py` y se abre sola. Debe quedar abierta en su ventana.
- **Herramientas:** `ver_mesa` (qué planetas hay, en qué plato y a qué distancia del Sol), `senalar` (ilumina un planeta
  y lo marca en la lámina), `escribir_libreta`, `dibujar_libreta` (tamaños o distancias a escala) y `borrar_libreta`.
- **Toques:** si tocas un planeta con la conversación abierta, Lumo se entera y responde.
- **Transcripción:** lo último que dijo Lumo se proyecta en una franja del lado de la persona.
- **Una sola voz:** la escena no tiene voz ni música propias; solo habla el agente (evita acoples con el micrófono).

Pendiente: el fondo pintado de Max bajo la capa viva, y afinar el calce de
las bolas grandes al girar (la corrección por altura es una estimación).
