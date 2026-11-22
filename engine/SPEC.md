# SL Scanner · spec v0

15-oct-2026 · lo escribió el orquestador Creativo (h1) a pedido de Max. La unidad h3 (Fable) planifica y ejecuta. Lo que
diga Max después manda sobre este archivo.

## Qué es

Un sistema propio de proyector + varias cámaras + luz LED controlable que:

1. se calibra solo (posición, orientación y FOV de cada cámara y del proyector),
2. escanea en 3D la escena completa con luz estructurada desde todas las cámaras,
3. saca materiales PBR (albedo, normal, rugosidad, especular) moviendo la luz,
4. usa ese 3D para hacer projection mapping, trackear los objetos y reproyectar sobre ellos cuando se mueven.

**Referencia:** Lightform LF1 (2018), en «Hands-On with Lightform Projection Mapping!» de Tested
(youtube.com/watch?v=zKAzVr8ULF4, desde 7:31). Era un proyector con una sola cámara: escaneaba la escena desde el punto
de vista del proyector, sacaba bordes y superficies para enmascarar contenido, y había que volver a escanear si algo se
movía. **Mejor que eso** significa:

- 3D completo fusionado desde varias cámaras, no un 2,5D visto desde el proyector;
- materiales PBR reales, no solo la foto;
- objetos que se mueven y la proyección los sigue.

**Base de código:** github.com/jhdewitt/sltk (Structured Light Toolkit). Es un conjunto de herramientas de línea de
comandos en C++: `sldisp` muestra patrones gray code y senoidales por OSC, `slcapture.py` captura, `slcrunch` decodifica
correspondencias y nubes de puntos, `slcalibrate` calibra cámaras, el proyector y la registración cámara-proyector.
Incluye `plotlens`, `plyalign`, `plymerge` y `plytrim`, y trae `sampledata/` con escaneos de conchas y su calibración.
Es de una cámara por proyector, usa OpenCV 2.4, SDL2, liblo y PCL, y se compiló por última vez en macOS 10.10–10.12. Sirve
como referencia de algoritmos y flujo; el plan decide si se porta o se reimplementa.

## Objetivos por fase

Las metas numéricas son iniciales. El plan técnico puede ajustarlas, pero cada fase cierra con una prueba medida y algo
que Max pueda ver: una captura o un artifact, no una descripción.

| Fase | Qué | Hecho cuando | Se muestra con |
|---|---|---|---|
| F0 | Base: sltk clonado, stack decidido, `sampledata` de sltk procesado de punta a punta sin hardware | Nube de puntos de la concha generada por nuestro pipeline | Captura de la nube |
| F1 | Calibración: intrínsecos de cada cámara y del proyector, extrínsecos de todos en un sistema común (mm), FOV derivado | Reproyección < 0,5 px por cámara y < 1 px proyector; poses estables entre dos sesiones | Visor 3D del rig (frustums) + mapa de distorsión |
| F2 | Escaneo 3D: gray code + phase shift desde el proyector, decodificación por par proyector-cámara, triangulación, fusión multicámara, malla limpia | Error < 1 mm a 1 m sobre un objeto de medida conocida | Malla en visor web |
| F3 | Materiales PBR **home friendly** (Max, 17-oct): sin comprar luces; se usan las ampolletas y lámparas del lugar, prendiéndolas y apagándolas de a una en una secuencia guiada (exposición fija o bracketing HDR); cada fuente se localiza en el espacio 3D global (vista directa desde la cámara + rig, o highlights especulares sobre la malla) con su temperatura de color y tipo (puntual, área, difusa); con eso, estéreo fotométrico y ajuste de BRDF (GGX) por texel sobre la malla: albedo, normal/bump, especular/reflectividad, rugosidad y absorción, texturas en UV. Depende del rig (P2) y la malla (P3); detalle en docs/BRIEF-pipeline-3d.md P7 | Re-render comparado con una foto bajo una lámpara que no se usó en el ajuste | Lado a lado foto vs render, error por texel y por objeto |
| F4 | Projection mapping: render desde el proyector con su calibración, contenido pegado a la geometría, tracking 6DoF de objetos rígidos y reproyección | Error < 2 px proyectado; latencia de tracking < 100 ms | Video de la proyección siguiendo un objeto |

## Hardware

Lo que sabemos hoy, 15-oct, revisado en el MacBook:

- **Máquina:** MacBook Air M4 (arm64, Metal 3). **Disco interno con 4,2 GB libres (98 %)**. Según la memoria del 4-oct,
  brew estaba roto (conflicto de merge en `brew.sh`); hay que comprobarlo antes de instalar dependencias. En la tailnet
  existe una torre Linux, con specs sin verificar.
