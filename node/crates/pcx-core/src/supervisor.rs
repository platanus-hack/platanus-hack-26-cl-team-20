//! `pcx up`: lanza y vigila los procesos del nodo según `pcx.toml` (health checks y reinicio con backoff).
//!
//! Cada hijo corre en su propio grupo de procesos. Al detener: en Unix SIGTERM al grupo (SIGKILL si no termina en
//! 5 s); en Windows `CREATE_NEW_PROCESS_GROUP` + `kill()`. Solo se tocan los procesos lanzados aquí (nunca pkill).
//! `cmd[0] = "pcx"` se resuelve al ejecutable actual (el mismo binario instalado).

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;

use serde::Deserialize;
use tokio::process::{Child, Command};
use tokio_util::sync::CancellationToken;

#[derive(Debug, Clone, Deserialize)]
struct RawProc {
    cmd: Vec<String>,
    #[serde(default)]
    cwd: Option<String>,
    #[serde(default)]
    env: BTreeMap<String, toml::Value>,
    #[serde(default)]
    health: Option<String>,
    #[serde(default = "always")]
    restart: String,
    #[serde(default = "yes")]
    enabled: bool,
}

fn always() -> String {
    "always".into()
}
fn yes() -> bool {
    true
}

#[derive(Debug, Clone, Deserialize, Default)]
struct RawCfg {
    #[serde(default)]
    env: BTreeMap<String, toml::Value>,
    #[serde(default)]
    process: BTreeMap<String, RawProc>,
}

#[derive(Debug, Clone)]
pub struct ProcSpec {
    pub name: String,
    pub cmd: Vec<String>,
    pub cwd: PathBuf,
    pub env: BTreeMap<String, String>,
    pub health: Option<String>,
    pub restart: String,
    pub health_every: Duration,
    pub health_fails: u32,
    pub startup_grace: Duration,
}

impl ProcSpec {
    pub fn new(name: &str, cmd: &[&str], cwd: &Path) -> Self {
        ProcSpec {
            name: name.into(),
            cmd: cmd.iter().map(|s| s.to_string()).collect(),
            cwd: cwd.into(),
            env: BTreeMap::new(),
            health: None,
            restart: "always".into(),
            health_every: Duration::from_secs(2),
            health_fails: 3,
            startup_grace: Duration::from_secs(20),
        }
    }
}

