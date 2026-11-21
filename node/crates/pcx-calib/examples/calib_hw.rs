//! Prueba en el hardware del montaje (Mac de Daniel). No abre la cámara ni ventanas propias: usa lo que ya corre.
//! - Proyector: runtime en 127.0.0.1:8790, `POST /api/command {"cmd":"overlay","args":{"path":<bmp>,"seconds":s}}`.
//!   Cada overlay vence solo (`--hold-s`, 2 s por defecto): si el proceso muere, vuelve la escena.
//! - Cámara: memoria compartida de `scripts/manos_mesa.py` (`/tmp/mesa/cenital_shm.npy` BGR + `cenital_seq.npy`).
//! - Mientras corre crea `/tmp/mesa/escaneo_en_curso` (pausa al seguidor, igual que `escaneo_rapido.py`).
//!
//! ```text
//! cargo run --release -p pcx-calib --example calib_hw -- quick   [--out DIR] [--mm-per-px 0.43] [--marker ID:X:Y:LADO]...
//! cargo run --release -p pcx-calib --example calib_hw -- precise [--out DIR] [--sync change]
//! cargo run --release -p pcx-calib --example calib_hw -- verify  [--out DIR]        # usa DIR/calibration.json
//! cargo run --release -p pcx-calib --example calib_hw -- print-marker ID archivo.bmp  # para imprimir
//! ```
//! Imprime el reporte JSON y deja `calibration.json`, `report_<modo>.json` y la LUT en `--out`.

use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use pcx_calib::markers;
use pcx_calib::{
    CalibConfig, CalibError, CalibRun, Calibration, Frame, FrameGrab, GrayImage, MetricRef, Mode, Pattern, PatternSink, PrintedMarker,
    SyncMode,
};

fn now_ns() -> i64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_nanos() as i64).unwrap_or(0)
}

fn io(e: impl std::fmt::Display) -> CalibError {
    CalibError::Io(e.to_string())
}

/// BMP de 24 bits (gris replicado en BGR), filas de abajo hacia arriba; escritura atómica.
fn write_bmp(path: &Path, img: &GrayImage) -> std::io::Result<()> {
    let (w, h) = (img.width as usize, img.height as usize);
    let row = (w * 3).div_ceil(4) * 4;
    let size = 54 + row * h;
    let mut b = Vec::with_capacity(size);
    b.extend_from_slice(b"BM");
    b.extend_from_slice(&(size as u32).to_le_bytes());
    b.extend_from_slice(&[0; 4]);
    b.extend_from_slice(&54u32.to_le_bytes());
    b.extend_from_slice(&40u32.to_le_bytes());
    b.extend_from_slice(&(w as i32).to_le_bytes());
    b.extend_from_slice(&(h as i32).to_le_bytes());
    b.extend_from_slice(&1u16.to_le_bytes());
    b.extend_from_slice(&24u16.to_le_bytes());
    b.extend_from_slice(&[0; 24]);
    for y in (0..h).rev() {
        for x in 0..w {
            let v = img.data[y * w + x];
            b.extend_from_slice(&[v, v, v]);
        }
        b.extend(std::iter::repeat_n(0u8, row - w * 3));
    }
    let tmp = path.with_extension("tmp.bmp");
    std::fs::write(&tmp, &b)?;
    std::fs::rename(&tmp, path)
}

/// Proyector = runtime de la mesa (overlay de un BMP a pantalla completa).
struct Runtime {
    addr: String,
    size: (u32, u32),
    hold_s: f64,
    dir: PathBuf,
    n: u64,
}

impl Runtime {
    fn command(&self, cmd: &str, args: serde_json::Value) -> Result<(), CalibError> {
        let body = serde_json::json!({ "cmd": cmd, "args": args }).to_string();
        let mut s = TcpStream::connect(&self.addr).map_err(io)?;
        s.set_read_timeout(Some(Duration::from_secs(2))).map_err(io)?;
        write!(
            s,
            "POST /api/command HTTP/1.1\r\nHost: {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
            self.addr,
            body.len(),
            body
        )
        .map_err(io)?;
        let mut resp = String::new();
        s.read_to_string(&mut resp).map_err(io)?;
        let json = resp.split("\r\n\r\n").nth(1).unwrap_or("");
        let v: serde_json::Value = serde_json::from_str(json).map_err(|e| io(format!("{e}: {resp:.200}")))?;
        if v["ok"].as_bool() != Some(true) {
            return Err(io(format!("runtime rechazó {cmd}: {}", v["error"])));
        }
        Ok(())
    }
}

