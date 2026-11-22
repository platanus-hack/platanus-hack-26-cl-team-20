//! Tipos de salida. Los del contrato (`HandState`, `Handedness`, `Touch`, `TouchPhase`, `Trigger`, `Event`,
//! `FrameMeta`, `HandsGone`) vienen de `pcx-contracts` (v2.1); aquí solo quedan los internos del crate
//! (`TouchInfo`, `HandsEvent`, con px de cámara) y su conversión al contrato.
use serde::{Deserialize, Serialize};

pub use pcx_contracts::{Event, FrameMeta, HandState, Handedness, HandsGone, PointMM, PointPX, Touch, TouchPhase};

/// Punto `[x, y]` (px o mm según el campo).
pub type Point2 = [f64; 2];

/// `Touch.trigger` del contrato. El «apoyo» (palma apoyada, `touch.py`) se emite como `rest` (v2.1).
pub type TouchTrigger = pcx_contracts::Trigger;

/// Evento del contrato (alias del de `pcx-contracts`).
pub type ContractEvent = Event;

fn mm(p: Point2) -> PointMM {
    PointMM(p[0], p[1])
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
            point_mm: mm(self.point_mm?),
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
    /// Fin de episodio: hubo manos y lleva `calm_s` sin manos → foto limpia de la mesa (`hands_gone`, v2.1).
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

    /// Evento del contrato (`None` si falta `point_mm`, es decir, sin homografía cámara→mesa).
    pub fn to_contract(&self) -> Option<Event> {
        match self {
            HandsEvent::TouchDown { touch, .. } => Some(Event::TouchDown { touch: touch.to_contract()? }),
            HandsEvent::TouchMove { touch, .. } => Some(Event::TouchMove { touch: touch.to_contract()? }),
            HandsEvent::TouchUp { touch, .. } => Some(Event::TouchUp { touch: touch.to_contract()? }),
            HandsEvent::Dwell { point_mm, progress, .. } => {
                Some(Event::Dwell { point_mm: mm((*point_mm)?), progress: *progress, widget_id: None, object_id: None })
            }
            HandsEvent::HandsGone { episode_start_ns, last_hand_ns, t_capture_ns } => {
                Some(Event::HandsGone(HandsGone {
                    t_capture_ns: *t_capture_ns,
                    episode_start_ns: Some(*episode_start_ns),
                    last_hand_ns: Some(*last_hand_ns),
                }))
            }
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
            landmarks_px: vec![PointPX(1.0, 2.0)],
            tip_mm: None,
            pinch: false,
            t_capture_ns: 5,
        };
        let v = serde_json::to_value(&h).unwrap();
        assert_eq!(v["handedness"], "right");
        assert_eq!(v["landmarks_px"][0], serde_json::json!([1.0, 2.0]));
        assert!(v["tip_mm"].is_null());
        let info = TouchInfo {
            id: "t1".into(),
            hand_id: "h1".into(),
            phase: TouchPhase::Down,
            trigger: TouchTrigger::Rest,
            point_px: [10.0, 20.0],
            point_mm: Some([1.0, 2.0]),
            reason: String::new(),
        };
        let e = HandsEvent::TouchDown { touch: info, t_capture_ns: 7 }.to_contract().unwrap();
        let v = serde_json::to_value(&e).unwrap();
        assert_eq!(v["type"], "touch_down");
        assert_eq!(v["touch"]["phase"], "down");
        assert_eq!(v["touch"]["trigger"], "rest");
        assert_eq!(v["touch"]["point_mm"], serde_json::json!([1.0, 2.0]));
        let m: FrameMeta =
            serde_json::from_str(r#"{"type":"frame_meta","camera_id":"c","seq":1,"t_capture_ns":2,"w":640,"h":480}"#)
                .unwrap();
        assert_eq!(m.w, 640);
        let g = HandsEvent::HandsGone { episode_start_ns: 1, last_hand_ns: 2, t_capture_ns: 3 };
        assert_eq!(
            serde_json::to_value(g.to_contract().unwrap()).unwrap(),
            serde_json::json!({"type": "hands_gone", "t_capture_ns": 3, "episode_start_ns": 1, "last_hand_ns": 2})
        );
    }
}
