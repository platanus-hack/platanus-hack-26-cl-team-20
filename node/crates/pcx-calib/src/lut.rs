//! LUT densa proyector → cámara sin `griddata`: coalesce por bloque del gray code (rejilla regular en el
//! proyector), relleno de agujeros chicos y bilineal por bloques que no mezcla a través de saltos de profundidad.
//! También el «lienzo rectificado» (polinomio cúbico robusto proyector → cámara: corrige lente, ignora bultos).

use std::io::Write;
use std::path::Path;

use nalgebra::{DMatrix, DVector};
use sha2::{Digest, Sha256};

use crate::graycode::Decoded;
use crate::homography::Pt;
use crate::image::percentile;

/// Muestras proyector → cámara en la rejilla de bloques del gray code.
#[derive(Debug, Clone)]
pub struct BlockGrid {
    pub gx: usize,
    pub gy: usize,
    pub step: (u32, u32),
    /// Cámara (x, y) por bloque; NaN = sin dato.
    pub cam: Vec<[f32; 2]>,
    /// Paso típico entre bloques vecinos en px de cámara.
    pub cam_step: f32,
}

impl BlockGrid {
    /// Coalesce: centroide de los píxeles de cámara que decodificaron cada bloque (`min_count` mínimo).
    pub fn from_decoded(d: &Decoded, min_count: u32) -> Self {
        let step = d.step();
        let gx = d.proj_size.0.div_ceil(step.0) as usize;
        let gy = d.proj_size.1.div_ceil(step.1) as usize;
        let mut acc = vec![(0f64, 0f64, 0u32); gx * gy];
        let w = d.cam_size.0 as usize;
        for (i, b) in d.block.iter().enumerate() {
            if b[0] < 0 || b[0] as usize >= gx || b[1] as usize >= gy {
                continue;
            }
            let k = b[1] as usize * gx + b[0] as usize;
            acc[k].0 += (i % w) as f64;
            acc[k].1 += (i / w) as f64;
            acc[k].2 += 1;
        }
        let cam: Vec<[f32; 2]> = acc
            .iter()
            .map(|&(sx, sy, c)| if c >= min_count { [(sx / c as f64) as f32, (sy / c as f64) as f32] } else { [f32::NAN; 2] })
            .collect();
        let mut g = Self { gx, gy, step, cam, cam_step: 1.0 };
        g.cam_step = g.typical_step();
        g
    }

    fn typical_step(&self) -> f32 {
        let mut d = Vec::new();
        for j in 0..self.gy {
            for i in 0..self.gx.saturating_sub(1) {
                let (a, b) = (self.cam[j * self.gx + i], self.cam[j * self.gx + i + 1]);
                if a[0].is_finite() && b[0].is_finite() {
                    d.push(((a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2)).sqrt());
                }
            }
        }
        let m = percentile(&d, 50.0);
        if m.is_finite() && m > 0.0 {
            m
        } else {
            1.0
        }
    }

    pub fn center(&self, i: usize, j: usize) -> Pt {
        let c = |b: usize, s: u32| b as f64 * s as f64 + (s as f64 - 1.0) / 2.0;
        [c(i, self.step.0), c(j, self.step.1)]
    }

    /// Rellena agujeros chicos: pasadas en las que un bloque vacío con ≥ 3 vecinos válidos toma su promedio.
    pub fn fill_holes(&mut self, passes: usize) -> usize {
        let mut filled = 0;
        for _ in 0..passes {
            let prev = self.cam.clone();
            let mut any = false;
            for j in 0..self.gy {
                for i in 0..self.gx {
                    let k = j * self.gx + i;
                    if prev[k][0].is_finite() {
                        continue;
                    }
                    let (mut sx, mut sy, mut c) = (0f32, 0f32, 0);
                    for dj in -1i64..=1 {
                        for di in -1i64..=1 {
                            let (ni, nj) = (i as i64 + di, j as i64 + dj);
                            if (di == 0 && dj == 0) || ni < 0 || nj < 0 || ni >= self.gx as i64 || nj >= self.gy as i64 {
                                continue;
                            }
                            let v = prev[nj as usize * self.gx + ni as usize];
                            if v[0].is_finite() {
                                sx += v[0];
                                sy += v[1];
                                c += 1;
                            }
                        }
                    }
                    if c >= 3 {
                        self.cam[k] = [sx / c as f32, sy / c as f32];
                        filled += 1;
                        any = true;
                    }
                }
            }
            if !any {
                break;
            }
        }
        filled
    }

