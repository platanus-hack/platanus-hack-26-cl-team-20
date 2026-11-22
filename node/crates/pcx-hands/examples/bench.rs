//! ms por cuadro a 640×480 y 1280×720 con una imagen de prueba pegada en el cuadro (sin cámara).
//! `cargo run -p pcx-hands --release --example bench [-- coreml]` (CoreML requiere `--features coreml`).
use pcx_hands::*;
use std::time::Instant;

fn frame_with_hands(w: u32, h: u32) -> Vec<u8> {
    let src = image::open(concat!(env!("CARGO_MANIFEST_DIR"), "/testdata/right_hands.jpg")).unwrap().to_rgb8();
    let s = (w as f32 * 0.8 / src.width() as f32).min(h as f32 * 0.8 / src.height() as f32);
    let small = image::imageops::resize(
        &src,
        (src.width() as f32 * s) as u32,
        (src.height() as f32 * s) as u32,
        image::imageops::FilterType::Triangle,
    );
    let mut canvas = image::RgbImage::from_pixel(w, h, image::Rgb([90, 80, 70]));
    image::imageops::overlay(&mut canvas, &small, ((w - small.width()) / 2) as i64, ((h - small.height()) / 2) as i64);
    canvas.into_raw()
}

fn main() -> Result<(), HandsError> {
    let backend = if std::env::args().any(|a| a == "coreml") { Backend::CoreMl } else { Backend::Cpu };
    let threads = std::env::args()
        .find_map(|a| a.strip_prefix("threads=").and_then(|v| v.parse().ok()))
        .unwrap_or(DetectorConfig::default().intra_threads);
    let n = 100;
    for (w, h) in [(640u32, 480u32), (1280, 720)] {
        let rgb = frame_with_hands(w, h);
        let frame = Frame::packed(&rgb, w as usize, h as usize, PixelFormat::Rgb8)?;
        let cfg = EngineConfig {
            detector: DetectorConfig { backend, intra_threads: threads, ..Default::default() },
            homography: Some([0.5, 0.0, 0.0, 0.0, 0.5, 0.0, 0.0, 0.0, 1.0]),
            ..Default::default()
        };
        let mut eng = HandsEngine::from_cache(cfg)?;
        let mut meta = FrameMeta {
            camera_id: "bench".into(),
            seq: 0,
            t_capture_ns: 0,
            w: w as i64,
            h: h as i64,
            segment: None,
            format: Default::default(),
            stride: None,
        };
        // calentamiento
        for _ in 0..10 {
            meta.seq += 1;
            meta.t_capture_ns += 33_000_000;
            eng.process(&frame, &meta)?;
        }
        // seguimiento (2 manos ya seguidas: sin detección de palma)
        let (mut tot, mut lm_runs, mut palm_runs, mut hands) = (0.0, 0, 0, 0);
        for _ in 0..n {
            meta.seq += 1;
            meta.t_capture_ns += 33_000_000;
            let t0 = Instant::now();
            let out = eng.process(&frame, &meta)?;
            tot += t0.elapsed().as_secs_f64() * 1e3;
            lm_runs += out.timings.landmark_runs;
            palm_runs += out.timings.palm_runs;
            hands += out.hands.len();
        }
        let track_ms = tot / n as f64;
        // detección de palma sola y landmarks solos
        let det = &mut eng.tracker.det;
        let t0 = Instant::now();
        let mut palms = vec![];
        for _ in 0..n {
            palms = det.detect_palms(&frame)?;
        }
        let palm_ms = t0.elapsed().as_secs_f64() * 1e3 / n as f64;
        let roi = palms[0].roi();
        let t0 = Instant::now();
        for _ in 0..n {
            det.landmarks(&frame, &roi)?;
        }
        let lm_ms = t0.elapsed().as_secs_f64() * 1e3 / n as f64;
        println!(
            "{backend:?} {threads} hilos {w}x{h}: seguimiento 2 manos {track_ms:.2} ms/cuadro ({:.0} fps; {:.1} manos, {:.2} landmarks y {:.2} palmas por cuadro) | palma {palm_ms:.2} ms | landmarks {lm_ms:.2} ms/mano | detección completa 2 manos ≈ {:.2} ms",
            1000.0 / track_ms,
            hands as f64 / n as f64,
            lm_runs as f64 / n as f64,
            palm_runs as f64 / n as f64,
            palm_ms + 2.0 * lm_ms
        );
    }
    Ok(())
}
