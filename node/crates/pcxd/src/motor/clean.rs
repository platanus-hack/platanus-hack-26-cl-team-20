//! Foto limpia del motor: `capture_clean` de `pcx-perception` con el apagón pedido a la web app por el WebSocket.
//!
//! - [`WsBlank`] implementa `BlankControl`: `request_blank` manda `blank{id, max_ms}` y `poll_blanked` devuelve la
//!   ventana cuando llegó el `blanked` de ese mismo `id`.
//! - [`CycleFrames`] implementa `FrameSource` con una compuerta ([`Gate`]): entre el pedido y `blanked` no entrega
//!   ningún cuadro, y después solo los capturados **tras** `blanked`. Si `blanked` no llega, `capture_clean` se
//!   queda sin cuadros y vence: no hay foto. No existe un camino que arme una foto limpia sin ese aviso.
//! - [`take_clean`] siempre termina con `unblank` si llegó a pedir el apagón.

use std::collections::HashMap;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use image::RgbImage;
use pcx_perception::clean::{
    capture_clean, BlankControl, BlankWindow, CleanConfig, CleanError, CleanMetrics, FrameSource, Mask,
};
use pcx_shm::{PixelFormat, SlotMeta};
use tokio::sync::mpsc::UnboundedSender;

use super::frames::{now_ns, FrameHub};
use super::proto::Out;

#[derive(Default)]
struct GateState {
    n: u64,
    /// `id` del `blank` en curso.
    armed: Option<String>,
    /// ns de época (reloj del motor) en que llegó su `blanked`.
    blanked_ns: Option<u64>,
}

/// Compuerta del apagón en curso, compartida entre el ciclo (hilo bloqueante) y quien recibe `blanked`.
#[derive(Default)]
pub struct Gate(Mutex<GateState>);

enum Phase {
    /// Todavía no se pidió `blank`.
    Before,
    /// `blank` pedido; `blanked` no llegó.
    Waiting,
    Blanked(u64),
}

impl Gate {
    fn arm(&self) -> String {
        let mut g = self.0.lock().unwrap();
        g.n += 1;
        let id = format!("b-{}", g.n);
        g.armed = Some(id.clone());
        g.blanked_ns = None;
        id
    }

    /// Llegó `blanked{id}`. Solo cuenta el del apagón en curso, y una vez.
    pub fn on_blanked(&self, id: &str) -> bool {
        let mut g = self.0.lock().unwrap();
        if g.armed.as_deref() != Some(id) || g.blanked_ns.is_some() {
            return false;
        }
        g.blanked_ns = Some(now_ns());
        true
    }

    /// `id` del apagón en curso (hay que mandar `unblank` si el motor se detiene).
    pub fn armed(&self) -> Option<String> {
        self.0.lock().unwrap().armed.clone()
    }

    fn blanked(&self, id: &str) -> Option<u64> {
        let g = self.0.lock().unwrap();
        (g.armed.as_deref() == Some(id)).then_some(g.blanked_ns).flatten()
    }

    fn phase(&self) -> Phase {
        let g = self.0.lock().unwrap();
        match (&g.armed, g.blanked_ns) {
            (None, _) => Phase::Before,
            (Some(_), None) => Phase::Waiting,
            (Some(_), Some(t)) => Phase::Blanked(t),
        }
    }

    fn disarm(&self) {
        let mut g = self.0.lock().unwrap();
        g.armed = None;
        g.blanked_ns = None;
    }
}

/// `BlankControl` sobre el WebSocket.
struct WsBlank<'a> {
    gate: &'a Gate,
    out: &'a UnboundedSender<Out>,
    /// Tope que se le da a la web app para volver sola de negro si el motor desaparece.
    max_ms: u32,
    sent: Option<(String, Instant)>,
}

impl BlankControl for WsBlank<'_> {
    fn request_blank(&mut self, _ms: u32) -> Result<String, String> {
        let id = self.gate.arm();
        self.out
            .send(Out::Blank { id: id.clone(), max_ms: self.max_ms })
            .map_err(|_| "el motor se detuvo".to_string())?;
        self.sent = Some((id.clone(), Instant::now()));
        Ok(id)
    }

    fn poll_blanked(&mut self, request_id: &str) -> Option<BlankWindow> {
        // El negro dura hasta `unblank` (o `max_ms`).
        self.gate
            .blanked(request_id)
            .map(|t| BlankWindow { t_start_ms: t as f64 / 1e6, t_end_ms: t as f64 / 1e6 + self.max_ms as f64 })
    }
}

/// Cuadros del ciclo: frescos antes del pedido, ninguno mientras se espera `blanked`, y después solo posteriores.
struct CycleFrames<'a> {
    hub: &'a FrameHub,
    gate: &'a Gate,
    t_start_ns: u64,
    /// Último `seq` descartado por viejo (para no releerlo en un bucle caliente).
    skipped: u64,
    /// `seq` → `t_capture_ns` de lo entregado.
    seen: HashMap<u64, u64>,
}

