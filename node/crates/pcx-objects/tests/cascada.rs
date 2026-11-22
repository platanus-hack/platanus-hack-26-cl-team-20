//! Escenas sintéticas para la cascada: niveles 0–2 sin modelos; 3–5 con DINO/SAM/LLM falsos.

mod common;

use common::*;
use pcx_objects::geom;
use pcx_objects::models::Detection;
use pcx_objects::{CleanFrame, MockLlm, ObjectEvent, Params, Tracker};
use serde_json::json;

fn frame(objs: &[Obj], light: i32, seed: u64) -> CleanFrame {
    CleanFrame { rgb: render(objs, light, seed), t_ns: seed as i64 }
}

fn two_objects() -> (Obj, Obj) {
    let a = Obj { tex: texture(1, 80, 56), cx: 180.0, cy: 140.0, angle_deg: 0.0 };
    let b = Obj { tex: texture(2, 72, 72), cx: 450.0, cy: 220.0, angle_deg: 0.0 };
    (a, b)
}

fn tracker_with(a: &Obj, b: &Obj) -> Tracker {
    let mut t = Tracker::new(canvas(), Params::default());
    let f0 = frame(&[a.clone(), b.clone()], 0, 1);
    t.add_object("a", "tarjeta A", "a card", mask_of(a), &f0);
    t.add_object("b", "tarjeta B", "a square card", mask_of(b), &f0);
    t
}

fn center_mm(t: &Tracker, id: &str) -> (f64, f64) {
    let s = t.objects().into_iter().find(|o| o.id == id).expect("visible");
    (s.center_mm.0, s.center_mm.1)
}

fn moved<'a>(evs: &'a [ObjectEvent], id: &str) -> Option<&'a ObjectEvent> {
    evs.iter().find(|e| matches!(e, ObjectEvent::ObjectMoved { object_id, .. } if object_id == id))
}

#[test]
fn n0_quieto_sin_eventos() {
    let (a, b) = two_objects();
    let mut t = tracker_with(&a, &b);
    let r = t.update(&frame(&[a, b], 0, 2)); // mismo cuadro, otro ruido de sensor
    assert!(r.events.is_empty());
    assert!(r.changed_px < 500, "{}", r.changed_px);
}

#[test]
fn n1_cambio_de_luz_sigue_igual() {
    let (a, b) = two_objects();
    let mut t = tracker_with(&a, &b);
    let r = t.update(&frame(&[a, b], 40, 3)); // luz +40 en toda la mesa: cambia todo, NCC = 1
    assert!(r.changed_px > 500);
    assert!(r.events.is_empty(), "{:?}", r.events);
    assert!(r.log.iter().any(|l| l.contains("N1 tarjeta A: sigue igual")), "{:?}", r.log);
}

#[test]
fn n2_traslacion() {
    let (a, b) = two_objects();
    let mut t = tracker_with(&a, &b);
    let c0 = center_mm(&t, "a");
    let a2 = Obj { cx: a.cx + 60.0, cy: a.cy + 25.0, ..a.clone() };
    let r = t.update(&frame(&[a2, b], 0, 4));
    let Some(ObjectEvent::ObjectMoved { aff, level, .. }) = moved(&r.events, "a") else {
        panic!("{:?} {:?}", r.events, r.log)
    };
    assert!(level.as_deref().unwrap().starts_with("N2"), "{level:?}");
    assert!(moved(&r.events, "b").is_none());
    let c1 = center_mm(&t, "a");
    assert!(((c1.0 - c0.0) - 120.0).abs() < 4.0 && ((c1.1 - c0.1) - 50.0).abs() < 4.0, "{c0:?} → {c1:?}");
    let aff = aff.unwrap();
    assert!((aff[0][2] - 120.0).abs() < 4.0 && (aff[1][2] - 50.0).abs() < 4.0, "{aff:?}");
}

