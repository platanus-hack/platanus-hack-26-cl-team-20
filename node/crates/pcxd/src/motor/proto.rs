//! Mensajes del protocolo motor ↔ web app, v0: JSON con el campo `type`.
//!
//! La entrada es tolerante con lo que el protocolo no fija: los `id` viajan como llegan (texto o número) y los
//! `t_ns` de la web app se aceptan enteros o con decimales (el motor nunca compara su reloj con el del navegador).

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};
use serde_json::Value;

/// Versión del protocolo (`v`).
pub const PROTO_V: u64 = 0;

#[derive(Debug, Clone, Deserialize)]
pub struct LensMsg {
    pub f: f64,
    pub cx: f64,
    pub cy: f64,
    pub k1: f64,
    pub k2: f64,
}

/// Web app → motor.
#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum In {
    Hello {
        #[serde(default)]
        v: Option<f64>,
        #[serde(default)]
        token: String,
        #[serde(default)]
        session_id: Value,
        #[serde(default)]
        app: Value,
    },
    Calib {
        camera_px: [f64; 2],
        /// Homografía px → mm, por filas.
        matrix_px_to_mm: [f64; 9],
        #[serde(default)]
        lens: Option<LensMsg>,
        surface_mm: [f64; 2],
    },
    Blanked {
        id: Value,
        #[serde(default)]
        t_ns: Option<f64>,
    },
    Unblanked {
        id: Value,
    },
    Hands {
        present: bool,
        #[serde(default)]
        t_ns: Option<f64>,
    },
    Perceive {},
    Reset {},
    /// v0.1: escena o preset activo en la web app; `null` = ninguna.
    Scene {
        #[serde(default)]
        name: Option<String>,
    },
    Look {
        id: Value,
        #[serde(default)]
        clean: bool,
    },
    Frame {
        id: Value,
        jpeg_b64: String,
        #[serde(default)]
        w: Option<f64>,
        #[serde(default)]
        h: Option<f64>,
        #[serde(default)]
        t_ns: Option<f64>,
    },
}

