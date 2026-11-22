# ESTADO — sl-scanner de Max en el MacBook

Repo: `git@github.com:MAXMARDONES/sl-scanner.git` @ `b8fecff` (sin modificar).
Ruta MacBook: `~/code/platanus-hack-26/sl-scanner/` · venv `.venv` con python3.13.

## Hardware detectado (2026-11-21)
- Proyector: **T749-fHD720** por HDMI, 1920×1080 @60, pantalla extendida (Mirror Off). ✅
- Cámara USB: **usb-webcam** (UVC VendorID 7532 / ProductID 259). No es Osmo.
  - `dale` llama `--camera osmo`; el resolver busca cuadro vertical o 25 fps → probablemente no la encuentre.
  - El código ya trae override (`slscan/capture.py:393`): `SLSCAN_CAMERA=<índice>` fuerza esa webcam. Índice: ver `python -m slscan cameras`.
  - Ojo: `dale` pide `--cam-size 3840x2160` por defecto y `--rotate`; con webcam horizontal usar `--rotate 0` y quizá `--cam-size 1920x1080`.

## Procesos lanzados en el MacBook
- (ninguno corriendo todavía). Instalado: venv python3.13 + requirements.txt OK (log `~/.local/state/sl-scanner/pip.log`).
- Repo copiado por rsync desde el mini (clon de GitHub se colgaba en la red del evento); limpio en `b8fecff`, origin = GitHub de Max.

## Pantallas (según `slscan.capture.list_displays`)
- 0 = pantalla del MacBook (principal) · **1 = proyector T749 1920×1080** (extendida, arriba del MacBook).

## Comando para correr (en Terminal del MacBook, no por ssh)
```
cd ~/code/platanus-hack-26/sl-scanner && source .venv/bin/activate
python -m slscan cameras                      # anotar índice de la webcam USB
SLSCAN_CAMERA=<índice> python -m playground.dale --views 1 --rotate 0 --cam-size 1920x1080 --downscale 1
```
Se detiene con Ctrl+C en esa Terminal.

## Incidentes
- Por ssh OpenCV no tiene permiso de cámara (TCC). Abrí Terminal en el MacBook vía osascript: pide permiso de Cámara → alguien debe aceptar en pantalla o activar Terminal en Ajustes › Privacidad › Cámara.
- SAM/escena automática: faltan modelos locales `~/hardware/models/slscan-segmentation/*.onnx` (+ Grounding DINO en caché HF y python con torch). No están en el MacBook ni en el mini; los tiene Max.
- ssh al MacBook se corta a ratos ("MacBook unreachable via reverse tunnel and Tailscale"); reintentar tras unos segundos.
- Clon de GitHub lento (~28 MB) por la red del evento.

## WhatsApp con Max
- 00:19 enviado (dictado por Daniel): "Max, estoy a lo que me necesites".
- Vigilancia `wap vigilar max --urgente --unidad wJ:pD` hasta 12:19 (avisos a la tab Max). Apagar: `wap vigilar --off max`.

## Traspaso (00:30)
- Sesión de Claude teletransportada al MacBook y abierta en Terminal gráfica: `cd ~/code/platanus-hack-26/max && claude -r c85dc019-4d8b-4ea5-9853-8b81030902ed`. Desde ahí sigue la orquestación (tiene permiso de cámara vía Terminal).
- Copia de este ESTADO también en el MacBook: `~/code/platanus-hack-26/max/ESTADO.md`.
- Modelos: script `~/.local/state/sl-scanner/instalar-modelos.sh` (MacBook), log `modelos.log`. MobileSAM ONNX en `~/hardware/models/slscan-segmentation/`, onnxruntime en `.venv`, python semántico en `~/hardware/models/slscan-segmentation/semantic-venv` (torch+transformers), Grounding DINO tiny en `~/.cache/huggingface`. Para auto_scene: `--semantic-python ~/hardware/models/slscan-segmentation/semantic-venv/bin/python`.
- Vigilancia WhatsApp de Max sigue ruteada a la tab Max del mini (wJ:pD).
- 00:35 La sesión del MacBook ahora vive en tmux propio lanzado desde Terminal (conserva permiso de cámara): socket `tmux -L max-sl`, sesión `max`. La instancia que abrí directo en Terminal la cerré (era la misma sesión).
  - Ver/usar: pane Herdr `wJ:pE` («Max · Claude MacBook», tab Max) corre `ssh -t macbook /opt/homebrew/bin/tmux -L max-sl attach -t max`.
  - Detener: `ssh macbook '/opt/homebrew/bin/tmux -L max-sl kill-session -t max'`.

