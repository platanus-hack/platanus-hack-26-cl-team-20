//! Caché de imágenes del widget `image`. `src` admite:
//! - `asset:NOMBRE` → `<assets>/NOMBRE.png` (`--assets DIR`);
//! - ruta local (`/abs/x.png`, `rel/x.png`) o `file:///abs/x.png`;
//! - `http://127.0.0.1:PUERTO/...` o `http://localhost:...` (solo hosts locales, GET HTTP/1.0).
//! Se decodifica una vez y se guarda; un fallo se reintenta cada 2 s.

use std::collections::HashMap;
use std::io::{Read, Write};
use std::path::PathBuf;
use std::time::{Duration, Instant};

use vello::peniko::{Blob, ImageAlphaType, ImageData, ImageFormat};

pub struct ImageCache {
    pub assets_dir: PathBuf,
    ok: HashMap<String, ImageData>,
    failed: HashMap<String, Instant>,
}

impl ImageCache {
    pub fn new(assets_dir: PathBuf) -> Self {
        Self { assets_dir, ok: HashMap::new(), failed: HashMap::new() }
    }

    pub fn get(&mut self, src: &str) -> Option<&ImageData> {
        if !self.ok.contains_key(src) {
            if self.failed.get(src).is_some_and(|t| t.elapsed() < Duration::from_secs(2)) {
                return None;
            }
            match self.load(src) {
                Ok(img) => {
                    self.failed.remove(src);
                    self.ok.insert(src.to_owned(), img);
                }
                Err(e) => {
                    eprintln!("[image] no pude cargar {src}: {e}");
                    self.failed.insert(src.to_owned(), Instant::now());
                    return None;
                }
            }
        }
        self.ok.get(src)
    }

    fn load(&self, src: &str) -> Result<ImageData, String> {
        let bytes = if let Some(name) = src.strip_prefix("asset:") {
            let mut p = self.assets_dir.join(name);
            if p.extension().is_none() {
                p.set_extension("png");
            }
            std::fs::read(&p).map_err(|e| format!("{}: {e}", p.display()))?
        } else if let Some(rest) = src.strip_prefix("http://") {
            http_get_local(rest)?
        } else {
            let p = src.strip_prefix("file://").unwrap_or(src);
            std::fs::read(p).map_err(|e| e.to_string())?
        };
        let img = image::load_from_memory(&bytes).map_err(|e| e.to_string())?.to_rgba8();
        let (width, height) = img.dimensions();
        Ok(ImageData {
            data: Blob::from(img.into_raw()),
            format: ImageFormat::Rgba8,
            alpha_type: ImageAlphaType::Alpha,
            width,
            height,
        })
    }
}

fn http_get_local(rest: &str) -> Result<Vec<u8>, String> {
    let (hostport, path) = rest.split_once('/').map_or((rest, "/".to_owned()), |(h, p)| (h, format!("/{p}")));
    let host = hostport.split(':').next().unwrap_or("");
    if !matches!(host, "127.0.0.1" | "localhost" | "[::1]") {
        return Err(format!("solo URLs locales (host {host})"));
    }
    let addr = if hostport.contains(':') { hostport.to_owned() } else { format!("{hostport}:80") };
    let mut s = std::net::TcpStream::connect(addr.replace("localhost", "127.0.0.1")).map_err(|e| e.to_string())?;
    s.set_read_timeout(Some(Duration::from_secs(2))).ok();
    write!(s, "GET {path} HTTP/1.0\r\nHost: {hostport}\r\n\r\n").map_err(|e| e.to_string())?;
    let mut buf = vec![];
    s.read_to_end(&mut buf).map_err(|e| e.to_string())?;
    let sep = buf.windows(4).position(|w| w == b"\r\n\r\n").ok_or("respuesta HTTP sin cuerpo")?;
    let head = String::from_utf8_lossy(&buf[..sep]);
    if !head.split_whitespace().nth(1).is_some_and(|c| c == "200") {
        return Err(format!("HTTP: {}", head.lines().next().unwrap_or("")));
    }
    Ok(buf[sep + 4..].to_vec())
}
