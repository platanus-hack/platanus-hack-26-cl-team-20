//! Operaciones de imagen y máscara equivalentes a las de OpenCV que usa `seguidor_v4.py`
//! (gris, blur 5×5, absdiff, morfología, relleno, contorno, histograma HSV, warpAffine), en Rust puro.

use crate::geom::{self, Affine};
use image::{GrayImage, Luma, Rgb, RgbImage, Rgba, RgbaImage};
use imageproc::distance_transform::Norm;
use std::collections::VecDeque;

/// Máscara binaria: 0 = fuera, 255 = dentro (así sirve directo para `imageproc::morphology`).
pub type Mask = GrayImage;

pub const ON: Luma<u8> = Luma([255]);

pub fn empty(w: u32, h: u32) -> Mask {
    GrayImage::new(w, h)
}

pub fn full(w: u32, h: u32) -> Mask {
    GrayImage::from_pixel(w, h, ON)
}

pub fn count(m: &Mask) -> u64 {
    m.as_raw().iter().filter(|&&v| v > 0).count() as u64
}

pub fn any(m: &Mask) -> bool {
    m.as_raw().iter().any(|&v| v > 0)
}

fn zip(a: &Mask, b: &Mask, f: impl Fn(bool, bool) -> bool) -> Mask {
    let mut o = GrayImage::new(a.width(), a.height());
    for ((d, &x), &y) in o.as_mut().iter_mut().zip(a.as_raw()).zip(b.as_raw()) {
        *d = if f(x > 0, y > 0) { 255 } else { 0 };
    }
    o
}

pub fn and(a: &Mask, b: &Mask) -> Mask {
    zip(a, b, |x, y| x && y)
}
pub fn and_not(a: &Mask, b: &Mask) -> Mask {
    zip(a, b, |x, y| x && !y)
}
pub fn or(a: &Mask, b: &Mask) -> Mask {
    zip(a, b, |x, y| x || y)
}

/// Fracción de la máscara `m` cubierta por `zona` (`cambio[o.mask].mean()`).
pub fn frac_inside(m: &Mask, zona: &Mask) -> f64 {
    let (mut n, mut k) = (0u64, 0u64);
    for (&a, &b) in m.as_raw().iter().zip(zona.as_raw()) {
        if a > 0 {
            n += 1;
            if b > 0 {
                k += 1;
            }
        }
    }
    if n == 0 {
        0.0
    } else {
        k as f64 / n as f64
    }
}

/// Gris como `cv2.COLOR_BGR2GRAY` (0.299 R + 0.587 G + 0.114 B).
pub fn gray(rgb: &RgbImage) -> GrayImage {
    let mut g = GrayImage::new(rgb.width(), rgb.height());
    for (o, p) in g.pixels_mut().zip(rgb.pixels()) {
        let [r, gg, b] = p.0;
        o.0[0] = (0.299 * r as f32 + 0.587 * gg as f32 + 0.114 * b as f32).round() as u8;
    }
    g
}

/// `cv2.GaussianBlur(g, (5, 5), 0)`: sigma = 0.3·((5−1)·0.5 − 1) + 0.8 = 1.1.
pub fn blur5(g: &GrayImage) -> GrayImage {
    imageproc::filter::gaussian_blur_f32(g, 1.1)
}

pub fn absdiff_gt(a: &GrayImage, b: &GrayImage, th: u8) -> Mask {
    let mut o = GrayImage::new(a.width(), a.height());
    for ((d, &x), &y) in o.as_mut().iter_mut().zip(a.as_raw()).zip(b.as_raw()) {
        *d = if x.abs_diff(y) > th { 255 } else { 0 };
    }
    o
}

/// Morfología con kernel cuadrado `k×k` (k impar), como `np.ones((k, k))` en OpenCV.
pub fn open_sq(m: &Mask, k: u8) -> Mask {
    imageproc::morphology::open(m, Norm::LInf, k / 2)
}
pub fn close_sq(m: &Mask, k: u8) -> Mask {
    imageproc::morphology::close(m, Norm::LInf, k / 2)
}
pub fn dilate_sq(m: &Mask, k: u8) -> Mask {
    imageproc::morphology::dilate(m, Norm::LInf, k / 2)
}

