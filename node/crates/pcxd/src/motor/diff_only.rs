//! `DiffOnly`: `Perceptor` de respaldo, sin modelos. Detecta regiones nuevas por diferencia contra la foto limpia
//! anterior y las publica como `objeto-N`.
//!
//! La referencia es la foto limpia anterior, salvo bajo los objetos vigentes, donde conserva la mesa como se veía
//! antes de que llegaran: así un objeto quieto sigue contando y, cuando se va, la región vuelve a calzar y se
//! publica su `remove`. Trabaja a ~320 px de ancho y solo dentro de la superficie calibrada.
//!
//! Límites (es el respaldo, no la cascada): no reconoce ni nombra; un objeto que ya estaba en la primera foto es
//! parte del fondo, y si se retira deja una región «nueva» hasta el próximo `reset`; un cambio de luz grande rehace
//! la referencia en vez de inventar objetos.

use std::time::Instant;

use image::RgbImage;
use pcx_perception::pipeline_api::{CalibIn, ObjectOut, PerceptionOut, Perceptor};

use super::geom;

#[derive(Debug, Clone)]
pub struct DiffParams {
    /// Ancho de trabajo aproximado (px).
    pub work_width: u32,
    /// Diferencia mínima por canal (niveles) para contar un píxel como cambiado.
    pub threshold: u8,
    /// Área mínima de una región, como fracción del cuadro de trabajo.
    pub min_area_frac: f64,
    /// Si cambia más que esta fracción de la superficie, fue la luz: se rehace la referencia.
    pub max_changed_frac: f64,
    /// Solapamiento (IoU) mínimo para considerar que una región es el mismo objeto de antes.
    pub min_iou: f64,
}

impl Default for DiffParams {
    fn default() -> Self {
        DiffParams { work_width: 320, threshold: 26, min_area_frac: 0.0004, max_changed_frac: 0.45, min_iou: 0.3 }
    }
}

struct Tracked {
    id: String,
    /// Índices de píxel (cuadro de trabajo).
    px: Vec<u32>,
    centroid: [f64; 2],
}

struct Model {
    cam: (u32, u32),
    scale: u32,
    w: usize,
    /// Referencia RGB a resolución de trabajo.
    bg: Vec<u8>,
    /// Píxeles dentro de la superficie calibrada.
    surface: Vec<bool>,
    calib_key: Vec<u64>,
    objs: Vec<Tracked>,
}

#[derive(Default)]
pub struct DiffOnly {
    p: DiffParams,
    model: Option<Model>,
    next: u32,
}

impl DiffOnly {
    pub fn new(p: DiffParams) -> Self {
        DiffOnly { p, model: None, next: 0 }
    }
}

fn calib_key(c: &CalibIn) -> Vec<u64> {
    let mut k: Vec<u64> = c.matrix_px_to_mm.iter().map(|v| v.to_bits()).collect();
    k.extend([c.surface_mm.0.to_bits(), c.surface_mm.1.to_bits(), c.camera_px.0 as u64, c.camera_px.1 as u64]);
    if let Some(l) = &c.lens {
        k.extend([l.f, l.cx, l.cy, l.k1, l.k2].map(f64::to_bits));
    }
    k
}

/// Promedio por bloques de `s × s`.
fn downscale(img: &RgbImage, s: u32) -> (usize, usize, Vec<u8>) {
    let (w, h) = ((img.width() / s).max(1) as usize, (img.height() / s).max(1) as usize);
    let (s, stride, raw) = (s as usize, img.width() as usize * 3, img.as_raw());
    let mut out = vec![0u8; w * h * 3];
    let (bw, bh) = (s.min(img.width() as usize), s.min(img.height() as usize));
    for y in 0..h {
        for x in 0..w {
            let mut acc = [0u32; 3];
            for dy in 0..bh {
                let row = (y * s + dy) * stride + x * s * 3;
                for px in raw[row..row + bw * 3].as_chunks::<3>().0 {
                    acc[0] += px[0] as u32;
                    acc[1] += px[1] as u32;
                    acc[2] += px[2] as u32;
                }
            }
            let n = (bw * bh) as u32;
            let o = (y * w + x) * 3;
            out[o..o + 3].copy_from_slice(&acc.map(|v| (v / n) as u8));
        }
    }
    (w, h, out)
}

