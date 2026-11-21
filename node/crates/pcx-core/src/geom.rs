//! Resolución de anclas a mm absolutos y hit-test de toques contra widgets (port de `geom.py`).

use std::collections::BTreeMap;

use pcx_contracts::{Anchor, PointMM, RectMM, Widget};

use crate::proto::ObjectState;

pub const GAP_MM: f64 = 20.0;

#[derive(Debug, Clone, PartialEq)]
pub struct WidgetError(pub String);

impl std::fmt::Display for WidgetError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}
impl std::error::Error for WidgetError {}

pub type Objects = BTreeMap<String, ObjectState>;

pub fn bbox_of(o: &ObjectState) -> RectMM {
    if let Some(b) = o.bbox_mm {
        return b;
    }
    let (mut x0, mut y0, mut x1, mut y1) = (f64::MAX, f64::MAX, f64::MIN, f64::MIN);
    for p in &o.polygon_mm {
        x0 = x0.min(p.0);
        y0 = y0.min(p.1);
        x1 = x1.max(p.0);
        y1 = y1.max(p.1);
    }
    RectMM(x0, y0, x1 - x0, y1 - y0)
}

pub fn inflate(poly: &[PointMM], c: PointMM, mm: f64) -> Vec<PointMM> {
    poly.iter()
        .map(|p| {
            let (dx, dy) = (p.0 - c.0, p.1 - c.1);
            let d = dx.hypot(dy);
            let d = if d == 0.0 { 1.0 } else { d };
            PointMM(p.0 + dx / d * mm, p.1 + dy / d * mm)
        })
        .collect()
}

fn off(a: Option<&Anchor>) -> (f64, f64) {
    a.and_then(|a| a.offset_mm).map(|o| (o.0, o.1)).unwrap_or((0.0, 0.0))
}

fn default_size(w: &Widget) -> (f64, f64) {
    match w {
        Widget::Panel(_) => (200.0, 110.0),
        Widget::Button(_) => (110.0, 45.0),
        _ => (120.0, 120.0),
    }
}

pub fn kind(w: &Widget) -> &'static str {
    match w {
        Widget::Label(_) => "label",
        Widget::Halo(_) => "halo",
        Widget::Arrow(_) => "arrow",
        Widget::Panel(_) => "panel",
        Widget::Button(_) => "button",
        Widget::Image(_) => "image",
        Widget::Effect(_) => "effect",
    }
}

fn rect_mut(w: &mut Widget) -> Option<&mut Option<RectMM>> {
    match w {
        Widget::Panel(x) => Some(&mut x.rect_mm),
        Widget::Button(x) => Some(&mut x.rect_mm),
        Widget::Image(x) => Some(&mut x.rect_mm),
        Widget::Effect(x) => Some(&mut x.rect_mm),
        _ => None,
    }
}

pub fn rect_of(w: &Widget) -> Option<RectMM> {
    match w {
        Widget::Panel(x) => x.rect_mm,
        Widget::Button(x) => x.rect_mm,
        Widget::Image(x) => x.rect_mm,
        Widget::Effect(x) => x.rect_mm,
        _ => None,
    }
}

