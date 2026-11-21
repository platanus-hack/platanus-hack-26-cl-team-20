//! Los fixtures de `contracts/fixtures/` se leen en Rust y su re-serialización debe ser idéntica a la forma
//! canónica que produce Python (`fixtures/canonical/`, regenerada por `python -m pcx_contracts.export`).

use std::path::PathBuf;

use pcx_contracts::*;
use serde_json::Value;

fn fixtures_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../fixtures")
}

fn read(rel: &str) -> String {
    std::fs::read_to_string(fixtures_dir().join(rel)).unwrap_or_else(|e| panic!("{rel}: {e}"))
}

fn check_to_renderer(name: &str) -> ToRenderer {
    let msg: ToRenderer = serde_json::from_str(&read(name)).expect(name);
    let ours = serde_json::to_value(&msg).unwrap();
    let canonical: Value = serde_json::from_str(&read(&format!("canonical/{name}"))).unwrap();
    assert_eq!(ours, canonical, "{name}: Rust y Python no coinciden");
    // ida y vuelta
    let again: ToRenderer = serde_json::from_value(ours).unwrap();
    assert_eq!(again, msg);
    msg
}

#[test]
fn scene_fixture_matches_python() {
    let ToRenderer::Scene(s) = check_to_renderer("scene.json") else { panic!("no es scene") };
    assert_eq!(s.rev, 12);
    assert_eq!(s.widgets.len(), 7);
    // LUT (v4, sin aproximación) gana la selección; la matriz sale por fallback de la homografía.
    let (i, t) = select_projector_transform(&s.transforms, "main").unwrap();
    assert_eq!(i, 1);
    assert!(matches!(t, Transform::Lut(l) if l.lut.width == 800 && l.lut.height == 500));
    assert_eq!(
        Transform::projector_matrix(&s.transforms, "main"),
        Some([2.16, 0.0, 96.0, 0.0, 2.16, 0.0, 0.0, 0.0, 1.0])
    );
    match &s.widgets[0] {
        Widget::Label(l) => {
            assert_eq!(l.common.anchor.as_ref().unwrap().resolved_point(), Some(PointMM(110.0, 314.0)));
            assert!(l.text.contains('☕'));
        }
        w => panic!("esperaba label, vino {w:?}"),
    }
    assert!(matches!(&s.widgets[6], Widget::Effect(e) if e.shader == "ripple"));
}

#[test]
fn scene_diff_fixture_matches_python() {
    let ToRenderer::SceneDiff(d) = check_to_renderer("scene_diff.json") else { panic!("no es scene_diff") };
    assert_eq!((d.rev, d.base_rev), (13, 12));
    assert_eq!(d.remove, vec!["flecha".to_string()]);
    let Widget::Button(b) = &d.upsert[0] else { panic!() };
    // defaults de OnPress iguales a Python
    let op = b.common.on_press.as_ref().unwrap();
    assert_eq!(op.triggers, vec![Trigger::Dwell, Trigger::Pinch, Trigger::Api]);
    assert_eq!(op.dwell_ms, 800);
}

#[test]
fn widgets_inside_other_fixtures_parse() {
    // tool_call.json: args.widgets del comando `project`
    let call: Value = serde_json::from_str(&read("tool_call.json")).unwrap();
    let ws: Vec<Widget> = serde_json::from_value(call["args"]["widgets"].clone()).unwrap();
    assert_eq!(ws.len(), 2);
    assert_eq!(ws[0].id(), "halo-llaves");
    // flow_guia_3_pasos.json: widgets de cada acción `project`
    let flow: Value = serde_json::from_str(&read("canonical/flow_guia_3_pasos.json")).unwrap();
    let mut n = 0;
    for st in flow["flow"]["states"].as_array().unwrap() {
        for a in st["on_enter"].as_array().unwrap() {
            if a["type"] == "project" {
                let ws: Vec<Widget> = serde_json::from_value(a["widgets"].clone()).unwrap();
                n += ws.len();
            }
        }
    }
    assert_eq!(n, 4);
}

#[test]
fn renderer_messages_roundtrip() {
    for raw in [
        r#"{"type":"presented","rev":12,"t_present_ms":1795231660130.5}"#,
        r#"{"type":"stats","fps":59.9,"frame_ms_p50":16.6,"frame_ms_p95":17.9}"#,
        r#"{"type":"blanked","request_id":"req-foto-limpia-0042","t_start_ms":1795231660200.0,"t_end_ms":1795231660500.0}"#,
    ] {
        let m: FromRenderer = serde_json::from_str(raw).unwrap();
        let back: FromRenderer = serde_json::from_str(&serde_json::to_string(&m).unwrap()).unwrap();
        assert_eq!(m, back);
    }
}

#[test]
fn blank_to_renderer_roundtrip() {
    let m: ToRenderer =
        serde_json::from_str(r#"{"type":"blank","request_id":"req-foto-limpia-0042","ms":300,"reason":"clean_photo"}"#)
            .unwrap();
    let ToRenderer::Blank(b) = &m else { panic!("no es blank") };
    assert_eq!(b.ms, 300);
    let back: ToRenderer = serde_json::from_str(&serde_json::to_string(&m).unwrap()).unwrap();
    assert_eq!(m, back);
}

/// `fixtures/invalid/cases.json`: los mismos casos que Python y ambos JSON Schema rechazan.
#[test]
fn shared_negative_fixtures_are_rejected() {
    let cases: Vec<Value> = serde_json::from_str(&read("invalid/cases.json")).unwrap();
    assert!(cases.len() >= 20);
    for c in &cases {
        let v = c["value"].clone();
        let why = c["why"].as_str().unwrap();
        let res = match c["root"].as_str().unwrap() {
            "widget" => serde_json::from_value::<Widget>(v).map(|_| ()),
            "transform" => serde_json::from_value::<Transform>(v).map(|_| ()),
            "to_renderer" => serde_json::from_value::<ToRenderer>(v).map(|_| ()),
            "from_renderer" => serde_json::from_value::<FromRenderer>(v).map(|_| ()),
            r => panic!("root desconocido {r}"),
        };
        assert!(res.is_err(), "Rust acepta un caso inválido: {why}");
    }
}

/// `fixtures/selection_cases.json`: misma selección LUT > homografía que Python.
#[test]
fn projector_transform_selection_matches_python() {
    let cases: Vec<Value> = serde_json::from_str(&read("selection_cases.json")).unwrap();
    for c in &cases {
        let name = c["name"].as_str().unwrap();
        let pid = c["projector_id"].as_str().unwrap();
        let ts: Vec<Transform> = serde_json::from_value(c["transforms"].clone()).expect(name);
        let idx = select_projector_transform(&ts, pid).map(|(i, _)| i as u64);
        assert_eq!(idx, c["selected_index"].as_u64(), "{name}");
        let m: Option<[f64; 9]> = serde_json::from_value(c["matrix"].clone()).unwrap();
        assert_eq!(Transform::projector_matrix(&ts, pid), m, "{name}");
    }
}
