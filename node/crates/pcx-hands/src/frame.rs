//! Entrada: un cuadro como `&[u8]` (RGB o BGR, 3 bytes por píxel) más su `FrameMeta`.
//!
//! El core conecta aquí el lector de memoria compartida implementando [`FrameSource`]; este crate no depende de él.
use crate::types::FrameMeta;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PixelFormat {
    Rgb8,
    Bgr8,
}

impl PixelFormat {
    /// Posición del rojo y del azul dentro del píxel.
    #[inline]
    pub(crate) fn rgb_offsets(self) -> (usize, usize) {
        match self {
            PixelFormat::Rgb8 => (0, 2),
            PixelFormat::Bgr8 => (2, 0),
        }
    }
}

/// Vista de un cuadro empaquetado (sin copiar).
#[derive(Debug, Clone, Copy)]
pub struct Frame<'a> {
    pub data: &'a [u8],
    pub width: usize,
    pub height: usize,
    /// Bytes por fila (≥ `width * 3`).
    pub stride: usize,
    pub format: PixelFormat,
}

impl<'a> Frame<'a> {
    /// Cuadro contiguo (`stride = width * 3`). Falla si el buffer es más chico.
    pub fn packed(
        data: &'a [u8],
        width: usize,
        height: usize,
        format: PixelFormat,
    ) -> Result<Self, crate::HandsError> {
        Self::with_stride(data, width, height, width * 3, format)
    }

    pub fn with_stride(
        data: &'a [u8],
        width: usize,
        height: usize,
        stride: usize,
        format: PixelFormat,
    ) -> Result<Self, crate::HandsError> {
        if width == 0
            || height == 0
            || stride < width * 3
            || data.len() < stride * (height - 1) + width * 3
        {
            return Err(crate::HandsError::BadFrame(format!(
                "{width}x{height} stride {stride} con {} bytes",
                data.len()
            )));
        }
        Ok(Self {
            data,
            width,
            height,
            stride,
            format,
        })
    }
}

/// Fuente de cuadros (p. ej. el lector de memoria compartida del core). Devuelve `None` cuando se acaba.
pub trait FrameSource {
    fn next_frame(&mut self) -> Option<(Frame<'_>, FrameMeta)>;
}
