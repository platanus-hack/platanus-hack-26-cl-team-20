//! Benchmark del anillo: escritor en este proceso, lector en otro (`pcx-capture bench-reader`).
//! Latencia escritura → lector con el cuadro en mano (p50/p95) y CPU de ambos procesos a ritmo de cámara; y
//! cuadros/s máximos sin pausa. Mismo método que `pcx_capture/bench.py` para comparar con Python.

use crate::contracts::now_ns;
use pcx_shm::{PixelFormat, RingReader, RingWriter, ShmError};
use std::io::{BufRead, BufReader};
use std::path::Path;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

/// Cifras de Python (README de node/capture, MacBook Apple Silicon): (transporte, tamaño) → (p50, p95, CPU %).
pub const PYTHON: &[(&str, &str, f64, f64, f64)] = &[
    ("shm", "1280x720", 0.33, 0.43, 3.0),
    ("shm-view", "1280x720", 0.25, 0.36, 2.0),
    ("shm", "1920x1080", 0.49, 0.67, 4.0),
    ("shm-view", "1920x1080", 0.28, 0.47, 3.0),
];

#[derive(Clone, Debug)]
pub struct Row {
    pub kind: String,
    pub size: String,
    pub n: usize,
    pub got: u64,
    pub p50_ms: f64,
    pub p95_ms: f64,
    pub cpu_pct: f64,
    pub max_write_fps: f64,
    pub max_read_fps: f64,
    pub retries: u64,
}

fn cpu_s() -> f64 {
    cpu_time::ProcessTime::now().as_duration().as_secs_f64()
}

fn frames(w: u32, h: u32, k: usize) -> Vec<Vec<u8>> {
    let mut x: u64 = 0x9E37_79B9_7F4A_7C15;
    (0..k)
        .map(|_| {
            (0..(w * h * 3) as usize)
                .map(|_| {
                    x ^= x << 13;
                    x ^= x >> 7;
                    x ^= x << 17;
                    x as u8
                })
                .collect()
        })
        .collect()
}

/// Proceso lector: imprime `READY`, lee hasta `closed` y devuelve una línea `lat_ns,…|got|cpu|wall|retries`.
pub fn reader_child(id: &str, n: u64, view: bool) -> Result<(), ShmError> {
    let mut r = RingReader::open(id, Duration::from_secs(5))?;
    println!("READY");
    let (mut lat, mut got, mut last) = (Vec::with_capacity(n as usize), 0u64, 0u64);
    let (c0, mut t0, mut t_last) = (cpu_s(), None::<Instant>, Instant::now());
    let mut buf = Vec::new();
    while got < n {
        let res = if view {
            r.wait_newer_with(last, Duration::from_secs(2), |_, b| b[0]).map(|o| o.map(|(m, _)| m))
        } else {
            r.wait_newer(last, Duration::from_secs(2), &mut buf)
        };
        match res {
            Ok(Some(m)) => {
                lat.push(now_ns().saturating_sub(m.t_capture_ns));
                t0.get_or_insert_with(Instant::now);
                (last, got, t_last) = (m.seq, got + 1, Instant::now());
            }
            Ok(None) | Err(ShmError::Closed(_)) => break,
            Err(e) => return Err(e),
        }
    }
    let wall = t0.map(|t| (t_last - t).as_secs_f64()).unwrap_or(0.0);
    let lat_s: Vec<String> = lat.iter().map(u64::to_string).collect();
    println!("{}|{got}|{}|{wall}|{}", lat_s.join(","), cpu_s() - c0, r.retries());
    Ok(())
}

fn pct(sorted: &[f64], p: f64) -> f64 {
    if sorted.is_empty() {
        return f64::NAN;
    }
    let k = (sorted.len() - 1) as f64 * p / 100.0;
    let (lo, hi) = (k.floor() as usize, k.ceil() as usize);
    sorted[lo] + (sorted[hi] - sorted[lo]) * (k - lo as f64)
}

struct One {
    lat_ms: Vec<f64>,
    got: u64,
    cpu_pct: f64,
    writer_fps: f64,
    reader_fps: f64,
    retries: u64,
}

