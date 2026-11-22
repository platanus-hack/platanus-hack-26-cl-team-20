//! Golden images en headless: renderiza los fixtures de `contracts/fixtures/` y compara con los PNG de
//! `tests/golden/` con tolerancia. `UPDATE_GOLDEN=1 cargo test --test golden` los regenera; si falta
//! una referencia, se escribe y el test pasa (primera corrida en una máquina nueva).
//! Las fuentes del SO cambian el texto: las referencias del repo se generaron en macOS.

use std::path::PathBuf;

use pcx_contracts::ToRenderer;
use pcx_renderer::engine::{Engine, EngineConfig};
use pcx_renderer::lut::{Lut, sha256_hex};
use pcx_renderer::scene::{Outcome, Projection, SceneState};

const W: u32 = 1920;
const H: u32 = 1080;

fn root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn tmpdir(name: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!("pcx-renderer-{name}-{}", std::process::id()));
    std::fs::create_dir_all(&d).unwrap();
    d
}

/// Engine con un asset `qr-celular.png` sintético (tablero) para el widget `image` del fixture.
fn engine() -> Engine {
    let assets = tmpdir("assets");
    let img = image::RgbaImage::from_fn(32, 32, |x, y| {
        if (x / 4 + y / 4) % 2 == 0 { image::Rgba([255, 255, 255, 255]) } else { image::Rgba([0, 0, 0, 255]) }
    });
    img.save(assets.join("qr-celular.png")).unwrap();
    Engine::headless(EngineConfig { assets_dir: assets, ..EngineConfig::default() })
}

fn fixture(name: &str) -> ToRenderer {
    let p = root().join("../contracts/fixtures").join(name);
    serde_json::from_str(&std::fs::read_to_string(p).unwrap()).unwrap()
}

fn render(eng: &mut Engine, state: &SceneState, t: f64) -> image::RgbaImage {
    let target = eng.create_target(W, H);
    let view = target.create_view(&Default::default());
    eng.render(state, t, 0.0, &view, W, H);
    eng.readback(&target)
}

/// Fracción de píxeles cuyo canal difiere más de `tol`.
fn diff_frac(a: &image::RgbaImage, b: &image::RgbaImage, tol: u8) -> f64 {
    assert_eq!(a.dimensions(), b.dimensions());
    let bad = a
        .pixels()
        .zip(b.pixels())
        .filter(|(p, q)| p.0.iter().zip(q.0.iter()).any(|(x, y)| x.abs_diff(*y) > tol))
        .count();
    bad as f64 / (a.width() * a.height()) as f64
}

fn check_golden(name: &str, img: &image::RgbaImage) {
    let p: PathBuf = root().join("tests/golden").join(format!("{name}.png"));
    if std::env::var_os("UPDATE_GOLDEN").is_some() || !p.exists() {
        img.save(&p).unwrap();
        eprintln!("golden escrito: {}", p.display());
        return;
    }
    let want = image::open(&p).unwrap().to_rgba8();
    let f = diff_frac(img, &want, 24);
    if f > 0.005 {
        let out = tmpdir("fail").join(format!("{name}.png"));
        img.save(&out).unwrap();
        panic!("{name}: {:.2}% de píxeles distintos (máx. 0,5%). Obtenido: {}", f * 100.0, out.display());
    }
}

fn non_black(img: &image::RgbaImage) -> usize {
    img.pixels().filter(|p| p.0[0] > 8 || p.0[1] > 8 || p.0[2] > 8).count()
}

#[test]
fn golden_fixtures() {
    let mut eng = engine();
    let mut s = SceneState::default();
    assert_eq!(s.apply(fixture("scene.json"), 0.0), Outcome::Applied);
    let a = render(&mut eng, &s, 0.25);
    assert!(non_black(&a) > 5000, "la escena salió casi negra: {}", non_black(&a));
    check_golden("scene", &a);

    assert_eq!(s.apply(fixture("scene_diff.json"), 0.0), Outcome::Applied);
    let b = render(&mut eng, &s, 0.25);
    check_golden("scene_diff", &b);
    assert!(diff_frac(&a, &b, 24) > 0.001, "el diff (quita la flecha, progreso 0.8) no cambió la imagen");

    // Mismo `t` → misma imagen (render determinista).
    let b2 = render(&mut eng, &s, 0.25);
    assert!(diff_frac(&b, &b2, 2) < 0.0001);
}

