//! Orquestación como máquina de pasos, independiente del hardware (`PatternSink` + `FrameGrab`).

use std::path::PathBuf;

use pcx_contracts::{HomographyTransform, Transform, TABLE_MM};
use serde::{Deserialize, Serialize};

use crate::graycode::{self, Decoded, GraySpec};
use crate::homography::{from_row_major, Mat3, Pt};
use crate::image::{ErrStats, GrayImage};
use crate::io::{CalibError, Frame, FrameGrab, PatternSink};
use crate::lut::{BlockGrid, DenseLut, Poly2};
use crate::sync::StreamReport;

mod steps;
pub use steps::fill_quad;

/// Modo del comando `calibrate` del contrato (`quick | precise | verify`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Mode {
    Quick,
    Precise,
    Verify,
}

/// Cómo se obtiene la escala/marco `table_mm`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MetricRef {
    /// Marcadores `pcx6` impresos sobre la mesa (≥ 1; repartidos = mejor, uno solo se extrapola).
    PrintedMarkers(Vec<PrintedMarker>),
    /// Escala fija de configuración (aproximada: supone proyector sin keystone).
    MmPerProjPx(f64),
}

/// Marcador impreso: id `pcx6` y esquinas TL, TR, BR, BL en `table_mm`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PrintedMarker {
    pub id: usize,
    pub corners_mm: [[f64; 2]; 4],
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SyncMode {
    /// Parches de sincronía en streaming (método rápido de Daniel).
    Marker,
    /// Lazo cerrado por cambio visible (más lento, no necesita homografía previa).
    Change,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CalibConfig {
    pub projector_id: String,
    pub camera_id: String,
    pub grid_cols: u32,
    pub grid_rows: u32,
    /// Celda de los marcadores en px del proyector; `None` = ancho/80.
    pub cell_px: Option<u32>,
    pub ransac_thr_proj_px: f64,
    pub metric: MetricRef,
    pub sync: SyncMode,
    /// Ancho mínimo de franja en px de cámara para capturar un bit (Daniel: bit 3 ≈ 3,6 px sí, 1,8 px no).
    pub min_stripe_cam_px: f64,
    pub lut_grid_step: u32,
    pub hole_fill_passes: usize,
    pub out_dir: PathBuf,
    pub drift_threshold_mm: f64,
    pub verify_square_mm: f64,
    pub version: i64,
}

impl Default for CalibConfig {
    fn default() -> Self {
        Self {
            projector_id: "main".into(),
            camera_id: "main".into(),
            grid_cols: 8,
            grid_rows: 5,
            cell_px: None,
            ransac_thr_proj_px: 10.0,
            metric: MetricRef::MmPerProjPx(0.5),
            sync: SyncMode::Marker,
            min_stripe_cam_px: 2.5,
            lut_grid_step: 1,
            hole_fill_passes: 4,
            out_dir: std::env::temp_dir().join("pcx-calib"),
            drift_threshold_mm: 2.0,
            verify_square_mm: 36.0,
            version: 1,
        }
    }
}

/// Estado persistente de una calibración (lo que `verify` necesita).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Calibration {
    pub projector_id: String,
    pub camera_id: String,
    pub proj_size: (u32, u32),
    pub cam_size: (u32, u32),
    /// cámara → proyector (plano de la mesa).
    pub h_cam_proj: [f64; 9],
    /// `table_mm` → proyector.
    pub h_table_proj: [f64; 9],
    pub residual_mm: f64,
    pub version: i64,
}

impl Calibration {
    pub fn h_cp(&self) -> Mat3 {
        from_row_major(&self.h_cam_proj)
    }
    pub fn h_tp(&self) -> Mat3 {
        from_row_major(&self.h_table_proj)
    }
    /// cámara → `table_mm`.
    pub fn h_ct(&self) -> Mat3 {
        self.h_tp().try_inverse().unwrap_or_else(Mat3::identity) * self.h_cp()
    }
    pub fn table_to_proj(&self) -> Transform {
        Transform::Homography(HomographyTransform {
            from_frame: TABLE_MM.into(),
            to_frame: format!("proj:{}", self.projector_id),
            version: self.version,
            residual_mm: Some(self.residual_mm),
            matrix: self.h_table_proj,
        })
    }
    pub fn cam_to_proj(&self) -> Transform {
        Transform::Homography(HomographyTransform {
            from_frame: format!("cam:{}", self.camera_id),
            to_frame: format!("proj:{}", self.projector_id),
            version: self.version,
            residual_mm: Some(self.residual_mm),
            matrix: self.h_cam_proj,
        })
    }
}

