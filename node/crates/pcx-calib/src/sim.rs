//! Simulador: proyector y cámara pinhole sobre una mesa (mm), con distorsión radial de la cámara, ruido, luz
//! ambiente, latencia variable, exposición que mezcla patrones, una caja alta (paralaje y sombra) y un marcador
//! `pcx6` impreso. Implementa `PatternSink` y `FrameGrab` con reloj virtual; expone la verdad para los tests.
//!
//! Mundo: x a la derecha, y hacia abajo (= `table_mm`), z hacia la mesa (la altura h está en z = −h).

use std::sync::{Arc, Mutex};

use nalgebra::{Matrix3, Vector3};

use crate::homography::Pt;
use crate::image::{GrayImage, Rng};
use crate::io::{CalibError, Frame, FrameGrab, Pattern, PatternSink};
use crate::markers;

type V3 = Vector3<f64>;

#[derive(Debug, Clone)]
pub struct Pinhole {
    pub w: u32,
    pub h: u32,
    pub f: f64,
    pub cx: f64,
    pub cy: f64,
    /// mundo → cámara.
    pub r: Matrix3<f64>,
    pub c: V3,
    pub k1: f64,
    pub k2: f64,
}

impl Pinhole {
    #[allow(clippy::too_many_arguments)]
    pub fn look_at(w: u32, h: u32, f: f64, cx: f64, cy: f64, center: [f64; 3], target: [f64; 3], k: (f64, f64)) -> Self {
        let c = V3::from(center);
        let z = (V3::from(target) - c).normalize();
        let x = V3::new(0.0, 1.0, 0.0).cross(&z).normalize();
        let y = z.cross(&x);
        let r = Matrix3::from_rows(&[x.transpose(), y.transpose(), z.transpose()]);
        Self { w, h, f, cx, cy, r, c, k1: k.0, k2: k.1 }
    }
    pub fn project(&self, x: V3) -> Option<Pt> {
        let p = self.r * (x - self.c);
        if p.z <= 1e-9 {
            return None;
        }
        let (xn, yn) = (p.x / p.z, p.y / p.z);
        let r2 = xn * xn + yn * yn;
        let d = 1.0 + self.k1 * r2 + self.k2 * r2 * r2;
        Some([self.f * xn * d + self.cx, self.f * yn * d + self.cy])
    }
    /// Dirección del rayo (mundo) del píxel `(u, v)`, deshaciendo la distorsión por punto fijo.
    pub fn ray(&self, u: f64, v: f64) -> V3 {
        let (xd, yd) = ((u - self.cx) / self.f, (v - self.cy) / self.f);
        let (mut xn, mut yn) = (xd, yd);
        for _ in 0..10 {
            let r2 = xn * xn + yn * yn;
            let d = 1.0 + self.k1 * r2 + self.k2 * r2 * r2;
            xn = xd / d;
            yn = yd / d;
        }
        (self.r.transpose() * V3::new(xn, yn, 1.0)).normalize()
    }
    pub fn depth(&self, x: V3) -> f64 {
        (self.r * (x - self.c)).z
    }
}

#[derive(Debug, Clone, Copy)]
pub struct BoxObj {
    pub x0: f64,
    pub y0: f64,
    pub x1: f64,
    pub y1: f64,
    pub height: f64,
}

