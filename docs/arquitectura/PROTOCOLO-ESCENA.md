# Protocolo común de los prototipos (renderer ↔ servidor de escena)

Lo cumplen los dos renderers (Rust y navegador) y el prototipo MCP, para que sean comparables y conectables entre sí.

## Transporte
- El renderer es **cliente** WebSocket. Se conecta a la URL de `--ws` (por defecto la de su propio driver de pruebas).
- JSON de texto. Relojes: milisegundos de época con decimales (Python `time.time()*1000`; navegador `performance.timeOrigin + performance.now()`; Rust `SystemTime::now()`). Todo corre en la misma máquina.

## Servidor → renderer
```json
{"type": "scene", "rev": 12, "t_send_ms": 1795231660123.4,
 "surface": {"width_mm": 800, "height_mm": 500},
 "homography": [h11, h12, h13, h21, h22, h23, h31, h32, h33],
 "widgets": [ ... ]}
```
- La escena se manda **completa** en cada mensaje (es chica).
- `homography`: fila mayor, lleva **mm de la superficie** (x a la derecha, y hacia abajo, origen arriba a la izquierda) a **px de salida** (ventana de 1920×1080). Puede cambiar en vivo (recalibración).

Widgets (todas las medidas en mm de la superficie; colores `#RRGGBB` o `#RRGGBBAA`):

| type | campos |
|---|---|
| `label` | `id, anchor:{x_mm,y_mm}, text, size_mm, color, bg?` (texto multilínea con `\n`, Unicode: tildes, ñ, ✓, emoji opcional) |
| `halo` | `id, polygon_mm:[[x,y],...], color, width_mm, pulse_hz` (borde luminoso animado) |
| `arrow` | `id, from_mm:[x,y], to_mm:[x,y], color, width_mm` |
| `panel` | `id, rect_mm:[x,y,w,h], title, body, color` (título + párrafo con ajuste de línea) |
| `button` | `id, rect_mm:[x,y,w,h], label, progress (0..1), color` (anillo o barra de progreso: botón por permanencia) |

## Renderer → servidor
- `{"type":"presented","rev":12,"t_present_ms":...}`: una vez por `rev`, en el primer cuadro **presentado** que la contiene.
- `{"type":"stats","fps":59.9,"frame_ms_p50":..,"frame_ms_p95":..}`: cada 1 s.

## Ventana
- Se abre en el **monitor externo** (1920×1080), como ventana sin bordes o normal del tamaño del monitor. **Nunca en pantalla completa nativa de macOS**: en el montaje real provocó kernel panics.
- Con vsync. Fondo negro (la luz proyectada es lo que no es negro).

## Puertos
- 9101: driver del renderer Rust · 9102: driver del renderer web · 9110: servidor MCP (HTTP `/mcp` y WS `/ws` para renderers).
- No usar 8787–8799 (hay otra demo corriendo).
