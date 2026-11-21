//! Anillo de cuadros en memoria compartida `pcx.cam.<id>`: un escritor, muchos lectores, el último cuadro gana.
//!
//! Mismo layout binario que `node/capture/src/pcx_capture/shm.py` (Python), así que un worker Python
//! (`RingReader`) lee lo que escribe Rust y viceversa. Todo u64 little-endian:
//!
//! ```text
//! [0:64)               global: magic "PCXRING1", (versión<<32)|n_slots, slot_capacity, head_seq, closed, writer_pid
//! [64:64+64n)          por slot: gen, seq, t_capture_ns, w, h, stride, fmt, nbytes
//! [data_off + i*cap)   datos del slot i (data_off y cap alineados a 4096)
//! ```
//!
//! Seqlock por slot: el escritor deja `gen` impar mientras escribe y par al terminar (con barreras
//! Release/Acquire), después publica `head_seq`. El lector lee el slot `head_seq % n` y valida que `gen` sea par,
//! igual antes y después, y que el `seq` del slot sea `head_seq`.

#[cfg(target_endian = "big")]
compile_error!("pcx-shm asume little-endian (mismo layout que la versión Python)");

mod seg;

use std::fmt;
use std::sync::atomic::{fence, AtomicU64, Ordering};
use std::time::{Duration, Instant};

pub const MAGIC: u64 = u64::from_le_bytes(*b"PCXRING1");
pub const VERSION: u64 = 1;
pub const HDR: usize = 64;
pub const SLOT_HDR: usize = 64;
pub const ALIGN: usize = 4096;
pub const DEFAULT_SLOTS: usize = 4;
/// Sondeo de `wait_newer` (igual que Python).
pub const DEFAULT_POLL: Duration = Duration::from_micros(200);

const G_MAGIC: usize = 0;
const G_VER_N: usize = 1;
const G_CAP: usize = 2;
const G_HEAD: usize = 3;
const G_CLOSED: usize = 4;
const G_PID: usize = 5;
const S_GEN: usize = 0;
const S_SEQ: usize = 1;
const S_TCAP: usize = 2;
const S_W: usize = 3;
const S_H: usize = 4;
const S_STRIDE: usize = 5;
const S_FMT: usize = 6;
const S_NBYTES: usize = 7;

/// Formato de píxel del slot (códigos compartidos con Python).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum PixelFormat {
    Bgr8 = 1,
    Gray8 = 2,
    Rgb8 = 3,
    Bgra8 = 4,
}

impl PixelFormat {
    pub fn code(self) -> u64 {
        self as u64
    }
    pub fn from_code(c: u64) -> Option<Self> {
        Some(match c {
            1 => Self::Bgr8,
            2 => Self::Gray8,
            3 => Self::Rgb8,
            4 => Self::Bgra8,
            _ => return None,
        })
    }
    pub fn channels(self) -> usize {
        match self {
            Self::Gray8 => 1,
            Self::Bgr8 | Self::Rgb8 => 3,
            Self::Bgra8 => 4,
        }
    }
    pub fn name(self) -> &'static str {
        match self {
            Self::Bgr8 => "bgr8",
            Self::Gray8 => "gray8",
            Self::Rgb8 => "rgb8",
            Self::Bgra8 => "bgra8",
        }
    }
}

#[derive(Debug)]
pub enum ShmError {
    Io(std::io::Error),
    /// Ya hay un escritor vivo con ese nombre (o, en Windows, alguien conserva un handle).
    Exists(String),
    NotARing(String),
    Version(u64),
    TooBig { nbytes: usize, capacity: usize },
    BadLength { expected: usize, got: usize },
    NameTooLong(String),
    /// El escritor cerró (o reemplazó) el anillo: volver a abrir el lector.
    Closed(String),
    Inconsistent(String),
}