#[test]
fn efectos_de_ejemplo() {
    let mut eng = engine();
    let mut s = SceneState::default();
    let scene = serde_json::json!({
        "type": "scene", "rev": 1, "surface": {"width_mm": 400, "height_mm": 225},
        "widgets": [
            {"type": "effect", "id": "a", "shader": "halo_glow",
             "polygon_mm": [[60, 60], [160, 50], [170, 150], [70, 170]], "params": {"color": "#00E5FF"}},
            {"type": "effect", "id": "b", "shader": "marco_respira",
             "rect_mm": [220, 40, 150, 140], "params": {"color": [1.0, 0.3, 0.6]}},
            {"type": "label", "id": "l", "anchor": {"point_mm": [235, 100]}, "text": "z encima", "size_mm": 10, "z": 1}
        ]
    });
    s.apply(serde_json::from_value(scene).unwrap(), 0.0);
    let img = render(&mut eng, &s, 0.4);
    assert!(non_black(&img) > 5000);
    check_golden("efectos", &img);
}

/// LUT v2 (table_mm → px de salida, `integer_centers`) construida desde la misma homografía: misma imagen.
/// Con sha256 equivocado no se usa y queda la homografía de respaldo.
#[test]
fn lut_equivale_a_homografia() {
    let mut eng = engine();
    let mut s = SceneState::default();
    s.apply(fixture("scene.json"), 0.0);
    // Referencia: solo la homografía del fixture (sin la LUT, cuyo archivo no existe aquí).
    s.transforms.retain(|t| matches!(t, pcx_contracts::Transform::Homography(_)));
    let a = render(&mut eng, &s, 0.25);
    let Projection::Homography(hm) = s.projection() else { panic!() };

    let step = 2.0;
    let (gw, gh) = ((s.surface.width_mm / step) as u32 + 1, (s.surface.height_mm / step) as u32 + 1);
    let mut data = Vec::with_capacity((gw * gh * 2) as usize);
    for v in 0..gh {
        for u in 0..gw {
            let (x, y) = (u as f64 * step, v as f64 * step);
            let q = [hm[0] * x + hm[1] * y + hm[2], hm[3] * x + hm[4] * y + hm[5], hm[6] * x + hm[7] * y + hm[8]];
            // Centro del px (0,0) en (0,0): la homografía del fixture usa bordes, de ahí el -0.5.
            data.push((q[0] / q[2] - 0.5) as f32);
            data.push((q[1] / q[2] - 0.5) as f32);
        }
    }
    // La malla de la LUT se usa de verdad: la misma LUT corrida 60 px a la derecha cambia la imagen.
    let shifted: Vec<f32> = data.iter().enumerate().map(|(i, v)| if i % 2 == 0 { v + 60.0 } else { *v }).collect();
    let sh_bytes = Lut { width: gw, height: gh, data: shifted }.to_npy();
    let sh_path = tmpdir("lut").join("lut_corrida.npy");
    std::fs::write(&sh_path, &sh_bytes).unwrap();
    let bytes = Lut { width: gw, height: gh, data }.to_npy();
    let path = tmpdir("lut").join("lut.npy");
    std::fs::write(&path, &bytes).unwrap();
    let lut_t = |version: i64, sha: &str| -> pcx_contracts::Transform {
        serde_json::from_value(serde_json::json!({
            "from_frame": "table_mm", "to_frame": "proj:main", "kind": "lut", "version": version,
            "lut": {"format": "npy_f32_hw2", "width": gw, "height": gh, "units": "output_px",
                    "sampling": "integer_centers", "grid_step": step, "path": path.to_str().unwrap(), "sha256": sha}
        }))
        .unwrap()
    };
    // En vivo, como lo mandaría el core en un scene_diff.
    s.transforms.push(lut_t(5, &sha256_hex(&bytes)));
    assert!(matches!(s.projection(), Projection::Lut { .. }));
    let b = render(&mut eng, &s, 0.25);
    let f = diff_frac(&a, &b, 40);
    assert!(f < 0.01, "LUT vs homografía: {:.2}% distinto", f * 100.0);

    s.transforms.pop();
    let mut corrida = lut_t(5, &sha256_hex(&sh_bytes));
    if let pcx_contracts::Transform::Lut(l) = &mut corrida {
        l.lut.path = sh_path.to_str().unwrap().into();
    }
    s.transforms.push(corrida);
    let d = render(&mut eng, &s, 0.25);
    assert!(diff_frac(&a, &d, 40) > 0.005, "la LUT corrida no cambió la imagen");

    // sha256 equivocado: se rechaza y se usa la homografía de respaldo.
    s.transforms.pop();
    s.transforms.push(lut_t(6, &"0".repeat(64)));
    let c = render(&mut eng, &s, 0.25);
    assert!(diff_frac(&a, &c, 2) < 0.0001);
}

