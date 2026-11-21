//! Implementación de cada paso de `CalibRun`.

use std::time::Instant;

use pcx_contracts::{LutDescriptor, LutFormat, LutSampling, LutTransform, LutUnits, Transform, TABLE_MM};

use super::*;
use crate::graycode::{self, bits_for, DecodeParams, GraySpec};
use crate::homography::{apply, dlt, local_scale, ransac, to_row_major, Mat3, Pt};
use crate::image::{diff, percentile, ErrStats, FloatMap, GrayImage};
use crate::io::{CalibError, FrameGrab, Pattern, PatternSink};
use crate::lut::{BlockGrid, DenseLut, Poly2};
use crate::markers::{self, MarkerGrid};
use crate::sync::{self, ChangeParams, StreamParams, SyncPatches};

fn ms(t: Instant) -> f64 {
    t.elapsed().as_secs_f64() * 1e3
}

/// Máscara de tinta y mapa continuo de un par patrón/inverso (binaria por signo, cancela luz ambiente y albedo).
fn sign_ink(p: &GrayImage, i: &GrayImage) -> (Vec<bool>, FloatMap) {
    let d = diff(p, i);
    let abs: Vec<f32> = d.data.iter().step_by(3).map(|v| v.abs()).collect();
    let t = 4f32.max(0.2 * percentile(&abs, 99.0));
    (d.data.iter().map(|&v| v > t).collect(), d)
}

/// Umbral adaptativo (media local por imagen integral) para el marcador impreso: tinta = oscuro.
fn adaptive_dark(g: &GrayImage, r: i64) -> (Vec<bool>, FloatMap) {
    let (w, h) = (g.width as i64, g.height as i64);
    let mut integ = vec![0u64; ((w + 1) * (h + 1)) as usize];
    for y in 0..h {
        let mut row = 0u64;
        for x in 0..w {
            row += g.data[(y * w + x) as usize] as u64;
            integ[((y + 1) * (w + 1) + x + 1) as usize] = integ[(y * (w + 1) + x + 1) as usize] + row;
        }
    }
    let mut ink = vec![false; (w * h) as usize];
    for y in 0..h {
        for x in 0..w {
            let (x0, y0, x1, y1) = ((x - r).max(0), (y - r).max(0), (x + r + 1).min(w), (y + r + 1).min(h));
            let s = integ[(y1 * (w + 1) + x1) as usize] + integ[(y0 * (w + 1) + x0) as usize]
                - integ[(y0 * (w + 1) + x1) as usize]
                - integ[(y1 * (w + 1) + x0) as usize];
            let mean = s as f64 / ((x1 - x0) * (y1 - y0)) as f64;
            ink[(y * w + x) as usize] = (g.data[(y * w + x) as usize] as f64) < 0.8 * mean - 2.0;
        }
    }
    let cont = FloatMap { width: g.width, height: g.height, data: g.data.iter().map(|&v| 255.0 - v as f32).collect() };
    (ink, cont)
}

fn mean_img(a: &GrayImage, b: &GrayImage) -> GrayImage {
    GrayImage { width: a.width, height: a.height, data: a.data.iter().zip(&b.data).map(|(&x, &y)| ((x as u16 + y as u16) / 2) as u8).collect() }
}

/// Rellena un cuadrilátero convexo (px del proyector, centros enteros).
pub fn fill_quad(img: &mut GrayImage, q: &[Pt; 4], v: u8) {
    let fold = |f: fn(f64, f64) -> f64, init: f64, k: usize| q.iter().map(|p| p[k]).fold(init, f);
    let (x0, x1) = (fold(f64::min, f64::INFINITY, 0), fold(f64::max, f64::NEG_INFINITY, 0));
    let (y0, y1) = (fold(f64::min, f64::INFINITY, 1), fold(f64::max, f64::NEG_INFINITY, 1));
    let area: f64 = (0..4).map(|i| q[i][0] * q[(i + 1) % 4][1] - q[(i + 1) % 4][0] * q[i][1]).sum();
    for y in (y0.floor().max(0.0) as u32)..=(y1.ceil().min(img.height as f64 - 1.0) as u32) {
        for x in (x0.floor().max(0.0) as u32)..=(x1.ceil().min(img.width as f64 - 1.0) as u32) {
            let inside = (0..4).all(|i| {
                let (a, b) = (q[i], q[(i + 1) % 4]);
                let c = (b[0] - a[0]) * (y as f64 - a[1]) - (b[1] - a[1]) * (x as f64 - a[0]);
                c * area.signum() >= 0.0
            });
            if inside {
                img.set(x, y, v);
            }
        }
    }
}

