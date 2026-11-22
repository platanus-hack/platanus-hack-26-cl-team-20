//! Familia de marcadores propia `pcx6` (sin OpenCV): 6x6 celdas, anillo exterior de «tinta» y 4x4 bits de datos.
//!
//! «Tinta» = luz del proyector en los marcadores proyectados (blanco sobre negro) o tinta negra en el impreso.
//! El diccionario se arma de forma determinista: códigos de 16 bits con distancia de Hamming ≥ 4 entre sí y
//! contra todas sus rotaciones (corrige 1 bit y detecta hasta 3). La detección: componentes conexas →
//! cuadrilátero por puntos extremos → bordes subpíxel (cruce del nivel medio) → rectas → esquinas → bits.

use std::sync::OnceLock;

use crate::homography::{apply, dlt, Mat3, Pt};
use crate::image::{components, Component, FloatMap, GrayImage, Rng};

pub const CELLS: usize = 6;
const DICT_SIZE: usize = 64;

fn rot90(code: u16) -> u16 {
    // bit r*4+c; rotación horaria: nuevo[r][c] = viejo[3-c][r]
    let mut out = 0u16;
    for r in 0..4 {
        for c in 0..4 {
            if code >> ((3 - c) * 4 + r) & 1 == 1 {
                out |= 1 << (r * 4 + c);
            }
        }
    }
    out
}

/// Diccionario `pcx6` (64 códigos).
pub fn dictionary() -> &'static [u16] {
    static DICT: OnceLock<Vec<u16>> = OnceLock::new();
    DICT.get_or_init(|| {
        let mut rng = Rng::new(0x005e_ed9c);
        let mut dict: Vec<u16> = Vec::new();
        let mut guard = 0;
        while dict.len() < DICT_SIZE && guard < 200_000 {
            guard += 1;
            let c = rng.next_u64() as u16;
            if !(5..=11).contains(&c.count_ones()) {
                continue;
            }
            let rots = [c, rot90(c), rot90(rot90(c)), rot90(rot90(rot90(c)))];
            if rots[1..].iter().any(|r| (r ^ c).count_ones() < 4) {
                continue;
            }
            if dict.iter().all(|d| rots.iter().all(|r| (r ^ d).count_ones() >= 4)) {
                dict.push(c);
            }
        }
        dict
    })
}

/// Celdas del marcador `id` (true = tinta), `[fila][columna]`.
pub fn cells(id: usize) -> [[bool; CELLS]; CELLS] {
    let code = dictionary()[id % DICT_SIZE];
    let mut m = [[true; CELLS]; CELLS];
    for r in 0..4 {
        for c in 0..4 {
            m[r + 1][c + 1] = code >> (r * 4 + c) & 1 == 1;
        }
    }
    m
}

/// Dibuja el marcador con su esquina superior izquierda en el píxel `(x0, y0)` y celdas de `cell` px.
pub fn draw(img: &mut GrayImage, id: usize, x0: i64, y0: i64, cell: i64, ink: u8, bg: u8) {
    let m = cells(id);
    for (r, row) in m.iter().enumerate() {
        for (c, &v) in row.iter().enumerate() {
            let (x, y) = (x0 + c as i64 * cell, y0 + r as i64 * cell);
            img.fill_rect(x, y, x + cell, y + cell, if v { ink } else { bg });
        }
    }
}

/// Esquinas TL, TR, BR, BL del borde exterior (convención de centros enteros: el borde está a −0,5 px).
pub fn outer_corners(x0: f64, y0: f64, side: f64) -> [Pt; 4] {
    let (a, b) = (x0 - 0.5, y0 - 0.5);
    [[a, b], [a + side, b], [a + side, b + side], [a, b + side]]
}

/// Grilla de marcadores proyectados en px nativos del proyector.
#[derive(Debug, Clone)]
pub struct MarkerGrid {
    pub proj_size: (u32, u32),
    pub cell_px: u32,
    /// `(id, x0, y0)` de cada marcador.
    pub markers: Vec<(usize, u32, u32)>,
}

