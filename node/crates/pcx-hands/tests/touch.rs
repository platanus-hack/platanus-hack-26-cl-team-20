//! Máquina de toque con secuencias sintéticas (mano plantilla de `engine/playground/hands/model.py`).
use pcx_hands::{HandsEvent, TouchConfig, TouchHand, TouchPhase, TouchTracker, TouchTrigger, Zone};

/// Mano derecha abierta, en unidades de muñeca→nudillo medio = 1; los dedos apuntan a −y.
const TEMPLATE: [[f64; 2]; 21] = [
    [0.00, 0.00],
    [-0.35, -0.15],
    [-0.60, -0.35],
    [-0.80, -0.55],
    [-0.95, -0.70],
    [-0.25, -0.95],
    [-0.30, -1.35],
    [-0.32, -1.60],
    [-0.34, -1.80],
    [0.00, -1.00],
    [0.00, -1.45],
    [0.00, -1.72],
    [0.00, -1.95],
    [0.22, -0.93],
    [0.27, -1.33],
    [0.30, -1.57],
    [0.32, -1.77],
    [0.42, -0.80],
    [0.52, -1.10],
    [0.58, -1.30],
    [0.62, -1.47],
];

/// Landmarks con la punta del índice en (cx, cy); `scale` = muñeca→nudillo medio en px.
fn hand(cx: f64, cy: f64, scale: f64, pinch: bool) -> [[f64; 2]; 21] {
    let mut t = TEMPLATE;
    if pinch {
        t[4] = [t[8][0] + 0.05, t[8][1] + 0.05];
        t[3] = [(t[2][0] + t[4][0]) / 2.0, (t[2][1] + t[4][1]) / 2.0];
    }
    t.map(|p| [(p[0] - t[8][0]) * scale + cx, (p[1] - t[8][1]) * scale + cy])
}

fn th(id: &str, lm: [[f64; 2]; 21]) -> TouchHand {
    TouchHand {
        id: id.into(),
        landmarks_px: lm,
    }
}

const MS: i64 = 1_000_000;
const T0: i64 = 1_700_000_000_000_000_000;

fn only_pinch() -> TouchConfig {
    TouchConfig {
        use_rest: false,
        use_dwell: false,
        ..Default::default()
    }
}

fn phases(ev: &[HandsEvent]) -> Vec<&'static str> {
    ev.iter()
        .map(|e| match e {
            HandsEvent::TouchDown { .. } => "down",
            HandsEvent::TouchMove { .. } => "move",
            HandsEvent::TouchUp { .. } => "up",
            HandsEvent::Dwell { .. } => "dwell",
            HandsEvent::HandsGone { .. } => "gone",
        })
        .collect()
}

#[test]
fn pinch_down_move_up_with_debounce() {
    let mut tt = TouchTracker::new(only_pinch(), None);
    let mut all = vec![];
    // abierta 3 cuadros, pinch 5 cuadros moviéndose, abierta 3
    for i in 0..3 {
        all.extend(tt.update(
            &[th("h1", hand(100.0, 100.0, 80.0, false))],
            T0 + i * 40 * MS,
        ));
    }
    for i in 3..8 {
        let x = 100.0 + (i - 3) as f64 * 10.0;
        let ev = tt.update(&[th("h1", hand(x, 100.0, 80.0, true))], T0 + i * 40 * MS);
        if i == 3 {
            assert!(ev.is_empty(), "antirrebote: 1 cuadro no basta");
        }
        all.extend(ev);
    }
    for i in 8..11 {
        all.extend(tt.update(
            &[th("h1", hand(140.0, 100.0, 80.0, false))],
            T0 + i * 40 * MS,
        ));
    }
    let p = phases(&all);
    assert_eq!(p.first(), Some(&"down"));
    assert_eq!(p.last(), Some(&"up"));
    assert!(p.iter().filter(|x| **x == "move").count() >= 2, "{p:?}");
    if let HandsEvent::TouchDown {
        touch,
        t_capture_ns,
    } = &all[0]
    {
        assert_eq!(touch.trigger, TouchTrigger::Pinch);
        assert_eq!(touch.phase, TouchPhase::Down);
        assert_eq!(*t_capture_ns, T0 + 4 * 40 * MS);
        assert!(touch.point_mm.is_none() && touch.to_contract().is_none());
    }
}

