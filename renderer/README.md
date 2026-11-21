# pcx-renderer · compositor del proyector

Recibe la escena del core por WebSocket (`contracts/rust/pcx-contracts`: `scene`, `scene_diff`) y la dibuja en la GPU
(wgpu 30 + vello 0.12 + parley 0.13; Metal, Vulkan o DX12). Se basa en el spike `spikes/render-rust`.

```
WS (hilo propio) ─► SceneState (rev, base_rev, ttl, z) ─► lienzo en mm × ppmm ─► pasada final ─► ventana / PNG
                                                          vello: label, halo, arrow,     homografía mm→px
                                                          panel, button, image           o LUT por píxel
                                                          WGSL: effect (effects/*.wgsl)
```

## Cómo correr

```bash
cargo test                       # unitarios, golden images, protocolo (core falso en :9231), recarga de shader, humo del bench
cargo build --release
./target/release/pcx-renderer --bench                        # benchmark headless (5, 50 y 200 widgets)
./target/release/pcx-renderer --headless --frames 60 --out /tmp/pcx --no-ws --scene ../contracts/fixtures/scene.json
./target/release/pcx-renderer --headless --out /tmp/pcx     # contra el core en ws://127.0.0.1:9200/render, sin fin
```

**Mac del montaje (con ventana; no se ejecuta en los tests ni en el Mac de desarrollo):**

```bash
./target/release/pcx-renderer --display 1                    # índice del display (se listan en el log al arrancar)
./target/release/pcx-renderer --display LS24 --ws ws://127.0.0.1:9200/render
```

Ventana sin bordes del tamaño del display, `PresentMode::Fifo` (vsync), **nunca pantalla completa nativa** (provocó kernel
panics). En macOS (`#[cfg(target_os = "macos")]`): visible en todos los Spaces, nivel `NSStatusWindowLevel` (sobre la barra
de menú) y `ActivationPolicy::Accessory` (sin Dock). En Windows y Linux: ventana sin bordes, siempre encima. ESC cierra.

### Flags

| flag | default | |
|---|---|---|
| `--ws URL` / `--no-ws` | `ws://127.0.0.1:9200/render` | core; reconexión con backoff 100 ms → 2 s, mantiene la última escena |
| `--scene ARCHIVO` (repetible) | | aplica `scene`/`scene_diff` desde JSON al arrancar |
| `--ppmm N` | 2.5 | px del lienzo por mm de superficie |
| `--effects DIR` | `renderer/effects` | catálogo WGSL con recarga en caliente |
| `--assets DIR` | `renderer/assets` | `image.src = "asset:NOMBRE"` → `DIR/NOMBRE.png` |
| `--fonts DIR` | `renderer/fonts` | `.ttf/.otf/.ttc` propios, primeros en la pila de fuentes |
| `--lut ARCHIVO` | | LUT por defecto si la escena no trae un transform `kind: lut` |
| `--headless` `--frames N` `--out DIR` `--hz N` `--time T` `--size WxH` | ∞, —, 60, reloj, 1920x1080 | offscreen; `frame_NNNNN.png` + `superficie.png`; `--time` fija la animación |
| `--display IDX\|NOMBRE` `--latency N` | —, 1 | ventana en el display (subcadena del nombre o índice) |
| `--bench` `--bench-frames N` | 200 | benchmark |

## Protocolo

- core → renderer: `scene` (reemplaza todo) y `scene_diff` (`upsert`/`remove`/`transforms`). Un diff con `rev <= actual` se
  ignora; si `base_rev` no es la rev actual, no se aplica y se pide snapshot.
- renderer → core: `presented{rev, t_present_ms}` por **cada** rev aplicada, en el primer cuadro presentado que la contiene
  (headless: cuando la GPU terminó); `stats{fps, frame_ms_p50, frame_ms_p95, dropped}` cada 1 s.
- **Extensión (no está en `FromRenderer` de contracts):** `{"type":"resync","have_rev":N|null}` para pedir un `scene`
  completo; se repite como máximo cada 250 ms mientras falte. Propuesta: agregarlo a `contracts/render`.
- `ttl_s`: cuenta desde que llegó esa versión del widget (reenviar el mismo widget no reinicia el TTL).
- Orden: por `z` (estable por orden de llegada). Dentro de un mismo `z`, los `effect` van encima de los vectores
  (así un `z` común cuesta una sola pasada de vello).
