//! `cam` en macOS: AVFoundation nativo con `objc2` (reemplaza a nokhwa; bugs B4, B6–B10 de
//! `docs/pruebas/hardware-manu/01-smoke.md`). Referencia probada: `qa-runs/smoke-01/scripts/c920cap.swift`.
//!
//! - Abre por `uniqueID` exacto o por subcadena única del nombre; nunca por índice (Continuity cambia el orden).
//! - Elige un formato que la cámara ofrece de verdad: 420v (NV12, rango video) al tamaño pedido con un rango de fps
//!   que alcance el pedido, y usa el `minFrameDuration` exacto de ese rango (B7). Lo reaplica tras `startRunning`,
//!   porque la sesión lo cambia al arrancar (B6).
//! - Pide los buffers en 420v (sin conversión de AVFoundation) y convierte NV12 → RGB aquí (B10).
//! - Exposición y balance de blancos: best-effort; cada llamada va dentro de `objc2::exception::catch` (B8, B9).
//! - TCC: `NotDetermined` pide permiso (`requestAccess…`) y espera la respuesta; `Denied`/`Restricted` son error.
//! - Fallas en vivo (desconexión, sesión detenida, 3 s sin cuadros) terminan la fuente; el runner devuelve error y
//!   el supervisor reinicia la captura. `ready` exige el primer cuadro.
//! - Listar (`list`) no abre ninguna cámara.

use super::camera::{CamControls, CameraInfo};
use crate::contracts::now_ns;
use crate::frame::{nv12_to_rgb, BoxError, Frame, LatestTx};
use image::RgbImage;
use objc2::rc::Retained;
use objc2::runtime::Bool;
use objc2::runtime::{AnyObject, NSObject, NSObjectProtocol, ProtocolObject};
use objc2::{define_class, msg_send, AllocAnyThread, DefinedClass};
use objc2_av_foundation::{
    AVAuthorizationStatus, AVCaptureConnection, AVCaptureDevice, AVCaptureDeviceFormat, AVCaptureDeviceInput,
    AVCaptureExposureMode, AVCaptureOutput, AVCaptureSession, AVCaptureVideoDataOutput,
    AVCaptureVideoDataOutputSampleBufferDelegate, AVCaptureWhiteBalanceMode, AVMediaTypeVideo,
};
use objc2_core_foundation::CFString;
use objc2_core_media::{
    CMClock, CMFormatDescription, CMSampleBuffer, CMTime, CMTimeFlags, CMVideoFormatDescriptionGetDimensions,
};
use objc2_core_video::{
    kCVImageBufferYCbCrMatrixKey, kCVImageBufferYCbCrMatrix_ITU_R_709_2,
    kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange, CVPixelBufferGetBaseAddressOfPlane,
    CVPixelBufferGetBytesPerRowOfPlane, CVPixelBufferGetHeight, CVPixelBufferGetPixelFormatType, CVPixelBufferGetWidth,
    CVPixelBufferLockBaseAddress, CVPixelBufferLockFlags, CVPixelBufferUnlockBaseAddress,
};
use objc2_foundation::{NSDictionary, NSNumber, NSString};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::Duration;

/// Sesión abierta: todo lo que debe vivir mientras se captura, más su descripción.
type Opened = (
    Retained<AVCaptureSession>,
    Retained<AVCaptureDevice>,
    Retained<Delegate>,
    Retained<AVCaptureDeviceFormat>,
    CMTime,
    String,
);

const FIRST_FRAME_TIMEOUT: Duration = Duration::from_secs(5);
const STALL_TIMEOUT: Duration = Duration::from_secs(3);

fn video_devices() -> Vec<Retained<AVCaptureDevice>> {
    let Some(mt) = (unsafe { AVMediaTypeVideo }) else { return Vec::new() };
    #[allow(deprecated)]
    let arr = unsafe { AVCaptureDevice::devicesWithMediaType(mt) };
    arr.iter().collect()
}

fn info(d: &AVCaptureDevice) -> CameraInfo {
    unsafe {
        CameraInfo { id: d.uniqueID().to_string(), name: d.localizedName().to_string(), model: d.modelID().to_string() }
    }
}

/// Cámaras de video visibles para AVFoundation (sin abrirlas ni pedir permiso).
pub fn list() -> Result<Vec<CameraInfo>, BoxError> {
    Ok(video_devices().iter().map(|d| info(d)).collect())
}

const NV12: u32 = kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange;

fn dims(desc: &CMFormatDescription) -> (i32, i32) {
    let d = unsafe { CMVideoFormatDescriptionGetDimensions(desc) };
    (d.width, d.height)
}

