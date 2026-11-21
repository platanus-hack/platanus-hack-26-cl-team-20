//! Sincronía adaptativa (sin `sleep` fijo).
//!
//! - **Por cambio** (`show_and_wait`): se acepta el cuadro cuando la cámara ve el cambio respecto del cuadro
//!   anterior y la imagen ya está quieta (2 cuadros iguales), como `Capturador.esperar_cuadro` de Max.
//! - **Por marcador** (`stream`): el método rápido de Daniel. Los patrones se proyectan en streaming
//!   (~2,5 cuadros cada uno) con dos parches de sincronía en una esquina que codifican `índice mod 4`; cada cuadro
//!   dice qué patrón muestra (no depende de la latencia), se usa el cuadro más limpio del centro de cada racha y se
//!   re-proyectan solo los patrones que quedaron sin cuadro limpio.

use crate::homography::{apply, Mat3};
use crate::image::GrayImage;
use crate::io::{CalibError, Frame, FrameGrab, Pattern, PatternSink};

/// Fracción de píxeles (submuestreados cada 4) que cambian más de 18 DN; `roi` opcional.
pub fn change_frac(a: &GrayImage, b: &GrayImage, roi: Option<&[bool]>) -> f64 {
    let (mut n, mut c) = (0u32, 0u32);
    for i in (0..a.data.len()).step_by(4) {
        if roi.is_some_and(|r| !r[i]) {
            continue;
        }
        n += 1;
        if (a.data[i] as i32 - b.data[i] as i32).abs() > 18 {
            c += 1;
        }
    }
    if n == 0 {
        0.0
    } else {
        c as f64 / n as f64
    }
}

#[derive(Debug, Clone)]
pub struct ChangeParams {
    pub min_change: f64,
    pub stable: f64,
    pub timeout_ns: i64,
}

impl Default for ChangeParams {
    fn default() -> Self {
        Self { min_change: 0.05, stable: 0.015, timeout_ns: 5_000_000_000 }
    }
}

/// Muestra `p` y espera a que la cámara vea el cambio respecto de `prev` y se estabilice. Devuelve el cuadro y la
/// latencia observada (envío → primer cuadro estable).
pub fn show_and_wait(
    sink: &mut dyn PatternSink,
    grab: &mut dyn FrameGrab,
    p: &Pattern,
    prev: &GrayImage,
    roi: Option<&[bool]>,
    prm: &ChangeParams,
) -> Result<(Frame, i64), CalibError> {
    let t0 = sink.show(p)?;
    let mut last: Option<Frame> = None;
    loop {
        let f = grab.grab(t0)?;
        if f.t_capture_ns - t0 > prm.timeout_ns {
            return Err(CalibError::SyncTimeout(format!("«{}»: la cámara no ve el cambio", p.label)));
        }
        if let Some(l) = &last {
            if change_frac(&l.image, prev, roi) >= prm.min_change && change_frac(&f.image, &l.image, roi) < prm.stable {
                let lat = f.t_capture_ns - t0;
                return Ok((f, lat));
            }
        }
        last = Some(f);
    }
}

/// Dos parches de sincronía en px del proyector (`[x0, y0, x1, y1)`), en la esquina superior izquierda.
#[derive(Debug, Clone)]
pub struct SyncPatches {
    pub rects: [[u32; 4]; 2],
}

