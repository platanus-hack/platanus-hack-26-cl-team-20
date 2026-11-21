//! Paridad contra el pipeline Python del opencv_zoo (`testdata/reference_zoo.json`, generado con
//! `testdata/make_reference.py`). Necesita los modelos en la caché (`cargo run --example fetch_models`); si no
//! están, el test se salta.
use pcx_hands::{models, DetectorConfig, Frame, HandDetector, PixelFormat};
use std::path::{Path, PathBuf};

fn testdata() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("testdata")
}

fn detector() -> Option<HandDetector> {
    let dir = models::models_dir();
    let (p, l) = (models::cached(&models::PALM, &dir)?, models::cached(&models::HANDPOSE, &dir)?);
    Some(HandDetector::new(&p, &l, DetectorConfig::default()).expect("ort"))
}

fn d(a: [f64; 2], b: [f64; 2]) -> f64 {
    ((a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2)).sqrt()
}

#[test]
fn landmarks_match_zoo_reference() {
    let Some(mut det) = detector() else {
        eprintln!("SKIP: faltan los modelos (cargo run -p pcx-hands --example fetch_models)");
        return;
    };
    let reference: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(testdata().join("reference_zoo.json")).unwrap()).unwrap();
    let mut all = Vec::new();
    for (name, r) in reference.as_object().unwrap() {
        let img = image::open(testdata().join(name)).unwrap().to_rgb8();
        let (w, h) = img.dimensions();
        let frame = Frame::packed(img.as_raw(), w as usize, h as usize, PixelFormat::Rgb8).unwrap();
        let palms = det.detect_palms(&frame).unwrap();
        let ref_hands = r["hands"].as_array().unwrap();
        assert_eq!(palms.len(), r["n_palms"].as_u64().unwrap() as usize, "{name}: n palmas");
        for rh in ref_hands {
            let rb: Vec<f64> = serde_json::from_value(rh["palm_box"].clone()).unwrap();
            let rc = [(rb[0] + rb[2]) / 2.0, (rb[1] + rb[3]) / 2.0];
            let p = palms
                .iter()
                .min_by(|a, b| {
                    let ca = [(a.bbox[0] + a.bbox[2]) / 2.0, (a.bbox[1] + a.bbox[3]) / 2.0];
                    let cb = [(b.bbox[0] + b.bbox[2]) / 2.0, (b.bbox[1] + b.bbox[3]) / 2.0];
                    d(ca, rc).total_cmp(&d(cb, rc))
                })
                .unwrap();
            let palm_err = (0..4).map(|i| (p.bbox[i] - rb[i]).abs()).sum::<f64>() / 4.0;
            let lm = det.landmarks(&frame, &p.roi()).unwrap();
            let rl: Vec<[f64; 3]> = serde_json::from_value(rh["landmarks"].clone()).unwrap();
            let err =
                (0..21).map(|i| d([lm.landmarks[i][0], lm.landmarks[i][1]], [rl[i][0], rl[i][1]])).sum::<f64>() / 21.0;
            let size =
                rl.iter().map(|p| p[1]).fold(f64::MIN, f64::max) - rl.iter().map(|p| p[1]).fold(f64::MAX, f64::min);
            let rconf = rh["conf"].as_f64().unwrap();
            let rhand = rh["handedness"].as_f64().unwrap();
            println!(
                "{name:24} palma {palm_err:5.2} px | landmarks {err:5.2} px ({:.1}% de la mano) | conf {:.3}/{rconf:.3} | lado {:.2}/{rhand:.2}",
                100.0 * err / size,
                lm.score,
                lm.handedness
            );
            assert!((lm.handedness as f64 > 0.5) == (rhand > 0.5), "{name}: lateralidad");
            all.push(err);
        }
    }
    let mean = all.iter().sum::<f64>() / all.len() as f64;
    let max = all.iter().copied().fold(0.0, f64::max);
    println!("manos {} · error medio {mean:.2} px · peor {max:.2} px", all.len());
    assert!(all.len() >= 10);
    assert!(mean < 3.0 && max < 6.0, "paridad: medio {mean:.2} px, peor {max:.2} px");
}

#[test]
fn engine_tracks_with_stable_ids_and_no_redetection() {
    let Some(det) = detector() else {
        eprintln!("SKIP: faltan los modelos");
        return;
    };
    use pcx_hands::{EngineConfig, FrameMeta, HandsEngine};
    let img = image::open(testdata().join("woman_hands.jpg")).unwrap().to_rgb8();
    let (w, h) = img.dimensions();
    let frame = Frame::packed(img.as_raw(), w as usize, h as usize, PixelFormat::Rgb8).unwrap();
    let cfg = EngineConfig { homography: Some([0.5, 0.0, 0.0, 0.0, 0.5, 0.0, 0.0, 0.0, 1.0]), ..Default::default() };
    let mut eng = HandsEngine::new(det, cfg);
    let mut ids = None;
    for i in 0..6 {
        let t = 1_000_000_000 + i * 33_000_000;
        let meta = FrameMeta {
            camera_id: "cam0".into(),
            seq: i,
            t_capture_ns: t,
            w: w as i64,
            h: h as i64,
            segment: None,
            format: Default::default(),
            stride: None,
        };
        let out = eng.process(&frame, &meta).unwrap();
        assert_eq!(out.hands.len(), 2, "cuadro {i}");
        let mut now: Vec<String> = out.hands.iter().map(|h| h.id.clone()).collect();
        now.sort();
        if i == 0 {
            assert_eq!(out.timings.palm_runs, 1);
        } else {
            assert_eq!(out.timings.palm_runs, 0, "con 2 manos seguidas no se redetecta");
            assert_eq!(ids.as_ref(), Some(&now), "IDs estables");
        }
        ids = Some(now);
        let hs = &out.hands[0];
        assert_eq!(hs.landmarks_px.len(), 21);
        let tip = hs.landmarks_px[8];
        let mm = hs.tip_mm.unwrap();
        assert!((mm.0 - tip.0 * 0.5).abs() < 1e-9 && (mm.1 - tip.1 * 0.5).abs() < 1e-9);
        assert_eq!(serde_json::to_value(hs).unwrap()["camera_id"], "cam0");
    }
}