impl MarkerGrid {
    /// `cols × rows` marcadores repartidos con un margen de 1 marcador/2 en los bordes.
    pub fn new(proj_size: (u32, u32), cols: u32, rows: u32, cell_px: u32) -> Self {
        let side = cell_px * CELLS as u32;
        let (w, h) = proj_size;
        let (mx, my) = (side / 2, side / 2);
        let mut markers = Vec::new();
        for j in 0..rows {
            for i in 0..cols {
                let x0 = mx + (i as f64 * (w - 2 * mx - side) as f64 / (cols - 1).max(1) as f64).round() as u32;
                let y0 = my + (j as f64 * (h - 2 * my - side) as f64 / (rows - 1).max(1) as f64).round() as u32;
                markers.push(((j * cols + i) as usize, x0, y0));
            }
        }
        Self { proj_size, cell_px, markers }
    }
    pub fn side(&self) -> f64 {
        (self.cell_px as usize * CELLS) as f64
    }
    /// Marcadores blancos sobre negro (`inverse = false`) o su inverso.
    pub fn rasterize(&self, inverse: bool) -> GrayImage {
        let (ink, bg) = if inverse { (0, 255) } else { (255, 0) };
        let mut img = GrayImage::new(self.proj_size.0, self.proj_size.1, bg);
        for &(id, x0, y0) in &self.markers {
            draw(&mut img, id, x0 as i64, y0 as i64, self.cell_px as i64, ink, bg);
        }
        img
    }
    pub fn corners(&self, id: usize) -> Option<[Pt; 4]> {
        self.markers.iter().find(|m| m.0 == id).map(|&(_, x0, y0)| outer_corners(x0 as f64, y0 as f64, self.side()))
    }
}

/// Marcador detectado: esquinas TL, TR, BR, BL en px de la imagen (orden del marcador, no de la imagen).
#[derive(Debug, Clone, PartialEq)]
pub struct Detection {
    pub id: usize,
    pub corners: [Pt; 4],
    /// Bits corregidos (0 o 1).
    pub bit_errors: u32,
}

/// Detecta marcadores. `ink`: máscara binaria de tinta; `cont`: imagen continua con la tinta alta (para el borde
/// subpíxel); `min_side`: lado mínimo en px.
pub fn detect(ink: &[bool], cont: &FloatMap, min_side: f64) -> Vec<Detection> {
    let (w, h) = (cont.width, cont.height);
    let min_area = (min_side * min_side * 0.45) as usize;
    let mut out: Vec<Detection> = Vec::new();
    for comp in components(ink, w, h, min_area.max(12)) {
        let Some(q) = rough_quad(&comp) else { continue };
        let side = (0..4).map(|i| dist(q[i], q[(i + 1) % 4])).fold(f64::INFINITY, f64::min);
        if side < min_side {
            continue;
        }
        let area = quad_area(&q);
        let fill = comp.pixels.len() as f64 / area.max(1.0);
        if !(0.4..=1.3).contains(&fill) {
            continue;
        }
        let refined = refine_quad(cont, &q).unwrap_or(q);
        if let Some(d) = decode(cont, &refined) {
            if !out.iter().any(|o| o.id == d.id) {
                out.push(d);
            } else {
                // id duplicado: ambigüedad, se descartan ambos
                out.retain(|o| o.id != d.id);
            }
        }
    }
    out
}

/// Cuadriláteros (sin decodificar) de las componentes de tinta: esquinas refinadas en orden horario.
pub fn quads(ink: &[bool], cont: &FloatMap, min_side: f64) -> Vec<[Pt; 4]> {
    let min_area = (min_side * min_side * 0.6) as usize;
    components(ink, cont.width, cont.height, min_area.max(12))
        .iter()
        .filter_map(|c| {
            let q = rough_quad(c)?;
            let fill = c.pixels.len() as f64 / quad_area(&q).max(1.0);
            (0.7..=1.3).contains(&fill).then(|| refine_quad(cont, &q).unwrap_or(q))
        })
        .collect()
}

/// Intersección de las diagonales (imagen del centro de un cuadrado bajo cualquier homografía).
pub fn diagonal_center(q: &[Pt; 4]) -> Option<Pt> {
    let l = |a: Pt, b: Pt| {
        let n = [a[1] - b[1], b[0] - a[0]];
        (n, n[0] * a[0] + n[1] * a[1])
    };
    intersect(l(q[0], q[2]), l(q[1], q[3]))
}

fn dist(a: Pt, b: Pt) -> f64 {
    ((a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2)).sqrt()
}

fn quad_area(q: &[Pt; 4]) -> f64 {
    let mut s = 0.0;
    for i in 0..4 {
        let (a, b) = (q[i], q[(i + 1) % 4]);
        s += a[0] * b[1] - b[0] * a[1];
    }
    s.abs() / 2.0
}

