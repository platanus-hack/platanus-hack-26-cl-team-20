//! `cam:<selector>`: cámara local con el backend nativo de cada SO.
//!
//! | SO | backend | id estable |
//! |---|---|---|
//! | macOS | AVFoundation (`objc2`, `avf.rs`) | `uniqueID` (p. ej. `0x1120000046d0892` = C920 USB) |
//! | Windows | nokhwa/Media Foundation (`--features uvc`; MF nativo pendiente) | nombre simbólico de nokhwa (`misc`) |
//! | Linux | nokhwa/V4L2 (`--features uvc`; V4L2 nativo pendiente) | ruta `/dev/videoN` de nokhwa (`misc`) |
//!
//! El selector es el id exacto o una subcadena **única** del nombre (sin distinguir mayúsculas). Nunca por índice:
//! en macOS la cámara de Continuity entra y sale y cambia el orden (B4).

use crate::frame::{BoxError, LatestTx};

#[derive(Clone, Debug, PartialEq, serde::Serialize)]
pub struct CameraInfo {
    /// Id estable para `cam:<id>`.
    pub id: String,
    pub name: String,
    pub model: String,
}

/// Controles best-effort: si la cámara no los acepta se informa y se sigue.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct CamControls {
    pub lock_exposure: bool,
    pub lock_white_balance: bool,
}

/// Índice de la cámara elegida por `sel`: id exacto, o subcadena única del nombre.
pub fn select(cams: &[CameraInfo], sel: &str) -> Result<usize, BoxError> {
    if let Some(i) = cams.iter().position(|c| c.id == sel) {
        return Ok(i);
    }
    let low = sel.to_lowercase();
    let hits: Vec<usize> =
        cams.iter().enumerate().filter(|(_, c)| c.name.to_lowercase().contains(&low)).map(|(i, _)| i).collect();
    match hits.as_slice() {
        [i] => Ok(*i),
        [] => Err(format!("no hay cámara {sel:?}; disponibles: {}", describe(cams)).into()),
        _ => Err(format!("{sel:?} es ambiguo ({} cámaras); usa el id: {}", hits.len(), describe(cams)).into()),
    }
}

fn describe(cams: &[CameraInfo]) -> String {
    if cams.is_empty() {
        return "ninguna".into();
    }
    cams.iter().map(|c| format!("{} «{}»", c.id, c.name)).collect::<Vec<_>>().join(", ")
}

/// Cámaras del sistema sin abrirlas.
pub fn list() -> Result<Vec<CameraInfo>, BoxError> {
    #[cfg(target_os = "macos")]
    return super::avf::list();
    #[cfg(all(not(target_os = "macos"), feature = "uvc"))]
    return fallback::list();
    #[cfg(all(not(target_os = "macos"), not(feature = "uvc")))]
    Err("sin backend de cámara en este SO: compila con --features uvc".into())
}

pub fn spawn(
    sel: &str,
    size: (u32, u32),
    fps: f64,
    ctl: CamControls,
    tx: LatestTx,
) -> Result<std::thread::JoinHandle<()>, BoxError> {
    #[cfg(target_os = "macos")]
    return super::avf::spawn(sel, size, fps, ctl, tx);
    #[cfg(all(not(target_os = "macos"), feature = "uvc"))]
    return fallback::spawn(sel, size, fps, ctl, tx);
    #[cfg(all(not(target_os = "macos"), not(feature = "uvc")))]
    {
        let _ = (sel, size, fps, ctl, tx);
        Err("sin backend de cámara en este SO: compila con --features uvc".into())
    }
}

/// Windows/Linux: nokhwa (Media Foundation / V4L2) como respaldo hasta tener los backends nativos.
#[cfg(all(not(target_os = "macos"), feature = "uvc"))]
mod fallback {
    use super::{CamControls, CameraInfo};
    use crate::frame::{BoxError, LatestTx};
    use nokhwa::utils::{ApiBackend, CameraIndex};

    pub fn list() -> Result<Vec<CameraInfo>, BoxError> {
        Ok(nokhwa::query(ApiBackend::Auto)?
            .into_iter()
            .map(|c| CameraInfo { id: c.misc(), name: c.human_name(), model: c.description().to_string() })
            .collect())
    }

    pub fn spawn(
        sel: &str,
        size: (u32, u32),
        fps: f64,
        _ctl: CamControls,
        tx: LatestTx,
    ) -> Result<std::thread::JoinHandle<()>, BoxError> {
        let all = nokhwa::query(ApiBackend::Auto)?;
        let infos = list()?;
        let i = super::select(&infos, sel)?;
        let idx = match all[i].index() {
            CameraIndex::Index(n) => *n,
            CameraIndex::String(_) => i as u32,
        };
        crate::sources::uvc::spawn(idx, size, fps as u32, tx)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cams() -> Vec<CameraInfo> {
        let c = |id: &str, name: &str| CameraInfo { id: id.into(), name: name.into(), model: String::new() };
        vec![
            c("0x1120000046d0892", "HD Pro Webcam C920"),
            c("FACE-1", "Cámara FaceTime HD"),
            c("IPH-2", "Cámara de iPhone"),
        ]
    }

    #[test]
    fn selecciona_por_id_o_nombre_unico() {
        assert_eq!(select(&cams(), "0x1120000046d0892").unwrap(), 0);
        assert_eq!(select(&cams(), "c920").unwrap(), 0);
        assert!(select(&cams(), "cámara").unwrap_err().to_string().contains("ambiguo"));
        assert!(select(&cams(), "1").is_err(), "nunca por índice");
        assert!(select(&cams(), "brio").is_err());
    }
}
