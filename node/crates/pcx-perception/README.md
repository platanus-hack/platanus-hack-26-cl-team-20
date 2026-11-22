# pcx-perception

Percepción de pcx: la **foto limpia** (`src/clean.rs`, tarea R1: la mesa sin proyección encima) y el **pipeline de
objetos del motor local** (`src/pipeline.rs`, más abajo, con la sección «Para Max»).

```rust
let photo = capture_clean(&mut ring_reader, &mut control, Some(&mask), &CleanConfig::default())?;
// photo.metrics: tiempos, caída y dispersión entre cuadros; photo.into_rgb(): w × h × 3 para pcx-objects
```

- `FrameSource`: de dónde salen los cuadros; ya está implementado para `pcx_shm::RingReader`.
- `BlankControl`: `request_blank(ms) -> request_id` y `poll_blanked(request_id)` (sin bloquear).
- `Mask`: zona que ilumina el proyector en la imagen de cámara. Opcional, pero conviene pasarla.

## Cómo elige los cuadros

1. Pide `blank{ms: 300}` y lee cada cuadro nuevo. La referencia de luminancia es el último cuadro anterior a
   `min_latency_ms` (80 ms): lo que el contenido cambie hasta ahí no cuenta como apagón.
2. Apagón claro: caída ≥ `max(8 niveles, 25 % de la referencia)`. Usa la meseta más profunda (cuadros a 2 % de la
   caída del mínimo, contiguos o no), deja fuera los bordes y sale con 3. No espera `blanked`.
3. Caída menor: espera `blanked` y decide por su ventana + la latencia esperada (215–260 ms), solo si la caída
   coincide con esa ventana.
4. Sin caída: `BlackoutNotSeen`, salvo `assume_dark_when_flat`. Los errores llegan al vencer el plazo (800 ms).

Límites: una atenuación del contenido de más del 25 % entre los 80 ms y la llegada real de la oscuridad no se
distingue de un apagón: congela la escena antes de pedir la foto. Bloquea la exposición en captura; sin máscara y
con autoexposición la selección es más débil y la foto queda con la ganancia del apagón.

## Cifras (`cargo test -p pcx-perception -- --nocapture`, simulado a 30 fps)

- Latencia de 100 a 400 ms, 5 fases, ruido σ = 2: siempre cuadros del apagón; error medio ≤ 1,1 niveles.
- De `blank` a foto: latencia + ≤ 120 ms. Queda en ≤ 350 ms hasta ~225 ms de latencia; con 260 ms son 367 ms.
- Mediana de 3 cuadros de 1280×720×3 (`-- --ignored`): 8–12 ms sin optimizar.

---

# Pipeline de objetos del motor local (`src/pipeline.rs`)

Foto limpia → objetos en mm de la web app. Es la pieza que el servidor del motor consume por el trait `Perceptor`
de `src/pipeline_api.rs` (contrato: `informes/motor/PROTOCOLO-MOTOR-v0.md`, sección «API Rust interna»).

```rust
use pcx_perception::pipeline::{Pipeline, PipelineConfig};
use pcx_perception::pipeline_api::Perceptor;
let mut p: Box<dyn Perceptor> = Box::new(Pipeline::new(None, PipelineConfig::from_env()));
let out = p.on_clean(&foto_rgb, &calib, t_ns)?;   // out.upsert / out.remove / out.stages_ms
```

Cada foto se rectifica con `matrix_px_to_mm` + `lens` al lienzo de `surface_mm` (1 px = 1 mm por defecto; si la
foto del motor tiene otra resolución que `camera_px`, se escala) y pasa por la cascada:

| Etapa (`stages_ms`) | Qué hace |
|---|---|
| `rectify` | Homografía + lente, con tabla precalculada (se rehace solo si cambia la calibración) |
| `n0` | Diferencia contra la foto anterior, con la ganancia global compensada: qué objetos hay que volver a mirar |
| `n1` | Correlación en el lugar con búsqueda de ±8 mm: «sigue ahí» o «se corrió un poco» |
| `regions` | Lo que se ve distinto de la **mesa vacía** (`pipeline/table.rs`) y no es de ningún objeto conocido |
| `n2` | ORB rígido de los perdidos contra esas regiones: movido o girado, conserva la máscara |
| `sam_encoder`, `sam_decoder` | MobileSAM, **caja sola**, solo en las regiones sin explicar; el encoder corre una vez por foto, sobre el recorte que las contiene, y no se repite si la foto es la misma |
| `n2b` | Perdidos ↔ candidatos uno a uno, por color, tamaño y lugar (así se conserva el id) |
| `table`, `publish` | La mesa aprende donde no hay nada; polígonos en mm de 64 puntos como máximo |
| `cascade`, `total` | Todo menos SAM, y todo |

