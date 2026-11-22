---
name: wui
description: "Instalar y operar WUI (World UI): una mesa con cámara cenital y proyector común sobre la que un agente ve y proyecta interfaz por MCP. Úsala para conectar un agente a una mesa (gateway desplegado o local), calibrar, proyectar un widget html con manos y eventos, encender las tres escenas de la demo (escritorio, sistema solar, laboratorio) o crear una experiencia nueva. También cuando haya que instalar WUI en una máquina nueva o diagnosticar por qué una escena no aparece."
---

# WUI: instalar y operar

WUI le da a un agente una mesa física: una cámara cenital para ver y un proyector para dibujar sobre los objetos. El
agente habla con la mesa por un servidor MCP que se llama `pcx` (nombre interno; WUI es el nombre visible).

Hay dos niveles, y conviene decidir cuál se necesita antes de instalar nada:

| Nivel | Qué permite | Qué exige |
|---|---|---|
| **A. Agente + mesa** | `look`, `project`, `calibrate`, `events_wait`: ver la mesa, proyectar widgets, recibir toques | Chrome, una cámara y un proyector. Nada que instalar: sirve el gateway desplegado. |
| **B. Escenas de la demo** | Escritorio, sistema solar y laboratorio, con percepción en milímetros y voz | macOS, gateway local parchado, entorno Python de `sl-scanner`, pesos de modelos, Doppler, ffmpeg. |

## Qué está verificado y qué no

Esta skill se escribió leyendo el código de `daniel/v2.1` (commit `03eaf5b`) y comprobando en la máquina de origen
(macOS, 22-nov-2026) lo que se podía comprobar sin interrumpir una demo en curso.

**Verificado:** el gateway desplegado responde (`/health` y `/mesa` con 200); el gateway local responde en 9217; el
cliente `scripts/pcxl.py` de esta skill lista las 13 tools del MCP local; `mesa_ctl.py` responde `/estado`;
`consola-local.patch` aplica limpio (`git apply --check`); el reemplazo de ruta de la sección 3 deja los 18 archivos
compilando; existen los pesos y entornos en las rutas indicadas; el ejemplo de widget coincide con el esquema de
`project` y con `html-layer.js`.

**No verificado:** la instalación desde cero en una máquina nueva (ningún paso de instalación se ejecutó en limpio);
proyectar el widget de ejemplo; correr `calibrar.py` y encender las escenas con las rutas ya reemplazadas; cualquier
cosa fuera de macOS. Cada paso sin verificar está marcado con **[sin verificar]**.

## 1. Nivel A: conectar un agente a una mesa

No requiere clonar nada. El gateway desplegado entrega los pasos en texto: `curl -fsS https://pcx-gateway.onrender.com/agente`.
Resumen (para un gateway local, cambiar la base por `http://localhost:9217`):

```sh
BASE=https://pcx-gateway.onrender.com
curl -fsS -X POST $BASE/api/sessions      # → session_id, node_id, node_token, user_token
```

1. Abrir en Chrome o Edge `$BASE/mesa#s=<session_id>.<node_token>.<user_token>`. Safari y Firefox no sirven para
   elegir la pantalla del proyector.
2. Pedirle a la persona: activar cámaras y elegir la cenital; elegir la pantalla del proyector y lanzar la proyección;
   activar el micrófono.
3. Conectar el MCP con el `user_token`:

```sh
claude mcp remove pcx 2>/dev/null; claude mcp add --transport http pcx $BASE/mcp --header "Authorization: Bearer <user_token>"
# Codex:
export PCX_USER_TOKEN=<user_token>; codex mcp remove pcx 2>/dev/null; codex mcp add pcx --url $BASE/mcp --bearer-token-env-var PCX_USER_TOKEN
```

4. Comprobar: `curl -fsS $BASE/api/nodes -H "Authorization: Bearer <user_token>"` debe mostrar el nodo
   `web-<session_id>` con `"connected": true`. Después `scene`, `look` y, si `look` dice `calibrated=false`, `calibrate`.

Los tokens son secretos de esa sesión: no van a archivos del repositorio. El gateway desplegado corre en un plan
gratuito que se duerme tras 15 minutos sin tráfico; la primera respuesta puede tardar.

