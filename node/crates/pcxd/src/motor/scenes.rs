//! Módulos de percepción por escena dentro del motor (protocolo v0.1; contrato en `pcx_perception::scene_api`).
//!
//! - [`registry`]: **aquí se registran los módulos** (`ScenePerceptor`) que trae el binario.
//! - [`Scenes`]: el módulo activo (`scene{name}` de la web app), su memoria y lo que ve del mundo. Lo comparten el
//!   ciclo cenital (`on_clean`, después del `Perceptor` base) y el hilo de la cámara en vivo (`on_live`).
//! - [`live_loop`]: el hilo que le pasa cada cuadro de la cámara lateral al módulo que la pidió.

use std::collections::HashMap;
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Duration, Instant};

use image::RgbImage;
use pcx_perception::pipeline_api::{CalibIn, ObjectOut};
use pcx_perception::scene_api::{check_prefix, SceneCtx, SceneOut, SceneRegistry};
use serde_json::{json, Value};
use tokio::sync::mpsc;
use tokio_util::sync::CancellationToken;

use super::frames::FrameHub;

const LOG: &str = "[pcx motor]";
/// Rol de la cámara de `--camera-lateral` (lo que devuelve `ScenePerceptor::live_camera`).
pub const LATERAL: &str = "lateral";
pub const CENITAL: &str = "cenital";
/// Presupuesto de `on_live` por cuadro; sobre esto se avisa.
pub const LIVE_BUDGET: Duration = Duration::from_millis(30);
/// Tope de `on_live`: tras [`LIVE_STRIKES`] cuadros seguidos sobre este tiempo, la cámara en vivo del módulo se
/// pausa hasta que la escena se vuelva a activar o llegue `reset`.
pub const LIVE_LIMIT: Duration = Duration::from_millis(250);
pub const LIVE_STRIKES: u32 = 10;

/// Módulos de escena del binario. **Max: registra aquí los tuyos**, por ejemplo
/// `r.register(Box::new(lab::Vasos::default()));`. El nombre del módulo (`name()`) es el de la escena o preset que
/// la web app manda en `scene{name}` y el prefijo de sus ids (`lab:vaso-b`).
pub fn registry() -> SceneRegistry {
    #[allow(clippy::let_and_return)]
    let r = SceneRegistry::default();
    r
}

/// Resultado de una llamada a un módulo.
pub struct SceneRun {
    pub scene: String,
    pub out: Result<SceneOut, String>,
    pub ms: f64,
    /// `on_live` (cámara en vivo) o `on_clean` (ciclo cenital).
    pub live: bool,
}

impl SceneRun {
    pub fn is_empty(&self) -> bool {
        matches!(&self.out, Ok(o) if o.upsert.is_empty() && o.remove.is_empty() && o.events.is_empty())
    }
}

/// Qué pasó con un `scene{name}`.
#[derive(Debug, PartialEq, Eq)]
pub struct Switch {
    /// Escena que se desactivó (ya con `reset`): sus objetos hay que retirarlos.
    pub previous: Option<String>,
    pub active: Option<String>,
    pub changed: bool,
}

struct Inner {
    registry: SceneRegistry,
    active: Option<String>,
    /// Memoria por escena; se borra cuando la escena se desactiva o con `reset`.
    memory: HashMap<String, Value>,
    /// Objetos vigentes del `Perceptor` base y calibración, para `on_live`.
    base: Arc<Vec<ObjectOut>>,
    calib: Option<CalibIn>,
    strikes: u32,
    live_paused: bool,
    last_warn: Option<Instant>,
}

pub struct Scenes(Mutex<Inner>);

impl Scenes {
    pub fn new(registry: SceneRegistry) -> Arc<Scenes> {
        Arc::new(Scenes(Mutex::new(Inner {
            registry,
            active: None,
            memory: HashMap::new(),
            base: Arc::default(),
            calib: None,
            strikes: 0,
            live_paused: false,
            last_warn: None,
        })))
    }

