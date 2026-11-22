//! Seguimiento estilo MediaPipe: se reusa el ROI del cuadro anterior (sacado de los landmarks) y la palma se
//! detecta solo cuando faltan manos. IDs estables (un ID perdido hace poco se reutiliza si la mano reaparece cerca)
//! y filtro One Euro sobre los landmarks.
use crate::frame::Frame;
use crate::geom::{self, Roi};
use crate::infer::{HandDetector, LandmarkResult};
use crate::types::{Handedness, Point2};
use crate::HandsError;

/// Filtro One Euro por punto (port de `OneEuro` en `engine/playground/vision_mesa.py`).
#[derive(Debug, Clone)]
pub struct OneEuro {
    pub min_cutoff: f64,
    pub beta: f64,
    pub d_cutoff: f64,
    x: Vec<[f64; 3]>,
    dx: Vec<[f64; 3]>,
    t: Option<f64>,
}

impl OneEuro {
    pub fn new(min_cutoff: f64, beta: f64, d_cutoff: f64) -> Self {
        Self { min_cutoff, beta, d_cutoff, x: vec![], dx: vec![], t: None }
    }

    fn alpha(cutoff: f64, dt: f64) -> f64 {
        let tau = 1.0 / (2.0 * std::f64::consts::PI * cutoff);
        1.0 / (1.0 + tau / dt)
    }

    /// Filtra `pts` (x, y, z) en el instante `t` (s). La frecuencia de corte depende de la velocidad en xy.
    pub fn filter(&mut self, pts: &[[f64; 3]], t: f64) -> Vec<[f64; 3]> {
        let Some(t0) = self.t.filter(|_| self.x.len() == pts.len()) else {
            self.x = pts.to_vec();
            self.dx = vec![[0.0; 3]; pts.len()];
            self.t = Some(t);
            return self.x.clone();
        };
        let dt = (t - t0).max(1e-3);
        self.t = Some(t);
        let a_d = Self::alpha(self.d_cutoff, dt);
        for ((p, x), dx) in pts.iter().zip(self.x.iter_mut()).zip(self.dx.iter_mut()) {
            for k in 0..3 {
                dx[k] = a_d * (p[k] - x[k]) / dt + (1.0 - a_d) * dx[k];
            }
            let speed = (dx[0].powi(2) + dx[1].powi(2)).sqrt();
            let a = Self::alpha(self.min_cutoff + self.beta * speed, dt);
            for k in 0..3 {
                x[k] = a * p[k] + (1.0 - a) * x[k];
            }
        }
        self.x.clone()
    }
}

#[derive(Debug, Clone, Copy)]
pub struct TrackerConfig {
    pub max_hands: usize,
    /// Presencia mínima para aceptar una mano nueva (zoo: 0,8).
    pub min_detection_score: f32,
    /// Presencia mínima para seguir una mano (MediaPipe `min_tracking_confidence`).
    pub min_tracking_score: f32,
    /// Con menos de `max_hands` manos, correr la palma cada N cuadros (1 = como MediaPipe).
    pub detect_interval: u32,
    /// Un ID perdido hace menos de esto se reusa si la mano reaparece cerca (s).
    pub id_ttl_s: f64,
    /// Invierte la lateralidad del modelo (zoo: > 0,5 = derecha). MediaPipe la define para imágenes espejadas
    /// (selfie); verificar con la cámara cenital de la mesa.
    pub flip_handedness: bool,
    pub one_euro: Option<(f64, f64, f64)>,
}

impl Default for TrackerConfig {
    fn default() -> Self {
        Self {
            max_hands: 2,
            min_detection_score: 0.8,
            min_tracking_score: 0.5,
            detect_interval: 1,
            id_ttl_s: 0.5,
            flip_handedness: false,
            one_euro: Some((1.2, 0.02, 1.0)),
        }
    }
}

/// Mano seguida en un cuadro.
#[derive(Debug, Clone)]
pub struct TrackedHand {
    pub id: u64,
    /// Landmarks filtrados (px de cámara; z relativa a la muñeca).
    pub landmarks: [[f64; 3]; 21],
    /// Landmarks crudos del modelo.
    pub raw: LandmarkResult,
    pub handedness: Handedness,
    pub score: f32,
    pub roi: Roi,
    /// `true` si salió de la detección de palma en este cuadro (no del seguimiento).
    pub fresh: bool,
}

impl TrackedHand {
    pub fn xy(&self) -> [Point2; 21] {
        self.landmarks.map(|p| [p[0], p[1]])
    }
}

struct Track {
    id: u64,
    roi: Roi,
    filter: Option<OneEuro>,
    last: Option<TrackedHand>,
}

struct Ghost {
    id: u64,
    center: Point2,
    size: f64,
    t: f64,
}

pub struct HandTracker {
    pub det: HandDetector,
    pub cfg: TrackerConfig,
    tracks: Vec<Track>,
    ghosts: Vec<Ghost>,
    next_id: u64,
    frames_since_detect: u32,
}

#[derive(Debug, Clone, Copy, Default)]
pub struct TrackerTimings {
    pub palm_ms: f64,
    pub landmarks_ms: f64,
    pub palm_runs: u32,
    pub landmark_runs: u32,
}

impl HandTracker {
    pub fn new(det: HandDetector, cfg: TrackerConfig) -> Self {
        Self { det, cfg, tracks: vec![], ghosts: vec![], next_id: 1, frames_since_detect: u32::MAX }
    }

    pub fn reset(&mut self) {
        self.tracks.clear();
        self.ghosts.clear();
        self.frames_since_detect = u32::MAX;
    }

