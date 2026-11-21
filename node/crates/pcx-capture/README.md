# pcx-capture · pcx-shm (Rust)
Port de `node/capture` (Python). Fuentes → anillo `pcx.cam.<id>` (`pcx-shm`, **mismo layout binario**: un worker
Python lee con `RingReader` sin cambios) → `FrameMeta` por `MetaSink` (closure, `ChannelSink` tokio o `ZmqPush`).
```bash
cargo run -p pcx-capture -- run --id cam0 --source replay:node/crates/pcx-capture/testdata/frames --size 640x360
cargo run -p pcx-capture -- run --id quest --source mjpeg:http://127.0.0.1:8787/cam.mjpg --print --no-zmq
cargo run -p pcx-capture -- run --id phone --source phone        # página getUserMedia + WS en :9220
cargo run -p pcx-capture -- peek --id cam0                       # fps, tamaño y edad del último cuadro
PCX_PY_CAPTURE=<ruta a node/capture> cargo test -p pcx-shm -p pcx-capture   # interop con Python vía uv
```
API: `open_source(&SourceSpec, &SourceOpts)` + `run(RunConfig, src, &mut [Box<dyn MetaSink>], stop)`; `cli::RunArgs` es
`clap::Args` (subcomando de `pcxd`). Lector: `RingReader::open(id, timeout)`, `latest()`, `latest_into(&mut buf)`,
`wait_newer(seq, timeout, &mut buf)`, `with_latest(|meta, bytes| …)` (cero copias, validado por seqlock). Se emite
`device_changed` (online/offline, `meta.shm`) y un `frame_meta` por cuadro: envelope v2, msgpack con nombres (lo
decodifica `msgspec`). ZeroMQ: `--ingest`/`PCX_INGEST` (`ipc:///tmp/pcx/ingest.sock`; Windows `tcp://127.0.0.1:9225`),
`--no-zmq` lo apaga; nunca bloquea (canal de 8, descarta).

**`libc` + `windows-sys` en vez de `shared_memory`/`memmap2`**: hay que calcar `multiprocessing.shared_memory`:
`shm_open("/pcx.cam.<id>")` + `shm_unlink` en POSIX y `CreateFileMappingW` sobre el archivo de paginación con el nombre
tal cual en Windows (el escritor conserva el handle). `shared_memory` agrega su propia capa (flink, persistencia en
Windows) y `memmap2` no crea mappings con nombre en Windows; son ~200 líneas. Seqlock con barreras Release/Acquire
reales. Huérfano por SIGKILL: se reemplaza y los lectores viejos ven `closed`. **Windows: no compilado ni probado aquí.**

Fuentes (salida bgr8 como Python): `replay` (carpeta jpg/png o `.mjpeg`, tiempo real, bucle; sin mp4: no hay ffmpeg),
`mjpeg` (Content-Length o corte FFD8/FFD9, reconexión 1→5 s), `phone` (axum HTTP+WS; HTTPS para la LAN pendiente),
`uvc` (nokhwa, `--features uvc`; compila en macOS). **Probar `uvc`** solo en la máquina del montaje, desde Terminal.app
de la sesión gráfica (TCC): `PCX_CAMERA=0 cargo test -p pcx-capture --features uvc -- --ignored uvc_manual --nocapture`;
luego `cargo run -p pcx-capture --features uvc -- run --id cam0 --source uvc:0` y `peek --id cam0` en otra terminal.

## Benchmark (`pcx-capture bench`, release, MacBook Apple Silicon con otros agentes compilando)
Procesos distintos, 300 cuadros a 60 fps; (Python). p95 «shm» ruidoso por carga: 720p 1,5–2,8 ms, 1080p 0,6–1,6 ms.

| Transporte | Tamaño | p50 ms | p95 ms | CPU % (2 procesos) | Escritura máx. c/s | Lector sin pausa c/s |
|---|---|---:|---:|---:|---:|---:|
| shm | 1280x720 | 0.35 (0.33) | 2.84 (0.43) | 3.4 (3) | 14293 (13367) | 10509 (9194) |
| shm-view | 1280x720 | 0.25 (0.25) | 0.31 (0.36) | 2.1 (2) | 15974 (15946) | 3971 (4236) |
| shm | 1920x1080 | 0.44 (0.49) | 0.59 (0.67) | 3.8 (4) | 5742 (5787) | 4677 (4718) |
| shm-view | 1920x1080 | 0.25 (0.28) | 0.40 (0.47) | 2.7 (3) | 6823 (6520) | 3858 (3691) |

p50 igual a Python: lo acotan el memcpy y el sondeo de 200 µs (`set_poll`). Rust gana alrededor: sin GIL ni OpenCV.
