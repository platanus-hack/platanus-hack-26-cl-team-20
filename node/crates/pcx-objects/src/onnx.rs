//! Nivel 3 local con ONNX Runtime (`ort`): Grounding DINO tiny (`onnx-community/grounding-dino-tiny-ONNX`, texto con
//! `tokenizers`) y MobileSAM (`Acly/MobileSAM`, encoder con el preprocesado adentro + decoder de una máscara).
//! Mismo preprocesado y posproceso que `detector_dino.py` / `appearance_prepare.MobileSAM`.

use crate::models::{self, catalog, BoxErr, Detection, Detector, ModelFile, SamOutput, SamPoint, Segmenter};
use image::RgbImage;
use ort::session::Session;
use ort::value::Tensor;

fn session(path: &std::path::Path, threads: usize) -> Result<Session, BoxErr> {
    Ok(Session::builder()?.with_intra_threads(threads)?.commit_from_file(path)?)
}

pub struct GroundingDino {
    session: Session,
    tokenizer: tokenizers::Tokenizer,
    /// Umbral de texto (`text_threshold` = 0.2 en el original); solo para rotular.
    pub text_threshold: f32,
}

impl GroundingDino {
    /// Descarga (si falta) a la caché del SO y carga el modelo int8.
    pub fn load() -> Result<GroundingDino, BoxErr> {
        Self::load_with(catalog::DINO_MODEL, 6)
    }

    pub fn load_with(model: ModelFile, threads: usize) -> Result<GroundingDino, BoxErr> {
        let m = models::ensure(&model)?;
        let t = models::ensure(&catalog::DINO_TOKENIZER)?;
        Ok(GroundingDino {
            session: session(&m, threads)?,
            tokenizer: tokenizers::Tokenizer::from_file(t)?,
            text_threshold: 0.2,
        })
    }
}

fn sigmoid(x: f32) -> f32 {
    1.0 / (1.0 + (-x).exp())
}

impl Detector for GroundingDino {
    fn detect(&mut self, img: &RgbImage, prompts: &[String], box_threshold: f32) -> Result<Vec<Detection>, BoxErr> {
        const S: u32 = 800; // preprocessor_config.json: size 800×800, bilineal, /255, media/desvío de ImageNet
        let (w0, h0) = img.dimensions();
        let small = image::imageops::resize(img, S, S, image::imageops::FilterType::Triangle);
        let (mean, std) = ([0.485f32, 0.456, 0.406], [0.229f32, 0.224, 0.225]);
        let n = (S * S) as usize;
        let mut px = vec![0f32; 3 * n];
        for (i, p) in small.pixels().enumerate() {
            for c in 0..3 {
                px[c * n + i] = (p.0[c] as f32 / 255.0 - mean[c]) / std[c];
            }
        }
        // texto: "a mug. a cloth." en minúsculas, como detector_dino.py
        let cleaned: Vec<String> = prompts.iter().map(|p| p.trim().trim_end_matches('.').to_lowercase()).collect();
        let text = cleaned.join(". ") + ".";
        let enc = self.tokenizer.encode(text.as_str(), true)?;
        let ids: Vec<i64> = enc.get_ids().iter().map(|&v| v as i64).collect();
        let l = ids.len();
        // a qué prompt pertenece cada token (por offsets de caracteres)
        let mut spans = vec![];
        let mut start = 0usize;
        for c in &cleaned {
            spans.push((start, start + c.len()));
            start += c.len() + 2;
        }
        let tok_prompt: Vec<Option<usize>> = enc
            .get_offsets()
            .iter()
            .zip(enc.get_special_tokens_mask())
            .map(|(&(a, b), &sp)| if sp == 1 || a == b { None } else { spans.iter().position(|&(s, e)| a >= s && b <= e) })
            .collect();
        let outputs = self.session.run(ort::inputs![
            "pixel_values" => Tensor::from_array(([1usize, 3, S as usize, S as usize], px))?,
            "input_ids" => Tensor::from_array(([1usize, l], ids))?,
            "token_type_ids" => Tensor::from_array(([1usize, l], vec![0i64; l]))?,
            "attention_mask" => Tensor::from_array(([1usize, l], vec![1i64; l]))?,
            "pixel_mask" => Tensor::from_array(([1usize, S as usize, S as usize], vec![1i64; n]))?,
        ])?;
        let (lshape, logits) = outputs["logits"].try_extract_tensor::<f32>()?;
        let (_, boxes) = outputs["pred_boxes"].try_extract_tensor::<f32>()?;
        let (nq, nt) = (lshape[1] as usize, lshape[2] as usize);
        let mut dets = vec![];
        for q in 0..nq {
            let row = &logits[q * nt..q * nt + nt];
            let probs: Vec<f32> = row.iter().take(l).map(|&v| sigmoid(v)).collect();
            let score = probs.iter().cloned().fold(0.0, f32::max);
            if score <= box_threshold {
                continue;
            }
            // prompt con mayor probabilidad entre sus tokens
            let mut best = (0usize, -1f32);
            for (t, &pt) in probs.iter().enumerate() {
                if let Some(k) = tok_prompt.get(t).copied().flatten() {
                    if pt > best.1 {
                        best = (k, pt);
                    }
                }
            }
            let label: Vec<String> = probs
                .iter()
                .enumerate()
                .filter(|&(t, &pt)| pt > self.text_threshold && tok_prompt.get(t).copied().flatten().is_some())
                .filter_map(|(t, _)| self.tokenizer.id_to_token(enc.get_ids()[t]))
                .collect();
            let b = &boxes[q * 4..q * 4 + 4];
            let (cx, cy, bw, bh) = (b[0] * w0 as f32, b[1] * h0 as f32, b[2] * w0 as f32, b[3] * h0 as f32);
            dets.push(Detection {
                prompt: prompts.get(best.0).cloned().unwrap_or_default(),
                label: label.join(" "),
                bbox: [cx - bw / 2.0, cy - bh / 2.0, cx + bw / 2.0, cy + bh / 2.0],
                score,
            });
        }
        dets.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(std::cmp::Ordering::Equal));
        Ok(dets)
    }
}