fn tv(v: &toml::Value) -> String {
    match v {
        toml::Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

/// Lee `pcx.toml`: procesos habilitados, rutas relativas al archivo.
pub fn load(path: &Path) -> anyhow::Result<Vec<ProcSpec>> {
    let cfg: RawCfg = toml::from_str(&std::fs::read_to_string(path)?)?;
    let base = path.parent().unwrap_or(Path::new(".")).to_path_buf();
    let genv: BTreeMap<String, String> = cfg.env.iter().map(|(k, v)| (k.clone(), tv(v))).collect();
    Ok(cfg
        .process
        .into_iter()
        .filter(|(_, p)| p.enabled)
        .map(|(name, p)| {
            let mut env = genv.clone();
            env.extend(p.env.iter().map(|(k, v)| (k.clone(), tv(v))));
            ProcSpec {
                cwd: base.join(p.cwd.unwrap_or_else(|| ".".into())),
                env,
                health: p.health,
                restart: p.restart,
                ..ProcSpec::new(&name, &p.cmd.iter().map(String::as_str).collect::<Vec<_>>(), &base)
            }
        })
        .collect())
}

pub struct Supervised {
    pub spec: ProcSpec,
    pub restarts: AtomicU64,
}

fn log(name: &str, msg: &str) {
    eprintln!("[pcx up] {name}: {msg}");
}

pub fn spawn_child(spec: &ProcSpec) -> std::io::Result<Child> {
    let mut argv = spec.cmd.clone();
    if argv.first().map(String::as_str) == Some("pcx") {
        if let Ok(me) = std::env::current_exe() {
            argv[0] = me.display().to_string();
        }
    }
    let mut c = Command::new(&argv[0]);
    c.args(&argv[1..]).current_dir(&spec.cwd).envs(&spec.env).stdin(Stdio::null()).kill_on_drop(true);
    #[cfg(unix)]
    c.process_group(0);
    #[cfg(windows)]
    c.creation_flags(0x0000_0200); // CREATE_NEW_PROCESS_GROUP
    c.spawn()
}

/// Detiene un hijo: SIGTERM al grupo y SIGKILL tras `timeout` (Unix); `kill()` en Windows.
pub async fn terminate(child: &mut Child, timeout: Duration) {
    if child.try_wait().ok().flatten().is_some() {
        return;
    }
    #[cfg(unix)]
    if let Some(pid) = child.id() {
        // SAFETY: killpg con un pid de grupo que creamos nosotros (process_group(0)).
        unsafe {
            libc::killpg(pid as i32, libc::SIGTERM);
        }
        if tokio::time::timeout(timeout, child.wait()).await.is_ok() {
            return;
        }
        unsafe {
            libc::killpg(pid as i32, libc::SIGKILL);
        }
    }
    let _ = child.kill().await;
    let _ = child.wait().await;
}

async fn healthy(client: &reqwest::Client, url: &Option<String>) -> bool {
    match url {
        None => true,
        Some(u) => client.get(u).timeout(Duration::from_secs(2)).send().await.map(|r| r.status().is_success()).unwrap_or(false),
    }
}

impl Supervised {
    pub fn new(spec: ProcSpec) -> Arc<Self> {
        Arc::new(Supervised { spec, restarts: AtomicU64::new(0) })
    }

    /// Lanza, vigila y reinicia hasta `stop`.
    pub async fn watch(self: Arc<Self>, stop: CancellationToken) {
        let client = reqwest::Client::new();
        let mut backoff = 1.0f64;
        let name = self.spec.name.clone();
        while !stop.is_cancelled() {
            let mut child = match spawn_child(&self.spec) {
                Ok(c) => c,
                Err(e) => {
                    log(&name, &format!("no se pudo lanzar: {e}"));
                    if self.spec.restart == "never" {
                        return;
                    }
                    tokio::select! { _ = stop.cancelled() => return, _ = tokio::time::sleep(Duration::from_secs_f64(backoff)) => {} }
                    backoff = (backoff * 2.0).min(30.0);
                    continue;
                }
            };
            log(&name, &format!("iniciado pid={:?}", child.id()));
            let started = tokio::time::Instant::now();
            let mut fails = 0;
            let code = loop {
                tokio::select! {
                    _ = stop.cancelled() => { terminate(&mut child, Duration::from_secs(5)).await; return; }
                    st = child.wait() => break st.ok().and_then(|s| s.code()),
                    _ = tokio::time::sleep(self.spec.health_every) => {
                        if healthy(&client, &self.spec.health).await {
                            fails = 0;
                            backoff = 1.0;
                        } else if started.elapsed() >= self.spec.startup_grace {
                            fails += 1;
                            if fails >= self.spec.health_fails {
                                log(&name, "health check falla; reiniciando");
                                terminate(&mut child, Duration::from_secs(5)).await;
                            }
                        }
                    }
                }
            };
            log(&name, &format!("terminó (código {code:?})"));
            if self.spec.restart == "never" {
                return;
            }
            self.restarts.fetch_add(1, Ordering::Relaxed);
            tokio::select! { _ = stop.cancelled() => return, _ = tokio::time::sleep(Duration::from_secs_f64(backoff)) => {} }
            backoff = (backoff * 2.0).min(30.0);
        }
    }
}

/// `pcx up`: vigila todos los procesos habilitados hasta `stop`.
pub async fn up(config: &Path, stop: CancellationToken) -> anyhow::Result<()> {
    let procs: Vec<Arc<Supervised>> = load(config)?.into_iter().map(Supervised::new).collect();
    if procs.is_empty() {
        eprintln!("[pcx up] no hay procesos habilitados");
        return Ok(());
    }
    let tasks: Vec<_> = procs.iter().map(|p| tokio::spawn(p.clone().watch(stop.clone()))).collect();
    for t in tasks {
        let _ = t.await;
    }
    eprintln!("[pcx up] detenido");
    Ok(())
}