impl SyncPatches {
    pub fn new(proj: (u32, u32)) -> Self {
        let s = (proj.0 / 32).max(12);
        let m = s / 2;
        Self { rects: [[m, m, m + s, m + s], [m + 2 * s, m, m + 3 * s, m + s]] }
    }
    /// Zona que no se decodifica (parches + margen).
    pub fn guard(&self) -> [u32; 4] {
        let s = self.rects[0][2] - self.rects[0][0];
        [0, 0, self.rects[1][2] + s / 2, self.rects[0][3] + s / 2]
    }
    pub fn paint(&self, img: &mut GrayImage, code: u8) {
        let g = self.guard();
        img.fill_rect(g[0] as i64, g[1] as i64, g[2] as i64, g[3] as i64, 0);
        for (k, r) in self.rects.iter().enumerate() {
            if code >> k & 1 == 1 {
                img.fill_rect(r[0] as i64, r[1] as i64, r[2] as i64, r[3] as i64, 255);
            }
        }
    }
    /// Centros y radio de lectura en la cámara (con la homografía proyector → cámara).
    pub fn in_camera(&self, h_pc: &Mat3) -> ([[f64; 2]; 2], f64) {
        let c = |r: &[u32; 4]| apply(h_pc, [(r[0] + r[2]) as f64 / 2.0 - 0.5, (r[1] + r[3]) as f64 / 2.0 - 0.5]);
        let a = c(&self.rects[0]);
        let r = &self.rects[0];
        let e = apply(h_pc, [r[2] as f64 - 0.5, (r[1] + r[3]) as f64 / 2.0 - 0.5]);
        let rad = (((e[0] - a[0]).powi(2) + (e[1] - a[1]).powi(2)).sqrt() * 0.4).max(1.0);
        ([a, c(&self.rects[1])], rad)
    }
}

fn read_patch(img: &GrayImage, c: [f64; 2], r: f64) -> f64 {
    let (mut s, mut n) = (0.0, 0.0);
    let ri = r.ceil() as i64;
    for dy in -ri..=ri {
        for dx in -ri..=ri {
            let (x, y) = (c[0].round() as i64 + dx, c[1].round() as i64 + dy);
            if x >= 0 && y >= 0 && x < img.width as i64 && y < img.height as i64 && ((dx * dx + dy * dy) as f64) <= r * r {
                s += img.get(x as u32, y as u32) as f64;
                n += 1.0;
            }
        }
    }
    if n > 0.0 {
        s / n
    } else {
        0.0
    }
}

#[derive(Debug, Clone)]
pub struct StreamParams {
    /// Tiempo de cada patrón, en períodos de cuadro (Daniel: 2,5).
    pub hold_frames: f64,
    /// Pasadas extra para patrones sin cuadro limpio.
    pub max_retries: usize,
    /// Claridad mínima del parche: |lectura − medio| / (alto − bajo) (0,4 = ≤ 10 % de mezcla).
    pub min_clarity: f64,
}

impl Default for StreamParams {
    fn default() -> Self {
        Self { hold_frames: 2.5, max_retries: 2, min_clarity: 0.4 }
    }
}

#[derive(Debug, Clone, Default, serde::Serialize)]
pub struct StreamReport {
    pub frame_period_ms: f64,
    pub latency_ms: f64,
    pub passes: usize,
    pub frames_seen: usize,
    pub retried: usize,
}

