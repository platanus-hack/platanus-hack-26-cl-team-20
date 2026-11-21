//! Punta a punta contra el simulador: error medido contra la verdad sintética.
//! `cargo test -p pcx-calib --release -- --nocapture` imprime las cifras del README.

use pcx_calib::homography::{apply, from_row_major};
use pcx_calib::sim::{Sim, SimConfig, Surface, PRINTED, PRINTED_SIDE_MM};
use pcx_calib::{CalibConfig, CalibOutcome, CalibRun, ErrStats, MetricRef, Mode, PrintedMarker, Step, SyncMode};
use pcx_contracts::Transform;

fn cfg(tag: &str) -> CalibConfig {
    CalibConfig {
        metric: MetricRef::PrintedMarkers(
            PRINTED
                .iter()
                .map(|&(id, x, y)| {
                    let s = PRINTED_SIDE_MM;
                    PrintedMarker { id, corners_mm: [[x, y], [x + s, y], [x + s, y + s], [x, y + s]] }
                })
                .collect(),
        ),
        out_dir: std::env::temp_dir().join(format!("pcx-calib-test-{tag}-{}", std::process::id())),
        ..Default::default()
    }
}

fn run(sim: &Sim, mode: Mode, c: CalibConfig, prior: Option<pcx_calib::Calibration>) -> CalibOutcome {
    CalibRun::new(mode, c, prior).run(&mut sim.projector(), &mut sim.camera()).expect("calibración")
}

fn on_box_margin(x: [f64; 2], m: f64) -> bool {
    (330.0 - m..450.0 + m).contains(&x[0]) && (230.0 - m..330.0 + m).contains(&x[1])
}

fn show(name: &str, s: &ErrStats, unit: &str) {
    println!("  {name:<34} n={:<6} p50={:.2} p95={:.2} max={:.2} {unit}", s.n, s.p50, s.p95, s.max);
}

#[test]
fn quick_mode_homography_in_mm() {
    let sim = Sim::new(SimConfig::default());
    let w = sim.world();
    let out = run(&sim, Mode::Quick, cfg("quick"), None);
    let cal = out.calibration.clone().unwrap();
    let h_tp = cal.h_tp();
    let (mut epx, mut emm) = (Vec::new(), Vec::new());
    for j in 1..15 {
        for i in 1..20 {
            let p = [1024.0 * i as f64 / 20.0, 768.0 * j as f64 / 15.0];
            let x = w.table_of_proj(p);
            if on_box_margin(x, 40.0) {
                continue;
            }
            let pe = apply(&h_tp, x);
            epx.push(((pe[0] - p[0]).powi(2) + (pe[1] - p[1]).powi(2)).sqrt());
            let xe = w.table_of_proj(pe);
            emm.push(((xe[0] - x[0]).powi(2) + (xe[1] - x[1]).powi(2)).sqrt());
        }
    }
    let (spx, smm) = (ErrStats::of(&epx), ErrStats::of(&emm));
    let q = out.report.quick.as_ref().unwrap();
    println!(
        "QUICK: {}/{} marcadores, adquisición {:.0} ms, cómputo {:.0} ms",
        q.markers_seen, q.markers_total, out.report.acquisition_ms, out.report.compute_ms
    );
    show("residuo esquinas (propio)", &q.residual_mm, "mm");
    show("mesa vs verdad", &spx, "px proy");
    show("mesa vs verdad", &smm, "mm");
    assert!(q.markers_seen >= 30, "{} marcadores", q.markers_seen);
    assert!(smm.p50 < 3.0 && smm.p95 < 8.0, "{smm:?}");
    let json = serde_json::to_string(&out.transforms).unwrap();
    assert!(json.contains("\"kind\":\"homography\"") && json.contains("table_mm"));
    let back: Vec<Transform> = serde_json::from_str(&json).unwrap();
    assert_eq!(back, out.transforms);
}

