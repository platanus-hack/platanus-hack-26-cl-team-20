//! `CalibrationDrift` y `Pattern` hacia los tipos de `pcx-contracts` (v2.1).

use pcx_calib::{CalibrationDrift, GrayImage, Pattern};
use pcx_contracts::{Event, PatternImage, ToRenderer};

#[test]
fn drift_serializa_igual_que_el_evento_del_contrato() {
    let d = CalibrationDrift { from_frame: "table_mm".into(), to_frame: "proj:main".into(), residual_mm: 3.2, threshold_mm: 2.0 };
    let ours = serde_json::to_value(&d).unwrap();
    let ev = d.to_event();
    assert_eq!(ours, serde_json::to_value(&ev).unwrap());
    assert_eq!(ours["type"], "calibration_drift");
    assert_eq!(Event::from(&d), ev);
}

#[test]
fn pattern_a_mensaje_de_render() {
    let p = Pattern::new("graycode-x-03", GrayImage::new(4, 2, 0));
    let img = PatternImage { path: Some("/tmp/g.png".into()), data_uri: None };
    let msg = ToRenderer::Pattern(p.to_render(img, None));
    let v = serde_json::to_value(&msg).unwrap();
    assert_eq!(v["type"], "pattern");
    assert_eq!(v["pattern_id"], "graycode-x-03");
    assert_eq!(serde_json::from_value::<ToRenderer>(v).unwrap(), msg);
}
