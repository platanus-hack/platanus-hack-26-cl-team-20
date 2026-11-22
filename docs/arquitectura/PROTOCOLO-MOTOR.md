# Protocolo motor ↔ web app, v0 (sáb 21-nov, 17:55)

Decisión del equipo (reunión de las 17:16): cámara → **motor Rust local** (segmentación + coordenadas) → **web app** (consola de Daniel en el navegador) → segunda pantalla → proyector.

El motor levanta un servidor local. La web app se conecta a una **URL de motor configurable**: hoy `ws://127.0.0.1:9230`; mañana, un motor remoto vía proxy. Por ahí viajan coordenadas, no video.

## Transporte
- WebSocket `ws://127.0.0.1:9230/v1/ws`. El primer mensaje del cliente es `hello` y lleva el token.
- `GET /v1/health` responde `{ok, version, camera, models}`.
- CORS solo para los orígenes permitidos: `https://pcx-gateway.onrender.com`, `http://localhost:*` y `http://127.0.0.1:*`. El preflight de Private/Local Network Access se responde con `Access-Control-Allow-Private-Network: true`.
- En el WS se valida `Origin` contra la misma lista.
- Token: `pcx up` lo genera y lo guarda en `~/.pcx/motor.token` (0600), e imprime la URL de emparejamiento `https://pcx-gateway.onrender.com/?motor=ws://127.0.0.1:9230&motor_token=<token>`.
- Mensajes JSON con el campo `type`. Versión `v: 0`.

## Unidades
Las coordenadas `*_mm` son las **mismas del mundo de la web app**: las que define `matrix_px_to_mm` de su calibración (superficie `surface_mm`).

## Web app → motor
| type | Campos | Cuándo |
|---|---|---|
| `hello` | `v, token, session_id, app` | Al conectar |
| `calib` | `camera_px:[w,h], matrix_px_to_mm:[9] (por filas), lens:{f,cx,cy,k1,k2}\|null, surface_mm:[w,h]` | Al conectar y cada vez que cambia la calibración |
| `blanked` | `id, t_ns` | La ventana del proyector ya mostró negro total (al menos 2 `requestAnimationFrame`, por encima de todo, incluido el html) |
| `unblanked` | `id` | Volvió a mostrar la escena |
| `hands` | `present:bool, t_ns` | Cambio de presencia de manos (MediaPipe de la web app) |
| `perceive` | — | Forzar un ciclo |
| `reset` | — | Olvidar los objetos |
| `look` | `id, clean:bool` | Pedir una foto de la cámara del motor |
| `frame` | `id, jpeg_b64, w, h, t_ns` | Respaldo: el motor no tiene cámara y la pidió con `need_frame` |

## Motor → web app
| type | Campos | Cuándo |
|---|---|---|
| `welcome` | `v, motor, camera:{id,name,px:[w,h]}\|null, models:{sam,hands}, needs_frames:bool` | Respuesta a `hello` |
| `blank` | `id, max_ms` | Pide negro total en el proyector para la foto limpia. La web app responde con `blanked` |
| `unblank` | `id` | Termina la foto limpia |
| `objects` | `rev, upsert:[{id,label,polygon_mm (≤64 puntos), center_mm, confidence}], remove:[id]` | **Incremental**. La web app fusiona y **nunca borra objetos de otras fuentes** |
| `status` | `state: idle\|calm\|blank\|capturing\|perceiving, last_cycle_ms, stages_ms:{…}` | Cada cambio de estado o cada ciclo |
| `look_frame` | `id, jpeg_b64, t_ns, w, h` | Respuesta a `look` |
| `need_frame` | `id` | Respaldo, sin cámara propia |
| `error` | `code, message` | — |

## Ciclo de percepción
1. Las manos se van (`hands present:false`).
2. Calma de 250 ms.
3. `blank` → `blanked`.
4. El motor captura 3 cuadros o más **posteriores** a `blanked` y verifica la caída de luminancia.
5. Mediana → `unblank`.
6. Cascada (N0 diferencia → N1 template → N2 ORB → N2b color) y SAM solo en las regiones nuevas.
7. `objects` incremental.

Además: un ciclo cada 45 s y otro con `perceive`.

## API Rust interna (`node/crates/pcx-perception/src/pipeline_api.rs`, texto idéntico en ambos frentes)
```rust
#[derive(Clone, Debug)] pub struct Lens { pub f: f64, pub cx: f64, pub cy: f64, pub k1: f64, pub k2: f64 }
#[derive(Clone, Debug)] pub struct CalibIn { pub camera_px: (u32, u32), pub matrix_px_to_mm: [f64; 9], pub lens: Option<Lens>, pub surface_mm: (f64, f64) }
#[derive(Clone, Debug)] pub struct ObjectOut { pub id: String, pub label: String, pub polygon_mm: Vec<[f64; 2]>, pub center_mm: [f64; 2], pub confidence: f32 }
#[derive(Clone, Debug, Default)] pub struct PerceptionOut { pub upsert: Vec<ObjectOut>, pub remove: Vec<String>, pub stages_ms: Vec<(String, f64)> }
pub trait Perceptor: Send {
    fn on_clean(&mut self, frame: &image::RgbImage, calib: &CalibIn, t_ns: u64) -> Result<PerceptionOut, String>;
    fn reset(&mut self);
}
```

## v0.1 · escenas, eventos y cámara en vivo (sáb 21-nov, 19:30)
Agregados compatibles con v0. Un cliente v0 ignora los mensajes nuevos.

**Web app → motor**
| type | Campos | Cuándo |
|---|---|---|
| `scene` | `name` (por ejemplo `lab`, `solar`, `desk`; `null` = ninguna) | Al conectar y cada vez que cambia la escena o el preset activo. El motor activa el módulo de percepción con ese nombre |

**Motor → web app**
| type | Campos | Cuándo |
|---|---|---|
| `objects` (sin cambios) | además puede traer objetos de escena, con ids prefijados `escena:` | — |
| `event` | `scene, event, data` | Evento de un módulo de escena (por ejemplo `nivel` con `{vaso, ml, objetivo_ml, estado}`). La web app lo reenvía al gateway como `flow_emitted { event, data }` |
| `welcome` (agrega) | `scenes: [nombres]`, `cameras: [{id, role: "cenital"\|"lateral", name}]` | — |

Justo después de `welcome`, el motor manda `objects` con todos los objetos vigentes.

**Módulos de escena (Rust, dentro del motor).** Trait `ScenePerceptor` en `node/crates/pcx-perception/src/scene_api.rs`:
- `on_clean(foto limpia cenital, ctx)` corre en cada ciclo, después del `Perceptor` base;
- `on_live(cuadro, ctx)` corre en cada cuadro de la cámara que pida `live_camera()`, por ejemplo `"lateral"`, y debe tardar menos de 30 ms;
- `ctx` trae los objetos vigentes, la calibración, `t_ns` y una memoria por escena;
- devuelven `upsert`/`remove` (ids con prefijo de escena) y `events`.

El motor registra los módulos en un `SceneRegistry` y activa el que coincide con `scene.name`. Cámara lateral: `pcx motor --camera-lateral <nombre|índice|replay:…>`.

**Dueños:** el motor y el protocolo, Manu; los módulos `ScenePerceptor` y el `Perceptor`, Max.