Arreglos del diagnóstico (`informes/tracking-diagnostico.md`, `tracking-revision-codex.md`):

- **Ids estables** (`obj-N`, nunca se reutilizan); un objeto retirado se recuerda 40 ciclos y recupera su id si vuelve.
- **Histéresis**: se retira tras `remove_after` (2) ciclos seguidos sin verlo. En el primero se republica con la
  confianza a la mitad, para que la web app lo pueda atenuar. Un fallo de SAM no cuenta como «no lo vi».
- **Objetos finos**: no hay apertura 5×5 ni áreas en píxeles. Los umbrales están en mm (`min_area_mm2` 90,
  `sliver_mm` 3) y la evidencia tenue se acepta si cuelga de una parte clara.
- **Los perdidos se reintentan en cada ciclo**, aunque no haya cambiado nada.
- **Sin duplicados**: candidatos deduplicados por IoU de máscara, máscaras excluyentes y asignación uno a uno.
- **SAM sin el punto del centro**; se usa su IoU predicho (`sam_min_score` 0,6; bajo 0,75 el objeto espera un
  ciclo antes de publicarse).

Nombres: `objeto-N`. Con `PCX_MOTOR_NOMBRES=claude` y `ANTHROPIC_API_KEY`, un hilo aparte le pide el nombre a Claude
visión (`pipeline/naming.rs`, gancho `Namer`) y el objeto se republica con su nombre en un ciclo posterior. Los
tests usan un juez simulado: nunca llaman a la API. Grounding DINO no participa.

## Modelos

MobileSAM, export de Acly: el mismo que usa `engine/playground/appearance_prepare.py`. Se buscan, en este orden, en
el `models_dir` que se pase, en `PCX_MODELS_DIR` y en `~/.pcx/models`. No se descargan solos.

| Archivo | URL | sha256 | Tamaño |
|---|---|---|---|
| `mobile_sam_image_encoder.onnx` | `https://huggingface.co/Acly/MobileSAM/resolve/main/mobile_sam_image_encoder.onnx` | `580f5fb648ea1062c0aabc26217aed56921985f03f0cbbd852bba81d760cc749` | 28 157 093 bytes |
| `sam_mask_decoder_single.onnx` | `https://huggingface.co/Acly/MobileSAM/resolve/main/sam_mask_decoder_single.onnx` | `93915fc7c993ab9d59ab8c9ccd3bce37f7509c81ab4150a74abd4d2abbd8570d` | 16 501 323 bytes |

Verificados el 21-nov contra `pcx_objects::models::catalog`. Sin los archivos, o sin compilar con `--features onnx`,
el pipeline trabaja solo con la cascada y lo dice en `Pipeline::sam_status()` y en la bitácora de cada ciclo.

## Para Max

**Estado (sáb 21-nov, 18:35).** Funciona de punta a punta con fotos reales y con escenas sintéticas. No lo probó
nadie en la mesa: no hay ninguna secuencia real con objetos moviéndose, solo las cuatro fotos de la misma escena de
`data/mesa/2026-11-21`, y los movimientos se simularon copiando recortes.

**Cómo probarlo.**

```sh
# sin modelos: unidades + escenas sintéticas (ids, histéresis, cuchara, lente, SAM simulado)
cargo test -p pcx-perception
# fotos reales, con SAM; deja las imágenes en PCX_DIAG_DIR
mkdir -p /tmp/pcx-fotos && for n in 1 2 3 4; do
  git show origin/daniel/consola:data/mesa/2026-11-21/mesa-limpia-$n.jpg > /tmp/pcx-fotos/mesa-limpia-$n.jpg; done
PCX_FOTOS_DIR=/tmp/pcx-fotos PCX_MODELS_DIR=/tmp/pcx-models PCX_DIAG_DIR=/tmp/pcx-diag \
  cargo test -p pcx-perception --release --features onnx --test pipeline_fotos -- --ignored --nocapture
```

