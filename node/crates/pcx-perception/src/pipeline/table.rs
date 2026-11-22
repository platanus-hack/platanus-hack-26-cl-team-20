//! Modelo de la mesa vacía. «No explicado» es lo que se ve distinto de este modelo y no pertenece a ningún objeto
//! conocido; comparar solo contra la foto anterior no alcanza cuando un objeto se corre encima de su lugar viejo.
//!
//! La primera foto no tiene con qué compararse, así que la mesa se **estima**: polinomio cúbico robusto por canal
//! (absorbe el degradé de la luz y deja fuera los objetos) más una corrección local suave con los píxeles que sí
//! son mesa. Debajo de un objeto es una estimación; cuando el objeto se va, se reemplaza por lo que se ve.

use super::img::{self, Region};
use image::RgbImage;

pub struct Table {
    pub w: usize,
    pub h: usize,
    /// Color de la mesa vacía por píxel.
    pub rgb: Vec<u8>,
    /// 255 donde la mesa se vio alguna vez; 0 donde solo es una estimación (había un objeto en la primera foto).
    pub seen: Vec<u8>,
}

const TERMS: usize = 10;

fn basis(u: f64, v: f64) -> [f64; TERMS] {
    [1.0, u, v, u * u, u * v, v * v, u * u * u, u * u * v, u * v * v, v * v * v]
}

/// Resuelve `A x = b` (simétrica definida positiva tras el término de regularización) por eliminación gaussiana.
fn solve(mut a: [[f64; TERMS]; TERMS], mut b: [f64; TERMS]) -> Option<[f64; TERMS]> {
    for c in 0..TERMS {
        let piv = (c..TERMS).max_by(|&i, &j| a[i][c].abs().total_cmp(&a[j][c].abs()))?;
        if a[piv][c].abs() < 1e-12 {
            return None;
        }
        a.swap(c, piv);
        b.swap(c, piv);
        for r in c + 1..TERMS {
            let f = a[r][c] / a[c][c];
            if f != 0.0 {
                for k in c..TERMS {
                    a[r][k] -= f * a[c][k];
                }
                b[r] -= f * b[c];
            }
        }
    }
    for c in (0..TERMS).rev() {
        for k in c + 1..TERMS {
            b[c] -= a[c][k] * b[k];
        }
        b[c] /= a[c][c];
    }
    Some(b)
}

/// Suma de caja de radio `r` sobre una grilla `w × h` (imagen integral).
fn box_sum(src: &[f32], w: usize, h: usize, r: usize) -> Vec<f32> {
    let mut integral = vec![0f64; (w + 1) * (h + 1)];
    for y in 0..h {
        let mut row = 0f64;
        for x in 0..w {
            row += src[y * w + x] as f64;
            integral[(y + 1) * (w + 1) + x + 1] = integral[y * (w + 1) + x + 1] + row;
        }
    }
    let mut out = vec![0f32; w * h];
    for y in 0..h {
        let (y0, y1) = (y.saturating_sub(r), (y + r + 1).min(h));
        for x in 0..w {
            let (x0, x1) = (x.saturating_sub(r), (x + r + 1).min(w));
            out[y * w + x] = (integral[y1 * (w + 1) + x1] - integral[y0 * (w + 1) + x1] - integral[y1 * (w + 1) + x0]
                + integral[y0 * (w + 1) + x0]) as f32;
        }
    }
    out
}

impl Table {
    /// Toma la foto tal cual como mesa (todo lo que ya está queda como fondo).
    pub fn from_frame(rgb: &RgbImage) -> Table {
        let (w, h) = (rgb.width() as usize, rgb.height() as usize);
        Table { w, h, rgb: rgb.as_raw().clone(), seen: vec![255; w * h] }
    }