- Transformaciones en vivo: homografía `table_mm → proj:*` (`matrix`, o `homography` legado). Sin ninguna, la superficie
  se centra en la salida. Un transform `kind: "lut"` con `lut_ref` = ruta (`/x.npy`, `file:/x.npy`) desde/hacia `proj:*`
  activa la LUT; cambia al cambiar `lut_ref` o `version`; si no carga, sigue la anterior.

## Formato de la LUT (superficies no planas)

Para cada píxel `(u, v)` del proyector, el punto `(x_mm, y_mm)` de `table_mm` que ilumina (mapa denso del gray code).
`NaN` (o `x < 0`) = sin cobertura → negro. Si no tiene la resolución de salida, se escala por vecino más cercano.

- **`.npy`** (v1/v2/v3): `float32` little-endian (`<f4`), C-order, forma `(H, W, 2)`. `np.save("lut.npy", lut.astype("<f4"))`.
- **raw `.pcxlut`**: 16 bytes de encabezado `b"PCXLUT01"` + `u32 LE ancho` + `u32 LE alto`, después `ancho*alto*2` `f32 LE`
  por filas (`x_mm, y_mm`).

## Efectos (`effects/*.wgsl`)

Cada archivo define `fn effect(p: vec2<f32>) -> vec4<f32>` (`p` en mm, RGBA sin premultiplicar) y se compila con
`src/effects_prelude.wgsl`: uniforms `fx` (tiempo `fx_time()`, bbox `fx.rect`, color `fx.color`, params `fx_param(i)`,
polígono `fx_point(i)`/`fx_n()`) y ayudas `poly_sdf(p)` y `poly_perimeter_pos(p)`. Encabezado `// params: a=1.0, b=2.0`
(máx. 8) → `fx_param(0..)`, sobreescribibles por nombre desde `widget.params`; `params.color` (`"#RRGGBB[AA]"` o
`[r,g,b(,a)]`) → `fx.color`; `margin_mm` agranda el área dibujada. Se revisan cada 30 cuadros; se validan con naga y
**un shader roto se rechaza (error en el log) y sigue el anterior**. Incluidos: `halo_glow` (halo que late alrededor
de un polígono), `marco_respira` (banda interior con gradiente que recorre el perímetro) y `ripple` (lo usa el fixture).

## Multiplataforma

Lo de macOS (objc2, Spaces, nivel de ventana, backend Metal) está detrás de `#[cfg(target_os = "macos")]`; fuera de
macOS se usan los backends primarios de wgpu. Pila de fuentes por SO (✓ y emoji): macOS `Helvetica Neue / Apple Color
Emoji / Zapf Dingbats / Apple Symbols`; Windows `Segoe UI / Segoe UI Emoji / Segoe UI Symbol`; Linux `Noto Sans / DejaVu
Sans / Noto Color Emoji / Noto Sans Symbols 2 / Symbola` (requiere fontconfig). Para no depender del SO, copiar fuentes
a `renderer/fonts/` (p. ej. Noto Sans + Noto Sans Symbols 2 + Noto Color Emoji, OFL): van primero en la pila.
**No verificado:** `cargo check` para Windows/Linux (este Mac no tiene `rustup` ni esos targets).

## Golden images

`tests/golden/*.png` (1920×1080, generadas en macOS). Tolerancia: ≤ 0,5 % de píxeles con algún canal a más de 24
niveles. Regenerar: `UPDATE_GOLDEN=1 cargo test --test golden`. Si falta una referencia (otra máquina u otro SO),
se escribe y el test pasa.

## Benchmark (`--bench`, medido)

MacBook Pro M1 Pro, macOS 26, `--release`, headless. Superficie de 800×500 mm a 2,5 px/mm (lienzo 2000×1250) → salida
1920×1080 con homografía no afín. Cada cuadro: escena nueva (como si llegara del core, texto que cambia) + vello +
efectos + composición + **espera a que la GPU termine**. 1 de cada 10 widgets es un `effect`. 300 cuadros.

| widgets | ms/cuadro p50 | p95 | media |
|---|---|---|---|
| 5 | 1,38 | 2,33 | 1,62 |
| 50 | 3,25 | 3,71 | 3,02 |
| 200 | 7,82 | 8,49 | 7,65 |

A 60 Hz el presupuesto es 16,7 ms: 200 widgets entran con margen de 2×.
