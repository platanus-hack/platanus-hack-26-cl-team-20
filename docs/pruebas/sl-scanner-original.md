# sl-scanner original en el MacBook de Daniel

Engine de Max (`MAXMARDONES/sl-scanner` @ `b8fecff`) en `~/code/platanus-hack-26/sl-scanner/` del MacBook.
Hardware: proyector T749 por HDMI y webcam USB genérica (no es la Osmo).
Las horas son las del registro: los logs de cada paso o, en las pruebas hechas por ssh, la hora en que se anotaron. La entrada de las 00:40 son pruebas que se hicieron desde la sesión gráfica del MacBook, sacadas de sus logs.

---

## 00:15 · Detectar hardware

**Comando:** `system_profiler SPDisplaysDataType` y `system_profiler SPCameraDataType` (por ssh).
**Pasó:** el proyector aparece como `T749-fHD720`, 1920×1080 a 60 Hz, extendido (Mirror Off). La cámara aparece como `usb-webcam` (UVC VendorID 7532 / ProductID 259).
**Aprendido:** `slscan.capture.list_displays()` lo ve como **display 1** en (-200,-1080). Coincide con lo que espera `dale` (`--screen 1920x1080`).
**Falta:** nada en este paso.

## 00:22 · Instalación

**Comando:** `git clone git@github.com:MAXMARDONES/sl-scanner.git` → se quedó pegado en ~14 MB de 28 MB (red del evento). Se reemplazó por `rsync` de una copia limpia del mini (mismo `b8fecff`, origin GitHub). Luego `python3.13 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt`.
**Pasó:** OK. Quedaron numpy 2.5.5, opencv-python 5.1.0.95, pygame 2.6.1, fastapi 0.165, scipy 1.18.1.
**Aprendido:** con la red del evento, clonar desde GitHub es poco confiable; conviene tener una copia local para pasarla con rsync. El python3 por defecto del MacBook es 3.14 y no sirve: hay que usar 3.13.
**Falta:** —

## 00:26 · Cámara por ssh

**Comando:** `python -m slscan cameras` (por ssh). Después, el mismo comando en una Terminal abierta con `osascript`.
**Pasó:** `OpenCV: not authorized to capture video (status 0)` en todos los índices.
**Aprendido:** macOS (TCC) no da cámara a procesos lanzados por ssh. Hay que correr desde Terminal.app de la sesión gráfica y aceptar el permiso de Cámara para Terminal. La sesión de Claude del MacBook se dejó en un tmux lanzado desde Terminal (`tmux -L max-sl`) para que conserve ese permiso.
**Falta:** —

## 00:28 · Webcam que no es Osmo

**Comando:** lectura de `slscan/capture.py:resolve_camera`.
**Pasó:** `dale` siempre pide `--camera osmo`. El resolver reconoce la Osmo por cuadro vertical o 25 fps.
**Aprendido:** el código ya trae un override, `SLSCAN_CAMERA=<índice>` (`capture.py:393`), y `dale` se lo hereda a sus subprocesos. No hace falta tocar código.
**Falta:** confirmar cómo se resolvió la cámara en la corrida real: el dashboard no tenía `SLSCAN_CAMERA` en su entorno y la webcam reporta 20 fps.

## 00:30 · Modelos para la escena automática (SAM + etiquetas)

**Comando:** `~/.local/state/sl-scanner/instalar-modelos.sh`. Baja MobileSAM ONNX de `huggingface.co/Acly/MobileSAM` a `~/hardware/models/slscan-segmentation/`, instala `onnxruntime` en `.venv`, crea un venv semántico (torch 2.15 + transformers) en `…/slscan-segmentation/semantic-venv` y descarga `IDEA-Research/grounding-dino-tiny` a la caché de Hugging Face.
**Pasó:** OK a las 00:38. Grounding DINO tardó unos 8 min en bajar.
**Aprendido:** los modelos no vienen en el repo. Para `auto_scene` hay que pasar `--semantic-python ~/hardware/models/slscan-segmentation/semantic-venv/bin/python` (el README usa el python3.14 de Max).
**Falta:** —

