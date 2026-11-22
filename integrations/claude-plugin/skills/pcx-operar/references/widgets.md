# Widgets de pcx: referencia

Fuente: `contracts/src/pcx_contracts/widgets.py` y `node/crates/pcx-core/src/geom.rs`. Medidas en mm de `table_mm`; colores `#RRGGBB` o `#RRGGBBAA`.

## Campos comunes

| Campo | Tipo | Default | Qué hace |
|---|---|---|---|
| `id` | string | obligatorio | Identidad. `project` reemplaza el widget con el mismo `id`. No puede empezar con `pcx:` |
| `anchor` | `{object_id?, point_mm?, offset_mm?}` | `null` | Ancla a un objeto (lo sigue) o a un punto, más un desplazamiento |
| `z` | int | 0 | Orden de dibujo y de toque |
| `ttl_s` | float | `null` | Segundos de vida; al vencer el nodo lo borra |
| `on_press` | `{value?, triggers?, dwell_ms?}` | `null` | Lo hace tocable: emite `widget_pressed{widget_id, value}` |

`on_press.triggers`: lista de `dwell`, `pinch`, `shadow`, `api`, `rest`. Default `["dwell", "pinch", "api"]`. `dwell_ms` default 800.

## Por tipo

### `label`
Texto centrado en el ancla. Admite `\n` y Unicode (tildes, ñ, ✓).
`text` (""), `size_mm` (12), `color` (#FFFFFF), `bg` (sin fondo).
Con `anchor.object_id`: centrado en x sobre el objeto, arriba de su caja.

```json
{"type": "label", "id": "aviso", "anchor": {"point_mm": [400, 60]}, "text": "Listo ✓", "size_mm": 16, "bg": "#00000099"}
```

### `halo`
Borde luminoso animado. `polygon_mm` (≥ 3 puntos), `color` (#00E5FF), `width_mm` (4), `pulse_hz` (1).
Sin `polygon_mm` usa el polígono del objeto anclado, agrandado 8 mm.

```json
{"type": "halo", "id": "h1", "anchor": {"object_id": "taza"}, "color": "#FFD400", "pulse_hz": 1.5}
```

### `arrow`
`from_mm`, `to_mm`, `color` (#FFD400), `width_mm` (5). Con ancla a objeto y sin puntos: flecha de 70 mm que termina a 6 mm del objeto, desde arriba (desde abajo si el objeto está a menos de 90 mm del borde superior).

```json
{"type": "arrow", "id": "a1", "from_mm": [330, 120], "to_mm": [330, 221]}
```

### `panel`
Título y párrafo con ajuste de línea. `rect_mm`, `title`, `body`, `color` (#FFFFFF). Tamaño por defecto si se ancla: 200×110 mm.

```json
{"type": "panel", "id": "p1", "rect_mm": [470, 220, 200, 110], "title": "Paso 1 de 3", "body": "Pon la taza al centro."}
```

### `button`
Siempre tocable. `rect_mm`, `label`, `progress` (0..1, lo maneja el nodo durante la permanencia), `color` (#00E676). Tamaño por defecto si se ancla: 110×45 mm. El área tocable se extiende 5 mm por lado.

```json
{"type": "button", "id": "next", "rect_mm": [470, 340, 110, 45], "label": "Listo", "on_press": {"value": "next"}}
```

### `image`
`rect_mm`, `src`, `opacity` (1). `src` admite `asset:NOMBRE` (archivo `NOMBRE.png` en la carpeta de assets del renderer), rutas dentro de esa carpeta y `data:image/…;base64,…`. Las URL `http(s)` se rechazan salvo que el renderer corra con `--allow-remote-images`. Máximo 8 MiB y 4096×4096 px. Tamaño por defecto si se ancla: 120×120 mm.

```json
{"type": "image", "id": "qr", "rect_mm": [700, 20, 80, 80], "src": "asset:qr-celular"}
```

### `effect`
Shader WGSL del catálogo del renderer (`renderer/effects/<shader>.wgsl`). `shader`, `params` (números por nombre; `color` como `"#RRGGBB"` o `[r, g, b]` en 0..1), `rect_mm` o `polygon_mm` (máx. 32 puntos). Con `anchor.object_id` usa el polígono del objeto.

Incluidos: `halo_glow` (`pulse_hz`, `width_mm`, `glow_mm`, `intensity`, `margin_mm`), `marco_respira` (`band_mm`, `speed`, `breath_hz`, `tail`), `ripple` (`speed`, `wavelength_mm`).

```json
{"type": "effect", "id": "ondas", "shader": "ripple", "params": {"speed": 0.8, "color": "#00E5FF"},
 "polygon_mm": [[680, 300], [760, 300], [760, 360], [680, 360]], "z": -1}
```

Un `shader` que no existe no da error: simplemente no se dibuja.

## Cómo se resuelve un ancla

1. `anchor.object_id` presente y el objeto no está → el widget no se dibuja (queda en espera).
2. Con objeto: posición según el tipo (ver arriba) + `offset_mm`.
3. Sin objeto y con `anchor.point_mm`: `label` centrado ahí; `panel`/`button`/`image`/`effect` con su esquina superior izquierda ahí y tamaño por defecto.
4. Sin ancla ni geometría propia → error.

`panel`/`button`/`image` anclados a un objeto: `x = caja.x + caja.w + 20`; si se sale de la mesa, `x = caja.x − 20 − ancho`. `y = caja.y` acotado a la mesa.

## Zona reservada del nodo

Confirmaciones de domótica: panel `pcx:confirm-q:<id>` en `[ancho−250, 20, 230, 60]` y botón `pcx:confirm:<id>` en `[ancho−250, 90, 230, 50]`, amarillo `#FFD400`, `z` 50 y 51. No proyectes ahí ni lo imites.