    fn lock(&self) -> MutexGuard<'_, Inner> {
        self.0.lock().unwrap_or_else(|e| e.into_inner())
    }

    pub fn names(&self) -> Vec<String> {
        self.lock().registry.names()
    }

    pub fn active(&self) -> Option<String> {
        self.lock().active.clone()
    }

    /// Activa el módulo `name`; `None` o un nombre sin módulo dejan el motor sin escena. El anterior recibe `reset`.
    pub fn activate(&self, name: Option<&str>) -> Switch {
        let mut g = self.lock();
        let next = name.filter(|n| g.registry.get_mut(n).is_some()).map(str::to_owned);
        if next == g.active {
            return Switch { previous: None, active: next, changed: false };
        }
        let previous = g.active.take();
        if let Some(p) = &previous {
            g.reset_scene(p);
        }
        if let Some(n) = &next {
            g.reset_scene(n);
        }
        g.active = next.clone();
        Switch { previous, active: next, changed: true }
    }

    /// `reset` de la web app: el módulo activo olvida todo.
    pub fn reset(&self) {
        let mut g = self.lock();
        if let Some(a) = g.active.clone() {
            g.reset_scene(&a);
        }
    }

    /// Lo que `on_live` ve del mundo; el motor lo actualiza después de cada ciclo y de cada `calib`.
    pub fn set_world(&self, base: Vec<ObjectOut>, calib: Option<CalibIn>) {
        let mut g = self.lock();
        g.base = Arc::new(base);
        g.calib = calib;
    }

    /// Foto limpia cenital → módulo activo. `objects`: los del `Perceptor` base ya con lo de este ciclo.
    pub fn on_clean(&self, frame: &RgbImage, objects: &[ObjectOut], calib: &CalibIn, t_ns: u64) -> Option<SceneRun> {
        let mut g = self.lock();
        let scene = g.active.clone()?;
        Some(g.call(&scene, frame, objects, calib, t_ns, false))
    }

    /// Cuadro de la cámara `role` → módulo activo, si pidió esa cámara. Sin `calib` todavía, recibe la identidad.
    pub fn on_live(&self, role: &str, frame: &RgbImage, t_ns: u64) -> Option<SceneRun> {
        let mut g = self.lock();
        let scene = g.active.clone()?;
        if g.live_paused || g.registry.get_mut(&scene)?.live_camera() != Some(role) {
            return None;
        }
        let base = g.base.clone();
        let calib = g.calib.clone().unwrap_or_else(|| {
            let (w, h) = frame.dimensions();
            CalibIn {
                camera_px: (w, h),
                matrix_px_to_mm: [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
                lens: None,
                surface_mm: (w as f64, h as f64),
            }
        });
        let mut run = g.call(&scene, frame, &base, &calib, t_ns, true);
        let took = Duration::from_secs_f64(run.ms / 1e3);
        if took > LIVE_BUDGET && g.last_warn.is_none_or(|t| t.elapsed() > Duration::from_secs(5)) {
            g.last_warn = Some(Instant::now());
            eprintln!(
                "{LOG} escena «{scene}»: on_live tardó {:.0} ms (presupuesto: {} ms)",
                run.ms,
                LIVE_BUDGET.as_millis()
            );
        }
        g.strikes = if took > LIVE_LIMIT { g.strikes + 1 } else { 0 };
        if g.strikes >= LIVE_STRIKES {
            g.live_paused = true;
            run.out = Err(format!(
                "on_live pasó de {} ms en {LIVE_STRIKES} cuadros seguidos: la cámara en vivo queda en pausa hasta \
                 reactivar la escena",
                LIVE_LIMIT.as_millis()
            ));
        }
        Some(run)
    }
}

impl Inner {
    fn reset_scene(&mut self, name: &str) {
        if let Some(m) = self.registry.get_mut(name) {
            m.reset();
        }
        self.memory.remove(name);
        self.strikes = 0;
        self.live_paused = false;
    }

    fn call(
        &mut self,
        scene: &str,
        frame: &RgbImage,
        objects: &[ObjectOut],
        calib: &CalibIn,
        t_ns: u64,
        live: bool,
    ) -> SceneRun {
        let t = Instant::now();
        let memory = self.memory.entry(scene.to_owned()).or_insert_with(|| json!({}));
        let mut ctx = SceneCtx { objects, calib, t_ns, memory };
        let out = match self.registry.get_mut(scene) {
            Some(m) if live => m.on_live(frame, &mut ctx),
            Some(m) => m.on_clean(frame, &mut ctx),
            None => Err("módulo sin registrar".into()),
        };
        // Ids sin el prefijo de la escena chocarían con los del catálogo: no se publica nada de esa llamada.
        let out = out.and_then(|o| check_prefix(scene, &o).map(|()| o));
        SceneRun { scene: scene.to_owned(), out, ms: t.elapsed().as_secs_f64() * 1e3, live }
    }
}

/// Hilo de la cámara en vivo: cada cuadro nuevo de `hub` va al módulo activo que pidió `role`. Siempre toma el
/// cuadro más reciente (si `on_live` se atrasa, los intermedios se saltan) y no toca el ciclo cenital.
pub fn live_loop(
    role: &'static str,
    hub: Arc<FrameHub>,
    scenes: Arc<Scenes>,
    tx: mpsc::UnboundedSender<SceneRun>,
    stop: CancellationToken,
) {
    let mut seq = 0;
    let mut last_err: Option<(String, Instant)> = None;
    while !stop.is_cancelled() {
        let Some((s, f)) = hub.wait_newer(seq, Duration::from_millis(200)) else { continue };
        seq = s;
        let Some(run) = scenes.on_live(role, &f.img, f.t_capture_ns) else { continue };
        if run.is_empty() {
            continue;
        }
        // El mismo error cuadro tras cuadro se informa cada 5 s.
        if let Err(e) = &run.out {
            if last_err.as_ref().is_some_and(|(m, t)| m == e && t.elapsed() < Duration::from_secs(5)) {
                continue;
            }
            last_err = Some((e.clone(), Instant::now()));
        }
        if tx.send(run).is_err() {
            return;
        }
    }
}