#[test]
fn n2_rotacion() {
    let (a, b) = two_objects();
    let mut t = tracker_with(&a, &b);
    let a2 = Obj { cx: a.cx + 30.0, cy: a.cy + 40.0, angle_deg: 30.0, ..a.clone() };
    let r = t.update(&frame(&[a2, b], 0, 5));
    let Some(ObjectEvent::ObjectMoved { aff, .. }) = moved(&r.events, "a") else {
        panic!("{:?} {:?}", r.events, r.log)
    };
    let ang = geom::angle_deg(&aff.unwrap());
    assert!((ang - 30.0).abs() < 3.0, "ángulo {ang}");
}

#[test]
fn n2_desaparece_y_reaparece() {
    let (a, b) = two_objects();
    let mut t = tracker_with(&a, &b);
    let r = t.update(&frame(std::slice::from_ref(&b), 0, 6));
    assert!(
        r.events.iter().any(|e| matches!(e, ObjectEvent::ObjectRemoved { object_id, .. } if object_id == "a")),
        "{:?}",
        r.log
    );
    assert!(t.objects().iter().all(|o| o.id != "a"));
    let a3 = Obj { cx: 300.0, cy: 290.0, angle_deg: -15.0, ..a.clone() };
    let r = t.update(&frame(&[a3, b], 0, 7));
    let ap = r.events.iter().find_map(|e| match e {
        ObjectEvent::ObjectAppeared { object, .. } if object.id == "a" => Some(object.clone()),
        _ => None,
    });
    let ap = ap.unwrap_or_else(|| panic!("{:?} {:?}", r.events, r.log));
    assert!((ap.center_mm.0 - 600.0).abs() < 6.0 && (ap.center_mm.1 - 580.0).abs() < 6.0, "{:?}", ap.center_mm);
}

#[test]
fn n2_intercambio() {
    let a = Obj { tex: texture(1, 72, 72), cx: 180.0, cy: 140.0, angle_deg: 0.0 };
    let b = Obj { tex: texture(2, 72, 72), cx: 450.0, cy: 220.0, angle_deg: 0.0 };
    let mut t = tracker_with(&a, &b);
    let (ca, cb) = (center_mm(&t, "a"), center_mm(&t, "b"));
    let a2 = Obj { cx: b.cx, cy: b.cy, ..a.clone() };
    let b2 = Obj { cx: a.cx, cy: a.cy, ..b.clone() };
    let r = t.update(&frame(&[a2, b2], 0, 8));
    assert!(moved(&r.events, "a").is_some() && moved(&r.events, "b").is_some(), "{:?}", r.log);
    let (na, nb) = (center_mm(&t, "a"), center_mm(&t, "b"));
    assert!((na.0 - cb.0).abs() < 4.0 && (na.1 - cb.1).abs() < 4.0, "{na:?} vs {cb:?}");
    assert!((nb.0 - ca.0).abs() < 4.0 && (nb.1 - ca.1).abs() < 4.0, "{nb:?} vs {ca:?}");
}

#[test]
fn n2_aparece_objeto_nuevo_sin_duenio() {
    let (a, b) = two_objects();
    let mut t = tracker_with(&a, &b);
    let c = Obj { tex: texture(9, 64, 64), cx: 320.0, cy: 300.0, angle_deg: 0.0 };
    let r = t.update(&frame(&[a, b, c], 0, 9));
    assert!(r.events.is_empty());
    assert_eq!(r.unclaimed_zones, 1, "{:?}", r.log);
}

#[test]
fn n3_arrugado_dino_sam() {
    let (a, b) = two_objects();
    let sam = FakeSam::default();
    let dino = FakeDino::default();
    let mut t = tracker_with(&a, &b).with_segmenter(Box::new(sam.clone())).with_detector(Box::new(dino.clone()));
    t.params.n2b_enabled = false; // cascada original: desde R4 este caso lo resuelve antes el nivel 2b (tests/n2b.rs)
                                  // arrugado: misma paleta, otra disposición → ORB no calza; DINO lo encuentra por prompt.
    let a2 = Obj { tex: scrambled(&a.tex, 7), cx: 330.0, cy: 90.0, angle_deg: 10.0 };
    let m2 = mask_of(&a2);
    *sam.truth.lock().unwrap() = vec![m2.clone(), mask_of(&b)];
    *dino.boxes.lock().unwrap() =
        vec![Detection { prompt: "a card".into(), label: "card".into(), bbox: bbox_f(&m2), score: 0.5 }];
    let r = t.update(&frame(&[a2, b], 0, 10));
    let Some(ObjectEvent::ObjectMoved { level, .. }) = moved(&r.events, "a") else {
        panic!("{:?} {:?}", r.events, r.log)
    };
    assert!(level.as_deref().unwrap().starts_with("N3"), "{level:?}");
}

