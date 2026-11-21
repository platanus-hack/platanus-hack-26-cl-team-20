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

/// Polinomio bivariado de grado `degree` que lleva px del lienzo (centro de píxel) a px de cámara. Es el «polinomio
/// robusto ajustado al escaneo» del lienzo rectificado de `seguidor_v4.Mapa`: absorbe homografía + lente en un solo
/// ajuste. Grado 2 solo alcanza para lentes casi sin distorsión; la radial (k1) es cúbica: usar grado ≥ 4.
#[derive(Debug, Clone, PartialEq)]
pub struct Poly2 {
    pub degree: usize,
    /// Coeficientes de `x_cam` e `y_cam`, en el orden de `Poly2::basis` (grado total creciente).
    pub cx: Vec<f64>,
    pub cy: Vec<f64>,
    /// Normalización de la entrada: `(u − u0) / s`, `(v − v0) / s` (mantiene el ajuste bien condicionado).
    pub norm: (f64, f64, f64),
}

/// Resultado del ajuste robusto.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct PolyFit {
    /// Residuo RMS (px de cámara) sobre los puntos aceptados.
    pub rms_px: f64,
    pub inliers: usize,
    pub total: usize,
}

impl Poly2 {
    pub fn terms(degree: usize) -> usize {
        (degree + 1) * (degree + 2) / 2
    }

    fn basis(degree: usize, x: f64, y: f64, out: &mut [f64]) {
        let mut k = 0;
        for d in 0..=degree {
            for j in 0..=d {
                out[k] = x.powi((d - j) as i32) * y.powi(j as i32);
                k += 1;
            }
        }
    }

    pub fn eval(&self, u: f64, v: f64) -> (f64, f64) {
        let mut b = [0.0; 45];
        let n = Self::terms(self.degree);
        Self::basis(self.degree, (u - self.norm.0) / self.norm.2, (v - self.norm.1) / self.norm.2, &mut b[..n]);
        let dot = |c: &[f64]| c.iter().zip(&b[..n]).map(|(a, b)| a * b).sum::<f64>();
        (dot(&self.cx), dot(&self.cy))
    }

    /// Ajuste robusto (mínimos cuadrados reponderados, Tukey) de pares lienzo px → cámara px. Los puntos que no
    /// siguen el plano de la mesa (los «bultos» de los objetos durante el escaneo) quedan con peso 0.
    pub fn fit(canvas_px: &[(f64, f64)], cam_px: &[(f64, f64)], degree: usize) -> Option<(Poly2, PolyFit)> {
        let n = canvas_px.len();
        let t = Self::terms(degree);
        if degree == 0 || degree > 8 || n != cam_px.len() || n < 2 * t {
            return None;
        }
        let (mut u0, mut v0, mut u1, mut v1) = (f64::MAX, f64::MAX, f64::MIN, f64::MIN);
        for &(u, v) in canvas_px {
            (u0, v0, u1, v1) = (u0.min(u), v0.min(v), u1.max(u), v1.max(v));
        }
        let norm = ((u0 + u1) / 2.0, (v0 + v1) / 2.0, ((u1 - u0).max(v1 - v0) / 2.0).max(1e-9));
        let mut rows = vec![0.0; n * t];
        for (i, &(u, v)) in canvas_px.iter().enumerate() {
            Self::basis(degree, (u - norm.0) / norm.2, (v - norm.1) / norm.2, &mut rows[i * t..(i + 1) * t]);
        }
        let mut w = vec![1.0; n];
        let mut poly = Poly2 { degree, cx: vec![], cy: vec![], norm };
        let mut res = vec![0.0; n];
        for _ in 0..6 {
            let mut ata = vec![0.0; t * t];
            let (mut bx, mut by) = (vec![0.0; t], vec![0.0; t]);
            for i in 0..n {
                if w[i] == 0.0 {
                    continue;
                }
                let r = &rows[i * t..(i + 1) * t];
                for a in 0..t {
                    let wa = w[i] * r[a];
                    bx[a] += wa * cam_px[i].0;
                    by[a] += wa * cam_px[i].1;
                    for b in a..t {
                        ata[a * t + b] += wa * r[b];
                    }
                }
            }
            for a in 0..t {
                for b in 0..a {
                    ata[a * t + b] = ata[b * t + a];
                }
            }
            (poly.cx, poly.cy) = solve2(ata, bx, by, t)?;
            for i in 0..n {
                let (x, y) = poly.eval(canvas_px[i].0, canvas_px[i].1);
                res[i] = (x - cam_px[i].0).hypot(y - cam_px[i].1);
            }
            let mut sorted = res.clone();
            sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
            let c = 4.685 * (1.4826 * sorted[n / 2]).max(0.05);
            for i in 0..n {
                let q = res[i] / c;
                w[i] = if q < 1.0 { (1.0 - q * q).powi(2) } else { 0.0 };
            }
        }
        let (mut s, mut k) = (0.0, 0usize);
        for i in 0..n {
            if w[i] > 0.0 {
                s += res[i] * res[i];
                k += 1;
            }
        }
        (k >= t).then(|| (poly, PolyFit { rms_px: (s / k as f64).sqrt(), inliers: k, total: n }))
    }
}

