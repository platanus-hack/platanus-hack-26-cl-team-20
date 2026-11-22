//! Configuración (variables de entorno, todas opcionales) y composición del core en un proceso.

use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::atomic::AtomicBool;
use std::sync::Arc;

use tokio_util::sync::CancellationToken;

use crate::bus::{self, zmq_endpoint, ZMQ_PORTS};
use crate::cloud::CloudLink;
use crate::frames::{FrameSource, SyntheticFrameSource};
use crate::proto::{Device, DeviceKind, DeviceStatus, HomePolicy, Json};
use crate::server::{self, AppState};
use crate::store::{default_data_dir, Store};
use crate::tools::Tools;
use crate::world::{World, WorldConfig, WorldHandle};

pub const RESERVED_PORTS: std::ops::RangeInclusive<u16> = 8787..=8799;

#[derive(Debug, Clone)]
pub struct Settings {
    pub host: String,
    pub port: u16,
    /// Bearer de MCP, estado y catálogo. `None` genera un secreto persistente al arrancar.
    pub token: Option<String>,
    pub console_token: Option<String>,
    pub debug_token: Option<String>,
    /// Secreto del worker de manos para la ingesta ZeroMQ (`["pcx-auth", token, sobre]`). Solo los toques que
    /// llegan con él pueden confirmar domótica.
    pub hands_token: Option<String>,
    /// Orígenes de navegador admitidos además del propio (`http://<host>:<puerto>`). Coincidencia exacta.
    pub allowed_origins: Vec<String>,
    /// Carpeta de assets del renderer: las rutas de imagen del agente deben quedar dentro.
    pub assets_dir: Option<PathBuf>,
    pub debug: bool,
    pub node_id: String,
    pub ingest_url: String,
    pub events_url: String,
    pub zmq_enabled: bool,
    /// Directorio del SQLite. `None` = en memoria (tests).
    pub data_dir: Option<PathBuf>,
    pub surface_mm: (f64, f64),
    pub cloud_url: Option<String>,
    pub node_token: Option<String>,
    pub tick_hz: f64,
    pub home_policy: HomePolicy,
}

impl Default for Settings {
    fn default() -> Self {
        Settings {
            host: "127.0.0.1".into(),
            port: 9200,
            token: None,
            console_token: None,
            debug_token: None,
            hands_token: None,
            allowed_origins: vec![],
            assets_dir: None,
            debug: false,
            node_id: "mesa-1".into(),
            ingest_url: zmq_endpoint("ingest", ZMQ_PORTS.0, None),
            events_url: zmq_endpoint("events", ZMQ_PORTS.1, None),
            zmq_enabled: true,
            data_dir: Some(default_data_dir()),
            surface_mm: (800.0, 500.0),
            cloud_url: None,
            node_token: None,
            tick_hz: 20.0,
            home_policy: HomePolicy::default(),
        }
    }
}

impl Settings {
    /// `PCX_HOST`, `PCX_PORT`, `PCX_TOKEN` (vacío = generar), `PCX_NODE_ID`, `PCX_INGEST`, `PCX_EVENTS`, `PCX_ZMQ=0`,
    /// `PCX_DATA_DIR`, `PCX_SURFACE_MM=800x500`, `PCX_CLOUD_URL`, `PCX_NODE_TOKEN`, `PCX_HOME_POLICY` (JSON).
    pub fn from_env() -> anyhow::Result<Settings> {
        let e = |k: &str| std::env::var(k).ok().filter(|v| !v.is_empty());
        let mut s = Settings::default();
        if let Some(v) = e("PCX_HOST") {
            s.host = v;
        }
        if let Some(v) = e("PCX_PORT") {
            s.port = v.parse()?;
        }
        if let Ok(v) = std::env::var("PCX_TOKEN") {
            s.token = (!v.is_empty()).then_some(v);
        }
        if let Some(v) = e("PCX_NODE_ID") {
            s.node_id = v;
        }
        if let Some(v) = e("PCX_INGEST") {
            s.ingest_url = v;
        }
        if let Some(v) = e("PCX_EVENTS") {
            s.events_url = v;
        }
        s.zmq_enabled = e("PCX_ZMQ").as_deref() != Some("0");
        if let Some(v) = e("PCX_SURFACE_MM") {
            let (w, h) = v
                .to_lowercase()
                .split_once('x')
                .map(|(a, b)| (a.to_string(), b.to_string()))
                .ok_or_else(|| anyhow::anyhow!("PCX_SURFACE_MM: WxH"))?;
            s.surface_mm = (w.parse()?, h.parse()?);
        }
        s.cloud_url = e("PCX_CLOUD_URL");
        s.node_token = e("PCX_NODE_TOKEN");
        s.console_token = e("PCX_CONSOLE_TOKEN");
        s.debug_token = e("PCX_DEBUG_TOKEN");
        s.hands_token = crate::security::hands_token_from_env()?;
        s.allowed_origins = e("PCX_ALLOWED_ORIGINS")
            .map(|v| {
                v.split(',').map(|o| o.trim().trim_end_matches('/').to_string()).filter(|o| !o.is_empty()).collect()
            })
            .unwrap_or_default();
        s.assets_dir = e("PCX_ASSETS_DIR").map(PathBuf::from);
        s.prepare_tokens()?;
        if let Some(v) = e("PCX_HOME_POLICY") {
            s.home_policy = serde_json::from_str(&v)?;
        }
        s.check()?;
        Ok(s)
    }

