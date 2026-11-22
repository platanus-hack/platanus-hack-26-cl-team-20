//! Fotos reales de la mesa (`data/mesa/2026-11-21` de `origin/daniel/consola`): calidad y latencias.
//!
//! `PCX_FOTOS_DIR=/tmp/pcx-fotos [PCX_MODELS_DIR=/tmp/pcx-models] [PCX_DIAG_DIR=…] \
//!  cargo test -p pcx-perception --release [--features onnx] --test pipeline_fotos -- --ignored --nocapture`
mod common;

use common::{calib_mesa, pct};
use image::RgbImage;
use pcx_perception::pipeline::{Pipeline, PipelineConfig};
use pcx_perception::pipeline_api::{PerceptionOut, Perceptor};
use std::collections::BTreeMap;
use std::path::PathBuf;

fn foto(n: u32) -> Option<RgbImage> {
    let dir = std::env::var("PCX_FOTOS_DIR").ok()?;
    Some(image::open(PathBuf::from(dir).join(format!("mesa-limpia-{n}.jpg"))).ok()?.to_rgb8())
}

fn diag(p: &Pipeline, name: &str) {
    let Ok(dir) = std::env::var("PCX_DIAG_DIR") else { return };
    let _ = std::fs::create_dir_all(&dir);
    if let Some(img) = p.debug_overlay() {
        let _ = img.save(PathBuf::from(&dir).join(name));
    }
    if let Some(d) = p.debug_distance() {
        let _ = d.save(PathBuf::from(&dir).join(name.replace(".jpg", "-dist.png")));
    }
}

/// Copia el parche `src` (x, y, w, h en px de cámara) a `dst` y tapa el lugar viejo con mesa tomada de `fill`.
fn mover(img: &RgbImage, src: (u32, u32, u32, u32), dst: Option<(u32, u32)>, fill: (u32, u32)) -> RgbImage {
    let mut out = img.clone();
    let patch = image::imageops::crop_imm(img, src.0, src.1, src.2, src.3).to_image();
    let table = image::imageops::crop_imm(img, fill.0, fill.1, src.2, src.3).to_image();
    image::imageops::replace(&mut out, &table, src.0 as i64, src.1 as i64);
    if let Some(d) = dst {
        image::imageops::replace(&mut out, &patch, d.0 as i64, d.1 as i64);
    }
    out
}

fn resumen(tag: &str, out: &PerceptionOut) {
    let st: Vec<String> = out.stages_ms.iter().map(|(n, v)| format!("{n} {v:.1}")).collect();
    let ids: Vec<&str> = out.upsert.iter().map(|o| o.id.as_str()).collect();
    println!("[{tag}] upsert {ids:?} · remove {:?} · {}", out.remove, st.join(" · "));
}

#[test]
#[ignore = "necesita PCX_FOTOS_DIR con las fotos reales"]
fn fotos_reales_secuencia_y_latencias() {
    let Some(f1) = foto(1) else {
        println!("sin PCX_FOTOS_DIR: salto");
        return;
    };
    let calib = calib_mesa();
    let mut cfg = PipelineConfig::default();
    if let Some(n) = std::env::var("PCX_SAM_THREADS").ok().and_then(|v| v.parse().ok()) {
        cfg.sam_threads = n;
    }
    let mut p = Pipeline::new(None, cfg);
    println!("{}", p.sam_status());
    let tag = if p.has_sam() { "sam" } else { "cascada" };
    let mut tiempos: BTreeMap<String, Vec<f64>> = BTreeMap::new();
    let mut anota = |clase: &str, out: &PerceptionOut| {
        for (n, v) in &out.stages_ms {
            tiempos.entry(format!("{clase}/{n}")).or_default().push(*v);
        }
    };

    // 1. registro: los objetos que ya están
    let out = p.on_clean(&f1, &calib, 1).unwrap();
    resumen("registro", &out);
    for l in p.log() {
        println!("   {l}");
    }
    anota("registro", &out);
    diag(&p, &format!("{tag}-1-registro.jpg"));
    let n0 = out.upsert.len();
    assert!(out.upsert.iter().all(|o| o.polygon_mm.len() <= 64 && o.polygon_mm.len() >= 3));
    for o in &out.upsert {
        println!("   {} {:?} conf {:.2} · {} puntos", o.id, o.center_mm, o.confidence, o.polygon_mm.len());
    }
    assert!(n0 >= 3, "esperaba encontrar los objetos de la mesa, hay {n0}");

    // 2. la misma escena otras tres veces: nada debe cambiar
    let mut ruido = 0;
    for n in 2..=4 {
        let out = p.on_clean(&foto(n).unwrap(), &calib, n as u64).unwrap();
        resumen(&format!("quieta {n}"), &out);
        ruido += out.upsert.len() + out.remove.len();
        anota("quieta", &out);
    }
    diag(&p, &format!("{tag}-2-quieta.jpg"));
    println!("escena quieta: {ruido} publicaciones de más en 3 fotos");

    // 3. la taza se mueve y vuelve, 8 veces; las cucharas se van y vuelven
    let taza = (672, 366, 76, 76);
    let cucharas = (728, 358, 88, 76);
    let libre = (490, 500);
    let movida = mover(&f1, taza, Some((486, 468)), libre);
    let sin_cucharas = mover(&f1, cucharas, None, libre);
    let ids = |p: &Pipeline| -> Vec<String> { p.snapshot().into_iter().map(|o| o.id).collect() };
    let antes = ids(&p);
    for k in 0..8 {
        let out = p.on_clean(&movida, &calib, 10 + k).unwrap();
        resumen("taza movida", &out);
        if k == 0 {
            for l in p.log() {
                println!("   {l}");
            }
            diag(&p, &format!("{tag}-3-taza-movida.jpg"));
        }
        anota("cambio", &out);
        let out = p.on_clean(&movida, &calib, 10 + k).unwrap();
        anota("quieta", &out);
        let out = p.on_clean(&f1, &calib, 20 + k).unwrap();
        resumen("taza vuelve", &out);
        anota("cambio", &out);
        let out = p.on_clean(&f1, &calib, 20 + k).unwrap();
        anota("quieta", &out);
    }
    let despues = ids(&p);
    let perdidos = antes.iter().filter(|i| !despues.contains(i)).count();
    println!("ids tras 16 movimientos: {} antes, {} después, {perdidos} cambiaron", antes.len(), despues.len());
    diag(&p, &format!("{tag}-4-tras-movimientos.jpg"));

    let out = p.on_clean(&sin_cucharas, &calib, 40).unwrap();
    resumen("sin cucharas (1)", &out);
    let primera = out.remove.len();
    let out = p.on_clean(&sin_cucharas, &calib, 41).unwrap();
    resumen("sin cucharas (2)", &out);
    for l in p.log() {
        println!("   {l}");
    }
    diag(&p, &format!("{tag}-5-sin-cucharas.jpg"));
    println!("histéresis: {primera} retirados en el primer ciclo, {} en el segundo", out.remove.len());
    let out = p.on_clean(&f1, &calib, 42).unwrap();
    resumen("cucharas vuelven", &out);
    diag(&p, &format!("{tag}-6-cucharas-vuelven.jpg"));

    println!("\n== latencias ({tag}), ms: etapa p50 / p95 (n) ==");
    for (k, v) in &tiempos {
        println!("{k:28} {:7.1} / {:7.1}  ({})", pct(v, 50.0), pct(v, 95.0), v.len());
    }
}