#[derive(Debug, Clone)]
pub struct World {
    pub cam: Pinhole,
    pub proj: Pinhole,
    pub boxo: Option<BoxObj>,
    /// Marcadores impresos: id, esquina TL en mm, lado en mm.
    pub printed: Vec<(usize, [f64; 2], f64)>,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Surface {
    Table,
    BoxTop,
    BoxSide,
}

impl World {
    /// Primer impacto del rayo `o + t·d`: (t, punto, albedo, superficie).
    pub fn hit(&self, o: V3, d: V3) -> Option<(f64, V3, f32, Surface)> {
        let tp = if d.z > 1e-12 { -o.z / d.z } else { f64::INFINITY };
        let mut best = (tp, Surface::Table);
        if let Some(b) = self.boxo {
            let (lo, hi) = (V3::new(b.x0, b.y0, -b.height), V3::new(b.x1, b.y1, 0.0));
            let (mut t0, mut t1, mut axis) = (f64::NEG_INFINITY, f64::INFINITY, 0);
            for k in 0..3 {
                if d[k].abs() < 1e-12 {
                    if o[k] < lo[k] || o[k] > hi[k] {
                        t0 = f64::INFINITY;
                    }
                    continue;
                }
                let (mut a, mut c) = ((lo[k] - o[k]) / d[k], (hi[k] - o[k]) / d[k]);
                if a > c {
                    std::mem::swap(&mut a, &mut c);
                }
                if a > t0 {
                    t0 = a;
                    axis = k;
                }
                t1 = t1.min(c);
            }
            if t0 <= t1 && t0 > 0.0 && t0 < best.0 {
                best = (t0, if axis == 2 { Surface::BoxTop } else { Surface::BoxSide });
            }
        }
        if !best.0.is_finite() {
            return None;
        }
        let x = o + d * best.0;
        let albedo = match best.1 {
            Surface::BoxTop => 0.6,
            Surface::BoxSide => 0.45,
            Surface::Table => self.table_albedo(x),
        };
        Some((best.0, x, albedo, best.1))
    }

    fn table_albedo(&self, x: V3) -> f32 {
        for &(id, o, side) in &self.printed {
            let (u, v) = ((x.x - o[0]) / side * 6.0, (x.y - o[1]) / side * 6.0);
            if (0.0..6.0).contains(&u) && (0.0..6.0).contains(&v) {
                return if markers::cells(id)[v as usize][u as usize] { 0.05 } else { 0.85 };
            }
            // margen blanco de papel alrededor
            if (-1.0..7.0).contains(&u) && (-1.0..7.0).contains(&v) {
                return 0.85;
            }
        }
        0.7
    }

    fn visible_from(&self, from: V3, x: V3) -> bool {
        let d = (x - from).normalize();
        self.hit(from, d).is_some_and(|(t, _, _, _)| (t - (x - from).norm()).abs() < 0.5)
    }

    /// Verdad: dónde ve la cámara la luz del píxel `p` del proyector (`None` si no llega o está oculto).
    pub fn cam_of_proj(&self, p: Pt) -> Option<(Pt, f64, Surface)> {
        let (_, x, _, s) = self.hit(self.proj.c, self.proj.ray(p[0], p[1]))?;
        if !self.visible_from(self.cam.c, x) {
            return None;
        }
        let c = self.cam.project(x)?;
        (c[0] >= 0.0 && c[1] >= 0.0 && c[0] <= (self.cam.w - 1) as f64 && c[1] <= (self.cam.h - 1) as f64)
            .then(|| (c, self.cam.depth(x) / self.cam.f, s))
    }
    pub fn proj_of_table(&self, x: Pt) -> Option<Pt> {
        self.proj.project(V3::new(x[0], x[1], 0.0))
    }
    pub fn table_of_proj(&self, p: Pt) -> Pt {
        let d = self.proj.ray(p[0], p[1]);
        let t = -self.proj.c.z / d.z;
        let x = self.proj.c + d * t;
        [x.x, x.y]
    }
}

/// Marcadores impresos del escenario por defecto: (id, x, y) de la esquina TL en mm.
pub const PRINTED: [(usize, f64, f64); 4] = [(60, 70.0, 60.0), (61, 640.0, 60.0), (62, 70.0, 470.0), (63, 640.0, 470.0)];
pub const PRINTED_SIDE_MM: f64 = 60.0;

#[derive(Debug, Clone)]
pub struct SimConfig {
    pub world: World,
    pub ambient: f32,
    pub gain: f32,
    pub noise: f32,
    pub frame_period_ns: i64,
    pub latency_ns: i64,
    pub jitter_ns: i64,
    pub seed: u64,
}

impl Default for SimConfig {
    fn default() -> Self {
        let cam = Pinhole::look_at(640, 480, 560.0, 320.0, 240.0, [420.0, 300.0, -1000.0], [400.0, 280.0, 0.0], (-0.08, 0.01));
        let proj = Pinhole::look_at(1024, 768, 1640.0, 512.0, 384.0, [600.0, 260.0, -1150.0], [400.0, 300.0, 0.0], (0.0, 0.0));
        Self {
            world: World {
                cam,
                proj,
                boxo: Some(BoxObj { x0: 330.0, y0: 230.0, x1: 450.0, y1: 330.0, height: 90.0 }),
                printed: PRINTED.iter().map(|&(id, x, y)| (id, [x, y], PRINTED_SIDE_MM)).collect(),
            },
            ambient: 40.0,
            gain: 170.0,
            noise: 2.0,
            frame_period_ns: 33_333_333,
            latency_ns: 120_000_000,
            jitter_ns: 8_000_000,
            seed: 1,
        }
    }
}

struct Sub {
    p: [f32; 2],
    albedo: f32,
}

struct State {
    cfg: SimConfig,
    subs: Vec<Sub>,
    clock: i64,
    next_k: i64,
    events: Vec<(i64, Arc<GrayImage>)>,
    cache: Vec<(Arc<GrayImage>, Arc<Vec<f32>>)>,
    rng: Rng,
    frames: usize,
}

const T0: i64 = 1_763_700_000_000_000_000;

impl State {
    fn precompute(cfg: &SimConfig) -> Vec<Sub> {
        let w = &cfg.world;
        let mut subs = Vec::with_capacity((w.cam.w * w.cam.h * 4) as usize);
        for v in 0..w.cam.h {
            for u in 0..w.cam.w {
                for (ox, oy) in [(-0.25, -0.25), (0.25, -0.25), (-0.25, 0.25), (0.25, 0.25)] {
                    let d = w.cam.ray(u as f64 + ox, v as f64 + oy);
                    let mut s = Sub { p: [f32::NAN; 2], albedo: 0.0 };
                    if let Some((_, x, a, _)) = w.hit(w.cam.c, d) {
                        s.albedo = a;
                        if w.visible_from(w.proj.c, x) {
                            if let Some(p) = w.proj.project(x) {
                                s.p = [p[0] as f32, p[1] as f32];
                            }
                        }
                    }
                    subs.push(s);
                }
            }
        }
        subs
    }

