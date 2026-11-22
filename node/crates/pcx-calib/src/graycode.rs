//! Gray code (port de `slscan/graycode.py` de Max + `escaneo_rapido.py` de Daniel).
//!
//! - Patrones: gray binario reflejado, por eje (x y luego y), bit MSB→`skip_bits`, cada uno con su inverso.
//!   Solo se capturan los bits útiles (los finos que la cámara no resuelve ni se proyectan).
//! - Decodificación diferencial (`patrón − inverso`) con **umbral relativo** por píxel (`k_rel` × contraste de los
//!   3 bits más gruesos, como `paso_decodificar` de Max) y auto-skip de bits finos ilegibles.
//! - Limpieza y refinado sub-bloque de Daniel: se descartan píxeles sin ≥ 3 vecinos coherentes y cada píxel pasa a
//!   ser el promedio de sus vecinos 5x5 coherentes.
//! - Convención de centros enteros: el bloque `b` de `s` px tiene su centro en `b·s + (s−1)/2`.

use crate::image::{percentile, FloatMap, GrayImage};

pub const AXIS_X: u8 = 0;
pub const AXIS_Y: u8 = 1;

pub fn bits_for(n: u32) -> u8 {
    (32 - (n.max(2) - 1).leading_zeros()) as u8
}

pub fn gray2bin(mut g: u32) -> u32 {
    let mut s = 1;
    while s < 32 {
        g ^= g >> s;
        s <<= 1;
    }
    g
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize)]
pub struct GraySpec {
    pub axis: u8,
    pub bit: u8,
    pub inverse: bool,
}

/// Secuencia de captura: eje x luego y, bit MSB→`skip_bits`, patrón e inverso.
pub fn sequence(proj: (u32, u32), skip_bits: (u8, u8)) -> Vec<GraySpec> {
    let mut v = Vec::new();
    for (axis, n, skip) in [(AXIS_X, proj.0, skip_bits.0), (AXIS_Y, proj.1, skip_bits.1)] {
        for bit in (skip..bits_for(n)).rev() {
            v.push(GraySpec { axis, bit, inverse: false });
            v.push(GraySpec { axis, bit, inverse: true });
        }
    }
    v
}

pub fn rasterize(spec: GraySpec, proj: (u32, u32)) -> GrayImage {
    let (w, h) = proj;
    let mut img = GrayImage::new(w, h, 0);
    let on = |c: u32| ((c ^ (c >> 1)) >> spec.bit & 1 == 1) != spec.inverse;
    if spec.axis == AXIS_X {
        let line: Vec<u8> = (0..w).map(|x| if on(x) { 255 } else { 0 }).collect();
        for y in 0..h {
            img.data[(y * w) as usize..((y + 1) * w) as usize].copy_from_slice(&line);
        }
    } else {
        for y in 0..h {
            let v = if on(y) { 255 } else { 0 };
            img.data[(y * w) as usize..((y + 1) * w) as usize].fill(v);
        }
    }
    img
}

#[derive(Debug, Clone)]
pub struct DecodeParams {
    /// Umbral relativo: |P − I| > k_rel · contraste de referencia del píxel.
    pub k_rel: f32,
    /// Umbral absoluto mínimo (DN).
    pub abs_min: f32,
    /// Fracción mínima de píxeles iluminados legibles para conservar un bit fino.
    pub min_frac: f32,
    /// Rectángulos del proyector `[x0, y0, x1, y1)` que no se decodifican (parches de sincronía).
    pub exclude: Vec<[u32; 4]>,
    pub refine: bool,
}

impl Default for DecodeParams {
    fn default() -> Self {
        Self { k_rel: 0.22, abs_min: 3.0, min_frac: 0.72, exclude: Vec::new(), refine: true }
    }
}

/// Mapa denso cámara → proyector.
#[derive(Debug, Clone)]
pub struct Decoded {
    pub cam_size: (u32, u32),
    pub proj_size: (u32, u32),
    /// Bits omitidos por eje (bloque = 2^skip px del proyector).
    pub skip: (u8, u8),
    /// Bloque entero por píxel de cámara (−1 = inválido), antes del refinado.
    pub block: Vec<[i32; 2]>,
    /// Coordenada del proyector refinada (NaN = inválido).
    pub proj: Vec<[f32; 2]>,
    pub stats: DecodeStats,
}

