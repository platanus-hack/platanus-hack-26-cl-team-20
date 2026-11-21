//! pcx-renderer: `--headless --frames N --out DIR` (offscreen a PNG), `--display IDX|NOMBRE`
//! (ventana sin bordes, vsync Fifo, en todos los Spaces) o `--bench`. Ver README.md.

use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Duration, Instant};

use pcx_renderer::engine::{self, Engine, EngineConfig};
use pcx_renderer::runtime::{Runtime, now_epoch_ms};
use vello::wgpu;

struct Args {
    ws: Option<String>,
    cfg: EngineConfig,
    scenes: Vec<PathBuf>,
    headless: bool,
    frames: Option<u64>,
    out: Option<PathBuf>,
    hz: f64,
    time: Option<f64>,
    size: (u32, u32),
    display: Option<String>,
    latency: u32,
    bench: bool,
    bench_frames: usize,
}

const HELP: &str = "pcx-renderer [--ws URL | --no-ws] [--scene ARCHIVO.json ...] [--ppmm N] [--effects DIR]
             [--assets DIR] [--fonts DIR] [--lut ARCHIVO.npy|.pcxlut]
  --headless [--frames N] [--out DIR] [--hz N] [--time T] [--size 1920x1080]
  --display <índice|nombre> [--latency N]
  --bench [--bench-frames N]";

fn parse_args() -> Args {
    let mut a = Args {
        ws: Some("ws://127.0.0.1:9200/render".into()),
        cfg: EngineConfig::default(),
        scenes: vec![],
        headless: false,
        frames: None,
        out: None,
        hz: 60.0,
        time: None,
        size: (1920, 1080),
        display: None,
        latency: 1,
        bench: false,
        bench_frames: 200,
    };
    let mut it = std::env::args().skip(1);
    let num = |v: Option<String>, k: &str| -> f64 { v.and_then(|v| v.parse().ok()).unwrap_or_else(|| panic!("{k} necesita un número")) };
    while let Some(k) = it.next() {
        match k.as_str() {
            "--ws" => a.ws = it.next(),
            "--no-ws" => a.ws = None,
            "--scene" => a.scenes.push(it.next().expect("--scene ARCHIVO").into()),
            "--ppmm" => a.cfg.ppmm = num(it.next(), "--ppmm"),
            "--effects" => a.cfg.effects_dir = it.next().expect("--effects DIR").into(),
            "--assets" => a.cfg.assets_dir = it.next().expect("--assets DIR").into(),
            "--lut" => a.cfg.lut_path = it.next(),
            "--fonts" => a.cfg.fonts_dir = it.next().expect("--fonts DIR").into(),
            "--headless" => a.headless = true,
            "--frames" => a.frames = Some(num(it.next(), "--frames") as u64),
            "--out" => a.out = it.next().map(PathBuf::from),
            "--hz" => a.hz = num(it.next(), "--hz"),
            "--time" => a.time = Some(num(it.next(), "--time")),
            "--size" => {
                let s = it.next().expect("--size WxH");
                let (w, h) = s.split_once('x').expect("--size WxH");
                a.size = (w.parse().expect("ancho"), h.parse().expect("alto"));
            }
            "--display" => a.display = it.next(),
            "--latency" => a.latency = num(it.next(), "--latency") as u32,
            "--bench" => a.bench = true,
            "--bench-frames" => a.bench_frames = num(it.next(), "--bench-frames") as usize,
            "-h" | "--help" => {
                println!("{HELP}");
                std::process::exit(0);
            }
            other => {
                eprintln!("argumento desconocido: {other}\n{HELP}");
                std::process::exit(2);
            }
        }
    }
    a
}

fn load_scenes(rt: &mut Runtime, files: &[PathBuf]) {
    for f in files {
        let s = std::fs::read_to_string(f).unwrap_or_else(|e| panic!("{}: {e}", f.display()));
        let msg = serde_json::from_str(&s).unwrap_or_else(|e| panic!("{}: {e}", f.display()));
        let o = rt.apply(msg);
        eprintln!("[scene] {} → {o:?} (rev {:?})", f.display(), rt.state.rev);
    }
}

fn main() {
    let args = parse_args();
    if args.bench {
        let mut eng = Engine::headless(args.cfg);
        println!("| widgets | cuadros | ms/cuadro p50 | p95 | media |");
        println!("|---|---|---|---|---|");
        for n in [5, 50, 200] {
            let r = pcx_renderer::bench::run(&mut eng, n, args.bench_frames);
            println!("| {} | {} | {:.2} | {:.2} | {:.2} |", r.widgets, r.frames, r.p50_ms, r.p95_ms, r.mean_ms);
        }
        return;
    }
    let mut rt = match &args.ws {
        Some(url) => Runtime::connect(url),
        None => Runtime::offline(),
    };
    load_scenes(&mut rt, &args.scenes);
    if args.headless {
        run_headless(args, rt);
    } else if args.display.is_some() {
        display::run(args, rt);
    } else {
        eprintln!("elige --headless, --display <índice|nombre> o --bench\n{HELP}");
        std::process::exit(2);
    }
}

