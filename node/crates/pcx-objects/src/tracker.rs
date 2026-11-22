//! La cascada (port de `Seguidor.revisar` / `registrar_todo` de `seguidor_v4.py`, versión 02:09 del 21-nov).
//!
//! Nivel 0 diff limpio/limpio · 1 NCC «¿sigue en su caja?» · 2 ORB rígido · 2b histograma HSV en las zonas
//! cambiadas sin dueño (barato, sin modelos; el nivel por color de `seguidor_objetos.py`/`seguidor_v2.py`) ·
//! 3 DINO(prompt)+SAM(caja) · 4 LLM desempata · 5 registro global (SAM automático + LLM nombra).
//! Mismos umbrales que el original en los niveles que existen allá.

use crate::contract::{ObjectEvent, ObjectState};
use crate::geom::{self, Affine, Canvas, IDENTITY};
use crate::llm::{LlmError, Part, SentImage, VisionLlm};
use crate::models::{self, BoxErr, Detection, Detector, Segmenter};
use crate::orb::{self, OrbParams};
use crate::raster::{self, Blob, Mask};
use image::{GrayImage, Rgb, RgbImage};
use pcx_contracts::{PointMM, RectMM};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet, HashMap};
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
    /// Nivel 2b (histograma HSV en zonas cambiadas sin dueño), antes de DINO. `false` = cascada original.
    pub n2b_enabled: bool,
    pub n2b_metric: HistMetric,
    /// Similitud mínima de histograma para aceptar sin preguntar (misma escala que `n3_sim_ok`).
    pub n2b_sim_ok: f64,
    /// Razón de área candidato/referencia admitida (un papel arrugado se achica).
    pub n2b_ratio: (f64, f64),
    /// Ventaja de puntaje sobre el segundo candidato; con menos, queda ambiguo y decide N3/N4.
    pub n2b_margin: f64,
    /// Fracción mínima del candidato dentro del área útil del lienzo.
    pub n2b_min_inside: f64,
    /// Un píxel «se parece al objeto» si su casillero HSV pesa más que esta fracción del máximo de la referencia.
    pub n2b_backproj: f32,
    /// Distancia máxima al lugar anterior (mm). `None`: cualquier lugar de la mesa es plausible.
    pub n2b_max_dist_mm: Option<f64>,
    /// Evidencia de que el candidato es un objeto y no mesa vacía: fracción mínima de sus píxeles que difieren (en
    /// algún canal, más que `n2b_contrast`) de la mediana de la mesa que lo rodea. Sin eso no se asigna.
    pub n2b_min_contrast_frac: f64,
    pub n2b_contrast: u8,
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
            n2b_enabled: true,
            n2b_metric: HistMetric::Correl,
            n2b_sim_ok: 0.6,
            n2b_ratio: (0.4, 2.0),
            n2b_margin: 0.25,
            n2b_min_inside: 0.9,
            n2b_backproj: 0.05,
            n2b_max_dist_mm: None,
            n2b_min_contrast_frac: 0.5,
            n2b_contrast: 20,
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

/// Comparación de histogramas del nivel 2b.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HistMetric {
    /// `cv2.HISTCMP_CORREL` (la del original; −1..1).
    Correl,
    /// Coeficiente de Bhattacharyya (0..1); más tolerante a cambios de proporción entre colores.
    Bhattacharyya,
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
    /// Objetos que el nivel 2b vio en más de un lugar (color parecido): no se asignan sin N3/N4.
    pub ambiguous: Vec<String>,
    /// Tiempo acumulado del nivel 2b en esta revisión (ms).
    pub n2b_ms: f64,
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
    retry: BTreeSet<String>,
    next_id: u64,
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

#[derive(Clone)]
struct Cand {
    mask: Mask,
    score: f64,
    sim: f64,
    ratio: f64,
    /// Centro y eje principal (solo nivel 2b).
    pose: Option<Pose>,
    from_hist: bool,
}

