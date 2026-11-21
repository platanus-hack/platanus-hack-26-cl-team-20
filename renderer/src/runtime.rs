//! Lazo del protocolo, común a `--headless` y `--display`: aplica lo que llega del WS a la escena,
//! pide snapshot si falta un rev, manda `presented` por cada rev en el primer cuadro presentado
//! que la contiene, `stats` cada 1 s y `blank` → cuadros negros + `blanked{t_start_ms, t_end_ms}`.

use std::sync::mpsc::{self, Receiver, Sender};
use std::time::{Duration, Instant};

use pcx_contracts::{Blanked, FromRenderer, Pattern, PatternShown, Presented, Resync, Stats, ToRenderer};

use crate::engine::Engine;
use crate::scene::{Outcome, SceneState};
use crate::ws::{self, WsEvent};

/// Destino de cuadros. Permite probar el protocolo y las confirmaciones sin adaptador GPU.
pub trait FrameRenderer {
    type Target;
    fn scene(&mut self, state: &SceneState, t: f64, now_s: f64, target: &Self::Target, size: (u32, u32));
    fn black(&mut self, target: &Self::Target);
    fn pattern(&mut self, generation: u64, src: &str, target: &Self::Target) -> bool;
}

impl FrameRenderer for Engine {
    type Target = vello::wgpu::TextureView;
    fn scene(&mut self, state: &SceneState, t: f64, now_s: f64, target: &Self::Target, size: (u32, u32)) {
        self.render(state, t, now_s, target, size.0, size.1);
    }
    fn black(&mut self, target: &Self::Target) {
        self.render_black(target);
    }
    fn pattern(&mut self, generation: u64, src: &str, target: &Self::Target) -> bool {
        self.render_pattern(generation, src, target)
    }
}

/// Pedido de snapshot completo (contrato v2.1).
pub fn resync_msg(have_rev: Option<i64>) -> String {
    serde_json::to_string(&FromRenderer::Resync(Resync { have_rev })).unwrap()
}

/// Pedido de `blank` en curso; `t_start_ms` = presentación de su primer cuadro negro.
#[derive(Debug, Clone)]
struct BlankReq {
    request_id: String,
    t_start_ms: Option<f64>,
}

struct ActivePattern {
    message: Pattern,
    serial: u64,
    shown: bool,
}

struct PatternFrame {
    serial: u64,
    pattern_id: String,
    rendered: bool,
}

pub struct Runtime {
    pattern: Option<ActivePattern>,
    pattern_serial: u64,
    last_frame_pattern: Option<PatternFrame>,
    pub state: SceneState,
    blanks: Vec<BlankReq>,
    /// `ms` del último `blank` recibido, a armar en el próximo cuadro.
    blank_arm: Option<u32>,
    blank_until: Option<Instant>,
    last_frame_black: bool,
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
            pattern: None,
            pattern_serial: 0,
            last_frame_pattern: None,
            blanks: vec![],
            blank_arm: None,
            blank_until: None,
            last_frame_black: false,
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
        match msg {
            ToRenderer::Blank(b) => {
                self.blank_arm = Some(b.ms);
                self.blanks.push(BlankReq { request_id: b.request_id, t_start_ms: None });
                return Outcome::Applied;
            }
            ToRenderer::Pattern(p) => {
                self.pattern_serial += 1;
                self.pattern = Some(ActivePattern { message: p, serial: self.pattern_serial, shown: false });
                return Outcome::Applied;
            }
            ToRenderer::PatternClear(_) => {
                self.pattern = None;
                return Outcome::Applied;
            }
            _ => {}
        }
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

    /// Un cuadro: drena el WS, decide `blank` y dibuja en `target`. Después, presentar y llamar
    /// a `after_present`. `t` fija el reloj de animación (por defecto, el del renderer).
    pub fn draw<R: FrameRenderer>(&mut self, eng: &mut R, target: &R::Target, w: u32, h: u32, t: Option<f64>) {
        self.pump();
        self.draw_frame(eng, target, (w, h), t, now_epoch_ms());
    }

