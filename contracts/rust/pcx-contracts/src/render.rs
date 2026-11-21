//! Protocolo core ↔ renderer (evolución de PROTOCOLO-ESCENA v0; ver `contracts/README.md`).

use serde::{Deserialize, Serialize};

use crate::geometry::{Matrix9, Surface, Transform};
use crate::validate as v;
use crate::widgets::Widget;

/// Escena completa (`type: "scene"`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SceneSnapshot {
    pub rev: i64,
    #[serde(default)]
    pub t_send_ms: Option<f64>,
    pub surface: Surface,
    #[serde(default)]
    pub transforms: Vec<Transform>,
    #[serde(default)]
    pub widgets: Vec<Widget>,
    /// Legado v0: igual a `Transform::projector_matrix(transforms, "main")`.
    #[serde(default)]
    pub homography: Option<Matrix9>,
}

/// Cambios sobre `base_rev` (`type: "scene_diff"`). Si `base_rev` no es la última aplicada, esperar un `scene`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SceneDiff {
    pub rev: i64,
    pub base_rev: i64,
    #[serde(default)]
    pub t_send_ms: Option<f64>,
    #[serde(default)]
    pub upsert: Vec<Widget>,
    #[serde(default)]
    pub remove: Vec<String>,
    /// Si viene, reemplaza la lista completa.
    #[serde(default)]
    pub transforms: Option<Vec<Transform>>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Presented {
    pub rev: i64,
    pub t_present_ms: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Stats {
    pub fps: f64,
    pub frame_ms_p50: f64,
    pub frame_ms_p95: f64,
    #[serde(default)]
    pub dropped: i64,
}

/// Salida negra durante `ms` desde el próximo vsync, encima de la escena (`type: "blank"`), para «fotos limpias».
/// Un `blank` nuevo durante uno activo lo reemplaza desde ese momento.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Blank {
    pub request_id: String,
    #[serde(deserialize_with = "v::blank_ms")]
    pub ms: u32,
    pub reason: String,
}

/// Intervalo realmente negro (`type: "blanked"`): `t_start_ms` = presentación del primer cuadro negro,
/// `t_end_ms` = presentación del primer cuadro con escena otra vez (ms de época).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Blanked {
    pub request_id: String,
    pub t_start_ms: f64,
    pub t_end_ms: f64,
}

/// core → renderer.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ToRenderer {
    Scene(SceneSnapshot),
    SceneDiff(SceneDiff),
    Blank(Blank),
}

/// renderer → core.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum FromRenderer {
    Presented(Presented),
    Stats(Stats),
    Blanked(Blanked),
}
