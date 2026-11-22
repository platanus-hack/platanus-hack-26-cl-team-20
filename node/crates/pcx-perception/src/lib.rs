//! Percepción de pcx: [`clean`] es la «foto limpia» (cuadros de cámara con el proyector apagado) y [`pipeline`] el
//! pipeline de objetos del motor local (cascada + MobileSAM), detrás del trait de [`pipeline_api`].

pub mod clean;
pub mod pipeline;
pub mod pipeline_api;
pub mod scene_api;

pub use clean::{
    capture_clean, median_stack, BlankControl, BlankWindow, CleanConfig, CleanError, CleanMetrics, CleanPhoto,
    FrameSource, Mask, Selection,
};
