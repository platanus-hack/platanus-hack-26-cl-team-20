//! Geometría: homografía cámara→mesa, lienzo rectificado (px ↔ mm), afines 2×3 y RANSAC de similitud
//! (equivalente a `cv2.estimateAffinePartial2D(..., RANSAC, ransacReprojThreshold)`).

use image::{Rgb, RgbImage};

/// Afín 2×3 en fila mayor: `[[a, b, tx], [c, d, ty]]`.
pub type Affine = [[f64; 3]; 2];

pub const IDENTITY: Affine = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]];

pub fn apply(a: &Affine, p: (f64, f64)) -> (f64, f64) {
    (a[0][0] * p.0 + a[0][1] * p.1 + a[0][2], a[1][0] * p.0 + a[1][1] * p.1 + a[1][2])
}

/// `a ∘ b` (primero `b`, después `a`).
pub fn compose(a: &Affine, b: &Affine) -> Affine {
    let mut r = [[0.0; 3]; 2];
    for i in 0..2 {
        for j in 0..3 {
            r[i][j] = a[i][0] * b[0][j] + a[i][1] * b[1][j] + if j == 2 { a[i][2] } else { 0.0 };
        }
    }
    r
}

pub fn invert(a: &Affine) -> Option<Affine> {
    let det = a[0][0] * a[1][1] - a[0][1] * a[1][0];
    if det.abs() < 1e-12 {
        return None;
    }
    let (ia, ib, ic, id) = (a[1][1] / det, -a[0][1] / det, -a[1][0] / det, a[0][0] / det);
    Some([
        [ia, ib, -(ia * a[0][2] + ib * a[1][2])],
        [ic, id, -(ic * a[0][2] + id * a[1][2])],
    ])
}

/// Escala de la parte lineal (`hypot(A[0,0], A[1,0])`, como el filtro de `seguidor_v4`).
pub fn scale_of(a: &Affine) -> f64 {
    a[0][0].hypot(a[1][0])
}

/// Rotación en grados (convención de imagen, como `pose_mesa.angulo`).
pub fn angle_deg(a: &Affine) -> f64 {
    a[1][0].atan2(a[0][0]).to_degrees()
}

/// Homografía 3×3 en fila mayor (mismo orden que `Transform.homography.matrix` del contrato).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Homography(pub [f64; 9]);

impl Homography {
    pub const IDENTITY: Homography = Homography([1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]);

    pub fn apply(&self, x: f64, y: f64) -> Option<(f64, f64)> {
        let m = &self.0;
        let w = m[6] * x + m[7] * y + m[8];
        if w.abs() < 1e-12 {
            return None;
        }
        Some(((m[0] * x + m[1] * y + m[2]) / w, (m[3] * x + m[4] * y + m[5]) / w))
    }

    pub fn inverse(&self) -> Option<Homography> {
        let m = &self.0;
        let c00 = m[4] * m[8] - m[5] * m[7];
        let c01 = m[5] * m[6] - m[3] * m[8];
        let c02 = m[3] * m[7] - m[4] * m[6];
        let det = m[0] * c00 + m[1] * c01 + m[2] * c02;
        if det.abs() < 1e-15 {
            return None;
        }
        let inv = [
            c00,
            m[2] * m[7] - m[1] * m[8],
            m[1] * m[5] - m[2] * m[4],
            c01,
            m[0] * m[8] - m[2] * m[6],
            m[2] * m[3] - m[0] * m[5],
            c02,
            m[1] * m[6] - m[0] * m[7],
            m[0] * m[4] - m[1] * m[3],
        ];
        Some(Homography(inv.map(|v| v / det)))
    }
}

/// Lienzo de trabajo: la mesa rectificada (equivale al «lienzo» de `seguidor_v4`, 1280×720).
/// Pixel `(u, v)` ↔ mm `(origin + (u + 0.5) * mm_per_px, ...)`. Todo el seguidor trabaja aquí, así una afín
/// del lienzo pasa a mm por conjugación exacta.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Canvas {
    pub width: u32,
    pub height: u32,
    pub mm_per_px: f64,
    pub origin_mm: (f64, f64),
}

impl Canvas {
    /// Lienzo que cubre `[x0, x0+w_mm] × [y0, y0+h_mm]` con `width` px de ancho.
    pub fn covering(x0: f64, y0: f64, w_mm: f64, h_mm: f64, width: u32) -> Canvas {
        let mm_per_px = w_mm / width as f64;
        Canvas { width, height: (h_mm / mm_per_px).round() as u32, mm_per_px, origin_mm: (x0, y0) }
    }

