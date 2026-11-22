# SL Scanner · pintar con luz

Un taller experimental para proyectar animaciones sobre cuadros, telas y objetos.
La cámara observa los patrones del proyector, el programa prepara la escena y el
dashboard permite encender efectos y cambiar colores, movimiento e intensidad.

**¿Vienes a crear? Empieza por [la guía para artistas](docs/GUIA-ARTISTAS.md).**
El [índice](docs/INDICE.md) reúne la documentación técnica y los experimentos.

Esta entrega incluye el código, documentación, una demo sin equipos y dos imágenes
de efectos sobre objetos. [BUNDLE.md](docs/BUNDLE.md) explica el origen, las exclusiones
y la verificación. Es una copia para experimentar, no un instalador de un producto terminado.

## Qué funciona y qué requiere preparación

| Camino | Estado y requisitos |
|---|---|
| Demo y dashboard | Objetos inventados, efectos básicos y controles; solo computador |
| Mapping de escena fija | Flujo `dale` → escena 2D → runtime → efectos; requiere cámara y proyector compatibles |
| Tela, vectores y estampados | Código incluido; requieren foto y máscaras preparadas de tu propia escena |
| Identificación automática | Preparación adicional con modelos locales, no incluidos; ver [escena automática](docs/ESCENA-AUTOMATICA.md) |
| Nube, malla y materiales 3D | Herramientas y pruebas sintéticas incluidas; dependen de calibración, capturas y contratos válidos |
| Secretaria por voz | Opcional; requiere cuenta/configuración propia de ElevenLabs y conexión a internet |

La precisión física de 2 mm, los materiales medidos de la sala y la proyección que
sigue al mover equipos **no están certificados**. Un efecto que parece tener relieve
no demuestra que exista una malla 3D medida. SPEC y PLAN conservan la historia y metas;
no son una lista de funciones disponibles.

## Equipo y software

- Para probar: Mac, Python 3.12 o 3.13, navegador y conexión para instalar dependencias.
  Ejecutar desde la carpeta descomprimida. No hay que compilar ni instalar Node.
- Para proyectar: proyector conectado como **pantalla extendida**, conexión HDMI o
  adaptador, soporte estable y cámara USB tipo Osmo Pocket 3 en modo webcam.
  El flujo `dale` está preparado para una cámara llamada `osmo` y proyector 1920×1080.
  Otro modelo o resolución necesita ajustar la captura; no basta con enchufarlo.
- Cámara y proyector deben ver la misma zona. No cambiar foco, zoom o corrección
  trapezoidal después del escaneo. La autoexposición de algunas webcams limita el resultado.
- Dejar espacio para dependencias y nuevas sesiones: el ZIP es pequeño, las capturas
  pueden ocupar cientos de MB por escaneo. No guardar sesiones dentro del ZIP.
- PC Windows con GPU NVIDIA: opcional para modelos y trabajo pesado. No es necesario
  para la demo ni para los efectos CPU. [Preparación de Windows](windows/README.md).
  Sus scripts de administración no forman parte de la instalación básica del Mac.

## Instalar una vez en el Mac

1. Descomprime el ZIP y abre Terminal en la carpeta `sl-scanner`.
2. Comprueba Python y prepara el entorno:

```sh
python3 --version
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Si no existe Python 3.12/3.13, instálalo antes de continuar. No uses un entorno con
`opencv-python-headless` para proyectar; las ventanas necesitan OpenCV/pygame normales.
Los rangos de dependencias no son un lockfile: las versiones verificadas están en BUNDLE.

3. Prueba sin cámara ni proyector:

```sh
python scripts/demo.py
```

Se abre el dashboard en **http://127.0.0.1:8795**. Ve a **Efectos**, elige
el cocodrilo y activa `contour_glow`; cambia el color. La miniatura muestra lo que
dibujaría el proyector. No pulses **Nueva vista** durante esta primera demo: inicia
otro flujo y puede detener el runtime. Cierra con **Ctrl+C** en esa terminal.
En cada terminal nueva activa el entorno con `source .venv/bin/activate`.

Alternativa en dos terminales, sin apertura automática del navegador:

```sh
python -m playground serve --scan '' --scene examples/demo/scene.json
# En otra terminal, desde la misma carpeta y con el entorno activo:
python -m playground.dashboard
```

## Primera escena real: dale → efectos

1. Cierra la demo. Monta objetos mates, cámara horizontal y proyector fijo. Configura
   pantalla extendida 1920×1080. Revisa los equipos sin iniciar un escaneo:

```sh
python -m slscan cameras
python -m playground.dashboard
```

2. Abre http://127.0.0.1:8795. En **Nueva vista**, deja una vista para el primer ensayo,
   desmarca **ensayo sintético** y **sin runtime**. Inicia y pulsa **Continuar** cuando
   el proyector esté quieto. Durante las franjas y colores no muevas los objetos ni
   te cruces. Sigue el estado, sin asumir un tiempo fijo de término.
3. `dale` libera el runtime anterior, intenta calibrar, captura patrones y barrido RGB,
   prepara la escena 2D y ejecuta las etapas 3D disponibles. Sin escala o con ajuste
   insuficiente puede seguir en 2D; el visor indica qué etapas quedaron bloqueadas.
4. Si aparecen objetos en **Efectos**, prende un solo contorno y comprueba dónde cae.
   Luego suma texturas o movimiento. Si faltan objetos o aparecen nombres de la escena
   antigua, prepara la escena nueva con [ESCENA-AUTOMATICA](docs/ESCENA-AUTOMATICA.md).
   Esa preparación se ejecuta aparte de `dale` y requiere modelos locales.
5. Si algo se movió, repite **Nueva vista**. **Apagar todo** apaga los efectos;
   **Cancelar** detiene una captura en curso, no equivale a cerrar todos los servidores.

Equivalente por terminal (detiene runtimes/trackers previos del playground):

```sh
python -m playground.dale --views 1 --rotate 0
```

Para volver a una sesión propia ya preparada, reemplaza `MI_SESION`:

```sh
python -m playground serve --scan captures/MI_SESION --projector
```

Una foto y sus recortes no se pueden reutilizar si cambiaste la escena. Para calibración
multivista, la versión exportada de autocalib mueve **solo la cámara** y mantiene fijo
el proyector; `dale` comprueba si el backend soporta `--motion`. Sigue la instrucción
que efectivamente aparece. Revisa [P2-AUTOCALIB](docs/P2-AUTOCALIB.md) antes de usar
varias vistas: una distancia entre carcasas no es una medición del centro óptico.

## Pantallas del sistema

| Dirección local | Para qué sirve | Cómo se inicia |
|---|---|---|
| 8795 | Dashboard: Nueva vista, Continuar, efectos y miniatura | `python -m playground.dashboard` |
| 8790 | Motor de efectos; `/api/state` muestra estado y `/api/frame.png` la imagen | `python -m playground serve …` |
| 8796 | Etapas del pipeline 3D | Lo abre `dale`; también `python -m slscan pipeline serve reports/pipeline/demo --port 8796` |
| 8797 | Visor auxiliar de calibración | Comando explícito de abajo; no se abre automáticamente con cada dale |
| 8798 | Escena 3D registrada | Ver [P4](docs/P4-ESCENA.md); requiere su `scene.json` |

Ensayo técnico sin tocar hardware ni detener la demo:

```sh
python -m slscan pipeline run examples/demo/entrada-sintetica --synthetic --out reports/pipeline/demo
python -m slscan pipeline serve reports/pipeline/demo --port 8796
```

Visor auxiliar de una toma real (otra terminal):

```sh
python -m slscan metric viewer --state reports/calibracion --port 8797
# En el comando dale, agregar: --viewer-state "$PWD/reports/calibracion"
```

`dale --synthetic --no-runtime` también existe, pero primero detiene runtimes previos
con `pkill` y deja el visor abierto. Para una prueba aislada usa `pipeline run` como arriba.
Cada servidor en primer plano se cierra con Ctrl+C en su propia terminal.

## Secretaria e ideas

La voz puede pedir efectos, cambiar parámetros y dejar ideas pendientes. Requiere
configuración propia; no se entrega la cuenta original ni un agente ya conectado.
Instala `requirements-optional.txt` solo si la vas a usar. Configura voz/modelo y
credenciales por referencia según [la guía de voz](docs/VOZ-Y-CONFIGURACION.md).
Los comandos de publicar agente crean o actualizan recursos en la cuenta configurada.
El dashboard funciona sin voz.

## Comandos útiles

```sh
python -m playground effects
python -m playground snapshot --spawn contour_glow:cocodrilo --out reports/mi-demo.png
python -m playground.showcase --help
python -m slscan --help
python -m playground.secretaria estado
```

## Si algo falla

| Síntoma | Qué revisar |
|---|---|
| `No module named …` | Entorno activo, carpeta raíz y `pip install -r requirements.txt`; para voz/MIDI, opcionales |
| Puerto ocupado | Cierra el proceso anterior desde su terminal; no abras dos runtimes |
| Dashboard dice que runtime no responde | Inicia la demo o `playground serve`; 8795 no dibuja por sí solo |
| Proyector muestra escritorio o pantalla azul | Cable, entrada HDMI, pantalla extendida y resolución; luego reinicia solo la sesión de proyección |
| No detecta la cámara | Modo webcam USB, cable de datos, permiso Cámara para Terminal, cerrar otra app que la use |
| Bordes corridos | Nada debe moverse después del scan; revisar foco, zoom, keystone y repetir captura |
| Efecto vacío o `effect_error` | Revisar objeto, máscara, foto y catálogo; tela/vectores necesitan sus assets propios |
| `blocked` / `unverified` en 3D | Leer la causa en 8796; una demo sintética no verifica la sala física |
| Voz muda | Configuración propia, permiso Micrófono, cuenta conectada; probar con audífonos |
| Imagen muy lenta | Apaga efectos y vuelve a agregar de a uno; [rendimiento](docs/PERFORMANCE-EFECTOS.md) |

Los caminos a `captures/scan5`, reportes históricos y modelos en los documentos
antiguos son ejemplos de sesiones del desarrollo, **no archivos incluidos**.
