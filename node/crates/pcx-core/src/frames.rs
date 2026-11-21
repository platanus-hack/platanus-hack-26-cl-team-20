//! Fuente de cuadros para `look`.
//!
//! `FrameSource` es la interfaz que implementa la captura (memoria compartida). Aquí va una implementación
//! sintética que dibuja el estado del mundo (no abre cámaras): mesa, grilla cada 50 mm, objetos, luz proyectada
//! y toques. Sin texto (sin fuentes del sistema): los ids van en el resumen de texto de `look`.

use std::sync::Arc;

use pcx_contracts::{PointMM, Widget};
use tokio::sync::watch;

use crate::geom;
use crate::proto::{now_ns, WorldState};

#[derive(Debug, Clone)]
pub struct Frame {
    pub camera_id: String,
    /// Imagen codificada (PNG o JPEG).
    pub data: Vec<u8>,
    pub mime_type: String,
    pub w: u32,
    pub h: u32,
    pub t_capture_ns: i64,
    pub annotated: bool,
}

/// Interfaz para `look`: último cuadro de una cámara (`None` = la cenital principal). Debe ser rápida (no bloquear
/// más de unos ms): la captura la implementa leyendo el último cuadro de memoria compartida.
pub trait FrameSource: Send + Sync {
    fn cameras(&self) -> Vec<String>;
    fn latest(&self, camera_id: Option<&str>, annotate: bool) -> Option<Frame>;
}

pub struct SyntheticFrameSource {
    pub camera_id: String,
    state: watch::Receiver<Arc<WorldState>>,
}

impl SyntheticFrameSource {
    pub fn new(state: watch::Receiver<Arc<WorldState>>) -> Self {
        SyntheticFrameSource { camera_id: "sim".into(), state }
    }
}

impl FrameSource for SyntheticFrameSource {
    fn cameras(&self) -> Vec<String> {
        vec![self.camera_id.clone()]
    }

    fn latest(&self, camera_id: Option<&str>, annotate: bool) -> Option<Frame> {
        if camera_id.is_some_and(|c| c != self.camera_id) {
            return None;
        }
        let ws = self.state.borrow().clone();
        let (png, w, h) = render_png(&ws, annotate);
        Some(Frame { camera_id: self.camera_id.clone(), data: png, mime_type: "image/png".into(), w, h, t_capture_ns: now_ns(), annotated: annotate })
    }
}

const PX_PER_MM: f64 = 1.25;
const MARGIN: f64 = 30.0;
const OBJ_COLORS: [[u8; 3]; 7] = [[0xC0, 0x39, 0x2B], [0x2E, 0x86, 0xC1], [0x28, 0xB4, 0x63], [0xAF, 0x7A, 0xC5], [0xF3, 0x9C, 0x12], [0x16, 0xA0, 0x85], [0xD3, 0x54, 0x00]];

struct Canvas {
    w: u32,
    h: u32,
    px: Vec<u8>,
}

impl Canvas {
    fn blend(&mut self, x: i64, y: i64, c: [u8; 3], a: f64) {
        if x < 0 || y < 0 || x >= self.w as i64 || y >= self.h as i64 {
            return;
        }
        let i = ((y as u32 * self.w + x as u32) * 3) as usize;
        for (dst, src) in self.px[i..i + 3].iter_mut().zip(c) {
            *dst = (*dst as f64 * (1.0 - a) + src as f64 * a) as u8;
        }
    }
    fn rect(&mut self, x0: f64, y0: f64, x1: f64, y1: f64, c: [u8; 3], a: f64) {
        for y in y0.round() as i64..y1.round() as i64 {
            for x in x0.round() as i64..x1.round() as i64 {
                self.blend(x, y, c, a);
            }
        }
    }
    fn line(&mut self, a: (f64, f64), b: (f64, f64), width: f64, c: [u8; 3]) {
        let n = ((b.0 - a.0).abs().max((b.1 - a.1).abs())).ceil().max(1.0) as i64;
        let r = (width / 2.0).max(0.5);
        for i in 0..=n {
            let t = i as f64 / n as f64;
            let (x, y) = (a.0 + (b.0 - a.0) * t, a.1 + (b.1 - a.1) * t);
            self.rect(x - r, y - r, x + r, y + r, c, 1.0);
        }
    }
    fn poly(&mut self, pts: &[(f64, f64)], c: [u8; 3], a: f64) {
        let pm: Vec<PointMM> = pts.iter().map(|p| PointMM(p.0, p.1)).collect();
        let (x0, x1) = pts.iter().fold((f64::MAX, f64::MIN), |(l, h), p| (l.min(p.0), h.max(p.0)));
        let (y0, y1) = pts.iter().fold((f64::MAX, f64::MIN), |(l, h), p| (l.min(p.1), h.max(p.1)));
        for y in y0 as i64..=y1 as i64 {
            for x in x0 as i64..=x1 as i64 {
                if geom::point_in_poly(x as f64 + 0.5, y as f64 + 0.5, &pm) {
                    self.blend(x, y, c, a);
                }
            }
        }
    }
    fn outline(&mut self, pts: &[(f64, f64)], width: f64, c: [u8; 3]) {
        for i in 0..pts.len() {
            self.line(pts[i], pts[(i + 1) % pts.len()], width, c);
        }
    }
}

fn hex(c: &str) -> [u8; 3] {
    let c = c.trim_start_matches('#');
    let p = |i: usize| u8::from_str_radix(c.get(i..i + 2).unwrap_or("ff"), 16).unwrap_or(255);
    [p(0), p(2), p(4)]
}

fn p(x: f64, y: f64) -> (f64, f64) {
    (MARGIN + x * PX_PER_MM, MARGIN + y * PX_PER_MM)
}