pub struct MobileSam {
    encoder: Session,
    decoder: Session,
    embedding: Option<(Vec<usize>, Vec<f32>)>,
    shape: (u32, u32),
    resized: (u32, u32),
}

impl MobileSam {
    pub fn load() -> Result<MobileSam, BoxErr> {
        let e = models::ensure(&catalog::SAM_ENCODER)?;
        let d = models::ensure(&catalog::SAM_DECODER)?;
        Ok(MobileSam { encoder: session(&e, 4)?, decoder: session(&d, 4)?, embedding: None, shape: (0, 0), resized: (0, 0) })
    }
}

impl Segmenter for MobileSam {
    fn set_image(&mut self, img: &RgbImage) -> Result<(), BoxErr> {
        let (w, h) = img.dimensions();
        let s = 1024.0 / w.max(h) as f64;
        let (rw, rh) = ((w as f64 * s).round() as u32, (h as f64 * s).round() as u32);
        let small = image::imageops::resize(img, rw, rh, image::imageops::FilterType::Triangle);
        let data: Vec<f32> = small.as_raw().iter().map(|&v| v as f32).collect();
        let out = self.encoder.run(ort::inputs!["input_image" => Tensor::from_array(([rh as usize, rw as usize, 3], data))?])?;
        let (shape, emb) = out[0].try_extract_tensor::<f32>()?;
        self.embedding = Some((shape.iter().map(|&d| d as usize).collect(), emb.to_vec()));
        self.shape = (w, h);
        self.resized = (rw, rh);
        Ok(())
    }

    fn predict(&mut self, pts: &[SamPoint]) -> Result<SamOutput, BoxErr> {
        let (shape, emb) = self.embedding.clone().ok_or("set_image antes de predict")?;
        let (w, h) = self.shape;
        let (sx, sy) = (self.resized.0 as f32 / w as f32, self.resized.1 as f32 / h as f32);
        let coords: Vec<f32> = pts.iter().flat_map(|p| [p.0 * sx, p.1 * sy]).collect();
        let labels: Vec<f32> = pts.iter().map(|p| p.2).collect();
        let n = pts.len();
        let out = self.decoder.run(ort::inputs![
            "image_embeddings" => Tensor::from_array((shape, emb))?,
            "point_coords" => Tensor::from_array(([1usize, n, 2], coords))?,
            "point_labels" => Tensor::from_array(([1usize, n], labels))?,
            "mask_input" => Tensor::from_array(([1usize, 1, 256, 256], vec![0f32; 256 * 256]))?,
            "has_mask_input" => Tensor::from_array(([1usize], vec![0f32]))?,
            "orig_im_size" => Tensor::from_array(([2usize], vec![h as f32, w as f32]))?,
        ])?;
        let (_, masks) = out[0].try_extract_tensor::<f32>()?;
        let (_, scores) = out[1].try_extract_tensor::<f32>()?;
        Ok(SamOutput { width: w, height: h, logits: masks[..(w * h) as usize].to_vec(), score: scores[0] })
    }
}
