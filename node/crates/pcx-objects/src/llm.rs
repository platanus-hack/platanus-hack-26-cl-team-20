//! Juez visual (niveles 4 y 5): Claude por HTTP crudo (`POST /v1/messages`, no hay SDK oficial de Rust) y un mock
//! determinista para tests. Cada llamada se registra en un `.jsonl` (modelo, segundos, tokens), como `llm_uso.jsonl`.

use crate::raster;
use base64::Engine;
use image::RgbImage;
use serde_json::{json, Value};
use std::io::Write;
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::{Duration, Instant};

pub const DEFAULT_MODEL: &str = "claude-opus-5-5";
pub const API_URL: &str = "https://api.anthropic.com/v1/messages";
pub const API_VERSION: &str = "2026-10-21";
pub const FALLBACK_BETA: &str = "server-side-fallback-2026-07-01";
/// Lado mayor máximo de las imágenes enviadas (las cajas que devuelva Claude vienen en esta escala).
pub const MAX_SIDE: u32 = 1568;

#[derive(Debug, thiserror::Error)]
pub enum LlmError {
    #[error("sin ANTHROPIC_API_KEY")]
    NoKey,
    #[error("http: {0}")]
    Http(String),
    #[error("api {status}: {body}")]
    Api { status: u16, body: String },
    #[error("Claude rechazó la petición (refusal, categoría {category:?})")]
    Refusal { category: Option<String> },
    #[error("respuesta sin JSON válido: {0}")]
    BadJson(String),
}

/// Imagen ya escalada para enviar, con el factor para volver a px originales.
#[derive(Debug, Clone)]
pub struct SentImage {
    pub media_type: &'static str,
    pub bytes: Vec<u8>,
    /// px originales por px enviado.
    pub scale: f64,
    pub width: u32,
    pub height: u32,
}

impl SentImage {
    /// JPEG (calidad 88), reducido si el lado mayor pasa de `MAX_SIDE`.
    pub fn jpeg(img: &RgbImage) -> SentImage {
        let (w, h) = img.dimensions();
        let k = (MAX_SIDE as f64 / w.max(h) as f64).min(1.0);
        let (nw, nh) = (((w as f64) * k).round().max(1.0) as u32, ((h as f64) * k).round().max(1.0) as u32);
        let small = if k < 1.0 {
            image::imageops::resize(img, nw, nh, image::imageops::FilterType::Triangle)
        } else {
            img.clone()
        };
        SentImage {
            media_type: "image/jpeg",
            bytes: raster::jpeg_bytes(&small, 88),
            scale: 1.0 / k,
            width: nw,
            height: nh,
        }
    }

    /// Lleva una caja `[x0, y0, x1, y1]` en px de la imagen enviada a px de la imagen original.
    pub fn box_to_original(&self, b: [f64; 4]) -> [f64; 4] {
        b.map(|v| v * self.scale)
    }
}

/// Parte del mensaje: texto o imagen (se intercalan para rotular sin dibujar texto).
#[derive(Debug, Clone)]
pub enum Part {
    Text(String),
    Image(SentImage),
}

#[derive(Debug, Clone, Default)]
pub struct Usage {
    pub model: String,
    pub seconds: f64,
    pub input_tokens: u64,
    pub output_tokens: u64,
}

/// Juez visual: recibe partes y un JSON Schema, devuelve el JSON pedido.
pub trait VisionLlm: Send {
    fn ask_json(&mut self, parts: &[Part], schema: &Value, effort: &str) -> Result<Value, LlmError>;
    /// Uso acumulado (para medir costo).
    fn usage(&self) -> Vec<Usage>;
}

pub struct ClaudeClient {
    pub model: String,
    pub max_tokens: u32,
    api_key: String,
    http: reqwest::Client,
    log_path: Option<PathBuf>,
    usage: Mutex<Vec<Usage>>,
}

impl ClaudeClient {
    /// Lee `ANTHROPIC_API_KEY` y `PCX_LLM_MODEL` (por defecto `claude-opus-5-5`).
    pub fn from_env() -> Result<ClaudeClient, LlmError> {
        let key = std::env::var("ANTHROPIC_API_KEY").ok().filter(|k| !k.is_empty()).ok_or(LlmError::NoKey)?;
        let model = std::env::var("PCX_LLM_MODEL").unwrap_or_else(|_| DEFAULT_MODEL.to_string());
        Ok(ClaudeClient::new(key, model))
    }