/// Resuelve `A x = b` para dos lados derechos (eliminación gaussiana con pivoteo parcial).
fn solve2(mut a: Vec<f64>, mut bx: Vec<f64>, mut by: Vec<f64>, n: usize) -> Option<(Vec<f64>, Vec<f64>)> {
    for c in 0..n {
        let piv = (c..n).max_by(|&i, &j| a[i * n + c].abs().partial_cmp(&a[j * n + c].abs()).unwrap_or(std::cmp::Ordering::Equal))?;
        if a[piv * n + c].abs() < 1e-13 {
            return None;
        }
        if piv != c {
            for k in 0..n {
                a.swap(c * n + k, piv * n + k);
            }
            bx.swap(c, piv);
            by.swap(c, piv);
        }
        for r in c + 1..n {
            let f = a[r * n + c] / a[c * n + c];
            if f == 0.0 {
                continue;
            }
            for k in c..n {
                a[r * n + k] -= f * a[c * n + k];
            }
            bx[r] -= f * bx[c];
            by[r] -= f * by[c];
        }
    }
    for c in (0..n).rev() {
        for k in c + 1..n {
            bx[c] -= a[c * n + k] * bx[k];
            by[c] -= a[c * n + k] * by[k];
        }
        bx[c] /= a[c * n + c];
        by[c] /= a[c * n + c];
    }
    Some((bx, by))
}

/// Distorsión radial de la lente (modelo de OpenCV sin tangencial): `p_d = c + f · x (1 + k1 r² + k2 r⁴)` con
/// `x = (p − c) / f`. Lleva px ideales (pinhole) a px de la foto real.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Radial {
    pub center: (f64, f64),
    pub focal_px: f64,
    pub k1: f64,
    pub k2: f64,
}

impl Radial {
    pub fn distort(&self, x: f64, y: f64) -> (f64, f64) {
        let (nx, ny) = ((x - self.center.0) / self.focal_px, (y - self.center.1) / self.focal_px);
        let r2 = nx * nx + ny * ny;
        let k = 1.0 + self.k1 * r2 + self.k2 * r2 * r2;
        (self.center.0 + self.focal_px * nx * k, self.center.1 + self.focal_px * ny * k)
    }
}

/// Entrada de la tabla de remapeo: índice del píxel superior izquierdo en la foto y pesos bilineales en 1/256.
#[derive(Debug, Clone, Copy)]
struct Tap {
    off: u32,
    fx: u16,
    fy: u16,
}

const NO_TAP: u32 = u32::MAX;

/// Tabla precalculada lienzo → foto de cámara (el `mx, my` de `lienzo_map.npz` + `cv2.remap`).
#[derive(Debug, Clone)]
struct Remap {
    cam: (u32, u32),
    taps: Vec<Tap>,
    valid: image::GrayImage,
}

