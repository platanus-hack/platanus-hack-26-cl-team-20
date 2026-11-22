//! Actor del mundo: una cola de trabajos y un solo escritor (sin locks sobre el estado).
//!
//! Todo cambio pasa por `WorldHandle::call()` → la tarea del actor lo aplica, despacha los eventos derivados
//! (ask, confirmaciones, flujos), publica el diff de escena y el snapshot de lectura. El tick (dwell, plazos, TTL,
//! temporizadores de flujos) corre dentro del mismo actor. El render nunca espera: `broadcast` de diffs y, si un
//! suscriptor se atrasa (`Lagged`), pide un `scene` completo.

use std::collections::{HashMap, VecDeque};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc,
};
use std::time::Duration;

use indexmap::IndexMap;
use pcx_contracts::{
    Anchor, Blank, Button, OnPress, Panel, PointMM, RectMM, SceneDiff, SceneSnapshot, Surface, ToRenderer, Transform,
    Trigger, Widget, WidgetCommon,
};
use serde_json::{json, Value};
use tokio::sync::{broadcast, mpsc, oneshot, watch};

use crate::flows::FlowRun;
use crate::geom::{self, Objects};
use crate::proto::*;
use crate::store::Store;

type Job = Box<dyn FnOnce(&mut World) + Send>;

#[derive(Debug, Clone)]
struct DwellState {
    widget_id: String,
    start_ns: i64,
    point: PointMM,
    fired: bool,
}

struct AskState {
    request_id: String,
    options: Vec<String>,
    button_ids: Vec<String>,
    widget_ids: Vec<String>,
    deadline_ns: i64,
    reply: Option<oneshot::Sender<Value>>,
}

struct PendingConfirm {
    request_id: String,
    digest: String,
    call: HomeCallBody,
    expires_ns: i64,
    widget_ids: Vec<String>,
    /// Texto generado por el core a partir de la acción real; es lo único que se proyecta.
    summary: String,
    /// `rev` de la primera escena que contiene el botón de esta confirmación.
    shown_rev: Option<i64>,
    /// Instante en que el renderer avisó (`presented`) que esa escena ya está en la mesa.
    presented_ns: Option<i64>,
}

/// Máximo de confirmaciones pendientes (una activa y el resto en cola).
const MAX_CONFIRMS: usize = 8;
/// Tras `presented`, margen antes de aceptar un `touch_down`: evita aprobar con un gesto ya en curso.
const CONFIRM_GRACE_NS: i64 = 500_000_000;

fn ha_id(s: &str) -> bool {
    let ok = |p: &str| !p.is_empty() && p.bytes().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'_');
    s.len() <= 96 && s.split('.').count() <= 2 && s.split('.').all(ok)
}

/// Resumen que ve la persona: lo arma el core con dominio, servicio, todos los destinos y los demás datos.
/// El agente no puede sustituirlo: los destinos se validan como identificadores y el resto va citado tras `datos:`.
pub fn action_summary(call: &HomeCallBody) -> Result<String, String> {
    let mut targets = vec![];
    let mut extras = vec![];
    for (k, v) in call.data.iter() {
        if matches!(k.as_str(), "entity_id" | "area_id" | "device_id") {
            let items: Vec<&Value> = match v {
                Value::Array(a) => a.iter().collect(),
                other => vec![other],
            };
            for it in items {
                let id = it
                    .as_str()
                    .filter(|s| ha_id(s))
                    .ok_or_else(|| format!("{k} inválido: debe ser un identificador"))?;
                targets.push(if k == "entity_id" { id.to_string() } else { format!("{k}:{id}") });
            }
        } else {
            if !ha_id(k) || k.contains('.') {
                return Err(format!("clave de datos inválida: {k:?}"));
            }
            let val: String = v.to_string().chars().filter(|c| !c.is_control()).collect();
            let val = if val.chars().count() > 40 { val.chars().take(40).collect::<String>() + "…" } else { val };
            extras.push(format!("{k}={val}"));
        }
    }
    if targets.len() > 8 || extras.len() > 6 {
        return Err("demasiados destinos o datos para mostrarlos en la confirmación".into());
    }
    let mut s = format!("{}.{}", call.domain, call.service);
    s += &if targets.is_empty() { " (sin destino)".to_string() } else { format!(" → {}", targets.join(", ")) };
    if !extras.is_empty() {
        s += &format!("\ndatos: {}", extras.join(", "));
    }
    Ok(s)
}

/// Resultado de aplicar un comando: inmediato o a esperar fuera del actor (`ask`).
pub enum CmdOut {
    Done(Value),
    Wait(oneshot::Receiver<Value>),
}

pub struct WorldConfig {
    pub node_id: String,
    pub surface_mm: (f64, f64),
    pub tick_hz: f64,
    pub home_policy: HomePolicy,
    /// Carpeta de assets del renderer; las rutas de imagen deben quedar dentro (canonicalizadas).
    pub assets_dir: Option<std::path::PathBuf>,
}

impl Default for WorldConfig {
    fn default() -> Self {
        WorldConfig {
            node_id: "mesa-1".into(),
            surface_mm: (800.0, 500.0),
            tick_hz: 20.0,
            home_policy: HomePolicy::default(),
            assets_dir: None,
        }
    }
}

pub struct World {
    pub node_id: String,
    pub surface: Surface,
    pub store: Arc<Store>,
    pub objects: Objects,
    pub hands: IndexMap<String, HandState>,
    pub touches: IndexMap<String, Touch>,
    /// Tal como los pidió el agente (anclas sin resolver).
    pub widgets: IndexMap<String, Widget>,
    pub expires_ns: HashMap<String, i64>,
    pub devices: IndexMap<String, Device>,
    pub transforms: Vec<Transform>,
    pub frames: HashMap<String, Value>,
    pub rev: i64,
    /// Escena resuelta (mm absolutos) que vio el renderer en `rev`.
    pub scene: IndexMap<String, Widget>,
    scene_json: HashMap<String, String>,
    transforms_json: String,
    pub render_tx: broadcast::Sender<Arc<ToRenderer>>,
    sent_ms: VecDeque<(i64, f64)>,
    pub render_stats: serde_json::Map<String, Value>,
    blanks: HashMap<String, String>,
    dwell: HashMap<String, DwellState>,
    asks: IndexMap<String, AskState>,
    ask_n: u64,
    pub(crate) runs: IndexMap<String, FlowRun>,
    pub calibrations: Vec<Value>,
    pub home_calls: Vec<Value>,
    pub home_policy: HomePolicy,
    confirms: IndexMap<String, PendingConfirm>,
    confirm_n: u64,
    pending: VecDeque<Event>,
    pub seq: i64,
    seq_tx: watch::Sender<i64>,
    state_tx: watch::Sender<Arc<WorldState>>,
    pub events_tx: broadcast::Sender<Arc<Envelope>>,
    dirty: bool,
    persist_dirty: bool,
    last_persist_ns: i64,
    requests: HashMap<String, Vec<oneshot::Sender<Result<Value, String>>>>,
    assets_dir: Option<std::path::PathBuf>,
}

#[derive(Clone)]
pub struct WorldHandle {
    /// Carril crítico: comandos, down/up, objetos, confirmaciones. Nunca descarta (contrapresión).
    tx: mpsc::Sender<Job>,
    /// Carril descartable: `move`, manos y estadísticas de alta frecuencia.
    lossy: mpsc::Sender<Job>,
    pub node_id: String,
    pub store: Arc<Store>,
    pub seq: watch::Receiver<i64>,
    pub state: watch::Receiver<Arc<WorldState>>,
    pub events: broadcast::Sender<Arc<Envelope>>,
    overloaded: Arc<AtomicBool>,
}