- **Proyector:** modelo pendiente. Hoy no hay ninguno conectado; el display externo es un monitor ASUS PA278QV.
- **Cámaras 4K (más de una):** modelos pendientes. Hoy el Mac solo ve la cámara del MacBook, Iriun (celular como
  webcam) e Immersed (virtual). Si las 4K son celulares, hay que revisar compresión, control manual de exposición y
  latencia.
- **LED con control de temperatura y color:** modelo y forma de control pendientes.
- **Escala de la v1 (supuesto, reversible):** escena de mesa o escritorio, 0,5–2 m. Si Max apunta a una sala (por
  ejemplo, el dj_venue), cambian el montaje y las metas.

## Requisitos técnicos que el plan tiene que resolver

- Exposición, foco y balance de blancos manuales y fijos en todas las cámaras. Captura sin compresión (RAW o PNG):
  MJPEG destruye los patrones finos.
- Sincronía proyector-cámara: medir la latencia del proyector y elegir trigger por software con margen o trigger
  externo.
- Respuesta (gamma) del proyector calibrada, necesaria para phase shift y para usar el proyector como fuente de luz.
- Un solo sistema de coordenadas en mm y un formato de calibración documentado (JSON/YAML) que lean todas las fases.
- Cada paso reproducible por CLI, con un visor (web/Three.js o Blender) para mostrar avances.
- Datos pesados fuera de git (`captures/` ignorada), en el disco que decida Max.
- Capturas guiadas: cuando haga falta que Max mueva una cámara, la LED o un tablero, el sistema le muestra en pantalla
  qué hacer y valida en vivo que la toma sirve (cobertura del tablero, foco, exposición).

## Compras posibles

Las decide Max; nada se compra sin él. La unidad propone con link y precio cuando la fase lo pida.

- Tablero ChArUco rígido (impreso sobre aluminio o vidrio), o mostrado en una tablet o monitor para los intrínsecos.
- Esferas cromadas (bolas de rodamiento) para ubicar la luz.
- Polarizadores lineales: lámina para el proyector y la LED, y filtros para las cámaras, para separar difuso y especular
  por polarización cruzada.
- Carta de color (ColorChecker) para calibrar color con la LED.
- SSD externo, trípodes o brazos, y una tornamesa motorizada opcional (sltk trae una con Arduino).

## Fuera de alcance en la v1

Exteriores y escenas grandes, objetos transparentes o espejados, gente en movimiento y publicar cualquier cosa.

## Decisiones abiertas para Max

Registradas en el orquestador Creativo; las respuestas le llegan a h3 por `orq-creativo send`.

- **q1 · Dónde corre y guarda.** Recomendada: MacBook + SSD externo para capturas. Alternativas: torre Linux, o liberar
  ≥ 60 GB internos.
- **q2 · Cámaras 4K y proyector.** Recomendada: Max los enchufa y la unidad los detecta. Alternativas: modelos por chat,
  son celulares, o proponer compra.
- **q3 · LED.** Recomendada: la maneja el sistema (marca o modelo y la unidad busca su API). Alternativas: a mano con
  instrucciones en pantalla, o comprar una con API o DMX.

## Cómo trabaja la unidad

- Una sola unidad, **h3, con Fable**, en `~/hardware/sl-scanner/`, con repo git local sin remote. Nada se publica, sube
  ni compra sin Max.
- Primero `PLAN.md`: arquitectura, stack (portar sltk a OpenCV 4 o reimplementar en Python/OpenCV 4 usando sltk como
  referencia), fases con hitos verificables y qué necesita de Max en cada una. Después ejecuta fase por fase.
- Sin flota: la cuota es la misma de Trabajo. Subagentes solo puntuales y de a uno.
- Tests solo de lo tocado y un build final; nada de suites completas repetidas.
- Las pruebas físicas (montar, enchufar, mover la LED o el tablero) las hace Max, guiado por el sistema.
- Reporta con `orq-creativo note h3 "…" --avisa` en cada hito. Si lo que sigue depende de Max, usa
  `orq-creativo wait h3 "Max: …"`. Decisiones nuevas con `orq-creativo ask` (con opciones, recomendada y vencimiento).

## Adiciones de Max · 15-oct, 17:40–17:55 (en el pane de h3)

Lo que dijo, resumido; sus palabras completas están en el registro del orquestador Creativo (q1–q3).

- **Dónde corre (q1).** Un **PC Windows con buena tarjeta de video y RAM** hace lo pesado; al Mac no le caben puertos.
  Se controla por SSH (y un servidor local cuando haga falta); todo se instala en Python allá.
