//! `uvc`: webcam con `nokhwa` (AVFoundation en macOS, Media Foundation en Windows, V4L2 en Linux), sin OpenCV.
//! Solo con `--features uvc`. Pide MJPEG al tamaño y fps más cercanos; el hilo lee sin pausa, así el búfer del
//! driver no acumula pasado (el buzón deja el último cuadro).

use crate::contracts::now_ns;
use crate::frame::{fit, BoxError, Frame, LatestTx};
use image::RgbImage;
use nokhwa::pixel_format::RgbFormat;
use nokhwa::utils::{CameraFormat, CameraIndex, FrameFormat, RequestedFormat, RequestedFormatType, Resolution};
use nokhwa::Camera;

pub fn spawn(index: u32, size: (u32, u32), fps: u32, tx: LatestTx) -> Result<std::thread::JoinHandle<()>, BoxError> {
    #[cfg(target_os = "macos")]
    nokhwa::nokhwa_initialize(|granted| eprintln!("[pcx-capture] permiso de cámara: {granted}"));
    let (ready_tx, ready_rx) = std::sync::mpsc::channel::<Result<String, String>>();
    let h = std::thread::Builder::new().name("uvc".into()).spawn(move || {
        let fmt = CameraFormat::new(Resolution::new(size.0, size.1), FrameFormat::MJPEG, fps);
        let req = RequestedFormat::new::<RgbFormat>(RequestedFormatType::Closest(fmt));
        let mut cam = match Camera::new(CameraIndex::Index(index), req).and_then(|mut c| c.open_stream().map(|_| c)) {
            Ok(c) => c,
            Err(e) => {
                let _ = ready_tx.send(Err(e.to_string()));
                return;
            }
        };
        let _ = ready_tx.send(Ok(format!("{} · {}", cam.info().human_name(), cam.camera_format())));
        while !tx.is_closed() {
            let Ok(buf) = cam.frame() else { continue };
            let t = now_ns();
            let Ok(img) = buf.decode_image::<RgbFormat>() else { continue };
            let (w, h) = (img.width(), img.height());
            if let Some(img) = RgbImage::from_raw(w, h, img.into_raw()) {
                tx.put(Frame { img: fit(&img, Some(size)).into_owned(), t_capture_ns: t });
            }
        }
        let _ = cam.stop_stream();
    })?;
    match ready_rx.recv() {
        Ok(Ok(desc)) => {
            eprintln!("[pcx-capture] uvc:{index} {desc}");
            Ok(h)
        }
        Ok(Err(e)) => Err(format!("uvc:{index}: {e}").into()),
        Err(_) => Err(format!("uvc:{index}: el hilo terminó sin abrir la cámara").into()),
    }
}
