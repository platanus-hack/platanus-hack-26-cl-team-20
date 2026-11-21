//! `pcx worker …`: procesos aislados del core. ONNX Runtime (C++) y las cámaras no comparten proceso con el actor
//! del mundo: si un worker se cae (segfault, abort de ORT, panic), el core sigue vivo y `pcx up` lo reinicia.
//!
//! - `pcx worker hands --camera <id>`: lee el anillo `pcx.cam.<id>` (`pcx-shm`), corre `pcx-hands` y publica por la
//!   ingesta ZeroMQ `{"type":"hands","hands":[HandState]}` (+ eventos de toque como `Envelope` si hay homografía).
//! - `pcx worker capture …`: la captura de `pcx-capture` (mismos argumentos que `pcx-capture run`).

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use clap::Args;
use pcx_hands::{Backend, EngineConfig, Frame, HandsEngine, PixelFormat};
use tokio::sync::mpsc;
use zeromq::{Socket, SocketSend};

#[derive(Args, Clone, Debug)]
pub struct HandsArgs {
    /// Id de la cámara: anillo `pcx.cam.<id>`.
    #[arg(long)]
    pub camera: String,
    /// Endpoint ZeroMQ PUSH del core (`PCX_INGEST`).
    #[arg(long, env = "PCX_INGEST")]
    pub ingest: Option<String>,
    /// `cpu` (por defecto) o `coreml` (solo si el binario se compiló con `--features coreml`).
    #[arg(long, default_value = "cpu")]
    pub backend: String,
    #[arg(long, default_value_t = 2)]
    pub threads: usize,
    /// Si los modelos no cargan en este tiempo, el worker sale (código 3) y el supervisor lo reinicia.
    #[arg(long, default_value_t = 30.0)]
    pub init_timeout_s: f64,
    /// Cada cuántos segundos imprimir estadísticas por stderr.
    #[arg(long, default_value_t = 5.0)]
    pub stats_every_s: f64,
    /// Terminar tras N cuadros procesados (pruebas).
    #[arg(long = "max")]
    pub max_frames: Option<u64>,
}

/// Mensaje hacia la ingesta (JSON).
type Out = Vec<u8>;

fn hands_backend(s: &str) -> anyhow::Result<Backend> {
    match s {
        "cpu" => Ok(Backend::Cpu),
        "coreml" if cfg!(feature = "coreml") => Ok(Backend::CoreMl),
        "coreml" => anyhow::bail!("este binario no tiene CoreML (compila pcxd con --features coreml)"),
        o => anyhow::bail!("backend desconocido: {o} (cpu|coreml)"),
    }
}

/// Un panic en un worker debe terminar el proceso entero (el supervisor lo reinicia), no dejar un hilo muerto.
fn die_on_panic() {
    let prev = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        prev(info);
        std::process::exit(101);
    }));
}

pub async fn hands(a: HandsArgs) -> anyhow::Result<()> {
    die_on_panic();
    let backend = hands_backend(&a.backend)?;
    let ingest = a.ingest.clone().unwrap_or_else(pcx_capture::default_ingest);
    let (tx, mut rx) = mpsc::channel::<Out>(16);
    let stop = Arc::new(AtomicBool::new(false));
    let st = stop.clone();
    let loop_thread = std::thread::Builder::new().name("hands".into()).spawn(move || hands_loop(a, backend, tx, st))?;
    let mut push = zeromq::PushSocket::new();
    push.connect(&ingest).await?;
    eprintln!("[pcx worker hands] → {ingest}");
    // una sola vez: si se recreara en cada vuelta, un SIGTERM que llega durante `send` se perdería
    let shutdown = pcx_core::shutdown_signal();
    tokio::pin!(shutdown);
    loop {
        tokio::select! {
            m = rx.recv() => match m {
                Some(m) => {
                    // la ingesta del core no debe frenar al worker: si no hay par, se descarta
                    let _ = tokio::time::timeout(Duration::from_millis(200), push.send(zeromq::ZmqMessage::from(m))).await;
                }
                None => break,
            },
            _ = &mut shutdown => { stop.store(true, Ordering::Relaxed); break; }
        }
    }
    stop.store(true, Ordering::Relaxed);
    // join sin bloquear el runtime
    match tokio::task::spawn_blocking(move || loop_thread.join()).await? {
        Ok(r) => r,
        Err(_) => anyhow::bail!("el hilo de manos terminó con panic"),
    }
}

