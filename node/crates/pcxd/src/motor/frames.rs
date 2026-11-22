//! Cuadros del motor: de su cámara propia (`pcx-capture`) o, sin cámara, de la web app (`need_frame` → `frame`).
//!
//! [`FrameHub`] es el buzón común: guarda el último cuadro con un `seq` creciente y deja esperar el siguiente desde
//! hilos bloqueantes (la foto limpia). Con cámara propia lo alimenta una bomba ([`Camera`]); en modo remoto, cada
//! lector llama a [`FrameHub::demand`], que hace salir un `need_frame`, y el cuadro que vuelve entra con la hora en
//! que se pidió: es una cota inferior de su captura, así «posterior a `blanked`» sigue siendo cierto.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

use base64::Engine as _;
use image::RgbImage;
use pcx_capture::sources::camera as cams;
use pcx_capture::{open_source, Frame, SourceOpts, SourceSpec};
use tokio::sync::mpsc;
use tokio_util::sync::CancellationToken;

use super::proto::CameraMsg;
use super::synthetic::Synthetic;

const LOG: &str = "[pcx motor]";
/// Un `need_frame` sin respuesta se da por perdido y se repite.
const REMOTE_RETRY: Duration = Duration::from_millis(700);
/// Lado mayor máximo de un cuadro (cámara o `frame` remoto).
pub const MAX_SIDE: u32 = 4096;

pub fn now_ns() -> u64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos() as u64).unwrap_or(0)
}

#[derive(Default)]
struct Slot {
    seq: u64,
    frame: Option<Arc<Frame>>,
}

#[derive(Default)]
struct Remote {
    enabled: bool,
    n: u64,
    /// id del `need_frame` → ns de época en que se pidió.
    pending: HashMap<String, u64>,
    last: Option<Instant>,
}

#[derive(Default)]
struct CamState {
    info: Option<CameraMsg>,
    /// Tamaño al que se entregan los cuadros (`calib.camera_px`); la bomba reescala si la fuente no lo da.
    target: Option<(u32, u32)>,
}

pub struct FrameHub {
    slot: Mutex<Slot>,
    cv: Condvar,
    remote: Mutex<Remote>,
    cam: Mutex<CamState>,
    need: mpsc::UnboundedSender<String>,
}

impl FrameHub {
    /// El receptor entrega los ids de `need_frame` que hay que mandar a la web app.
    pub fn new() -> (Arc<Self>, mpsc::UnboundedReceiver<String>) {
        let (need, rx) = mpsc::unbounded_channel();
        let hub = FrameHub {
            slot: Mutex::default(),
            cv: Condvar::new(),
            remote: Mutex::default(),
            cam: Mutex::default(),
            need,
        };
        (Arc::new(hub), rx)
    }

    pub fn put(&self, frame: Arc<Frame>) -> u64 {
        let mut s = self.slot.lock().unwrap();
        s.seq += 1;
        s.frame = Some(frame);
        self.cv.notify_all();
        s.seq
    }

    pub fn latest(&self) -> Option<(u64, Arc<Frame>)> {
        let s = self.slot.lock().unwrap();
        s.frame.clone().map(|f| (s.seq, f))
    }

    /// Espera hasta `timeout` un cuadro con `seq > after`. Bloquea el hilo: solo desde `spawn_blocking`.
    pub fn wait_newer(&self, after: u64, timeout: Duration) -> Option<(u64, Arc<Frame>)> {
        let s = self.slot.lock().unwrap();
        let (s, _) = self.cv.wait_timeout_while(s, timeout, |s| s.seq <= after || s.frame.is_none()).unwrap();
        (s.seq > after).then(|| s.frame.clone().map(|f| (s.seq, f))).flatten()
    }

    /// Primer cuadro capturado (o pedido) desde `t_min_ns`, esperando hasta `timeout`.
    pub fn wait_fresh(&self, t_min_ns: u64, timeout: Duration) -> Option<Arc<Frame>> {
        let deadline = Instant::now() + timeout;
        let mut after = 0;
        loop {
            self.demand();
            let left = deadline.saturating_duration_since(Instant::now());
            match self.wait_newer(after, left.min(Duration::from_millis(50))) {
                Some((_, f)) if f.t_capture_ns >= t_min_ns => return Some(f),
                Some((seq, _)) => after = seq,
                None => {}
            }
            if left.is_zero() {
                return None;
            }
        }
    }

