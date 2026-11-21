//! Tipos serde compatibles con `contracts/schema/` (v2): `Envelope`, `FrameMeta`, `DeviceChanged`, `Device`.
//! Solo lo que publica la captura. Por ZeroMQ viajan en msgpack con nombres de campo (igual que `msgspec`).

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

pub const CONTRACT_VERSION: u32 = 2;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct FrameMeta {
    pub camera_id: String,
    pub seq: u64,
    pub t_capture_ns: u64,
    pub w: u32,
    pub h: u32,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DeviceKind {
    Camera,
    Projector,
    Phone,
    HomeAssistant,
    Midi,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DeviceStatus {
    Online,
    Offline,
    Degraded,
    #[default]
    Unknown,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Device {
    pub id: String,
    pub kind: DeviceKind,
    /// Adaptador que lo maneja, p. ej. `uvc`, `replay`, `mjpeg`, `phone`.
    pub adapter: String,
    #[serde(default)]
    pub caps: Vec<String>,
    #[serde(default)]
    pub status: DeviceStatus,
    #[serde(default)]
    pub meta: BTreeMap<String, serde_json::Value>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct DeviceChanged {
    pub device: Device,
}

/// Subconjunto de `Event` (unión por `type`) que emite la captura.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum CaptureEvent {
    FrameMeta(FrameMeta),
    DeviceChanged(DeviceChanged),
}

impl CaptureEvent {
    pub fn type_name(&self) -> &'static str {
        match self {
            Self::FrameMeta(_) => "frame_meta",
            Self::DeviceChanged(_) => "device_changed",
        }
    }
}

/// Sobre `{type, v, seq, t_capture_ns, t_pub_ns, source, node_id, payload}`.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Envelope {
    #[serde(rename = "type")]
    pub type_: String,
    pub v: u32,
    pub seq: u64,
    pub t_capture_ns: Option<u64>,
    pub t_pub_ns: u64,
    pub source: String,
    pub node_id: String,
    pub payload: CaptureEvent,
}

impl Envelope {
    pub fn wrap(payload: CaptureEvent, seq: u64, source: &str, node_id: &str, t_capture_ns: Option<u64>) -> Self {
        Self {
            type_: payload.type_name().to_string(),
            v: CONTRACT_VERSION,
            seq,
            t_capture_ns,
            t_pub_ns: now_ns(),
            source: source.to_string(),
            node_id: node_id.to_string(),
            payload,
        }
    }

    /// msgpack con nombres de campo (lo que decodifica `msgspec.msgpack.Decoder(Envelope)`).
    pub fn to_msgpack(&self) -> Vec<u8> {
        rmp_serde::to_vec_named(self).expect("Envelope siempre serializa")
    }
}

pub fn now_ns() -> u64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos() as u64).unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn frame_meta_matches_schema_shape() {
        let env = Envelope::wrap(
            CaptureEvent::FrameMeta(FrameMeta { camera_id: "cam0".into(), seq: 7, t_capture_ns: 11, w: 1280, h: 720 }),
            3,
            "capture-cam0",
            "mesa",
            Some(11),
        );
        let mut j = serde_json::to_value(&env).unwrap();
        j["t_pub_ns"] = 0.into();
        assert_eq!(
            j,
            serde_json::json!({"type": "frame_meta", "v": 2, "seq": 3, "t_capture_ns": 11, "t_pub_ns": 0,
                "source": "capture-cam0", "node_id": "mesa",
                "payload": {"type": "frame_meta", "camera_id": "cam0", "seq": 7, "t_capture_ns": 11, "w": 1280, "h": 720}})
        );
        let back: Envelope = rmp_serde::from_slice(&env.to_msgpack()).unwrap();
        assert_eq!(back, env);
        // msgpack como mapa con nombres (no arreglo posicional)
        let v: serde_json::Value = rmp_serde::from_slice(&env.to_msgpack()).unwrap();
        assert_eq!(v["payload"]["camera_id"], "cam0");
    }

    #[test]
    fn device_defaults() {
        let d: Device = serde_json::from_str(r#"{"id":"cam0","kind":"camera","adapter":"uvc"}"#).unwrap();
        assert_eq!((d.status, d.caps.len()), (DeviceStatus::Unknown, 0));
        let j = serde_json::to_value(CaptureEvent::DeviceChanged(DeviceChanged { device: d })).unwrap();
        assert_eq!(j["type"], "device_changed");
        assert_eq!(j["device"]["status"], "unknown");
    }
}