impl fmt::Display for ShmError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Io(e) => write!(f, "io: {e}"),
            Self::Exists(n) => write!(f, "{n}: ya existe un escritor vivo"),
            Self::NotARing(n) => write!(f, "{n} no es un anillo pcx (o aún se está creando)"),
            Self::Version(v) => write!(f, "versión de anillo {v} != {VERSION}"),
            Self::TooBig { nbytes, capacity } => write!(f, "cuadro de {nbytes} B no cabe en el slot ({capacity} B)"),
            Self::BadLength { expected, got } => write!(f, "se esperaban {expected} B y llegaron {got}"),
            Self::NameTooLong(n) => write!(f, "nombre de segmento demasiado largo para macOS: {n:?} (máx. 30)"),
            Self::Closed(n) => write!(f, "{n}: el escritor cerró el anillo"),
            Self::Inconsistent(n) => write!(f, "{n}: no se logró una lectura consistente"),
        }
    }
}

impl std::error::Error for ShmError {}

impl From<std::io::Error> for ShmError {
    fn from(e: std::io::Error) -> Self {
        Self::Io(e)
    }
}

pub type Result<T> = std::result::Result<T, ShmError>;

/// `pcx.cam.<id>`; máx. 30 caracteres (macOS: PSHMNAMLEN = 31 con la "/").
pub fn segment_name(camera_id: &str) -> Result<String> {
    let name = format!("pcx.cam.{camera_id}");
    if name.len() > 30 {
        return Err(ShmError::NameTooLong(name));
    }
    Ok(name)
}

/// Metadatos de un cuadro leído.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct SlotMeta {
    pub seq: u64,
    pub t_capture_ns: u64,
    pub w: u32,
    pub h: u32,
    pub stride: u32,
    pub format: PixelFormat,
    pub slot: usize,
    pub gen: u64,
}

impl SlotMeta {
    pub fn nbytes(&self) -> usize {
        self.stride as usize * self.h as usize
    }
}

fn align(n: usize) -> usize {
    n.div_ceil(ALIGN) * ALIGN
}

fn data_off(n_slots: usize) -> usize {
    align(HDR + SLOT_HDR * n_slots)
}

fn now_ns() -> u64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos() as u64).unwrap_or(0)
}

/// Vista tipada sobre el segmento mapeado.
struct Layout {
    base: *mut u8,
    n_slots: usize,
    capacity: usize,
    data_off: usize,
}

impl Layout {
    #[inline]
    fn g(&self, i: usize) -> &AtomicU64 {
        unsafe { &*(self.base.add(i * 8) as *const AtomicU64) }
    }
    #[inline]
    fn s(&self, slot: usize, i: usize) -> &AtomicU64 {
        unsafe { &*(self.base.add(HDR + slot * SLOT_HDR + i * 8) as *const AtomicU64) }
    }
    #[inline]
    fn data(&self, slot: usize) -> *mut u8 {
        unsafe { self.base.add(self.data_off + slot * self.capacity) }
    }
}

// ------------------------------------------------------------------------------------------------ escritor

/// Dueño del segmento. `write()` copia el cuadro al siguiente slot (un memcpy) y lo publica.
/// Al soltarse (`Drop`) marca `closed` y hace `unlink` (POSIX) o suelta su handle (Windows).
pub struct RingWriter {
    name: String,
    lay: Layout,
    seq: u64,
    closed: bool,
    _seg: seg::Segment,
}

unsafe impl Send for RingWriter {}

impl RingWriter {
    /// Slots dimensionados para `max_w × max_h × channels`.
    pub fn create(camera_id: &str, max_w: usize, max_h: usize, channels: usize, n_slots: usize) -> Result<Self> {
        Self::with_capacity(camera_id, max_w * max_h * channels, n_slots)
    }