#[test]
fn pattern_native_pixels_without_warp_or_widgets() {
    use base64::Engine as _;
    use pcx_renderer::runtime::{Runtime, now_epoch_ms};
    use serde_json::json;
    let assets = tmpdir("pattern");
    let checker = image::RgbaImage::from_fn(160, 90, |x, y| {
        if (x + y) % 2 == 0 { image::Rgba([255, 32, 16, 255]) } else { image::Rgba([0, 160, 255, 255]) }
    });
    checker.save(assets.join("calib.png")).unwrap();
    let mut eng = Engine::headless(EngineConfig { assets_dir: assets.clone(), ..EngineConfig::default() });
    let target = eng.create_target(320, 180);
    let view = target.create_view(&Default::default());
    let mut rt = Runtime::offline();
    rt.apply(fixture("scene.json")); // Incluye LUT, homografía y widgets: ninguno puede afectar al patrón.
    let pattern = |id: &str, image: serde_json::Value, until: Option<f64>| {
        serde_json::from_value(json!({"type":"pattern", "pattern_id":id, "image":image, "until_ms":until})).unwrap()
    };
    rt.apply(pattern("path", json!({"path":assets.join("calib.png")}), None));
    rt.draw(&mut eng, &view, 320, 180, Some(0.25));
    eng.wait_idle();
    rt.after_present(now_epoch_ms());
    let actual = eng.readback(&target);
    let expected = image::RgbaImage::from_fn(320, 180, |x, y| {
        if x < 160 && y < 90 { *checker.get_pixel(x, y) } else { image::Rgba([0, 0, 0, 255]) }
    });
    assert_eq!(actual, expected, "no debe escalar, filtrar ni aplicar warp");
    check_golden("pattern", &actual);
    assert_eq!(rt.state.unpresented, vec![12], "el patrón no presenta la escena");

    // El core puede reescribir la misma ruta para el siguiente patrón: no reutilizar texels viejos.
    image::RgbaImage::from_pixel(160, 90, image::Rgba([0, 255, 0, 255])).save(assets.join("calib.png")).unwrap();
    rt.apply(pattern("rewritten", json!({"path":assets.join("calib.png")}), None));
    rt.draw(&mut eng, &view, 320, 180, None);
    assert_eq!(eng.readback(&target).get_pixel(40, 40).0, [0, 255, 0, 255]);
    checker.save(assets.join("calib.png")).unwrap();

    let uri = format!(
        "data:image/png;base64,{}",
        base64::engine::general_purpose::STANDARD.encode(std::fs::read(assets.join("calib.png")).unwrap())
    );
    rt.apply(pattern("data", json!({"data_uri":uri}), None));
    rt.draw(&mut eng, &view, 320, 180, None);
    assert_eq!(eng.readback(&target), expected);
    // Salida más pequeña: recorta, jamás reescala.
    let small = eng.create_target(80, 45);
    rt.draw(&mut eng, &small.create_view(&Default::default()), 80, 45, None);
    assert_eq!(eng.readback(&small), image::imageops::crop_imm(&checker, 0, 0, 80, 45).to_image());

    // blank tiene prioridad y no destruye el patrón.
    rt.apply(serde_json::from_value(json!({"type":"blank", "request_id":"b", "ms":20, "reason":"test"})).unwrap());
    rt.draw(&mut eng, &view, 320, 180, None);
    assert_eq!(non_black(&eng.readback(&target)), 0);
    rt.after_present(now_epoch_ms());
    std::thread::sleep(std::time::Duration::from_millis(25));
    rt.draw(&mut eng, &view, 320, 180, None);
    assert_eq!(eng.readback(&target), expected);
    rt.after_present(now_epoch_ms());

    rt.apply(serde_json::from_value(json!({"type":"pattern_clear"})).unwrap());
    rt.draw(&mut eng, &view, 320, 180, Some(0.25));
    rt.after_present(now_epoch_ms());
    let scene = eng.readback(&target);
    assert_ne!(scene, expected);
    assert!(rt.state.unpresented.is_empty());
    // Vencido antes del primer cuadro: nunca muestra ni confirma el patrón.
    rt.apply(pattern("expired", json!({"path":assets.join("calib.png")}), Some(now_epoch_ms() - 1.0)));
    rt.draw(&mut eng, &view, 320, 180, Some(0.25));
    assert_eq!(eng.readback(&target), scene);
    // Una ruta remota inválida produce negro, aunque exista escena por debajo.
    rt.apply(pattern("rejected", json!({"path":"https://example.com/calib.png"}), None));
    rt.draw(&mut eng, &view, 320, 180, None);
    assert_eq!(non_black(&eng.readback(&target)), 0);
}