## 00:47 sáb — corriendo local en el MacBook (sesión Claude 79f8fdd0, bajo Herdr local)
- Cámara: webcam USB = índice OpenCV 0 (FaceTime = 1). Máx 2560×1440 @20fps. Proyector = display 1 (T749).
- Ojo TCC: desde tmux -L max-sl (Terminal) la cámara NO abre; desde la sesión bajo herdr sí. dale se corre desde ahí.
- dale: `SLSCAN_CAMERA=0 python -m playground.dale --views 1 --rotate 0 --cam-size 2560x1440 --downscale 1` (log /tmp/sldale.log). Sesión captures/dale-20261121-0040.
  - autocalib métrico falló: "coordenadas fuera del raster" (selfcalib.fit). Scan gray code OK (11.3% px), escena 2D OK (mediana 1.75 px). 3D bloqueado (sin rig).
- auto_scene con SAM+DINO activado: reports/scene_regions/dale-20261121-0040/auto-20261121-004528-b24178 (10 objetos).
- Corriendo: runtime `playground serve --projector` (8790), dashboard en tmux max-sl ventana dash (8795), visor pipeline 8796. Detener: pkill -f "playground serve"; Ctrl+C en ventana dash.

## 00:55 sáb — etiquetas y manos (rama local hack/daniel-mesa del sl-scanner, solo archivos nuevos, sin commit)
- playground/effects/etiqueta_rolling.py: nombre corre alrededor del contorno + velo. Spawneado en los 10 objetos (clasificados a mano: plato, toalla de papel, cucharas, mantel a cuadros, taza Buk, lanyard PH26, credencial PH26, placa Arduino, llaves, llavero).
- scripts/manos_mesa.py (venv ~/.venvs/slhands, mediapipe 1.2.3): webcam 0 @2560x1440 → HandLandmarker → LUT gray code captures/dale-20261121-0040/cam2proj_lut.npz → comando "hands" con 21 pts. ~24 fps. Log /tmp/slmanos.log. Cuadro: http://127.0.0.1:8799/frame.jpg. Detener: pkill -f manos_mesa.py (necesario antes de otro dale: es dueño de la cámara).
- playground/effects/mano_esqueleto.py: dibuja la armadura de 21 puntos (instancia e21).

## 01:10 sáb — escena «mesa viva» (rama local hack/daniel-mesa, archivos nuevos, sin commit)
- e22 cabritas (bowl, partículas con la mano) · e26 mantel_disco (108 celdas, Game of Life) · e27 taza_buk (letras trazadas + ticker/KPIs)
- e30 arduino_red (cables a toda la mesa + 7 pasos server) · e31 credencial_linkedin · e25 cucharas_tesla (rayos a los dedos)
- helper playground/mesa_libre.py (espacio libre para paneles). Etiquetas vivas: toalla, lanyard, llaves, llavero. e21 mano_esqueleto.
- manos_mesa.py ahora descarta manos fuera de la zona calibrada (antes dibujaba rayas largas al borde).

## 01:22 sáb — seguidor, anfitrión, micrófono
- scripts/seguidor_objetos.py (log /tmp/mesa/seguidor.log) + efecto seguimiento (e33): diff de imagen → relocaliza por template (rotación) o Hough (bowl) → /tmp/mesa/poses.json. Detener: pkill -f seguidor_objetos.py.
- manos_mesa comparte cuadro 1280x720 en /tmp/mesa/cenital_shm.npy y detecta la cenital por resolución (índices cambian al enchufar cámaras).
- mesa/ (anfitrión Sonnet en loop: anfitrion.sh, ANFITRION.md, diario.md; herramienta mesa). Detener: pkill -f anfitrion.sh. caffeinate -dimsu corriendo.
- mesa/microfono.py: seña 🤘 1 s → graba mic del MacBook; otra 🤘 → para y transcribe en el whisper del mini (localhost:7071 vía ssh). Textos en /tmp/mesa/voz y mesa/voz.md. Detener: pkill -f microfono.py.