    pub fn with_capacity(camera_id: &str, slot_bytes: usize, n_slots: usize) -> Result<Self> {
        let name = segment_name(camera_id)?;
        let n_slots = n_slots.max(2);
        let capacity = align(slot_bytes.max(1));
        let size = data_off(n_slots) + capacity * n_slots;
        let segm = match seg::create(&name, size) {
            Ok(s) => s,
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
                if cfg!(windows) {
                    return Err(ShmError::Exists(name));
                }
                // Quedó de una corrida que murió con SIGKILL: se marca cerrado y se reemplaza.
                if let Ok(old) = seg::open(&name, true) {
                    let lay = Layout { base: old.ptr, n_slots: 0, capacity: 0, data_off: 0 };
                    let pid = lay.g(G_PID).load(Ordering::Acquire) as u32;
                    let closed = lay.g(G_CLOSED).load(Ordering::Acquire) != 0;
                    if pid != 0 && pid != std::process::id() && seg::pid_alive(pid) && !closed {
                        return Err(ShmError::Exists(format!("{name} (pid {pid})")));
                    }
                    lay.g(G_CLOSED).store(1, Ordering::Release);
                }
                let _ = seg::unlink(&name);
                seg::create(&name, size)?
            }
            Err(e) => return Err(e.into()),
        };
        let lay = Layout { base: segm.ptr, n_slots, capacity, data_off: data_off(n_slots) };
        for slot in 0..n_slots {
            for i in 0..8 {
                lay.s(slot, i).store(0, Ordering::Relaxed);
            }
        }
        for i in 0..8 {
            lay.g(i).store(0, Ordering::Relaxed);
        }
        lay.g(G_VER_N).store((VERSION << 32) | n_slots as u64, Ordering::Relaxed);
        lay.g(G_CAP).store(capacity as u64, Ordering::Relaxed);
        lay.g(G_PID).store(std::process::id() as u64, Ordering::Relaxed);
        lay.g(G_MAGIC).store(MAGIC, Ordering::Release); // al final: los lectores esperan el magic
        Ok(Self { name, lay, seq: 0, closed: false, _seg: segm })
    }

    pub fn name(&self) -> &str {
        &self.name
    }
    pub fn seq(&self) -> u64 {
        self.seq
    }
    pub fn capacity(&self) -> usize {
        self.lay.capacity
    }
    pub fn n_slots(&self) -> usize {
        self.lay.n_slots
    }

    /// Copia un cuadro empaquetado (`stride = w × canales`). `t_capture_ns = None` usa la hora actual.
    pub fn write(&mut self, data: &[u8], w: u32, h: u32, fmt: PixelFormat, t_capture_ns: Option<u64>) -> Result<u64> {
        let expected = w as usize * h as usize * fmt.channels();
        if data.len() != expected {
            return Err(ShmError::BadLength { expected, got: data.len() });
        }
        self.write_with(w, h, fmt, t_capture_ns, |dst| dst.copy_from_slice(data))
    }

    /// Escribe directo en el slot (p. ej. decodificar o convertir sin búfer intermedio). `fill` recibe exactamente
    /// `w × h × canales` bytes.
    pub fn write_with<F: FnOnce(&mut [u8])>(
        &mut self,
        w: u32,
        h: u32,
        fmt: PixelFormat,
        t_capture_ns: Option<u64>,
        fill: F,
    ) -> Result<u64> {
        if self.closed {
            return Err(ShmError::Closed(self.name.clone()));
        }
        let stride = w as usize * fmt.channels();
        let nbytes = stride * h as usize;
        if nbytes > self.lay.capacity {
            return Err(ShmError::TooBig { nbytes, capacity: self.lay.capacity });
        }
        let seq = self.seq + 1;
        let i = (seq % self.lay.n_slots as u64) as usize;
        let gen = self.lay.s(i, S_GEN).load(Ordering::Relaxed);
        self.lay.s(i, S_GEN).store(gen + 1, Ordering::Relaxed); // impar: escribiendo
        fence(Ordering::Release);
        let dst = unsafe { std::slice::from_raw_parts_mut(self.lay.data(i), nbytes) };
        fill(dst);
        let s = |k: usize, v: u64| self.lay.s(i, k).store(v, Ordering::Relaxed);
        s(S_SEQ, seq);
        s(S_TCAP, t_capture_ns.unwrap_or_else(now_ns));
        s(S_W, w as u64);
        s(S_H, h as u64);
        s(S_STRIDE, stride as u64);
        s(S_FMT, fmt.code());
        s(S_NBYTES, nbytes as u64);
        self.lay.s(i, S_GEN).store(gen + 2, Ordering::Release); // par: listo
        self.lay.g(G_HEAD).store(seq, Ordering::Release);
        self.seq = seq;
        Ok(seq)
    }

    /// Marca `closed` y libera el nombre. Idempotente; también lo hace `Drop`.
    pub fn close(&mut self) {
        if self.closed {
            return;
        }
        self.closed = true;
        self.lay.g(G_CLOSED).store(1, Ordering::Release);
        let _ = seg::unlink(&self.name);
    }
}

