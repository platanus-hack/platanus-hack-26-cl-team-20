//! Máquina de toque: port de `engine/playground/hands/touch.py` (pinch con histéresis, «apoyo» por tamaño de
//! palma en el plano de la mesa, zonas excluidas, antirrebote, EMA + banda muerta, mano perdida → up) más el
//! **dwell** de `vision_mesa.py` (dedo quieto < `dwell_radius_px` durante > `dwell_s`) y los **episodios** de
//! `seguidor_v3/v4` (sin manos durante `calm_s` → `HandsGone`, foto limpia).
use crate::geom::apply_homography;
use crate::types::{HandsEvent, Point2, TouchInfo, TouchPhase, TouchTrigger};
use std::collections::{HashMap, VecDeque};

pub const WRIST: usize = 0;
pub const THUMB_TIP: usize = 4;
pub const INDEX_MCP: usize = 5;
pub const INDEX_PIP: usize = 6;
pub const INDEX_TIP: usize = 8;
pub const MIDDLE_MCP: usize = 9;
pub const PINKY_MCP: usize = 17;

fn dist(a: Point2, b: Point2) -> f64 {
    ((a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2)).sqrt()
}

/// Tamaño de palma: media de muñeca→nudillo medio y ancho de nudillos (puntos rígidos).
pub fn palm_size(p: &[Point2; 21]) -> f64 {
    (dist(p[WRIST], p[MIDDLE_MCP]) + dist(p[INDEX_MCP], p[PINKY_MCP])) / 2.0
}

/// Pulgar–índice relativo a la palma (≈ 0,1 juntos, > 0,6 abiertos).
pub fn pinch_ratio(p: &[Point2; 21]) -> f64 {
    dist(p[THUMB_TIP], p[INDEX_TIP]) / palm_size(p).max(1e-6)
}

/// Polígono (en el marco de `point_mm`, `table_mm`) donde no empieza un toque.
#[derive(Debug, Clone)]
pub struct Zone {
    pub name: String,
    pub polygon: Vec<Point2>,
}

impl Zone {
    /// Dentro del polígono o a menos de `margin` de su borde.
    pub fn contains(&self, p: Point2, margin: f64) -> bool {
        let n = self.polygon.len();
        if n < 3 {
            return false;
        }
        let mut inside = false;
        let mut min_d = f64::MAX;
        for i in 0..n {
            let (a, b) = (self.polygon[i], self.polygon[(i + 1) % n]);
            if (a[1] > p[1]) != (b[1] > p[1])
                && p[0] < (b[0] - a[0]) * (p[1] - a[1]) / (b[1] - a[1]) + a[0]
            {
                inside = !inside;
            }
            let ab = [b[0] - a[0], b[1] - a[1]];
            let l2 = (ab[0] * ab[0] + ab[1] * ab[1]).max(1e-12);
            let t = (((p[0] - a[0]) * ab[0] + (p[1] - a[1]) * ab[1]) / l2).clamp(0.0, 1.0);
            min_d = min_d.min(dist(p, [a[0] + t * ab[0], a[1] + t * ab[1]]));
        }
        inside || min_d <= margin
    }
}

#[derive(Debug, Clone)]
pub struct TouchConfig {
    pub use_pinch: bool,
    pub use_rest: bool,
    pub use_dwell: bool,
    pub pinch_on: f64,
    pub pinch_off: f64,
    /// Palma en el plano / palma apoyada: down si < `ratio_down`, up si > `ratio_up`.
    pub ratio_down: f64,
    pub ratio_up: f64,
    pub frames_down: u32,
    pub frames_up: u32,
    /// Palma apoyada en el plano (mm con homografía, px sin ella). `None`: se aprende (percentil bajo).
    pub hand_ref: Option<f64>,
    pub auto_ref_window: usize,
    pub auto_ref_pct: f64,
    pub auto_ref_min: usize,
    pub ema_alpha: f64,
    /// Banda muerta del `move` (px de cámara).
    pub deadband_px: f64,
    /// Mano perdida más que esto → up (s).
    pub lost_s: f64,
    /// Dwell: radio de quietud (px de cámara) y tiempo para confirmar (s).
    pub dwell_radius_px: f64,
    pub dwell_s: f64,
    /// Un toque por dwell se suelta al alejarse más que esto del punto (px de cámara).
    pub dwell_release_px: f64,
    /// Pasos de `progress` para los eventos `Dwell` (0,25 → 0,25/0,5/0,75/1).
    pub dwell_step: f64,
    /// El dwell solo cuenta con el índice estirado (muñeca→punta > muñeca→falange media).
    pub dwell_require_pointing: bool,
    pub zones: Vec<Zone>,
    /// Margen de las zonas (unidades de `point_mm`).
    pub zone_margin: f64,
    /// Sin manos durante esto tras un episodio → `HandsGone` (s).
    pub calm_s: f64,
}