/// Erosión (`grow = false`) o dilatación 3×3.
fn morph(m: &[bool], w: usize, h: usize, grow: bool) -> Vec<bool> {
    let mut out = vec![false; m.len()];
    for y in 0..h {
        for x in 0..w {
            let mut all = true;
            let mut any = false;
            for dy in -1i32..=1 {
                for dx in -1i32..=1 {
                    let (nx, ny) = (x as i32 + dx, y as i32 + dy);
                    let v = nx >= 0
                        && ny >= 0
                        && (nx as usize) < w
                        && (ny as usize) < h
                        && m[ny as usize * w + nx as usize];
                    all &= v;
                    any |= v;
                }
            }
            out[y * w + x] = if grow { any } else { all };
        }
    }
    out
}

/// Componentes conexas (8 vecinos) como listas de índices de píxel.
fn components(m: &[bool], w: usize, h: usize, min_area: usize) -> Vec<Vec<u32>> {
    let mut seen = vec![false; m.len()];
    let mut out = Vec::new();
    let mut stack = Vec::new();
    for start in 0..m.len() {
        if !m[start] || seen[start] {
            continue;
        }
        let mut comp = Vec::new();
        seen[start] = true;
        stack.push(start);
        while let Some(i) = stack.pop() {
            comp.push(i as u32);
            let (x, y) = ((i % w) as i32, (i / w) as i32);
            for dy in -1..=1 {
                for dx in -1..=1 {
                    let (nx, ny) = (x + dx, y + dy);
                    if nx < 0 || ny < 0 || nx as usize >= w || ny as usize >= h {
                        continue;
                    }
                    let j = ny as usize * w + nx as usize;
                    if m[j] && !seen[j] {
                        seen[j] = true;
                        stack.push(j);
                    }
                }
            }
        }
        if comp.len() >= min_area {
            comp.sort_unstable();
            out.push(comp);
        }
    }
    out
}

fn centroid(px: &[u32], w: usize) -> [f64; 2] {
    let (mut sx, mut sy) = (0.0, 0.0);
    for &i in px {
        sx += (i as usize % w) as f64 + 0.5;
        sy += (i as usize / w) as f64 + 0.5;
    }
    [sx / px.len() as f64, sy / px.len() as f64]
}

/// Casco convexo de la región en px de trabajo (esquinas de los extremos de cada fila).
fn hull(px: &[u32], w: usize) -> Vec<[f64; 2]> {
    let mut rows: std::collections::BTreeMap<usize, (usize, usize)> = Default::default();
    for &i in px {
        let (x, y) = (i as usize % w, i as usize / w);
        let e = rows.entry(y).or_insert((x, x));
        *e = (e.0.min(x), e.1.max(x));
    }
    let mut pts = Vec::with_capacity(rows.len() * 4);
    for (y, (x0, x1)) in rows {
        for (x, y) in [(x0, y), (x1 + 1, y), (x0, y + 1), (x1 + 1, y + 1)] {
            pts.push([x as f64, y as f64]);
        }
    }
    geom::convex_hull(pts)
}

fn overlap(a: &[u32], b: &[u32]) -> usize {
    let (mut i, mut j, mut n) = (0, 0, 0);
    while i < a.len() && j < b.len() {
        match a[i].cmp(&b[j]) {
            std::cmp::Ordering::Less => i += 1,
            std::cmp::Ordering::Greater => j += 1,
            std::cmp::Ordering::Equal => {
                n += 1;
                i += 1;
                j += 1;
            }
        }
    }
    n
}

impl Model {
    fn new(calib: &CalibIn, w: usize, h: usize, scale: u32, bg: Vec<u8>) -> Model {
        let s = scale as f64;
        let (sw, sh) = calib.surface_mm;
        let surface = (0..w * h)
            .map(|i| {
                let p = [((i % w) as f64 + 0.5) * s, ((i / w) as f64 + 0.5) * s];
                geom::px_to_mm(calib, p).is_some_and(|m| m[0] >= 0.0 && m[0] <= sw && m[1] >= 0.0 && m[1] <= sh)
            })
            .collect();
        Model { cam: calib.camera_px, scale, w, bg, surface, calib_key: calib_key(calib), objs: Vec::new() }
    }

