//! Geometría de la calibración de la web app: px de cámara ↔ mm de su mundo.
//!
//! Mismo modelo que `calib.js` de la consola: sin `lens`, `matrix_px_to_mm` lleva píxeles de la foto a mm; con
//! `lens` (`{f, cx, cy, k1, k2}`, radial de OpenCV: `x_d = x·(1 + k1·r² + k2·r⁴)` en coordenadas normalizadas)
//! primero se quita la distorsión y la matriz lleva esos píxeles corregidos a mm.

use pcx_perception::pipeline_api::{CalibIn, Lens};

/// Píxel de la foto (distorsionado) → píxel corregido. La inversa del modelo se resuelve iterando (12 vueltas).
pub fn undistort(l: &Lens, [u, v]: [f64; 2]) -> [f64; 2] {
    let (xd, yd) = ((u - l.cx) / l.f, (v - l.cy) / l.f);
    let (mut x, mut y) = (xd, yd);
    for _ in 0..12 {
        let r2 = x * x + y * y;
        let k = 1.0 + l.k1 * r2 + l.k2 * r2 * r2;
        (x, y) = (xd / k, yd / k);
    }
    [x * l.f + l.cx, y * l.f + l.cy]
}

/// Píxel corregido → píxel de la foto.
pub fn distort(l: &Lens, [u, v]: [f64; 2]) -> [f64; 2] {
    let (x, y) = ((u - l.cx) / l.f, (v - l.cy) / l.f);
    let r2 = x * x + y * y;
    let k = 1.0 + l.k1 * r2 + l.k2 * r2 * r2;
    [x * k * l.f + l.cx, y * k * l.f + l.cy]
}

/// Homografía 3×3 por filas aplicada a un punto. `None` si cae en el infinito.
pub fn apply(m: &[f64; 9], [x, y]: [f64; 2]) -> Option<[f64; 2]> {
    let w = m[6] * x + m[7] * y + m[8];
    (w.abs() > 1e-12).then(|| [(m[0] * x + m[1] * y + m[2]) / w, (m[3] * x + m[4] * y + m[5]) / w])
}

/// Inversa de una 3×3 por filas (`None` si es singular).
pub fn invert(m: &[f64; 9]) -> Option<[f64; 9]> {
    let c = [
        m[4] * m[8] - m[5] * m[7],
        m[2] * m[7] - m[1] * m[8],
        m[1] * m[5] - m[2] * m[4],
        m[5] * m[6] - m[3] * m[8],
        m[0] * m[8] - m[2] * m[6],
        m[2] * m[3] - m[0] * m[5],
        m[3] * m[7] - m[4] * m[6],
        m[1] * m[6] - m[0] * m[7],
        m[0] * m[4] - m[1] * m[3],
    ];
    let det = m[0] * c[0] + m[1] * c[3] + m[2] * c[6];
    let scale = m.iter().fold(0.0f64, |a, v| a.max(v.abs()));
    (det.is_finite() && det.abs() > 1e-12 * scale.powi(3).max(1e-300)).then(|| c.map(|v| v / det))
}

pub fn px_to_mm(c: &CalibIn, p: [f64; 2]) -> Option<[f64; 2]> {
    let q = c.lens.as_ref().map_or(p, |l| undistort(l, p));
    apply(&c.matrix_px_to_mm, q).filter(|m| m[0].is_finite() && m[1].is_finite())
}

/// mm → píxel de la foto. `inv` es la inversa de `matrix_px_to_mm` (ver [`invert`]).
pub fn mm_to_px(c: &CalibIn, inv: &[f64; 9], p: [f64; 2]) -> Option<[f64; 2]> {
    let q = apply(inv, p)?;
    Some(c.lens.as_ref().map_or(q, |l| distort(l, q)))
}

/// Esquinas de la superficie (`surface_mm`) en píxeles de la foto, en orden (0,0) → (w,0) → (w,h) → (0,h).
pub fn surface_quad_px(c: &CalibIn) -> Option<[[f64; 2]; 4]> {
    let inv = invert(&c.matrix_px_to_mm)?;
    let (w, h) = c.surface_mm;
    let mut out = [[0.0; 2]; 4];
    for (o, mm) in out.iter_mut().zip([[0.0, 0.0], [w, 0.0], [w, h], [0.0, h]]) {
        *o = mm_to_px(c, &inv, mm).filter(|p| p[0].is_finite() && p[1].is_finite())?;
    }
    Some(out)
}

/// ¿`p` dentro del polígono? (regla par-impar; sirve para cuadriláteros no convexos por la lente).
pub fn inside(poly: &[[f64; 2]], p: [f64; 2]) -> bool {
    let mut odd = false;
    let mut j = poly.len().wrapping_sub(1);
    for i in 0..poly.len() {
        let (a, b) = (poly[i], poly[j]);
        if (a[1] > p[1]) != (b[1] > p[1]) && p[0] < (b[0] - a[0]) * (p[1] - a[1]) / (b[1] - a[1]) + a[0] {
            odd = !odd;
        }
        j = i;
    }
    odd
}

/// Máscara `w × h` (≠ 0 = dentro) de la superficie vista por la cámara: la zona que ilumina el proyector. Se
/// evalúa en una grilla de `step` px (la foto limpia muestrea la luminancia más grueso que eso).
pub fn surface_mask(c: &CalibIn, w: u32, h: u32, step: u32) -> Option<Vec<u8>> {
    let quad = surface_quad_px(c)?;
    let (w, h, step) = (w as usize, h as usize, step.max(1) as usize);
    let mut data = vec![0u8; w * h];
    let mut any = false;
    for y0 in (0..h).step_by(step) {
        for x0 in (0..w).step_by(step) {
            let center = [x0 as f64 + step as f64 / 2.0, y0 as f64 + step as f64 / 2.0];
            if inside(&quad, center) {
                any = true;
                for row in data.chunks_mut(w).skip(y0).take(step) {
                    let x1 = (x0 + step).min(w);
                    row[x0..x1].fill(255);
                }
            }
        }
    }
    any.then_some(data)
}

