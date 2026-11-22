//! Orquestador del motor: un actor que decide cuándo mirar la mesa y publica lo que cambió.
//!
//! Disparos de un ciclo: las manos se van (calma de 250 ms), cada 45 s, `perceive`, una calibración nueva y
//! `look{clean:true}`. El ciclo (foto limpia + `Perceptor`) corre en un hilo bloqueante; mientras tanto el actor
//! sigue atendiendo el WebSocket (`blanked`, `hands`, `look`…). Estados que se informan en `status`:
//! `idle → calm → blank → capturing → perceiving → idle`.

use std::collections::{BTreeMap, HashMap};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use pcx_capture::Frame;
use pcx_perception::clean::Mask;
use pcx_perception::pipeline_api::{CalibIn, Lens, ObjectOut, PerceptionOut, Perceptor};
use serde_json::Value;
use tokio::sync::{mpsc, oneshot};
use tokio_util::sync::CancellationToken;

use super::clean::{mask_from, take_clean, CleanSettings, Gate};
use super::frames::{jpeg_b64, now_ns, Camera, FrameHub, MAX_SIDE};
use super::geom;
use super::proto::{id_text, CameraRole, In, Models, ObjectMsg, Out, State, PROTO_V};
use super::scenes::{SceneRun, Scenes, CENITAL, LATERAL};

const LOG: &str = "[pcx motor]";
/// Clientes simultáneos (pestañas de la web app).
pub const MAX_CLIENTS: usize = 8;
/// Mensajes en cola por cliente; el que no los lee se desconecta.
pub const CLIENT_QUEUE: usize = 64;
const JPEG_QUALITY: u8 = 85;
/// Ciclos extra encadenados tras un cambio: uno retira lo que se fue y otro confirma que la mesa quedó quieta.
const MAX_SETTLES: u32 = 2;

#[derive(Debug, Clone)]
pub struct EngineConfig {
    /// Calma sin manos antes de la foto.
    pub calm: Duration,
    /// Ciclo periódico; cero lo apaga.
    pub review_every: Duration,
    pub clean: CleanSettings,
    /// Diferencia media máxima entre el primer y el último cuadro de la foto; sobre eso algo se movió.
    pub max_spread: f32,
    pub models: Models,
    /// Tras un ciclo que cambió algo se pide otro a este plazo: el `Pipeline` retira un objeto recién en el segundo
    /// ciclo sin verlo. Cero lo apaga.
    pub settle_after: Duration,
}

impl Default for EngineConfig {
    fn default() -> Self {
        EngineConfig {
            calm: Duration::from_millis(250),
            review_every: Duration::from_secs(45),
            clean: CleanSettings::new(300, false),
            max_spread: 6.0,
            models: Models::default(),
            settle_after: Duration::from_secs(1),
        }
    }
}

pub enum EngineMsg {
    Attach { tx: mpsc::Sender<String>, reply: oneshot::Sender<Option<u64>> },
    Detach { conn: u64 },
    In { conn: u64, msg: In },
}

#[derive(Clone)]
pub struct EngineHandle(pub mpsc::Sender<EngineMsg>);

