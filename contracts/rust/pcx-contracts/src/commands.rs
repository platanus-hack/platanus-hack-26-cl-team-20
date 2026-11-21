//! Comandos hacia el nodo, acciones y flujos (espejo de `commands.py`). Todo comando lleva `request_id`.

use std::collections::BTreeMap;

use serde::de::Error as _;
use serde::{Deserialize, Deserializer, Serialize};

use crate::geometry::Transform;
use crate::widgets::{Anchor, Widget};
use crate::Json;

pub const IDEMPOTENCY_WINDOW_S: i64 = 600;
pub const MAX_IMMEDIATE_TRANSITIONS: usize = 8;
pub const RESERVED_WIDGET_PREFIX: &str = "pcx:";

fn yes() -> bool {
    true
}

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
    Project {
        widgets: Vec<Widget>,
    },
    Update {
        id: String,
        set: Json,
    },
    Remove {
        ids: Vec<String>,
    },
    Clear {},
    Emit {
        event: String,
        #[serde(default)]
        data: Json,
    },
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
    fn cyc<'a>(
        n: &'a str,
        g: &BTreeMap<&'a str, Vec<&'a str>>,
        stack: &mut Vec<&'a str>,
        seen: &mut Vec<&'a str>,
    ) -> bool {
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
        anchor: Option<Anchor>,
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