    pub fn needs_frames(&self) -> bool {
        self.remote.lock().unwrap().enabled
    }

    pub fn set_remote(&self, enabled: bool) {
        let mut r = self.remote.lock().unwrap();
        r.enabled = enabled;
        r.pending.clear();
        r.last = None;
    }

    /// Modo remoto: pide un cuadro a la web app si no hay un pedido en vuelo. Con cámara propia no hace nada.
    pub fn demand(&self) {
        let mut r = self.remote.lock().unwrap();
        if !r.enabled || r.last.is_some_and(|t| t.elapsed() < REMOTE_RETRY) && !r.pending.is_empty() {
            return;
        }
        r.n += 1;
        let id = format!("f-{}", r.n);
        if r.pending.len() >= 4 {
            r.pending.clear();
        }
        r.pending.insert(id.clone(), now_ns());
        r.last = Some(Instant::now());
        let _ = self.need.send(id);
    }

    /// ¿Hay un `need_frame` con ese id esperando respuesta?
    pub fn is_pending(&self, id: &str) -> bool {
        self.remote.lock().unwrap().pending.contains_key(id)
    }

    /// Cuadro que respondió a `need_frame{id}`. Los que nadie pidió se descartan.
    pub fn accept_remote(&self, id: &str, img: RgbImage) -> bool {
        let t_req = {
            let mut r = self.remote.lock().unwrap();
            let Some(t) = r.pending.remove(id) else { return false };
            r.last = None;
            t
        };
        let img = match self.target() {
            Some(size) => pcx_capture::frame::fit(&img, Some(size)).into_owned(),
            None => img,
        };
        self.put(Arc::new(Frame { img, t_capture_ns: t_req }));
        true
    }

    pub fn camera(&self) -> Option<CameraMsg> {
        self.cam.lock().unwrap().info.clone()
    }

    fn target(&self) -> Option<(u32, u32)> {
        self.cam.lock().unwrap().target
    }
}

/// Qué cámara usa el motor (`--camera`).
#[derive(Clone, Debug)]
pub enum CameraSpec {
    /// Sin cámara propia: `needs_frames: true`.
    None,
    Source {
        spec: SourceSpec,
        id: String,
        name: String,
    },
    Synthetic(Synthetic),
}

impl CameraSpec {
    /// `--camera <nombre|índice>`: subcadena única del nombre o id estable, o la posición en `pcx cameras`. También
    /// `replay:<carpeta|archivo.mjpeg>` (o cualquier fuente de `pcx-capture`), `synthetic` y `none`.
    pub fn parse(arg: Option<&str>, size: (u32, u32)) -> Result<CameraSpec, String> {
        let Some(arg) = arg.map(str::trim).filter(|a| !a.is_empty() && *a != "none") else {
            return Ok(CameraSpec::None);
        };
        if arg == "synthetic" {
            return Ok(CameraSpec::Synthetic(Synthetic::demo(size)));
        }
        let kind = arg.split_once(':').map(|(k, _)| k);
        if matches!(kind, Some("replay" | "mjpeg" | "uvc" | "cam")) {
            let spec: SourceSpec = arg.parse()?;
            if let SourceSpec::Cam(sel) = &spec {
                return Self::local(sel);
            }
            let id = match &spec {
                SourceSpec::Replay(p) => {
                    PathBuf::from(p).file_name().map_or("replay".into(), |n| n.to_string_lossy().into())
                }
                _ => spec.adapter().to_string(),
            };
            return Ok(CameraSpec::Source { spec, id, name: arg.to_string() });
        }
        Self::local(arg)
    }

    /// Cámara local por nombre, id o índice. Se resuelve contra la lista del sistema, sin abrir ninguna.
    fn local(sel: &str) -> Result<CameraSpec, String> {
        let list = cams::list().map_err(|e| e.to_string())?;
        let i = match sel.parse::<usize>() {
            Ok(i) if !list.iter().any(|c| c.id == sel) => {
                if i >= list.len() {
                    return Err(format!("no hay cámara con índice {i}: `pcx cameras` lista {}", list.len()));
                }
                i
            }
            _ => cams::select(&list, sel).map_err(|e| e.to_string())?,
        };
        let c = &list[i];
        // Siempre se abre por el id estable: el orden cambia cuando entra o sale una cámara (Continuity).
        Ok(CameraSpec::Source { spec: SourceSpec::Cam(c.id.clone()), id: c.id.clone(), name: c.name.clone() })
    }
}