Tools del MCP (lista del gateway local): `scene`, `look`, `project`, `remove`, `ask`, `events_wait`, `run_flow`,
`calibrate`, `devices`, `home`, `history`, `scenes`, `present`. `project`, `remove`, `calibrate`, `ask` y `run_flow`
exigen `request_id` (clave de idempotencia).

## 2. Proyectar un widget

El widget que usan todas las escenas es un `effect` con `shader: "html"`: un iframe aislado sobre la proyección.
Ejemplo mínimo, con manos y un evento de vuelta:

```json
{
  "request_id": "hola-1",
  "widgets": [
    {"type": "effect", "id": "manos", "shader": "hands", "params": {"skeleton": false, "trail": false}},
    {"type": "effect", "id": "hola", "shader": "html", "rect_mm": [300, 180, 400, 200],
     "params": {
       "html": "<div id=\"b\">Posa la mano aquí</div>",
       "css": "html,body{margin:0;background:transparent}#b{position:absolute;inset:0;display:grid;place-items:center;border:4px solid #fff;border-radius:16px;font:700 28px system-ui;color:#fff}#b.on{background:#fff;color:#000}",
       "js": "const b=document.getElementById('b');let t0=0,hecho=false;wui.onHands(hs=>{const [w,h]=wui.size;const en=(hs||[]).some(m=>m.tip&&m.tip[0]>0&&m.tip[0]<w&&m.tip[1]>0&&m.tip[1]<h);const ahora=performance.now();if(en){if(!t0)t0=ahora;if(!hecho&&ahora-t0>300){hecho=true;b.classList.add('on');wui.emit('toque:hola');}}else{t0=0;hecho=false;b.classList.remove('on');}});"
     }}
  ]
}
```

Se manda con la tool `project`. Después `events_wait` con el `cursor` anterior devuelve un evento `widget_pressed`
cuyo valor es `toque:hola`. Para borrarlo: `remove` con `{"request_id": "hola-rm-1", "ids": ["hola", "manos"]}`.

**[sin verificar]** Este ejemplo no se proyectó. Su estructura está comprobada contra el esquema de `project`
(`type`, `id`, `shader`, `rect_mm`, `params`), contra `cloud/gateway/src/pcx_gateway/console/html-layer.js` (objeto
`wui`) y contra el patrón de `experimentos/v2-mesa/manos_capa.py`, que usa las mismas llamadas.

Reglas del widget html, leídas de `html-layer.js`:

- `rect_mm` es `[x, y, ancho, alto]`. Dentro, 1 px de CSS es 1 unidad del canvas. Sin `rect_mm` se centra en el ancla
  con `params.size_mm` (300 × 200 por defecto).
- Sin `params.js`: no corre código propio; puede cargar imágenes `https:`; un elemento con `data-press="nombre"` emite
  `widget_pressed` al tocarlo.
- Con `params.js` (widget vivo): el script corre sin red (ni imágenes `https:`; usar `data:`). Dispone de
  `wui.onHands(fn)` (unas 30 veces por segundo; cada mano trae `tip`, `palm`, `pinch`, `inside`, `handed`, `points`,
  en coordenadas del widget), `wui.emit(valor)` (máximo 10 por segundo y 500 caracteres), `wui.size` y `wui.onAudio(fn)`.
- Un script que deja de dar señales por 4 s se detiene solo.
- Las manos se activan con el efecto `hands` (el primer widget del ejemplo; todas las escenas lo incluyen) o con la
  casilla «Manos» de la consola. La pestaña `/mesa` debe ser la pestaña activa de su ventana: en segundo plano Chrome
  frena las manos a un cuadro por segundo.

Sobre las coordenadas: después de `calibrate`, el canvas de la consola mide 1000 × 563 unidades que la consola llama
«mm». No son milímetros reales de la mesa. Las escenas del nivel B construyen el marco real aparte
(`fp-metric.json`) y convierten con su homografía `mm_to_canvas`.

## 3. Nivel B: instalar las escenas en una máquina nueva

**Todo este nivel está [sin verificar] en una máquina limpia.** Lo que sigue es lo que el código exige, comprobado
contra la máquina de origen.

### 3.1 Dependencias del sistema

