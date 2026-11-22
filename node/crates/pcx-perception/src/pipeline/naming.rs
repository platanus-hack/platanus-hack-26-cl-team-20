//! Nombres de los objetos. Por defecto `objeto-N`. El gancho [`Namer`] permite ponerles nombre aparte, sin frenar
//! el ciclo: se le pide el nombre cuando el objeto aparece y se recoge cuando esté listo (ciclos después).
//!
//! [`LlmNamer`] lo hace con un juez visual (`pcx_objects::llm::VisionLlm`, p. ej. Claude) en un hilo propio.
//! Ninguna llamada de red ocurre en el camino de `on_clean`.

use image::RgbImage;
use pcx_objects::llm::{ClaudeClient, Part, SentImage, VisionLlm};
use serde_json::{json, Value};
use std::sync::mpsc::{channel, Receiver, Sender};

/// Gancho de nombres. Las dos llamadas deben volver al instante.
pub trait Namer: Send {
    /// Pide nombre para el objeto `id`, visto en `crop` (recorte de la foto limpia rectificada).
    fn request(&mut self, id: &str, crop: &RgbImage);
    /// Nombres que quedaron listos desde la última llamada: `(id, nombre)`.
    fn poll(&mut self) -> Vec<(String, String)>;
}

/// Máximo de recortes por pregunta al juez.
const BATCH: usize = 8;

/// Nombra con un juez visual en un hilo de trabajo (fuera de Tokio, como pide `ClaudeClient::ask_json`).
pub struct LlmNamer {
    jobs: Sender<(String, RgbImage)>,
    done: Receiver<(String, String)>,
}

impl LlmNamer {
    pub fn spawn(mut llm: Box<dyn VisionLlm>) -> LlmNamer {
        let (jobs, job_rx) = channel::<(String, RgbImage)>();
        let (done_tx, done) = channel::<(String, String)>();
        let worker = move || {
            while let Ok(first) = job_rx.recv() {
                let mut batch = vec![first];
                while batch.len() < BATCH {
                    match job_rx.try_recv() {
                        Ok(job) => batch.push(job),
                        Err(_) => break,
                    }
                }
                for (id, name) in name_batch(llm.as_mut(), &batch) {
                    if done_tx.send((id, name)).is_err() {
                        return;
                    }
                }
            }
        };
        // Si el hilo no se puede crear, las peticiones se pierden y los objetos quedan como `objeto-N`.
        let _ = std::thread::Builder::new().name("pcx-nombres".into()).spawn(worker);
        LlmNamer { jobs, done }
    }

    /// Claude visión si hay `ANTHROPIC_API_KEY` (modelo: `PCX_LLM_MODEL` o el de `pcx_objects::llm`).
    pub fn claude_from_env() -> Option<LlmNamer> {
        ClaudeClient::from_env().ok().map(|c| LlmNamer::spawn(Box::new(c)))
    }
}

impl Namer for LlmNamer {
    fn request(&mut self, id: &str, crop: &RgbImage) {
        let _ = self.jobs.send((id.to_string(), crop.clone()));
    }

    fn poll(&mut self) -> Vec<(String, String)> {
        let mut out = vec![];
        while let Ok(pair) = self.done.try_recv() {
            out.push(pair);
        }
        out
    }
}

/// Una pregunta para todo el lote. Si falla (red, rechazo, JSON raro), no devuelve nombres: quedan los de siempre.
fn name_batch(llm: &mut dyn VisionLlm, batch: &[(String, RgbImage)]) -> Vec<(String, String)> {
    let mut parts = vec![];
    for (k, (_, crop)) in batch.iter().enumerate() {
        parts.push(Part::Text(format!("Objeto {}:", k + 1)));
        parts.push(Part::Image(SentImage::jpeg(crop)));
    }
    parts.push(Part::Text(format!(
        "Son {} recortes de una foto cenital de una mesa, uno por objeto. Dale a cada uno un nombre corto en español \
         (1 a 3 palabras, en minúsculas, sin artículo): «taza», «cuchara», «pastillero». Si no se distingue qué es, \
         usa null. Responde SOLO JSON: {{\"nombres\": [\"taza\", null]}} con un elemento por objeto, en orden.",
        batch.len()
    )));
    let schema = json!({"type": "object", "additionalProperties": false, "required": ["nombres"], "properties": {
        "nombres": {"type": "array", "items": {"anyOf": [{"type": "string"}, {"type": "null"}]}}}});
    let Ok(res) = llm.ask_json(&parts, &schema, "low") else { return vec![] };
    let Some(names) = res.get("nombres").and_then(Value::as_array) else { return vec![] };
    batch
        .iter()
        .zip(names)
        .filter_map(|((id, _), n)| {
            let n = n.as_str()?.trim().to_lowercase();
            (!n.is_empty() && n.chars().count() <= 40).then(|| (id.clone(), n))
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use pcx_objects::llm::MockLlm;

    #[test]
    fn nombra_un_lote_con_un_juez_simulado() {
        let mut llm = MockLlm::new(json!({"nombres": ["Taza", null, "  cuchara "]}));
        let crop = RgbImage::new(8, 8);
        let batch: Vec<(String, RgbImage)> =
            ["obj-1", "obj-2", "obj-3"].map(|id| (id.to_string(), crop.clone())).into();
        let got = name_batch(&mut llm, &batch);
        assert_eq!(got, vec![("obj-1".to_string(), "taza".to_string()), ("obj-3".to_string(), "cuchara".to_string())]);
        // una sola pregunta, con las tres imágenes
        assert_eq!(llm.calls.len(), 1);
        assert_eq!(llm.calls[0].0, 3);
    }

    #[test]
    fn el_hilo_devuelve_el_nombre_sin_bloquear() {
        let mut namer = LlmNamer::spawn(Box::new(MockLlm::new(json!({"nombres": ["pastillero"]}))));
        namer.request("obj-7", &RgbImage::new(8, 8));
        let mut got = vec![];
        for _ in 0..400 {
            got.extend(namer.poll());
            if !got.is_empty() {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(5));
        }
        assert_eq!(got, vec![("obj-7".to_string(), "pastillero".to_string())]);
    }

    #[test]
    fn un_juez_que_falla_no_rompe_nada() {
        let mut llm = MockLlm::new(json!({"otra_cosa": 1}));
        assert!(name_batch(&mut llm, &[("a".into(), RgbImage::new(4, 4))]).is_empty());
    }
}