impl PatternSink for Runtime {
    fn size(&self) -> (u32, u32) {
        self.size
    }
    fn show(&mut self, p: &Pattern) -> Result<i64, CalibError> {
        self.n += 1;
        let path = self.dir.join(format!("pcx_calib_{}.bmp", self.n % 3));
        write_bmp(&path, &p.image).map_err(io)?;
        self.command("overlay", serde_json::json!({ "path": path.display().to_string(), "seconds": self.hold_s }))?;
        Ok(now_ns())
    }
    fn release(&mut self) -> Result<(), CalibError> {
        Ok(()) // el último overlay vence solo en `hold_s`
    }
}

/// Cabecera de un `.npy` simple: (descr, shape, offset de los datos).
fn npy_header(b: &[u8]) -> Option<(String, Vec<usize>, usize)> {
    if b.len() < 12 || &b[..6] != b"\x93NUMPY" {
        return None;
    }
    let (hl, off) = if b[6] == 1 { (u16::from_le_bytes([b[8], b[9]]) as usize, 10) } else { (u32::from_le_bytes([b[8], b[9], b[10], b[11]]) as usize, 12) };
    let h = std::str::from_utf8(b.get(off..off + hl)?).ok()?;
    let descr = h.split("'descr':").nth(1)?.split('\'').nth(1)?.to_string();
    let shape_s = h.split("'shape':").nth(1)?.split('(').nth(1)?.split(')').next()?;
    let shape = shape_s.split(',').filter_map(|x| x.trim().parse().ok()).collect();
    Some((descr, shape, off + hl))
}

/// Cámara = memoria compartida de manos_mesa; la hora del cuadro es cuando se ve subir el contador (como Daniel).
struct Shm {
    frame: PathBuf,
    seq: PathBuf,
    last: f64,
}

impl Shm {
    fn read_seq(&self) -> Result<f64, CalibError> {
        let b = std::fs::read(&self.seq).map_err(io)?;
        let (d, _, o) = npy_header(&b).ok_or_else(|| io("cenital_seq.npy ilegible"))?;
        let x: [u8; 8] = b.get(o..o + 8).and_then(|s| s.try_into().ok()).ok_or_else(|| io("seq corto"))?;
        Ok(if d == "<f8" { f64::from_le_bytes(x) } else { i64::from_le_bytes(x) as f64 })
    }
}

impl FrameGrab for Shm {
    fn grab(&mut self, not_before_ns: i64) -> Result<Frame, CalibError> {
        let t0 = Instant::now();
        loop {
            let s = self.read_seq()?;
            if s != self.last {
                let t = now_ns();
                let b = std::fs::read(&self.frame).map_err(io)?;
                self.last = s;
                if t >= not_before_ns {
                    let (_, shape, o) = npy_header(&b).ok_or_else(|| io("cenital_shm.npy ilegible"))?;
                    let (h, w, c) = (shape[0], shape[1], *shape.get(2).unwrap_or(&1));
                    let px = b.get(o..o + w * h * c).ok_or_else(|| io("cuadro corto"))?;
                    let data = if c == 3 {
                        px.chunks(3).map(|p| ((p[0] as u32 * 29 + p[1] as u32 * 150 + p[2] as u32 * 77) >> 8) as u8).collect()
                    } else {
                        px.to_vec()
                    };
                    return Ok(Frame { t_capture_ns: t, image: GrayImage { width: w as u32, height: h as u32, data } });
                }
            }
            if t0.elapsed() > Duration::from_secs(5) {
                return Err(CalibError::SyncTimeout("manos_mesa no publica cuadros".into()));
            }
            std::thread::sleep(Duration::from_micros(500));
        }
    }
}

fn arg(args: &[String], k: &str) -> Option<String> {
    args.iter().position(|a| a == k).and_then(|i| args.get(i + 1)).cloned()
}