/// `relleno()`: la componente más grande, con los agujeros rellenos.
pub fn fill_largest(m: &Mask) -> Mask {
    let (w, h) = m.dimensions();
    let mut out = empty(w, h);
    if !any(m) {
        return out;
    }
    let labels = imageproc::region_labelling::connected_components(
        m,
        imageproc::region_labelling::Connectivity::Eight,
        Luma([0u8]),
    );
    let mut sizes = std::collections::HashMap::<u32, u64>::new();
    for p in labels.pixels() {
        if p.0[0] > 0 {
            *sizes.entry(p.0[0]).or_default() += 1;
        }
    }
    let best = sizes.into_iter().max_by_key(|&(l, n)| (n, std::cmp::Reverse(l))).map(|(l, _)| l).unwrap();
    // fondo alcanzable desde el borde (4-conexo) = afuera; todo lo demás queda adentro.
    let idx = |x: u32, y: u32| (y * w + x) as usize;
    let comp: Vec<bool> = labels.pixels().map(|p| p.0[0] == best).collect();
    let mut outside = vec![false; (w * h) as usize];
    let mut q = VecDeque::new();
    for x in 0..w {
        for y in [0, h - 1] {
            if !comp[idx(x, y)] && !outside[idx(x, y)] {
                outside[idx(x, y)] = true;
                q.push_back((x, y));
            }
        }
    }
    for y in 0..h {
        for x in [0, w - 1] {
            if !comp[idx(x, y)] && !outside[idx(x, y)] {
                outside[idx(x, y)] = true;
                q.push_back((x, y));
            }
        }
    }
    while let Some((x, y)) = q.pop_front() {
        let nb = [(x.wrapping_sub(1), y), (x + 1, y), (x, y.wrapping_sub(1)), (x, y + 1)];
        for (nx, ny) in nb {
            if nx < w && ny < h && !comp[idx(nx, ny)] && !outside[idx(nx, ny)] {
                outside[idx(nx, ny)] = true;
                q.push_back((nx, ny));
            }
        }
    }
    for (i, o) in out.as_mut().iter_mut().enumerate() {
        if !outside[i] {
            *o = 255;
        }
    }
    out
}

/// Componentes conexas (8) con su área en px.
pub fn components(m: &Mask) -> Vec<(u32, u64)> {
    let labels = imageproc::region_labelling::connected_components(
        m,
        imageproc::region_labelling::Connectivity::Eight,
        Luma([0u8]),
    );
    let mut sizes = std::collections::BTreeMap::<u32, u64>::new();
    for p in labels.pixels() {
        if p.0[0] > 0 {
            *sizes.entry(p.0[0]).or_default() += 1;
        }
    }
    sizes.into_iter().collect()
}

/// Caja `(x0, y0, x1, y1)` exclusiva.
pub fn bbox(m: &Mask) -> Option<(u32, u32, u32, u32)> {
    let (mut x0, mut y0, mut x1, mut y1) = (u32::MAX, u32::MAX, 0, 0);
    for (x, y, p) in m.enumerate_pixels() {
        if p.0[0] > 0 {
            x0 = x0.min(x);
            y0 = y0.min(y);
            x1 = x1.max(x + 1);
            y1 = y1.max(y + 1);
        }
    }
    (x0 != u32::MAX).then_some((x0, y0, x1, y1))
}

/// Centroide en coordenadas de centro de píxel (`argwhere(m).mean(0)` + 0.5).
pub fn centroid(m: &Mask) -> Option<(f64, f64)> {
    let (mut sx, mut sy, mut n) = (0.0, 0.0, 0u64);
    for (x, y, p) in m.enumerate_pixels() {
        if p.0[0] > 0 {
            sx += x as f64;
            sy += y as f64;
            n += 1;
        }
    }
    (n > 0).then(|| (sx / n as f64 + 0.5, sy / n as f64 + 0.5))
}

/// Contorno externo más grande simplificado con Douglas–Peucker (`approxPolyDP(c, 1.2, True)`), en px.
pub fn contour_polygon(m: &Mask, eps: f64) -> Vec<(f64, f64)> {
    let cs = imageproc::contours::find_contours::<i32>(m);
    let best = cs
        .iter()
        .filter(|c| c.border_type == imageproc::contours::BorderType::Outer)
        .max_by(|a, b| {
            let (aa, bb) = (poly_area(&a.points), poly_area(&b.points));
            aa.partial_cmp(&bb).unwrap_or(std::cmp::Ordering::Equal)
        });
    let Some(c) = best else { return vec![] };
    let simp = imageproc::geometry::approximate_polygon_dp(&c.points, eps, true);
    simp.iter().map(|p| (p.x as f64 + 0.5, p.y as f64 + 0.5)).collect()
}

