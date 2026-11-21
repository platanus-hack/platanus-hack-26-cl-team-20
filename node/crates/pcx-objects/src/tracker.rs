//! La cascada (port de `Seguidor.revisar` / `registrar_todo` de `seguidor_v4.py`, versión 02:09 del 21-nov).
//!
//! Nivel 0 diff limpio/limpio · 1 NCC «¿sigue en su caja?» · 2 ORB rígido · 3 DINO(prompt)+SAM(caja) ·
//! 4 LLM desempata · 5 registro global (SAM automático + LLM nombra). Mismos umbrales que el original.

use crate::contract::{ObjectEvent, ObjectState};
use crate::geom::{self, Affine, Canvas, IDENTITY};
use crate::llm::{LlmError, Part, SentImage, VisionLlm};
use crate::models::{self, BoxErr, Detection, Detector, Segmenter};
use crate::orb::{self, OrbParams};
use crate::raster::{self, Mask};
use image::{GrayImage, Rgb, RgbImage};
use pcx_contracts::{PointMM, RectMM};
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashMap};
use std::path::PathBuf;
use std::time::Instant;

/// Umbrales: los mismos defaults que los `argparse` y las constantes de `seguidor_v4.py`.
#[derive(Debug, Clone)]
pub struct Params {
    /// `--umbral`: diferencia de gris para «cambió» (nivel 0).
    pub umbral: u8,
    /// `--frac`: fracción de la máscara cambiada para sospechar del objeto.
    pub frac: f64,
    /// `--igual`: NCC mínima para «sigue igual» (nivel 1).
    pub igual: f64,
    /// `--min-zona`: px cambiados mínimos para mirar (nivel 0).
    pub min_zona: u64,
    pub orb: OrbParams,
    pub n2_min_inliers: usize,
    pub n2_ransac_px: f64,
    pub n2_max_scale_dev: f64,
    pub n3_box_threshold: f32,
    pub n3_sim_ok: f64,
    pub n3_ratio: (f64, f64),
    pub n3_margin: f64,
    pub n4_sim_min: f64,
    pub n4_effort: String,
    pub n5_grid: (u32, u32),
    pub n5_min_area: u64,
    pub n5_effort: String,
}

impl Default for Params {
    fn default() -> Self {
        Params {
            umbral: 24,
            frac: 0.2,
            igual: 0.85,
            min_zona: 500,
            orb: OrbParams::default(),
            n2_min_inliers: 10,
            n2_ransac_px: 4.0,
            n2_max_scale_dev: 0.15,
            n3_box_threshold: 0.22,
            n3_sim_ok: 0.6,
            n3_ratio: (0.5, 2.0),
            n3_margin: 0.25,
            n4_sim_min: 0.35,
            n4_effort: "low".into(),
            n5_grid: (28, 16),
            n5_min_area: 200,
            n5_effort: "medium".into(),
        }
    }
}

/// Foto limpia (proyector apagado, marcador de sincronía ya borrado) **rectificada al lienzo** de la mesa.
/// Usa `geom::Rectifier` para pasar de la foto de cámara + homografía cámara→mm a esto.
#[derive(Clone)]
pub struct CleanFrame {
    pub rgb: RgbImage,
    pub t_ns: i64,
}

#[derive(Clone)]
struct Ref {
    color: RgbImage,
    mask: Mask,
    bbox: (u32, u32, u32, u32),
}

/// Estado interno de un objeto (el objeto es la fuente de verdad: máscara, afín y apariencia).
#[derive(Clone)]
pub struct TrackedObject {
    pub id: String,
    pub label: String,
    pub prompt: String,
    pub mask: Mask,
    pub hidden: bool,
    pub seq: u64,
    /// Afín registro → ahora en `table_mm`.
    pub aff_mm: Affine,
    pub confidence: f64,
    pub since_ns: i64,
    pub last_seen_ns: i64,
    pub appearance: Option<String>,
    reference: Option<Ref>,
}

/// Lo que pasó en una revisión.
#[derive(Debug, Default, Clone)]
pub struct Report {
    pub events: Vec<ObjectEvent>,
    /// px del lienzo que cambiaron (nivel 0, tras la morfología).
    pub changed_px: u64,
    /// Zonas cambiadas grandes que ningún objeto reclamó (¿objeto nuevo? → `register_all`).
    pub unclaimed_zones: usize,
    /// Bitácora como la del original (`N1 taza: sigue igual`, `N2 rígido (34 pts)`...).
    pub log: Vec<String>,
    pub seconds: f64,
}

