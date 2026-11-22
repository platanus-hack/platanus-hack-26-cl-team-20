# Diagnóstico del tracking de objetos de la mesa

Sábado 21-nov-2026, 17:40. Ramas leídas: `origin/daniel/consola` @ 8c808dd (la desplegada ya no es fa0e29f: hay un commit
más, de las 16:55), `origin/max/motor` @ 01e3a0e, `origin/manu/plataforma` @ 8e7d213.

## Lo esencial

1. **SAM funciona bien.** El uso de MobileSAM es correcto en cada punto que pediste revisar y, con una caja buena,
   las máscaras calzan con los objetos (taza, pastillero, canasto, lámina). No hay que tocarlo.
2. **Lo que se ve como «SAM no funciona del todo» son otras tres cosas:**
   - **La cámara cenital se está moviendo.** Lo proyectado caía a ~21 mm de donde dice la calibración a las 17:18 y a
     ~31 mm a las 17:31. Son ~0,75 mm por minuto desde el `calibrate` de las 16:57. Cualquier halo queda corrido 2–3 cm,
     con o sin SAM.
   - **`pintura.py` convierte px → mm a mano** con una copia vieja de la homografía y sin el lente: con la calibración
     vigente eso da 23 mm de error mediano y hasta 196 mm. Trae además un parche fijo de −18 mm.
   - **Grounding DINO tiny trabaja pegado al umbral y se equivoca de nombre.** Con la escena quieta, 2 de 7 objetos
     entran y salen; la taza sale como «pastillero» y el pastillero de verdad se descarta.
3. **De los ~12 s del ciclo, 6,7 s son `sleep` del script**, 2,4–4,8 s son los modelos y 2,2–6,9 s (estimado) son cinco
   viajes al gateway (Render plan free, lento e irregular). El nodo navegador casi no cuesta: `look` 44 ms, `project` 1 ms.
4. **Ese diseño no baja de ~2,5 s** aunque se optimice todo: cada ciclo paga una foto de ~250 KB por Render más DINO y SAM.
5. **Camino recomendado a menos de 1 s: el seguimiento dentro del nodo navegador (JS), con DINO/SAM solo para dar de
   alta y nombrar.** Al soltar un objeto: 0,25 s de calma + ~0,38 s de apagón + menos de 0,05 s de cómputo ≈ 0,7 s, sin
   red en el camino. Son 4,5–5,5 horas de trabajo contando 1 h en la mesa (3–4 h de reloj con dos agentes en paralelo).

Qué es medido y qué no: los tiempos de modelos, la calidad de máscaras, la deriva de la cámara y los tiempos del nodo
están medidos. Los viajes de `look`/`project` por Render están estimados, porque no podía llamar tools sobre la mesa.
El tracker propuesto no existe: sus tiempos salen de medir las mismas operaciones con OpenCV sobre cuadros reales.

## 1. ¿SAM funciona bien?

### 1.1 Revisión del uso (clase `MobileSAM`, `engine/playground/appearance_prepare.py:137-179`)

La usan `tools/mesa-agente/pintura.py:5-6,83-85`, `sam_seguir.py:5-6,32-34` y `engine/scripts/seguidor_v4.py:220-226`.
Bajé el export público que espera (`Acly/MobileSAM`) y revisé entradas y salidas del ONNX.

| Punto | Código | Veredicto |
|---|---|---|
| Export | encoder `input_image [H,W,3] float` → `image_embeddings [1,256,64,64]`; decoder `sam_mask_decoder_single.onnx` | Calza con el modelo real |
| Lado largo 1024 | `:162-164` | Correcto |
| RGB, float 0–255, sin normalizar ni rellenar | `:164,166` | Correcto: este export normaliza y rellena adentro. Lo comprobé con variantes erradas a propósito: normalizar con media/desvío baja el calce de la máscara a 0,79; dividir por 255, a 0,54; rellenar a 1024×1024 da lo mismo (0,997) |
| Cajas y puntos al espacio 1024 | `:173` | Correcto (`resized/orig` por eje) |
| Caja como dos puntos con labels 2 y 3; positivos 1, negativos 0 | `:170-171` | Correcto; con caja no va punto de relleno |
| `mask_input` en ceros, `has_mask_input` 0, `orig_im_size` (alto, ancho) | `:175-176` | Correcto |
| Elección de máscara | decoder `single`; caja + centro son 3 puntos | Sale el token de máscara única, que es el indicado para cajas |
| Umbral de logits | `:179` (`> 0`) | Correcto |
| Contorno → polígono | `pintura.py:86-90`, `sam_seguir.py:35-39`: apertura 3×3, contorno externo mayor, `approxPolyDP` 1,5 px | Conserva ≥ 92 % de la máscara salvo en objetos tapados (ver 1.3) |