| Qué | Para qué | Comprobar |
|---|---|---|
| macOS | Los scripts usan AppleScript (`osascript`), `afplay`, AVFoundation y `/usr/bin/python3` | `sw_vers` |
| Google Chrome | Consola, proyector, manos y página de voz. Los scripts buscan ventanas de «Google Chrome» por nombre | `ls /Applications \| grep Chrome` |
| Python 3.12 o 3.13 | Entorno de `sl-scanner` (no 3.14, según el `README.md` raíz original). Origen: 3.13.2 | `python3.13 -V` |
| `uv` | Correr el gateway local | `uv --version` |
| `ffmpeg` | Grabar la nota de voz del escritorio | `ffmpeg -version` |
| Doppler CLI con acceso al proyecto `platanus-hack-26`, configuración `dev` | Llaves: `ELEVENLABS_API_KEY` (voz y transcripción) y `AI_GATEWAY_API_KEY` (Jev) | `doppler secrets --only-names -p platanus-hack-26 -c dev` |
| Xcode Command Line Tools | Proveen `/usr/bin/python3`, con el que los scripts lanzan `pcxl.py` | `/usr/bin/python3 -V` |

Nunca imprimir las llaves. Sin `AI_GATEWAY_API_KEY`, Jev responde «sin llave» y las escenas siguen con valores por
defecto (no se lee la libreta ni se verifica el vaso). Sin `ELEVENLABS_API_KEY` no hay voz.

Variables opcionales: `JEV_VISION_MODEL` (por defecto `typesafe-ai/jev-1.14.0`), `PROY_K` (ajuste del encaje estimado,
1,28), `SOLAR_KP` (corrección por altura de las bolas, `0.13,0.28`).

### 3.2 Entorno `sl-scanner/.venv`

El pipeline importa código de `sl-scanner` (el motor de Max) desde una ruta fija: `~/code/platanus-hack-26/sl-scanner`
(`pipeline.py`, `calibrar.py`). De ahí usa `playground.appearance_prepare.MobileSAM`, `python -m slscan scan` y
`scripts/detector_dino.py`.

```sh
mkdir -p ~/code/platanus-hack-26 && cd ~/code/platanus-hack-26
git clone -b hack/daniel-mesa git@github.com:MAXMARDONES/sl-scanner.git      # rama con detector_dino.py
cd sl-scanner && python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt onnxruntime
```

- La rama importa: `scripts/detector_dino.py` existe en `hack/daniel-mesa` y **no** en `master` ni en la carpeta
  `engine/` de este repositorio.
- `onnxruntime` no está en `requirements.txt` pero MobileSAM lo importa; por eso va explícito.
- Paquetes que el código de la mesa usa de verdad: `numpy`, `opencv-python`, `onnxruntime` y, vía `slscan`, lo que
  trae `requirements.txt`. Versiones en la máquina de origen: numpy 2.5.5, opencv-python 5.1.0.95, onnxruntime 1.32.0.
  `scipy` solo lo usan `fit3d.py` y `fit3d_b.py`, que están fuera de uso. El resto de los scripts usa la biblioteca estándar.

### 3.3 Pesos de modelos

| Modelo | Usado por | Dónde se guarda | De dónde se baja |
|---|---|---|---|
| **MobileSAM** (`mobile_sam_image_encoder.onnx`, 28 MB, y `sam_mask_decoder_single.onnx`, 17 MB) | `pipeline.py` | `~/hardware/models/slscan-segmentation/` (ruta fija en `pipeline.py`) | Hugging Face `Acly/MobileSAM`. Las URL fijadas a una revisión y sus `sha256` están en `scripts/install/modelos.json` de este repositorio. |
| **Grounding DINO tiny** (`IDEA-Research/grounding-dino-tiny`) | `detector_dino.py` | Caché de Hugging Face: `~/.cache/huggingface/hub/models--IDEA-Research--grounding-dino-tiny` | Hugging Face. El servicio lo carga con `local_files_only=True`: **no descarga solo**, hay que bajarlo antes. |
| **MediaPipe Hands** (`hand_landmarker.task`) | La consola, en el navegador (`hands.js`) | No se guarda en disco: Chrome lo carga en cada sesión | `storage.googleapis.com/mediapipe-models/…/hand_landmarker.task`, con la biblioteca `@mediapipe/tasks-vision@0.10.14` desde jsDelivr. Necesita internet. |

