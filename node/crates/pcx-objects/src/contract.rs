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
}