    fn radiance(&mut self, pat: &Arc<GrayImage>) -> Arc<Vec<f32>> {
        if let Some((_, r)) = self.cache.iter().find(|(p, _)| Arc::ptr_eq(p, pat)) {
            return r.clone();
        }
        let (amb, gain) = (self.cfg.ambient, self.cfg.gain / 255.0);
        let (pw, ph) = (pat.width as f32, pat.height as f32);
        let sample = |p: [f32; 2]| -> f32 {
            if !(p[0] >= -0.5 && p[1] >= -0.5 && p[0] <= pw - 0.5 && p[1] <= ph - 0.5) {
                return 0.0;
            }
            let (x, y) = (p[0].clamp(0.0, pw - 1.0), p[1].clamp(0.0, ph - 1.0));
            let (x0, y0) = (x.floor() as u32, y.floor() as u32);
            let (x1, y1) = ((x0 + 1).min(pat.width - 1), (y0 + 1).min(pat.height - 1));
            let (fx, fy) = (x - x0 as f32, y - y0 as f32);
            let g = |a: u32, b: u32| pat.data[(b * pat.width + a) as usize] as f32;
            (g(x0, y0) * (1.0 - fx) + g(x1, y0) * fx) * (1.0 - fy) + (g(x0, y1) * (1.0 - fx) + g(x1, y1) * fx) * fy
        };
        let out: Vec<f32> = self
            .subs
            .chunks(4)
            .map(|c| c.iter().map(|s| s.albedo * (amb + if s.p[0].is_nan() { 0.0 } else { gain * sample(s.p) })).sum::<f32>() / 4.0)
            .collect();
        let r = Arc::new(out);
        self.cache.push((pat.clone(), r.clone()));
        if self.cache.len() > 4 {
            self.cache.remove(0);
        }
        r
    }

    fn active_at(&self, t: i64) -> usize {
        self.events.iter().rposition(|e| e.0 <= t).unwrap_or(0)
    }

