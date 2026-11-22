//! Escenas sintéticas y benchmark headless (ms por cuadro con N widgets).

use std::time::Instant;

use pcx_contracts::ToRenderer;
use serde_json::{Value, json};

use crate::engine::Engine;
use crate::runtime::Runtime;

/// Escena con `n` widgets de todos los tipos (1 de cada 10 es un `effect`). `t` anima geometría y texto.
pub fn synthetic_scene(n: usize, rev: i64, t: f64) -> ToRenderer {
    let mut widgets: Vec<Value> = vec![];
    for i in 0..n {
        let (col, row) = ((i % 14) as f64, (i / 14) as f64);
        let (x, y) = (15.0 + col * 55.0, 15.0 + (row % 15.0) * 32.0);
        let id = format!("w{i}");
        let wob = (t * 2.0 + i as f64).sin() * 3.0;
        let w = match i % 10 {
            0 | 5 => {
                json!({"type":"label","id":id,"anchor":{"point_mm":[x, y + wob]},"text":format!("Etiqueta {i} ✓ {:.1}", t),"size_mm":6,"color":"#FFFFFF","bg":"#00000099"})
            }
            1 => {
                json!({"type":"halo","id":id,"polygon_mm":[[x,y],[x+40.0,y],[x+40.0,y+25.0],[x,y+25.0]],"color":"#00E5FF","width_mm":2,"pulse_hz":1.5})
            }
            2 | 7 => {
                json!({"type":"arrow","id":id,"from_mm":[x,y],"to_mm":[x+40.0,y+20.0+wob],"color":"#FFD400","width_mm":2})
            }
            3 => {
                json!({"type":"panel","id":id,"rect_mm":[x,y,50,28],"title":"Paso","body":"Texto de un panel con ajuste de línea.","color":"#FFFFFF"})
            }
            4 | 8 => {
                json!({"type":"button","id":id,"rect_mm":[x,y,45,20],"label":"OK ✓","progress":(t * 0.3 + i as f64 * 0.1).fract(),"color":"#00E676"})
            }
            6 => {
                json!({"type":"effect","id":id,"shader":"marco_respira","rect_mm":[x,y,45,26],"params":{"color":"#FF4081"}})
            }
            _ => {
                json!({"type":"effect","id":id,"shader":"halo_glow","polygon_mm":[[x+20.0,y],[x+40.0,y+12.0],[x+20.0,y+25.0],[x,y+12.0]],"params":{"margin_mm":6}})
            }
        };
        widgets.push(w);
    }
    serde_json::from_value(json!({
        "type":"scene","rev":rev,"surface":{"width_mm":800,"height_mm":500},
        "transforms":[{"from_frame":"table_mm","to_frame":"proj:main","kind":"homography",
                       "matrix":[2.2,0.08,60,0.02,2.1,20,0.00002,0.0001,1]}],
        "widgets":widgets
    }))
    .expect("escena sintética")
}

#[derive(Debug, Clone)]
pub struct BenchResult {
    pub widgets: usize,
    pub frames: usize,
    pub p50_ms: f64,
    pub p95_ms: f64,
    pub mean_ms: f64,
}

/// Dibuja `frames` cuadros (escena nueva en cada uno, como si llegara del core) a 1920×1080
/// y mide el tiempo de cuadro completo: aplicar + vello + efectos + composición + esperar a la GPU.
pub fn run(engine: &mut Engine, n: usize, frames: usize) -> BenchResult {
    let target = engine.create_target(1920, 1080);
    let view = target.create_view(&Default::default());
    let mut rt = Runtime::offline();
    let mut ms = vec![];
    for f in 0..frames + 10 {
        let t = f as f64 / 60.0;
        let t0 = Instant::now();
        rt.apply(synthetic_scene(n, f as i64 + 1, t));
        engine.render(&rt.state, t, rt.now_s(), &view, 1920, 1080);
        engine.wait_idle();
        if f >= 10 {
            ms.push(t0.elapsed().as_secs_f64() * 1000.0);
        }
    }
    let mean = ms.iter().sum::<f64>() / ms.len() as f64;
    ms.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let pct = |p: f64| ms[((ms.len() - 1) as f64 * p).round() as usize];
    BenchResult { widgets: n, frames, p50_ms: pct(0.5), p95_ms: pct(0.95), mean_ms: mean }
}
