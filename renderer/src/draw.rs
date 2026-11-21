//! Widgets vectoriales con vello (en px de la superficie = mm × ppmm). Texto con parley
//! (shaping, fallback y ajuste de línea; layouts cacheados). Los `effect` van por `effects.rs`.

use std::collections::HashMap;
use std::f64::consts::TAU;
use std::path::Path;

use parley::{FontContext, FontFamily, FontWeight, Layout, LayoutContext, PositionedLayoutItem, StyleProperty};
use pcx_contracts::{PointMM, Widget};
use vello::Scene;
use vello::kurbo::{Affine, BezPath, Cap, Join, Line, Point, Rect, RoundedRect, Stroke, Vec2};
use vello::peniko::{Blob, Color, Fill, ImageBrush};

use crate::images::ImageCache;

#[derive(Hash, PartialEq, Eq, Clone)]
struct Key {
    text: String,
    size_bits: u32,
    bold: bool,
    max_w_bits: Option<u32>,
}

pub struct Painter {
    font_cx: FontContext,
    layout_cx: LayoutContext<()>,
    cache: HashMap<Key, (Layout<()>, u64)>,
    frame: u64,
    /// Pila de familias: las registradas desde `fonts/` primero y después las del sistema.
    stack: String,
}

impl Default for Painter {
    fn default() -> Self {
        Self::new(None)
    }
}

pub fn parse_color(s: &str) -> Color {
    let h = s.trim_start_matches('#');
    let p = |i: usize| u8::from_str_radix(h.get(i..i + 2).unwrap_or("ff"), 16).unwrap_or(255);
    if h.len() >= 8 {
        Color::from_rgba8(p(0), p(2), p(4), p(6))
    } else if h.len() >= 6 {
        Color::from_rgba8(p(0), p(2), p(4), 255)
    } else {
        Color::WHITE
    }
}

/// Pila explícita por SO: el fallback automático de fontique no siempre encuentra ✓ (U+2713) ni
/// emoji (ver spike). Las familias que no existen en el SO se ignoran.
#[cfg(target_os = "macos")]
const SYSTEM_FONTS: &str = "'Helvetica Neue', 'Apple Color Emoji', 'Lucida Grande', 'Zapf Dingbats', 'Apple Symbols'";
#[cfg(target_os = "windows")]
const SYSTEM_FONTS: &str = "'Segoe UI', 'Segoe UI Emoji', 'Segoe UI Symbol', 'Arial Unicode MS'";
#[cfg(not(any(target_os = "macos", target_os = "windows")))]
const SYSTEM_FONTS: &str = "'Noto Sans', 'DejaVu Sans', 'Noto Color Emoji', 'Noto Sans Symbols 2', 'Noto Sans Symbols', 'Symbola'";

impl Painter {
    /// `fonts_dir`: `.ttf/.otf/.ttc` que se registran y van primero en la pila (fuentes propias del
    /// repo para no depender del SO). Sin directorio, solo fuentes del sistema.
    pub fn new(fonts_dir: Option<&Path>) -> Self {
        let mut font_cx = FontContext::new();
        let mut own: Vec<String> = vec![];
        if let Some(rd) = fonts_dir.and_then(|d| std::fs::read_dir(d).ok()) {
            let mut files: Vec<_> = rd.flatten().map(|e| e.path()).collect();
            files.sort();
            for f in files {
                let ext = f.extension().and_then(|x| x.to_str()).unwrap_or("").to_ascii_lowercase();
                if !matches!(ext.as_str(), "ttf" | "otf" | "ttc") {
                    continue;
                }
                let Ok(bytes) = std::fs::read(&f) else { continue };
                for (fam, _) in font_cx.collection.register_fonts(Blob::from(bytes), None) {
                    if let Some(name) = font_cx.collection.family_name(fam).map(str::to_owned)
                        && !own.contains(&name)
                    {
                        eprintln!("[fonts] {name} ({})", f.display());
                        own.push(name);
                    }
                }
            }
        }
        let mut stack: Vec<String> = own.iter().map(|n| format!("'{n}'")).collect();
        stack.push(SYSTEM_FONTS.into());
        stack.push("sans-serif".into());
        Self { font_cx, layout_cx: LayoutContext::new(), cache: HashMap::new(), frame: 0, stack: stack.join(", ") }
    }

