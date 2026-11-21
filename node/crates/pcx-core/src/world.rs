//! Actor del mundo: una cola de trabajos y un solo escritor (sin locks sobre el estado).
//!
//! Todo cambio pasa por `WorldHandle::call()` → la tarea del actor lo aplica, despacha los eventos derivados
//! (ask, confirmaciones, flujos), publica el diff de escena y el snapshot de lectura. El tick (dwell, plazos, TTL,
//! temporizadores de flujos) corre dentro del mismo actor. El render nunca espera: `broadcast` de diffs y, si un
//! suscriptor se atrasa (`Lagged`), pide un `scene` completo.

use std::collections::{HashMap, VecDeque};
use std::sync::Arc;
use std::time::Duration;

use indexmap::IndexMap;
use pcx_contracts::{
    Anchor, Blank, Button, OnPress, Panel, PointMM, RectMM, SceneDiff, SceneSnapshot, Surface, ToRenderer, Transform, Trigger,
    Widget, WidgetCommon,
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
}

impl Default for WorldConfig {
    fn default() -> Self {
        WorldConfig { node_id: "mesa-1".into(), surface_mm: (800.0, 500.0), tick_hz: 20.0, home_policy: HomePolicy::default() }
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
}

#[derive(Clone)]
pub struct WorldHandle {
    tx: mpsc::UnboundedSender<Job>,
    pub node_id: String,
    pub store: Arc<Store>,
    pub seq: watch::Receiver<i64>,
    pub state: watch::Receiver<Arc<WorldState>>,
    pub events: broadcast::Sender<Arc<Envelope>>,
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
        };
        w.restore();
        w.flush_scene();
        w.publish_state();
        let (tx, mut rx) = mpsc::unbounded_channel::<Job>();
        let period = Duration::from_secs_f64(1.0 / cfg.tick_hz.max(1.0));
        tokio::spawn(async move {
            let mut iv = tokio::time::interval(period);
            iv.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
            loop {
                tokio::select! {
                    job = rx.recv() => match job {
                        Some(job) => job(&mut w),
                        None => break,
                    },
                    _ = iv.tick() => w.tick(),
                }
                w.after();
            }
            w.persist(true);
        });
        WorldHandle { tx, node_id: cfg.node_id, store, seq: seq_rx, state: state_rx, events: events_tx }
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
                if let Some(cid) = widget_id.strip_prefix("pcx:confirm:") {
                    let cid = cid.to_string();
                    self.resolve_confirm(&cid, true, ResolvedVia::Touch);
                }
            }
            self.flows_on_event(&ev);
        }
        self.flush_scene();
        self.publish_state();
        self.persist(false);
    }

    fn publish_state(&mut self) {
        if self.dirty {
            self.dirty = false;
            self.persist_dirty = true;
            let _ = self.state_tx.send(Arc::new(self.snapshot()));
        }
    }

    // ------------------------------------------------------------ comandos

    /// Aplica un comando. `owner` = (flow_id) si viene de un flujo.
    pub fn apply(&mut self, cmd: Command, owner: Option<&str>) -> Result<CmdOut, String> {
        cmd.validate()?;
        self.dirty = true;
        let done = |v: Value| Ok(CmdOut::Done(v));
        match cmd {
            Command::Project { widgets, .. } => done(json!({"ids": self.project(widgets, owner.is_none(), owner)?, "rev": 0})),
            Command::Update { id, set, .. } => done(json!({"updated": self.update(&id, &set)?, "rev": 0})),
            Command::Remove { ids, .. } => done(json!({"removed": self.remove(&ids), "rev": 0})),
            Command::Clear { .. } => {
                let ids: Vec<String> = match owner {
                    Some(f) => self.runs.get(f).map(|r| r.widgets.iter().cloned().collect()).unwrap_or_default(),
                    None => self.widgets.keys().cloned().collect(),
                };
                done(json!({"removed": self.remove(&ids), "rev": 0}))
            }
            Command::Ask { request_id, question, options, timeout_s, anchor } => {
                Ok(CmdOut::Wait(self.ask(request_id, question, options, timeout_s, anchor)?))
            }
            Command::Emit { request_id, event, data } => {
                self.emit(Event::FlowEmitted { flow_id: owner.map(Into::into), request_id: Some(request_id), event: event.clone(), data }, "core", None);
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
                self.expires_ns.insert(id.clone(), t + (ttl * 1e9) as i64);
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

    fn update(&mut self, id: &str, set: &Json) -> Result<String, String> {
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
        self.widgets.insert(id.into(), nw);
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

    fn ask(&mut self, request_id: String, question: String, options: Vec<String>, timeout_s: f64, anchor: Option<Anchor>) -> Result<oneshot::Receiver<Value>, String> {
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
                x0 = if bb.0 + bb.2 + 20.0 + total_w <= sw { bb.0 + bb.2 + 20.0 } else { (bb.0 - 20.0 - total_w).max(5.0) };
                y0 = bb.1.max(5.0).min(sh - 130.0);
            } else if let Some(p) = a.point_mm {
                (x0, y0) = (p.0, p.1);
            }
        }
        let common = |id: String, z: i64, on_press: Option<OnPress>| WidgetCommon { id, anchor: None, z, ttl_s: None, on_press };
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
            let op = OnPress { value: Some(opt.clone()), triggers: vec![Trigger::Dwell, Trigger::Pinch, Trigger::Api], dwell_ms: 800 };
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
            AskState { request_id, options, button_ids: bids, widget_ids: wids, deadline_ns: now_ns() + (timeout_s * 1e9) as i64, reply: Some(tx) },
        );
        Ok(rx)
    }

    fn finish_ask(&mut self, aid: &str, idx: Option<usize>) {
        let Some(mut a) = self.asks.shift_remove(aid) else { return };
        self.remove(&a.widget_ids);
        let answer = idx.map(|i| a.options[i].clone());
        self.emit(
            Event::AskAnswered { request_id: a.request_id.clone(), answer: answer.clone(), index: idx.map(|i| i as i64), timed_out: idx.is_none() },
            "core",
            None,
        );
        if let Some(tx) = a.reply.take() {
            let _ = tx.send(json!({"status": if idx.is_none() {"timeout"} else {"answered"}, "answer": answer,
                                   "index": idx, "ask_id": aid, "request_id": a.request_id}));
        }
    }

    // ------------------------------------------------------------ domótica (política del nodo)

    pub fn home_call(&mut self, call: HomeCallBody, request_id: &str, flow_id: Option<&str>) -> HomeCallResult {
        let decision = decide(&self.home_policy, &call);
        self.home_calls.push(json!({"domain": call.domain, "service": call.service, "data": call.data, "flow_id": flow_id,
                                    "request_id": request_id, "decision": format!("{decision:?}").to_lowercase()}));
        let mut res = HomeCallResult { request_id: request_id.into(), status: HomeCallStatus::Denied, confirmation_id: None, action_digest: None, expires_ns: None, error: None };
        match decision {
            Decision::Deny => {
                res.error = Some(format!("{}.{} no está permitido por la política del nodo", call.domain, call.service));
                self.emit(Event::HomeCallDone { request_id: request_id.into(), confirmation_id: None, status: HomeDoneStatus::Denied, error: res.error.clone() }, "core", None);
            }
            Decision::Allow => {
                let r = self.home_exec(&call);
                res.status = if r.is_ok() { HomeCallStatus::Done } else { HomeCallStatus::Failed };
                res.error = r.clone().err();
                let st = if r.is_ok() { HomeDoneStatus::Done } else { HomeDoneStatus::Failed };
                self.emit(Event::HomeCallDone { request_id: request_id.into(), confirmation_id: None, status: st, error: r.err() }, "core", None);
            }
            Decision::Confirm => {
                self.confirm_n += 1;
                let cid = format!("cf-{}-{}", now_ns() % 1_000_000, self.confirm_n);
                let digest = action_digest(&call);
                let expires = now_ns() + (self.home_policy.confirm_timeout_s * 1e9) as i64;
                let summary = format!("{}.{} {}", call.domain, call.service, call.data.get("entity_id").and_then(Value::as_str).unwrap_or(""));
                self.store.confirmation_put(&cid, request_id, &digest, &serde_json::to_value(&call).unwrap_or_default(), expires, "pending");
                let mut wids = vec![];
                if self.home_policy.confirm_via.contains(&ConfirmVia::Touch) {
                    let (x, y) = (self.surface.width_mm - 250.0, 20.0);
                    let panel = Widget::Panel(Panel {
                        common: WidgetCommon { id: format!("pcx:confirm-q:{cid}"), anchor: None, z: 50, ttl_s: None, on_press: None },
                        rect_mm: Some(RectMM(x, y, 230.0, 60.0)),
                        title: "¿Confirmas?".into(),
                        body: summary.trim().to_string(),
                        color: "#FFD400".into(),
                    });
                    let btn = Widget::Button(Button {
                        common: WidgetCommon {
                            id: format!("pcx:confirm:{cid}"),
                            anchor: None,
                            z: 51,
                            ttl_s: None,
                            on_press: Some(OnPress { value: Some("confirm".into()), triggers: vec![Trigger::Dwell, Trigger::Pinch], dwell_ms: 1200 }),
                        },
                        rect_mm: Some(RectMM(x, y + 70.0, 230.0, 50.0)),
                        label: "Confirmar".into(),
                        progress: 0.0,
                        color: "#FFD400".into(),
                    });
                    wids = vec![panel.id().to_string(), btn.id().to_string()];
                    for w in [panel, btn] {
                        self.widgets.insert(w.id().to_string(), w);
                    }
                }
                self.confirms.insert(cid.clone(), PendingConfirm { request_id: request_id.into(), digest: digest.clone(), call, expires_ns: expires, widget_ids: wids });
                self.emit(
                    Event::ConfirmationRequested {
                        confirmation_id: cid.clone(),
                        request_id: request_id.into(),
                        action_digest: digest.clone(),
                        summary: summary.trim().into(),
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

    /// Ejecutor de Home Assistant. TODO: adaptador `ha-ws`; hoy no hay HA conectado.
    fn home_exec(&mut self, call: &HomeCallBody) -> Result<(), String> {
        Err(format!("Home Assistant no conectado (stub): {}.{}", call.domain, call.service))
    }

    fn resolve_confirm(&mut self, cid: &str, approved: bool, via: ResolvedVia) {
        let Some(p) = self.confirms.shift_remove(cid) else { return };
        self.remove(&p.widget_ids);
        self.store.confirmation_put(cid, &p.request_id, &p.digest, &Value::Null, p.expires_ns, if approved { "approved" } else { "rejected" });
        self.emit(
            Event::ConfirmationResolved { confirmation_id: cid.into(), request_id: p.request_id.clone(), action_digest: p.digest.clone(), approved, via },
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
        self.emit(Event::HomeCallDone { request_id: p.request_id, confirmation_id: Some(cid.into()), status, error }, "core", None);
    }

    /// Consola local → nodo. Debe repetir el `action_digest`.
    pub fn confirm_action(&mut self, c: &ConfirmAction) -> Result<(), String> {
        let p = self.confirms.get(&c.confirmation_id).ok_or_else(|| format!("confirmación desconocida: {}", c.confirmation_id))?;
        if p.digest != c.action_digest {
            return Err("action_digest no coincide".into());
        }
        if !self.home_policy.confirm_via.contains(&ConfirmVia::Console) {
            return Err("la política no admite confirmación por consola".into());
        }
        let cid = c.confirmation_id.clone();
        self.resolve_confirm(&cid, c.approve, ResolvedVia::Console);
        Ok(())
    }

    // ------------------------------------------------------------ ingesta

    pub fn ingest(&mut self, env: Envelope) -> Result<Value, String> {
        let (src, tc) = (env.source.clone(), env.t_capture_ns);
        self.dirty = true;
        match env.payload {
            Payload::Command(c) => {
                return match self.apply(c, None)? {
                    CmdOut::Done(v) => Ok(v),
                    CmdOut::Wait(_) => Ok(json!({"status": "asked"})),
                };
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
                    let poly = polygon_mm.clone().unwrap_or_else(|| o.polygon_mm.iter().map(|q| PointMM(q.0 + dx, q.1 + dy)).collect());
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
                    let t = self.touch(touch, &src, tc);
                    return Ok(serde_json::to_value(t).unwrap_or_default());
                }
                Event::FrameMeta { ref camera_id, .. } => {
                    self.frames.insert(camera_id.clone(), serde_json::to_value(&ev).unwrap_or_default());
                }
                Event::DeviceChanged { device } => {
                    self.store.device_save(&device.id, &serde_json::to_value(&device).unwrap_or_default());
                    self.devices.insert(device.id.clone(), device.clone());
                    self.emit(Event::DeviceChanged { device }, &src, tc);
                }
                // externos que pasan tal cual: botón físico/api, deriva de calibración, permanencia, blanked
                ev @ (Event::WidgetPressed { .. } | Event::CalibrationDrift { .. } | Event::Dwell { .. } | Event::Blanked { .. }) => {
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

    pub fn touch(&mut self, t: Touch, source: &str, tc: Option<i64>) -> Touch {
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
            self.touches.shift_remove(&t.id);
        } else {
            self.touches.insert(t.id.clone(), t.clone());
        }
        self.emit(ev, source, tc);
        let target = hit.filter(geom::pressable);
        let triggers: Vec<Trigger> = match &target {
            Some(w) => w.common().on_press.as_ref().map(|o| o.triggers.clone()).unwrap_or(vec![Trigger::Dwell, Trigger::Pinch, Trigger::Api]),
            None => vec![Trigger::Dwell, Trigger::Pinch, Trigger::Api],
        };
        if let Some(w) = &target {
            if t.trigger != Trigger::Dwell && (triggers.contains(&t.trigger) || t.trigger == Trigger::Shadow) {
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
                self.dwell.insert(t.id.clone(), DwellState { widget_id: target.id().into(), start_ns: now_ns(), point: t.point_mm, fired: false });
            }
        }
        t
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
        let late: Vec<String> = self.confirms.iter().filter(|(_, c)| t >= c.expires_ns).map(|(k, _)| k.clone()).collect();
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
            .filter_map(|(k, w)| geom::resolve(w, &self.objects, size).ok().flatten().map(|r| (k.clone(), r)))
            .collect()
    }

    fn flush_scene(&mut self) {
        let new = self.resolved();
        let new_json: HashMap<String, String> = new.iter().map(|(k, v)| (k.clone(), serde_json::to_string(v).unwrap_or_default())).collect();
        let upsert: Vec<Widget> = new.iter().filter(|(k, _)| self.scene_json.get(*k) != new_json.get(*k)).map(|(_, v)| v.clone()).collect();
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
        self.render_stats.insert("presented_rev".into(), rev.into());
        if let Some((_, sent)) = self.sent_ms.iter().find(|(r, _)| *r == rev) {
            self.render_stats.insert("latency_ms".into(), json!(((t_present_ms - sent) * 100.0).round() / 100.0));
        }
    }

    pub fn on_blanked(&mut self, b: pcx_contracts::Blanked) {
        let proj = self.blanks.remove(&b.request_id).unwrap_or_else(|| "main".into());
        self.emit(Event::Blanked { request_id: b.request_id, projector_id: proj, t_start_ms: b.t_start_ms, t_end_ms: b.t_end_ms }, "renderer", None);
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
            let _ = tx.send(f(w));
        });
        if self.tx.send(job).is_err() {
            panic!("el actor del mundo terminó");
        }
        rx.await.expect("el actor del mundo terminó")
    }

    /// Encola sin esperar (ingesta de alta frecuencia).
    pub fn post(&self, f: impl FnOnce(&mut World) + Send + 'static) {
        let _ = self.tx.send(Box::new(f));
    }

    /// Comando del agente con idempotencia por `request_id` (ventana de 10 min, persistente).
    pub async fn command(&self, cmd: Command) -> Result<Value, String> {
        let rid = cmd.request_id().to_string();
        let tool = tag_of(&cmd);
        if let Some(prev) = self.store.request_get(&rid, IDEMPOTENCY_WINDOW_S) {
            return Ok(prev);
        }
        let out = self.call(move |w| w.apply(cmd, None)).await?;
        let mut v = match out {
            CmdOut::Done(v) => v,
            CmdOut::Wait(rx) => rx.await.map_err(|_| "ask cancelado".to_string())?,
        };
        if let Some(m) = v.as_object_mut() {
            if m.contains_key("rev") {
                let rev = self.state.borrow().rev;
                m.insert("rev".into(), rev.into());
            }
        }
        self.store.request_put(&rid, &tool, &v);
        Ok(v)
    }

    pub async fn ingest(&self, env: Envelope) -> Result<Value, String> {
        self.call(move |w| w.ingest(env)).await
    }

    pub fn ingest_nowait(&self, env: Envelope) {
        self.post(move |w| {
            if let Err(e) = w.ingest(env) {
                tracing::warn!("ingesta: {e}");
            }
        });
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
    pub async fn events_wait(&self, cursor: i64, types: Vec<String>, timeout_s: f64, max_events: i64) -> EventsWaitResult {
        let mut seq_rx = self.seq.clone();
        let deadline = tokio::time::Instant::now() + Duration::from_secs_f64(timeout_s.clamp(0.0, 50.0));
        let mut cursor = if cursor > self.last_seq() { 0 } else { cursor.max(0) };
        let first = self.store.first_seq();
        let gap = (first > 0 && cursor + 1 < first).then(|| HistoryGap {
            stream_id: self.store.stream_id.clone(),
            from_: cursor + 1,
            to: Some(first - 1),
            reason: GapReason::Retention,
        });
        loop {
            seq_rx.borrow_and_update();
            let (evs, cur) = self.store.after(cursor, &types, max_events.clamp(1, 500));
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
