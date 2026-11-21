//! Motor GPU: widgets en el espacio de la superficie (mm × ppmm) sobre un lienzo, capas por `z`
//! (vello para vectores/texto/imágenes, pipelines WGSL para `effect`) y una pasada final que aplica
//! la homografía mm→px o la LUT por píxel hacia el destino (ventana o textura offscreen).

use std::num::NonZeroUsize;
use std::path::PathBuf;
use std::time::{Duration, Instant};

use pcx_contracts::Widget;
use vello::peniko::Color;
use vello::{AaConfig, AaSupport, RenderParams, Renderer, RendererOptions, wgpu};

use crate::draw::Painter;
use crate::effects::Effects;
use crate::images::ImageCache;
use crate::lut::Lut;
use crate::scene::{Projection, SceneState};

const CANVAS_FMT: wgpu::TextureFormat = wgpu::TextureFormat::Rgba8Unorm;

pub struct EngineConfig {
    pub ppmm: f64,
    pub effects_dir: PathBuf,
    pub assets_dir: PathBuf,
    pub allow_remote_images: bool,
    /// Fuentes propias (`.ttf/.otf/.ttc`), primero en la pila; las del sistema quedan de respaldo.
    pub fonts_dir: PathBuf,
    /// LUT local por defecto (`--lut RUTA.npy`, `--lut-step MM`) si la escena no trae una `kind: lut`
    /// para este proyector. Sin verificación de sha256 (la eligió quien lanza el renderer).
    pub lut_path: Option<String>,
    pub lut_grid_step: f64,
}

impl Default for EngineConfig {
    fn default() -> Self {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        Self {
            allow_remote_images: false,
            ppmm: 2.5,
            effects_dir: root.join("effects"),
            assets_dir: root.join("assets"),
            fonts_dir: root.join("fonts"),
            lut_path: None,
            lut_grid_step: 1.0,
        }
    }
}

struct Canvas {
    tex: wgpu::Texture,
    view: wgpu::TextureView,
    scratch_view: wgpu::TextureView,
    blit_bg: wgpu::BindGroup,
    w: u32,
    h: u32,
}

/// LUT cargada como malla en la GPU. `key` = (ruta, versión, sha256).
struct LutState {
    key: (String, i64, String),
    verts: wgpu::Buffer,
    index: wgpu::Buffer,
    n_index: u32,
}

pub struct Engine {
    pub device: wgpu::Device,
    pub queue: wgpu::Queue,
    vello: Renderer,
    painter: Painter,
    pub images: ImageCache,
    pub effects: Effects,
    out_format: wgpu::TextureFormat,
    compose: wgpu::RenderPipeline,
    compose_bgl: wgpu::BindGroupLayout,
    blit: wgpu::RenderPipeline,
    pattern_pipeline: wgpu::RenderPipeline,
    pattern: Option<(u64, String, wgpu::Texture, wgpu::BindGroup)>,
    pattern_failed: Option<(u64, Instant)>,
    blit_bgl: wgpu::BindGroupLayout,
    sampler: wgpu::Sampler,
    uniforms: wgpu::Buffer,
    mesh: wgpu::RenderPipeline,
    canvas: Option<Canvas>,
    lut: Option<LutState>,
    lut_failed: Option<(String, i64, String)>,
    pub cfg: EngineConfig,
    vscene: vello::Scene,
}

/// Instancia + adaptador + device Metal (o el backend primario fuera de macOS).
pub fn gpu_init(
    instance: &wgpu::Instance,
    surface: Option<&wgpu::Surface<'_>>,
) -> (wgpu::Adapter, wgpu::Device, wgpu::Queue) {
    let adapter = pollster::block_on(instance.request_adapter(&wgpu::RequestAdapterOptions {
        power_preference: wgpu::PowerPreference::HighPerformance,
        compatible_surface: surface,
        force_fallback_adapter: false,
        apply_limit_buckets: false,
    }))
    .expect("adapter");
    let (device, queue) = pollster::block_on(adapter.request_device(&wgpu::DeviceDescriptor {
        label: None,
        required_features: wgpu::Features::empty(),
        required_limits: wgpu::Limits::default(),
        ..Default::default()
    }))
    .expect("device");
    (adapter, device, queue)
}

