//! `pcx-calib`: calibración cámara ↔ proyector ↔ mesa del nodo pcx.
//!
//! - **quick**: grilla de marcadores `pcx6` (par patrón/inverso, binaria por signo) → homografía cámara→proyector
//!   (DLT normalizado + RANSAC) + marco `table_mm` (marcador impreso de tamaño conocido o escala de config).
//! - **precise**: gray code solo con los bits útiles, sincronía por parches (streaming, método de Daniel) o por cambio,
//!   umbral relativo, refinado sub-bloque y LUT densa proyector→cámara por bloques (sin `griddata`).
//! - **verify**: 12 cuadrados de 36 mm; residuo en mm y `CalibrationDrift` si pasa el umbral.
//!
//! La orquestación (`CalibRun`) es una máquina de pasos que solo habla con `PatternSink` y `FrameGrab`.
//! `sim` trae una cámara virtual (homografía real de un pinhole + distorsión + ruido + ambiente + caja alta).

pub mod calib;
pub mod graycode;
pub mod homography;
pub mod image;
pub mod io;
pub mod lut;
pub mod markers;
pub mod sim;
pub mod sync;

pub use calib::{
    CalibConfig, CalibOutcome, CalibReport, CalibRun, Calibration, CalibrationDrift, MetricRef, Mode, PrintedMarker, Step,
    SyncMode,
};
pub use image::{ErrStats, GrayImage};
pub use io::{CalibError, Frame, FrameGrab, Pattern, PatternSink};
