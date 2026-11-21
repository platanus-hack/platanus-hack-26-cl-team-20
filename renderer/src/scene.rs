//! Estado de la escena en el renderer: aplica `scene` y `scene_diff` (con `base_rev`),
//! vence widgets por `ttl_s` y los entrega ordenados por `z`. Sin GPU: se prueba en unitarios.

use pcx_contracts::{SceneDiff, SceneSnapshot, Surface, TABLE_MM, ToRenderer, Transform, TransformKind, Widget};

#[derive(Debug, Clone)]
pub struct Entry {
    pub widget: Widget,
    /// Segundos (reloj del renderer) en que llegó esta versión del widget; base del `ttl_s`.
    pub born_s: f64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Outcome {
    Applied,
    /// Diff viejo o duplicado (rev <= la actual): se ignora.
    Stale,
    /// Falta un rev (o no hay base): hay que pedir un `scene` completo.
    NeedSnapshot,
}

#[derive(Debug, Clone)]
pub struct SceneState {
    pub rev: Option<i64>,
    pub surface: Surface,
    pub transforms: Vec<Transform>,
    pub entries: Vec<Entry>,
    /// Revs aplicadas que aún no salen en un cuadro presentado.
    pub unpresented: Vec<i64>,
    pub need_snapshot: bool,
}

impl Default for SceneState {
    fn default() -> Self {
        Self {
            rev: None,
            surface: Surface { width_mm: 800.0, height_mm: 500.0 },
            transforms: vec![],
            entries: vec![],
            unpresented: vec![],
            need_snapshot: false,
        }
    }
}

impl SceneState {
    pub fn apply(&mut self, msg: ToRenderer, now_s: f64) -> Outcome {
        match msg {
            ToRenderer::Scene(s) => {
                self.apply_snapshot(s, now_s);
                Outcome::Applied
            }
            ToRenderer::SceneDiff(d) => self.apply_diff(d, now_s),
        }
    }

    fn apply_snapshot(&mut self, s: SceneSnapshot, now_s: f64) {
        let mut transforms = s.transforms;
        // Legado v0: `homography` suelta sin transform equivalente.
        if Transform::projector_matrix(&transforms).is_none()
            && let Some(h) = s.homography.filter(|h| h.len() == 9)
        {
            transforms.push(Transform {
                from_frame: TABLE_MM.into(),
                to_frame: "proj:main".into(),
                kind: TransformKind::Homography,
                matrix: Some(h),
                lut_ref: None,
                version: 0,
                residual_mm: None,
            });
        }
        // Un widget idéntico que ya estaba conserva su edad (el TTL no se reinicia al reenviar la escena).
        let entries = s
            .widgets
            .into_iter()
            .map(|w| {
                let born_s = self
                    .entries
                    .iter()
                    .find(|e| e.widget == w)
                    .map_or(now_s, |e| e.born_s);
                Entry { widget: w, born_s }
            })
            .collect();
        self.entries = entries;
        self.surface = s.surface;
        self.transforms = transforms;
        self.rev = Some(s.rev);
        self.unpresented.push(s.rev);
        self.need_snapshot = false;
    }

    fn apply_diff(&mut self, d: SceneDiff, now_s: f64) -> Outcome {
        let Some(cur) = self.rev else {
            self.need_snapshot = true;
            return Outcome::NeedSnapshot;
        };
        if d.rev <= cur {
            return Outcome::Stale;
        }
        if d.base_rev != cur || self.need_snapshot {
            self.need_snapshot = true;
            return Outcome::NeedSnapshot;
        }
        for id in &d.remove {
            self.entries.retain(|e| e.widget.id() != id);
        }
        for w in d.upsert {
            match self.entries.iter_mut().find(|e| e.widget.id() == w.id()) {
                Some(e) => {
                    if e.widget != w {
                        e.widget = w;
                        e.born_s = now_s;
                    }
                }
                None => self.entries.push(Entry { widget: w, born_s: now_s }),
            }
        }
        if let Some(t) = d.transforms {
            self.transforms = t;
        }
        self.rev = Some(d.rev);
        self.unpresented.push(d.rev);
        Outcome::Applied
    }

    /// Quita los widgets vencidos por `ttl_s`.
    pub fn expire(&mut self, now_s: f64) {
        self.entries.retain(|e| match e.widget.common().ttl_s {
            Some(ttl) => now_s - e.born_s < ttl,
            None => true,
        });
    }

