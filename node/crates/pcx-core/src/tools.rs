//! Tools MCP de `contracts/schema/tools.json`, implementadas sobre el actor. Las usan MCP (HTTP/stdio) y el enlace a
//! la nube (`tool_call` → `tool_result`) con el mismo resultado neutro. Cada llamada queda en `audit`.

use std::sync::Arc;
use std::time::Instant;

use base64::Engine;
use serde_json::{json, Value};

use crate::frames::{summary_text, FrameSource};
use crate::proto::*;
use crate::world::WorldHandle;

/// `tools/list` tal cual lo genera `contracts` (esquemas planos, anotaciones incluidas).
pub const TOOLS_JSON: &str = include_str!("../../../../contracts/schema/tools.json");

pub const INSTRUCTIONS: &str = "pcx: una mesa física con cámara cenital y proyector. Coordenadas en mm (marco table_mm): origen arriba a la \
izquierda, x a la derecha, y hacia abajo.
1. `scene` (JSON) o `look` (imagen + texto) para ver qué hay. Usa los id de objeto para anclar widgets (anchor.object_id): \
si el objeto se mueve, el widget lo sigue.
2. `project` crea o reemplaza widgets (label, halo, arrow, panel, button, image, effect); `remove` los borra.
3. `ask` proyecta una pregunta con botones y espera el toque. `events_wait(cursor)` hace long-poll de eventos.
4. `run_flow` ejecuta en la mesa una máquina de estados (guías paso a paso) sin pasar por ti en cada toque.
Las tools que cambian la mesa piden `request_id` (único por operación; repítelo solo al reintentar la misma).
Textos cortos y legibles (size_mm 10–20). Deja la mesa limpia al terminar.";

pub fn tool_names() -> Vec<String> {
    let v: Vec<Value> = serde_json::from_str(TOOLS_JSON).unwrap_or_default();
    v.iter().filter_map(|t| t.get("name").and_then(Value::as_str).map(String::from)).collect()
}

#[derive(Debug, Clone, Default)]
pub struct ToolOutput {
    pub text: String,
    pub image: Option<(Vec<u8>, String)>,
    pub structured: Option<Value>,
    pub is_error: bool,
    pub request_id: Option<String>,
}

impl ToolOutput {
    fn data(v: Value) -> Self {
        ToolOutput { text: serde_json::to_string(&v).unwrap_or_default(), structured: Some(v), ..Default::default() }
    }
    pub fn error(msg: impl Into<String>) -> Self {
        ToolOutput { text: msg.into(), is_error: true, ..Default::default() }
    }

    pub fn to_cloud(&self, call_id: &str) -> CloudMessage {
        let mut content = vec![];
        if let Some((img, mime)) = &self.image {
            content.push(ContentItem::Image { data: base64::engine::general_purpose::STANDARD.encode(img), mime_type: mime.clone() });
        }
        content.push(ContentItem::Text { text: self.text.clone() });
        CloudMessage::ToolResult {
            call_id: call_id.into(),
            request_id: self.request_id.clone(),
            ok: !self.is_error,
            content,
            structured: self.structured.as_ref().and_then(|v| v.as_object().cloned()),
        }
    }
}

#[derive(Clone)]
pub struct Tools {
    pub world: WorldHandle,
    pub frames: Arc<dyn FrameSource>,
}

fn parse<T: serde::de::DeserializeOwned>(args: &Value) -> Result<T, String> {
    serde_json::from_value(args.clone()).map_err(|e| e.to_string())
}

fn command(tag: &str, args: &Value) -> Result<Command, String> {
    let mut m = args.as_object().cloned().unwrap_or_default();
    m.insert("type".into(), tag.into());
    let c: Command = serde_json::from_value(Value::Object(m)).map_err(|e| e.to_string())?;
    c.validate()?;
    Ok(c)
}

