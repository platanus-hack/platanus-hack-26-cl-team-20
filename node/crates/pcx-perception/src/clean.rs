//! Foto limpia: la mesa sin proyección encima (port de `seguidor_v4.py::limpio`, sin marcador de sincronía).
//!
//! 1. Pide `blank{ms}` por [`BlankControl`] y lee cada cuadro nuevo de la [`FrameSource`] (el anillo solo guarda
//!    los últimos, así que se leen en vivo). La referencia de luminancia es el último cuadro capturado antes de
//!    `min_latency_ms`: lo que el contenido cambie hasta ahí no cuenta como apagón.
//! 2. Hay apagón claro cuando la luminancia del área proyectada cae `max(min_drop, min_drop_rel × referencia)`.
//!    Los cuadros interiores son los de la meseta más profunda (a `plateau_tol × caída` del mínimo); los de borde
//!    quedan fuera. Sale apenas junta `min_frames`: mediana por píxel.
//! 3. Con una caída menor (proyección tenue, o contenido que se atenuó) no se decide por luminancia: se espera
//!    `blanked` y se usa su ventana corrida por la latencia esperada, y solo si la caída coincide con esa ventana.
//! 4. Sin caída devuelve [`CleanError::BlackoutNotSeen`] en vez de una foto contaminada.
//!
//! No se espera `blanked` en el camino normal: el renderer lo manda al terminar el apagón (≥ `ms`).
//!
//! Límite conocido: si el contenido proyectado pierde más de `min_drop_rel` de la luminancia entre `min_latency_ms`
//! y la llegada real de la oscuridad, y se queda ahí tres cuadros, no se distingue de un apagón. Quien llama debe
//! congelar la escena antes de pedir la foto, o subir `min_latency_ms` a la latencia medida.

use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use pcx_shm::{PixelFormat, RingReader, SlotMeta};

/// Intervalo negro que reporta el renderer (`blanked`): ms de época, reloj de presentación (se adelanta a la luz).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct BlankWindow {
    pub t_start_ms: f64,
    pub t_end_ms: f64,
}

/// Canal hacia el core/renderer, sin atarse al transporte.
pub trait BlankControl {
    /// Envía `blank{ms}` y devuelve su `request_id`. No espera la respuesta.
    fn request_blank(&mut self, ms: u32) -> Result<String, String>;
    /// `blanked` con ese `request_id` si ya llegó (los de otros pedidos se ignoran). No bloquea.
    fn poll_blanked(&mut self, request_id: &str) -> Option<BlankWindow>;
}

/// Fuente de cuadros con reloj propio (el de `t_capture_ns`), para poder simularla en tests.
pub trait FrameSource {
    /// Ahora, en el reloj de `t_capture_ns` (ns de época en el anillo real).
    fn now_ns(&self) -> u64;
    /// Espera hasta `timeout` un cuadro con `seq > after_seq` y lo copia en `buf`.
    fn next_frame(
        &mut self,
        after_seq: u64,
        timeout: Duration,
        buf: &mut Vec<u8>,
    ) -> Result<Option<SlotMeta>, CleanError>;
}

impl FrameSource for RingReader {
    fn now_ns(&self) -> u64 {
        SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_nanos() as u64).unwrap_or(0)
    }
    fn next_frame(
        &mut self,
        after_seq: u64,
        timeout: Duration,
        buf: &mut Vec<u8>,
    ) -> Result<Option<SlotMeta>, CleanError> {
        Ok(self.wait_newer(after_seq, timeout, buf)?)
    }
}

/// Zona que ilumina el proyector en la imagen de cámara (≠ 0 = dentro), a la resolución del cuadro.
#[derive(Debug, Clone)]
pub struct Mask {
    pub w: u32,
    pub h: u32,
    pub data: Vec<u8>,
}

