//! ORB propio en Rust puro (nivel 2): FAST-9 (`imageproc`) + orientación por centroide de intensidad +
//! BRIEF rotado de 256 bits sobre una pirámide. Mismos parámetros que `cv2.ORB_create(800, scaleFactor=1.2,
//! nlevels=6, edgeThreshold=11, patchSize=21)` y el mismo emparejado (kNN Hamming + razón 0.8).
//!
//! Por qué propio y no `akaze`/`cv` de rust-cv: rust-cv está sin mantenimiento (nalgebra/ndarray viejos) y AKAZE
//! cuesta ~10× más en CPU; el template matching de `imageproc` no tolera rotación sin barrer ángulos.
//! ORB son ~250 líneas, deterministas y sin dependencias de sistema (macOS/Windows/Linux iguales).

use crate::geom::Lcg;
use crate::raster::Mask;
use image::{imageops, GrayImage};

#[derive(Debug, Clone, Copy)]
pub struct OrbParams {
    pub n_features: usize,
    pub scale_factor: f32,
    pub n_levels: usize,
    pub edge_threshold: u32,
    pub patch_size: u32,
    pub fast_threshold: u8,
}

impl Default for OrbParams {
    fn default() -> Self {
        OrbParams {
            n_features: 800,
            scale_factor: 1.2,
            n_levels: 6,
            edge_threshold: 11,
            patch_size: 21,
            fast_threshold: 20,
        }
    }
}

#[derive(Debug, Clone, Copy)]
pub struct Keypoint {
    /// Posición en px de la imagen original (nivel 0).
    pub x: f32,
    pub y: f32,
    pub angle: f32,
    pub level: u8,
    pub score: f32,
}

pub type Descriptor = [u64; 4];

/// Patrón BRIEF fijo: 256 pares en el disco de radio `patch/2 - 1`, gaussiano σ = patch/5 (como el paper).
fn pattern(patch: u32) -> Vec<[(f32, f32); 2]> {
    let r = (patch / 2) as f32 - 1.0;
    let sigma = patch as f64 / 5.0;
    let mut rng = Lcg(0x0DB5_EED5);
    let mut pt = || loop {
        // Box–Muller
        let (u1, u2) = (rng.unit().max(1e-12), rng.unit());
        let m = (-2.0 * u1.ln()).sqrt() * sigma;
        let (x, y) = ((m * (std::f64::consts::TAU * u2).cos()) as f32, (m * (std::f64::consts::TAU * u2).sin()) as f32);
        if x * x + y * y <= r * r {
            return (x, y);
        }
    };
    (0..256).map(|_| [pt(), pt()]).collect()
}

