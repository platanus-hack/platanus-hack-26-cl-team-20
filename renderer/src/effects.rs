//! Catálogo de efectos WGSL (`effects/*.wgsl`) con recarga en caliente.
//!
//! Cada archivo define `fn effect(p: vec2<f32>) -> vec4<f32>` (p en mm) y se compila con el
//! prelude `effects_prelude.wgsl` (uniforms `fx`: tiempo, bbox, color, params, polígono).
//! Antes de crear el pipeline se valida con naga: un shader roto se rechaza y sigue el anterior.
//! Encabezado opcional `// params: a=1.0, b=2.0` → `fx_param(0)`, `fx_param(1)` (máx. 8),
//! con los valores por defecto; `widget.params` los sobreescribe por nombre. `params.color`
//! (`"#RRGGBB[AA]"` o `[r,g,b(,a)]` 0..1) va a `fx.color`. `margin_mm` agranda el bbox dibujado.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::time::SystemTime;

use pcx_contracts::Effect;
use serde_json::Value;
use vello::wgpu;

pub const PRELUDE: &str = include_str!("effects_prelude.wgsl");
pub const UNIFORM_SIZE: u64 = 6 * 16 + 16 * 16;
const MAX_PARAMS: usize = 8;
const MAX_PTS: usize = 32;

pub struct FxShader {
    pub pipeline: wgpu::RenderPipeline,
    pub params: Vec<(String, f32)>,
    mtime: Option<SystemTime>,
}

pub struct Effects {
    pub dir: PathBuf,
    format: wgpu::TextureFormat,
    bgl: wgpu::BindGroupLayout,
    layout: wgpu::PipelineLayout,
    pub shaders: HashMap<String, FxShader>,
    /// Último error por efecto (se limpia cuando el archivo vuelve a compilar).
    pub errors: HashMap<String, String>,
    seen: HashMap<String, Option<SystemTime>>,
}

/// Valida `PRELUDE + src` con naga. Devuelve el WGSL completo.
pub fn validate(src: &str) -> Result<String, String> {
    let full = format!("{PRELUDE}{src}");
    let module = wgpu::naga::front::wgsl::parse_str(&full).map_err(|e| e.emit_to_string(&full))?;
    wgpu::naga::valid::Validator::new(wgpu::naga::valid::ValidationFlags::all(), wgpu::naga::valid::Capabilities::all())
        .validate(&module)
        .map_err(|e| e.emit_to_string(&full))?;
    Ok(full)
}

/// Lee `// params: a=1, b=2` del encabezado.
pub fn parse_params_header(src: &str) -> Vec<(String, f32)> {
    let Some(line) = src.lines().find_map(|l| l.trim().strip_prefix("// params:")) else { return vec![] };
    line.split(',')
        .filter_map(|kv| {
            let (k, v) = kv.split_once('=').unwrap_or((kv, "0"));
            let k = k.trim();
            (!k.is_empty()).then(|| (k.to_owned(), v.trim().parse().unwrap_or(0.0)))
        })
        .take(MAX_PARAMS)
        .collect()
}