impl Drop for RingWriter {
    fn drop(&mut self) {
        self.close();
    }
}

// ------------------------------------------------------------------------------------------------ lector

/// Se adjunta a `pcx.cam.<id>` en solo lectura.
pub struct RingReader {
    name: String,
    lay: Layout,
    poll: Duration,
    retries: u64,
    _seg: seg::Segment,
}

unsafe impl Send for RingReader {}

impl RingReader {
    /// Se adjunta; si el segmento no existe o está a medio crear reintenta hasta `timeout`.
    pub fn open(camera_id: &str, timeout: Duration) -> Result<Self> {
        let name = segment_name(camera_id)?;
        let deadline = Instant::now() + timeout;
        loop {
            let err = match seg::open(&name, false) {
                Ok(s) => {
                    let probe = Layout { base: s.ptr, n_slots: 0, capacity: 0, data_off: 0 };
                    if probe.g(G_MAGIC).load(Ordering::Acquire) == MAGIC {
                        let ver_n = probe.g(G_VER_N).load(Ordering::Relaxed);
                        if ver_n >> 32 != VERSION {
                            return Err(ShmError::Version(ver_n >> 32));
                        }
                        let n_slots = (ver_n & 0xFFFF_FFFF) as usize;
                        let capacity = probe.g(G_CAP).load(Ordering::Relaxed) as usize;
                        if n_slots == 0 || s.len < data_off(n_slots) + capacity * n_slots {
                            return Err(ShmError::NotARing(name));
                        }
                        let lay = Layout { base: s.ptr, n_slots, capacity, data_off: data_off(n_slots) };
                        return Ok(Self { name, lay, poll: DEFAULT_POLL, retries: 0, _seg: s });
                    }
                    ShmError::NotARing(name.clone())
                }
                Err(e) => ShmError::Io(e),
            };
            if Instant::now() >= deadline {
                return Err(err);
            }
            std::thread::sleep(Duration::from_millis(10));
        }
    }

    pub fn name(&self) -> &str {
        &self.name
    }
    pub fn n_slots(&self) -> usize {
        self.lay.n_slots
    }
    pub fn capacity(&self) -> usize {
        self.lay.capacity
    }
    /// Lecturas partidas detectadas y reintentadas.
    pub fn retries(&self) -> u64 {
        self.retries
    }
    pub fn set_poll(&mut self, poll: Duration) {
        self.poll = poll;
    }
    pub fn closed(&self) -> bool {
        self.lay.g(G_CLOSED).load(Ordering::Acquire) != 0
    }
    pub fn latest_seq(&self) -> u64 {
        self.lay.g(G_HEAD).load(Ordering::Acquire)
    }
    pub fn writer_pid(&self) -> u32 {
        self.lay.g(G_PID).load(Ordering::Relaxed) as u32
    }
    /// True si el slot de una vista sin copia no fue sobrescrito desde que se leyó.
    pub fn still_valid(&self, meta: &SlotMeta) -> bool {
        fence(Ordering::Acquire);
        self.lay.s(meta.slot, S_GEN).load(Ordering::Relaxed) == meta.gen
    }

