#[derive(Clone, Debug)]
pub struct Lens {
    pub f: f64,
    pub cx: f64,
    pub cy: f64,
    pub k1: f64,
    pub k2: f64,
}
#[derive(Clone, Debug)]
pub struct CalibIn {
    pub camera_px: (u32, u32),
    pub matrix_px_to_mm: [f64; 9],
    pub lens: Option<Lens>,
    pub surface_mm: (f64, f64),
}
#[derive(Clone, Debug)]
pub struct ObjectOut {
    pub id: String,
    pub label: String,
    pub polygon_mm: Vec<[f64; 2]>,
    pub center_mm: [f64; 2],
    pub confidence: f32,
}
#[derive(Clone, Debug, Default)]
pub struct PerceptionOut {
    pub upsert: Vec<ObjectOut>,
    pub remove: Vec<String>,
    pub stages_ms: Vec<(String, f64)>,
}
pub trait Perceptor: Send {
    fn on_clean(&mut self, frame: &image::RgbImage, calib: &CalibIn, t_ns: u64) -> Result<PerceptionOut, String>;
    fn reset(&mut self);
}
