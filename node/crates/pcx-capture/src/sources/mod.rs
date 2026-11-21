//! Adaptadores de fuente. Cada uno corre en su hilo o tarea y deja el último cuadro en un `LatestTx`; termina cuando
//! la bomba suelta el receptor. Para agregar uno: una función `spawn(…, tx)` y una rama en `open_source`.

#[cfg(target_os = "macos")]
pub mod avf;
pub mod camera;
pub mod mjpeg;
pub mod phone;
pub mod replay;
#[cfg(feature = "uvc")]
pub mod uvc;

use crate::contracts::DeviceKind;
use crate::frame::{latest_box, BoxError, LatestRx};
use std::net::SocketAddr;
use std::path::PathBuf;

#[derive(Clone, Debug, PartialEq)]
pub enum SourceSpec {
    Replay(PathBuf),
    Uvc(u32),
    /// Cámara local con backend nativo, por id estable o subcadena única del nombre (`pcx-capture list`).
    Cam(String),
    Mjpeg(String),
    Phone(u16),
}

impl std::str::FromStr for SourceSpec {
    type Err = String;
    /// `replay:<ruta>` · `cam:<id|nombre>` · `uvc:<índice>` · `mjpeg:<url>` · `phone[:<puerto>]`.
    fn from_str(s: &str) -> Result<Self, String> {
        let (kind, arg) = s.split_once(':').unwrap_or((s, ""));
        Ok(match kind {
            "replay" => Self::Replay(PathBuf::from(arg)),
            "uvc" => Self::Uvc(if arg.is_empty() { 0 } else { arg.parse().map_err(|_| format!("índice inválido: {arg}"))? }),
            "cam" if !arg.is_empty() => Self::Cam(arg.to_string()),
            "mjpeg" => Self::Mjpeg(arg.to_string()),
            "phone" => Self::Phone(if arg.is_empty() { phone::DEFAULT_PORT } else { arg.parse().map_err(|_| "puerto inválido")? }),
            _ => return Err(format!("fuente desconocida: {s:?} (replay:|cam:|uvc:|mjpeg:|phone)")),
        })
    }
}

impl SourceSpec {
    pub fn adapter(&self) -> &'static str {
        match self {
            Self::Replay(_) => "replay",
            Self::Uvc(_) => "uvc",
            Self::Cam(_) => "cam",
            Self::Mjpeg(_) => "mjpeg",
            Self::Phone(_) => "phone",
        }
    }
    pub fn device_kind(&self) -> DeviceKind {
        if matches!(self, Self::Phone(_)) { DeviceKind::Phone } else { DeviceKind::Camera }
    }
}

#[derive(Clone, Debug)]
pub struct SourceOpts {
    /// Tamaño de salida (w, h); los cuadros se redimensionan si no calzan.
    pub size: Option<(u32, u32)>,
    pub fps: Option<f64>,
    pub max_speed: bool,
    pub looped: bool,
    /// Interfaz para `phone` (por defecto 0.0.0.0).
    pub bind_ip: std::net::IpAddr,
    /// Exposición/balance de blancos fijos (best-effort, solo `cam`).
    pub controls: camera::CamControls,
}

impl Default for SourceOpts {
    fn default() -> Self {
        Self { size: Some((1280, 720)), fps: None, max_speed: false, looped: true, bind_ip: [0, 0, 0, 0].into(), controls: Default::default() }
    }
}

/// Fuente en marcha: soltar `rx` la detiene.
pub struct RunningSource {
    pub name: String,
    pub spec: SourceSpec,
    pub rx: LatestRx,
    /// Texto para el usuario (p. ej. la URL del celular).
    pub info: Option<String>,
    pub phone: Option<phone::PhoneServer>,
}

pub async fn open_source(spec: &SourceSpec, opts: &SourceOpts) -> Result<RunningSource, BoxError> {
    let (tx, rx) = latest_box();
    let mut info = None;
    let mut phone_srv = None;
    let name = match spec {
        SourceSpec::Replay(p) => {
            let o = replay::ReplayOpts {
                fps: opts.fps.unwrap_or(30.0),
                max_speed: opts.max_speed,
                looped: opts.looped,
                size: opts.size,
            };
            replay::spawn(p, o, tx)?;
            format!("replay:{}", p.display())
        }
        SourceSpec::Mjpeg(url) => {
            mjpeg::spawn(url.clone(), opts.size, tx);
            format!("mjpeg:{url}")
        }
        SourceSpec::Phone(port) => {
            let srv = phone::spawn(SocketAddr::new(opts.bind_ip, *port), opts.size, opts.fps.unwrap_or(30.0) as u32, tx).await?;
            info = Some(format!("abre {} en el celular", srv.url()));
            phone_srv = Some(srv);
            format!("phone:{port}")
        }
        SourceSpec::Cam(sel) => {
            camera::spawn(sel, opts.size.unwrap_or((1280, 720)), opts.fps.unwrap_or(30.0), opts.controls, tx)?;
            format!("cam:{sel}")
        }
        #[cfg(feature = "uvc")]
        SourceSpec::Uvc(i) => {
            uvc::spawn(*i, opts.size.unwrap_or((1280, 720)), opts.fps.unwrap_or(30.0) as u32, tx)?;
            format!("uvc:{i}")
        }
        #[cfg(not(feature = "uvc"))]
        SourceSpec::Uvc(_) => {
            drop(tx);
            return Err("uvc no está compilado: cargo build -p pcx-capture --features uvc".into());
        }
    };
    Ok(RunningSource { name, spec: spec.clone(), rx, info, phone: phone_srv })
}