    /// Lee el último cuadro completo con `f(meta, bytes)` (cero copias si `f` no copia) y lo valida con el seqlock;
    /// si el escritor lo pisó durante `f`, reintenta. `None` si aún no hay cuadros.
    fn read_latest<R>(&mut self, mut f: impl FnMut(&SlotMeta, &[u8]) -> R) -> Result<Option<(SlotMeta, R)>> {
        for _ in 0..1000 {
            if self.closed() {
                return Err(ShmError::Closed(self.name.clone()));
            }
            let head = self.lay.g(G_HEAD).load(Ordering::Acquire);
            if head == 0 {
                return Ok(None);
            }
            let i = (head % self.lay.n_slots as u64) as usize;
            let gen = self.lay.s(i, S_GEN).load(Ordering::Acquire);
            let ld = |k: usize| self.lay.s(i, k).load(Ordering::Relaxed);
            if gen & 1 == 1 || ld(S_SEQ) != head {
                self.retries += 1;
                std::hint::spin_loop();
                continue;
            }
            let (w, h, stride, code, tcap, nbytes) = (ld(S_W), ld(S_H), ld(S_STRIDE), ld(S_FMT), ld(S_TCAP), ld(S_NBYTES));
            let Some(format) = PixelFormat::from_code(code) else {
                self.retries += 1;
                continue;
            };
            if nbytes as usize > self.lay.capacity {
                self.retries += 1;
                continue;
            }
            let meta = SlotMeta { seq: head, t_capture_ns: tcap, w: w as u32, h: h as u32, stride: stride as u32, format, slot: i, gen };
            let bytes = unsafe { std::slice::from_raw_parts(self.lay.data(i) as *const u8, nbytes as usize) };
            let r = f(&meta, bytes);
            fence(Ordering::Acquire);
            if self.lay.s(i, S_GEN).load(Ordering::Relaxed) != gen {
                self.retries += 1;
                continue;
            }
            return Ok(Some((meta, r)));
        }
        Err(ShmError::Inconsistent(self.name.clone()))
    }

    /// Último cuadro copiado en `buf` (se reutiliza la memoria). `None` si aún no hay cuadros.
    pub fn latest_into(&mut self, buf: &mut Vec<u8>) -> Result<Option<SlotMeta>> {
        Ok(self
            .read_latest(|_, src| {
                buf.clear();
                buf.extend_from_slice(src);
            })?
            .map(|(m, _)| m))
    }

    /// Último cuadro copiado en un `Vec` nuevo.
    pub fn latest(&mut self) -> Result<Option<(SlotMeta, Vec<u8>)>> {
        self.read_latest(|_, src| src.to_vec())
    }

    /// Cero copias: `f` ve los bytes del slot. Si el escritor lo pisó mientras `f` corría, se reintenta (así que `f`
    /// puede llamarse más de una vez); lo devuelto es consistente.
    pub fn with_latest<R>(&mut self, f: impl FnMut(&SlotMeta, &[u8]) -> R) -> Result<Option<(SlotMeta, R)>> {
        self.read_latest(f)
    }

    /// Bloquea (sondeo `poll`) hasta un cuadro con `seq > after` y lo copia en `buf`. `None` si vence `timeout`.
    pub fn wait_newer(&mut self, after: u64, timeout: Duration, buf: &mut Vec<u8>) -> Result<Option<SlotMeta>> {
        if !self.wait_head(after, timeout)? {
            return Ok(None);
        }
        self.latest_into(buf)
    }

    /// Como `wait_newer` pero cero copias.
    pub fn wait_newer_with<R>(
        &mut self,
        after: u64,
        timeout: Duration,
        f: impl FnMut(&SlotMeta, &[u8]) -> R,
    ) -> Result<Option<(SlotMeta, R)>> {
        if !self.wait_head(after, timeout)? {
            return Ok(None);
        }
        self.read_latest(f)
    }