fn run_headless(args: Args, mut rt: Runtime) {
    let (w, h) = args.size;
    let mut eng = Engine::headless(args.cfg);
    let target = eng.create_target(w, h);
    let view = target.create_view(&Default::default());
    if let Some(dir) = &args.out {
        std::fs::create_dir_all(dir).expect("--out");
    }
    let period = (args.hz > 0.0).then(|| Duration::from_secs_f64(1.0 / args.hz));
    let mut next = Instant::now();
    let mut n = 0u64;
    while args.frames.is_none_or(|f| n < f) {
        if n % 30 == 0 {
            eng.poll_effects();
        }
        rt.pump();
        let now = rt.now_s();
        let t = args.time.unwrap_or(now);
        eng.render(&rt.state, t, now, &view, w, h);
        eng.wait_idle();
        rt.after_present(now_epoch_ms());
        if let Some(dir) = &args.out {
            let p = dir.join(format!("frame_{n:05}.png"));
            eng.readback(&target).save(&p).expect("png");
        }
        n += 1;
        if let Some(p) = period {
            next += p;
            let now = Instant::now();
            if next > now {
                std::thread::sleep(next - now);
            } else {
                next = now;
            }
        }
    }
    if let Some(dir) = &args.out {
        if let Some(c) = eng.readback_canvas() {
            c.save(dir.join("superficie.png")).expect("png");
        }
        eprintln!("[headless] {n} cuadros en {}", dir.display());
    }
}

/// Modo con ventana (Mac del montaje). No se ejecuta en los tests.
mod display {
    use super::*;
    use winit::application::ApplicationHandler;
    use winit::dpi::{LogicalPosition, LogicalSize};
    use winit::event::{ElementState, KeyEvent, WindowEvent};
    use winit::event_loop::{ActiveEventLoop, ControlFlow, EventLoop};
    use winit::keyboard::{Key, NamedKey};
    use winit::window::{Window, WindowId, WindowLevel};

    struct Gfx {
        window: Arc<Window>,
        surface: wgpu::Surface<'static>,
        config: wgpu::SurfaceConfiguration,
        engine: Engine,
        _instance: wgpu::Instance,
    }

    struct App {
        args: Args,
        rt: Runtime,
        gfx: Option<Gfx>,
        frame_no: u64,
        cfg: Option<EngineConfig>,
    }

    impl App {
        fn create(&mut self, el: &ActiveEventLoop) {
            let monitors: Vec<_> = el.available_monitors().collect();
            for (i, m) in monitors.iter().enumerate() {
                eprintln!("[win] display {i}: {:?} pos={:?} size={:?} escala={}", m.name(), m.position(), m.size(), m.scale_factor());
            }
            let sel = self.args.display.clone().unwrap_or_default();
            let mon = match sel.parse::<usize>() {
                Ok(i) => monitors.get(i).cloned(),
                Err(_) => monitors.iter().find(|m| m.name().is_some_and(|n| n.contains(&sel))).cloned(),
            }
            .or_else(|| {
                let primary = el.primary_monitor();
                monitors.iter().find(|m| Some(*m) != primary.as_ref()).cloned()
            })
            .or(monitors.first().cloned())
            .expect("sin displays");
            let sf = mon.scale_factor();
            let pos: LogicalPosition<f64> = mon.position().to_logical(sf);
            let size: LogicalSize<f64> = mon.size().to_logical(sf);
            // Ventana sin bordes del tamaño del display. NUNCA pantalla completa nativa (kernel panics en el montaje).
            let attrs = Window::default_attributes()
                .with_title("pcx-renderer")
                .with_position(pos)
                .with_inner_size(size)
                .with_resizable(false)
                .with_decorations(false)
                .with_window_level(WindowLevel::AlwaysOnTop);
            let window = Arc::new(el.create_window(attrs).expect("ventana"));
            pin_window(&window);
            let instance = engine::new_instance();
            let surface = instance.create_surface(window.clone()).expect("surface");
            let (adapter, device, queue) = engine::gpu_init(&instance, Some(&surface));
            let caps = surface.get_capabilities(&adapter);
            let format = caps
                .formats
                .iter()
                .copied()
                .find(|f| matches!(f, wgpu::TextureFormat::Bgra8Unorm | wgpu::TextureFormat::Rgba8Unorm))
                .unwrap_or(caps.formats[0]);
            let phys = window.inner_size();
            let config = wgpu::SurfaceConfiguration {
                usage: wgpu::TextureUsages::RENDER_ATTACHMENT,
                format,
                width: phys.width.max(1),
                height: phys.height.max(1),
                present_mode: wgpu::PresentMode::Fifo,
                color_space: wgpu::SurfaceColorSpace::Auto,
                desired_maximum_frame_latency: self.args.latency,
                alpha_mode: wgpu::CompositeAlphaMode::Auto,
                view_formats: vec![],
            };
            surface.configure(&device, &config);
            eprintln!("[win] salida {}x{} {format:?}", config.width, config.height);
            let engine = Engine::new(device, queue, format, self.cfg.take().unwrap());
            self.gfx = Some(Gfx { window, surface, config, engine, _instance: instance });
        }

