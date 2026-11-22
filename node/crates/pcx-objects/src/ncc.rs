//! Nivel 1: correlación normalizada con máscara. Con plantilla e imagen del mismo tamaño,
//! `cv2.matchTemplate(b, a, TM_CCOEFF_NORMED, mask=m)` da un único valor: el ZNCC sobre los píxeles de `m`.

use crate::raster::Mask;
use image::GrayImage;

pub fn masked_zncc(a: &GrayImage, b: &GrayImage, m: &Mask) -> f64 {
    let (mut n, mut sa, mut sb) = (0f64, 0f64, 0f64);
    for ((&x, &y), &k) in a.as_raw().iter().zip(b.as_raw()).zip(m.as_raw()) {
        if k > 0 {
            n += 1.0;
            sa += x as f64;
            sb += y as f64;
        }
    }
    if n < 2.0 {
        return 0.0;
    }
    let (ma, mb) = (sa / n, sb / n);
    let (mut num, mut da, mut db) = (0f64, 0f64, 0f64);
    for ((&x, &y), &k) in a.as_raw().iter().zip(b.as_raw()).zip(m.as_raw()) {
        if k > 0 {
            let (p, q) = (x as f64 - ma, y as f64 - mb);
            num += p * q;
            da += p * p;
            db += q * q;
        }
    }
    let den = (da * db).sqrt();
    if den < 1e-9 {
        0.0 // nan_to_num del original
    } else {
        num / den
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn zncc_identity_and_inverse() {
        let a = GrayImage::from_fn(20, 20, |x, y| image::Luma([((x * 13 + y * 7) % 255) as u8]));
        let inv = GrayImage::from_fn(20, 20, |x, y| image::Luma([255 - a.get_pixel(x, y).0[0]]));
        let m = crate::raster::full(20, 20);
        assert!((masked_zncc(&a, &a, &m) - 1.0).abs() < 1e-9);
        assert!((masked_zncc(&a, &inv, &m) + 1.0).abs() < 1e-9);
    }
}