/// Formato 420v al tamaño exacto con un rango que alcance `fps`. Duración: en rangos discretos (C920: 30–30) el
/// `minFrameDuration` exacto del rango (B7); en rangos continuos (1–60) `1/fps`, para no subir a 60 fps.
fn pick_format(
    dev: &AVCaptureDevice,
    size: (u32, u32),
    fps: f64,
) -> Option<(Retained<AVCaptureDeviceFormat>, CMTime, f64)> {
    let mut best: Option<(Retained<AVCaptureDeviceFormat>, CMTime, f64)> = None;
    for f in unsafe { dev.formats() }.iter() {
        let desc = unsafe { f.formatDescription() };
        if unsafe { desc.media_sub_type() } != NV12 || dims(&desc) != (size.0 as i32, size.1 as i32) {
            continue;
        }
        for r in unsafe { f.videoSupportedFrameRateRanges() }.iter() {
            let max = unsafe { r.maxFrameRate() };
            if max + 0.5 < fps {
                continue;
            }
            let better = best.as_ref().is_none_or(|(_, _, m)| (max - fps).abs() < (m - fps).abs());
            if better {
                let min = unsafe { r.minFrameRate() };
                let dur = if max - min > 0.5 && fps >= min && fps <= max && fps.fract() == 0.0 {
                    CMTime { value: 1, timescale: fps as i32, flags: CMTimeFlags::Valid, epoch: 0 }
                } else {
                    unsafe { r.minFrameDuration() }
                };
                best = Some((f.clone(), dur, max));
            }
        }
    }
    best
}

fn offered(dev: &AVCaptureDevice) -> String {
    let mut v: Vec<String> = unsafe { dev.formats() }
        .iter()
        .map(|f| {
            let desc = unsafe { f.formatDescription() };
            let st = unsafe { desc.media_sub_type() }.to_be_bytes();
            let (w, h) = dims(&desc);
            let max = unsafe { f.videoSupportedFrameRateRanges() }
                .iter()
                .map(|r| unsafe { r.maxFrameRate() })
                .fold(0.0, f64::max);
            format!("{}{w}x{h}@{max:.0}", String::from_utf8_lossy(&st))
        })
        .collect();
    v.dedup();
    v.join(" ")
}

/// Ejecuta `f` atrapando excepciones de Objective-C (AVFoundation lanza `NSInvalidArgumentException` con formatos o
/// modos que no acepta).
fn guarded<R>(what: &str, f: impl FnOnce() -> R) -> Option<R> {
    match objc2::exception::catch(std::panic::AssertUnwindSafe(f)) {
        Ok(r) => Some(r),
        Err(e) => {
            eprintln!("[pcx-capture] avf: {what}: excepción {:?}", e.map(|e| format!("{e:?}")));
            None
        }
    }
}

fn configure(dev: &AVCaptureDevice, fmt: &AVCaptureDeviceFormat, dur: CMTime, ctl: &CamControls, log: bool) -> bool {
    if let Err(e) = unsafe { dev.lockForConfiguration() } {
        eprintln!("[pcx-capture] avf: lockForConfiguration: {}", e.localizedDescription());
        return false;
    }
    let ok = guarded("formato", || unsafe {
        dev.setActiveFormat(fmt);
        dev.setActiveVideoMinFrameDuration(dur);
        dev.setActiveVideoMaxFrameDuration(dur);
    })
    .is_some();
    // best-effort: en la C920 «locked» no tiene efecto y no hay modos de balance de blancos (B9); no debe caerse.
    if ctl.lock_exposure {
        let r = guarded("exposición", || unsafe {
            let m = AVCaptureExposureMode::Locked;
            let s = dev.isExposureModeSupported(m);
            if s {
                dev.setExposureMode(m);
            }
            s
        });
        if log {
            eprintln!("[pcx-capture] avf: exposición fija: soportada={r:?}");
        }
    }
    if ctl.lock_white_balance {
        let r = guarded("balance de blancos", || unsafe {
            let m = AVCaptureWhiteBalanceMode::Locked;
            let s = dev.isWhiteBalanceModeSupported(m);
            if s {
                dev.setWhiteBalanceMode(m);
            }
            s
        });
        if log {
            eprintln!("[pcx-capture] avf: balance de blancos fijo: soportado={r:?}");
        }
    }
    unsafe { dev.unlockForConfiguration() };
    ok
}

pub struct Ivars {
    tx: LatestTx,
    size: (u32, u32),
    frames: AtomicU64,
    /// Cuadros descartados por formato inesperado (p. ej. 420f); se avisa una vez.
    skipped: AtomicU64,
    warned: AtomicBool,
}

