//! Inferencia con `ort`: detección de palma (192², anchors SSD, NMS) y 21 landmarks (224² sobre un ROI rotado).
//! Referencia exacta: `mp_palmdet.py` y `mp_handpose.py` del opencv_zoo.
use crate::frame::Frame;
use crate::geom::{self, Affine, Roi};
use crate::types::Point2;
use crate::HandsError;
use ort::session::Session;
use ort::value::Tensor;
use std::path::Path;

const PALM_N: usize = 192;
const LM_N: usize = 224;

/// Execution provider.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Backend {
    #[default]
    Cpu,
    /// CoreML (macOS, feature `coreml`); sin la feature cae a CPU.
    CoreMl,
}

#[derive(Debug, Clone, Copy)]
pub struct DetectorConfig {
    pub palm_score: f32,
    pub palm_nms_iou: f64,
    pub backend: Backend,
    pub intra_threads: usize,
}

impl Default for DetectorConfig {
    fn default() -> Self {
        Self { palm_score: 0.6, palm_nms_iou: 0.3, backend: Backend::Cpu, intra_threads: 2 }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct PalmDetection {
    /// `[x1, y1, x2, y2]` px.
    pub bbox: [f64; 4],
    /// 7 puntos: muñeca, nudillos índice/medio/anular/meñique, base del pulgar ×2.
    pub keypoints: [Point2; 7],
    pub score: f32,
}

impl PalmDetection {
    pub fn roi(&self) -> Roi {
        geom::roi_from_palm(self.bbox, &self.keypoints)
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct LandmarkResult {
    /// 21 × (x, y, z) en px de la imagen; z relativa a la muñeca, misma escala.
    pub landmarks: [[f64; 3]; 21],
    /// 21 × (x, y, z) en metros, marco de la mano.
    pub world: [[f64; 3]; 21],
    /// Presencia de mano (0..1).
    pub score: f32,
    /// 0 = izquierda, 1 = derecha (convención del zoo; imagen no espejada → al revés).
    pub handedness: f32,
}

impl LandmarkResult {
    pub fn xy(&self) -> [Point2; 21] {
        self.landmarks.map(|p| [p[0], p[1]])
    }
}

pub struct HandDetector {
    _env: ort::environment::Environment,
    palm: Session,
    lm: Session,
    anchors: Vec<[f32; 2]>,
    cfg: DetectorConfig,
    palm_buf: Vec<f32>,
    lm_buf: Vec<f32>,
}

fn ort_err<E: std::fmt::Display>(e: E) -> HandsError {
    HandsError::Ort(e.to_string())
}

/// `Environment` de ort del proceso (se crea una vez; ort no admite un segundo).
pub fn shared_env() -> Result<ort::environment::Environment, HandsError> {
    static ENV: std::sync::OnceLock<Result<ort::environment::Environment, String>> = std::sync::OnceLock::new();
    ENV.get_or_init(|| ort::init().with_name("pcx").build().map_err(|e| e.to_string())).clone().map_err(HandsError::Ort)
}

fn session(env: &ort::environment::Environment, path: &Path, cfg: &DetectorConfig) -> Result<Session, HandsError> {
    // `allow_spinning = 0`: los hilos de ORT no giran en vacío (con la máquina cargada, la inferencia se dispara).
    let mut b = Session::builder(env)
        .map_err(ort_err)?
        .with_intra_threads(cfg.intra_threads)
        .map_err(ort_err)?
        .with_intra_op_spinning(false)
        .map_err(ort_err)?;
    if cfg.backend == Backend::CoreMl {
        #[cfg(feature = "coreml")]
        {
            use ort::ep::CoreML;
            b = b.with_execution_providers([CoreML::default().build()]).map_err(ort_err)?;
        }
    }
    b.commit_from_file(path).map_err(ort_err)
}

/// Anchors SSD de MediaPipe para 192²: strides [8, 16, 16, 16], 2 anchors por capa, offset 0,5 → 2016.
pub fn palm_anchors() -> Vec<[f32; 2]> {
    let mut out = Vec::with_capacity(2016);
    for (grid, per_cell) in [(24usize, 2usize), (12, 6)] {
        for y in 0..grid {
            for x in 0..grid {
                let c = [(x as f32 + 0.5) / grid as f32, (y as f32 + 0.5) / grid as f32];
                out.extend(std::iter::repeat_n(c, per_cell));
            }
        }
    }
    out
}

impl HandDetector {
    pub fn new(palm_model: &Path, landmark_model: &Path, cfg: DetectorConfig) -> Result<Self, HandsError> {
        Self::with_env(shared_env()?, palm_model, landmark_model, cfg)
    }

    /// Con un `Environment` de ort ya creado (ort admite uno por proceso; úsalo si otro crate creó el suyo).
    pub fn with_env(
        env: ort::environment::Environment,
        palm_model: &Path,
        landmark_model: &Path,
        cfg: DetectorConfig,
    ) -> Result<Self, HandsError> {
        Ok(Self {
            palm: session(&env, palm_model, &cfg)?,
            lm: session(&env, landmark_model, &cfg)?,
            _env: env,
            anchors: palm_anchors(),
            cfg,
            palm_buf: vec![0.0; PALM_N * PALM_N * 3],
            lm_buf: vec![0.0; LM_N * LM_N * 3],
        })
    }

    /// Baja (o reutiliza de la caché) los modelos y crea el detector.
    pub fn from_cache(cfg: DetectorConfig) -> Result<Self, HandsError> {
        let (p, l) = crate::models::ensure_all()?;
        Self::new(&p, &l, cfg)
    }

    /// Palmas en la imagen completa (letterbox a 192² como `MPPalmDet._preprocess`).
    pub fn detect_palms(&mut self, frame: &Frame) -> Result<Vec<PalmDetection>, HandsError> {
        let (w, h) = (frame.width as f64, frame.height as f64);
        let ratio = (PALM_N as f64 / h).min(PALM_N as f64 / w);
        let (rw, rh) = ((w * ratio) as usize, (h * ratio) as usize);
        let (left, top) = ((PALM_N - rw) / 2, (PALM_N - rh) / 2);
        let pad_bias = [(left as f64 / ratio).trunc(), (top as f64 / ratio).trunc()];
        let (sx, sy) = (w / rw as f64, h / rh as f64);
        let map = Affine([sx, 0.0, (0.5 - left as f64) * sx - 0.5, 0.0, sy, (0.5 - top as f64) * sy - 0.5]);
        self.palm_buf.fill(0.0);
        geom::warp_to_tensor(
            frame,
            &map,
            PALM_N,
            PALM_N,
            1,
            true,
            Some([left, top, left + rw, top + rh]),
            &mut self.palm_buf,
        );
        let input = Tensor::from_array(([1usize, PALM_N, PALM_N, 3], self.palm_buf.clone())).map_err(ort_err)?;
        let outs = self.palm.run(ort::inputs!["input_1" => input]).map_err(ort_err)?;
        let (_, reg) = outs["Identity"].try_extract_tensor::<f32>().map_err(ort_err)?;
        let (_, raw) = outs["Identity_1"].try_extract_tensor::<f32>().map_err(ort_err)?;

        let scale = w.max(h);
        let n = PALM_N as f64;
        let mut cands: Vec<(f32, usize, [f64; 4])> = Vec::new();
        for (i, &r) in raw.iter().enumerate() {
            let s = 1.0 / (1.0 + (-r.clamp(-80.0, 80.0)).exp());
            if s < self.cfg.palm_score {
                continue;
            }
            let d = &reg[i * 18..i * 18 + 4];
            let a = self.anchors[i];
            let (cx, cy) = (d[0] as f64 / n + a[0] as f64, d[1] as f64 / n + a[1] as f64);
            let (bw, bh) = (d[2] as f64 / n, d[3] as f64 / n);
            let b = [
                (cx - bw / 2.0) * scale - pad_bias[0],
                (cy - bh / 2.0) * scale - pad_bias[1],
                (cx + bw / 2.0) * scale - pad_bias[0],
                (cy + bh / 2.0) * scale - pad_bias[1],
            ];
            cands.push((s, i, b));
        }
        cands.sort_by(|a, b| b.0.total_cmp(&a.0));
        let mut keep: Vec<(f32, usize, [f64; 4])> = Vec::new();
        for c in cands {
            if keep.iter().all(|k| geom::iou(k.2, c.2) <= self.cfg.palm_nms_iou) {
                keep.push(c);
            }
        }
        Ok(keep
            .into_iter()
            .map(|(score, i, bbox)| {
                let a = self.anchors[i];
                let kp = std::array::from_fn(|j| {
                    let d = &reg[i * 18 + 4 + j * 2..i * 18 + 6 + j * 2];
                    [
                        (d[0] as f64 / n + a[0] as f64) * scale - pad_bias[0],
                        (d[1] as f64 / n + a[1] as f64) * scale - pad_bias[1],
                    ]
                });
                PalmDetection { bbox, keypoints: kp, score }
            })
            .collect())
    }

    /// 21 landmarks dentro de `roi` (recorte rotado 224², negro fuera de la imagen).
    pub fn landmarks(&mut self, frame: &Frame, roi: &Roi) -> Result<LandmarkResult, HandsError> {
        let ss = (roi.size / LM_N as f64).ceil().clamp(1.0, 3.0) as usize;
        geom::warp_to_tensor(frame, &Affine::from_roi(roi, LM_N), LM_N, LM_N, ss, false, None, &mut self.lm_buf);
        let input = Tensor::from_array(([1usize, LM_N, LM_N, 3], self.lm_buf.clone())).map_err(ort_err)?;
        let outs = self.lm.run(ort::inputs!["input_1" => input]).map_err(ort_err)?;
        let (_, lm) = outs["Identity"].try_extract_tensor::<f32>().map_err(ort_err)?;
        let (_, conf) = outs["Identity_1"].try_extract_tensor::<f32>().map_err(ort_err)?;
        let (_, hand) = outs["Identity_2"].try_extract_tensor::<f32>().map_err(ort_err)?;
        let (_, world) = outs["Identity_3"].try_extract_tensor::<f32>().map_err(ort_err)?;
        let k = roi.size / LM_N as f64;
        let landmarks = std::array::from_fn(|i| {
            let p = roi.to_image([lm[i * 3] as f64, lm[i * 3 + 1] as f64], LM_N as f64);
            [p[0], p[1], lm[i * 3 + 2] as f64 * k]
        });
        let world = std::array::from_fn(|i| {
            let p = geom::rotate([world[i * 3] as f64, world[i * 3 + 1] as f64], roi.angle);
            [p[0], p[1], world[i * 3 + 2] as f64]
        });
        Ok(LandmarkResult { landmarks, world, score: conf[0], handedness: hand[0] })
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn anchors_match_zoo() {
        let a = super::palm_anchors();
        assert_eq!(a.len(), 2016);
        assert!((a[0][0] - 0.020_833_33).abs() < 1e-6);
        assert!((a[2][0] - 0.0625).abs() < 1e-6);
        assert!((a[1152][0] - 0.041_666_67).abs() < 1e-6);
        assert!((a[2015][1] - 0.958_333_3).abs() < 1e-6);
    }
}