    /// Estima la mesa vacía de una sola foto. `cell`: lado en px de la celda de trabajo (≈ 4 mm); `thr`: diferencia
    /// (niveles) que ya cuenta como «no es mesa»; `radius_cells`: radio de la corrección local.
    pub fn estimate(rgb: &RgbImage, valid: &[u8], cell: usize, thr: f32, radius_cells: usize) -> Table {
        let (w, h) = (rgb.width() as usize, rgb.height() as usize);
        let cell = cell.max(1);
        let (cw, ch) = (w.div_ceil(cell), h.div_ceil(cell));
        let px = rgb.as_raw();
        // promedio por celda
        let mut mean = vec![[0f32; 3]; cw * ch];
        let mut ok = vec![false; cw * ch];
        for cy in 0..ch {
            for cx in 0..cw {
                let (mut s, mut n) = ([0u32; 3], 0u32);
                for y in cy * cell..((cy + 1) * cell).min(h) {
                    for x in cx * cell..((cx + 1) * cell).min(w) {
                        let i = y * w + x;
                        if valid[i] > 0 {
                            for c in 0..3 {
                                s[c] += px[i * 3 + c] as u32;
                            }
                            n += 1;
                        }
                    }
                }
                let total = (((cy + 1) * cell).min(h) - cy * cell) * (((cx + 1) * cell).min(w) - cx * cell);
                if n > 0 && n as usize * 2 >= total {
                    mean[cy * cw + cx] = s.map(|v| v as f32 / n as f32);
                    ok[cy * cw + cx] = true;
                }
            }
        }
        // polinomio cúbico robusto (mínimos cuadrados reponderados, Tukey), pesos comunes a los tres canales
        let scale = cw.max(ch) as f64 / 2.0;
        let coord = |cx: usize, cy: usize| {
            ((cx as f64 + 0.5 - cw as f64 / 2.0) / scale, (cy as f64 + 0.5 - ch as f64 / 2.0) / scale)
        };
        let mut weight: Vec<f64> = ok.iter().map(|&o| if o { 1.0 } else { 0.0 }).collect();
        let mut coef = [[0f64; TERMS]; 3];
        let mut res = vec![0f32; cw * ch];
        let mut spread = 2.0f32;
        for _ in 0..10 {
            let mut ata = [[0f64; TERMS]; TERMS];
            let mut atb = [[0f64; TERMS]; 3];
            for cy in 0..ch {
                for cx in 0..cw {
                    let wt = weight[cy * cw + cx];
                    if wt == 0.0 {
                        continue;
                    }
                    let (u, v) = coord(cx, cy);
                    let b = basis(u, v);
                    for i in 0..TERMS {
                        let wb = wt * b[i];
                        for j in i..TERMS {
                            ata[i][j] += wb * b[j];
                        }
                        for c in 0..3 {
                            atb[c][i] += wb * mean[cy * cw + cx][c] as f64;
                        }
                    }
                }
            }
            for i in 0..TERMS {
                for j in 0..i {
                    ata[i][j] = ata[j][i];
                }
                ata[i][i] += 1e-6;
            }
            let mut solved = true;
            for c in 0..3 {
                match solve(ata, atb[c]) {
                    Some(x) => coef[c] = x,
                    None => solved = false,
                }
            }
            if !solved {
                break;
            }
            let mut sorted = vec![];
            for cy in 0..ch {
                for cx in 0..cw {
                    let i = cy * cw + cx;
                    if !ok[i] {
                        continue;
                    }
                    let (u, v) = coord(cx, cy);
                    let b = basis(u, v);
                    let mut worst = 0f32;
                    for c in 0..3 {
                        let pred: f64 = coef[c].iter().zip(&b).map(|(a, b)| a * b).sum();
                        worst = worst.max((mean[i][c] - pred as f32).abs());
                    }
                    res[i] = worst;
                    sorted.push(worst);
                }
            }
            if sorted.is_empty() {
                break;
            }
            sorted.sort_by(f32::total_cmp);
            spread = (1.4826 * sorted[sorted.len() / 2]).max(1.5);
            let cut = 4.685 * spread;
            for i in 0..cw * ch {
                if ok[i] {
                    let q = res[i] / cut;
                    weight[i] = if q < 1.0 { ((1.0 - q * q) * (1.0 - q * q)) as f64 } else { 0.0 };
                }
            }
        }
        let poly = |cx: usize, cy: usize, c: usize| -> f32 {
            let (u, v) = coord(cx, cy);
            coef[c].iter().zip(&basis(u, v)).map(|(a, b)| a * b).sum::<f64>() as f32
        };
        // corrección local: promedio suave del residuo de las celdas que sí son mesa (dos pasadas)
        let cut = (0.4 * thr).max(2.5 * spread);
        let mut corr = vec![[0f32; 3]; cw * ch];
        let prior = 0.08 * ((2 * radius_cells + 1) * (2 * radius_cells + 1)) as f32;
        for _ in 0..2 {
            let mut out = vec![0u8; cw * ch];
            for cy in 0..ch {
                for cx in 0..cw {
                    let i = cy * cw + cx;
                    let far = (0..3).any(|c| (mean[i][c] - poly(cx, cy, c) - corr[i][c]).abs() > cut);
                    if !ok[i] || far {
                        out[i] = 255;
                    }
                }
            }
            let out = img::dilate(&out, cw, ch, 1);
            let wt: Vec<f32> = out.iter().map(|&o| if o > 0 { 0.0 } else { 1.0 }).collect();
            let den = box_sum(&wt, cw, ch, radius_cells);
            for c in 0..3 {
                let num: Vec<f32> = (0..cw * ch)
                    .map(|i| if wt[i] > 0.0 { mean[i][c] - poly(i % cw, i / cw, c) } else { 0.0 })
                    .collect();
                let num = box_sum(&num, cw, ch, radius_cells);
                for i in 0..cw * ch {
                    corr[i][c] = num[i] / (den[i] + prior);
                }
            }
        }
        let cells: Vec<[f32; 3]> =
            (0..cw * ch).map(|i| [0, 1, 2].map(|c| (poly(i % cw, i / cw, c) + corr[i][c]).clamp(0.0, 255.0))).collect();
        // a resolución completa, bilineal entre centros de celda
        let mut out = vec![0u8; w * h * 3];
        for y in 0..h {
            let fy = ((y as f32 + 0.5) / cell as f32 - 0.5).clamp(0.0, (ch - 1) as f32);
            let (y0, ty) = (fy.floor() as usize, fy - fy.floor());
            let y1 = (y0 + 1).min(ch - 1);
            for x in 0..w {
                let fx = ((x as f32 + 0.5) / cell as f32 - 0.5).clamp(0.0, (cw - 1) as f32);
                let (x0, tx) = (fx.floor() as usize, fx - fx.floor());
                let x1 = (x0 + 1).min(cw - 1);
                let (a, b, c, d) = (cells[y0 * cw + x0], cells[y0 * cw + x1], cells[y1 * cw + x0], cells[y1 * cw + x1]);
                for k in 0..3 {
                    let top = a[k] * (1.0 - tx) + b[k] * tx;
                    let bot = c[k] * (1.0 - tx) + d[k] * tx;
                    out[(y * w + x) * 3 + k] = (top * (1.0 - ty) + bot * ty).round() as u8;
                }
            }
        }
        Table { w, h, rgb: out, seen: vec![0; w * h] }
    }