    fn draw_frame<R: FrameRenderer>(&mut self, eng: &mut R, target: &R::Target, size: (u32, u32), t: Option<f64>, epoch_ms: f64) {
        let now = self.now_s();
        self.last_frame_pattern = None;
        if self.pattern.as_ref().is_some_and(|p| p.message.until_ms.is_some_and(|u| epoch_ms >= u)) {
            self.pattern = None;
        }
        if self.blank_frame() {
            eng.black(target);
        } else if let Some(p) = &self.pattern {
            // path solo local; data_uri debe declarar explícitamente data:.
            let src = match (&p.message.image.path, &p.message.image.data_uri) {
                (Some(path), None) if !path.contains("://") && !path.starts_with("data:") => Some(path.as_str()),
                (None, Some(data)) if data.starts_with("data:") => Some(data.as_str()),
                _ => None,
            };
            let rendered = src.is_some_and(|src| eng.pattern(p.serial, src, target));
            if !rendered {
                eng.black(target);
            }
            // Incluso un patrón rechazado tapa la escena; nunca confirma un patrón no dibujado.
            self.last_frame_pattern = Some(PatternFrame { serial: p.serial, pattern_id: p.message.pattern_id.clone(), rendered });
        } else {
            eng.scene(&self.state, t.unwrap_or(now), now, target, size);
        }
    }

    /// ¿Este cuadro va negro (`blank`)? Llamar una vez por cuadro, justo antes de dibujar.
    pub fn blank_frame(&mut self) -> bool {
        let now = Instant::now();
        if let Some(ms) = self.blank_arm.take() {
            self.blank_until = Some(now + Duration::from_millis(ms as u64));
        }
        self.last_frame_black = self.blank_until.is_some_and(|u| now < u);
        self.last_frame_black
    }