### 1.2 Lo que medí (M1 Pro, CPU, mismos filtros que `pintura.py`)

- **Calidad:** con las cajas de DINO, las máscaras de canasto, cucharas, bolas y la parte de la caja que no tapa el
  cable son ajustadas
  (`tracking/exp1-tal-cual-foto1.jpg`). Con cajas puestas a mano para lo que DINO no nombró bien, SAM segmenta bien la
  taza, el pastillero real (con su tapa transparente) y la lámina (`tracking/exp4-sam-con-cajas-correctas.jpg`).
- **Estabilidad:** entre las 4 fotos de la misma escena, con la misma caja, el calce de la máscara consigo misma es
  ≥ 0,98 (cucharas 0,91–0,93).
- **Tiempo:** encoder 0,39–0,48 s con 4 hilos (1,3 s con 1 hilo; 8 hilos no mejora), decoder 24 ms por caja. CoreML no
  carga este export.
- **Caja + centro contra solo caja:** misma máscara (≥ 0,98) en todo menos en las cucharas: 884 px contra 1 432 px
  (0,59). El punto en el centro de la caja cae fuera de un objeto delgado en diagonal.

### 1.3 Bugs y riesgos de SAM

| Sev. | Dónde | Qué pasa | Evidencia |
|---|---|---|---|
| Alta (latencia) | `engine/scripts/seguidor_v4.py:222` (`max/motor`) | `if getattr(self, "_sam_img", None) is not id(col):` compara identidad de enteros, siempre es verdadero: recalcula el encoder en cada caja | 0,4 s desperdiciados por caja; arreglo: `!=` |
| Media | `pintura.py:85`, `sam_seguir.py:34`, `seguidor_v4.py:384`, `node/crates/pcx-objects/src/tracker.rs:779` | Punto positivo fijo en el centro de la caja | Cuchara: la máscara pierde 38 % del área |
| Baja | `pintura.py:88`, `sam_seguir.py:37` | Solo se queda con el contorno mayor | La caja blanca, cruzada por un cable, pierde 30 % |
| Media, sin medir | todo el camino px → mm | El polígono es la silueta que ve la cámara (tapa y paredes), no la base apoyada; la homografía vale solo para el plano de la mesa | Con gran angular, un objeto de 10 cm lejos del centro se corre varios cm hacia afuera |

### 1.4 El problema de calidad está en DINO (`engine/scripts/detector_dino.py`)

El uso del modelo es correcto (procesador de HuggingFace, 800/1333, prompts unidos con «. »). Tarda 1,8–2,1 s por foto
en esta máquina; el README de `mesa-agente` declara 3–4 s en la de la mesa.