#[derive(Clone, Copy, Debug, Default)]
pub struct CameraOpts {
    pub fps: Option<f64>,
    pub lock_exposure: bool,
}

/// Cámara propia en marcha: una bomba que deja cada cuadro en el [`FrameHub`], al tamaño pedido por la calibración.
pub struct Camera {
    spec: CameraSpec,
    opts: CameraOpts,
    hub: Arc<FrameHub>,
    /// Tamaño con que está abierta la fuente.
    native: (u32, u32),
    pump: Option<(CancellationToken, tokio::task::JoinHandle<()>)>,
}

impl Camera {
    /// Abre la cámara. Si no se puede, avisa y el motor sigue sin cámara (`needs_frames: true`).
    pub async fn start(spec: CameraSpec, size: (u32, u32), opts: CameraOpts, hub: Arc<FrameHub>) -> Camera {
        let mut cam = Camera { spec, opts, hub, native: size, pump: None };
        if let Err(e) = cam.open(size).await {
            eprintln!("{LOG} no pude abrir la cámara: {e}");
            eprintln!("{LOG} sigo sin cámara propia: los cuadros se le piden a la web app (needs_frames)");
            cam.fall_back();
        }
        cam
    }

    pub fn synthetic(&self) -> Option<&Synthetic> {
        match &self.spec {
            CameraSpec::Synthetic(s) => Some(s),
            _ => None,
        }
    }

    fn fall_back(&mut self) {
        self.spec = CameraSpec::None;
        self.hub.cam.lock().unwrap().info = None;
        self.hub.set_remote(true);
    }

    async fn close(&mut self) {
        if let Some((cancel, task)) = self.pump.take() {
            cancel.cancel();
            let _ = task.await;
        }
    }

    async fn open(&mut self, size: (u32, u32)) -> Result<(), String> {
        self.close().await;
        let (id, name) = match &self.spec {
            CameraSpec::None => {
                self.hub.cam.lock().unwrap().target = Some(size);
                self.hub.set_remote(true);
                return Ok(());
            }
            CameraSpec::Source { id, name, .. } => (id.clone(), name.clone()),
            CameraSpec::Synthetic(_) => ("synthetic".to_string(), "cámara sintética".to_string()),
        };
        let cancel = CancellationToken::new();
        let task = match &self.spec {
            CameraSpec::Synthetic(s) => {
                let (tx, rx) = pcx_capture::frame::latest_box();
                s.spawn(tx).map_err(|e| e.to_string())?;
                self.native = s.size();
                tokio::spawn(pump(rx, None, self.hub.clone(), cancel.clone()))
            }
            CameraSpec::Source { spec, .. } => {
                let o = SourceOpts {
                    size: Some(size),
                    fps: self.opts.fps,
                    controls: cams::CamControls { lock_exposure: self.opts.lock_exposure, ..Default::default() },
                    ..Default::default()
                };
                let src = open_source(spec, &o).await.map_err(|e| e.to_string())?;
                self.native = size;
                let (rx, keep) = (src.rx, src.phone);
                let (spec, hub) = (spec.clone(), self.hub.clone());
                tokio::spawn(pump_source(rx, keep, spec, o, hub, cancel.clone()))
            }
            CameraSpec::None => unreachable!(),
        };
        self.pump = Some((cancel, task));
        let mut cam = self.hub.cam.lock().unwrap();
        cam.info = Some(CameraMsg { id, name, px: [size.0, size.1] });
        cam.target = Some(size);
        drop(cam);
        self.hub.set_remote(false);
        Ok(())
    }

