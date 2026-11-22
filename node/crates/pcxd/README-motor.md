# `pcx motor`: el motor local

cámara → **motor (Rust, este proceso)** → web app (consola en el navegador) → segunda pantalla → proyector.
El motor mira la mesa con su cámara, toma fotos limpias (le pide a la web app que apague la proyección un instante)
y publica los objetos en mm del mundo de la web app. Por el WebSocket viajan coordenadas, no video.
Protocolo v0: `GET /v1/health` y WS `/v1/ws` en `127.0.0.1:9230`. Arquitectura: `docs/arquitectura/MOTOR-LOCAL.md`.

## Uso

```sh
pcx up                                  # lo levanta según pcx.toml ([process.motor]) e imprime la URL de emparejamiento
pcx motor --camera c920                 # suelto, con cámara propia (nombre, id o índice de `pcx cameras`)
pcx motor                               # sin cámara: pide los cuadros a la web app (needs_frames)
pcx motor --camera synthetic            # mesa simulada, sin hardware: sirve para probar la consola
pcx motor --camera replay:carpeta/      # cuadros grabados (jpg/png o .mjpeg)
pcx motor --camera c920 --camera-lateral brio   # además, cámara lateral en vivo para los módulos de escena
```

Al arrancar genera `~/.pcx/motor.token` (0600; `--dir` o `PCX_MOTOR_DIR` lo mueven) e imprime
`https://pcx-gateway.onrender.com/?motor=ws://127.0.0.1:9230&motor_token=<token>`: se abre en el navegador y la
consola queda emparejada. Otras opciones: `--host`, `--port`, `--gateway` (otra web app), `--allow-origin`,
`--size`, `--fps`, `--lock-exposure`, `--calm-ms` (250), `--review-every-s` (45), `--blank-ms`, `--require-drop`,
`--models <carpeta>`, `--settle-ms` (1000), `--camera-lateral`, `--lateral-size`.

## Ciclo de percepción

Lo disparan: las manos se van (`hands present:false` + calma de 250 ms), cada 45 s, `perceive`, una calibración
nueva y `look{clean:true}`.

1. Cuadro de referencia → `blank{id, max_ms}` → espera `blanked{id}`.
2. Tres cuadros o más **posteriores** a `blanked`, con la caída de luminancia verificada (`capture_clean` de
   `pcx-perception`) → mediana → `unblank{id}`.
3. `Perceptor::on_clean` → `objects{rev, upsert, remove}` incremental → `status` con los tiempos por etapa.
4. Si el ciclo cambió algo, el motor pide **otro ciclo** `--settle-ms` después (1 s; hasta dos seguidos): el
   `Pipeline` retira un objeto recién en el segundo ciclo sin verlo (en el primero lo republica con media confianza).

Si `blanked` no llega no hay foto: entre el pedido y `blanked` el ciclo no recibe ningún cuadro, así que vence con
`error{code:"blanked_timeout"}` y manda `unblank` igual. Si alguien mete la mano durante la foto se descarta
(`scene_moved`) y se reintenta. Con la escena ya oscura la luminancia no cae: con `blanked` recibido los cuadros se
eligen por tiempos (latencia esperada 215–260 ms); `--require-drop` lo vuelve estricto.

## Lo que el protocolo no fija y aquí se decidió

- Justo después de `welcome` va un `objects` con todos los objetos vigentes, y un `status`.
- `welcome.motor` = `"pcx-motor/<versión>"`; `models` = `{sam: bool, hands: bool, note}`. `note` dice qué percepción
  corre (`"cascada + MobileSAM de <carpeta>"`) o por qué no hay SAM (`"sin SAM: solo la cascada (…)"`). El mismo
  `models` va en `/v1/health` y en cada `status`.
- Los `id` de `blank` y `need_frame` son texto (`b-1`, `f-1`); el `id` de `look` se devuelve tal cual llegó.
- `error` trae además `id` cuando responde a un `look` que no se pudo servir.
- Códigos de `error`: `unauthorized`, `bad_version`, `hello_required` (cierran con 4401), `bad_message`,
  `bad_calib`, `no_calib`, `blanked_timeout`, `no_luminance_drop`, `too_few_frames`, `no_frames`, `scene_moved`,
  `perception_failed`, `scene_failed`, `bad_frame`, `busy`.