impl FrameSource for CycleFrames<'_> {
    fn now_ns(&self) -> u64 {
        now_ns()
    }

    fn next_frame(
        &mut self,
        after_seq: u64,
        timeout: Duration,
        buf: &mut Vec<u8>,
    ) -> Result<Option<SlotMeta>, CleanError> {
        let floor = match self.gate.phase() {
            Phase::Before => self.t_start_ns,
            Phase::Waiting => {
                std::thread::sleep(timeout);
                return Ok(None);
            }
            Phase::Blanked(t) => t,
        };
        self.hub.demand();
        let Some((seq, f)) = self.hub.wait_newer(after_seq.max(self.skipped), timeout) else { return Ok(None) };
        if f.t_capture_ns < floor {
            self.skipped = seq;
            return Ok(None);
        }
        let (w, h) = f.img.dimensions();
        buf.clear();
        buf.extend_from_slice(f.img.as_raw());
        self.seen.insert(seq, f.t_capture_ns);
        Ok(Some(SlotMeta {
            seq,
            t_capture_ns: f.t_capture_ns,
            w,
            h,
            stride: w * 3,
            format: PixelFormat::Rgb8,
            slot: 0,
            gen: 0,
        }))
    }
}

#[derive(Debug, Clone)]
pub struct CleanSettings {
    pub clean: CleanConfig,
    /// `max_ms` de `blank`.
    pub blank_max_ms: u32,
}

impl CleanSettings {
    /// `blank_ms`: ventana de captura tras el pedido. `require_drop`: sin caída de luminancia no hay foto (si no,
    /// con `blanked` recibido y la escena ya oscura se eligen los cuadros por tiempos).
    pub fn new(blank_ms: u32, require_drop: bool) -> Self {
        let clean = CleanConfig { blank_ms, assume_dark_when_flat: !require_drop, ..CleanConfig::default() };
        let blank_max_ms = clean.blank_ms + clean.max_latency_ms + clean.margin_ms + 700;
        CleanSettings { clean, blank_max_ms }
    }
}

#[derive(Debug)]
pub struct CleanShot {
    pub img: RgbImage,
    pub t_ns: u64,
    pub metrics: CleanMetrics,
    /// Del pedido de `blank` a `blanked`.
    pub blank_ms: f64,
    /// De `blanked` a tener la foto.
    pub capture_ms: f64,
}

#[derive(Debug)]
pub enum ShotError {
    /// La web app no contestó `blanked`: no hay foto.
    BlankedTimeout {
        waited_ms: f64,
    },
    Clean(CleanError),
    Internal(String),
}

impl ShotError {
    pub fn code(&self) -> &'static str {
        match self {
            ShotError::BlankedTimeout { .. } => "blanked_timeout",
            ShotError::Clean(CleanError::NoFrames(_)) => "no_frames",
            ShotError::Clean(CleanError::BlackoutNotSeen { .. } | CleanError::NoBlanked { .. }) => "no_luminance_drop",
            ShotError::Clean(CleanError::TooFewFrames { .. }) => "too_few_frames",
            ShotError::Clean(_) | ShotError::Internal(_) => "clean_failed",
        }
    }
}

impl std::fmt::Display for ShotError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ShotError::BlankedTimeout { waited_ms } => {
                write!(f, "la web app no contestó `blanked` en {waited_ms:.0} ms; no hay foto limpia")
            }
            ShotError::Clean(e) => write!(f, "{e}"),
            ShotError::Internal(e) => write!(f, "{e}"),
        }
    }
}

/// Toma la foto limpia: referencia → `blank` → `blanked` → ≥ 3 cuadros posteriores con la caída de luminancia
/// verificada → mediana → `unblank`. Bloquea (hasta ~1 s si `blanked` no llega).
pub fn take_clean(
    hub: &FrameHub,
    gate: &Gate,
    out: &UnboundedSender<Out>,
    mask: Option<&Mask>,
    cfg: &CleanSettings,
) -> Result<CleanShot, ShotError> {
    gate.disarm();
    let mut src = CycleFrames { hub, gate, t_start_ns: now_ns(), skipped: 0, seen: HashMap::new() };
    let mut ctl = WsBlank { gate, out, max_ms: cfg.blank_max_ms, sent: None };
    let res = capture_clean(&mut src, &mut ctl, mask, &cfg.clean);
    let done = Instant::now();
    let sent = ctl.sent.take();
    let blanked = sent.as_ref().and_then(|(id, _)| gate.blanked(id));
    if let Some((id, _)) = &sent {
        let _ = out.send(Out::Unblank { id: id.clone() });
    }
    gate.disarm();
    let waited_ms = sent.as_ref().map_or(0.0, |(_, t)| done.duration_since(*t).as_secs_f64() * 1e3);
    let photo = match res {
        Ok(p) => p,
        Err(_) if sent.is_some() && blanked.is_none() => return Err(ShotError::BlankedTimeout { waited_ms }),
        Err(e) => return Err(ShotError::Clean(e)),
    };
    // Por la compuerta esto no puede fallar; se comprueba igual antes de dar la foto por limpia.
    let Some(t_blanked) = blanked else { return Err(ShotError::BlankedTimeout { waited_ms }) };
    if photo.seqs.iter().any(|s| src.seen.get(s).is_none_or(|t| *t < t_blanked)) {
        return Err(ShotError::Internal("la foto usó cuadros anteriores a `blanked`".into()));
    }
    let (w, h, t_ns, metrics) = (photo.w, photo.h, photo.t_capture_ns, photo.metrics.clone());
    // De `blank` a `blanked`, y de ahí a la foto, en el reloj del motor.
    let capture_ms = (now_ns().saturating_sub(t_blanked)) as f64 / 1e6;
    let blank_ms = (waited_ms - capture_ms).max(0.0);
    let img =
        RgbImage::from_raw(w, h, photo.into_rgb()).ok_or_else(|| ShotError::Internal("foto mal formada".into()))?;
    Ok(CleanShot { img, t_ns, metrics, blank_ms, capture_ms })
}