    fn wait_head(&self, after: u64, timeout: Duration) -> Result<bool> {
        let deadline = Instant::now() + timeout;
        while self.latest_seq() <= after {
            if self.closed() {
                return Err(ShmError::Closed(self.name.clone()));
            }
            if Instant::now() >= deadline {
                return Ok(false);
            }
            std::thread::sleep(self.poll);
        }
        Ok(true)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cid(tag: &str) -> String {
        format!("u{tag}{}", std::process::id() % 100_000)
    }

    #[test]
    fn roundtrip_latest_wins_and_wrap() {
        let id = cid("rt");
        let mut w = RingWriter::create(&id, 32, 16, 3, 4).unwrap();
        let mut r = RingReader::open(&id, Duration::ZERO).unwrap();
        assert!(r.latest().unwrap().is_none());
        for s in 1..=10u64 {
            w.write(&vec![s as u8; 32 * 16 * 3], 32, 16, PixelFormat::Bgr8, Some(1000 + s)).unwrap();
        }
        let (m, data) = r.latest().unwrap().unwrap();
        assert_eq!((m.seq, m.t_capture_ns, m.w, m.h, m.stride, m.format), (10, 1010, 32, 16, 96, PixelFormat::Bgr8));
        assert!(data.iter().all(|&b| b == 10));
        let (mv, sum) = r.with_latest(|_, b| b.iter().map(|&x| x as u64).sum::<u64>()).unwrap().unwrap();
        assert_eq!(sum, 10 * 32 * 16 * 3);
        w.write(&vec![11; 32 * 16 * 3], 32, 16, PixelFormat::Bgr8, None).unwrap();
        assert!(r.still_valid(&mv));
        for s in 12..=15u8 {
            w.write(&vec![s; 32 * 16 * 3], 32, 16, PixelFormat::Bgr8, None).unwrap();
        }
        assert!(!r.still_valid(&mv));
        let mut buf = Vec::new();
        assert!(r.wait_newer(15, Duration::from_millis(20), &mut buf).unwrap().is_none());
        assert_eq!(r.wait_newer(14, Duration::from_millis(20), &mut buf).unwrap().unwrap().seq, 15);
        assert_eq!(segment_name("cam0").unwrap(), "pcx.cam.cam0");
        assert!(segment_name("una-camara-con-nombre-largo").is_err());
    }

    #[test]
    fn gray_too_big_and_close() {
        let id = cid("gr");
        let mut w = RingWriter::create(&id, 32, 16, 3, 4).unwrap();
        let mut r = RingReader::open(&id, Duration::ZERO).unwrap();
        w.write(&[7; 32 * 16], 32, 16, PixelFormat::Gray8, None).unwrap();
        let (m, d) = r.latest().unwrap().unwrap();
        assert_eq!((m.format, d.len()), (PixelFormat::Gray8, 32 * 16));
        assert!(matches!(w.write(&[0; 64 * 32 * 3], 64, 32, PixelFormat::Bgr8, None), Err(ShmError::TooBig { .. })));
        drop(w);
        assert!(r.closed());
        assert!(matches!(r.latest(), Err(ShmError::Closed(_))));
        drop(r);
        assert!(RingReader::open(&id, Duration::ZERO).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn stale_segment_is_replaced_but_live_writer_is_not() {
        let id = cid("st");
        let w1 = RingWriter::create(&id, 8, 8, 3, 4).unwrap();
        // Mismo pid: se trata como huérfano (simula SIGKILL) y se reemplaza; el lector viejo ve `closed`.
        let r_old = RingReader::open(&id, Duration::ZERO).unwrap();
        std::mem::forget(w1);
        let mut w2 = RingWriter::create(&id, 8, 8, 3, 4).unwrap();
        assert!(r_old.closed());
        let mut r = RingReader::open(&id, Duration::ZERO).unwrap();
        w2.write(&[1; 8 * 8 * 3], 8, 8, PixelFormat::Bgr8, None).unwrap();
        assert_eq!(r.latest().unwrap().unwrap().0.seq, 1);
    }
}