pub fn new_instance() -> wgpu::Instance {
    wgpu::Instance::new(wgpu::InstanceDescriptor {
        display: None,
        backends: if cfg!(target_os = "macos") { wgpu::Backends::METAL } else { wgpu::Backends::PRIMARY },
        flags: wgpu::InstanceFlags::default(),
        memory_budget_thresholds: Default::default(),
        backend_options: Default::default(),
    })
}

fn tex_entry(binding: u32, filterable: bool) -> wgpu::BindGroupLayoutEntry {
    wgpu::BindGroupLayoutEntry {
        binding,
        visibility: wgpu::ShaderStages::FRAGMENT,
        ty: wgpu::BindingType::Texture {
            sample_type: wgpu::TextureSampleType::Float { filterable },
            view_dimension: wgpu::TextureViewDimension::D2,
            multisampled: false,
        },
        count: None,
    }
}

fn fullscreen_pipeline(
    device: &wgpu::Device,
    label: &str,
    src: &str,
    bgl: &wgpu::BindGroupLayout,
    format: wgpu::TextureFormat,
    blend: Option<wgpu::BlendState>,
) -> wgpu::RenderPipeline {
    let module = device.create_shader_module(wgpu::ShaderModuleDescriptor {
        label: Some(label),
        source: wgpu::ShaderSource::Wgsl(src.into()),
    });
    let layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
        label: Some(label),
        bind_group_layouts: &[Some(bgl)],
        immediate_size: 0,
    });
    device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
        label: Some(label),
        layout: Some(&layout),
        vertex: wgpu::VertexState { module: &module, entry_point: Some("vs_main"), compilation_options: Default::default(), buffers: &[] },
        primitive: wgpu::PrimitiveState::default(),
        depth_stencil: None,
        multisample: wgpu::MultisampleState::default(),
        fragment: Some(wgpu::FragmentState {
            module: &module,
            entry_point: Some("fs_main"),
            compilation_options: Default::default(),
            targets: &[Some(wgpu::ColorTargetState { format, blend, write_mask: wgpu::ColorWrites::ALL })],
        }),
        multiview_mask: None,
        cache: None,
    })
}

/// Homografía por defecto: la superficie centrada y escalada para caber en la salida.
pub fn fit_homography(w_mm: f64, h_mm: f64, out_w: f64, out_h: f64) -> [f64; 9] {
    let s = (out_w / w_mm).min(out_h / h_mm);
    [s, 0.0, (out_w - w_mm * s) / 2.0, 0.0, s, (out_h - h_mm * s) / 2.0, 0.0, 0.0, 1.0]
}

pub fn invert3(m: &[f64; 9]) -> Option<[f64; 9]> {
    let [a, b, c, d, e, f, g, h, i] = *m;
    let co = [e * i - f * h, -(d * i - f * g), d * h - e * g];
    let det = a * co[0] + b * co[1] + c * co[2];
    if det.abs() < 1e-12 {
        return None;
    }
    let inv = [co[0], -(b * i - c * h), b * f - c * e, co[1], a * i - c * g, -(a * f - c * d), co[2], -(a * h - b * g), a * e - b * d];
    Some(inv.map(|v| v / det))
}

fn mesh_pipeline(device: &wgpu::Device, bgl: &wgpu::BindGroupLayout, format: wgpu::TextureFormat) -> wgpu::RenderPipeline {
    let module = device.create_shader_module(wgpu::ShaderModuleDescriptor {
        label: Some("lut-mesh"),
        source: wgpu::ShaderSource::Wgsl(include_str!("compose.wgsl").into()),
    });
    let layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
        label: Some("lut-mesh"),
        bind_group_layouts: &[Some(bgl)],
        immediate_size: 0,
    });
    device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
        label: Some("lut-mesh"),
        layout: Some(&layout),
        vertex: wgpu::VertexState {
            module: &module,
            entry_point: Some("vs_mesh"),
            compilation_options: Default::default(),
            buffers: &[Some(wgpu::VertexBufferLayout {
                array_stride: 16,
                step_mode: wgpu::VertexStepMode::Vertex,
                attributes: &[wgpu::VertexAttribute { format: wgpu::VertexFormat::Float32x4, offset: 0, shader_location: 0 }],
            })],
        },
        primitive: wgpu::PrimitiveState::default(),
        depth_stencil: None,
        multisample: wgpu::MultisampleState::default(),
        fragment: Some(wgpu::FragmentState {
            module: &module,
            entry_point: Some("fs_mesh"),
            compilation_options: Default::default(),
            targets: &[Some(wgpu::ColorTargetState { format, blend: None, write_mask: wgpu::ColorWrites::ALL })],
        }),
        multiview_mask: None,
        cache: None,
    })
}

