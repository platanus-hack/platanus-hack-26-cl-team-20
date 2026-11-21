//! Bomba: fuente → anillo `pcx.cam.<id>` (bgr8, igual que Python) → `FrameMeta` a cada `MetaSink`.

use crate::contracts::{wrap, CaptureEvent, Device, DeviceStatus, Envelope, Event, FrameMeta, Payload};
use crate::frame::{fit, rgb_to_bgr, BoxError};
use crate::sink::MetaSink;
use crate::sources::{RunningSource, SourceSpec};
use pcx_shm::{PixelFormat, RingWriter, DEFAULT_SLOTS};
use std::future::Future;
use std::time::{Duration, Instant};

/// Fuente en vivo (cámara) sin cuadros durante este plazo → error (el supervisor reinicia la captura).
pub const LIVE_STALL: Duration = Duration::from_secs(5);

#[derive(Clone, Debug)]
pub struct RunConfig {
    pub camera_id: String,
    /// Tamaño del anillo y de los cuadros (w, h).
    pub size: (u32, u32),
    pub slots: usize,
    pub max_frames: Option<u64>,
    pub node_id: String,
    pub verbose: bool,
}

impl RunConfig {
    pub fn new(camera_id: &str) -> Self {
        Self {
            camera_id: camera_id.into(),
            size: (1280, 720),
            slots: DEFAULT_SLOTS,
            max_frames: None,
            node_id: default_node_id(),
            verbose: false,
        }
    }
}

pub fn default_node_id() -> String {
    std::env::var("PCX_NODE_ID")
        .ok()
        .or_else(|| std::env::var("HOSTNAME").ok())
        .or_else(|| std::env::var("COMPUTERNAME").ok())
        .map(|h| h.split('.').next().unwrap_or("").to_string())
        .filter(|h| !h.is_empty())
        .unwrap_or_else(|| "pcx-node".into())
}

/// Numera los envelopes de este proceso (`seq` monótono; el core lo renumera al ingerir).
pub struct Publisher {
    pub camera_id: String,
    pub source: String,
    pub node_id: String,
    /// Segmento de memoria compartida que va en `FrameMeta.segment` (`pcx.cam.<id>`).
    pub segment: Option<String>,
    seq: i64,
}

impl Publisher {
    pub fn new(camera_id: &str, node_id: &str) -> Self {
        Self {
            camera_id: camera_id.into(),
            source: format!("capture-{camera_id}"),
            node_id: node_id.into(),
            segment: pcx_shm::segment_name(camera_id).ok(),
            seq: 0,
        }
    }
    pub fn envelope(&mut self, ev: CaptureEvent, t_capture_ns: Option<u64>) -> Envelope {
        self.seq += 1;
        wrap(Payload::Event(ev), self.seq, &self.source, &self.node_id, t_capture_ns.map(|t| t as i64))
    }
    /// `frame_meta` de un cuadro bgr8 empaquetado (`stride = w * 3`) en `segment`.
    pub fn frame_meta(&mut self, seq: u64, t: u64, w: u32, h: u32) -> Envelope {
        let fm = FrameMeta {
            camera_id: self.camera_id.clone(),
            seq: seq as i64,
            t_capture_ns: t as i64,
            w: w as i64,
            h: h as i64,
            segment: self.segment.clone(),
            format: pcx_contracts::PixelFormat::Bgr8,
            stride: Some(w * 3),
        };
        self.envelope(Event::FrameMeta(fm), Some(t))
    }
}

#[derive(Clone, Debug, Default)]
pub struct RunStats {
    pub frames: u64,
}

fn device_event(cfg: &RunConfig, src: &RunningSource, ring: &str, status: DeviceStatus) -> CaptureEvent {
    let mut meta = pcx_contracts::Json::new();
    meta.insert("shm".into(), serde_json::json!(ring));
    meta.insert("format".into(), serde_json::json!("bgr8"));
    meta.insert("w".into(), serde_json::json!(cfg.size.0));
    meta.insert("h".into(), serde_json::json!(cfg.size.1));
    meta.insert("source".into(), serde_json::json!(src.name));
    Event::DeviceChanged {
        device: Device {
            id: cfg.camera_id.clone(),
            kind: src.spec.device_kind(),
            adapter: src.spec.adapter().into(),
            caps: vec!["frames".into()],
            status,
            meta,
        },
    }
}

/// Corre hasta que la fuente se acabe, `max_frames` o `stop`. Emite `device_changed` (online/offline) al
/// empezar y al terminar, y un `frame_meta` por cuadro. Con una cámara (`cam:`/`uvc:`), que la fuente termine o
/// pase [`LIVE_STALL`] sin cuadros es un **error** (cámara desconectada): el proceso sale con código ≠ 0.
pub async fn run(
    cfg: RunConfig,
    mut src: RunningSource,
    sinks: &mut [Box<dyn MetaSink>],
    stop: impl Future<Output = ()>,
) -> Result<RunStats, BoxError> {
    let (w, h) = cfg.size;
    let mut writer = RingWriter::create(&cfg.camera_id, w as usize, h as usize, 3, cfg.slots)?;
    let mut publ = Publisher::new(&cfg.camera_id, &cfg.node_id);
    let ring = writer.name().to_string();
    publ.segment = Some(ring.clone());
    let send = |sinks: &mut [Box<dyn MetaSink>], env: Envelope| sinks.iter_mut().for_each(|s| s.publish(&env));
    send(sinks, publ.envelope(device_event(&cfg, &src, &ring, DeviceStatus::Online), None));
    if cfg.verbose {
        eprintln!("[pcx-capture] {} → shm {ring} ({w}x{h}, {} slots)", src.name, writer.n_slots());
        if let Some(i) = &src.info {
            eprintln!("[pcx-capture] {i}");
        }
    }
    let mut stats = RunStats::default();
    let (mut t0, mut n0) = (Instant::now(), 0u64);
    tokio::pin!(stop);
    let live = matches!(src.spec, SourceSpec::Cam(_) | SourceSpec::Uvc(_));
    let mut failure: Option<String> = None;
    loop {
        tokio::select! {
            _ = &mut stop => break,
            r = src.rx.changed() => if r.is_err() {
                if live {
                    failure = Some(format!("{}: la fuente terminó (cámara desconectada o detenida)", src.name));
                }
                break
            },
            _ = tokio::time::sleep(LIVE_STALL), if live => {
                failure = Some(format!("{}: sin cuadros en {LIVE_STALL:?}", src.name));
                break
            }
        }
        let Some(frame) = src.rx.borrow_and_update().clone() else { continue };
        let img = fit(&frame.img, Some(cfg.size));
        let seq = writer
            .write_with(w, h, PixelFormat::Bgr8, Some(frame.t_capture_ns), |dst| rgb_to_bgr(img.as_raw(), dst))?;
        send(sinks, publ.frame_meta(seq, frame.t_capture_ns, w, h));
        stats.frames += 1;
        if cfg.max_frames.is_some_and(|m| stats.frames >= m) {
            break;
        }
        if cfg.verbose && t0.elapsed().as_secs_f64() >= 5.0 {
            let fps = (stats.frames - n0) as f64 / t0.elapsed().as_secs_f64();
            eprintln!("[pcx-capture] {fps:5.1} fps · seq {seq}");
            (t0, n0) = (Instant::now(), stats.frames);
        }
    }
    send(sinks, publ.envelope(device_event(&cfg, &src, &ring, DeviceStatus::Offline), None));
    writer.close();
    match failure {
        Some(f) => Err(f.into()),
        None => Ok(stats),
    }
}