/// Widget con geometría absoluta en mm (`Ok(None)` si su objeto no está). Error si falta geometría y no hay ancla.
pub fn resolve(w: &Widget, objects: &Objects, surface: (f64, f64)) -> Result<Option<Widget>, WidgetError> {
    let a = w.common().anchor.as_ref();
    let mut obj = None;
    if let Some(oid) = a.and_then(|a| a.object_id.as_ref()) {
        match objects.get(oid) {
            Some(o) => obj = Some(o),
            None => return Ok(None),
        }
    }
    let (dx, dy) = off(a);
    let base = a.and_then(|a| a.point_mm);
    let id = w.id().to_string();
    let mut out = w.clone();
    match &mut out {
        Widget::Label(l) => {
            let p = if let Some(o) = obj {
                let bb = bbox_of(o);
                PointMM(o.center_mm.0, l.size_mm.max(bb.1 - l.size_mm))
            } else if let Some(b) = base {
                b
            } else {
                return Err(WidgetError(format!("{id}: label necesita anchor.point_mm o anchor.object_id")));
            };
            l.common.anchor = Some(Anchor {
                object_id: a.and_then(|a| a.object_id.clone()),
                point_mm: Some(PointMM(p.0 + dx, p.1 + dy)),
                offset_mm: None,
            });
        }
        Widget::Halo(h) => {
            if h.polygon_mm.is_none() {
                let Some(o) = obj else {
                    return Err(WidgetError(format!("{id}: halo necesita polygon_mm o anchor.object_id")));
                };
                h.polygon_mm =
                    Some(inflate(&o.polygon_mm, o.center_mm, 8.0).into_iter().map(|p| PointMM(p.0 + dx, p.1 + dy)).collect());
            }
        }
        Widget::Arrow(ar) if ar.to_mm.is_none() || ar.from_mm.is_none() => {
            {
                let tip = if let Some(o) = obj {
                    let bb = bbox_of(o);
                    if bb.1 > 90.0 {
                        PointMM(o.center_mm.0, bb.1 - 6.0)
                    } else {
                        PointMM(o.center_mm.0, bb.1 + bb.3 + 6.0)
                    }
                } else if let Some(b) = base {
                    b
                } else {
                    return Err(WidgetError(format!("{id}: arrow necesita from_mm/to_mm o un ancla")));
                };
                let up = obj.map(|o| tip.1 < o.center_mm.1).unwrap_or(true);
                let tail = PointMM(tip.0, if up { tip.1 - 70.0 } else { tip.1 + 70.0 });
                ar.to_mm = Some(ar.to_mm.unwrap_or(PointMM(tip.0 + dx, tip.1 + dy)));
                ar.from_mm = Some(ar.from_mm.unwrap_or(PointMM(tail.0 + dx, tail.1 + dy)));
            }
        }
        _ => {}
    }
    let needs_rect = match &out {
        Widget::Panel(_) | Widget::Button(_) | Widget::Image(_) => true,
        Widget::Effect(e) => e.polygon_mm.is_none(),
        _ => false,
    };
    if needs_rect && rect_of(&out).is_none() {
        let (dw, dh) = default_size(&out);
        if let (Widget::Effect(e), Some(o)) = (&mut out, obj) {
            e.polygon_mm = Some(o.polygon_mm.clone());
        } else if let Some(o) = obj {
            let bb = bbox_of(o);
            let mut x = bb.0 + bb.2 + GAP_MM;
            if x + dw > surface.0 {
                x = (bb.0 - GAP_MM - dw).max(0.0);
            }
            let y = bb.1.max(0.0).min(surface.1 - dh);
            if let Some(r) = rect_mut(&mut out) {
                *r = Some(RectMM(x + dx, y + dy, dw, dh));
            }
        } else if let Some(b) = base {
            if let Some(r) = rect_mut(&mut out) {
                *r = Some(RectMM(b.0 + dx, b.1 + dy, dw, dh));
            }
        } else {
            return Err(WidgetError(format!("{id}: {} necesita rect_mm o un ancla", kind(&out))));
        }
    }
    Ok(Some(out))
}