/// Proyecta `patterns` en streaming y devuelve un cuadro limpio por patrón (mismo orden).
pub fn stream(
    sink: &mut dyn PatternSink,
    grab: &mut dyn FrameGrab,
    patterns: &[Pattern],
    patches: &SyncPatches,
    h_pc: &Mat3,
    prm: &StreamParams,
) -> Result<(Vec<Frame>, StreamReport), CalibError> {
    let (centers, rad) = patches.in_camera(h_pc);
    let proj = sink.size();
    let mut rep = StreamReport::default();
    // reinicio: negro con código 3 (≡ −1 mod 4) y espera a verlo, mide período y latencia
    let mut reset = GrayImage::new(proj.0, proj.1, 0);
    patches.paint(&mut reset, 3);
    let reset = Pattern::new("sync_reset", reset);
    let mut prev = grab.grab(0)?;
    let t_prev = prev.t_capture_ns;
    let first = grab.grab(t_prev + 1)?;
    let period = (first.t_capture_ns - t_prev).max(1);
    prev = first;
    let (_, lat) = show_and_wait(sink, grab, &reset, &prev.image, None, &ChangeParams { min_change: 0.0005, ..Default::default() })?;
    rep.frame_period_ms = period as f64 / 1e6;
    rep.latency_ms = lat as f64 / 1e6;
    let hold = (prm.hold_frames * period as f64) as i64;
    let mut chosen: Vec<Option<(f64, Frame)>> = vec![None; patterns.len()];
    let mut todo: Vec<usize> = (0..patterns.len()).collect();
    for pass in 0..=prm.max_retries {
        if todo.is_empty() {
            break;
        }
        rep.passes = pass + 1;
        if pass > 0 {
            rep.retried += todo.len();
        }
        let mut frames: Vec<Frame> = Vec::new();
        let mut t_show: Vec<i64> = Vec::new();
        let mut last_t = 0;
        for (i, &k) in todo.iter().enumerate() {
            let mut img = (*patterns[k].image).clone();
            patches.paint(&mut img, (i % 4) as u8);
            let t = sink.show(&Pattern::new(patterns[k].label.clone(), img))?;
            t_show.push(t);
            loop {
                let f = grab.grab(last_t + 1)?;
                last_t = f.t_capture_ns;
                frames.push(f);
                if last_t >= t + hold {
                    break;
                }
            }
        }
        // cola: hasta que pase la latencia + 2 holds desde el último patrón
        let end = t_show.last().copied().unwrap_or(0) + lat + 2 * hold;
        while last_t < end {
            let f = grab.grab(last_t + 1)?;
            last_t = f.t_capture_ns;
            frames.push(f);
        }
        // reposo: vuelve al negro de reinicio para la próxima pasada
        sink.show(&reset)?;
        rep.frames_seen += frames.len();
        // niveles por parche
        let reads: Vec<[f64; 2]> = frames.iter().map(|f| [read_patch(&f.image, centers[0], rad), read_patch(&f.image, centers[1], rad)]).collect();
        let lv: Vec<(f64, f64)> = (0..2)
            .map(|k| {
                let v: Vec<f64> = reads.iter().map(|r| r[k]).collect();
                (crate::image::percentile_f64(&v, 3.0), crate::image::percentile_f64(&v, 97.0))
            })
            .collect();
        if lv.iter().any(|(lo, hi)| hi - lo < 10.0) {
            return Err(CalibError::SyncTimeout("no veo los parches de sincronía".into()));
        }
        // asignación cuadro → índice mostrado
        let mut runs: Vec<Vec<(usize, f64)>> = vec![Vec::new(); todo.len()];
        let mut cur: i64 = -1;
        for (fi, r) in reads.iter().enumerate() {
            let mut code = 0u8;
            let mut clarity = f64::INFINITY;
            for k in 0..2 {
                let (lo, hi) = lv[k];
                let x = (r[k] - lo) / (hi - lo);
                clarity = clarity.min((x - 0.5).abs());
                if x > 0.5 {
                    code |= 1 << k;
                }
            }
            if clarity < 0.25 {
                continue;
            }
            let ahead = (code as i64 - cur).rem_euclid(4);
            let k = cur + ahead;
            if k >= todo.len() as i64 || (k >= 0 && frames[fi].t_capture_ns < t_show[k as usize]) {
                continue;
            }
            cur = k;
            if k >= 0 {
                runs[k as usize].push((fi, clarity));
            }
        }
        let mut next = Vec::new();
        for (i, run) in runs.iter().enumerate() {
            let inner: &[(usize, f64)] = if run.len() >= 3 { &run[1..run.len() - 1] } else { run };
            let best = inner.iter().copied().max_by(|a, b| a.1.total_cmp(&b.1));
            match best {
                Some((fi, c)) if c >= prm.min_clarity => {
                    let k = todo[i];
                    if chosen[k].as_ref().is_none_or(|(cc, _)| c > *cc) {
                        chosen[k] = Some((c, frames[fi].clone()));
                    }
                }
                _ => next.push(todo[i]),
            }
        }
        todo = next;
    }
    if !todo.is_empty() {
        return Err(CalibError::SyncTimeout(format!("{} patrones sin cuadro limpio", todo.len())));
    }
    Ok((chosen.into_iter().map(|c| c.expect("todos asignados").1).collect(), rep))
}