define_class!(
    #[unsafe(super(NSObject))]
    #[name = "PcxAvfFrameDelegate"]
    #[ivars = Ivars]
    struct Delegate;

    unsafe impl NSObjectProtocol for Delegate {}

    unsafe impl AVCaptureVideoDataOutputSampleBufferDelegate for Delegate {
        #[unsafe(method(captureOutput:didOutputSampleBuffer:fromConnection:))]
        fn did_output(&self, _o: &AVCaptureOutput, sb: &CMSampleBuffer, _c: &AVCaptureConnection) {
            self.on_frame(sb);
        }
    }
);

impl Delegate {
    fn new(iv: Ivars) -> Retained<Self> {
        let this = Self::alloc().set_ivars(iv);
        unsafe { msg_send![super(this), init] }
    }

    fn on_frame(&self, sb: &CMSampleBuffer) {
        let iv = self.ivars();
        let arrival = now_ns();
        // edad del cuadro según el reloj del host (PTS) → hora de captura en época (≈ 50–60 ms antes de la llegada)
        let age_s = unsafe { CMClock::host_time_clock().time().subtract(sb.presentation_time_stamp()).seconds() };
        let t_capture_ns =
            if age_s.is_finite() && (0.0..1.0).contains(&age_s) { arrival - (age_s * 1e9) as u64 } else { arrival };
        let Some(pb) = (unsafe { sb.image_buffer() }) else { return };
        let pf = CVPixelBufferGetPixelFormatType(&pb);
        if pf != NV12 {
            iv.skipped.fetch_add(1, Ordering::Relaxed);
            if !iv.warned.swap(true, Ordering::Relaxed) {
                eprintln!(
                    "[pcx-capture] avf: formato inesperado {}; se descartan esos cuadros",
                    String::from_utf8_lossy(&pf.to_be_bytes())
                );
            }
            return;
        }
        // matriz YCbCr del buffer; sin ella, BT.709 para HD
        let bt709 = unsafe { pb.attachment(kCVImageBufferYCbCrMatrixKey, std::ptr::null_mut()) }
            .and_then(|v| v.downcast_ref::<CFString>().map(|s| s.to_string()))
            .map(|m| m == unsafe { kCVImageBufferYCbCrMatrix_ITU_R_709_2 }.to_string())
            .unwrap_or(CVPixelBufferGetHeight(&pb) >= 720);
        unsafe { CVPixelBufferLockBaseAddress(&pb, CVPixelBufferLockFlags::ReadOnly) };
        let (w, h) = (CVPixelBufferGetWidth(&pb), CVPixelBufferGetHeight(&pb));
        let (y_ptr, uv_ptr) = (CVPixelBufferGetBaseAddressOfPlane(&pb, 0), CVPixelBufferGetBaseAddressOfPlane(&pb, 1));
        let (y_bpr, uv_bpr) = (CVPixelBufferGetBytesPerRowOfPlane(&pb, 0), CVPixelBufferGetBytesPerRowOfPlane(&pb, 1));
        let mut rgb = vec![0u8; w * h * 3];
        if !y_ptr.is_null() && !uv_ptr.is_null() {
            // SAFETY: planos bloqueados de solo lectura; tamaños según CoreVideo.
            let (y, uv) = unsafe {
                (
                    std::slice::from_raw_parts(y_ptr as *const u8, y_bpr * h),
                    std::slice::from_raw_parts(uv_ptr as *const u8, uv_bpr * h.div_ceil(2)),
                )
            };
            nv12_to_rgb(y, y_bpr, uv, uv_bpr, w, h, bt709, &mut rgb);
        }
        unsafe { CVPixelBufferUnlockBaseAddress(&pb, CVPixelBufferLockFlags::ReadOnly) };
        if let Some(img) = RgbImage::from_raw(w as u32, h as u32, rgb) {
            let img = crate::frame::fit(&img, Some(iv.size)).into_owned();
            iv.tx.put(Frame { img, t_capture_ns });
            iv.frames.fetch_add(1, Ordering::Relaxed);
        }
    }
}

fn find(sel: &str) -> Result<Retained<AVCaptureDevice>, BoxError> {
    if let Some(d) = unsafe { AVCaptureDevice::deviceWithUniqueID(&NSString::from_str(sel)) } {
        return Ok(d);
    }
    let all = video_devices();
    let infos: Vec<CameraInfo> = all.iter().map(|d| info(d)).collect();
    let i = super::camera::select(&infos, sel)?;
    Ok(all[i].clone())
}