fn d2(a: Pt, b: Pt) -> f64 {
    (a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2)
}

impl CalibRun {
    fn grid(&self) -> MarkerGrid {
        let cell = self.cfg.cell_px.unwrap_or((self.proj.0 / 80).max(6));
        MarkerGrid::new(self.proj, self.cfg.grid_cols, self.cfg.grid_rows, cell)
    }

    pub(super) fn quick_grid(&mut self, sink: &mut dyn PatternSink, grab: &mut dyn FrameGrab, compute: &mut f64) -> Result<String, CalibError> {
        let grid = self.grid();
        let prev = self.current(grab)?;
        let cp = ChangeParams::default();
        let (fp, _) = sync::show_and_wait(sink, grab, &Pattern::new("markers", grid.rasterize(false)), &prev.image, None, &cp)?;
        let (fi, _) = sync::show_and_wait(sink, grab, &Pattern::new("markers_inv", grid.rasterize(true)), &fp.image, None, &cp)?;
        self.remember(&fi);
        let t = Instant::now();
        let (ink, cont) = sign_ink(&fp.image, &fi.image);
        let dets = markers::detect(&ink, &cont, 12.0);
        let mut pairs = Vec::new();
        for d in &dets {
            if let Some(pc) = grid.corners(d.id) {
                pairs.extend(d.corners.iter().copied().zip(pc));
            }
        }
        self.sum_img = Some(mean_img(&fp.image, &fi.image));
        let seen = pairs.len() / 4;
        if seen < 2 {
            *compute += ms(t);
            if self.prior.is_some() && self.mode == Mode::Precise {
                self.h_cp = self.prior.as_ref().map(|p| p.h_cp());
                return Ok(format!("solo {seen} marcadores: uso la calibración previa"));
            }
            return Err(CalibError::NotEnough(format!("{seen} marcadores legibles")));
        }
        let (src, dst): (Vec<Pt>, Vec<Pt>) = pairs.iter().copied().unzip();
        let fit = ransac(&src, &dst, self.cfg.ransac_thr_proj_px, 400, 7).ok_or_else(|| CalibError::NotEnough("homografía degenerada".into()))?;
        self.corners = pairs.iter().zip(&fit.inliers).filter(|(_, ok)| **ok).map(|(p, _)| *p).collect();
        self.h_cp = Some(fit.h);
        let res: Vec<f64> = fit.residuals.iter().zip(&fit.inliers).filter(|(_, ok)| **ok).map(|(r, _)| *r).collect();
        self.out.report.quick = Some(QuickReport {
            markers_seen: seen,
            markers_total: grid.markers.len(),
            corners_inliers: res.len(),
            residual_proj_px: ErrStats::of(&res),
            residual_mm: ErrStats::default(),
        });
        *compute += ms(t);
        Ok(format!("{seen}/{} marcadores", grid.markers.len()))
    }