    fn frame_at(&mut self, t: i64) -> GrayImage {
        let tp = self.cfg.frame_period_ns;
        let (a, b) = (t - tp, t);
        // segmentos de exposición por patrón activo
        let mut cuts = vec![a];
        cuts.extend(self.events.iter().map(|e| e.0).filter(|&x| x > a && x < b));
        cuts.push(b);
        let mut acc: Option<Vec<f32>> = None;
        for w in cuts.windows(2) {
            let wgt = (w[1] - w[0]) as f32 / tp as f32;
            let pat = self.events[self.active_at(w[0])].1.clone();
            let r = self.radiance(&pat);
            match acc.as_mut() {
                None => acc = Some(r.iter().map(|v| v * wgt).collect()),
                Some(v) => v.iter_mut().zip(r.iter()).for_each(|(x, y)| *x += y * wgt),
            }
        }
        let acc = acc.unwrap_or_default();
        let n = self.cfg.noise;
        let data = acc
            .iter()
            .map(|v| {
                let g = (self.rng.uniform() + self.rng.uniform() + self.rng.uniform() + self.rng.uniform() - 2.0) as f32 * 1.732;
                (v + n * g).round().clamp(0.0, 255.0) as u8
            })
            .collect();
        self.frames += 1;
        GrayImage { width: self.cfg.world.cam.w, height: self.cfg.world.cam.h, data }
    }
}

/// Simulador compartido entre el proyector y la cámara virtuales.
#[derive(Clone)]
pub struct Sim(Arc<Mutex<State>>);

impl Sim {
    pub fn new(cfg: SimConfig) -> Self {
        let subs = State::precompute(&cfg);
        let black = Arc::new(GrayImage::new(cfg.world.proj.w, cfg.world.proj.h, 0));
        let rng = Rng::new(cfg.seed);
        Sim(Arc::new(Mutex::new(State { cfg, subs, clock: T0, next_k: 0, events: vec![(0, black)], cache: Vec::new(), rng, frames: 0 })))
    }
    pub fn projector(&self) -> SimProjector {
        SimProjector(self.clone())
    }
    pub fn camera(&self) -> SimCamera {
        SimCamera(self.clone())
    }
    pub fn world(&self) -> World {
        self.0.lock().unwrap().cfg.world.clone()
    }
    /// Reloj virtual (ns de época).
    pub fn now_ns(&self) -> i64 {
        self.0.lock().unwrap().clock
    }
    pub fn frames_rendered(&self) -> usize {
        self.0.lock().unwrap().frames
    }
    /// Mueve el proyector (mm) y recalcula la escena (para probar la alarma de deriva).
    pub fn move_projector(&self, dx: f64, dy: f64) {
        let mut s = self.0.lock().unwrap();
        s.cfg.world.proj.c += V3::new(dx, dy, 0.0);
        s.subs = State::precompute(&s.cfg);
        s.cache.clear();
    }
}

pub struct SimProjector(Sim);
pub struct SimCamera(Sim);

impl PatternSink for SimProjector {
    fn size(&self) -> (u32, u32) {
        let s = self.0 .0.lock().unwrap();
        (s.cfg.world.proj.w, s.cfg.world.proj.h)
    }
    fn show(&mut self, p: &Pattern) -> Result<i64, CalibError> {
        let mut s = self.0 .0.lock().unwrap();
        let j = ((s.rng.uniform() - 0.5) * 2.0 * s.cfg.jitter_ns as f64) as i64;
        let t = s.clock;
        let ts = (t + s.cfg.latency_ns + j).max(s.events.last().map_or(0, |e| e.0) + 1);
        s.events.push((ts, p.image.clone()));
        Ok(t)
    }
    fn release(&mut self) -> Result<(), CalibError> {
        let mut s = self.0 .0.lock().unwrap();
        let black = Arc::new(GrayImage::new(s.cfg.world.proj.w, s.cfg.world.proj.h, 0));
        let ts = s.clock + s.cfg.latency_ns;
        s.events.push((ts, black));
        Ok(())
    }
}

impl FrameGrab for SimCamera {
    fn grab(&mut self, not_before_ns: i64) -> Result<Frame, CalibError> {
        let mut s = self.0 .0.lock().unwrap();
        let tp = s.cfg.frame_period_ns;
        let k = s.next_k.max((not_before_ns - T0 + tp - 1).div_euclid(tp));
        s.next_k = k + 1;
        let t = T0 + k * tp;
        s.clock = s.clock.max(t);
        // poda de eventos viejos (conserva el activo)
        let keep_from = s.active_at(t - 2 * tp);
        if keep_from > 0 {
            s.events.drain(..keep_from);
        }
        let image = s.frame_at(t);
        Ok(Frame { t_capture_ns: t, image })
    }
}