fn poly_area(pts: &[imageproc::point::Point<i32>]) -> f64 {
    let n = pts.len();
    let mut s = 0.0;
    for i in 0..n {
        let (a, b) = (pts[i], pts[(i + 1) % n]);
        s += a.x as f64 * b.y as f64 - b.x as f64 * a.y as f64;
    }
    (s / 2.0).abs()
}

/// `cv2.warpAffine(m, A, (w, h), flags=INTER_NEAREST)`.
pub fn warp_affine_nearest(m: &Mask, a: &Affine, w: u32, h: u32) -> Mask {
    let mut out = empty(w, h);
    let Some(inv) = geom::invert(a) else { return out };
    for y in 0..h {
        for x in 0..w {
            let (sx, sy) = geom::apply(&inv, (x as f64, y as f64));
            let (sx, sy) = (sx.round(), sy.round());
            if sx >= 0.0 && sy >= 0.0 && (sx as u32) < m.width() && (sy as u32) < m.height() && m.get_pixel(sx as u32, sy as u32).0[0] > 0 {
                out.put_pixel(x, y, ON);
            }
        }
    }
    out
}

pub fn crop_rgb(img: &RgbImage, b: (u32, u32, u32, u32)) -> RgbImage {
    image::imageops::crop_imm(img, b.0, b.1, b.2 - b.0, b.3 - b.1).to_image()
}
pub fn crop_gray(img: &GrayImage, b: (u32, u32, u32, u32)) -> GrayImage {
    image::imageops::crop_imm(img, b.0, b.1, b.2 - b.0, b.3 - b.1).to_image()
}

/// Pega `m` (recortada en `b`) en una máscara de `w×h`.
pub fn place(m: &Mask, b: (u32, u32, u32, u32), w: u32, h: u32) -> Mask {
    let mut out = empty(w, h);
    for (x, y, p) in m.enumerate_pixels() {
        if p.0[0] > 0 && b.0 + x < w && b.1 + y < h {
            out.put_pixel(b.0 + x, b.1 + y, ON);
        }
    }
    out
}

/// Histograma HSV 18×8 normalizado L2 (`calcHist([0,1], [18,8], [0,180,0,256])` + `normalize`).
pub fn hsv_hist(rgb: &RgbImage, m: &Mask) -> Vec<f32> {
    let mut h = vec![0f32; 18 * 8];
    for (p, mk) in rgb.pixels().zip(m.pixels()) {
        if mk.0[0] == 0 {
            continue;
        }
        let (hue, sat) = hue_sat_cv(p);
        let hb = ((hue as usize) * 18 / 180).min(17);
        let sb = ((sat as usize) * 8 / 256).min(7);
        h[hb * 8 + sb] += 1.0;
    }
    let n = h.iter().map(|v| v * v).sum::<f32>().sqrt();
    if n > 0.0 {
        h.iter_mut().for_each(|v| *v /= n);
    }
    h
}

/// H en [0,180), S en [0,255] como OpenCV 8 bits.
fn hue_sat_cv(p: &Rgb<u8>) -> (f32, f32) {
    let [r, g, b] = p.0.map(|v| v as f32);
    let mx = r.max(g).max(b);
    let mn = r.min(g).min(b);
    let d = mx - mn;
    let s = if mx > 0.0 { 255.0 * d / mx } else { 0.0 };
    let mut hdeg = if d == 0.0 {
        0.0
    } else if mx == r {
        60.0 * (g - b) / d
    } else if mx == g {
        120.0 + 60.0 * (b - r) / d
    } else {
        240.0 + 60.0 * (r - g) / d
    };
    if hdeg < 0.0 {
        hdeg += 360.0;
    }
    ((hdeg / 2.0).min(179.0), s)
}

/// `cv2.compareHist(a, b, HISTCMP_CORREL)`.
pub fn correl(a: &[f32], b: &[f32]) -> f64 {
    let n = a.len() as f64;
    let ma = a.iter().map(|&v| v as f64).sum::<f64>() / n;
    let mb = b.iter().map(|&v| v as f64).sum::<f64>() / n;
    let (mut num, mut da, mut db) = (0.0, 0.0, 0.0);
    for (&x, &y) in a.iter().zip(b) {
        let (x, y) = (x as f64 - ma, y as f64 - mb);
        num += x * y;
        da += x * x;
        db += y * y;
    }
    if da * db <= 0.0 {
        return if da == db { 1.0 } else { 0.0 };
    }
    num / (da * db).sqrt()
}

