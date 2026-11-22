# Operación de la demo

Cómo se corre la demo de las tres escenas en la máquina de origen (macOS), qué revisar antes y qué hacer cuando algo
falla. Instalación en una máquina nueva: [skills/wui/SKILL.md](../../skills/wui/SKILL.md).

## Qué tiene que estar corriendo

| Proceso | Cómo se lanza | Cómo saber que está |
|---|---|---|
| Gateway local, puerto 9217 | `cd cloud/gateway && PORT=9217 PCX_OPEN_SESSIONS=1 uv run python -m pcx_gateway` (con `consola-local.patch` aplicado) | `curl -s -o /dev/null -w '%{http_code}\n' http://localhost:9217/health` → `200` |
| Consola `/mesa` en Chrome | Abrir `http://localhost:9217/mesa`, activar cámaras, lanzar proyección | La ventana «WUI · proyector» está en el proyector, a pantalla completa |
| Detección, puerto 8805 | En `sl-scanner`: `~/hardware/models/slscan-segmentation/semantic-venv/bin/python scripts/detector_dino.py` | `lsof -iTCP:8805 -sTCP:LISTEN` |
| Archivos, puerto 9300 | En `v2-mesa`: `python3 -m http.server 9300 --bind 127.0.0.1` | `http://127.0.0.1:9300/dashboard.html` abre |
| Control, puerto 9301 | En `v2-mesa`: `doppler run -p platanus-hack-26 -c dev -- ../sl-scanner/.venv/bin/python mesa_ctl.py` | `curl -s http://127.0.0.1:9301/estado` responde JSON |

No tiene que estar corriendo: el motor Rust (`pcx`). Apaga la proyección para tomar su foto limpia y rompe el relevo
sin parpadeo de las escenas.

La cámara la abren los procesos de escena directamente (dispositivo 0, AVFoundation) y también la consola. Si otro
programa la tiene abierta, puede entregar 1080p en vez de 4K; el código se adapta al tamaño que reciba.

## Antes de la demo

1. **Calibrar** si la cámara, el proyector o la mesa se movieron: `../sl-scanner/.venv/bin/python calibrar.py`, con
   el teléfono de referencia sobre la mesa y los objetos altos del escritorio en su lugar (el escaneo de luz
   estructurada mide su encaje). Unos dos minutos. `--sin-sl` omite la luz estructurada.
2. **Pestaña `/mesa` a la vista.** Debe ser la pestaña activa de su ventana y la ventana no puede estar minimizada. En
   segundo plano Chrome frena las manos a un cuadro por segundo.
3. **Agentes de voz creados**: `out/lumo-agente.json` y `out/lab-agente.json` existen. Si no:
   `doppler run -p platanus-hack-26 -c dev -- python lumo_agente.py` y lo mismo con `lab_agente.py`.
4. **Permisos de Chrome**: micrófono para `http://127.0.0.1:9325` y, en el menú Ver → Opciones para desarrolladores, la
   opción que permite JavaScript desde eventos de Apple (el nombre exacto cambia según la versión de Chrome). Sin eso el botón proyectado de «Hablar» no puede partir la conversación.
5. **Sonido**: un solo parlante, lejos del micrófono. Las escenas no tienen música para evitar acoples.
6. **Laboratorio**: los objetos fijos donde dice `lab-modelo.json`. Si se movió alguno, corregir su rectángulo ahí.

## Correr la demo

El control tiene tres acciones. Se pueden dar desde el dashboard (`http://127.0.0.1:9300/dashboard.html`), desde la
presentación (`presentacion.html`) o con `curl`.

| Acción | Tecla en el dashboard | HTTP | Qué pasa |
|---|---|---|---|
| Preparar la siguiente | P | `/preparar` | La escena detecta o rastrea sin proyectar y **se lanza sola** al estar lista. |
| Preparar una específica | E, S, L | `/preparar?escena=escritorio\|solar\|lab` | Igual, con esa escena. |
| Preparar sin lanzar | — | `/preparar?manual=1` | Queda esperando «Dale». |
| Dale | D o Enter | `/dale` | Arranque con el logo y aparece la escena. Solo actúa si está preparada. |
| Terminar | T o X | `/fin` | Mata los procesos y limpia la proyección. |
| Preparar y dar de una vez | — | `/start?escena=…` | Para pruebas. |
| Estado | — | `/estado` | `{vivos, escena, fase, siguiente}` |

Secuencia: mesa de escritorio puesta → Preparar → (aparece) → Terminar → cambiar a los platos → Preparar → Terminar →
montar el laboratorio → Preparar.

Tiempos según `HOJAS.md`: sistema solar y laboratorio quedan listos en unos 4 s; escritorio tarda unos 20 s, por eso
se prepara antes de empezar. Son cifras anotadas por quien operó la mesa, no medidas en esta revisión.

Nota: `DEMO.md` describe «Preparar» y «Dale» como dos pasos manuales. Desde el commit `03eaf5b` el lanzamiento es
automático salvo con `manual=1`; para escritorio, que se prepara antes de empezar a hablar, conviene `manual=1`.