/// Qué hacer con cada estado de TCC.
#[derive(Debug, PartialEq, Eq)]
enum Access {
    Granted,
    /// `NotDetermined`: hay que pedir permiso y esperar (no es una negativa).
    Ask,
    Refused,
}

fn access_for(st: AVAuthorizationStatus) -> Access {
    if st == AVAuthorizationStatus::Authorized {
        Access::Granted
    } else if st == AVAuthorizationStatus::NotDetermined {
        Access::Ask
    } else {
        Access::Refused
    }
}

/// Permiso de cámara (TCC). `NotDetermined`: pide acceso y espera la respuesta (hasta 60 s).
fn ensure_access(mt: &objc2_av_foundation::AVMediaType) -> Result<(), String> {
    let st = unsafe { AVCaptureDevice::authorizationStatusForMediaType(mt) };
    let action = access_for(st);
    if action == Access::Granted {
        return Ok(());
    }
    if action == Access::Ask {
        eprintln!("[pcx-capture] avf: pidiendo permiso de cámara (TCC)…");
        let (tx, rx) = std::sync::mpsc::channel::<bool>();
        let block = block2::RcBlock::new(move |granted: Bool| {
            let _ = tx.send(granted.as_bool());
        });
        unsafe { AVCaptureDevice::requestAccessForMediaType_completionHandler(mt, &block) };
        return match rx.recv_timeout(Duration::from_secs(60)) {
            Ok(true) => Ok(()),
            Ok(false) => Err("TCC: la persona denegó el permiso de cámara (Ajustes → Privacidad → Cámara)".into()),
            Err(_) => Err("TCC: sin respuesta al pedido de permiso de cámara en 60 s".into()),
        };
    }
    Err(format!(
        "TCC: cámara {} para este proceso (estado {}); habilítala en Ajustes → Privacidad → Cámara para la terminal que lanza pcx",
        if st == AVAuthorizationStatus::Denied { "denegada" } else { "restringida" },
        st.0
    ))
}

