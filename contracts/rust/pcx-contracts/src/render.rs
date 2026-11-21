//! Protocolo core ↔ renderer (evolución de PROTOCOLO-ESCENA v0; ver `contracts/README.md`).

use serde::{Deserialize, Serialize};

use crate::geometry::{Surface, Transform};
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
    /// Legado v0: igual a la `matrix` de `table_mm → proj:*`.
    #[serde(default)]
    pub homography: Option<Vec<f64>>,
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

/// core → renderer.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ToRenderer {
    Scene(SceneSnapshot),
    SceneDiff(SceneDiff),
}

/// renderer → core.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum FromRenderer {
    Presented(Presented),
    Stats(Stats),
}