- Varios clientes a la vez (hasta 8): todos reciben `blank`, `objects` y `status`; vale el primer `blanked`.
- `calib.camera_px` manda sobre la resolución: la cámara se reabre a ese tamaño; si no lo ofrece, se reescala.

## Seguridad

Solo escucha en `127.0.0.1`. `Host` debe ser loopback con el puerto del motor (421). `Origin`, si viene, debe ser
`https://pcx-gateway.onrender.com`, `http://localhost:*` o `http://127.0.0.1:*` (403), en HTTP y en el upgrade del
WebSocket; el preflight responde `Access-Control-Allow-Private-Network: true`. El primer mensaje del WS debe ser
`hello` con el token (tiempo constante). Sin `Origin` (clientes nativos) se pasa: autentica el token. Igual que en
el core, otro proceso del mismo usuario puede leer `motor.token`.

## Percepción

`Perceptor` (`pcx-perception/src/pipeline_api.rs`) separa el motor de los modelos. `motor::default_perceptor()` arma
el `Pipeline` real de `pcx-perception` (cascada N0–N2b + MobileSAM, ids estables `obj-N`, histéresis; ver el README
de ese crate). `PCX_MOTOR_PERCEPTOR=diff` deja el de respaldo, `DiffOnly` (`src/motor/diff_only.rs`).

- **SAM** necesita el binario compilado con `--features onnx` (`pcxd/onnx` → `pcx-perception/onnx`; el release lo
  activa) y los pesos `mobile_sam_image_encoder.onnx` + `sam_mask_decoder_single.onnx`. Se buscan en `--models`,
  después en `PCX_MODELS_DIR` y después en `~/.pcx/models` (donde los deja el instalador).
- Sin pesos o sin la feature el motor **no falla**: corre solo la cascada, lo imprime al arrancar y lo informa en
  `models.sam = false` + `models.note`.
- `PCX_MOTOR_NOMBRES=claude` + `ANTHROPIC_API_KEY`: nombres por Claude visión (ver `PipelineConfig::from_env`).

## Escenas (protocolo v0.1)

Módulos `ScenePerceptor` (`pcx-perception/src/scene_api.rs`) dentro del motor. **Se registran en
`motor::scenes::registry()`** (`src/motor/scenes.rs`); `welcome.scenes` lista sus nombres.

- `scene{name}` activa el módulo con ese nombre (el anterior recibe `reset`, pierde su memoria y sus objetos
  `<escena>:…` se retiran con un `objects`). `null` o un nombre sin módulo dejan el motor sin escena, sin error.
  La escena es una sola para todos los clientes. Activar una escena dispara un ciclo.
- En cada ciclo, después del `Perceptor` base, corre `on_clean` del módulo activo con la misma foto limpia;
  `ctx.objects` son los objetos del `Perceptor` base ya al día (sin los de la escena). Su tiempo va en
  `stages_ms.scene`.
- `--camera-lateral`: un hilo aparte llama `on_live` con cada cuadro (siempre el más reciente) si el módulo activo
  pide `live_camera() == Some("lateral")`. No toca el ciclo cenital. Sobre 30 ms avisa por stderr (cada 5 s); con
  10 cuadros seguidos sobre 250 ms la cámara en vivo de ese módulo se pausa (`error scene_failed`) hasta reactivar
  la escena. Antes de la primera `calib`, `ctx.calib` es la identidad sobre el cuadro lateral.
- Lo que devuelven: `upsert`/`remove` se fusionan en `objects` (ids sin el prefijo `<escena>:` → `error
  scene_failed` y no se publica nada de esa llamada) y cada `SceneEvent` sale como `event{scene, event, data}`.
- `welcome.cameras` = `[{id, role: "cenital"|"lateral", name}]` con las cámaras que abrió el motor.

## Tests

```sh
cargo test -p pcxd --lib motor      # protocolo, orígenes, geometría, foto limpia, DiffOnly
cargo test -p pcxd --test motor     # cliente WS que hace de web app + cámara sintética, replay y sin cámara; token y Origin;
                                    # escenas (módulo falso, cambio de escena, `on_live` con la lateral sintética)
# el binario con el Pipeline real sobre las fotos de la mesa (replay), con SAM y sin SAM (PCX_E2E_SAM=0)
PCX_FOTOS_DIR=/tmp/pcx-fotos PCX_MODELS_DIR=/tmp/pcx-models \
  cargo test -p pcxd --release --features onnx --test motor_fotos -- --ignored --nocapture
```

Ningún test abre cámaras ni ventanas.