impl Remap {
    fn build(canvas: &Canvas, cam: (u32, u32), f: impl Fn(f64, f64) -> Option<(f64, f64)>) -> Remap {
        let mut taps = Vec::with_capacity((canvas.width * canvas.height) as usize);
        let mut valid = image::GrayImage::new(canvas.width, canvas.height);
        let (cw, ch) = (cam.0 as f64, cam.1 as f64);
        for v in 0..canvas.height {
            for u in 0..canvas.width {
                let mut tap = Tap { off: NO_TAP, fx: 0, fy: 0 };
                if let Some((x, y)) = f(u as f64 + 0.5, v as f64 + 0.5) {
                    let (fx, fy) = (x - 0.5, y - 0.5);
                    if cam.0 >= 2 && cam.1 >= 2 && fx >= 0.0 && fy >= 0.0 && fx <= cw - 1.0 && fy <= ch - 1.0 {
                        let (x0, y0) = ((fx.floor() as u32).min(cam.0 - 2), (fy.floor() as u32).min(cam.1 - 2));
                        tap = Tap {
                            off: y0 * cam.0 + x0,
                            fx: ((fx - x0 as f64) * 256.0).round() as u16,
                            fy: ((fy - y0 as f64) * 256.0).round() as u16,
                        };
                        valid.put_pixel(u, v, image::Luma([255]));
                    }
                }
                taps.push(tap);
            }
        }
        Remap { cam, taps, valid }
    }

    /// Muestreo bilineal en enteros, por franjas de filas en paralelo (`std::thread::scope`).
    fn apply(&self, cam: &RgbImage, width: u32, height: u32) -> RgbImage {
        assert_eq!(cam.dimensions(), self.cam, "la tabla es para otro tamaño de foto");
        assert_eq!(self.taps.len(), (width * height) as usize);
        let mut out = vec![0u8; (width * height * 3) as usize];
        let src: &[u8] = cam.as_raw();
        let stride = self.cam.0 as usize * 3;
        let threads = std::thread::available_parallelism().map(|n| n.get()).unwrap_or(1).min(8);
        // franjas de 16 filas repartidas por una cola: los núcleos rápidos toman más franjas que los eficientes
        let strip = 16 * width as usize;
        let queue = std::sync::Mutex::new(out.chunks_mut(strip * 3).zip(self.taps.chunks(strip)));
        std::thread::scope(|s| {
            for _ in 0..threads {
                s.spawn(|| loop {
                    let next = queue.lock().expect("cola de franjas").next();
                    let Some((dst, taps)) = next else { break };
                    let (sp, dp) = (src.as_ptr(), dst.as_mut_ptr());
                    let mut k = 0usize;
                    for t in taps {
                        if t.off != NO_TAP {
                            let (fx, fy) = (t.fx as u32, t.fy as u32);
                            let (gx, gy) = (256 - fx, 256 - fy);
                            // SAFETY: `build` solo emite `off ≤ (alto−2)·ancho + ancho−2` para una foto de `self.cam`
                            // (verificado arriba contra `cam.dimensions()`), así que las dos filas de 6 bytes existen;
                            // `k + 2 < dst.len()` porque `dst` tiene 3 bytes por tap. Sin chequeos de índice para que
                            // en debug también quepa en el presupuesto.
                            unsafe {
                                let a = sp.add(t.off as usize * 3);
                                let b = a.add(stride);
                                let top = *a as u32 * gx + *a.add(3) as u32 * fx;
                                let bot = *b as u32 * gx + *b.add(3) as u32 * fx;
                                *dp.add(k) = ((top * gy + bot * fy + 32768) >> 16) as u8;
                                let top = *a.add(1) as u32 * gx + *a.add(4) as u32 * fx;
                                let bot = *b.add(1) as u32 * gx + *b.add(4) as u32 * fx;
                                *dp.add(k + 1) = ((top * gy + bot * fy + 32768) >> 16) as u8;
                                let top = *a.add(2) as u32 * gx + *a.add(5) as u32 * fx;
                                let bot = *b.add(2) as u32 * gx + *b.add(5) as u32 * fx;
                                *dp.add(k + 2) = ((top * gy + bot * fy + 32768) >> 16) as u8;
                            }
                        }
                        k += 3;
                    }
                });
            }
        });
        RgbImage::from_raw(width, height, out).expect("tamaño del lienzo")
    }
}

