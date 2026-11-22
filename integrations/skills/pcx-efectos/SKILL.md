---
name: pcx-efectos
description: "Crear o modificar efectos visuales de pcx como shaders WGSL para el proyector: el contrato fn effect(p) con los uniforms fx (tiempo, bbox, color, params, polígono), el encabezado params, la recarga en caliente del renderer, la validación con naga, la prueba headless a PNG y la estética de proyección (luz sobre fondo negro, contraste). Úsala siempre que pidan un efecto nuevo, una animación, un brillo, ondas, un barrido, un resplandor o cualquier look que los widgets label, halo, arrow, panel y button no logren; al tocar archivos de renderer/effects; o cuando un widget effect no se ve o el log dice que un shader fue rechazado."
license: MIT
compatibility: Requiere el repo de pcx (renderer/) y, para la prueba headless, el binario pcx-renderer con una GPU disponible.
metadata:
  author: pcx
  version: "0.1.0"
---

# pcx-efectos: shaders WGSL para el proyector

Un efecto es un archivo `renderer/effects/<nombre>.wgsl` que define **una función**. El renderer le antepone un preludio con los uniforms y el vertex shader, lo valida con naga y lo dibuja sobre el área del widget. El nombre del archivo es el `shader` que usa el agente:

```json
{"type": "effect", "id": "fx1", "shader": "barrido", "anchor": {"object_id": "taza"}, "params": {"color": "#FFB300", "speed": 0.6}}
```

## Contrato

```wgsl
// Qué hace, en una línea.
// params: speed=0.35, width_mm=18.0
fn effect(p: vec2<f32>) -> vec4<f32> {
    // p: punto en mm de la superficie (table_mm). Devuelve RGBA sin premultiplicar.
    return vec4<f32>(fx.color.rgb, 1.0);
}
```

- `p` está en **mm**, no en píxeles ni en 0..1: los tamaños que escribas (anchos, radios) son milímetros reales en la mesa.
- El resultado se mezcla con alfa sobre el lienzo y se recorta a 0..1. Alfa 0 = no tocar.
- Se dibuja solo dentro del bbox del polígono o rectángulo del widget, más `margin_mm`.

Lo que el preludio (`renderer/src/effects_prelude.wgsl`) te da:

| Nombre | Qué es |
|---|---|
| `fx.rect` | bbox dibujado en mm: `x, y, w, h` (ya incluye el margen) |
| `fx.color` | `params.color` del widget, RGBA 0..1 (default `#00E5FF`) |
| `fx_time()` | segundos; úsalo para animar |
| `fx_param(i)` | parámetro `i` (0–7) del encabezado `// params:` |
| `fx_n()`, `fx_point(i)` | cantidad de puntos y punto `i` del polígono en mm (máx. 32; un `rect_mm` llega como 4 puntos) |
| `poly_sdf(p)` | distancia con signo al polígono en mm: negativa adentro, positiva afuera |
| `poly_perimeter_pos(p)` | posición 0..1 a lo largo del perímetro del punto del borde más cercano |
| `fx.misc` | `t_s, ppmm, n_puntos, margin_mm` |
| `fx.canvas` | ancho y alto del lienzo en px |

No redefinas nombres del preludio (`Fx`, `fx`, `VsOut`, `vs_main`, `fs_main`, `fx_*`, `poly_*`) ni declares bindings: el efecto es solo `fn effect` y funciones auxiliares tuyas.

### Parámetros

`// params: a=1.0, b=2.0` en el encabezado (máximo 8) declara los parámetros y sus valores por defecto, en orden: `a` es `fx_param(0)`, `b` es `fx_param(1)`. El widget los sobreescribe **por nombre** en `params`. Dos nombres tienen significado propio:

- `color` (no va en el encabezado): `"#RRGGBB"`, `"#RRGGBBAA"` o `[r, g, b]` en 0..1 → `fx.color`.
- `margin_mm` (sí va en el encabezado): agranda el área dibujada más allá del polígono. Necesario para resplandores que salen del contorno. También queda en `fx.misc.w`.

## Flujo de trabajo

1. Copia la plantilla [assets/barrido.wgsl](assets/barrido.wgsl) a `renderer/effects/<nombre>.wgsl` y edítala. Lee los tres incluidos (`halo_glow`, `marco_respira`, `ripple`) como referencia de estilo.
2. Valida y mira el resultado con la prueba headless (abajo).
3. Con el renderer corriendo, **guardar el archivo basta**: se recarga solo.
4. Úsalo desde el agente con un widget `effect`.

### Recarga en caliente

El renderer revisa la carpeta de efectos cada 30 cuadros (medio segundo a 60 fps) y recompila lo que cambió. En su stderr:

```
[fx] barrido cargado
[fx] barrido rechazado (sigo con el anterior):
error: …
[fx] barrido rechazado (sin versión previa):
error: …
```

`sigo con el anterior` = había una versión buena y se mantiene. `sin versión previa` = el efecto nunca compiló: los widgets que lo usan no dibujan nada.

Un shader roto **no tumba nada**: se rechaza y sigue dibujándose la última versión buena (o nada si nunca compiló). Por eso un efecto que «no cambia» casi siempre es un error de compilación: mira el log.

### Validar con naga

La validación es la del propio renderer (`effects::validate`: preludio + tu archivo, parseo WGSL y validador de naga con todas las capacidades). Formas de correrla sobre **tu** archivo:

