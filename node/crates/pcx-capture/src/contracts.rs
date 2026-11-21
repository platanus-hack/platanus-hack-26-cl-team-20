//! Tipos del contrato (v2.1) que publica la captura, tomados de `pcx-contracts`: `Envelope`, `Event::FrameMeta`,
//! `Event::DeviceChanged`, `Device`. Por ZeroMQ viajan en msgpack con nombres de campo (igual que `msgspec`).

pub use pcx_contracts::{wrap, Device, DeviceKind, DeviceStatus, Envelope, Event, FrameMeta, Payload, PixelFormat};

/// Evento que emite la captura (`frame_meta` o `device_changed`); alias de `pcx_contracts::Event`.
pub type CaptureEvent = Event;

/// msgpack con nombres de campo (lo que decodifica `msgspec.msgpack.Decoder(Envelope)`).
pub trait ToMsgpack {
    fn to_msgpack(&self) -> Vec<u8>;
}

impl ToMsgpack for Envelope {
    fn to_msgpack(&self) -> Vec<u8> {
        rmp_serde::to_vec_named(self).expect("Envelope siempre serializa")
    }
}

/// ns de época (u64, como `pcx-shm` y las fuentes).
pub fn now_ns() -> u64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos() as u64).unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn frame_meta_matches_schema_shape() {
        let fm = FrameMeta {
            camera_id: "cam0".into(),
            seq: 7,
            t_capture_ns: 11,
            w: 1280,
            h: 720,
            segment: Some("pcx.cam.cam0".into()),
            format: PixelFormat::Bgr8,
            stride: Some(3840),
        };
        let env = wrap(Payload::Event(Event::FrameMeta(fm)), 3, "capture-cam0", "mesa", Some(11));
        let mut j = serde_json::to_value(&env).unwrap();
        j["t_pub_ns"] = 0.into();
        assert_eq!(
            j,
            serde_json::json!({"type": "frame_meta", "v": 2, "seq": 3, "t_capture_ns": 11, "t_pub_ns": 0,
                "source": "capture-cam0", "node_id": "mesa",
                "payload": {"type": "frame_meta", "camera_id": "cam0", "seq": 7, "t_capture_ns": 11, "w": 1280, "h": 720,
                            "segment": "pcx.cam.cam0", "format": "bgr8", "stride": 3840}})
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
        let j = serde_json::to_value(Event::DeviceChanged { device: d }).unwrap();
        assert_eq!(j["type"], "device_changed");
        assert_eq!(j["device"]["status"], "unknown");
    }
}
