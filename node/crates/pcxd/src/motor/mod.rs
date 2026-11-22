//! `pcx motor`: el motor local. Cámara → foto limpia → objetos en mm, servidos a la web app por WebSocket.
//!
//! cámara → **motor (este proceso)** → web app (consola en el navegador) → segunda pantalla → proyector.
//! Protocolo v0: `GET /v1/health` y WS `/v1/ws` en `127.0.0.1:9230`; ver `README-motor.md` de este crate.
//!
//! - [`server`]: HTTP/WS, token, `Origin`, CORS y Private Network Access.
//! - [`engine`]: cuándo mirar la mesa (manos fuera, cada 45 s, `perceive`) y qué publicar (`objects`, `status`).
//! - [`clean`]: foto limpia con el apagón pedido a la web app (`blank` → `blanked` → cuadros → `unblank`).
//! - [`frames`]: cámara propia (`pcx-capture`) o cuadros pedidos a la web app (`need_frame`).
//! - [`default_perceptor`]: el `Pipeline` real de `pcx-perception` (cascada + MobileSAM con la feature `onnx`).
//! - [`scenes`]: módulos de percepción por escena (`scene{name}`, `event`, cámara lateral en vivo). Se registran en
//!   [`scenes::registry`].
//! - [`diff_only`]: `Perceptor` de respaldo (`PCX_MOTOR_PERCEPTOR=diff`, y el de los tests).

pub mod clean;
pub mod diff_only;
pub mod engine;
pub mod frames;
pub mod geom;
pub mod origin;
pub mod proto;
pub mod scenes;
pub mod server;
pub mod synthetic;

use std::collections::BTreeMap;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use clap::Args;
use pcx_perception::pipeline::{Pipeline, PipelineConfig};
use pcx_perception::pipeline_api::Perceptor;
use pcx_perception::scene_api::SceneRegistry;
use tokio::sync::mpsc;
use tokio_util::sync::CancellationToken;

use self::clean::CleanSettings;
use self::engine::{Engine, EngineConfig, EngineHandle};
use self::frames::{Camera, CameraOpts, CameraSpec, FrameHub};
use self::origin::{Origins, GATEWAY_URL};
use self::proto::Models;

pub const DEFAULT_HOST: &str = "127.0.0.1";
pub const DEFAULT_PORT: u16 = 9230;
/// Directorio del token (`motor.token`); por defecto `~/.pcx`.
pub const DIR_ENV: &str = "PCX_MOTOR_DIR";
const LOG: &str = "[pcx motor]";

fn parse_size(s: &str) -> Result<(u32, u32), String> {
    let (w, h) = s.split_once('x').ok_or("formato WxH, p. ej. 1280x720")?;
    Ok((w.parse().map_err(|_| "ancho inválido")?, h.parse().map_err(|_| "alto inválido")?))
}