| Sev. | Dónde | Qué pasa | Evidencia |
|---|---|---|---|
| Alta | `sam_seguir.py:14` (umbral 0,30), `pintura.py:17` (0,24) | Los puntajes de casi todo están entre 0,25 y 0,50 | 6 cuadros en vivo, escena quieta, umbral 0,30: dos «planetas» presentes en 2/6 y 4/6. Con 0,24 las bolas pasan por 0,01–0,04 |
| Alta | DINO tiny con 9 prompts juntos | Nombres equivocados | Foto de las 14:23: la taza sale «a pill organizer» 0,25; «a coffee mug» 0,62 se va a unos platos de cartón en el basurero, fuera de la mesa; la plasticina sale «a pill organizer» 0,50 |
| Alta | `pintura.py:81` | «Un solo pastillero»: se queda con el de mayor puntaje | El pastillero real (0,30) se descarta y la grilla 7×4 se pinta sobre la plasticina (`tracking/exp1-tal-cual-foto1.jpg`) |
| Alta | `pintura.py:22`, `sam_seguir.py:25,28` | Polígono de la mesa fijo en píxeles del encuadre de las 14:23 | La cámara se reencuadró: hoy ese polígono cubre 25 % (18 % en `sam_seguir`) de la superficie y deja afuera los 6 objetos que hay en la mesa |
| Media | `console/app.js:181,200` | `look` reduce la foto a 1280 px aunque la cámara se pide a 1920×1080 (`app.js:99,114`) | A las 14:23 la mesa medía 0,57 px/mm; hoy 0,89 px/mm |
| Media | `pintura.py:14,69-71`, `sam_seguir.py:11` | No usan `look clean:true` (existe desde 8c808dd); `pintura` borra, espera 0,7 s y repone | La foto trae lo proyectado, que mueve los puntajes |
| Baja | `detector_dino.py:36` | La frase se asigna por palabras en común contando el artículo «a»; una etiqueta vacía cae en el primer prompt («a bowl») | Lectura de código |

Dato útil: **recortar a la mesa y bajar la entrada de DINO a lado corto 480** da 0,78 s en vez de 1,78 s y acierta 10 de
11 objetos de la foto (la taza sube a 0,47) contra 8 de 11 con el cuadro completo. Un prompt por pasada (14,5 s) y los
prompts genéricos («an object») no ayudan. El pastillero sigue confundido con la plasticina en todas las variantes:
los nombres no se arreglan con umbrales.

### 1.5 Geometría px → mm

| Sev. | Dónde | Qué pasa | Evidencia |
|---|---|---|---|
| Alta | `pintura.py:11-12` con `H.npy` (`:6`) | Convierte con la matriz sola. Con modelo de lente, `matrix_px_to_mm` vale para píxeles ya corregidos (`console/node.js` lo avisa en `lens_note`) | Con la calibración vigente (k1 −0,091): error mediano 23 mm, p95 138 mm, máximo 196 mm |
| Alta | `pintura.py:6` | `H.npy` es una copia guardada a mano; no se entera de un `calibrate` nuevo ni del reencuadre | La cámara se movió entre las 14:23 y las 17:18 |
| Media | `pintura.py:7,12` (`DY = -18.0`) | Parche fijo en y | Tapa a ojo la deriva de la sección 2, que crece con el tiempo |

`sam_seguir.py:41` hace lo correcto: manda `polygon_px` y el nodo aplica lente + homografía (`node.js:228-246`).

## 2. Observación en vivo (solo lectura, 17:18 y 17:31)

Me conecté dos veces al visor (`/view`) mandando solo el token; no pedí foto, cámara ni escena. 35 s y 40 s.

- **No había seguimiento corriendo:** `world.objects` y `widgets` vacíos en ambas capturas. No pude ver halos sobre
  objetos; medí en su lugar la interfaz proyectada (pestañas y botón «Hablar»), que tiene posición conocida en mm.
- **Lo proyectado no cae donde dice la calibración** (`tracking/vivo-04-…png`, `vivo-05-…png`, `vivo-01-…jpg`):

  | Hora | Pestaña «Principal» | Pestaña «planetas» | Botón «Hablar» |
  |---|---|---|---|
  | 17:18 | (−1, +25) mm | (−5, +19,5) mm | (−11, +21) mm |
  | 17:31 | (−2, +35) mm | (−6,5, +29,5) mm | (−15,5, +31,5) mm |

- **La causa es que la cámara se mueve.** Entre las dos capturas la imagen completa, piso incluido, se corrió
  (+1,2, −7,1) px a 960 de ancho (1 094 puntos coincidentes), que equivale a 10 mm sobre la mesa: lo mismo que creció
  el desfase. Extrapolando hacia atrás, el desfase era cero cerca del `calibrate` de las 16:57. Dentro de una misma
  captura, cuando pasa una persona, la imagen se mueve 1 px y gira 0,24°.
- **Efecto sobre la cascada de fotos limpias:** comparar el cuadro de las 17:18 con el de las 17:31 marca 9,3 % de la
  mesa como «cambiada» sin que nada se haya movido; compensando el corrimiento baja a 1,4 %
  (`tracking/vivo-03-deriva-camara-diff-1718-1731.jpg`).