#[test]
fn precise_mode_lut_corrects_parallax() {
    let sim = Sim::new(SimConfig::default());
    let w = sim.world();
    let out = run(&sim, Mode::Precise, cfg("precise"), None);
    let lut = out.lut.as_ref().unwrap();
    let h_pc = out.approx_h_pc.unwrap();
    let canvas = out.canvas.as_ref().unwrap();
    let (mut lt, mut lb, mut ht, mut hb, mut ct) = (Vec::new(), Vec::new(), Vec::new(), Vec::new(), Vec::new());
    let (mut lb_mm, mut hb_mm, mut lt_mm) = (Vec::new(), Vec::new(), Vec::new());
    for v in (2..lut.height - 2).step_by(3) {
        for u in (2..lut.width - 2).step_by(3) {
            let p = [u as f64, v as f64];
            let Some((c, gsd, surf)) = w.cam_of_proj(p) else { continue };
            let Some(l) = lut.get(u, v) else { continue };
            let el = ((l[0] - c[0]).powi(2) + (l[1] - c[1]).powi(2)).sqrt();
            let hp = apply(&h_pc, p);
            let eh = ((hp[0] - c[0]).powi(2) + (hp[1] - c[1]).powi(2)).sqrt();
            match surf {
                Surface::BoxTop => {
                    lb.push(el);
                    hb.push(eh);
                    lb_mm.push(el * gsd);
                    hb_mm.push(eh * gsd);
                }
                Surface::Table => {
                    lt.push(el);
                    ht.push(eh);
                    lt_mm.push(el * gsd);
                    let pc = canvas.eval(p);
                    ct.push(((pc[0] - c[0]).powi(2) + (pc[1] - c[1]).powi(2)).sqrt());
                }
                Surface::BoxSide => {}
            }
        }
    }
    let pr = out.report.precise.as_ref().unwrap();
    let st = pr.stream.as_ref().unwrap();
    println!(
        "PRECISE: skip {:?}, {} patrones, {} pasadas ({} reintentos), latencia {:.0} ms, cobertura LUT {:.1} %",
        pr.skip_bits,
        pr.patterns,
        st.passes,
        st.retried,
        st.latency_ms,
        pr.lut_coverage * 100.0
    );
    println!(
        "  adquisición {:.0} ms (reloj de cámara), cómputo {:.0} ms",
        out.report.acquisition_ms, out.report.compute_ms
    );
    for s in &out.report.steps {
        println!("    {:?}: adq {:.0} ms, cómputo {:.1} ms — {}", s.step, s.acquisition_ms, s.compute_ms, s.note);
    }
    show("LUT mesa", &ErrStats::of(&lt), "px cám");
    show("LUT mesa", &ErrStats::of(&lt_mm), "mm");
    show("LUT caja (90 mm)", &ErrStats::of(&lb), "px cám");
    show("LUT caja (90 mm)", &ErrStats::of(&lb_mm), "mm");
    show("homografía aprox. caja", &ErrStats::of(&hb), "px cám");
    show("homografía aprox. caja", &ErrStats::of(&hb_mm), "mm");
    show("homografía aprox. mesa", &ErrStats::of(&ht), "px cám");
    show("lienzo (polinomio) mesa", &ErrStats::of(&ct), "px cám");
    show("LUT vs esquinas de marcadores", &pr.check_cam_px, "px cám");
    let (slb, shb, slt, sct, sht) =
        (ErrStats::of(&lb), ErrStats::of(&hb), ErrStats::of(&lt), ErrStats::of(&ct), ErrStats::of(&ht));
    assert!(slt.p50 < 1.0, "LUT mesa {slt:?}");
    assert!(slb.n > 500 && slb.p50 < 1.5, "LUT caja {slb:?}");
    assert!(shb.p50 > 4.0 * slb.p50, "la homografía no muestra paralaje: {shb:?} vs {slb:?}");
    assert!(sct.p50 < sht.p50, "el lienzo no corrige la lente: {sct:?} vs {sht:?}");
    assert!(st.latency_ms > 100.0);
    // descriptor de la LUT
    let Some(Transform::Lut(l)) = out.transforms.iter().find(|t| matches!(t, Transform::Lut(_))) else {
        panic!("sin LUT")
    };
    let bytes = std::fs::read(&l.lut.path).unwrap();
    assert_eq!(&bytes[..6], b"\x93NUMPY");
    assert_eq!((bytes.len() - 128) / 8, (l.lut.width * l.lut.height) as usize);
    assert!(l.approx_matrix.is_some() && l.from_frame == "proj:main" && l.to_frame == "cam:main");
}

#[test]
fn verify_ok_then_drift_when_projector_moves() {
    let sim = Sim::new(SimConfig::default());
    let cal = run(&sim, Mode::Quick, cfg("verify"), None).calibration.unwrap();
    let ok = run(&sim, Mode::Verify, cfg("verify"), Some(cal.clone()));
    let v = ok.report.verify.as_ref().unwrap();
    println!(
        "VERIFY quieto: {} cuadrados, adq {:.0} ms, cómputo {:.0} ms",
        v.err_mm.n, ok.report.acquisition_ms, ok.report.compute_ms
    );
    show("cuadrados", &v.err_mm, "mm");
    show("cuadrados", &v.err_proj_px, "px proy");
    assert!(v.ok && ok.drift.is_none() && v.err_mm.n >= 10, "{v:?}");
    sim.move_projector(12.0, -6.0);
    let moved = run(&sim, Mode::Verify, cfg("verify"), Some(cal));
    let v = moved.report.verify.as_ref().unwrap();
    show("cuadrados tras mover 13 mm", &v.err_mm, "mm");
    let drift = moved.drift.expect("CalibrationDrift");
    let json = serde_json::to_value(&drift).unwrap();
    assert_eq!(json["type"], "calibration_drift");
    assert!(drift.residual_mm > drift.threshold_mm);
}

#[test]
fn precise_with_change_sync_and_step_machine() {
    let sim = Sim::new(SimConfig::default());
    let mut c = cfg("change");
    c.sync = SyncMode::Change;
    c.lut_grid_step = 4;
    let mut run = CalibRun::new(Mode::Precise, c, None);
    let (mut p, mut g) = (sim.projector(), sim.camera());
    let mut seen = vec![run.next_step()];
    while run.next_step() != Step::Done {
        seen.push(run.step(&mut p, &mut g).unwrap());
    }
    assert_eq!(
        seen,
        [Step::QuickGrid, Step::Metric, Step::ChooseBits, Step::GrayCapture, Step::Decode, Step::BuildLut, Step::Done]
    );
    let out = run.finish();
    println!(
        "PRECISE (sincronía por cambio): adquisición {:.0} ms, cómputo {:.0} ms",
        out.report.acquisition_ms, out.report.compute_ms
    );
    let cal = out.calibration.unwrap();
    let h = from_row_major(&cal.h_cam_proj);
    assert!(h.iter().all(|v| v.is_finite()));
    assert!(out.lut.unwrap().coverage() > 0.5);
}
