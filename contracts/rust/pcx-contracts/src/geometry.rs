//! Marco canónico `table_mm`: x a la derecha, y hacia abajo, origen arriba a la izquierda, en mm.
//! Puntos y rectángulos son arrays de largo exacto (serde rechaza `[x, y, z]`).

use serde::{Deserialize, Serialize};

use crate::validate as v;

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

/// Polígono cerrado (≥ 3 vértices; se valida donde se deserializa).
pub type PolygonMM = Vec<PointMM>;
/// Homografía 3x3 en fila mayor (exactamente 9 números).
pub type Matrix9 = [f64; 9];

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Surface {
    pub width_mm: f64,
    pub height_mm: f64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
pub enum LutFormat {
    /// float32, forma (height, width, 2), C-order.
    #[default]
    #[serde(rename = "npy_f32_hw2")]
    NpyF32Hw2,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
pub enum LutUnits {
    /// Valores (x, y) en px del marco de salida (`to_frame`).
    #[default]
    #[serde(rename = "output_px")]
    OutputPx,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
pub enum LutSampling {
    /// `lut[v][u]` = muestra con centro en `(u * grid_step, v * grid_step)` del marco de entrada.
    #[default]
    #[serde(rename = "integer_centers")]
    IntegerCenters,
}

/// Descriptor de LUT (ver `geometry.LutDescriptor` en Python para la convención completa).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LutDescriptor {
    #[serde(default)]
    pub format: LutFormat,
    #[serde(deserialize_with = "v::positive_u32")]
    pub width: u32,
    #[serde(deserialize_with = "v::positive_u32")]
    pub height: u32,
    #[serde(default)]
    pub units: LutUnits,
    #[serde(default)]
    pub sampling: LutSampling,
    #[serde(default = "one_f", deserialize_with = "v::positive_f64")]
    pub grid_step: f64,
    pub path: String,
    #[serde(deserialize_with = "v::sha256")]
    pub sha256: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct HomographyTransform {
    pub from_frame: String,
    pub to_frame: String,
    #[serde(default = "one")]
    pub version: i64,
    #[serde(default)]
    pub residual_mm: Option<f64>,
    pub matrix: Matrix9,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LutTransform {
    pub from_frame: String,
    pub to_frame: String,
    #[serde(default = "one")]
    pub version: i64,
    #[serde(default)]
    pub residual_mm: Option<f64>,
    pub lut: LutDescriptor,
    /// Homografía aproximada opcional para consumidores sin LUT.
    #[serde(default)]
    pub approx_matrix: Option<Matrix9>,
}

/// Transformación con nombre y versión entre dos marcos (unión discriminada por `kind`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Transform {
    Homography(HomographyTransform),
    Lut(LutTransform),
}

fn one() -> i64 {
    1
}
fn one_f() -> f64 {
    1.0
}

impl Transform {
    pub fn from_frame(&self) -> &str {
        match self {
            Transform::Homography(t) => &t.from_frame,
            Transform::Lut(t) => &t.from_frame,
        }
    }
    pub fn to_frame(&self) -> &str {
        match self {
            Transform::Homography(t) => &t.to_frame,
            Transform::Lut(t) => &t.to_frame,
        }
    }
    pub fn version(&self) -> i64 {
        match self {
            Transform::Homography(t) => t.version,
            Transform::Lut(t) => t.version,
        }
    }

    /// Homografía para renderers sin LUT (igual que `projector_matrix` en Python): la seleccionada si es homografía;
    /// si es LUT, su `approx_matrix`; si esa LUT no trae aproximación, la mejor homografía (fallback).
    pub fn projector_matrix(transforms: &[Transform], projector_id: &str) -> Option<Matrix9> {
        match select_projector_transform(transforms, projector_id)? {
            (_, Transform::Homography(h)) => Some(h.matrix),
            (_, Transform::Lut(l)) => l.approx_matrix.or_else(|| {
                best_of(transforms, projector_id, |t| matches!(t, Transform::Homography(_))).and_then(
                    |(_, t)| match t {
                        Transform::Homography(h) => Some(h.matrix),
                        Transform::Lut(_) => None,
                    },
                )
            }),
        }
    }
}

fn best_of<'a>(
    transforms: &'a [Transform],
    projector_id: &str,
    pred: impl Fn(&Transform) -> bool,
) -> Option<(usize, &'a Transform)> {
    let target = format!("proj:{projector_id}");
    let mut best: Option<(usize, &Transform)> = None;
    for (i, t) in transforms.iter().enumerate() {
        if t.from_frame() == TABLE_MM
            && t.to_frame() == target
            && pred(t)
            && best.is_none_or(|(_, b)| t.version() > b.version())
        {
            best = Some((i, t));
        }
    }
    best
}

/// Transformación `table_mm → proj:<projector_id>` que usa el renderer: LUT > homografía; dentro de cada tipo la
/// de mayor `version` (empate: la primera). Devuelve `(índice, transformación)`. Misma regla que Python;
/// casos compartidos en `fixtures/selection_cases.json`.
pub fn select_projector_transform<'a>(
    transforms: &'a [Transform],
    projector_id: &str,
) -> Option<(usize, &'a Transform)> {
    best_of(transforms, projector_id, |t| matches!(t, Transform::Lut(_)))
        .or_else(|| best_of(transforms, projector_id, |t| matches!(t, Transform::Homography(_))))
}