/// Máscara de la zona proyectada para `capture_clean`, del tamaño del cuadro.
pub fn mask_from(data: Vec<u8>, w: u32, h: u32) -> Mask {
    Mask { w, h, data }
}

#[cfg(test)]
mod tests {
    use super::*;
    use pcx_capture::Frame;
    use std::sync::Arc;

    fn feed(hub: &Arc<FrameHub>, stop: Arc<std::sync::atomic::AtomicBool>, dark: Arc<std::sync::atomic::AtomicBool>) {
        use std::sync::atomic::Ordering::Relaxed;
        let hub = hub.clone();
        std::thread::spawn(move || {
            while !stop.load(Relaxed) {
                let v = if dark.load(Relaxed) { 30 } else { 150 };
                let img = RgbImage::from_pixel(64, 48, image::Rgb([v, v, v]));
                hub.put(Arc::new(Frame { img, t_capture_ns: now_ns() }));
                std::thread::sleep(Duration::from_millis(8));
            }
        });
    }

    fn settings() -> CleanSettings {
        let mut s = CleanSettings::new(200, false);
        s.clean.min_latency_ms = 0;
        s.clean.max_latency_ms = 150;
        s
    }

    #[test]
    fn sin_blanked_no_hay_foto_y_se_manda_unblank() {
        use std::sync::atomic::AtomicBool;
        let (hub, _need) = FrameHub::new();
        let (stop, dark) = (Arc::new(AtomicBool::new(false)), Arc::new(AtomicBool::new(false)));
        feed(&hub, stop.clone(), dark.clone());
        let gate = Gate::default();
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        // La escena se oscurece igual (como si el proyector se hubiera apagado), pero nadie avisa `blanked`.
        dark.store(true, std::sync::atomic::Ordering::Relaxed);
        let e = take_clean(&hub, &gate, &tx, None, &settings()).unwrap_err();
        stop.store(true, std::sync::atomic::Ordering::Relaxed);
        assert_eq!(e.code(), "blanked_timeout", "{e}");
        let id = match rx.try_recv().unwrap() {
            Out::Blank { id, .. } => id,
            o => panic!("{o:?}"),
        };
        assert_eq!(rx.try_recv().unwrap(), Out::Unblank { id });
        assert!(gate.armed().is_none());
    }

    #[test]
    fn con_blanked_usa_solo_cuadros_posteriores() {
        use std::sync::atomic::{AtomicBool, Ordering::Relaxed};
        let (hub, _need) = FrameHub::new();
        let (stop, dark) = (Arc::new(AtomicBool::new(false)), Arc::new(AtomicBool::new(false)));
        feed(&hub, stop.clone(), dark.clone());
        let gate = Arc::new(Gate::default());
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let (g2, d2) = (gate.clone(), dark.clone());
        // «Web app»: al ver `blank` apaga, espera y recién entonces contesta `blanked`.
        let app = std::thread::spawn(move || loop {
            match rx.blocking_recv() {
                Some(Out::Blank { id, .. }) => {
                    d2.store(true, Relaxed);
                    std::thread::sleep(Duration::from_millis(40));
                    assert!(g2.on_blanked(&id));
                    assert!(!g2.on_blanked(&id), "un solo blanked por apagón");
                    assert!(!g2.on_blanked("b-999"));
                }
                Some(Out::Unblank { .. }) => {
                    d2.store(false, Relaxed);
                    return;
                }
                _ => return,
            }
        });
        let shot = take_clean(&hub, &gate, &tx, None, &settings()).unwrap();
        stop.store(true, Relaxed);
        app.join().unwrap();
        assert!(shot.metrics.frames_used >= 3);
        assert!(shot.metrics.luminance_drop > 100.0, "{:?}", shot.metrics);
        assert!(shot.img.pixels().all(|p| p.0 == [30, 30, 30]), "la foto es la escena oscura");
        assert!(shot.blank_ms >= 35.0, "esperó a blanked: {}", shot.blank_ms);
    }
}