#[test]
fn n4_llm_desempata() {
    let (a, b) = two_objects();
    let sam = FakeSam::default();
    let dino = FakeDino::default();
    let llm = MockLlm::new(json!({"n": 2}));
    let mut t = tracker_with(&a, &b)
        .with_segmenter(Box::new(sam.clone()))
        .with_detector(Box::new(dino.clone()))
        .with_llm(Box::new(llm));
    // dos «arrugados» parecidos: N3 no puede decidir (margen < 0.25) → el LLM elige el 2.
    let x1 = Obj { tex: scrambled(&a.tex, 7), cx: 330.0, cy: 90.0, angle_deg: 0.0 };
    let x2 = Obj { tex: scrambled(&a.tex, 8), cx: 330.0, cy: 300.0, angle_deg: 0.0 };
    let (m1, m2) = (mask_of(&x1), mask_of(&x2));
    *sam.truth.lock().unwrap() = vec![m1.clone(), m2.clone(), mask_of(&b)];
    *dino.boxes.lock().unwrap() = vec![
        Detection { prompt: "a card".into(), label: "card".into(), bbox: bbox_f(&m1), score: 0.5 },
        Detection { prompt: "a card".into(), label: "card".into(), bbox: bbox_f(&m2), score: 0.5 },
    ];
    let r = t.update(&frame(&[x1, x2, b], 0, 11));
    let Some(ObjectEvent::ObjectMoved { level, center_mm, .. }) = moved(&r.events, "a") else {
        panic!("{:?} {:?}", r.events, r.log)
    };
    assert_eq!(level.as_deref(), Some("N4 llm"));
    let llm_calls = t.llm().unwrap().usage().len();
    assert_eq!(llm_calls, 1);
    // el candidato 2 del orden por puntaje: debe ser uno de los dos y no el objeto B
    assert!((center_mm.0 - 660.0).abs() < 6.0, "{center_mm:?}");
}

