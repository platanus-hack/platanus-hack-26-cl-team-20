//! Imágenes acotadas: archivos bajo assets (sin traversal), data URI y HTTP(S) opt-in.
use std::collections::HashMap;
use std::io::{Cursor, Read};
use std::path::{Component, Path, PathBuf};
use std::time::{Duration, Instant};

use base64::Engine as _;
use vello::peniko::{Blob, ImageAlphaType, ImageData, ImageFormat};

pub const MAX_BYTES: usize = 8 * 1024 * 1024;
pub const MAX_DIMENSION: u32 = 4096;

pub struct ImageCache {
    assets_dir: PathBuf,
    allow_remote: bool,
    ok: HashMap<String, ImageData>,
    failed: HashMap<String, Instant>,
}

impl ImageCache {
    pub fn new(assets_dir: PathBuf) -> Self {
        Self::with_policy(assets_dir, false)
    }

    pub fn with_policy(assets_dir: PathBuf, allow_remote: bool) -> Self {
        Self { assets_dir, allow_remote, ok: HashMap::new(), failed: HashMap::new() }
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
                    // No imprimir data URIs (pueden medir megabytes).
                    eprintln!("[image] imagen rechazada: {e}");
                    self.failed.insert(src.to_owned(), Instant::now());
                    return None;
                }
            }
        }
        self.ok.get(src)
    }

    pub fn load(&self, src: &str) -> Result<ImageData, String> {
        let bytes = if src.starts_with("data:") {
            decode_data_uri(src)?
        } else if src.starts_with("http://") || src.starts_with("https://") {
            if !self.allow_remote {
                return Err("HTTP(S) requiere --allow-remote-images".into());
            }
            let response = reqwest::blocking::Client::builder()
                .timeout(Duration::from_secs(2))
                .redirect(reqwest::redirect::Policy::none())
                .build()
                .map_err(|e| e.to_string())?
                .get(src)
                .send()
                .and_then(|r| r.error_for_status())
                .map_err(|e| e.to_string())?;
            if !response.status().is_success() {
                return Err("redirección HTTP rechazada".into());
            }
            read_limited(response)?
        } else {
            let path = if let Some(name) = src.strip_prefix("asset:") {
                let mut p = PathBuf::from(name);
                if p.extension().is_none() {
                    p.set_extension("png");
                }
                p
            } else {
                PathBuf::from(src.strip_prefix("file://").unwrap_or(src))
            };
            if path.components().any(|c| c == Component::ParentDir) {
                return Err("ruta con .. rechazada".into());
            }
            let root = self.assets_dir.canonicalize().map_err(|e| e.to_string())?;
            let path = if path.is_absolute() { path } else { root.join(path) };
            let path = path.canonicalize().map_err(|e| e.to_string())?;
            if !path.starts_with(&root) {
                return Err("archivo fuera de --assets-dir".into());
            }
            read_file(&path)?
        };
        decode_image(&bytes)
    }
}

/// Los patrones de calibración usan rutas locales del contrato (pueden ser temporales fuera de assets).
/// Mantienen los límites de bytes y decodificación, y nunca descargan URLs.
pub fn load_pattern(src: &str) -> Result<ImageData, String> {
    let bytes = if src.starts_with("data:") {
        decode_data_uri(src)?
    } else if src.contains("://") {
        return Err("pattern requiere archivo local o data URI".into());
    } else {
        read_file(Path::new(src))?
    };
    decode_image(&bytes)
}

fn read_file(path: &Path) -> Result<Vec<u8>, String> {
    let f = std::fs::File::open(path).map_err(|e| e.to_string())?;
    if !f.metadata().map_err(|e| e.to_string())?.is_file() {
        return Err("se requiere un archivo regular".into());
    }
    read_limited(f)
}

fn read_limited(reader: impl Read) -> Result<Vec<u8>, String> {
    let mut bytes = Vec::new();
    reader.take(MAX_BYTES as u64 + 1).read_to_end(&mut bytes).map_err(|e| e.to_string())?;
    if bytes.len() > MAX_BYTES {
        return Err("imagen supera 8 MiB".into());
    }
    Ok(bytes)
}

fn decode_data_uri(src: &str) -> Result<Vec<u8>, String> {
    let (header, data) = src.split_once(',').ok_or("data URI sin cuerpo")?;
    if !header.starts_with("data:image/") || !header.ends_with(";base64") {
        return Err("se requiere data:image/...;base64".into());
    }
    if data.len() > MAX_BYTES.div_ceil(3) * 4 {
        return Err("data URI supera 8 MiB".into());
    }
    let bytes = base64::engine::general_purpose::STANDARD.decode(data).map_err(|e| e.to_string())?;
    if bytes.len() > MAX_BYTES {
        return Err("imagen supera 8 MiB".into());
    }
    Ok(bytes)
}