    pub fn prepare_tokens(&mut self) -> anyhow::Result<()> {
        let dir = self.data_dir.clone();
        for (name, token) in [
            ("mcp", &mut self.token),
            ("console", &mut self.console_token),
            ("debug", &mut self.debug_token),
            ("hands", &mut self.hands_token),
        ] {
            if token.as_ref().is_none_or(|t| t.is_empty()) {
                *token = Some(match &dir {
                    Some(d) => crate::security::load_token(d, name)?,
                    None => crate::security::random_token(),
                });
            }
        }
        let all = [&self.token, &self.console_token, &self.debug_token, &self.hands_token];
        let distinct: std::collections::HashSet<_> = all.iter().collect();
        anyhow::ensure!(distinct.len() == all.len(), "MCP, consola, debug y manos requieren tokens distintos");
        Ok(())
    }

    pub fn check(&self) -> anyhow::Result<()> {
        if RESERVED_PORTS.contains(&self.port) {
            anyhow::bail!("puerto {} reservado (8787–8799)", self.port);
        }
        Ok(())
    }
}

/// Todo el nodo en un proceso: actor, log, tools, HTTP/MCP, bus y enlace a la nube.
pub struct Core {
    pub settings: Settings,
    pub world: WorldHandle,
    pub tools: Tools,
    pub cancel: CancellationToken,
    pub cloud_connected: Arc<AtomicBool>,
    pub bus: Option<Arc<bus::BusStats>>,
    _instance: Option<std::fs::File>,
}

impl Core {
    /// Arranca el actor (restaurando estado y `seq` del SQLite), el bus y la nube. `frames` = la fuente de la
    /// captura; `None` = sintética.
    pub async fn start(mut settings: Settings, frames: Option<Arc<dyn FrameSource>>) -> anyhow::Result<Core> {
        settings.check()?;
        let instance = settings.data_dir.as_ref().map(|d| crate::security::instance_lock(d)).transpose()?;
        settings.prepare_tokens()?;
        let dir = settings.data_dir.clone();
        let cfg = WorldConfig {
            node_id: settings.node_id.clone(),
            surface_mm: settings.surface_mm,
            tick_hz: settings.tick_hz,
            home_policy: settings.home_policy.clone(),
            assets_dir: settings.assets_dir.clone(),
        };
        let world = tokio::task::spawn_blocking(move || -> anyhow::Result<_> {
            let store = Arc::new(Store::open(dir.as_deref())?);
            Ok(World::spawn(cfg, store))
        })
        .await??;
        let frames: Arc<dyn FrameSource> =
            frames.unwrap_or_else(|| Arc::new(SyntheticFrameSource::new(world.state.clone())));
        for cam in frames.cameras() {
            let d = Device {
                id: cam,
                kind: DeviceKind::Camera,
                adapter: "synthetic".into(),
                caps: vec!["frames".into()],
                status: DeviceStatus::Online,
                meta: Json::new(),
            };
            world
                .call(move |w| {
                    w.devices.insert(d.id.clone(), d);
                })
                .await;
        }
        let tools = Tools::new(world.clone(), frames);
        let cancel = CancellationToken::new();
        let bus = if settings.zmq_enabled {
            Some(
                bus::start(
                    world.clone(),
                    &settings.ingest_url,
                    &settings.events_url,
                    settings.hands_token.clone(),
                    cancel.child_token(),
                )
                .await?,
            )
        } else {
            None
        };
        let cloud_connected = Arc::new(AtomicBool::new(false));
        if let Some(url) = &settings.cloud_url {
            let mut link = CloudLink::new(url.clone(), settings.node_token.clone().unwrap_or_default());
            link.connected = cloud_connected.clone();
            Arc::new(link).spawn(tools.clone(), cancel.child_token());
        }
        Ok(Core { settings, world, tools, cancel, cloud_connected, bus, _instance: instance })
    }

    pub fn router(&self) -> axum::Router {
        let st = AppState {
            world: self.world.clone(),
            tools: self.tools.clone(),
            token: self.settings.token.clone(),
            console_token: self.settings.console_token.clone(),
            debug_token: self.settings.debug_token.clone(),
            debug: self.settings.debug,
            host: self.settings.host.clone(),
            port: self.settings.port,
            allowed_origins: self.settings.allowed_origins.clone(),
            touch_n: Default::default(),
            zmq: self.bus.is_some(),
            cloud_connected: self.cloud_connected.clone(),
        };
        server::router(st, self.cancel.clone())
    }

    /// Sirve HTTP en `host:port` hasta `shutdown` (o la cancelación del core).
    pub async fn serve(&self, shutdown: impl std::future::Future<Output = ()> + Send + 'static) -> anyhow::Result<()> {
        let addr: SocketAddr = format!("{}:{}", self.settings.host, self.settings.port).parse()?;
        let listener = tokio::net::TcpListener::bind(addr).await?;
        let cancel = self.cancel.clone();
        axum::serve(listener, self.router())
            .with_graceful_shutdown(async move {
                tokio::select! { _ = shutdown => {}, _ = cancel.cancelled() => {} }
            })
            .await?;
        Ok(())
    }

    /// Detiene bus/nube y espera a que el actor persista el snapshot.
    pub async fn stop(&self) {
        self.cancel.cancel();
        self.world.call(|w| w.flush()).await;
    }
}

/// Señal de apagado en los 3 SO: Ctrl-C y, en Unix, SIGTERM.
pub async fn shutdown_signal() {
    #[cfg(unix)]
    {
        let mut term = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()).expect("SIGTERM");
        tokio::select! { _ = tokio::signal::ctrl_c() => {}, _ = term.recv() => {} }
    }
    #[cfg(not(unix))]
    {
        let _ = tokio::signal::ctrl_c().await;
    }
}
