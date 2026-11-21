//! CLI `pcx-capture` (`run`, `peek`, `bench`). `RunArgs` es `clap::Args`: el binario del nodo puede montarlo como
//! subcomando y llamar a `run_cmd`.

use crate::bench;
use crate::frame::BoxError;
use crate::runner::{default_node_id, run, RunConfig, RunStats};
use crate::sink::{default_ingest, MetaSink};
use crate::sources::{open_source, SourceOpts, SourceSpec};
use clap::{Args, Parser, Subcommand};
use std::process::ExitCode;
use std::time::{Duration, Instant};

#[derive(Parser)]
#[command(name = "pcx-capture", about = "Fuentes de cámara → anillo pcx.cam.<id> + FrameMeta")]
pub struct Cli {
    #[command(subcommand)]
    pub cmd: Cmd,
}

#[derive(Subcommand)]
pub enum Cmd {
    /// Captura: fuente → anillo → FrameMeta.
    Run(RunArgs),
    /// Fps y edad del último cuadro de un anillo (sin ventanas).
    Peek {
        #[arg(long)]
        id: String,
        #[arg(long, default_value_t = 3.0)]
        secs: f64,
    },
    /// Benchmark escritura → lectura entre procesos (720p y 1080p).
    Bench {
        #[arg(long, default_value_t = 300)]
        frames: usize,
        #[arg(long, default_value_t = 60.0)]
        fps: f64,
    },
    #[command(hide = true)]
    BenchReader {
        #[arg(long)]
        id: String,
        #[arg(long)]
        n: u64,
        #[arg(long)]
        view: bool,
    },
}

fn parse_size(s: &str) -> Result<(u32, u32), String> {
    let (w, h) = s.split_once('x').ok_or("formato WxH, p. ej. 1280x720")?;
    Ok((w.parse().map_err(|_| "ancho inválido")?, h.parse().map_err(|_| "alto inválido")?))
}

#[derive(Args, Clone, Debug)]
pub struct RunArgs {
    /// Id de la cámara: anillo `pcx.cam.<id>` (≤ 22 caracteres).
    #[arg(long)]
    pub id: String,
    /// `replay:<carpeta|archivo.mjpeg>` · `uvc:<índice>` · `mjpeg:<url>` · `phone[:<puerto>]`.
    #[arg(long)]
    pub source: SourceSpec,
    #[arg(long, value_parser = parse_size, default_value = "1280x720")]
    pub size: (u32, u32),
    /// fps de replay (30) o pedido a la cámara/celular.
    #[arg(long)]
    pub fps: Option<f64>,
    /// replay sin pausa.
    #[arg(long)]
    pub max_speed: bool,
    /// replay una sola vez.
    #[arg(long)]
    pub no_loop: bool,
    #[arg(long, default_value_t = pcx_shm::DEFAULT_SLOTS)]
    pub slots: usize,
    /// Terminar tras N cuadros.
    #[arg(long = "max")]
    pub max_frames: Option<u64>,
    /// Endpoint ZeroMQ PUSH (`PCX_INGEST`; ipc:///tmp/pcx/ingest.sock o tcp://127.0.0.1:9225 en Windows).
    #[arg(long)]
    pub ingest: Option<String>,
    /// No publicar por ZeroMQ (solo stdout con --print).
    #[arg(long)]
    pub no_zmq: bool,
    /// Imprime cada FrameMeta como JSON por stdout.
    #[arg(long)]
    pub print: bool,
    #[arg(long)]
    pub node_id: Option<String>,
    /// Interfaz de `phone` (0.0.0.0 = LAN).
    #[arg(long, default_value = "0.0.0.0")]
    pub bind: std::net::IpAddr,
}

async fn stop_signal() {
    #[cfg(unix)]
    {
        let mut term = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()).expect("SIGTERM");
        let mut hup = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::hangup()).expect("SIGHUP");
        tokio::select! { _ = tokio::signal::ctrl_c() => {}, _ = term.recv() => {}, _ = hup.recv() => {} }
    }
    #[cfg(not(unix))]
    {
        let _ = tokio::signal::ctrl_c().await;
    }
}

/// Corre la captura hasta Ctrl-C/SIGTERM (el anillo se libera al salir). Requiere runtime tokio.
pub async fn run_cmd(a: RunArgs) -> Result<RunStats, BoxError> {
    let opts =
        SourceOpts { size: Some(a.size), fps: a.fps, max_speed: a.max_speed, looped: !a.no_loop, bind_ip: a.bind };
    let src = open_source(&a.source, &opts).await?;
    let mut sinks: Vec<Box<dyn MetaSink>> = Vec::new();
    let ingest = a.ingest.clone().unwrap_or_else(default_ingest);
    #[cfg(feature = "zmq")]
    if !a.no_zmq {
        sinks.push(Box::new(crate::sink::ZmqPush::spawn(&ingest)));
        eprintln!("[pcx-capture] FrameMeta → {ingest}");
    }
    #[cfg(not(feature = "zmq"))]
    let _ = &ingest;
    if a.print {
        sinks.push(Box::new(|env: &crate::Envelope| println!("{}", serde_json::to_string(env).unwrap_or_default())));
    }
    let cfg = RunConfig {
        camera_id: a.id.clone(),
        size: a.size,
        slots: a.slots,
        max_frames: a.max_frames,
        node_id: a.node_id.clone().unwrap_or_else(default_node_id),
        verbose: true,
    };
    run(cfg, src, &mut sinks, stop_signal()).await
}

fn peek(id: &str, secs: f64) -> Result<(), BoxError> {
    let mut r = pcx_shm::RingReader::open(id, Duration::from_secs(5))?;
    let t0 = Instant::now();
    let (s0, mut n) = (r.latest_seq(), 0u64);
    let mut last = s0;
    while t0.elapsed().as_secs_f64() < secs {
        if let Some((m, _)) = r.wait_newer_with(last, Duration::from_millis(500), |_, _| ())? {
            last = m.seq;
            n += 1;
        }
    }
    let fps = n as f64 / t0.elapsed().as_secs_f64();
    match r.with_latest(|_, _| ())? {
        Some((m, _)) => {
            let age_ms = (crate::contracts::now_ns().saturating_sub(m.t_capture_ns)) as f64 / 1e6;
            println!(
                "{} · {fps:.1} fps · seq {} · {}x{} {} · edad {age_ms:.1} ms · pid {} · reintentos {}",
                r.name(),
                m.seq,
                m.w,
                m.h,
                m.format.name(),
                r.writer_pid(),
                r.retries()
            );
        }
        None => println!("{} · sin cuadros aún", r.name()),
    }
    Ok(())
}

pub fn main() -> ExitCode {
    let cli = Cli::parse();
    let res: Result<(), BoxError> = match cli.cmd {
        Cmd::Run(a) => tokio::runtime::Runtime::new()
            .map_err(BoxError::from)
            .and_then(|rt| rt.block_on(run_cmd(a)))
            .map(|s| eprintln!("[pcx-capture] {} cuadros", s.frames)),
        Cmd::Peek { id, secs } => peek(&id, secs),
        Cmd::Bench { frames, fps } => std::env::current_exe().map_err(BoxError::from).and_then(|exe| {
            let rows = bench::run_bench(&exe, &[(1280, 720), (1920, 1080)], frames, fps)?;
            println!("{}", bench::format_table(&rows, fps));
            Ok(())
        }),
        Cmd::BenchReader { id, n, view } => bench::reader_child(&id, n, view).map_err(Into::into),
    };
    match res {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("[pcx-capture] error: {e}");
            ExitCode::FAILURE
        }
    }
}
