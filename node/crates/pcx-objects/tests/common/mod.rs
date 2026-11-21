//! Escenas sintéticas: mesa lisa con degradé + «tarjetas» con textura de bloques (rígidas) que se mueven,
//! giran, desaparecen o se arrugan (textura revuelta con los mismos colores).
#![allow(dead_code)]

use image::{Rgb, RgbImage};
use pcx_objects::geom::Canvas;
use pcx_objects::models::{BoxErr, Detection, Detector, SamOutput, SamPoint, Segmenter};
use pcx_objects::raster::{self, Mask};
use std::sync::{Arc, Mutex};

pub const W: u32 = 640;
pub const H: u32 = 360;

pub fn canvas() -> Canvas {
    Canvas::covering(0.0, 0.0, 1280.0, 720.0, W) // 2 mm por px
}

struct Rng(u64);
impl Rng {
    fn next(&mut self) -> u64 {
        self.0 = self.0.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
        self.0 >> 33
    }
}

/// Textura de bloques de 8 px con colores al azar (semilla fija).
pub fn texture(seed: u64, w: u32, h: u32) -> RgbImage {
    let mut r = Rng(seed);
    let cols: Vec<[u8; 3]> = (0..((w / 8 + 1) * (h / 8 + 1)))
        .map(|_| [(r.next() % 256) as u8, (r.next() % 256) as u8, (r.next() % 256) as u8])
        .collect();
    RgbImage::from_fn(w, h, |x, y| Rgb(cols[((y / 8) * (w / 8 + 1) + x / 8) as usize]))
}

/// Misma paleta, bloques revueltos («arrugado/doblado»: no rígido, mismo color).
pub fn scrambled(t: &RgbImage, seed: u64) -> RgbImage {
    let (w, h) = t.dimensions();
    let (bw, bh) = (w / 8, h / 8);
    let mut idx: Vec<(u32, u32)> = (0..bh).flat_map(|y| (0..bw).map(move |x| (x, y))).collect();
    let mut r = Rng(seed);
    for i in (1..idx.len()).rev() {
        let j = (r.next() as usize) % (i + 1);
        idx.swap(i, j);
    }
    RgbImage::from_fn(w, h, |x, y| {
        let (bx, by) = ((x / 8).min(bw - 1), (y / 8).min(bh - 1));
        let (sx, sy) = idx[(by * bw + bx) as usize];
        *t.get_pixel(sx * 8 + x % 8, sy * 8 + y % 8)
    })
}

#[derive(Clone)]
pub struct Obj {
    pub tex: RgbImage,
    pub cx: f64,
    pub cy: f64,
    pub angle_deg: f64,
}

pub fn background(light: i32) -> RgbImage {
    RgbImage::from_fn(W, H, |x, y| {
        let v = 130 + (x as i32 * 20 / W as i32) + (y as i32 * 10 / H as i32) + light;
        Rgb([(v + 15).clamp(0, 255) as u8, v.clamp(0, 255) as u8, (v - 20).clamp(0, 255) as u8])
    })
}

pub fn render(objs: &[Obj], light: i32, noise_seed: u64) -> RgbImage {
    let mut img = background(light);
    for o in objs {
        let (c, s) = (o.angle_deg.to_radians().cos(), o.angle_deg.to_radians().sin());
        let (tw, th) = (o.tex.width() as f64, o.tex.height() as f64);
        for y in 0..H {
            for x in 0..W {
                let (dx, dy) = (x as f64 + 0.5 - o.cx, y as f64 + 0.5 - o.cy);
                let (u, v) = (c * dx + s * dy + tw / 2.0, -s * dx + c * dy + th / 2.0);
                if u >= 0.0 && v >= 0.0 && u < tw && v < th {
                    let p = o.tex.get_pixel(u as u32, v as u32).0;
                    img.put_pixel(x, y, Rgb(p.map(|q| (q as i32 + light).clamp(0, 255) as u8)));
                }
            }
        }
    }
    let mut r = Rng(noise_seed);
    for p in img.pixels_mut() {
        let n = (r.next() % 5) as i32 - 2;
        p.0 = p.0.map(|q| (q as i32 + n).clamp(0, 255) as u8);
    }
    img
}

pub fn mask_of(o: &Obj) -> Mask {
    let mut m = raster::empty(W, H);
    let (c, s) = (o.angle_deg.to_radians().cos(), o.angle_deg.to_radians().sin());
    let (tw, th) = (o.tex.width() as f64, o.tex.height() as f64);
    for y in 0..H {
        for x in 0..W {
            let (dx, dy) = (x as f64 + 0.5 - o.cx, y as f64 + 0.5 - o.cy);
            let (u, v) = (c * dx + s * dy + tw / 2.0, -s * dx + c * dy + th / 2.0);
            if u >= 0.0 && v >= 0.0 && u < tw && v < th {
                m.put_pixel(x, y, raster::ON);
            }
        }
    }
    m
}

/// SAM falso: devuelve la máscara verdadera que mejor calza con la caja o contiene el punto.
#[derive(Clone, Default)]
pub struct FakeSam {
    pub truth: Arc<Mutex<Vec<Mask>>>,
}

impl Segmenter for FakeSam {
    fn set_image(&mut self, _img: &RgbImage) -> Result<(), BoxErr> {
        Ok(())
    }
    fn predict(&mut self, pts: &[SamPoint]) -> Result<SamOutput, BoxErr> {
        let truth = self.truth.lock().unwrap();
        let boxed: Vec<_> = pts.iter().filter(|p| p.2 == 2.0 || p.2 == 3.0).collect();
        let pick = if boxed.len() == 2 {
            let b = (boxed[0].0 as u32, boxed[0].1 as u32, boxed[1].0 as u32, boxed[1].1 as u32);
            truth.iter().max_by_key(|m| {
                let mut n = 0;
                for y in b.1..b.3.min(H) {
                    for x in b.0..b.2.min(W) {
                        n += (m.get_pixel(x, y).0[0] > 0) as u32;
                    }
                }
                n
            })
        } else {
            let p = pts.iter().find(|p| p.2 == 1.0).unwrap();
            truth.iter().find(|m| m.get_pixel(p.0 as u32, p.1 as u32).0[0] > 0)
        };
        let logits = match pick {
            Some(m) => m.as_raw().iter().map(|&v| if v > 0 { 5.0 } else { -5.0 }).collect(),
            None => vec![-5.0; (W * H) as usize],
        };
        Ok(SamOutput { width: W, height: H, logits, score: 0.95 })
    }
}

/// DINO falso: devuelve las cajas configuradas para cada prompt.
#[derive(Clone, Default)]
pub struct FakeDino {
    pub boxes: Arc<Mutex<Vec<Detection>>>,
}

impl Detector for FakeDino {
    fn detect(&mut self, _img: &RgbImage, prompts: &[String], _th: f32) -> Result<Vec<Detection>, BoxErr> {
        Ok(self.boxes.lock().unwrap().iter().filter(|d| prompts.contains(&d.prompt)).cloned().collect())
    }
}

pub fn bbox_f(m: &Mask) -> [f32; 4] {
    let b = raster::bbox(m).unwrap();
    [b.0 as f32, b.1 as f32, b.2 as f32, b.3 as f32]
}
