# Brief · pipeline 3D del escáner de luz estructurada (ola Codex, 17-oct-2026)

Pedido de Max (textual, 17-oct 12:1x): «no estoy satisfecho con el pipeline: el scan y calibración con luz estructurada no
está generando el point cloud y luego 3D mesh de la escena, luego proyectar eso en una escena 3D y hacer match de la
cámara 3D con la escena física. Una vez en ese point cloud/mesh con texturas ahí podemos identificar cuadros, teclas de
piano y la verdad cualquier objeto, iluminar una escena 3D, etc. Además ahora voy a hacer una nueva escena y las cosas
como el cocodrilo ya no van: ahora hay otros cuadros, sofás, etc. La gracia es que un agente analice los objetos; que el
orquestador use varios agentes de Codex que usen la data que hay antes, que mejoren los algoritmos, el pipeline y la
visualización de lo que está pasando.»

## Situación honesta (lo que hay y lo que falta)

Lo que hay hoy es **projection mapping 2D en coordenadas del proyector**, no una reconstrucción 3D:

- Gray code proyector→cámara (`slscan/graycode.py`, `slscan/recon.py::coalesce`): correspondencias densas píxel de
  proyector ↔ píxel de cámara. Funciona bien (scan5: 209 k px decodificados, verificado con marcadores ArUco a 1,05 px).
- `scene_pov.png`: la foto de la cámara reproyectada a la vista del proyector (textura "desde el proyector").
- `height_proj.npy`: altura **relativa** por paralaje (sin unidades, no métrica). No hay rig calibrado.
- Segmentación heurística 2D sobre `scene_pov.png` (`playground/segment_pov.py`): reglas específicas por objeto
  (cocodrilo por tono, teclado por periodicidad, platos por círculos…). **No generaliza** a la escena nueva.
- Calibración métrica: `slscan/calib.py` (ChArUco, intrínsecos, proyector por homografías, bundle) y `slscan/metric.py`
  (hoja carta + ChArUco proyectado + malla) existen y tienen tests, pero **nunca se corrió un rig real**: no hay
  `captures/rig.json`, por lo tanto no hay triangulación ni nube de puntos en mm (`recon.triangulate_pair` está
  sin usar con datos reales).
- Visor 3D (`playground/web/`, Three.js): muestra el heightfield relativo + objetos; el proyector es supuesto
  (throw ratio 1,2), no calibrado.
- Runtime (`playground/runtime.py`): efectos 2D en px del proyector con escena desde el escaneo (`scene_from_scan`).

Lo que Max quiere (y no existe): **nube de puntos métrica → malla texturizada → escena 3D con cámara y proyector
registrados → detección de objetos sobre la malla (cuadros, sofás, teclas, cualquier cosa) → iluminación/efectos en 3D
renderizados desde el proyector virtual**, y ver el pipeline mientras corre.

## Datos disponibles (Mac, `captures/`)

- `captures/scan5/osmo/`: 44 cuadros gray code `[AA-BB-CC_NN].png` (1080x1920, Osmo Pocket 3 cenital, 4K reducida 2x,
  rotada 270°), `color.png` (luz blanca), `map.npz` (`Decoded`: px, py por píxel de cámara, color, stats).
- `captures/scan5/`: `proj_to_cam.npz` (mapx, mapy, valid: proyector→cámara denso, 1920x1080), `scene_pov.png`,
  `height_proj.npy`, `segments.json`, `workbench.json`, `keys.json` (61 teclas, polígonos en px del proyector),
  `debug_aruco_*.png` (marcadores proyectados vistos por la cámara), `ajustes.json`, `salida.json` (corrimiento global
  de salida del proyector (-21, +21) medido anoche), `corrimiento.json`, `align_*.json`.
- `captures/scan5_brio/brio/`: `map.npz` + `color.png` de la webcam Brio del PC (segunda cámara, baja, en la pared);
  `captures/scan5_brio/proj_to_cam.npz`. Hoy el PC está apagado: esta cámara no está disponible en vivo.
- `captures/scan2/osmo/`: `map.npz`, `color.png` (escena anterior, por si sirve de segundo ejemplo).
- `captures/proj_charuco_1920x1080.png`, `reports/latency_osmo_proj1.json`, `tests/data/keys_scan2.json`.
- Hardware hoy: solo el MacBook (M-series, OpenCV 5, scipy, Three.js, sin Open3D instalado por disco), proyector T749
  1920x1080 (display principal; la terminal de Max está en el display interno), Osmo Pocket 3 (hay que ponerla en modo
  webcam; se pide 3840x2160 y se reduce 2x; `Camera("osmo", size="3840x2160", rotate=270, downscale=2)`).
  Hoja carta (215,9 x 279,4 mm) como patrón métrico; ChArUco impreso `reports/board_7x5_40mm.png`.