impl EngineHandle {
    /// Registra un cliente ya autenticado. `None`: el motor se detuvo o no caben más.
    pub async fn attach(&self, tx: mpsc::Sender<String>) -> Option<u64> {
        let (reply, rx) = oneshot::channel();
        self.0.send(EngineMsg::Attach { tx, reply }).await.ok()?;
        rx.await.ok().flatten()
    }
    pub async fn detach(&self, conn: u64) {
        let _ = self.0.send(EngineMsg::Detach { conn }).await;
    }
    pub async fn send(&self, conn: u64, msg: In) {
        let _ = self.0.send(EngineMsg::In { conn, msg }).await;
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
enum Reason {
    Periodic,
    /// Ciclo extra después de un cambio (ver `EngineConfig::settle_after`).
    Settle,
    Retry,
    /// Se activó una escena: su módulo tiene que ver la mesa.
    Scene,
    Calib,
    HandsGone,
    Forced,
}

struct Client {
    tx: mpsc::Sender<String>,
    hands: bool,
}

struct CycleOut {
    perceptor: Box<dyn Perceptor>,
    /// Foto limpia (para `look{clean:true}`), si se llegó a tomar.
    clean: Option<Arc<Frame>>,
    perception: Option<PerceptionOut>,
    /// Lo que devolvió el módulo de la escena activa sobre esta foto.
    scene: Option<SceneRun>,
    error: Option<(String, String)>,
    stages: Vec<(String, f64)>,
    /// La foto se descartó porque algo se movió: conviene reintentar.
    retry: bool,
    reason: Reason,
    started: Instant,
}

pub struct Engine {
    cfg: EngineConfig,
    hub: Arc<FrameHub>,
    camera: Camera,
    gate: Arc<Gate>,
    clients: HashMap<u64, Client>,
    next_conn: u64,
    calib: Option<CalibIn>,
    mask: Option<Arc<Mask>>,
    objects: BTreeMap<String, ObjectMsg>,
    rev: u64,
    state: State,
    last_cycle_ms: Option<f64>,
    stages: BTreeMap<String, f64>,
    perceptor: Option<Box<dyn Perceptor>>,
    want: Option<Reason>,
    /// Desde cuándo no hay manos (`None` = hay manos ahora).
    hands_absent_since: Option<Instant>,
    last_end: Option<Instant>,
    retry_at: Option<Instant>,
    retries: u32,
    /// Ciclos `Settle` seguidos; se corta en [`MAX_SETTLES`] para no parpadear sin fin con una escena ruidosa.
    settles: u32,
    disturbed: Arc<AtomicBool>,
    reset_pending: bool,
    resize_pending: Option<(u32, u32)>,
    /// `look{clean:true}` esperando la próxima foto limpia.
    clean_looks: Vec<(u64, Value)>,
    scenes: Arc<Scenes>,
    scene_names: Vec<String>,
    /// Buzón de la cámara lateral, si hay.
    lateral: Option<Arc<FrameHub>>,
}

impl Engine {
    pub fn new(
        cfg: EngineConfig,
        hub: Arc<FrameHub>,
        camera: Camera,
        perceptor: Box<dyn Perceptor>,
        scenes: Arc<Scenes>,
        lateral: Option<Arc<FrameHub>>,
    ) -> Self {
        Engine {
            scene_names: scenes.names(),
            scenes,
            lateral,
            cfg,
            hub,
            camera,
            gate: Arc::new(Gate::default()),
            clients: HashMap::new(),
            next_conn: 0,
            calib: None,
            mask: None,
            objects: BTreeMap::new(),
            rev: 0,
            state: State::Idle,
            last_cycle_ms: None,
            stages: BTreeMap::new(),
            perceptor: Some(perceptor),
            want: None,
            hands_absent_since: Some(Instant::now()),
            last_end: None,
            retry_at: None,
            retries: 0,
            settles: 0,
            disturbed: Arc::new(AtomicBool::new(false)),
            reset_pending: false,
            resize_pending: None,
            clean_looks: Vec::new(),
        }
    }

    pub async fn run(
        mut self,
        mut rx: mpsc::Receiver<EngineMsg>,
        mut need: mpsc::UnboundedReceiver<String>,
        mut scene_rx: mpsc::UnboundedReceiver<SceneRun>,
        stop: CancellationToken,
    ) {
        let (cyc_tx, mut cyc_rx) = mpsc::unbounded_channel::<Out>();
        let mut cycle: Option<tokio::task::JoinHandle<CycleOut>> = None;
        loop {
            if cycle.is_none() {
                cycle = self.maybe_start(&cyc_tx);
            }
            let wake = if cycle.is_none() { self.next_deadline() } else { None };
            tokio::select! {
                _ = stop.cancelled() => break,
                m = rx.recv() => match m {
                    Some(m) => self.on_msg(m, cycle.is_some()).await,
                    None => break,
                },
                Some(out) = cyc_rx.recv() => self.on_cycle_msg(out),
                Some(id) = need.recv() => self.broadcast(&Out::NeedFrame { id }),
                Some(run) = scene_rx.recv() => self.on_scene(run),
                r = async { cycle.as_mut().expect("ciclo en curso").await }, if cycle.is_some() => {
                    cycle = None;
                    // Lo que el ciclo haya dejado en cola (`unblank`) sale antes que su resultado.
                    while let Ok(out) = cyc_rx.try_recv() {
                        self.on_cycle_msg(out);
                    }
                    match r {
                        Ok(out) => self.on_cycle_done(out).await,
                        Err(e) => {
                            eprintln!("{LOG} el ciclo terminó con pánico: {e}");
                            std::process::exit(101);
                        }
                    }
                }
                _ = async { tokio::time::sleep_until(wake.expect("hay plazo").into()).await }, if wake.is_some() => {}
            }
        }
        // Nunca dejar la mesa a oscuras al salir.
        if let Some(id) = self.gate.armed() {
            self.broadcast(&Out::Unblank { id });
        }
        if let Some(c) = cycle {
            let _ = tokio::time::timeout(Duration::from_secs(3), c).await;
        }
        self.camera.stop().await;
    }

    // ------------------------------------------------------------------------------------------ salida

    fn send_to(&mut self, conn: u64, out: &Out) {
        let Some(c) = self.clients.get(&conn) else { return };
        if c.tx.try_send(out.json()).is_err() {
            self.drop_client(conn);
        }
    }

    fn broadcast(&mut self, out: &Out) {
        let text = out.json();
        let dead: Vec<u64> =
            self.clients.iter().filter(|(_, c)| c.tx.try_send(text.clone()).is_err()).map(|(k, _)| *k).collect();
        for conn in dead {
            self.drop_client(conn);
        }
    }

    fn drop_client(&mut self, conn: u64) {
        if self.clients.remove(&conn).is_some() {
            self.clean_looks.retain(|(c, _)| *c != conn);
            self.refresh_hands();
        }
    }

    fn status(&self) -> Out {
        Out::Status {
            state: self.state,
            last_cycle_ms: self.last_cycle_ms,
            stages_ms: self.stages.clone(),
            models: self.cfg.models.clone(),
        }
    }

    fn set_state(&mut self, s: State) {
        if self.state != s {
            self.state = s;
            let st = self.status();
            self.broadcast(&st);
        }
    }

    // ------------------------------------------------------------------------------------------ entrada

    async fn on_msg(&mut self, m: EngineMsg, cycling: bool) {
        match m {
            EngineMsg::Attach { tx, reply } => {
                if self.clients.len() >= MAX_CLIENTS {
                    let _ = reply.send(None);
                    return;
                }
                self.next_conn += 1;
                let conn = self.next_conn;
                self.clients.insert(conn, Client { tx, hands: false });
                let welcome = Out::Welcome {
                    v: PROTO_V,
                    motor: format!("pcx-motor/{}", env!("CARGO_PKG_VERSION")),
                    camera: self.hub.camera(),
                    models: self.cfg.models.clone(),
                    needs_frames: self.hub.needs_frames(),
                    scenes: self.scene_names.clone(),
                    cameras: self.cameras(),
                };
                self.send_to(conn, &welcome);
                // Justo después de `welcome`: todos los objetos vigentes (una consola recargada parte de ahí).
                let snap =
                    Out::Objects { rev: self.rev, upsert: self.objects.values().cloned().collect(), remove: vec![] };
                self.send_to(conn, &snap);
                let st = self.status();
                self.send_to(conn, &st);
                let _ = reply.send(Some(conn));
            }
            EngineMsg::Detach { conn } => self.drop_client(conn),
            EngineMsg::In { conn, msg } => self.on_in(conn, msg, cycling).await,
        }
    }

    async fn on_in(&mut self, conn: u64, msg: In, cycling: bool) {
        match msg {
            In::Hello { .. } | In::Frame { .. } | In::Unblanked { .. } => {}
            In::Calib { camera_px, matrix_px_to_mm, lens, surface_mm } => {
                let lens = lens.map(|l| Lens { f: l.f, cx: l.cx, cy: l.cy, k1: l.k1, k2: l.k2 });
                match check_calib(camera_px, matrix_px_to_mm, lens, surface_mm) {
                    Ok(c) => {
                        if cycling {
                            self.resize_pending = Some(c.camera_px);
                        } else {
                            self.camera.ensure_size(c.camera_px).await;
                        }
                        let (w, h) = c.camera_px;
                        self.mask = geom::surface_mask(&c, w, h, 4).map(|d| Arc::new(mask_from(d, w, h)));
                        self.calib = Some(c);
                        self.sync_world();
                        self.want = self.want.max(Some(Reason::Calib));
                    }
                    Err(e) => self.send_to(conn, &Out::error("bad_calib", e)),
                }
            }
            In::Blanked { id, .. } => {
                if self.gate.on_blanked(&id_text(&id)) {
                    if let Some(s) = self.camera.synthetic().filter(|s| s.auto()) {
                        s.set_projector(false);
                    }
                    self.set_state(State::Capturing);
                }
            }
            In::Hands { present, .. } => {
                if let Some(c) = self.clients.get_mut(&conn) {
                    c.hands = present;
                }
                self.refresh_hands();
            }
            In::Perceive {} => {
                if self.calib.is_none() {
                    self.send_to(
                        conn,
                        &Out::error("no_calib", "falta `calib`: sin calibración no hay coordenadas en mm"),
                    );
                } else {
                    self.want = Some(Reason::Forced);
                }
            }
            In::Scene { name } => {
                let sw = self.scenes.activate(name.as_deref());
                if let (Some(n), None) = (&name, &sw.active) {
                    eprintln!("{LOG} escena «{n}»: no hay módulo con ese nombre (hay: {:?})", self.scene_names);
                }
                if let Some(prev) = sw.previous {
                    self.remove_scene_objects(&prev);
                }
                if sw.changed && sw.active.is_some() {
                    self.want = self.want.max(Some(Reason::Scene));
                }
            }
            In::Reset {} => {
                if cycling {
                    self.reset_pending = true;
                } else {
                    self.do_reset();
                }
            }
            In::Look { id, clean: true } => {
                self.clean_looks.push((conn, id));
                self.want = Some(Reason::Forced);
            }
            In::Look { id, clean: false } => self.look_live(conn, id),
        }
    }

    fn refresh_hands(&mut self) {
        let present = self.clients.values().any(|c| c.hands);
        match (present, self.hands_absent_since) {
            (true, Some(_)) => {
                self.hands_absent_since = None;
                self.disturbed.store(true, Ordering::Relaxed);
                if self.state == State::Calm {
                    self.set_state(State::Idle);
                }
            }
            (false, None) => {
                self.hands_absent_since = Some(Instant::now());
                self.want = self.want.max(Some(Reason::HandsGone));
            }
            _ => {}
        }
    }

    fn do_reset(&mut self) {
        if let Some(p) = self.perceptor.as_mut() {
            p.reset();
        }
        self.scenes.reset();
        self.scenes.set_world(vec![], self.calib.clone());
        let remove: Vec<String> = std::mem::take(&mut self.objects).into_keys().collect();
        if !remove.is_empty() {
            self.rev += 1;
            let rev = self.rev;
            self.broadcast(&Out::Objects { rev, upsert: vec![], remove });
        }
    }

    /// `look{clean:false}`: el cuadro más reciente de la cámara, tal como está (con lo proyectado encima).
    fn look_live(&mut self, conn: u64, id: Value) {
        let Some(tx) = self.clients.get(&conn).map(|c| c.tx.clone()) else { return };
        let hub = self.hub.clone();
        tokio::task::spawn_blocking(move || {
            let fresh = now_ns().saturating_sub(1_000_000_000);
            let out = match hub.wait_fresh(fresh, Duration::from_secs(2)) {
                Some(f) => look_frame(id, &f),
                None => Out::Error {
                    code: "no_frames".into(),
                    message: "la cámara no entregó cuadros".into(),
                    id: Some(id),
                },
            };
            let _ = tx.blocking_send(out.json());
        });
    }

    // ------------------------------------------------------------------------------------------ ciclo

    /// Qué ciclo toca ahora, si alguno. Deja el estado en `calm` mientras se espera la calma.
    fn due(&mut self, now: Instant) -> Option<Reason> {
        if self.clients.is_empty() {
            return None;
        }
        if self.want == Some(Reason::Forced) {
            return Some(Reason::Forced);
        }
        self.calib.as_ref()?;
        let calm_ok = self.hands_absent_since.is_some_and(|t| now.duration_since(t) >= self.cfg.calm);
        let periodic = !self.cfg.review_every.is_zero()
            && self.last_end.is_some_and(|t| now.duration_since(t) >= self.cfg.review_every);
        let wanted = match self.want {
            Some(Reason::Retry | Reason::Settle) if self.retry_at.is_some_and(|t| now < t) => None,
            w => w,
        };
        let reason = wanted.or(periodic.then_some(Reason::Periodic))?;
        if !calm_ok {
            if self.hands_absent_since.is_some() && reason != Reason::Periodic {
                self.set_state(State::Calm);
            }
            return None;
        }
        Some(reason)
    }

    fn next_deadline(&self) -> Option<Instant> {
        if self.clients.is_empty() || self.calib.is_none() {
            return None;
        }
        // Con manos encima no hay plazo: el próximo paso lo da `hands{present:false}`.
        let calm = self.hands_absent_since? + self.cfg.calm;
        let periodic =
            (!self.cfg.review_every.is_zero()).then(|| self.last_end.map(|t| t + self.cfg.review_every)).flatten();
        let wanted = match self.want {
            Some(Reason::Retry | Reason::Settle) => self.retry_at,
            Some(_) => Some(calm),
            None => None,
        };
        let now = Instant::now();
        [wanted, periodic].into_iter().flatten().map(|t| t.max(calm).max(now + Duration::from_millis(1))).min()
    }

    fn maybe_start(&mut self, cyc_tx: &mpsc::UnboundedSender<Out>) -> Option<tokio::task::JoinHandle<CycleOut>> {
        let reason = self.due(Instant::now())?;
        let mut perceptor = self.perceptor.take()?;
        self.want = None;
        self.retry_at = None;
        self.disturbed.store(false, Ordering::Relaxed);
        let calm_ms = self.hands_absent_since.map_or(0.0, |t| t.elapsed().as_secs_f64() * 1e3);
        let (hub, gate, out, cfg) = (self.hub.clone(), self.gate.clone(), cyc_tx.clone(), self.cfg.clone());
        let (calib, mask, disturbed) = (self.calib.clone(), self.mask.clone(), self.disturbed.clone());
        let (scenes, base) = (self.scenes.clone(), self.base_objects());
        let forced = reason == Reason::Forced;
        Some(tokio::task::spawn_blocking(move || {
            let started = Instant::now();
            let mut o = CycleOut {
                perceptor: Box::new(Idle),
                clean: None,
                perception: None,
                scene: None,
                error: None,
                stages: vec![("calm".into(), calm_ms.min(cfg.calm.as_secs_f64() * 1e3))],
                retry: false,
                reason,
                started,
            };
            // La máscara solo vale si la cámara ya entrega el tamaño de la calibración.
            let size = hub.latest().map(|(_, f)| f.img.dimensions());
            let mask = mask.filter(|m| size.is_none_or(|s| s == (m.w, m.h)));
            match take_clean(&hub, &gate, &out, mask.as_deref(), &cfg.clean) {
                Err(e) => o.error = Some((e.code().into(), e.to_string())),
                Ok(shot) => {
                    o.stages.extend([
                        ("blank".to_string(), shot.blank_ms),
                        ("capture".to_string(), shot.capture_ms),
                        ("median".to_string(), shot.metrics.median_ms),
                    ]);
                    let moved = shot.metrics.frame_spread > cfg.max_spread;
                    if !forced && (moved || disturbed.load(Ordering::Relaxed)) {
                        o.retry = true;
                        o.error = Some((
                            "scene_moved".into(),
                            format!(
                                "algo se movió durante la foto (dispersión {:.1}); se descarta",
                                shot.metrics.frame_spread
                            ),
                        ));
                    } else {
                        if let Some(c) = &calib {
                            let t = Instant::now();
                            match perceptor.on_clean(&shot.img, c, shot.t_ns) {
                                Ok(p) => {
                                    o.stages.push(("perceive".into(), t.elapsed().as_secs_f64() * 1e3));
                                    o.stages.extend(p.stages_ms.iter().cloned());
                                    // El módulo de la escena activa ve la misma foto y los objetos ya al día.
                                    let now = merged(base, &p);
                                    o.scene = scenes.on_clean(&shot.img, &now, c, shot.t_ns);
                                    if let Some(run) = &o.scene {
                                        o.stages.push(("scene".into(), run.ms));
                                    }
                                    o.perception = Some(p);
                                }
                                Err(e) => o.error = Some(("perception_failed".into(), e)),
                            }
                        }
                        o.clean = Some(Arc::new(Frame { img: shot.img, t_capture_ns: shot.t_ns }));
                    }
                }
            }
            o.perceptor = perceptor;
            o
        }))
    }

    /// Mensajes que emite el ciclo desde su hilo (`blank`, `unblank`).
    fn on_cycle_msg(&mut self, out: Out) {
        let next = match &out {
            Out::Blank { .. } => Some(State::Blank),
            Out::Unblank { .. } => {
                if let Some(s) = self.camera.synthetic().filter(|s| s.auto()) {
                    s.set_projector(true);
                }
                Some(State::Perceiving)
            }
            _ => None,
        };
        self.broadcast(&out);
        if let Some(s) = next {
            self.set_state(s);
        }
    }

    async fn on_cycle_done(&mut self, o: CycleOut) {
        self.perceptor = Some(o.perceptor);
        self.last_end = Some(Instant::now());
        let mut changed = false;
        if self.reset_pending {
            self.reset_pending = false;
            self.do_reset();
        } else if let Some(p) = o.perception {
            changed = self.apply(p);
            if let Some(run) = o.scene {
                self.on_scene(run);
            }
            self.sync_world();
        }
        // `look{clean:true}` pendientes: la foto de este ciclo o su error.
        for (conn, id) in std::mem::take(&mut self.clean_looks) {
            let Some(tx) = self.clients.get(&conn).map(|c| c.tx.clone()) else { continue };
            match (&o.clean, &o.error) {
                (Some(f), _) => {
                    let f = f.clone();
                    tokio::task::spawn_blocking(move || tx.blocking_send(look_frame(id, &f).json()));
                }
                (None, e) => {
                    let (code, message) = e.clone().unwrap_or(("clean_failed".into(), "no hay foto limpia".into()));
                    self.send_to(conn, &Out::Error { code, message, id: Some(id) });
                }
            }
        }
        match &o.error {
            Some((code, message)) => {
                eprintln!("{LOG} ciclo ({:?}) sin resultado: {code}: {message}", o.reason);
                self.broadcast(&Out::error(code, message.clone()));
                if o.retry && self.retries < 2 {
                    self.retries += 1;
                    self.want = self.want.max(Some(Reason::Retry));
                    self.retry_at = Some(Instant::now() + Duration::from_millis(400));
                }
            }
            None => self.retries = 0,
        }
        if o.reason != Reason::Settle {
            self.settles = 0;
        }
        if changed && !self.cfg.settle_after.is_zero() && self.settles < MAX_SETTLES && self.want.is_none() {
            self.settles += 1;
            self.want = Some(Reason::Settle);
            self.retry_at = Some(Instant::now() + self.cfg.settle_after);
        }
        let total = o.started.elapsed().as_secs_f64() * 1e3;
        self.last_cycle_ms = Some(total);
        self.stages = o.stages.into_iter().collect();
        self.stages.insert("total".into(), total);
        self.state = State::Idle;
        let st = self.status();
        self.broadcast(&st);
        if let Some(size) = self.resize_pending.take() {
            self.camera.ensure_size(size).await;
        }
    }

    /// Fusiona lo que entregó el `Perceptor` en la tabla y publica el `objects` incremental.
    /// `true` si cambió algo.
    fn apply(&mut self, p: PerceptionOut) -> bool {
        let mut upsert = Vec::new();
        for o in p.upsert {
            let Some(msg) = object_msg(o) else { continue };
            if self.objects.get(&msg.id) != Some(&msg) {
                self.objects.insert(msg.id.clone(), msg.clone());
                upsert.push(msg);
            }
        }
        let mut remove = Vec::new();
        for id in p.remove {
            if !upsert.iter().any(|u| u.id == id) && self.objects.remove(&id).is_some() {
                remove.push(id);
            }
        }
        let changed = !upsert.is_empty() || !remove.is_empty();
        if changed {
            self.rev += 1;
            let rev = self.rev;
            self.broadcast(&Out::Objects { rev, upsert, remove });
        }
        changed
    }
}

impl Engine {
    fn cameras(&self) -> Vec<CameraRole> {
        let role = |hub: &Arc<FrameHub>, role: &str| {
            hub.camera().map(|c| CameraRole { id: c.id, role: role.into(), name: c.name })
        };
        role(&self.hub, CENITAL).into_iter().chain(self.lateral.as_ref().and_then(|h| role(h, LATERAL))).collect()
    }

    fn is_scene_id(&self, id: &str) -> bool {
        id.split_once(':').is_some_and(|(p, _)| self.scene_names.iter().any(|n| n == p))
    }

    /// Objetos del `Perceptor` base (sin los de las escenas), como los recibe un módulo en `ctx.objects`.
    fn base_objects(&self) -> Vec<ObjectOut> {
        let out = |m: &ObjectMsg| ObjectOut {
            id: m.id.clone(),
            label: m.label.clone(),
            polygon_mm: m.polygon_mm.clone(),
            center_mm: m.center_mm,
            confidence: m.confidence,
        };
        self.objects.values().filter(|m| !self.is_scene_id(&m.id)).map(out).collect()
    }

    fn sync_world(&mut self) {
        self.scenes.set_world(self.base_objects(), self.calib.clone());
    }

    fn remove_scene_objects(&mut self, scene: &str) {
        let prefix = format!("{scene}:");
        let remove: Vec<String> = self.objects.keys().filter(|id| id.starts_with(&prefix)).cloned().collect();
        if !remove.is_empty() {
            self.apply(PerceptionOut { remove, ..Default::default() });
        }
    }

    /// Resultado de un módulo de escena (del ciclo o de la cámara en vivo): objetos a la tabla, eventos a la web app.
    /// Lo de una escena que ya no está activa se descarta.
    fn on_scene(&mut self, run: SceneRun) {
        if self.scenes.active().as_deref() != Some(run.scene.as_str()) {
            return;
        }
        match run.out {
            Err(e) => {
                let call = if run.live { "on_live" } else { "on_clean" };
                eprintln!("{LOG} escena «{}»: {call}: {e}", run.scene);
                self.broadcast(&Out::error("scene_failed", format!("escena «{}» ({call}): {e}", run.scene)));
            }
            Ok(out) => {
                self.apply(PerceptionOut { upsert: out.upsert, remove: out.remove, stages_ms: vec![] });
                for ev in out.events {
                    self.broadcast(&Out::Event { scene: run.scene.clone(), event: ev.event, data: ev.data });
                }
            }
        }
    }
}

/// `base` con lo que cambió en este ciclo.
fn merged(mut base: Vec<ObjectOut>, p: &PerceptionOut) -> Vec<ObjectOut> {
    base.retain(|o| !p.remove.contains(&o.id) && !p.upsert.iter().any(|u| u.id == o.id));
    base.extend(p.upsert.iter().cloned());
    base
}

/// Marcador mientras el `Perceptor` real viaja dentro del ciclo.
struct Idle;
impl Perceptor for Idle {
    fn on_clean(&mut self, _: &image::RgbImage, _: &CalibIn, _: u64) -> Result<PerceptionOut, String> {
        Err("sin perceptor".into())
    }
    fn reset(&mut self) {}
}

fn look_frame(id: Value, f: &Frame) -> Out {
    match jpeg_b64(&f.img, JPEG_QUALITY) {
        Ok(jpeg_b64) => Out::LookFrame { id, jpeg_b64, t_ns: f.t_capture_ns, w: f.img.width(), h: f.img.height() },
        Err(e) => Out::Error { code: "look_failed".into(), message: e, id: Some(id) },
    }
}

/// Objeto del `Perceptor` → mensaje: números finitos y polígono de ≤ 64 puntos, o no se publica.
fn object_msg(o: ObjectOut) -> Option<ObjectMsg> {
    let finite = |p: &[f64; 2]| p[0].is_finite() && p[1].is_finite();
    if o.id.is_empty() || !finite(&o.center_mm) || !o.polygon_mm.iter().all(finite) {
        return None;
    }
    Some(ObjectMsg {
        id: o.id,
        label: o.label,
        polygon_mm: geom::decimate(o.polygon_mm, 64),
        center_mm: o.center_mm,
        confidence: if o.confidence.is_finite() { o.confidence.clamp(0.0, 1.0) } else { 0.0 },
    })
}

fn check_calib(px: [f64; 2], m: [f64; 9], lens: Option<Lens>, surface: [f64; 2]) -> Result<CalibIn, String> {
    let side = |v: f64| v.is_finite() && v >= 16.0 && v <= MAX_SIDE as f64 && v.fract() == 0.0;
    if !side(px[0]) || !side(px[1]) {
        return Err(format!("camera_px inválido: {px:?}"));
    }
    if !(surface.iter().all(|v| v.is_finite() && *v > 0.0)) {
        return Err(format!("surface_mm inválido: {surface:?}"));
    }
    if !m.iter().all(|v| v.is_finite()) || geom::invert(&m).is_none() {
        return Err("matrix_px_to_mm no es invertible".into());
    }
    if let Some(l) = &lens {
        if ![l.f, l.cx, l.cy, l.k1, l.k2].iter().all(|v| v.is_finite()) || l.f <= 0.0 {
            return Err("lens inválido".into());
        }
    }
    Ok(CalibIn {
        camera_px: (px[0] as u32, px[1] as u32),
        matrix_px_to_mm: m,
        lens,
        surface_mm: (surface[0], surface[1]),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn calibracion_validada() {
        let id = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0];
        assert!(check_calib([1280.0, 720.0], id, None, [800.0, 500.0]).is_ok());
        assert!(check_calib([0.0, 720.0], id, None, [800.0, 500.0]).is_err());
        assert!(check_calib([1e9, 720.0], id, None, [800.0, 500.0]).is_err());
        assert!(check_calib([1280.0, 720.0], [0.0; 9], None, [800.0, 500.0]).is_err());
        assert!(check_calib([1280.0, 720.0], id, None, [f64::NAN, 500.0]).is_err());
        let bad = Lens { f: 0.0, cx: 0.0, cy: 0.0, k1: 0.0, k2: 0.0 };
        assert!(check_calib([1280.0, 720.0], id, Some(bad), [800.0, 500.0]).is_err());
    }

    #[test]
    fn objetos_saneados() {
        let o = |poly: Vec<[f64; 2]>, c: [f64; 2]| ObjectOut {
            id: "a".into(),
            label: "a".into(),
            polygon_mm: poly,
            center_mm: c,
            confidence: 7.0,
        };
        let big: Vec<[f64; 2]> = (0..200).map(|i| [i as f64, 0.0]).collect();
        let m = object_msg(o(big, [1.0, 2.0])).unwrap();
        assert_eq!((m.polygon_mm.len(), m.confidence), (64, 1.0));
        assert!(object_msg(o(vec![[f64::NAN, 0.0]], [1.0, 2.0])).is_none());
        assert!(object_msg(o(vec![], [f64::INFINITY, 2.0])).is_none());
    }
}