/// Rectifica fotos de cámara al lienzo de la mesa. Tres modelos: homografía cámara→`table_mm` (`new`), homografía +
/// distorsión radial k1/k2 (`with_radial`) y polinomio ajustado al escaneo (`from_poly`, el de Daniel). Los dos
/// últimos (y `new` tras `precompute`) usan una **tabla de remapeo precalculada** para un tamaño de foto fijo.
#[derive(Debug, Clone)]
pub struct Rectifier {
    pub canvas: Canvas,
    table_to_cam: Option<Homography>,
    remap: Option<std::sync::Arc<Remap>>,
}

impl Rectifier {
    pub fn new(cam_to_table_mm: Homography, canvas: Canvas) -> Option<Rectifier> {
        Some(Rectifier { canvas, table_to_cam: Some(cam_to_table_mm.inverse()?), remap: None })
    }

    /// Precalcula la tabla de la homografía para fotos de `cam_size` (ancho, alto).
    pub fn precompute(mut self, cam_size: (u32, u32)) -> Rectifier {
        if let Some(h) = self.table_to_cam {
            let c = self.canvas;
            self.remap = Some(std::sync::Arc::new(Remap::build(&c, cam_size, |u, v| {
                let (x, y) = c.px_to_mm(u, v);
                h.apply(x, y)
            })));
        }
        self
    }

    /// Homografía (del pinhole ideal) + distorsión radial de la lente, con tabla precalculada.
    pub fn with_radial(cam_to_table_mm: Homography, lens: Radial, canvas: Canvas, cam_size: (u32, u32)) -> Option<Rectifier> {
        let h = cam_to_table_mm.inverse()?;
        let remap = Remap::build(&canvas, cam_size, |u, v| {
            let (x, y) = canvas.px_to_mm(u, v);
            h.apply(x, y).map(|(x, y)| lens.distort(x, y))
        });
        Some(Rectifier { canvas, table_to_cam: None, remap: Some(std::sync::Arc::new(remap)) })
    }

    /// Polinomio lienzo px → cámara px (ver `Poly2::fit`), con tabla precalculada para fotos de `cam_size`.
    pub fn from_poly(poly: &Poly2, canvas: Canvas, cam_size: (u32, u32)) -> Rectifier {
        let remap = Remap::build(&canvas, cam_size, |u, v| Some(poly.eval(u, v)));
        Rectifier { canvas, table_to_cam: None, remap: Some(std::sync::Arc::new(remap)) }
    }

    /// Px del lienzo que caen dentro de la foto (sirve de `Tracker::with_area`). Solo con tabla precalculada.
    pub fn valid_area(&self) -> Option<&image::GrayImage> {
        self.remap.as_deref().map(|r| &r.valid)
    }

