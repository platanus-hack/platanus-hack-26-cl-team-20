//! Estado del mundo que mantiene el nodo (espejo de `world.py` y `devices.py`). Tiempos: ns de época (i64).

use serde::{Deserialize, Serialize};

use crate::geometry::{PointMM, PointPX, PolygonMM, RectMM, Surface, Transform};
use crate::widgets::{Trigger, Widget};
use crate::Json;

fn t_dwell() -> Trigger {
    Trigger::Dwell
}

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
