//! Domótica: política del nodo, digest de la acción y confirmación (espejo de `home.py`).

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::commands::HomeCallBody;
use crate::events::ConfirmVia;

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

fn console() -> ConfirmVia {
    ConfirmVia::Console
}

/// Consola (local por HTTP, o de la nube como `CloudMessage::ConfirmAction`) → nodo. Debe repetir el
/// `action_digest`. v2.1: `approve` → `approved`, nuevo `by`, `operator` opcional. Sin tag propio: el `type`
/// lo pone `CloudMessage` (por HTTP local se acepta con o sin `"type"`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ConfirmAction {
    pub confirmation_id: String,
    pub approved: bool,
    #[serde(default = "console")]
    pub by: ConfirmVia,
    #[serde(deserialize_with = "crate::validate::sha256")]
    pub action_digest: String,
    #[serde(default)]
    pub operator: Option<String>,
}