fn print_marker(args: &[String]) -> Result<(), Box<dyn std::error::Error>> {
    let id: usize = args.get(2).ok_or("falta ID")?.parse()?;
    let path = PathBuf::from(args.get(3).ok_or("falta archivo.bmp")?);
    let (cell, margin) = (100i64, 100i64);
    let side = (cell * 6 + 2 * margin) as u32;
    let mut img = GrayImage::new(side, side, 255);
    markers::draw(&mut img, id, margin, margin, cell, 0, 255);
    write_bmp(&path, &img)?;
    println!("marcador {id} en {} (el borde negro exterior es el lado a medir)", path.display());
    Ok(())
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = std::env::args().collect();
    let mode = match args.get(1).map(String::as_str) {
        Some("quick") => Mode::Quick,
        Some("precise") => Mode::Precise,
        Some("verify") => Mode::Verify,
        Some("print-marker") => return print_marker(&args),
        _ => return Err("uso: calib_hw quick|precise|verify|print-marker [--out DIR] [--mm-per-px 0.43] [--marker ID:X:Y:LADO]".into()),
    };
    let out = PathBuf::from(arg(&args, "--out").unwrap_or_else(|| "/tmp/mesa/pcx-calib".into()));
    let proj = arg(&args, "--proj").unwrap_or_else(|| "1920x1080".into());
    let (pw, ph) = proj.split_once('x').ok_or("--proj WxH")?;
    std::fs::create_dir_all(&out)?;
    let mut sink = Runtime {
        addr: arg(&args, "--runtime").unwrap_or_else(|| "127.0.0.1:8790".into()),
        size: (pw.parse()?, ph.parse()?),
        hold_s: arg(&args, "--hold-s").map_or(Ok(2.0), |s| s.parse())?,
        dir: PathBuf::from("/tmp/mesa"),
        n: 0,
    };
    let mut grab = Shm { frame: "/tmp/mesa/cenital_shm.npy".into(), seq: "/tmp/mesa/cenital_seq.npy".into(), last: f64::NAN };
    // marco table_mm: marcadores impresos (`--marker 60:100:80:60`, repetible) o escala fija (0,43 mm/px ≈ lienzo de Daniel)
    let mut printed = Vec::new();
    for (i, a) in args.iter().enumerate() {
        if a == "--marker" {
            let v: Vec<f64> = args.get(i + 1).ok_or("--marker ID:X:Y:LADO")?.split(':').map(str::parse).collect::<Result<_, _>>()?;
            let [id, x, y, s] = v[..] else { return Err("--marker ID:X:Y:LADO".into()) };
            printed.push(PrintedMarker { id: id as usize, corners_mm: [[x, y], [x + s, y], [x + s, y + s], [x, y + s]] });
        }
    }
    let metric = if printed.is_empty() { MetricRef::MmPerProjPx(arg(&args, "--mm-per-px").map_or(Ok(0.43), |s| s.parse())?) } else { MetricRef::PrintedMarkers(printed) };
    let cfg = CalibConfig {
        metric,
        sync: if arg(&args, "--sync").as_deref() == Some("change") { SyncMode::Change } else { SyncMode::Marker },
        out_dir: out.clone(),
        ..Default::default()
    };
    let cal_path = out.join("calibration.json");
    let prior: Option<Calibration> = std::fs::read(&cal_path).ok().and_then(|b| serde_json::from_slice(&b).ok());
    let flag = Path::new("/tmp/mesa/escaneo_en_curso");
    std::fs::write(flag, b"pcx-calib")?;
    let t = Instant::now();
    let res = CalibRun::new(mode, cfg, prior).run(&mut sink, &mut grab);
    let _ = std::fs::remove_file(flag);
    let o = res?;
    if let (Some(c), Mode::Quick | Mode::Precise) = (&o.calibration, mode) {
        std::fs::write(&cal_path, serde_json::to_vec_pretty(c)?)?;
    }
    let report = serde_json::json!({
        "mode": mode,
        "wall_s": t.elapsed().as_secs_f64(),
        "report": o.report,
        "transforms": o.transforms,
        "drift": o.drift,
    });
    std::fs::write(out.join(format!("report_{mode:?}.json").to_lowercase()), serde_json::to_vec_pretty(&report)?)?;
    println!("{}", serde_json::to_string_pretty(&report)?);
    Ok(())
}
