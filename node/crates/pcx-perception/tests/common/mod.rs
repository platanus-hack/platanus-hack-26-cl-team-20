//! Ayudas de los tests del pipeline: homografía por 4 puntos, calibraciones y escenas sintéticas.
#![allow(dead_code, clippy::needless_range_loop)]

use image::{Rgb, RgbImage};
use pcx_perception::pipeline_api::{CalibIn, Lens, PerceptionOut};

/// Homografía que lleva `src[i]` a `dst[i]` (4 pares), por filas.
pub fn homography(src: [(f64, f64); 4], dst: [(f64, f64); 4]) -> [f64; 9] {
    let mut a = [[0f64; 9]; 8];
    for i in 0..4 {
        let ((x, y), (u, v)) = (src[i], dst[i]);
        a[2 * i] = [x, y, 1.0, 0.0, 0.0, 0.0, -u * x, -u * y, u];
        a[2 * i + 1] = [0.0, 0.0, 0.0, x, y, 1.0, -v * x, -v * y, v];
    }
    for c in 0..8 {
        let piv = (c..8).max_by(|&i, &j| a[i][c].abs().total_cmp(&a[j][c].abs())).unwrap();
        a.swap(c, piv);
        for r in 0..8 {
            if r != c {
                let f = a[r][c] / a[c][c];
                for k in c..9 {
                    a[r][k] -= f * a[c][k];
                }
            }
        }
    }
    let mut h = [1f64; 9];
    for i in 0..8 {
        h[i] = a[i][8] / a[i][i];
    }
    h
}

/// Calibración de las fotos de `data/mesa/2026-11-21` (homografía manual de 4 puntos del README, sin lente).
pub fn calib_mesa() -> CalibIn {
    let m = homography(
        [(771.0, 602.0), (322.0, 527.0), (352.0, 318.0), (803.0, 400.0)],
        [(100.0, 100.0), (900.0, 100.0), (900.0, 463.0), (100.0, 463.0)],
    );
    CalibIn { camera_px: (1280, 720), matrix_px_to_mm: m, lens: None, surface_mm: (1000.0, 563.0) }
}

/// Cámara sintética que mira la superficie de frente: `scale` px por mm, con margen de `off` px.
pub fn calib_plana(cam: (u32, u32), surface: (f64, f64), scale: f64, off: (f64, f64), lens: Option<Lens>) -> CalibIn {
    // mm = (px − off) / scale, con el centro del píxel en coordenada entera
    let m = [1.0 / scale, 0.0, -off.0 / scale, 0.0, 1.0 / scale, -off.1 / scale, 0.0, 0.0, 1.0];
    CalibIn { camera_px: cam, matrix_px_to_mm: m, lens, surface_mm: surface }
}

pub fn ms(out: &PerceptionOut, stage: &str) -> f64 {
    out.stages_ms.iter().find(|(n, _)| n == stage).map(|(_, v)| *v).unwrap_or(0.0)
}

pub struct Rng(pub u64);
impl Rng {
    pub fn next(&mut self) -> u64 {
        self.0 = self.0.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
        self.0 >> 33
    }
}

/// Mesa gris-beige con degradé de luz y ruido de sensor.
pub fn mesa(w: u32, h: u32, light: i32, seed: u64) -> RgbImage {
    let mut r = Rng(seed);
    RgbImage::from_fn(w, h, |x, y| {
        let v = 128 + (x as i32 * 30 / w as i32) + (y as i32 * 16 / h as i32) + light + (r.next() % 5) as i32 - 2;
        Rgb([(v + 8).clamp(0, 255) as u8, v.clamp(0, 255) as u8, (v - 14).clamp(0, 255) as u8])
    })
}

/// Objeto de prueba: forma + color (+ textura de bloques opcional).
#[derive(Clone)]
pub struct Cosa {
    pub cx: f64,
    pub cy: f64,
    /// Semiejes en px de cámara.
    pub a: f64,
    pub b: f64,
    pub ang: f64,
    pub color: [u8; 3],
    pub textura: Option<u64>,
    pub elipse: bool,
}

impl Cosa {
    pub fn caja(cx: f64, cy: f64, a: f64, b: f64, color: [u8; 3]) -> Cosa {
        Cosa { cx, cy, a, b, ang: 0.0, color, textura: None, elipse: false }
    }
    pub fn disco(cx: f64, cy: f64, r: f64, color: [u8; 3]) -> Cosa {
        Cosa { cx, cy, a: r, b: r, ang: 0.0, color, textura: None, elipse: true }
    }
    pub fn en(&self, cx: f64, cy: f64) -> Cosa {
        Cosa { cx, cy, ..self.clone() }
    }
    pub fn girada(&self, ang: f64) -> Cosa {
        Cosa { ang, ..self.clone() }
    }
    pub fn con_textura(&self, seed: u64) -> Cosa {
        Cosa { textura: Some(seed), ..self.clone() }
    }
    fn dentro(&self, x: f64, y: f64) -> Option<(f64, f64)> {
        let (c, s) = (self.ang.to_radians().cos(), self.ang.to_radians().sin());
        let (dx, dy) = (x - self.cx, y - self.cy);
        let (u, v) = (c * dx + s * dy, -s * dx + c * dy);
        let ok = if self.elipse {
            (u / self.a).powi(2) + (v / self.b).powi(2) <= 1.0
        } else {
            u.abs() <= self.a && v.abs() <= self.b
        };
        ok.then_some((u, v))
    }
}

pub fn pintar(img: &mut RgbImage, cosas: &[Cosa]) {
    for o in cosas {
        let r = o.a.hypot(o.b).ceil() as i64 + 1;
        for y in (o.cy as i64 - r).max(0)..(o.cy as i64 + r).min(img.height() as i64) {
            for x in (o.cx as i64 - r).max(0)..(o.cx as i64 + r).min(img.width() as i64) {
                if let Some((u, v)) = o.dentro(x as f64 + 0.5, y as f64 + 0.5) {
                    let mut c = o.color;
                    if let Some(seed) = o.textura {
                        let (bu, bv) = (((u + 512.0) / 7.0) as u64, ((v + 512.0) / 7.0) as u64);
                        let mut r = Rng(seed ^ bu.wrapping_mul(7919) ^ bv.wrapping_mul(104729));
                        r.next();
                        c = [(r.next() % 256) as u8, (r.next() % 256) as u8, (r.next() % 256) as u8];
                    }
                    img.put_pixel(x as u32, y as u32, Rgb(c));
                }
            }
        }
    }
}

pub fn escena(w: u32, h: u32, cosas: &[Cosa], light: i32, seed: u64) -> RgbImage {
    let mut img = mesa(w, h, light, seed);
    pintar(&mut img, cosas);
    img
}

/// Percentil (0–100) de una lista de tiempos.
pub fn pct(v: &[f64], p: f64) -> f64 {
    let mut s = v.to_vec();
    s.sort_by(f64::total_cmp);
    if s.is_empty() {
        return 0.0;
    }
    s[((p / 100.0) * (s.len() - 1) as f64).round() as usize]
}