```sh
# MobileSAM
mkdir -p ~/hardware/models/slscan-segmentation && cd ~/hardware/models/slscan-segmentation
python3 - <<'PY'
import json, hashlib, urllib.request, pathlib
for m in json.load(open(pathlib.Path.home()/'RUTA-AL-REPO/scripts/install/modelos.json'))['modelos']:
    if m['uso'] != 'sam': continue
    urllib.request.urlretrieve(m['url'], m['nombre'])
    assert hashlib.sha256(open(m['nombre'],'rb').read()).hexdigest() == m['sha256'], m['nombre']
    print('ok', m['nombre'])
PY

# Grounding DINO: entorno aparte con torch y transformers, y el modelo en la caché
python3.13 -m venv ~/hardware/models/slscan-segmentation/semantic-venv
~/hardware/models/slscan-segmentation/semantic-venv/bin/pip install torch transformers pillow
~/hardware/models/slscan-segmentation/semantic-venv/bin/python -c "
from transformers import AutoModelForZeroShotObjectDetection as M, AutoProcessor as P
P.from_pretrained('IDEA-Research/grounding-dino-tiny'); M.from_pretrained('IDEA-Research/grounding-dino-tiny')"
```

**[sin verificar]** Ninguno de estos dos bloques se ejecutó: en la máquina de origen los pesos ya estaban. La ruta
`RUTA-AL-REPO` hay que reemplazarla. El entorno de origen tiene torch 2.15.0 y transformers 5.23.0. El entorno
`semantic-venv` puede vivir en otra ruta: solo se usa para lanzar el servicio.

### 3.4 El cliente `pcxl.py` (deuda de portabilidad)

Los scripts no usan una biblioteca MCP: lanzan `/usr/bin/python3 <ruta>/pcxl.py <tool> '<json>'`. Esa ruta está
escrita como constante en **18 archivos** y apunta a un directorio temporal de la máquina de origen
(`/private/tmp/claude-501/…/scratchpad/pcxl.py`), que desaparece con un reinicio. El cliente original no está en el
repositorio. `escritorio_auto.py` además lee el archivo `local-token` de esa misma carpeta.

Esta skill trae una copia portable: [scripts/pcxl.py](scripts/pcxl.py). Para dejar la carpeta funcionando:

```sh
mkdir -p ~/.wui && cp skills/wui/scripts/pcxl.py ~/.wui/pcxl.py
printf '%s' "<user_token de la sesión local>" > ~/.wui/local-token && chmod 600 ~/.wui/local-token

cd experimentos/v2-mesa
sed -i '' -E "s#/private/tmp/claude-501/[^/]+/[^/]+/scratchpad/pcxl\.py#$HOME/.wui/pcxl.py#g" *.py
grep -l "scratchpad/pcxl.py" *.py      # no debe listar nada
```

Verificado: el `sed` reemplaza los 18 archivos y todos siguen compilando; el cliente portable responde `list` contra
el gateway local. **[sin verificar]**: correr las escenas con la ruta reemplazada.

Lo que falta para que esto sea portable de verdad, sin `sed`:

1. Mover `pcxl.py` al repositorio y que los scripts lo resuelvan relativo a su propia carpeta, o por una variable de entorno.
2. Leer la URL del MCP y el token de variables (`WUI_MCP_URL`, `WUI_TOKEN`), como ya hace la copia de esta skill.
3. Reemplazar las otras rutas fijas: `~/code/platanus-hack-26/sl-scanner`, `~/hardware/models/slscan-segmentation` y
   `/usr/bin/python3`.
4. Sacar a un archivo de configuración lo que hoy está escrito en el código y depende del montaje: las esquinas del
   tablero y la zona del teléfono (`fp_geo.py`), las medidas del teléfono de referencia (`fp_metric.py`), la posición
   de cámara y proyector (`altura.py`), el índice de cámara 0, el título de la ventana del proyector y `localhost:9217`.
5. Crear `out/` al arrancar: no está versionada y varios scripts fallan si no existe.

### 3.5 Gateway local con el parche

Sistema solar y laboratorio necesitan que sus widgets lean `http://127.0.0.1:9302/poses`, y la consola lo bloquea
salvo con `experimentos/v2-mesa/consola-local.patch`. Por eso estas dos escenas no funcionan contra el gateway desplegado.

```sh
git apply experimentos/v2-mesa/consola-local.patch          # no commitear: es un cambio solo local
cd cloud/gateway && uv sync --frozen
PORT=9217 PCX_OPEN_SESSIONS=1 uv run python -m pcx_gateway
```