    fn object(&self, t: &Tracked, calib: &CalibIn, confidence: f32) -> Option<ObjectOut> {
        let s = self.scale as f64;
        let to_mm = |p: [f64; 2]| geom::px_to_mm(calib, [p[0] * s, p[1] * s]);
        let polygon_mm: Option<Vec<[f64; 2]>> =
            geom::decimate(hull(&t.px, self.w), 64).into_iter().map(to_mm).collect();
        let polygon_mm = polygon_mm.filter(|p| p.len() >= 3)?;
        Some(ObjectOut { id: t.id.clone(), label: t.id.clone(), polygon_mm, center_mm: to_mm(t.centroid)?, confidence })
    }
}

impl Perceptor for DiffOnly {
    fn on_clean(&mut self, frame: &RgbImage, calib: &CalibIn, _t_ns: u64) -> Result<PerceptionOut, String> {
        let dims = frame.dimensions();
        if dims != calib.camera_px {
            return Err(format!(
                "la foto es de {}x{} y la calibración de {}x{}",
                dims.0, dims.1, calib.camera_px.0, calib.camera_px.1
            ));
        }
        let mut out = PerceptionOut::default();
        let t0 = Instant::now();
        let scale = dims.0.div_ceil(self.p.work_width.max(16)).max(1);
        let (w, h, cur) = downscale(frame, scale);

        // Primera foto, o cámara de otro tamaño: es la referencia.
        if self.model.as_ref().is_none_or(|m| m.cam != dims) {
            if let Some(old) = self.model.take() {
                out.remove = old.objs.into_iter().map(|o| o.id).collect();
            }
            self.model = Some(Model::new(calib, w, h, scale, cur));
            out.stages_ms.push(("n0_referencia".into(), t0.elapsed().as_secs_f64() * 1e3));
            return Ok(out);
        }
        let m = self.model.as_mut().expect("modelo presente");
        let recalib = m.calib_key != calib_key(calib);
        if recalib {
            let fresh = Model::new(calib, w, h, scale, Vec::new());
            (m.surface, m.calib_key) = (fresh.surface, fresh.calib_key);
        }

        // Ganancia global (autoexposición, luz ambiente): se iguala la luminancia media fuera de los objetos.
        let mut occupied = vec![false; w * h];
        for o in &m.objs {
            o.px.iter().for_each(|&i| occupied[i as usize] = true);
        }
        let (mut sum_c, mut sum_b) = (0u64, 0u64);
        for i in (0..w * h).filter(|&i| m.surface[i] && !occupied[i]) {
            sum_c += cur[i * 3..i * 3 + 3].iter().map(|&v| v as u64).sum::<u64>();
            sum_b += m.bg[i * 3..i * 3 + 3].iter().map(|&v| v as u64).sum::<u64>();
        }
        let gain = if sum_c > 0 { (sum_b as f64 / sum_c as f64).clamp(0.6, 1.6) } else { 1.0 };
        let th = self.p.threshold as f64;
        let mut diff = vec![0f32; w * h];
        let mut mask = vec![false; w * h];
        for i in (0..w * h).filter(|&i| m.surface[i]) {
            let d = (0..3).map(|c| (cur[i * 3 + c] as f64 * gain - m.bg[i * 3 + c] as f64).abs()).fold(0.0, f64::max);
            diff[i] = d as f32;
            mask[i] = d > th;
        }
        // Abrir (quita motas) y cerrar (tapa huecos).
        let mask = morph(&morph(&mask, w, h, false), w, h, true);
        let mask = morph(&morph(&mask, w, h, true), w, h, false);
        let surface_px = m.surface.iter().filter(|&&s| s).count().max(1);
        let changed = mask.iter().filter(|&&v| v).count();
        out.stages_ms.push(("n0_diferencia".into(), t0.elapsed().as_secs_f64() * 1e3));
        if changed as f64 > self.p.max_changed_frac * surface_px as f64 {
            // Cambió la luz, no la mesa: nueva referencia; los objetos vigentes se conservan como están.
            m.bg = cur;
            out.stages_ms.push(("n0_nueva_referencia".into(), 0.0));
            return Ok(out);
        }

        let t1 = Instant::now();
        let min_area = ((self.p.min_area_frac * (w * h) as f64).ceil() as usize).max(9);
        let comps = components(&mask, w, h, min_area);
        // Empareja regiones con los objetos de antes por solapamiento, de mayor a menor.
        let mut pairs: Vec<(f64, usize, usize)> = Vec::new();
        for (ci, c) in comps.iter().enumerate() {
            for (oi, o) in m.objs.iter().enumerate() {
                let inter = overlap(c, &o.px);
                let iou = inter as f64 / (c.len() + o.px.len() - inter).max(1) as f64;
                if iou >= self.p.min_iou {
                    pairs.push((iou, ci, oi));
                }
            }
        }
        pairs.sort_by(|a, b| b.0.total_cmp(&a.0));
        let mut comp_obj: Vec<Option<usize>> = vec![None; comps.len()];
        let mut obj_used = vec![false; m.objs.len()];
        for (_, ci, oi) in pairs {
            if comp_obj[ci].is_none() && !obj_used[oi] {
                comp_obj[ci] = Some(oi);
                obj_used[oi] = true;
            }
        }
        let mut old: Vec<Option<Tracked>> = std::mem::take(&mut m.objs).into_iter().map(Some).collect();
        let mut kept = Vec::with_capacity(comps.len());
        for (c, matched) in comps.into_iter().zip(comp_obj) {
            let cen = centroid(&c, w);
            let mean = c.iter().map(|&i| diff[i as usize] as f64).sum::<f64>() / c.len() as f64;
            let confidence = (0.5 + (mean - th) / 200.0).clamp(0.5, 0.95) as f32;
            let (t, changed) = match matched.and_then(|oi| old[oi].take()) {
                Some(prev) => {
                    let moved = (prev.centroid[0] - cen[0]).hypot(prev.centroid[1] - cen[1]) > 1.5;
                    let resized = (prev.px.len() as f64 - c.len() as f64).abs() > 0.2 * prev.px.len() as f64;
                    (Tracked { id: prev.id, px: c, centroid: cen }, moved || resized || recalib)
                }
                None => {
                    self.next += 1;
                    (Tracked { id: format!("objeto-{}", self.next), px: c, centroid: cen }, true)
                }
            };
            if changed {
                match m.object(&t, calib, confidence) {
                    Some(o) => out.upsert.push(o),
                    None => continue,
                }
            }
            kept.push(t);
        }
        out.remove.extend(old.into_iter().flatten().map(|o| o.id));
        m.objs = kept;
        // La referencia sigue a la foto en todo lo que no es objeto (con un margen de 1 px alrededor).
        let mut keep = vec![false; w * h];
        for o in &m.objs {
            o.px.iter().for_each(|&i| keep[i as usize] = true);
        }
        let keep = morph(&keep, w, h, true);
        for i in (0..w * h).filter(|&i| !keep[i]) {
            m.bg[i * 3..i * 3 + 3].copy_from_slice(&cur[i * 3..i * 3 + 3]);
        }
        out.stages_ms.push(("n0_regiones".into(), t1.elapsed().as_secs_f64() * 1e3));
        Ok(out)
    }