/// Cuadrilátero aproximado por puntos extremos; orden horario en la imagen (y hacia abajo) partiendo de cualquiera.
fn rough_quad(c: &Component) -> Option<[Pt; 4]> {
    let rows = (c.max.1 - c.min.1 + 1) as usize;
    let mut lo = vec![u32::MAX; rows];
    let mut hi = vec![0u32; rows];
    let (mut sx, mut sy) = (0.0, 0.0);
    for &(x, y) in &c.pixels {
        let r = (y - c.min.1) as usize;
        lo[r] = lo[r].min(x);
        hi[r] = hi[r].max(x);
        sx += x as f64;
        sy += y as f64;
    }
    let n = c.pixels.len() as f64;
    let cen = [sx / n, sy / n];
    let mut pts: Vec<Pt> = Vec::with_capacity(rows * 2);
    for r in 0..rows {
        if lo[r] != u32::MAX {
            let y = (c.min.1 as usize + r) as f64;
            pts.push([lo[r] as f64, y]);
            pts.push([hi[r] as f64, y]);
        }
    }
    let far = |from: Pt, pts: &[Pt]| pts.iter().copied().max_by(|a, b| dist(*a, from).total_cmp(&dist(*b, from)));
    let c1 = far(cen, &pts)?;
    let c3 = far(c1, &pts)?;
    let (dx, dy) = (c3[0] - c1[0], c3[1] - c1[1]);
    let sd = |p: &Pt| (p[0] - c1[0]) * dy - (p[1] - c1[1]) * dx;
    let c2 = *pts.iter().max_by(|a, b| sd(a).total_cmp(&sd(b)))?;
    let c4 = *pts.iter().min_by(|a, b| sd(a).total_cmp(&sd(b)))?;
    let mut q = [c1, c2, c3, c4];
    let qc = [(q[0][0] + q[1][0] + q[2][0] + q[3][0]) / 4.0, (q[0][1] + q[1][1] + q[2][1] + q[3][1]) / 4.0];
    q.sort_by(|a, b| (a[1] - qc[1]).atan2(a[0] - qc[0]).total_cmp(&(b[1] - qc[1]).atan2(b[0] - qc[0])));
    Some(q)
}

/// Bordes subpíxel: a lo largo de la normal de cada lado busca el cruce del nivel medio; ajusta rectas (TLS) y
/// las intersecta.
fn refine_quad(cont: &FloatMap, q: &[Pt; 4]) -> Option<[Pt; 4]> {
    let mut lines = Vec::with_capacity(4);
    for i in 0..4 {
        let (a, b) = (q[i], q[(i + 1) % 4]);
        let len = dist(a, b);
        let d = [(b[0] - a[0]) / len, (b[1] - a[1]) / len];
        let nrm = [d[1], -d[0]]; // hacia afuera en orden horario (y hacia abajo)
        let ns = ((len / 1.5) as usize).clamp(6, 60);
        let mut edge: Vec<Pt> = Vec::new();
        for k in 0..ns {
            let t = 0.15 + 0.7 * k as f64 / (ns - 1) as f64;
            let base = [a[0] + d[0] * len * t, a[1] + d[1] * len * t];
            let prof: Vec<(f64, f64)> = (0..=24)
                .filter_map(|j| {
                    let s = -3.0 + j as f64 * 0.25;
                    cont.sample(base[0] + nrm[0] * s, base[1] + nrm[1] * s).map(|v| (s, v))
                })
                .collect();
            if prof.len() < 20 {
                continue;
            }
            let hi = prof.iter().map(|p| p.1).fold(f64::NEG_INFINITY, f64::max);
            let lo = prof.iter().map(|p| p.1).fold(f64::INFINITY, f64::min);
            if hi - lo < 4.0 {
                continue;
            }
            let mid = (hi + lo) / 2.0;
            // último cruce descendente (de tinta a fondo) yendo hacia afuera
            let mut found = None;
            for w in prof.windows(2) {
                if w[0].1 >= mid && w[1].1 < mid {
                    let f = (w[0].1 - mid) / (w[0].1 - w[1].1);
                    found = Some(w[0].0 + f * (w[1].0 - w[0].0));
                }
            }
            if let Some(s) = found {
                edge.push([base[0] + nrm[0] * s, base[1] + nrm[1] * s]);
            }
        }
        if edge.len() < 4 {
            return None;
        }
        lines.push(fit_line(&edge)?);
    }
    let mut out = [[0.0; 2]; 4];
    for i in 0..4 {
        out[i] = intersect(lines[(i + 3) % 4], lines[i])?;
        if dist(out[i], q[i]) > 4.0 {
            return None;
        }
    }
    Some(out)
}

/// Recta `n·p = c` por mínimos cuadrados totales.
fn fit_line(p: &[Pt]) -> Option<([f64; 2], f64)> {
    let n = p.len() as f64;
    let (mx, my) = p.iter().fold((0.0, 0.0), |a, q| (a.0 + q[0] / n, a.1 + q[1] / n));
    let (mut sxx, mut sxy, mut syy) = (0.0, 0.0, 0.0);
    for q in p {
        let (dx, dy) = (q[0] - mx, q[1] - my);
        sxx += dx * dx;
        sxy += dx * dy;
        syy += dy * dy;
    }
    // normal = autovector del menor autovalor de la covarianza
    let theta = 0.5 * (2.0 * sxy).atan2(sxx - syy);
    let nrm = [-theta.sin(), theta.cos()];
    Some((nrm, nrm[0] * mx + nrm[1] * my))
}

