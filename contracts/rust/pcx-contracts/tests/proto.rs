//! Paridad con Python de `events`, `commands`, `envelope`, `home`, `cloud` y los mensajes v2.1 de `render`:
//! cada fixture de `contracts/fixtures/` re-serializado en Rust debe ser igual a `fixtures/canonical/`.

use std::path::PathBuf;

use pcx_contracts::*;
use serde::de::DeserializeOwned;
use serde::Serialize;
use serde_json::{json, Value};

fn read(rel: &str) -> String {
    let p = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../fixtures").join(rel);
    std::fs::read_to_string(&p).unwrap_or_else(|e| panic!("{rel}: {e}"))
}

/// Decodifica el fixture como `T`, compara con el canónico de Python y hace ida y vuelta.
fn parity<T: DeserializeOwned + Serialize + PartialEq + std::fmt::Debug>(name: &str) -> T {
    let msg: T = serde_json::from_str(&read(name)).unwrap_or_else(|e| panic!("{name}: {e}"));
    let ours = serde_json::to_value(&msg).unwrap();
    let canonical: Value = serde_json::from_str(&read(&format!("canonical/{name}"))).unwrap();
    assert_eq!(ours, canonical, "{name}: Rust y Python no coinciden");
    let again: T = serde_json::from_value(ours).unwrap();
    assert_eq!(again, msg);
    msg
}

#[test]
fn envelopes_iguales_a_python() {
    let e: Envelope = parity("envelope_widget_pressed.json");
    assert!(matches!(e.payload, Payload::Event(Event::WidgetPressed { .. })));
    let e: Envelope = parity("envelope_frame_meta.json");
    let Payload::Event(Event::FrameMeta(fm)) = e.payload else { panic!("no es frame_meta") };
    assert_eq!((fm.segment.as_deref(), fm.format, fm.stride), (Some("pcx.cam.cam0"), PixelFormat::Bgr8, Some(3840)));
    let e: Envelope = parity("envelope_hands_gone.json");
    assert!(matches!(e.payload, Payload::Event(Event::HandsGone(HandsGone { last_hand_ns: None, .. }))));
}

#[test]
fn comandos_iguales_a_python() {
    let c: Command = parity("flow_guia_3_pasos.json");
    assert!(matches!(c, Command::RunFlow { .. }));
    let c: Command = parity("blank.json");
    assert_eq!(c.request_id(), "req-foto-limpia-0042");
}

#[test]
fn cloud_iguales_a_python() {
    let tc: CloudMessage = parity("tool_call.json");
    assert!(matches!(tc, CloudMessage::ToolCall { ref tool, .. } if tool == "project"));
    let ca: CloudMessage = parity("confirm_action.json");
    let CloudMessage::ConfirmAction(c) = ca else { panic!("no es confirm_action") };
    assert!(c.approved && c.by == ConfirmVia::Console);
    // por HTTP local se acepta sin `type`, con `by`/`operator` por defecto
    let c: ConfirmAction =
        serde_json::from_value(json!({"confirmation_id": "c", "approved": false, "action_digest": "a".repeat(64)}))
            .unwrap();
    assert_eq!((c.by, c.operator), (ConfirmVia::Console, None));
    // el nombre v2 `approve` ya no vale
    let old =
        json!({"type": "confirm_action", "confirmation_id": "c", "approve": true, "action_digest": "a".repeat(64)});
    assert!(serde_json::from_value::<CloudMessage>(old).is_err());
}

#[test]
fn render_v21_iguales_a_python() {
    let p: ToRenderer = parity("pattern.json");
    assert!(matches!(p, ToRenderer::Pattern(Pattern { ref image, until_ms: Some(_), .. }) if image.path.is_some()));
    let r: FromRenderer = parity("resync.json");
    assert_eq!(r, FromRenderer::Resync(Resync { have_rev: Some(12) }));
    let clear: ToRenderer = serde_json::from_value(json!({"type": "pattern_clear"})).unwrap();
    assert_eq!(serde_json::to_value(&clear).unwrap(), json!({"type": "pattern_clear"}));
    for bad in [json!({}), json!({"path": "/a.png", "data_uri": "data:,"})] {
        let v = json!({"type": "pattern", "pattern_id": "g", "image": bad});
        assert!(serde_json::from_value::<ToRenderer>(v).is_err());
    }
    let shown: FromRenderer =
        serde_json::from_value(json!({"type": "pattern_shown", "pattern_id": "g", "t_present_ms": 1.5})).unwrap();
    assert!(matches!(shown, FromRenderer::PatternShown(_)));
}

#[test]
fn trigger_rest_y_frame_meta_por_defecto() {
    let t: Event = serde_json::from_value(json!({"type": "touch_down",
        "touch": {"id": "t", "phase": "down", "point_mm": [1, 2], "trigger": "rest"}}))
    .unwrap();
    assert!(matches!(t, Event::TouchDown { touch: Touch { trigger: Trigger::Rest, .. } }));
    let fm: Event = serde_json::from_value(json!({"type": "frame_meta", "camera_id": "c", "seq": 1,
        "t_capture_ns": 2, "w": 4, "h": 2}))
    .unwrap();
    let Event::FrameMeta(fm) = fm else { panic!() };
    assert_eq!((fm.segment, fm.format, fm.stride), (None, PixelFormat::Bgr8, None));
    let bad =
        json!({"type": "frame_meta", "camera_id": "c", "seq": 1, "t_capture_ns": 2, "w": 4, "h": 2, "format": "yuyv"});
    assert!(serde_json::from_value::<Event>(bad).is_err());
}

#[test]
fn envelope_rechaza_version_y_type() {
    let mut v: Value = serde_json::from_str(&read("envelope_widget_pressed.json")).unwrap();
    v["v"] = 1.into();
    assert!(serde_json::from_value::<Envelope>(v.clone()).is_err());
    v["v"] = 2.into();
    v["type"] = "touch_down".into();
    assert!(serde_json::from_value::<Envelope>(v).is_err());
}

#[test]
fn flujo_invalido_y_home() {
    let bad =
        json!({"type": "run_flow", "request_id": "r", "flow": {"id": "f", "initial": "zzz", "states": [{"id": "a"}]}});
    assert!(serde_json::from_value::<Command>(bad).unwrap_err().to_string().contains("initial"));
    let cyc = json!({"id": "f", "initial": "a", "states": [
        {"id": "a", "transitions": [{"after_s": 0, "to": "b"}]}, {"id": "b", "transitions": [{"after_s": 0, "to": "a"}]}]});
    assert!(check_flow(&serde_json::from_value(cyc).unwrap()).iter().any(|e| e.contains("ciclo")));
    let old = json!({"type": "home_call", "request_id": "r", "domain": "light", "service": "turn_on", "requires_confirmation": true});
    assert!(serde_json::from_value::<Command>(old).is_err());
    let p = HomePolicy { allow: vec!["light.*".into()], confirm: vec!["lock.*".into()], ..Default::default() };
    let call = HomeCallBody { domain: "light".into(), service: "turn_on".into(), data: Json::new() };
    assert_eq!(decide(&p, &call), Decision::Allow);
    assert_eq!(decide(&HomePolicy::default(), &call), Decision::Deny);
    assert_eq!(action_digest(&call).len(), 64);
}
