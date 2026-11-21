//! Cableado de punta a punta sin hardware, todo lanzado por `pcx up` (procesos reales):
//! captura en replay (fotos de manos de `pcx-hands/testdata`) → anillo `pcx-shm` → `pcx worker hands` (ONNX en su
//! proceso) → ingesta ZeroMQ → core → `/state.hands`; un cliente MCP llama a `project` y el renderer (cliente WS de
//! `/render`, como `pcx-renderer --headless`) recibe el widget. Luego: se mata el worker y el supervisor lo reinicia
//! sin que el core caiga; un segundo `pcx up` choca con el lock; y si `pcx up` muere con SIGKILL, sus hijos se cierran.
//!
//! Requiere los modelos de manos en la caché (`cargo run -p pcx-hands --example fetch_models`); si faltan, se omite.

use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

use futures::StreamExt;
use rmcp::model::CallToolRequestParams;
use rmcp::transport::streamable_http_client::StreamableHttpClientTransportConfig;
use rmcp::transport::StreamableHttpClientTransport;
use rmcp::ServiceExt;
use serde_json::{json, Value};
use tokio_tungstenite::tungstenite::Message;

const TOKEN: &str = "t-cableado";

/// Puerto efímero libre (evita choques con otros tests o un core corriendo).
fn free_port() -> u16 {
    std::net::TcpListener::bind("127.0.0.1:0").unwrap().local_addr().unwrap().port()
}