pub struct Tracker {
    pub params: Params,
    pub canvas: Canvas,
    /// Área útil del lienzo (px válidos de la rectificación).
    pub area: Mask,
    objects: BTreeMap<String, TrackedObject>,
    prev: Option<(GrayImage, RgbImage)>,
    detector: Option<Box<dyn Detector>>,
    segmenter: Option<Box<dyn Segmenter>>,
    llm: Option<Box<dyn VisionLlm>>,
    appearance_dir: Option<PathBuf>,
}

#[derive(Debug, thiserror::Error)]
pub enum TrackError {
    #[error("falta {0} para este nivel")]
    Missing(&'static str),
    #[error("modelo: {0}")]
    Model(#[from] BoxErr),
    #[error("llm: {0}")]
    Llm(#[from] LlmError),
}

struct Cand {
    mask: Mask,
    score: f64,
    sim: f64,
    ratio: f64,
}

impl Tracker {
    pub fn new(canvas: Canvas, params: Params) -> Tracker {
        let area = raster::full(canvas.width, canvas.height);
        Tracker { params, canvas, area, objects: BTreeMap::new(), prev: None, detector: None, segmenter: None, llm: None, appearance_dir: None }
    }
    pub fn with_area(mut self, area: Mask) -> Self {
        self.area = area;
        self
    }
    pub fn with_detector(mut self, d: Box<dyn Detector>) -> Self {
        self.detector = Some(d);
        self
    }
    pub fn with_segmenter(mut self, s: Box<dyn Segmenter>) -> Self {
        self.segmenter = Some(s);
        self
    }
    pub fn with_llm(mut self, l: Box<dyn VisionLlm>) -> Self {
        self.llm = Some(l);
        self
    }
    /// Carpeta donde escribir los recortes PNG de apariencia (`<id>_<seq>.png`).
    pub fn with_appearance_dir(mut self, d: PathBuf) -> Self {
        let _ = std::fs::create_dir_all(&d);
        self.appearance_dir = Some(d);
        self
    }

    pub fn llm(&self) -> Option<&dyn VisionLlm> {
        self.llm.as_deref()
    }

    /// Objetos visibles (lo que va a `WorldState.objects`).
    pub fn objects(&self) -> Vec<ObjectState> {
        self.objects.values().filter(|o| !o.hidden).filter_map(|o| self.state_of(o)).collect()
    }

    pub fn object(&self, id: &str) -> Option<&TrackedObject> {
        self.objects.get(id)
    }

    /// Registra un objeto a mano (o retoma el catálogo) con su máscara en px del lienzo.
    pub fn add_object(&mut self, id: &str, label: &str, prompt: &str, mask: Mask, frame: &CleanFrame) -> Option<ObjectEvent> {
        let mut o = TrackedObject {
            id: id.into(),
            label: label.into(),
            prompt: prompt.into(),
            mask,
            hidden: false,
            seq: 0,
            aff_mm: IDENTITY,
            confidence: 1.0,
            since_ns: frame.t_ns,
            last_seen_ns: frame.t_ns,
            appearance: None,
            reference: None,
        };
        recrop(&mut o, &frame.rgb);
        self.publish_appearance(&mut o, &frame.rgb);
        let ev = self.state_of(&o).map(|s| ObjectEvent::ObjectAppeared { object: s, level: Some("registro manual".into()) });
        self.objects.insert(id.into(), o);
        if self.prev.is_none() {
            self.set_reference(frame);
        }
        ev
    }

    /// Igual que `add_object` pero con un polígono en mm (p. ej. al retomar `ObjectState` guardados).
    pub fn add_object_polygon_mm(&mut self, id: &str, label: &str, prompt: &str, poly: &[PointMM], frame: &CleanFrame) -> Option<ObjectEvent> {
        let pts: Vec<imageproc::point::Point<i32>> = poly
            .iter()
            .map(|p| {
                let (u, v) = self.canvas.mm_to_px(p.0, p.1);
                imageproc::point::Point::new(u.round() as i32, v.round() as i32)
            })
            .collect();
        let mut m = raster::empty(self.canvas.width, self.canvas.height);
        if pts.len() >= 3 {
            let mut pts = pts;
            while pts.len() > 1 && pts.first() == pts.last() {
                pts.pop();
            }
            imageproc::drawing::draw_polygon_mut(&mut m, &pts, raster::ON);
        }
        self.add_object(id, label, prompt, m, frame)
    }

    /// Fija la foto limpia de referencia (la «R» del original) sin revisar.
    pub fn set_reference(&mut self, frame: &CleanFrame) {
        self.prev = Some((raster::blur5(&raster::gray(&frame.rgb)), frame.rgb.clone()));
    }

    /// Revisión con una foto limpia nueva: niveles 0 → 4. Devuelve los eventos.
    pub fn update(&mut self, frame: &CleanFrame) -> Report {
        let t0 = Instant::now();
        let mut rep = Report::default();
        let g = raster::blur5(&raster::gray(&frame.rgb));
        let Some((g_prev, _)) = self.prev.take() else {
            self.prev = Some((g, frame.rgb.clone()));
            return rep;
        };
        let p = self.params.clone();
        // ---- nivel 0 ----
        let mut cambio = raster::and(&raster::absdiff_gt(&g, &g_prev, p.umbral), &self.area);
        cambio = raster::open_sq(&cambio, 5);
        cambio = raster::close_sq(&cambio, 21);
        rep.changed_px = raster::count(&cambio);
        if rep.changed_px < p.min_zona {
            self.prev = Some((g, frame.rgb.clone()));
            rep.seconds = t0.elapsed().as_secs_f64();
            return rep;
        }
        let sosp: Vec<String> = self
            .objects
            .values()
            .filter(|o| !o.hidden && raster::any(&o.mask) && raster::frac_inside(&o.mask, &cambio) > p.frac)
            .map(|o| o.id.clone())
            .collect();
        let ocultos: Vec<String> = self.objects.values().filter(|o| o.hidden).map(|o| o.id.clone()).collect();
        rep.log.push(format!("N0 cambio: {} px · sospechosos: {:?} · ocultos: {}", rep.changed_px, sosp, ocultos.len()));
        let mut libre = cambio.clone();
        for o in self.objects.values() {
            if !o.hidden && !sosp.contains(&o.id) {
                libre = raster::and_not(&libre, &o.mask);
            }
        }
        let rgb = &frame.rgb;
        let mut dino_cache: HashMap<String, Vec<Detection>> = HashMap::new();
        let mut sam_ready = false;
        for id in sosp.iter().chain(ocultos.iter()) {
            let t1 = Instant::now();
            let is_sosp = sosp.contains(id);
            let mut o = self.objects.remove(id).expect("objeto");
            // ---- nivel 1 ----
            if is_sosp && self.n1_same(&o, rgb) {
                rep.log.push(format!("  N1 {}: sigue igual", o.label));
                recrop(&mut o, rgb);
                o.last_seen_ns = frame.t_ns;
                self.objects.insert(id.clone(), o);
                continue;
            }
            let was_hidden = o.hidden;
            let (mut nuevo, mut metodo, mut conf) = (None::<Mask>, String::new(), 0.0);
            // ---- nivel 2 ----
            if let Some((a, ninl)) = self.n2_rigid(&o, rgb, &libre) {
                let base = if raster::any(&o.mask) { o.mask.clone() } else { ref_mask_full(&o, self.canvas.width, self.canvas.height) };
                nuevo = Some(raster::fill_largest(&raster::warp_affine_nearest(&base, &a, self.canvas.width, self.canvas.height)));
                o.aff_mm = geom::compose(&self.canvas.affine_px_to_mm(&a), &o.aff_mm);
                metodo = format!("N2 rígido ({ninl} pts)");
                conf = (0.5 + ninl as f64 / 60.0).min(1.0);
            } else {
                // ---- nivel 3 ----
                let cands = match self.n3_candidates(&o, rgb, &libre, &mut dino_cache, &mut sam_ready) {
                    Ok(c) => c,
                    Err(e) => {
                        rep.log.push(format!("  N3 {}: {e}", o.label));
                        vec![]
                    }
                };
                let buenos: Vec<&Cand> =
                    cands.iter().filter(|c| c.sim > p.n3_sim_ok && p.n3_ratio.0 < c.ratio && c.ratio < p.n3_ratio.1).collect();
                let mut elegido: Option<&Cand> = None;
                if !buenos.is_empty() && (buenos.len() == 1 || buenos[0].score - buenos[1].score > p.n3_margin) {
                    elegido = Some(buenos[0]);
                    metodo = format!("N3 dino+sam (sim {:.2})", buenos[0].sim);
                    conf = buenos[0].sim.clamp(0.0, 1.0);
                } else {
                    let dudosos: Vec<&Cand> = cands.iter().filter(|c| c.sim > p.n4_sim_min).collect();
                    if !dudosos.is_empty() {
                        // ---- nivel 4 ----
                        match self.n4_llm(&o, rgb, &dudosos) {
                            Ok(Some(k)) => {
                                elegido = Some(dudosos[k]);
                                metodo = "N4 llm".into();
                                conf = 0.6;
                            }
                            Ok(None) => {}
                            Err(e) => rep.log.push(format!("  N4 {}: {e}", o.label)),
                        }
                    }
                }
                if let Some(c) = elegido {
                    if let (Some(c1), Some(c0)) = (raster::centroid(&c.mask), raster::centroid(&o.mask)) {
                        o.aff_mm[0][2] += (c1.0 - c0.0) * self.canvas.mm_per_px;
                        o.aff_mm[1][2] += (c1.1 - c0.1) * self.canvas.mm_per_px;
                    }
                    nuevo = Some(c.mask.clone());
                }
            }
            match nuevo.filter(raster::any) {
                Some(m) => {
                    o.mask = m;
                    o.hidden = false;
                    o.confidence = conf;
                    o.last_seen_ns = frame.t_ns;
                    if was_hidden {
                        o.since_ns = frame.t_ns;
                    }
                    recrop(&mut o, rgb);
                    self.publish_appearance(&mut o, rgb);
                    libre = raster::and_not(&libre, &raster::dilate_sq(&o.mask, 15));
                    rep.log.push(format!("  {}: {metodo} ({:.2}s)", o.label, t1.elapsed().as_secs_f64()));
                    if let Some(s) = self.state_of(&o) {
                        rep.events.push(if was_hidden {
                            ObjectEvent::ObjectAppeared { object: s, level: Some(metodo) }
                        } else {
                            ObjectEvent::ObjectMoved {
                                object_id: s.id,
                                center_mm: s.center_mm,
                                polygon_mm: Some(s.polygon_mm),
                                aff: s.aff,
                                confidence: Some(s.confidence),
                                appearance: s.appearance,
                                level: Some(metodo),
                            }
                        });
                    }
                }
                None if is_sosp => {
                    o.hidden = true;
                    o.seq += 1;
                    o.mask = raster::empty(self.canvas.width, self.canvas.height);
                    rep.log.push(format!("  {}: no lo encuentro → oculto ({:.2}s)", o.label, t1.elapsed().as_secs_f64()));
                    rep.events.push(ObjectEvent::ObjectRemoved { object_id: o.id.clone(), label: Some(o.label.clone()) });
                }
                None => {}
            }
            self.objects.insert(id.clone(), o);
        }
        rep.unclaimed_zones = raster::components(&libre).iter().filter(|&&(_, n)| n > 4 * p.min_zona).count();
        if rep.unclaimed_zones > 0 {
            rep.log.push(format!("  zonas sin dueño (¿objeto nuevo?): {}", rep.unclaimed_zones));
        }
        self.prev = Some((g, frame.rgb.clone()));
        rep.seconds = t0.elapsed().as_secs_f64();
        rep.log.push(format!("cascada lista en {:.2}s", rep.seconds));
        rep
    }

    // ---------- niveles ----------

    /// Nivel 1: NCC (TM_CCOEFF_NORMED con máscara) del recorte viejo contra el mismo lugar ahora.
    pub fn n1_same(&self, o: &TrackedObject, rgb: &RgbImage) -> bool {
        let Some(r) = &o.reference else { return false };
        let a = raster::gray(&r.color);
        let b = raster::gray(&raster::crop_rgb(rgb, r.bbox));
        crate::ncc::masked_zncc(&a, &b, &r.mask) > self.params.igual
    }

    /// Nivel 2: ORB del recorte de referencia contra la foto (solo en las zonas cambiadas) → similitud por RANSAC.
    pub fn n2_rigid(&self, o: &TrackedObject, rgb: &RgbImage, zonas: &Mask) -> Option<(Affine, usize)> {
        let r = o.reference.as_ref()?;
        let p = &self.params;
        let (k1, d1) = orb::detect_and_compute(&raster::gray(&r.color), Some(&r.mask), &p.orb);
        let zon = raster::dilate_sq(zonas, 31);
        let (k2, d2) = orb::detect_and_compute(&raster::gray(rgb), Some(&zon), &p.orb);
        if k1.len() < 10 || k2.len() < 10 {
            return None;
        }
        let ms = orb::match_ratio(&d1, &d2, 0.8);
        if ms.len() < p.n2_min_inliers {
            return None;
        }
        let p1: Vec<(f64, f64)> = ms.iter().map(|&(i, _)| (k1[i].x as f64 + r.bbox.0 as f64, k1[i].y as f64 + r.bbox.1 as f64)).collect();
        let p2: Vec<(f64, f64)> = ms.iter().map(|&(_, j)| (k2[j].x as f64, k2[j].y as f64)).collect();
        let (a, inl) = geom::ransac_similarity(&p1, &p2, p.n2_ransac_px, 2000)?;
        let n = inl.iter().filter(|&&b| b).count();
        if n < p.n2_min_inliers || (geom::scale_of(&a) - 1.0).abs() > p.n2_max_scale_dev {
            return None;
        }
        Some((a, n))
    }

    fn n3_candidates(
        &mut self,
        o: &TrackedObject,
        rgb: &RgbImage,
        zonas: &Mask,
        cache: &mut HashMap<String, Vec<Detection>>,
        sam_ready: &mut bool,
    ) -> Result<Vec<Cand>, TrackError> {
        let (Some(det), Some(_)) = (self.detector.as_mut(), self.segmenter.as_ref()) else { return Ok(vec![]) };
        if !cache.contains_key(&o.prompt) {
            let d = det.detect(rgb, std::slice::from_ref(&o.prompt), self.params.n3_box_threshold)?;
            cache.insert(o.prompt.clone(), d);
        }
        let seg = self.segmenter.as_mut().expect("segmentador");
        let (w, h) = (self.canvas.width, self.canvas.height);
        let (ref_hist, ref_area) = match &o.reference {
            Some(r) => (raster::hsv_hist(&r.color, &r.mask), raster::count(&r.mask).max(1)),
            None => (raster::hsv_hist(rgb, &o.mask), raster::count(&o.mask).max(1)),
        };
        let mut out = vec![];
        for d in &cache[&o.prompt] {
            let b = d.bbox;
            if ((b[2] - b[0]) * (b[3] - b[1])) as f64 > 0.4 * (w * h) as f64 {
                continue; // caja = toda la mesa: no sirve
            }
            if !*sam_ready {
                seg.set_image(rgb)?;
                *sam_ready = true;
            }
            let (m, _) = seg.predict_box(b, Some(((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)))?;
            let m = raster::fill_largest(&raster::open_sq(&raster::and(&m, &self.area), 5));
            let n = raster::count(&m);
            if n < 80 {
                continue;
            }
            let sim = raster::correl(&ref_hist, &raster::hsv_hist(rgb, &m));
            let en_zona = raster::count(&raster::and(zonas, &m)) as f64 / n as f64;
            let ratio = n as f64 / ref_area as f64;
            let score = d.score as f64 + 0.8 * sim + 0.4 * (en_zona * 2.0).min(1.0) - 0.5 * ratio.max(1e-3).ln().abs();
            out.push(Cand { mask: m, score, sim, ratio });
        }
        out.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(std::cmp::Ordering::Equal));
        Ok(out)
    }

    /// Nivel 4: el LLM elige entre REF y hasta 5 candidatos rotulados. `Ok(Some(i))` = índice en `cands`.
    fn n4_llm(&mut self, o: &TrackedObject, rgb: &RgbImage, cands: &[&Cand]) -> Result<Option<usize>, TrackError> {
        let llm = self.llm.as_mut().ok_or(TrackError::Missing("LLM (nivel 4)"))?;
        let mut parts = vec![];
        if let Some(r) = &o.reference {
            parts.push(Part::Text("REF:".into()));
            parts.push(Part::Image(SentImage::jpeg(&r.color)));
        }
        let n = cands.len().min(5);
        for (k, c) in cands.iter().take(n).enumerate() {
            if let Some(b) = raster::bbox(&c.mask) {
                let b = pad(b, 6, rgb.width(), rgb.height());
                parts.push(Part::Text(format!("Candidato {}:", k + 1)));
                parts.push(Part::Image(SentImage::jpeg(&raster::crop_rgb(rgb, b))));
            }
        }
        parts.push(Part::Text(format!(
            "En las imágenes, REF es '{}' antes de moverlo; los demás son candidatos numerados. ¿Cuál es EXACTAMENTE el \
             mismo objeto físico (puede estar girado, doblado o arrugado)? Un reflejo de luz, una sombra o un objeto \
             parecido pero distinto NO cuenta. Sé estricto. Responde SOLO JSON {{\"n\": 2}} o {{\"n\": null}} si ninguno.",
            o.label
        )));
        let schema = json!({"type": "object", "properties": {"n": {"anyOf": [{"type": "integer"}, {"type": "null"}]}},
                            "required": ["n"], "additionalProperties": false});
        let res = llm.ask_json(&parts, &schema, &self.params.n4_effort)?;
        Ok(res.get("n").and_then(Value::as_u64).map(|k| k as usize).filter(|&k| 1 <= k && k <= n).map(|k| k - 1))
    }

    /// Nivel 5: registro global. SAM automático (grilla 28×16) → el LLM clasifica y nombra cada región → catálogo nuevo.
    pub fn register_all(&mut self, frame: &CleanFrame) -> Result<Report, TrackError> {
        let t0 = Instant::now();
        let mut rep = Report::default();
        let rgb = &frame.rgb;
        let (w, h) = (self.canvas.width, self.canvas.height);
        let seg = self.segmenter.as_mut().ok_or(TrackError::Missing("segmentador (nivel 5)"))?;
        let props = models::automatic_masks(seg.as_mut(), rgb, &self.area, self.params.n5_grid.0, self.params.n5_grid.1, self.params.n5_min_area)?;
        let area_total = raster::count(&self.area) as f64;
        let props: Vec<Mask> = props.into_iter().filter(|p| (p.area as f64) < 0.25 * area_total).map(|p| p.mask).collect();
        // imagen 1: vista con regiones numeradas · imagen 2: hoja de recortes
        let mut vis = rgb.clone();
        for (k, m) in props.iter().enumerate() {
            raster::draw_outline(&mut vis, m, [0, 220, 0]);
            if let Some((cx, cy)) = raster::centroid(m) {
                raster::draw_number(&mut vis, cx as i64 - 6, cy as i64 - 8, k + 1, 3, [0, 220, 0]);
            }
        }
        let sheet = contact_sheet(rgb, &props);
        let n = props.len();
        let prompt = format!(
            "Imagen 1: foto cenital de una mesa (sin proyección) con regiones segmentadas numeradas en verde. Imagen 2: \
             recorte ampliado de cada región con su número (lo de afuera va oscurecido). Clasifica CADA región 1..{n}: \
             ¿es (parte de) un objeto físico suelto sobre la mesa? Si es mesa, borde, reflejo, sombra, luz, cable del \
             notebook, persona o mano → objeto null. Si una región es solo una parte (ej. el interior de un plato, un \
             pliegue de un paño), usa el MISMO nombre de objeto que su parte principal para que se agrupen. Nombres \
             cortos en español, distintos por objeto (si hay dos cucharas: 'cuchara 1', 'cuchara 2'), y un prompt corto \
             en inglés para un detector (ej. 'a white mug'). Responde SOLO JSON: {{\"regiones\": [{{\"n\": 1, \
             \"objeto\": \"plato blanco\", \"prompt\": \"a white plate\"}}, {{\"n\": 2, \"objeto\": null, \"prompt\": null}}]}}"
        );
        let schema = json!({"type": "object", "additionalProperties": false, "required": ["regiones"], "properties": {
            "regiones": {"type": "array", "items": {"type": "object", "additionalProperties": false,
                "required": ["n", "objeto", "prompt"], "properties": {
                    "n": {"type": "integer"},
                    "objeto": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "prompt": {"anyOf": [{"type": "string"}, {"type": "null"}]}}}}}});
        let llm = self.llm.as_mut().ok_or(TrackError::Missing("LLM (nivel 5)"))?;
        let res = llm.ask_json(
            &[Part::Image(SentImage::jpeg(&vis)), Part::Image(SentImage::jpeg(&sheet)), Part::Text(prompt)],
            &schema,
            &self.params.n5_effort,
        )?;
        let groups = group_regions(&res, n);
        rep.log.push(format!("registro: {n} regiones, {} objetos, {:.1}s", groups.len(), t0.elapsed().as_secs_f64()));
        let old: Vec<(String, String, bool)> = self.objects.values().map(|o| (o.id.clone(), o.label.clone(), o.hidden)).collect();
        self.objects.clear();
        for (k, (nombre, prompt, regs)) in groups.into_iter().enumerate() {
            let mut m = raster::empty(w, h);
            for r in &regs {
                m = raster::or(&m, &props[r - 1]);
            }
            if regs.len() > 1 {
                m = raster::close_sq(&m, 31);
            }
            let m = raster::fill_largest(&m);
            let id = format!("m{:02}", k + 1);
            let mut o = TrackedObject {
                id: id.clone(),
                label: nombre.clone(),
                prompt,
                mask: m,
                hidden: false,
                seq: 0,
                aff_mm: IDENTITY,
                confidence: 0.8,
                since_ns: frame.t_ns,
                last_seen_ns: frame.t_ns,
                appearance: None,
                reference: None,
            };
            recrop(&mut o, rgb);
            self.publish_appearance(&mut o, rgb);
            if let Some(s) = self.state_of(&o) {
                rep.events.push(ObjectEvent::ObjectAppeared { object: s, level: Some("N5 registro".into()) });
            }
            rep.log.push(format!("  {id}: {nombre} ('{}') regiones {regs:?}", o.prompt));
            self.objects.insert(id, o);
        }
        for (id, label, hidden) in old {
            if !hidden && !self.objects.contains_key(&id) {
                rep.events.push(ObjectEvent::ObjectRemoved { object_id: id, label: Some(label) });
            }
        }
        self.set_reference(frame);
        rep.seconds = t0.elapsed().as_secs_f64();
        Ok(rep)
    }

    // ---------- publicación ----------

    fn publish_appearance(&self, o: &mut TrackedObject, rgb: &RgbImage) {
        o.seq += 1;
        let Some(dir) = &self.appearance_dir else { return };
        let Some(cut) = raster::cutout_rgba(rgb, &o.mask) else { return };
        let path = dir.join(format!("{}_{:05}.png", o.id, o.seq));
        if cut.save(&path).is_ok() {
            if let Some(old) = o.appearance.replace(path.to_string_lossy().into_owned()) {
                let _ = std::fs::remove_file(old);
            }
        }
    }

    fn state_of(&self, o: &TrackedObject) -> Option<ObjectState> {
        let c = &self.canvas;
        let poly: Vec<PointMM> = raster::contour_polygon(&o.mask, 1.2)
            .into_iter()
            .map(|(u, v)| {
                let (x, y) = c.px_to_mm(u, v);
                PointMM(x, y)
            })
            .collect();
        if poly.len() < 3 {
            return None;
        }
        let (cu, cv) = raster::centroid(&o.mask)?;
        let (cx, cy) = c.px_to_mm(cu, cv);
        let b = raster::bbox(&o.mask)?;
        let (x0, y0) = c.px_to_mm(b.0 as f64, b.1 as f64);
        Some(ObjectState {
            id: o.id.clone(),
            label: o.label.clone(),
            confidence: o.confidence,
            polygon_mm: poly,
            center_mm: PointMM(cx, cy),
            bbox_mm: Some(RectMM(x0, y0, (b.2 - b.0) as f64 * c.mm_per_px, (b.3 - b.1) as f64 * c.mm_per_px)),
            since_ns: o.since_ns,
            last_seen_ns: o.last_seen_ns,
            appearance: o.appearance.clone(),
            aff: Some(o.aff_mm),
            prompt: Some(o.prompt.clone()),
            seq: Some(o.seq),
        })
    }
}

/// `Obj.recortar`: recorte color + máscara con 6 px de margen de la última foto limpia.
fn recrop(o: &mut TrackedObject, rgb: &RgbImage) {
    if raster::count(&o.mask) < 30 {
        return;
    }
    let b = pad(raster::bbox(&o.mask).expect("máscara no vacía"), 6, rgb.width(), rgb.height());
    o.reference = Some(Ref { color: raster::crop_rgb(rgb, b), mask: raster::crop_gray(&o.mask, b), bbox: b });
}

fn ref_mask_full(o: &TrackedObject, w: u32, h: u32) -> Mask {
    match &o.reference {
        Some(r) => raster::place(&r.mask, r.bbox, w, h),
        None => raster::empty(w, h),
    }
}

fn pad(b: (u32, u32, u32, u32), m: u32, w: u32, h: u32) -> (u32, u32, u32, u32) {
    (b.0.saturating_sub(m), b.1.saturating_sub(m), (b.2 + m).min(w), (b.3 + m).min(h))
}

/// Hoja de recortes (6 por fila, 170×180, fuera de la región oscurecido al 35 %), como `registro_recortes.jpg`.
fn contact_sheet(rgb: &RgbImage, props: &[Mask]) -> RgbImage {
    let (tw, th) = (170u32, 180u32);
    let rows = props.len().div_ceil(6).max(1) as u32;
    let mut sheet = RgbImage::from_pixel(tw * 6, th * rows, Rgb([255, 255, 255]));
    for (k, m) in props.iter().enumerate() {
        let Some(b) = raster::bbox(m) else { continue };
        let b = pad(b, 8, rgb.width(), rgb.height());
        let mut crop = raster::crop_rgb(rgb, b);
        for (x, y, p) in crop.enumerate_pixels_mut() {
            if m.get_pixel(b.0 + x, b.1 + y).0[0] == 0 {
                p.0 = p.0.map(|v| (v as f32 * 0.35) as u8);
            }
        }
        let s = 150.0 / crop.width().max(crop.height()) as f64;
        let (nw, nh) = (((crop.width() as f64 * s) as u32).max(1), ((crop.height() as f64 * s) as u32).max(1));
        let crop = image::imageops::resize(&crop, nw, nh, image::imageops::FilterType::Triangle);
        let (ox, oy) = ((k as u32 % 6) * tw, (k as u32 / 6) * th);
        image::imageops::overlay(&mut sheet, &crop, (ox + 10) as i64, (oy + 26) as i64);
        raster::draw_number(&mut sheet, ox as i64 + 6, oy as i64 + 6, k + 1, 3, [0, 140, 0]);
    }
    sheet
}

/// Agrupa la respuesta del LLM por nombre de objeto. Acepta la forma de lista (`{"regiones": [{"n", "objeto",
/// "prompt"}]}`), la de mapa de Daniel (`{"regiones": {"1": {...}}}`) y la antigua (`{"objetos": [...]}`).
pub fn group_regions(res: &Value, n_regions: usize) -> Vec<(String, String, Vec<usize>)> {
    let mut groups: Vec<(String, String, Vec<usize>)> = vec![];
    let mut add = |k: usize, nombre: &str, prompt: Option<&str>| {
        if k < 1 || k > n_regions {
            return;
        }
        match groups.iter_mut().find(|g| g.0 == nombre) {
            Some(g) => {
                if !g.2.contains(&k) {
                    g.2.push(k)
                }
            }
            None => groups.push((nombre.to_string(), prompt.unwrap_or(nombre).to_string(), vec![k])),
        }
    };
    match res.get("regiones") {
        Some(Value::Array(a)) => {
            for r in a {
                if let (Some(k), Some(nombre)) = (r["n"].as_u64(), r["objeto"].as_str()) {
                    add(k as usize, nombre, r["prompt"].as_str());
                }
            }
        }
        Some(Value::Object(m)) => {
            let mut keys: Vec<_> = m.keys().filter_map(|k| k.parse::<usize>().ok()).collect();
            keys.sort();
            for k in keys {
                let v = &m[&k.to_string()];
                if let Some(nombre) = v["objeto"].as_str() {
                    add(k, nombre, v["prompt"].as_str());
                }
            }
        }
        _ => {
            for ob in res["objetos"].as_array().into_iter().flatten() {
                let nombre = ob["nombre"].as_str().unwrap_or("objeto");
                for r in ob["regiones"].as_array().into_iter().flatten().filter_map(Value::as_u64) {
                    add(r as usize, nombre, ob["prompt"].as_str());
                }
            }
        }
    }
    groups
}