- **La cámara no ve 7,1 % de la superficie proyectable** (una esquina queda fuera del cuadro).
- **Tiempos del nodo** (registro de actividad): `look` 44 ms, `look clean` 388 ms (381 ms apagado), `project`,
  `remove` y `scenes objects` 1 ms, `calibrate` con lente 30,5 s (102 de 144 marcas).
- **Video del visor:** 6–6,5 cuadros/s a 960 px (58 kB por cuadro), contra los ~9 que manda el nodo. Llega a 960 y no
  a 1280 porque había más de una persona mirando o porque bajó por atraso; no distingo cuál.
- Hay una franja clara de ~18 mm en el borde de arriba de la proyección, encima de las pestañas. En la foto de las
  14:23 esa franja dice «'Claude' started debugging this browser»: es la barra de Chrome de la automatización. No la
  pude leer hoy y no la relacioné con el desfase; conviene proyectar desde un perfil de Chrome sin extensiones.

## 3. Desglose de los ~12 s (`tools/mesa-agente/pintura.py`, argumentos por defecto)

| Paso | Línea | Tiempo | Origen del dato |
|---|---|---|---|
| `remove` de la pintura | `:69` | 0,3–1,1 s | estimado |
| `sleep` | `:69` | 0,7 s | código |
| `look` | `:70` | 1,0–2,5 s | nodo 44 ms medido; el resto estimado |
| `project` (reponer) | `:71` | 0,3–1,1 s | estimado |
| DINO | `:73` | 1,8–2,1 s aquí; 3–4 s en la mesa | medido / declarado en el README |
| SAM: encoder + 9 a 16 cajas | `:83-85` | 0,6–0,8 s | medido |
| `scene` | `:91` | 0,3–1,1 s | estimado |
| `project` | `:93` | 0,3–1,1 s | estimado |
| `sleep` | `:95` | **6,0 s** | código (`argv[3]`, por defecto 6) |
| **Total** | | **11,3–18,4 s** | Daniel mide ~12 s |

- **Sleeps: 6,7 s**, la mitad del ciclo.
- **Modelos: 2,4–4,8 s.**
- **Cinco viajes al gateway: 2,2–6,9 s.** Cada llamada lanza un proceso de Python nuevo (`pintura.py:10`; 0,05–0,16 s
  medido) y abre una conexión TLS nueva (`pcx.py:5-9`). El gateway corre en Render **plan free** en Virginia
  (`render.yaml:10-11`) y hace dos escrituras a la DB por llamada, tres en `look` (`gateway.py:317,333,458`).
- **Chile–Virginia no es el cuello:** TLS termina en Cloudflare Santiago (conexión lista en 30–110 ms) y `/health`
  responde en 0,20–0,30 s en total (0,15 s con conexión viva). Lo lento es el servicio: un archivo estático de 90 KB tarda 0,5–1,2 s y uno de
  23 KB entre 0,3 y 2,9 s. La foto de `look` pesa ~250 KB (340 KB en base64) y hace ese camino dos veces.
- `sam_seguir.py` no tiene sleeps: `look` + DINO + SAM + `project` ≈ 4–9 s.

**Piso de este diseño** con todo optimizado (conexión viva, DINO recortado 0,8 s, SAM 0,6 s): foto ≥ 0,6–1,5 s +
modelos 1,4 s + `project` 0,2–0,5 s ≈ 2,2–3,4 s.

## 4. Dónde corre la percepción: tres opciones para menos de 1 s

La idea común (es la de `seguidor_v4`): DINO y SAM no van en cada ciclo. Dan de alta y nombran una vez; después se
sigue con operaciones baratas sobre fotos limpias. Sobre cuadros reales de la mesa, rectificados a 1000×563, medí con
OpenCV nativo: rectificar 0,8 ms, diferencia + morfología (N0) 0,6 ms, correlación de una plantilla de 280×200 en una
ventana de 480×400 (N1) 1,6 ms, ORB de 800 puntos (N2) 5,6 ms. En JS o wasm será 5–10 veces más lento y sigue sobrando.