    fn layout(&mut self, text: &str, size_px: f32, bold: bool, max_w: Option<f32>) -> &Layout<()> {
        let key = Key { text: text.to_owned(), size_bits: size_px.to_bits(), bold, max_w_bits: max_w.map(f32::to_bits) };
        let frame = self.frame;
        let e = self.cache.entry(key).or_insert_with(|| {
            let mut b = self.layout_cx.ranged_builder(&mut self.font_cx, text, 1.0, true);
            b.push_default(StyleProperty::FontSize(size_px));
            b.push_default(StyleProperty::FontFamily(FontFamily::Source(self.stack.as_str().into())));
            if bold {
                b.push_default(StyleProperty::FontWeight(FontWeight::BOLD));
            }
            let mut l: Layout<()> = b.build(text);
            l.break_all_lines(max_w);
            (l, frame)
        });
        e.1 = frame;
        &e.0
    }

    /// Avanza un cuadro y desaloja layouts sin uso.
    pub fn begin_frame(&mut self) {
        self.frame += 1;
        if self.frame.is_multiple_of(60) {
            let f = self.frame;
            self.cache.retain(|_, (_, used)| f - *used < 120);
        }
    }

    /// Dibuja un widget (no `effect`) en `scene`. `t` en segundos (animación del halo).
    pub fn draw(&mut self, scene: &mut Scene, w: &Widget, ppmm: f64, t: f64, images: &mut ImageCache) {
        let mm = Affine::scale(ppmm);
        match w {
            Widget::Label(l) => {
                let Some(PointMM(ax, ay)) = l.common.anchor.as_ref().and_then(|a| a.resolved_point()) else { return };
                let pos = Vec2::new(ax * ppmm, ay * ppmm);
                let size_px = (l.size_mm * ppmm) as f32;
                let (lw, lh) = {
                    let lay = self.layout(&l.text, size_px, false, None);
                    (lay.width() as f64, lay.height() as f64)
                };
                if let Some(bg) = &l.bg {
                    let pad = l.size_mm * ppmm * 0.3;
                    let r = RoundedRect::new(-pad, -pad, lw + pad, lh + pad, pad);
                    scene.fill(Fill::NonZero, Affine::translate(pos), parse_color(bg), None, &r);
                }
                let lay = self.layout(&l.text, size_px, false, None);
                draw_layout(scene, lay, Affine::translate(pos), parse_color(&l.color));
            }
            Widget::Halo(h) => {
                let Some(poly) = h.polygon_mm.as_ref().filter(|p| p.len() >= 2) else { return };
                let path = poly_path(poly);
                let pulse = 0.5 + 0.5 * (TAU * h.pulse_hz * t).sin();
                let c = parse_color(&h.color);
                for (k, a) in [(4.0, 0.10), (2.5, 0.20), (1.6, 0.35)] {
                    let st = Stroke::new(h.width_mm * k * (0.8 + 0.4 * pulse)).with_join(Join::Round);
                    scene.stroke(&st, mm, c.multiply_alpha((a * (0.4 + 0.6 * pulse)) as f32), None, &path);
                }
                let st = Stroke::new(h.width_mm).with_join(Join::Round);
                scene.stroke(&st, mm, c.multiply_alpha((0.6 + 0.4 * pulse) as f32), None, &path);
            }
            Widget::Arrow(ar) => {
                let (Some(f), Some(to)) = (ar.from_mm, ar.to_mm) else { return };
                let (a, b) = (Point::new(f.0, f.1), Point::new(to.0, to.1));
                let d = b - a;
                let len = d.hypot();
                if len < 1e-6 {
                    return;
                }
                let u = d / len;
                let n = Vec2::new(-u.y, u.x);
                let head = (ar.width_mm * 4.0).min(len * 0.6);
                let c = parse_color(&ar.color);
                let st = Stroke::new(ar.width_mm).with_caps(Cap::Round);
                scene.stroke(&st, mm, c, None, &Line::new(a, b - u * head * 0.8));
                let mut tri = BezPath::new();
                tri.move_to(b);
                tri.line_to(b - u * head + n * head * 0.55);
                tri.line_to(b - u * head - n * head * 0.55);
                tri.close_path();
                scene.fill(Fill::NonZero, mm, c, None, &tri);
            }
            Widget::Panel(p) => {
                let Some(r) = p.rect_mm else { return };
                let (x, y, w, h) = (r.0, r.1, r.2, r.3);
                let c = parse_color(&p.color);
                let rr = RoundedRect::new(x, y, x + w, y + h, 3.0);
                scene.fill(Fill::NonZero, mm, c.multiply_alpha(0.15), None, &rr);
                scene.stroke(&Stroke::new(0.6), mm, c, None, &rr);
                let pad = 4.0;
                let inner_w = ((w - 2.0 * pad).max(1.0) * ppmm) as f32;
                let origin = Vec2::new((x + pad) * ppmm, (y + pad) * ppmm);
                let th = {
                    let tl = self.layout(&p.title, (7.0 * ppmm) as f32, true, Some(inner_w));
                    draw_layout(scene, tl, Affine::translate(origin), c);
                    tl.height() as f64
                };
                scene.push_clip_layer(Fill::NonZero, mm, &Rect::new(x, y, x + w, y + h - 1.0));
                let bl = self.layout(&p.body, (5.5 * ppmm) as f32, false, Some(inner_w));
                draw_layout(scene, bl, Affine::translate(origin + Vec2::new(0.0, th + 2.0 * ppmm)), Color::WHITE.multiply_alpha(0.92));
                scene.pop_layer();
            }
            Widget::Button(b) => {
                let Some(r) = b.rect_mm else { return };
                let (x, y, w, h) = (r.0, r.1, r.2, r.3);
                let c = parse_color(&b.color);
                let rr = RoundedRect::new(x, y, x + w, y + h, (h * 0.25).min(4.0));
                scene.fill(Fill::NonZero, mm, c.multiply_alpha(0.12), None, &rr);
                let p = b.progress.clamp(0.0, 1.0);
                if p > 0.0 {
                    scene.push_clip_layer(Fill::NonZero, mm, &rr);
                    scene.fill(Fill::NonZero, mm, c.multiply_alpha(0.45), None, &Rect::new(x, y, x + w * p, y + h));
                    scene.pop_layer();
                }
                scene.stroke(&Stroke::new(0.8), mm, c, None, &rr);
                let size = (h * 0.4).min(8.0);
                let lay = self.layout(&b.label, (size * ppmm) as f32, true, None);
                let (lw, lh) = (lay.width() as f64, lay.height() as f64);
                let pos = Vec2::new((x + w / 2.0) * ppmm - lw / 2.0, (y + h / 2.0) * ppmm - lh / 2.0);
                draw_layout(scene, lay, Affine::translate(pos), Color::WHITE);
            }
            Widget::Image(im) => {
                let Some(r) = im.rect_mm else { return };
                let (x, y, w, h) = (r.0, r.1, r.2, r.3);
                match images.get(&im.src) {
                    Some(data) => {
                        let (iw, ih) = (data.width as f64, data.height as f64);
                        let xf = Affine::scale(ppmm)
                            * Affine::translate((x, y))
                            * Affine::scale_non_uniform(w / iw, h / ih);
                        let brush = ImageBrush::new(data.clone()).with_alpha(im.opacity.clamp(0.0, 1.0) as f32);
                        scene.draw_image(&brush, xf);
                    }
                    None => {
                        // Marcador: la imagen no está (aún).
                        let rr = Rect::new(x, y, x + w, y + h);
                        scene.stroke(&Stroke::new(0.6).with_dashes(0.0, [3.0, 2.0]), mm, Color::from_rgba8(255, 80, 80, 200), None, &rr);
                    }
                }
            }
            Widget::Effect(_) => {}
        }
    }
}

fn poly_path(poly: &[PointMM]) -> BezPath {
    let mut path = BezPath::new();
    path.move_to((poly[0].0, poly[0].1));
    for p in &poly[1..] {
        path.line_to((p.0, p.1));
    }
    path.close_path();
    path
}

fn draw_layout(scene: &mut Scene, layout: &Layout<()>, transform: Affine, color: Color) {
    for line in layout.lines() {
        for item in line.items() {
            let PositionedLayoutItem::GlyphRun(gr) = item else { continue };
            let run = gr.run();
            // parley 0.13 usa parlance::NormalizedCoord; vello 0.12 espera i16.
            let coords: Vec<i16> = run.normalized_coords().iter().map(|c| c.to_bits()).collect();
            let glyph_xform = run.synthesis().skew().map(|a| Affine::skew((a as f64).to_radians().tan(), 0.0));
            scene
                .draw_glyphs(run.font())
                .brush(color)
                .transform(transform)
                .glyph_transform(glyph_xform)
                .font_size(run.font_size())
                .normalized_coords(&coords)
                .draw(Fill::NonZero, gr.positioned_glyphs().map(|g| vello::Glyph { id: g.id, x: g.x, y: g.y }));
        }
    }
}
