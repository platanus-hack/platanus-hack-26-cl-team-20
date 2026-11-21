//! Supervisor (`pcx up`): reinicio, cortacircuito, terminación, instancia única y carga de `pcx.toml`.

use std::sync::atomic::Ordering;
use std::time::Duration;

use pcxd::supervisor::{self, ProcSpec, Supervised};
use tokio_util::sync::CancellationToken;

/// Spec con el binario `pcx` real (para `pcx guard`), no el binario de tests.
fn spec(name: &str, cmd: &[&str], dir: &std::path::Path) -> ProcSpec {
    ProcSpec { pcx_bin: Some(env!("CARGO_BIN_EXE_pcx").into()), ..ProcSpec::new(name, cmd, dir) }
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

fn lines(p: &std::path::Path) -> usize {
    std::fs::read_to_string(p).map(|s| s.lines().count()).unwrap_or(0)
}

#[cfg(unix)]
#[tokio::test]
async fn reinicia_y_detiene() {
    let dir = tempfile::tempdir().unwrap();
    let marca = dir.path().join("corridas");
    let sh = format!("echo x >> '{}'; sleep 0.2; exit 1", marca.display());
    let s = Supervised::new(ProcSpec { backoff0: Duration::from_millis(50), ..spec("corto", &["sh", "-c", &sh], dir.path()) });
    let stop = CancellationToken::new();
    let task = tokio::spawn(s.clone().watch(stop.clone()));
    until(|| s.restarts.load(Ordering::Relaxed) >= 1 && lines(&marca) >= 2).await;
    stop.cancel();
    tokio::time::timeout(Duration::from_secs(10), task).await.unwrap().unwrap();
}

#[cfg(unix)]
#[tokio::test]
async fn cortacircuito_tras_caidas_seguidas() {
    let dir = tempfile::tempdir().unwrap();
    let marca = dir.path().join("corridas");
    let sh = format!("echo x >> '{}'; exit 3", marca.display());
    let sp = ProcSpec { max_crashes: 3, backoff0: Duration::from_millis(10), ..spec("cae", &["sh", "-c", &sh], dir.path()) };
    let s = Supervised::new(sp);
    let task = tokio::spawn(s.clone().watch(CancellationToken::new()));
    tokio::time::timeout(Duration::from_secs(10), task).await.expect("debe rendirse solo").unwrap();
    assert!(s.failed.load(Ordering::Relaxed));
    assert_eq!((s.restarts.load(Ordering::Relaxed), lines(&marca)), (2, 3), "corrió sh de verdad 3 veces");
}

#[cfg(unix)]
#[tokio::test]
async fn salida_limpia_no_cuenta_como_caida() {
    let dir = tempfile::tempdir().unwrap();
    let sp = ProcSpec { restart: "on-failure".into(), ..spec("ok", &["sh", "-c", "exit 0"], dir.path()) };
    let s = Supervised::new(sp);
    tokio::time::timeout(Duration::from_secs(5), tokio::spawn(s.clone().watch(CancellationToken::new()))).await.unwrap().unwrap();
    assert!(!s.failed.load(Ordering::Relaxed));
    assert_eq!(s.restarts.load(Ordering::Relaxed), 0);
}

/// El hijo real (bajo `pcx guard` en macOS) ignora SIGTERM: `terminate` debe dejar el grupo vacío con SIGKILL.
#[cfg(unix)]
#[tokio::test]
async fn terminate_vacia_el_grupo_aunque_ignoren_sigterm() {
    let dir = tempfile::tempdir().unwrap();
    let pidf = dir.path().join("pid");
    let sh = format!("trap '' TERM; echo $$ > '{}'; exec sleep 47", pidf.display());
    let mut child = supervisor::spawn_child(&spec("terco", &["sh", "-c", &sh], dir.path())).unwrap();
    let pgid = child.id().unwrap() as i32;
    until(|| std::fs::read_to_string(&pidf).is_ok_and(|s| s.trim().parse::<i32>().is_ok())).await;
    let real: i32 = std::fs::read_to_string(&pidf).unwrap().trim().parse().unwrap();
    assert!(unsafe { libc::kill(real, 0) } == 0, "sleep 47 corre de verdad");
    let t0 = std::time::Instant::now();
    supervisor::terminate(&mut child, Duration::from_secs(1)).await;
    assert!(t0.elapsed() < Duration::from_secs(4), "{:?}", t0.elapsed());
    until(|| !supervisor::group_alive(pgid)).await;
    assert!(unsafe { libc::kill(real, 0) } != 0, "no queda huérfano");
}

#[cfg(unix)]
#[tokio::test]
async fn termina_proceso_largo() {
    let dir = tempfile::tempdir().unwrap();
    let pidf = dir.path().join("pid");
    let sh = format!("echo $$ > '{}'; exec sleep 60", pidf.display());
    let mut child = supervisor::spawn_child(&spec("largo", &["sh", "-c", &sh], dir.path())).unwrap();
    until(|| std::fs::read_to_string(&pidf).is_ok_and(|s| !s.trim().is_empty())).await;
    let t0 = std::time::Instant::now();
    supervisor::terminate(&mut child, Duration::from_secs(3)).await;
    assert!(child.try_wait().unwrap().is_some() && t0.elapsed() < Duration::from_secs(4));
    assert!(!supervisor::group_alive(child.id().unwrap_or(0) as i32) || child.id().is_none());
}

#[cfg(windows)]
#[tokio::test]
async fn termina_proceso_largo() {
    let dir = tempfile::tempdir().unwrap();
    let mut child = supervisor::spawn_child(&spec("largo", &["ping", "-n", "60", "127.0.0.1"], dir.path())).unwrap();
    let t0 = std::time::Instant::now();
    supervisor::terminate(&mut child, Duration::from_secs(3)).await;
    assert!(child.try_wait().unwrap().is_some() && t0.elapsed() < Duration::from_secs(4));
}

#[test]
fn restart_invalido_es_error() {
    let dir = tempfile::tempdir().unwrap();
    let p = dir.path().join("pcx.toml");
    std::fs::write(&p, "[process.x]\ncmd = [\"sh\"]\nrestart = \"siempre\"\n").unwrap();
    assert!(supervisor::load(&p).unwrap_err().to_string().contains("inválido"));
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

/// Todos los procesos en `failed` → `pcx up` sale con código ≠ 0 y conserva `pcx-up.json`.
#[cfg(unix)]
#[test]
fn up_sale_con_error_si_todo_queda_failed() {
    let dir = tempfile::tempdir().unwrap();
    let cfg = dir.path().join("pcx.toml");
    std::fs::write(&cfg, "[supervisor]\nmax_crashes = 2\n[process.malo]\ncmd = [\"sh\", \"-c\", \"exit 7\"]\n").unwrap();
    let out = std::process::Command::new(env!("CARGO_BIN_EXE_pcx"))
        .args(["up", "--config", cfg.to_str().unwrap()])
        .env("PCX_RUNTIME_DIR", dir.path().join("run"))
        .output()
        .unwrap();
    assert!(!out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    let st: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(dir.path().join("run/pcx-up.json")).unwrap()).unwrap();
    assert_eq!(st["procs"]["malo"]["failed"], true);
}
