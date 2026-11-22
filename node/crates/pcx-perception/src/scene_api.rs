//! Módulos de percepción por escena (vaso en ml, pastillero, libreta…), en Rust y dentro del motor.
//!
//! Contrato entre el motor (Manu) y la percepción de cada escena (Max). El motor:
//! 1. corre el `Perceptor` base (foto limpia → cascada + SAM → objetos del catálogo);
//! 2. llama a `on_clean` del módulo de la **escena activa** con la misma foto limpia y los objetos ya seguidos;
//! 3. si el módulo pidió una cámara en vivo (`live_camera`), llama a `on_live` en cada cuadro de esa cámara;
//! 4. publica lo que devuelven: objetos (`objects`, con ids prefijados por la escena) y eventos (`event` → la web app
//!    los reenvía al gateway como `flow_emitted`).
//!
//! Reglas para quien escribe un módulo:
//! - Funciones puras respecto del mundo: sin abrir cámaras, sin red y sin hilos propios. El estado entre llamadas va
//!   en `ctx.memory`, que el motor conserva por escena.
//! - `on_live` es barato: menos de 30 ms por cuadro. Lo caro va en `on_clean`.
//! - Los ids de los objetos llevan el prefijo de la escena (`lab:vaso-b`, `solar:tierra`) para no chocar con los
//!   del catálogo (`m01`…). El motor rechaza ids sin prefijo.
//! - Coordenadas en mm del mundo de la web app (`calib.matrix_px_to_mm` + `calib.lens`), igual que el `Perceptor`.

use crate::pipeline_api::{CalibIn, ObjectOut};

/// Evento de escena. Llega al agente por `events_wait` como `flow_emitted { event, data }`.
#[derive(Clone, Debug)]
pub struct SceneEvent {
    /// Nombre corto, por ejemplo `"nivel"`, `"celda"`, `"libreta_texto"`.
    pub event: String,
    /// Datos del evento, por ejemplo `{"vaso":"B","ml":42.5,"objetivo_ml":40,"estado":"te_pasaste"}`.
    pub data: serde_json::Value,
}

/// Lo que devuelve un módulo en cada llamada. Todo es opcional.
#[derive(Clone, Debug, Default)]
pub struct SceneOut {
    /// Objetos propios de la escena (alta o actualización). Ids con prefijo de la escena.
    pub upsert: Vec<ObjectOut>,
    /// Ids propios que dejan de existir.
    pub remove: Vec<String>,
    /// Eventos para el agente.
    pub events: Vec<SceneEvent>,
}

/// Contexto que el motor entrega en cada llamada.
pub struct SceneCtx<'a> {
    /// Objetos vigentes del `Perceptor` base (catálogo `mNN` y otros), ya en mm.
    pub objects: &'a [ObjectOut],
    /// Calibración vigente (homografía + lente + superficie).
    pub calib: &'a CalibIn,
    /// Marca de tiempo del cuadro, ns de época.
    pub t_ns: u64,
    /// Memoria propia de la escena; el motor la conserva entre llamadas y la reinicia con `reset`.
    pub memory: &'a mut serde_json::Value,
}

/// Un módulo de percepción de escena.
pub trait ScenePerceptor: Send {
    /// Nombre de la escena y prefijo de sus ids: `"lab"`, `"solar"`, `"desk"`. Debe coincidir con el nombre del
    /// preset/escena activa que la web app le manda al motor (`scene { name }`).
    fn name(&self) -> &str;

    /// Cámara en vivo que este módulo necesita además de la cenital (por ejemplo `"lateral"`), o `None`.
    fn live_camera(&self) -> Option<&str> {
        None
    }

    /// Cada foto limpia de la cámara cenital (proyector apagado un instante). Sin rectificar: usa `ctx.calib`.
    fn on_clean(&mut self, _frame: &image::RgbImage, _ctx: &mut SceneCtx) -> Result<SceneOut, String> {
        Ok(SceneOut::default())
    }

    /// Cada cuadro de la cámara pedida en `live_camera` (sin rectificar). Debe tardar menos de 30 ms.
    fn on_live(&mut self, _frame: &image::RgbImage, _ctx: &mut SceneCtx) -> Result<SceneOut, String> {
        Ok(SceneOut::default())
    }

    /// La escena se desactivó o se pidió `reset`.
    fn reset(&mut self) {}
}

/// Registro de módulos disponibles en el binario. El motor activa el que coincide con la escena de la web app.
#[derive(Default)]
pub struct SceneRegistry {
    modules: Vec<Box<dyn ScenePerceptor>>,
}

impl SceneRegistry {
    pub fn register(&mut self, module: Box<dyn ScenePerceptor>) {
        self.modules.push(module);
    }

    pub fn get_mut(&mut self, name: &str) -> Option<&mut Box<dyn ScenePerceptor>> {
        self.modules.iter_mut().find(|m| m.name() == name)
    }

    pub fn names(&self) -> Vec<String> {
        self.modules.iter().map(|m| m.name().to_string()).collect()
    }
}

/// Valida que los ids de un `SceneOut` lleven el prefijo `"<escena>:"`.
pub fn check_prefix(scene: &str, out: &SceneOut) -> Result<(), String> {
    let prefix = format!("{scene}:");
    let bad = out
        .upsert
        .iter()
        .map(|o| o.id.as_str())
        .chain(out.remove.iter().map(String::as_str))
        .find(|id| !id.starts_with(&prefix));
    match bad {
        Some(id) => Err(format!("id «{id}» sin el prefijo «{prefix}»")),
        None => Ok(()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Vaso;
    impl ScenePerceptor for Vaso {
        fn name(&self) -> &str {
            "lab"
        }
        fn live_camera(&self) -> Option<&str> {
            Some("lateral")
        }
        fn on_live(&mut self, _f: &image::RgbImage, ctx: &mut SceneCtx) -> Result<SceneOut, String> {
            ctx.memory["cuadros"] = (ctx.memory["cuadros"].as_u64().unwrap_or(0) + 1).into();
            Ok(SceneOut {
                events: vec![SceneEvent { event: "nivel".into(), data: serde_json::json!({"vaso":"B","ml":42.5}) }],
                ..Default::default()
            })
        }
    }

    #[test]
    fn registro_memoria_y_prefijo() {
        let mut reg = SceneRegistry::default();
        reg.register(Box::new(Vaso));
        assert_eq!(reg.names(), vec!["lab"]);
        let calib = CalibIn {
            camera_px: (4, 4),
            matrix_px_to_mm: [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
            lens: None,
            surface_mm: (4.0, 4.0),
        };
        let mut memory = serde_json::json!({});
        let frame = image::RgbImage::new(4, 4);
        let m = reg.get_mut("lab").unwrap();
        assert_eq!(m.live_camera(), Some("lateral"));
        for _ in 0..2 {
            let mut ctx = SceneCtx { objects: &[], calib: &calib, t_ns: 0, memory: &mut memory };
            let out = m.on_live(&frame, &mut ctx).unwrap();
            assert_eq!(out.events[0].event, "nivel");
        }
        assert_eq!(memory["cuadros"], 2);
        let malo = SceneOut {
            upsert: vec![ObjectOut {
                id: "vaso".into(),
                label: "Vaso".into(),
                polygon_mm: vec![],
                center_mm: [0.0, 0.0],
                confidence: 1.0,
            }],
            ..Default::default()
        };
        assert!(check_prefix("lab", &malo).is_err());
        assert!(check_prefix("lab", &SceneOut::default()).is_ok());
    }
}