enum Layer<'a> {
    Vector(Vec<&'a Widget>),
    Effect(&'a pcx_contracts::Effect),
}

#[derive(Debug, Default, Clone, Copy)]
pub struct FrameInfo {
    pub build_ms: f64,
    pub layers: usize,
}

impl Engine {
    pub fn new(device: wgpu::Device, queue: wgpu::Queue, out_format: wgpu::TextureFormat, cfg: EngineConfig) -> Self {
        let vello = Renderer::new(
            &device,
            RendererOptions {
                use_cpu: false,
                antialiasing_support: AaSupport::area_only(),
                num_init_threads: NonZeroUsize::new(1),
                pipeline_cache: None,
            },
        )
        .expect("vello");
        let uniform_entry = wgpu::BindGroupLayoutEntry {
            binding: 2,
            visibility: wgpu::ShaderStages::VERTEX_FRAGMENT,
            ty: wgpu::BindingType::Buffer { ty: wgpu::BufferBindingType::Uniform, has_dynamic_offset: false, min_binding_size: None },
            count: None,
        };
        let compose_bgl = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("compose"),
            entries: &[
                tex_entry(0, true),
                wgpu::BindGroupLayoutEntry {
                    binding: 1,
                    visibility: wgpu::ShaderStages::FRAGMENT,
                    ty: wgpu::BindingType::Sampler(wgpu::SamplerBindingType::Filtering),
                    count: None,
                },
                uniform_entry,
            ],
        });
        let compose = fullscreen_pipeline(&device, "compose", include_str!("compose.wgsl"), &compose_bgl, out_format, None);
        let blit_bgl = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("blit"),
            entries: &[tex_entry(0, false)],
        });
        let blend = wgpu::BlendState {
            color: wgpu::BlendComponent {
                src_factor: wgpu::BlendFactor::SrcAlpha,
                dst_factor: wgpu::BlendFactor::OneMinusSrcAlpha,
                operation: wgpu::BlendOperation::Add,
            },
            alpha: wgpu::BlendComponent::OVER,
        };
        let blit = fullscreen_pipeline(&device, "blit", include_str!("blit.wgsl"), &blit_bgl, CANVAS_FMT, Some(blend));
        let pattern_pipeline = fullscreen_pipeline(&device, "pattern", include_str!("pattern.wgsl"), &blit_bgl, out_format, None);
        let sampler = device.create_sampler(&wgpu::SamplerDescriptor {
            mag_filter: wgpu::FilterMode::Linear,
            min_filter: wgpu::FilterMode::Linear,
            ..Default::default()
        });
        let uniforms = device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("compose-u"),
            size: 80,
            usage: wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST,
            mapped_at_creation: false,
        });
        let mesh = mesh_pipeline(&device, &compose_bgl, out_format);
        let effects = Effects::new(&device, cfg.effects_dir.clone(), CANVAS_FMT);
        Self {
            vello,
            painter: Painter::new(Some(&cfg.fonts_dir)),
            images: ImageCache::with_policy(cfg.assets_dir.clone(), cfg.allow_remote_images),
            effects,
            out_format,
            compose,
            compose_bgl,
            blit,
            pattern_pipeline,
            pattern: None,
            pattern_failed: None,
            blit_bgl,
            sampler,
            uniforms,
            mesh,
            canvas: None,
            lut: None,
            lut_failed: None,
            cfg,
            vscene: vello::Scene::new(),
            device,
            queue,
        }
    }

    /// Motor sin ventana (tests, benchmark y `--headless`).
    pub fn headless(cfg: EngineConfig) -> Self {
        let instance = new_instance();
        let (_, device, queue) = gpu_init(&instance, None);
        Self::new(device, queue, wgpu::TextureFormat::Rgba8Unorm, cfg)
    }

    pub fn out_format(&self) -> wgpu::TextureFormat {
        self.out_format
    }

    pub fn create_target(&self, w: u32, h: u32) -> wgpu::Texture {
        self.device.create_texture(&wgpu::TextureDescriptor {
            label: Some("offscreen"),
            size: wgpu::Extent3d { width: w, height: h, depth_or_array_layers: 1 },
            mip_level_count: 1,
            sample_count: 1,
            dimension: wgpu::TextureDimension::D2,
            format: self.out_format,
            usage: wgpu::TextureUsages::RENDER_ATTACHMENT | wgpu::TextureUsages::COPY_SRC,
            view_formats: &[],
        })
    }

    fn ensure_canvas(&mut self, cw: u32, ch: u32) {
        if self.canvas.as_ref().is_some_and(|c| c.w == cw && c.h == ch) {
            return;
        }
        let mk = |label, usage| {
            self.device.create_texture(&wgpu::TextureDescriptor {
                label: Some(label),
                size: wgpu::Extent3d { width: cw, height: ch, depth_or_array_layers: 1 },
                mip_level_count: 1,
                sample_count: 1,
                dimension: wgpu::TextureDimension::D2,
                format: CANVAS_FMT,
                usage,
                view_formats: &[],
            })
        };
        let u = wgpu::TextureUsages::STORAGE_BINDING | wgpu::TextureUsages::TEXTURE_BINDING;
        let tex = mk("canvas", u | wgpu::TextureUsages::RENDER_ATTACHMENT | wgpu::TextureUsages::COPY_SRC);
        let scratch = mk("scratch", u);
        let view = tex.create_view(&Default::default());
        let scratch_view = scratch.create_view(&Default::default());
        let blit_bg = self.device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("blit"),
            layout: &self.blit_bgl,
            entries: &[wgpu::BindGroupEntry { binding: 0, resource: wgpu::BindingResource::TextureView(&scratch_view) }],
        });
        self.canvas = Some(Canvas { tex, view, scratch_view, blit_bg, w: cw, h: ch });
    }

    /// Deja lista la LUT que pide la escena (o `--lut`). Devuelve `false` si no hay LUT usable.
    fn update_lut(&mut self, projection: &Projection) -> bool {
        let want = match projection {
            Projection::Lut { lut, .. } => Some((
                lut.lut.path.clone(),
                lut.version,
                lut.lut.sha256.clone(),
                lut.lut.grid_step,
                Some((lut.lut.width, lut.lut.height)),
            )),
            _ => self.cfg.lut_path.clone().map(|p| (p, 0, String::new(), self.cfg.lut_grid_step, None)),
        };
        let Some((path, version, sha, step, dims)) = want else {
            self.lut = None;
            return false;
        };
        let key = (path, version, sha);
        if self.lut.as_ref().is_some_and(|l| l.key == key) {
            return true;
        }
        if self.lut_failed.as_ref() == Some(&key) {
            return false;
        }
        let loaded = Lut::load(&key.0, (!key.2.is_empty()).then_some(key.2.as_str())).and_then(|l| match dims {
            Some((w, h)) if (w, h) != (l.width, l.height) => {
                Err(format!("el descriptor dice {w}x{h} y el archivo {}x{}", l.width, l.height))
            }
            _ => Ok(l),
        });
        match loaded {
            Ok(lut) => {
                use vello::wgpu::util::DeviceExt;
                let (verts, idx) = lut.mesh(step);
                eprintln!("[lut] {} v{} ({}x{}, {} triángulos)", key.0, key.1, lut.width, lut.height, idx.len() / 3);
                let vb: Vec<u8> = verts.iter().flatten().flat_map(|f| f.to_le_bytes()).collect();
                let ib: Vec<u8> = idx.iter().flat_map(|i| i.to_le_bytes()).collect();
                let verts = self.device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
                    label: Some("lut-v"),
                    contents: &vb,
                    usage: wgpu::BufferUsages::VERTEX,
                });
                let index = self.device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
                    label: Some("lut-i"),
                    contents: if ib.is_empty() { &[0u8; 4] } else { &ib },
                    usage: wgpu::BufferUsages::INDEX,
                });
                self.lut = Some(LutState { key, verts, index, n_index: idx.len() as u32 });
                self.lut_failed = None;
                true
            }
            Err(e) => {
                // Como `projector_matrix` del contrato: `approx_matrix` o la mejor homografía de la escena
                // (no una LUT anterior que la escena ya no lista).
                eprintln!("[lut] no pude usar {}: {e}; uso la homografía de respaldo", key.0);
                self.lut_failed = Some(key);
                self.lut = None;
                false
            }
        }
    }

    /// Recarga efectos cambiados en disco.
    pub fn poll_effects(&mut self) -> Vec<(String, Result<(), String>)> {
        self.effects.poll(&self.device)
    }

    /// Dibuja `state` en el instante `t` (s) hacia `target` (`out_w × out_h` px).
    pub fn render(&mut self, state: &SceneState, t: f64, now_s: f64, target: &wgpu::TextureView, out_w: u32, out_h: u32) -> FrameInfo {
        let ppmm = self.cfg.ppmm;
        let surf = &state.surface;
        let cw = (surf.width_mm * ppmm).round().clamp(1.0, 8192.0) as u32;
        let ch = (surf.height_mm * ppmm).round().clamp(1.0, 8192.0) as u32;
        self.ensure_canvas(cw, ch);
        let projection = state.projection();
        let use_lut = self.update_lut(&projection);
        self.painter.begin_frame();

        // Capas por `z`: dentro de un mismo `z`, primero los vectores (una sola pasada de vello) y
        // encima los efectos. Así un `z` común cuesta un render de vello, no uno por cada efecto.
        let widgets = state.visible(now_s);
        let mut layers: Vec<Layer> = vec![];
        let mut i = 0;
        while i < widgets.len() {
            let z = widgets[i].common().z;
            let j = widgets[i..].iter().position(|w| w.common().z != z).map_or(widgets.len(), |k| i + k);
            let group = &widgets[i..j];
            let vecs: Vec<&Widget> = group.iter().copied().filter(|w| !matches!(w, Widget::Effect(_))).collect();
            if !vecs.is_empty() {
                match layers.last_mut() {
                    Some(Layer::Vector(v)) => v.extend(vecs),
                    _ => layers.push(Layer::Vector(vecs)),
                }
            }
            for w in group {
                if let Widget::Effect(e) = w {
                    layers.push(Layer::Effect(e));
                }
            }
            i = j;
        }

        let mut build_ms = 0.0;
        let canvas = self.canvas.as_ref().unwrap();
        let mut cleared = false;
        let canvas_load = |cleared: bool| if cleared { wgpu::LoadOp::Load } else { wgpu::LoadOp::Clear(wgpu::Color::BLACK) };
        for layer in &layers {
            match layer {
                Layer::Vector(ws) => {
                    let tb = Instant::now();
                    self.vscene.reset();
                    for w in ws {
                        self.painter.draw(&mut self.vscene, w, ppmm, t, &mut self.images);
                    }
                    build_ms += tb.elapsed().as_secs_f64() * 1000.0;
                    let (view, base) = if cleared { (&canvas.scratch_view, Color::TRANSPARENT) } else { (&canvas.view, Color::BLACK) };
                    self.vello
                        .render_to_texture(&self.device, &self.queue, &self.vscene, view, &RenderParams {
                            base_color: base,
                            width: cw,
                            height: ch,
                            antialiasing_method: AaConfig::Area,
                        })
                        .expect("vello render");
                    if cleared {
                        let mut enc = self.device.create_command_encoder(&Default::default());
                        {
                            let mut pass = canvas_pass(&mut enc, &canvas.view, wgpu::LoadOp::Load);
                            pass.set_pipeline(&self.blit);
                            pass.set_bind_group(0, &canvas.blit_bg, &[]);
                            pass.draw(0..3, 0..1);
                        }
                        self.queue.submit([enc.finish()]);
                    }
                    cleared = true;
                }
                Layer::Effect(e) => {
                    let prepared = self.effects.prepare(&self.device, e, t, ppmm, (cw, ch));
                    let mut enc = self.device.create_command_encoder(&Default::default());
                    {
                        let mut pass = canvas_pass(&mut enc, &canvas.view, canvas_load(cleared));
                        if let Some((pipeline, bg)) = &prepared {
                            pass.set_pipeline(pipeline);
                            pass.set_bind_group(0, bg, &[]);
                            pass.draw(0..6, 0..1);
                        }
                    }
                    self.queue.submit([enc.finish()]);
                    cleared = true;
                }
            }
        }

        // Pasada final: homografía o LUT hacia la salida.
        let fit = || fit_homography(surf.width_mm, surf.height_mm, out_w as f64, out_h as f64);
        let hom = match projection {
            Projection::Homography(m) => m,
            Projection::Lut { fallback, .. } => fallback.unwrap_or_else(fit),
            Projection::Fit => fit(),
        };
        let hinv = invert3(&hom).unwrap_or([1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]);
        let u: [f32; 20] = [
            hinv[0] as f32, hinv[1] as f32, hinv[2] as f32, 0.0,
            hinv[3] as f32, hinv[4] as f32, hinv[5] as f32, 0.0,
            hinv[6] as f32, hinv[7] as f32, hinv[8] as f32, 0.0,
            surf.width_mm as f32, surf.height_mm as f32, 0.0, 0.0,
            out_w as f32, out_h as f32, 0.0, 0.0,
        ];
        let bytes: Vec<u8> = u.iter().flat_map(|f| f.to_le_bytes()).collect();
        self.queue.write_buffer(&self.uniforms, 0, &bytes);
        let bg = self.device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("compose"),
            layout: &self.compose_bgl,
            entries: &[
                wgpu::BindGroupEntry { binding: 0, resource: wgpu::BindingResource::TextureView(&canvas.view) },
                wgpu::BindGroupEntry { binding: 1, resource: wgpu::BindingResource::Sampler(&self.sampler) },
                wgpu::BindGroupEntry { binding: 2, resource: self.uniforms.as_entire_binding() },
            ],
        });
        let mut enc = self.device.create_command_encoder(&Default::default());
        if !cleared {
            drop(canvas_pass(&mut enc, &canvas.view, wgpu::LoadOp::Clear(wgpu::Color::BLACK)));
        }
        {
            let mut pass = enc.begin_render_pass(&wgpu::RenderPassDescriptor {
                label: Some("compose"),
                color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                    view: target,
                    depth_slice: None,
                    resolve_target: None,
                    ops: wgpu::Operations { load: wgpu::LoadOp::Clear(wgpu::Color::BLACK), store: wgpu::StoreOp::Store },
                })],
                depth_stencil_attachment: None,
                timestamp_writes: None,
                occlusion_query_set: None,
                multiview_mask: None,
            });
            pass.set_bind_group(0, &bg, &[]);
            match (&self.lut, use_lut) {
                (Some(l), true) => {
                    pass.set_pipeline(&self.mesh);
                    pass.set_vertex_buffer(0, l.verts.slice(..));
                    pass.set_index_buffer(l.index.slice(..), wgpu::IndexFormat::Uint32);
                    pass.draw_indexed(0..l.n_index, 0, 0..1);
                }
                _ => {
                    pass.set_pipeline(&self.compose);
                    pass.draw(0..3, 0..1);
                }
            }
        }
        self.queue.submit([enc.finish()]);
        FrameInfo { build_ms, layers: layers.len() }
    }

    /// Patrón en coordenadas nativas: origen (0,0), 1 texel = 1 px, negro fuera de la imagen.
    /// Se salta por completo el lienzo, widgets, homografía y LUT.
    pub fn render_pattern(&mut self, generation: u64, src: &str, target: &wgpu::TextureView) -> bool {
        if !self.pattern.as_ref().is_some_and(|(serial, key, _, _)| *serial == generation && key == src) {
            if self.pattern_failed.as_ref().is_some_and(|(serial, time)| *serial == generation && time.elapsed() < Duration::from_secs(2)) {
                return false;
            }
            let image = match crate::images::load_pattern(src) {
                Ok(image) => image,
                Err(error) => {
                    eprintln!("[pattern] imagen rechazada: {error}");
                    self.pattern_failed = Some((generation, Instant::now()));
                    return false;
                }
            };
            self.pattern_failed = None;
            let texture = self.device.create_texture(&wgpu::TextureDescriptor {
                label: Some("pattern"),
                size: wgpu::Extent3d { width: image.width, height: image.height, depth_or_array_layers: 1 },
                mip_level_count: 1,
                sample_count: 1,
                dimension: wgpu::TextureDimension::D2,
                format: wgpu::TextureFormat::Rgba8Unorm,
                usage: wgpu::TextureUsages::TEXTURE_BINDING | wgpu::TextureUsages::COPY_DST,
                view_formats: &[],
            });
            self.queue.write_texture(
                texture.as_image_copy(),
                image.data.data(),
                wgpu::TexelCopyBufferLayout { offset: 0, bytes_per_row: Some(image.width * 4), rows_per_image: Some(image.height) },
                texture.size(),
            );
            let bg = self.device.create_bind_group(&wgpu::BindGroupDescriptor {
                label: Some("pattern"),
                layout: &self.blit_bgl,
                entries: &[wgpu::BindGroupEntry {
                    binding: 0,
                    resource: wgpu::BindingResource::TextureView(&texture.create_view(&Default::default())),
                }],
            });
            self.pattern = Some((generation, src.to_owned(), texture, bg));
        }
        let mut enc = self.device.create_command_encoder(&Default::default());
        {
            let mut pass = canvas_pass(&mut enc, target, wgpu::LoadOp::Clear(wgpu::Color::BLACK));
            pass.set_pipeline(&self.pattern_pipeline);
            pass.set_bind_group(0, &self.pattern.as_ref().unwrap().3, &[]);
            pass.draw(0..3, 0..1);
        }
        self.queue.submit([enc.finish()]);
        true
    }

    /// Cuadro negro (para `blank`): la escena no se dibuja.
    pub fn render_black(&self, target: &wgpu::TextureView) {
        let mut enc = self.device.create_command_encoder(&Default::default());
        drop(canvas_pass(&mut enc, target, wgpu::LoadOp::Clear(wgpu::Color::BLACK)));
        self.queue.submit([enc.finish()]);
    }

    /// Espera a que la GPU termine lo enviado (headless: "presentado").
    pub fn wait_idle(&self) {
        let _ = self.device.poll(wgpu::PollType::wait_indefinitely());
    }

    /// Lee una textura RGBA/BGRA a RGBA8.
    pub fn readback(&self, tex: &wgpu::Texture) -> image::RgbaImage {
        let (w, h) = (tex.width(), tex.height());
        let bpr = (w * 4).div_ceil(wgpu::COPY_BYTES_PER_ROW_ALIGNMENT) * wgpu::COPY_BYTES_PER_ROW_ALIGNMENT;
        let buf = self.device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("readback"),
            size: (bpr * h) as u64,
            usage: wgpu::BufferUsages::COPY_DST | wgpu::BufferUsages::MAP_READ,
            mapped_at_creation: false,
        });
        let mut enc = self.device.create_command_encoder(&Default::default());
        enc.copy_texture_to_buffer(
            tex.as_image_copy(),
            wgpu::TexelCopyBufferInfo {
                buffer: &buf,
                layout: wgpu::TexelCopyBufferLayout { offset: 0, bytes_per_row: Some(bpr), rows_per_image: Some(h) },
            },
            tex.size(),
        );
        self.queue.submit([enc.finish()]);
        buf.slice(..).map_async(wgpu::MapMode::Read, |_| {});
        self.wait_idle();
        let bgra = matches!(tex.format(), wgpu::TextureFormat::Bgra8Unorm | wgpu::TextureFormat::Bgra8UnormSrgb);
        let data = buf.slice(..).get_mapped_range().expect("map");
        let mut px = Vec::with_capacity((w * h * 4) as usize);
        for row in 0..h {
            for c in data[(row * bpr) as usize..(row * bpr + w * 4) as usize].as_chunks::<4>().0 {
                if bgra {
                    px.extend_from_slice(&[c[2], c[1], c[0], 255]);
                } else {
                    px.extend_from_slice(&[c[0], c[1], c[2], 255]);
                }
            }
        }
        drop(data);
        buf.unmap();
        image::RgbaImage::from_raw(w, h, px).expect("imagen")
    }

    /// Lienzo de la superficie (antes de la homografía), para depurar.
    pub fn readback_canvas(&self) -> Option<image::RgbaImage> {
        self.canvas.as_ref().map(|c| self.readback(&c.tex))
    }
}

fn canvas_pass<'e>(enc: &'e mut wgpu::CommandEncoder, view: &'e wgpu::TextureView, load: wgpu::LoadOp<wgpu::Color>) -> wgpu::RenderPass<'e> {
    enc.begin_render_pass(&wgpu::RenderPassDescriptor {
        label: Some("canvas"),
        color_attachments: &[Some(wgpu::RenderPassColorAttachment {
            view,
            depth_slice: None,
            resolve_target: None,
            ops: wgpu::Operations { load, store: wgpu::StoreOp::Store },
        })],
        depth_stencil_attachment: None,
        timestamp_writes: None,
        occlusion_query_set: None,
        multiview_mask: None,
    })
}

#[cfg(test)]
mod pattern_tests {
    #[test]
    fn pattern_shader_validates_without_gpu() {
        let module = vello::wgpu::naga::front::wgsl::parse_str(include_str!("pattern.wgsl")).unwrap();
        vello::wgpu::naga::valid::Validator::new(
            vello::wgpu::naga::valid::ValidationFlags::all(),
            vello::wgpu::naga::valid::Capabilities::all(),
        )
        .validate(&module)
        .unwrap();
    }
}
