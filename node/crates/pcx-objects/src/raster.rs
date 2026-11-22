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
    let best = cs.iter().filter(|c| c.border_type == imageproc::contours::BorderType::Outer).max_by(|a, b| {
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
            if sx >= 0.0
                && sy >= 0.0
                && (sx as u32) < m.width()
                && (sy as u32) < m.height()
                && m.get_pixel(sx as u32, sy as u32).0[0] > 0
            {
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
    let (mw, mh) = (m.width() as usize, m.height() as usize);
    if b.0 >= w || mw == 0 {
        return out;
    }
    let cols = mw.min((w - b.0) as usize);
    let dst: &mut [u8] = out.as_mut();
    for (y, row) in m.as_raw().chunks_exact(mw).enumerate().take(mh.min(h.saturating_sub(b.1) as usize)) {
        let o = (b.1 as usize + y) * w as usize + b.0 as usize;
        for (d, &v) in dst[o..o + cols].iter_mut().zip(row) {
            *d = if v > 0 { 255 } else { 0 };
        }
    }
    out
}

/// Histograma HSV 18×8 normalizado L2 (`calcHist([0,1], [18,8], [0,180,0,256])` + `normalize`).
pub fn hsv_hist(rgb: &RgbImage, m: &Mask) -> Vec<f32> {
    let mut h = vec![0f32; HSV_BINS];
    for (p, mk) in rgb.pixels().zip(m.pixels()) {
        if mk.0[0] == 0 {
            continue;
        }
        h[hsv_bin(p)] += 1.0;
    }
    l2_normalize(&mut h);
    h
}

/// Casillero del histograma 18×8 (tono × saturación) de un píxel.
pub fn hsv_bin(p: &Rgb<u8>) -> usize {
    let (hue, sat) = hue_sat_cv(p);
    ((hue as usize) * 18 / 180).min(17) * 8 + ((sat as usize) * 8 / 256).min(7)
}

pub const HSV_BINS: usize = 18 * 8;

pub fn l2_normalize(h: &mut [f32]) {
    let n = h.iter().map(|v| v * v).sum::<f32>().sqrt();
    if n > 0.0 {
        h.iter_mut().for_each(|v| *v /= n);
    }
}

/// Coeficiente de Bhattacharyya `Σ √(p·q)` con `p`, `q` normalizados a suma 1: 1 = iguales, 0 = sin traslape
/// (`1 − cv2.compareHist(..., HISTCMP_BHATTACHARYYA)²`).
pub fn bhattacharyya(a: &[f32], b: &[f32]) -> f64 {
    let (sa, sb): (f64, f64) = (a.iter().map(|&v| v as f64).sum(), b.iter().map(|&v| v as f64).sum());
    if sa <= 0.0 || sb <= 0.0 {
        return 0.0;
    }
    a.iter().zip(b).map(|(&x, &y)| (x as f64 * y as f64).sqrt()).sum::<f64>() / (sa * sb).sqrt()
}

/// Corrida horizontal de píxeles encendidos: fila `y`, columnas `[x0, x1)`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Run {
    pub y: u32,
    pub x0: u32,
    pub x1: u32,
}

/// Componente conexa como lista de corridas (en orden de fila), con su área y su caja exclusiva.
#[derive(Debug, Clone)]
pub struct Blob {
    pub runs: Vec<Run>,
    pub area: u64,
    pub bbox: (u32, u32, u32, u32),
}

/// Etiqueta por corridas + union-find. `on`: qué valor cuenta (encendido o fondo); `diag`: conexidad 8 (si no, 4).
/// Las filas vacías se saltan con una comparación de bloque y dentro de la fila se avanza de a 16 bytes, así una
/// máscara rala de 1280×720 cuesta ~1 ms incluso sin optimizar (el nivel 2b corre sobre esto).
fn label_runs(raw: &[u8], w: usize, on: bool, diag: bool) -> Vec<Blob> {
    let mut runs: Vec<Run> = vec![];
    let mut parent: Vec<u32> = vec![];
    fn find(parent: &mut [u32], mut i: u32) -> u32 {
        while parent[i as usize] != i {
            parent[i as usize] = parent[parent[i as usize] as usize];
            i = parent[i as usize];
        }
        i
    }
    let zeros = vec![0u8; w];
    let (z16, f16) = ([0u8; 16], [255u8; 16]);
    let (mut prev, mut prev_y) = (0..0, usize::MAX);
    for (y, row) in raw.chunks_exact(w).enumerate() {
        if on && row == &zeros[..] {
            continue;
        }
        let start = runs.len();
        let mut x = 0;
        let mut open: Option<usize> = None;
        while x < w {
            if on && x + 16 <= w {
                let c = &row[x..x + 16];
                if (open.is_none() && c == z16) || (open.is_some() && c == f16) {
                    x += 16;
                    continue;
                }
            }
            let active = (row[x] > 0) == on;
            match (open, active) {
                (None, true) => open = Some(x),
                (Some(x0), false) => {
                    runs.push(Run { y: y as u32, x0: x0 as u32, x1: x as u32 });
                    open = None;
                }
                _ => {}
            }
            x += 1;
        }
        if let Some(x0) = open {
            runs.push(Run { y: y as u32, x0: x0 as u32, x1: w as u32 });
        }
        for i in start..runs.len() {
            parent.push(i as u32);
            if prev_y.wrapping_add(1) != y {
                continue;
            }
            let c = runs[i];
            for q in prev.clone() {
                let r = runs[q];
                let toca = if diag { r.x0 <= c.x1 && c.x0 <= r.x1 } else { r.x0 < c.x1 && c.x0 < r.x1 };
                if toca {
                    let (ra, rb) = (find(&mut parent, i as u32), find(&mut parent, q as u32));
                    if ra != rb {
                        parent[ra.max(rb) as usize] = ra.min(rb);
                    }
                }
            }
        }
        (prev, prev_y) = (start..runs.len(), y);
    }
    let mut slot = vec![u32::MAX; runs.len()];
    let mut out: Vec<Blob> = vec![];
    for (i, r) in runs.iter().enumerate() {
        let root = find(&mut parent, i as u32) as usize;
        if slot[root] == u32::MAX {
            slot[root] = out.len() as u32;
            out.push(Blob { runs: vec![], area: 0, bbox: (u32::MAX, u32::MAX, 0, 0) });
        }
        let b = &mut out[slot[root] as usize];
        b.runs.push(*r);
        b.area += (r.x1 - r.x0) as u64;
        b.bbox = (b.bbox.0.min(r.x0), b.bbox.1.min(r.y), b.bbox.2.max(r.x1), b.bbox.3.max(r.y + 1));
    }
    out
}

/// Componentes conexas (8) de al menos `min_area` px, en orden de aparición (fila, columna).
pub fn blobs(m: &Mask, min_area: u64) -> Vec<Blob> {
    if m.width() == 0 {
        return vec![];
    }
    let mut b = label_runs(m.as_raw(), m.width() as usize, true, true);
    b.retain(|b| b.area >= min_area);
    b
}

/// Igual que `fill_largest` (componente más grande con los agujeros rellenos) pero por corridas: para los recortes
/// del nivel 2b, donde la versión con `imageproc` cuesta decenas de ms sin optimizar.
pub fn fill_largest_fast(m: &Mask) -> Mask {
    let (w, h) = m.dimensions();
    let mut out = empty(w, h);
    if w == 0 || h == 0 {
        return out;
    }
    let comps = label_runs(m.as_raw(), w as usize, true, true);
    let mut best: Option<&Blob> = None;
    for c in &comps {
        if best.is_none_or(|b| c.area > b.area) {
            best = Some(c);
        }
    }
    let Some(best) = best else { return out };
    let paint = |out: &mut Mask, runs: &[Run]| {
        let raw: &mut [u8] = out.as_mut();
        for r in runs {
            let o = (r.y * w) as usize;
            raw[o + r.x0 as usize..o + r.x1 as usize].fill(255);
        }
    };
    paint(&mut out, &best.runs);
    // fondo que no toca el borde (4-conexo) = agujero
    let holes: Vec<Blob> = label_runs(out.as_raw(), w as usize, false, false)
        .into_iter()
        .filter(|b| b.bbox.0 > 0 && b.bbox.1 > 0 && b.bbox.2 < w && b.bbox.3 < h)
        .collect();
    for b in &holes {
        paint(&mut out, &b.runs);
    }
    out
}

/// Apertura con kernel cuadrado `k×k` por conteo deslizante (separable). Fuera de la imagen cuenta como encendido
/// al erosionar (no se come lo que toca el borde), como el borde por defecto de OpenCV.
pub fn open_sq_fast(m: &Mask, k: u8) -> Mask {
    let (w, h) = (m.width() as usize, m.height() as usize);
    let r = (k / 2) as usize;
    if w == 0 || h == 0 || r == 0 {
        return m.clone();
    }
    // una pasada 1-D sobre `n` elementos de paso `step` desde `base`: erosión (todos) o dilatación (alguno)
    fn pass(
        src: &[u8],
        dst: &mut [u8],
        (base, step, n): (usize, usize, usize),
        r: usize,
        erode: bool,
        acc: &mut [u32],
    ) {
        acc[0] = 0;
        for i in 0..n {
            acc[i + 1] = acc[i] + (src[base + i * step] > 0) as u32;
        }
        for i in 0..n {
            let (a, b) = (i.saturating_sub(r), (i + r + 1).min(n));
            let c = acc[b] - acc[a];
            let v = if erode { c as usize == b - a } else { c > 0 };
            dst[base + i * step] = if v { 255 } else { 0 };
        }
    }
    let mut a = m.as_raw().clone();
    let mut b = vec![0u8; a.len()];
    let mut acc = vec![0u32; w.max(h) + 1];
    for erode in [true, false] {
        for y in 0..h {
            pass(&a, &mut b, (y * w, 1, w), r, erode, &mut acc);
        }
        for x in 0..w {
            pass(&b, &mut a, (x, w, h), r, erode, &mut acc);
        }
    }
    Mask::from_raw(w as u32, h as u32, a).expect("mismo tamaño")
}

/// Casilleros del descriptor de color del nivel 2b: los 18×8 de tono × saturación más 4 **acromáticos** por valor
/// (negro, gris oscuro, gris medio, blanco). El histograma tono × saturación solo no distingue negro, gris y blanco
/// (todos saturación ≈ 0) entre sí ni de una mesa gris: un objeto negro retirado «se parecía» a la mesa vacía.
pub const COLOR_BINS: usize = HSV_BINS + 4;

/// Casillero de `COLOR_BINS`. Acromático si es oscuro (V < 50: ahí la saturación es puro ruido) o poco saturado
/// (S < 40 de 255); si no, el casillero HSV de siempre.
pub fn color_bin(p: &Rgb<u8>) -> usize {
    let [r, g, b] = p.0;
    let (mx, mn) = (r.max(g).max(b) as u32, r.min(g).min(b) as u32);
    if mx < 50 {
        HSV_BINS
    } else if (mx - mn) * 255 < 40 * mx {
        HSV_BINS
            + if mx < 120 {
                1
            } else if mx < 190 {
                2
            } else {
                3
            }
    } else {
        hsv_bin(p)
    }
}

/// `color_bin` por color cuantizado a 5 bits por canal, calculado a demanda. Evita la conversión a HSV por píxel
/// (lo caro del nivel 2b); referencia y candidato deben usar la misma tabla para que los histogramas sean comparables.
pub struct BinLut(Vec<u8>);

impl Default for BinLut {
    fn default() -> Self {
        BinLut(vec![u8::MAX; 1 << 15])
    }
}

impl BinLut {
    /// `rgb`: los 3 bytes del píxel.
    pub fn bin(&mut self, rgb: &[u8]) -> usize {
        let key = ((rgb[0] as usize >> 3) << 10) | ((rgb[1] as usize >> 3) << 5) | (rgb[2] as usize >> 3);
        if self.0[key] == u8::MAX {
            self.0[key] = color_bin(&Rgb([rgb[0] & 0xF8 | 4, rgb[1] & 0xF8 | 4, rgb[2] & 0xF8 | 4])) as u8;
        }
        self.0[key] as usize
    }
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

    #[test]
    fn blobs_y_bhattacharyya() {
        let mut m = empty(64, 40);
        for y in 5..15 {
            for x in 3..20 {
                m.put_pixel(x, y, ON);
            }
        }
        for y in 20..30 {
            for x in 40..50 {
                m.put_pixel(x, y, ON);
            }
        }
        m.put_pixel(50, 30, ON); // diagonal: misma componente (8-conexa)
        m.put_pixel(63, 39, ON);
        let b = blobs(&m, 5);
        assert_eq!(b.len(), 2);
        assert_eq!((b[0].area, b[0].bbox), (170, (3, 5, 20, 15)));
        assert_eq!((b[1].area, b[1].bbox), (101, (40, 20, 51, 31)));
        assert_eq!(blobs(&m, 1).len(), 3);
        let (p, q, r) = ([4.0f32, 0.0, 1.0], [8.0f32, 0.0, 2.0], [0.0f32, 3.0, 0.0]);
        assert!((bhattacharyya(&p, &q) - 1.0).abs() < 1e-6);
        assert_eq!(bhattacharyya(&p, &r), 0.0);
    }

    #[test]
    fn color_bin_separa_acromaticos() {
        let bin = |c: [u8; 3]| color_bin(&Rgb(c));
        let (negro, oscuro, mesa, blanco) =
            (bin([28, 28, 30]), bin([90, 90, 92]), bin([145, 145, 145]), bin([240, 240, 236]));
        let todos = [negro, oscuro, mesa, blanco];
        for (i, a) in todos.iter().enumerate() {
            assert!(*a >= HSV_BINS && *a < COLOR_BINS);
            assert!(todos.iter().skip(i + 1).all(|b| b != a), "{todos:?}");
        }
        // el negro con ruido de croma (saturación alta por ser oscuro) sigue siendo negro
        assert_eq!(bin([26, 30, 32]), negro);
        assert_eq!(bin([147, 143, 146]), mesa);
        // un color de verdad conserva su casillero HSV
        assert_eq!(bin([160, 84, 30]), hsv_bin(&Rgb([160, 84, 30])));
        let mut lut = BinLut::default();
        assert_eq!(lut.bin(&[28, 28, 30]), negro);
        assert_eq!(lut.bin(&[240, 240, 236]), blanco);
    }

    #[test]
    fn versiones_rapidas_equivalen() {
        // manchas pseudoaleatorias con agujeros, en forma de U y sueltas
        let mut s = 99u64;
        let mut m = empty(97, 61);
        for _ in 0..60 {
            s = s.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
            let (x, y, a, b) = ((s >> 33) % 90, (s >> 41) % 55, 2 + (s >> 20) % 12, 2 + (s >> 12) % 9);
            for yy in y..(y + b).min(61) {
                for xx in x..(x + a).min(97) {
                    m.put_pixel(xx as u32, yy as u32, ON);
                }
            }
        }
        for (x, y) in [(30, 30), (31, 30), (50, 20), (70, 40)] {
            m.put_pixel(x, y, Luma([0]));
        }
        assert_eq!(fill_largest_fast(&m), fill_largest(&m));
        assert_eq!(blobs(&m, 1).len(), components(&m).len());
        assert_eq!(blobs(&m, 1).iter().map(|b| b.area).sum::<u64>(), count(&m));
        // apertura: igual a la de imageproc lejos del borde
        let (a, b) = (open_sq_fast(&m, 5), open_sq(&m, 5));
        for y in 3..58 {
            for x in 3..94 {
                assert_eq!(a.get_pixel(x, y), b.get_pixel(x, y), "({x}, {y})");
            }
        }
        let cut = crop_gray(&m, (10, 5, 40, 30));
        assert_eq!(place(&cut, (10, 5, 40, 30), 97, 61), and(&m, &place(&full(30, 25), (10, 5, 40, 30), 97, 61)));
    }
}