#[test]
fn n5_registro_global_mock() {
    let (a, b) = two_objects();
    let sam = FakeSam::default();
    *sam.truth.lock().unwrap() = vec![mask_of(&a), mask_of(&b)];
    let llm = MockLlm::new(json!({"regiones": [
        {"n": 1, "objeto": "tarjeta cuadrada", "prompt": "a square card"},
        {"n": 2, "objeto": "tarjeta", "prompt": "a card"}]}));
    let dir = std::env::temp_dir().join(format!("pcx-objects-test-{}", std::process::id()));
    let mut t = Tracker::new(canvas(), Params::default())
        .with_segmenter(Box::new(sam))
        .with_llm(Box::new(llm))
        .with_appearance_dir(dir.clone());
    let f = frame(&[a, b], 0, 12);
    let r = t.register_all(&f).unwrap();
    let labels: Vec<String> = r
        .events
        .iter()
        .filter_map(
            |e| if let ObjectEvent::ObjectAppeared { object, .. } = e { Some(object.label.clone()) } else { None },
        )
        .collect();
    assert_eq!(labels, vec!["tarjeta cuadrada", "tarjeta"], "{:?}", r.log);
    let objs = t.objects();
    assert_eq!(objs.len(), 2);
    let png = objs[0].appearance.as_ref().expect("apariencia");
    assert!(std::path::Path::new(png).exists());
    // la serialización cumple las claves obligatorias del contrato
    let v = serde_json::to_value(&objs[0]).unwrap();
    for k in ["id", "label", "confidence", "polygon_mm", "center_mm", "since_ns", "last_seen_ns"] {
        assert!(v.get(k).is_some());
    }
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn n3_never_assigns_one_detection_to_two_objects() {
    let (a, _) = two_objects();
    let b = Obj { cx: 450.0, cy: 220.0, ..a.clone() };
    let initial = frame(&[a.clone(), b.clone()], 0, 1);
    let dino = FakeDino::default();
    let sam = FakeSam::default();
    let params = Params { n2_min_inliers: 100000, ..Default::default() };
    let mut t =
        Tracker::new(canvas(), params).with_detector(Box::new(dino.clone())).with_segmenter(Box::new(sam.clone()));
    t.add_object("a", "same", "card", mask_of(&a), &initial);
    t.add_object("b", "same", "card", mask_of(&b), &initial);
    let target = Obj { tex: scrambled(&a.tex, 7), cx: 320.0, cy: 300.0, angle_deg: 0.0 };
    let m = mask_of(&target);
    *sam.truth.lock().unwrap() = vec![m.clone()];
    *dino.boxes.lock().unwrap() =
        vec![Detection { prompt: "card".into(), label: "card".into(), bbox: bbox_f(&m), score: 0.8 }];
    t.update(&frame(&[target], 0, 2));
    assert_eq!(t.objects().len(), 1, "una detección física solo puede tener una identidad");
}

#[test]
fn detector_failure_preserves_object_and_retries_a_static_frame() {
    use pcx_objects::models::{BoxErr, Detector};
    use std::sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    };
    struct Flaky {
        fail: Arc<AtomicBool>,
        dino: FakeDino,
    }
    impl Detector for Flaky {
        fn detect(&mut self, img: &image::RgbImage, prompts: &[String], th: f32) -> Result<Vec<Detection>, BoxErr> {
            if self.fail.swap(false, Ordering::SeqCst) {
                Err("transient".into())
            } else {
                self.dino.detect(img, prompts, th)
            }
        }
    }
    let (a, b) = two_objects();
    let sam = FakeSam::default();
    let dino = FakeDino::default();
    let mut t = tracker_with(&a, &b)
        .with_segmenter(Box::new(sam.clone()))
        .with_detector(Box::new(Flaky { fail: Arc::new(AtomicBool::new(true)), dino: dino.clone() }));
    t.params.n2_min_inliers = 100000;
    t.params.n2b_enabled = false; // sin el nivel de color, para que la reidentificación dependa del detector
    let a2 = Obj { tex: scrambled(&a.tex, 7), cx: 330.0, cy: 90.0, angle_deg: 10.0 };
    let m = mask_of(&a2);
    *sam.truth.lock().unwrap() = vec![m.clone(), mask_of(&b)];
    *dino.boxes.lock().unwrap() =
        vec![Detection { prompt: "a card".into(), label: "card".into(), bbox: bbox_f(&m), score: 0.8 }];
    let next = frame(&[a2, b], 0, 22);
    let failed = t.update(&next);
    assert!(!failed.events.iter().any(|e| matches!(e,ObjectEvent::ObjectRemoved {object_id,..} if object_id=="a")));
    assert_eq!(t.objects().len(), 2);
    let recovered = t.update(&next);
    assert!(moved(&recovered.events, "a").is_some(), "{:?}", recovered.log);
    assert!((center_mm(&t, "a").0 - 660.0).abs() < 6.0);
}

#[test]
fn global_registration_keeps_physical_identity_when_llm_order_changes() {
    let (a, b) = two_objects();
    let sam = FakeSam::default();
    *sam.truth.lock().unwrap() = vec![mask_of(&a), mask_of(&b)];
    let first = json!({"regiones":[{"n":1,"objeto":"B","prompt":"square"},{"n":2,"objeto":"A","prompt":"card"}]});
    let second = json!({"regiones":[{"n":2,"objeto":"A","prompt":"card"},{"n":1,"objeto":"B","prompt":"square"}]});
    let mut t = Tracker::new(canvas(), Params::default())
        .with_segmenter(Box::new(sam))
        .with_llm(Box::new(MockLlm::new(second).then(first)));
    let f = frame(&[a, b], 0, 30);
    t.register_all(&f).unwrap();
    let old = t.objects();
    t.register_all(&f).unwrap();
    let new = t.objects();
    for o in old {
        let n = new.iter().find(|n| n.id == o.id).unwrap();
        assert_eq!(n.label, o.label);
        assert_eq!(n.center_mm, o.center_mm);
        assert_eq!(n.since_ns, o.since_ns);
    }
}
