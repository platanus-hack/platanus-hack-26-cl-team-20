//! Benchmark de los niveles 0–2 sobre un lienzo de 1280×720 (el tamaño del lienzo de `seguidor_v4`).
//! `cargo bench -p pcx-objects` (sin dependencias: mediana de N corridas con `Instant`).

use image::{Rgb, RgbImage};
use pcx_objects::geom::Canvas;
use pcx_objects::raster;
use pcx_objects::{CleanFrame, Params, Tracker};
use std::time::Instant;

const W: u32 = 1280;
const H: u32 = 720;

fn texture(seed: u64, w: u32, h: u32) -> RgbImage {
    let mut s = seed;
    let mut next = || {
        s = s.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
        (s >> 33) as u8
    };
    let cols: Vec<[u8; 3]> = (0..((w / 12 + 1) * (h / 12 + 1))).map(|_| [next(), next(), next()]).collect();
    RgbImage::from_fn(w, h, |x, y| Rgb(cols[((y / 12) * (w / 12 + 1) + x / 12) as usize]))
}

fn scene(objs: &[(&RgbImage, i64, i64)]) -> RgbImage {
    let mut img = RgbImage::from_fn(W, H, |x, y| {
        let v = (130 + x * 20 / W + y * 10 / H) as u8;
        Rgb([v + 15, v, v - 20])
    });
    for (t, x, y) in objs {
        image::imageops::overlay(&mut img, *t, *x, *y);
    }
    img
}

fn median(mut v: Vec<f64>) -> f64 {
    v.sort_by(|a, b| a.partial_cmp(b).unwrap());
    v[v.len() / 2]
}

fn bench(name: &str, n: usize, mut f: impl FnMut()) {
    f();
    let v: Vec<f64> = (0..n)
        .map(|_| {
            let t = Instant::now();
            f();
            t.elapsed().as_secs_f64() * 1e3
        })
        .collect();
    println!("{name:<44} {:>8.2} ms (mediana de {n})", median(v));
}

fn main() {
    let canvas = Canvas::covering(0.0, 0.0, 1280.0, 720.0, W);
    let a = texture(1, 160, 112);
    let b = texture(2, 144, 144);
    let f0 = CleanFrame { rgb: scene(&[(&a, 300, 250), (&b, 850, 400)]), t_ns: 0 };
    let quieto = CleanFrame { rgb: f0.rgb.clone(), t_ns: 1 };
    let movido = CleanFrame { rgb: scene(&[(&a, 420, 300), (&b, 850, 400)]), t_ns: 2 };
    let luz = CleanFrame {
        rgb: RgbImage::from_fn(W, H, |x, y| Rgb(f0.rgb.get_pixel(x, y).0.map(|v| v.saturating_add(40)))),
        t_ns: 3,
    };
    let mk = || {
        let mut t = Tracker::new(canvas, Params::default());
        let mut ma = raster::empty(W, H);
        let mut mb = raster::empty(W, H);
        for y in 250..362 {
            for x in 300..460 {
                ma.put_pixel(x, y, raster::ON);
            }
        }
        for y in 400..544 {
            for x in 850..994 {
                mb.put_pixel(x, y, raster::ON);
            }
        }
        t.add_object("a", "A", "a card", ma, &f0);
        t.add_object("b", "B", "a card", mb, &f0);
        t
    };
    println!("pcx-objects · lienzo {W}x{H}");
    bench("N0 diff + morfología (escena quieta)", 15, || {
        let mut t = mk();
        let r = t.update(&quieto);
        assert!(r.events.is_empty());
    });
    bench("N0+N1 cambio de luz → NCC «sigue igual»", 15, || {
        let mut t = mk();
        let r = t.update(&luz);
        assert!(r.events.is_empty());
    });
    bench("N0+N1+N2 objeto movido → ORB + RANSAC", 15, || {
        let mut t = mk();
        let r = t.update(&movido);
        assert_eq!(r.events.len(), 1);
    });
    let mut t = mk();
    bench("  solo construir el seguidor (add_object×2)", 15, || {
        t = mk();
    });
    let g = raster::gray(&movido.rgb);
    bench("  ORB detect+compute (imagen completa)", 15, || {
        let _ = pcx_objects::orb::detect_and_compute(&g, None, &Default::default());
    });
}
