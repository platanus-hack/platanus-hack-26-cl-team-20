//! Calibración real con `pcx-calib` (modo precise: grilla pcx6 → homografía, gray code → LUT).
//! - `Sink`: cada patrón se escribe como PNG y se muestra con un widget `image` a pantalla completa (1:1); se espera
//!   `presented` (el renderer de manu/plataforma aún ignora el mensaje `pattern`).
//! - `Grab`: «cámara virtual» sobre el anillo pcx.cam.c920: salta la latencia medida (motion-to-photon p95 255 ms),
//!   promedia N cuadros, suaviza 3×3 y normaliza cada píxel entre las referencias negra y blanca del proyector
//!   (flat-field), con estiramiento ×2,5 alrededor de 0,5. Así el contraste de ~3 niveles (luz ambiente) llega a 0–255
//!   y los umbrales absolutos de `pcx-calib` (cambio > 18 niveles) funcionan.

use std::path::{Path, PathBuf};
use std::time::Duration;

use pcx_calib::{CalibConfig, CalibError, CalibRun, Frame, FrameGrab, GrayImage, MetricRef, Mode, Pattern, PatternSink, SyncMode};
use serde_json::json;

use crate::ws::Ws;

pub struct Sink<'a> {
    pub ws: &'a mut Ws,
    pub dir: PathBuf,
    pub n: u64,
}

pub fn save_gray_png(path: &Path, img: &GrayImage) {
    let im = image::GrayImage::from_raw(img.width, img.height, img.data.clone()).unwrap();
    let f = std::fs::File::create(path).unwrap();
    let enc = image::codecs::png::PngEncoder::new_with_quality(
        std::io::BufWriter::new(f),
        image::codecs::png::CompressionType::Fast,
        image::codecs::png::FilterType::NoFilter,
    );
    im.write_with_encoder(enc).unwrap();
}

impl PatternSink for Sink<'_> {
    fn size(&self) -> (u32, u32) {
        (1920, 1080)
    }
    fn show(&mut self, p: &Pattern) -> Result<i64, CalibError> {
        self.n += 1;
        let path = self.dir.join(format!("p{:03}_{}.png", self.n, p.label.replace(['/', ' '], "_")));
        save_gray_png(&path, &p.image);
        let t = self.ws.image_full(&path.display().to_string());
        Ok((t * 1e6) as i64)
    }
    fn release(&mut self) -> Result<(), CalibError> {
        self.ws.scene(json!([]));
        Ok(())
    }
}

pub struct Grab {
    pub r: pcx_shm::RingReader,
    buf: Vec<u8>,
    last: u64,
    pub lat_ns: i64,
    pub n_avg: usize,
    pub w: usize,
    pub h: usize,
    pub dark: Vec<f32>,
    pub span: Vec<f32>,
    pub grabs: u64,
    pub debug: Option<PathBuf>,
    cached_nb: Option<i64>,
    cached: Option<(GrayImage, i64)>,
}

pub fn blur3(v: &[f32], w: usize, h: usize) -> Vec<f32> {
    let mut t = vec![0f32; v.len()];
    for y in 0..h {
        for x in 0..w {
            let (a, b) = (x.saturating_sub(1), (x + 1).min(w - 1));
            t[y * w + x] = (v[y * w + a] + v[y * w + x] + v[y * w + b]) / 3.0;
        }
    }
    let mut o = vec![0f32; v.len()];
    for y in 0..h {
        let (a, b) = (y.saturating_sub(1), (y + 1).min(h - 1));
        for x in 0..w {
            o[y * w + x] = (t[a * w + x] + t[y * w + x] + t[b * w + x]) / 3.0;
        }
    }
    o
}

impl Grab {
    pub fn new(ring: &str) -> Self {
        let r = pcx_shm::RingReader::open(ring, Duration::from_secs(5)).expect("anillo c920");
        let last = r.latest_seq();
        Self { r, buf: vec![], last, lat_ns: 320_000_000, n_avg: 15, w: 0, h: 0, dark: vec![], span: vec![], grabs: 0, debug: None, cached_nb: None, cached: None }
    }
    /// Promedio de luma de `n` cuadros nuevos con t_capture ≥ `not_before`.
    pub fn raw_avg(&mut self, not_before: i64, n: usize) -> Result<(Vec<f32>, i64), CalibError> {
        let mut acc: Vec<f32> = vec![];
        let (mut got, mut t_last) = (0usize, 0i64);
        while got < n {
            let m = self
                .r
                .wait_newer(self.last, Duration::from_secs(3), &mut self.buf)
                .map_err(|e| CalibError::Io(e.to_string()))?
                .ok_or_else(|| CalibError::SyncTimeout("sin cuadros en el anillo".into()))?;
            self.last = m.seq;
            if (m.t_capture_ns as i64) < not_before {
                continue;
            }
            (self.w, self.h) = (m.w as usize, m.h as usize);
            if acc.is_empty() {
                acc = vec![0.0; self.w * self.h];
            }
            for (a, p) in acc.iter_mut().zip(self.buf.chunks_exact(3)) {
                *a += (29 * p[0] as u32 + 150 * p[1] as u32 + 77 * p[2] as u32) as f32 / 256.0;
            }
            got += 1;
            t_last = m.t_capture_ns as i64;
        }
        acc.iter_mut().for_each(|a| *a /= n as f32);
        Ok((blur3(&acc, self.w, self.h), t_last))
    }
    pub fn set_refs(&mut self, dark: Vec<f32>, white: Vec<f32>) {
        self.span = white.iter().zip(&dark).map(|(w, d)| w - d).collect();
        self.dark = dark;
    }
    pub fn normalize(&self, v: &[f32]) -> Vec<u8> {
        v.iter()
            .zip(&self.dark)
            .zip(&self.span)
            .map(|((v, d), s)| {
                if *s < 1.0 {
                    0
                } else {
                    let n = (v - d) / s;
                    (((n - 0.5) * 2.5 + 0.5) * 255.0).clamp(0.0, 255.0) as u8
                }
            })
            .collect()
    }
}