Después crear la sesión local y abrir la mesa como en la sección 1, con `BASE=http://localhost:9217`, y guardar el
`user_token` en `~/.wui/local-token`. El puerto tiene que ser 9217: está escrito en `escritorio_auto.py`, `frente.sh` y
en la copia original de `pcxl.py`. **[sin verificar]** en limpio; en la máquina de origen el gateway local corre así
desde otro worktree con el parche aplicado.

En Chrome: permitir cámara, micrófono y manejo de ventanas para `localhost:9217`; permitir micrófono para
`127.0.0.1:9325`; y activar, en Ver → Opciones para desarrolladores, la opción que permite JavaScript desde eventos
de Apple (la página de voz se maneja con AppleScript).

### 3.6 Servicios auxiliares

```sh
# Detección (deja el modelo en memoria; la primera carga tarda)
cd ~/code/platanus-hack-26/sl-scanner && ~/hardware/models/slscan-segmentation/semantic-venv/bin/python scripts/detector_dino.py

# Dashboard y archivos de out/ (desde experimentos/v2-mesa)
mkdir -p out && python3 -m http.server 9300 --bind 127.0.0.1

# Control de la demo (desde experimentos/v2-mesa)
doppler run -p platanus-hack-26 -c dev -- ~/code/platanus-hack-26/sl-scanner/.venv/bin/python mesa_ctl.py
```

El motor Rust (`pcx`) debe estar detenido: apaga la proyección para su foto limpia.

## 4. Calibrar

```sh
cd experimentos/v2-mesa
~/code/platanus-hack-26/sl-scanner/.venv/bin/python calibrar.py            # completa, unos 2 minutos
~/code/platanus-hack-26/sl-scanner/.venv/bin/python calibrar.py --sin-sl   # sin luz estructurada
```

Antes: un teléfono de tamaño conocido sobre la mesa (el código supone un iPhone XR, 150,9 × 75,7 mm) y los objetos
altos en su lugar. El script pone el proyector a pantalla completa, corre `calibrate precise`, saca una foto 4K,
calcula el marco en milímetros (`fp-metric.json`) y hace el escaneo de luz estructurada (`fp-parallax.npz`).

**En una mesa distinta esto no va a funcionar sin editar código**: `fp_geo.py` parte de las esquinas aproximadas del
tablero escritas a mano para la foto de la mesa de origen y busca el teléfono en una zona fija de la imagen. Hay que
abrir `hires-0.jpg`, anotar las cuatro esquinas del tablero (arriba izquierda, arriba derecha, abajo derecha, abajo
izquierda, en píxeles de la foto 4K) en la matriz `A` y ajustar la zona `x0, y0` del teléfono. Si el teléfono es otro,
cambiar sus medidas en `fp_metric.py`. **[sin verificar]** fuera de la mesa de origen.

Cómo saber que quedó bien: `fp_metric.py` imprime «fono medido (mm)», que debe dar cerca de 75,7 y 150,9, y el tamaño
del tablero, que debe coincidir con la mesa real. `fp-geo.jpg` muestra los bordes encontrados.

Detalle del pipeline: `docs/maestro/PIPELINE.md`.

## 5. Encender cada escena

Con el control corriendo (`:9301`):

| Escena | Preparar y lanzar sola | Qué necesita en la mesa |
|---|---|---|
| Escritorio | `curl "http://127.0.0.1:9301/preparar?escena=escritorio"` | Computador, libreta, lata, taza, mouse, teléfono, plátano. Servicio de detección en `:8805`. |
| Sistema solar | `curl "http://127.0.0.1:9301/preparar?escena=solar"` | Tres platos con sus bolas y el engranaje, lámina impresa, libreta. `out/lumo-agente.json`. |
| Laboratorio | `curl "http://127.0.0.1:9301/preparar?escena=lab"` | El montaje de `lab-modelo.json`, con cada objeto fijo en su rectángulo. `out/lab-agente.json`. |

- `/preparar` lanza la escena sola cuando está lista. Con `&manual=1` espera a `curl http://127.0.0.1:9301/dale`.
- Apagar: `curl http://127.0.0.1:9301/fin`. Estado: `curl http://127.0.0.1:9301/estado`.
- Desde el dashboard (`http://127.0.0.1:9300/dashboard.html`): E, S y L preparan una escena; P la siguiente; D o Enter
  es «Dale»; T o X termina.