impl Mask {
    /// Máscara rectangular `[x0, x1) × [y0, y1)`.
    pub fn from_rect(w: u32, h: u32, x0: u32, y0: u32, x1: u32, y1: u32) -> Self {
        let mut data = vec![0u8; w as usize * h as usize];
        let (x0, x1) = (x0.min(w), x1.min(w).max(x0.min(w)));
        for y in y0.min(h)..y1.min(h) {
            data[(y * w + x0) as usize..(y * w + x1) as usize].fill(255);
        }
        Self { w, h, data }
    }
}

#[derive(Debug, Clone)]
pub struct CleanConfig {
    /// Duración pedida del apagón.
    pub blank_ms: u32,
    /// Cuadros interiores para la mediana: sale apenas tiene `min_frames`; nunca usa más de `max_frames` (≤ 8).
    pub min_frames: usize,
    pub max_frames: usize,
    /// Latencia proyector→cámara máxima que se espera; define hasta cuándo se buscan cuadros oscuros.
    pub max_latency_ms: u32,
    /// Holgura extra de la espera (≈ 3 cuadros a 30 fps).
    pub margin_ms: u32,
    /// Antes de `min_latency_ms` desde el pedido la oscuridad no puede haber llegado: esos cuadros no son del apagón
    /// y el último de ellos es la referencia de luminancia.
    pub min_latency_ms: u32,
    /// Caída mínima para confiar solo en la luminancia: `max(min_drop niveles, min_drop_rel × referencia)`. Un
    /// cambio de contenido (algo proyectado que se atenúa) baja poco; apagar el proyector baja mucho.
    pub min_drop: f32,
    pub min_drop_rel: f32,
    /// Entre `ambiguous_drop` y `min_drop` la caída es ambigua: se decide por tiempos. Debajo, no hubo apagón.
    pub ambiguous_drop: f32,
    /// Meseta: un cuadro es interior si está a menos de `plateau_tol × caída` del más oscuro. Deja fuera los bordes.
    pub plateau_tol: f32,
    /// Latencia esperada (mín, máx) para el respaldo por tiempos. En hardware se midió 215–260 ms.
    pub expected_latency_ms: (u32, u32),
    /// Guarda a cada lado de la ventana por tiempos (≈ 1 cuadro).
    pub edge_guard_ms: u32,
    /// Quien llama asegura que no hay nada proyectado: sin caída se elige por tiempos en vez de fallar.
    pub assume_dark_when_flat: bool,
    /// Igualar la exposición de los cuadros oscuros entre sí antes de la mediana (necesita máscara con zona de
    /// referencia). Apagado por defecto: lo correcto es bloquear la exposición en captura.
    pub compensate_exposure: bool,
    /// Submuestreo de la medición de luminancia (1 de cada `sample_step` píxeles por eje).
    pub sample_step: usize,
    /// Espera por cuadro nuevo en cada vuelta.
    pub poll: Duration,
}

impl Default for CleanConfig {
    fn default() -> Self {
        Self {
            blank_ms: 300,
            min_frames: 3,
            max_frames: 5,
            max_latency_ms: 400,
            margin_ms: 100,
            min_latency_ms: 80,
            min_drop: 8.0,
            min_drop_rel: 0.25,
            ambiguous_drop: 2.0,
            plateau_tol: 0.02,
            expected_latency_ms: (215, 260),
            edge_guard_ms: 34,
            assume_dark_when_flat: false,
            compensate_exposure: false,
            sample_step: 4,
            poll: Duration::from_millis(5),
        }
    }
}

/// Cómo se eligieron los cuadros.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Selection {
    /// Caída de luminancia clara en el área proyectada.
    Luminance,
    /// Caída ambigua o nula: ventana de `blanked` corrida por la latencia esperada.
    Times,
}

