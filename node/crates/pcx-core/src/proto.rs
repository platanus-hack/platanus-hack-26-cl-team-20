//! Tipos de contrato v2 que aún solo existen en Python (`world`, `devices`, `events`, `commands`, `envelope`,
//! `home`, `cloud`, args de `tools`). Espejo serde de `contracts/src/pcx_contracts/*.py` y de `contracts/schema/`.
//!
//! TODO(contratos): mover este módulo a `pcx-contracts` (geometry, widgets y render ya viven ahí).
//! Convenciones: opcionales viajan como `null`; defaults iguales a Python; `*_ns` son i64 de época.

use std::collections::BTreeMap;

use pcx_contracts::{PointMM, PointPX, PolygonMM, RectMM, Surface, Transform, Trigger, Widget, CONTRACT_VERSION};
use serde::de::Error as _;
use serde::{Deserialize, Deserializer, Serialize};
use serde_json::{Map, Value};

pub type Json = Map<String, Value>;
pub const SUPPORTED_VERSIONS: &[u32] = &[2];
pub const IDEMPOTENCY_WINDOW_S: i64 = 600;
pub const MAX_IMMEDIATE_TRANSITIONS: usize = 8;
pub const RESERVED_WIDGET_PREFIX: &str = "pcx:";

fn t_dwell() -> Trigger {
    Trigger::Dwell
}
fn yes() -> bool {
    true
}