    fn handedness(&self, r: &LandmarkResult) -> Handedness {
        let right = (r.handedness > 0.5) != self.cfg.flip_handedness;
        if right {
            Handedness::Right
        } else {
            Handedness::Left
        }
    }

    fn make(
        &self,
        id: u64,
        r: LandmarkResult,
        roi: Roi,
        fresh: bool,
        filter: &mut Option<OneEuro>,
        t: f64,
    ) -> TrackedHand {
        let landmarks = match filter {
            Some(f) => {
                let v = f.filter(&r.landmarks, t);
                std::array::from_fn(|i| v[i])
            }
            None => r.landmarks,
        };
        TrackedHand { id, landmarks, handedness: self.handedness(&r), score: r.score, roi, fresh, raw: r }
    }

    /// Procesa un cuadro capturado en `t_s` (s). Devuelve las manos vivas.
    pub fn process(&mut self, frame: &Frame, t_s: f64) -> Result<(Vec<TrackedHand>, TrackerTimings), HandsError> {
        let mut tm = TrackerTimings::default();
        // 1. seguimiento: ROI del cuadro anterior
        let mut alive = Vec::with_capacity(self.tracks.len());
        for mut tr in std::mem::take(&mut self.tracks) {
            let t0 = std::time::Instant::now();
            let r = self.det.landmarks(frame, &tr.roi)?;
            tm.landmarks_ms += t0.elapsed().as_secs_f64() * 1e3;
            tm.landmark_runs += 1;
            if r.score < self.cfg.min_tracking_score {
                let c = tr.roi.center;
                self.ghosts.push(Ghost { id: tr.id, center: c, size: tr.roi.size, t: t_s });
                continue;
            }
            let roi_used = tr.roi;
            tr.roi = geom::roi_from_landmarks(&r.xy());
            let mut f = tr.filter.take();
            let h = self.make(tr.id, r, roi_used, false, &mut f, t_s);
            tr.filter = f;
            tr.last = Some(h);
            alive.push(tr);
        }
        // dos seguimientos que convergieron en la misma mano → queda el primero (más antiguo)
        let mut dedup: Vec<Track> = Vec::with_capacity(alive.len());
        for tr in alive {
            let b = geom::bounds(tr.last.as_ref().map(|h| h.xy()).unwrap_or_default());
            let dup = dedup
                .iter()
                .any(|o| geom::iou(b, geom::bounds(o.last.as_ref().map(|h| h.xy()).unwrap_or_default())) > 0.5);
            if !dup {
                dedup.push(tr);
            }
        }
        self.tracks = dedup;
        self.ghosts.retain(|g| t_s - g.t <= self.cfg.id_ttl_s);

        // 2. detección de palma si faltan manos
        self.frames_since_detect = self.frames_since_detect.saturating_add(1);
        if self.tracks.len() < self.cfg.max_hands && self.frames_since_detect >= self.cfg.detect_interval {
            self.frames_since_detect = 0;
            let t0 = std::time::Instant::now();
            let palms = self.det.detect_palms(frame)?;
            tm.palm_ms += t0.elapsed().as_secs_f64() * 1e3;
            tm.palm_runs += 1;
            for p in palms {
                if self.tracks.len() >= self.cfg.max_hands {
                    break;
                }
                let pc = [(p.bbox[0] + p.bbox[2]) / 2.0, (p.bbox[1] + p.bbox[3]) / 2.0];
                let covered = self.tracks.iter().any(|tr| {
                    let b = geom::bounds(tr.last.as_ref().map(|h| h.xy()).unwrap_or_default());
                    pc[0] >= b[0] && pc[0] <= b[2] && pc[1] >= b[1] && pc[1] <= b[3]
                });
                if covered {
                    continue;
                }
                let roi = p.roi();
                let t0 = std::time::Instant::now();
                let r = self.det.landmarks(frame, &roi)?;
                tm.landmarks_ms += t0.elapsed().as_secs_f64() * 1e3;
                tm.landmark_runs += 1;
                if r.score < self.cfg.min_detection_score {
                    continue;
                }
                let id = self.reuse_id(roi.center, roi.size).unwrap_or_else(|| {
                    self.next_id += 1;
                    self.next_id - 1
                });
                let mut f = self.cfg.one_euro.map(|(a, b, c)| OneEuro::new(a, b, c));
                let next_roi = geom::roi_from_landmarks(&r.xy());
                let h = self.make(id, r, roi, true, &mut f, t_s);
                self.tracks.push(Track { id, roi: next_roi, filter: f, last: Some(h) });
            }
        }
        Ok((self.tracks.iter().filter_map(|t| t.last.clone()).collect(), tm))
    }

    fn reuse_id(&mut self, c: Point2, size: f64) -> Option<u64> {
        let (i, _) = self
            .ghosts
            .iter()
            .enumerate()
            .map(|(i, g)| (i, ((g.center[0] - c[0]).powi(2) + (g.center[1] - c[1]).powi(2)).sqrt() / g.size.max(size)))
            .filter(|(_, d)| *d < 0.5)
            .min_by(|a, b| a.1.total_cmp(&b.1))?;
        Some(self.ghosts.remove(i).id)
    }
}

#[cfg(test)]
mod tests {
    use super::OneEuro;

    #[test]
    fn one_euro_still_converges_and_moves() {
        let mut f = OneEuro::new(1.2, 0.02, 1.0);
        let a = f.filter(&[[10.0, 10.0, 0.0]], 0.0);
        assert_eq!(a[0][0], 10.0);
        let mut last = 0.0;
        for i in 1..60 {
            last = f.filter(&[[20.0, 10.0, 0.0]], i as f64 / 30.0)[0][0];
        }
        assert!((last - 20.0).abs() < 0.1, "{last}");
    }
}
