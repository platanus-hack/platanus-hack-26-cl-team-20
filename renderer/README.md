# pcx-renderer · compositor del proyector

Recibe la escena del core por WebSocket (contratos v2, `contracts/rust/pcx-contracts`: `scene`, `scene_diff`, `blank`)
y la dibuja en la GPU
(wgpu 30 + vello 0.12 + parley 0.13; Metal, Vulkan o DX12). Se basa en el spike `spikes/render-rust`.

```
WS (hilo propio) ─► SceneState (rev, base_rev, ttl, z) ─► lienzo en mm × ppmm ─► pasada final ─► ventana / PNG
                                                          vello: label, halo, arrow,     homografía mm→px
                                                          panel, button, image           o LUT por píxel
                                                          WGSL: effect (effects/*.wgsl)   (malla)
```

## Cómo correr

Es miembro del workspace Rust de la raíz (un solo `Cargo.lock` y `target/` en la raíz del repo).

```bash
cargo test -p pcx-renderer       # unitarios, golden images, protocolo (core falso en :9231), recarga de shader, humo del bench
cargo build --release -p pcx-renderer
./target/release/pcx-renderer --bench                        # benchmark headless (5, 50 y 200 widgets)
./target/release/pcx-renderer --headless --frames 60 --out /tmp/pcx --no-ws --scene contracts/fixtures/scene.json
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
| `--projector ID` | `main` | usa los transforms `table_mm → proj:<ID>` |
| `--lut ARCHIVO.npy` `--lut-step MM` | —, 1 | LUT local si la escena no trae una `kind: lut` para el proyector (sin sha256) |
| `--headless` `--frames N` `--out DIR` `--hz N` `--time T` `--size WxH` | ∞, —, 60, reloj, 1920x1080 | offscreen; `frame_NNNNN.png` + `superficie.png`; `--time` fija la animación |
| `--display IDX\|NOMBRE` `--latency N` | —, 1 | ventana en el display (subcadena del nombre o índice) |
| `--bench` `--bench-frames N` | 200 | benchmark |

## Protocolo

- core → renderer: `scene` (reemplaza todo), `scene_diff` (`upsert`/`remove`/`transforms`) y `blank`. Un diff con `rev <= actual` se
  ignora; si `base_rev` no es la rev actual, no se aplica y se pide snapshot.
- renderer → core: `presented{rev, t_present_ms}` por **cada** rev aplicada, en el primer cuadro presentado que la contiene
  (headless: cuando la GPU terminó; un cuadro negro de `blank` no cuenta); `stats{fps, frame_ms_p50, frame_ms_p95, dropped}`
  cada 1 s.
- `blank{request_id, ms}`: desde el próximo cuadro la salida va negra `ms` ms, encima de la escena (que se sigue
  actualizando). Un `blank` nuevo durante uno activo reemplaza el plazo desde ese momento. Al volver la escena se manda
  `blanked{request_id, t_start_ms, t_end_ms}` por cada pedido: presentación del primer cuadro negro y del primer cuadro
  con escena otra vez.
- **Extensión (no está en `FromRenderer` de contracts):** `{"type":"resync","have_rev":N|null}` para pedir un `scene`
  completo; se repite como máximo cada 250 ms mientras falte. Propuesta: agregarlo a `contracts/render`.
- `ttl_s`: cuenta desde que llegó esa versión del widget (reenviar el mismo widget no reinicia el TTL).
- Orden: por `z` (estable por orden de llegada). Dentro de un mismo `z`, los `effect` van encima de los vectores
  (así un `z` común cuesta una sola pasada de vello).
- Transformaciones en vivo (contrato v2): se elige con `select_projector_transform(transforms, projector_id)`
  (LUT > homografía; mayor `version`). `homography` legado se usa si no hay transform para el proyector. Sin ninguna,
  la superficie se centra en la salida. La LUT se recarga al cambiar `path`, `version` o `sha256`; si no carga (no existe,
  sha256 distinto, tamaño distinto del descriptor) se usa el respaldo del contrato: `approx_matrix` o la mejor homografía.

## LUT (superficies no planas)

Es la del contrato v2 (`LutTransform.lut`, ver `contracts/src/pcx_contracts/geometry.py`): `.npy` `float32` little-endian
(`<f4`), C-order, forma `(height, width, 2)` (`format: npy_f32_hw2`). `lut[v, u]` es la muestra de `table_mm` con centro
en `(u * grid_step, v * grid_step)` mm (`integer_centers`) y vale `(x, y)` en **px de salida** del proyector
(`output_px`, centro del px (0,0) en (0,0)). `NaN` = sin dato. Se verifica el `sha256` del archivo completo y que
`width`/`height` coincidan con el archivo.

Se dibuja como malla: cada celda de la grilla es un quad con vértices en `lut[v, u]` (px de salida) que muestrea el
lienzo en `(u, v) * grid_step` mm; las celdas con algún `NaN` no se dibujan. Con 2 mm de paso (401×251) son 200.000
triángulos, que se suben una vez por versión.

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
| 5 | 1,76 | 2,39 | 1,78 |
| 50 | 3,42 | 3,78 | 3,21 |
| 200 | 8,00 | 8,57 | 8,06 |

A 60 Hz el presupuesto es 16,7 ms: 200 widgets entran con margen de 2×.