- Agentes de voz, una vez por cuenta de ElevenLabs:
  `doppler run -p platanus-hack-26 -c dev -- python3 lumo_agente.py` y lo mismo con `lab_agente.py`. Copian la
  configuración de voz de dos agentes de Max por su id; con otra cuenta de ElevenLabs esos ids no existen y hay que
  cambiarlos. **[sin verificar]** con otra cuenta.
- Sistema solar con otro montaje: con la mesa sin proyección, `solar_vivo.py --modelo`, y corregir `ANOT` y `LAMINA`
  dentro de `solar_vivo.py`.
- Laboratorio con otro montaje: corregir los rectángulos de `fijos` en `lab-modelo.json`.

Aviso sobre el commit `03eaf5b`: `solar_vivo.py` usa la variable `perdido` en la línea 599 antes de definirla en la
603. Leído el código, la escena de sistema solar muere al arrancar con `NameError`. Si `out/solar_vivo.log` lo
confirma, mover esa línea debajo de la definición. No se ejecutó para comprobarlo.

Operación y problemas conocidos: `docs/maestro/OPERACION.md`. Detalle de cada escena: `docs/maestro/ESCENAS.md`.

## 6. Crear una experiencia nueva

Hay dos caminos. El primero no necesita nada del nivel B.

**Camino corto: el agente es la escena.** Con el MCP conectado: `look` para ver la mesa, `project` con un widget html
(sección 2) y `events_wait` en un ciclo para reaccionar. Sirve para interfaces sobre posiciones que el agente ve en la
foto. No da milímetros reales ni seguimiento.

**Camino largo: un proceso de escena**, como las tres de la demo. El patrón, leído de `solar_vivo.py` y `lab_vivo.py`:

1. **Percibir.** Para objetos quietos, correr el pipeline con un archivo de nombres propio y leer el resultado:

   ```sh
   echo '{"a coffee mug": "taza", "a notebook": "libreta"}' > prompts-mia.json
   $PY pipeline.py una --dino --prompts prompts-mia.json --foto out/limpia-4k.jpg     # → out/objetos.json
   ```

   Cada objeto trae `etiqueta`, `centro_mm`, `tam_mm`, `poligono_mm` y, si es alto, `poligono_proy_mm`. La foto debe
   tomarse sin proyección encima. Para algo que se mueve, escribir un rastreador propio sobre la vista rectificada
   (`fp_rect.mapa`), como los platos de `solar_vivo.py`.
2. **Convertir a canvas.** Los polígonos están en milímetros reales; el proyector espera coordenadas del canvas:

   ```python
   Hm = np.array(json.load(open('fp-metric.json'))['mm_to_canvas'])
   def canvas(p):
       q = np.c_[np.asarray(p, float), np.ones(len(p))] @ Hm.T
       return (q[:, :2] / q[:, 2:]).round(1).tolist()
   ```

   Para objetos altos, dibujar sobre `poligono_proy_mm` y detectar la mano sobre `poligono_mm`, como hace `manos_capa.py`.
3. **Proyectar un widget a canvas completo** (`rect_mm: [0, 0, 1000, 563]`) con un `<canvas>` y el script en
   `params.js`, más el efecto `hands`. Los datos van incrustados en el script como JSON.
4. **Estado en vivo (opcional).** Si lo proyectado cambia varias veces por segundo, servir un JSON en
   `127.0.0.1:9302` y leerlo desde el widget con `fetch`. Es el único destino que permite el parche de la consola.
5. **Eventos.** El widget emite `wui.emit('algo:valor')`; el proceso los lee con `events_wait` en un ciclo, guardando el `cursor`.
6. **Arranque en dos tiempos.** Respetar `ESPERA=1`: preparar sin mostrar, escribir `out/lista.flag` al estar listo y
   mostrar recién cuando exista `out/visible.flag`.
7. **Registrar la escena** en `mesa_ctl.py`: agregarla a `ORDEN`, `NOMBRE` y `PROC`, su widget a `WIDGETS` (para que
   `/fin` lo borre) y su tiempo de arranque a `ARR`.
8. **Voz (opcional).** Copiar `lumo_agente.py`, cambiar prompt y herramientas, y reutilizar las clases `Voz` y `Poses`
   de `solar_vivo.py` como hace `lab_vivo.py` (`import solar_vivo as sv`, luego `sv.AGENTE`, `sv.TOOLS_VOZ` y `sv.RESOLVER`).