#[derive(Args, Clone, Debug)]
pub struct MotorArgs {
    #[arg(long, env = "PCX_MOTOR_HOST", default_value = DEFAULT_HOST)]
    pub host: String,
    #[arg(long, env = "PCX_MOTOR_PORT", default_value_t = DEFAULT_PORT)]
    pub port: u16,
    /// Cámara propia: nombre (subcadena única), id o índice de `pcx cameras`; `replay:<carpeta|archivo.mjpeg>`;
    /// `synthetic` (mesa simulada); `none` o nada = sin cámara, los cuadros se piden a la web app.
    #[arg(long, env = "PCX_MOTOR_CAMERA")]
    pub camera: Option<String>,
    /// Cámara lateral (en vivo) para los módulos de escena que la pidan: nombre, id o índice de `pcx cameras`,
    /// `replay:<carpeta|archivo.mjpeg>` o `synthetic`.
    #[arg(long, env = "PCX_MOTOR_CAMERA_LATERAL")]
    pub camera_lateral: Option<String>,
    /// Tamaño de la cámara lateral.
    #[arg(long, value_parser = parse_size, default_value = "1280x720")]
    pub lateral_size: (u32, u32),
    /// Tamaño inicial de la cámara; al llegar `calib` se usa su `camera_px`.
    #[arg(long, value_parser = parse_size, default_value = "1280x720")]
    pub size: (u32, u32),
    #[arg(long)]
    pub fps: Option<f64>,
    /// Fija la exposición de la cámara (best-effort): la foto limpia queda más pareja.
    #[arg(long)]
    pub lock_exposure: bool,
    /// Directorio de `motor.token` (0600). Por defecto `~/.pcx`.
    #[arg(long, env = DIR_ENV)]
    pub dir: Option<PathBuf>,
    /// Web app: su origen queda permitido y con ella se arma la URL de emparejamiento.
    #[arg(long, env = "PCX_MOTOR_GATEWAY", default_value = GATEWAY_URL)]
    pub gateway: String,
    /// Orígenes extra permitidos (exactos, separados por coma).
    #[arg(long = "allow-origin", env = "PCX_MOTOR_ALLOWED_ORIGINS", value_delimiter = ',')]
    pub allow_origin: Vec<String>,
    /// Calma sin manos antes de la foto limpia (ms).
    #[arg(long, default_value_t = 250)]
    pub calm_ms: u64,
    /// Ciclo periódico (s); 0 lo apaga.
    #[arg(long, default_value_t = 45.0)]
    pub review_every_s: f64,
    /// Ventana de captura tras pedir `blank` (ms). Por defecto 300 con cámara propia y 800 sin ella.
    #[arg(long)]
    pub blank_ms: Option<u32>,
    /// Sin caída de luminancia no hay foto (por defecto, con `blanked` y la escena ya oscura se elige por tiempos).
    #[arg(long)]
    pub require_drop: bool,
    /// Carpeta con los pesos de MobileSAM. Si no los tiene se mira `PCX_MODELS_DIR` y después `~/.pcx/models`; sin
    /// pesos (o sin la feature `onnx`) el motor corre solo con la cascada y lo dice en `welcome.models`.
    #[arg(long)]
    pub models: Option<PathBuf>,
    /// Tras un ciclo con cambios, pide otro a este plazo (ms): ahí se retira lo que se fue. 0 lo apaga.
    #[arg(long, default_value_t = 1000)]
    pub settle_ms: u64,
}

/// `diff` deja el `Perceptor` de respaldo en vez del `Pipeline`.
pub const PERCEPTOR_ENV: &str = "PCX_MOTOR_PERCEPTOR";

/// `Perceptor` de respaldo: diferencia contra la mesa vacía, sin ids estables ni SAM.
pub fn fallback_perceptor() -> (Box<dyn Perceptor>, Models) {
    (Box::new(diff_only::DiffOnly::default()), Models::default())
}

/// `Perceptor` del motor: el `Pipeline` de `pcx-perception` (cascada N0–N2b + MobileSAM). Los pesos se buscan en
/// `models_dir`, `PCX_MODELS_DIR` y `~/.pcx/models`; si no están, o el binario no trae la feature `onnx`, corre solo
/// la cascada y `Models::note` dice por qué.
pub fn default_perceptor(models_dir: Option<PathBuf>) -> (Box<dyn Perceptor>, Models) {
    if std::env::var(PERCEPTOR_ENV).is_ok_and(|v| v.eq_ignore_ascii_case("diff")) {
        return fallback_perceptor();
    }
    let p = Pipeline::new(models_dir, PipelineConfig::from_env());
    let (sam, status) = (p.has_sam(), p.sam_status());
    let note = if sam { format!("cascada + {status}") } else { status.to_string() };
    (Box::new(p), Models { sam, hands: false, note })
}

