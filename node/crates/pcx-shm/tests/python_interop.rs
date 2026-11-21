//! Interoperabilidad con la versión Python (`node/capture/src/pcx_capture/shm.py`), vía `uv run`:
//! Rust escribe → Python `RingReader` lee, y Python `RingWriter` escribe → Rust lee.
//!
//! Carpeta del proyecto Python: `PCX_PY_CAPTURE` o `<repo>/node/capture`. Si no existe o no hay `uv`, se omite
//! (lo avisa por stderr).

use pcx_shm::{PixelFormat, RingReader, RingWriter};
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::time::Duration;

const W: u32 = 64;
const H: u32 = 48;

fn py_project() -> Option<PathBuf> {
    let p = std::env::var_os("PCX_PY_CAPTURE")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../capture"));
    if !p.join("src/pcx_capture/shm.py").exists() || Command::new("uv").arg("--version").output().is_err() {
        eprintln!("OMITIDO: no hay proyecto Python en {} (usa PCX_PY_CAPTURE) o falta uv", p.display());
        return None;
    }
    Some(p)
}

fn uv_python(project: &PathBuf, script: &str, args: &[&str]) -> Command {
    let mut c = Command::new("uv");
    c.args(["run", "--no-sync", "--project"]).arg(project).args(["python", "-c", script]).args(args);
    c
}

fn pattern() -> Vec<u8> {
    (0..H as usize)
        .flat_map(|y| (0..W as usize).flat_map(move |x| [(x % 256) as u8, (y % 256) as u8, ((x + y) % 256) as u8]))
        .collect()
}

const PY_READ: &str = r#"
import sys
from pcx_capture.shm import RingReader
r = RingReader(sys.argv[1], timeout=5)
meta, img = r.wait_newer(int(sys.argv[2]), 5.0)
print(meta.seq, meta.t_capture_ns, meta.w, meta.h, meta.stride, meta.format, int(img.astype('uint64').sum()),
      list(img.shape), int(img[7, 5, 0]), int(img[7, 5, 1]), int(img[7, 5, 2]), flush=True)
r.close()
"#;

#[test]
fn rust_writes_python_reads() {
    let Some(proj) = py_project() else { return };
    let id = format!("py{}", std::process::id() % 100_000);
    let mut w = RingWriter::create(&id, W as usize, H as usize, 3, 4).unwrap();
    let frame = pattern();
    for _ in 0..5 {
        w.write(&frame, W, H, PixelFormat::Bgr8, Some(123_456_789)).unwrap(); // da la vuelta a los 4 slots
    }
    let out = uv_python(&proj, PY_READ, &[&id, "4"]).output().unwrap();
    assert!(out.status.success(), "python: {}", String::from_utf8_lossy(&out.stderr));
    let s = String::from_utf8(out.stdout).unwrap();
    let sum: u64 = frame.iter().map(|&b| b as u64).sum();
    assert_eq!(s.trim(), format!("5 123456789 {W} {H} {} bgr8 {sum} [{H}, {W}, 3] 5 7 12", W * 3));
}

const PY_WRITE: &str = r#"
import sys, numpy as np
from pcx_capture.shm import RingWriter
w = RingWriter(sys.argv[1], 64, 48, 3)
y, x = np.mgrid[0:48, 0:64]
img = np.stack([x % 256, y % 256, (x + y) % 256], axis=-1).astype(np.uint8)
for _ in range(6):
    w.write(img, t_capture_ns=987654321)
print("READY", flush=True)
sys.stdin.readline()
w.close()
"#;

#[test]
fn python_writes_rust_reads() {
    let Some(proj) = py_project() else { return };
    let id = format!("pr{}", std::process::id() % 100_000);
    let mut child = uv_python(&proj, PY_WRITE, &[&id]).stdin(Stdio::piped()).stdout(Stdio::piped()).spawn().unwrap();
    let mut line = String::new();
    BufReader::new(child.stdout.take().unwrap()).read_line(&mut line).unwrap();
    assert_eq!(line.trim(), "READY");
    let mut r = RingReader::open(&id, Duration::from_secs(5)).unwrap();
    let (m, data) = r.latest().unwrap().unwrap();
    assert_eq!((m.seq, m.t_capture_ns, m.w, m.h, m.stride, m.format), (6, 987654321, W, H, W * 3, PixelFormat::Bgr8));
    assert!(data == pattern(), "los bytes BGR no coinciden");
    assert_eq!(r.n_slots(), 4);
    child.stdin.take().unwrap().write_all(b"\n").unwrap();
    assert!(child.wait().unwrap().success());
    assert!(r.closed());
}