## 00:40 · Primer `dale` real (lanzado desde el dashboard del MacBook)

**Comando** (lo armó el dashboard): `python -m playground.dale` con `--rotate 0 --downscale 1 --cam-size 2560x1440 --views 1`. Sesión `captures/dale-20261121-0040`.
**Pasó:**
- **Calibración automática:** capturó los 44 patrones y encontró 95.885 celdas comunes, pero **falló** en el ajuste con «coordenadas fuera del raster» (código 2).
- **Escaneo de luz estructurada:** OK. Decodificó 416.234 px de 3.686.400 (11,3 %). Sin rig, deja solo mapas (sin nube).
- **Radiometría:** OK, 36 patrones.
- **Escena 2D (recalib):** OK. 13 marcadores ArUco, mediana de error 1,75 px, 49 de 52 esquinas bajo 3 px.
- **Pipeline 3D:** queda en `blocked` (requiere `register_render` y `segment`). `escena3d`: sin rig ni malla.
- **Showcase `halos`:** `contour_glow` y `keys_map` devuelven HTTP 400.
- **Proyector:** ventana SDL 1920×1080 a pantalla completa en el display 1, vsync a 60 Hz.

**Aprendido:**
- Con webcam, el mapping 2D funciona aunque falle la calibración automática.
- La webcam no acepta control manual de exposición, balance de blancos ni autofoco (`accepted: false`).
- La recalibración segmenta con nombres de la sala de Max («torre», «controladores»), y `halos` apunta a objetos que no existen acá; de ahí los 400.
- Falta `pyobjc` (módulos `Quartz`/`objc`): no está en `requirements.txt`, así que la ventana no puede subir sobre la barra de menú y `pattern.log` se llena de `display_present falló` cada segundo.

**Falta:** decidir si se instala `pyobjc` (es una dependencia, no un cambio de código) y entender el fallo de autocalib con la webcam.

## 00:45 · Escena automática (MobileSAM + Grounding DINO)

**Comando:** `playground.auto_scene` sobre `dale-20261121-0040` (`reports/scene_regions/dale-20261121-0040/auto-20261121-004528-b24178/`).
**Pasó:** OK, quedan 10 objetos. Grounding DINO etiquetó lámpara (0,52), mouse pad, peluche, cojín, celular, mesa, mouse, cuadro, controlador MIDI y audífonos (scores 0,31–0,52). En el runtime quedaron 3 objetos con nombre (`lámpara 1`, `mouse 2`, `audífonos 3`) y 7 como `segmento N`.
**Aprendido:** la cadena completa de la escena automática corre en el MacBook con los modelos instalados. Las etiquetas bajo ~0,4 son poco confiables y el resto queda como «segmento».
**Falta:** —

## 00:56–01:07 · Efectos nuevos sobre la mesa (rama `hack/daniel-mesa`)

**Comando:** no lo lancé yo. En el checkout del MacBook aparecen la rama `hack/daniel-mesa` y archivos nuevos sin commitear: `playground/effects/{arduino_red,cabritas,credencial_linkedin,cucharas_tesla,etiqueta_rolling,mano_esqueleto,mantel_disco,mensaje_mesa,taza_buk}.py`, `playground/mesa_libre.py` y `scripts/manos_mesa.py`. No hay archivos originales modificados.
**Pasó:** el runtime en 8790 los tiene activos (hot reload), sin errores en `/api/state`.
**Aprendido:** el hot reload permite sumar efectos sin reiniciar el runtime.
**Falta:** que su autor confirme qué rama y dueño corresponden (la regla para cambios es `max/<tema>` con OK de Max).

### Procesos vivos en el MacBook (01:08)
- `python -m playground.dashboard` (8795) · `python -m playground serve --scan captures/dale-20261121-0040 --projector --port 8790` · `python -m slscan pipeline serve … --port 8796`. Se detienen con Ctrl+C en su Terminal.
- Sesión de Claude: `tmux -L max-sl`, sesión `max`.
