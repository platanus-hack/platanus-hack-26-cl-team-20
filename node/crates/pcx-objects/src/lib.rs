//! # pcx-objects
//!
//! Seguimiento de objetos sobre la mesa con la cascada de `seguidor_v4.py` (Daniel), portada a Rust:
//! nivel 0 diff limpio/limpio · 1 NCC · 2 ORB rígido · 2b histograma HSV (sin modelos) · 3 Grounding DINO +
//! MobileSAM · 4 LLM desempata · 5 registro global. El objeto es la fuente de verdad: máscara, afín 2×3 y apariencia (PNG).
//!
//! Uso desde el core:
//! ```no_run
//! use pcx_objects::{geom::{Canvas, Homography, Rectifier}, CleanFrame, Params, Tracker};
//! let canvas = Canvas::covering(0.0, 0.0, 1200.0, 675.0, 1280);            // mesa en mm → lienzo 1280 px
//! let rect = Rectifier::new(Homography::IDENTITY, canvas).unwrap();         // homografía cámara → table_mm
//! let mut tracker = Tracker::new(canvas, Params::default());
//! # let foto_camara = image::RgbImage::new(1280, 720);
//! let (rgb, valid) = rect.rectify(&foto_camara);                           // foto limpia (proyector apagado)
//! let tracker_area = tracker.area.clone();
//! let report = tracker.update(&CleanFrame { rgb, t_ns: 0 });              // → report.events (ObjectEvent)
//! # let _ = (valid, report, tracker_area);
//! ```

pub mod contract;
pub mod geom;
pub mod llm;
pub mod models;
pub mod ncc;
#[cfg(feature = "onnx")]
pub mod onnx;
pub mod orb;
pub mod raster;
pub mod tracker;

pub use contract::{ObjectEvent, ObjectState};
pub use llm::{ClaudeClient, MockLlm, VisionLlm};
pub use models::{Detection, Detector, Segmenter};
pub use tracker::{CleanFrame, HistMetric, Params, Report, TrackError, TrackedObject, Tracker};