    /// Deja los cuadros al tamaño de `calib.camera_px`: reabre la fuente a ese tamaño; si la cámara no lo ofrece,
    /// vuelve al anterior y reescala; si tampoco abre, el motor sigue sin cámara propia.
    pub async fn ensure_size(&mut self, size: (u32, u32)) {
        if self.hub.target() == Some(size) {
            return;
        }
        if matches!(self.spec, CameraSpec::None | CameraSpec::Synthetic(_)) {
            let mut cam = self.hub.cam.lock().unwrap();
            cam.target = Some(size);
            if let Some(info) = cam.info.as_mut() {
                info.px = [size.0, size.1];
            }
            return;
        }
        let before = self.native;
        // AVFoundation tarda un instante en soltar la sesión anterior.
        self.close().await;
        tokio::time::sleep(Duration::from_millis(150)).await;
        if let Err(e) = self.open(size).await {
            eprintln!(
                "{LOG} la cámara no abre a {}x{} ({e}); la dejo a {}x{} y reescalo",
                size.0, size.1, before.0, before.1
            );
            tokio::time::sleep(Duration::from_millis(150)).await;
            match self.open(before).await {
                Ok(()) => {
                    let mut cam = self.hub.cam.lock().unwrap();
                    cam.target = Some(size);
                    if let Some(info) = cam.info.as_mut() {
                        info.px = [size.0, size.1];
                    }
                }
                Err(e) => {
                    eprintln!("{LOG} la cámara no volvió a abrir ({e}): sigo pidiendo cuadros a la web app");
                    self.fall_back();
                    self.hub.cam.lock().unwrap().target = Some(size);
                }
            }
        }
    }

    pub async fn stop(mut self) {
        self.close().await;
    }
}

/// Copia cada cuadro de la fuente al buzón (reescalado si hace falta) hasta que la fuente termine o se cancele.
/// Devuelve `true` si la fuente terminó sola.
async fn pump(
    mut rx: pcx_capture::frame::LatestRx,
    keep: Option<pcx_capture::sources::phone::PhoneServer>,
    hub: Arc<FrameHub>,
    cancel: CancellationToken,
) {
    let _keep = keep;
    loop {
        tokio::select! {
            _ = cancel.cancelled() => return,
            r = rx.changed() => if r.is_err() { return },
        }
        let Some(f) = rx.borrow_and_update().clone() else { continue };
        let f = match hub.target() {
            Some(size) if f.img.dimensions() != size => {
                let img = pcx_capture::frame::fit(&f.img, Some(size)).into_owned();
                Arc::new(Frame { img, t_capture_ns: f.t_capture_ns })
            }
            _ => f,
        };
        hub.put(f);
    }
}

/// Bomba de una fuente real: si la cámara se cae (desconexión, 3 s sin cuadros), la reabre cada 3 s.
async fn pump_source(
    rx: pcx_capture::frame::LatestRx,
    keep: Option<pcx_capture::sources::phone::PhoneServer>,
    spec: SourceSpec,
    opts: SourceOpts,
    hub: Arc<FrameHub>,
    cancel: CancellationToken,
) {
    let mut cur = Some((rx, keep));
    loop {
        if let Some((rx, keep)) = cur.take() {
            pump(rx, keep, hub.clone(), cancel.clone()).await;
        }
        if cancel.is_cancelled() {
            return;
        }
        eprintln!("{LOG} la cámara dejó de entregar cuadros; reintento en 3 s");
        tokio::select! { _ = cancel.cancelled() => return, _ = tokio::time::sleep(Duration::from_secs(3)) => {} }
        match open_source(&spec, &opts).await {
            Ok(src) => cur = Some((src.rx, src.phone)),
            Err(e) => eprintln!("{LOG} la cámara no reabre: {e}"),
        }
    }
}

/// JPEG en base64 (lo que viaja en `look_frame`).
pub fn jpeg_b64(img: &RgbImage, quality: u8) -> Result<String, String> {
    let mut buf = Vec::new();
    image::codecs::jpeg::JpegEncoder::new_with_quality(&mut buf, quality)
        .encode_image(img)
        .map_err(|e| e.to_string())?;
    Ok(base64::engine::general_purpose::STANDARD.encode(buf))
}

