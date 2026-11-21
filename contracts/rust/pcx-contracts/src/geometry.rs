//! Marco canónico `table_mm`: x a la derecha, y hacia abajo, origen arriba a la izquierda, en mm.

use serde::{Deserialize, Serialize};

pub const TABLE_MM: &str = "table_mm";

/// Punto en mm. En el cable: `[x, y]`.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct PointMM(pub f64, pub f64);

/// Punto en px de una imagen. En el cable: `[x, y]`.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct PointPX(pub f64, pub f64);

/// Rectángulo en mm. En el cable: `[x, y, w, h]` (esquina superior izquierda + tamaño).
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct RectMM(pub f64, pub f64, pub f64, pub f64);

impl PointMM {
    pub fn x(&self) -> f64 {
        self.0
    }
    pub fn y(&self) -> f64 {
        self.1
    }
}

impl RectMM {
    pub fn x(&self) -> f64 {
        self.0
    }
    pub fn y(&self) -> f64 {
        self.1
    }
    pub fn w(&self) -> f64 {
        self.2
    }
    pub fn h(&self) -> f64 {
        self.3
    }
}

pub type PolygonMM = Vec<PointMM>;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Surface {
    pub width_mm: f64,
    pub height_mm: f64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TransformKind {
    #[default]
    Homography,
    Lut,
}

/// Transformación con nombre y versión. `matrix`: 9 floats en fila mayor (`from_frame` → `to_frame`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Transform {
    pub from_frame: String,
    pub to_frame: String,
    #[serde(default)]
    pub kind: TransformKind,
    #[serde(default)]
    pub matrix: Option<Vec<f64>>,
    #[serde(default)]
    pub lut_ref: Option<String>,
    #[serde(default = "one")]
    pub version: i64,
    #[serde(default)]
    pub residual_mm: Option<f64>,
}

fn one() -> i64 {
    1
}

impl Transform {
    /// Homografía `table_mm → proj:*` (la que usa el renderer), si existe.
    pub fn projector_matrix(transforms: &[Transform]) -> Option<[f64; 9]> {
        transforms
            .iter()
            .find(|t| t.from_frame == TABLE_MM && t.to_frame.starts_with("proj:"))
            .and_then(|t| t.matrix.as_ref())
            .and_then(|m| <[f64; 9]>::try_from(m.as_slice()).ok())
    }
}