        fn frame(&mut self) {
            let Some(g) = self.gfx.as_mut() else { return };
            self.frame_no += 1;
            if self.frame_no % 30 == 0 {
                g.engine.poll_effects();
            }
            let st = match g.surface.get_current_texture() {
                wgpu::CurrentSurfaceTexture::Success(t) | wgpu::CurrentSurfaceTexture::Suboptimal(t) => t,
                wgpu::CurrentSurfaceTexture::Outdated | wgpu::CurrentSurfaceTexture::Lost => {
                    g.surface.configure(&g.engine.device, &g.config);
                    return;
                }
                _ => {
                    // Tapada u ocupada: no girar en vacío.
                    self.rt.dropped += 1;
                    std::thread::sleep(Duration::from_millis(10));
                    return;
                }
            };
            // La escena se toma lo más tarde posible (después de obtener el drawable).
            self.rt.pump();
            let now = self.rt.now_s();
            let view = st.texture.create_view(&Default::default());
            g.engine.render(&self.rt.state, now, now, &view, g.config.width, g.config.height);
            g.window.pre_present_notify();
            g.engine.queue.present(st);
            self.rt.after_present(now_epoch_ms());
        }
    }

    impl ApplicationHandler for App {
        fn resumed(&mut self, el: &ActiveEventLoop) {
            if self.gfx.is_none() {
                self.create(el);
            }
        }

        fn window_event(&mut self, el: &ActiveEventLoop, _id: WindowId, event: WindowEvent) {
            match event {
                WindowEvent::CloseRequested => el.exit(),
                WindowEvent::KeyboardInput {
                    event: KeyEvent { logical_key: Key::Named(NamedKey::Escape), state: ElementState::Pressed, .. },
                    ..
                } => el.exit(),
                WindowEvent::Resized(sz) => {
                    if let Some(g) = self.gfx.as_mut() {
                        g.config.width = sz.width.max(1);
                        g.config.height = sz.height.max(1);
                        g.surface.configure(&g.engine.device, &g.config);
                    }
                }
                WindowEvent::RedrawRequested => self.frame(),
                _ => {}
            }
        }

        fn about_to_wait(&mut self, _el: &ActiveEventLoop) {
            if let Some(g) = &self.gfx {
                g.window.request_redraw();
            }
        }
    }

    /// Visible en todos los Spaces, fuera de Mission Control y sobre la barra de menú (ver spike).
    #[cfg(target_os = "macos")]
    fn pin_window(window: &Window) {
        use objc2::{msg_send, runtime::AnyObject};
        use raw_window_handle::{HasWindowHandle, RawWindowHandle};
        let Ok(h) = window.window_handle() else { return };
        if let RawWindowHandle::AppKit(h) = h.as_raw() {
            let view = h.ns_view.as_ptr() as *mut AnyObject;
            // canJoinAllSpaces | stationary | ignoresCycle | fullScreenAuxiliary
            let behavior: usize = (1 << 0) | (1 << 4) | (1 << 6) | (1 << 8);
            unsafe {
                let win: *mut AnyObject = msg_send![view, window];
                if !win.is_null() {
                    let _: () = msg_send![win, setCollectionBehavior: behavior];
                    let _: () = msg_send![win, setLevel: 25isize]; // NSStatusWindowLevel
                    let _: () = msg_send![win, setHidesOnDeactivate: false];
                }
            }
        }
    }

    #[cfg(not(target_os = "macos"))]
    fn pin_window(_window: &Window) {}

    pub fn run(mut args: Args, rt: Runtime) {
        let cfg = std::mem::take(&mut args.cfg);
        let mut builder = EventLoop::builder();
        #[cfg(target_os = "macos")]
        {
            // Accessory: sin ícono en el Dock; puede quedar sobre Spaces de pantalla completa de otras apps.
            use winit::platform::macos::{ActivationPolicy, EventLoopBuilderExtMacOS};
            builder.with_activation_policy(ActivationPolicy::Accessory);
        }
        let event_loop = builder.build().expect("event loop");
        event_loop.set_control_flow(ControlFlow::Poll);
        let mut app = App { args, rt, gfx: None, frame_no: 0, cfg: Some(cfg) };
        event_loop.run_app(&mut app).expect("run");
    }
}
