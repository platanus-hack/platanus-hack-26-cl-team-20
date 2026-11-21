//! Widgets proyectados (unión discriminada por `type`). Medidas en mm; colores `#RRGGBB` o `#RRGGBBAA`.
//! Hacia el renderer la geometría llega resuelta (mm absolutos); `ttl_s` y `on_press` los gestiona el core.

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use crate::geometry::{PointMM, PolygonMM, RectMM};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Trigger {
    Dwell,
    Pinch,
    Shadow,
    Api,
}

#[derive(Debug, Clone, PartialEq, Default, Serialize, Deserialize)]
pub struct Anchor {
    #[serde(default)]
    pub object_id: Option<String>,
    #[serde(default)]
    pub point_mm: Option<PointMM>,
    #[serde(default)]
    pub offset_mm: Option<PointMM>,
}

impl Anchor {
    /// Punto absoluto (point_mm + offset_mm), si hay `point_mm`.
    pub fn resolved_point(&self) -> Option<PointMM> {
        let p = self.point_mm?;
        let o = self.offset_mm.unwrap_or(PointMM(0.0, 0.0));
        Some(PointMM(p.0 + o.0, p.1 + o.1))
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct OnPress {
    #[serde(default)]
    pub value: Option<String>,
    #[serde(default = "default_triggers")]
    pub triggers: Vec<Trigger>,
    #[serde(default = "default_dwell_ms")]
    pub dwell_ms: i64,
}

fn default_triggers() -> Vec<Trigger> {
    vec![Trigger::Dwell, Trigger::Pinch, Trigger::Api]
}
fn default_dwell_ms() -> i64 {
    800
}

/// Campos comunes a todos los widgets.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct WidgetCommon {
    pub id: String,
    #[serde(default)]
    pub anchor: Option<Anchor>,
    #[serde(default)]
    pub z: i64,
    #[serde(default)]
    pub ttl_s: Option<f64>,
    #[serde(default)]
    pub on_press: Option<OnPress>,
}

macro_rules! default_fn {
    ($name:ident, $t:ty, $v:expr) => {
        fn $name() -> $t {
            $v
        }
    };
}
default_fn!(white, String, "#FFFFFF".into());
default_fn!(cyan, String, "#00E5FF".into());
default_fn!(yellow, String, "#FFD400".into());
default_fn!(green, String, "#00E676".into());
default_fn!(f12, f64, 12.0);
default_fn!(f4, f64, 4.0);
default_fn!(f5, f64, 5.0);
default_fn!(f1, f64, 1.0);

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Label {
    #[serde(flatten)]
    pub common: WidgetCommon,
    #[serde(default)]
    pub text: String,
    #[serde(default = "f12")]
    pub size_mm: f64,
    #[serde(default = "white")]
    pub color: String,
    #[serde(default)]
    pub bg: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Halo {
    #[serde(flatten)]
    pub common: WidgetCommon,
    #[serde(default)]
    pub polygon_mm: Option<PolygonMM>,
    #[serde(default = "cyan")]
    pub color: String,
    #[serde(default = "f4")]
    pub width_mm: f64,
    #[serde(default = "f1")]
    pub pulse_hz: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Arrow {
    #[serde(flatten)]
    pub common: WidgetCommon,
    #[serde(default)]
    pub from_mm: Option<PointMM>,
    #[serde(default)]
    pub to_mm: Option<PointMM>,
    #[serde(default = "yellow")]
    pub color: String,
    #[serde(default = "f5")]
    pub width_mm: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Panel {
    #[serde(flatten)]
    pub common: WidgetCommon,
    #[serde(default)]
    pub rect_mm: Option<RectMM>,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub body: String,
    #[serde(default = "white")]
    pub color: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Button {
    #[serde(flatten)]
    pub common: WidgetCommon,
    #[serde(default)]
    pub rect_mm: Option<RectMM>,
    #[serde(default)]
    pub label: String,
    #[serde(default)]
    pub progress: f64,
    #[serde(default = "green")]
    pub color: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Image {
    #[serde(flatten)]
    pub common: WidgetCommon,
    #[serde(default)]
    pub rect_mm: Option<RectMM>,
    pub src: String,
    #[serde(default = "f1")]
    pub opacity: f64,
}

/// Shader WGSL por nombre (catálogo del compositor) con parámetros libres.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Effect {
    #[serde(flatten)]
    pub common: WidgetCommon,
    pub shader: String,
    #[serde(default)]
    pub params: Map<String, Value>,
    #[serde(default)]
    pub rect_mm: Option<RectMM>,
    #[serde(default)]
    pub polygon_mm: Option<PolygonMM>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Widget {
    Label(Label),
    Halo(Halo),
    Arrow(Arrow),
    Panel(Panel),
    Button(Button),
    Image(Image),
    Effect(Effect),
}

impl Widget {
    pub fn common(&self) -> &WidgetCommon {
        match self {
            Widget::Label(w) => &w.common,
            Widget::Halo(w) => &w.common,
            Widget::Arrow(w) => &w.common,
            Widget::Panel(w) => &w.common,
            Widget::Button(w) => &w.common,
            Widget::Image(w) => &w.common,
            Widget::Effect(w) => &w.common,
        }
    }
    pub fn id(&self) -> &str {
        &self.common().id
    }
}
