//! Nivel 3 real: descarga Grounding DINO + MobileSAM a la caché del SO y corre sobre una imagen de prueba.
//! `PCX_TEST_IMAGE=/ruta.png cargo test -p pcx-objects --release --features onnx -- --ignored --nocapture`
//! Prompts por `PCX_TEST_PROMPTS` («a keyboard;a painting»).
#![cfg(feature = "onnx")]

use pcx_objects::models::{Detector, Segmenter};
use pcx_objects::onnx::{GroundingDino, MobileSam};
use pcx_objects::raster;
use std::time::Instant;

#[test]
#[ignore = "descarga ~250 MB de modelos"]
fn dino_mas_sam_en_imagen_de_prueba() {
    let path = std::env::var("PCX_TEST_IMAGE").expect("PCX_TEST_IMAGE");
    let prompts: Vec<String> = std::env::var("PCX_TEST_PROMPTS")
        .unwrap_or_else(|_| "a keyboard;a painting".into())
        .split(';')
        .map(str::to_string)
        .collect();
    let img = image::open(&path).unwrap().to_rgb8();
    let t = Instant::now();
    let mut dino = GroundingDino::load().unwrap();
    let mut sam = MobileSam::load().unwrap();
    println!("carga (con descarga si faltaba): {:.2}s", t.elapsed().as_secs_f64());
    for _ in 0..2 {
        let t = Instant::now();
        let dets = dino.detect(&img, &prompts, 0.25).unwrap();
        println!("dino {}x{}: {:.2}s, {} cajas", img.width(), img.height(), t.elapsed().as_secs_f64(), dets.len());
        for d in dets.iter().take(5) {
            println!("  {:?} '{}' {:.3} {:?}", d.prompt, d.label, d.score, d.bbox.map(|v| v.round()));
        }
        assert!(!dets.is_empty());
        let t = Instant::now();
        sam.set_image(&img).unwrap();
        let te = t.elapsed().as_secs_f64();
        let b = dets[0].bbox;
        let t = Instant::now();
        let (m, s) = sam.predict_box(b, Some(((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0))).unwrap();
        println!(
            "sam encoder {:.2}s · decoder {:.3}s · score {:.2} · área {} px",
            te,
            t.elapsed().as_secs_f64(),
            s,
            raster::count(&m)
        );
        assert!(raster::count(&m) > 50);
    }
}
