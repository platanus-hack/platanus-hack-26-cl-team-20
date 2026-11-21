//! `replay`: carpeta de imágenes (jpg/png) o archivo MJPEG (JPEG concatenados), al ritmo real, en bucle.
//! Los cuadros se guardan codificados y se decodifican al emitirlos (memoria acotada). Video (mp4) no: sin ffmpeg.

use crate::frame::{decode, fit, BoxError, Frame, LatestTx};
use crate::contracts::now_ns;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

const IMAGE_EXT: &[&str] = &["jpg", "jpeg", "png"];

pub struct ReplayOpts {
    pub fps: f64,
    pub max_speed: bool,
    pub looped: bool,
    pub size: Option<(u32, u32)>,
}

/// Lee los cuadros codificados de una carpeta o de un `.mjpeg`.
pub fn load(path: &Path) -> Result<Vec<Vec<u8>>, BoxError> {
    if path.is_dir() {
        let mut files: Vec<PathBuf> = std::fs::read_dir(path)?
            .filter_map(|e| e.ok().map(|e| e.path()))
            .filter(|p| {
                p.extension().and_then(|e| e.to_str()).is_some_and(|e| IMAGE_EXT.contains(&e.to_ascii_lowercase().as_str()))
            })
            .collect();
        files.sort();
        if files.is_empty() {
            return Err(format!("{} no tiene imágenes", path.display()).into());
        }
        return files.iter().map(|p| std::fs::read(p).map_err(Into::into)).collect();
    }
    let buf = std::fs::read(path)?;
    let parts: Vec<Vec<u8>> = crate::frame::split_jpegs(&buf).0.into_iter().map(<[u8]>::to_vec).collect();
    if parts.is_empty() {
        return Err(format!("{}: no es MJPEG ni carpeta de imágenes", path.display()).into());
    }
    Ok(parts)
}

/// Hilo que emite los cuadros hasta que la bomba suelte el receptor.
pub fn spawn(path: &Path, opts: ReplayOpts, tx: LatestTx) -> Result<std::thread::JoinHandle<()>, BoxError> {
    let frames = load(path)?;
    let period = Duration::from_secs_f64(1.0 / opts.fps.max(0.1));
    Ok(std::thread::Builder::new().name("replay".into()).spawn(move || {
        let mut t_next = Instant::now();
        loop {
            for raw in &frames {
                if tx.is_closed() {
                    return;
                }
                let img = match decode(raw) {
                    Ok(i) => fit(&i, opts.size).into_owned(),
                    Err(_) => continue,
                };
                if !opts.max_speed {
                    let now = Instant::now();
                    if t_next > now {
                        std::thread::sleep(t_next - now);
                    }
                    // si se atrasa, no acumula deuda
                    t_next = (t_next + period).max(Instant::now().checked_sub(period).unwrap_or(t_next));
                }
                tx.put(Frame { img, t_capture_ns: now_ns() });
            }
            if !opts.looped {
                return;
            }
        }
    })?)
}
