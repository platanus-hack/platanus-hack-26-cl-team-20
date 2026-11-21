//! Tipos serde compatibles con `contracts/schema` (`ObjectState`, `ObjectAppeared/Moved/Removed`).
//!
//! Los campos del contrato v2 van tal cual. Las extensiones del seguidor (`appearance`, `aff`, `hidden`,
//! `level`, `seq`, `prompt`) son opcionales y se omiten si faltan: el schema no prohíbe propiedades extra y
//! `msgspec` las ignora al decodificar, así que el JSON sigue validando contra `pcx.schema.json`.

use crate::geom::Affine;
use pcx_contracts::{PointMM, RectMM};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ObjectState {
    pub id: String,
    pub label: String,
    pub confidence: f64,
    pub polygon_mm: Vec<PointMM>,
    pub center_mm: PointMM,
    #[serde(default)]
    pub bbox_mm: Option<RectMM>,
    pub since_ns: i64,
    pub last_seen_ns: i64,
    // ---- extensiones (fuera del contrato v2) ----
    /// Ruta a un PNG RGBA con el recorte del objeto en la última foto limpia (fuera de la máscara transparente).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub appearance: Option<String>,
    /// Afín 2×3 en `table_mm`: pose del registro → pose actual (como `aff` en `poses.json` de Daniel, en mm).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub aff: Option<Affine>,
    /// Prompt en inglés para el detector (lo da el LLM en el registro).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub prompt: Option<String>,
    /// Sube en cada cambio publicado (como `seq` de `poses.json`).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub seq: Option<u64>,
}

/// Eventos de objetos. `type` en snake_case, como `contracts/schema/event.schema.json`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ObjectEvent {
    ObjectAppeared {
        object: ObjectState,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        level: Option<String>,
    },
    ObjectMoved {
        object_id: String,
        center_mm: PointMM,
        #[serde(default)]
        polygon_mm: Option<Vec<PointMM>>,
        // ---- extensiones ----
        /// Afín acumulada registro → ahora, en mm (2×3).
        #[serde(default, skip_serializing_if = "Option::is_none")]
        aff: Option<Affine>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        confidence: Option<f64>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        appearance: Option<String>,
        /// Nivel de la cascada que lo resolvió (`"N2 rigido (34 pts)"`, `"N3 dino+sam"`...).
        #[serde(default, skip_serializing_if = "Option::is_none")]
        level: Option<String>,
    },
    ObjectRemoved {
        object_id: String,
        #[serde(default)]
        label: Option<String>,
    },
}

impl ObjectEvent {
    pub fn object_id(&self) -> &str {
        match self {
            ObjectEvent::ObjectAppeared { object, .. } => &object.id,
            ObjectEvent::ObjectMoved { object_id, .. } | ObjectEvent::ObjectRemoved { object_id, .. } => object_id,
        }
    }

    /// Evento del contrato (`pcx_contracts::Event`), sin las extensiones del seguidor.
    pub fn to_contract(&self) -> pcx_contracts::Event {
        match self {
            ObjectEvent::ObjectAppeared { object, .. } => pcx_contracts::Event::ObjectAppeared { object: object.to_contract() },
            ObjectEvent::ObjectMoved { object_id, center_mm, polygon_mm, .. } => pcx_contracts::Event::ObjectMoved {
                object_id: object_id.clone(),
                center_mm: *center_mm,
                polygon_mm: polygon_mm.clone(),
            },
            ObjectEvent::ObjectRemoved { object_id, label } => {
                pcx_contracts::Event::ObjectRemoved { object_id: object_id.clone(), label: label.clone() }
            }
        }
    }
}

impl ObjectState {
    /// `ObjectState` del contrato, sin las extensiones (`appearance`, `aff`, `prompt`, `seq`).
    pub fn to_contract(&self) -> pcx_contracts::ObjectState {
        pcx_contracts::ObjectState {
            id: self.id.clone(),
            label: self.label.clone(),
            confidence: self.confidence,
            polygon_mm: self.polygon_mm.clone(),
            center_mm: self.center_mm,
            bbox_mm: self.bbox_mm,
            since_ns: self.since_ns,
            last_seen_ns: self.last_seen_ns,
        }
    }
}