| | (a) En el nodo navegador | (b) Proceso Python local (`pcx_motor`) | (c) Nodo Rust local |
|---|---|---|---|
| Cuadros | Locales (`<video>`, 30 por segundo) | Por `look`: ≥ 1–2,5 s cada uno por Render. Abriendo la cámara: locales, pero comparte la webcam con Chrome | Locales (captura nativa) |
| Apagón | Local: `showCalib({dots: []})`, ~380 ms (`node.js:755-769`) | Dentro de `look clean:true` (388 ms + el viaje), o pedido por MCP con 0,3–1 s de incertidumbre | Local (`pcx-perception`, pensado para eso) |
| Actualizar la proyección | Local: `setObjects` (`node.js:619-660`) y los widgets con `anchor.object_id` siguen solos | `scenes objects` por Render: 0,3–1,1 s | Local |
| Saber que la mano se fue | Local (MediaPipe ya corre ahí) | `scene include_hands` a 2 Hz: hasta 0,5 s más; no hay evento `hands_gone` | Worker de manos propio |
| Latencia al soltar un objeto | **≈ 0,7 s** (0,25 calma + 0,38 apagón + cómputo) | **2,5–3,5 s** por Render; ~1 s solo con el gateway en localhost | ≈ 1 s según `PARIDAD-MESA.md` §4, nunca medido en la mesa |
| Qué hay hecho | Nada del tracker; los puntos de enganche existen | `pcx_motor` no existe (solo `MOTOR.md`); `seguidor_v4` depende del runtime `:8790`, de la memoria compartida y de `lienzo_map.npz`, que el navegador no tiene | La cascada N0–N2b y la foto limpia, con tests sintéticos; falta el worker de objetos (sin commitear), el `calibrate` real, y la demo perdería html, manos, escenas y voz |
| Esfuerzo | (a1) JS: 4–5 h de agente. (a2) Rust → wasm: 6–9 h | 3–5 h para adaptar v4 a MCP | 10 h o más |
| Riesgo | Bajo | Bajo, pero no llega a 1 s por Render | Alto para el domingo 08:30 |

Sobre **(a2) compilar `pcx-objects` a wasm32**: no pude correr `cargo check`, porque esta máquina no tiene `rustup` y
el Rust de Homebrew solo trae `aarch64-apple-darwin`. Leyendo dependencias, no compila tal cual:

- `reqwest` con `blocking` y `tokio` con `net` son incondicionales (`node/crates/pcx-objects/Cargo.toml:24-25`) y los
  usan `llm.rs:97,112,214-217` y `models.rs:197-201`; `ort` y `tokenizers` (con `onig`, en C) sí están tras `onnx`.
- En ejecución fallarían `Instant::now()` (`tracker.rs:19,320,361,395,825`) y los hilos de `geom.rs:344-381`.
- `getrandom 0.2` (vía `imageproc`) necesita la feature `js`.
- El algoritmo (N0–N2b, rectificador con lente, `raster`, `ncc`, `orb`) no depende de nada de eso: son unos 5 `cfg`.
- `pcx-perception` depende de `pcx-shm` sin condición (`pcx-perception/Cargo.toml:10`), que solo tiene backend unix y
  windows. No conviene portarlo: el navegador ya hace la foto limpia.

## 5. Recomendación

**(a1): un `tracker.js` dentro de la consola, y DINO/SAM solo como registrador externo.** Es lo único que quita la
red del camino sin cambiar de nodo ni instalar toolchain, y deja el Rust como mejora posterior detrás de la misma
interfaz.

Qué hace el tracker (unas 300 líneas, sin dependencias):

1. **Alta:** cuando llegan objetos por `scenes objects` (ya existe), guarda de la siguiente foto limpia el recorte en
   gris y la máscara de cada uno, en una vista rectificada chica (500×282, 2 mm por píxel) con la tabla de
   `mmToPx` de `calib.js:187-191`.
2. **Disparo:** manos presentes y luego ausentes 250 ms (MediaPipe ya está en la pestaña), o cada N segundos en reposo.
3. **Foto limpia local:** lo mismo que hace `look clean` hoy, sin pasar por el gateway.
4. **N0:** diferencia contra la foto limpia anterior, compensando antes el corrimiento global de la cámara.
5. **N1 y búsqueda:** correlación enmascarada en el lugar; si falla, búsqueda en las zonas cambiadas. Mueve el
   polígono y llama a `setObjects`; lo que no encuentra queda como «perdido» para que el registrador lo vuelva a mirar.