#[derive(Debug, Clone, PartialEq)]
pub struct CleanMetrics {
    /// De pedir `blank` a tener la foto (reloj de la fuente + cómputo de la mediana).
    pub blank_to_photo_ms: f64,
    /// Solo la mediana por píxel (reloj de pared).
    pub median_ms: f64,
    pub frames_used: usize,
    /// Cuadros leídos después del pedido.
    pub frames_seen: usize,
    /// Luminancia media del área proyectada antes del apagón y en los cuadros usados (normalizada por exposición).
    pub luminance_base: f32,
    pub luminance_dark: f32,
    /// `luminance_base - luminance_dark`.
    pub luminance_drop: f32,
    /// Del pedido a la captura del primer cuadro usado: latencia proyector→cámara aparente.
    pub first_dark_ms: f64,
    /// Diferencia media absoluta entre el primer y el último cuadro usado (muestreada). Si es alta, algo se movió
    /// durante el apagón (p. ej. volvió la mano) y conviene descartar la foto.
    pub frame_spread: f32,
    pub selection: Selection,
    /// `blanked`, si llegó antes de terminar.
    pub blanked: Option<BlankWindow>,
}

/// Foto limpia: mismo layout que los cuadros del anillo (`h × stride` bytes).
#[derive(Debug, Clone)]
pub struct CleanPhoto {
    pub w: u32,
    pub h: u32,
    pub stride: u32,
    pub format: PixelFormat,
    pub data: Vec<u8>,
    /// `seq` de los cuadros usados.
    pub seqs: Vec<u64>,
    /// Captura del cuadro central.
    pub t_capture_ns: u64,
    pub metrics: CleanMetrics,
}

impl CleanPhoto {
    /// Píxeles como RGB compacto (`w × h × 3`), que es lo que consume `pcx-objects`. Sin copia si ya venía así.
    pub fn into_rgb(self) -> Vec<u8> {
        let (w, h, stride, ch) = (self.w as usize, self.h as usize, self.stride as usize, self.format.channels());
        let mut data = self.data;
        if ch == 3 && stride == w * 3 {
            if self.format == PixelFormat::Bgr8 {
                for i in (2..data.len()).step_by(3) {
                    data.swap(i - 2, i);
                }
            }
            return data;
        }
        let mut out = Vec::with_capacity(w * h * 3);
        for row in data.chunks(stride).take(h) {
            for p in row[..w * ch].chunks_exact(ch) {
                out.extend_from_slice(&match self.format {
                    PixelFormat::Gray8 => [p[0]; 3],
                    PixelFormat::Rgb8 => [p[0], p[1], p[2]],
                    PixelFormat::Bgr8 | PixelFormat::Bgra8 => [p[2], p[1], p[0]],
                });
            }
        }
        out
    }
}

#[derive(Debug, thiserror::Error)]
pub enum CleanError {
    #[error("la cámara no entregó cuadros en {0} ms")]
    NoFrames(u64),
    #[error("no se pudo pedir blank: {0}")]
    Blank(String),
    #[error("anillo de cuadros: {0}")]
    Shm(#[from] pcx_shm::ShmError),
    #[error("la máscara es {mask_w}×{mask_h} y el cuadro {w}×{h}")]
    MaskSize { mask_w: u32, mask_h: u32, w: u32, h: u32 },
    #[error("no se vio el apagón: la luminancia cayó {drop:.1} niveles en {waited_ms:.0} ms (¿latencia mayor que la ventana, o blank sin efecto?); no hay foto")]
    BlackoutNotSeen { drop: f32, waited_ms: f64 },
    #[error("apagón con solo {got} cuadros interiores (hacen falta {need}; caída {drop:.1}); no hay foto")]
    TooFewFrames { got: usize, need: usize, drop: f32 },
    #[error("caída ambigua ({drop:.1} niveles) y no llegó blanked para decidir por tiempos; no hay foto")]
    NoBlanked { drop: f32 },
}

/// Puntos de muestreo de luminancia: dentro del área proyectada y fuera (referencia de exposición).
struct Sampler {
    inside: Vec<u32>,
    outside: Vec<u32>,
    ch: usize,
}

impl Sampler {
    fn new(meta: &SlotMeta, mask: Option<&Mask>, step: usize) -> Result<Self, CleanError> {
        if let Some(m) = mask {
            if m.w != meta.w || m.h != meta.h || m.data.len() != m.w as usize * m.h as usize {
                return Err(CleanError::MaskSize { mask_w: m.w, mask_h: m.h, w: meta.w, h: meta.h });
            }
        }
        let (w, h, stride, step) = (meta.w as usize, meta.h as usize, meta.stride as usize, step.max(1));
        let (mut inside, mut outside) = (Vec::new(), Vec::new());
        for y in (step / 2..h).step_by(step) {
            for x in (step / 2..w).step_by(step) {
                let off = (y * stride + x * meta.format.channels()) as u32;
                match mask {
                    Some(m) if m.data[y * w + x] == 0 => outside.push(off),
                    _ => inside.push(off),
                }
            }
        }
        // Máscara vacía: se mide todo el cuadro.
        if inside.is_empty() {
            std::mem::swap(&mut inside, &mut outside);
        }
        // Sin zona de referencia suficiente no se normaliza.
        if outside.len() < 64 {
            outside.clear();
        }
        Ok(Self { inside, outside, ch: meta.format.channels().min(3) })
    }

