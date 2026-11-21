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

/// NV12 de rango video (420v: Y 16–235, CbCr 16–240) → RGB8 empaquetado, en punto fijo. `bt709` para HD.
#[allow(clippy::too_many_arguments)]
pub fn nv12_to_rgb(y: &[u8], y_stride: usize, uv: &[u8], uv_stride: usize, w: usize, h: usize, bt709: bool, out: &mut [u8]) {
    // coeficientes ×1024: R = 1.164(Y−16) + a·V; G = … − b·U − c·V; B = … + d·U
    let (a, b, c, d) = if bt709 { (1836, 218, 546, 2163) } else { (1634, 401, 833, 2066) };
    let clamp = |v: i32| ((v + 512) >> 10).clamp(0, 255) as u8;
    for row in 0..h {
        let yr = &y[row * y_stride..row * y_stride + w];
        let uvr = &uv[(row / 2) * uv_stride..];
        let o = &mut out[row * w * 3..(row + 1) * w * 3];
        for x in 0..w {
            let yy = 1192 * (yr[x] as i32 - 16);
            let u = uvr[x & !1] as i32 - 128;
            let v = uvr[(x & !1) + 1] as i32 - 128;
            o[x * 3] = clamp(yy + a * v);
            o[x * 3 + 1] = clamp(yy - b * u - c * v);
            o[x * 3 + 2] = clamp(yy + d * u);
        }
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

#[cfg(test)]
mod tests {
    #[test]
    fn nv12_grises_y_colores() {
        // 2×2: Y=16 (negro) y 235 (blanco) con croma neutra; luego rojo puro BT.601 (Y=81, U=90, V=240)
        let mut out = [0u8; 12];
        super::nv12_to_rgb(&[16, 235, 16, 235], 2, &[128, 128], 2, 2, 2, false, &mut out);
        assert_eq!(&out[..6], &[0, 0, 0, 255, 255, 255]);
        super::nv12_to_rgb(&[81, 81, 81, 81], 2, &[90, 240], 2, 2, 2, false, &mut out);
        let (r, g, b) = (out[0] as i32, out[1] as i32, out[2] as i32);
        assert!(r > 245 && g < 10 && b < 10, "{r} {g} {b}");
    }

    /// Stride > ancho (caso real de CoreVideo, filas alineadas), ancho impar y valores exactos por canal.
    #[test]
    fn nv12_stride_ancho_impar_y_bt709() {
        // 3×2, stride Y 8 y UV 8 (bytes de relleno = 99 que no deben leerse)
        let y = [16, 126, 235, 99, 99, 99, 99, 99, 16, 126, 235, 99, 99, 99, 99, 99];
        let uv = [128, 128, 128, 128, 99, 99, 99, 99];
        let mut out = [0u8; 18];
        super::nv12_to_rgb(&y, 8, &uv, 8, 3, 2, false, &mut out);
        assert_eq!(&out[..9], &[0, 0, 0, 128, 128, 128, 255, 255, 255]);
        assert_eq!(&out[9..], &out[..9]);
        // BT.709 vs BT.601 con croma: Y=126, U=100, V=180 → valores de referencia en flotante redondeados
        let (y, uv) = ([126u8, 126, 126, 126], [100u8, 180]);
        let ref_px = |kr: f64, kb: f64| {
            let (yy, u, v) = (1.164 * (126.0 - 16.0), 100.0 - 128.0, 180.0 - 128.0);
            let kg = 1.0 - kr - kb;
            let r = yy + 2.0 * (1.0 - kr) * 255.0 / 224.0 * v;
            let b = yy + 2.0 * (1.0 - kb) * 255.0 / 224.0 * u;
            let g = (yy - kr * r - kb * b) / kg;
            [r, g, b].map(|c| c.round().clamp(0.0, 255.0) as i32)
        };
        for (bt709, kr, kb) in [(false, 0.299, 0.114), (true, 0.2126, 0.0722)] {
            super::nv12_to_rgb(&y, 2, &uv, 2, 2, 2, bt709, &mut out[..12]);
            let got = [out[0] as i32, out[1] as i32, out[2] as i32];
            let want = ref_px(kr, kb);
            assert!(got.iter().zip(want).all(|(a, b)| (a - b).abs() <= 1), "bt709={bt709} {got:?} vs {want:?}");
        }
    }
}
