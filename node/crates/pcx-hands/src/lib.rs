//! pcx-hands: manos y toque para el nodo local de pcx.
//!
//! Cuadro (`&[u8]` RGB/BGR + [`FrameMeta`]) → [`HandTracker`] (palma + 21 landmarks con `ort`, seguimiento, IDs,
//! One Euro) → [`TouchTracker`] (pinch, apoyo, dwell, zonas, episodios) → [`HandsOutput`]: `HandState` del
//! contrato y eventos con la hora de captura.
//!
//! ```no_run
//! use pcx_hands::*;
//! let mut eng = HandsEngine::from_cache(EngineConfig::default()).unwrap();
//! # let (rgb, meta): (Vec<u8>, FrameMeta) = unimplemented!();
//! let frame = Frame::packed(&rgb, meta.w as usize, meta.h as usize, PixelFormat::Rgb8).unwrap();
//! let out = eng.process(&frame, &meta).unwrap();
//! for e in &out.events { println!("{}", serde_json::to_string(e).unwrap()); }
//! ```
pub mod frame;
pub mod geom;
pub mod infer;
pub mod models;
pub mod touch;
pub mod tracker;
pub mod types;

pub use frame::{Frame, FrameSource, PixelFormat};
pub use geom::Roi;
pub use infer::{Backend, DetectorConfig, HandDetector, LandmarkResult, PalmDetection};
pub use touch::{TouchConfig, TouchHand, TouchTracker, Zone};
pub use tracker::{HandTracker, OneEuro, TrackedHand, TrackerConfig, TrackerTimings};
pub use types::*;

#[derive(Debug, thiserror::Error)]
pub enum HandsError {
    #[error("onnxruntime: {0}")]
    Ort(String),
    #[error("modelo: {0}")]
    Model(String),
    #[error("cuadro inválido: {0}")]
    BadFrame(String),
    #[error(transparent)]
    Io(#[from] std::io::Error),
}

#[derive(Debug, Clone, Default)]
pub struct EngineConfig {
    pub detector: DetectorConfig,
    pub tracker: TrackerConfig,
    pub touch: TouchConfig,
    /// Homografía cámara (px) → `table_mm` (fila mayor). Sin ella no hay `tip_mm` ni eventos del contrato.
    pub homography: Option<[f64; 9]>,
}

/// Resultado de un cuadro.
#[derive(Debug, Clone)]
pub struct HandsOutput {
    pub t_capture_ns: i64,
    pub hands: Vec<HandState>,
    pub tracked: Vec<TrackedHand>,
    pub events: Vec<HandsEvent>,
    pub timings: TrackerTimings,
    pub total_ms: f64,
}

/// Percepción + toque de una cámara.
pub struct HandsEngine {
    pub tracker: HandTracker,
    pub touch: TouchTracker,
}

impl HandsEngine {
    pub fn new(det: HandDetector, cfg: EngineConfig) -> Self {
        Self {
            tracker: HandTracker::new(det, cfg.tracker),
            touch: TouchTracker::new(cfg.touch, cfg.homography),
        }
    }

    /// Modelos desde la caché del SO (los baja si faltan, con sha256).
    pub fn from_cache(cfg: EngineConfig) -> Result<Self, HandsError> {
        Ok(Self::new(HandDetector::from_cache(cfg.detector)?, cfg))
    }

    pub fn set_homography(&mut self, h: Option<[f64; 9]>) {
        self.touch.homography = h;
    }

    pub fn process(&mut self, frame: &Frame, meta: &FrameMeta) -> Result<HandsOutput, HandsError> {
        let t0 = std::time::Instant::now();
        let (tracked, timings) = self
            .tracker
            .process(frame, meta.t_capture_ns as f64 * 1e-9)?;
        let inputs: Vec<TouchHand> = tracked
            .iter()
            .map(|h| TouchHand {
                id: format!("h{}", h.id),
                landmarks_px: h.xy(),
            })
            .collect();
        let events = self.touch.update(&inputs, meta.t_capture_ns);
        let hands = tracked
            .iter()
            .zip(&inputs)
            .map(|(h, i)| HandState {
                id: i.id.clone(),
                camera_id: meta.camera_id.clone(),
                handedness: h.handedness,
                landmarks_px: i.landmarks_px.to_vec(),
                tip_mm: self.touch.to_mm(i.landmarks_px[touch::INDEX_TIP]),
                pinch: self.touch.pinch(&i.id),
                t_capture_ns: meta.t_capture_ns,
            })
            .collect();
        Ok(HandsOutput {
            t_capture_ns: meta.t_capture_ns,
            hands,
            tracked,
            events,
            timings,
            total_ms: t0.elapsed().as_secs_f64() * 1e3,
        })
    }

    /// Bucle simple: consume `src` hasta que se acabe y entrega cada resultado.
    pub fn run(
        &mut self,
        src: &mut impl FrameSource,
        mut on_output: impl FnMut(&HandsOutput),
    ) -> Result<(), HandsError> {
        while let Some((frame, meta)) = src.next_frame() {
            let out = self.process(&frame, &meta)?;
            on_output(&out);
        }
        Ok(())
    }
}