    pub fn px_to_mm(&self, u: f64, v: f64) -> (f64, f64) {
        (self.origin_mm.0 + u * self.mm_per_px, self.origin_mm.1 + v * self.mm_per_px)
    }

    pub fn mm_to_px(&self, x: f64, y: f64) -> (f64, f64) {
        ((x - self.origin_mm.0) / self.mm_per_px, (y - self.origin_mm.1) / self.mm_per_px)
    }

    /// Afín de px del lienzo a mm.
    pub fn px_to_mm_affine(&self) -> Affine {
        let s = self.mm_per_px;
        [[s, 0.0, self.origin_mm.0], [0.0, s, self.origin_mm.1]]
    }

    /// Lleva una afín expresada en px del lienzo al marco `table_mm`: `S ∘ A ∘ S⁻¹`.
    pub fn affine_px_to_mm(&self, a: &Affine) -> Affine {
        let s = self.px_to_mm_affine();
        compose(&s, &compose(a, &invert(&s).expect("escala no nula")))
    }
}

/// Rectifica fotos de cámara al lienzo de la mesa con la homografía cámara→`table_mm`.
#[derive(Debug, Clone)]
pub struct Rectifier {
    pub canvas: Canvas,
    table_to_cam: Homography,
}

impl Rectifier {
    pub fn new(cam_to_table_mm: Homography, canvas: Canvas) -> Option<Rectifier> {
        Some(Rectifier { canvas, table_to_cam: cam_to_table_mm.inverse()? })
    }

    /// Remuestreo bilineal (equivale a `cv2.remap(..., INTER_LINEAR)`). Devuelve la imagen y la máscara de
    /// píxeles válidos (los que caen dentro de la foto).
    pub fn rectify(&self, cam: &RgbImage) -> (RgbImage, image::GrayImage) {
        let c = &self.canvas;
        let mut out = RgbImage::new(c.width, c.height);
        let mut valid = image::GrayImage::new(c.width, c.height);
        let (cw, ch) = (cam.width() as f64, cam.height() as f64);
        for v in 0..c.height {
            for u in 0..c.width {
                let (x, y) = c.px_to_mm(u as f64 + 0.5, v as f64 + 0.5);
                let Some((cx, cy)) = self.table_to_cam.apply(x, y) else { continue };
                let (fx, fy) = (cx - 0.5, cy - 0.5);
                if fx < 0.0 || fy < 0.0 || fx > cw - 1.0 || fy > ch - 1.0 {
                    continue;
                }
                out.put_pixel(u, v, bilinear(cam, fx, fy));
                valid.put_pixel(u, v, image::Luma([255]));
            }
        }
        (out, valid)
    }
}

fn bilinear(img: &RgbImage, x: f64, y: f64) -> Rgb<u8> {
    let (x0, y0) = (x.floor() as u32, y.floor() as u32);
    let (x1, y1) = ((x0 + 1).min(img.width() - 1), (y0 + 1).min(img.height() - 1));
    let (dx, dy) = (x - x0 as f64, y - y0 as f64);
    let p = |xx, yy| img.get_pixel(xx, yy).0;
    let (a, b, c, d) = (p(x0, y0), p(x1, y0), p(x0, y1), p(x1, y1));
    let mut o = [0u8; 3];
    for k in 0..3 {
        let top = a[k] as f64 * (1.0 - dx) + b[k] as f64 * dx;
        let bot = c[k] as f64 * (1.0 - dx) + d[k] as f64 * dx;
        o[k] = (top * (1.0 - dy) + bot * dy).round().clamp(0.0, 255.0) as u8;
    }
    Rgb(o)
}

/// Similitud (rotación + escala uniforme + traslación) por mínimos cuadrados (Umeyama 2D).
pub fn fit_similarity(src: &[(f64, f64)], dst: &[(f64, f64)]) -> Option<Affine> {
    let n = src.len();
    if n < 2 || n != dst.len() {
        return None;
    }
    let nf = n as f64;
    let (mut sx, mut sy, mut dx, mut dy) = (0.0, 0.0, 0.0, 0.0);
    for i in 0..n {
        sx += src[i].0;
        sy += src[i].1;
        dx += dst[i].0;
        dy += dst[i].1;
    }
    let (sx, sy, dx, dy) = (sx / nf, sy / nf, dx / nf, dy / nf);
    let (mut a, mut b, mut var) = (0.0, 0.0, 0.0);
    for i in 0..n {
        let (px, py) = (src[i].0 - sx, src[i].1 - sy);
        let (qx, qy) = (dst[i].0 - dx, dst[i].1 - dy);
        a += px * qx + py * qy;
        b += px * qy - py * qx;
        var += px * px + py * py;
    }
    if var < 1e-12 {
        return None;
    }
    let (ca, sb) = (a / var, b / var);
    Some([[ca, -sb, dx - (ca * sx - sb * sy)], [sb, ca, dy - (sb * sx + ca * sy)]])
}