    /// Cámara para un punto del proyector: bilineal entre los 4 bloques vecinos; si hay salto de profundidad
    /// (los vecinos se separan > 2,5 pasos típicos) usa el bloque más cercano.
    pub fn sample(&self, x: f64, y: f64) -> Option<Pt> {
        let fx = (x - (self.step.0 as f64 - 1.0) / 2.0) / self.step.0 as f64;
        let fy = (y - (self.step.1 as f64 - 1.0) / 2.0) / self.step.1 as f64;
        let i0 = (fx.floor() as i64).clamp(0, self.gx as i64 - 2) as usize;
        let j0 = (fy.floor() as i64).clamp(0, self.gy as i64 - 2) as usize;
        let (tx, ty) = ((fx - i0 as f64).clamp(-0.5, 1.5), (fy - j0 as f64).clamp(-0.5, 1.5));
        let corners = [(i0, j0, (1.0 - tx) * (1.0 - ty)), (i0 + 1, j0, tx * (1.0 - ty)), (i0, j0 + 1, (1.0 - tx) * ty), (i0 + 1, j0 + 1, tx * ty)];
        // (cámara, peso, distancia en bloques) de los vecinos con dato, sin asignar memoria
        let mut vals = [([0f32; 2], 0f64, 0f64); 4];
        let mut n = 0;
        for &(i, j, w) in &corners {
            let c = self.cam[j * self.gx + i];
            if c[0].is_finite() {
                vals[n] = (c, w, ((i as f64 - fx).powi(2) + (j as f64 - fy).powi(2)).sqrt());
                n += 1;
            }
        }
        let vals = &vals[..n];
        let nearest = vals.iter().min_by(|a, b| a.2.total_cmp(&b.2))?;
        if nearest.2 > 1.0 {
            return None;
        }
        // salto de profundidad: dos vecinos más separados que 2,5 pasos típicos (en diagonal)
        let lim = (2.5 * std::f32::consts::SQRT_2 * self.cam_step).powi(2);
        let jump = vals.iter().any(|a| vals.iter().any(|b| (a.0[0] - b.0[0]).powi(2) + (a.0[1] - b.0[1]).powi(2) > lim));
        let ws: f64 = vals.iter().map(|v| v.1).sum();
        if jump || ws < 0.25 {
            return Some([nearest.0[0] as f64, nearest.0[1] as f64]);
        }
        let (sx, sy) = vals.iter().fold((0.0, 0.0), |a, v| (a.0 + v.0[0] as f64 * v.1, a.1 + v.0[1] as f64 * v.1));
        Some([sx / ws, sy / ws])
    }

    /// Pares (proyector, cámara) de los bloques con dato original.
    pub fn pairs(&self) -> (Vec<Pt>, Vec<Pt>) {
        let mut p = Vec::new();
        let mut c = Vec::new();
        for j in 0..self.gy {
            for i in 0..self.gx {
                let v = self.cam[j * self.gx + i];
                if v[0].is_finite() {
                    p.push(self.center(i, j));
                    c.push([v[0] as f64, v[1] as f64]);
                }
            }
        }
        (p, c)
    }
}

/// LUT densa `H×W×2` float32 (convención `integer_centers` del contrato), NaN = sin dato.
#[derive(Debug, Clone)]
pub struct DenseLut {
    pub width: u32,
    pub height: u32,
    pub grid_step: u32,
    pub data: Vec<f32>,
}

impl DenseLut {
    pub fn build(grid: &BlockGrid, proj: (u32, u32), grid_step: u32) -> Self {
        let (w, h) = (proj.0.div_ceil(grid_step), proj.1.div_ceil(grid_step));
        let mut data = vec![f32::NAN; (w * h * 2) as usize];
        for v in 0..h {
            for u in 0..w {
                if let Some(c) = grid.sample((u * grid_step) as f64, (v * grid_step) as f64) {
                    let k = ((v * w + u) * 2) as usize;
                    data[k] = c[0] as f32;
                    data[k + 1] = c[1] as f32;
                }
            }
        }
        Self { width: w, height: h, grid_step, data }
    }

    pub fn get(&self, u: u32, v: u32) -> Option<Pt> {
        let k = ((v * self.width + u) * 2) as usize;
        let (x, y) = (self.data[k], self.data[k + 1]);
        (x.is_finite() && y.is_finite()).then_some([x as f64, y as f64])
    }

    pub fn coverage(&self) -> f64 {
        let n = self.data.len() / 2;
        (0..n).filter(|&i| self.data[2 * i].is_finite()).count() as f64 / n as f64
    }

