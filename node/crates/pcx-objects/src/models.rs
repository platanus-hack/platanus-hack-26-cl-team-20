//! Interfaces de los modelos del nivel 3/5 (detector por texto y segmentador por caja/punto), la grilla
//! automática de SAM (`automatic_masks.py`) y la caché de modelos del SO con verificación sha256.

use crate::raster::{self, Mask};
use image::RgbImage;
use sha2::{Digest, Sha256};
use std::io::{Read, Write};
use std::path::PathBuf;

pub type BoxErr = Box<dyn std::error::Error + Send + Sync>;

/// Caja de un detector por texto (Grounding DINO), en px de la imagen dada.
#[derive(Debug, Clone, PartialEq)]
pub struct Detection {
    pub prompt: String,
    pub label: String,
    /// `[x0, y0, x1, y1]`
    pub bbox: [f32; 4],
    pub score: f32,
}

pub trait Detector: Send {
    fn detect(&mut self, img: &RgbImage, prompts: &[String], box_threshold: f32) -> Result<Vec<Detection>, BoxErr>;
}

/// Salida de un decodificador SAM: logits del tamaño de la imagen y el puntaje (IoU predicho).
pub struct SamOutput {
    pub width: u32,
    pub height: u32,
    pub logits: Vec<f32>,
    pub score: f32,
}

impl SamOutput {
    pub fn mask(&self, th: f32) -> Mask {
        let mut m = raster::empty(self.width, self.height);
        for (o, &l) in m.as_mut().iter_mut().zip(&self.logits) {
            *o = if l > th { 255 } else { 0 };
        }
        m
    }
}

/// Punto del prompt de SAM: etiqueta 1 = positivo, 0 = negativo, 2/3 = esquinas de caja, -1 = relleno.
pub type SamPoint = (f32, f32, f32);

pub trait Segmenter: Send {
    /// Calcula (y guarda) el embedding de la imagen. Se reutiliza para todos los prompts.
    fn set_image(&mut self, img: &RgbImage) -> Result<(), BoxErr>;
    fn predict(&mut self, points: &[SamPoint]) -> Result<SamOutput, BoxErr>;

    /// `MobileSAM.predict(box, positive=[punto])`.
    fn predict_box(&mut self, b: [f32; 4], positive: Option<(f32, f32)>) -> Result<(Mask, f32), BoxErr> {
        let mut pts: Vec<SamPoint> = positive.map(|(x, y)| vec![(x, y, 1.0)]).unwrap_or_default();
        pts.push((b[0], b[1], 2.0));
        pts.push((b[2], b[3], 3.0));
        let o = self.predict(&pts)?;
        Ok((o.mask(0.0), o.score))
    }
}

/// Propuesta de la grilla automática.
pub struct Proposal {
    pub mask: Mask,
    pub area: u64,
    pub score: f32,
    pub stability: f32,
}

/// Port de `automatic_masks()` (grilla de puntos + filtros de score/estabilidad + NMS por IoU 0.85).
pub fn automatic_masks(
    seg: &mut dyn Segmenter,
    img: &RgbImage,
    valid: &Mask,
    columns: u32,
    rows: u32,
    min_area: u64,
) -> Result<Vec<Proposal>, BoxErr> {
    let (score_th, stab_th) = (0.72f32, 0.82f32);
    let (w, h) = img.dimensions();
    seg.set_image(img)?;
    let mut cands: Vec<(Proposal, Mask)> = Vec::new();
    for r in 0..rows {
        let y = h as f32 / (2.0 * rows as f32) + r as f32 * (h as f32 - h as f32 / rows as f32) / (rows - 1).max(1) as f32;
        for c in 0..columns {
            let x = w as f32 / (2.0 * columns as f32) + c as f32 * (w as f32 - w as f32 / columns as f32) / (columns - 1).max(1) as f32;
            let (xi, yi) = ((x.round() as u32).min(w - 1), (y.round() as u32).min(h - 1));
            if valid.get_pixel(xi, yi).0[0] == 0 {
                continue;
            }
            let o = seg.predict(&[(x, y, 1.0), (0.0, 0.0, -1.0)])?;
            let hi = o.logits.iter().filter(|&&l| l > 2.0).count() as f32;
            let lo = o.logits.iter().filter(|&&l| l > -2.0).count().max(1) as f32;
            let stability = hi / lo;
            if !o.score.is_finite() || o.score < score_th || stability < stab_th {
                continue;
            }
            let mask = raster::and(&o.mask(0.0), valid);
            let area = raster::count(&mask);
            if area < min_area || area as f64 > (w * h) as f64 * 0.8 {
                continue;
            }
            let small = image::imageops::resize(&mask, w.min(240), h.min(135), image::imageops::FilterType::Nearest);
            cands.push((Proposal { mask, area, score: o.score, stability }, small));
        }
    }
    cands.sort_by(|a, b| (b.0.score * b.0.stability).partial_cmp(&(a.0.score * a.0.stability)).unwrap_or(std::cmp::Ordering::Equal));
    let mut sel: Vec<(Proposal, Mask)> = Vec::new();
    for (p, s) in cands {
        let dup = sel.iter().any(|(_, q)| {
            let i = raster::count(&raster::and(&s, q));
            let u = raster::count(&raster::or(&s, q)).max(1);
            i as f64 / u as f64 > 0.85
        });
        if !dup {
            sel.push((p, s));
        }
    }
    let mut out: Vec<Proposal> = sel.into_iter().map(|(p, _)| p).collect();
    out.sort_by_key(|p| std::cmp::Reverse(p.area));
    Ok(out)
}

