//! Contratos del nodo: re-exporta `pcx-contracts` (world, events, commands, envelope, home, cloud, render) y agrega
//! lo propio del core: códec msgpack del sobre y args de las tools de solo lectura.

pub use pcx_contracts::*;
use serde::{Deserialize, Serialize};

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
    fn envelope_msgpack_ida_y_vuelta() {
        for name in ["envelope_widget_pressed.json", "envelope_frame_meta.json", "envelope_hands_gone.json"] {
            let env: Envelope = serde_json::from_str(&fixture(name)).unwrap();
            assert_eq!(decode_envelope(&encode_msgpack(&env)).unwrap(), env, "{name}");
            assert_eq!(decode_envelope(fixture(name).as_bytes()).unwrap(), env, "{name}");
        }
    }
}