// ------------------------------------------------------------------ world / devices

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ObjectState {
    pub id: String,
    pub label: String,
    pub confidence: f64,
    pub polygon_mm: PolygonMM,
    pub center_mm: PointMM,
    #[serde(default)]
    pub bbox_mm: Option<RectMM>,
    pub since_ns: i64,
    pub last_seen_ns: i64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Handedness {
    Left,
    Right,
    #[default]
    Unknown,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct HandState {
    pub id: String,
    pub camera_id: String,
    #[serde(default)]
    pub handedness: Handedness,
    #[serde(default)]
    pub landmarks_px: Vec<PointPX>,
    #[serde(default)]
    pub tip_mm: Option<PointMM>,
    #[serde(default)]
    pub pinch: bool,
    pub t_capture_ns: i64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TouchPhase {
    Down,
    Move,
    Up,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Touch {
    pub id: String,
    pub phase: TouchPhase,
    pub point_mm: PointMM,
    #[serde(default)]
    pub widget_id: Option<String>,
    #[serde(default)]
    pub object_id: Option<String>,
    #[serde(default = "t_dwell")]
    pub trigger: Trigger,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DeviceKind {
    Camera,
    Projector,
    Phone,
    HomeAssistant,
    Midi,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DeviceStatus {
    Online,
    Offline,
    Degraded,
    #[default]
    Unknown,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Device {
    pub id: String,
    pub kind: DeviceKind,
    pub adapter: String,
    #[serde(default)]
    pub caps: Vec<String>,
    #[serde(default)]
    pub status: DeviceStatus,
    #[serde(default)]
    pub meta: Json,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct WorldState {
    pub rev: i64,
    pub surface: Surface,
    #[serde(default)]
    pub objects: Vec<ObjectState>,
    #[serde(default)]
    pub hands: Vec<HandState>,
    #[serde(default)]
    pub touches: Vec<Touch>,
    #[serde(default)]
    pub widgets: Vec<Widget>,
    #[serde(default)]
    pub devices: Vec<Device>,
    #[serde(default)]
    pub transforms: Vec<Transform>,
    #[serde(default)]
    pub active_flows: Vec<String>,
}

// ------------------------------------------------------------------ events

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ConfirmVia {
    Touch,
    Console,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ResolvedVia {
    Touch,
    Console,
    Timeout,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum HomeDoneStatus {
    Done,
    Failed,
    Denied,
    Expired,
    Rejected,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Event {
    ObjectAppeared {
        object: ObjectState,
    },
    ObjectMoved {
        object_id: String,
        center_mm: PointMM,
        #[serde(default)]
        polygon_mm: Option<PolygonMM>,
    },
    ObjectRemoved {
        object_id: String,
        #[serde(default)]
        label: Option<String>,
    },
    TouchDown {
        touch: Touch,
    },
    TouchMove {
        touch: Touch,
    },
    TouchUp {
        touch: Touch,
    },
    WidgetPressed {
        widget_id: String,
        #[serde(default)]
        value: Option<String>,
        #[serde(default = "t_dwell")]
        trigger: Trigger,
        #[serde(default)]
        point_mm: Option<PointMM>,
    },
    Dwell {
        point_mm: PointMM,
        progress: f64,
        #[serde(default)]
        widget_id: Option<String>,
        #[serde(default)]
        object_id: Option<String>,
    },
    AskAnswered {
        request_id: String,
        #[serde(default)]
        answer: Option<String>,
        #[serde(default)]
        index: Option<i64>,
        #[serde(default)]
        timed_out: bool,
    },
    CalibrationDrift {
        from_frame: String,
        to_frame: String,
        residual_mm: f64,
        threshold_mm: f64,
    },
    FlowStateChanged {
        flow_id: String,
        request_id: String,
        #[serde(default)]
        from_state: Option<String>,
        #[serde(default)]
        to_state: Option<String>,
        #[serde(default)]
        cause: Option<String>,
    },
    FlowEmitted {
        #[serde(default)]
        flow_id: Option<String>,
        #[serde(default)]
        request_id: Option<String>,
        event: String,
        #[serde(default)]
        data: Json,
    },
    FrameMeta {
        camera_id: String,
        seq: i64,
        t_capture_ns: i64,
        w: i64,
        h: i64,
    },
    DeviceChanged {
        device: Device,
    },
    ConfirmationRequested {
        confirmation_id: String,
        request_id: String,
        action_digest: String,
        summary: String,
        via: Vec<ConfirmVia>,
        expires_ns: i64,
    },
    ConfirmationResolved {
        confirmation_id: String,
        request_id: String,
        action_digest: String,
        approved: bool,
        via: ResolvedVia,
    },
    HomeCallDone {
        request_id: String,
        #[serde(default)]
        confirmation_id: Option<String>,
        status: HomeDoneStatus,
        #[serde(default)]
        error: Option<String>,
    },
    Blanked {
        request_id: String,
        projector_id: String,
        t_start_ms: f64,
        t_end_ms: f64,
    },
}

impl Event {
    /// `type` del evento (tag serde).
    pub fn tag(&self) -> String {
        tag_of(self)
    }
}

pub fn tag_of<T: Serialize>(v: &T) -> String {
    match serde_json::to_value(v) {
        Ok(Value::Object(m)) => m.get("type").and_then(Value::as_str).unwrap_or_default().to_string(),
        _ => String::new(),
    }
}

// ------------------------------------------------------------------ commands, acciones y flujos

fn request_id<'de, D: Deserializer<'de>>(d: D) -> Result<String, D::Error> {
    let s = String::deserialize(d)?;
    if s.is_empty() || s.len() > 128 {
        return Err(D::Error::custom("request_id debe tener 1..128 caracteres"));
    }
    Ok(s)
}

fn service_name<'de, D: Deserializer<'de>>(d: D) -> Result<String, D::Error> {
    let s = String::deserialize(d)?;
    if s.is_empty() || !s.bytes().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'_') {
        return Err(D::Error::custom(format!("{s:?} no calza ^[a-z0-9_]+$")));
    }
    Ok(s)
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HomeCallBody {
    #[serde(deserialize_with = "service_name")]
    pub domain: String,
    #[serde(deserialize_with = "service_name")]
    pub service: String,
    #[serde(default)]
    pub data: Json,
}

/// `home_call` con `request_id` (campos desconocidos, p. ej. el viejo `requires_confirmation`, se rechazan).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HomeCallCmd {
    #[serde(deserialize_with = "request_id")]
    pub request_id: String,
    #[serde(deserialize_with = "service_name")]
    pub domain: String,
    #[serde(deserialize_with = "service_name")]
    pub service: String,
    #[serde(default)]
    pub data: Json,
}

impl HomeCallCmd {
    pub fn body(&self) -> HomeCallBody {
        HomeCallBody { domain: self.domain.clone(), service: self.service.clone(), data: self.data.clone() }
    }
}

/// Acción de un flujo = comando sin `request_id` (el nodo lo deriva).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Action {
    Project { widgets: Vec<Widget> },
    Update { id: String, set: Json },
    Remove { ids: Vec<String> },
    Clear {},
    Emit { event: String, #[serde(default)] data: Json },
    HomeCall(HomeCallBody),
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct EventFilter {
    pub event: String,
    #[serde(default, rename = "where")]
    pub where_: Json,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Transition {
    #[serde(default)]
    pub on: Option<EventFilter>,
    #[serde(default)]
    pub after_s: Option<f64>,
    pub to: String,
    #[serde(default)]
    pub actions: Vec<Action>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct FlowState {
    pub id: String,
    #[serde(default)]
    pub on_enter: Vec<Action>,
    #[serde(default)]
    pub transitions: Vec<Transition>,
    #[serde(default)]
    pub r#final: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Flow {
    pub id: String,
    pub initial: String,
    pub states: Vec<FlowState>,
    #[serde(default = "yes")]
    pub cleanup: bool,
    #[serde(default)]
    pub timeout_s: Option<f64>,
}

/// Errores de coherencia del flujo (vacío = válido). Igual que `commands.check_flow` en Python.
pub fn check_flow(flow: &Flow) -> Vec<String> {
    let mut errs = Vec::new();
    let ids: Vec<&str> = flow.states.iter().map(|s| s.id.as_str()).collect();
    if ids.is_empty() {
        errs.push("el flujo no tiene estados".into());
    }
    let mut sorted = ids.clone();
    sorted.sort();
    sorted.dedup();
    for i in &sorted {
        if ids.iter().filter(|x| *x == i).count() > 1 {
            errs.push(format!("estado duplicado: {i}"));
        }
    }
    if !ids.contains(&flow.initial.as_str()) {
        errs.push(format!("initial {:?} no existe", flow.initial));
    }
    if let Some(t) = flow.timeout_s {
        if !(t > 0.0 && t <= 86400.0) {
            errs.push("timeout_s fuera de (0, 86400]".into());
        }
    }
    let mut immediate: BTreeMap<&str, Vec<&str>> = BTreeMap::new();
    for s in &flow.states {
        if s.r#final && !s.transitions.is_empty() {
            errs.push(format!("{}: un estado final no puede tener transiciones", s.id));
        }
        for t in &s.transitions {
            if !ids.contains(&t.to.as_str()) {
                errs.push(format!("{}: transición a {:?} que no existe", s.id, t.to));
            }
            match (&t.on, t.after_s) {
                (None, None) => errs.push(format!("{}: transición a {:?} sin `on` ni `after_s`", s.id, t.to)),
                (Some(_), Some(_)) => {
                    errs.push(format!("{}: transición a {:?} con `on` y `after_s` a la vez (ambiguo)", s.id, t.to))
                }
                _ => {}
            }
            if let Some(a) = t.after_s {
                if !(0.0..=86400.0).contains(&a) {
                    errs.push(format!("{}: after_s negativo o fuera de rango", s.id));
                }
                if t.on.is_none() && a == 0.0 {
                    immediate.entry(s.id.as_str()).or_default().push(t.to.as_str());
                }
            }
        }
    }
    fn cyc<'a>(n: &'a str, g: &BTreeMap<&'a str, Vec<&'a str>>, stack: &mut Vec<&'a str>, seen: &mut Vec<&'a str>) -> bool {
        if stack.contains(&n) {
            return true;
        }
        if seen.contains(&n) {
            return false;
        }
        seen.push(n);
        stack.push(n);
        let r = g.get(n).is_some_and(|ns| ns.iter().any(|m| cyc(m, g, stack, seen)));
        stack.pop();
        r
    }
    let mut seen = Vec::new();
    if immediate.keys().any(|n| cyc(n, &immediate, &mut Vec::new(), &mut seen)) {
        errs.push("ciclo de transiciones inmediatas (after_s: 0)".into());
    }
    errs
}

fn de_flow<'de, D: Deserializer<'de>>(d: D) -> Result<Flow, D::Error> {
    let f = Flow::deserialize(d)?;
    let errs = check_flow(&f);
    if errs.is_empty() {
        Ok(f)
    } else {
        Err(D::Error::custom(format!("flujo inválido: {}", errs.join("; "))))
    }
}

fn ask_timeout() -> f64 {
    60.0
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CalibrateMode {
    #[default]
    Quick,
    Precise,
    Verify,
}

/// Comando hacia el nodo (todos con `request_id` obligatorio).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Command {
    Project {
        #[serde(deserialize_with = "request_id")]
        request_id: String,
        widgets: Vec<Widget>,
    },
    Update {
        #[serde(deserialize_with = "request_id")]
        request_id: String,
        id: String,
        set: Json,
    },
    Remove {
        #[serde(deserialize_with = "request_id")]
        request_id: String,
        ids: Vec<String>,
    },
    Clear {
        #[serde(deserialize_with = "request_id")]
        request_id: String,
    },
    Ask {
        #[serde(deserialize_with = "request_id")]
        request_id: String,
        question: String,
        options: Vec<String>,
        #[serde(default = "ask_timeout")]
        timeout_s: f64,
        #[serde(default)]
        anchor: Option<pcx_contracts::Anchor>,
    },
    Emit {
        #[serde(deserialize_with = "request_id")]
        request_id: String,
        event: String,
        #[serde(default)]
        data: Json,
    },
    RunFlow {
        #[serde(deserialize_with = "request_id")]
        request_id: String,
        #[serde(deserialize_with = "de_flow")]
        flow: Flow,
    },
    StopFlow {
        #[serde(deserialize_with = "request_id")]
        request_id: String,
        flow_id: String,
    },
    Calibrate {
        #[serde(deserialize_with = "request_id")]
        request_id: String,
        #[serde(default)]
        mode: CalibrateMode,
        #[serde(default)]
        camera_id: Option<String>,
        #[serde(default)]
        projector_id: Option<String>,
    },
    SetTransform {
        #[serde(deserialize_with = "request_id")]
        request_id: String,
        transform: Transform,
    },
    HomeCall(HomeCallCmd),
    Blank {
        #[serde(deserialize_with = "request_id")]
        request_id: String,
        ms: u32,
        reason: String,
        #[serde(default)]
        projector_id: Option<String>,
    },
}

impl Command {
    pub fn request_id(&self) -> &str {
        match self {
            Command::Project { request_id, .. }
            | Command::Update { request_id, .. }
            | Command::Remove { request_id, .. }
            | Command::Clear { request_id }
            | Command::Ask { request_id, .. }
            | Command::Emit { request_id, .. }
            | Command::RunFlow { request_id, .. }
            | Command::StopFlow { request_id, .. }
            | Command::Calibrate { request_id, .. }
            | Command::SetTransform { request_id, .. }
            | Command::HomeCall(HomeCallCmd { request_id, .. })
            | Command::Blank { request_id, .. } => request_id,
        }
    }

    /// Restricciones que serde no expresa (ids reservados, rangos).
    pub fn validate(&self) -> Result<(), String> {
        let reserved = |ids: &mut dyn Iterator<Item = &str>| -> Result<(), String> {
            let bad: Vec<&str> = ids.filter(|i| i.starts_with(RESERVED_WIDGET_PREFIX)).collect();
            if bad.is_empty() {
                Ok(())
            } else {
                Err(format!("ids reservados del nodo ({RESERVED_WIDGET_PREFIX}*): {bad:?}"))
            }
        };
        match self {
            Command::Project { widgets, .. } => reserved(&mut widgets.iter().map(|w| w.id())),
            Command::Update { id, .. } => reserved(&mut std::iter::once(id.as_str())),
            Command::Remove { ids, .. } => reserved(&mut ids.iter().map(String::as_str)),
            Command::Ask { options, timeout_s, .. } => {
                if options.is_empty() || options.len() > 6 {
                    Err("options: entre 1 y 6".into())
                } else if !(1.0..=600.0).contains(timeout_s) {
                    Err("timeout_s fuera de [1, 600]".into())
                } else {
                    Ok(())
                }
            }
            Command::Blank { ms, .. } if !(1..=2000).contains(ms) => Err(format!("ms {ms} fuera de [1, 2000]")),
            _ => Ok(()),
        }
    }
}

// ------------------------------------------------------------------ envelope

/// Payload del sobre: comando o evento (sus `type` no se solapan).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum Payload {
    Event(Event),
    Command(Command),
}

impl Payload {
    pub fn tag(&self) -> String {
        tag_of(self)
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(try_from = "RawEnvelope")]
pub struct Envelope {
    #[serde(rename = "type")]
    pub type_: String,
    pub v: u32,
    pub seq: i64,
    pub t_capture_ns: Option<i64>,
    pub t_pub_ns: i64,
    pub source: String,
    pub node_id: String,
    pub payload: Payload,
}

#[derive(Deserialize)]
struct RawEnvelope {
    #[serde(rename = "type")]
    type_: String,
    v: u32,
    seq: i64,
    #[serde(default)]
    t_capture_ns: Option<i64>,
    t_pub_ns: i64,
    source: String,
    node_id: String,
    payload: Payload,
}

impl TryFrom<RawEnvelope> for Envelope {
    type Error = String;
    fn try_from(r: RawEnvelope) -> Result<Self, String> {
        if !SUPPORTED_VERSIONS.contains(&r.v) {
            return Err(format!("versión de contrato desconocida: {} (soportadas: {SUPPORTED_VERSIONS:?})", r.v));
        }
        let tag = r.payload.tag();
        if r.type_ != tag {
            return Err(format!("type {:?} no coincide con payload.type {tag:?}", r.type_));
        }
        Ok(Envelope {
            type_: r.type_,
            v: r.v,
            seq: r.seq,
            t_capture_ns: r.t_capture_ns,
            t_pub_ns: r.t_pub_ns,
            source: r.source,
            node_id: r.node_id,
            payload: r.payload,
        })
    }
}

pub fn now_ns() -> i64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos() as i64).unwrap_or(0)
}

pub fn now_ms() -> f64 {
    now_ns() as f64 / 1e6
}

pub fn wrap(payload: Payload, seq: i64, source: &str, node_id: &str, t_capture_ns: Option<i64>) -> Envelope {
    Envelope {
        type_: payload.tag(),
        v: CONTRACT_VERSION,
        seq,
        t_capture_ns,
        t_pub_ns: now_ns(),
        source: source.into(),
        node_id: node_id.into(),
        payload,
    }
}

/// Decodifica un sobre en JSON o msgpack (detecta por el primer byte).
pub fn decode_envelope(raw: &[u8]) -> Result<Envelope, String> {
    match raw.iter().find(|b| !b.is_ascii_whitespace()) {
        Some(b'{') => serde_json::from_slice(raw).map_err(|e| e.to_string()),
        _ => rmp_serde::from_slice(raw).map_err(|e| e.to_string()),
    }
}

pub fn encode_msgpack<T: Serialize>(v: &T) -> Vec<u8> {
    rmp_serde::to_vec_named(v).unwrap_or_default()
}

// ------------------------------------------------------------------ home

fn star() -> Vec<String> {
    vec!["*".into()]
}
fn both_via() -> Vec<ConfirmVia> {
    vec![ConfirmVia::Touch, ConfirmVia::Console]
}
fn thirty() -> f64 {
    30.0
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct HomePolicy {
    #[serde(default)]
    pub allow: Vec<String>,
    #[serde(default = "star")]
    pub confirm: Vec<String>,
    #[serde(default = "both_via")]
    pub confirm_via: Vec<ConfirmVia>,
    #[serde(default = "thirty")]
    pub confirm_timeout_s: f64,
}

impl Default for HomePolicy {
    fn default() -> Self {
        HomePolicy { allow: vec![], confirm: star(), confirm_via: both_via(), confirm_timeout_s: 30.0 }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Decision {
    Allow,
    Confirm,
    Deny,
}

/// `fnmatchcase` reducido a `*` y `?` (lo que usa la política).
pub fn glob(pat: &str, s: &str) -> bool {
    fn go(p: &[u8], s: &[u8]) -> bool {
        match (p.first(), s.first()) {
            (None, None) => true,
            (Some(b'*'), _) => go(&p[1..], s) || (!s.is_empty() && go(p, &s[1..])),
            (Some(b'?'), Some(_)) => go(&p[1..], &s[1..]),
            (Some(a), Some(b)) if a == b => go(&p[1..], &s[1..]),
            _ => false,
        }
    }
    go(pat.as_bytes(), s.as_bytes())
}

pub fn decide(policy: &HomePolicy, call: &HomeCallBody) -> Decision {
    let name = format!("{}.{}", call.domain, call.service);
    if !policy.allow.iter().any(|p| glob(p, &name)) {
        Decision::Deny
    } else if policy.confirm.iter().any(|p| glob(p, &name)) {
        Decision::Confirm
    } else {
        Decision::Allow
    }
}

/// sha256 hex de `{"domain","service","data"}` en JSON canónico (claves ordenadas, sin espacios, UTF-8).
pub fn action_digest(call: &HomeCallBody) -> String {
    use sha2::{Digest, Sha256};
    fn sorted(v: &Value) -> Value {
        match v {
            Value::Object(m) => {
                let b: BTreeMap<&String, Value> = m.iter().map(|(k, v)| (k, sorted(v))).collect();
                serde_json::to_value(b).unwrap_or(Value::Null)
            }
            Value::Array(a) => Value::Array(a.iter().map(sorted).collect()),
            x => x.clone(),
        }
    }
    let body = serde_json::json!({"data": sorted(&Value::Object(call.data.clone())), "domain": call.domain, "service": call.service});
    let raw = serde_json::to_string(&body).unwrap_or_default();
    let mut h = Sha256::new();
    h.update(raw.as_bytes());
    h.finalize().iter().map(|b| format!("{b:02x}")).collect()
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum HomeCallStatus {
    Done,
    Pending,
    Denied,
    Failed,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct HomeCallResult {
    pub request_id: String,
    pub status: HomeCallStatus,
    #[serde(default)]
    pub confirmation_id: Option<String>,
    #[serde(default)]
    pub action_digest: Option<String>,
    #[serde(default)]
    pub expires_ns: Option<i64>,
    #[serde(default)]
    pub error: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename = "confirm_action")]
pub struct ConfirmAction {
    pub confirmation_id: String,
    pub action_digest: String,
    pub approve: bool,
    pub operator: String,
}

// ------------------------------------------------------------------ cloud

fn ten_k() -> i64 {
    10_000
}
fn hour() -> f64 {
    3600.0
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LogRetention {
    #[serde(default = "ten_k")]
    pub max_events: i64,
    #[serde(default = "hour")]
    pub max_age_s: f64,
}

impl Default for LogRetention {
    fn default() -> Self {
        LogRetention { max_events: 10_000, max_age_s: 3600.0 }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GapReason {
    Retention,
    StreamReset,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct HistoryGap {
    pub stream_id: String,
    #[serde(rename = "from")]
    pub from_: i64,
    pub to: Option<i64>,
    pub reason: GapReason,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ContentItem {
    Text { text: String },
    Image { data: String, #[serde(default = "png_mime")] mime_type: String },
}

fn png_mime() -> String {
    "image/png".into()
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum CloudMessage {
    Hello {
        node_id: String,
        token: String,
        #[serde(default)]
        capabilities: Vec<String>,
        version: String,
        contract_versions: Vec<u32>,
        stream_id: String,
        log_first_cursor: i64,
        log_last_cursor: i64,
        #[serde(default)]
        retention: LogRetention,
    },
    Welcome {
        session_id: String,
        contract_version: u32,
        #[serde(default)]
        last_stream_id: Option<String>,
        #[serde(default)]
        last_cursor: Option<i64>,
    },
    HelloRejected {
        reason: String,
        #[serde(default)]
        supported_versions: Vec<u32>,
        #[serde(default)]
        detail: Option<String>,
    },
    StateSnapshot {
        stream_id: String,
        cursor: i64,
        world: WorldState,
    },
    HistoryGap(HistoryGap),
    EventBatch {
        stream_id: String,
        cursor: i64,
        events: Vec<Envelope>,
        #[serde(default)]
        replay: bool,
    },
    ReplayDone {
        stream_id: String,
        cursor: i64,
        history_complete: bool,
    },
    ToolCall {
        call_id: String,
        tool: String,
        #[serde(default)]
        args: Json,
        #[serde(default)]
        timeout_s: Option<f64>,
    },
    ToolResult {
        call_id: String,
        #[serde(default)]
        request_id: Option<String>,
        ok: bool,
        #[serde(default)]
        content: Vec<ContentItem>,
        #[serde(default)]
        structured: Option<Json>,
    },
    Heartbeat {
        t_ms: f64,
        #[serde(default)]
        cursor: Option<i64>,
    },
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct EventsWaitResult {
    pub stream_id: String,
    pub cursor: i64,
    #[serde(default)]
    pub events: Vec<Envelope>,
    #[serde(default)]
    pub gap: Option<HistoryGap>,
}

// ------------------------------------------------------------------ args de tools de solo lectura

#[derive(Debug, Clone, Default, Deserialize)]
pub struct SceneArgs {
    #[serde(default)]
    pub include_hands: bool,
    #[serde(default)]
    pub include_devices: bool,
}

fn true_() -> bool {
    true
}

#[derive(Debug, Clone, Deserialize)]
pub struct LookArgs {
    #[serde(default)]
    pub camera_id: Option<String>,
    #[serde(default = "true_")]
    pub annotate: bool,
}

fn t25() -> f64 {
    25.0
}
fn fifty() -> i64 {
    50
}

#[derive(Debug, Clone, Deserialize)]
pub struct EventsWaitArgs {
    #[serde(default)]
    pub cursor: i64,
    #[serde(default)]
    pub types: Vec<String>,
    #[serde(default = "t25")]
    pub timeout_s: f64,
    #[serde(default = "fifty")]
    pub max_events: i64,
}

#[derive(Debug, Clone, Default, Deserialize)]
pub struct DevicesArgs {
    #[serde(default)]
    pub kind: Option<DeviceKind>,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture(rel: &str) -> String {
        let p = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../contracts/fixtures").join(rel);
        std::fs::read_to_string(p).unwrap()
    }

    #[test]
    fn envelope_y_tool_call_iguales_a_python() {
        {
            let name = "envelope_widget_pressed.json";
            let env: Envelope = serde_json::from_str(&fixture(name)).unwrap();
            let canon: Value = serde_json::from_str(&fixture(&format!("canonical/{name}"))).unwrap();
            assert_eq!(serde_json::to_value(&env).unwrap(), canon);
            // msgpack ida y vuelta
            assert_eq!(decode_envelope(&encode_msgpack(&env)).unwrap(), env);
        }
        let tc: CloudMessage = serde_json::from_str(&fixture("tool_call.json")).unwrap();
        assert!(matches!(tc, CloudMessage::ToolCall { ref tool, .. } if tool == "project"));
        let flow: Command = serde_json::from_str(&fixture("flow_guia_3_pasos.json")).unwrap();
        let canon: Value = serde_json::from_str(&fixture("canonical/flow_guia_3_pasos.json")).unwrap();
        assert_eq!(serde_json::to_value(&flow).unwrap(), canon);
    }

    #[test]
    fn envelope_rechaza_version_y_type() {
        let mut v: Value = serde_json::from_str(&fixture("envelope_widget_pressed.json")).unwrap();
        v["v"] = 1.into();
        assert!(serde_json::from_value::<Envelope>(v.clone()).is_err());
        v["v"] = 2.into();
        v["type"] = "touch_down".into();
        assert!(serde_json::from_value::<Envelope>(v).is_err());
    }

    #[test]
    fn flujo_invalido_y_home() {
        let bad = serde_json::json!({"type": "run_flow", "request_id": "r", "flow": {"id": "f", "initial": "zzz", "states": [{"id": "a"}]}});
        assert!(serde_json::from_value::<Command>(bad).unwrap_err().to_string().contains("initial"));
        let cyc = serde_json::json!({"id": "f", "initial": "a", "states": [
            {"id": "a", "transitions": [{"after_s": 0, "to": "b"}]}, {"id": "b", "transitions": [{"after_s": 0, "to": "a"}]}]});
        assert!(check_flow(&serde_json::from_value(cyc).unwrap()).iter().any(|e| e.contains("ciclo")));
        let old = serde_json::json!({"type": "home_call", "request_id": "r", "domain": "light", "service": "turn_on", "requires_confirmation": true});
        assert!(serde_json::from_value::<Command>(old).is_err());
        let p = HomePolicy { allow: vec!["light.*".into()], confirm: vec!["lock.*".into()], ..Default::default() };
        let call = HomeCallBody { domain: "light".into(), service: "turn_on".into(), data: Json::new() };
        assert_eq!(decide(&p, &call), Decision::Allow);
        assert_eq!(decide(&HomePolicy::default(), &call), Decision::Deny);
        assert_eq!(action_digest(&call).len(), 64);
    }
}