    /// Ganancia por canal entre la foto y el modelo (mediana sobre una muestra de píxeles con `ok` encendido).
    pub fn gains(&self, rgb: &RgbImage, ok: &[u8]) -> [f32; 3] {
        let px = rgb.as_raw();
        let mut hist = [[0u32; 1024]; 3];
        let mut n = 0u32;
        let mut i = 0;
        while i < self.w * self.h {
            if ok[i] > 0 {
                let (c, r) = (&px[i * 3..i * 3 + 3], &self.rgb[i * 3..i * 3 + 3]);
                if r[0] >= 24 && r[1] >= 24 && r[2] >= 24 {
                    for k in 0..3 {
                        hist[k][((c[k] as u32 * 256) / r[k] as u32).min(1023) as usize] += 1;
                    }
                    n += 1;
                }
            }
            i += 7;
        }
        if n < 200 {
            return [1.0; 3];
        }
        hist.map(|h| {
            let (mut acc, mut k) = (0u32, 0usize);
            while k < 1023 && acc + h[k] <= n / 2 {
                acc += h[k];
                k += 1;
            }
            (k as f32 / 256.0).clamp(0.5, 2.0)
        })
    }

    /// Distancia por píxel entre la foto y la mesa: el mayor `|foto − mesa · ganancia|` de los tres canales.
    pub fn distance(&self, rgb: &RgbImage, gains: [f32; 3]) -> Vec<u8> {
        let luts = gains.map(img::gain_lut);
        let mut out = vec![0u8; self.w * self.h];
        for ((o, c), b) in out.iter_mut().zip(rgb.as_raw().chunks_exact(3)).zip(self.rgb.chunks_exact(3)) {
            *o = c[0]
                .abs_diff(luts[0][b[0] as usize])
                .max(c[1].abs_diff(luts[1][b[1] as usize]))
                .max(c[2].abs_diff(luts[2][b[2] as usize]));
        }
        out
    }