#[test]
fn pinch_hysteresis_holds_between_on_and_off() {
    let mut tt = TouchTracker::new(only_pinch(), None);
    let mut lm = hand(100.0, 100.0, 80.0, true);
    for i in 0..3 {
        tt.update(&[th("h", lm)], T0 + i * 40 * MS);
    }
    assert!(tt.pinch("h"));
    // separación entre pinch_on (0,30) y pinch_off (0,42): sigue en pinch
    let palm = pcx_hands::touch::palm_size(&lm);
    lm[4] = [lm[8][0] + 0.36 * palm, lm[8][1]];
    for i in 3..8 {
        assert!(tt.update(&[th("h", lm)], T0 + i * 40 * MS).is_empty());
    }
    assert!(tt.pinch("h"));
}

#[test]
fn lost_hand_releases_touch() {
    let mut tt = TouchTracker::new(only_pinch(), None);
    for i in 0..3 {
        tt.update(&[th("h", hand(50.0, 50.0, 80.0, true))], T0 + i * 40 * MS);
    }
    assert!(tt
        .update(&[], T0 + 300 * MS)
        .iter()
        .all(|e| !matches!(e, HandsEvent::TouchUp { .. })));
    let ev = tt.update(&[], T0 + 500 * MS);
    match &ev[0] {
        HandsEvent::TouchUp { touch, .. } => assert_eq!(touch.reason, "hand_lost"),
        e => panic!("{e:?}"),
    }
}

#[test]
fn dwell_progress_then_touch_then_release_on_move() {
    let cfg = TouchConfig {
        use_pinch: false,
        use_rest: false,
        ..Default::default()
    };
    let h = [1.0, 0.0, 10.0, 0.0, 1.0, 20.0, 0.0, 0.0, 1.0]; // px → mm: traslación
    let mut tt = TouchTracker::new(cfg, Some(h));
    let mut ev = vec![];
    for i in 0..20 {
        ev.extend(tt.update(
            &[th("h", hand(200.0, 150.0, 80.0, false))],
            T0 + i * 40 * MS,
        ));
    }
    let dw: Vec<f64> = ev
        .iter()
        .filter_map(|e| {
            if let HandsEvent::Dwell { progress, .. } = e {
                Some(*progress)
            } else {
                None
            }
        })
        .collect();
    assert_eq!(dw, vec![0.25, 0.5, 0.75, 1.0]);
    let down = ev.iter().find_map(|e| {
        if let HandsEvent::TouchDown { touch, .. } = e {
            Some(touch.clone())
        } else {
            None
        }
    });
    let down = down.expect("dwell abre un toque");
    assert_eq!(down.trigger, TouchTrigger::Dwell);
    assert_eq!(down.point_mm, Some([210.0, 170.0]));
    let c = serde_json::to_value(down.to_contract().unwrap()).unwrap();
    assert_eq!(c["trigger"], "dwell");
    // moverse más que dwell_release_px suelta
    let mut ev = vec![];
    for i in 20..26 {
        ev.extend(tt.update(
            &[th("h", hand(260.0, 150.0, 80.0, false))],
            T0 + i * 40 * MS,
        ));
    }
    assert!(
        ev.iter()
            .any(|e| matches!(e, HandsEvent::TouchUp { touch, .. } if touch.reason == "moved")),
        "{ev:?}"
    );
}

#[test]
fn moving_finger_never_dwells() {
    let cfg = TouchConfig {
        use_pinch: false,
        use_rest: false,
        ..Default::default()
    };
    let mut tt = TouchTracker::new(cfg, None);
    for i in 0..40 {
        let x = 100.0 + i as f64 * 8.0;
        assert!(tt
            .update(&[th("h", hand(x, 100.0, 80.0, false))], T0 + i * 40 * MS)
            .is_empty());
    }
}