impl From<&ObjectState> for pcx_contracts::ObjectState {
    fn from(o: &ObjectState) -> Self {
        o.to_contract()
    }
}

impl From<&ObjectEvent> for pcx_contracts::Event {
    fn from(e: &ObjectEvent) -> Self {
        e.to_contract()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn serializes_with_contract_fields() {
        let o = ObjectState {
            id: "m01".into(),
            label: "taza".into(),
            confidence: 0.9,
            polygon_mm: vec![PointMM(0.0, 0.0), PointMM(1.0, 0.0), PointMM(1.0, 1.0)],
            center_mm: PointMM(0.5, 0.5),
            bbox_mm: None,
            since_ns: 1,
            last_seen_ns: 2,
            appearance: None,
            aff: None,
            prompt: None,
            seq: None,
        };
        let v = serde_json::to_value(ObjectEvent::ObjectAppeared { object: o.clone(), level: None }).unwrap();
        assert_eq!(v["type"], "object_appeared");
        for k in ["id", "label", "confidence", "polygon_mm", "center_mm", "bbox_mm", "since_ns", "last_seen_ns"] {
            assert!(v["object"].get(k).is_some(), "falta {k}");
        }
        assert_eq!(v["object"]["polygon_mm"][1], serde_json::json!([1.0, 0.0]));
        let m = serde_json::to_value(ObjectEvent::ObjectMoved {
            object_id: "m01".into(),
            center_mm: PointMM(3.0, 4.0),
            polygon_mm: None,
            aff: Some(crate::geom::IDENTITY),
            confidence: None,
            appearance: None,
            level: None,
        })
        .unwrap();
        assert_eq!(m["type"], "object_moved");
        assert_eq!(m["polygon_mm"], serde_json::Value::Null);
        assert_eq!(m["aff"], serde_json::json!([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]));
        let r = serde_json::to_value(ObjectEvent::ObjectRemoved { object_id: "m01".into(), label: Some("taza".into()) }).unwrap();
        assert_eq!(r, serde_json::json!({"type": "object_removed", "object_id": "m01", "label": "taza"}));
    }

    #[test]
    fn to_contract_equals_json_without_extensions() {
        let o = ObjectState {
            id: "m01".into(),
            label: "taza".into(),
            confidence: 0.9,
            polygon_mm: vec![PointMM(0.0, 0.0), PointMM(1.0, 0.0), PointMM(1.0, 1.0)],
            center_mm: PointMM(0.5, 0.5),
            bbox_mm: Some(RectMM(0.0, 0.0, 1.0, 1.0)),
            since_ns: 1,
            last_seen_ns: 2,
            appearance: Some("/tmp/m01.png".into()),
            aff: Some(crate::geom::IDENTITY),
            prompt: Some("mug".into()),
            seq: Some(3),
        };
        let evs = [
            ObjectEvent::ObjectAppeared { object: o.clone(), level: Some("N5".into()) },
            ObjectEvent::ObjectMoved {
                object_id: "m01".into(),
                center_mm: PointMM(3.0, 4.0),
                polygon_mm: Some(o.polygon_mm.clone()),
                aff: Some(crate::geom::IDENTITY),
                confidence: Some(0.8),
                appearance: Some("/tmp/m01.png".into()),
                level: Some("N2".into()),
            },
            ObjectEvent::ObjectRemoved { object_id: "m01".into(), label: Some("taza".into()) },
        ];
        let ext = ["appearance", "aff", "prompt", "seq"];
        for e in &evs {
            // JSON local sin extensiones == JSON del evento del contrato
            let mut local = serde_json::to_value(e).unwrap();
            let m = local.as_object_mut().unwrap();
            for k in ["aff", "confidence", "appearance", "level"] {
                m.remove(k);
            }
            if let Some(obj) = m.get_mut("object").and_then(|v| v.as_object_mut()) {
                for k in ext {
                    obj.remove(k);
                }
            }
            let contract = serde_json::to_value(pcx_contracts::Event::from(e)).unwrap();
            assert_eq!(local, contract);
            // y el contrato lo vuelve a leer
            let back: pcx_contracts::Event = serde_json::from_value(contract).unwrap();
            assert_eq!(back, e.to_contract());
        }
    }
}
