//! Sobre común `{type, v, seq, t_capture_ns, t_pub_ns, source, node_id, payload}` (espejo de `envelope.py`).
//! `seq` es monótono por `stream_id` del nodo; **el core lo renumera** al ingerir lo que publican los
//! productores locales (captura, manos, objetos): el `seq` de ellos solo vale en su proceso.

use serde::{Deserialize, Serialize};

use crate::commands::Command;
use crate::events::{tag_of, Event};
use crate::CONTRACT_VERSION;

pub const SUPPORTED_VERSIONS: &[u32] = &[2];

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
