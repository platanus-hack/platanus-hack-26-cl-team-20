//! `pcx`: binario único del nodo local.
//!
//! - `pcx serve`   core (HTTP/MCP en 127.0.0.1:9200, /render, ZeroMQ, nube opcional).
//! - `pcx mcp`     MCP por stdio: proxy al daemon si responde; si no, core en proceso.
//! - `pcx up`      supervisor según `pcx.toml`.
//! - `pcx replay`  reinyecta por ZeroMQ los eventos de entrada de otro `pcx.db` (demo de respaldo).

use std::path::PathBuf;
use std::time::Duration;

use clap::{Parser, Subcommand};
use pcx_core::mcp::PcxMcp;
use pcx_core::{shutdown_signal, Core, Settings};
use rmcp::ServiceExt;
use tokio_util::sync::CancellationToken;

#[derive(Parser)]
#[command(name = "pcx", version, about = "Nodo local de pcx (contexto físico para agentes)")]
struct Cli {
    #[command(subcommand)]
    cmd: Option<Cmd>,
}

#[derive(Subcommand)]
enum Cmd {
    /// Core: MCP Streamable HTTP, WS /render, ingesta ZeroMQ, enlace a la nube.
    Serve {
        #[arg(long)]
        port: Option<u16>,
    },
    /// MCP por stdio (proxy al daemon o core en proceso).
    Mcp,
    /// Lanza y vigila los procesos de `pcx.toml`.
    Up {
        #[arg(long)]
        config: Option<PathBuf>,
    },
    /// Reinyecta objetos y toques de otro log (`<dir>/pcx.db`) por la ingesta ZeroMQ.
    Replay {
        /// Directorio que contiene el `pcx.db` grabado.
        dir: PathBuf,
        /// 0 = sin esperas.
        #[arg(long, default_value_t = 1.0)]
        speed: f64,
        #[arg(long)]
        ingest: Option<String>,
    },
}

const INPUT_TYPES: &[&str] = &["object_appeared", "object_moved", "object_removed", "touch_down", "touch_move", "touch_up", "frame_meta", "device_changed"];

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_writer(std::io::stderr)
        .with_env_filter(tracing_subscriber::EnvFilter::try_from_env("PCX_LOG").unwrap_or_else(|_| "info".into()))
        .init();
    match Cli::parse().cmd.unwrap_or(Cmd::Serve { port: None }) {
        Cmd::Serve { port } => serve(port).await,
        Cmd::Mcp => mcp_stdio().await,
        Cmd::Up { config } => {
            let cfg = config.unwrap_or_else(|| ["pcx.toml", "node/pcx.toml"].iter().map(PathBuf::from).find(|p| p.exists()).unwrap_or("pcx.toml".into()));
            anyhow::ensure!(cfg.exists(), "no existe {}", cfg.display());
            let stop = CancellationToken::new();
            let s2 = stop.clone();
            tokio::spawn(async move {
                shutdown_signal().await;
                s2.cancel();
            });
            pcx_core::supervisor::up(&cfg, stop).await
        }
        Cmd::Replay { dir, speed, ingest } => replay(dir, speed, ingest).await,
    }
}

async fn serve(port: Option<u16>) -> anyhow::Result<()> {
    let mut s = Settings::from_env()?;
    if let Some(p) = port {
        s.port = p;
        s.check()?;
    }
    let core = Core::start(s.clone(), None).await?;
    eprintln!(
        "pcx: http://{h}:{p}/mcp  ws://{h}:{p}/render  ingest={i}  events={e}  db={db:?}  nube={n}",
        h = s.host,
        p = s.port,
        i = s.ingest_url,
        e = s.events_url,
        db = core.world.store.path,
        n = if s.cloud_url.is_some() { "sí" } else { "no" }
    );
    let r = core.serve(shutdown_signal()).await;
    core.stop().await;
    r
}

async fn mcp_stdio() -> anyhow::Result<()> {
    use rmcp::transport::streamable_http_client::StreamableHttpClientTransportConfig;
    use rmcp::transport::StreamableHttpClientTransport;
    let s = Settings::from_env()?;
    let base = std::env::var("PCX_CORE_URL").unwrap_or_else(|_| format!("http://{}:{}", s.host, s.port));
    let up = reqwest::Client::new()
        .get(format!("{base}/health"))
        .timeout(Duration::from_secs(1))
        .send()
        .await
        .map(|r| r.status().is_success())
        .unwrap_or(false);
    if up {
        // proxy: todos los agentes comparten la mesa del daemon
        let mut cfg = StreamableHttpClientTransportConfig::with_uri(format!("{base}/mcp"));
        if let Some(t) = &s.token {
            cfg = cfg.auth_header(t.clone());
        }
        let client = ().serve(StreamableHttpClientTransport::from_config(cfg)).await?;
        eprintln!("pcx (stdio): proxy hacia {base}");
        PcxMcp::proxy(client).serve(rmcp::transport::stdio()).await?.waiting().await?;
        return Ok(());
    }
    // sin daemon: core en proceso (+ lado HTTP si el puerto está libre)
    let free = std::net::TcpListener::bind((s.host.as_str(), s.port)).is_ok();
    let core = Core::start(s.clone(), None).await?;
    let stop = CancellationToken::new();
    if free {
        let st = stop.clone();
        let router = core.router();
        let listener = tokio::net::TcpListener::bind((s.host.as_str(), s.port)).await?;
        tokio::spawn(async move {
            let _ = axum_serve(listener, router, st).await;
        });
    }
    eprintln!("pcx (stdio): core en proceso; HTTP {}", if free { format!("en :{}", s.port) } else { "desactivado".into() });
    PcxMcp::local(core.tools.clone()).serve(rmcp::transport::stdio()).await?.waiting().await?;
    stop.cancel();
    core.stop().await;
    Ok(())
}

async fn axum_serve(listener: tokio::net::TcpListener, router: axum::Router, stop: CancellationToken) -> std::io::Result<()> {
    axum::serve(listener, router).with_graceful_shutdown(async move { stop.cancelled().await }).await
}

async fn replay(dir: PathBuf, speed: f64, ingest: Option<String>) -> anyhow::Result<()> {
    use zeromq::{PushSocket, Socket, SocketSend, ZmqMessage};
    let store = pcx_core::store::Store::open(Some(&dir))?;
    let types: Vec<String> = INPUT_TYPES.iter().map(|s| s.to_string()).collect();
    let (envs, _) = store.after(0, &types, i64::MAX);
    let url = ingest.or_else(|| std::env::var("PCX_INGEST").ok()).unwrap_or_else(|| Settings::default().ingest_url);
    let mut push = PushSocket::new();
    push.connect(&url).await?;
    tokio::time::sleep(Duration::from_millis(200)).await;
    let mut prev: Option<i64> = None;
    for e in &envs {
        if let (Some(p), true) = (prev, speed > 0.0) {
            tokio::time::sleep(Duration::from_secs_f64(((e.t_pub_ns - p).max(0) as f64 / 1e9) / speed)).await;
        }
        prev = Some(e.t_pub_ns);
        push.send(ZmqMessage::from(pcx_core::proto::encode_msgpack(e))).await?;
    }
    eprintln!("replay: {} eventos enviados a {url}", envs.len());
    Ok(())
}