- La escena física va a cambiar (Max la está armando): cuadros, sofás, etc. Nada específico del cocodrilo/teclado debe
  quedar hardcodeado.

## Foco de Max (17-oct 17:5x) y criterios duros de aceptación

Textual en lo esencial: «quiero enfocarme en el escaneo de luz estructurada y lograr que genere un modelo 3D con PBR
textures de todo lo que está en la visión del proyector, que lo ponga en una escena 3D a escala y que coloque en el
espacio 3D el proyector y la cámara según donde cree que están. Quiero precisión de 0,2 cm de tolerancia máxima. Lo de
proyectar los outlines de los objetos sobre los objetos y sacar una foto se puede mejorar: ahí debería decir si mover la
superficie o no, pero ese finetuning por objeto no debería ocurrir si el setup está bien calibrado; tal vez el FOV o una
distancia errónea entre la cámara y el lente del proyector no se está ajustando. Los outlines salían como líneas blancas
con fondo negro y debería ser al revés, para que en la foto se vean los objetos que se quieren modelar. Luego afinarlo
para que el proyector sea casi handheld: lo apoyo, apunto donde quiera y tengo una experiencia inmersiva mapeada,
detectando cuadros y objetos, a qué parte del modelo 3D corresponden, enmascarando y recortando.»

Criterios duros (cada paquete se mide contra esto):

1. **Error ≤ 2 mm** en la nube de puntos (contra la hoja carta plana y sus 215,9 × 279,4 mm) **y** en la reproyección
   sobre lo físico (contornos proyectados vs bordes reales, medidos en la cámara y convertidos a mm por la escala local).
2. **Texturas PBR**: albedo, normal y rugosidad (SPEC F3), sobre la malla, derivadas de las tomas con luz blanca,
   gray code y, si hace falta, luz rasante proyectada desde varias direcciones.
3. **Escena a escala** (mm) con las poses de cámara y proyector **estimadas por calibración**, nunca supuestas.
4. **Intrínsecos del proyector (FOV/distancia focal, punto principal, distorsión) y baseline cámara–proyector
   calibrados por optimización conjunta** (bundle con todas las vistas); prohibido un throw ratio asumido y prohibidos
   los corrimientos por objeto (`ajustes.json`, `salida.json`) como solución: si hay corrimiento sistemático, es un
   síntoma de calibración y se arregla ahí.
5. **Verificación estándar**: proyectar todo en **blanco con los contornos en negro**, fotografiar, y medir el residuo en
   mm por objeto (y el residuo global modelado como traslación / escala / homografía para diagnosticar FOV vs baseline).
   La foto debe dejar ver los objetos que se modelan.
6. **Meta posterior**: proyector casi handheld, con relocalización continua contra el modelo 3D (detectar cuadros y
   objetos, saber a qué parte del modelo corresponden, enmascarar y recortar).
7. Sin impresora: la hoja carta/A4 blanca es el único patrón físico (ChArUco impreso solo si hace falta, decisión q4).

## Reglas duras

- Nunca abrir ventanas en el display interno del Mac ni en un Sidecar/iPad; proyectar solo con `slscan.capture.PatternWindow(screen="1920x1080")`
  o, si el runtime está proyectando (`python -m playground serve --projector`), por su comando `overlay`
  (POST /api/command {"cmd":"overlay","args":{"path":"/abs.png","seconds":2}}): macOS deja la ventana del runtime
  encima de cualquier otra.
- Capturas de cámara son privadas: en `reports/` (fuera de git), borrarlas al terminar, nunca pegarlas ni subirlas.
- Disco del Mac: ~5 GB libres. Nada pesado (sin Open3D si no cabe; preferir numpy/scipy/OpenCV; mallas por grilla).
- Una tarea, una rama (worktree propio). Sin commit en master; sin push. Tests de lo tocado; suite completa en verde.
- Nada sale de la máquina. Llaves por referencia.

## Paquetes de trabajo (uno por agente Codex)

1. **Análisis y plan** (primero, el resto se encadena): leer PLAN.md, SPEC.md, `slscan/`, `playground/`, los datos de
   `captures/scan5`, y producir `docs/ANALISIS-pipeline-3d.md`: diagnóstico preciso de por qué no hay nube/malla
   métrica, qué se reutiliza, qué se bota, y el plan por paquetes con criterios de "listo" medibles.