impl World {
    pub fn spawn(cfg: WorldConfig, store: Arc<Store>) -> WorldHandle {
        let seq = store.last_seq();
        let surface = Surface { width_mm: cfg.surface_mm.0, height_mm: cfg.surface_mm.1 };
        let (seq_tx, seq_rx) = watch::channel(seq);
        let empty = WorldState {
            rev: 0,
            surface: surface.clone(),
            objects: vec![],
            hands: vec![],
            touches: vec![],
            widgets: vec![],
            devices: vec![],
            transforms: vec![],
            active_flows: vec![],
        };
        let (state_tx, state_rx) = watch::channel(Arc::new(empty));
        let (events_tx, _) = broadcast::channel(4096);
        let (render_tx, _) = broadcast::channel(256);
        let policy = store.home_policy().unwrap_or(cfg.home_policy);
        let mut w = World {
            node_id: cfg.node_id.clone(),
            surface,
            store: store.clone(),
            objects: Objects::new(),
            hands: IndexMap::new(),
            touches: IndexMap::new(),
            widgets: IndexMap::new(),
            expires_ns: HashMap::new(),
            devices: IndexMap::new(),
            transforms: vec![],
            frames: HashMap::new(),
            rev: 0,
            scene: IndexMap::new(),
            scene_json: HashMap::new(),
            transforms_json: "[]".into(),
            render_tx,
            sent_ms: VecDeque::new(),
            render_stats: Default::default(),
            blanks: HashMap::new(),
            dwell: HashMap::new(),
            asks: IndexMap::new(),
            ask_n: 0,
            runs: IndexMap::new(),
            calibrations: vec![],
            home_calls: vec![],
            home_policy: policy,
            confirms: IndexMap::new(),
            confirm_n: 0,
            pending: VecDeque::new(),
            seq,
            seq_tx,
            state_tx,
            events_tx: events_tx.clone(),
            dirty: true,
            persist_dirty: false,
            last_persist_ns: 0,
            requests: HashMap::new(),
            assets_dir: cfg.assets_dir.as_ref().and_then(|d| d.canonicalize().ok()),
        };
        w.restore();
        w.flush_scene();
        w.publish_state();
        let (tx, mut rx) = mpsc::channel::<Job>(256);
        let (lossy, mut lossy_rx) = mpsc::channel::<Job>(256);
        let overloaded = Arc::new(AtomicBool::new(false));
        let overload_rx = overloaded.clone();
        let period = Duration::from_secs_f64(1.0 / cfg.tick_hz.max(1.0));
        std::thread::Builder::new()
            .name("pcx-world-sqlite".into())
            .spawn(move || {
                let rt = tokio::runtime::Builder::new_current_thread().enable_all().build().expect("actor runtime");
                let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                    rt.block_on(async move {
                        let mut iv = tokio::time::interval(period);
                        iv.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
                        loop {
                            tokio::select! {
                                job = rx.recv() => match job {
                                    Some(job) => job(&mut w),
                                    None => break,
                                },
                                Some(job) = lossy_rx.recv() => job(&mut w),
                                _ = iv.tick() => if !overload_rx.load(Ordering::Relaxed) { w.tick(); },
                            }
                            if overload_rx.load(Ordering::Relaxed) {
                                for d in std::mem::take(&mut w.dwell).into_values() {
                                    w.set_progress(&d.widget_id, 0.0);
                                }
                                if lossy_rx.is_empty() {
                                    overload_rx.store(false, Ordering::Relaxed);
                                }
                            }
                            w.after();
                        }
                        w.persist(true);
                    })
                }));
                if result.is_err() {
                    tracing::error!("panic del actor: se termina el proceso para recuperar estado durable");
                    std::process::exit(70);
                }
            })
            .expect("actor thread");
        WorldHandle {
            tx,
            lossy,
            node_id: cfg.node_id,
            store,
            seq: seq_rx,
            state: state_rx,
            events: events_tx,
            overloaded,
        }
    }

    // ------------------------------------------------------------ persistencia del snapshot

    fn restore(&mut self) {
        if let Some(v) = self.store.load_world() {
            let get = |k: &str| v.get(k).cloned().unwrap_or(Value::Null);
            self.objects = serde_json::from_value(get("objects")).unwrap_or_default();
            self.widgets = serde_json::from_value(get("widgets")).unwrap_or_default();
            self.expires_ns = serde_json::from_value(get("expires_ns")).unwrap_or_default();
            self.devices = serde_json::from_value(get("devices")).unwrap_or_default();
            self.transforms = serde_json::from_value(get("transforms")).unwrap_or_default();
            self.rev = get("rev").as_i64().unwrap_or(0);
        }
        self.widgets.retain(|id, _| !id.starts_with("pcx:")); // las confirmaciones nunca se reanudan tras un crash
        if self.transforms.is_empty() {
            self.transforms = self.store.calibrations_latest();
        }
        for d in self.store.devices() {
            if let Ok(d) = serde_json::from_value::<Device>(d) {
                self.devices.entry(d.id.clone()).or_insert(d);
            }
        }
    }

    /// Guarda el snapshot ya (al apagar).
    pub fn flush(&mut self) {
        self.persist_dirty = true;
        self.persist(true);
    }

    fn persist(&mut self, force: bool) {
        let t = now_ns();
        if !self.persist_dirty || (!force && t - self.last_persist_ns < 250_000_000) {
            return;
        }
        let v = json!({"rev": self.rev, "objects": self.objects, "widgets": self.widgets, "expires_ns": self.expires_ns,
                       "devices": self.devices, "transforms": self.transforms});
        self.store.save_world(self.seq, &v);
        self.persist_dirty = false;
        self.last_persist_ns = t;
    }

    // ------------------------------------------------------------ eventos

    pub fn emit(&mut self, ev: Event, source: &str, t_capture_ns: Option<i64>) -> i64 {
        let seq = self.seq + 1;
        let env = wrap(Payload::Event(ev.clone()), seq, source, &self.node_id, t_capture_ns);
        if let Err(e) = self.store.append(&env) {
            tracing::error!("log de eventos: {e}");
        }
        self.seq = seq;
        let _ = self.seq_tx.send(seq);
        let _ = self.events_tx.send(Arc::new(env));
        self.pending.push_back(ev);
        self.dirty = true;
        seq
    }

    fn after(&mut self) {
        let mut n = 0;
        while let Some(ev) = self.pending.pop_front() {
            n += 1;
            if n > 1000 {
                tracing::warn!("demasiados eventos encadenados; se cortan");
                self.pending.clear();
                break;
            }
            if let Event::WidgetPressed { widget_id, .. } = &ev {
                let hit: Vec<(String, usize)> = self
                    .asks
                    .iter()
                    .filter_map(|(k, a)| a.button_ids.iter().position(|b| b == widget_id).map(|i| (k.clone(), i)))
                    .collect();
                for (k, i) in hit {
                    self.finish_ask(&k, Some(i));
                }
            }
            self.flows_on_event(&ev);
        }
        self.flush_scene();
        self.publish_state();
        self.persist(true);
    }

    fn publish_state(&mut self) {
        if self.dirty {
            self.dirty = false;
            self.persist_dirty = true;
            let _ = self.state_tx.send(Arc::new(self.snapshot()));
        }
    }

    fn durable_world(&self) -> Value {
        json!({"rev": self.rev, "objects": self.objects, "widgets": self.widgets, "expires_ns": self.expires_ns,
               "devices": self.devices, "transforms": self.transforms})
    }

    fn complete_request(&mut self, rid: &str, result: Result<Value, String>) {
        self.after();
        let result = result.and_then(|mut v| {
            if let Some(m) = v.as_object_mut() {
                if m.contains_key("rev") {
                    m.insert("rev".into(), self.rev.into());
                }
            }
            self.store.commit_request(rid, &v, self.seq, &self.durable_world()).map_err(|e| e.to_string())?;
            Ok(v)
        });
        for reply in self.requests.remove(rid).unwrap_or_default() {
            let _ = reply.send(result.clone());
        }
    }

    fn submit(&mut self, cmd: Command) -> oneshot::Receiver<Result<Value, String>> {
        let (tx, rx) = oneshot::channel();
        let rid = cmd.request_id().to_string();
        if let Some(v) = self.store.request_get(&rid, IDEMPOTENCY_WINDOW_S) {
            let _ = tx.send(Ok(v));
        } else if let Some(waiters) = self.requests.get_mut(&rid) {
            if waiters.len() >= 32 {
                let _ = tx.send(Err("demasiados reintentos pendientes".into()));
            } else {
                waiters.push(tx);
            }
        } else {
            if self.requests.len() >= 128 {
                let _ = tx.send(Err("demasiadas peticiones pendientes".into()));
                return rx;
            }
            self.requests.insert(rid.clone(), vec![tx]);
            match self.apply(cmd, None) {
                Ok(CmdOut::Done(v)) => self.complete_request(&rid, Ok(v)),
                Ok(CmdOut::Wait(_)) => {}
                Err(e) => self.complete_request(&rid, Err(e)),
            }
        }
        rx
    }

    // ------------------------------------------------------------ comandos

    /// Aplica un comando. `owner` = (flow_id) si viene de un flujo.
    pub fn apply(&mut self, cmd: Command, owner: Option<&str>) -> Result<CmdOut, String> {
        cmd.validate()?;
        self.dirty = true;
        let done = |v: Value| Ok(CmdOut::Done(v));
        match cmd {
            Command::Project { widgets, .. } => {
                done(json!({"ids": self.project(widgets, owner.is_none(), owner)?, "rev": 0}))
            }
            Command::Update { id, set, .. } => done(json!({"updated": self.update(&id, &set)?, "rev": 0})),
            Command::Remove { ids, .. } => done(json!({"removed": self.remove(&ids), "rev": 0})),
            Command::Clear { .. } => {
                let ids: Vec<String> = match owner {
                    Some(f) => self.runs.get(f).map(|r| r.widgets.iter().cloned().collect()).unwrap_or_default(),
                    None => self.widgets.keys().filter(|id| !id.starts_with("pcx:")).cloned().collect(),
                };
                done(json!({"removed": self.remove(&ids), "rev": 0}))
            }
            Command::Ask { request_id, question, options, timeout_s, anchor } => {
                Ok(CmdOut::Wait(self.ask(request_id, question, options, timeout_s, anchor)?))
            }
            Command::Emit { request_id, event, data } => {
                self.emit(
                    Event::FlowEmitted {
                        flow_id: owner.map(Into::into),
                        request_id: Some(request_id),
                        event: event.clone(),
                        data,
                    },
                    "core",
                    None,
                );
                done(json!({"emitted": event}))
            }
            Command::RunFlow { request_id, flow } => done(self.flow_start(flow, request_id)?),
            Command::StopFlow { flow_id, .. } => done(self.flow_stop(&flow_id, "stop")),
            Command::Calibrate { request_id, mode, camera_id, projector_id } => {
                let c = json!({"request_id": request_id, "mode": mode, "camera_id": camera_id, "projector_id": projector_id});
                self.calibrations.push(c);
                done(json!({"status": "queued", "mode": mode,
                            "message": "calibración encolada; la ejecuta el proceso de calibración (set_transform al terminar)"}))
            }
            Command::SetTransform { transform, .. } => {
                let _ = self.store.calibration_add(&transform);
                let key = (transform.from_frame().to_string(), transform.to_frame().to_string());
                self.transforms.retain(|t| (t.from_frame(), t.to_frame()) != (key.0.as_str(), key.1.as_str()));
                self.transforms.push(transform);
                done(json!({"transforms": self.transforms.len(), "rev": 0}))
            }
            Command::HomeCall(c) => {
                let r = self.home_call(c.body(), &c.request_id, owner);
                done(serde_json::to_value(r).unwrap_or(Value::Null))
            }
            Command::Blank { request_id, ms, reason, projector_id } => {
                self.blanks.insert(request_id.clone(), projector_id.clone().unwrap_or_else(|| "main".into()));
                let n = self.render_tx.send(Arc::new(ToRenderer::Blank(Blank { request_id, ms, reason }))).unwrap_or(0);
                done(json!({"status": "sent", "renderers": n}))
            }
        }
    }

    pub fn project(&mut self, widgets: Vec<Widget>, strict: bool, owner: Option<&str>) -> Result<Vec<String>, String> {
        let size = (self.surface.width_mm, self.surface.height_mm);
        for w in &widgets {
            self.validate_agent_widget(w)?;
            if strict {
                if let Some(oid) = w.common().anchor.as_ref().and_then(|a| a.object_id.as_ref()) {
                    if !self.objects.contains_key(oid) {
                        return Err(format!("{}: objeto desconocido {oid:?} (usa `scene`)", w.id()));
                    }
                }
            }
            geom::resolve(w, &self.objects, size).map_err(|e| e.0)?;
        }
        let t = now_ns();
        let mut ids = vec![];
        for w in widgets {
            let id = w.id().to_string();
            if let Some(ttl) = w.common().ttl_s {
                let deadline = t.checked_add((ttl * 1e9) as i64).ok_or("TTL desborda reloj")?;
                self.expires_ns.insert(id.clone(), deadline);
            }
            if let Some(f) = owner {
                if let Some(r) = self.runs.get_mut(f) {
                    r.widgets.insert(id.clone());
                }
            }
            self.widgets.insert(id.clone(), w);
            ids.push(id);
        }
        Ok(ids)
    }

    /// Misma política que el renderer: `asset:<nombre>`, `data:image/(png|jpeg|webp);base64,` o una ruta que,
    /// canonicalizada, queda dentro de la carpeta de assets. Sin URLs, sin `..`, sin rutas absolutas ajenas.
    pub(crate) fn check_image_src(&self, src: &str) -> Result<(), String> {
        use std::path::{Component, Path};
        if let Some(rest) = src.strip_prefix("data:") {
            let head = rest.split_once(',').map(|x| x.0).unwrap_or_default();
            return if matches!(head, "image/png;base64" | "image/jpeg;base64" | "image/webp;base64") {
                Ok(())
            } else {
                Err("image.src data: solo admite image/png, image/jpeg o image/webp en base64".into())
            };
        }
        let (name, asset) = match src.strip_prefix("asset:") {
            Some(n) => (n, true),
            None => (src.strip_prefix("file://").unwrap_or(src), false),
        };
        let path = Path::new(name);
        let absolute = path.is_absolute() || name.starts_with(['/', '\\']);
        if name.is_empty()
            || name.starts_with('~')
            || name.contains("://")
            || path.components().any(|c| c == Component::ParentDir)
            || (asset && absolute)
            || (!absolute && name.contains(':'))
        {
            return Err(
                "image.src solo admite asset:<nombre>, data:image/ o rutas dentro de la carpeta de assets".into()
            );
        }
        let Some(root) = &self.assets_dir else {
            // Sin carpeta configurada solo pasan nombres relativos; el renderer los resuelve dentro de la suya.
            return if absolute { Err("image.src absoluto requiere PCX_ASSETS_DIR".into()) } else { Ok(()) };
        };
        let mut full = if absolute { path.to_path_buf() } else { root.join(path) };
        if asset && full.extension().is_none() {
            full.set_extension("png");
        }
        match full.canonicalize() {
            Ok(real) if real.starts_with(root) => Ok(()),
            Ok(_) => Err("image.src apunta fuera de la carpeta de assets".into()),
            Err(_) if !absolute => Ok(()), // aún no existe: el renderer fallará sin leer nada ajeno
            Err(e) => Err(format!("image.src ilegible: {e}")),
        }
    }

    pub(crate) fn validate_agent_widget(&self, w: &Widget) -> Result<(), String> {
        if w.id().starts_with("pcx:") {
            return Err("id reservado pcx:".into());
        }
        if w.common().ttl_s.is_some_and(|t| !t.is_finite() || !(0.0..=86400.0).contains(&t)) {
            return Err("ttl_s debe estar en [0, 86400]".into());
        }
        if let Widget::Image(i) = w {
            self.check_image_src(&i.src)?;
        }
        Ok(())
    }

    fn update(&mut self, id: &str, set: &Json) -> Result<String, String> {
        if id.starts_with("pcx:") {
            return Err("id reservado".into());
        }
        let w = self.widgets.get(id).ok_or_else(|| format!("widget desconocido: {id}"))?;
        let mut v = serde_json::to_value(w).map_err(|e| e.to_string())?;
        let ty = v.get("type").cloned();
        if let Value::Object(m) = &mut v {
            for (k, x) in set {
                m.insert(k.clone(), x.clone());
            }
            m.insert("id".into(), id.into());
            if let Some(t) = ty {
                m.insert("type".into(), t);
            }
        }
        let nw: Widget = serde_json::from_value(v).map_err(|e| format!("update {id}: {e}"))?;
        self.project(vec![nw], true, None)?;
        Ok(id.into())
    }

    pub fn remove(&mut self, ids: &[String]) -> Vec<String> {
        let mut out = vec![];
        for i in ids {
            if self.widgets.shift_remove(i).is_some() {
                out.push(i.clone());
            }
            self.expires_ns.remove(i);
        }
        self.dirty = true;
        out
    }

    // ------------------------------------------------------------ ask

    fn ask(
        &mut self,
        request_id: String,
        question: String,
        options: Vec<String>,
        timeout_s: f64,
        anchor: Option<Anchor>,
    ) -> Result<oneshot::Receiver<Value>, String> {
        self.ask_n += 1;
        let aid = format!("ask-{}", self.ask_n);
        let n = options.len() as f64;
        let (bw, bh, gap) = (110.0, 45.0, 12.0);
        let total_w = (n * bw + (n - 1.0) * gap).max(260.0);
        let (sw, sh) = (self.surface.width_mm, self.surface.height_mm);
        let (mut x0, mut y0) = ((sw - total_w) / 2.0, sh - 130.0);
        if let Some(a) = &anchor {
            if let Some(o) = a.object_id.as_ref().and_then(|i| self.objects.get(i)) {
                let bb = geom::bbox_of(o);
                x0 = if bb.0 + bb.2 + 20.0 + total_w <= sw {
                    bb.0 + bb.2 + 20.0
                } else {
                    (bb.0 - 20.0 - total_w).max(5.0)
                };
                y0 = bb.1.max(5.0).min(sh - 130.0);
            } else if let Some(p) = a.point_mm {
                (x0, y0) = (p.0, p.1);
            }
        }
        let common =
            |id: String, z: i64, on_press: Option<OnPress>| WidgetCommon { id, anchor: None, z, ttl_s: None, on_press };
        let mut ws = vec![Widget::Panel(Panel {
            common: common(format!("{aid}-q"), 10, None),
            rect_mm: Some(RectMM(x0, y0, total_w, 60.0)),
            title: question,
            body: "Toca una opción".into(),
            color: "#FFFFFF".into(),
        })];
        let mut bids = vec![];
        for (i, opt) in options.iter().enumerate() {
            let bid = format!("{aid}-b{i}");
            bids.push(bid.clone());
            let op = OnPress {
                value: Some(opt.clone()),
                triggers: vec![Trigger::Dwell, Trigger::Pinch, Trigger::Api],
                dwell_ms: 800,
            };
            ws.push(Widget::Button(Button {
                common: common(bid, 11, Some(op)),
                rect_mm: Some(RectMM(x0 + i as f64 * (bw + gap), y0 + 70.0, bw, bh)),
                label: opt.clone(),
                progress: 0.0,
                color: "#00E676".into(),
            }));
        }
        let wids = ws.iter().map(|w| w.id().to_string()).collect();
        self.project(ws, false, None)?;
        let (tx, rx) = oneshot::channel();
        self.asks.insert(
            aid,
            AskState {
                request_id,
                options,
                button_ids: bids,
                widget_ids: wids,
                deadline_ns: now_ns() + (timeout_s * 1e9) as i64,
                reply: Some(tx),
            },
        );
        Ok(rx)
    }

    fn finish_ask(&mut self, aid: &str, idx: Option<usize>) {
        let Some(mut a) = self.asks.shift_remove(aid) else { return };
        self.remove(&a.widget_ids);
        let answer = idx.map(|i| a.options[i].clone());
        self.emit(
            Event::AskAnswered {
                request_id: a.request_id.clone(),
                answer: answer.clone(),
                index: idx.map(|i| i as i64),
                timed_out: idx.is_none(),
            },
            "core",
            None,
        );
        let result = json!({"status": if idx.is_none() {"timeout"} else {"answered"}, "answer": answer,
                                   "index": idx, "ask_id": aid, "request_id": a.request_id});
        if let Some(tx) = a.reply.take() {
            let _ = tx.send(result.clone());
        }
        self.complete_request(&a.request_id, Ok(result));
    }

    // ------------------------------------------------------------ domótica (política del nodo)

    pub fn home_call(&mut self, call: HomeCallBody, request_id: &str, flow_id: Option<&str>) -> HomeCallResult {
        let summary = action_summary(&call);
        let decision = match decide(&self.home_policy, &call) {
            Decision::Confirm if summary.is_err() || self.confirms.len() >= MAX_CONFIRMS => Decision::Deny,
            d => d,
        };
        self.home_calls
            .push(json!({"domain": call.domain, "service": call.service, "data": call.data, "flow_id": flow_id,
                                    "request_id": request_id, "decision": format!("{decision:?}").to_lowercase()}));
        let mut res = HomeCallResult {
            request_id: request_id.into(),
            status: HomeCallStatus::Denied,
            confirmation_id: None,
            action_digest: None,
            expires_ns: None,
            error: None,
        };
        match decision {
            Decision::Deny => {
                res.error = Some(match &summary {
                    Err(e) => e.clone(),
                    Ok(_) if self.confirms.len() >= MAX_CONFIRMS => "demasiadas confirmaciones pendientes".into(),
                    Ok(_) => format!("{}.{} no está permitido por la política del nodo", call.domain, call.service),
                });
                self.emit(
                    Event::HomeCallDone {
                        request_id: request_id.into(),
                        confirmation_id: None,
                        status: HomeDoneStatus::Denied,
                        error: res.error.clone(),
                    },
                    "core",
                    None,
                );
            }
            Decision::Allow => {
                let r = self.home_exec(&call);
                res.status = if r.is_ok() { HomeCallStatus::Done } else { HomeCallStatus::Failed };
                res.error = r.clone().err();
                let st = if r.is_ok() { HomeDoneStatus::Done } else { HomeDoneStatus::Failed };
                self.emit(
                    Event::HomeCallDone {
                        request_id: request_id.into(),
                        confirmation_id: None,
                        status: st,
                        error: r.err(),
                    },
                    "core",
                    None,
                );
            }
            Decision::Confirm => {
                self.confirm_n += 1;
                let cid = format!("cf-{}-{}", now_ns() % 1_000_000, self.confirm_n);
                let digest = action_digest(&call);
                let expires = now_ns() + (self.home_policy.confirm_timeout_s * 1e9) as i64;
                let summary = summary.unwrap_or_default();
                self.store.confirmation_put(
                    &cid,
                    request_id,
                    &digest,
                    &serde_json::to_value(&call).unwrap_or_default(),
                    expires,
                    "pending",
                );
                self.confirms.insert(
                    cid.clone(),
                    PendingConfirm {
                        request_id: request_id.into(),
                        digest: digest.clone(),
                        call,
                        expires_ns: expires,
                        widget_ids: vec![],
                        summary: summary.clone(),
                        shown_rev: None,
                        presented_ns: None,
                    },
                );
                self.present_confirm();
                self.emit(
                    Event::ConfirmationRequested {
                        confirmation_id: cid.clone(),
                        request_id: request_id.into(),
                        action_digest: digest.clone(),
                        summary,
                        via: self.home_policy.confirm_via.clone(),
                        expires_ns: expires,
                    },
                    "core",
                    None,
                );
                res.status = HomeCallStatus::Pending;
                res.confirmation_id = Some(cid);
                res.action_digest = Some(digest);
                res.expires_ns = Some(expires);
            }
        }
        res
    }

    /// Proyecta SOLO la confirmación en cabeza de la cola: panel con el resumen del core y botón cuyo id lleva
    /// su `confirmation_id`. Las demás esperan (se anuncian como «+N en cola») y se presentan al resolverse esta.
    fn present_confirm(&mut self) {
        if !self.home_policy.confirm_via.contains(&ConfirmVia::Touch) {
            return;
        }
        let queued = self.confirms.len().saturating_sub(1);
        let Some((cid, p)) = self.confirms.first() else { return };
        let (cid, first) = (cid.clone(), p.widget_ids.is_empty());
        let body = if queued > 0 { format!("{}\n(+{queued} en cola)", p.summary) } else { p.summary.clone() };
        let (x, y) = (self.surface.width_mm - 250.0, 20.0);
        let common =
            |id: String, z: i64, on_press: Option<OnPress>| WidgetCommon { id, anchor: None, z, ttl_s: None, on_press };
        let panel = Widget::Panel(Panel {
            common: common(format!("pcx:confirm-q:{cid}"), i64::MAX - 1, None),
            rect_mm: Some(RectMM(x, y, 230.0, 80.0)),
            title: "¿Confirmas?".into(),
            body,
            color: "#FFD400".into(),
        });
        let mut wids = vec![panel.id().to_string()];
        self.widgets.insert(panel.id().to_string(), panel);
        let bid = format!("pcx:confirm:{cid}");
        if first {
            let on_press = OnPress {
                value: Some("confirm".into()),
                triggers: vec![Trigger::Dwell, Trigger::Pinch],
                dwell_ms: 1500,
            };
            let btn = Widget::Button(Button {
                common: common(bid.clone(), i64::MAX, Some(on_press)),
                rect_mm: Some(RectMM(x, y + 90.0, 230.0, 50.0)),
                label: "Confirmar".into(),
                progress: 0.0,
                color: "#FFD400".into(),
            });
            self.widgets.insert(bid.clone(), btn);
        }
        wids.push(bid);
        if let Some(p) = self.confirms.get_mut(&cid) {
            p.widget_ids = wids;
        }
        self.dirty = true;
    }

    /// Ejecutor de Home Assistant. TODO: adaptador `ha-ws`; hoy no hay HA conectado.
    fn home_exec(&mut self, call: &HomeCallBody) -> Result<(), String> {
        Err(format!("Home Assistant no conectado (stub): {}.{}", call.domain, call.service))
    }

    fn resolve_confirm(&mut self, cid: &str, approved: bool, via: ResolvedVia) {
        let Some(p) = self.confirms.shift_remove(cid) else { return };
        self.remove(&p.widget_ids);
        // Ningún gesto en curso sirve para la siguiente: exige un `touch_down` nuevo tras presentarla.
        let stale: Vec<String> = self
            .dwell
            .iter()
            .filter(|(_, d)| d.widget_id.starts_with("pcx:confirm:"))
            .map(|(k, _)| k.clone())
            .collect();
        for k in stale {
            self.dwell.remove(&k);
        }
        self.present_confirm();
        self.store.confirmation_put(
            cid,
            &p.request_id,
            &p.digest,
            &Value::Null,
            p.expires_ns,
            if approved { "approved" } else { "rejected" },
        );
        self.emit(
            Event::ConfirmationResolved {
                confirmation_id: cid.into(),
                request_id: p.request_id.clone(),
                action_digest: p.digest.clone(),
                approved,
                via,
            },
            "core",
            None,
        );
        let (status, error) = if !approved {
            (if via == ResolvedVia::Timeout { HomeDoneStatus::Expired } else { HomeDoneStatus::Rejected }, None)
        } else {
            match self.home_exec(&p.call) {
                Ok(()) => (HomeDoneStatus::Done, None),
                Err(e) => (HomeDoneStatus::Failed, Some(e)),
            }
        };
        self.emit(
            Event::HomeCallDone { request_id: p.request_id, confirmation_id: Some(cid.into()), status, error },
            "core",
            None,
        );
    }

    /// Consola (local por HTTP o de la nube como `CloudMessage`) → nodo. Debe repetir el `action_digest`.
    pub fn confirm_action(&mut self, c: &ConfirmAction) -> Result<(), String> {
        if c.by != ConfirmVia::Console {
            return Err("ConfirmAction solo admite consola".into());
        }
        let p = self
            .confirms
            .get(&c.confirmation_id)
            .ok_or_else(|| format!("confirmación desconocida: {}", c.confirmation_id))?;
        if now_ns() >= p.expires_ns {
            return Err("confirmación expirada".into());
        }
        if p.digest != c.action_digest {
            return Err("action_digest no coincide".into());
        }
        if !self.home_policy.confirm_via.contains(&c.by) {
            return Err(format!("la política no admite confirmación por {:?}", c.by));
        }
        let via = match c.by {
            ConfirmVia::Console => ResolvedVia::Console,
            ConfirmVia::Touch => ResolvedVia::Touch,
        };
        let cid = c.confirmation_id.clone();
        self.resolve_confirm(&cid, c.approved, via);
        Ok(())
    }

    // ------------------------------------------------------------ ingesta

    pub fn ingest(&mut self, env: Envelope) -> Result<Value, String> {
        self.ingest_from(env, false)
    }

    /// `physical` = el sobre llegó por el canal autenticado del worker de manos (token propio en la ingesta
    /// ZeroMQ o llamada en proceso). El campo `source` lo declara el emisor y no da ningún privilegio.
    pub fn ingest_from(&mut self, env: Envelope, physical: bool) -> Result<Value, String> {
        let (src, tc) = (env.source.clone(), env.t_capture_ns);
        self.dirty = true;
        match env.payload {
            Payload::Command(c) => {
                drop(self.submit(c));
                return Ok(json!({"status": "queued"}));
            }
            Payload::Event(ev) => match ev {
                Event::ObjectAppeared { mut object } => {
                    if let Some(prev) = self.objects.get(&object.id) {
                        object.since_ns = prev.since_ns.min(object.since_ns);
                    }
                    self.objects.insert(object.id.clone(), object.clone());
                    self.emit(Event::ObjectAppeared { object }, &src, tc);
                }
                Event::ObjectMoved { object_id, center_mm, polygon_mm } => {
                    let Some(o) = self.objects.get_mut(&object_id) else { return Ok(Value::Null) };
                    let (dx, dy) = (center_mm.0 - o.center_mm.0, center_mm.1 - o.center_mm.1);
                    let poly = polygon_mm
                        .clone()
                        .unwrap_or_else(|| o.polygon_mm.iter().map(|q| PointMM(q.0 + dx, q.1 + dy)).collect());
                    o.bbox_mm = match (o.bbox_mm, &polygon_mm) {
                        (Some(b), None) => Some(RectMM(b.0 + dx, b.1 + dy, b.2, b.3)),
                        _ => None,
                    };
                    o.center_mm = center_mm;
                    o.polygon_mm = poly;
                    o.last_seen_ns = tc.unwrap_or_else(now_ns);
                    self.emit(Event::ObjectMoved { object_id, center_mm, polygon_mm }, &src, tc);
                }
                Event::ObjectRemoved { object_id, label } => {
                    if let Some(o) = self.objects.remove(&object_id) {
                        self.emit(Event::ObjectRemoved { object_id, label: label.or(Some(o.label)) }, &src, tc);
                    }
                }
                Event::TouchDown { touch } | Event::TouchMove { touch } | Event::TouchUp { touch } => {
                    let t = self.touch(touch, &src, tc, physical);
                    return Ok(serde_json::to_value(t).unwrap_or_default());
                }
                Event::FrameMeta(ref fm) => {
                    self.frames.insert(fm.camera_id.clone(), serde_json::to_value(&ev).unwrap_or_default());
                }
                Event::DeviceChanged { device } => {
                    self.store.device_save(&device.id, &serde_json::to_value(&device).unwrap_or_default());
                    self.devices.insert(device.id.clone(), device.clone());
                    self.emit(Event::DeviceChanged { device }, &src, tc);
                }
                // externos que pasan tal cual: botón físico/api, deriva de calibración, permanencia, blanked,
                // fin de episodio de manos (v2.1)
                // `widget_pressed` solo lo genera el core por hit-test; desde fuera siempre es una falsificación.
                Event::WidgetPressed { .. } => {
                    return Err("widget_pressed no se acepta por ingesta: lo genera el core".into());
                }
                ev @ (Event::CalibrationDrift { .. }
                | Event::Dwell { .. }
                | Event::Blanked { .. }
                | Event::HandsGone(_)) => {
                    self.emit(ev, &src, tc);
                }
                other => tracing::debug!("ingesta ignora {}", other.tag()),
            },
        }
        Ok(Value::Null)
    }

    /// Manos (extensión local del core; aún no hay evento de contrato para manos).
    pub fn set_hands(&mut self, hands: Vec<HandState>) {
        self.hands = hands.into_iter().map(|h| (h.id.clone(), h)).collect();
        self.dirty = true;
    }

    // ------------------------------------------------------------ interacción

    pub fn touch(&mut self, t: Touch, source: &str, tc: Option<i64>, physical: bool) -> Touch {
        let fresh_down = t.phase == TouchPhase::Down && !self.touches.contains_key(&t.id);
        // Los `move` viajan por el carril descartable y pueden llegar después del `up`: sin contacto vivo se ignoran
        // (si no, resucitarían un toque ya terminado).
        if t.phase == TouchPhase::Move && !self.touches.contains_key(&t.id) {
            return t;
        }
        let (x, y) = (t.point_mm.0, t.point_mm.1);
        let hit = geom::hit_test(self.scene.values(), x, y).cloned();
        let mut t = t;
        t.widget_id = hit.as_ref().map(|w| w.id().to_string());
        t.object_id = geom::object_at(&self.objects, x, y);
        let ev = match t.phase {
            TouchPhase::Down => Event::TouchDown { touch: t.clone() },
            TouchPhase::Move => Event::TouchMove { touch: t.clone() },
            TouchPhase::Up => Event::TouchUp { touch: t.clone() },
        };
        if t.phase == TouchPhase::Up {
            if let Some(d) = self.dwell.remove(&t.id) {
                self.set_progress(&d.widget_id, 0.0);
            }
            self.touches.shift_remove(&t.id);
        } else {
            self.touches.insert(t.id.clone(), t.clone());
        }
        self.emit(ev, source, tc);
        let target = hit.filter(geom::pressable).filter(|w| {
            !w.id().starts_with("pcx:confirm:") || (physical && matches!(t.trigger, Trigger::Dwell | Trigger::Pinch))
        });
        let triggers: Vec<Trigger> = match &target {
            Some(w) => w.common().on_press.as_ref().map(|o| o.triggers.clone()).unwrap_or(vec![
                Trigger::Dwell,
                Trigger::Pinch,
                Trigger::Api,
            ]),
            None => vec![Trigger::Dwell, Trigger::Pinch, Trigger::Api],
        };
        let target = target.filter(|_| triggers.contains(&t.trigger));
        if let Some(w) = target.as_ref().filter(|w| w.id().starts_with("pcx:confirm:")) {
            self.confirm_touch(&t, w.id(), fresh_down);
            return t;
        }
        if let Some(w) = &target {
            if t.trigger != Trigger::Dwell {
                if t.phase == TouchPhase::Down {
                    self.press(w.id(), t.trigger, Some(t.point_mm));
                }
                return t;
            }
        }
        let d = self.dwell.get(&t.id).cloned();
        let target = match target {
            Some(w) if t.phase != TouchPhase::Up && triggers.contains(&Trigger::Dwell) => w,
            _ => {
                if let Some(d) = d {
                    self.set_progress(&d.widget_id, 0.0);
                    self.dwell.remove(&t.id);
                }
                return t;
            }
        };
        match d {
            Some(d) if d.widget_id == target.id() => {
                if let Some(x) = self.dwell.get_mut(&t.id) {
                    x.point = t.point_mm;
                }
            }
            other => {
                if let Some(d) = other {
                    self.set_progress(&d.widget_id, 0.0);
                }
                self.dwell.insert(
                    t.id.clone(),
                    DwellState { widget_id: target.id().into(), start_ns: now_ns(), point: t.point_mm, fired: false },
                );
            }
        }
        t
    }

    /// Toque físico sobre el botón de confirmación. Solo cuenta un `touch_down` NUEVO, dentro del botón, posterior
    /// a que el renderer presentó la confirmación activa; luego debe sostenerse el dwell completo (también el
    /// pinch). Un dedo que ya estaba apoyado, o que entra deslizándose, no inicia nada.
    fn confirm_touch(&mut self, t: &Touch, wid: &str, fresh_down: bool) {
        let now = now_ns();
        let prev = self.dwell.get(&t.id).cloned();
        if t.phase == TouchPhase::Move {
            match prev {
                Some(d) if d.widget_id == wid => {
                    if let Some(x) = self.dwell.get_mut(&t.id) {
                        x.point = t.point_mm;
                    }
                }
                Some(d) => {
                    self.set_progress(&d.widget_id, 0.0);
                    self.dwell.remove(&t.id);
                }
                None => {}
            }
            return;
        }
        if let Some(d) = prev {
            self.set_progress(&d.widget_id, 0.0);
            self.dwell.remove(&t.id);
        }
        let cid = wid.strip_prefix("pcx:confirm:").unwrap_or_default();
        let active = self.confirms.first().is_some_and(|(k, p)| {
            k == cid && now < p.expires_ns && p.presented_ns.is_some_and(|ns| now >= ns + CONFIRM_GRACE_NS)
        });
        if t.phase == TouchPhase::Down && fresh_down && active {
            self.dwell.insert(
                t.id.clone(),
                DwellState { widget_id: wid.into(), start_ns: now, point: t.point_mm, fired: false },
            );
        }
    }

    fn set_progress(&mut self, wid: &str, progress: f64) {
        if let Some(Widget::Button(b)) = self.widgets.get_mut(wid) {
            if (b.progress - progress).abs() > 1e-6 {
                b.progress = (progress * 1000.0).round() / 1000.0;
                self.dirty = true;
            }
        }
    }

    fn press(&mut self, wid: &str, trigger: Trigger, point: Option<PointMM>) {
        if let Some(cid) = wid.strip_prefix("pcx:confirm:") {
            if !matches!(trigger, Trigger::Dwell | Trigger::Pinch) {
                return;
            }
            let cid = cid.to_string();
            // Solo la confirmación activa (la proyectada) y ya presentada: mismo id y mismo digest que se ve.
            let active = self
                .confirms
                .first()
                .is_some_and(|(k, p)| *k == cid && now_ns() < p.expires_ns && p.presented_ns.is_some());
            if active {
                self.resolve_confirm(&cid, true, ResolvedVia::Touch);
            }
            return;
        }
        let value = self.widgets.get(wid).and_then(|w| w.common().on_press.as_ref()).and_then(|o| o.value.clone());
        self.emit(Event::WidgetPressed { widget_id: wid.into(), value, trigger, point_mm: point }, "core", None);
    }

    fn tick(&mut self) {
        let t = now_ns();
        let ids: Vec<String> = self.dwell.keys().cloned().collect();
        for tid in ids {
            let Some(d) = self.dwell.get(&tid).cloned() else { continue };
            let Some(w) = self.widgets.get(&d.widget_id) else {
                self.dwell.remove(&tid);
                continue;
            };
            if d.fired {
                continue;
            }
            let dwell_ms = w.common().on_press.as_ref().map(|o| o.dwell_ms).unwrap_or(800).max(1) as f64;
            let prog = ((t - d.start_ns) as f64 / 1e6 / dwell_ms).min(1.0);
            let cur = if let Widget::Button(b) = w { Some(b.progress) } else { None };
            if prog >= 1.0 {
                if let Some(x) = self.dwell.get_mut(&tid) {
                    x.fired = true;
                }
                self.set_progress(&d.widget_id, 0.0);
                self.press(&d.widget_id, Trigger::Dwell, Some(d.point));
            } else if cur.is_some_and(|c| prog - c >= 0.05) {
                self.set_progress(&d.widget_id, prog);
            }
        }
        let late: Vec<String> = self.asks.iter().filter(|(_, a)| t >= a.deadline_ns).map(|(k, _)| k.clone()).collect();
        for k in late {
            self.finish_ask(&k, None);
        }
        let late: Vec<String> =
            self.confirms.iter().filter(|(_, c)| t >= c.expires_ns).map(|(k, _)| k.clone()).collect();
        for k in late {
            self.resolve_confirm(&k, false, ResolvedVia::Timeout);
        }
        let expired: Vec<String> = self.expires_ns.iter().filter(|(_, e)| t >= **e).map(|(k, _)| k.clone()).collect();
        if !expired.is_empty() {
            self.remove(&expired);
        }
        self.flows_tick(t);
    }

    // ------------------------------------------------------------ escena

    pub fn resolved(&self) -> IndexMap<String, Widget> {
        let size = (self.surface.width_mm, self.surface.height_mm);
        self.widgets
            .iter()
            .filter(|(_, w)| self.confirms.is_empty() || w.id().starts_with("pcx:confirm"))
            .filter_map(|(k, w)| geom::resolve(w, &self.objects, size).ok().flatten().map(|r| (k.clone(), r)))
            .collect()
    }

    fn flush_scene(&mut self) {
        let new = self.resolved();
        let new_json: HashMap<String, String> =
            new.iter().map(|(k, v)| (k.clone(), serde_json::to_string(v).unwrap_or_default())).collect();
        let upsert: Vec<Widget> =
            new.iter().filter(|(k, _)| self.scene_json.get(*k) != new_json.get(*k)).map(|(_, v)| v.clone()).collect();
        let remove: Vec<String> = self.scene.keys().filter(|k| !new_json.contains_key(*k)).cloned().collect();
        let tj = serde_json::to_string(&self.transforms).unwrap_or_default();
        let tchanged = tj != self.transforms_json;
        if upsert.is_empty() && remove.is_empty() && !tchanged {
            return;
        }
        self.rev += 1;
        self.dirty = true;
        self.scene = new;
        self.scene_json = new_json;
        let rev = self.rev;
        if let Some((cid, p)) = self.confirms.first_mut() {
            if p.shown_rev.is_none() && self.scene.contains_key(&format!("pcx:confirm:{cid}")) {
                p.shown_rev = Some(rev);
            }
        }
        self.transforms_json = tj;
        let t_ms = now_ms();
        self.sent_ms.push_back((self.rev, t_ms));
        while self.sent_ms.len() > 512 {
            self.sent_ms.pop_front();
        }
        let diff = SceneDiff {
            rev: self.rev,
            base_rev: self.rev - 1,
            t_send_ms: Some(t_ms),
            upsert,
            remove,
            transforms: if tchanged { Some(self.transforms.clone()) } else { None },
        };
        let _ = self.render_tx.send(Arc::new(ToRenderer::SceneDiff(diff)));
    }

    pub fn scene_snapshot(&self) -> SceneSnapshot {
        SceneSnapshot {
            rev: self.rev,
            t_send_ms: Some(now_ms()),
            surface: self.surface.clone(),
            transforms: self.transforms.clone(),
            widgets: self.scene.values().cloned().collect(),
            homography: Transform::projector_matrix(&self.transforms, "main"),
        }
    }

    pub fn on_presented(&mut self, rev: i64, t_present_ms: f64) {
        if let Some((_, p)) = self.confirms.first_mut() {
            if p.presented_ns.is_none() && p.shown_rev.is_some_and(|shown| rev >= shown) {
                p.presented_ns = Some(now_ns());
            }
        }
        self.render_stats.insert("presented_rev".into(), rev.into());
        if let Some((_, sent)) = self.sent_ms.iter().find(|(r, _)| *r == rev) {
            self.render_stats.insert("latency_ms".into(), json!(((t_present_ms - sent) * 100.0).round() / 100.0));
        }
    }

    pub fn on_blanked(&mut self, b: pcx_contracts::Blanked) {
        let proj = self.blanks.remove(&b.request_id).unwrap_or_else(|| "main".into());
        self.emit(
            Event::Blanked {
                request_id: b.request_id,
                projector_id: proj,
                t_start_ms: b.t_start_ms,
                t_end_ms: b.t_end_ms,
            },
            "renderer",
            None,
        );
    }

    pub fn snapshot(&self) -> WorldState {
        WorldState {
            rev: self.rev,
            surface: self.surface.clone(),
            objects: self.objects.values().cloned().collect(),
            hands: self.hands.values().cloned().collect(),
            touches: self.touches.values().cloned().collect(),
            widgets: self.scene.values().cloned().collect(),
            devices: self.devices.values().cloned().collect(),
            transforms: self.transforms.clone(),
            active_flows: self.runs.keys().cloned().collect(),
        }
    }

    /// Objeto rectangular de depuración (crea o mueve conservando `since_ns`).
    pub fn make_object(&self, oid: &str, label: Option<&str>, center: (f64, f64), size: (f64, f64)) -> ObjectState {
        let (cx, cy) = center;
        let (w, h) = size;
        let poly = vec![
            PointMM(cx - w / 2.0, cy - h / 2.0),
            PointMM(cx + w / 2.0, cy - h / 2.0),
            PointMM(cx + w / 2.0, cy + h / 2.0),
            PointMM(cx - w / 2.0, cy + h / 2.0),
        ];
        let t = now_ns();
        let prev = self.objects.get(oid);
        ObjectState {
            id: oid.into(),
            label: label.map(Into::into).or_else(|| prev.map(|p| p.label.clone())).unwrap_or_else(|| oid.into()),
            confidence: 0.9,
            polygon_mm: poly,
            center_mm: PointMM(cx, cy),
            bbox_mm: Some(RectMM(cx - w / 2.0, cy - h / 2.0, w, h)),
            since_ns: prev.map(|p| p.since_ns).unwrap_or(t),
            last_seen_ns: t,
        }
    }
}

