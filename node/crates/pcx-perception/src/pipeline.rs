//! Pipeline de percepción del motor local: foto limpia → objetos en mm de la web app.
//!
//! Cada foto limpia se rectifica (homografía + lente) al lienzo de la superficie y pasa por la cascada, de lo
//! barato a lo caro:
//!
//! - **N0** diferencia contra la foto limpia anterior: qué objetos conocidos hay que volver a mirar.
//! - **N1** correlación en el lugar, con una búsqueda de pocos mm: «sigue ahí» o «se corrió un poco».
//! - **N2** ORB rígido en las regiones sin explicar: movido o girado, conserva la máscara.
//! - **N2b** color y tamaño: empareja uno a uno los objetos perdidos con las regiones nuevas.
//! - **SAM** (MobileSAM, caja sola) solo en las regiones que nadie explicó; el encoder corre una vez por foto.
//!
//! «Sin explicar» es lo que se ve distinto de la mesa vacía ([`table`]) y no es de ningún objeto conocido.
//! Los ids son estables, un objeto se retira recién tras `remove_after` ciclos sin verlo, y ningún umbral está en
//! píxeles: todo se da en mm.

// Código numérico por índices (píxeles, matrices chicas): los bucles con rango se leen mejor que los iteradores.
#[allow(clippy::needless_range_loop, clippy::chunks_exact_to_as_chunks)]
mod img;
mod naming;
#[allow(clippy::needless_range_loop, clippy::chunks_exact_to_as_chunks)]
mod table;

pub use naming::{LlmNamer, Namer};

use crate::pipeline_api::{CalibIn, ObjectOut, PerceptionOut, Perceptor};
use image::{GrayImage, RgbImage};
use img::Region;
use pcx_objects::geom::{self, Affine, Canvas, Homography, Radial, Rectifier};
use pcx_objects::models::Segmenter;
use pcx_objects::orb::{self, Descriptor, Keypoint, OrbParams};
use pcx_objects::raster;
use std::collections::VecDeque;
use std::path::PathBuf;
use std::time::Instant;
use table::Table;

/// Parámetros del pipeline. Las distancias y áreas van en mm de la superficie.
#[derive(Clone, Debug)]
pub struct PipelineConfig {
    /// Resolución del lienzo de trabajo.
    pub mm_per_px: f64,
    /// Tope del lado mayor del lienzo (si la superficie es muy grande se trabaja más grueso).
    pub max_canvas_px: u32,
    /// N0: diferencia de gris contra la foto anterior que cuenta como cambio.
    pub diff_thr: u8,
    /// N0: fracción cambiada de un objeto para volver a mirarlo.
    pub suspect_frac: f64,
    /// Diferencia con la mesa vacía que ya es evidencia clara de objeto.
    pub fg_thr: u8,
    /// Diferencia tenue que se acepta si cuelga de una parte clara (así no se pierde el mango de una cuchara).
    pub fg_thr_low: u8,
    /// Área mínima de un objeto.
    pub min_area_mm2: f64,
    /// Área mínima de evidencia clara dentro de una región.
    pub min_strong_mm2: f64,
    /// Fragmentos más cerca que esto se unen.
    pub close_mm: f64,
    /// Margen alrededor de un objeto conocido que se da por explicado.
    pub explain_margin_mm: f64,
    /// Una región más angosta que esto en todo su largo es un borde, no un objeto.
    pub sliver_mm: f64,
    /// N1: correlación mínima para «sigue igual».
    pub n1_same: f64,
    /// N1: diferencia media (niveles) bajo la cual también es «igual» aunque la correlación sea baja (objeto liso).
    pub n1_mad: f64,
    /// N1: cuánto se busca alrededor.
    pub n1_search_mm: f64,
    pub n2_min_inliers: usize,
    pub n2_ransac_px: f64,
    pub n2_max_scale_dev: f64,
    /// N2b: parecido de color mínimo (Bhattacharyya, 0–1) y razón de áreas admitida.
    pub match_sim: f64,
    pub match_ratio: (f64, f64),
    /// Histéresis: ciclos seguidos sin ver un objeto antes de retirarlo.
    pub remove_after: u32,
    /// Ciclos que se recuerda un objeto retirado para devolverle su id si vuelve.
    pub remember_cycles: u32,
    pub sam_threads: usize,
    /// Tope de cajas por foto.
    pub sam_max_prompts: usize,
    /// Puntaje (IoU predicho) mínimo de una máscara de SAM, y el que alcanza para publicarla de inmediato.
    pub sam_min_score: f32,
    pub sam_confirm_score: f32,
    /// Contexto alrededor de las regiones nuevas y lado mínimo del recorte que ve SAM.
    pub sam_context_mm: f64,
    pub sam_min_side_mm: f64,
    /// Sin SAM: área desde la cual una región nueva se publica de inmediato (las menores esperan otro ciclo).
    pub blob_confirm_mm2: f64,
    /// Un «objeto» que ocupa más que esta fracción de la superficie se descarta.
    pub max_object_frac: f64,
    pub max_polygon_points: usize,
    pub polygon_eps_mm: f64,
    /// Una región nueva se descarta si menos de esta fracción suya es evidencia clara (sombras tenues)…
    pub min_strong_frac: f64,
    /// …o si más de esta fracción es «la misma mesa con otra luz» (sombra o reflejo: cambia el brillo, no el color).
    pub max_light_frac: f64,
    /// Las dos reglas anteriores solo aplican a regiones de borde difuso: en más de esta fracción del contorno la
    /// diferencia con la mesa no corta en seco. Una cuchara gris tiene borde neto y se queda.
    pub soft_edge_ratio: f64,
    /// Con SAM: dos objetos que se tocan forman una sola región; si al adelgazarla esto por lado quedan varios
    /// núcleos, cada núcleo se pregunta por separado.
    pub split_mm: f64,
    /// Primera foto: buscar los objetos que ya están (`true`) o tomarla como mesa de fondo (`false`).
    pub bootstrap: bool,
    /// Nombrar con Claude visión si hay `ANTHROPIC_API_KEY` (en un hilo aparte; nunca frena el ciclo).
    pub name_with_claude: bool,
}

impl Default for PipelineConfig {
    fn default() -> Self {
        PipelineConfig {
            mm_per_px: 1.0,
            max_canvas_px: 1400,
            diff_thr: 18,
            suspect_frac: 0.08,
            fg_thr: 28,
            fg_thr_low: 14,
            min_area_mm2: 90.0,
            min_strong_mm2: 25.0,
            close_mm: 3.0,
            explain_margin_mm: 4.0,
            sliver_mm: 3.0,
            n1_same: 0.85,
            n1_mad: 5.0,
            n1_search_mm: 8.0,
            n2_min_inliers: 10,
            n2_ransac_px: 4.0,
            n2_max_scale_dev: 0.15,
            match_sim: 0.6,
            match_ratio: (0.5, 2.0),
            remove_after: 2,
            remember_cycles: 40,
            sam_threads: 4,
            sam_max_prompts: 40,
            sam_min_score: 0.6,
            sam_confirm_score: 0.75,
            sam_context_mm: 40.0,
            sam_min_side_mm: 220.0,
            blob_confirm_mm2: 400.0,
            max_object_frac: 0.35,
            max_polygon_points: 64,
            polygon_eps_mm: 0.8,
            min_strong_frac: 0.4,
            max_light_frac: 0.7,
            soft_edge_ratio: 0.5,
            split_mm: 6.0,
            bootstrap: true,
            name_with_claude: false,
        }
    }
}