/// Cámara del motor en `welcome.cameras` (v0.1).
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct CameraRole {
    pub id: String,
    /// `"cenital"` o `"lateral"`.
    pub role: String,
    pub name: String,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct CameraMsg {
    pub id: String,
    pub name: String,
    pub px: [u32; 2],
}

/// Modelos cargados en el motor. Con el `Perceptor` de respaldo (`DiffOnly`) ambos son `false`: las manos las
/// detecta la web app (MediaPipe).
#[derive(Debug, Clone, PartialEq, Eq, Default, Serialize)]
pub struct Models {
    pub sam: bool,
    pub hands: bool,
    /// Qué percepción corre y, si falta SAM, por qué (`"cascada + MobileSAM de …"`, `"sin SAM: solo la cascada (…)"`).
    /// Vacío con el `Perceptor` de respaldo.
    #[serde(skip_serializing_if = "String::is_empty")]
    pub note: String,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct ObjectMsg {
    pub id: String,
    pub label: String,
    /// ≤ 64 puntos, en mm del mundo de la web app.
    pub polygon_mm: Vec<[f64; 2]>,
    pub center_mm: [f64; 2],
    pub confidence: f32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum State {
    Idle,
    Calm,
    Blank,
    Capturing,
    Perceiving,
}

/// Motor → web app.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Out {
    Welcome {
        v: u64,
        motor: String,
        camera: Option<CameraMsg>,
        models: Models,
        needs_frames: bool,
        /// v0.1: módulos de escena que trae el motor.
        scenes: Vec<String>,
        /// v0.1: cámaras abiertas por el motor.
        cameras: Vec<CameraRole>,
    },
    /// v0.1: evento de un módulo de escena; la web app lo reenvía al gateway como `flow_emitted`.
    Event {
        scene: String,
        event: String,
        data: Value,
    },
    Blank {
        id: String,
        max_ms: u32,
    },
    Unblank {
        id: String,
    },
    Objects {
        rev: u64,
        upsert: Vec<ObjectMsg>,
        remove: Vec<String>,
    },
    Status {
        state: State,
        /// `null` hasta que termina el primer ciclo.
        last_cycle_ms: Option<f64>,
        stages_ms: BTreeMap<String, f64>,
        /// Lo mismo que `welcome.models`: así la web app ve en cada ciclo si hay SAM o solo cascada.
        models: Models,
    },
    LookFrame {
        id: Value,
        jpeg_b64: String,
        t_ns: u64,
        w: u32,
        h: u32,
    },
    NeedFrame {
        id: String,
    },
    Error {
        code: String,
        message: String,
        /// Solo cuando el error responde a un pedido con `id` (p. ej. un `look` que no se pudo servir).
        #[serde(skip_serializing_if = "Option::is_none")]
        id: Option<Value>,
    },
}

impl Out {
    pub fn error(code: &str, message: impl Into<String>) -> Out {
        Out::Error { code: code.into(), message: message.into(), id: None }
    }

    pub fn json(&self) -> String {
        serde_json::to_string(self).expect("los mensajes del motor siempre serializan")
    }
}

/// `id` tal como lo mandó la otra punta, llevado a texto para comparar (`"b-3"` y `3` son ids válidos).
pub fn id_text(id: &Value) -> String {
    match id {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn entrada_tolerante() {
        let h: In =
            serde_json::from_value(json!({"type": "hello", "v": 0, "token": "t", "session_id": "s", "app": {"n": 1}}))
                .unwrap();
        assert!(matches!(h, In::Hello { v: Some(v), .. } if v == 0.0));
        // id numérico, t_ns con decimales (performance.now()) y campos de más
        let b: In = serde_json::from_value(json!({"type": "blanked", "id": 7, "t_ns": 123.5, "extra": true})).unwrap();
        assert!(matches!(b, In::Blanked { ref id, .. } if id_text(id) == "7"));
        assert!(matches!(serde_json::from_value::<In>(json!({"type": "perceive"})).unwrap(), In::Perceive {}));
        assert!(matches!(serde_json::from_value::<In>(json!({"type": "reset", "x": 1})).unwrap(), In::Reset {}));
        assert!(serde_json::from_value::<In>(json!({"type": "otra_cosa"})).is_err());
        let c: In = serde_json::from_value(json!({"type": "calib", "camera_px": [1280, 720],
            "matrix_px_to_mm": [1, 0, 0, 0, 1, 0, 0, 0, 1], "lens": null, "surface_mm": [800, 500]}))
        .unwrap();
        assert!(matches!(c, In::Calib { lens: None, camera_px: [w, _], .. } if w == 1280.0));
    }

    #[test]
    fn salida_con_la_forma_del_protocolo() {
        let w = Out::Welcome {
            v: 0,
            motor: "pcx-motor/0.1.0".into(),
            camera: None,
            models: Models::default(),
            needs_frames: true,
            scenes: vec!["lab".into()],
            cameras: vec![CameraRole { id: "c1".into(), role: "lateral".into(), name: "C920".into() }],
        };
        assert_eq!(
            serde_json::to_value(&w).unwrap(),
            json!({"type": "welcome", "v": 0, "motor": "pcx-motor/0.1.0", "camera": null,
                   "models": {"sam": false, "hands": false}, "needs_frames": true, "scenes": ["lab"],
                   "cameras": [{"id": "c1", "role": "lateral", "name": "C920"}]})
        );
        let e = Out::Event { scene: "lab".into(), event: "nivel".into(), data: json!({"ml": 42.5}) };
        assert_eq!(
            serde_json::to_value(&e).unwrap(),
            json!({"type": "event", "scene": "lab", "event": "nivel", "data": {"ml": 42.5}})
        );
        let s: In = serde_json::from_value(json!({"type": "scene", "name": null})).unwrap();
        assert!(matches!(s, In::Scene { name: None }));
        let models = Models { sam: false, hands: false, note: "sin SAM: solo la cascada".into() };
        let s = Out::Status { state: State::Capturing, last_cycle_ms: None, stages_ms: BTreeMap::new(), models };
        assert_eq!(
            serde_json::to_value(&s).unwrap(),
            json!({"type": "status", "state": "capturing", "last_cycle_ms": null, "stages_ms": {},
                   "models": {"sam": false, "hands": false, "note": "sin SAM: solo la cascada"}})
        );
        assert_eq!(
            serde_json::to_value(Out::error("x", "y")).unwrap(),
            json!({"type": "error", "code": "x", "message": "y"})
        );
        let o = Out::Objects { rev: 2, upsert: vec![], remove: vec!["objeto-1".into()] };
        assert_eq!(
            serde_json::to_value(&o).unwrap(),
            json!({"type": "objects", "rev": 2, "upsert": [], "remove": ["objeto-1"]})
        );
    }
}
