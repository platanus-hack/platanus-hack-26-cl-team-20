//! Cuadro decodificado (RGB8) y el buzón «último gana» entre una fuente y la bomba.

use image::RgbImage;
use std::borrow::Cow;
use std::sync::Arc;
use tokio::sync::watch;

pub type BoxError = Box<dyn std::error::Error + Send + Sync>;

#[derive(Clone, Debug)]
pub struct Frame {
    pub img: RgbImage,
    /// ns de época de captura (o de llegada al nodo, si la fuente no trae reloj confiable).
    pub t_capture_ns: u64,
}

/// Lado productor del buzón: `put` pisa el cuadro anterior si nadie lo leyó.
#[derive(Clone)]
pub struct LatestTx(watch::Sender<Option<Arc<Frame>>>);

pub type LatestRx = watch::Receiver<Option<Arc<Frame>>>;

pub fn latest_box() -> (LatestTx, LatestRx) {
    let (tx, rx) = watch::channel(None);
    (LatestTx(tx), rx)
}

impl LatestTx {
    pub fn put(&self, frame: Frame) {
        self.0.send_replace(Some(Arc::new(frame)));
    }
    /// True cuando la bomba soltó el receptor: la fuente debe terminar.
    pub fn is_closed(&self) -> bool {
        self.0.is_closed()
    }
    pub async fn closed(&self) {
        self.0.closed().await
    }
}

pub fn decode(bytes: &[u8]) -> Result<RgbImage, BoxError> {
    Ok(image::load_from_memory(bytes)?.into_rgb8())
}

/// Redimensiona a `size` (w, h) si no calza ya (como `fit` de Python: no conserva aspecto).
pub fn fit(img: &RgbImage, size: Option<(u32, u32)>) -> Cow<'_, RgbImage> {
    match size {
        Some((w, h)) if img.dimensions() != (w, h) => {
            Cow::Owned(image::imageops::resize(img, w, h, image::imageops::FilterType::Triangle))
        }
        _ => Cow::Borrowed(img),
    }
}

/// RGB → BGR mientras se copia al slot (sin búfer intermedio).
pub fn rgb_to_bgr(src: &[u8], dst: &mut [u8]) {
    for (s, d) in src.as_chunks::<3>().0.iter().zip(dst.as_chunks_mut::<3>().0) {
        *d = [s[2], s[1], s[0]];
    }
}

/// Partes JPEG (SOI FFD8 … EOI FFD9) de un búfer: archivo `.mjpeg` o cuerpo sin `Content-Length`.
pub fn split_jpegs(buf: &[u8]) -> (Vec<&[u8]>, usize) {
    let mut out = Vec::new();
    let mut pos = 0;
    while let Some(start) = find(&buf[pos..], &[0xFF, 0xD8]).map(|i| pos + i) {
        match find(&buf[start + 2..], &[0xFF, 0xD9]) {
            Some(e) => {
                let end = start + 2 + e + 2;
                out.push(&buf[start..end]);
                pos = end;
            }
            None => return (out, start),
        }
    }
    (out, buf.len().saturating_sub(1).max(pos))
}

pub fn find(hay: &[u8], needle: &[u8]) -> Option<usize> {
    hay.windows(needle.len()).position(|w| w == needle)
}