- **Cámaras y proyectores (q2).** **2 proyectores**, cada uno en un **cabezal móvil** que rota 360°: hay que ir
  rotándolo para cubrir la escena y, al apuntar, saber qué parte de la pieza está iluminando. **Una Osmo pegada al
  lente de cada proyector** para alinear. **Una Sony FX3** viendo toda la escena. **Una webcam cenital**. 3 monitores
  en el escritorio principal. **Todo el feed es 4K.** Max conecta la Osmo para que la detectemos.
- **Luces (q3).** **Un foco DMX USB-C de fotografía** con RGB y temperatura de color: el sistema lo calibra, con un
  recorrido de posiciones. **Una barra RGB con temperatura, manual**: Max la mueve y el sistema la trackea.
  Las dos luces se reconstruyen en 3D.
- **PBR.** Hay un paper reciente de texturas PBR con fuentes de luz y una sola cámara que Max quiere usar como
  referencia (link pendiente).
- **Tracking de objetos (lo más importante).** Con la geometría y las texturas listas, segmentar la escena 3D
  (mesa, pantallas, objetos…). Si no hay un modelo 3D tipo SAM, pasar la imagen por SAM y aplicar los bounds al
  pointcloud o mesh para tener máscaras; cruzar eso con la geometría (referencia: OpenPCSeg,
  github.com/BAI-Yeqi/OpenPCSeg). Todo tiene que terminar en malla. Luego trackear cada objeto en 3D, reconstruirlo
  y aplicar shading, cosas sobre la mesa, interacciones.
- **Personas.** Backend y tracking de poses con armature, full realtime, para proyectar sobre la gente. Con varias
  cámaras, trackear a distintas personas con reconocimiento de cara, cuerpo y ropa. Solo con librerías abiertas (no
  se usa código de clientes de SmartUp; lo decide Max con h1).
- **Playground.** Un espacio para ir creando experimentos: shading, interactividad, tracking, interacciones.
- **GPU.** Raytracing y efectos en tiempo real.
- **Pedido inmediato.** Preparar lo que tiene que correr en el PC Windows para tener control por SSH.
- **Robustez (18:00).** Los efectos de tracking y superposición tienen que resistir que los objetos se muevan en la
  escena y que la cámara y el proyector apunten a otras cosas (cabezales móviles): el mapa 3D y las poses de los
  dispositivos se mantienen aunque cambie lo que cada uno ve.

## Live Mapping Playground · Max, 15-oct 18:28 (vía h1)

Sus palabras: «también tendré conectado un Circuit Tracks al que quiero poder controlarle los parámetros y notas MIDI
etc vía USB, también tendré un cocodrilo de hule que lo quiero hacer como un instrumento en vivo, un teclado/piano que
quiero poder proyectarle cosas... pero lo más importante es que prepares una sesión y un orquestador de este live
mapping playground con una secretaria que tenga muchos tools y pueda hacer request para mejorar o crear features tanto
en temas de sonido como visuales, todo con hot reload, y con herramientas para hacer crop visual en objetos, entender
su geometría, tracking de gestos, entender las cámaras, controlar el Ableton, etc. Quiero también una galería de
efectos que se ajusten a la geometría de todo, entiendan los contornos de los objetos, las texturas, reconozcan
cuadros y usando el feed de la cámara los proyecten planos, todo un menú de efectos y todos pueden ser spawneados.
Para el body tracking también me debe escanear y proyectar en el espacio 3D mi pose con un modelo ajustado a mi
cuerpo. Se debe reconstruir un live PBR 3D world, accurate, con las dimensiones y las posiciones de todo.»

Resumen de alcance:
- Instrumentos: Circuit Tracks por USB MIDI (parámetros y notas), cocodrilo de hule como instrumento en vivo
  (tracking + gestos → sonido), teclado/piano con proyección encima, Ableton (ya hay MCP en el Mac).
- Playground en vivo con hot reload de visuales y sonido; galería de efectos spawneables que leen geometría, contornos,
  texturas y cuadros (proyección plana del feed de cámara sobre un cuadro).
- Secretaria: agente con muchos tools (crop y segmentación de objetos, geometría, gestos, cámaras, efectos, Ableton,
  MIDI, LED DMX) que opera en vivo y deja pedidos de features; una unidad los implementa con hot reload.
- Body tracking con modelo ajustado al cuerpo de Max (escaneo + pose en 3D proyectada).
- Mundo PBR vivo y exacto: dimensiones y posiciones de todo.