impl FrameGrab for Grab {
    fn grab(&mut self, not_before_ns: i64) -> Result<Frame, CalibError> {
        // Cámara virtual «por patrón»: el primer grab tras un `show` promedia `n_avg` cuadros reales tomados después
        // de la latencia medida; los grabs siguientes del mismo `show` devuelven ese promedio (es el cuadro estable)
        // con un timestamp nuevo. Así la espera por cambio de pcx-calib converge con contraste de ~2 niveles.
        if self.cached_nb == Some(not_before_ns) {
            if let Some((img, t)) = &mut self.cached {
                *t += 1;
                return Ok(Frame { t_capture_ns: *t, image: img.clone() });
            }
        }
        let (avg, t) = self.raw_avg(not_before_ns + self.lat_ns, self.n_avg)?;
        self.grabs += 1;
        let img = GrayImage { width: self.w as u32, height: self.h as u32, data: self.normalize(&avg) };
        if let Some(d) = &self.debug {
            save_gray_png(&d.join(format!("virtual_{:03}.png", self.grabs)), &img);
            if let Some((p, _)) = &self.cached {
                eprintln!("[grab {}] cambio vs patrón anterior {:.3}", self.grabs, pcx_calib::sync::change_frac(&img, p, None));
            }
        }
        self.cached_nb = Some(not_before_ns);
        self.cached = Some((img.clone(), t));
        Ok(Frame { t_capture_ns: t, image: img })
    }
}

pub fn save_f32_png16(path: &Path, v: &[f32], w: usize, h: usize) {
    let img = image::ImageBuffer::<image::Luma<u16>, Vec<u16>>::from_fn(w as u32, h as u32, |x, y| {
        image::Luma([(v[y as usize * w + x as usize] * 64.0).round().clamp(0.0, 65535.0) as u16])
    });
    img.save(path).unwrap();
}

pub fn run(port: u16, out: &Path) {
    std::fs::create_dir_all(out.join("patrones")).unwrap();
    let mut ws = Ws::accept(port);
    let mut grab = Grab::new("c920");
    // Referencias negra y blanca (45 cuadros cada una, tras la latencia).
    let negro = out.join("patrones/ref_negro.png");
    let blanco = out.join("patrones/ref_blanco.png");
    save_gray_png(&negro, &GrayImage::new(1920, 1080, 0));
    save_gray_png(&blanco, &GrayImage::new(1920, 1080, 255));
    let t = ws.image_full(&negro.display().to_string());
    let (dark, _) = grab.raw_avg((t * 1e6) as i64 + 400_000_000, 45).unwrap();
    let t = ws.image_full(&blanco.display().to_string());
    let (white, _) = grab.raw_avg((t * 1e6) as i64 + 400_000_000, 45).unwrap();
    save_f32_png16(&out.join("ref_negro16.png"), &dark, grab.w, grab.h);
    save_f32_png16(&out.join("ref_blanco16.png"), &white, grab.w, grab.h);
    let span: Vec<f32> = white.iter().zip(&dark).map(|(w, d)| w - d).collect();
    let lit = span.iter().filter(|s| **s >= 1.0).count();
    eprintln!("[calib] referencias: {lit} px de cámara con blanco−negro ≥ 1 nivel ({:.1} %)", 100.0 * lit as f64 / span.len() as f64);
    grab.set_refs(dark, white);
    std::fs::create_dir_all(out.join("virtual")).unwrap();
    grab.debug = Some(out.join("virtual"));
    // Volver a negro antes de empezar: el «cuadro previo» de pcx-calib queda negro (si no, la grilla sobre fondo
    // blanco casi no cambia respecto de la referencia blanca).
    ws.image_full(&negro.display().to_string());
    std::thread::sleep(Duration::from_millis(700));
    let cfg = CalibConfig { sync: SyncMode::Change, metric: MetricRef::MmPerProjPx(0.5), out_dir: out.to_path_buf(), ..Default::default() };
    let t0 = std::time::Instant::now();
    let res = {
        let mut sink = Sink { ws: &mut ws, dir: out.join("patrones"), n: 0 };
        CalibRun::new(Mode::Precise, cfg, None).run(&mut sink, &mut grab)
    };
    let wall = t0.elapsed().as_secs_f64();
    ws.scene(json!([]));
    match res {
        Ok(o) => {
            if let Some(c) = &o.calibration {
                std::fs::write(out.join("calibration.json"), serde_json::to_vec_pretty(c).unwrap()).unwrap();
            }
            if let Some(l) = &o.lut {
                let sha = l.write_npy(&out.join("lut_proj_cam.npy")).unwrap();
                eprintln!("[calib] LUT {}x{} paso {} cobertura {:.1} % sha256 {sha}", l.width, l.height, l.grid_step, 100.0 * l.coverage());
            }
            let h_pc = o.approx_h_pc.map(|m| pcx_calib::homography::to_row_major(&m));
            let rep = json!({"wall_s": wall, "grabs": grab.grabs, "report": o.report, "transforms": o.transforms, "approx_h_pc": h_pc});
            std::fs::write(out.join("report.json"), serde_json::to_vec_pretty(&rep).unwrap()).unwrap();
            println!("{}", serde_json::to_string_pretty(&json!({"wall_s": wall, "report": o.report})).unwrap());
        }
        Err(e) => {
            println!("CALIB ERROR: {e}");
            std::process::exit(1);
        }
    }
}
