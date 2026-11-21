//! Protocolo nodo ↔ nube por WSS (espejo de `cloud.py`).

use serde::{Deserialize, Serialize};

use crate::envelope::Envelope;
use crate::home::ConfirmAction;
use crate::world::WorldState;
use crate::Json;

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
    /// v2.1, nube → nodo: solo desde la consola de personas (nunca un agente).
    ConfirmAction(ConfirmAction),
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