    /// Tras presentar un cuadro: `presented` de las revs nuevas (si el cuadro mostró la escena),
    /// `blanked` al volver de un `blank` y `stats` cada 1 s.
    pub fn after_present(&mut self, t_present_ms: f64) {
        if !self.last_frame_black {
            self.blank_until = None;
            let done: Vec<BlankReq> = std::mem::take(&mut self.blanks);
            let (started, pending): (Vec<_>, Vec<_>) = done.into_iter().partition(|b| b.t_start_ms.is_some());
            self.blanks = pending;
            for b in started {
                let m =
                    FromRenderer::Blanked(Blanked { request_id: b.request_id, t_start_ms: b.t_start_ms.unwrap(), t_end_ms: t_present_ms });
                self.send(serde_json::to_string(&m).unwrap());
            }
        }
        if self.last_frame_black {
            for b in self.blanks.iter_mut().filter(|b| b.t_start_ms.is_none()) {
                b.t_start_ms = Some(t_present_ms);
            }
        } else if let Some(PatternFrame { serial, pattern_id, rendered }) = self.last_frame_pattern.take() {
            if rendered && self.pattern.as_ref().is_some_and(|p| p.serial == serial && !p.shown) {
                self.send(serde_json::to_string(&FromRenderer::PatternShown(PatternShown { pattern_id, t_present_ms })).unwrap());
                self.pattern.as_mut().unwrap().shown = true;
            }
        } else {
            for rev in std::mem::take(&mut self.state.unpresented) {
                let m = FromRenderer::Presented(Presented { rev, t_present_ms });
                self.send(serde_json::to_string(&m).unwrap());
            }
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

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{Value, json};

    #[derive(Default)]
    struct Output {
        content: String,
        valid_pattern: bool,
    }
    impl FrameRenderer for Output {
        type Target = ();
        fn scene(&mut self, _: &SceneState, _: f64, _: f64, _: &(), _: (u32, u32)) {
            self.content = "scene".into();
        }
        fn black(&mut self, _: &()) {
            self.content = "black".into();
        }
        fn pattern(&mut self, _: u64, src: &str, _: &()) -> bool {
            self.content = src.into();
            self.valid_pattern
        }
    }
    fn apply(rt: &mut Runtime, msg: Value) {
        assert_eq!(rt.apply(serde_json::from_value(msg).unwrap()), Outcome::Applied);
    }
    fn pattern(id: &str, until: Option<f64>) -> Value {
        json!({"type":"pattern", "pattern_id":id, "image":{"path":"calib.png"}, "until_ms":until})
    }
    fn render(rt: &mut Runtime, out: &mut Output, epoch: f64) {
        rt.draw_frame(out, &(), (100, 100), None, epoch);
    }
    fn messages(rx: &Receiver<String>) -> Vec<Value> {
        rx.try_iter().map(|s| serde_json::from_str(&s).unwrap()).collect()
    }
    fn runtime() -> (Runtime, Receiver<String>, Output) {
        let (tx, rx) = mpsc::channel();
        let mut rt = Runtime::offline();
        rt.tx = Some(tx);
        (rt, rx, Output { valid_pattern: true, ..Output::default() })
    }

    #[test]
    fn pattern_ack_only_after_first_presentation_and_scene_waits() {
        let (mut rt, rx, mut out) = runtime();
        apply(&mut rt, json!({"type":"scene", "rev":1, "surface":{"width_mm":100,"height_mm":100}}));
        apply(&mut rt, pattern("p1", None));
        render(&mut rt, &mut out, 100.0);
        assert_eq!(out.content, "calib.png");
        assert!(messages(&rx).is_empty(), "ack antes de presentar");
        rt.after_present(110.0);
        assert_eq!(messages(&rx), vec![json!({"type":"pattern_shown", "pattern_id":"p1", "t_present_ms":110.0})]);
        render(&mut rt, &mut out, 120.0);
        rt.after_present(130.0);
        assert!(messages(&rx).is_empty(), "ack duplicado");
        apply(&mut rt, pattern("p2", None));
        render(&mut rt, &mut out, 140.0);
        rt.after_present(150.0);
        assert_eq!(messages(&rx)[0]["pattern_id"], "p2");
        apply(&mut rt, json!({"type":"pattern_clear"}));
        render(&mut rt, &mut out, 160.0);
        assert_eq!(out.content, "scene");
        rt.after_present(170.0);
        assert_eq!(messages(&rx), vec![json!({"type":"presented","rev":1,"t_present_ms":170.0})]);
    }

    #[test]
    fn pattern_expiry_blank_and_failed_images() {
        let (mut rt, rx, mut out) = runtime();
        apply(&mut rt, pattern("active", Some(100.0)));
        render(&mut rt, &mut out, 90.0);
        assert_eq!(out.content, "calib.png");
        rt.after_present(95.0);
        assert_eq!(messages(&rx)[0]["pattern_id"], "active");
        render(&mut rt, &mut out, 100.0);
        assert_eq!(out.content, "scene");
        rt.after_present(110.0);
        assert!(messages(&rx).is_empty());
        apply(&mut rt, pattern("never-shown", Some(100.0)));
        render(&mut rt, &mut out, 120.0);
        rt.after_present(125.0);
        assert!(messages(&rx).is_empty());
        apply(&mut rt, pattern("failed", None));
        out.valid_pattern = false;
        render(&mut rt, &mut out, 120.0);
        assert_eq!(out.content, "black");
        rt.after_present(130.0);
        assert!(messages(&rx).is_empty());
        // Reintento exitoso confirma una sola vez, en el cuadro realmente dibujado.
        out.valid_pattern = true;
        render(&mut rt, &mut out, 140.0);
        rt.after_present(150.0);
        assert_eq!(messages(&rx)[0]["pattern_id"], "failed");
        apply(&mut rt, pattern("blocked", None));
        apply(&mut rt, json!({"type":"blank","request_id":"b","ms":100,"reason":"test"}));
        render(&mut rt, &mut out, 160.0);
        assert_eq!(out.content, "black");
        rt.after_present(170.0);
        assert!(messages(&rx).is_empty());
        rt.blank_until = Some(Instant::now() - Duration::from_millis(1));
        render(&mut rt, &mut out, 180.0);
        rt.after_present(190.0);
        let done = messages(&rx);
        assert_eq!(
            done,
            vec![
                json!({"type":"blanked","request_id":"b","t_start_ms":170.0,"t_end_ms":190.0}),
                json!({"type":"pattern_shown","pattern_id":"blocked","t_present_ms":190.0}),
            ]
        );
        apply(&mut rt, json!({"type":"pattern", "pattern_id":"remote", "image":{"path":"https://example.com/x.png"}}));
        render(&mut rt, &mut out, 200.0);
        assert_eq!(out.content, "black");
        rt.after_present(210.0);
        assert!(messages(&rx).is_empty());
    }

    #[test]
    fn resync_uses_contract_and_throttles() {
        let (mut rt, rx, _) = runtime();
        let diff = json!({"type":"scene_diff","rev":2,"base_rev":1});
        assert_eq!(rt.apply(serde_json::from_value(diff.clone()).unwrap()), Outcome::NeedSnapshot);
        assert_eq!(rt.apply(serde_json::from_value(diff).unwrap()), Outcome::NeedSnapshot);
        let msgs = messages(&rx);
        assert_eq!(msgs, vec![json!({"type":"resync","have_rev":null})]);
        assert!(matches!(serde_json::from_value::<FromRenderer>(msgs[0].clone()).unwrap(), FromRenderer::Resync(_)));
    }
}