Para mirar qué pasó en un ciclo: `Pipeline::log()` (bitácora), `debug_overlay()` (contornos sobre la foto
rectificada) y `debug_distance()` (qué tan distinto de la mesa es cada píxel; sirve para ajustar `fg_thr`).
`snapshot()` da el estado completo para un cliente que recién se conecta.

**Latencias medidas** (M1 Pro, release, lienzo 1000×563, con la máquina ocupada por otros procesos: carga 4–9).

| Ciclo | p50 | p95 | Meta |
|---|---|---|---|
| Escena quieta (solo cascada) | 18 ms | 25 ms | < 150 ms |
| Cambio, solo cascada (sin modelos) | 27 ms | 28 ms | < 150 ms |
| Cambio con SAM: parte de cascada | 82 ms | 103 ms | < 150 ms |
| Cambio con SAM: ciclo completo | 1,2 s | 2,4 s | < 1 s: **no se cumple** |
| Registro inicial con SAM (36 cajas) | 4,2 s (una corrida) | | |

El ciclo con SAM se va en el encoder: 0,82 s en el mejor caso, 1,1 s de mediana, contra los 0,39–0,48 s que midió
el diagnóstico con la máquina libre. El decoder cuesta unos 70 ms por caja. Con 6 u 8 hilos no mejora. Falta
repetir la medición con la máquina sin carga; con el encoder en 0,45 s el ciclo quedaría cerca de 0,6 s.

**Calidad en las fotos reales** (imágenes en `informes/motor/percepcion/`).

- Con SAM, registro: canasto, lámina, plasticina, pastillero, caja blanca, cucharas y el botón proyectado quedan
  bien contorneados. Las cuatro fotos de la escena quieta no producen cambios después del primer ciclo.
- La **taza queda dentro de la máscara del canasto**: es blanca sobre mesa clara (poca evidencia) y toca una bola
  que toca el canasto. Dos objetos que se tocan pueden salir como uno.
- Las dos cucharas salen como **un solo objeto**.
- Quedan 6–8 objetos falsos chicos en la zona del reflejo de la lámpara y del cable. El filtro de sombras y
  reflejos descarta unos 10 por registro, pero no todos.
- Sin SAM, el registro inicial junta vecinos en 3–4 manchas grandes (lo que predijo el diagnóstico). Sirve para
  seguir lo que se mueve después, no para dar de alta una mesa llena.
- Histéresis: al quitar las cucharas no se retira nada en el primer ciclo y se retiran en el segundo.

**Lo que falta.**

1. Probarlo en la mesa con objetos moviéndose de verdad y ajustar `fg_thr`, `min_strong_frac`, `max_light_frac`
   y `soft_edge_ratio` con `debug_distance()`.
2. Separar objetos que se tocan (taza + canasto, las dos cucharas).
3. Objetos puestos **encima** de otro (una bola dentro del canasto) no se detectan como nuevos.
4. La deriva de la cámara que midió el diagnóstico (0,75 mm por minuto) no se compensa: hay que recalibrar.
5. Bajar el registro inicial (4 s): menos cajas, o decodificar a media resolución.
6. Con `remove_after = 2`, un objeto que se lleva de la mesa sigue publicado hasta el ciclo siguiente. `pcx motor` ya
   pide ese ciclo ~1 s después de un cambio (`--settle-ms`, hasta dos seguidos).
7. El catálogo v2 (`origin/feat/catalogo-v2`) no está conectado: los objetos no sobreviven a un reinicio.

**Dos cosas que cambié fuera del pipeline.**

- `pcx-objects` con `--features onnx` **no compilaba** contra `ort` rc.15 (`Session::builder` ahora pide el
  `Environment`). Arreglado en `pcx-objects/src/onnx.rs`. Como ort admite un solo `Environment` por proceso, se usa
  el de `pcx_hands::infer::shared_env()`, así manos y SAM pueden vivir en el mismo binario.
- `MobileSam::from_dir` / `from_files`: carga por ruta, sin red.