impl PipelineConfig {
    /// Los valores por defecto, con `PCX_MOTOR_NOMBRES=claude` para activar los nombres.
    pub fn from_env() -> PipelineConfig {
        let name = std::env::var("PCX_MOTOR_NOMBRES").map(|v| v.eq_ignore_ascii_case("claude")).unwrap_or(false);
        PipelineConfig { name_with_claude: name, ..Default::default() }
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
enum Life {
    /// Evidencia débil: no se publica hasta verlo en otra foto.
    Tentative,
    Present,
    /// Ciclos seguidos sin verlo (todavía publicado).
    Missing(u32),
    /// Retirado; se recuerda este número de ciclos por si vuelve.
    Retired(u32),
}

struct Track {
    id: String,
    n: u64,
    label: String,
    region: Region,
    /// Recortes de referencia en `origin`: gris desenfocado (N1), gris crudo (N2), soporte de N1 (objeto + borde)
    /// y máscara exacta.
    ref_blur: GrayImage,
    ref_raw: GrayImage,
    ref_support: GrayImage,
    ref_mask: GrayImage,
    origin: (u32, u32),
    hist: Vec<f32>,
    life: Life,
    confidence: f32,
    /// Confianza con la que se vio la última vez (la publicada baja mientras está perdido).
    seen_confidence: f32,
    dirty: bool,
    published: bool,
}

struct Scene {
    table: Table,
    prev: GrayImage,
}

struct Rect {
    key: String,
    rectifier: Rectifier,
    canvas: Canvas,
}

struct Cand {
    region: Region,
    score: f32,
    hist: Vec<f32>,
    weak: bool,
}

struct Stages {
    t: Instant,
    start: Instant,
    list: Vec<(String, f64)>,
}

impl Stages {
    fn new() -> Stages {
        let now = Instant::now();
        Stages { t: now, start: now, list: vec![] }
    }
    fn mark(&mut self, name: &str) {
        let now = Instant::now();
        let ms = (now - self.t).as_secs_f64() * 1e3;
        self.t = now;
        match self.list.iter_mut().find(|(n, _)| n == name) {
            Some(e) => e.1 += ms,
            None => self.list.push((name.to_string(), ms)),
        }
    }
    fn finish(mut self) -> Vec<(String, f64)> {
        let total = self.start.elapsed().as_secs_f64() * 1e3;
        let sam: f64 = self.list.iter().filter(|(n, _)| n.starts_with("sam_")).map(|(_, v)| v).sum();
        self.list.push(("cascade".into(), total - sam));
        self.list.push(("total".into(), total));
        self.list
    }
}

/// El pipeline. Implementa [`Perceptor`].
pub struct Pipeline {
    cfg: PipelineConfig,
    sam: Option<Box<dyn Segmenter>>,
    sam_status: String,
    namer: Option<Box<dyn Namer>>,
    rect: Option<Rect>,
    scene: Option<Scene>,
    tracks: Vec<Track>,
    next_id: u64,
    pending_remove: Vec<String>,
    sam_key: Option<(u64, [u32; 4])>,
    log: Vec<String>,
    last_rgb: Option<RgbImage>,
    last_dist: Option<GrayImage>,
}

impl Pipeline {
    /// Busca los pesos de MobileSAM en `models_dir`, `PCX_MODELS_DIR` o `~/.pcx/models`. Si no están (o el crate
    /// se compiló sin la feature `onnx`), el pipeline trabaja solo con la cascada: ver [`Pipeline::sam_status`].
    pub fn new(models_dir: Option<PathBuf>, cfg: PipelineConfig) -> Pipeline {
        let (sam, sam_status) = load_sam(models_dir, cfg.sam_threads);
        let namer: Option<Box<dyn Namer>> = if cfg.name_with_claude {
            LlmNamer::claude_from_env().map(|n| Box::new(n) as Box<dyn Namer>)
        } else {
            None
        };
        Pipeline {
            cfg,
            sam,
            sam_status,
            namer,
            rect: None,
            scene: None,
            tracks: vec![],
            next_id: 0,
            pending_remove: vec![],
            sam_key: None,
            log: vec![],
            last_rgb: None,
            last_dist: None,
        }
    }

    /// Solo la cascada, sin buscar modelos.
    pub fn cascade_only(cfg: PipelineConfig) -> Pipeline {
        let mut p = Pipeline::new(Some(PathBuf::from("/nonexistent/pcx-sin-modelos")), cfg);
        p.sam = None;
        p.sam_status = "sin SAM: solo la cascada (pedido así)".into();
        p
    }

    /// Reemplaza el segmentador (para tests o para otro export de SAM).
    pub fn with_segmenter(mut self, s: Box<dyn Segmenter>) -> Pipeline {
        self.sam = Some(s);
        self.sam_status = "SAM inyectado".into();
        self
    }

    pub fn with_namer(mut self, n: Box<dyn Namer>) -> Pipeline {
        self.namer = Some(n);
        self
    }

    pub fn has_sam(&self) -> bool {
        self.sam.is_some()
    }

    /// De dónde salió SAM, o por qué no está.
    pub fn sam_status(&self) -> &str {
        &self.sam_status
    }

    /// Bitácora del último ciclo.
    pub fn log(&self) -> &[String] {
        &self.log
    }

    /// Objetos publicados en este momento (estado completo, para un cliente que recién se conecta).
    pub fn snapshot(&self) -> Vec<ObjectOut> {
        let Some(r) = &self.rect else { return vec![] };
        self.tracks
            .iter()
            .filter(|t| t.published && matches!(t.life, Life::Present | Life::Missing(_)))
            .filter_map(|t| self.object_out(t, &r.canvas))
            .collect()
    }

    /// Última foto rectificada con los contornos de los objetos (verde: visto; naranja: perdido; gris: en espera).
    pub fn debug_overlay(&self) -> Option<RgbImage> {
        let mut out = self.last_rgb.clone()?;
        let (w, h) = (out.width(), out.height());
        for t in &self.tracks {
            let color = match t.life {
                Life::Present => [0, 230, 60],
                Life::Missing(_) => [255, 150, 0],
                Life::Tentative => [170, 170, 170],
                Life::Retired(_) => continue,
            };
            let mut full = vec![0u8; (w * h) as usize];
            t.region.paint(&mut full, w as usize, 255u8);
            raster::draw_outline(&mut out, &img::as_gray(full, w as usize, h as usize), color);
            let (cx, cy) = t.region.centroid();
            raster::draw_number(&mut out, cx as i64 - 6, cy as i64 - 5, t.n as usize, 2, color);
        }
        Some(out)
    }

    /// Mapa «qué tan distinto de la mesa vacía» del último ciclo (0–255), para diagnosticar umbrales.
    pub fn debug_distance(&self) -> Option<&GrayImage> {
        self.last_dist.as_ref()
    }

    fn px(&self, canvas: &Canvas, mm: f64) -> u32 {
        (mm / canvas.mm_per_px).round().max(0.0) as u32
    }

    fn px2(&self, canvas: &Canvas, mm2: f64) -> u64 {
        (mm2 / (canvas.mm_per_px * canvas.mm_per_px)).round().max(1.0) as u64
    }

    fn object_out(&self, t: &Track, canvas: &Canvas) -> Option<ObjectOut> {
        let eps = self.cfg.polygon_eps_mm / canvas.mm_per_px;
        let bridge = self.px(canvas, 10.0);
        let poly = img::polygon(&t.region, eps, self.cfg.max_polygon_points, bridge);
        if poly.len() < 3 {
            return None;
        }
        let to_mm = |(u, v): (f64, f64)| {
            let (x, y) = canvas.px_to_mm(u, v);
            [(x * 10.0).round() / 10.0, (y * 10.0).round() / 10.0]
        };
        Some(ObjectOut {
            id: t.id.clone(),
            label: t.label.clone(),
            polygon_mm: poly.into_iter().map(to_mm).collect(),
            center_mm: to_mm(t.region.centroid()),
            confidence: t.confidence,
        })
    }

    fn set_appearance(&self, t: &mut Track, canvas: &Canvas, rgb: &RgbImage, gray: &GrayImage, blur: &GrayImage) {
        let rim = self.px(canvas, 3.0).max(2);
        let support = t.region.dilated(rim, canvas.width, canvas.height);
        let b = (support.x0, support.y0, support.x1(), support.y1());
        t.origin = (b.0, b.1);
        t.ref_blur = raster::crop_gray(blur, b);
        t.ref_raw = raster::crop_gray(gray, b);
        t.ref_support = support.mask_image();
        let mut exact = vec![0u8; (support.w * support.h) as usize];
        for y in 0..t.region.h {
            for x in 0..t.region.w {
                if t.region.m[(y * t.region.w + x) as usize] > 0 {
                    let (sx, sy) = (t.region.x0 + x - b.0, t.region.y0 + y - b.1);
                    exact[(sy * support.w + sx) as usize] = 255;
                }
            }
        }
        t.ref_mask = img::as_gray(exact, support.w as usize, support.h as usize);
        t.hist = histogram(rgb, &t.region);
    }

    /// N1: ¿sigue en su lugar? Devuelve el corrimiento (px) con el que calza, o `None`.
    fn n1(&self, t: &Track, canvas: &Canvas, blur: &GrayImage) -> Option<(i32, i32)> {
        let same = |(z, mad): (f64, f64)| z >= self.cfg.n1_same || (mad <= self.cfg.n1_mad && z >= 0.4);
        let at = |d: (i32, i32)| img::zncc_at(&t.ref_blur, &t.ref_support, t.origin, blur, d);
        let mut best = at((0, 0))?;
        if same(best) {
            return Some((0, 0));
        }
        let reach = self.px(canvas, self.cfg.n1_search_mm) as i32;
        let mut d = (0i32, 0i32);
        for _ in 0..(2 * reach.max(1)) {
            let mut next = None;
            for (ex, ey) in [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, -1), (-1, 1), (1, 1)] {
                let c = (d.0 + ex, d.1 + ey);
                if c.0.abs() > reach || c.1.abs() > reach {
                    continue;
                }
                if let Some(v) = at(c) {
                    if v.0 > best.0 {
                        best = v;
                        next = Some(c);
                    }
                }
            }
            match next {
                Some(c) => d = c,
                None => break,
            }
        }
        (best.0 >= self.cfg.n1_same).then_some(d)
    }

    fn new_track(&mut self, c: Cand, canvas: &Canvas, rgb: &RgbImage, gray: &GrayImage, blur: &GrayImage) -> Track {
        self.next_id += 1;
        let n = self.next_id;
        let empty = GrayImage::new(1, 1);
        let mut t = Track {
            id: format!("obj-{n}"),
            n,
            label: format!("objeto-{n}"),
            region: c.region,
            ref_blur: empty.clone(),
            ref_raw: empty.clone(),
            ref_support: empty.clone(),
            ref_mask: empty,
            origin: (0, 0),
            hist: vec![],
            life: if c.weak { Life::Tentative } else { Life::Present },
            confidence: c.score,
            seen_confidence: c.score,
            dirty: true,
            published: false,
        };
        self.set_appearance(&mut t, canvas, rgb, gray, blur);
        t
    }

    fn ask_name(&mut self, t: &Track, rgb: &RgbImage) {
        if let Some(n) = self.namer.as_mut() {
            let b = (
                t.region.x0.saturating_sub(8),
                t.region.y0.saturating_sub(8),
                (t.region.x1() + 8).min(rgb.width()),
                (t.region.y1() + 8).min(rgb.height()),
            );
            n.request(&t.id, &raster::crop_rgb(rgb, b));
        }
    }

    fn apply_names(&mut self) {
        let Some(n) = self.namer.as_mut() else { return };
        for (id, name) in n.poll() {
            let mut label = name.clone();
            let mut k = 2;
            while self.tracks.iter().any(|t| t.id != id && t.label == label) {
                label = format!("{name} {k}");
                k += 1;
            }
            if let Some(t) = self.tracks.iter_mut().find(|t| t.id == id) {
                if t.label != label {
                    t.label = label;
                    t.dirty = true;
                }
            }
        }
    }

    /// SAM con caja sola sobre cada región sin explicar. El encoder corre una vez por foto (y no corre si la foto
    /// y el recorte son los mismos de la llamada anterior). Lo que una máscara deja sin cubrir vuelve a la cola.
    fn sam_candidates(
        &mut self,
        canvas: &Canvas,
        rgb: &RgbImage,
        valid: &[u8],
        weak: &[u8],
        comps: &[Region],
        st: &mut Stages,
    ) -> Result<Vec<Cand>, String> {
        let (cw, ch) = (canvas.width, canvas.height);
        let ctx = self.px(canvas, self.cfg.sam_context_mm);
        let min_side = self.px(canvas, self.cfg.sam_min_side_mm);
        let (mut x0, mut y0, mut x1, mut y1) = (u32::MAX, u32::MAX, 0u32, 0u32);
        for c in comps {
            (x0, y0, x1, y1) = (x0.min(c.x0), y0.min(c.y0), x1.max(c.x1()), y1.max(c.y1()));
        }
        let grow = |a: u32, b: u32, lim: u32| {
            let extra = ctx.max(min_side.saturating_sub(b - a).div_ceil(2));
            (a.saturating_sub(extra), (b + extra).min(lim))
        };
        let ((x0, x1), (y0, y1)) = (grow(x0, x1, cw), grow(y0, y1, ch));
        let (rw, rh) = ((x1 - x0) as usize, (y1 - y0) as usize);
        let key = (checksum(rgb), [x0, y0, x1, y1]);
        let min_area = self.px2(canvas, self.cfg.min_area_mm2);
        let min_strong = self.px2(canvas, self.cfg.min_strong_mm2);
        let pad = self.px(canvas, 2.0).max(2);
        let margin = self.px(canvas, self.cfg.explain_margin_mm);
        let gap = self.px(canvas, 4.0).max(1);
        let core_r = self.px(canvas, self.cfg.split_mm);
        let sliver = (self.px(canvas, self.cfg.sliver_mm) / 2).max(1);
        let max_area = (self.cfg.max_object_frac * (cw as f64) * (ch as f64)) as u64;
        let (min_score, confirm, max_prompts) =
            (self.cfg.sam_min_score, self.cfg.sam_confirm_score, self.cfg.sam_max_prompts);
        let seg = self.sam.as_mut().ok_or("sin SAM")?;
        if self.sam_key != Some(key) {
            self.sam_key = None;
            seg.set_image(&raster::crop_rgb(rgb, (x0, y0, x1, y1))).map_err(|e| format!("SAM encoder: {e}"))?;
            self.sam_key = Some(key);
        }
        st.mark("sam_encoder");
        let mut queue: VecDeque<(Region, u32)> = comps.iter().cloned().map(|c| (c, 0)).collect();
        let mut cands: Vec<Cand> = vec![];
        let mut prompts = 0;
        while let Some((c, depth)) = queue.pop_front() {
            // lo que ya cubrió una máscara anterior no se vuelve a preguntar
            if cands.iter().any(|k| k.region.inter(&c) * 10 >= c.area * 6) {
                continue;
            }
            if prompts >= max_prompts {
                self.log.push(format!("SAM: tope de {max_prompts} cajas; quedan {} regiones", queue.len() + 1));
                break;
            }
            let b = [
                c.x0.saturating_sub(pad).max(x0) - x0,
                c.y0.saturating_sub(pad).max(y0) - y0,
                (c.x1() + pad).min(x1) - x0,
                (c.y1() + pad).min(y1) - y0,
            ]
            .map(|v| v as f32);
            let out = seg.predict(&[(b[0], b[1], 2.0), (b[2], b[3], 3.0)]).map_err(|e| format!("SAM decoder: {e}"))?;
            prompts += 1;
            if out.logits.len() < rw * rh {
                return Err("SAM devolvió una máscara de otro tamaño".into());
            }
            let mut m = vec![0u8; rw * rh];
            for y in 0..rh {
                for x in 0..rw {
                    let i = y * rw + x;
                    if out.logits[i] > 0.0 && valid[(y0 as usize + y) * cw as usize + x0 as usize + x] > 0 {
                        m[i] = 255;
                    }
                }
            }
            let mask = Region::from_mask(&m, rw, rh, x0, y0).and_then(|r| {
                // la parte mayor, más las partes grandes pegadas a ella (un cable puede partir el objeto en dos);
                // lo demás son motas o un vecino, que tendrá su propia caja
                let parts = r.parts(1);
                let big = parts.first()?.clone();
                let near = big.dilated(gap, cw, ch);
                let mut keep = vec![0u8; (cw * ch) as usize];
                for p in parts.iter().filter(|p| p.area * 100 >= big.area * 15 && p.inter(&near) > 0) {
                    p.paint(&mut keep, cw as usize, 255u8);
                }
                Region::from_mask(&keep, cw as usize, ch as usize, 0, 0)
            });
            // una máscara no puede quedarse con lo que ya es de otra (primero se preguntan los núcleos chicos,
            // así dos objetos que se tocan no terminan fundidos), ni salirse mucho de la caja que se le dio
            let mask = mask.and_then(|r| {
                let full = r.area;
                let mut r = r;
                for k in &cands {
                    if k.region.inter(&r) > 0 {
                        r = r.minus(&k.region)?;
                    }
                }
                let r = r.parts(1).into_iter().next()?;
                let grow = pad + (c.w.max(c.h) * 15 / 100);
                let inside = r.inter(&Region::from_mask(&[255u8], 1, 1, 0, 0)?.boxed(
                    c.x0.saturating_sub(grow),
                    c.y0.saturating_sub(grow),
                    (c.x1() + grow).min(cw),
                    (c.y1() + grow).min(ch),
                ));
                (r.area * 10 >= full * 4 && inside * 100 >= r.area * 65).then_some(r)
            });
            let accepted = mask.filter(|r| {
                // la máscara tiene que ser algo que no es mesa, y tiene que ser lo que se le preguntó
                out.score.is_finite()
                    && out.score >= min_score
                    && r.area >= min_area
                    && r.area <= max_area
                    && r.count_in(weak, cw as usize) * 2 >= r.area
                    && r.inter(&c) * 10 >= c.area * 3
            });
            let Some(r) = accepted else {
                self.log.push(format!(
                    "SAM: región de {} px en {:.0?} descartada (puntaje {:.2})",
                    c.area,
                    c.centroid(),
                    out.score
                ));
                continue;
            };
            // ¿la máscara junta dos objetos que apenas se tocan? Si al adelgazarla se parte, se pregunta por cada
            // núcleo (y por el conjunto al final, que se queda con lo que los núcleos no tomen)
            if depth < 2 {
                let cores = split_cores(r.clone(), core_r, min_area * 3, cw, ch);
                if cores.len() > 1 {
                    for k in cores.into_iter().rev() {
                        queue.push_front((k, depth + 2));
                    }
                    continue;
                }
            }
            if depth < 3 {
                if let Some(rest) = c.minus(&r.dilated(margin, cw, ch)) {
                    for p in rest.parts(min_strong) {
                        if p.survives_erosion(sliver) {
                            queue.push_back((p, depth + 1));
                        }
                    }
                }
            }
            let hist = histogram(rgb, &r);
            cands.push(Cand { region: r, score: out.score.clamp(0.0, 1.0), hist, weak: out.score < confirm });
        }
        st.mark("sam_decoder");
        // deduplicar por IoU de máscara: gana la de mejor puntaje
        cands.sort_by(|a, b| b.score.total_cmp(&a.score));
        let mut kept: Vec<Cand> = vec![];
        for c in cands {
            let dup = kept.iter().any(|k| {
                let i = k.region.inter(&c.region);
                c.region.iou(&k.region) > 0.5 || i * 10 > c.region.area * 6
            });
            if !dup {
                kept.push(c);
            }
        }
        self.log.push(format!("SAM: {prompts} cajas → {} máscaras", kept.len()));
        Ok(kept)
    }

    fn cycle(
        &mut self,
        canvas: &Canvas,
        rgb: &RgbImage,
        valid: &GrayImage,
        st: &mut Stages,
    ) -> Result<Vec<String>, String> {
        let (cw, ch) = (canvas.width, canvas.height);
        let (w, h) = (cw as usize, ch as usize);
        let vraw = valid.as_raw();
        let gray = img::gray(rgb);
        let blur = img::blur5(&gray);
        let first = self.scene.is_none();
        let cell = self.px(canvas, 4.0).max(1) as usize;
        let mut scene = match self.scene.take() {
            Some(s) => s,
            None if !self.cfg.bootstrap => {
                self.scene = Some(Scene { table: Table::from_frame(rgb), prev: blur });
                self.log.push("primera foto: se toma como mesa de fondo".into());
                return Ok(vec![]);
            }
            None => {
                let radius = (48.0 / (cell as f64 * canvas.mm_per_px)).round().max(2.0) as usize;
                let table = Table::estimate(rgb, vraw, cell, self.cfg.fg_thr as f32, radius);
                Scene { table, prev: blur.clone() }
            }
        };
        let min_area = self.px2(canvas, self.cfg.min_area_mm2);
        let margin = self.px(canvas, self.cfg.explain_margin_mm);
        let mut removed: Vec<String> = vec![];
        let mut tracks = std::mem::take(&mut self.tracks);

        // ---- N0: qué cambió desde la foto anterior ----
        let changed: Vec<u8> = if first {
            vraw.clone()
        } else {
            let g = img::gain(blur.as_raw(), scene.prev.as_raw(), |i| vraw[i] > 0, 11);
            let lut = img::gain_lut(g);
            let thr = self.cfg.diff_thr;
            blur.as_raw()
                .iter()
                .zip(scene.prev.as_raw())
                .zip(vraw)
                .map(|((&a, &b), &v)| if v > 0 && a.abs_diff(lut[b as usize]) > thr { 255 } else { 0 })
                .collect()
        };
        let changed_px = changed.iter().filter(|&&v| v > 0).count();
        st.mark("n0");

        // ---- N1: los sospechosos y los perdidos, ¿siguen en su lugar? ----
        let mut lost: Vec<usize> = vec![];
        let mut confirmed: Vec<usize> = vec![];
        for (k, t) in tracks.iter_mut().enumerate() {
            if matches!(t.life, Life::Retired(_)) {
                continue;
            }
            let n = t.region.count_in(&changed, w);
            let suspect = !matches!(t.life, Life::Present)
                || n as f64 >= self.cfg.suspect_frac * t.region.area as f64
                || n >= 8 * min_area;
            if !suspect {
                continue;
            }
            match self.n1(t, canvas, &blur) {
                Some(d) => {
                    if d != (0, 0) {
                        if let Some(r) = t.region.shifted(d.0, d.1, cw, ch) {
                            t.region = r;
                            t.dirty = true;
                        }
                    }
                    if t.life != Life::Present {
                        if t.life == Life::Tentative {
                            confirmed.push(k);
                        }
                        t.life = Life::Present;
                        t.confidence = t.seen_confidence;
                        t.dirty = true;
                    }
                    self.log.push(format!("N1 {}: sigue ahí (corrido {d:?} px)", t.id));
                    self.set_appearance(t, canvas, rgb, &gray, &blur);
                }
                None => lost.push(k),
            }
        }
        for &k in &confirmed {
            self.ask_name(&tracks[k], rgb);
        }
        st.mark("n1");

        // ---- lo que se ve distinto de la mesa y nadie explica ----
        let mut expl = vec![0u8; w * h];
        for (k, t) in tracks.iter().enumerate() {
            if !matches!(t.life, Life::Retired(_)) && !lost.contains(&k) {
                t.region.dilated(margin, cw, ch).paint(&mut expl, w, 255u8);
            }
        }
        let gains = if first {
            [1.0; 3]
        } else {
            let ok: Vec<u8> = vraw.iter().zip(&expl).map(|(&v, &e)| if v > 0 && e == 0 { 255 } else { 0 }).collect();
            scene.table.gains(rgb, &ok)
        };
        let dist = img::box3(&scene.table.distance(rgb, gains), w, h);
        self.last_dist = Some(img::as_gray(dist.clone(), w, h));
        let (lo, hi) = (self.cfg.fg_thr_low, self.cfg.fg_thr);
        let weak: Vec<u8> = dist.iter().zip(vraw).map(|(&d, &v)| if v > 0 && d >= lo { 255 } else { 0 }).collect();
        let strong: Vec<u8> = dist.iter().zip(vraw).map(|(&d, &v)| if v > 0 && d >= hi { 255 } else { 0 }).collect();
        let sliver = (self.px(canvas, self.cfg.sliver_mm) / 2).max(1);
        let min_strong = self.px2(canvas, self.cfg.min_strong_mm2);
        let close_r = self.px(canvas, self.cfg.close_mm) as usize;
        let free = |m: &[u8]| -> Vec<u8> {
            let open: Vec<u8> = m.iter().zip(&expl).map(|(&f, &e)| if f > 0 && e == 0 { 255 } else { 0 }).collect();
            let closed = img::close(&open, w, h, close_r);
            closed
                .iter()
                .zip(&expl)
                .zip(vraw)
                .map(|((&c, &e), &v)| if c > 0 && e == 0 && v > 0 { 255 } else { 0 })
                .collect()
        };
        // Con SAM basta la evidencia clara para saber dónde mirar (y así dos objetos vecinos no se funden en una
        // región); sin SAM la región es la máscara, y se le deja crecer por lo tenue.
        let found: Vec<Region> = if self.sam.is_some() {
            let m = free(&strong);
            let core_r = self.px(canvas, self.cfg.split_mm);
            let mut whole: Vec<Region> =
                raster::blobs(&img::as_gray(m, w, h), min_strong).iter().map(Region::from_blob).collect();
            whole.sort_by_key(|c| std::cmp::Reverse(c.area));
            whole.into_iter().flat_map(|c| split_cores(c, core_r, min_strong, cw, ch)).collect()
        } else {
            img::hysteresis_regions(&free(&weak), &strong, w, h, min_area, min_strong)
                .into_iter()
                .filter(|c| c.survives_erosion(sliver))
                .collect()
        };
        let mut comps: Vec<Region> = found
            .into_iter()
            // solo donde hay evidencia nueva: algo cambió ahí, o es el lugar de un objeto perdido
            .filter(|c| {
                first
                    || c.count_in(&changed, w) >= (c.area * 3 / 100).max(8)
                    || lost.iter().any(|&k| tracks[k].region.inter(c) > 0)
            })
            .collect();
        if !first {
            // una región que se ve igual que la mesa que la rodea es mesa destapada (o una mala estimación)
            let blocked: Vec<u8> = weak.iter().zip(&expl).map(|(&f, &e)| f | e).collect();
            let ring = self.px(canvas, 8.0).max(3);
            comps.retain(|c| {
                let like = table_like(rgb, c, &blocked, vraw, ring, hi, cw, ch);
                if like >= 0.7 {
                    scene.table.replace(rgb, c);
                    self.log.push(format!("región de {} px: es mesa destapada", c.area));
                }
                like < 0.7
            });
        }
        if self.sam.is_none() {
            comps.sort_by_key(|c| std::cmp::Reverse(c.area));
        }
        self.log.push(format!(
            "N0: {changed_px} px cambiaron · {} por revisar · {} perdidos · {} regiones sin explicar",
            lost.len() + confirmed.len(),
            lost.len(),
            comps.len()
        ));
        st.mark("regions");

        // ---- N2: ORB rígido para los perdidos con textura ----
        if !lost.is_empty() && !comps.is_empty() {
            let cur = current_keypoints(&gray, &comps, cw, ch);
            let mut still = vec![];
            for &k in &lost {
                match self.n2(&tracks[k], &cur, &weak, cw, ch) {
                    Some((r, inliers)) => {
                        let grown = r.dilated(margin, cw, ch);
                        comps = comps
                            .iter()
                            .filter_map(|c| c.minus(&grown))
                            .flat_map(|c| c.parts(min_area))
                            .filter(|c| c.survives_erosion(sliver))
                            .collect();
                        let t = &mut tracks[k];
                        self.log.push(format!("N2 {}: movido, rígido ({inliers} puntos)", t.id));
                        t.region = r;
                        t.confidence = (0.5 + inliers as f32 / 60.0).min(1.0);
                        t.seen_confidence = t.confidence;
                        if t.life == Life::Tentative {
                            confirmed.push(k);
                        }
                        t.life = Life::Present;
                        t.dirty = true;
                        self.set_appearance(t, canvas, rgb, &gray, &blur);
                    }
                    None => still.push(k),
                }
            }
            lost = still;
        }
        st.mark("n2");

        // ---- regiones nuevas: SAM (caja sola) o, sin modelos, el contorno de la región ----
        let mut failed = false;
        let blob_confirm = self.px2(canvas, self.cfg.blob_confirm_mm2);
        let blobs = |comps: &[Region]| -> Vec<Cand> {
            comps
                .iter()
                .map(|c| Cand { region: c.clone(), score: 0.5, hist: histogram(rgb, c), weak: c.area < blob_confirm })
                .collect()
        };
        let mut cands: Vec<Cand> = if comps.is_empty() {
            vec![]
        } else if self.sam.is_some() {
            match self.sam_candidates(canvas, rgb, vraw, &weak, &comps, st) {
                Ok(c) => c,
                Err(e) => {
                    // un fallo del modelo no es «no lo vi»: este ciclo no cuenta para retirar a nadie
                    self.log.push(format!("{e}; sigo con el contorno de las regiones"));
                    failed = true;
                    blobs(&comps)
                }
            }
        } else {
            blobs(&comps)
        };
        // una máscara que cae sobre un objeto que sigue en su lugar es ese mismo objeto: no se duplica
        cands.retain(|c| {
            !tracks.iter().enumerate().any(|(k, t)| {
                !matches!(t.life, Life::Retired(_))
                    && !lost.contains(&k)
                    && t.region.inter(&c.region) * 2 > c.region.area
            })
        });

        // sombras y reflejos: poca evidencia clara, o el mismo color de la mesa con otra luz
        let mut claimed = expl.clone();
        for c in &cands {
            c.region.paint(&mut claimed, w, 255u8);
        }
        cands.retain(|c| {
            let strong_frac = c.region.count_in(&strong, w) as f64 / c.region.area as f64;
            let light = light_frac(rgb, &scene.table, gains, &c.region);
            let edge = edge_softness(&dist, &claimed, &c.region, cw, ch);
            let dim = strong_frac < self.cfg.min_strong_frac || light > self.cfg.max_light_frac;
            let keep = !(dim && edge > self.cfg.soft_edge_ratio);
            if !keep {
                self.log.push(format!(
                    "región de {} px en {:.0?} descartada: sombra o reflejo (evidencia {strong_frac:.2}, luz {light:.2}, borde {edge:.2})",
                    c.region.area,
                    c.region.centroid()
                ));
            }
            keep
        });

        // ---- N2b: perdidos y recordados ↔ candidatos, uno a uno, por color, tamaño y lugar ----
        let mut pairs: Vec<(f64, usize, usize)> = vec![];
        for (k, t) in tracks.iter().enumerate() {
            let retired = matches!(t.life, Life::Retired(_));
            if !retired && !lost.contains(&k) {
                continue;
            }
            for (j, c) in cands.iter().enumerate() {
                let sim = raster::bhattacharyya(&t.hist, &c.hist);
                let ratio = c.region.area as f64 / t.region.area.max(1) as f64;
                let home = if retired { 0.0 } else { t.region.iou(&c.region) };
                let (a, b) = (t.region.centroid(), c.region.centroid());
                let near = (-(a.0 - b.0).hypot(a.1 - b.1) * canvas.mm_per_px / 150.0).exp();
                let looks = if retired {
                    sim >= self.cfg.match_sim.max(0.75) && (0.7..1.4).contains(&ratio)
                } else {
                    sim >= self.cfg.match_sim && self.cfg.match_ratio.0 < ratio && ratio < self.cfg.match_ratio.1
                };
                if looks || home >= 0.4 {
                    pairs.push((sim - 0.5 * ratio.ln().abs() + home + 0.15 * near, k, j));
                }
            }
        }
        pairs.sort_by(|a, b| b.0.total_cmp(&a.0));
        let mut taken = vec![false; cands.len()];
        let mut found = vec![false; tracks.len()];
        for &(score, k, j) in &pairs {
            if taken[j] || found[k] {
                continue;
            }
            (taken[j], found[k]) = (true, true);
            let c = &cands[j];
            let t = &mut tracks[k];
            self.log.push(format!("N2b {}: es la región de {} px (puntaje {score:.2})", t.id, c.region.area));
            t.region = c.region.clone();
            t.confidence = c.score;
            t.seen_confidence = c.score;
            if t.life == Life::Tentative {
                confirmed.push(k);
            }
            t.life = Life::Present;
            t.dirty = true;
            self.set_appearance(t, canvas, rgb, &gray, &blur);
        }
        st.mark("n2b");

        // ---- histéresis: los que no aparecieron ----
        for &k in &lost {
            if found[k] || failed {
                continue;
            }
            let t = &mut tracks[k];
            let misses = match t.life {
                Life::Missing(n) => n + 1,
                Life::Tentative => u32::MAX,
                _ => 1,
            };
            if misses >= self.cfg.remove_after {
                self.log.push(format!("{}: {misses} ciclos sin verlo → se retira", t.id));
                if t.published {
                    removed.push(t.id.clone());
                }
                t.published = false;
                t.life = if misses == u32::MAX { Life::Retired(0) } else { Life::Retired(self.cfg.remember_cycles) };
            } else {
                self.log.push(format!("{}: no lo veo ({misses} de {})", t.id, self.cfg.remove_after));
                t.life = Life::Missing(misses);
                t.confidence = (t.seen_confidence * 0.5f32.powi(misses as i32)).max(0.05);
                t.dirty = true;
            }
        }
        for (k, t) in tracks.iter_mut().enumerate() {
            if let Life::Retired(n) = t.life {
                if !lost.contains(&k) {
                    t.life = Life::Retired(n.saturating_sub(1));
                }
            }
        }
        tracks.retain(|t| t.life != Life::Retired(0));

        // ---- objetos nuevos ----
        for (j, c) in cands.into_iter().enumerate() {
            if taken[j] {
                continue;
            }
            let t = self.new_track(c, canvas, rgb, &gray, &blur);
            self.log.push(format!(
                "{}: nuevo, {} px{}",
                t.id,
                t.region.area,
                if t.life == Life::Tentative { " (en espera)" } else { "" }
            ));
            if t.life == Life::Present {
                self.ask_name(&t, rgb);
            }
            tracks.push(t);
        }

        // ---- la mesa aprende donde no hay nada ----
        let mut skip = weak;
        for t in tracks.iter().filter(|t| !matches!(t.life, Life::Retired(_))) {
            t.region.dilated(margin, cw, ch).paint(&mut skip, w, 255u8);
        }
        for (s, &v) in skip.iter_mut().zip(vraw) {
            if v == 0 {
                *s = 255;
            }
        }
        scene.table.learn(rgb, &img::dilate(&skip, w, h, 2), if first { 256 } else { 128 });
        scene.prev = blur;
        self.scene = Some(scene);
        self.tracks = tracks;
        st.mark("table");
        Ok(removed)
    }

    /// N2: ORB de la referencia contra los puntos de las regiones sin explicar → similitud por RANSAC.
    fn n2(&self, t: &Track, cur: &CurKeypoints, weak: &[u8], cw: u32, ch: u32) -> Option<(Region, usize)> {
        let params = OrbParams::default();
        let (k1, d1) = orb::detect_and_compute(&t.ref_raw, Some(&t.ref_mask), &params);
        if k1.len() < 10 || cur.kp.len() < 10 {
            return None;
        }
        let ms = orb::match_ratio(&d1, &cur.desc, 0.8);
        if ms.len() < self.cfg.n2_min_inliers {
            return None;
        }
        let p1: Vec<(f64, f64)> =
            ms.iter().map(|&(i, _)| (k1[i].x as f64 + t.origin.0 as f64, k1[i].y as f64 + t.origin.1 as f64)).collect();
        let p2: Vec<(f64, f64)> = ms
            .iter()
            .map(|&(_, j)| (cur.kp[j].x as f64 + cur.off.0 as f64, cur.kp[j].y as f64 + cur.off.1 as f64))
            .collect();
        let (a, inl) = geom::ransac_similarity(&p1, &p2, self.cfg.n2_ransac_px, 1500)?;
        let n = inl.iter().filter(|&&b| b).count();
        if n < self.cfg.n2_min_inliers || (geom::scale_of(&a) - 1.0).abs() > self.cfg.n2_max_scale_dev {
            return None;
        }
        let r = warp(&t.region, &a, cw, ch)?;
        // la máscara llevada al lugar nuevo tiene que caer sobre algo que no es mesa
        (r.count_in(weak, cw as usize) * 10 >= r.area * 6).then_some((r, n))
    }
}

struct CurKeypoints {
    kp: Vec<Keypoint>,
    desc: Vec<Descriptor>,
    off: (u32, u32),
}

/// Puntos ORB de la foto, solo en la caja de las regiones sin explicar.
fn current_keypoints(gray: &GrayImage, comps: &[Region], cw: u32, ch: u32) -> CurKeypoints {
    let (mut x0, mut y0, mut x1, mut y1) = (u32::MAX, u32::MAX, 0u32, 0u32);
    for c in comps {
        (x0, y0, x1, y1) = (x0.min(c.x0), y0.min(c.y0), x1.max(c.x1()), y1.max(c.y1()));
    }
    let grow = |a: u32, b: u32, lim: u32| {
        let extra = 24u32.max(128u32.saturating_sub(b - a).div_ceil(2));
        (a.saturating_sub(extra), (b + extra).min(lim))
    };
    let ((x0, x1), (y0, y1)) = (grow(x0, x1, cw), grow(y0, y1, ch));
    let (w, h) = ((x1 - x0) as usize, (y1 - y0) as usize);
    let mut mask = vec![0u8; w * h];
    for c in comps {
        for y in 0..c.h {
            for x in 0..c.w {
                if c.m[(y * c.w + x) as usize] > 0 {
                    mask[((c.y0 + y - y0) as usize) * w + (c.x0 + x - x0) as usize] = 255;
                }
            }
        }
    }
    let mask = img::as_gray(img::dilate(&mask, w, h, 8), w, h);
    let crop = raster::crop_gray(gray, (x0, y0, x1, y1));
    let (kp, desc) = orb::detect_and_compute(&crop, Some(&mask), &OrbParams::default());
    CurKeypoints { kp, desc, off: (x0, y0) }
}

/// Lleva una región por una afín del lienzo (vecino más cercano).
fn warp(r: &Region, a: &Affine, cw: u32, ch: u32) -> Option<Region> {
    let inv = geom::invert(a)?;
    let corners = [(r.x0, r.y0), (r.x1(), r.y0), (r.x0, r.y1()), (r.x1(), r.y1())]
        .map(|(x, y)| geom::apply(a, (x as f64, y as f64)));
    let lo = |f: fn(&(f64, f64)) -> f64| corners.iter().map(f).fold(f64::MAX, f64::min).floor().max(0.0) as u32;
    let hi = |f: fn(&(f64, f64)) -> f64, lim: u32| {
        (corners.iter().map(f).fold(f64::MIN, f64::max).ceil().max(0.0) as u32 + 1).min(lim)
    };
    let (x0, y0, x1, y1) = (lo(|p| p.0), lo(|p| p.1), hi(|p| p.0, cw), hi(|p| p.1, ch));
    if x0 >= x1 || y0 >= y1 {
        return None;
    }
    let (w, h) = ((x1 - x0) as usize, (y1 - y0) as usize);
    let mut m = vec![0u8; w * h];
    for y in 0..h {
        for x in 0..w {
            let (sx, sy) = geom::apply(&inv, ((x0 as usize + x) as f64 + 0.5, (y0 as usize + y) as f64 + 0.5));
            if sx >= 0.0 && sy >= 0.0 && r.at(sx as u32, sy as u32) {
                m[y * w + x] = 255;
            }
        }
    }
    Region::from_mask(&m, w, h, x0, y0)
}

/// Histograma de color de la región (tono × saturación + 4 casilleros sin color), normalizado.
fn histogram(rgb: &RgbImage, r: &Region) -> Vec<f32> {
    let mut lut = raster::BinLut::default();
    let mut hist = vec![0f32; raster::COLOR_BINS];
    let px = rgb.as_raw();
    r.for_each(rgb.width() as usize, |i| hist[lut.bin(&px[i * 3..i * 3 + 3])] += 1.0);
    raster::l2_normalize(&mut hist);
    hist
}

/// Fracción de la región que se ve igual (± `thr`) que la mediana de la mesa libre que la rodea. 0 si no hay
/// mesa libre alrededor con qué comparar.
#[allow(clippy::too_many_arguments)]
fn table_like(rgb: &RgbImage, r: &Region, blocked: &[u8], valid: &[u8], ring: u32, thr: u8, cw: u32, ch: u32) -> f64 {
    let grown = r.dilated(ring, cw, ch);
    let px = rgb.as_raw();
    let mut hist = [[0u32; 256]; 3];
    let mut n = 0u32;
    grown.for_each(cw as usize, |i| {
        if blocked[i] == 0 && valid[i] > 0 {
            for c in 0..3 {
                hist[c][px[i * 3 + c] as usize] += 1;
            }
            n += 1;
        }
    });
    if n < 30 {
        return 0.0;
    }
    let med = hist.map(|h| {
        let (mut acc, mut v) = (0u32, 0usize);
        while v < 255 && acc + h[v] <= n / 2 {
            acc += h[v];
            v += 1;
        }
        v as u8
    });
    let mut same = 0u64;
    r.for_each(cw as usize, |i| {
        let p = &px[i * 3..i * 3 + 3];
        same += (p[0].abs_diff(med[0]) <= thr && p[1].abs_diff(med[1]) <= thr && p[2].abs_diff(med[2]) <= thr) as u64;
    });
    same as f64 / r.area.max(1) as f64
}

/// Fracción de la región que es «la mesa con otra luz»: los tres canales cambian en la misma proporción (entre
/// la mitad y el doble), como en una sombra o un reflejo suave. Un objeto cambia el color, no solo el brillo.
fn light_frac(rgb: &RgbImage, table: &Table, gains: [f32; 3], r: &Region) -> f64 {
    let px = rgb.as_raw();
    let mut n = 0u64;
    r.for_each(table.w, |i| {
        let mut lo = f32::MAX;
        let mut hi = 0f32;
        for c in 0..3 {
            let q = (px[i * 3 + c] as f32 + 4.0) / (table.rgb[i * 3 + c] as f32 * gains[c] + 4.0);
            lo = lo.min(q);
            hi = hi.max(q);
        }
        n += (lo > 0.5 && hi < 2.0 && hi - lo < 0.10) as u64;
    });
    n as f64 / r.area.max(1) as f64
}

/// Qué tan difuso es el borde: fracción del anillo de afuera (de 2 a 5 px, sin contar lo que es de otro objeto)
/// donde la diferencia con la mesa sigue siendo más de la mitad de la de adentro. Un objeto corta en seco (≈ 0);
/// una sombra o un reflejo se van apagando de a poco (→ 1). Sin anillo libre que mirar, se da por neto.
fn edge_softness(dist: &[u8], claimed: &[u8], r: &Region, cw: u32, ch: u32) -> f64 {
    let (mut s, mut n) = (0u64, 0u64);
    r.for_each(cw as usize, |i| {
        s += dist[i] as u64;
        n += 1;
    });
    let inside = s as f64 / n.max(1) as f64;
    let (inner, outer) = (r.dilated(2, cw, ch), r.dilated(5, cw, ch));
    let (mut soft, mut total) = (0u64, 0u64);
    for y in 0..outer.h {
        for x in 0..outer.w {
            let (gx, gy) = (outer.x0 + x, outer.y0 + y);
            let i = (gy * cw + gx) as usize;
            if outer.m[(y * outer.w + x) as usize] > 0 && !inner.at(gx, gy) && claimed[i] == 0 {
                total += 1;
                soft += (dist[i] as f64 > 0.5 * inside) as u64;
            }
        }
    }
    if total * 5 < outer.area - inner.area {
        return 0.0;
    }
    soft as f64 / total as f64
}

/// Si la región, adelgazada `r` px, se separa en varios núcleos, devuelve los núcleos (engordados de vuelta);
/// si no, la región tal cual.
fn split_cores(c: Region, r: u32, min_area: u64, cw: u32, ch: u32) -> Vec<Region> {
    if r == 0 || c.w <= 4 * r || c.h <= 4 * r {
        return vec![c];
    }
    let (w, h) = (c.w as usize + 2, c.h as usize + 2);
    let mut m = vec![0u8; w * h];
    for y in 0..c.h as usize {
        m[(y + 1) * w + 1..(y + 1) * w + 1 + c.w as usize]
            .copy_from_slice(&c.m[y * c.w as usize..(y + 1) * c.w as usize]);
    }
    let eroded = img::erode(&m, w, h, r as usize);
    let Some(core) = Region::from_mask(&eroded, w, h, c.x0.saturating_sub(1), c.y0.saturating_sub(1)) else {
        return vec![c];
    };
    let cores = core.parts(min_area.max(4));
    if cores.len() < 2 {
        return vec![c];
    }
    // primero los núcleos, del más chico al más grande; al final la región entera, por lo que ellos no cubran
    let mut out: Vec<Region> = cores.iter().rev().map(|k| k.dilated(r, cw, ch)).collect();
    out.push(c);
    out
}

/// Huella barata de la foto (una muestra de bytes): para no repetir el encoder sobre la misma foto.
fn checksum(rgb: &RgbImage) -> u64 {
    let mut h = 0xcbf2_9ce4_8422_2325u64 ^ ((rgb.width() as u64) << 32 | rgb.height() as u64);
    for &b in rgb.as_raw().iter().step_by(101) {
        h = (h ^ b as u64).wrapping_mul(0x0000_0100_0000_01b3);
    }
    h
}

fn models_dirs(explicit: Option<PathBuf>) -> Vec<PathBuf> {
    let mut dirs: Vec<PathBuf> = explicit.into_iter().collect();
    if let Ok(d) = std::env::var("PCX_MODELS_DIR") {
        if !d.is_empty() {
            dirs.push(PathBuf::from(d));
        }
    }
    if let Some(home) = std::env::var_os("HOME").or_else(|| std::env::var_os("USERPROFILE")) {
        dirs.push(PathBuf::from(home).join(".pcx").join("models"));
    }
    dirs
}

/// Archivos de MobileSAM (export de Acly, los mismos de `engine/playground/appearance_prepare.py`).
pub const SAM_FILES: [&str; 2] = ["mobile_sam_image_encoder.onnx", "sam_mask_decoder_single.onnx"];

fn load_sam(explicit: Option<PathBuf>, threads: usize) -> (Option<Box<dyn Segmenter>>, String) {
    let dirs = models_dirs(explicit);
    let Some(dir) = dirs.iter().find(|d| SAM_FILES.iter().all(|f| d.join(f).is_file())) else {
        let looked: Vec<String> = dirs.iter().map(|d| d.display().to_string()).collect();
        return (
            None,
            format!("sin SAM: solo la cascada (no encontré {} en {})", SAM_FILES.join(" + "), looked.join(", ")),
        );
    };
    load_sam_from(dir, threads)
}

#[cfg(feature = "onnx")]
fn load_sam_from(dir: &std::path::Path, threads: usize) -> (Option<Box<dyn Segmenter>>, String) {
    match pcx_objects::onnx::MobileSam::from_dir(dir, threads.max(1)) {
        Ok(s) => (Some(Box::new(s)), format!("MobileSAM de {}", dir.display())),
        Err(e) => (None, format!("sin SAM: solo la cascada (no cargó desde {}: {e})", dir.display())),
    }
}

#[cfg(not(feature = "onnx"))]
fn load_sam_from(dir: &std::path::Path, _threads: usize) -> (Option<Box<dyn Segmenter>>, String) {
    (
        None,
        format!("sin SAM: solo la cascada (hay pesos en {}, pero falta compilar con --features onnx)", dir.display()),
    )
}

/// Arma el rectificador de una calibración de la web app para fotos de `frame` px.
///
/// `matrix_px_to_mm` y `lens` hablan de píxeles de una foto de `camera_px` con el centro del píxel en coordenada
/// entera (así los mide la consola); si la foto del motor es más grande o más chica, se escala.
fn build_rect(calib: &CalibIn, frame: (u32, u32), cfg: &PipelineConfig) -> Result<Rect, String> {
    let (sw, sh) = calib.surface_mm;
    if !(sw.is_finite() && sh.is_finite() && sw > 1.0 && sh > 1.0) {
        return Err(format!("surface_mm inválida: {sw} × {sh}"));
    }
    if calib.matrix_px_to_mm.iter().any(|v| !v.is_finite()) {
        return Err("matrix_px_to_mm tiene valores no finitos".into());
    }
    let (fw, fh) = (frame.0 as f64, frame.1 as f64);
    let (cw, ch) = if calib.camera_px.0 > 0 && calib.camera_px.1 > 0 {
        (calib.camera_px.0 as f64, calib.camera_px.1 as f64)
    } else {
        (fw, fh)
    };
    let s = fw / cw;
    if ((fh / ch) / s - 1.0).abs() > 0.02 {
        return Err(format!("la foto es de {fw}×{fh} px y la calibración de {cw}×{ch}: otra proporción"));
    }
    let m = &calib.matrix_px_to_mm;
    // px de la foto (centro en +0,5) → px de la calibración (centro en entero): p = q / s − 0,5
    let col = |a: f64, b: f64, c: f64| [a / s, b / s, c - 0.5 * (a + b)];
    let (r0, r1, r2) = (col(m[0], m[1], m[2]), col(m[3], m[4], m[5]), col(m[6], m[7], m[8]));
    let hom = Homography([r0[0], r0[1], r0[2], r1[0], r1[1], r1[2], r2[0], r2[1], r2[2]]);
    let mm_per_px = cfg.mm_per_px.max(sw.max(sh) / cfg.max_canvas_px.max(64) as f64).max(0.05);
    let canvas = Canvas {
        width: (sw / mm_per_px).round().max(8.0) as u32,
        height: (sh / mm_per_px).round().max(8.0) as u32,
        mm_per_px,
        origin_mm: (0.0, 0.0),
    };
    let rectifier = match &calib.lens {
        Some(l) => {
            if !(l.f.is_finite() && l.f > 0.0 && [l.cx, l.cy, l.k1, l.k2].iter().all(|v| v.is_finite())) {
                return Err("lente inválido".into());
            }
            let lens = Radial { center: (s * (l.cx + 0.5), s * (l.cy + 0.5)), focal_px: s * l.f, k1: l.k1, k2: l.k2 };
            Rectifier::with_radial(hom, lens, canvas, frame)
        }
        None => Rectifier::new(hom, canvas).map(|r| r.precompute(frame)),
    }
    .ok_or("matrix_px_to_mm no es invertible")?;
    let key = format!("{calib:?}|{frame:?}|{mm_per_px}");
    Ok(Rect { key, rectifier, canvas })
}

impl Perceptor for Pipeline {
    fn on_clean(&mut self, frame: &RgbImage, calib: &CalibIn, _t_ns: u64) -> Result<PerceptionOut, String> {
        let mut st = Stages::new();
        self.log.clear();
        let key = format!("{calib:?}|{:?}|", frame.dimensions());
        if !self.rect.as_ref().is_some_and(|r| r.key.starts_with(&key)) {
            let rect = build_rect(calib, frame.dimensions(), &self.cfg)?;
            if self.rect.is_some() {
                self.log.push("calibración nueva: se olvidan los objetos".into());
                self.reset();
            }
            self.rect = Some(rect);
        }
        let (canvas, rectified) = {
            let r = self.rect.as_ref().expect("recién armado");
            (r.canvas, r.rectifier.rectify(frame).map_err(|e| e.to_string())?)
        };
        let (rgb, valid) = rectified;
        st.mark("rectify");
        self.apply_names();
        let mut remove = std::mem::take(&mut self.pending_remove);
        let result = self.cycle(&canvas, &rgb, &valid, &mut st);
        self.last_rgb = Some(rgb);
        remove.extend(result?);
        let mut upsert = vec![];
        let mut tracks = std::mem::take(&mut self.tracks);
        for t in tracks.iter_mut() {
            if t.dirty && matches!(t.life, Life::Present | Life::Missing(_)) {
                if let Some(o) = self.object_out(t, &canvas) {
                    upsert.push(o);
                    t.published = true;
                }
                t.dirty = false;
            }
        }
        self.tracks = tracks;
        remove.retain(|id| !upsert.iter().any(|o| &o.id == id));
        st.mark("publish");
        if !self.has_sam() {
            self.log.push(self.sam_status.clone());
        }
        Ok(PerceptionOut { upsert, remove, stages_ms: st.finish() })
    }

    fn reset(&mut self) {
        self.pending_remove.extend(self.tracks.iter().filter(|t| t.published).map(|t| t.id.clone()));
        self.tracks.clear();
        self.scene = None;
        self.sam_key = None;
    }
}