fn one(exe: &Path, w: u32, h: u32, n: usize, fps: Option<f64>, view: bool, fr: &[Vec<u8>]) -> Result<One, String> {
    let id = format!("bench{}", std::process::id() % 100_000);
    let mut writer = RingWriter::create(&id, w as usize, h as usize, 3, 4).map_err(|e| e.to_string())?;
    let mut cmd = Command::new(exe);
    cmd.args(["bench-reader", "--id", &id, "--n", &n.to_string()]);
    if view {
        cmd.arg("--view");
    }
    let mut child = cmd.stdout(Stdio::piped()).spawn().map_err(|e| e.to_string())?;
    let mut out = BufReader::new(child.stdout.take().unwrap());
    let mut line = String::new();
    out.read_line(&mut line).map_err(|e| e.to_string())?;
    if line.trim() != "READY" {
        return Err(format!("lector: {line:?}"));
    }
    std::thread::sleep(Duration::from_millis(200));
    let (c0, t0) = (cpu_s(), Instant::now());
    let period = fps.map(|f| Duration::from_secs_f64(1.0 / f));
    let mut t_next = t0;
    for i in 0..n {
        if let Some(p) = period {
            let now = Instant::now();
            if t_next > now {
                std::thread::sleep(t_next - now);
            }
            t_next += p;
        }
        writer.write(&fr[i % fr.len()], w, h, PixelFormat::Bgr8, Some(now_ns())).map_err(|e| e.to_string())?;
    }
    let (wall_w, cpu_w) = (t0.elapsed().as_secs_f64(), cpu_s() - c0);
    std::thread::sleep(Duration::from_millis(50));
    writer.close();
    line.clear();
    out.read_line(&mut line).map_err(|e| e.to_string())?;
    let _ = child.wait();
    let f: Vec<&str> = line.trim().split('|').collect();
    if f.len() != 5 {
        return Err(format!("salida del lector: {line:?}"));
    }
    let mut lat_ms: Vec<f64> = f[0].split(',').filter_map(|s| s.parse::<u64>().ok()).map(|x| x as f64 / 1e6).collect();
    lat_ms.sort_by(|a, b| a.total_cmp(b));
    let got: u64 = f[1].parse().unwrap_or(0);
    let (cpu_r, wall_r): (f64, f64) = (f[2].parse().unwrap_or(0.0), f[3].parse().unwrap_or(0.0));
    Ok(One {
        lat_ms,
        got,
        cpu_pct: 100.0 * (cpu_w + cpu_r) / wall_w.max(1e-9),
        writer_fps: n as f64 / wall_w.max(1e-9),
        reader_fps: got as f64 / wall_r.max(1e-9),
        retries: f[4].parse().unwrap_or(0),
    })
}

/// `exe` = binario `pcx-capture` (para lanzar el lector).
pub fn run_bench(exe: &Path, sizes: &[(u32, u32)], n: usize, fps: f64) -> Result<Vec<Row>, String> {
    let mut rows = Vec::new();
    for &(w, h) in sizes {
        let fr = frames(w, h, 6);
        for view in [false, true] {
            let paced = one(exe, w, h, n, Some(fps), view, &fr)?;
            let flat = one(exe, w, h, n, None, view, &fr)?;
            rows.push(Row {
                kind: if view { "shm-view" } else { "shm" }.into(),
                size: format!("{w}x{h}"),
                n,
                got: paced.got,
                p50_ms: pct(&paced.lat_ms, 50.0),
                p95_ms: pct(&paced.lat_ms, 95.0),
                cpu_pct: paced.cpu_pct,
                max_write_fps: flat.writer_fps,
                max_read_fps: flat.reader_fps,
                retries: paced.retries + flat.retries,
            });
        }
    }
    Ok(rows)
}

pub fn format_table(rows: &[Row], fps: f64) -> String {
    let mut out = vec![
        format!(
            "| Transporte | Tamaño | p50 ms (Python) | p95 ms (Python) | CPU % a {fps:.0} fps (Python) | Recibidos | \
             Escritura máx. (cuadros/s) | Lector sin pausa (cuadros/s) |"
        ),
        "|---|---|---:|---:|---:|---:|---:|---:|".to_string(),
    ];
    for r in rows {
        let py = PYTHON.iter().find(|p| p.0 == r.kind && p.1 == r.size);
        let (p50, p95, cpu) = py.map(|p| (p.2, p.3, p.4)).unwrap_or((f64::NAN, f64::NAN, f64::NAN));
        out.push(format!(
            "| {} | {} | {:.2} ({p50:.2}) | {:.2} ({p95:.2}) | {:.1} ({cpu:.0}) | {}/{} | {:.0} | {:.0} |",
            r.kind, r.size, r.p50_ms, r.p95_ms, r.cpu_pct, r.got, r.n, r.max_write_fps, r.max_read_fps
        ));
    }
    out.join("\n")
}