/// Abre la cámara `sel` (uniqueID o subcadena única del nombre) y entrega cuadros RGB al buzón hasta que se cierre.
pub fn spawn(
    sel: &str,
    size: (u32, u32),
    fps: f64,
    ctl: CamControls,
    tx: LatestTx,
) -> Result<std::thread::JoinHandle<()>, BoxError> {
    let sel = sel.to_string();
    let (ready_tx, ready_rx) = std::sync::mpsc::channel::<Result<String, String>>();
    let h = std::thread::Builder::new().name("avf".into()).spawn(move || {
        // Todos los objetos de AVFoundation viven y mueren en este hilo.
        let r = (|| -> Result<Opened, String> {
            let mt = unsafe { AVMediaTypeVideo }.ok_or("AVMediaTypeVideo no disponible")?;
            // listar no requiere permiso: un selector inválido falla sin abrir ni pedir nada
            let dev = find(&sel).map_err(|e| e.to_string())?;
            ensure_access(mt)?;
            let (fmt, dur, maxfps) = pick_format(&dev, size, fps).ok_or_else(|| {
                format!(
                    "{} no ofrece 420v {}x{}@{fps}; ofrece: {}",
                    unsafe { dev.localizedName() },
                    size.0,
                    size.1,
                    offered(&dev)
                )
            })?;
            let session = unsafe { AVCaptureSession::new() };
            let input = unsafe { AVCaptureDeviceInput::deviceInputWithDevice_error(&dev) }
                .map_err(|e| format!("input: {}", e.localizedDescription()))?;
            let output = unsafe { AVCaptureVideoDataOutput::new() };
            let key = NSString::from_str("PixelFormatType"); // kCVPixelBufferPixelFormatTypeKey
            let val = NSNumber::new_u32(NV12);
            let settings: Retained<NSDictionary<NSString, AnyObject>> = unsafe {
                Retained::cast_unchecked(NSDictionary::<NSString, NSNumber>::from_slices(&[&*key], &[&*val]))
            };
            let delegate = Delegate::new(Ivars {
                tx: tx.clone(),
                size,
                frames: AtomicU64::new(0),
                skipped: AtomicU64::new(0),
                warned: AtomicBool::new(false),
            });
            let queue = dispatch2::DispatchQueue::new("pcx.avf", None);
            guarded("sesión", || unsafe {
                session.beginConfiguration();
                if !session.canAddInput(&input) {
                    session.commitConfiguration();
                    return Err("la sesión no acepta la cámara como entrada (¿ocupada por otro proceso?)");
                }
                session.addInput(&input);
                output.setVideoSettings(Some(&settings));
                output.setAlwaysDiscardsLateVideoFrames(true);
                output.setSampleBufferDelegate_queue(Some(ProtocolObject::from_ref(&*delegate)), Some(&queue));
                if !session.canAddOutput(&output) {
                    session.commitConfiguration();
                    return Err("la sesión no acepta la salida de video");
                }
                session.addOutput(&output);
                Ok(())
            })
            .ok_or("la sesión rechazó la configuración")??;
            configure(&dev, &fmt, dur, &CamControls::default(), false);
            guarded("commit/start", || unsafe {
                session.commitConfiguration();
                session.startRunning();
            })
            .ok_or("startRunning falló")?;
            let fail = |e: String| {
                guarded("stopRunning", || unsafe { session.stopRunning() });
                Err(e)
            };
            // la sesión puede cambiar formato y duración al arrancar: siempre se reaplica (B6)
            if !configure(&dev, &fmt, dur, &ctl, true) {
                return fail("no se pudo fijar el formato tras startRunning".into());
            }
            // listo solo con el primer cuadro
            let t0 = std::time::Instant::now();
            while delegate.ivars().frames.load(Ordering::Relaxed) == 0 {
                if t0.elapsed() > FIRST_FRAME_TIMEOUT || !unsafe { session.isRunning() } {
                    return fail(format!("la cámara no entregó cuadros en {FIRST_FRAME_TIMEOUT:?}"));
                }
                std::thread::sleep(Duration::from_millis(20));
            }
            let active = unsafe { dev.activeFormat().formatDescription() };
            let (aw, ah) = dims(&active);
            let desc = format!(
                "{} uid={} activo 420v {aw}x{ah} min_dur={:.4}s (rango máx {maxfps:.1} fps)",
                unsafe { dev.localizedName() },
                unsafe { dev.uniqueID() },
                unsafe { dev.activeVideoMinFrameDuration().seconds() }
            );
            Ok((session, dev, delegate, fmt, dur, desc))
        })();
        let (session, _dev, delegate, _fmt, _dur, desc) = match r {
            Ok(v) => v,
            Err(e) => {
                let _ = ready_tx.send(Err(e));
                return;
            }
        };
        let _ = ready_tx.send(Ok(desc));
        // vigilancia en vivo: desconexión, sesión detenida o sin cuadros → termina (el runner da error y se reinicia)
        let (mut last, mut t_last) = (0u64, std::time::Instant::now());
        while !tx.is_closed() {
            std::thread::sleep(Duration::from_millis(100));
            let n = delegate.ivars().frames.load(Ordering::Relaxed);
            if n != last {
                (last, t_last) = (n, std::time::Instant::now());
            }
            let why = if !unsafe { _dev.isConnected() } {
                Some("cámara desconectada".to_string())
            } else if !unsafe { session.isRunning() } {
                Some("la sesión de captura se detuvo".to_string())
            } else if t_last.elapsed() > STALL_TIMEOUT {
                Some(format!("sin cuadros hace {STALL_TIMEOUT:?}"))
            } else {
                None
            };
            if let Some(w) = why {
                eprintln!("[pcx-capture] avf: {w}; cierro la fuente");
                break;
            }
        }
        guarded("stopRunning", || unsafe {
            for o in session.outputs().iter() {
                if let Ok(v) = o.downcast::<AVCaptureVideoDataOutput>() {
                    v.setSampleBufferDelegate_queue(None, None);
                }
            }
            session.stopRunning()
        });
        eprintln!("[pcx-capture] avf: detenida tras {} cuadros", delegate.ivars().frames.load(Ordering::Relaxed));
    })?;
    // el pedido de permiso TCC puede esperar a la persona hasta 60 s
    match ready_rx.recv_timeout(Duration::from_secs(90)) {
        Ok(Ok(desc)) => {
            eprintln!("[pcx-capture] cam {desc}");
            Ok(h)
        }
        Ok(Err(e)) => Err(format!("cam:{e}").into()),
        Err(_) => Err("cam: el hilo terminó (o tardó > 90 s) sin abrir la cámara".into()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tcc_no_determinado_pide_permiso() {
        assert_eq!(access_for(AVAuthorizationStatus::NotDetermined), Access::Ask);
        assert_eq!(access_for(AVAuthorizationStatus::Authorized), Access::Granted);
        assert_eq!(access_for(AVAuthorizationStatus::Denied), Access::Refused);
        assert_eq!(access_for(AVAuthorizationStatus::Restricted), Access::Refused);
    }
}