2. **Rig métrico real**: protocolo repetible para calibrar cámara + proyector con la hoja carta y/o ChArUco impreso +
   ChArUco proyectado (3-4 poses), con `slscan metric capture/calib` o lo que haga falta; salida `captures/rig.json`
   (K, dist, R, t de cámara y proyector, error de reproyección por vista). Simulación sintética para validar antes de
   la toma real; luego la toma real cuando Max ponga la hoja (paso a paso guiado, proyectado).
3. **Nube y malla**: triangular las correspondencias del gray code con el rig → `scan/cloud.ply` (mm, color),
   limpieza (outliers, agujeros), malla por grilla del proyector o Poisson liviano → `scan/mesh.ply` con textura UV de
   `color.png`/`scene_pov.png`; métricas (densidad, ruido en mm sobre la hoja plana, error sobre la hoja de 215,9 mm).
4. **Escena 3D registrada**: `playground/web` (Three.js) con la malla texturizada, la cámara y el proyector reales del
   rig (frustum, pose, intrínsecos) y un modo "vista desde el proyector" que renderiza exactamente lo que hay que
   proyectar (render-to-texture → el runtime lo manda al proyector); verificación: lo renderizado sobre la malla cae
   sobre lo físico (capturas con la Osmo).
5. **Detección de objetos genérica sobre la malla/textura**: planos (cuadros, pantallas, mesa, hojas) por RANSAC en la
   nube + textura; objetos sobre la mesa por altura; "cualquier objeto" por segmentación de imagen (SAM/FastSAM
   liviano si cabe en disco, si no superpíxeles + altura); salida `scan/objects.json` (contorno 2D en px del proyector +
   bbox 3D en mm + plano) que `scene_from_scan` consuma sin reglas por objeto. Debe funcionar en la escena nueva.
6. **Visualización del pipeline**: página web (en `playground/web` o nueva) que muestre en vivo cada etapa: cuadros del
   gray code decodificados, mapa, nube, malla, objetos, proyector/cámara, y qué paso está corriendo; más un comando
   `slscan pipeline run <scan>` que encadena todo con reporte JSON por etapa.

7. **PBR "home friendly" con las lámparas de la casa** (pedido de Max, 17-oct 18:0x, textual: «para la textura PBR lo
   quiero hacer home friendly, donde ocupamos las ampolletas o lámparas naturales del lugar. Requisito: que la
   ampolleta o fuente de luz se vea desde la cámara del proyector, entonces prendiendo y apagando distintas lámparas
   del lugar. Luego, con otro pipeline, se detectan en el espacio 3D global los puntos de luz, su temperatura y tipo de
   luz, etc., para así poder hacer reverse engineering de los bump maps, reflectivity maps, absorption maps etc.
   presentes»). Sin comprar luces. Depende de P2 (rig) y P3 (malla).
   - Secuencia guiada (proyectada y por voz): prender y apagar cada lámpara de a una, con exposición fija de la cámara o
     bracketing HDR si la cámara no deja fijarla (la Osmo en modo webcam no fija exposición: medir y compensar con un
     parche gris/blanco de referencia en cuadro, o usar el Pixel con exposición manual).
   - Localizar cada fuente en 3D: si se ve en la cámara, por su posición en imagen + rig (triangulación desde dos
     cámaras o desde la malla: el rayo que la ve corta la escena); si no, desde los highlights especulares sobre la
     malla (la normal conocida refleja el rayo hacia la fuente). Salida `scan/lights.json` (posición mm, tipo puntual |
     área | difusa, temperatura de color estimada del balance de blancos sobre el parche de referencia, intensidad
     relativa).
   - Estéreo fotométrico con las lámparas como fuentes (normales finas / bump) y ajuste de BRDF por texel sobre la malla
     (albedo, normal, especular/reflectividad, rugosidad, absorción), con la malla y el rig de P2–P3 como geometría.
   - Verificación: re-render de la escena con una lámpara que NO se usó en el ajuste vs la foto real con esa lámpara
     (error por texel y por objeto); y el albedo debe quedar sin sombras ni brillos "horneados".
   - Entrega: `scan/pbr/{albedo,normal,roughness,specular,absorption}.png` en el UV de la malla + `lights.json`.

Cada paquete: README corto, tests sin hardware, y una línea con qué se verificó de verdad (sintético, datos de scan5,
o en vivo).