`manos_capa.py` (85 líneas) es el mejor punto de partida: lee objetos, los convierte, proyecta un widget con manos y
emite eventos, sin estado en vivo ni voz.

**[sin verificar]** No se creó una escena nueva siguiendo estos pasos; describen cómo están hechas las existentes.

## 7. Lista de verificación de «funciona»

Correr en orden. Cada línea dice qué debe salir.

```sh
# Nivel A
curl -s -o /dev/null -w '%{http_code}\n' https://pcx-gateway.onrender.com/health        # 200 (puede tardar si estaba dormido)

# Nivel B: herramientas
/usr/bin/python3 -V && uv --version && ffmpeg -version | head -1 && doppler --version
doppler secrets --only-names -p platanus-hack-26 -c dev | grep -E "ELEVENLABS_API_KEY|AI_GATEWAY_API_KEY"   # las dos

# Entorno y pesos
~/code/platanus-hack-26/sl-scanner/.venv/bin/python -c "import cv2, numpy, onnxruntime; print('ok')"
ls ~/code/platanus-hack-26/sl-scanner/scripts/detector_dino.py
ls ~/hardware/models/slscan-segmentation/mobile_sam_image_encoder.onnx ~/hardware/models/slscan-segmentation/sam_mask_decoder_single.onnx
ls -d ~/.cache/huggingface/hub/models--IDEA-Research--grounding-dino-tiny

# Rutas reemplazadas y cliente
cd experimentos/v2-mesa && grep -l "scratchpad/pcxl.py" *.py ; echo "(no debe listar archivos)"
test -s ~/.wui/local-token && echo "token presente"
/usr/bin/python3 ~/.wui/pcxl.py list | grep -c "::"                                      # 13

# Servicios
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:9217/health                    # 200
lsof -iTCP:8805 -sTCP:LISTEN | tail -1                                                   # un proceso Python
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9300/dashboard.html            # 200
curl -s http://127.0.0.1:9301/estado                                                     # {"ok": true, …}
grep -c "connect-src http://127.0.0.1:9302" ../../cloud/gateway/src/pcx_gateway/console/html-layer.js   # 1 (parche aplicado)

# Mesa
/usr/bin/python3 ~/.wui/pcxl.py scene '{}' | head -c 300                                 # JSON con surface y widgets
/usr/bin/python3 ~/.wui/pcxl.py look '{}'                                                # IMAGE <ruta>: abrirla y ver la mesa
test -s fp-metric.json && test -s fp-parallax.npz && echo "calibrada"

# Escena
curl -s "http://127.0.0.1:9301/preparar?escena=escritorio"; sleep 30; curl -s http://127.0.0.1:9301/estado   # "fase": "encendida"
tail -3 out/escritorio_auto.log                                                          # «escena actualizada sin apagar: N objetos»
curl -s http://127.0.0.1:9301/fin
```

En la máquina de origen se comprobaron las líneas de `health` (ambas), `estado`, `list` (con la copia de esta skill y
el token de la sesión en curso) y la existencia de pesos y entornos. Las de `scene`, `look` y la escena no se
ejecutaron para no interferir con la demo.

## Errores frecuentes

| Qué se ve | Qué es |
|---|---|
| `FileNotFoundError … scratchpad/local-token`, `JSONDecodeError`, o los scripts no proyectan nada y no dan error | La ruta del cliente no se reemplazó (sección 3.4). `subprocess` captura la salida, así que el fallo es silencioso. |
| `pcxl: falta el token` o HTTP 401 | Falta `~/.wui/local-token`, o es de otra sesión. |
| `ModuleNotFoundError: playground` | `sl-scanner` no está en `~/code/platanus-hack-26/sl-scanner`. |
| `pipeline.py` termina con error de conexión a `127.0.0.1:8805` | El servicio de detección no está corriendo. |
| `OSError … local_files_only` al lanzar `detector_dino.py` | Grounding DINO no está en la caché de Hugging Face. |
| `FileNotFoundError: out/…` | Falta crear `out/`. |
| Sistema solar o laboratorio: logo y nada más | Falta el parche de la consola o recargar la ventana del proyector; o el aviso de la sección 5. |
| Las manos no responden | La pestaña `/mesa` no está a la vista. |
