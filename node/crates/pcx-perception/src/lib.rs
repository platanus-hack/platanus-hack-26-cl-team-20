//! Percepción de pcx. Por ahora solo [`clean`]: la «foto limpia» (cuadros de cámara con el proyector apagado).

pub mod clean;

pub use clean::{
    capture_clean, median_stack, BlankControl, BlankWindow, CleanConfig, CleanError, CleanMetrics, CleanPhoto,
    FrameSource, Mask, Selection,
};
