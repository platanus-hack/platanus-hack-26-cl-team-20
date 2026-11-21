//! Supervisor (`pcx up`): reinicio, cortacircuito, terminación, instancia única y carga de `pcx.toml`.

use std::sync::atomic::Ordering;
use std::time::Duration;

use pcxd::supervisor::{self, ProcSpec, Supervised};
use tokio_util::sync::CancellationToken;

fn short_cmd() -> Vec<&'static str> {
    if cfg!(windows) {
        vec!["cmd", "/C", "exit 0"]
    } else {
        vec!["sh", "-c", "sleep 0.2"]
    }
}

fn long_cmd() -> Vec<&'static str> {
    if cfg!(windows) {
        vec!["ping", "-n", "60", "127.0.0.1"]
    } else {
        vec!["sleep", "60"]
    }
}

async fn until(mut pred: impl FnMut() -> bool) {
    for _ in 0..500 {
        if pred() {
            return;
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    panic!("timeout");
}

#[tokio::test]
async fn reinicia_y_detiene() {
    let dir = tempfile::tempdir().unwrap();
    let s = Supervised::new(ProcSpec::new("corto", &short_cmd(), dir.path()));
    let stop = CancellationToken::new();
    let task = tokio::spawn(s.clone().watch(stop.clone()));
    until(|| s.restarts.load(Ordering::Relaxed) >= 1).await;
    stop.cancel();
    tokio::time::timeout(Duration::from_secs(10), task).await.unwrap().unwrap();
}

#[tokio::test]
async fn cortacircuito_tras_caidas_seguidas() {
    let dir = tempfile::tempdir().unwrap();
    let spec = ProcSpec { max_crashes: 3, backoff0: Duration::from_millis(10), ..ProcSpec::new("cae", &short_cmd(), dir.path()) };
    let s = Supervised::new(spec);
    let task = tokio::spawn(s.clone().watch(CancellationToken::new()));
    tokio::time::timeout(Duration::from_secs(10), task).await.expect("debe rendirse solo").unwrap();
    assert!(s.failed.load(Ordering::Relaxed));
    assert_eq!(s.restarts.load(Ordering::Relaxed), 2);
}

#[tokio::test]
async fn termina_proceso_largo() {
    let dir = tempfile::tempdir().unwrap();
    let mut child = supervisor::spawn_child(&ProcSpec::new("largo", &long_cmd(), dir.path())).unwrap();
    let t0 = std::time::Instant::now();
    supervisor::terminate(&mut child, Duration::from_secs(3)).await;
    assert!(child.try_wait().unwrap().is_some() && t0.elapsed() < Duration::from_secs(4));
}

#[test]
fn instancia_unica() {
    let dir = tempfile::tempdir().unwrap();
    let a = supervisor::lock_instance(dir.path()).unwrap();
    let e = supervisor::lock_instance(dir.path()).err().expect("el segundo lock debe fallar").to_string();
    assert!(e.contains(&std::process::id().to_string()), "{e}");
    drop(a);
    supervisor::lock_instance(dir.path()).unwrap();
}

#[test]
fn pcx_toml_carga_solo_habilitados() {
    let specs = supervisor::load(&std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../pcx.toml")).unwrap();
    assert_eq!(specs.iter().map(|p| p.name.as_str()).collect::<Vec<_>>(), vec!["core"]);
    assert!(specs[0].health.as_deref().unwrap().ends_with(":9200/health"));
    assert_eq!(specs[0].cmd, vec!["pcx", "serve"]);
}

#[test]
fn pcx_toml_ejemplo_mac_completo() {
    let p = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../pcx.mac-c920.toml");
    let specs = supervisor::load(&p).unwrap();
    let names: Vec<_> = specs.iter().map(|p| p.name.as_str()).collect();
    assert_eq!(names, vec!["capture", "core", "hands", "renderer"]);
    let cap = specs.iter().find(|p| p.name == "capture").unwrap();
    assert!(cap.cmd.iter().any(|a| a == "cam:0x1120000046d0892"), "{:?}", cap.cmd);
    let r = specs.iter().find(|p| p.name == "renderer").unwrap();
    assert!(r.cmd.windows(2).any(|w| w[0] == "--display" && w[1] == "T749"), "{:?}", r.cmd);
    assert!(std::path::Path::new(&r.cmd[0]).is_absolute(), "ruta relativa resuelta contra pcx.toml");
}