#[test]
fn rest_by_palm_size_with_reference() {
    // apoyada: palma ≈ referencia; levantada: 20 % más grande
    let cfg = TouchConfig {
        use_pinch: false,
        use_dwell: false,
        hand_ref: Some(80.0),
        ..Default::default()
    };
    let mut tt = TouchTracker::new(cfg, None);
    let up = pcx_hands::touch::palm_size(&hand(0.0, 0.0, 80.0, false));
    let s_hover = 80.0 * 80.0 * 1.2 / up;
    let s_rest = 80.0 * 80.0 / up;
    let mut ev = vec![];
    for i in 0..3 {
        ev.extend(tt.update(
            &[th("h", hand(100.0, 100.0, s_hover, false))],
            T0 + i * 40 * MS,
        ));
    }
    assert!(ev.is_empty());
    for i in 3..6 {
        ev.extend(tt.update(
            &[th("h", hand(100.0, 100.0, s_rest, false))],
            T0 + i * 40 * MS,
        ));
    }
    assert!(
        matches!(&ev[0], HandsEvent::TouchDown { touch, .. } if touch.trigger == TouchTrigger::Rest)
    );
    // 1,09 está entre ratio_down y ratio_up: no suelta (histéresis)
    for i in 6..10 {
        let e = tt.update(
            &[th("h", hand(100.0, 100.0, s_rest * 1.09, false))],
            T0 + i * 40 * MS,
        );
        assert!(!e.iter().any(|e| matches!(e, HandsEvent::TouchUp { .. })));
    }
    let mut e = vec![];
    for i in 10..13 {
        e.extend(tt.update(
            &[th("h", hand(100.0, 100.0, s_hover, false))],
            T0 + i * 40 * MS,
        ));
    }
    assert!(e.iter().any(|e| matches!(e, HandsEvent::TouchUp { .. })));
}

#[test]
fn excluded_zone_blocks_touch_start() {
    let zone = Zone {
        name: "cocodrilo".into(),
        polygon: vec![[0.0, 0.0], [50.0, 0.0], [50.0, 50.0], [0.0, 50.0]],
    };
    let cfg = TouchConfig {
        use_rest: false,
        use_dwell: false,
        zones: vec![zone],
        ..Default::default()
    };
    let id = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0];
    let mut tt = TouchTracker::new(cfg, Some(id));
    for i in 0..6 {
        assert!(tt
            .update(&[th("h", hand(20.0, 20.0, 80.0, true))], T0 + i * 40 * MS)
            .is_empty());
    }
    let mut tt2 = TouchTracker::new(tt.cfg.clone(), Some(id));
    let ev: Vec<_> = (0..3)
        .flat_map(|i| tt2.update(&[th("h", hand(200.0, 200.0, 80.0, true))], T0 + i * 40 * MS))
        .collect();
    assert_eq!(phases(&ev), vec!["down"]);
}

#[test]
fn episode_ends_after_calm() {
    let mut tt = TouchTracker::new(only_pinch(), None);
    assert!(
        tt.update(&[], T0).is_empty(),
        "sin episodio no hay HandsGone"
    );
    tt.update(&[th("h", hand(10.0, 10.0, 80.0, false))], T0 + 100 * MS);
    tt.update(&[th("h", hand(10.0, 10.0, 80.0, false))], T0 + 140 * MS);
    assert!(tt.update(&[], T0 + 400 * MS).is_empty(), "0,26 s: aún no");
    let ev = tt.update(&[], T0 + 600 * MS);
    match ev.last() {
        Some(HandsEvent::HandsGone {
            episode_start_ns,
            last_hand_ns,
            t_capture_ns,
        }) => {
            assert_eq!(*episode_start_ns, T0 + 100 * MS);
            assert_eq!(*last_hand_ns, T0 + 140 * MS);
            assert_eq!(*t_capture_ns, T0 + 600 * MS);
        }
        e => panic!("{e:?}"),
    }
    assert!(
        tt.update(&[], T0 + 900 * MS).is_empty(),
        "una sola vez por episodio"
    );
}