/// Detecta y describe. `mask` (opcional) en coordenadas de `img`.
pub fn detect_and_compute(img: &GrayImage, mask: Option<&Mask>, p: &OrbParams) -> (Vec<Keypoint>, Vec<Descriptor>) {
    let pat = pattern(p.patch_size);
    let half = (p.patch_size / 2) as i32;
    let border = p.edge_threshold.max(p.patch_size / 2 + 1);
    let f = 1.0 / p.scale_factor;
    // reparto de features por nivel como OpenCV
    let mut per_level = Vec::with_capacity(p.n_levels);
    let first = p.n_features as f32 * (1.0 - f) / (1.0 - f.powi(p.n_levels as i32));
    let mut acc = 0usize;
    for l in 0..p.n_levels {
        let n = if l + 1 == p.n_levels {
            p.n_features.saturating_sub(acc)
        } else {
            (first * f.powi(l as i32)).round() as usize
        };
        acc += n;
        per_level.push(n);
    }
    let (mut kps, mut descs) = (Vec::new(), Vec::new());
    for (l, &n_l) in per_level.iter().enumerate() {
        let s = p.scale_factor.powi(l as i32);
        let (w, h) = ((img.width() as f32 / s).round() as u32, (img.height() as f32 / s).round() as u32);
        if w <= 2 * border || h <= 2 * border {
            break;
        }
        let lvl = if l == 0 { img.clone() } else { imageops::resize(img, w, h, imageops::FilterType::Triangle) };
        let lmask =
            mask.map(|m| if l == 0 { m.clone() } else { imageops::resize(m, w, h, imageops::FilterType::Nearest) });
        let mut corners: Vec<_> = imageproc::corners::corners_fast9(&lvl, p.fast_threshold)
            .into_iter()
            .filter(|c| c.x >= border && c.y >= border && c.x < w - border && c.y < h - border)
            .filter(|c| lmask.as_ref().is_none_or(|m| m.get_pixel(c.x, c.y).0[0] > 0))
            .collect();
        corners.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(std::cmp::Ordering::Equal));
        // supresión de no-máximos simple: radio 3 px
        let mut taken: Vec<(u32, u32)> = Vec::new();
        let smooth = imageproc::filter::gaussian_blur_f32(&lvl, 2.0);
        for c in corners {
            if taken.len() >= n_l {
                break;
            }
            if taken.iter().any(|&(x, y)| x.abs_diff(c.x) <= 3 && y.abs_diff(c.y) <= 3) {
                continue;
            }
            taken.push((c.x, c.y));
            let angle = ic_angle(&lvl, c.x as i32, c.y as i32, half);
            let d = describe(&smooth, c.x as i32, c.y as i32, angle, &pat);
            kps.push(Keypoint { x: c.x as f32 * s, y: c.y as f32 * s, angle, level: l as u8, score: c.score });
            descs.push(d);
        }
    }
    (kps, descs)
}

fn ic_angle(img: &GrayImage, cx: i32, cy: i32, r: i32) -> f32 {
    let (mut m01, mut m10) = (0f64, 0f64);
    for dy in -r..=r {
        let w = ((r * r - dy * dy) as f64).sqrt() as i32;
        for dx in -w..=w {
            let v = img.get_pixel((cx + dx) as u32, (cy + dy) as u32).0[0] as f64;
            m10 += dx as f64 * v;
            m01 += dy as f64 * v;
        }
    }
    m01.atan2(m10) as f32
}

fn describe(img: &GrayImage, cx: i32, cy: i32, angle: f32, pat: &[[(f32, f32); 2]]) -> Descriptor {
    let (c, s) = (angle.cos(), angle.sin());
    let at = |(x, y): (f32, f32)| -> u8 {
        let rx = (x * c - y * s).round() as i32;
        let ry = (x * s + y * c).round() as i32;
        let (px, py) = ((cx + rx).clamp(0, img.width() as i32 - 1), (cy + ry).clamp(0, img.height() as i32 - 1));
        img.get_pixel(px as u32, py as u32).0[0]
    };
    let mut d = [0u64; 4];
    for (i, pair) in pat.iter().enumerate() {
        if at(pair[0]) < at(pair[1]) {
            d[i / 64] |= 1 << (i % 64);
        }
    }
    d
}

pub fn hamming(a: &Descriptor, b: &Descriptor) -> u32 {
    (0..4).map(|i| (a[i] ^ b[i]).count_ones()).sum()
}

/// `BFMatcher(NORM_HAMMING).knnMatch(k=2)` + test de razón. Devuelve pares `(i_query, i_train)`.
pub fn match_ratio(q: &[Descriptor], t: &[Descriptor], ratio: f32) -> Vec<(usize, usize)> {
    let mut out = Vec::new();
    if t.len() < 2 {
        return out;
    }
    for (i, dq) in q.iter().enumerate() {
        let (mut b1, mut b2, mut j1) = (u32::MAX, u32::MAX, 0);
        for (j, dt) in t.iter().enumerate() {
            let d = hamming(dq, dt);
            if d < b1 {
                b2 = b1;
                b1 = d;
                j1 = j;
            } else if d < b2 {
                b2 = d;
            }
        }
        if (b1 as f32) < ratio * b2 as f32 {
            out.push((i, j1));
        }
    }
    out
}