#[derive(Debug, Clone, Default, serde::Serialize)]
pub struct DecodeStats {
    pub lit: usize,
    pub valid_raw: usize,
    pub valid: usize,
    pub legible_x: Vec<(u8, f32)>,
    pub legible_y: Vec<(u8, f32)>,
}

impl Decoded {
    pub fn step(&self) -> (u32, u32) {
        (1 << self.skip.0, 1 << self.skip.1)
    }
}

/// Decodifica pares `(spec, cuadro)`. Debe haber patrón e inverso para cada bit de `captured_skip..top` por eje.
pub fn decode(
    frames: &[(GraySpec, &GrayImage)],
    proj: (u32, u32),
    captured_skip: (u8, u8),
    p: &DecodeParams,
) -> Option<Decoded> {
    let (w, h) = (frames.first()?.1.width, frames.first()?.1.height);
    let n = (w * h) as usize;
    let top = (bits_for(proj.0), bits_for(proj.1));
    let find = |axis: u8, bit: u8, inv: bool| {
        frames.iter().find(|(s, _)| s.axis == axis && s.bit == bit && s.inverse == inv).map(|f| f.1)
    };
    // diferencias por (eje, bit)
    let mut diffs: Vec<Vec<FloatMap>> = vec![Vec::new(), Vec::new()];
    for axis in [AXIS_X, AXIS_Y] {
        let (sk, tp) = if axis == AXIS_X { (captured_skip.0, top.0) } else { (captured_skip.1, top.1) };
        for bit in sk..tp {
            let (a, b) = (find(axis, bit, false)?, find(axis, bit, true)?);
            diffs[axis as usize].push(crate::image::diff(a, b));
        }
    }
    // contraste de referencia: mediana de |diff| de los 3 bits más gruesos de cada eje
    let mut refc = vec![0f32; n];
    let mut tmp = Vec::with_capacity(6);
    for (i, r) in refc.iter_mut().enumerate() {
        tmp.clear();
        for d in &diffs {
            for m in d.iter().rev().take(3) {
                tmp.push(m.data[i].abs());
            }
        }
        tmp.sort_by(|a, b| a.total_cmp(b));
        *r = tmp[tmp.len() / 2];
    }
    let p995 = percentile(&refc, 99.5);
    let lit_thr = 6f32.max(0.12 * p995);
    let lit: Vec<bool> = refc.iter().map(|&r| r > lit_thr).collect();
    let n_lit = lit.iter().filter(|x| **x).count().max(1);
    let thr: Vec<f32> = refc.iter().map(|&r| p.abs_min.max(p.k_rel * r)).collect();
    // auto-skip por eje
    let mut skip = [captured_skip.0, captured_skip.1];
    let mut legible: [Vec<(u8, f32)>; 2] = [Vec::new(), Vec::new()];
    for axis in 0..2 {
        let base = skip[axis];
        let topb = if axis == 0 { top.0 } else { top.1 };
        for (k, m) in diffs[axis].iter().enumerate() {
            let fr = (0..n).filter(|&i| lit[i] && m.data[i].abs() > thr[i]).count() as f32 / n_lit as f32;
            legible[axis].push((base + k as u8, fr));
        }
        while skip[axis] + 3 < topb && legible[axis][(skip[axis] - base) as usize].1 < p.min_frac {
            skip[axis] += 1;
        }
    }
    let mut block = vec![[-1i32; 2]; n];
    let mut valid = lit.clone();
    for axis in 0..2 {
        let base = if axis == 0 { captured_skip.0 } else { captured_skip.1 };
        let mut g = vec![0u32; n];
        for (k, m) in diffs[axis].iter().enumerate() {
            let bit = base + k as u8;
            if bit < skip[axis] {
                continue;
            }
            for i in 0..n {
                if m.data[i] > 0.0 {
                    g[i] |= 1 << bit;
                }
                if m.data[i].abs() <= thr[i] {
                    valid[i] = false;
                }
            }
        }
        let size = if axis == 0 { proj.0 } else { proj.1 };
        for i in 0..n {
            let b = (gray2bin(g[i]) >> skip[axis]) as i32;
            let s = 1i32 << skip[axis];
            if b * s >= size as i32 {
                valid[i] = false;
            }
            block[i][axis] = b;
        }
    }
    let step = (1u32 << skip[0], 1u32 << skip[1]);
    let center = |b: i32, s: u32| b as f32 * s as f32 + (s as f32 - 1.0) / 2.0;
    for i in 0..n {
        if valid[i] {
            let (x, y) = (center(block[i][0], step.0), center(block[i][1], step.1));
            if p.exclude.iter().any(|r| x >= r[0] as f32 && x < r[2] as f32 && y >= r[1] as f32 && y < r[3] as f32) {
                valid[i] = false;
            }
        }
        if !valid[i] {
            block[i] = [-1, -1];
        }
    }
    let valid_raw = valid.iter().filter(|x| **x).count();
    // limpieza: ≥ 3 vecinos 3x3 coherentes (±2 bloques)
    let (wi, hi) = (w as i64, h as i64);
    fn coherent(block: &[[i32; 2]], i: usize, j: usize, tol: i32) -> bool {
        block[j][0] >= 0 && (block[j][0] - block[i][0]).abs() <= tol && (block[j][1] - block[i][1]).abs() <= tol
    }
    let mut keep = valid.clone();
    for y in 0..hi {
        for x in 0..wi {
            let i = (y * wi + x) as usize;
            if !valid[i] {
                continue;
            }
            let mut c = 0;
            for dy in -1..=1 {
                for dx in -1..=1 {
                    let (nx, ny) = (x + dx, y + dy);
                    if (dx != 0 || dy != 0)
                        && nx >= 0
                        && ny >= 0
                        && nx < wi
                        && ny < hi
                        && coherent(&block, i, (ny * wi + nx) as usize, 2)
                    {
                        c += 1;
                    }
                }
            }
            keep[i] = c >= 3;
        }
    }
    for i in 0..n {
        if !keep[i] {
            block[i] = [-1, -1];
        }
    }
    // refinado sub-bloque: promedio de vecinos 5x5 coherentes (±1 bloque)
    let mut proj_xy = vec![[f32::NAN; 2]; n];
    for y in 0..hi {
        for x in 0..wi {
            let i = (y * wi + x) as usize;
            if block[i][0] < 0 {
                continue;
            }
            let (mut sx, mut sy, mut c) = (0f32, 0f32, 0f32);
            let r = if p.refine { 2 } else { 0 };
            for dy in -r..=r {
                for dx in -r..=r {
                    let (nx, ny) = (x + dx, y + dy);
                    if nx < 0 || ny < 0 || nx >= wi || ny >= hi {
                        continue;
                    }
                    let j = (ny * wi + nx) as usize;
                    if coherent(&block, i, j, 1) {
                        sx += center(block[j][0], step.0);
                        sy += center(block[j][1], step.1);
                        c += 1.0;
                    }
                }
            }
            proj_xy[i] = [sx / c, sy / c];
        }
    }
    let valid_n = block.iter().filter(|b| b[0] >= 0).count();
    Some(Decoded {
        cam_size: (w, h),
        proj_size: proj,
        skip: (skip[0], skip[1]),
        block,
        proj: proj_xy,
        stats: DecodeStats {
            lit: n_lit,
            valid_raw,
            valid: valid_n,
            legible_x: legible[0].clone(),
            legible_y: legible[1].clone(),
        },
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn identity_camera_decodes_exactly() {
        let proj = (256, 128);
        let specs = sequence(proj, (0, 0));
        assert_eq!(specs.len(), 2 * (8 + 7));
        let imgs: Vec<GrayImage> = specs.iter().map(|s| rasterize(*s, proj)).collect();
        let frames: Vec<(GraySpec, &GrayImage)> = specs.iter().copied().zip(imgs.iter()).collect();
        let d = decode(&frames, proj, (0, 0), &DecodeParams { refine: false, ..Default::default() }).unwrap();
        for &(x, y) in &[(0u32, 0u32), (17, 33), (255, 127), (100, 64)] {
            let i = (y * proj.0 + x) as usize;
            assert_eq!(d.block[i], [x as i32, y as i32]);
        }
    }
}