fn decode_image(bytes: &[u8]) -> Result<ImageData, String> {
    let mut reader = image::ImageReader::new(Cursor::new(bytes)).with_guessed_format().map_err(|e| e.to_string())?;
    let mut limits = image::Limits::default();
    limits.max_image_width = Some(MAX_DIMENSION);
    limits.max_image_height = Some(MAX_DIMENSION);
    limits.max_alloc = Some(128 * 1024 * 1024);
    reader.limits(limits);
    let img = reader.decode().map_err(|e| e.to_string())?.to_rgba8();
    let (width, height) = img.dimensions();
    Ok(ImageData {
        data: Blob::from(img.into_raw()),
        format: ImageFormat::Rgba8,
        alpha_type: ImageAlphaType::Alpha,
        width,
        height,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> (PathBuf, ImageCache) {
        static NEXT: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
        let id = NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        let root = std::env::temp_dir().join(format!("pcx-image-security-{}-{id}", std::process::id()));
        std::fs::create_dir_all(&root).unwrap();
        image::RgbaImage::from_pixel(2, 2, image::Rgba([255, 0, 0, 255])).save(root.join("red.png")).unwrap();
        let cache = ImageCache::new(root.clone());
        (root, cache)
    }
    #[test]
    fn files_data_uris_and_limits() {
        let (root, cache) = fixture();
        assert_eq!(cache.load("asset:red").unwrap().width, 2);
        assert!(cache.load("red.png").is_ok());
        assert!(cache.load(root.join("red.png").to_str().unwrap()).is_ok());
        assert!(cache.load("../red.png").is_err());
        assert!(cache.load("asset:../red").is_err());
        assert!(cache.load("/etc/passwd").is_err());
        assert_eq!(load_pattern(root.join("red.png").to_str().unwrap()).unwrap().width, 2);
        assert!(load_pattern("https://example.com/pattern.png").is_err());
        let bytes = std::fs::read(root.join("red.png")).unwrap();
        let uri = format!("data:image/png;base64,{}", base64::engine::general_purpose::STANDARD.encode(bytes));
        assert_eq!(cache.load(&uri).unwrap().height, 2);
        assert!(cache.load("data:image/png;base64,invalid").is_err());
        assert!(cache.load("data:text/plain;base64,YQ==").is_err());
        assert!(cache.load(&format!("data:image/png;base64,{}", "A".repeat(MAX_BYTES.div_ceil(3) * 4 + 4))).is_err());
        let big = root.join("large.png");
        std::fs::File::create(&big).unwrap().set_len(MAX_BYTES as u64 + 1).unwrap();
        assert!(cache.load(big.to_str().unwrap()).unwrap_err().contains("8 MiB"));
        image::RgbaImage::new(MAX_DIMENSION + 1, 1).save(root.join("wide.png")).unwrap();
        image::RgbaImage::new(1, MAX_DIMENSION + 1).save(root.join("tall.png")).unwrap();
        assert!(cache.load("wide.png").is_err());
        assert!(cache.load("tall.png").is_err());
    }
    #[test]
    fn remote_urls_rejected_even_on_loopback() {
        let cache = ImageCache::new(PathBuf::from("."));
        for url in [
            "http://127.0.0.1:1/x.png",
            "http://localhost/x.png",
            "https://example.com/x.png",
            "http://example.com/x.png",
        ] {
            assert!(cache.load(url).unwrap_err().contains("--allow-remote-images"));
        }
    }
    #[cfg(unix)]
    #[test]
    fn symlink_outside_assets_rejected() {
        use std::os::unix::fs::symlink;
        let (root, cache) = fixture();
        let link = root.join("escape.png");
        let _ = std::fs::remove_file(&link);
        symlink("/etc/passwd", &link).unwrap();
        assert!(cache.load("escape.png").unwrap_err().contains("fuera"));
    }
    #[test]
    fn opt_in_remote_fetch_is_bounded() {
        use std::io::Write;
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}/red.png", listener.local_addr().unwrap());
        let (root, _) = fixture();
        let bytes = std::fs::read(root.join("red.png")).unwrap();
        let server = std::thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            let mut request = [0; 4096];
            assert!(socket.read(&mut request).unwrap() > 0);
            write!(socket, "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", bytes.len()).unwrap();
            socket.write_all(&bytes).unwrap();
        });
        let cache = ImageCache::with_policy(root, true);
        assert_eq!(cache.load(&url).unwrap().width, 2);
        server.join().unwrap();
        assert!(read_limited(std::io::repeat(0).take(MAX_BYTES as u64 + 1)).is_err());
    }
}