pub fn default_dir() -> PathBuf {
    std::env::var_os(DIR_ENV)
        .filter(|d| !d.is_empty())
        .map(PathBuf::from)
        .or_else(|| std::env::home_dir().map(|h| h.join(".pcx")))
        .unwrap_or_else(|| std::env::temp_dir().join("pcx"))
}

/// Token del motor: `<dir>/motor.token` (0600, directorio 0700); se genera la primera vez.
pub fn load_token(dir: &Path) -> anyhow::Result<String> {
    pcx_core::security::load_token(dir, "motor")
}

/// URL que se abre en el navegador para emparejar la web app con este motor.
pub fn pairing_url(gateway: &str, host: &str, port: u16, token: &str) -> String {
    format!("{}/?motor=ws://{host}:{port}&motor_token={token}", gateway.trim_end_matches('/'))
}

/// Dónde escucha y dónde guarda el token un `pcx motor` lanzado con `cmd` y `env` (lo usa `pcx up`).
pub fn endpoint_of(cmd: &[String], env: &BTreeMap<String, String>) -> (String, u16, PathBuf, String) {
    let flag = |name: &str| {
        cmd.iter().enumerate().find_map(|(i, a)| match a.strip_prefix(name) {
            Some("") => cmd.get(i + 1).cloned(),
            Some(rest) => rest.strip_prefix('=').map(str::to_owned),
            None => None,
        })
    };
    let var = |k: &str| env.get(k).cloned().or_else(|| std::env::var(k).ok()).filter(|v| !v.is_empty());
    let host = flag("--host").or_else(|| var("PCX_MOTOR_HOST")).unwrap_or_else(|| DEFAULT_HOST.into());
    let port = flag("--port").or_else(|| var("PCX_MOTOR_PORT")).and_then(|p| p.parse().ok()).unwrap_or(DEFAULT_PORT);
    let dir = flag("--dir").or_else(|| var(DIR_ENV)).map(PathBuf::from).unwrap_or_else(default_dir);
    let gateway = flag("--gateway").or_else(|| var("PCX_MOTOR_GATEWAY")).unwrap_or_else(|| GATEWAY_URL.into());
    (host, port, dir, gateway)
}

pub struct MotorConfig {
    pub host: String,
    /// 0 = puerto libre cualquiera (tests).
    pub port: u16,
    pub token: String,
    pub origins: Origins,
    pub camera: CameraSpec,
    pub size: (u32, u32),
    pub camera_opts: CameraOpts,
    pub engine: EngineConfig,
    pub perceptor: Box<dyn Perceptor>,
    pub hello_timeout: Duration,
    /// Cámara lateral (`--camera-lateral`) y su tamaño.
    pub lateral: CameraSpec,
    pub lateral_size: (u32, u32),
    /// Módulos de escena; por defecto los de [`scenes::registry`].
    pub scenes: SceneRegistry,
}

impl MotorConfig {
    /// Valores por defecto del motor con el `Perceptor` de respaldo (el real: [`default_perceptor`]).
    pub fn new(token: &str, camera: CameraSpec) -> Self {
        let (perceptor, models) = fallback_perceptor();
        MotorConfig {
            host: DEFAULT_HOST.into(),
            port: DEFAULT_PORT,
            token: token.into(),
            origins: Origins::new(GATEWAY_URL, &[]),
            camera,
            size: (1280, 720),
            camera_opts: CameraOpts::default(),
            engine: EngineConfig { models, ..EngineConfig::default() },
            perceptor,
            hello_timeout: Duration::from_secs(5),
            lateral: CameraSpec::None,
            lateral_size: (1280, 720),
            scenes: scenes::registry(),
        }
    }
}

/// Motor en marcha.
pub struct Running {
    pub addr: SocketAddr,
    stop: CancellationToken,
    tasks: Vec<tokio::task::JoinHandle<()>>,
}

impl Running {
    pub async fn shutdown(mut self) {
        self.stop.cancel();
        for t in std::mem::take(&mut self.tasks) {
            let _ = t.await;
        }
    }
}