impl Effects {
    pub fn new(device: &wgpu::Device, dir: PathBuf, format: wgpu::TextureFormat) -> Self {
        let bgl = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("fx"),
            entries: &[wgpu::BindGroupLayoutEntry {
                binding: 0,
                visibility: wgpu::ShaderStages::VERTEX_FRAGMENT,
                ty: wgpu::BindingType::Buffer {
                    ty: wgpu::BufferBindingType::Uniform,
                    has_dynamic_offset: false,
                    min_binding_size: None,
                },
                count: None,
            }],
        });
        let layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
            label: Some("fx"),
            bind_group_layouts: &[Some(&bgl)],
            immediate_size: 0,
        });
        let mut fx = Self { dir, format, bgl, layout, shaders: HashMap::new(), errors: HashMap::new(), seen: HashMap::new() };
        fx.poll(device);
        fx
    }

    /// Revisa el directorio y recompila lo que cambió. Devuelve `(nombre, Ok|Err)` de lo recargado.
    pub fn poll(&mut self, device: &wgpu::Device) -> Vec<(String, Result<(), String>)> {
        let mut out = vec![];
        let Ok(rd) = std::fs::read_dir(&self.dir) else { return out };
        for e in rd.flatten() {
            let path = e.path();
            if path.extension().and_then(|x| x.to_str()) != Some("wgsl") {
                continue;
            }
            let Some(name) = path.file_stem().and_then(|s| s.to_str()).map(str::to_owned) else { continue };
            let mtime = e.metadata().and_then(|m| m.modified()).ok();
            if self.seen.get(&name) == Some(&mtime) {
                continue;
            }
            self.seen.insert(name.clone(), mtime);
            let r = self.load(device, &name, &path, mtime);
            match &r {
                Ok(()) => {
                    self.errors.remove(&name);
                    eprintln!("[fx] {name} cargado");
                }
                Err(err) => {
                    let keep = if self.shaders.contains_key(&name) { "sigo con el anterior" } else { "sin versión previa" };
                    eprintln!("[fx] {name} rechazado ({keep}):\n{err}");
                    self.errors.insert(name.clone(), err.clone());
                }
            }
            out.push((name, r));
        }
        out
    }

    fn load(&mut self, device: &wgpu::Device, name: &str, path: &Path, mtime: Option<SystemTime>) -> Result<(), String> {
        let src = std::fs::read_to_string(path).map_err(|e| e.to_string())?;
        let full = validate(&src)?;
        let module = device.create_shader_module(wgpu::ShaderModuleDescriptor {
            label: Some(name),
            source: wgpu::ShaderSource::Wgsl(full.into()),
        });
        let blend = wgpu::BlendState {
            color: wgpu::BlendComponent {
                src_factor: wgpu::BlendFactor::SrcAlpha,
                dst_factor: wgpu::BlendFactor::OneMinusSrcAlpha,
                operation: wgpu::BlendOperation::Add,
            },
            alpha: wgpu::BlendComponent::OVER,
        };
        let pipeline = device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
            label: Some(name),
            layout: Some(&self.layout),
            vertex: wgpu::VertexState { module: &module, entry_point: Some("vs_main"), compilation_options: Default::default(), buffers: &[] },
            primitive: wgpu::PrimitiveState::default(),
            depth_stencil: None,
            multisample: wgpu::MultisampleState::default(),
            fragment: Some(wgpu::FragmentState {
                module: &module,
                entry_point: Some("fs_main"),
                compilation_options: Default::default(),
                targets: &[Some(wgpu::ColorTargetState { format: self.format, blend: Some(blend), write_mask: wgpu::ColorWrites::ALL })],
            }),
            multiview_mask: None,
            cache: None,
        });
        self.shaders.insert(name.to_owned(), FxShader { pipeline, params: parse_params_header(&src), mtime });
        Ok(())
    }

    /// Prepara el bind group de un widget `effect`. `None` si el shader no existe o no hay geometría.
    pub fn prepare(
        &self,
        device: &wgpu::Device,
        w: &Effect,
        t: f64,
        ppmm: f64,
        canvas_px: (u32, u32),
    ) -> Option<(&wgpu::RenderPipeline, wgpu::BindGroup)> {
        let sh = self.shaders.get(&w.shader)?;
        let pts: Vec<[f64; 2]> = match (&w.polygon_mm, &w.rect_mm) {
            (Some(p), _) if p.len() >= 3 => p.iter().take(MAX_PTS).map(|p| [p.0, p.1]).collect(),
            (_, Some(r)) => vec![[r.0, r.1], [r.0 + r.2, r.1], [r.0 + r.2, r.1 + r.3], [r.0, r.1 + r.3]],
            _ => return None,
        };
        let mut params = [0f32; MAX_PARAMS];
        let mut margin = 0.0f32;
        for (i, (k, def)) in sh.params.iter().enumerate() {
            let v = w.params.get(k).and_then(Value::as_f64).map_or(*def, |v| v as f32);
            params[i] = v;
            if k == "margin_mm" {
                margin = v;
            }
        }
        let color = w.params.get("color").map_or([0.0, 0.898, 1.0, 1.0], value_color);
        let (mut x0, mut y0, mut x1, mut y1) = (f64::MAX, f64::MAX, f64::MIN, f64::MIN);
        for p in &pts {
            x0 = x0.min(p[0]);
            y0 = y0.min(p[1]);
            x1 = x1.max(p[0]);
            y1 = y1.max(p[1]);
        }
        let m = margin as f64;
        let mut u: Vec<f32> = vec![
            (x0 - m) as f32, (y0 - m) as f32, (x1 - x0 + 2.0 * m) as f32, (y1 - y0 + 2.0 * m) as f32,
        ];
        u.extend_from_slice(&color);
        u.extend_from_slice(&params);
        u.extend_from_slice(&[t as f32, ppmm as f32, pts.len() as f32, margin]);
        u.extend_from_slice(&[canvas_px.0 as f32, canvas_px.1 as f32, 0.0, 0.0]);
        for i in 0..MAX_PTS {
            let p = pts.get(i).copied().unwrap_or([0.0, 0.0]);
            u.extend_from_slice(&[p[0] as f32, p[1] as f32]);
        }
        let bytes: Vec<u8> = u.iter().flat_map(|f| f.to_le_bytes()).collect();
        debug_assert_eq!(bytes.len() as u64, UNIFORM_SIZE);
        let buf = device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("fx-u"),
            size: UNIFORM_SIZE,
            usage: wgpu::BufferUsages::UNIFORM,
            mapped_at_creation: true,
        });
        buf.slice(..).get_mapped_range_mut().expect("map fx").copy_from_slice(&bytes);
        buf.unmap();
        let bg = device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("fx"),
            layout: &self.bgl,
            entries: &[wgpu::BindGroupEntry { binding: 0, resource: buf.as_entire_binding() }],
        });
        Some((&sh.pipeline, bg))
    }

    pub fn mtime(&self, name: &str) -> Option<SystemTime> {
        self.shaders.get(name).and_then(|s| s.mtime)
    }
}

