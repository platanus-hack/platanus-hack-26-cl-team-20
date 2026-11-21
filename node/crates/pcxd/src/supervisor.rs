//! `pcx up`: lanza y vigila los procesos del nodo según `pcx.toml`.
//!
//! - **Health checks** (URL opcional) y **reinicio con backoff** 1 → 30 s; **cortacircuito**: `max_crashes` caídas en
//!   `crash_window_s` → el proceso queda `failed` y no se reintenta (se registra).
//! - **Instancia única**: lock de archivo (`File::try_lock`, flock/LockFileEx) en `<runtime>/pcx-up.lock` con el pid.
//! - **Si el padre muere, los hijos se cierran**: Linux, `prctl(PR_SET_PDEATHSIG, SIGTERM)` en el hijo; macOS y otros
//!   Unix, cada hijo corre bajo `pcx guard`, que vigila al supervisor con kqueue (`EVFILT_PROC`/`NOTE_EXIT`) y cierra su
//!   hijo; Windows, Job Object con `KILL_ON_JOB_CLOSE` (si muere `pcx up`, el SO mata el árbol).
//! - Al detener: Unix, SIGTERM al grupo del hijo y SIGKILL a los 5 s; Windows, `kill()` (el Job cubre el árbol).
//!   Solo se tocan procesos lanzados aquí (nunca `pkill`).
//! - `cmd[0] = "pcx"` se resuelve al ejecutable actual.

use std::collections::{BTreeMap, VecDeque};
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
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
    /// Segundos de gracia antes de exigir el health check.
    #[serde(default)]
    startup_grace_s: Option<f64>,
}

fn always() -> String {
    "always".into()
}
fn yes() -> bool {
    true
}

#[derive(Debug, Clone, Deserialize, Default)]
struct RawSupervisor {
    max_crashes: Option<u32>,
    crash_window_s: Option<f64>,
}

#[derive(Debug, Clone, Deserialize, Default)]
struct RawCfg {
    #[serde(default)]
    env: BTreeMap<String, toml::Value>,
    #[serde(default)]
    supervisor: RawSupervisor,
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
    /// Cortacircuito: `max_crashes` caídas dentro de `crash_window` → `failed`.
    pub max_crashes: u32,
    pub crash_window: Duration,
    /// Backoff inicial entre reinicios.
    pub backoff0: Duration,
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
            max_crashes: 5,
            crash_window: Duration::from_secs(60),
            backoff0: Duration::from_secs(1),
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
    let path = &std::fs::canonicalize(path)?;
    let cfg: RawCfg = toml::from_str(&std::fs::read_to_string(path)?)?;
    let base = path.parent().unwrap_or(Path::new(".")).to_path_buf();
    let genv: BTreeMap<String, String> = cfg.env.iter().map(|(k, v)| (k.clone(), tv(v))).collect();
    let sup = cfg.supervisor;
    Ok(cfg
        .process
        .into_iter()
        .filter(|(_, p)| p.enabled)
        .map(|(name, p)| {
            let mut env = genv.clone();
            env.extend(p.env.iter().map(|(k, v)| (k.clone(), tv(v))));
            let mut cmd = p.cmd.clone();
            // `./x` o `../x` se resuelven contra el directorio de pcx.toml (no contra el cwd de quien lanza)
            if let Some(c0) = cmd.first_mut() {
                let pth = Path::new(c0.as_str());
                if pth.is_relative() && pth.components().count() > 1 {
                    *c0 = base.join(pth).display().to_string();
                }
            }
            let d = ProcSpec::new(&name, &cmd.iter().map(String::as_str).collect::<Vec<_>>(), &base);
            ProcSpec {
                cwd: base.join(p.cwd.unwrap_or_else(|| ".".into())),
                env,
                health: p.health,
                restart: p.restart,
                startup_grace: p.startup_grace_s.map(Duration::from_secs_f64).unwrap_or(d.startup_grace),
                max_crashes: sup.max_crashes.unwrap_or(d.max_crashes),
                crash_window: sup.crash_window_s.map(Duration::from_secs_f64).unwrap_or(d.crash_window),
                ..d
            }
        })
        .collect())
}

pub struct Supervised {
    pub spec: ProcSpec,
    pub restarts: AtomicU64,
    pub pid: AtomicU64,
    pub failed: AtomicBool,
}

fn log(name: &str, msg: &str) {
    eprintln!("[pcx up] {name}: {msg}");
}

/// Variable con el pid del supervisor que `pcx guard` vigila.
pub const SUPERVISOR_PID_ENV: &str = "PCX_SUPERVISOR_PID";