impl Tools {
    pub fn new(world: WorldHandle, frames: Arc<dyn FrameSource>) -> Self {
        Tools { world, frames }
    }

    /// Ejecuta una tool. `origin` (`mcp`, `cloud`, …) y `call_id` van a la auditoría.
    pub async fn call(&self, name: &str, args: Value, origin: &str, call_id: Option<&str>) -> ToolOutput {
        let t0 = Instant::now();
        let args = if args.is_null() { json!({}) } else { args };
        let out = match self.dispatch(name, &args).await {
            Ok(o) => o,
            Err(e) => ToolOutput::error(format!("{name}: {e}")),
        };
        let rid = args.get("request_id").and_then(Value::as_str);
        let out = ToolOutput { request_id: out.request_id.clone().or(rid.map(String::from)), ..out };
        let res = if out.image.is_some() { "[imagen] ".to_string() + &out.text } else { out.text.clone() };
        self.world.store.audit(origin, name, call_id, rid, &args, !out.is_error, t0.elapsed().as_secs_f64() * 1000.0, &res);
        out
    }

    async fn dispatch(&self, name: &str, args: &Value) -> Result<ToolOutput, String> {
        let w = &self.world;
        Ok(match name {
            "scene" => {
                let a: SceneArgs = parse(args)?;
                let mut v = serde_json::to_value(&*w.snapshot()).map_err(|e| e.to_string())?;
                if let Some(m) = v.as_object_mut() {
                    if !a.include_hands {
                        m.remove("hands");
                    }
                    if !a.include_devices {
                        m.remove("devices");
                    }
                    m.insert("cursor".into(), w.last_seq().into());
                    m.insert("stream_id".into(), w.store.stream_id.clone().into());
                }
                ToolOutput::data(v)
            }
            "look" => {
                let a: LookArgs = parse(args)?;
                let text = summary_text(&w.snapshot(), w.last_seq());
                match self.frames.latest(a.camera_id.as_deref(), a.annotate) {
                    Some(f) => ToolOutput {
                        text: format!("Cámara {} ({}×{} px).\n{text}", f.camera_id, f.w, f.h),
                        image: Some((f.data, f.mime_type)),
                        ..Default::default()
                    },
                    None => ToolOutput::error(format!("Sin cuadro de la cámara {:?}. Disponibles: {:?}\n{text}", a.camera_id, self.frames.cameras())),
                }
            }
            "events_wait" => {
                let a: EventsWaitArgs = parse(args)?;
                if !(0.0..=50.0).contains(&a.timeout_s) || !(1..=500).contains(&a.max_events) {
                    return Err("timeout_s en [0, 50] y max_events en [1, 500]".into());
                }
                let r = w.events_wait(a.cursor, a.types, a.timeout_s, a.max_events).await;
                ToolOutput::data(serde_json::to_value(r).map_err(|e| e.to_string())?)
            }
            "devices" => {
                let a: DevicesArgs = parse(args)?;
                let snap = w.snapshot();
                let devs: Vec<&Device> = snap.devices.iter().filter(|d| a.kind.is_none_or(|k| d.kind == k)).collect();
                ToolOutput::data(json!({"devices": devs}))
            }
            "project" | "remove" | "ask" | "calibrate" | "home" | "run_flow" => {
                let tag = if name == "home" { "home_call" } else { name };
                let cmd = command(tag, args)?;
                let rid = cmd.request_id().to_string();
                let mut v = w.command(cmd).await?;
                if name == "run_flow" {
                    if let Some(m) = v.as_object_mut() {
                        m.insert("cursor".into(), w.last_seq().into());
                        m.insert("hint".into(), "usa events_wait con types=['flow_state_changed','flow_emitted'] para seguirlo".into());
                    }
                }
                let mut out = ToolOutput::data(v);
                out.request_id = Some(rid);
                out
            }
            _ => return Err(format!("tool desconocida: {name}")),
        })
    }
}