/// Decodifica el `jpeg_b64` de un `frame` (acepta también una `data:` URL). Rechaza imágenes desmedidas.
pub fn decode_jpeg_b64(b64: &str) -> Result<RgbImage, String> {
    let b64 = b64.rsplit_once("base64,").map_or(b64, |(_, d)| d).trim();
    let bytes = base64::engine::general_purpose::STANDARD.decode(b64).map_err(|e| format!("base64 inválido: {e}"))?;
    let mut dec = image::codecs::jpeg::JpegDecoder::new(std::io::Cursor::new(&bytes)).map_err(|e| e.to_string())?;
    let (w, h) = image::ImageDecoder::dimensions(&dec);
    if w == 0 || h == 0 || w > MAX_SIDE || h > MAX_SIDE {
        return Err(format!("imagen de {w}x{h} fuera de rango"));
    }
    let mut limits = image::Limits::default();
    limits.max_alloc = Some(256 << 20);
    image::ImageDecoder::set_limits(&mut dec, limits).map_err(|e| e.to_string())?;
    Ok(image::DynamicImage::from_decoder(dec).map_err(|e| e.to_string())?.into_rgb8())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn frame(v: u8, t: u64) -> Arc<Frame> {
        Arc::new(Frame { img: RgbImage::from_pixel(8, 6, image::Rgb([v, v, v])), t_capture_ns: t })
    }

    #[test]
    fn buzon_ultimo_gana_y_espera() {
        let (hub, _need) = FrameHub::new();
        assert!(hub.latest().is_none());
        assert!(hub.wait_newer(0, Duration::from_millis(10)).is_none());
        assert_eq!(hub.put(frame(1, 10)), 1);
        assert_eq!(hub.put(frame(2, 20)), 2);
        assert_eq!(hub.wait_newer(0, Duration::ZERO).unwrap().0, 2);
        assert!(hub.wait_newer(2, Duration::from_millis(10)).is_none());
        let h2 = hub.clone();
        let t = std::thread::spawn(move || h2.wait_newer(2, Duration::from_secs(5)).map(|(s, f)| (s, f.t_capture_ns)));
        std::thread::sleep(Duration::from_millis(30));
        hub.put(frame(3, 30));
        assert_eq!(t.join().unwrap(), Some((3, 30)));
    }

    #[test]
    fn modo_remoto_pide_de_a_uno_y_fecha_por_pedido() {
        let (hub, mut need) = FrameHub::new();
        hub.demand();
        assert!(need.try_recv().is_err(), "con cámara propia no se pide nada");
        hub.set_remote(true);
        let t0 = now_ns();
        hub.demand();
        hub.demand();
        let id = need.try_recv().unwrap();
        assert!(need.try_recv().is_err(), "un solo pedido en vuelo");
        assert!(hub.is_pending(&id) && !hub.is_pending("f-99"));
        assert!(!hub.accept_remote("f-99", RgbImage::new(4, 4)), "cuadro que nadie pidió");
        assert!(hub.accept_remote(&id, RgbImage::new(4, 4)));
        assert!(!hub.accept_remote(&id, RgbImage::new(4, 4)), "un id sirve una vez");
        let (_, f) = hub.latest().unwrap();
        assert!(f.t_capture_ns >= t0 && f.t_capture_ns <= now_ns(), "se fecha con la hora del pedido");
        hub.demand();
        assert_ne!(need.try_recv().unwrap(), id);
    }

    #[test]
    fn jpeg_ida_y_vuelta_y_limites() {
        let img = RgbImage::from_fn(32, 20, |x, y| image::Rgb([(x * 8) as u8, (y * 12) as u8, 90]));
        let b64 = jpeg_b64(&img, 90).unwrap();
        let back = decode_jpeg_b64(&format!("data:image/jpeg;base64,{b64}")).unwrap();
        assert_eq!(back.dimensions(), (32, 20));
        let err: i32 = img.pixels().zip(back.pixels()).map(|(a, b)| (a[0] as i32 - b[0] as i32).abs()).max().unwrap();
        assert!(err < 24, "error máximo {err}");
        assert!(decode_jpeg_b64("no es base64 !!").is_err());
        assert!(decode_jpeg_b64("aGVsbG8=").is_err(), "base64 válido que no es JPEG");
    }

    #[test]
    fn camara_sin_abrir_nada() {
        assert!(matches!(CameraSpec::parse(None, (640, 360)).unwrap(), CameraSpec::None));
        assert!(matches!(CameraSpec::parse(Some("none"), (640, 360)).unwrap(), CameraSpec::None));
        assert!(matches!(CameraSpec::parse(Some("synthetic"), (640, 360)).unwrap(), CameraSpec::Synthetic(_)));
        match CameraSpec::parse(Some("replay:/tmp/mesa/cuadros"), (640, 360)).unwrap() {
            CameraSpec::Source { spec: SourceSpec::Replay(p), id, name } => {
                assert_eq!(
                    (p, id.as_str(), name.as_str()),
                    (PathBuf::from("/tmp/mesa/cuadros"), "cuadros", "replay:/tmp/mesa/cuadros")
                );
            }
            other => panic!("{other:?}"),
        }
        assert!(CameraSpec::parse(Some("uvc:x"), (640, 360)).is_err());
    }
}