impl WorldHandle {
    /// Ejecuta `f` dentro del actor y devuelve su resultado.
    pub async fn call<R: Send + 'static>(&self, f: impl FnOnce(&mut World) -> R + Send + 'static) -> R {
        let (tx, rx) = oneshot::channel();
        let job: Job = Box::new(move |w| {
            let result = f(w);
            w.after();
            let _ = tx.send(result);
        });
        if self.tx.send(job).await.is_err() {
            panic!("el actor del mundo terminó");
        }
        rx.await.expect("el actor del mundo terminó")
    }

    /// Carril descartable (alta frecuencia: `move`, manos, estadísticas). Si se llena, se descarta y se cancela
    /// todo dwell en curso (fallo seguro). Nada crítico debe pasar por aquí.
    pub fn post(&self, f: impl FnOnce(&mut World) + Send + 'static) {
        if self.lossy.try_send(Box::new(f)).is_err() {
            self.overloaded.store(true, Ordering::Relaxed);
            tracing::warn!("carril descartable lleno: entrada de alta frecuencia descartada, dwell cancelado");
        }
    }

    /// Carril crítico sin esperar el resultado: espera cupo (contrapresión) en vez de descartar.
    pub async fn post_critical(&self, f: impl FnOnce(&mut World) + Send + 'static) {
        if self.tx.send(Box::new(f)).await.is_err() {
            tracing::error!("el actor del mundo terminó");
        }
    }

    /// Comando del agente con idempotencia por `request_id` (ventana de 10 min, persistente).
    pub async fn command(&self, cmd: Command) -> Result<Value, String> {
        self.call(move |w| w.submit(cmd)).await.await.map_err(|_| "actor cerrado".to_string())?
    }

    pub async fn ingest(&self, env: Envelope) -> Result<Value, String> {
        if let Payload::Command(c) = env.payload {
            return self.command(c).await;
        }
        self.call(move |w| w.ingest(env)).await
    }

    /// Ingesta del bus. Solo `touch_move`, `object_moved` y `frame_meta` son descartables; down/up, altas y
    /// bajas de objetos, comandos y el resto esperan cupo en el carril crítico.
    pub async fn ingest_bus(&self, env: Envelope, physical: bool) {
        let droppable = matches!(
            &env.payload,
            Payload::Event(Event::TouchMove { .. } | Event::ObjectMoved { .. } | Event::FrameMeta(_))
        );
        let job = move |w: &mut World| {
            if let Err(e) = w.ingest_from(env, physical) {
                tracing::warn!("ingesta: {e}");
            }
        };
        if droppable {
            self.post(job);
        } else {
            self.post_critical(job).await;
        }
    }

    /// Eventos del worker de manos por canal en proceso (autenticado por construcción).
    pub async fn ingest_hands(&self, env: Envelope) -> Result<Value, String> {
        self.call(move |w| w.ingest_from(env, true)).await
    }

    pub fn set_hands(&self, hands: Vec<HandState>) {
        self.post(move |w| w.set_hands(hands));
    }

    pub async fn confirm(&self, c: ConfirmAction) -> Result<(), String> {
        self.call(move |w| w.confirm_action(&c)).await
    }

    pub fn last_seq(&self) -> i64 {
        *self.seq.borrow()
    }

    pub fn snapshot(&self) -> Arc<WorldState> {
        self.state.borrow().clone()
    }

    /// Suscripción atómica al render: (scene completo, receptor de diffs posteriores).
    pub async fn subscribe_render(&self) -> (SceneSnapshot, broadcast::Receiver<Arc<ToRenderer>>) {
        self.call(|w| (w.scene_snapshot(), w.render_tx.subscribe())).await
    }

    /// Long-poll: eventos con `seq > cursor` (filtrados por tipo) hasta `timeout_s`.
    pub async fn events_wait(
        &self,
        cursor: i64,
        types: Vec<String>,
        timeout_s: f64,
        max_events: i64,
    ) -> EventsWaitResult {
        let mut seq_rx = self.seq.clone();
        let deadline = tokio::time::Instant::now() + Duration::from_secs_f64(timeout_s.clamp(0.0, 50.0));
        let mut cursor = if cursor > self.last_seq() { 0 } else { cursor.max(0) };
        let first = self.call(|w| w.store.first_seq()).await;
        let gap = (first > 0 && cursor + 1 < first).then(|| HistoryGap {
            stream_id: self.store.stream_id.clone(),
            from_: cursor + 1,
            to: Some(first - 1),
            reason: GapReason::Retention,
        });
        loop {
            seq_rx.borrow_and_update();
            let filter = types.clone();
            let (evs, cur) = self.call(move |w| w.store.after(cursor, &filter, max_events.clamp(1, 500))).await;
            cursor = cur;
            if !evs.is_empty() {
                return EventsWaitResult { stream_id: self.store.stream_id.clone(), cursor, events: evs, gap };
            }
            if tokio::time::timeout_at(deadline, seq_rx.changed()).await.is_err() {
                return EventsWaitResult { stream_id: self.store.stream_id.clone(), cursor, events: vec![], gap };
            }
        }
    }
}
