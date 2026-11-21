//! Lazo del protocolo, común a `--headless` y `--display`: aplica lo que llega del WS a la escena,
//! pide snapshot si falta un rev, manda `presented` por cada rev en el primer cuadro presentado
//! que la contiene y `stats` cada 1 s.

use std::sync::mpsc::{self, Receiver, Sender};
use std::time::{Duration, Instant};

use pcx_contracts::{FromRenderer, Presented, Stats, ToRenderer};

use crate::scene::{Outcome, SceneState};
use crate::ws::{self, WsEvent};

/// Pedido de snapshot completo (extensión del protocolo; ver README).
pub fn resync_msg(have_rev: Option<i64>) -> String {
    serde_json::json!({"type": "resync", "have_rev": have_rev}).to_string()
}

pub struct Runtime {
    pub state: SceneState,
    rx: Option<Receiver<WsEvent>>,
    tx: Option<Sender<String>>,
    t0: Instant,
    last_resync: Option<Instant>,
    pub connected: bool,
    // stats
    window_start: Instant,
    frames: u32,
    intervals: Vec<f64>,
    last_present: Option<Instant>,
    pub dropped: i64,
    pub last_stats: Option<Stats>,
}

pub fn now_epoch_ms() -> f64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64() * 1000.0
}

fn percentile(v: &mut [f64], p: f64) -> f64 {
    if v.is_empty() {
        return 0.0;
    }
    v.sort_by(|a, b| a.partial_cmp(b).unwrap());
    v[((v.len() - 1) as f64 * p).round() as usize]
}

impl Runtime {
    /// Sin WS (escena desde archivos).
    pub fn offline() -> Self {
        Self {
            state: SceneState::default(),
            rx: None,
            tx: None,
            t0: Instant::now(),
            last_resync: None,
            connected: false,
            window_start: Instant::now(),
            frames: 0,
            intervals: vec![],
            last_present: None,
            dropped: 0,
            last_stats: None,
        }
    }

    pub fn connect(url: &str) -> Self {
        let (in_tx, in_rx) = mpsc::channel();
        let (out_tx, out_rx) = mpsc::channel();
        ws::spawn(url.to_owned(), in_tx, out_rx);
        Self { rx: Some(in_rx), tx: Some(out_tx), ..Self::offline() }
    }

    /// Segundos desde el arranque (reloj de animación y de TTL).
    pub fn now_s(&self) -> f64 {
        self.t0.elapsed().as_secs_f64()
    }

    fn send(&self, s: String) {
        if let Some(tx) = &self.tx {
            let _ = tx.send(s);
        }
    }

    pub fn apply(&mut self, msg: ToRenderer) -> Outcome {
        let now = self.now_s();
        let o = self.state.apply(msg, now);
        if o == Outcome::NeedSnapshot {
            self.request_snapshot();
        }
        o
    }

    fn request_snapshot(&mut self) {
        // Como máximo un pedido cada 250 ms mientras siga faltando.
        if self.last_resync.is_some_and(|t| t.elapsed() < Duration::from_millis(250)) {
            return;
        }
        self.last_resync = Some(Instant::now());
        eprintln!("[proto] falta un rev (tengo {:?}): pido snapshot", self.state.rev);
        self.send(resync_msg(self.state.rev));
    }

    /// Drena el canal del WS (llamar justo antes de dibujar, lo más tarde posible).
    pub fn pump(&mut self) {
        let mut msgs = vec![];
        if let Some(rx) = &self.rx {
            while let Ok(ev) = rx.try_recv() {
                msgs.push(ev);
            }
        }
        for ev in msgs {
            match ev {
                WsEvent::Connected => self.connected = true,
                // Desconectado: se mantiene la última escena.
                WsEvent::Disconnected => self.connected = false,
                WsEvent::Msg(m) => {
                    self.apply(m);
                }
            }
        }
        if self.state.need_snapshot && self.connected {
            self.request_snapshot();
        }
        let now = self.now_s();
        self.state.expire(now);
    }

    /// Tras presentar un cuadro: `presented` de las revs nuevas y `stats` cada 1 s.
    pub fn after_present(&mut self, t_present_ms: f64) {
        for rev in std::mem::take(&mut self.state.unpresented) {
            let m = FromRenderer::Presented(Presented { rev, t_present_ms });
            self.send(serde_json::to_string(&m).unwrap());
        }
        let now = Instant::now();
        if let Some(lp) = self.last_present {
            self.intervals.push((now - lp).as_secs_f64() * 1000.0);
        }
        self.last_present = Some(now);
        self.frames += 1;
        let el = self.window_start.elapsed().as_secs_f64();
        if el >= 1.0 {
            let st = Stats {
                fps: self.frames as f64 / el,
                frame_ms_p50: percentile(&mut self.intervals, 0.5),
                frame_ms_p95: percentile(&mut self.intervals, 0.95),
                dropped: self.dropped,
            };
            self.send(serde_json::to_string(&FromRenderer::Stats(st.clone())).unwrap());
            self.last_stats = Some(st);
            self.intervals.clear();
            self.frames = 0;
            self.dropped = 0;
            self.window_start = Instant::now();
        }
    }
}