    pub(super) fn metric(&mut self, compute: &mut f64) -> Result<String, CalibError> {
        let t = Instant::now();
        let h_cp = self.h_cp.ok_or(CalibError::MissingPrior)?;
        let (h_tp, note) = match &self.cfg.metric {
            MetricRef::MmPerProjPx(s) => (Mat3::new(1.0 / s, 0.0, 0.0, 0.0, 1.0 / s, 0.0, 0.0, 0.0, 1.0), format!("escala fija {s} mm/px")),
            MetricRef::PrintedMarkers(list) => {
                let g = self.sum_img.as_ref().ok_or(CalibError::MissingPrior)?;
                let (ink, cont) = adaptive_dark(g, 15);
                let dets = markers::detect(&ink, &cont, 10.0);
                let (mut cam, mut mm, mut ids) = (Vec::new(), Vec::new(), Vec::new());
                for m in list {
                    if let Some(d) = dets.iter().find(|d| d.id == m.id) {
                        cam.extend_from_slice(&d.corners);
                        mm.extend_from_slice(&m.corners_mm);
                        ids.push(m.id);
                    }
                }
                match dlt(&cam, &mm).and_then(|h_ct| h_ct.try_inverse()) {
                    Some(h_tc) => (h_cp * h_tc, format!("marcadores impresos {ids:?} de {}", list.len())),
                    None => match &self.prior {
                        Some(p) => (h_cp * p.h_ct().try_inverse().unwrap_or_else(Mat3::identity), "marcadores impresos no vistos: marco previo".into()),
                        None => return Err(CalibError::NotEnough("no veo los marcadores impresos".into())),
                    },
                }
            }
        };
        let h_pt = h_tp.try_inverse().ok_or_else(|| CalibError::NotEnough("H mesa→proyector singular".into()))?;
        let mm: Vec<f64> = self
            .corners
            .iter()
            .map(|(c, p)| d2(apply(&h_cp, *c), *p).sqrt() * local_scale(&h_pt, *p))
            .collect();
        let st = ErrStats::of(&mm);
        if let Some(q) = self.out.report.quick.as_mut() {
            q.residual_mm = st;
        }
        let residual = if st.n > 0 { st.p50 } else { self.prior.as_ref().map_or(f64::NAN, |p| p.residual_mm) };
        let cal = Calibration {
            projector_id: self.cfg.projector_id.clone(),
            camera_id: self.cfg.camera_id.clone(),
            proj_size: self.proj,
            cam_size: self.cam,
            h_cam_proj: to_row_major(&h_cp),
            h_table_proj: to_row_major(&h_tp),
            residual_mm: residual,
            version: self.cfg.version,
        };
        self.out.transforms = vec![cal.table_to_proj(), cal.cam_to_proj()];
        self.calib = Some(cal);
        *compute += ms(t);
        Ok(note)
    }

    pub(super) fn choose_bits(&mut self, compute: &mut f64) -> Result<String, CalibError> {
        let t = Instant::now();
        let h_pc = self.h_cp.and_then(|h| h.try_inverse()).ok_or(CalibError::MissingPrior)?;
        let (w, h) = (self.proj.0 as f64, self.proj.1 as f64);
        let (mut sx, mut sy) = (Vec::new(), Vec::new());
        for j in 1..8 {
            for i in 1..8 {
                let p = [w * i as f64 / 8.0, h * j as f64 / 8.0];
                let c = apply(&h_pc, p);
                sx.push(d2(apply(&h_pc, [p[0] + 1.0, p[1]]), c).sqrt());
                sy.push(d2(apply(&h_pc, [p[0], p[1] + 1.0]), c).sqrt());
            }
        }
        let min_stripe = self.cfg.min_stripe_cam_px;
        let pick = |s: f64, n: u32| {
            let mut b = 0u8;
            while (1u32 << b) as f64 * s < min_stripe && b + 3 < bits_for(n) {
                b += 1;
            }
            b
        };
        let (mx, my) = (crate::image::percentile_f64(&sx, 50.0), crate::image::percentile_f64(&sy, 50.0));
        self.skip = (pick(mx, self.proj.0), pick(my, self.proj.1));
        *compute += ms(t);
        Ok(format!("{mx:.2}×{my:.2} px de cámara por px de proyector → omito {:?} bits", self.skip))
    }

    pub(super) fn gray_capture(&mut self, sink: &mut dyn PatternSink, grab: &mut dyn FrameGrab) -> Result<String, CalibError> {
        let specs = graycode::sequence(self.proj, self.skip);
        let pats: Vec<Pattern> = specs
            .iter()
            .map(|s| Pattern::new(format!("gray_{}{}_{}", if s.axis == 0 { 'x' } else { 'y' }, s.bit, s.inverse as u8), graycode::rasterize(*s, self.proj)))
            .collect();
        let frames = match (self.cfg.sync, self.h_cp.and_then(|h| h.try_inverse())) {
            (SyncMode::Marker, Some(h_pc)) => {
                let (f, rep) = sync::stream(sink, grab, &pats, &SyncPatches::new(self.proj), &h_pc, &StreamParams::default())?;
                self.out.report.precise.get_or_insert_with(Default::default).stream = Some(rep);
                f
            }
            _ => {
                let mut prev = self.current(grab)?;
                let mut v = Vec::new();
                for p in &pats {
                    let (f, _) = sync::show_and_wait(sink, grab, p, &prev.image, None, &ChangeParams::default())?;
                    prev = f.clone();
                    v.push(f);
                }
                v
            }
        };
        // el último cuadro visto (para medir la adquisición del paso)
        if let Some(l) = frames.iter().max_by_key(|f| f.t_capture_ns).cloned() {
            self.remember(&l);
        }
        let n = frames.len();
        self.gray = specs.into_iter().zip(frames).collect();
        self.out.report.precise.get_or_insert_with(Default::default).patterns = n;
        Ok(format!("{n} patrones"))
    }