fn hands_loop(a: HandsArgs, backend: Backend, tx: mpsc::Sender<Out>, stop: Arc<AtomicBool>) -> anyhow::Result<()> {
    // Carga de modelos con vigilante: ORT/CoreML puede quedarse colgado al compilar; mejor salir y reintentar.
    let ready = Arc::new(AtomicBool::new(false));
    let (r2, timeout) = (ready.clone(), Duration::from_secs_f64(a.init_timeout_s));
    std::thread::spawn(move || {
        std::thread::sleep(timeout);
        if !r2.load(Ordering::Relaxed) {
            eprintln!("[pcx worker hands] los modelos no cargaron en {timeout:?}; salgo para que el supervisor reintente");
            std::process::exit(3);
        }
    });
    let t0 = Instant::now();
    let mut cfg = EngineConfig::default();
    cfg.detector.backend = backend;
    cfg.detector.intra_threads = a.threads.max(1);
    let mut eng = HandsEngine::from_cache(cfg)?;
    ready.store(true, Ordering::Relaxed);
    eprintln!("[pcx worker hands] modelos listos en {:.0} ms (backend {:?})", t0.elapsed().as_secs_f64() * 1e3, backend);

    let node_id = std::env::var("PCX_NODE_ID").unwrap_or_else(|_| "local".into());
    let mut buf = Vec::new();
    let mut reader: Option<pcx_shm::RingReader> = None;
    let (mut last_seq, mut n, mut n_stat, mut ms_stat, mut with_hands) = (0u64, 0u64, 0u64, Vec::<f64>::new(), 0u64);
    let (mut had_hands, mut last_sent) = (false, Instant::now());
    let mut t_stat = Instant::now();
    while !stop.load(Ordering::Relaxed) {
        let r = match reader.as_mut() {
            Some(r) if !r.closed() => r,
            _ => {
                match pcx_shm::RingReader::open(&a.camera, Duration::from_secs(2)) {
                    Ok(r) => {
                        eprintln!("[pcx worker hands] leyendo {} (pid escritor {})", r.name(), r.writer_pid());
                        last_seq = r.latest_seq().saturating_sub(1);
                        reader = Some(r);
                    }
                    Err(_) => {
                        reader = None;
                        if tx.is_closed() {
                            break;
                        }
                    }
                }
                continue;
            }
        };
        let meta = match r.wait_newer(last_seq, Duration::from_millis(500), &mut buf) {
            Ok(Some(m)) => m,
            Ok(None) => continue,
            Err(e) => {
                eprintln!("[pcx worker hands] anillo: {e}; reabro");
                reader = None;
                continue;
            }
        };
        last_seq = meta.seq;
        let fmt = match meta.format {
            pcx_shm::PixelFormat::Bgr8 => PixelFormat::Bgr8,
            pcx_shm::PixelFormat::Rgb8 => PixelFormat::Rgb8,
            _ => continue,
        };
        let frame = Frame::with_stride(&buf, meta.w as usize, meta.h as usize, meta.stride as usize, fmt)?;
        let fm = pcx_hands::FrameMeta {
            camera_id: a.camera.clone(),
            seq: meta.seq as i64,
            t_capture_ns: meta.t_capture_ns as i64,
            w: meta.w as i64,
            h: meta.h as i64,
            segment: Some(r.name().to_string()),
            format: Default::default(),
            stride: Some(meta.stride),
        };
        let out = eng.process(&frame, &fm)?;
        n += 1;
        n_stat += 1;
        ms_stat.push(out.total_ms);
        let has = !out.hands.is_empty();
        with_hands += has as u64;
        // estado de manos: siempre que haya manos, una vez al irse y como latido cada 1 s
        if has || had_hands || last_sent.elapsed() > Duration::from_secs(1) {
            let msg = serde_json::json!({"type": "hands", "hands": out.hands, "camera_id": a.camera});
            let _ = tx.try_send(serde_json::to_vec(&msg)?);
            last_sent = Instant::now();
        }
        had_hands = has;
        for ev in &out.events {
            if let Some(e) = ev.to_contract() {
                let env = pcx_contracts::wrap(pcx_contracts::Payload::Event(e), 0, "pcx-hands", &node_id, Some(out.t_capture_ns));
                let _ = tx.try_send(serde_json::to_vec(&env)?);
            }
        }
        if t_stat.elapsed().as_secs_f64() >= a.stats_every_s {
            ms_stat.sort_by(f64::total_cmp);
            let p = |q: f64| ms_stat.get(((ms_stat.len() - 1) as f64 * q) as usize).copied().unwrap_or(0.0);
            eprintln!(
                "[pcx worker hands] {:.1} fps procesados · {} cuadros · con manos {:.0} % · ms p50 {:.1} p95 {:.1}",
                n_stat as f64 / t_stat.elapsed().as_secs_f64(),
                n,
                100.0 * with_hands as f64 / n_stat.max(1) as f64,
                p(0.5),
                p(0.95)
            );
            (n_stat, with_hands, t_stat) = (0, 0, Instant::now());
            ms_stat.clear();
        }
        if a.max_frames.is_some_and(|m| n >= m) {
            break;
        }
    }
    eprintln!("[pcx worker hands] fin: {n} cuadros");
    Ok(())
}