    /// Acerca el modelo a la foto donde `skip` está apagado (`alpha` en 1/256) y marca esos píxeles como vistos.
    pub fn learn(&mut self, rgb: &RgbImage, skip: &[u8], alpha: u32) {
        let px = rgb.as_raw();
        for i in 0..self.w * self.h {
            if skip[i] == 0 {
                for k in i * 3..i * 3 + 3 {
                    let (b, c) = (self.rgb[k] as i32, px[k] as i32);
                    self.rgb[k] = (b + ((c - b) * alpha as i32 + 128) / 256) as u8;
                }
                self.seen[i] = 255;
            }
        }
    }

    /// La región ahora muestra mesa: se copia tal cual de la foto.
    pub fn replace(&mut self, rgb: &RgbImage, region: &Region) {
        let px = rgb.as_raw();
        let (rgb_out, seen) = (&mut self.rgb, &mut self.seen);
        region.for_each(self.w, |i| {
            rgb_out[i * 3..i * 3 + 3].copy_from_slice(&px[i * 3..i * 3 + 3]);
            seen[i] = 255;
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use image::Rgb;

    /// Mesa con degradé + un rectángulo oscuro grande y uno claro chico: la estimación debe ignorarlos.
    #[test]
    fn estima_la_mesa_debajo_de_los_objetos() {
        let (w, h) = (400u32, 240u32);
        let mesa = |x: u32, y: u32| {
            let v = 120.0 + 40.0 * x as f32 / w as f32 + 25.0 * (y as f32 / h as f32).powi(2);
            [v + 10.0, v, v - 15.0].map(|q| q.round() as u8)
        };
        let img = RgbImage::from_fn(w, h, |x, y| {
            if (80..200).contains(&x) && (60..150).contains(&y) {
                Rgb([30, 40, 60])
            } else if (300..330).contains(&x) && (180..200).contains(&y) {
                Rgb([250, 250, 250])
            } else {
                Rgb(mesa(x, y))
            }
        });
        let valid = vec![255u8; (w * h) as usize];
        let t = Table::estimate(&img, &valid, 4, 26.0, 12);
        let mut worst = 0u8;
        for y in 0..h {
            for x in 0..w {
                let m = mesa(x, y);
                let i = ((y * w + x) * 3) as usize;
                for c in 0..3 {
                    worst = worst.max(t.rgb[i + c].abs_diff(m[c]));
                }
            }
        }
        assert!(worst <= 6, "error máximo de la mesa estimada: {worst}");
        let d = t.distance(&img, [1.0; 3]);
        assert!(d[(100 * w + 120) as usize] > 60, "el objeto se ve distinto de la mesa");
        assert!(d[(20 * w + 20) as usize] < 8);
        assert!(t.seen.iter().all(|&v| v == 0));
    }

    #[test]
    fn aprende_y_reemplaza() {
        let a = RgbImage::from_pixel(20, 10, Rgb([100, 100, 100]));
        let b = RgbImage::from_pixel(20, 10, Rgb([120, 120, 120]));
        let mut t = Table::from_frame(&a);
        assert!((t.gains(&b, &[255; 200])[0] - 1.2).abs() < 0.02 || t.gains(&b, &[255; 200]) == [1.0; 3]);
        let mut skip = vec![0u8; 200];
        skip[0] = 255;
        t.learn(&b, &skip, 128);
        assert_eq!((t.rgb[0], t.rgb[3]), (100, 110));
        let r = Region::from_mask(&[255u8], 1, 1, 0, 0).unwrap();
        t.replace(&b, &r);
        assert_eq!(t.rgb[0], 120);
    }
}