fn value_color(v: &Value) -> [f32; 4] {
    match v {
        Value::String(s) => {
            let c = crate::draw::parse_color(s).to_rgba8();
            [c.r as f32 / 255.0, c.g as f32 / 255.0, c.b as f32 / 255.0, c.a as f32 / 255.0]
        }
        Value::Array(a) => {
            let f = |i: usize, d: f32| a.get(i).and_then(Value::as_f64).map_or(d, |v| v as f32);
            [f(0, 1.0), f(1, 1.0), f(2, 1.0), f(3, 1.0)]
        }
        _ => [1.0; 4],
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ejemplos_validan_y_roto_no() {
        for f in ["halo_glow", "marco_respira", "ripple"] {
            let p = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("effects").join(format!("{f}.wgsl"));
            let src = std::fs::read_to_string(p).unwrap();
            validate(&src).unwrap_or_else(|e| panic!("{f}: {e}"));
        }
        assert!(validate("fn effect(p: vec2<f32>) -> vec4<f32> { return 1.0; }").is_err());
        assert!(validate("fn effect(p: vec2<f32>) -> vec4<f32> { return vec4<f32>(fx_nope()); }").is_err());
    }

    #[test]
    fn encabezado_de_params() {
        let p = parse_params_header("// x\n// params: a=1.5, b, c=-2\nfn effect...");
        assert_eq!(p, vec![("a".into(), 1.5), ("b".into(), 0.0), ("c".into(), -2.0)]);
    }
}
