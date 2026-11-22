//! Confirmación de domótica de punta a punta, con procesos reales lanzados por `pcx up`:
//! el supervisor entrega el token de manos por `PCX_HANDS_TOKEN_FILE` (archivo 0600, nunca argv); un worker
//! (`examples/hands_probe`, que usa el mismo `IngestSender` que `pcx worker hands`) manda el toque autenticado y
//! confirma; un emisor sin token que dice ser `hands` no confirma. El renderer es un cliente WS de `/render` que
//! contesta `presented` como `pcx-renderer`; antes de ese aviso nadie puede confirmar. Al final se revisa que ningún
//! log contenga los tokens.
#![cfg(unix)]

use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

use futures::{SinkExt, StreamExt};
use rmcp::model::{CallToolRequestParams, CallToolResult};
use rmcp::service::RunningService;
use rmcp::transport::streamable_http_client::StreamableHttpClientTransportConfig;
use rmcp::transport::StreamableHttpClientTransport;
use rmcp::{RoleClient, ServiceExt};
use serde_json::{json, Value};
use tokio_tungstenite::tungstenite::Message;

const TOKEN: &str = "t-agente-confirmacion";
const CONSOLE: &str = "t-consola-confirmacion";

struct Up(Child);
impl Drop for Up {
    fn drop(&mut self) {
        unsafe { libc::kill(self.0.id() as i32, libc::SIGTERM) };
        let t0 = Instant::now();
        while t0.elapsed() < Duration::from_secs(8) {
            if self.0.try_wait().ok().flatten().is_some() {
                return;
            }
            std::thread::sleep(Duration::from_millis(50));
        }
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

fn probe() -> PathBuf {
    // target/debug/pcx → target/debug/examples/hands_probe (cargo test compila los ejemplos)
    let p = Path::new(env!("CARGO_BIN_EXE_pcx")).parent().unwrap().join("examples/hands_probe");
    if !p.exists() {
        // `cargo test --test confirmacion` no compila los ejemplos: se compila aquí con el mismo cargo.
        let cargo = std::env::var("CARGO").unwrap_or_else(|_| "cargo".into());
        let st = Command::new(cargo)
            .args(["build", "-p", "pcxd", "--example", "hands_probe"])
            .current_dir(env!("CARGO_MANIFEST_DIR"))
            .status()
            .unwrap();
        assert!(st.success());
    }
    assert!(p.exists(), "falta {}", p.display());
    p
}

async fn call(c: &RunningService<RoleClient, ()>, tool: &'static str, args: Value) -> Value {
    let r: CallToolResult =
        c.call_tool(CallToolRequestParams::new(tool).with_arguments(args.as_object().unwrap().clone())).await.unwrap();
    let text = r.content.iter().filter_map(|c| c.as_text().map(|t| t.text.clone())).collect::<Vec<_>>().join("\n");
    assert_ne!(r.is_error, Some(true), "{text}");
    serde_json::from_str(&text).unwrap()
}

/// Resoluciones de confirmación vistas por el agente (`events_wait`).
async fn resolutions(c: &RunningService<RoleClient, ()>) -> Vec<Value> {
    let r = call(c, "events_wait", json!({"cursor": 0, "types": ["confirmation_resolved"], "timeout_s": 0})).await;
    r["events"].as_array().cloned().unwrap_or_default()
}

async fn wait_file(p: &Path, secs: u64) {
    let t0 = Instant::now();
    while !p.exists() {
        assert!(t0.elapsed() < Duration::from_secs(secs), "no apareció {}", p.display());
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn worker_del_supervisor_confirma_y_un_emisor_sin_token_no() {
    let port = std::net::TcpListener::bind("127.0.0.1:0").unwrap().local_addr().unwrap().port();
    let tmp = tempfile::tempdir().unwrap();
    let (run, data) = (tmp.path().join("run"), tmp.path().join("data"));
    let (ingest, events) =
        (format!("ipc://{}/in.sock", tmp.path().display()), format!("ipc://{}/ev.sock", tmp.path().display()));
    let go = |n: &str| tmp.path().join(format!("go-{n}"));
    // «Confirmar» queda en [550,110,230,50] sobre la mesa de 800×500: su centro es (665,135).
    let toml = format!(
        r#"
[env]
PCX_PORT = "{port}"
PCX_TOKEN = "{TOKEN}"
PCX_CONSOLE_TOKEN = "{CONSOLE}"
PCX_DATA_DIR = "{data}"
PCX_INGEST = "{ingest}"
PCX_EVENTS = "{events}"
PCX_LOG = "trace"
PCX_HOME_POLICY = '{{"allow":["lock.unlock"],"confirm":["*"]}}'
[process.core]
cmd = ["pcx", "serve"]
health = "http://127.0.0.1:{port}/health"
[process.antes]
cmd = ["{probe}", "665", "135", "2200", "antes", "{g0}"]
restart = "never"
[process.intruso]
cmd = ["{probe}", "665", "135", "2200", "intruso", "{g1}", "--no-auth"]
restart = "never"
[process.manos]
cmd = ["{probe}", "665", "135", "2200", "manos", "{g2}"]
restart = "never"
"#,
        data = data.display(),
        probe = probe().display(),
        g0 = go("antes").display(),
        g1 = go("intruso").display(),
        g2 = go("manos").display(),
    );
    let cfg = tmp.path().join("pcx.toml");
    std::fs::write(&cfg, toml).unwrap();
    let log_path = tmp.path().join("up.log");
    let up = Up(Command::new(env!("CARGO_BIN_EXE_pcx"))
        .args(["up", "--config", cfg.to_str().unwrap()])
        .env("PCX_RUNTIME_DIR", &run)
        .env_remove("PCX_HANDS_TOKEN")
        .env_remove("PCX_HANDS_TOKEN_FILE")
        .stdout(Stdio::from(std::fs::File::create(tmp.path().join("up.out")).unwrap()))
        .stderr(Stdio::from(std::fs::File::create(&log_path).unwrap()))
        .spawn()
        .unwrap());

    let http = reqwest::Client::new();
    let t0 = Instant::now();
    loop {
        let ok =
            http.get(format!("http://127.0.0.1:{port}/health")).send().await.is_ok_and(|r| r.status().is_success());
        if ok {
            break;
        }
        assert!(t0.elapsed() < Duration::from_secs(30), "el core no respondió /health");
        tokio::time::sleep(Duration::from_millis(100)).await;
    }

    // El supervisor dejó el token en un archivo 0600 del directorio de datos y pasó solo su RUTA por entorno.
    use std::os::unix::fs::PermissionsExt;
    let token_file = data.join("hands.token");
    let hands_token = std::fs::read_to_string(&token_file).unwrap().trim().to_string();
    assert_eq!(hands_token.len(), 64);
    assert_eq!(std::fs::metadata(&token_file).unwrap().permissions().mode() & 0o777, 0o600);
    let ps = Command::new("ps").args(["-axww", "-o", "args="]).output().unwrap();
    assert!(!String::from_utf8_lossy(&ps.stdout).contains(&hands_token), "el token no va en argv");

    let (mut rws, _) = tokio_tungstenite::connect_async(format!("ws://127.0.0.1:{port}/render")).await.unwrap();
    let Some(Ok(Message::Text(t))) = rws.next().await else { panic!("scene") };
    assert_eq!(serde_json::from_str::<Value>(&t).unwrap()["type"], "scene");
    let cfg_mcp =
        StreamableHttpClientTransportConfig::with_uri(format!("http://127.0.0.1:{port}/mcp")).auth_header(TOKEN);
    let mcp = ().serve(StreamableHttpClientTransport::from_config(cfg_mcp)).await.unwrap();

    let pending = call(
        &mcp,
        "home",
        json!({"request_id": "abrir", "domain": "lock", "service": "unlock", "data": {"entity_id": "lock.puerta"}}),
    )
    .await;
    assert_eq!(pending["status"], "pending", "{pending}");
    let button = format!("pcx:confirm:{}", pending["confirmation_id"].as_str().unwrap());

    // El renderer recibe el diff con el botón, pero todavía no avisa `presented`.
    let rev = loop {
        let Some(Ok(Message::Text(t))) = tokio::time::timeout(Duration::from_secs(5), rws.next()).await.expect("diff")
        else {
            panic!()
        };
        let d: Value = serde_json::from_str(&t).unwrap();
        if d["upsert"].as_array().is_some_and(|u| u.iter().any(|w| w["id"] == button.as_str())) {
            let panel = d["upsert"].as_array().unwrap().iter().find(|w| w["type"] == "panel").unwrap().clone();
            assert_eq!(panel["body"], "lock.unlock → lock.puerta");
            break d["rev"].as_i64().unwrap();
        }
    };

    // 1) Worker autenticado, pero la confirmación aún no está en la mesa: no confirma.
    std::fs::write(go("antes"), b"").unwrap();
    wait_file(&tmp.path().join("go-antes.done"), 15).await;
    assert!(resolutions(&mcp).await.is_empty(), "sin `presented` nadie confirma");

    // El renderer presenta la escena (mismo mensaje que `pcx-renderer`), y pasa el margen de gracia.
    let presented = json!({"type": "presented", "rev": rev, "t_present_ms": 1.0});
    rws.send(Message::Text(presented.to_string().into())).await.unwrap();
    tokio::time::sleep(Duration::from_millis(700)).await;

    // 2) Emisor sin token que dice ser `hands`: su toque entra, pero no confirma.
    std::fs::write(go("intruso"), b"").unwrap();
    wait_file(&tmp.path().join("go-intruso.done"), 15).await;
    assert!(resolutions(&mcp).await.is_empty(), "un emisor sin token no confirma");

    // 3) Worker lanzado por el supervisor, con el token: confirma.
    std::fs::write(go("manos"), b"").unwrap();
    wait_file(&tmp.path().join("go-manos.done"), 15).await;
    let res = resolutions(&mcp).await;
    assert_eq!(res.len(), 1, "{res:?}");
    let p = &res[0]["payload"];
    assert_eq!((p["approved"].as_bool(), p["via"].as_str()), (Some(true), Some("touch")), "{p}");
    assert_eq!(p["request_id"], "abrir");
    assert_eq!(p["action_digest"], pending["action_digest"]);

    // Ningún log (core en `trace`, supervisor, workers) contiene secretos.
    drop(mcp);
    drop(up);
    let mut logs = std::fs::read_to_string(&log_path).unwrap();
    logs += &std::fs::read_to_string(tmp.path().join("up.out")).unwrap();
    assert!(logs.contains("[pcx up] manos: iniciado"), "{logs}");
    let mut secrets = vec![TOKEN.to_string(), CONSOLE.to_string(), hands_token];
    for name in ["mcp", "console", "debug"] {
        if let Ok(t) = std::fs::read_to_string(data.join(format!("{name}.token"))) {
            secrets.push(t.trim().to_string());
        }
    }
    assert!(secrets.len() >= 4, "debug.token debe existir");
    for s in secrets.iter().filter(|s| !s.is_empty()) {
        assert!(!logs.contains(s.as_str()), "un token quedó en los logs");
    }
}