    pub(super) fn decode(&mut self, compute: &mut f64) -> Result<String, CalibError> {
        let t = Instant::now();
        let frames: Vec<(GraySpec, &GrayImage)> = self.gray.iter().map(|(s, f)| (*s, &f.image)).collect();
        let mut prm = DecodeParams::default();
        if self.cfg.sync == SyncMode::Marker {
            prm.exclude.push(SyncPatches::new(self.proj).guard());
        }
        let d = graycode::decode(&frames, self.proj, self.skip, &prm).ok_or_else(|| CalibError::NotEnough("faltan patrones".into()))?;
        let note = format!("{} px válidos de {} iluminados; bloque {:?}", d.stats.valid, d.stats.lit, d.step());
        let pr = self.out.report.precise.get_or_insert_with(Default::default);
        pr.skip_bits = d.skip;
        pr.decode = Some(d.stats.clone());
        self.out.decoded = Some(d);
        self.gray.clear();
        *compute += ms(t);
        Ok(note)
    }

    pub(super) fn build_lut(&mut self, compute: &mut f64) -> Result<String, CalibError> {
        let t = Instant::now();
        let d = self.out.decoded.as_ref().ok_or(CalibError::MissingPrior)?;
        let mut grid = BlockGrid::from_decoded(d, 2);
        let (ps, cs) = grid.pairs();
        if ps.len() < 50 {
            return Err(CalibError::NotEnough(format!("{} bloques decodificados", ps.len())));
        }
        let filled = grid.fill_holes(self.cfg.hole_fill_passes);
        let lut = DenseLut::build(&grid, self.proj, self.cfg.lut_grid_step);
        let approx = ransac(&ps, &cs, 2.0_f64.max(0.5 * grid.cam_step as f64), 300, 11).map(|f| f.h);
        let canvas = Poly2::fit_robust(&ps, &cs, 1.0);
        let path = self.cfg.out_dir.join(format!("lut_proj_{}_to_cam_{}.npy", self.cfg.projector_id, self.cfg.camera_id));
        let sha = lut.write_npy(&path).map_err(|e| CalibError::Io(e.to_string()))?;
        // control independiente: esquinas de los marcadores (otros cuadros) contra la LUT
        let h_ct = self.calib.as_ref().map(|c| c.h_ct());
        let (mut ecam, mut emm) = (Vec::new(), Vec::new());
        for (c, p) in &self.corners {
            if let Some(l) = grid.sample(p[0], p[1]) {
                let e = d2(l, *c).sqrt();
                ecam.push(e);
                if let Some(h) = &h_ct {
                    emm.push(e * local_scale(h, *c));
                }
            }
        }
        let (scam, smm) = (ErrStats::of(&ecam), ErrStats::of(&emm));
        let pr = self.out.report.precise.get_or_insert_with(Default::default);
        pr.blocks_filled = filled;
        pr.lut_coverage = lut.coverage();
        pr.lut_path = Some(path.display().to_string());
        pr.check_cam_px = scam;
        pr.check_mm = smm;
        // la homografía del plano sale ahora del gray code (miles de muestras; RANSAC deja fuera los objetos)
        if let (Some(h_pc), Some(cal)) = (approx, self.calib.as_mut()) {
            if let Some(h_cp) = h_pc.try_inverse() {
                let h_tc = cal.h_ct().try_inverse().unwrap_or_else(Mat3::identity);
                cal.h_cam_proj = to_row_major(&h_cp);
                cal.h_table_proj = to_row_major(&(h_cp * h_tc));
                if smm.n > 0 {
                    cal.residual_mm = smm.p50;
                }
            }
        }
        let mut ts = self.calib.as_ref().map(|c| vec![c.table_to_proj(), c.cam_to_proj()]).unwrap_or_default();
        ts.push(Transform::Lut(LutTransform {
            from_frame: format!("proj:{}", self.cfg.projector_id),
            to_frame: format!("cam:{}", self.cfg.camera_id),
            version: self.cfg.version,
            residual_mm: (smm.n > 0).then_some(smm.p50),
            lut: LutDescriptor {
                format: LutFormat::NpyF32Hw2,
                width: lut.width,
                height: lut.height,
                units: LutUnits::OutputPx,
                sampling: LutSampling::IntegerCenters,
                grid_step: self.cfg.lut_grid_step as f64,
                path: path.display().to_string(),
                sha256: sha,
            },
            approx_matrix: approx.map(|h| to_row_major(&h)),
        }));
        self.out.transforms = ts;
        let note = format!("LUT {}x{} cobertura {:.1} %, {} bloques rellenados", lut.width, lut.height, lut.coverage() * 100.0, filled);
        self.out.approx_h_pc = approx;
        self.out.canvas = canvas;
        self.out.grid = Some(grid);
        self.out.lut = Some(lut);
        *compute += ms(t);
        Ok(note)
    }