/// PNG cenital sintético del mundo. Devuelve (bytes, ancho, alto).
pub fn render_png(ws: &WorldState, annotate: bool) -> (Vec<u8>, u32, u32) {
    let (sw, sh) = (ws.surface.width_mm, ws.surface.height_mm);
    let (w, h) = ((sw * PX_PER_MM + MARGIN * 2.0) as u32, (sh * PX_PER_MM + MARGIN * 2.0) as u32);
    let mut cv = Canvas { w, h, px: [25u8, 25, 28].repeat((w * h) as usize) };
    let (a, b) = (p(0.0, 0.0), p(sw, sh));
    cv.rect(a.0, a.1, b.0, b.1, [122, 92, 64], 1.0);
    if annotate {
        let mut x = 0.0;
        while x <= sw {
            let c = if (x as i64) % 100 == 0 { [160, 130, 95] } else { [140, 110, 80] };
            cv.line(p(x, 0.0), p(x, sh), 1.0, c);
            x += 50.0;
        }
        let mut y = 0.0;
        while y <= sh {
            let c = if (y as i64) % 100 == 0 { [160, 130, 95] } else { [140, 110, 80] };
            cv.line(p(0.0, y), p(sw, y), 1.0, c);
            y += 50.0;
        }
    }
    for (i, o) in ws.objects.iter().enumerate() {
        let pts: Vec<(f64, f64)> = o.polygon_mm.iter().map(|q| p(q.0, q.1)).collect();
        cv.poly(&pts, OBJ_COLORS[i % OBJ_COLORS.len()], 1.0);
        cv.outline(&pts, 1.5, [20, 20, 20]);
    }
    for wd in &ws.widgets {
        match wd {
            Widget::Halo(x) => {
                if let Some(poly) = &x.polygon_mm {
                    let pts: Vec<(f64, f64)> = poly.iter().map(|q| p(q.0, q.1)).collect();
                    cv.outline(&pts, (x.width_mm * PX_PER_MM).max(2.0), hex(&x.color));
                }
            }
            Widget::Arrow(x) => {
                if let (Some(f), Some(t)) = (x.from_mm, x.to_mm) {
                    cv.line(p(f.0, f.1), p(t.0, t.1), (x.width_mm * PX_PER_MM).max(2.0), hex(&x.color));
                    let tp = p(t.0, t.1);
                    cv.rect(tp.0 - 6.0, tp.1 - 6.0, tp.0 + 6.0, tp.1 + 6.0, hex(&x.color), 1.0);
                }
            }
            Widget::Label(x) => {
                if let Some(pt) = x.common.anchor.as_ref().and_then(|a| a.point_mm) {
                    let hw = x.text.chars().count() as f64 * x.size_mm * 0.3 * PX_PER_MM;
                    let hh = x.size_mm * 0.4 * PX_PER_MM;
                    let c = p(pt.0, pt.1);
                    cv.rect(c.0 - hw, c.1 - hh, c.0 + hw, c.1 + hh, hex(&x.color), 0.5);
                }
            }
            other => {
                if let Some(r) = geom::rect_of(other) {
                    let (a, b) = (p(r.0, r.1), p(r.0 + r.2, r.1 + r.3));
                    let col = match other {
                        Widget::Button(x) => hex(&x.color),
                        Widget::Panel(x) => hex(&x.color),
                        _ => [255, 255, 255],
                    };
                    cv.rect(a.0, a.1, b.0, b.1, if matches!(other, Widget::Button(_)) { col } else { [0, 0, 0] }, 0.4);
                    cv.outline(&[a, (b.0, a.1), b, (a.0, b.1)], 3.0, col);
                    if let Widget::Button(x) = other {
                        cv.rect(a.0, b.1 - 6.0, a.0 + (b.0 - a.0) * x.progress, b.1, [255, 255, 255], 1.0);
                    }
                }
            }
        }
    }
    for t in &ws.touches {
        let c = p(t.point_mm.0, t.point_mm.1);
        cv.outline(&[(c.0 - 7.0, c.1 - 7.0), (c.0 + 7.0, c.1 - 7.0), (c.0 + 7.0, c.1 + 7.0), (c.0 - 7.0, c.1 + 7.0)], 3.0, [0xFF, 0x2D, 0x95]);
    }
    let mut out = Vec::new();
    {
        let mut enc = png::Encoder::new(&mut out, w, h);
        enc.set_color(png::ColorType::Rgb);
        enc.set_depth(png::BitDepth::Eight);
        if let Ok(mut wr) = enc.write_header() {
            let _ = wr.write_image_data(&cv.px);
        }
    }
    (out, w, h)
}

pub fn summary_text(ws: &WorldState, cursor: i64) -> String {
    let s = &ws.surface;
    let mut lines = vec![format!(
        "Mesa {:.0}×{:.0} mm (origen arriba a la izquierda, x→, y↓). rev={} cursor={cursor}",
        s.width_mm, s.height_mm, ws.rev
    )];
    if ws.objects.is_empty() {
        lines.push("Objetos: ninguno".into());
    } else {
        lines.push("Objetos (en la imagen, colores en este orden: rojo, azul, verde, violeta, naranja…):".into());
        for o in &ws.objects {
            lines.push(format!("- {} ({}) centro=({:.0}, {:.0}) mm conf={:.2}", o.id, o.label, o.center_mm.0, o.center_mm.1, o.confidence));
        }
    }
    if !ws.widgets.is_empty() {
        let ws: Vec<String> = ws.widgets.iter().map(|w| format!("{} ({})", w.id(), geom::kind(w))).collect();
        lines.push(format!("Widgets proyectados: {}", ws.join(", ")));
    }
    lines.join("\n")
}
