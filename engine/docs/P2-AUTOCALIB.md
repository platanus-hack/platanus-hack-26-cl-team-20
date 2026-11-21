# Autocalibración de escena sin tablero

Para la Osmo montada al T749, ver el [protocolo de rig rígido](P2-RIG-RIGIDO.md),
incluidos los movimientos, la condición del gimbal y resultados de replay.

## Qué puede observarse

Gray code proporciona correspondencias entre dos dispositivos proyectivos.
Muchos puntos distribuidos en profundidad permiten estimar la matriz fundamental
de un par. No eliminan por sí solos la ambigüedad proyectiva ni dan milímetros.
Con centros principales, aspecto y skew conocidos, un par genérico puede
determinar dos focales adicionales. Si se liberan los centros y la distorsión,
se necesitan restricciones adicionales y geometría favorable. Ejes casi paralelos,
poco relieve y movimiento solo axial son casos especialmente débiles. Esto
concuerda con el análisis de [Hayman, sección 8.4](https://www.robots.ox.ac.uk/ActiveVision/Papers/hayman_dphil2000/hayman_dphil2000.pdf).

El modo recomendado para la Osmo montada al T749 es **`--motion rigid --views 3`**.
Se mueve el conjunto completo, con intrínsecos compartidos y un solo extrínseco
cámara→proyector. La escena queda quieta. Cada vista tiene su propia pose del
proyector: el mismo código Gray NO identifica el mismo objeto entre vistas.
Se siguen puntos físicos de las fotos uniformes `view_XX/color.png`, usando
SIFT, matches mutuos con ratio 0,75 y filtro fundamental RANSAC. En cada punto
se interpola el mapa Gray local con un ajuste afín, rechazando discontinuidades.
Las fotos y mapas forman parte de los hashes del rig.

El modo anterior `--motion camera` conserva proyector y escena inmóviles y mueve
solo la cámara. En ese caso sí se enlazan códigos Gray. Los replays antiguos sin
campo `motion` conservan ese comportamiento; un override incompatible se rechaza.
Live usa `rigid` por defecto y exige al menos dos vistas antes de abrir hardware.

El bundle ajusta focal de cámara compartida, focal de proyector, ambos centros
principales, k1/k2, pose del proyector, poses de cámaras adicionales y XYZ de la
escena. Fija cámara 0 como mundo y norma del baseline a 1 para quitar el gauge.
Asume píxeles cuadrados, skew cero y p1/p2/k3 cero. Los priors son explícitos:
focales logarítmicas con sigma 0,30, centros con sigma de 4% del raster, k1 con
sigma 0,15 y k2 con sigma 0,10. Hay límites numéricos amplios. Un prior no cuenta
como medición: el rango se calcula sobre el Jacobiano de datos, eliminando XYZ
por punto y excluyendo las filas de regularización.

DJI publica 20 mm equivalentes para [Osmo Pocket 3](https://www.dji.com/osmo-pocket-3/specs).
Se usa `max(frame_size) * 20 / 36` solo como inicio amplio. Resolución entregada,
crop de webcam, procesamiento y enfoque impiden convertir ese dato comercial en
una K medida. El proyector parte de `fx/ancho=1,1`, también supuesto, configurable.
La recuperación de pose entrega traslación de norma unitaria, [no escala métrica](https://docs.opencv.org/4.13.0/d9/d0c/group__calib3d.html).

El rango numérico es condicional al modelo y a las observaciones, no una prueba
de exactitud. La simulación compara una vista, tres vistas y priors más débiles;
elige de antemano la solución de tres vistas con priors nominales, sin seleccionar
por la verdad reservada. Un RMS pequeño puede coexistir con una forma incorrecta.

## Resultado medido en la simulación de imágenes

Corrida `d9169e85d097475cb6c388ccc6021ef5`: 3 vistas de 1080×1920, 44 patrones
por vista, 1920×1080 de proyector. Se usaron el decoder real y el bundle, sin
datos geométricos verdaderos como observaciones.

- Comparación del visor, 336 tracks de ajuste y 84 reservados: una vista deja
  máximo XYZ reservado **126,85 mm**; tres vistas, **13,63 mm**; tres vistas con
  priors más débiles, **11,68 mm**. Esa primera comparación agotó su presupuesto
  de 250 evaluaciones y lo registra como no convergente.
- El comando final `autocalib --replay`, sobre los mismos mapas, usa 360 tracks y
  90 reservados: **converge en 152 evaluaciones**, rango de datos 27/27, RMS
  cámara **0,288 px**, proyector **0,128 px**. XYZ reservado: RMS **6,78 mm**,
  p95 **10,63 mm**, máximo **12,58 mm**, sin alineación libre.
- Se aplicó solo el baseline sintético exacto de **302,241625 mm** como escala
  externa después del ajuste. El resultado es sintético y no acredita una huincha.
- El `rig-cli.json` resultante pasó `validate(data, 'rig')` del contrato P6 y
  conserva `evidence=synthetic`, `metric_status=unverified`.

**Esta prueba no cumple ≤2 mm.** No se oculta el error con escala libre por objeto
ni seleccionando la variante que mejor calza. El cambio de priors muestra
sensibilidad; los centroides Gray de cuatro píxeles tampoco son correspondencias
subpíxel exactas. Hace falta investigar y reducir esos errores, luego repetir
controles independientes. La captura física posterior de una vista falló por rango y reproyección; no publicó rig.

Decisión retransmitida por h1 a las 20:00: el candidato puede usarse esta noche
para el show, declarado **sin verificar**. Esto no cierra el objetivo P2 de 2 mm.
El flujo de h3 reenvía los eventos JSONL, narra `move_camera`, espera «continuar»
o Enter y escribe `continue` en stdin. Exit 2 continúa con escena 2D, sin rig.

## Escala sin imprimir

**Preferir una distancia larga entre dos puntos visibles de un objeto rígido.**
Puede ser un tramo del teclado, medido realmente; ni el ancho del capuchón ni el
paso entre teclas son universales. Detectar un teclado no aporta su tolerancia.

La contribución de escala a una longitud o distancia L es `L * u(D)/D`.
Ejemplos condicionales, no precisiones medidas del hardware ni de una huincha:

| Referencia | Incertidumbre supuesta | Error de escala a 1 m |
|---|---:|---:|
| Una tecla de 19 mm | ±0,2 mm | ±10,53 mm |
| Tramo de teclado de 300 mm | ±0,2 mm | ±0,67 mm |
| Baseline óptico de 250 mm | ±1 mm | ±4,00 mm |
| Baseline óptico de 250 mm | ±0,2 mm | ±0,80 mm |

A 2 m esas contribuciones se duplican. Para dejar como máximo 1 mm del presupuesto
a escala en una escena a 1 m, hace falta u(D) ≤ 0,1% de D. El resto debe cubrir
errores de geometría, distorsión, correspondencia, estabilidad y selección de
extremos. No se suman como si todas fueran sigmas independientes: aquí son cotas
ilustrativas. Una huincha entre carcasas o frente de lentes no identifica los
centros ópticos; sin resolver ese offset no sirve como baseline de precisión.

La escala se aplica una vez al rig completo, sin ajustes por objeto. Sin referencia
no se publica `rig.json` en mm: P6 exige mm. El reporte relativo permanece para
diagnosticar. Con referencia pero sin cota de incertidumbre se permite un rig
candidato, siempre `metric_status=unverified`; no se certifican 2 mm.

## Contrato para el flujo de h3

Ejemplo preparado para el turno autorizado, **no ejecutado en hardware**:

```sh
python -m slscan autocalib reports/p2-scene-live-a \
  --out reports/p2-scene-live-a/rig.json \
  --display-confirmed --motion rigid --views 3 --settle 2 \
  --viewer-state /RUTA/ABSOLUTA/DEL/ESTADO/DEL/VISOR \
  --scale huincha:MEDIDA_MM --scale-uncertainty-mm COTA_MM
```

- Captura con `Camera('osmo', size='3840x2160', rotate=270, downscale=2)`;
  frame efectivo obligatorio 1080×1920. `PatternWindow(screen='1920x1080')` exige
  raster físico/lógico 1:1. `--display-confirmed` representa el turno autorizado
  y las comprobaciones ópticas; no reemplaza q21 ni pide permisos automáticamente.
- El parser conserva `--views 1`, pero live rígido lo rechaza antes de abrir hardware.
  Seleccionar explícitamente `--views 3`; entre vistas emite
  `step=move_camera`, `motion=rigid` y espera `continue\n` en stdin (o la línea literal
  `{"action":"continue"}`). El lanzador debe narrar **`msg`**, que dice mover el conjunto completo, y esperar
  a Max; EOF cancela. No mandar confirmaciones adelantadas.
- Durante las 44 franjas el lanzador muestra progreso y el conjunto queda quieto.
  Entre vistas se mueve TODO el rig; los objetos quedan fijos. La óptica y el modo deben permanecer
  iguales. En el rig publicado, `osmo` identifica la **última posición**, donde
  queda la cámara para el scan siguiente. En modo rígido `proj` también usa la
  última pose; el archivo contiene exactamente un proyector. `osmo_reference` conserva el marco de
  la primera vista, fijo; las intermedias se llaman `osmo_1`. No mover el conjunto
  entre el fin de la calibración y el scan.
- stdout contiene JSONL: `stage`, `step`, `progress` entre 0 y 1, `msg`, `ok`.
  Los eventos adicionales incluyen vista y ruta. Logs ajenos van a stderr.
- Salida 0: se escribió candidato `slscan-rig/2`, mm, poses y residuos, procedencia
  y hashes de mapas. Salida 2: fallo, rango insuficiente, no convergencia, geometría
  casi plana, profundidad inválida, residuos excesivos o falta de escala; sin rig
  nuevo. Nunca sobrescribe una salida existente. El reporte explica el fallo.
- `--scale huincha:MM` sin extremos significa distancia entre **centros ópticos**.
  Para un tramo medido en la escena, agregar `--scale-pixels x1,y1,x2,y2` sobre la
  imagen de cámara 0; ambos extremos deben tener Gray válido. `--scale teclas:MM`
  exige esos extremos y MM es la distancia medida entre ellos, no un pitch supuesto.
- No puede acreditarse precisión a partir de esos extremos Gray cuantizados:
  se necesita extracción fina de extremos, incertidumbre y control independiente.
- El visor debe servir la misma carpeta `--viewer-state`, en puerto 8797, con
  `metric viewer --state CARPETA --port 8797`, sin `--simulate` en la toma real.
  El observador es de solo lectura. El servidor bloquea controles de simulación
  cuando el estado tiene evidencia física. No ejecutar dos productores de estado.

Ejemplo de evento:

```json
{"stage":"autocalib","step":"capture","progress":0.1,"msg":"Vista 1: patrón 20/44","ok":true,"view":1}
```

Replay, sin abrir cámara ni display:

```sh
python -m slscan autocalib SESION --replay --out SESION/rig-candidato.json \
  --scale huincha:MEDIDA_MM --scale-uncertainty-mm COTA_MM
```

Requiere `autocalib-session.json` con procedencia y tamaños, y `view_XX_map.npz`.
Se prefija 20% de tracks reservados; su reproyección no entra al bundle. En modo legado de proyector fijo, las celdas
se agrupan por código del proyector, descartando las demasiado dispersas. Esto es
un centroide de parche, no un punto subpíxel perfecto: se reporta esa aproximación.
Se corrige explícitamente el centro de bloques Gray de cuatro píxeles de 2 a 1,5.

### Generar un replay de imágenes, sin parches

```sh
python -m slscan autocalib-simulate reports/p2-replay-a
python -m slscan autocalib reports/p2-replay-a --replay \
  --out reports/p2-replay-a/rig.json \
  --scale huincha:302.24162519325414 --scale-uncertainty-mm 0
```

La escala de ese ejemplo es el baseline **sintético exacto**, guardado en
`simulation-truth.json`; no es una medición física. El estimador no lee ese
archivo. `autocalib-simulate` renderiza 44 imágenes por vista, las escribe como
PNG, las vuelve a leer con `load_sequence`, ejecuta el decoder y guarda los NPZ
con `Decoded.save`. No rasteriza puntos ideales como sustituto de la captura.
No abre cámara/display y rechaza carpetas que ya contengan datos.

Default: tres vistas, cámara 1080×1920 y proyector 1920×1080. Para ensayos rápidos,
`--camera-scale .5` usa 540×960 (el proyector sigue nativo). `--seed` fija ruido;
`--keep-images` conserva los PNG de esa corrida. Sin esa opción, solo se eliminan
esos PNG tras guardar cada mapa. `autocalib-session.json` registra tamaños,
procedencia, modelo y estado. La regresión `test_replay_session.py` usa el comando
a media resolución y después ejecuta `autocalib --replay` completo, sin mocks
del renderer, serialización, decoder, correspondencias ni solver.

### Cobertura de celdas: riesgo compartido por live y replay

Ambas rutas del modo legado `motion=camera` pasan por `dense_tracks`. Se prefieren celdas con al menos dos
píxeles; si no hay 100 comunes, se incluyen también celdas de un solo píxel,
con su cuantización declarada. Esto permite puntos aislados legítimos y sensores
que submuestrean el patrón. No crea correspondencias donde no existen: sigue
exigiendo 100 códigos comunes y rechazando celdas demasiado dispersas. El evento
JSONL `coverage` informa conteos por vista, número común, modo de muestreo y
cuántas muestras seleccionadas son de un solo píxel. Si falla, el mensaje incluye
los conteos para distinguir falta de contraste, falta de solape y mapas vacíos.
El fallback no acredita precisión; se conservan los controles de rango, ajuste,
reproyección y estado sin verificar.

Chequeo de solo lectura del 17-oct, sin abrir hardware:

| Datos | Píxeles válidos | Celdas multipíxel utilizables |
|---|---:|---:|
| Osmo real histórica `scan5`, 1080×1920, skip_bits=2 | 209.504 | 65.884 |
| Sala renderizada nativa, vista 0 | 525.089 | 106.549 |
| Sala renderizada nativa, vista 1 | 592.830 | 111.763 |
| Sala renderizada nativa, vista 2 | 549.325 | 97.345 |

Las tres vistas sintéticas comparten 90.303 celdas multipíxel. La captura histórica
solo demuestra cobertura en **una** vista real: no confirma el contraste ni el
solape de la próxima sesión, que se comprobarán al adquirirla. Ya se había
ejecutado el replay nativo completo antes de este cambio; ahora además queda un
generador reproducible y una prueba integral automatizada.

Validación del cambio: **20 pruebas afectadas pasan** (94,70 s), incluidas la
cadena completa de 132 PNG y la aceptación explícita de correspondencias de un
solo píxel. El rig de ese replay también pasó el validador P6.

## Simulación y visor

```sh
python -m slscan metric viewer --simulate --scene \
  --state reports/p2-scene-viewer --port 8797
```

Sala amueblada con mesa, teclado con teclas en relieve, libros, taza facetada,
cuadros, paredes y distintas profundidades. Sin papel ni tablero. Raycasting con
oclusión de cámara y sombras desde proyector, distorsión de ambos dispositivos,
patrones raster nativos, blur, ruido de lectura/señal, exposición, gamma y 8 bits.
Las 44 imágenes completas por vista pasan por el decoder real. Se descartan solo
los PNG temporales de esa corrida tras guardar mapas; no se borran datos ajenos.

El visor muestra la sala, patrón proyectado, sensor sintético, cámaras, proyector
y nube estimada. La vista 3D es una previsualización geométrica; el backend de
sensor es el que calcula oclusiones y ruido para las observaciones. No es un
gemelo medido de T749/Osmo ni un render fotográfico; no modela ISP/Bayer, rolling
shutter, especulares, rebotes o autoexposición real. El modelo de latencia es
ilustrativo y está documentado en [P2-VISOR.md](P2-VISOR.md).

La comparación contra verdad usa un baseline sintético exacto **solo después del
ajuste**, como fuente externa de escala, y ninguna alineación adicional. XYZ de
entrenamiento y reservado se comparan contra intersecciones verdaderas del rayo
del proyector. No se entregan XYZ, K, distorsión ni poses verdaderas al solver.
Las tres cámaras comparten intrínsecos. Variar priors muestra sensibilidad; no se
escoge la variante que mejor calza con la verdad reservada.

Para certificar ≤2 mm físicos faltan controles independientes en varias zonas y
profundidades, incertidumbre de la referencia, repetibilidad y estabilidad del
rig. Un ensayo sintético fallido se muestra como fallido y conserva sus residuos.