    pub(super) fn verify(&mut self, sink: &mut dyn PatternSink, grab: &mut dyn FrameGrab, compute: &mut f64) -> Result<String, CalibError> {
        let cal = self.prior.clone().ok_or(CalibError::MissingPrior)?;
        let (h_tp, h_ct) = (cal.h_tp(), cal.h_ct());
        let h_pt = h_tp.try_inverse().ok_or(CalibError::MissingPrior)?;
        let (w, h) = (self.proj.0 as f64, self.proj.1 as f64);
        let side = self.cfg.verify_square_mm;
        let mut targets = Vec::new();
        let mut img = GrayImage::new(self.proj.0, self.proj.1, 0);
        for j in 0..3 {
            for i in 0..4 {
                let p = [w * (0.14 + 0.24 * i as f64), h * (0.2 + 0.3 * j as f64)];
                let x = apply(&h_pt, p);
                let q: [Pt; 4] = [[-1.0, -1.0], [1.0, -1.0], [1.0, 1.0], [-1.0, 1.0]].map(|d| apply(&h_tp, [x[0] + d[0] * side / 2.0, x[1] + d[1] * side / 2.0]));
                fill_quad(&mut img, &q, 255);
                targets.push(x);
            }
        }
        let inv = GrayImage { width: img.width, height: img.height, data: img.data.iter().map(|v| 255 - v).collect() };
        let prev = self.current(grab)?;
        let cp = ChangeParams::default();
        let (fp, _) = sync::show_and_wait(sink, grab, &Pattern::new("verify_squares", img), &prev.image, None, &cp)?;
        let (fi, _) = sync::show_and_wait(sink, grab, &Pattern::new("verify_squares_inv", inv), &fp.image, None, &cp)?;
        self.remember(&fi);
        let t = Instant::now();
        let (ink, cont) = sign_ink(&fp.image, &fi.image);
        let centers: Vec<Pt> = markers::quads(&ink, &cont, 6.0).iter().filter_map(markers::diagonal_center).map(|c| apply(&h_ct, c)).collect();
        let mut squares = Vec::new();
        for tgt in &targets {
            let best = centers.iter().copied().min_by(|a, b| d2(*a, *tgt).total_cmp(&d2(*b, *tgt))).filter(|m| d2(*m, *tgt).sqrt() < side);
            squares.push(SquareResult {
                target_mm: *tgt,
                measured_mm: best,
                err_mm: best.map(|m| d2(m, *tgt).sqrt()),
                err_proj_px: best.map(|m| d2(apply(&h_tp, m), apply(&h_tp, *tgt)).sqrt()),
            });
        }
        let emm: Vec<f64> = squares.iter().filter_map(|s| s.err_mm).collect();
        let epx: Vec<f64> = squares.iter().filter_map(|s| s.err_proj_px).collect();
        let st = ErrStats::of(&emm);
        let residual = if st.n >= 6 { st.p50 } else { f64::INFINITY };
        let ok = residual <= self.cfg.drift_threshold_mm;
        if !ok {
            self.out.drift = Some(CalibrationDrift {
                from_frame: TABLE_MM.into(),
                to_frame: format!("proj:{}", cal.projector_id),
                residual_mm: if residual.is_finite() { residual } else { 1e6 },
                threshold_mm: self.cfg.drift_threshold_mm,
            });
        }
        self.out.report.verify = Some(VerifyReport { squares, err_mm: st, err_proj_px: ErrStats::of(&epx), threshold_mm: self.cfg.drift_threshold_mm, ok });
        self.calib = Some(cal);
        *compute += ms(t);
        Ok(format!("{}/12 cuadrados, mediana {:.2} mm → {}", st.n, st.p50, if ok { "ok" } else { "DERIVA" }))
    }
}