## Registros

Cada proceso escribe en `out/<nombre>.log` (`escritorio_auto.log`, `gato.log`, `opciones.log`, `interaccion.log`,
`solar_vivo.log`, `lab_vivo.log`). `mesa_ctl.py` no guarda registro propio.

```sh
tail -f out/lab_vivo.log
curl -s http://127.0.0.1:9301/estado
curl -s http://127.0.0.1:9302/poses | head -c 400     # solo con sistema solar o laboratorio encendido
```

## Problemas conocidos y qué hacer

| Síntoma | Causa | Qué hacer |
|---|---|---|
| Sistema solar: suena el arranque, sale el logo y no aparece nada | En `03eaf5b`, `solar_vivo.py` usa `perdido` antes de definirla (línea 599): el proceso muere al arrancar. Ver [ESCENAS.md](ESCENAS.md). No ejecutado para confirmar. | Revisar `out/solar_vivo.log`. Si dice `NameError: name 'perdido'`, mover esa línea debajo de la que define `perdido`. |
| Sistema solar o laboratorio: aparece el logo pero nada se mueve | El widget no puede leer `127.0.0.1:9302`: falta `consola-local.patch` en el gateway local, o la ventana del proyector no se recargó después de aplicarlo | Aplicar el parche y recargar la ventana del proyector. |
| Las manos van a saltos o los toques no responden | La pestaña `/mesa` quedó en segundo plano | Dejarla como pestaña activa de su ventana. |
| Todo lo proyectado está corrido | Se movió la cámara, el proyector o la mesa | `calibrar.py`. |
| Luz derramada detrás de la taza, la lata o el plátano | El objeto se movió después del escaneo | `calibrar.py` con el objeto en su lugar. |
| Escritorio no arma nada, o el registro dice «detección sin objetos» | El servicio de `:8805` no está, o la mesa quedó sin objetos reconocibles | Levantar `detector_dino.py`; mirar `out/3-deteccion.jpg`. |
| Escritorio no se rearma al mover un objeto | Jev vio una mano sobre la mesa y espera a que salga | Retirar manos y brazos de la mesa. |
| El botón «Hablar» no hace nada | La página `127.0.0.1:9325` no está abierta, Chrome no permite JavaScript desde eventos de Apple, o falta el permiso de micrófono | Abrir la página a mano y apretar su botón; revisar los permisos. |
| La voz responde «sin URL firmada» | Falta `ELEVENLABS_API_KEY` (el control no se lanzó con Doppler) o no existe `out/lumo-agente.json` / `out/lab-agente.json` | Relanzar `mesa_ctl.py` con `doppler run`; crear el agente. |
| La voz se acopla | El parlante entra al micrófono | Bajar el volumen o alejar el parlante. |
| Laboratorio: los compartimentos o los rótulos no calzan | Un objeto fijo se movió | Corregir su rectángulo en `lab-modelo.json` y volver a preparar. |
| Laboratorio: alerta de teléfono falsa | Un objeto oscuro y rectangular de unos 15 × 7,5 cm fuera de la caja | Retirarlo de la mesa. |
| Una escena queda a medias al cambiar | Quedó un proceso vivo | `curl http://127.0.0.1:9301/fin`; si `/estado` aún lista algo en `vivos`, `pkill -f <script>`. |
| La mesa queda con widgets viejos | Se mataron procesos sin pasar por el control | `/fin` quita los widgets conocidos. Para borrar todo: `scene` y `remove` por MCP. |
| La ventana del proyector perdió la pantalla completa | Cambio de foco o de pantalla | `./frente.sh`. |
| `mesa_ctl.py` no parte: «Address already in use» | Ya hay un control en 9301 | `lsof -iTCP:9301 -sTCP:LISTEN` y cerrar el anterior. |
| El gateway desplegado tarda o no responde | El plan gratuito de Render se duerme tras 15 min sin tráfico | Reintentar; la primera respuesta puede tardar. La demo no depende de él. |

## Cosas que conviene saber

- `/fin` usa `pkill -f <nombre del script>`: mata **cualquier** proceso cuya línea de comandos contenga ese nombre,
  también uno lanzado a mano en otra terminal.
- Sistema solar y laboratorio usan los mismos puertos (9302 y 9325): nunca a la vez.
- Dos carpetas `v2-mesa`: en la máquina de origen la demo corre desde una copia fuera del repositorio
  (`~/code/platanus-hack-26/v2-mesa`). Lo que se edita ahí no está en git hasta copiarlo a `experimentos/v2-mesa/`.
- `out/` no está versionada ni excluida en `.gitignore`: ahí quedan los ids de los agentes de voz, fotos de la mesa y
  notas de voz transcritas. No hacer `git add` de esa carpeta.
- `HOJAS.md` menciona `remoto/LEEME.md` (transmisión desde el teléfono de Matilda); ese archivo no está en el repositorio.

## Apagar todo

```sh
curl -s http://127.0.0.1:9301/fin          # escenas y proyección
# después, Ctrl-C en mesa_ctl.py, en el servidor de 9300, en detector_dino.py y en el gateway
```
