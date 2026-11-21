//! Frontera con el hardware: el core implementa estos dos traits (renderer y captura); el simulador también.

use std::sync::Arc;

use crate::image::GrayImage;

#[derive(Debug, Clone, PartialEq)]
pub enum CalibError {
    /// El sink o la cámara fallaron (texto del adaptador).
    Io(String),
    /// La cámara no vio el cambio esperado dentro del plazo.
    SyncTimeout(String),
    /// No hay datos suficientes (marcadores, píxeles decodificados, etc.).
    NotEnough(String),
    /// Falta una calibración previa (modo `verify`, o `precise` sin homografía ni grilla legible).
    MissingPrior,
}

impl std::fmt::Display for CalibError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            CalibError::Io(s) => write!(f, "io: {s}"),
            CalibError::SyncTimeout(s) => write!(f, "sincronía: {s}"),
            CalibError::NotEnough(s) => write!(f, "datos insuficientes: {s}"),
            CalibError::MissingPrior => write!(f, "falta una calibración previa"),
        }
    }
}

impl std::error::Error for CalibError {}

/// Patrón a pantalla completa en px nativos del proyector (luma 8 bits; el renderer lo replica en RGB).
#[derive(Debug, Clone)]
pub struct Pattern {
    pub label: String,
    pub image: Arc<GrayImage>,
}

impl Pattern {
    pub fn new(label: impl Into<String>, image: GrayImage) -> Self {
        Self { label: label.into(), image: Arc::new(image) }
    }
}

/// Salida del proyector. Tiempos en ns de época, mismo reloj que `Frame::t_capture_ns`.
pub trait PatternSink {
    /// Tamaño nativo del proyector (px).
    fn size(&self) -> (u32, u32);
    /// Muestra `p` a pantalla completa (encima de la escena) hasta el próximo `show`/`release`; devuelve el
    /// instante de presentación (o de envío si el renderer no lo informa).
    fn show(&mut self, p: &Pattern) -> Result<i64, CalibError>;
    /// Devuelve la salida a la escena (equivale a terminar el modo patrón).
    fn release(&mut self) -> Result<(), CalibError>;
}

/// Cuadro de cámara en luma 8 bits.
#[derive(Debug, Clone)]
pub struct Frame {
    pub t_capture_ns: i64,
    pub image: GrayImage,
}

/// Entrada de la cámara.
pub trait FrameGrab {
    /// Primer cuadro **nuevo** (nunca repite uno ya entregado) con `t_capture_ns ≥ not_before_ns`; bloquea.
    fn grab(&mut self, not_before_ns: i64) -> Result<Frame, CalibError>;
}
