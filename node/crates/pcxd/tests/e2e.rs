//! e2e: `pcx serve` en un subproceso + cliente MCP (rmcp) por Streamable HTTP + renderer WS falso + gateway WS falso.
//! Proyectar sobre un objeto → toque simulado → `widget_pressed` por `events_wait`; el renderer recibe diffs; la nube
//! recibe hello/snapshot/replay; se reinicia el core y `seq` (y la idempotencia) continúan.

use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use futures::{SinkExt, StreamExt};
use rmcp::model::{CallToolRequestParams, CallToolResult};
use rmcp::service::RunningService;
use rmcp::transport::streamable_http_client::StreamableHttpClientTransportConfig;
use rmcp::transport::StreamableHttpClientTransport;
use rmcp::{RoleClient, ServiceExt};
use serde_json::{json, Value};
use tokio_tungstenite::tungstenite::Message;

const PORT: u16 = 9201;
const GW_PORT: u16 = 9202;
const TOKEN: &str = "test-token";

fn base() -> String {
    format!("http://127.0.0.1:{PORT}")
}

/// Mata el core aunque el test falle (no deja procesos huérfanos).
struct Core(Child);

impl Drop for Core {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

fn spawn_core(data: &std::path::Path) -> Core {
    let zdir = data.join("zmq");
    let (ingest, events) = if cfg!(windows) {
        ("tcp://127.0.0.1:9203".to_string(), "tcp://127.0.0.1:9204".to_string())
    } else {
        (format!("ipc://{}/ingest.sock", zdir.display()), format!("ipc://{}/events.sock", zdir.display()))
    };
    Command::new(env!("CARGO_BIN_EXE_pcx"))
        .arg("serve")
        .arg("--debug")
        .env("PCX_PORT", PORT.to_string())
        .env("PCX_TOKEN", TOKEN)
        .env("PCX_DEBUG_TOKEN", "test-debug-token")
        .env("PCX_CONSOLE_TOKEN", "test-console-token")
        .env("PCX_DATA_DIR", data)
        .env("PCX_INGEST", ingest)
        .env("PCX_EVENTS", events)
        .env("PCX_CLOUD_URL", format!("ws://127.0.0.1:{GW_PORT}/node"))
        .env("PCX_NODE_TOKEN", "node-tok")
        .env("PCX_LOG", "warn")
        .stdout(Stdio::null())
        .spawn()
        .map(Core)
        .expect("lanzar pcx serve")
}

async fn http() -> reqwest::Client {
    reqwest::Client::new()
}

async fn get(path: &str) -> Value {
    let r = http()
        .await
        .get(format!("{}{path}", base()))
        .header("Authorization", format!("Bearer {TOKEN}"))
        .send()
        .await
        .unwrap();
    serde_json::from_str(&r.text().await.unwrap()).unwrap()
}

async fn post(path: &str, body: Value) -> Value {
    let r = http()
        .await
        .post(format!("{}{path}", base()))
        .header(
            "Authorization",
            format!("Bearer {}", if path.starts_with("/debug") { "test-debug-token" } else { TOKEN }),
        )
        .header("Content-Type", "application/json")
        .body(body.to_string())
        .send()
        .await
        .unwrap();
    assert!(r.status().is_success(), "{path}: {}", r.status());
    serde_json::from_str(&r.text().await.unwrap()).unwrap()
}

async fn wait_health() -> Value {
    for _ in 0..200 {
        if let Ok(r) = http().await.get(format!("{}/health", base())).send().await {
            if r.status().is_success() {
                return serde_json::from_str(&r.text().await.unwrap()).unwrap();
            }
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    panic!("el core no respondió /health");
}

async fn mcp() -> RunningService<RoleClient, ()> {
    let cfg = StreamableHttpClientTransportConfig::with_uri(format!("{}/mcp", base())).auth_header(TOKEN);
    ().serve(StreamableHttpClientTransport::from_config(cfg)).await.expect("cliente MCP")
}

async fn call(c: &RunningService<RoleClient, ()>, tool: &'static str, args: Value) -> CallToolResult {
    c.call_tool(CallToolRequestParams::new(tool).with_arguments(args.as_object().unwrap().clone())).await.unwrap()
}

fn text(r: &CallToolResult) -> String {
    r.content.iter().filter_map(|c| c.as_text().map(|t| t.text.clone())).collect::<Vec<_>>().join("\n")
}

fn data(r: &CallToolResult) -> Value {
    assert_ne!(r.is_error, Some(true), "{}", text(r));
    serde_json::from_str(&text(r)).unwrap()
}

type Sessions = Arc<Mutex<Vec<Vec<Value>>>>;

/// Gateway falso: guarda los mensajes por sesión; `welcome.last_cursor` = último cursor recibido (durable).
async fn fake_gateway(sessions: Sessions) {
    let l = tokio::net::TcpListener::bind(("127.0.0.1", GW_PORT)).await.unwrap();
    let durable = Arc::new(Mutex::new((None::<String>, 0i64)));
    loop {
        let (sock, _) = l.accept().await.unwrap();
        let (sessions, durable) = (sessions.clone(), durable.clone());
        tokio::spawn(async move {
            let Ok(mut ws) = tokio_tungstenite::accept_async(sock).await else { return };
            let n = {
                let mut s = sessions.lock().unwrap();
                s.push(vec![]);
                s.len() - 1
            };
            while let Some(Ok(m)) = ws.next().await {
                let Message::Text(t) = m else { continue };
                let v: Value = serde_json::from_str(&t).unwrap();
                sessions.lock().unwrap()[n].push(v.clone());
                match v["type"].as_str() {
                    Some("hello") => {
                        let (sid, cur) = durable.lock().unwrap().clone();
                        let w = json!({"type": "welcome", "session_id": format!("s{n}"), "contract_version": 2, "last_stream_id": sid, "last_cursor": cur});
                        let _ = ws.send(Message::Text(w.to_string().into())).await;
                    }
                    Some("event_batch") => {
                        let mut d = durable.lock().unwrap();
                        d.0 = v["stream_id"].as_str().map(String::from);
                        d.1 = d.1.max(v["cursor"].as_i64().unwrap());
                    }
                    _ => {}
                }
            }
        });
    }
}

fn session_has(s: &Sessions, n: usize, pred: impl Fn(&Value) -> bool) -> bool {
    s.lock().unwrap().get(n).is_some_and(|m| m.iter().any(pred))
}

async fn until(mut pred: impl FnMut() -> bool, what: &str) {
    for _ in 0..300 {
        if pred() {
            return;
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    panic!("no ocurrió: {what}");
}

#[tokio::test(flavor = "multi_thread")]
async fn e2e_mcp_renderer_nube_y_reinicio() {
    let data_dir = tempfile::tempdir().unwrap();
    let sessions: Sessions = Default::default();
    tokio::spawn(fake_gateway(sessions.clone()));
    let core = spawn_core(data_dir.path());
    let h = wait_health().await;
    let stream_id = h["stream_id"].as_str().unwrap().to_string();

    // auth
    let r = http().await.post(format!("{}/debug/touch", base())).body("{}").send().await.unwrap();
    assert_eq!(r.status().as_u16(), 401);

    // renderer falso
    let (mut rws, _) = tokio_tungstenite::connect_async(format!("ws://127.0.0.1:{PORT}/render")).await.unwrap();
    let Some(Ok(Message::Text(t))) = rws.next().await else { panic!("scene") };
    let snap: Value = serde_json::from_str(&t).unwrap();
    assert_eq!((snap["type"].as_str(), snap["surface"]["width_mm"].as_f64()), (Some("scene"), Some(800.0)));

    post("/debug/object", json!({"id": "taza", "label": "taza roja", "center_mm": [400, 250]})).await;
    let c = mcp().await;
    let tools = c.list_all_tools().await.unwrap();
    assert_eq!(tools.len(), 10);
    assert!(tools.iter().any(|t| t.name == "events_wait"));
    let cursor = data(&call(&c, "scene", json!({})).await)["cursor"].as_i64().unwrap();
    let project = json!({"request_id": "r-hola", "widgets": [{"type": "label", "id": "hola", "text": "Hola",
        "anchor": {"object_id": "taza"}, "on_press": {"value": "saludo"}}]});
    let r = data(&call(&c, "project", project.clone()).await);
    assert_eq!(r["ids"], json!(["hola"]));

    // el renderer recibe el diff con la geometría resuelta
    let diff = loop {
        let Some(Ok(Message::Text(t))) = tokio::time::timeout(Duration::from_secs(2), rws.next()).await.unwrap() else {
            panic!()
        };
        let d: Value = serde_json::from_str(&t).unwrap();
        if d["type"] == "scene_diff" && d["upsert"].as_array().is_some_and(|u| u.iter().any(|w| w["id"] == "hola")) {
            break d;
        }
    };
    let lbl = diff["upsert"].as_array().unwrap().iter().find(|w| w["id"] == "hola").unwrap();
    assert_eq!(lbl["anchor"]["point_mm"][0].as_f64(), Some(400.0));
    let pres = json!({"type": "presented", "rev": diff["rev"], "t_present_ms": 1.0e12});
    rws.send(Message::Text(pres.to_string().into())).await.unwrap();

    // toque simulado → widget_pressed por events_wait
    tokio::spawn(async {
        tokio::time::sleep(Duration::from_millis(300)).await;
        post("/debug/touch", json!({"widget_id": "hola"})).await
    });
    let ev =
        data(&call(&c, "events_wait", json!({"cursor": cursor, "types": ["widget_pressed"], "timeout_s": 5})).await);
    assert_eq!(ev["events"][0]["payload"]["widget_id"], "hola");
    assert_eq!(ev["events"][0]["payload"]["value"], "saludo");
    assert_eq!(ev["stream_id"].as_str(), Some(stream_id.as_str()));

    // look: imagen + texto
    let look = call(&c, "look", json!({})).await;
    assert!(look.content[0].as_image().is_some() && text(&look).contains("taza"));

    // idempotencia: el mismo request_id devuelve el mismo resultado
    assert_eq!(data(&call(&c, "project", project.clone()).await), r);

    let h = wait_health().await;
    assert_eq!(h["render"]["presented_rev"], diff["rev"]);
    assert_eq!(h["renderers"].as_u64(), Some(1));

    // la nube: hello → snapshot → replay_done → en vivo (incluye widget_pressed)
    until(
        || {
            session_has(&sessions, 0, |m| {
                m["type"] == "event_batch"
                    && m["replay"] == false
                    && m["events"].as_array().unwrap().iter().any(|e| e["type"] == "widget_pressed")
            })
        },
        "widget_pressed en la nube",
    )
    .await;
    {
        let s0 = sessions.lock().unwrap()[0].clone();
        let kinds: Vec<&str> = s0.iter().take(2).map(|m| m["type"].as_str().unwrap()).collect();
        assert_eq!(kinds, vec!["hello", "state_snapshot"]);
        assert_eq!(s0[0]["stream_id"].as_str(), Some(stream_id.as_str()));
        assert!(s0.iter().any(|m| m["type"] == "replay_done"));
    }

    // reinicio: el seq continúa, el stream es el mismo y la nube reanuda desde su cursor
    let last = get("/state").await["cursor"].as_i64().unwrap();
    until(
        || session_has(&sessions, 0, |m| m["type"] == "event_batch" && m["cursor"].as_i64() == Some(last)),
        "nube al día",
    )
    .await;
    drop(c);
    let crash_widget = json!({"request_id":"crash-now","widgets":[{"type":"label","id":"crash-now","text":"durable","anchor":{"point_mm":[10,10]}}]});
    let fresh = mcp().await;
    assert_eq!(data(&call(&fresh, "project", crash_widget).await)["ids"], json!(["crash-now"]));
    drop(core); // SIGKILL justo después del ack, sin esperar snapshot periódico
    drop(fresh);
    let core = spawn_core(data_dir.path());
    let h = wait_health().await;
    assert_eq!((h["cursor"].as_i64(), h["stream_id"].as_str()), (Some(last), Some(stream_id.as_str())));
    until(|| session_has(&sessions, 1, |m| m["type"] == "replay_done"), "2.ª sesión").await;
    {
        let s1 = sessions.lock().unwrap()[1].clone();
        assert_eq!(s1[0]["log_last_cursor"].as_i64(), Some(last));
        assert!(s1.iter().filter(|m| m["type"] == "event_batch").all(|m| m["events"]
            .as_array()
            .unwrap()
            .iter()
            .all(|e| e["seq"].as_i64().unwrap() > last)));
    }
    // el mundo se restauró y el próximo evento sigue la secuencia
    let st = get("/state").await;
    assert!(st["objects"].as_array().unwrap().iter().any(|o| o["id"] == "taza"));
    assert!(st["widgets"].as_array().unwrap().iter().any(|w| w["id"] == "hola"));
    assert!(st["widgets"].as_array().unwrap().iter().any(|w| w["id"] == "crash-now"));
    post("/debug/object", json!({"id": "frasco", "center_mm": [150, 300]})).await;
    let c = mcp().await;
    let ev =
        data(&call(&c, "events_wait", json!({"cursor": last, "types": ["object_appeared"], "timeout_s": 2})).await);
    assert_eq!(ev["events"][0]["seq"].as_i64(), Some(last + 1));
    assert_eq!(data(&call(&c, "project", project).await), r); // idempotencia persistente
    drop(c);
    drop(core);
}