/// Recorte RGBA del objeto (fuera de la máscara transparente) para la «apariencia».
pub fn cutout_rgba(img: &RgbImage, m: &Mask) -> Option<RgbaImage> {
    let b = bbox(m)?;
    let mut out = RgbaImage::new(b.2 - b.0, b.3 - b.1);
    for (x, y, o) in out.enumerate_pixels_mut() {
        let (sx, sy) = (b.0 + x, b.1 + y);
        let p = img.get_pixel(sx, sy).0;
        let a = if m.get_pixel(sx, sy).0[0] > 0 { 255 } else { 0 };
        *o = Rgba([p[0], p[1], p[2], a]);
    }
    Some(out)
}

/// Pinta el borde de la máscara (2 px) sobre la imagen.
pub fn draw_outline(img: &mut RgbImage, m: &Mask, color: [u8; 3]) {
    let inner = imageproc::morphology::erode(m, Norm::LInf, 2);
    for (x, y, p) in m.enumerate_pixels() {
        if p.0[0] > 0 && inner.get_pixel(x, y).0[0] == 0 {
            img.put_pixel(x, y, Rgb(color));
        }
    }
}

/// Dígitos 3×5 (bits de arriba abajo, izquierda a derecha) para rotular regiones sin depender de fuentes.
const DIGITS: [u16; 10] = [
    0b111_101_101_101_111,
    0b010_110_010_010_111,
    0b111_001_111_100_111,
    0b111_001_111_001_111,
    0b101_101_111_001_001,
    0b111_100_111_001_111,
    0b111_100_111_101_111,
    0b111_001_010_010_010,
    0b111_101_111_101_111,
    0b111_101_111_001_111,
];

/// Escribe `n` con dígitos de `3s×5s` px y un fondo negro para que se lea sobre cualquier color.
pub fn draw_number(img: &mut RgbImage, x: i64, y: i64, n: usize, s: i64, color: [u8; 3]) {
    let txt = n.to_string();
    let wtot = txt.len() as i64 * 4 * s;
    for yy in (y - s)..(y + 6 * s) {
        for xx in (x - s)..(x + wtot) {
            if xx >= 0 && yy >= 0 && (xx as u32) < img.width() && (yy as u32) < img.height() {
                img.put_pixel(xx as u32, yy as u32, Rgb([0, 0, 0]));
            }
        }
    }
    for (k, ch) in txt.bytes().enumerate() {
        let bits = DIGITS[(ch - b'0') as usize];
        for r in 0..5 {
            for c in 0..3 {
                if bits >> (14 - (r * 3 + c)) & 1 == 1 {
                    for dy in 0..s {
                        for dx in 0..s {
                            let (px, py) = (x + k as i64 * 4 * s + c * s + dx, y + r * s + dy);
                            if px >= 0 && py >= 0 && (px as u32) < img.width() && (py as u32) < img.height() {
                                img.put_pixel(px as u32, py as u32, Rgb(color));
                            }
                        }
                    }
                }
            }
        }
    }
}

/// Codifica a PNG en memoria.
pub fn png_bytes(img: &image::DynamicImage) -> Vec<u8> {
    let mut buf = std::io::Cursor::new(Vec::new());
    img.write_to(&mut buf, image::ImageFormat::Png).expect("png en memoria");
    buf.into_inner()
}

/// Codifica a JPEG (calidad 88, como el registro de `seguidor_v4`).
pub fn jpeg_bytes(img: &RgbImage, quality: u8) -> Vec<u8> {
    let mut buf = Vec::new();
    let mut enc = image::codecs::jpeg::JpegEncoder::new_with_quality(&mut buf, quality);
    enc.encode_image(img).expect("jpeg en memoria");
    buf
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fill_largest_fills_holes_and_keeps_biggest() {
        let mut m = empty(40, 40);
        for y in 5..25 {
            for x in 5..25 {
                if !(10..15).contains(&x) || !(10..15).contains(&y) {
                    m.put_pixel(x, y, ON);
                }
            }
        }
        m.put_pixel(35, 35, ON);
        let f = fill_largest(&m);
        assert_eq!(count(&f), 400);
        assert_eq!(f.get_pixel(35, 35).0[0], 0);
    }

    #[test]
    fn hist_correl_same_is_one() {
        let img = RgbImage::from_fn(10, 10, |x, y| Rgb([(x * 20) as u8, (y * 20) as u8, 90]));
        let m = full(10, 10);
        let h = hsv_hist(&img, &m);
        assert!((correl(&h, &h) - 1.0).abs() < 1e-9);
    }
}
