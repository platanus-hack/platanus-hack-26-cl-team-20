# Live Mapping Playground · MVP

Runtime con hot reload, galería de efectos, cola de pedidos y MCP para la secretaria. Hoy corre en el Mac con una
escena demo (mesa, pantalla, cuadro, cocodrilo, teclado); en el PC se alimenta del escáner y del tracking.

## Correr

```
cd ~/hardware/sl-scanner
.venv/bin/python -m playground serve --window          # runtime + API en http://127.0.0.1:8790 (+ ventana, q para salir)
.venv/bin/python -m playground effects                 # galería
.venv/bin/python -m playground snapshot --spawn contour_glow:cocodrilo --spawn texture_grid:mesa --out reports/playground_demo.png
.venv/bin/python -m pytest tests/test_playground.py -q
```

API: `GET /api/state` · `POST /api/command {cmd, args}` · `GET /api/events` (SSE) · `GET /api/frame.png` · `GET /api/schema`.

## Efectos (plugins con hot reload)

Un archivo en `playground/effects/` con una clase `Effect` (ver `effects/base.py`). Guardar el archivo basta: el
runtime lo recarga en el siguiente cuadro y reconstruye las instancias vivas con sus parámetros. Un error de sintaxis
o en `draw` no tumba el runtime: queda en `errors` y en el evento `reload_error`/`effect_error`.

| Efecto | Lee | Sobre |
|---|---|---|
| `contour_glow` | contornos, beat | props, instrumentos, cuadros, pantallas, personas |
| `texture_grid` | texturas, geometría, contornos | cualquiera (patrón según material) |
| `frame_feed` | planos, cuadros, cámara | cuadro, pantalla, mesa (feed rectificado a sus esquinas) |
| `bass_pulse` | audio (bajo, golpes), formas del escaneo | halo del Buda y teclas (px del proyector) |
| `bass_ripple` | audio (golpes), beat | ondas desde los platos de la DDJ y el Circuit |
| `channel_react` | audio por canal, `audio/mapping.json` | Buda, teclas, platos, cocodrilo, Circuit |
| `buda_breath` | foto del escaneo (`scene_pov.png`), contorno fino del tapiz | Buda (o cualquier objeto con foto): respira en su lugar, bordes Canny, halo |
| `ddj_skin` | foto, platos como elipses, pads/perillas/faders de la foto, MIDI de la DDJ | DDJ |
| `torre_faces` | caras del PC (`torre_caras.json` o `split_y`), relieve `height_proj.npy` | torre |
| `papel_canvas` | 4 esquinas de la hoja, `scene.hud` | hoja carta (HUD: estado, texto, imagen) |
| `scene_texture` | foto completa realzada, viñeta por objeto | toda la escena o un objeto |

Visuales v3 (16-oct): la geometría fina sale de la foto con `playground/scan_geom.py` y `scene_from_scan` la deja en
la escena (hoja real, tapiz del Buda, platos como elipses, caras de la torre); los efectos la toman de la escena viva
(con los ajustes finos) y, si la escena es de antes, de la foto + el ajuste del objeto. Param `capa` en cualquier
efecto: menor = se dibuja más abajo (empate: orden de spawn).

Audio reactivo (captura, canales, latencia, enrutamiento de la mezcladora): `playground/audio/README.md`.
`contour_glow` y `control_glow` aceptan `audio: "bass"` (apagado por defecto).
| `control_glow` | controles del OHM | el OHM (pads apretados, perillas y faders que se mueven) |
| `pianohero` | teclas (keys.json), MIDI del EK-50 | sin objeto: dibuja en píxeles del proyector (ver `pianohero/README.md`) |

**Piano Hero**: `python -m playground.pianohero --level 1|2|3 --song oda|estrellita|elisa|escala|x.mid [--practice]`
proyecta el juego sobre las teclas reales del Korg EK-50. El MIDI llega del PC por `windows/midi_bridge.py` (UDP
8796) o directo si el teclado está en el Mac. Detalle, niveles y diagnóstico del teclado: `playground/pianohero/README.md`.

## MCP `playground` (para la secretaria)

```
.venv/bin/python -m playground mcp                     # stdio; PLAYGROUND_URL apunta al runtime
```

Registro en Claude Code (lo decide Max/h1; no se instala solo):
`claude mcp add playground -- /Users/max/hardware/sl-scanner/.venv/bin/python -m playground mcp` con
`cwd` el repo. Skill: `playground/skill/SKILL.md` → copiar a `~/.claude/skills/live-mapping/SKILL.md`.

Tools: `pg_state pg_scene_objects pg_find pg_geometry pg_list_effects pg_spawn pg_kill pg_set_param pg_snapshot
pg_beat pg_request_feature pg_requests pg_cameras pg_crop_object pg_gestures pg_midi pg_dmx pg_ableton pg_audio`.
pg_beat pg_request_feature pg_requests pg_cameras pg_crop_object pg_gestures pg_midi pg_dmx pg_ableton pg_pianohero`.
Las de hardware son stubs honestos hasta que el PC y los equipos entren.

## Pedidos de features

`requests/NNN-slug.md` (ver `requests/README.md`). Los crea la secretaria (`pg_request_feature`) o h4; los toma la
unidad que programa en vivo, los implementa con hot reload y los cierra (`set_status`).

## VNC al PC (control de la sesión gráfica)

TightVNC Server corre como servicio en NEW-WHITE-4K (puerto 5900, solo LAN y tailnet; contraseña en el llavero del
Mac, entrada `sl-scanner-vnc`). Cliente propio, sin dependencias:

```
.venv/bin/python -m playground vnc info                              # host por defecto 192.168.1.233:5900 (VNC_HOST para otro)
.venv/bin/python -m playground vnc screenshot --out reports/pc_desktop.png
.venv/bin/python -m playground vnc click --x 1664 --y 1416          # coordenadas en píxeles de la pantalla del PC (2560x1440)
.venv/bin/python -m playground vnc key --text "hola"
```

Las capturas quedan en `reports/`, fuera de git, y se borran al terminar de usarlas: muestran la sesión personal del PC.
No se leen ni se tocan ventanas personales (correo, chats, navegador); solo ventanas propias del playground.

## Calibración del proyector 1 con la Osmo (resumen)

`slscan show-board --screen 2` (tablero en el iPad) → `slscan capture-calib --cameras 0 --names osmo --square <mm>`
→ `slscan calib-intrinsics …` → `slscan calib-procam --camera 0 --screen 1 --square <mm>` (cartulina mate con el
tablero impreso, 10 poses) → `slscan calib-rig captures/procam --intrinsics captures --proj-size 1920x1080`.
Pasos completos en PLAN › "Proyector 1 + Osmo: primera toma guiada".