fn intersect(a: ([f64; 2], f64), b: ([f64; 2], f64)) -> Option<Pt> {
    let det = a.0[0] * b.0[1] - a.0[1] * b.0[0];
    if det.abs() < 1e-9 {
        return None;
    }
    Some([(a.1 * b.0[1] - a.0[1] * b.1) / det, (a.0[0] * b.1 - a.1 * b.0[0]) / det])
}

fn canonical() -> [Pt; 4] {
    let s = CELLS as f64;
    [[0.0, 0.0], [s, 0.0], [s, s], [0.0, s]]
}

fn decode(cont: &FloatMap, q: &[Pt; 4]) -> Option<Detection> {
    let dict = dictionary();
    for shift in 0..4 {
        let qs: [Pt; 4] = std::array::from_fn(|i| q[(i + shift) % 4]);
        let Some(h) = dlt(&canonical(), &qs) else { continue };
        let Some(vals) = sample_cells(cont, &h) else { continue };
        // nivel: entre la media del anillo (tinta) y el fondo justo afuera
        let ring: Vec<f64> =
            (0..CELLS).flat_map(|i| [vals[0][i], vals[CELLS - 1][i], vals[i][0], vals[i][CELLS - 1]]).collect();
        let ring_mean = ring.iter().sum::<f64>() / ring.len() as f64;
        let outside = sample_outside(cont, &h)?;
        let level = (ring_mean + outside) / 2.0;
        if ring_mean - outside < 4.0 || ring.iter().filter(|v| **v < level).count() > 2 {
            return None;
        }
        let mut code = 0u16;
        for r in 0..4 {
            for c in 0..4 {
                if vals[r + 1][c + 1] > level {
                    code |= 1 << (r * 4 + c);
                }
            }
        }
        if let Some((id, e)) = dict.iter().enumerate().map(|(i, d)| (i, (d ^ code).count_ones())).min_by_key(|x| x.1) {
            if e <= 1 {
                return Some(Detection { id, corners: qs, bit_errors: e });
            }
        }
    }
    None
}

fn sample_cells(cont: &FloatMap, h: &Mat3) -> Option<[[f64; CELLS]; CELLS]> {
    let mut v = [[0.0; CELLS]; CELLS];
    for (r, row) in v.iter_mut().enumerate() {
        for (c, val) in row.iter_mut().enumerate() {
            let mut acc = 0.0;
            for (ox, oy) in [(0.5, 0.5), (0.3, 0.3), (0.7, 0.3), (0.3, 0.7), (0.7, 0.7)] {
                let p = apply(h, [c as f64 + ox, r as f64 + oy]);
                acc += cont.sample(p[0], p[1])?;
            }
            *val = acc / 5.0;
        }
    }
    Some(v)
}

fn sample_outside(cont: &FloatMap, h: &Mat3) -> Option<f64> {
    let s = CELLS as f64;
    let mut acc = Vec::new();
    for k in 0..CELLS {
        let t = k as f64 + 0.5;
        for p in [[t, -0.5], [t, s + 0.5], [-0.5, t], [s + 0.5, t]] {
            let q = apply(h, p);
            if let Some(v) = cont.sample(q[0], q[1]) {
                acc.push(v);
            }
        }
    }
    if acc.len() < 8 {
        return None;
    }
    acc.sort_by(|a, b| a.total_cmp(b));
    Some(acc[acc.len() / 2])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dictionary_is_large_and_separated() {
        let d = dictionary();
        assert_eq!(d.len(), DICT_SIZE);
        for (i, a) in d.iter().enumerate() {
            for b in &d[i + 1..] {
                assert!((a ^ b).count_ones() >= 4);
            }
        }
    }

    #[test]
    fn detects_synthetic_grid_exactly() {
        let g = MarkerGrid::new((640, 400), 5, 3, 10);
        let img = g.rasterize(false);
        let cont = FloatMap { width: 640, height: 400, data: img.data.iter().map(|&v| v as f32).collect() };
        let ink: Vec<bool> = img.data.iter().map(|&v| v > 127).collect();
        let det = detect(&ink, &cont, 20.0);
        assert_eq!(det.len(), 15);
        for d in det {
            let c = g.corners(d.id).unwrap();
            for (a, b) in c.iter().zip(&d.corners) {
                assert!(dist(*a, *b) < 0.3, "{a:?} vs {b:?}");
            }
        }
    }
}