    /// Remuestreo bilineal (equivale a `cv2.remap(..., INTER_LINEAR)`). Devuelve la imagen y la máscara de
    /// píxeles válidos (los que caen dentro de la foto). Con tabla precalculada y foto del tamaño previsto usa la
    /// tabla; si no, evalúa la homografía píxel a píxel (lento) o devuelve todo inválido si no hay homografía.
    pub fn rectify(&self, cam: &RgbImage) -> (RgbImage, image::GrayImage) {
        let c = &self.canvas;
        if let Some(r) = self.remap.as_deref().filter(|r| r.cam == cam.dimensions()) {
            return (r.apply(cam, c.width, c.height), r.valid.clone());
        }
        let mut out = RgbImage::new(c.width, c.height);
        let mut valid = image::GrayImage::new(c.width, c.height);
        let Some(table_to_cam) = self.table_to_cam else { return (out, valid) };
        let (cw, ch) = (cam.width() as f64, cam.height() as f64);
        for v in 0..c.height {
            for u in 0..c.width {
                let (x, y) = c.px_to_mm(u as f64 + 0.5, v as f64 + 0.5);
                let Some((cx, cy)) = table_to_cam.apply(x, y) else { continue };
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

    /// Cámara simulada 1920×1080 con lente k1 = −0.08 mirando la mesa en leve perspectiva.
    fn lente_simulada() -> (Canvas, Homography, Radial) {
        let canvas = Canvas::covering(0.0, 0.0, 1280.0, 720.0, 1280);
        // mesa mm → px ideales (escala ~1.2 px/mm, algo de giro y perspectiva)
        let table_to_cam = Homography([1.22, 0.03, 180.0, -0.02, 1.25, 90.0, 0.00003, 0.00005, 1.0]);
        let lens = Radial { center: (960.0, 540.0), focal_px: 1400.0, k1: -0.08, k2: 0.0 };
        (canvas, table_to_cam.inverse().unwrap(), lens)
    }

    #[test]
    fn poly_corrige_lente_k1() {
        let (canvas, cam_to_table, lens) = lente_simulada();
        let t2c = cam_to_table.inverse().unwrap();
        let truth = |u: f64, v: f64| {
            let (x, y) = canvas.px_to_mm(u, v);
            let (x, y) = t2c.apply(x, y).unwrap();
            lens.distort(x, y)
        };
        // «escaneo»: grilla 48×27 con ruido de decodificación ±0.1 px y bultos (objetos sobre la mesa + sueltos)
        let mut rng = Lcg(7);
        let (mut src, mut dst) = (vec![], vec![]);
        for j in 0..27 {
            for i in 0..48 {
                let (u, v) = (13.0 + i as f64 * 26.7, 13.0 + j as f64 * 26.7);
                let (mut x, mut y) = truth(u, v);
                x += (rng.unit() - 0.5) * 0.2;
                y += (rng.unit() - 0.5) * 0.2;
                let bulto = (12..17).contains(&i) && (8..12).contains(&j) || (30..36).contains(&i) && (15..18).contains(&j);
                if bulto || rng.unit() < 0.03 {
                    x += 6.0 + 10.0 * rng.unit();
                    y -= 4.0 + 6.0 * rng.unit();
                }
                src.push((u, v));
                dst.push((x, y));
            }
        }
        let err = |f: &dyn Fn(f64, f64) -> (f64, f64)| {
            let (mut s, mut mx, mut n) = (0.0, 0.0f64, 0);
            for v in (0..720).step_by(9) {
                for u in (0..1280).step_by(9) {
                    let (a, b) = (f(u as f64 + 0.5, v as f64 + 0.5), truth(u as f64 + 0.5, v as f64 + 0.5));
                    let e = (a.0 - b.0).hypot(a.1 - b.1);
                    s += e * e;
                    mx = mx.max(e);
                    n += 1;
                }
            }
            ((s / n as f64).sqrt(), mx)
        };
        // solo homografía (lo que había): la lente deja varios px de error
        let (h_rms, h_max) = err(&|u, v| {
            let (x, y) = canvas.px_to_mm(u, v);
            t2c.apply(x, y).unwrap()
        });
        assert!(h_rms > 3.0, "{h_rms}");
        let mut linea = format!("lente k1=-0.08 · solo homografía: rms {h_rms:.2} px, máx {h_max:.2} px");
        for deg in [2usize, 3, 4, 5] {
            let (p, fit) = Poly2::fit(&src, &dst, deg).unwrap();
            let (rms, max) = err(&|u, v| p.eval(u, v));
            linea += &format!(
                "\n  Poly2 grado {deg}: rms {rms:.3} px, máx {max:.3} px (ajuste {:.3} px, {}/{} puntos)",
                fit.rms_px, fit.inliers, fit.total
            );
            if deg >= 4 {
                assert!(rms < 0.3 && max < 0.3, "grado {deg}: rms {rms} máx {max}");
                assert!(fit.inliers < fit.total - 30, "los bultos deben quedar fuera: {fit:?}");
            }
        }
        println!("{linea}");
    }

    #[test]
    fn rectificar_con_tabla_es_rapido_y_exacto() {
        let (canvas, cam_to_table, lens) = lente_simulada();
        let (cw, ch) = (1920u32, 1080u32);
        // la foto: un patrón suave definido en mm de mesa, visto por la cámara con lente
        let pat = |x: f64, y: f64| 128.0 + 60.0 * (x / 90.0).sin() + 50.0 * (y / 70.0).cos();
        let t0 = std::time::Instant::now();
        let r = Rectifier::with_radial(cam_to_table, lens, canvas, (cw, ch)).unwrap();
        let build_ms = t0.elapsed().as_secs_f64() * 1e3;
        // foto sintética: invierte la distorsión por punto fijo para saber qué mm ve cada px de cámara
        let cam = RgbImage::from_fn(cw, ch, |px, py| {
            let (xd, yd) = ((px as f64 + 0.5 - 960.0) / 1400.0, (py as f64 + 0.5 - 540.0) / 1400.0);
            let (mut x, mut y) = (xd, yd);
            for _ in 0..6 {
                let k = 1.0 + lens.k1 * (x * x + y * y);
                (x, y) = (xd / k, yd / k);
            }
            let (mx, my) = cam_to_table.apply(960.0 + 1400.0 * x, 540.0 + 1400.0 * y).unwrap();
            let v = pat(mx, my).round().clamp(0.0, 255.0) as u8;
            Rgb([v, v, 255 - v])
        });
        let mut ms: Vec<f64> = (0..9)
            .map(|_| {
                let t = std::time::Instant::now();
                let (img, _) = r.rectify(&cam);
                assert_eq!(img.dimensions(), (1280, 720));
                t.elapsed().as_secs_f64() * 1e3
            })
            .collect();
        ms.sort_by(|a, b| a.partial_cmp(b).unwrap());
        let (img, valid) = r.rectify(&cam);
        let (mut worst, mut n) = (0.0f64, 0u32);
        for v in (0..720).step_by(7) {
            for u in (0..1280).step_by(7) {
                if valid.get_pixel(u, v).0[0] == 0 {
                    continue;
                }
                let (x, y) = canvas.px_to_mm(u as f64 + 0.5, v as f64 + 0.5);
                worst = worst.max((img.get_pixel(u, v).0[0] as f64 - pat(x, y)).abs());
                n += 1;
            }
        }
        println!(
            "rectificar 1280x720 desde 1920x1080 (debug): mín {:.2} ms, mediana {:.2} ms · tabla {build_ms:.0} ms · error foto {worst:.1}/255",
            ms[0], ms[4]
        );
        assert!(n > 15_000 && worst <= 3.0, "{n} px, error {worst}");
        assert!(ms[0] < 10.0, "rectificar tardó {ms:?} ms");
        // sin tabla (homografía sola) sigue funcionando como antes; con `precompute` da lo mismo
        let chico = Canvas::covering(0.0, 0.0, 64.0, 36.0, 64);
        let foto = RgbImage::from_pixel(64, 36, Rgb([9, 8, 7]));
        let lento = Rectifier::new(Homography::IDENTITY, chico).unwrap();
        let (a, va) = lento.rectify(&foto);
        assert_eq!(a.get_pixel(10, 10).0, [9, 8, 7]);
        assert!(va.get_pixel(10, 10).0[0] > 0);
        assert_eq!(lento.precompute((64, 36)).rectify(&foto).0.get_pixel(10, 10).0, [9, 8, 7]);
    }
}