/// Evento `calibration_drift` (espejo de `events.CalibrationDrift`; el core lo envuelve en el `Envelope`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename = "calibration_drift")]
pub struct CalibrationDrift {
    pub from_frame: String,
    pub to_frame: String,
    pub residual_mm: f64,
    pub threshold_mm: f64,
}

impl CalibrationDrift {
    /// Evento del contrato (`pcx_contracts::Event::CalibrationDrift`), listo para el `Envelope`.
    pub fn to_event(&self) -> pcx_contracts::Event {
        pcx_contracts::Event::CalibrationDrift {
            from_frame: self.from_frame.clone(),
            to_frame: self.to_frame.clone(),
            residual_mm: self.residual_mm,
            threshold_mm: self.threshold_mm,
        }
    }
}

impl From<&CalibrationDrift> for pcx_contracts::Event {
    fn from(d: &CalibrationDrift) -> Self {
        d.to_event()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Step {
    QuickGrid,
    Metric,
    ChooseBits,
    GrayCapture,
    Decode,
    BuildLut,
    VerifySquares,
    Done,
}

#[derive(Debug, Clone, Serialize)]
pub struct StepLog {
    pub step: Step,
    /// Adquisición (reloj de la cámara) y cómputo (pared) del paso, en ms.
    pub acquisition_ms: f64,
    pub compute_ms: f64,
    pub note: String,
}

#[derive(Debug, Clone, Default, Serialize)]
pub struct QuickReport {
    pub markers_seen: usize,
    pub markers_total: usize,
    pub corners_inliers: usize,
    pub residual_proj_px: ErrStats,
    pub residual_mm: ErrStats,
}

#[derive(Debug, Clone, Default, Serialize)]
pub struct PreciseReport {
    pub skip_bits: (u8, u8),
    pub patterns: usize,
    pub stream: Option<StreamReport>,
    pub decode: Option<graycode::DecodeStats>,
    pub blocks_filled: usize,
    pub lut_coverage: f64,
    pub lut_path: Option<String>,
    /// LUT vs esquinas de marcadores medidas aparte (px de cámara y mm).
    pub check_cam_px: ErrStats,
    pub check_mm: ErrStats,
}

#[derive(Debug, Clone, Serialize)]
pub struct SquareResult {
    pub target_mm: Pt,
    pub measured_mm: Option<Pt>,
    pub err_mm: Option<f64>,
    pub err_proj_px: Option<f64>,
}

#[derive(Debug, Clone, Default, Serialize)]
pub struct VerifyReport {
    pub squares: Vec<SquareResult>,
    pub err_mm: ErrStats,
    pub err_proj_px: ErrStats,
    pub threshold_mm: f64,
    pub ok: bool,
}

#[derive(Debug, Clone, Default, Serialize)]
pub struct CalibReport {
    pub steps: Vec<StepLog>,
    pub quick: Option<QuickReport>,
    pub precise: Option<PreciseReport>,
    pub verify: Option<VerifyReport>,
    pub acquisition_ms: f64,
    pub compute_ms: f64,
}

/// Resultado final.
#[derive(Debug, Clone)]
pub struct CalibOutcome {
    pub mode: Mode,
    pub calibration: Option<Calibration>,
    /// Para `set_transform`: quick → table_mm→proj y cam→proj; precise → además la LUT proj→cam.
    pub transforms: Vec<Transform>,
    pub drift: Option<CalibrationDrift>,
    pub lut: Option<DenseLut>,
    pub grid: Option<BlockGrid>,
    /// Homografía aproximada proyector → cámara de la LUT (plano de la mesa, RANSAC).
    pub approx_h_pc: Option<Mat3>,
    /// Lienzo rectificado: polinomio robusto proyector → cámara (lente sí, bultos de objetos no).
    pub canvas: Option<Poly2>,
    pub decoded: Option<Decoded>,
    pub report: CalibReport,
}

/// Una corrida de calibración, paso a paso.
pub struct CalibRun {
    mode: Mode,
    cfg: CalibConfig,
    plan: Vec<Step>,
    idx: usize,
    prior: Option<Calibration>,
    last: Option<Frame>,
    first_t: Option<i64>,
    proj: (u32, u32),
    cam: (u32, u32),
    // estado intermedio
    sum_img: Option<GrayImage>,
    corners: Vec<(Pt, Pt)>,
    h_cp: Option<Mat3>,
    calib: Option<Calibration>,
    skip: (u8, u8),
    gray: Vec<(GraySpec, Frame)>,
    out: CalibOutcome,
}

impl CalibRun {
    pub fn new(mode: Mode, cfg: CalibConfig, prior: Option<Calibration>) -> Self {
        let plan = match mode {
            Mode::Quick => vec![Step::QuickGrid, Step::Metric, Step::Done],
            Mode::Precise => vec![Step::QuickGrid, Step::Metric, Step::ChooseBits, Step::GrayCapture, Step::Decode, Step::BuildLut, Step::Done],
            Mode::Verify => vec![Step::VerifySquares, Step::Done],
        };
        let out = CalibOutcome {
            mode,
            calibration: None,
            transforms: Vec::new(),
            drift: None,
            lut: None,
            grid: None,
            approx_h_pc: None,
            canvas: None,
            decoded: None,
            report: CalibReport::default(),
        };
        Self {
            mode,
            cfg,
            plan,
            idx: 0,
            prior,
            last: None,
            first_t: None,
            proj: (0, 0),
            cam: (0, 0),
            sum_img: None,
            corners: Vec::new(),
            h_cp: None,
            calib: None,
            skip: (0, 0),
            gray: Vec::new(),
            out,
        }
    }

    pub fn mode(&self) -> Mode {
        self.mode
    }

    /// Próximo paso a ejecutar.
    pub fn next_step(&self) -> Step {
        self.plan[self.idx]
    }

    /// Ejecuta un paso; devuelve el siguiente (`Done` cuando terminó).
    pub fn step(&mut self, sink: &mut dyn PatternSink, grab: &mut dyn FrameGrab) -> Result<Step, CalibError> {
        let step = self.plan[self.idx];
        if step == Step::Done {
            return Ok(Step::Done);
        }
        self.proj = sink.size();
        let t_acq0 = self.last.as_ref().map(|f| f.t_capture_ns);
        let mut compute = 0.0;
        let note = match step {
            Step::QuickGrid => self.quick_grid(sink, grab, &mut compute)?,
            Step::Metric => self.metric(&mut compute)?,
            Step::ChooseBits => self.choose_bits(&mut compute)?,
            Step::GrayCapture => self.gray_capture(sink, grab)?,
            Step::Decode => self.decode(&mut compute)?,
            Step::BuildLut => self.build_lut(&mut compute)?,
            Step::VerifySquares => self.verify(sink, grab, &mut compute)?,
            Step::Done => String::new(),
        };
        let t_acq1 = self.last.as_ref().map(|f| f.t_capture_ns);
        let acq = match (t_acq0.or(self.first_t), t_acq1) {
            (Some(a), Some(b)) => (b - a) as f64 / 1e6,
            _ => 0.0,
        };
        self.out.report.acquisition_ms += acq;
        self.out.report.compute_ms += compute;
        self.out.report.steps.push(StepLog { step, acquisition_ms: acq, compute_ms: compute, note });
        self.idx += 1;
        Ok(self.plan[self.idx])
    }

    /// Corre hasta el final y libera la salida del proyector (también si falla).
    pub fn run(mut self, sink: &mut dyn PatternSink, grab: &mut dyn FrameGrab) -> Result<CalibOutcome, CalibError> {
        let r = (|| {
            while self.step(sink, grab)? != Step::Done {}
            Ok::<(), CalibError>(())
        })();
        let rel = sink.release();
        r?;
        rel?;
        Ok(self.finish())
    }

    pub fn finish(mut self) -> CalibOutcome {
        self.out.calibration = self.calib.clone();
        self.out
    }

    fn remember(&mut self, f: &Frame) {
        self.cam = (f.image.width, f.image.height);
        self.last = Some(f.clone());
    }

    fn current(&mut self, grab: &mut dyn FrameGrab) -> Result<Frame, CalibError> {
        let f = grab.grab(0)?;
        if self.last.is_none() {
            self.first_t = Some(f.t_capture_ns);
        }
        self.remember(&f);
        Ok(f)
    }
}