    fn mean(&self, offs: &[u32], data: &[u8]) -> f32 {
        let mut sum = 0u64;
        for &o in offs {
            let o = o as usize;
            for c in 0..self.ch {
                sum += data[o + c] as u64;
            }
        }
        sum as f32 / (offs.len() * self.ch).max(1) as f32
    }

    /// Diferencia media absoluta entre dos cuadros en los puntos de muestreo.
    fn spread(&self, a: &[u8], b: &[u8]) -> f32 {
        let mut sum = 0u64;
        for &o in self.inside.iter().chain(&self.outside) {
            for c in 0..self.ch {
                sum += a[o as usize + c].abs_diff(b[o as usize + c]) as u64;
            }
        }
        sum as f32 / ((self.inside.len() + self.outside.len()) * self.ch).max(1) as f32
    }

    /// (luminancia dentro, luminancia de referencia fuera; 0 si no hay referencia).
    fn measure(&self, data: &[u8]) -> (f32, f32) {
        (self.mean(&self.inside, data), if self.outside.is_empty() { 0.0 } else { self.mean(&self.outside, data) })
    }
}

struct Sample {
    seq: u64,
    t_ns: u64,
    /// Luminancia del área proyectada, tal como viene.
    lum: f32,
    /// Luminancia de la zona de referencia (0 si no hay).
    r: f32,
    data: Option<Vec<u8>>,
}

const MAX_KEEP: usize = 32;
/// Sin zona de referencia la autoexposición no se puede normalizar: se tolera que cada cuadro oscuro suba esto
/// (× nivel oscuro) por cuadro de distancia al más oscuro, hasta 4 cuadros.
const NO_REF_AE_SLACK: f32 = 0.12;

/// Toma la foto limpia. Bloquea hasta tenerla o hasta `blank_ms + max_latency_ms + margin_ms`.
pub fn capture_clean(
    src: &mut impl FrameSource,
    ctl: &mut impl BlankControl,
    mask: Option<&Mask>,
    cfg: &CleanConfig,
) -> Result<CleanPhoto, CleanError> {
    let min_frames = cfg.min_frames.max(1);
    let max_frames = cfg.max_frames.clamp(min_frames, 8);
    let mut buf = Vec::new();

    // Cuadro de referencia, justo antes del pedido (proyector encendido).
    let t_wait = src.now_ns();
    let base_meta = loop {
        if let Some(m) = src.next_frame(0, cfg.poll, &mut buf)? {
            break m;
        }
        if src.now_ns().saturating_sub(t_wait) > 500_000_000 {
            return Err(CleanError::NoFrames(500));
        }
    };
    if buf.len() < base_meta.nbytes() {
        return Err(CleanError::NoFrames(0));
    }
    let sampler = Sampler::new(&base_meta, mask, cfg.sample_step)?;
    let has_ref = !sampler.outside.is_empty();
    let mut base = sampler.measure(&buf).0;
    let same_shape = |m: &SlotMeta| {
        m.w == base_meta.w && m.h == base_meta.h && m.stride == base_meta.stride && m.format == base_meta.format
    };

    let t_req = src.now_ns();
    let request_id = ctl.request_blank(cfg.blank_ms).map_err(CleanError::Blank)?;
    let min_latency = cfg.min_latency_ms as u64 * 1_000_000;
    let deadline = t_req + (cfg.blank_ms + cfg.max_latency_ms + cfg.margin_ms) as u64 * 1_000_000;

    let mut samples: Vec<Sample> = Vec::new();
    let mut blanked = None;
    let mut last_seq = base_meta.seq;
    let mut picked: Option<Vec<usize>> = None;
    let mut drop = 0.0f32;
    let clear = |drop: f32, base: f32| drop >= cfg.min_drop.max(cfg.min_drop_rel * base);

    while src.now_ns() < deadline {
        blanked = blanked.or_else(|| ctl.poll_blanked(&request_id));
        let Some(meta) = src.next_frame(last_seq, cfg.poll, &mut buf)? else { continue };
        last_seq = meta.seq;
        if !same_shape(&meta) || buf.len() < meta.nbytes() {
            continue;
        }
        let (lum, r) = sampler.measure(&buf);
        if meta.t_capture_ns < t_req + min_latency {
            // La oscuridad todavía no puede haber llegado: lo que cambie hasta acá es contenido (una animación que
            // termina, un halo que se apaga). La referencia es siempre el último cuadro previo al apagón posible.
            base = lum;
            continue;
        }
        let keep = cfg.assume_dark_when_flat || lum < base - cfg.ambiguous_drop * 0.5;
        samples.push(Sample {
            seq: meta.seq,
            t_ns: meta.t_capture_ns,
            lum,
            r,
            data: keep.then(|| std::mem::take(&mut buf)),
        });

        // Nada es del apagón antes de que el renderer presente el primer negro (si ya avisó).
        let t_min = blanked.map_or(0, |b: BlankWindow| (b.t_start_ms * 1e6) as u64 + min_latency);
        let (d, interior) = plateau(&samples, base, t_min, has_ref, cfg);
        drop = d;
        if clear(drop, base) {
            // Ya se sabe dónde está la oscuridad: suelta los cuadros claros.
            for s in samples.iter_mut().filter(|s| s.lum >= base - drop * 0.5) {
                s.data = None;
            }
            if interior.len() >= min_frames {
                picked = Some(interior);
                break;
            }
        } else if samples.iter().filter(|s| s.data.is_some()).count() > MAX_KEEP {
            let brightest = (0..samples.len())
                .filter(|&i| samples[i].data.is_some())
                .max_by(|&a, &b| samples[a].lum.total_cmp(&samples[b].lum));
            if let Some(i) = brightest {
                samples[i].data = None;
            }
        }
    }

    let (mut idx, selection) = if let Some(idx) = picked {
        (idx, Selection::Luminance)
    } else if clear(drop, base) {
        let got = plateau(&samples, base, 0, has_ref, cfg).1.len();
        return Err(CleanError::TooFewFrames { got, need: min_frames, drop });
    } else if drop >= cfg.ambiguous_drop || cfg.assume_dark_when_flat {
        // Respaldo por tiempos: hace falta `blanked` (llega al terminar el apagón).
        let grace = deadline + 200_000_000;
        while blanked.is_none() && src.now_ns() < grace {
            blanked = ctl.poll_blanked(&request_id);
            if blanked.is_none() {
                if let Some(m) = src.next_frame(last_seq, cfg.poll, &mut buf)? {
                    last_seq = m.seq;
                }
            }
        }
        let win = blanked.ok_or(CleanError::NoBlanked { drop })?;
        let (lat_lo, lat_hi, guard) =
            (cfg.expected_latency_ms.0 as f64, cfg.expected_latency_ms.1 as f64, cfg.edge_guard_ms as f64);
        let (lo, hi) = (win.t_start_ms + lat_hi + guard, win.t_end_ms + lat_lo - guard);
        let ambiguous = drop >= cfg.ambiguous_drop;
        let darkish = |s: &Sample| s.lum < base - drop * 0.5;
        let t_ms = |s: &Sample| s.t_ns as f64 / 1e6;
        // La caída tiene que coincidir con la ventana: si también está oscuro donde seguro había luz, lo que bajó
        // fue el contenido y no el proyector.
        let (sure_lo, sure_hi) = (win.t_start_ms + lat_lo - guard, win.t_end_ms + lat_hi + guard);
        if ambiguous && samples.iter().any(|s| darkish(s) && (t_ms(s) < sure_lo || t_ms(s) > sure_hi)) {
            return Err(CleanError::BlackoutNotSeen { drop, waited_ms: (src.now_ns() - t_req) as f64 / 1e6 });
        }
        let idx: Vec<usize> = (0..samples.len())
            .filter(|&i| {
                let s = &samples[i];
                s.data.is_some() && t_ms(s) >= lo && t_ms(s) <= hi && (!ambiguous || darkish(s))
            })
            .collect();
        if idx.len() < min_frames {
            return Err(CleanError::TooFewFrames { got: idx.len(), need: min_frames, drop });
        }
        (idx, Selection::Times)
    } else {
        return Err(CleanError::BlackoutNotSeen { drop, waited_ms: (src.now_ns() - t_req) as f64 / 1e6 });
    };
    idx.truncate(max_frames);

    let t0 = Instant::now();
    let used: Vec<&Sample> = idx.iter().map(|&i| &samples[i]).collect();
    let frames: Vec<&[u8]> = used.iter().filter_map(|s| s.data.as_deref()).map(|d| &d[..base_meta.nbytes()]).collect();
    // Exposición: los cuadros oscuros se igualan entre sí (referencia = mediana de la racha), nunca contra el cuadro
    // previo al apagón, porque la zona de referencia también pierde la luz rebotada del proyector.
    let mut rs: Vec<f32> = used.iter().map(|s| s.r).collect();
    rs.sort_by(f32::total_cmp);
    let r_med = rs[rs.len() / 2];
    let gains: Vec<f32> = used.iter().map(|s| if has_ref && s.r > 1.0 { r_med / s.r } else { 1.0 }).collect();
    let compensate = cfg.compensate_exposure && gains.iter().any(|g| (g - 1.0).abs() > 0.02);
    let data = median_impl(&frames, compensate.then_some(&gains[..]));
    let median_ms = t0.elapsed().as_secs_f64() * 1e3;

    let dark = used.iter().map(|s| s.lum).sum::<f32>() / used.len() as f32;
    let metrics = CleanMetrics {
        blank_to_photo_ms: (src.now_ns() - t_req) as f64 / 1e6 + median_ms,
        median_ms,
        frames_used: used.len(),
        frames_seen: samples.len(),
        luminance_base: base,
        luminance_dark: dark,
        luminance_drop: base - dark,
        first_dark_ms: (used[0].t_ns - t_req) as f64 / 1e6,
        frame_spread: sampler.spread(frames[0], frames[frames.len() - 1]),
        selection,
        blanked: blanked.or_else(|| ctl.poll_blanked(&request_id)),
    };
    Ok(CleanPhoto {
        w: base_meta.w,
        h: base_meta.h,
        stride: base_meta.stride,
        format: base_meta.format,
        data,
        seqs: used.iter().map(|s| s.seq).collect(),
        t_capture_ns: used[used.len() / 2].t_ns,
        metrics,
    })
}

/// Caída respecto de `base` y cuadros de la meseta más profunda: los que están a `plateau_tol × caída` del mínimo.
/// No hace falta que sean contiguos (un parpadeo en medio no corta nada) y, si después aparece un cuadro más
/// oscuro, la meseta se muda sola a ese nivel. Los cuadros a medio apagar quedan fuera por estar sobre la banda.
fn plateau(samples: &[Sample], base: f32, t_min_ns: u64, has_ref: bool, cfg: &CleanConfig) -> (f32, Vec<usize>) {
    let cand = |i: &usize| samples[*i].t_ns >= t_min_ns;
    let Some(m) = (0..samples.len()).filter(cand).min_by(|&a, &b| samples[a].lum.total_cmp(&samples[b].lum)) else {
        return (0.0, Vec::new());
    };
    let (min, r_min) = (samples[m].lum, samples[m].r);
    let drop = base - min;
    if drop <= 0.0 {
        return (drop, Vec::new());
    }
    let darkish = |s: &Sample| s.lum < base - drop * 0.5;
    let band = min + cfg.plateau_tol * drop + 0.5;
    let out = (0..samples.len())
        .filter(cand)
        .filter(|&i| {
            let s = &samples[i];
            // Con referencia, la autoexposición se cancela comparando contra el cuadro más oscuro.
            let lum = if has_ref && s.r > 1.0 && r_min > 1.0 { s.lum * r_min / s.r } else { s.lum };
            // Sin referencia la ganancia que sube disimula un borde de entrada: el primer cuadro oscuro no se usa.
            let lead_ok = has_ref || (i > 0 && darkish(&samples[i - 1]));
            let slack = if has_ref { 0.0 } else { NO_REF_AE_SLACK * min * i.abs_diff(m).min(4) as f32 };
            s.data.is_some() && darkish(s) && lum <= band + slack && lead_ok
        })
        .collect();
    (drop, out)
}

/// Mediana por píxel de `frames` (todos del mismo largo; de 1 a 8). Con cantidad par promedia los dos centrales.
pub fn median_stack(frames: &[&[u8]]) -> Vec<u8> {
    median_impl(frames, None)
}

fn median_impl(frames: &[&[u8]], gains: Option<&[f32]>) -> Vec<u8> {
    let n = frames.len();
    assert!((1..=8).contains(&n), "median_stack: de 1 a 8 cuadros");
    let len = frames[0].len();
    assert!(frames.iter().all(|f| f.len() == len), "median_stack: cuadros de distinto largo");
    // Con ganancia, cada cuadro pasa por su tabla antes de comparar.
    let luts: Option<Vec<[u8; 256]>> = gains
        .map(|gs| gs.iter().map(|g| std::array::from_fn(|v| (v as f32 * g + 0.5).clamp(0.0, 255.0) as u8)).collect());
    let mut out = vec![0u8; len];
    let threads = std::thread::available_parallelism().map_or(1, |n| n.get()).min(8);
    let chunk = len.div_ceil(threads).max(1 << 16);
    std::thread::scope(|sc| {
        for (k, dst) in out.chunks_mut(chunk).enumerate() {
            let luts = luts.as_deref();
            sc.spawn(move || {
                let off = k * chunk;
                let srcs: Vec<&[u8]> = frames.iter().map(|f| &f[off..off + dst.len()]).collect();
                median_chunk(&srcs, luts, dst);
            });
        }
    });
    out
}

fn median_chunk(srcs: &[&[u8]], luts: Option<&[[u8; 256]]>, dst: &mut [u8]) {
    let n = srcs.len();
    if n == 3 {
        let (a, b, c) = (srcs[0], srcs[1], srcs[2]);
        for (i, d) in dst.iter_mut().enumerate() {
            let (x, y, z) = match luts {
                Some(l) => (l[0][a[i] as usize], l[1][b[i] as usize], l[2][c[i] as usize]),
                None => (a[i], b[i], c[i]),
            };
            let (lo, hi) = if x < y { (x, y) } else { (y, x) };
            *d = if z < lo {
                lo
            } else if z > hi {
                hi
            } else {
                z
            };
        }
        return;
    }
    let mut v = [0u8; 8];
    for (i, d) in dst.iter_mut().enumerate() {
        for j in 0..n {
            v[j] = match luts {
                Some(l) => l[j][srcs[j][i] as usize],
                None => srcs[j][i],
            };
        }
        v[..n].sort_unstable();
        *d = if n % 2 == 1 { v[n / 2] } else { (v[n / 2 - 1] as u16 + v[n / 2] as u16).div_ceil(2) as u8 };
    }
}