pub fn point_in_poly(x: f64, y: f64, poly: &[PointMM]) -> bool {
    let mut inside = false;
    let n = poly.len();
    if n == 0 {
        return false;
    }
    let mut j = n - 1;
    for i in 0..n {
        let (xi, yi, xj, yj) = (poly[i].0, poly[i].1, poly[j].0, poly[j].1);
        if (yi > y) != (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi {
            inside = !inside;
        }
        j = i;
    }
    inside
}

fn in_rect(x: f64, y: f64, r: RectMM, pad: f64) -> bool {
    r.0 - pad <= x && x <= r.0 + r.2 + pad && r.1 - pad <= y && y <= r.1 + r.3 + pad
}

/// `w` ya resuelto.
pub fn hits(w: &Widget, x: f64, y: f64) -> bool {
    match w {
        Widget::Panel(p) => p.rect_mm.is_some_and(|r| in_rect(x, y, r, 0.0)),
        Widget::Image(p) => p.rect_mm.is_some_and(|r| in_rect(x, y, r, 0.0)),
        Widget::Button(p) => p.rect_mm.is_some_and(|r| in_rect(x, y, r, 5.0)),
        Widget::Effect(e) => match (e.rect_mm, &e.polygon_mm) {
            (Some(r), _) => in_rect(x, y, r, 0.0),
            (None, Some(p)) => point_in_poly(x, y, p),
            _ => false,
        },
        Widget::Halo(h) => h.polygon_mm.as_ref().is_some_and(|p| point_in_poly(x, y, p)),
        Widget::Label(l) => {
            let Some(p) = l.common.anchor.as_ref().and_then(|a| a.point_mm) else { return false };
            let lines: Vec<&str> = l.text.split('\n').collect();
            let maxc = lines.iter().map(|s| s.chars().count()).max().unwrap_or(0) as f64;
            let hw = maxc * l.size_mm * 0.3 + 5.0;
            let hh = lines.len().max(1) as f64 * l.size_mm * 0.7 + 5.0;
            (x - p.0).abs() <= hw && (y - p.1).abs() <= hh
        }
        Widget::Arrow(_) => false,
    }
}

pub fn pressable(w: &Widget) -> bool {
    w.common().on_press.is_some() || matches!(w, Widget::Button(_))
}

/// Widget bajo el punto: primero los tocables, luego mayor `z`, botones antes que el resto.
pub fn hit_test<'a>(widgets: impl IntoIterator<Item = &'a Widget>, x: f64, y: f64) -> Option<&'a Widget> {
    let cands: Vec<&Widget> = widgets.into_iter().filter(|w| hits(w, x, y)).collect();
    // empate: el primero de la lista (como el sort estable de Python)
    cands
        .into_iter()
        .rev()
        .max_by_key(|w| (pressable(w), w.common().z, matches!(w, Widget::Button(_))))
}

pub fn object_at(objects: &Objects, x: f64, y: f64) -> Option<String> {
    objects.values().find(|o| point_in_poly(x, y, &o.polygon_mm)).map(|o| o.id.clone())
}

/// Centro tocable de un widget resuelto.
pub fn center(w: &Widget) -> Option<(f64, f64)> {
    if let Some(r) = rect_of(w) {
        return Some((r.0 + r.2 / 2.0, r.1 + r.3 / 2.0));
    }
    if let Some(p) = w.common().anchor.as_ref().and_then(|a| a.point_mm) {
        return Some((p.0, p.1));
    }
    let poly = match w {
        Widget::Halo(h) => h.polygon_mm.as_ref(),
        Widget::Effect(e) => e.polygon_mm.as_ref(),
        _ => None,
    }?;
    let n = poly.len() as f64;
    Some((poly.iter().map(|p| p.0).sum::<f64>() / n, poly.iter().map(|p| p.1).sum::<f64>() / n))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn w(v: serde_json::Value) -> Widget {
        serde_json::from_value(v).unwrap()
    }

    #[test]
    fn hit_test_prioriza_tocables() {
        let panel = w(json!({"type": "panel", "id": "p", "rect_mm": [0, 0, 200, 200], "z": 5}));
        let btn = w(json!({"type": "button", "id": "b", "rect_mm": [50, 50, 100, 40]}));
        let halo = w(json!({"type": "halo", "id": "h", "polygon_mm": [[0, 0], [300, 0], [300, 300], [0, 300]]}));
        let all = [panel.clone(), btn, halo.clone()];
        assert_eq!(hit_test(&all, 60.0, 60.0).unwrap().id(), "b");
        let two = [panel, halo];
        assert_eq!(hit_test(&two, 10.0, 10.0).unwrap().id(), "p");
        assert_eq!(hit_test(&two, 250.0, 250.0).unwrap().id(), "h");
        assert!(hit_test(&two[..1], 500.0, 500.0).is_none());
    }
}