## 01:55 sáb — TRASPASO (sesión Claude MacBook 79f8fdd0) · versión vigente: seguidor_v4
Daniel pidió (01:40): partir de cero, mesa oscura, pipeline ordenado y genérico (kit: cualquier efecto sobre cualquier objeto), estética sobria. Hecho:
- Limpieza: se apagaron todos los efectos viejos, el anfitrión (pkill anfitrion.sh) y microfono.py. v1–v3 del seguidor quedan obsoletos; vigente = scripts/seguidor_v4.py.
- Diseño: sl-scanner/docs/MESA-PIPELINE.md (cascada N0 diff limpio · N1 sigue igual · N2 ORB rígido · N3 Grounding DINO(prompt)+SAM(caja) · N4 LLM chico desempata · N5 registro global SAM auto + LLM nombra).
- Corriendo (MacBook): runtime `playground serve --projector` (8790) · dashboard tmux max-sl:dash (8795) · scripts/manos_mesa.py (venv ~/.venvs/slhands; cámara cenital + cuadro compartido /tmp/mesa/cenital_shm.npy + manos como sensor, ya NO proyecta esqueleto) · scripts/detector_dino.py (8805, python semántico) · scripts/seguidor_v4.py (catálogo /tmp/mesa/catalogo.json, poses /tmp/mesa/poses.json, log /tmp/mesa/seguidor.log) · caffeinate -dimsu.
- Efectos vivos: e33 seguimiento (puente: crea objetos m01..m12 en la escena, fundido de entrada) + etiqueta_rolling sobria en m01..m12 + mantel_disco sobre m02 (grilla re-detectada desde la foto limpia actual: extra['apariencia']).
- Re-registrar todo: touch /tmp/mesa/relocalizar (~65 s, terra). Revisión al soltar la mano: apagón 0.3 s + cascada.
- Costo LLM (Codex por suscripción): log /tmp/mesa/llm_uso.jsonl. Hasta 01:55: luna 4 llamadas (~18 s c/u), terra 3 (~36 s c/u); tokens no se registraban antes de 01:55 (arreglado).
- Levantar todo desde cero (MacBook, desde la sesión bajo Herdr local, que tiene permiso de cámara): ver sección «Arranque» (pendiente: script único demo_segura.sh).
- Pendiente: probar la cascada con movimientos reales; script único de arranque con auto-recuperación; guion 5–6 momentos; video de respaldo; cartel «esta mesa usa cámaras».

## 02:10 sáb — TRASPASO 2 (sesión Claude MacBook 79f8fdd0)
Vigente: scripts/seguidor_v4.py (cascada; docs/MESA-PIPELINE.md). Cambios desde 01:55:
- LIENZO rectificado (como el tracker del moledor de Max): captures/dale-20261121-0040/lienzo_poly.npz + lienzo_map.npz = polinomio robusto proyector→cámara del plano de la mesa (error mediano 0.2 px, corrige lente, ignora bultos del escaneo). El seguidor trabaja todo en el lienzo 1280x720 (= px proyector / 1.5). No se reescanea por cada movimiento.
- Foto limpia con MARCADOR de sincronía (cuadradito blanco en la esquina durante el apagón): ya no depende de la latencia.
- Registro global (touch /tmp/mesa/relocalizar o --registrar): SAM automático + Claude Sonnet por CLI (juez_claude, ~15–25 s, ~US$0.13 equiv. por registro). luna/terra/sol nombraban mal. N4 (desempate) también pasa a Sonnet, más estricto; N3 exige sim>0.6.
- Verificación general (DINO con todos los prompts + SAM) al arrancar y en cada revisión periódica (45 s).
- `mesa kit` (~/code/platanus-hack-26/mesa/mesa): reaplica etiqueta sobria a todo el catálogo + mantel_disco a lo cuadriculado.
- marca_equipo (e55): 6 tarjetas GRUPO 20 de colores girando y rebotando (incluye «esta mesa usa cámaras»).
- Pausa: si existe /tmp/mesa/escaneo_en_curso el seguidor no apaga el proyector.
- Agente en curso: escaneo rápido de luz estructurada ≤5 s (scripts/escaneo_rapido.py, usa cuadros de /tmp/mesa/cenital_shm.npy + overlay del runtime).
Problemas abiertos: objetos altos (bowl, rollo, taza) quedan corridos al moverlos (paralaje; falta 3D); N2 rígido deja el contorno ~40 px corrido en el Arduino; objetos que salen del área deberían ocultarse (antes luna eligió el reflejo de la lámpara); Daniel va a conectar una 2ª cámara cenital.
Arranque manual: runtime (dale) → manos_mesa.py (~/.venvs/slhands) → detector_dino.py (semantic-venv) → seguidor_v4.py → mesa kit → spawn seguimiento/marca_equipo. Falta demo_segura.sh.

## 02:15 sáb — escaneo rápido de luz estructurada (agente)
- scripts/escaneo_rapido.py: 4.4 s de punta a punta (32 patrones gray+inverso a 10 Hz, bits 10..3), cobertura 84 % vs referencia, error mediano 2.0 px (p90 3.2; hay sesgo −2 px en x). 3/3 corridas buenas con el código final.
- Comando: cd sl-scanner && .venv/bin/python scripts/escaneo_rapido.py --sin-crudo --ref ""  → captures/rapido-<fecha>/view_00_map.npz (1280x720).
- Usa los cuadros compartidos de manos_mesa (depende de sus fps: a 15 fps ~6 s) y el overlay del runtime; marca /tmp/mesa/escaneo_en_curso (el seguidor pausa). Detalle: docs/ESCANEO-RAPIDO.md.