/// Centroide (px del lienzo), ángulo del eje principal (rad, módulo 180°) y elongación (razón de ejes).
#[derive(Debug, Clone, Copy)]
struct Pose {
    c: (f64, f64),
    theta: f64,
    elong: f64,
}

impl Tracker {
    pub fn new(canvas: Canvas, params: Params) -> Tracker {
        let area = raster::full(canvas.width, canvas.height);
        Tracker {
            params,
            canvas,
            area,
            objects: BTreeMap::new(),
            prev: None,
            detector: None,
            segmenter: None,
            llm: None,
            appearance_dir: None,
            retry: BTreeSet::new(),
            next_id: 0,
        }
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
    pub fn add_object(
        &mut self,
        id: &str,
        label: &str,
        prompt: &str,
        mask: Mask,
        frame: &CleanFrame,
    ) -> Option<ObjectEvent> {
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
        let ev =
            self.state_of(&o).map(|s| ObjectEvent::ObjectAppeared { object: s, level: Some("registro manual".into()) });
        self.objects.insert(id.into(), o);
        if self.prev.is_none() {
            self.set_reference(frame);
        }
        ev
    }

    /// Igual que `add_object` pero con un polígono en mm (p. ej. al retomar `ObjectState` guardados).
    pub fn add_object_polygon_mm(
        &mut self,
        id: &str,
        label: &str,
        prompt: &str,
        poly: &[PointMM],
        frame: &CleanFrame,
    ) -> Option<ObjectEvent> {
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
        if rep.changed_px < p.min_zona && self.retry.is_empty() && !self.objects.values().any(|o| o.hidden) {
            self.prev = Some((g, frame.rgb.clone()));
            rep.seconds = t0.elapsed().as_secs_f64();
            return rep;
        }
        let sosp: Vec<String> = self
            .objects
            .values()
            .filter(|o| {
                !o.hidden
                    && (self.retry.contains(&o.id)
                        || (raster::any(&o.mask) && raster::frac_inside(&o.mask, &cambio) > p.frac))
            })
            .map(|o| o.id.clone())
            .collect();
        let ocultos: Vec<String> = self.objects.values().filter(|o| o.hidden).map(|o| o.id.clone()).collect();
        rep.log.push(format!(
            "N0 cambio: {} px · sospechosos: {:?} · ocultos: {}",
            rep.changed_px,
            sosp,
            ocultos.len()
        ));
        let mut libre = if self.retry.is_empty() && ocultos.is_empty() { cambio.clone() } else { self.area.clone() };
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
                libre = raster::and_not(&libre, &raster::dilate_sq(&o.mask, 15));
                self.retry.remove(id);
                self.objects.insert(id.clone(), o);
                continue;
            }
            let was_hidden = o.hidden;
            let (mut nuevo, mut metodo, mut conf) = (None::<Mask>, String::new(), 0.0);
            // ---- nivel 2 ----
            if let Some((a, ninl)) = self.n2_rigid(&o, rgb, &libre) {
                let base = if raster::any(&o.mask) {
                    o.mask.clone()
                } else {
                    ref_mask_full(&o, self.canvas.width, self.canvas.height)
                };
                nuevo = Some(raster::fill_largest(&raster::warp_affine_nearest(
                    &base,
                    &a,
                    self.canvas.width,
                    self.canvas.height,
                )));
                o.aff_mm = geom::compose(&self.canvas.affine_px_to_mm(&a), &o.aff_mm);
                metodo = format!("N2 rígido ({ninl} pts)");
                conf = (0.5 + ninl as f64 / 60.0).min(1.0);
            } else {
                let mut pick: Option<Cand> = None;
                // ---- nivel 2b ----
                let mut c2b = vec![];
                if p.n2b_enabled {
                    let tb = Instant::now();
                    c2b = self.n2b_candidates(&o, rgb, &libre);
                    let ok: Vec<&Cand> = c2b.iter().filter(|c| c.sim > p.n2b_sim_ok).collect();
                    if !ok.is_empty() && (ok.len() == 1 || ok[0].score - ok[1].score > p.n2b_margin) {
                        metodo = format!("N2b hist (sim {:.2})", ok[0].sim);
                        conf = (0.9 * ok[0].sim).clamp(0.0, 1.0);
                        pick = Some(ok[0].clone());
                    } else if ok.len() > 1 {
                        rep.ambiguous.push(o.id.clone());
                        rep.log.push(format!(
                            "  N2b {}: ambiguo ({} candidatos de color parecido) → N3/N4",
                            o.label,
                            ok.len()
                        ));
                    }
                    let ms = tb.elapsed().as_secs_f64() * 1e3;
                    rep.n2b_ms += ms;
                    rep.log.push(format!("  N2b {}: {} candidatos en {ms:.1} ms", o.label, c2b.len()));
                }
                if pick.is_none() {
                    // ---- nivel 3 ----
                    let mut cands = match self.n3_candidates(&o, rgb, &libre, &mut dino_cache, &mut sam_ready) {
                        Ok(c) => c,
                        Err(e) => {
                            // error transitorio: se reintenta en vez de publicar una desaparición
                            rep.log.push(format!("  N3 {}: {e}", o.label));
                            self.retry.insert(id.clone());
                            self.objects.insert(id.clone(), o);
                            continue;
                        }
                    };
                    // los candidatos por color que DINO no trajo también pueden ir al juez
                    for c in c2b {
                        if !cands.iter().any(|k| iou(&k.mask, &c.mask) > 0.5) {
                            cands.push(c);
                        }
                    }
                    cands.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(std::cmp::Ordering::Equal));
                    let buenos: Vec<&Cand> = cands
                        .iter()
                        .filter(|c| c.sim > p.n3_sim_ok && p.n3_ratio.0 < c.ratio && c.ratio < p.n3_ratio.1)
                        .collect();
                    let mut elegido: Option<&Cand> = None;
                    if !buenos.is_empty() && (buenos.len() == 1 || buenos[0].score - buenos[1].score > p.n3_margin) {
                        elegido = Some(buenos[0]);
                        let nivel = if buenos[0].from_hist { "N2b hist" } else { "N3 dino+sam" };
                        metodo = format!("{nivel} (sim {:.2})", buenos[0].sim);
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
                                // sin juez configurado no hay nada que reintentar: queda ambiguo, como antes
                                Err(e @ TrackError::Missing(_)) => rep.log.push(format!("  N4 {}: {e}", o.label)),
                                Err(e) => {
                                    // error transitorio del juez: se reintenta en vez de publicar una desaparición
                                    rep.log.push(format!("  N4 {}: {e}", o.label));
                                    self.retry.insert(id.clone());
                                    self.objects.insert(id.clone(), o);
                                    continue;
                                }
                            }
                        }
                    }
                    pick = elegido.cloned();
                }
                if let Some(c) = pick {
                    let antes = if o.hidden {
                        None
                    } else {
                        o.reference.as_ref().and_then(|r| pose_of(&r.mask, (r.bbox.0, r.bbox.1)))
                    };
                    if let (Some(p1), Some(p0)) = (c.pose, antes) {
                        // sin textura no hay puntos que casar: el giro sale del eje principal (módulo 180°) y solo
                        // si la forma se conservó; si no, traslación de centroide como en N3.
                        let rigido = p0.elong > 1.2 && p1.elong > 1.2 && 0.8 < c.ratio && c.ratio < 1.25;
                        let mut d = if rigido { p1.theta - p0.theta } else { 0.0 };
                        if d > std::f64::consts::FRAC_PI_2 {
                            d -= std::f64::consts::PI;
                        } else if d <= -std::f64::consts::FRAC_PI_2 {
                            d += std::f64::consts::PI;
                        }
                        let (cs, sn) = (d.cos(), d.sin());
                        let a: Affine = [
                            [cs, -sn, p1.c.0 - (cs * p0.c.0 - sn * p0.c.1)],
                            [sn, cs, p1.c.1 - (sn * p0.c.0 + cs * p0.c.1)],
                        ];
                        o.aff_mm = geom::compose(&self.canvas.affine_px_to_mm(&a), &o.aff_mm);
                    } else if let (Some(c1), Some(c0)) = (raster::centroid(&c.mask), raster::centroid(&o.mask)) {
                        o.aff_mm[0][2] += (c1.0 - c0.0) * self.canvas.mm_per_px;
                        o.aff_mm[1][2] += (c1.1 - c0.1) * self.canvas.mm_per_px;
                    }
                    nuevo = Some(c.mask);
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
                    // Conservar la última pose para calcular la afín al reaparecer.
                    rep.log.push(format!(
                        "  {}: no lo encuentro → oculto ({:.2}s)",
                        o.label,
                        t1.elapsed().as_secs_f64()
                    ));
                    rep.events
                        .push(ObjectEvent::ObjectRemoved { object_id: o.id.clone(), label: Some(o.label.clone()) });
                }
                None => {}
            }
            self.retry.remove(id);
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
        if k1.len() < 10 {
            return None; // sin textura: ni vale la pena el ORB de la foto completa (lo toma el nivel 2b)
        }
        let zon = raster::dilate_sq(zonas, 31);
        let (k2, d2) = orb::detect_and_compute(&raster::gray(rgb), Some(&zon), &p.orb);
        if k1.len() < 10 || k2.len() < 10 {
            return None;
        }
        let ms = orb::match_ratio(&d1, &d2, 0.8);
        if ms.len() < p.n2_min_inliers {
            return None;
        }
        let p1: Vec<(f64, f64)> =
            ms.iter().map(|&(i, _)| (k1[i].x as f64 + r.bbox.0 as f64, k1[i].y as f64 + r.bbox.1 as f64)).collect();
        let p2: Vec<(f64, f64)> = ms.iter().map(|&(_, j)| (k2[j].x as f64, k2[j].y as f64)).collect();
        let (a, inl) = geom::ransac_similarity(&p1, &p2, p.n2_ransac_px, 2000)?;
        let n = inl.iter().filter(|&&b| b).count();
        if n < p.n2_min_inliers || (geom::scale_of(&a) - 1.0).abs() > p.n2_max_scale_dev {
            return None;
        }
        Some((a, n))
    }

    /// Nivel 2b: candidatos por histograma HSV en las zonas cambiadas que nadie reclamó. Sin modelos: la máscara
    /// nueva sale del contorno de la zona (arrugado/doblado, o girado sin textura donde ORB no tiene puntos).
    /// Las zonas pegadas al lugar anterior se juntan con la máscara vieja y se les quita lo que ahora es mesa
    /// (píxeles cuyo color no está en el histograma de referencia), así también sirve si se movió o giró en el lugar.
    fn n2b_candidates(&self, o: &TrackedObject, rgb: &RgbImage, libre: &Mask) -> Vec<Cand> {
        let p = &self.params;
        let Some(r) = o.reference.as_ref() else { return vec![] };
        let (w, h) = (self.canvas.width, self.canvas.height);
        let (wz, px, area) = (w as usize, rgb.as_raw(), self.area.as_raw());
        // histograma de referencia con la misma tabla cuantizada que los candidatos
        let mut lut = raster::BinLut::default();
        let (rm, rc, rw) = (r.mask.as_raw(), r.color.as_raw(), r.mask.width());
        let mut ref_hist = vec![0f32; raster::COLOR_BINS];
        let mut ref_area = 0u64;
        for (i, &v) in rm.iter().enumerate() {
            if v > 0 {
                ref_hist[lut.bin(&rc[i * 3..i * 3 + 3])] += 1.0;
                ref_area += 1;
            }
        }
        if ref_area == 0 {
            return vec![];
        }
        let ref_area = ref_area as f64;
        raster::l2_normalize(&mut ref_hist);
        let thr = p.n2b_backproj * ref_hist.iter().cloned().fold(0f32, f32::max);
        let old = !o.hidden;
        let in_old = |x: i64, y: i64| {
            old && x >= r.bbox.0 as i64
                && x < r.bbox.2 as i64
                && y >= r.bbox.1 as i64
                && y < r.bbox.3 as i64
                && rm[((y as u32 - r.bbox.1) * rw + (x as u32 - r.bbox.0)) as usize] > 0
        };
        let old_pose = if old { pose_of(&r.mask, (r.bbox.0, r.bbox.1)) } else { None };
        let blobs = raster::blobs(libre, 30);
        let mut groups: Vec<(Vec<&Blob>, bool)> = vec![];
        let mut home: Vec<&Blob> = vec![];
        for b in &blobs {
            // ¿pegada (a ≤ 4 px) al lugar anterior? muestreo cada 4 px a lo largo de cada corrida
            let cerca = old
                && b.bbox.0 < r.bbox.2 + 4
                && r.bbox.0 < b.bbox.2 + 4
                && b.bbox.1 < r.bbox.3 + 4
                && r.bbox.1 < b.bbox.3 + 4
                && b.runs.iter().any(|run| {
                    let y = run.y as i64;
                    (run.x0 as i64 - 4..run.x1 as i64 + 4)
                        .step_by(4)
                        .any(|x| in_old(x, y - 4) || in_old(x, y) || in_old(x, y + 4))
                });
            if cerca {
                home.push(b);
            } else if b.area as f64 >= p.n2b_ratio.0 * ref_area {
                groups.push((vec![b], false));
            }
        }
        if !home.is_empty() {
            groups.push((home, true));
        }
        let mut out = vec![];
        for (group, with_old) in groups {
            let mut bb = if with_old { r.bbox } else { group[0].bbox };
            for b in &group {
                bb = (bb.0.min(b.bbox.0), bb.1.min(b.bbox.1), bb.2.max(b.bbox.2), bb.3.max(b.bbox.3));
            }
            let bb = pad(bb, 3, w, h);
            let (cw, ch) = ((bb.2 - bb.0) as usize, (bb.3 - bb.1) as usize);
            let mut m = vec![0u8; cw * ch];
            for run in group.iter().flat_map(|b| &b.runs) {
                let row = (run.y - bb.1) as usize * cw;
                if !with_old {
                    m[row + (run.x0 - bb.0) as usize..row + (run.x1 - bb.0) as usize].fill(255);
                    continue;
                }
                for x in run.x0..run.x1 {
                    // lugar anterior que ahora muestra la mesa: fuera
                    let i = (run.y as usize * wz + x as usize) * 3;
                    if !in_old(x as i64, run.y as i64) || ref_hist[lut.bin(&px[i..i + 3])] > thr {
                        m[row + (x - bb.0) as usize] = 255;
                    }
                }
            }
            if with_old {
                // lo que no cambió porque el objeto se tapa a sí mismo (color liso) sigue siendo objeto
                for (k, &v) in rm.iter().enumerate() {
                    if v > 0 {
                        let (x, y) = (r.bbox.0 + k as u32 % rw, r.bbox.1 + k as u32 / rw);
                        let i = (y as usize * wz + x as usize) * 3;
                        if ref_hist[lut.bin(&px[i..i + 3])] > thr {
                            m[(y - bb.1) as usize * cw + (x - bb.0) as usize] = 255;
                        }
                    }
                }
            }
            let m = Mask::from_raw(cw as u32, ch as u32, m).expect("recorte");
            let m = raster::fill_largest_fast(&raster::open_sq_fast(&m, 5));
            // la mesa alrededor: píxeles del recorte ampliado que no son candidato ni zona cambiada (mediana por canal)
            let ring = pad(bb, 12, w, h);
            let mut med = [[0u32; 256]; 3];
            let mut n_ring = 0u32;
            let lib = libre.as_raw();
            for y in ring.1..ring.3 {
                let in_y = y >= bb.1 && y < bb.3;
                for x in ring.0..ring.2 {
                    let i = y as usize * wz + x as usize;
                    let en_cand =
                        in_y && x >= bb.0 && x < bb.2 && m.as_raw()[(y - bb.1) as usize * cw + (x - bb.0) as usize] > 0;
                    if en_cand || lib[i] > 0 || area[i] == 0 {
                        continue;
                    }
                    for c in 0..3 {
                        med[c][px[i * 3 + c] as usize] += 1;
                    }
                    n_ring += 1;
                }
            }
            let mesa = med.map(|hc| {
                let (mut acc, mut v) = (0u32, 0usize);
                while v < 255 && acc + hc[v] <= n_ring / 2 {
                    acc += hc[v];
                    v += 1;
                }
                v as u8
            });
            let mut hist = vec![0f32; raster::COLOR_BINS];
            let (mut n, mut inside, mut distinto) = (0u64, 0u64, 0u64);
            for (k, &v) in m.as_raw().iter().enumerate() {
                if v > 0 {
                    let i = (bb.1 as usize + k / cw) * wz + bb.0 as usize + k % cw;
                    let q = &px[i * 3..i * 3 + 3];
                    n += 1;
                    inside += (area[i] > 0) as u64;
                    hist[lut.bin(q)] += 1.0;
                    distinto += (q[0].abs_diff(mesa[0]) > p.n2b_contrast
                        || q[1].abs_diff(mesa[1]) > p.n2b_contrast
                        || q[2].abs_diff(mesa[2]) > p.n2b_contrast) as u64;
                }
            }
            // un lugar que ahora se ve igual que la mesa que lo rodea es un retiro, no el objeto (y sin mesa
            // alrededor con qué comparar no hay evidencia: tampoco se asigna)
            if n < 80 || n_ring < 50 || (distinto as f64) < p.n2b_min_contrast_frac * n as f64 {
                continue;
            }
            let ratio = n as f64 / ref_area;
            if n < 80
                || ratio <= p.n2b_ratio.0
                || ratio >= p.n2b_ratio.1
                || (inside as f64) < p.n2b_min_inside * n as f64
            {
                continue;
            }
            let pose = pose_of(&m, (bb.0, bb.1));
            if let (Some(max_mm), Some(p1), Some(p0)) = (p.n2b_max_dist_mm, pose, old_pose) {
                if (p1.c.0 - p0.c.0).hypot(p1.c.1 - p0.c.1) * self.canvas.mm_per_px > max_mm {
                    continue;
                }
            }
            raster::l2_normalize(&mut hist);
            let sim = match p.n2b_metric {
                HistMetric::Correl => raster::correl(&ref_hist, &hist),
                HistMetric::Bhattacharyya => raster::bhattacharyya(&ref_hist, &hist),
            };
            // mismo puntaje que N3 sin el término de DINO (toda la zona cambió: en_zona = 1)
            let score = 0.8 * sim + 0.4 - 0.5 * ratio.ln().abs();
            out.push(Cand { mask: raster::place(&m, bb, w, h), score, sim, ratio, pose, from_hist: true });
        }
        out.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(std::cmp::Ordering::Equal));
        out
    }

    fn n3_candidates(
        &mut self,
        o: &TrackedObject,
        rgb: &RgbImage,
        zonas: &Mask,
        cache: &mut HashMap<String, Vec<Detection>>,
        sam_ready: &mut bool,
    ) -> Result<Vec<Cand>, TrackError> {
        let (Some(det), Some(_)) = (self.detector.as_mut(), self.segmenter.as_ref()) else {
            return Ok(vec![]);
        };
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
            if self.objects.values().any(|other| {
                !other.hidden
                    && raster::count(&raster::and(&other.mask, &m)) as f64 / n as f64 > 0.5
                    && !self.retry.contains(&other.id)
                    && raster::frac_inside(&other.mask, zonas) < 0.5
            }) {
                continue;
            }
            let sim = raster::correl(&ref_hist, &raster::hsv_hist(rgb, &m));
            let en_zona = raster::count(&raster::and(zonas, &m)) as f64 / n as f64;
            if en_zona < 0.5 {
                continue;
            }
            let ratio = n as f64 / ref_area as f64;
            let score = d.score as f64 + 0.8 * sim + 0.4 * (en_zona * 2.0).min(1.0) - 0.5 * ratio.max(1e-3).ln().abs();
            out.push(Cand { mask: m, score, sim, ratio, pose: None, from_hist: false });
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
        let props = models::automatic_masks(
            seg.as_mut(),
            rgb,
            &self.area,
            self.params.n5_grid.0,
            self.params.n5_grid.1,
            self.params.n5_min_area,
        )?;
        let area_total = raster::count(&self.area) as f64;
        let props: Vec<Mask> =
            props.into_iter().filter(|p| (p.area as f64) < 0.25 * area_total).map(|p| p.mask).collect();
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
        let old: Vec<(String, String, bool)> =
            self.objects.values().map(|o| (o.id.clone(), o.label.clone(), o.hidden)).collect();
        let mut previous = std::mem::take(&mut self.objects);
        for (nombre, prompt, regs) in groups {
            let mut m = raster::empty(w, h);
            for r in &regs {
                m = raster::or(&m, &props[r - 1]);
            }
            if regs.len() > 1 {
                m = raster::close_sq(&m, 31);
            }
            let m = raster::fill_largest(&m);
            let matched = previous
                .values()
                .filter(|o| o.prompt == prompt || o.label == nombre)
                .filter_map(|o| {
                    let intersection = raster::count(&raster::and(&o.mask, &m)) as f64;
                    let union = raster::count(&raster::or(&o.mask, &m)) as f64;
                    let iou = intersection / union.max(1.0);
                    (iou > 0.5).then_some((o.id.clone(), iou))
                })
                .max_by(|a, b| a.1.total_cmp(&b.1))
                .map(|(id, _)| id);
            let prior = matched.as_ref().and_then(|id| previous.remove(id));
            let id = matched.unwrap_or_else(|| loop {
                self.next_id += 1;
                let id = format!("m{:02}", self.next_id);
                if !old.iter().any(|(old, _, _)| old == &id) && !self.objects.contains_key(&id) {
                    break id;
                }
            });
            let mut o = TrackedObject {
                id: id.clone(),
                label: nombre.clone(),
                prompt,
                mask: m,
                hidden: false,
                seq: prior.as_ref().map_or(0, |o| o.seq),
                aff_mm: prior.as_ref().map_or(IDENTITY, |o| o.aff_mm),
                confidence: 0.8,
                since_ns: prior.as_ref().map_or(frame.t_ns, |o| o.since_ns),
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

/// Centroide y eje principal por momentos de segundo orden de una máscara recortada con origen `origin`.
fn pose_of(m: &Mask, origin: (u32, u32)) -> Option<Pose> {
    let (mut n, mut sx, mut sy, mut sxx, mut syy, mut sxy) = (0.0f64, 0.0, 0.0, 0.0, 0.0, 0.0);
    let w = m.width() as usize;
    for (k, &v) in m.as_raw().iter().enumerate() {
        if v > 0 {
            let (x, y) = ((k % w) as f64, (k / w) as f64);
            n += 1.0;
            sx += x;
            sy += y;
            sxx += x * x;
            syy += y * y;
            sxy += x * y;
        }
    }
    if n < 1.0 {
        return None;
    }
    let (mx, my) = (sx / n, sy / n);
    let (a, b, c) = (sxx / n - mx * mx, sxy / n - mx * my, syy / n - my * my);
    let (t, d) = ((a + c) / 2.0, (((a - c) / 2.0).powi(2) + b * b).sqrt());
    Some(Pose {
        c: (origin.0 as f64 + mx + 0.5, origin.1 as f64 + my + 0.5),
        theta: 0.5 * (2.0 * b).atan2(a - c),
        elong: ((t + d) / (t - d).max(1e-9)).sqrt(),
    })
}

fn iou(a: &Mask, b: &Mask) -> f64 {
    let (mut i, mut u) = (0u64, 0u64);
    for (&x, &y) in a.as_raw().iter().zip(b.as_raw()) {
        i += (x > 0 && y > 0) as u64;
        u += (x > 0 || y > 0) as u64;
    }
    if u == 0 {
        0.0
    } else {
        i as f64 / u as f64
    }
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