impl Default for TouchConfig {
    fn default() -> Self {
        Self {
            use_pinch: true,
            use_rest: true,
            use_dwell: true,
            pinch_on: 0.30,
            pinch_off: 0.42,
            ratio_down: 1.06,
            ratio_up: 1.12,
            frames_down: 2,
            frames_up: 2,
            hand_ref: None,
            auto_ref_window: 300,
            auto_ref_pct: 10.0,
            auto_ref_min: 30,
            ema_alpha: 0.5,
            deadband_px: 3.0,
            lost_s: 0.3,
            dwell_radius_px: 6.0,
            dwell_s: 0.45,
            dwell_release_px: 18.0,
            dwell_step: 0.25,
            dwell_require_pointing: true,
            zones: vec![],
            zone_margin: 8.0,
            calm_s: 0.4,
        }
    }
}

/// Entrada por mano: ID estable y 21 landmarks en px de cámara.
#[derive(Debug, Clone)]
pub struct TouchHand {
    pub id: String,
    pub landmarks_px: [Point2; 21],
}

#[derive(Debug, Default)]
struct HandSt {
    smooth: Option<Point2>,
    emitted: Point2,
    touching: bool,
    blocked: bool,
    pinch: bool,
    rest: bool,
    cnt_on: u32,
    cnt_off: u32,
    last_seen: f64,
    trigger: TouchTrigger,
    touch_id: String,
    dwell_anchor: Option<Point2>,
    still_since: f64,
    dwell_steps: u32,
    dwell_latched: bool,
}

pub struct TouchTracker {
    pub cfg: TouchConfig,
    /// Homografía cámara (px) → `table_mm`, fila mayor.
    pub homography: Option<[f64; 9]>,
    states: HashMap<String, HandSt>,
    sizes: VecDeque<f64>,
    n: u64,
    episode_start: Option<f64>,
    last_hand: f64,
    episode_start_ns: i64,
    last_hand_ns: i64,
}

impl TouchTracker {
    pub fn new(cfg: TouchConfig, homography: Option<[f64; 9]>) -> Self {
        Self {
            cfg,
            homography,
            states: HashMap::new(),
            sizes: VecDeque::new(),
            n: 0,
            episode_start: None,
            last_hand: 0.0,
            episode_start_ns: 0,
            last_hand_ns: 0,
        }
    }

    /// Referencia de palma apoyada: la configurada o el percentil `auto_ref_pct` de lo observado.
    pub fn hand_ref(&self) -> Option<f64> {
        if let Some(r) = self.cfg.hand_ref {
            return Some(r);
        }
        if self.sizes.len() < self.cfg.auto_ref_min {
            return None;
        }
        let mut v: Vec<f64> = self.sizes.iter().copied().collect();
        v.sort_by(f64::total_cmp);
        // percentil con interpolación lineal (como np.percentile)
        let pos = self.cfg.auto_ref_pct / 100.0 * (v.len() - 1) as f64;
        let (i, f) = (pos.floor() as usize, pos.fract());
        Some(v[i] + (v[(i + 1).min(v.len() - 1)] - v[i]) * f)
    }

    pub fn to_mm(&self, p: Point2) -> Option<Point2> {
        self.homography
            .as_ref()
            .and_then(|h| apply_homography(h, p))
    }

