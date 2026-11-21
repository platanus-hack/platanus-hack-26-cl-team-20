//! Tipos de salida compatibles con `contracts/schema/` (v2): `HandState`, `Touch`, `TouchDown/Move/Up`, `Dwell`,
//! `FrameMeta`. `HandsGone` NO está en contracts v2: es propio de este crate (propuesta para v3).
use serde::{Deserialize, Serialize};

/// Punto `[x, y]` (px o mm según el campo).
pub type Point2 = [f64; 2];

/// `world.schema.json#/$defs/HandState`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct HandState {
    pub id: String,
    pub camera_id: String,
    #[serde(default)]
    pub handedness: Handedness,
    /// 21 landmarks en px de cámara (orden MediaPipe).
    #[serde(default)]
    pub landmarks_px: Vec<Point2>,
    /// Punta del índice en `table_mm` (solo si hay homografía cámara→mesa).
    #[serde(default)]
    pub tip_mm: Option<Point2>,
    #[serde(default)]
    pub pinch: bool,
    pub t_capture_ns: i64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Handedness {
    Left,
    Right,
    #[default]
    Unknown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TouchPhase {
    Down,
    Move,
    Up,
}

/// `Touch.trigger` del contrato. El «apoyo» (palma apoyada, `touch.py`) no existe en v2: se emite como `shadow`
/// (contacto físico inferido por visión). Propuesta: agregar `rest` al contrato.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TouchTrigger {
    Api,
    #[default]
    Dwell,
    Pinch,
    Shadow,
}

/// `event.schema.json#/$defs/Touch`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Touch {
    pub id: String,
    pub phase: TouchPhase,
    pub point_mm: Point2,
    #[serde(default)]
    pub widget_id: Option<String>,
    #[serde(default)]
    pub object_id: Option<String>,
    #[serde(default)]
    pub trigger: TouchTrigger,
}

/// Eventos del contrato que produce este crate (unión por `type`, como `Event` en `event.schema.json`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ContractEvent {
    TouchDown {
        touch: Touch,
    },
    TouchMove {
        touch: Touch,
    },
    TouchUp {
        touch: Touch,
    },
    Dwell {
        point_mm: Point2,
        progress: f64,
        #[serde(default)]
        widget_id: Option<String>,
        #[serde(default)]
        object_id: Option<String>,
    },
}

/// `event.schema.json#/$defs/FrameMeta`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename = "frame_meta")]
pub struct FrameMeta {
    pub camera_id: String,
    pub seq: i64,
    pub t_capture_ns: i64,
    pub w: i64,
    pub h: i64,
}

/// Datos del toque en el marco de la cámara y (si hay homografía) de la mesa.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TouchInfo {
    pub id: String,
    pub hand_id: String,
    pub phase: TouchPhase,
    pub trigger: TouchTrigger,
    pub point_px: Point2,
    pub point_mm: Option<Point2>,
    /// Motivo del `up` («hand_lost», «moved», «released»); vacío en down/move.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub reason: String,
}

impl TouchInfo {
    /// `Touch` del contrato; `None` si no hay `point_mm` (sin homografía cámara→mesa).
    pub fn to_contract(&self) -> Option<Touch> {
        Some(Touch {
            id: self.id.clone(),
            phase: self.phase,
            point_mm: self.point_mm?,
            widget_id: None,
            object_id: None,
            trigger: self.trigger,
        })
    }
}

/// Lo que emite el nodo de manos. Cada evento lleva la hora de captura del cuadro que lo originó.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum HandsEvent {
    TouchDown {
        touch: TouchInfo,
        t_capture_ns: i64,
    },
    TouchMove {
        touch: TouchInfo,
        t_capture_ns: i64,
    },
    TouchUp {
        touch: TouchInfo,
        t_capture_ns: i64,
    },
    /// Dedo quieto: `progress` sube de 0 a 1; 1 confirma (y abre un toque `trigger: dwell`).
    Dwell {
        hand_id: String,
        point_px: Point2,
        point_mm: Option<Point2>,
        progress: f64,
        t_capture_ns: i64,
    },
    /// Fin de episodio: hubo manos y lleva `calm_s` sin manos → foto limpia de la mesa. No está en contracts v2.
    HandsGone {
        episode_start_ns: i64,
        last_hand_ns: i64,
        t_capture_ns: i64,
    },
}

impl HandsEvent {
    pub fn t_capture_ns(&self) -> i64 {
        match self {
            HandsEvent::TouchDown { t_capture_ns, .. }
            | HandsEvent::TouchMove { t_capture_ns, .. }
            | HandsEvent::TouchUp { t_capture_ns, .. }
            | HandsEvent::Dwell { t_capture_ns, .. }
            | HandsEvent::HandsGone { t_capture_ns, .. } => *t_capture_ns,
        }
    }

    /// Evento del contrato v2 (`None` para `HandsGone` o si falta `point_mm`).
    pub fn to_contract(&self) -> Option<ContractEvent> {
        match self {
            HandsEvent::TouchDown { touch, .. } => Some(ContractEvent::TouchDown {
                touch: touch.to_contract()?,
            }),
            HandsEvent::TouchMove { touch, .. } => Some(ContractEvent::TouchMove {
                touch: touch.to_contract()?,
            }),
            HandsEvent::TouchUp { touch, .. } => Some(ContractEvent::TouchUp {
                touch: touch.to_contract()?,
            }),
            HandsEvent::Dwell {
                point_mm, progress, ..
            } => Some(ContractEvent::Dwell {
                point_mm: (*point_mm)?,
                progress: *progress,
                widget_id: None,
                object_id: None,
            }),
            HandsEvent::HandsGone { .. } => None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn serde_matches_contract_shape() {
        let h = HandState {
            id: "h1".into(),
            camera_id: "cam0".into(),
            handedness: Handedness::Right,
            landmarks_px: vec![[1.0, 2.0]],
            tip_mm: None,
            pinch: false,
            t_capture_ns: 5,
        };
        let v = serde_json::to_value(&h).unwrap();
        assert_eq!(v["handedness"], "right");
        assert!(v["tip_mm"].is_null());
        let e = ContractEvent::TouchDown {
            touch: Touch {
                id: "t1".into(),
                phase: TouchPhase::Down,
                point_mm: [1.0, 2.0],
                widget_id: None,
                object_id: None,
                trigger: TouchTrigger::Pinch,
            },
        };
        let v = serde_json::to_value(&e).unwrap();
        assert_eq!(v["type"], "touch_down");
        assert_eq!(v["touch"]["phase"], "down");
        assert_eq!(v["touch"]["trigger"], "pinch");
        let m: FrameMeta = serde_json::from_str(
            r#"{"type":"frame_meta","camera_id":"c","seq":1,"t_capture_ns":2,"w":640,"h":480}"#,
        )
        .unwrap();
        assert_eq!(m.w, 640);
        assert_eq!(serde_json::to_value(&m).unwrap()["type"], "frame_meta");
    }
}
