//! Eventos que publica el nodo (espejo de `events.py`), unión por `type`. Viajan dentro de `Envelope`.

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::geometry::{PointMM, PolygonMM};
use crate::widgets::Trigger;
use crate::world::{Device, ObjectState, Touch};
use crate::Json;

fn t_dwell() -> Trigger {
    Trigger::Dwell
}

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
    FrameMeta(FrameMeta),
    /// v2.1: fin de un episodio de manos.
    HandsGone(HandsGone),
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

/// Formato de píxel del anillo de memoria compartida.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PixelFormat {
    #[default]
    Bgr8,
    Rgb8,
    Gray8,
}

impl PixelFormat {
    pub fn channels(self) -> u32 {
        match self {
            PixelFormat::Bgr8 | PixelFormat::Rgb8 => 3,
            PixelFormat::Gray8 => 1,
        }
    }
}

/// Cuadro nuevo en el anillo `segment` (`type: "frame_meta"`). v2.1: `segment`, `format`, `stride`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FrameMeta {
    pub camera_id: String,
    /// Secuencia del cuadro en la cámara (no es `Envelope.seq`).
    pub seq: i64,
    pub t_capture_ns: i64,
    pub w: i64,
    pub h: i64,
    /// Segmento de memoria compartida (`pcx-shm`), p. ej. `pcx.cam.cam0`; `None` = sin anillo.
    #[serde(default)]
    pub segment: Option<String>,
    #[serde(default)]
    pub format: PixelFormat,
    /// Bytes por fila (`None` = `w * canales`).
    #[serde(default)]
    pub stride: Option<u32>,
}

/// Fin de un episodio de manos (`type: "hands_gone"`): hubo manos y ya no hay.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct HandsGone {
    /// Cuadro que cerró el episodio.
    pub t_capture_ns: i64,
    #[serde(default)]
    pub episode_start_ns: Option<i64>,
    #[serde(default)]
    pub last_hand_ns: Option<i64>,
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