Lo que no cubre esta primera versión: rotación de objetos no redondos (el halo queda girado respecto del objeto) y
objetos nuevos sin dar de alta. Para eso está (a2) o el registrador.

| Trabajo | Horas de agente |
|---|---|
| `tracker.js`: rectificado, foto limpia local, N0, N1 y búsqueda, `setObjects` | 2–2,5 |
| Alta desde `scenes objects` y pruebas con `?sim=1` | 1 |
| Compensar la deriva con la interfaz proyectada como referencia, o aviso de «recalibrar» | 0,5–1 |
| Ajuste en la mesa real | 1 (con una persona) |
| **Total** | **4,5–5,5 h; 3–4 h de reloj con dos agentes** |

En paralelo, para el registrador (Max o Daniel): `look clean:true`, DINO sobre el recorte de la mesa a lado corto 480,
SAM solo con caja, y publicar por `scenes objects`. Mejor aún para los nombres: que los ponga el agente (Claude ve la
foto), porque DINO tiny confunde justo los objetos de la demo.

**Decisión para Manu:** si se quiere mantener un motor Python externo en el camino rápido, la única forma de acercarlo
a 1 s es **correr el gateway en el computador de la mesa** (`docs/EMPEZAR.md` trae el comando). Eso cambia la URL y la
sesión de la demo; no lo recomiendo como camino principal, pero es una palanca de 10 minutos si (a1) se atrasa.

## 6. Arreglos inmediatos, independientes de la opción

1. **Fijar la cámara y recalibrar justo antes de la demo.** Es lo que más se nota: 2–3 cm de desfase a la media hora.
2. `pintura.py:95`: quitar el `sleep` de 6 s por defecto; `:69-71`: reemplazar borrar + esperar + reponer por
   `look` con `clean: true`.
3. `pintura.py:11-12,6-7`: dejar de convertir a mano; usar `lens` y `matrix_px_to_mm` que trae cada `look`
   (`tracking/scripts/geo.py` es el espejo de `calib.js` en 40 líneas) o mandar `polygon_px`. Borrar `H.npy` y `DY`.
4. `pintura.py:22`, `sam_seguir.py:25,28`: sacar el polígono de la mesa de la calibración, no de constantes.
5. `pintura.py:10`, `pcx.py:5-9`: una sesión HTTP viva en el mismo proceso en vez de un proceso por llamada.
6. `pintura.py:81`: quitar la regla de «un solo pastillero».
7. `seguidor_v4.py:222`: `!=` en vez de `is not`.
8. Render: pasar de `free` a `starter` cuando haya crédito (`render.yaml:11`).

## 7. Hallazgos en nuestro Rust (`node/crates/pcx-objects`, feature `onnx`)

- **MobileSAM (`src/onnx.rs:151-186`)**: espejo fiel de la clase de Python, sin bugs. Hereda el punto en el centro de
  la caja (`tracker.rs:779`).
- **Alta — DINO no usa el preprocesado de `detector_dino.py`**: aplasta la imagen a 800×800 (`onnx.rs:44-46`); la
  referencia respeta el aspecto (lado corto 800, largo 1333). Una foto 16:9 pierde 40 % de resolución horizontal y
  se deforma. Dado lo justo que anda DINO con objetos chicos, espero menos detecciones; no lo medí.
- **Alta — el nivel 3 nunca corrió de punta a punta**: el README conserva el marcador `NIVEL3_CIFRAS`
  (`README.md:79`) y el test está `#[ignore]` (`tests/onnx_nivel3.rs:12`).
- **Media — sin NMS entre candidatos** (`onnx.rs:93-124`, `tracker.rs:770-790`), igual que v4: dos cajas del mismo
  objeto empatan y el caso escala al LLM.