    pub fn new(api_key: String, model: String) -> ClaudeClient {
        let http = reqwest::Client::builder().timeout(Duration::from_secs(180)).build().expect("cliente http");
        ClaudeClient { model, max_tokens: 16000, api_key, http, log_path: None, usage: Mutex::new(Vec::new()) }
    }

    /// Archivo `.jsonl` donde registrar cada llamada.
    pub fn with_usage_log(mut self, p: PathBuf) -> Self {
        self.log_path = Some(p);
        self
    }

    /// Modelos con fallback del servidor (`fallbacks: "default"`).
    pub fn supports_fallback(model: &str) -> bool {
        ["claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-fable-5-2", "claude-sonnet-5-5"]
            .contains(&model)
    }

    /// Cuerpo y headers extra de la petición (separado para testearlo sin red).
    pub fn build_request(&self, parts: &[Part], schema: &Value, effort: &str) -> (Value, Vec<(&'static str, String)>) {
        let content: Vec<Value> = parts
            .iter()
            .map(|p| match p {
                Part::Text(t) => json!({"type": "text", "text": t}),
                Part::Image(i) => json!({"type": "image", "source": {"type": "base64", "media_type": i.media_type,
                    "data": base64::engine::general_purpose::STANDARD.encode(&i.bytes)}}),
            })
            .collect();
        let mut body = json!({
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": content}],
            "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        });
        let mut headers = vec![];
        if Self::supports_fallback(&self.model) {
            body["fallbacks"] = json!("default");
            headers.push(("anthropic-beta", FALLBACK_BETA.to_string()));
        }
        (body, headers)
    }

    /// Interpreta la respuesta: `stop_reason` primero (refusal), después el texto (ignora `thinking`/`fallback`).
    pub fn parse_response(v: &Value) -> Result<Value, LlmError> {
        if v.get("stop_reason").and_then(Value::as_str) == Some("refusal") {
            let category = v.pointer("/stop_details/category").and_then(Value::as_str).map(str::to_string);
            return Err(LlmError::Refusal { category });
        }
        let text: String = v
            .get("content")
            .and_then(Value::as_array)
            .map(|a| a.iter().filter(|b| b["type"] == "text").filter_map(|b| b["text"].as_str()).collect())
            .unwrap_or_default();
        extract_json(&text).ok_or(LlmError::BadJson(text))
    }

    fn record(&self, u: Usage) {
        if let Some(p) = &self.log_path {
            if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(p) {
                let t = std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .map(|d| d.as_secs_f64())
                    .unwrap_or(0.0);
                let line = json!({"t": t, "modelo": u.model, "seg": (u.seconds * 10.0).round() / 10.0,
                    "tokens": u.input_tokens + u.output_tokens, "input_tokens": u.input_tokens, "output_tokens": u.output_tokens});
                let _ = writeln!(f, "{line}");
            }
        }
        self.usage.lock().expect("uso").push(u);
    }
}

impl ClaudeClient {
    /// Camino async para llamadores Tokio; la API síncrona pertenece al hilo del worker.
    pub async fn ask_json_async(&self, parts: &[Part], schema: &Value, effort: &str) -> Result<Value, LlmError> {
        let t0 = Instant::now();
        let (body, extra) = self.build_request(parts, schema, effort);
        let mut req = self
            .http
            .post(API_URL)
            .header("x-api-key", &self.api_key)
            .header("anthropic-version", API_VERSION)
            .header("content-type", "application/json");
        for (k, v) in extra {
            req = req.header(k, v);
        }
        let resp = req.json(&body).send().await.map_err(|e| LlmError::Http(e.to_string()))?;
        let status = resp.status().as_u16();
        let v: Value = resp.json().await.map_err(|e| LlmError::Http(e.to_string()))?;
        let usage = Usage {
            model: v["model"].as_str().unwrap_or(&self.model).to_string(),
            seconds: t0.elapsed().as_secs_f64(),
            input_tokens: v.pointer("/usage/input_tokens").and_then(Value::as_u64).unwrap_or(0),
            output_tokens: v.pointer("/usage/output_tokens").and_then(Value::as_u64).unwrap_or(0),
        };
        self.record(usage);
        if status != 200 {
            return Err(LlmError::Api { status, body: v.to_string() });
        }
        Self::parse_response(&v)
    }
}

impl VisionLlm for ClaudeClient {
    fn ask_json(&mut self, parts: &[Part], schema: &Value, effort: &str) -> Result<Value, LlmError> {
        if tokio::runtime::Handle::try_current().is_ok() {
            return Err(LlmError::Http("usar ask_json_async o un hilo de worker fuera de Tokio".into()));
        }
        tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .map_err(|e| LlmError::Http(e.to_string()))?
            .block_on(self.ask_json_async(parts, schema, effort))
    }

    fn usage(&self) -> Vec<Usage> {
        self.usage.lock().expect("uso").clone()
    }
}

/// Último objeto `{...}` del texto (como el `re.findall(r"\{.*\}")` del original).
pub fn extract_json(text: &str) -> Option<Value> {
    if let Ok(v) = serde_json::from_str::<Value>(text.trim()) {
        return Some(v);
    }
    let (a, b) = (text.find('{')?, text.rfind('}')?);
    serde_json::from_str(&text[a..=b]).ok()
}

/// Mock determinista: devuelve respuestas en cola (o una fija) y registra qué se le preguntó.
pub struct MockLlm {
    pub replies: std::collections::VecDeque<Value>,
    pub default: Value,
    pub calls: Vec<(usize, String)>,
}

impl MockLlm {
    pub fn new(default: Value) -> MockLlm {
        MockLlm { replies: Default::default(), default, calls: vec![] }
    }
    pub fn then(mut self, v: Value) -> MockLlm {
        self.replies.push_back(v);
        self
    }
}

impl VisionLlm for MockLlm {
    fn ask_json(&mut self, parts: &[Part], _schema: &Value, _effort: &str) -> Result<Value, LlmError> {
        let n_img = parts.iter().filter(|p| matches!(p, Part::Image(_))).count();
        let txt = parts
            .iter()
            .filter_map(|p| if let Part::Text(t) = p { Some(t.as_str()) } else { None })
            .collect::<Vec<_>>()
            .join(" ");
        self.calls.push((n_img, txt));
        Ok(self.replies.pop_front().unwrap_or_else(|| self.default.clone()))
    }
    fn usage(&self) -> Vec<Usage> {
        self.calls.iter().map(|_| Usage { model: "mock".into(), ..Default::default() }).collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn request_has_fallback_and_schema() {
        let c = ClaudeClient::new("k".into(), DEFAULT_MODEL.into());
        let img = SentImage::jpeg(&RgbImage::new(3136, 100));
        assert_eq!(img.width, 1568);
        assert_eq!(img.box_to_original([10.0, 0.0, 20.0, 50.0]), [20.0, 0.0, 40.0, 100.0]);
        let (body, headers) =
            c.build_request(&[Part::Text("hola".into()), Part::Image(img)], &json!({"type": "object"}), "low");
        assert_eq!(body["fallbacks"], "default");
        assert_eq!(body["model"], "claude-opus-5-5");
        assert_eq!(body["output_config"]["effort"], "low");
        assert_eq!(body["messages"][0]["content"][1]["source"]["media_type"], "image/jpeg");
        assert!(body.get("temperature").is_none());
        assert_eq!(headers, vec![("anthropic-beta", FALLBACK_BETA.to_string())]);
        let other = ClaudeClient::new("k".into(), "claude-haiku-4-5".into());
        assert!(other.build_request(&[], &json!({}), "low").0.get("fallbacks").is_none());
    }

    #[test]
    fn parse_refusal_before_content() {
        let v = json!({"stop_reason": "refusal", "stop_details": {"type": "refusal", "category": "cyber"},
                       "content": [{"type": "text", "text": "{\"n\": 1}"}]});
        assert!(
            matches!(ClaudeClient::parse_response(&v), Err(LlmError::Refusal { category: Some(c) }) if c == "cyber")
        );
        let ok = json!({"stop_reason": "end_turn", "content": [{"type": "thinking", "thinking": ""},
            {"type": "fallback", "from": {}, "to": {}}, {"type": "text", "text": "Listo: {\"n\": 2}"}]});
        assert_eq!(ClaudeClient::parse_response(&ok).unwrap(), json!({"n": 2}));
    }
}

#[cfg(test)]
mod async_tests {
    use super::*;
    #[test]
    fn synchronous_api_inside_tokio_returns_error_without_panicking() {
        tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap().block_on(async {
            let mut client = ClaudeClient::new("test".into(), DEFAULT_MODEL.into());
            let err = client.ask_json(&[], &json!({}), "low").unwrap_err();
            assert!(err.to_string().contains("ask_json_async"));
        });
    }
}