/// Archivo de modelo descargable con sha256 fijo.
#[derive(Debug, Clone, Copy)]
pub struct ModelFile {
    pub url: &'static str,
    pub file_name: &'static str,
    pub sha256: &'static str,
}

/// Grounding DINO tiny (ONNX, `onnx-community/grounding-dino-tiny-ONNX`) y MobileSAM (`Acly/MobileSAM`).
pub mod catalog {
    use super::ModelFile;
    /// fp32 (719 MB): el que se usa. Las variantes int8/uint8 usan `ConvInteger`, que el EP de CPU de ORT no tiene.
    pub const DINO_MODEL: ModelFile = ModelFile {
        url: "https://huggingface.co/onnx-community/grounding-dino-tiny-ONNX/resolve/main/onnx/model.onnx",
        file_name: "grounding-dino-tiny.onnx",
        sha256: "267c8b00d269dc2dda0d8592812d3027abd5fb59f1cabf8e3d56f8fdf07f68ec",
    };
    /// int8 (204 MB): NO corre en el EP de CPU (`ConvInteger(10)` sin implementación); queda para otros EP.
    pub const DINO_MODEL_INT8: ModelFile = ModelFile {
        url: "https://huggingface.co/onnx-community/grounding-dino-tiny-ONNX/resolve/main/onnx/model_int8.onnx",
        file_name: "grounding-dino-tiny-int8.onnx",
        sha256: "3bff430de583461ab3c1e8b99b19508f4fb238bf0fea0cde2c45f840a0082a26",
    };
    pub const DINO_TOKENIZER: ModelFile = ModelFile {
        url: "https://huggingface.co/onnx-community/grounding-dino-tiny-ONNX/resolve/main/tokenizer.json",
        file_name: "grounding-dino-tiny.tokenizer.json",
        sha256: "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66",
    };
    pub const SAM_ENCODER: ModelFile = ModelFile {
        url: "https://huggingface.co/Acly/MobileSAM/resolve/main/mobile_sam_image_encoder.onnx",
        file_name: "mobile_sam_image_encoder.onnx",
        sha256: "580f5fb648ea1062c0aabc26217aed56921985f03f0cbbd852bba81d760cc749",
    };
    pub const SAM_DECODER: ModelFile = ModelFile {
        url: "https://huggingface.co/Acly/MobileSAM/resolve/main/sam_mask_decoder_single.onnx",
        file_name: "sam_mask_decoder_single.onnx",
        sha256: "93915fc7c993ab9d59ab8c9ccd3bce37f7509c81ab4150a74abd4d2abbd8570d",
    };
}

/// Carpeta de caché del SO: `~/Library/Caches/pcx/models` (macOS), `%LOCALAPPDATA%\pcx\models` (Windows),
/// `~/.cache/pcx/models` (Linux). Se puede forzar con `PCX_MODELS_DIR`.
pub fn cache_dir() -> PathBuf {
    if let Ok(d) = std::env::var("PCX_MODELS_DIR") {
        return PathBuf::from(d);
    }
    dirs::cache_dir().unwrap_or_else(std::env::temp_dir).join("pcx").join("models")
}

pub fn sha256_file(p: &std::path::Path) -> std::io::Result<String> {
    let mut f = std::fs::File::open(p)?;
    let mut h = Sha256::new();
    let mut buf = vec![0u8; 1 << 20];
    loop {
        let n = f.read(&mut buf)?;
        if n == 0 {
            break;
        }
        h.update(&buf[..n]);
    }
    Ok(h.finalize().iter().map(|b| format!("{b:02x}")).collect())
}

/// Devuelve la ruta local del modelo; lo descarga si falta y verifica el sha256 (si está fijado).
pub fn ensure(m: &ModelFile) -> Result<PathBuf, BoxErr> {
    let dir = cache_dir();
    std::fs::create_dir_all(&dir)?;
    let path = dir.join(m.file_name);
    if path.exists() && (m.sha256.is_empty() || sha256_file(&path)? == m.sha256) {
        return Ok(path);
    }
    let tmp = dir.join(format!("{}.part", m.file_name));
    let mut resp = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(1800))
        .build()?
        .get(m.url)
        .send()?
        .error_for_status()?;
    let mut f = std::fs::File::create(&tmp)?;
    let mut buf = vec![0u8; 1 << 20];
    loop {
        let n = resp.read(&mut buf)?;
        if n == 0 {
            break;
        }
        f.write_all(&buf[..n])?;
    }
    drop(f);
    let got = sha256_file(&tmp)?;
    if !m.sha256.is_empty() && got != m.sha256 {
        let _ = std::fs::remove_file(&tmp);
        return Err(format!("sha256 de {} no coincide: {got}", m.file_name).into());
    }
    std::fs::rename(&tmp, &path)?;
    Ok(path)
}
