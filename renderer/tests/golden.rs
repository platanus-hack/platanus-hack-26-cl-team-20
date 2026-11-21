//! Golden images en headless: renderiza los fixtures de `contracts/fixtures/` y compara con los PNG de
//! `tests/golden/` con tolerancia. `UPDATE_GOLDEN=1 cargo test --test golden` los regenera; si falta
//! una referencia, se escribe y el test pasa (primera corrida en una máquina nueva).
//! Las fuentes del SO cambian el texto: las referencias del repo se generaron en macOS.

use std::path::PathBuf;

use pcx_contracts::ToRenderer;
use pcx_renderer::engine::{Engine, EngineConfig, fit_homography, invert3};
use pcx_renderer::lut::Lut;
use pcx_renderer::scene::{Outcome, SceneState};

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
    let bad = a.pixels().zip(b.pixels()).filter(|(p, q)| p.0.iter().zip(q.0.iter()).any(|(x, y)| x.abs_diff(*y) > tol)).count();
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

/// La LUT por píxel construida desde la misma homografía da la misma imagen.
#[test]
fn lut_equivale_a_homografia() {
    let mut eng = engine();
    let mut s = SceneState::default();
    s.apply(fixture("scene.json"), 0.0);
    // Homografía de referencia: la de "encajar" en W×H (el fixture trae una para 1920×1080).
    s.transforms.clear();
    let a = render(&mut eng, &s, 0.25);

    let hinv = invert3(&fit_homography(s.surface.width_mm, s.surface.height_mm, W as f64, H as f64)).unwrap();
    let mut data = Vec::with_capacity((W * H * 2) as usize);
    for v in 0..H {
        for u in 0..W {
            let (x, y) = (u as f64 + 0.5, v as f64 + 0.5);
            let q = [hinv[0] * x + hinv[1] * y + hinv[2], hinv[3] * x + hinv[4] * y + hinv[5], hinv[6] * x + hinv[7] * y + hinv[8]];
            data.push((q[0] / q[2]) as f32);
            data.push((q[1] / q[2]) as f32);
        }
    }
    let lut_path = tmpdir("lut").join("lut.pcxlut");
    std::fs::write(&lut_path, Lut { width: W, height: H, data }.to_raw()).unwrap();
    // En vivo: se agrega como transform `kind: lut` (como lo mandaría el core en un scene_diff).
    s.transforms.push(serde_json::from_value(serde_json::json!({
        "from_frame": "table_mm", "to_frame": "proj:main", "kind": "lut",
        "lut_ref": lut_path.to_str().unwrap(), "version": 1
    })).unwrap());
    let b = render(&mut eng, &s, 0.25);
    let f = diff_frac(&a, &b, 40);
    assert!(f < 0.01, "LUT vs homografía: {:.2}% distinto", f * 100.0);

    // Quitar la LUT vuelve a la homografía.
    s.transforms.clear();
    let c = render(&mut eng, &s, 0.25);
    assert!(diff_frac(&a, &c, 2) < 0.0001);
}