- **Media — una pasada de DINO por prompt** (`tracker.rs:759-762`).
- Las cifras de la cascada (N0 ≈ 16 ms, N1 ≈ 46 ms, N2 ≈ 123 ms acumulados) son de un benchmark sintético
  (`README.md:65-67`); no hay ninguna con fotos reales.

## 8. Lo que no verifiqué

- El tiempo real de `look`, `project` y `scenes objects` de punta a punta por Render (no podía llamar tools).
- Halos sobre objetos en vivo: no había seguimiento corriendo en ninguna de las dos capturas.
- Qué mueve la cámara (soporte, cable, golpes): solo medí que se mueve.
- El paralaje de los objetos altos y el desfase que agrega.
- Que el tracker propuesto aguante la mesa real: no hay ninguna secuencia con objetos moviéndose para probarlo; las
  fotos de `data/mesa` son la misma escena cuatro veces.
- La segmentación por «distinto del color de la mesa», sin modelos, la probé y **no sirve sola**: une canasto,
  pastillero y el reflejo de la lámpara en una mancha (`tracking/exp5-…jpg`). Hace falta comparar contra una foto
  limpia anterior.
- Onnxruntime-web con MobileSAM en el navegador: no lo probé. Con 1 hilo nativo el encoder ya tarda 1,3 s.
- Los archivos de `max/motor` y del Rust los revisaron dos subagentes; comprobé a mano las líneas que cito como bugs
  (`seguidor_v4.py:222`, `onnx.rs:44-46`, los dos `Cargo.toml`). Encontré una omisión en esa revisión (`tokio` con
  `net`), ya incluida arriba.

## Anexo: archivos

Todo en `/Users/manuelblanco/Projects/platanus_hack_26/docs/pruebas/tracking/`.

| Archivo | Qué muestra |
|---|---|
| `exp1-tal-cual-foto1..4.jpg` | El pipeline de `pintura.py` tal cual sobre las fotos de las 14:23 (recorte ampliado ×2). Taza y vaso sin detectar; «pastillero» sobre la plasticina |
| `exp4-sam-con-cajas-correctas.jpg` | SAM con cajas buenas: taza, pastillero real y lámina bien segmentados |
| `exp3-vivo-pintura.jpg`, `exp3-vivo-sam_seguir.jpg` | El pipeline sobre un cuadro en vivo de las 17:18, con el polígono de la mesa sacado de la calibración |
| `exp5-clasico-vs-sam-vivo-rectificado.jpg` | Segmentación por color de mesa (verde) contra SAM (magenta) |
| `vivo-00-cuadro-1718.jpg`, `vivo-00-cuadro-1731.jpg` | Cuadros del visor |
| `vivo-01-ui-esperada-sobre-camara.jpg` | Superficie y grilla según la calibración, y dónde debería caer la interfaz proyectada (verde) |
| `vivo-02-rectificado-mm.jpg` | El cuadro de las 17:18 rectificado a mm con lente + homografía |
| `vivo-03-deriva-camara-diff-1718-1731.jpg` | Izquierda 17:18; derecha 17:31 con lo que N0 marcaría como cambiado (rojo) |
| `vivo-04-…png`, `vivo-05-…png` | Pestañas y botón «Hablar» rectificados a 2 px/mm, con su posición esperada en verde |
| `vivo-captura-1718/`, `vivo-captura-1731/` | Los cuadros del visor (uno por segundo y uno cada dos) y `mensajes.jsonl` con el estado del mundo, la calibración vigente y el registro de actividad. Sin tokens |
| `scripts/` | `pipe.py` (réplica del pipeline), `geo.py` (espejo de `calib.js`), `exp1..5.py`, `ui_offset.py`, `vivo_calce.py`, `visor.py` (lee el token de `PCX_VIEW_TOKEN`) |

Para repetir los experimentos hacen falta `Acly/MobileSAM` (61 MB) e `IDEA-Research/grounding-dino-tiny` (690 MB) de
HuggingFace y un venv con `onnxruntime`, `opencv-python-headless`, `torch`, `transformers` y `websockets`. Los borré
al terminar, junto con los worktrees de `/tmp`. Los scripts quedaron con las rutas de trabajo que usé
(`/tmp/trk-work`, `/tmp/trk-models`, `/tmp/trk-consola`): hay que ajustarlas antes de correrlos.