    fn reset(&mut self) {
        self.model = None;
        self.next = 0;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const W: u32 = 640;
    const H: u32 = 360;

    /// 0,5 mm por px: la superficie de 320×180 mm cubre el cuadro entero.
    fn calib() -> CalibIn {
        CalibIn {
            camera_px: (W, H),
            matrix_px_to_mm: [0.5, 0.0, 0.0, 0.0, 0.5, 0.0, 0.0, 0.0, 1.0],
            lens: None,
            surface_mm: (320.0, 180.0),
        }
    }

    fn table(objs: &[[u32; 4]], gain: f32) -> RgbImage {
        RgbImage::from_fn(W, H, |x, y| {
            let mut v = [60.0 + 40.0 * x as f32 / W as f32 + ((x / 2 + y / 2) % 5) as f32; 3];
            if objs.iter().any(|r| x >= r[0] && x < r[2] && y >= r[1] && y < r[3]) {
                v = [190.0, 70.0, 50.0];
            }
            image::Rgb(v.map(|c| (c * gain).clamp(0.0, 255.0) as u8))
        })
    }

    #[test]
    fn aparece_se_queda_se_mueve_y_se_va() {
        let (mut d, c) = (DiffOnly::default(), calib());
        let o = d.on_clean(&table(&[], 1.0), &c, 0).unwrap();
        assert!(o.upsert.is_empty() && o.remove.is_empty(), "la primera foto es la referencia");

        let rect = [200, 100, 280, 160];
        let o = d.on_clean(&table(&[rect], 1.0), &c, 1).unwrap();
        assert_eq!(o.upsert.len(), 1, "{o:?}");
        let obj = &o.upsert[0];
        assert_eq!((obj.id.as_str(), obj.label.as_str()), ("objeto-1", "objeto-1"));
        // centro en mm = centro en px × 0,5
        assert!((obj.center_mm[0] - 120.0).abs() < 2.0 && (obj.center_mm[1] - 65.0).abs() < 2.0, "{:?}", obj.center_mm);
        assert!((3..=64).contains(&obj.polygon_mm.len()));
        let (xs, ys): (Vec<f64>, Vec<f64>) = obj.polygon_mm.iter().map(|p| (p[0], p[1])).unzip();
        let (x0, x1) = (xs.iter().cloned().fold(f64::MAX, f64::min), xs.iter().cloned().fold(f64::MIN, f64::max));
        let (y0, y1) = (ys.iter().cloned().fold(f64::MAX, f64::min), ys.iter().cloned().fold(f64::MIN, f64::max));
        assert!(
            (x0 - 100.0).abs() < 3.0 && (x1 - 140.0).abs() < 3.0 && (y0 - 50.0).abs() < 3.0 && (y1 - 80.0).abs() < 3.0
        );
        assert!(o.stages_ms.iter().any(|(k, _)| k == "n0_diferencia"));

        // quieto, y con la luz un 10 % más fuerte: nada que publicar
        let o = d.on_clean(&table(&[rect], 1.1), &c, 2).unwrap();
        assert!(o.upsert.is_empty() && o.remove.is_empty(), "{o:?}");

        // se corre un poco: mismo id, polígono nuevo
        let o = d.on_clean(&table(&[[220, 110, 300, 170]], 1.0), &c, 3).unwrap();
        assert_eq!(o.upsert.iter().map(|u| u.id.as_str()).collect::<Vec<_>>(), ["objeto-1"]);
        assert!(o.remove.is_empty());

        // llega otro lejos; después se va el primero
        let o = d.on_clean(&table(&[[220, 110, 300, 170], [450, 200, 520, 280]], 1.0), &c, 4).unwrap();
        assert_eq!(o.upsert.iter().map(|u| u.id.as_str()).collect::<Vec<_>>(), ["objeto-2"]);
        let o = d.on_clean(&table(&[[450, 200, 520, 280]], 1.0), &c, 5).unwrap();
        assert_eq!((o.upsert.len(), o.remove.clone()), (0, vec!["objeto-1".to_string()]));

        d.reset();
        let o = d.on_clean(&table(&[[450, 200, 520, 280]], 1.0), &c, 6).unwrap();
        assert!(o.upsert.is_empty() && o.remove.is_empty(), "tras reset, lo que hay es la referencia");
        let o = d.on_clean(&table(&[[450, 200, 520, 280], [60, 60, 120, 120]], 1.0), &c, 7).unwrap();
        assert_eq!(o.upsert[0].id, "objeto-1", "la numeración recomienza");
    }

    #[test]
    fn cambio_de_luz_no_inventa_objetos_y_fuera_de_la_superficie_no_cuenta() {
        let (mut d, c) = (DiffOnly::default(), calib());
        d.on_clean(&table(&[], 1.0), &c, 0).unwrap();
        let oscuro = RgbImage::from_fn(W, H, |x, _| image::Rgb(if x < W / 2 { [5, 5, 5] } else { [250, 250, 250] }));
        let o = d.on_clean(&oscuro, &c, 1).unwrap();
        assert!(o.upsert.is_empty() && o.remove.is_empty());
        assert!(o.stages_ms.iter().any(|(k, _)| k == "n0_nueva_referencia"));

        // superficie = mitad izquierda: un objeto a la derecha queda fuera
        let media = CalibIn { surface_mm: (160.0, 180.0), ..calib() };
        let mut d = DiffOnly::default();
        d.on_clean(&table(&[], 1.0), &media, 0).unwrap();
        let o = d.on_clean(&table(&[[450, 200, 520, 280]], 1.0), &media, 1).unwrap();
        assert!(o.upsert.is_empty(), "{o:?}");
        assert!(d.on_clean(&RgbImage::new(320, 180), &media, 2).is_err(), "tamaño distinto al de la calibración");
    }
}