fn argv_of(spec: &ProcSpec) -> Vec<String> {
    let me = std::env::current_exe().ok().map(|p| p.display().to_string());
    let mut argv = spec.cmd.clone();
    if let (Some("pcx"), Some(me)) = (argv.first().map(String::as_str), &me) {
        argv[0] = me.clone();
    }
    // macOS/BSD no tienen PDEATHSIG: los hijos `pcx …` se vigilan solos (`follow_supervisor`); el resto corre bajo
    // `pcx guard`, que lo cierra si el supervisor muere.
    #[cfg(all(unix, not(target_os = "linux")))]
    if let (Some(me), false) = (me, spec.cmd.first().map(String::as_str) == Some("pcx")) {
        let mut g = vec![me, "guard".into(), "--".into()];
        g.extend(argv);
        return g;
    }
    argv
}

pub fn spawn_child(spec: &ProcSpec) -> std::io::Result<Child> {
    let argv = argv_of(spec);
    let mut c = Command::new(&argv[0]);
    c.args(&argv[1..])
        .current_dir(&spec.cwd)
        .envs(&spec.env)
        .env(SUPERVISOR_PID_ENV, std::process::id().to_string())
        .stdin(Stdio::null())
        .kill_on_drop(true);
    #[cfg(unix)]
    c.process_group(0);
    #[cfg(target_os = "linux")]
    // SAFETY: solo llamadas async-signal-safe entre fork y exec.
    unsafe {
        let parent = libc::getpid();
        c.pre_exec(move || {
            if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGTERM) != 0 {
                return Err(std::io::Error::last_os_error());
            }
            if libc::getppid() != parent {
                libc::_exit(0); // el padre ya murió entre fork y prctl
            }
            Ok(())
        });
    }
    #[cfg(windows)]
    c.creation_flags(0x0000_0200); // CREATE_NEW_PROCESS_GROUP
    let child = c.spawn()?;
    #[cfg(windows)]
    win_job::assign(&child);
    Ok(child)
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
    #[cfg(not(unix))]
    let _ = timeout;
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
        Arc::new(Supervised { spec, restarts: AtomicU64::new(0), pid: AtomicU64::new(0), failed: AtomicBool::new(false) })
    }

    /// Lanza, vigila y reinicia hasta `stop` (o hasta el cortacircuito).
    pub async fn watch(self: Arc<Self>, stop: CancellationToken) {
        let client = reqwest::Client::new();
        let b0 = self.spec.backoff0.as_secs_f64();
        let mut backoff = b0;
        let mut crashes: VecDeque<tokio::time::Instant> = VecDeque::new();
        let name = self.spec.name.clone();
        while !stop.is_cancelled() {
            let mut child = match spawn_child(&self.spec) {
                Ok(c) => c,
                Err(e) => {
                    log(&name, &format!("no se pudo lanzar: {e}"));
                    if self.spec.restart == "never" || self.trip(&mut crashes) {
                        return;
                    }
                    tokio::select! { _ = stop.cancelled() => return, _ = tokio::time::sleep(Duration::from_secs_f64(backoff)) => {} }
                    backoff = (backoff * 2.0).min(30.0);
                    continue;
                }
            };
            self.pid.store(child.id().unwrap_or(0) as u64, Ordering::Relaxed);
            log(&name, &format!("iniciado pid={:?}", child.id()));
            let started = tokio::time::Instant::now();
            let mut fails = 0;
            let code = loop {
                tokio::select! {
                    _ = stop.cancelled() => { terminate(&mut child, Duration::from_secs(5)).await; return; }
                    st = child.wait() => break st.ok().map(|s| s.to_string()),
                    _ = tokio::time::sleep(self.spec.health_every) => {
                        if healthy(&client, &self.spec.health).await {
                            fails = 0;
                            if started.elapsed() > Duration::from_secs(10) {
                                backoff = b0;
                            }
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
            self.pid.store(0, Ordering::Relaxed);
            log(&name, &format!("terminó ({})", code.unwrap_or_else(|| "?".into())));
            if self.spec.restart == "never" || self.trip(&mut crashes) {
                return;
            }
            self.restarts.fetch_add(1, Ordering::Relaxed);
            log(&name, &format!("reinicio en {backoff:.1} s"));
            tokio::select! { _ = stop.cancelled() => return, _ = tokio::time::sleep(Duration::from_secs_f64(backoff)) => {} }
            backoff = (backoff * 2.0).min(30.0);
        }
    }

    /// Registra una caída; true si se abre el cortacircuito.
    fn trip(&self, crashes: &mut VecDeque<tokio::time::Instant>) -> bool {
        let now = tokio::time::Instant::now();
        crashes.push_back(now);
        while crashes.front().is_some_and(|t| now.duration_since(*t) > self.spec.crash_window) {
            crashes.pop_front();
        }
        if crashes.len() as u32 >= self.spec.max_crashes {
            self.failed.store(true, Ordering::Relaxed);
            log(
                &self.spec.name,
                &format!("FAILED: {} caídas en {:.0} s; no se reintenta (revisa el log y relanza `pcx up`)", crashes.len(), self.spec.crash_window.as_secs_f64()),
            );
            return true;
        }
        false
    }
}

/// Lock de instancia única. Se libera al soltarlo (o si el proceso muere: lo suelta el SO).
pub struct InstanceLock {
    _file: std::fs::File,
    pub path: PathBuf,
}

/// Directorio de runtime: `PCX_RUNTIME_DIR` o `<tmp>/pcx`.
pub fn runtime_dir() -> PathBuf {
    std::env::var_os("PCX_RUNTIME_DIR").map(PathBuf::from).unwrap_or_else(|| std::env::temp_dir().join("pcx"))
}

pub fn lock_instance(dir: &Path) -> anyhow::Result<InstanceLock> {
    use std::io::{Read, Seek, Write};
    std::fs::create_dir_all(dir)?;
    let path = dir.join("pcx-up.lock");
    let mut f = std::fs::OpenOptions::new().read(true).write(true).create(true).truncate(false).open(&path)?;
    if f.try_lock().is_err() {
        let mut other = String::new();
        let _ = f.read_to_string(&mut other);
        anyhow::bail!("ya hay un `pcx up` corriendo (pid {}; lock {})", other.trim(), path.display());
    }
    f.set_len(0)?;
    f.rewind()?;
    write!(f, "{}", std::process::id())?;
    f.flush()?;
    Ok(InstanceLock { _file: f, path })
}

/// `pcx up`: instancia única + vigila todos los procesos habilitados hasta `stop`.
pub async fn up(config: &Path, stop: CancellationToken) -> anyhow::Result<()> {
    let _lock = lock_instance(&runtime_dir())?;
    let procs: Vec<Arc<Supervised>> = load(config)?.into_iter().map(Supervised::new).collect();
    if procs.is_empty() {
        eprintln!("[pcx up] no hay procesos habilitados");
        return Ok(());
    }
    eprintln!(
        "[pcx up] pid {} · {} · procesos: {}",
        std::process::id(),
        config.display(),
        procs.iter().map(|p| p.spec.name.as_str()).collect::<Vec<_>>().join(", ")
    );
    let tasks: Vec<_> = procs.iter().map(|p| tokio::spawn(p.clone().watch(stop.clone()))).collect();
    let status_path = runtime_dir().join("pcx-up.json");
    let (ps, st) = (procs.clone(), stop.clone());
    let sp = status_path.clone();
    tokio::spawn(async move {
        while !st.is_cancelled() {
            let _ = std::fs::write(&sp, status_json(&ps).to_string());
            tokio::select! { _ = st.cancelled() => {}, _ = tokio::time::sleep(Duration::from_millis(500)) => {} }
        }
    });
    for t in tasks {
        let _ = t.await;
    }
    let _ = std::fs::remove_file(&status_path);
    eprintln!("[pcx up] detenido");
    Ok(())
}

/// Estado para `<runtime>/pcx-up.json`: `{"pid", "procs": {nombre: {pid, restarts, failed}}}`.
pub fn status_json(procs: &[Arc<Supervised>]) -> serde_json::Value {
    let m: serde_json::Map<String, serde_json::Value> = procs
        .iter()
        .map(|p| {
            let v = serde_json::json!({
                "pid": p.pid.load(Ordering::Relaxed),
                "restarts": p.restarts.load(Ordering::Relaxed),
                "failed": p.failed.load(Ordering::Relaxed),
            });
            (p.spec.name.clone(), v)
        })
        .collect();
    serde_json::json!({"pid": std::process::id(), "procs": m})
}

/// `pcx guard -- <argv>` (Unix sin PDEATHSIG): lanza `argv` y lo cierra si muere el supervisor (`PCX_SUPERVISOR_PID`,
/// o el padre). Devuelve el código del hijo.
#[cfg(unix)]
pub fn guard(argv: &[String]) -> i32 {
    use std::os::unix::process::ExitStatusExt;
    let Some((prog, args)) = argv.split_first() else {
        eprintln!("pcx guard: falta el comando");
        return 2;
    };
    let watched: i32 = std::env::var(SUPERVISOR_PID_ENV).ok().and_then(|s| s.parse().ok()).unwrap_or_else(|| unsafe { libc::getppid() });
    // si el supervisor ya murió, no lanzar nada
    if unsafe { libc::getppid() } != watched || unsafe { libc::kill(watched, 0) } != 0 {
        return 0;
    }
    let mut child = match std::process::Command::new(prog).args(args).spawn() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("pcx guard: no se pudo lanzar {prog}: {e}");
            return 127;
        }
    };
    let pid = child.id() as i32;
    std::thread::spawn(move || {
        wait_exit(watched);
        // SAFETY: señales a nuestro propio hijo.
        unsafe { libc::kill(pid, libc::SIGTERM) };
        std::thread::sleep(Duration::from_secs(3));
        unsafe { libc::kill(pid, libc::SIGKILL) };
        std::process::exit(0);
    });
    match child.wait() {
        Ok(st) => st.code().unwrap_or_else(|| 128 + st.signal().unwrap_or(0)),
        Err(_) => 1,
    }
}

/// En un hijo `pcx …` lanzado por `pcx up` (macOS/BSD): si el supervisor muere, SIGTERM a sí mismo (apagado
/// ordenado) y salida forzada a los 5 s.
#[cfg(unix)]
pub fn follow_supervisor() {
    let Some(pid) = std::env::var(SUPERVISOR_PID_ENV).ok().and_then(|s| s.parse::<i32>().ok()) else { return };
    if cfg!(target_os = "linux") {
        return; // PDEATHSIG
    }
    std::thread::spawn(move || {
        wait_exit(pid);
        eprintln!("[pcx] el supervisor (pid {pid}) terminó; cierro");
        // SAFETY: señal a este mismo proceso.
        unsafe { libc::kill(libc::getpid(), libc::SIGTERM) };
        std::thread::sleep(Duration::from_secs(5));
        std::process::exit(0);
    });
}

/// Bloquea hasta que termine `pid`: kqueue `EVFILT_PROC`/`NOTE_EXIT` en macOS/BSD; sondeo en el resto.
#[cfg(unix)]
fn wait_exit(pid: i32) {
    #[cfg(any(target_os = "macos", target_os = "freebsd", target_os = "openbsd", target_os = "netbsd"))]
    // SAFETY: kqueue/kevent con estructuras inicializadas; el fd se usa solo en este hilo.
    unsafe {
        let kq = libc::kqueue();
        if kq >= 0 {
            let mut ev: libc::kevent = std::mem::zeroed();
            ev.ident = pid as usize;
            ev.filter = libc::EVFILT_PROC;
            ev.flags = libc::EV_ADD | libc::EV_ONESHOT;
            ev.fflags = libc::NOTE_EXIT;
            let mut out: libc::kevent = std::mem::zeroed();
            let n = libc::kevent(kq, &ev, 1, &mut out, 1, std::ptr::null());
            libc::close(kq);
            if n >= 0 {
                return; // evento de salida (o ESRCH ya informado como evento)
            }
        }
    }
    while unsafe { libc::kill(pid, 0) } == 0 {
        std::thread::sleep(Duration::from_millis(250));
    }
}

/// Windows: un Job Object con `KILL_ON_JOB_CLOSE` por supervisor; al morir `pcx up` se cierra el handle y el SO mata
/// el árbol. No compilado en esta máquina (macOS); revisar en Windows.
#[cfg(windows)]
mod win_job {
    use std::sync::OnceLock;
    use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation, SetInformationJobObject,
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };
    use windows_sys::Win32::System::Threading::{OpenProcess, PROCESS_SET_QUOTA, PROCESS_TERMINATE};

    struct Job(HANDLE);
    unsafe impl Send for Job {}
    unsafe impl Sync for Job {}

    fn job() -> Option<&'static Job> {
        static JOB: OnceLock<Option<Job>> = OnceLock::new();
        JOB.get_or_init(|| unsafe {
            let h = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if h.is_null() {
                return None;
            }
            let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            let ok = SetInformationJobObject(
                h,
                JobObjectExtendedLimitInformation,
                &info as *const _ as *const _,
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            );
            (ok != 0).then_some(Job(h))
        })
        .as_ref()
    }

    pub fn assign(child: &tokio::process::Child) {
        let (Some(job), Some(pid)) = (job(), child.id()) else { return };
        unsafe {
            let p = OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, 0, pid);
            if !p.is_null() {
                AssignProcessToJobObject(job.0, p);
                CloseHandle(p);
            }
        }
    }
}