    /// Bytes del `.npy` (v1.0, `<f4`, C-order, forma `(H, W, 2)`).
    pub fn npy_bytes(&self) -> Vec<u8> {
        let dict = format!("{{'descr': '<f4', 'fortran_order': False, 'shape': ({}, {}, 2), }}", self.height, self.width);
        let mut header = dict.into_bytes();
        let total = 10 + header.len() + 1;
        let pad = (64 - total % 64) % 64;
        header.extend(std::iter::repeat_n(b' ', pad));
        header.push(b'\n');
        let mut out = Vec::with_capacity(10 + header.len() + self.data.len() * 4);
        out.extend_from_slice(b"\x93NUMPY\x01\x00");
        out.extend_from_slice(&(header.len() as u16).to_le_bytes());
        out.extend_from_slice(&header);
        for v in &self.data {
            out.extend_from_slice(&v.to_le_bytes());
        }
        out
    }

    /// Escribe el `.npy` de forma atómica y devuelve su sha256 (hex).
    pub fn write_npy(&self, path: &Path) -> std::io::Result<String> {
        let bytes = self.npy_bytes();
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir)?;
        }
        let tmp = path.with_extension("npy.tmp");
        let mut f = std::fs::File::create(&tmp)?;
        f.write_all(&bytes)?;
        f.sync_all()?;
        std::fs::rename(&tmp, path)?;
        Ok(hex(&Sha256::digest(&bytes)))
    }
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

/// Polinomio cúbico 2D (10 términos por coordenada) con entrada normalizada.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct Poly2 {
    pub center: Pt,
    pub scale: f64,
    pub cx: Vec<f64>,
    pub cy: Vec<f64>,
}

fn terms(x: f64, y: f64) -> [f64; 10] {
    [1.0, x, y, x * x, x * y, y * y, x * x * x, x * x * y, x * y * y, y * y * y]
}

impl Poly2 {
    pub fn eval(&self, p: Pt) -> Pt {
        let t = terms((p[0] - self.center[0]) / self.scale, (p[1] - self.center[1]) / self.scale);
        let dot = |c: &[f64]| c.iter().zip(&t).map(|(a, b)| a * b).sum::<f64>();
        [dot(&self.cx), dot(&self.cy)]
    }

    /// Ajuste robusto (3 rondas, descarta residuos > max(floor, 3·1,4826·MAD)): los bultos de objetos quedan fuera.
    pub fn fit_robust(src: &[Pt], dst: &[Pt], floor: f64) -> Option<Self> {
        if src.len() < 20 {
            return None;
        }
        let n = src.len() as f64;
        let center = src.iter().fold([0.0, 0.0], |a, p| [a[0] + p[0] / n, a[1] + p[1] / n]);
        let scale = src.iter().map(|p| (p[0] - center[0]).abs().max((p[1] - center[1]).abs())).fold(1.0, f64::max);
        let mut keep = vec![true; src.len()];
        let mut model = None;
        for _ in 0..4 {
            let idx: Vec<usize> = (0..src.len()).filter(|&i| keep[i]).collect();
            if idx.len() < 20 {
                break;
            }
            let mut a = DMatrix::<f64>::zeros(idx.len(), 10);
            let mut bx = DVector::<f64>::zeros(idx.len());
            let mut by = DVector::<f64>::zeros(idx.len());
            for (r, &i) in idx.iter().enumerate() {
                let t = terms((src[i][0] - center[0]) / scale, (src[i][1] - center[1]) / scale);
                for (c, v) in t.iter().enumerate() {
                    a[(r, c)] = *v;
                }
                bx[r] = dst[i][0];
                by[r] = dst[i][1];
            }
            let svd = a.svd(true, true);
            let cx = svd.solve(&bx, 1e-12).ok()?;
            let cy = svd.solve(&by, 1e-12).ok()?;
            let m = Poly2 { center, scale, cx: cx.iter().copied().collect(), cy: cy.iter().copied().collect() };
            let res: Vec<f64> = src.iter().zip(dst).map(|(s, d)| {
                let e = m.eval(*s);
                ((e[0] - d[0]).powi(2) + (e[1] - d[1]).powi(2)).sqrt()
            }).collect();
            let kept: Vec<f32> = idx.iter().map(|&i| res[i] as f32).collect();
            let med = percentile(&kept, 50.0) as f64;
            let mad: Vec<f32> = kept.iter().map(|r| (*r as f64 - med).abs() as f32).collect();
            let lim = floor.max(med + 3.0 * 1.4826 * percentile(&mad, 50.0) as f64);
            keep = res.iter().map(|r| *r < lim).collect();
            model = Some(m);
        }
        model
    }
}