    /// Widgets vigentes, ordenados por `z` (estable: a igual `z`, el orden de llegada).
    pub fn visible(&self, now_s: f64) -> Vec<&Widget> {
        let mut v: Vec<&Entry> = self
            .entries
            .iter()
            .filter(|e| e.widget.common().ttl_s.is_none_or(|ttl| now_s - e.born_s < ttl))
            .collect();
        v.sort_by_key(|e| e.widget.common().z);
        v.into_iter().map(|e| &e.widget).collect()
    }

    /// Homografía `table_mm → proj:*` vigente.
    pub fn homography(&self) -> Option<[f64; 9]> {
        Transform::projector_matrix(&self.transforms)
    }

    /// LUT por píxel vigente: `(ruta, version)` de un transform `kind: lut` hacia/desde `proj:*`.
    pub fn lut_ref(&self) -> Option<(String, i64)> {
        self.transforms
            .iter()
            .find(|t| {
                t.kind == TransformKind::Lut
                    && (t.to_frame.starts_with("proj:") || t.from_frame.starts_with("proj:"))
            })
            .and_then(|t| t.lut_ref.clone().map(|r| (r, t.version)))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(s: &str) -> ToRenderer {
        serde_json::from_str(s).unwrap()
    }

    const FIX_SCENE: &str = include_str!("../../contracts/fixtures/scene.json");
    const FIX_DIFF: &str = include_str!("../../contracts/fixtures/scene_diff.json");

    #[test]
    fn snapshot_y_diff_de_los_fixtures() {
        let mut s = SceneState::default();
        assert_eq!(s.apply(parse(FIX_SCENE), 0.0), Outcome::Applied);
        assert_eq!(s.rev, Some(12));
        assert_eq!(s.entries.len(), 7);
        assert!(s.homography().is_some());
        assert_eq!(s.apply(parse(FIX_DIFF), 0.1), Outcome::Applied);
        assert_eq!(s.rev, Some(13));
        assert_eq!(s.entries.len(), 6);
        assert!(s.entries.iter().all(|e| e.widget.id() != "flecha"));
        let Widget::Button(b) = &s.entries.iter().find(|e| e.widget.id() == "siguiente").unwrap().widget else {
            panic!()
        };
        assert_eq!(b.progress, 0.8);
        assert_eq!(s.unpresented, vec![12, 13]);
        // Duplicado: se ignora.
        assert_eq!(s.apply(parse(FIX_DIFF), 0.2), Outcome::Stale);
    }

    #[test]
    fn hueco_de_rev_pide_snapshot_y_bloquea_diffs() {
        let mut s = SceneState::default();
        assert_eq!(s.apply(parse(FIX_DIFF), 0.0), Outcome::NeedSnapshot);
        s.apply(parse(FIX_SCENE), 0.0);
        let gap = FIX_DIFF.replace("\"rev\": 13", "\"rev\": 15").replace("\"base_rev\": 12", "\"base_rev\": 14");
        assert_eq!(s.apply(parse(&gap), 0.0), Outcome::NeedSnapshot);
        assert!(s.need_snapshot);
        assert_eq!(s.rev, Some(12));
        // Un snapshot nuevo lo destraba.
        s.apply(parse(&FIX_SCENE.replace("\"rev\": 12", "\"rev\": 16")), 0.0);
        assert!(!s.need_snapshot);
    }

    #[test]
    fn ttl_y_orden_por_z() {
        let mut s = SceneState::default();
        s.apply(parse(FIX_SCENE), 0.0);
        let ids: Vec<_> = s.visible(0.0).iter().map(|w| w.id().to_owned()).collect();
        assert_eq!(ids.first().map(String::as_str), Some("ondas")); // z = -1
        assert_eq!(ids.last().map(String::as_str), Some("lbl-taza")); // z = 2
        assert!(ids.contains(&"receta".to_owned()));
        // receta tiene ttl_s = 120.
        assert!(!s.visible(121.0).iter().any(|w| w.id() == "receta"));
        // Reenviar la misma escena no reinicia el TTL.
        s.apply(parse(&FIX_SCENE.replace("\"rev\": 12", "\"rev\": 20")), 100.0);
        s.expire(121.0);
        assert!(!s.entries.iter().any(|e| e.widget.id() == "receta"));
    }
}