impl Drop for Running {
    fn drop(&mut self) {
        self.stop.cancel();
    }
}

/// Abre la cámara, levanta el orquestador y empieza a escuchar.
pub async fn start(cfg: MotorConfig) -> anyhow::Result<Running> {
    anyhow::ensure!(cfg.token.len() >= 16, "el token del motor es demasiado corto");
    let listener = tokio::net::TcpListener::bind((cfg.host.as_str(), cfg.port)).await.map_err(|e| {
        anyhow::anyhow!("no pude escuchar en {}:{} ({e}); ¿ya hay un `pcx motor` corriendo?", cfg.host, cfg.port)
    })?;
    let addr = listener.local_addr()?;
    let (hub, need) = FrameHub::new();
    let camera = Camera::start(cfg.camera, cfg.size, cfg.camera_opts, hub.clone()).await;
    let stop = CancellationToken::new();
    let (tx, rx) = mpsc::channel(256);
    let models = cfg.engine.models.clone();
    let scenes = scenes::Scenes::new(cfg.scenes);
    let (scene_tx, scene_rx) = mpsc::unbounded_channel();
    let mut tasks = Vec::new();
    // Cámara lateral: su propio buzón y un hilo que le pasa cada cuadro al módulo de escena que la pidió.
    let lateral = if matches!(cfg.lateral, CameraSpec::None) {
        None
    } else {
        let (lhub, _need) = FrameHub::new();
        let cam = Camera::start(cfg.lateral, cfg.lateral_size, cfg.camera_opts, lhub.clone()).await;
        if lhub.camera().is_none() {
            eprintln!("{LOG} sin cámara lateral: los módulos de escena no reciben cuadros en vivo");
            cam.stop().await;
            None
        } else {
            let (h, s, st) = (lhub.clone(), scenes.clone(), stop.clone());
            // Hilo propio (no del pool de tokio): si el motor se suelta sin `shutdown`, no frena la salida.
            std::thread::Builder::new()
                .name("pcx-motor-lateral".into())
                .spawn(move || scenes::live_loop(scenes::LATERAL, h, s, scene_tx, st))?;
            let st = stop.clone();
            tasks.push(tokio::spawn(async move {
                st.cancelled().await;
                cam.stop().await;
            }));
            Some(lhub)
        }
    };
    let engine = Engine::new(cfg.engine, hub.clone(), camera, cfg.perceptor, scenes, lateral);
    tasks.push(tokio::spawn(engine.run(rx, need, scene_rx, stop.clone())));
    let st = Arc::new(server::ServerState {
        engine: EngineHandle(tx),
        hub,
        origins: cfg.origins,
        token: cfg.token,
        host: cfg.host,
        port: addr.port(),
        models,
        hello_timeout: cfg.hello_timeout,
    });
    let s2 = stop.clone();
    let http = tokio::spawn(async move {
        let serve =
            axum::serve(listener, server::router(st)).with_graceful_shutdown(async move { s2.cancelled().await });
        // Los WebSocket abiertos no deben frenar la salida.
        if let Err(e) = serve.await {
            eprintln!("{LOG} servidor: {e}");
        }
    });
    tasks.push(http);
    Ok(Running { addr, stop, tasks })
}