/// RANSAC de similitud con muestras de 2 puntos y refinamiento final por mínimos cuadrados sobre los inliers.
/// Determinista (semilla fija). Devuelve la afín y la máscara de inliers.
pub fn ransac_similarity(
    src: &[(f64, f64)],
    dst: &[(f64, f64)],
    thresh: f64,
    iters: usize,
) -> Option<(Affine, Vec<bool>)> {
    let n = src.len();
    if n < 2 {
        return None;
    }
    let mut rng = Lcg(0x9E37_79B9_7F4A_7C15);
    let t2 = thresh * thresh;
    let inliers_of = |a: &Affine| -> Vec<bool> {
        (0..n)
            .map(|i| {
                let (x, y) = apply(a, src[i]);
                (x - dst[i].0).powi(2) + (y - dst[i].1).powi(2) <= t2
            })
            .collect()
    };
    let mut best: Option<(usize, Affine)> = None;
    for _ in 0..iters {
        let i = rng.below(n);
        let mut j = rng.below(n);
        if i == j {
            j = (j + 1) % n;
        }
        let Some(a) = fit_similarity(&[src[i], src[j]], &[dst[i], dst[j]]) else { continue };
        let cnt = inliers_of(&a).iter().filter(|&&b| b).count();
        if best.as_ref().is_none_or(|(c, _)| cnt > *c) {
            best = Some((cnt, a));
        }
    }
    let (_, a) = best?;
    let inl = inliers_of(&a);
    let (s2, d2): (Vec<_>, Vec<_>) = (0..n).filter(|&i| inl[i]).map(|i| (src[i], dst[i])).unzip();
    let refined = fit_similarity(&s2, &d2).unwrap_or(a);
    let inl2 = inliers_of(&refined);
    if inl2.iter().filter(|&&b| b).count() >= inl.iter().filter(|&&b| b).count() {
        Some((refined, inl2))
    } else {
        Some((a, inl))
    }
}

/// Generador congruencial determinista (para RANSAC y el patrón BRIEF; sin dependencia de `rand`).
pub(crate) struct Lcg(pub u64);

impl Lcg {
    pub fn next_u64(&mut self) -> u64 {
        self.0 = self.0.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
        self.0 ^ (self.0 >> 29)
    }
    pub fn below(&mut self, n: usize) -> usize {
        (self.next_u64() >> 11) as usize % n
    }
    pub fn unit(&mut self) -> f64 {
        (self.next_u64() >> 11) as f64 / (1u64 << 53) as f64
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn similarity_and_ransac() {
        let a: Affine = {
            let (c, s) = (0.3f64.cos() * 1.05, 0.3f64.sin() * 1.05);
            [[c, -s, 12.0], [s, c, -7.0]]
        };
        let src: Vec<_> = (0..40).map(|i| ((i * 7 % 50) as f64, (i * 13 % 37) as f64)).collect();
        let mut dst: Vec<_> = src.iter().map(|&p| apply(&a, p)).collect();
        for d in dst.iter_mut().take(10) {
            d.0 += 50.0; // outliers
        }
        let (r, inl) = ransac_similarity(&src, &dst, 4.0, 500).unwrap();
        assert_eq!(inl.iter().filter(|&&b| b).count(), 30);
        for i in 0..2 {
            for j in 0..3 {
                assert!((r[i][j] - a[i][j]).abs() < 1e-6);
            }
        }
        assert!((angle_deg(&r) - 0.3f64.to_degrees()).abs() < 1e-6);
    }

    #[test]
    fn homography_inverse_and_canvas() {
        let h = Homography([2.0, 0.1, 5.0, 0.0, 1.5, -3.0, 0.0001, 0.0, 1.0]);
        let (x, y) = h.apply(10.0, 20.0).unwrap();
        let (u, v) = h.inverse().unwrap().apply(x, y).unwrap();
        assert!((u - 10.0).abs() < 1e-9 && (v - 20.0).abs() < 1e-9);
        let c = Canvas::covering(0.0, 0.0, 1280.0, 720.0, 640);
        let a: Affine = [[1.0, 0.0, 10.0], [0.0, 1.0, 0.0]];
        let m = c.affine_px_to_mm(&a);
        assert!((m[0][2] - 20.0).abs() < 1e-9);
    }
}