```bash
# 1. Prueba headless (también dibuja): busca "[fx] <nombre> cargado" o "rechazado" en la salida
pcx-renderer --headless --no-ws --frames 2 --out /tmp/pcx-fx --scene escena.json --effects renderer/effects

# 2. CLI de naga, si la tienes (cargo install naga-cli): hay que anteponer el preludio
cat renderer/src/effects_prelude.wgsl renderer/effects/barrido.wgsl > /tmp/barrido.full.wgsl && naga /tmp/barrido.full.wgsl
```

Ojo: `cargo test -p pcx-renderer --test hot_reload` **no** valida tu shader. Ese test escribe sus propios shaders en un directorio temporal y prueba el mecanismo de recarga; no lee `renderer/effects/`.

Para que el catálogo quede cubierto por `cargo test`, propón junto con tu efecto un test que valide todos los archivos (no existe hoy en el repo; `effects::validate` es pública y no necesita GPU):

```rust
// renderer/tests/effects_validate.rs
#[test]
fn todos_los_efectos_validan() {
    let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("effects");
    for e in std::fs::read_dir(dir).unwrap().flatten() {
        let p = e.path();
        if p.extension().and_then(|x| x.to_str()) != Some("wgsl") { continue; }
        let src = std::fs::read_to_string(&p).unwrap();
        pcx_renderer::effects::validate(&src).unwrap_or_else(|err| panic!("{}:\n{err}", p.display()));
    }
}
```

Los números de línea de los errores cuentan el preludio (unas 100 líneas antes de tu código).

### Prueba headless

No abre ventanas: dibuja offscreen y escribe PNG. Usa una escena mínima como [assets/escena-prueba.json](assets/escena-prueba.json) (un mensaje `scene` con tu efecto sobre un rectángulo y un polígono).

```bash
pcx-renderer --headless --no-ws --frames 1 --time 0.7 --size 1920x1080 \
  --scene integrations/skills/pcx-efectos/assets/escena-prueba.json \
  --effects renderer/effects --out /tmp/pcx-fx
```

- `--time T` fija el reloj de la animación: corre con dos o tres valores (0.0, 0.7, 1.9) para ver el movimiento.
- Salen `frame_NNNNN.png` (lo que iría al proyector) y `superficie.png` (el lienzo en mm).
- Abre el PNG y **míralo**: que compile no significa que se vea bien.
- El binario sale de `cargo build -p pcx-renderer` (`target/debug/pcx-renderer`). Necesita un adaptador gráfico; en un sandbox sin GPU falla con `adapter: NotFound`.
- Borra la carpeta de salida al terminar.

## Estética: es luz, no pantalla

Un proyector solo **suma luz**. Negro es «no proyectar»; no existe el color oscuro.

- **Fondo negro siempre.** Devuelve alfa 0 donde no haya nada. Un relleno tenue «de fondo» lava el contraste de todo lo demás y, sobre la mesa, se ve como una mancha gris.
- **Poca área encendida, mucho contraste.** Líneas, bordes y acentos se leen mejor que rellenos. En el montaje real el contraste medido fue bajo (1,16:1 con luz ambiente fuerte): lo sutil no se ve. Si rellenas, hazlo con alfa bajo (0,05–0,15) y un acento brillante.
- **Colores saturados y claros.** Cian `#00E5FF`, ámbar `#FFB300`, verde `#00E676`, amarillo `#FFD400`. Los tonos oscuros (azul marino, marrón) casi no emiten luz. El blanco puro grande deslumbra a la cámara.
- **Trazos gruesos en mm.** Menos de 3 mm se pierde; 4–8 mm para bordes.
- **Desvanece antes del borde del área.** Si el brillo llega encendido al borde del bbox se ve el corte. Con `margin_mm`, apaga con `smoothstep` antes de `fx.misc.w` (así lo hace `halo_glow`).
- **Movimiento lento.** 0,3–1,5 Hz respira; más de 3 Hz parpadea, cansa y confunde a la percepción. Nada de destellos de pantalla completa.
- **La cámara también lo ve.** Luz proyectada sobre un objeto cambia cómo lo ve el seguidor de objetos. Los efectos alrededor del objeto (halos, marcos) son más seguros que los que lo bañan.
- **Costo.** `poly_sdf` y `poly_perimeter_pos` recorren el polígono por píxel (hasta 32 puntos): bien para áreas normales; evita anidarlos en bucles propios.

## Errores comunes

| Síntoma | Causa | Arreglo |
|---|---|---|
| El widget `effect` no se ve y nadie reporta error | `shader` no coincide con ningún archivo, o el efecto nunca compiló | El nombre es el del archivo sin `.wgsl`; revisar el log `[fx]` |
| Guardé y no cambia | El shader nuevo fue rechazado y sigue el anterior | Leer `[fx] … rechazado (sigo con el anterior)` |
| `redefinition of fx_time` o similar | Redefiniste algo del preludio | Renombrar |
| Error de tipos en `return` | `effect` debe devolver `vec4<f32>` | `vec4<f32>(rgb, a)` |
| El parámetro no hace nada | Nombre distinto entre `// params:` y `widget.params`, o más de 8 | Igualar nombres |
| El resplandor se corta en recto | Falta `margin_mm` o no se desvanece antes del margen | Declarar `margin_mm` y apagar con `smoothstep` |
| Se ve un rectángulo gris | Alfa mayor que 0 fuera de la figura | `if (poly_sdf(p) > 0.0) { return vec4<f32>(0.0); }` |
| El polígono se ve truncado | Más de 32 puntos | Simplificar el polígono |
| `adapter: NotFound` en la prueba | Sin GPU en ese entorno | Correr en una máquina con GPU; o solo validar |
