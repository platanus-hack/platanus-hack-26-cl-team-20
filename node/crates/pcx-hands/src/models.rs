//! Modelos ONNX de MediaPipe del OpenCV Model Zoo (Apache-2.0). No van en el repo: se bajan a la caché del SO
//! (`directories`: `~/Library/Caches/ai.pcx.pcx`, `~/.cache/pcx`, `%LOCALAPPDATA%\pcx\pcx\cache`) y se verifican
//! con sha256.
use crate::HandsError;
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};

pub struct ModelSpec {
    pub file: &'static str,
    pub url: &'static str,
    pub sha256: &'static str,
}

pub const PALM: ModelSpec = ModelSpec {
    file: "palm_detection_mediapipe_2023feb.onnx",
    url: "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/palm_detection_mediapipe/palm_detection_mediapipe_2023feb.onnx",
    sha256: "78ff51c38496b7fc8b8ebdb6cc8c1abb02fa6c38427c6848254cdaba57fcce7c",
};

pub const HANDPOSE: ModelSpec = ModelSpec {
    file: "handpose_estimation_mediapipe_2023feb.onnx",
    url: "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/handpose_estimation_mediapipe/handpose_estimation_mediapipe_2023feb.onnx",
    sha256: "db0898ae717b76b075d9bf563af315b29562e11f8df5027a1ef07b02bef6d81c",
};

/// Carpeta de modelos: `$PCX_MODELS_DIR` o la caché del SO + `models`.
pub fn models_dir() -> PathBuf {
    if let Some(d) = std::env::var_os("PCX_MODELS_DIR") {
        return PathBuf::from(d);
    }
    directories::ProjectDirs::from("ai", "pcx", "pcx")
        .map(|d| d.cache_dir().join("models"))
        .unwrap_or_else(|| std::env::temp_dir().join("pcx-models"))
}

pub fn sha256_file(path: &Path) -> Result<String, HandsError> {
    let bytes = std::fs::read(path)?;
    Ok(hex(&Sha256::digest(&bytes)))
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

/// Ruta del modelo si ya está en `dir` con el sha256 correcto.
pub fn cached(spec: &ModelSpec, dir: &Path) -> Option<PathBuf> {
    let p = dir.join(spec.file);
    (p.exists() && sha256_file(&p).ok()? == spec.sha256).then_some(p)
}

/// Devuelve la ruta del modelo; si falta (o el hash no calza) lo baja a `dir` (escritura atómica + sha256).
pub fn ensure(spec: &ModelSpec, dir: &Path) -> Result<PathBuf, HandsError> {
    if let Some(p) = cached(spec, dir) {
        return Ok(p);
    }
    download(spec, dir)
}

#[cfg(feature = "download")]
fn download(spec: &ModelSpec, dir: &Path) -> Result<PathBuf, HandsError> {
    use std::io::Read;
    std::fs::create_dir_all(dir)?;
    let resp = ureq::get(spec.url)
        .call()
        .map_err(|e| HandsError::Model(format!("{}: {e}", spec.url)))?;
    let mut bytes = Vec::new();
    resp.into_body()
        .into_reader()
        .take(64 << 20)
        .read_to_end(&mut bytes)?;
    let got = hex(&Sha256::digest(&bytes));
    if got != spec.sha256 {
        return Err(HandsError::Model(format!(
            "{}: sha256 {got} ≠ {}",
            spec.file, spec.sha256
        )));
    }
    let tmp = dir.join(format!("{}.part", spec.file));
    std::fs::write(&tmp, &bytes)?;
    let dst = dir.join(spec.file);
    std::fs::rename(&tmp, &dst)?;
    Ok(dst)
}

#[cfg(not(feature = "download"))]
fn download(spec: &ModelSpec, dir: &Path) -> Result<PathBuf, HandsError> {
    Err(HandsError::Model(format!(
        "falta {} en {} (compila con la feature `download` o bájalo de {})",
        spec.file,
        dir.display(),
        spec.url
    )))
}

/// Rutas `(palma, landmarks)` listas para usar (bajándolas si hace falta).
pub fn ensure_all() -> Result<(PathBuf, PathBuf), HandsError> {
    let dir = models_dir();
    Ok((ensure(&PALM, &dir)?, ensure(&HANDPOSE, &dir)?))
}