struct Up(Child);
impl Drop for Up {
    fn drop(&mut self) {
        #[cfg(unix)]
        unsafe {
            libc::kill(self.0.id() as i32, libc::SIGTERM);
        }
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

fn models_cached() -> bool {
    let dir = std::env::var_os("PCX_MODELS_DIR")
        .map(std::path::PathBuf::from)
        .or_else(|| directories_dir().map(|d| d.join("models")));
    dir.is_some_and(|d| {
        d.join("palm_detection_mediapipe_2023feb.onnx").exists()
            && d.join("handpose_estimation_mediapipe_2023feb.onnx").exists()
    })
}

fn directories_dir() -> Option<std::path::PathBuf> {
    // misma ruta que `pcx-hands::models` (directories: ai.pcx.pcx)
    if cfg!(target_os = "macos") {
        std::env::var_os("HOME").map(|h| std::path::PathBuf::from(h).join("Library/Caches/ai.pcx.pcx"))
    } else if cfg!(windows) {
        std::env::var_os("LOCALAPPDATA").map(|h| std::path::PathBuf::from(h).join("pcx/pcx/cache"))
    } else {
        std::env::var_os("XDG_CACHE_HOME")
            .map(std::path::PathBuf::from)
            .or_else(|| std::env::var_os("HOME").map(|h| std::path::PathBuf::from(h).join(".cache")))
            .map(|c| c.join("pcx"))
    }
}

async fn get(port: u16, path: &str) -> Option<Value> {
    let r = reqwest::Client::new()
        .get(format!("http://127.0.0.1:{port}{path}"))
        .header("Authorization", format!("Bearer {TOKEN}"))
        .timeout(Duration::from_secs(2))
        .send()
        .await
        .ok()?;
    serde_json::from_str(&r.text().await.ok()?).ok()
}

async fn wait_for<T>(secs: u64, what: &str, mut f: impl AsyncFnMut() -> Option<T>) -> T {
    let t0 = Instant::now();
    while t0.elapsed() < Duration::from_secs(secs) {
        if let Some(v) = f().await {
            return v;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    panic!("no ocurrió en {secs} s: {what}");
}

fn status(run: &std::path::Path) -> Option<Value> {
    serde_json::from_str(&std::fs::read_to_string(run.join("pcx-up.json")).ok()?).ok()
}

fn hands_of(state: &Value) -> Vec<Value> {
    state["hands"].as_array().or_else(|| state["world"]["hands"].as_array()).cloned().unwrap_or_default()
}

#[cfg(unix)]
fn alive(pid: i64) -> bool {
    pid > 0 && unsafe { libc::kill(pid as i32, 0) } == 0
}

#[tokio::test(flavor = "multi_thread")]
async fn up_captura_manos_core_mcp_renderer() {
    if !models_cached() {
        // en CI se exige (PCX_E2E_REQUIRE_MODELS=1) para que el «ok» no sea vacío
        assert!(std::env::var_os("PCX_E2E_REQUIRE_MODELS").is_none(), "faltan los modelos de pcx-hands");
        eprintln!(
            "SKIP (no es un ok real): faltan los modelos de pcx-hands; cargo run -p pcx-hands --example fetch_models"
        );
        return;
    }
    let port = free_port();
    let tmp = tempfile::tempdir().unwrap();
    let (run, data) = (tmp.path().join("run"), tmp.path().join("data"));
    let frames = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../pcx-hands/testdata");
    let cam = format!("e2e{}", std::process::id());
    let (ingest, events) = if cfg!(windows) {
        ("tcp://127.0.0.1:9213".to_string(), "tcp://127.0.0.1:9214".to_string())
    } else {
        (format!("ipc://{}/in.sock", tmp.path().display()), format!("ipc://{}/ev.sock", tmp.path().display()))
    };
    let toml = format!(
        r#"
[supervisor]
max_crashes = 5
crash_window_s = 60
[env]
PCX_PORT = "{port}"
PCX_TOKEN = "{TOKEN}"
PCX_DATA_DIR = "{data}"
PCX_INGEST = "{ingest}"
PCX_EVENTS = "{events}"
PCX_LOG = "warn"
[process.core]
cmd = ["pcx", "serve"]
health = "http://127.0.0.1:{port}/health"
[process.capture]
cmd = ["pcx", "worker", "capture", "--id", "{cam}", "--source", "replay:{frames}", "--size", "640x480", "--fps", "15"]
[process.hands]
cmd = ["pcx", "worker", "hands", "--camera", "{cam}", "--stats-every-s", "2"]
[process.ajeno]
cmd = ["sh", "-c", "trap '' TERM; exec sleep 300"]
"#,
        data = data.display(),
        frames = frames.display()
    );
    let cfg = tmp.path().join("pcx.toml");
    std::fs::write(&cfg, toml).unwrap();
    let spawn_up = || {
        Command::new(env!("CARGO_BIN_EXE_pcx"))
            .args(["up", "--config", cfg.to_str().unwrap()])
            .env("PCX_RUNTIME_DIR", &run)
            .stdout(Stdio::null())
            .stderr(Stdio::piped())
            .spawn()
            .unwrap()
    };
    let mut up = Up(spawn_up());
    let _stderr = up.0.stderr.take(); // se descarta (evita llenar el pipe)
    let drain = std::thread::spawn(move || {
        use std::io::Read;
        let mut s = String::new();
        if let Some(mut e) = _stderr {
            let _ = e.read_to_string(&mut s);
        }
        s
    });

    wait_for(30, "core /health", async || get(port, "/health").await).await;

    // renderer (lado WS de /render): recibe la escena y luego el widget proyectado por MCP
    let (mut rws, _) = tokio_tungstenite::connect_async(format!("ws://127.0.0.1:{port}/render")).await.unwrap();
    let Some(Ok(Message::Text(t))) = rws.next().await else { panic!("scene") };
    assert_eq!(serde_json::from_str::<Value>(&t).unwrap()["type"], "scene");
    let cfg_mcp =
        StreamableHttpClientTransportConfig::with_uri(format!("http://127.0.0.1:{port}/mcp")).auth_header(TOKEN);
    let mcp = ().serve(StreamableHttpClientTransport::from_config(cfg_mcp)).await.unwrap();
    let args = json!({"request_id": "r-etq", "widgets": [{"type": "label", "id": "etq", "text": "Hola mesa", "anchor": {"point_mm": [400, 250]}}]});
    let r = mcp
        .call_tool(CallToolRequestParams::new("project").with_arguments(args.as_object().unwrap().clone()))
        .await
        .unwrap();
    assert_ne!(r.is_error, Some(true), "{:?}", r.content);
    loop {
        let Some(Ok(Message::Text(t))) = tokio::time::timeout(Duration::from_secs(5), rws.next()).await.expect("diff")
        else {
            panic!()
        };
        let d: Value = serde_json::from_str(&t).unwrap();
        if d["upsert"].as_array().is_some_and(|u| u.iter().any(|w| w["id"] == "etq")) {
            break;
        }
    }

    // manos: replay → anillo → worker (otro proceso) → ingesta → estado
    let t_first = wait_for(120, "hands en /state", async || {
        let h = hands_of(&get(port, "/state").await?);
        h.first().and_then(|h| h["t_capture_ns"].as_i64())
    })
    .await;
    assert!(t_first > 0);

    // matar el worker de manos: el supervisor lo reinicia y el core sigue vivo
    #[cfg(unix)]
    {
        let st = wait_for(5, "pcx-up.json", async || status(&run)).await;
        let (old, core_pid) =
            (st["procs"]["hands"]["pid"].as_i64().unwrap(), st["procs"]["core"]["pid"].as_i64().unwrap());
        assert!(alive(old) && alive(core_pid));
        unsafe { libc::kill(old as i32, libc::SIGKILL) };
        let t_kill = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos() as i64;
        let st = wait_for(20, "reinicio del worker", async || {
            let s = status(&run)?;
            let p = s["procs"]["hands"]["pid"].as_i64()?;
            (p != old && p > 0 && s["procs"]["hands"]["restarts"].as_u64()? >= 1).then_some(s)
        })
        .await;
        assert_eq!(st["procs"]["core"]["pid"].as_i64(), Some(core_pid), "el core no se reinició");
        wait_for(120, "manos nuevas tras el reinicio", async || {
            hands_of(&get(port, "/state").await?)
                .iter()
                .any(|h| h["t_capture_ns"].as_i64().unwrap_or(0) > t_kill)
                .then_some(())
        })
        .await;

        // instancia única
        let out = Command::new(env!("CARGO_BIN_EXE_pcx"))
            .args(["up", "--config", cfg.to_str().unwrap()])
            .env("PCX_RUNTIME_DIR", &run)
            .output()
            .unwrap();
        assert!(!out.status.success());
        assert!(String::from_utf8_lossy(&out.stderr).contains("ya hay un `pcx up`"));

        // si `pcx up` muere sin avisar (SIGKILL), los hijos se cierran solos
        let st = status(&run).unwrap();
        let kids: Vec<i64> =
            ["core", "capture", "hands", "ajeno"].iter().filter_map(|n| st["procs"][n]["pid"].as_i64()).collect();
        assert_eq!(kids.len(), 4);
        // el «ajeno» (no es `pcx`) corre bajo `pcx guard`; su nieto `sleep` ignora SIGTERM y debe morir igual
        let pg_ajeno = st["procs"]["ajeno"]["pid"].as_i64().unwrap() as i32;
        let _ = up.0.kill();
        let _ = up.0.wait();
        wait_for(10, "hijos cerrados tras SIGKILL del supervisor", async || {
            kids.iter().all(|p| !alive(*p)).then_some(())
        })
        .await;
        wait_for(10, "grupo del ajeno vacío", async || (unsafe { libc::killpg(pg_ajeno, 0) } != 0).then_some(())).await;
    }
    drop(up);
    let log = drain.join().unwrap_or_default();
    assert!(log.contains("[pcx up] hands: iniciado"), "{log}");
    for l in log.lines().filter(|l| l.contains("[pcx up]") || l.contains("worker hands]")) {
        eprintln!("{l}");
    }
}

/// Un selector de cámara que no existe falla con error claro y código ≠ 0 (sin abrir ni pedir permiso de nada):
/// así el supervisor lo cuenta como caída y reintenta.
#[test]
fn captura_con_camara_inexistente_sale_con_error() {
    let id = format!("nx{}", std::process::id());
    let out = Command::new(env!("CARGO_BIN_EXE_pcx"))
        .args(["worker", "capture", "--id", &id, "--source", "cam:no-existe-pcx-xyz", "--no-zmq"])
        .env("PCX_LOG", "warn")
        .output()
        .unwrap();
    assert!(!out.status.success());
    let err = String::from_utf8_lossy(&out.stderr);
    assert!(err.contains("no-existe-pcx-xyz"), "{err}");
}