/// `pcx motor`: corre hasta `stop`.
pub async fn run(a: MotorArgs, stop: CancellationToken) -> anyhow::Result<()> {
    let dir = a.dir.clone().unwrap_or_else(default_dir);
    let token = load_token(&dir)?;
    let camera = CameraSpec::parse(a.camera.as_deref(), a.size).map_err(|e| anyhow::anyhow!("--camera: {e}"))?;
    let own_camera = !matches!(camera, CameraSpec::None);
    let lateral = CameraSpec::parse(a.camera_lateral.as_deref(), a.lateral_size)
        .map_err(|e| anyhow::anyhow!("--camera-lateral: {e}"))?;
    let (perceptor, models) = default_perceptor(a.models.clone());
    let note = models.note.clone();
    let cfg = MotorConfig {
        host: a.host.clone(),
        port: a.port,
        token: token.clone(),
        origins: Origins::new(&a.gateway, &a.allow_origin),
        camera,
        size: a.size,
        camera_opts: CameraOpts { fps: a.fps, lock_exposure: a.lock_exposure },
        engine: EngineConfig {
            calm: Duration::from_millis(a.calm_ms),
            review_every: Duration::from_secs_f64(a.review_every_s.max(0.0)),
            clean: CleanSettings::new(a.blank_ms.unwrap_or(if own_camera { 300 } else { 800 }), a.require_drop),
            models,
            settle_after: Duration::from_millis(a.settle_ms),
            ..EngineConfig::default()
        },
        perceptor,
        hello_timeout: Duration::from_secs(5),
        lateral,
        lateral_size: a.lateral_size,
        scenes: scenes::registry(),
    };
    let running = start(cfg).await?;
    eprintln!("{LOG} percepción: {}", if note.is_empty() { "de respaldo (diff)" } else { &note });
    eprintln!(
        "{LOG} ws://{}/v1/ws · http://{}/v1/health · token en {}",
        running.addr,
        running.addr,
        dir.join("motor.token").display()
    );
    // Bajo `pcx up` la URL ya la imprimió el supervisor.
    if std::env::var_os(crate::supervisor::SUPERVISOR_PID_ENV).is_none() {
        eprintln!("{LOG} abre: {}", pairing_url(&a.gateway, &a.host, running.addr.port(), &token));
    }
    stop.cancelled().await;
    // Con clientes conectados el cierre ordenado de axum espera; se le da un momento y se sale.
    let _ = tokio::time::timeout(Duration::from_secs(3), running.shutdown()).await;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn url_de_emparejamiento_como_en_el_protocolo() {
        assert_eq!(
            pairing_url("https://pcx-gateway.onrender.com/", "127.0.0.1", 9230, "abc"),
            "https://pcx-gateway.onrender.com/?motor=ws://127.0.0.1:9230&motor_token=abc"
        );
    }

    #[test]
    fn endpoint_desde_cmd_y_entorno() {
        let cmd = |s: &str| s.split(' ').map(str::to_owned).collect::<Vec<_>>();
        let mut env = BTreeMap::new();
        env.insert(DIR_ENV.to_string(), "/tmp/x".to_string());
        env.insert("PCX_MOTOR_PORT".to_string(), "9999".to_string());
        let (h, p, d, g) = endpoint_of(&cmd("--camera c920"), &env);
        assert_eq!((h.as_str(), p, d, g.as_str()), ("127.0.0.1", 9999, PathBuf::from("/tmp/x"), GATEWAY_URL));
        let (h, p, d, g) =
            endpoint_of(&cmd("--port=9300 --host localhost --dir /tmp/y --gateway http://localhost:8000"), &env);
        assert_eq!(
            (h.as_str(), p, d, g.as_str()),
            ("localhost", 9300, PathBuf::from("/tmp/y"), "http://localhost:8000")
        );
    }

    #[cfg(unix)]
    #[test]
    fn token_privado_y_estable() {
        use std::os::unix::fs::PermissionsExt;
        let d = tempfile::tempdir().unwrap();
        let dir = d.path().join(".pcx");
        let t = load_token(&dir).unwrap();
        assert_eq!(t.len(), 64);
        assert_eq!(load_token(&dir).unwrap(), t);
        assert_eq!(std::fs::metadata(dir.join("motor.token")).unwrap().permissions().mode() & 0o777, 0o600);
        assert_eq!(std::fs::metadata(&dir).unwrap().permissions().mode() & 0o777, 0o700);
    }
}