    /// Palma proyectada al plano de la mesa (mm con homografía; px de cámara sin ella).
    pub fn plane_palm(&self, lm: &[Point2; 21]) -> f64 {
        match &self.homography {
            Some(h) => {
                let m: [Point2; 21] =
                    std::array::from_fn(|i| apply_homography(h, lm[i]).unwrap_or(lm[i]));
                palm_size(&m)
            }
            None => palm_size(lm),
        }
    }

    /// Estado del pinch de la mano (para `HandState.pinch`).
    pub fn pinch(&self, hand_id: &str) -> bool {
        self.states.get(hand_id).is_some_and(|s| s.pinch)
    }

    fn info(&self, st: &HandSt, hand: &str, phase: TouchPhase, reason: &str) -> TouchInfo {
        TouchInfo {
            id: st.touch_id.clone(),
            hand_id: hand.to_string(),
            phase,
            trigger: st.trigger,
            point_px: st.emitted,
            point_mm: self.to_mm(st.emitted),
            reason: reason.to_string(),
        }
    }

    /// Un paso: manos vistas en el cuadro capturado en `t_capture_ns`.
    pub fn update(&mut self, hands: &[TouchHand], t_capture_ns: i64) -> Vec<HandsEvent> {
        let t = t_capture_ns as f64 * 1e-9;
        let tns = t_capture_ns;
        let mut ev = Vec::new();
        let mut seen = Vec::with_capacity(hands.len());

        for h in hands {
            seen.push(h.id.clone());
            let lm = &h.landmarks_px;
            let mut st = self.states.remove(&h.id).unwrap_or_default();
            st.last_seen = t;
            let tip = lm[INDEX_TIP];
            let a = self.cfg.ema_alpha;
            let smooth = match st.smooth {
                Some(s) => [a * tip[0] + (1.0 - a) * s[0], a * tip[1] + (1.0 - a) * s[1]],
                None => tip,
            };
            st.smooth = Some(smooth);

            // señales: pinch (histéresis) y apoyo (ratio de palma en el plano)
            let r = pinch_ratio(lm);
            st.pinch = r < if st.pinch {
                self.cfg.pinch_off
            } else {
                self.cfg.pinch_on
            };
            let size = self.plane_palm(lm);
            if self.cfg.hand_ref.is_none() {
                if self.sizes.len() >= self.cfg.auto_ref_window {
                    self.sizes.pop_front();
                }
                self.sizes.push_back(size);
            }
            st.rest = match self.hand_ref() {
                Some(rf) if rf > 0.0 => {
                    size / rf
                        < if st.rest {
                            self.cfg.ratio_up
                        } else {
                            self.cfg.ratio_down
                        }
                }
                _ => false,
            };

            // dwell: dedo quieto
            let radius = if st.dwell_latched {
                self.cfg.dwell_release_px
            } else {
                self.cfg.dwell_radius_px
            };
            let pointing = !self.cfg.dwell_require_pointing
                || dist(lm[WRIST], tip) > dist(lm[WRIST], lm[INDEX_PIP]);
            match st.dwell_anchor {
                Some(an) if dist(an, smooth) < radius && pointing => {}
                _ => {
                    st.dwell_anchor = Some(smooth);
                    st.still_since = t;
                    st.dwell_steps = 0;
                    st.dwell_latched = false;
                }
            }
            let dwell_on = self.cfg.use_dwell
                && pointing
                && !(st.touching && st.trigger != TouchTrigger::Dwell);
            if dwell_on && !st.dwell_latched {
                let progress = ((t - st.still_since) / self.cfg.dwell_s).clamp(0.0, 1.0);
                let step = self.cfg.dwell_step.max(1e-3);
                let k = ((progress + 1e-9) / step).floor() as u32;
                if k > st.dwell_steps {
                    st.dwell_steps = k;
                    let an = st.dwell_anchor.unwrap_or(smooth);
                    let p = if progress >= 1.0 {
                        1.0
                    } else {
                        k as f64 * step
                    };
                    ev.push(HandsEvent::Dwell {
                        hand_id: h.id.clone(),
                        point_px: an,
                        point_mm: self.to_mm(an),
                        progress: p,
                        t_capture_ns: tns,
                    });
                }
                if progress >= 1.0 {
                    st.dwell_latched = true;
                }
            }

            let (contact, trigger) = if self.cfg.use_pinch && st.pinch {
                (true, TouchTrigger::Pinch)
            } else if self.cfg.use_rest && st.rest {
                (true, TouchTrigger::Shadow)
            } else if self.cfg.use_dwell && st.dwell_latched {
                (true, TouchTrigger::Dwell)
            } else {
                (false, TouchTrigger::Dwell)
            };

            if !st.touching {
                st.cnt_on = if contact { st.cnt_on + 1 } else { 0 };
                if st.cnt_on >= self.cfg.frames_down || (contact && trigger == TouchTrigger::Dwell)
                {
                    st.touching = true;
                    st.cnt_on = 0;
                    st.cnt_off = 0;
                    st.trigger = trigger;
                    st.emitted = if trigger == TouchTrigger::Dwell {
                        st.dwell_anchor.unwrap_or(smooth)
                    } else {
                        smooth
                    };
                    st.blocked = match self.to_mm(st.emitted) {
                        Some(mm) => self
                            .cfg
                            .zones
                            .iter()
                            .any(|z| z.contains(mm, self.cfg.zone_margin)),
                        None => false,
                    };
                    if !st.blocked {
                        self.n += 1;
                        st.touch_id = format!("t{}", self.n);
                        ev.push(HandsEvent::TouchDown {
                            touch: self.info(&st, &h.id, TouchPhase::Down, ""),
                            t_capture_ns: tns,
                        });
                    }
                }
            } else {
                st.cnt_off = if contact { 0 } else { st.cnt_off + 1 };
                if st.cnt_off >= self.cfg.frames_up {
                    if !st.blocked {
                        let why = if st.trigger == TouchTrigger::Dwell {
                            "moved"
                        } else {
                            "released"
                        };
                        ev.push(HandsEvent::TouchUp {
                            touch: self.info(&st, &h.id, TouchPhase::Up, why),
                            t_capture_ns: tns,
                        });
                    }
                    st.touching = false;
                    st.blocked = false;
                    st.cnt_off = 0;
                } else if !st.blocked && dist(smooth, st.emitted) >= self.cfg.deadband_px {
                    st.emitted = smooth;
                    ev.push(HandsEvent::TouchMove {
                        touch: self.info(&st, &h.id, TouchPhase::Move, ""),
                        t_capture_ns: tns,
                    });
                }
            }
            self.states.insert(h.id.clone(), st);
        }

        // manos perdidas
        let lost: Vec<String> = self
            .states
            .iter()
            .filter(|(k, st)| !seen.contains(k) && t - st.last_seen > self.cfg.lost_s)
            .map(|(k, _)| k.clone())
            .collect();
        for k in lost {
            if let Some(st) = self.states.remove(&k) {
                if st.touching && !st.blocked {
                    ev.push(HandsEvent::TouchUp {
                        touch: self.info(&st, &k, TouchPhase::Up, "hand_lost"),
                        t_capture_ns: tns,
                    });
                }
            }
        }

        // episodios: manos → calma → HandsGone
        if !hands.is_empty() {
            if self.episode_start.is_none() {
                self.episode_start = Some(t);
                self.episode_start_ns = tns;
            }
            self.last_hand = t;
            self.last_hand_ns = tns;
        } else if self.episode_start.is_some() && t - self.last_hand > self.cfg.calm_s {
            self.episode_start = None;
            ev.push(HandsEvent::HandsGone {
                episode_start_ns: self.episode_start_ns,
                last_hand_ns: self.last_hand_ns,
                t_capture_ns: tns,
            });
        }
        ev
    }

    /// Toques vivos: (id del toque, mano, punto px).
    pub fn active(&self) -> Vec<(String, String, Point2)> {
        self.states
            .iter()
            .filter(|(_, s)| s.touching && !s.blocked)
            .map(|(k, s)| (s.touch_id.clone(), k.clone(), s.emitted))
            .collect()
    }
}