/// Casco convexo (cadena monótona de Andrew), en sentido antihorario y sin puntos colineales.
pub fn convex_hull(mut pts: Vec<[f64; 2]>) -> Vec<[f64; 2]> {
    pts.sort_by(|a, b| a[0].total_cmp(&b[0]).then(a[1].total_cmp(&b[1])));
    pts.dedup();
    if pts.len() < 3 {
        return pts;
    }
    let cross = |o: [f64; 2], a: [f64; 2], b: [f64; 2]| (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
    let mut hull: Vec<[f64; 2]> = Vec::with_capacity(pts.len() * 2);
    for pass in 0..2 {
        let start = hull.len();
        let it: Box<dyn Iterator<Item = &[f64; 2]>> =
            if pass == 0 { Box::new(pts.iter()) } else { Box::new(pts.iter().rev()) };
        for &p in it {
            while hull.len() >= start + 2 && cross(hull[hull.len() - 2], hull[hull.len() - 1], p) <= 0.0 {
                hull.pop();
            }
            hull.push(p);
        }
        hull.pop();
    }
    hull
}

/// Deja como mucho `max` puntos, repartidos parejo a lo largo del contorno.
pub fn decimate(poly: Vec<[f64; 2]>, max: usize) -> Vec<[f64; 2]> {
    if poly.len() <= max || max == 0 {
        return poly;
    }
    (0..max).map(|i| poly[i * poly.len() / max]).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn calib(lens: Option<Lens>) -> CalibIn {
        // 0,5 mm por px con un corrimiento y algo de perspectiva
        CalibIn {
            camera_px: (1280, 720),
            matrix_px_to_mm: [0.5, 0.01, -20.0, -0.02, 0.5, -10.0, 1e-5, 2e-5, 1.0],
            lens,
            surface_mm: (600.0, 340.0),
        }
    }

    #[test]
    fn ida_y_vuelta_con_y_sin_lente() {
        let lens = Lens { f: 1000.0, cx: 640.0, cy: 360.0, k1: -0.12, k2: 0.03 };
        for c in [calib(None), calib(Some(lens))] {
            let inv = invert(&c.matrix_px_to_mm).unwrap();
            for p in [[0.0, 0.0], [640.0, 360.0], [1279.0, 719.0], [100.0, 650.0]] {
                let mm = px_to_mm(&c, p).unwrap();
                let back = mm_to_px(&c, &inv, mm).unwrap();
                assert!((back[0] - p[0]).abs() < 1e-3 && (back[1] - p[1]).abs() < 1e-3, "{p:?} → {mm:?} → {back:?}");
            }
        }
        // sin lente es la homografía a secas
        let mm = px_to_mm(&calib(None), [100.0, 200.0]).unwrap();
        let w = 1e-5 * 100.0 + 2e-5 * 200.0 + 1.0;
        assert!((mm[0] - (0.5 * 100.0 + 0.01 * 200.0 - 20.0) / w).abs() < 1e-9);
    }

    #[test]
    fn matriz_singular_no_se_invierte() {
        assert!(invert(&[1.0, 2.0, 3.0, 2.0, 4.0, 6.0, 0.0, 0.0, 1.0]).is_none());
        assert!(invert(&[0.0; 9]).is_none());
        assert!(surface_quad_px(&CalibIn { matrix_px_to_mm: [0.0; 9], ..calib(None) }).is_none());
    }

    #[test]
    fn mascara_de_la_superficie() {
        // 2 mm por px: la superficie de 200×100 mm ocupa [0,100)×[0,50) px de una foto de 160×90
        let c = CalibIn {
            camera_px: (160, 90),
            matrix_px_to_mm: [2.0, 0.0, 0.0, 0.0, 2.0, 0.0, 0.0, 0.0, 1.0],
            lens: None,
            surface_mm: (200.0, 100.0),
        };
        let m = surface_mask(&c, 160, 90, 2).unwrap();
        let at = |x: usize, y: usize| m[y * 160 + x] != 0;
        assert!(at(0, 0) && at(99, 49) && at(50, 25));
        assert!(!at(100, 25) && !at(50, 50) && !at(159, 89));
        // superficie fuera de la foto: sin máscara (la foto limpia mide el cuadro entero)
        let lejos = CalibIn { matrix_px_to_mm: [2.0, 0.0, 9000.0, 0.0, 2.0, 9000.0, 0.0, 0.0, 1.0], ..c };
        assert!(surface_mask(&lejos, 160, 90, 2).is_none());
    }

    #[test]
    fn casco_y_decimado() {
        let h = convex_hull(vec![[0.0, 0.0], [4.0, 0.0], [4.0, 3.0], [0.0, 3.0], [2.0, 1.0], [2.0, 0.0], [4.0, 0.0]]);
        assert_eq!(h, vec![[0.0, 0.0], [4.0, 0.0], [4.0, 3.0], [0.0, 3.0]]);
        assert_eq!(convex_hull(vec![[1.0, 1.0], [1.0, 1.0]]).len(), 1);
        let circle: Vec<[f64; 2]> = (0..200).map(|i| (i as f64 * 0.0314159).sin_cos()).map(|(s, c)| [c, s]).collect();
        let d = decimate(convex_hull(circle), 64);
        assert_eq!(d.len(), 64);
        assert!(inside(&d, [0.0, 0.0]) && !inside(&d, [1.5, 0.0]));
    }
}
