//! Recarga en caliente de efectos: un shader roto se rechaza (naga) y sigue el anterior.

use std::time::Duration;

use pcx_renderer::engine::{Engine, EngineConfig};
use pcx_renderer::scene::SceneState;

const OK_RED: &str = "// params: k=1.0\nfn effect(p: vec2<f32>) -> vec4<f32> { return vec4<f32>(fx_param(0), 0.0, 0.0, 1.0); }\n";
const OK_GREEN: &str = "fn effect(p: vec2<f32>) -> vec4<f32> { return vec4<f32>(0.0, 1.0, 0.0, 1.0); }\n";
const BROKEN: &str = "fn effect(p: vec2<f32>) -> vec4<f32> { return vec3<f32>(1.0); }\n";

fn center_pixel(eng: &mut Engine, s: &SceneState) -> [u8; 4] {
    let target = eng.create_target(200, 100);
    let view = target.create_view(&Default::default());
    eng.render(s, 0.0, 0.0, &view, 200, 100);
    eng.readback(&target).get_pixel(100, 50).0
}

fn write(path: &std::path::Path, src: &str) {
    // Asegura un mtime distinto aunque el FS tenga resolución gruesa.
    std::thread::sleep(Duration::from_millis(20));
    std::fs::write(path, src).unwrap();
    let t = std::time::SystemTime::now() + Duration::from_secs(1 + src.len() as u64 % 7);
    std::fs::File::options().write(true).open(path).unwrap().set_modified(t).unwrap();
}

#[test]
fn shader_roto_se_rechaza_y_sigue_el_anterior() {
    let dir = std::env::temp_dir().join(format!("pcx-fx-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let f = dir.join("prueba.wgsl");
    std::fs::write(&f, OK_RED).unwrap();
    let mut eng = Engine::headless(EngineConfig { effects_dir: dir.clone(), ..EngineConfig::default() });
    assert!(eng.effects.shaders.contains_key("prueba"));

    let mut s = SceneState::default();
    let scene = serde_json::json!({"type": "scene", "rev": 1, "surface": {"width_mm": 200, "height_mm": 100},
        "widgets": [{"type": "effect", "id": "e", "shader": "prueba", "rect_mm": [0, 0, 200, 100]}]});
    s.apply(serde_json::from_value(scene).unwrap(), 0.0);
    let px = center_pixel(&mut eng, &s);
    assert!(px[0] > 200 && px[1] < 30, "{px:?}");

    write(&f, BROKEN);
    let r = eng.poll_effects();
    assert!(matches!(r.as_slice(), [(n, Err(_))] if n == "prueba"), "{r:?}");
    assert!(eng.effects.errors["prueba"].contains("effect"), "{}", eng.effects.errors["prueba"]);
    // Sigue el anterior: sigue rojo.
    let px = center_pixel(&mut eng, &s);
    assert!(px[0] > 200 && px[1] < 30, "{px:?}");

    write(&f, OK_GREEN);
    let r = eng.poll_effects();
    assert!(matches!(r.as_slice(), [(_, Ok(()))]), "{r:?}");
    assert!(eng.effects.errors.is_empty());
    let px = center_pixel(&mut eng, &s);
    assert!(px[1] > 200 && px[0] < 30, "{px:?}");
    let _ = std::fs::remove_dir_all(dir);
}
