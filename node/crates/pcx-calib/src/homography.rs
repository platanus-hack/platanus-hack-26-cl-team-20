//! Homografías 3x3: DLT normalizado (Hartley) + RANSAC, aplicación y escala local.

use nalgebra::{DMatrix, Matrix3, Vector3};

use crate::image::Rng;

pub type Mat3 = Matrix3<f64>;
pub type Pt = [f64; 2];

pub fn apply(h: &Mat3, p: Pt) -> Pt {
    let v = h * Vector3::new(p[0], p[1], 1.0);
    [v[0] / v[2], v[1] / v[2]]
}

/// Matriz en fila mayor (`Matrix9` del contrato).
pub fn to_row_major(h: &Mat3) -> [f64; 9] {
    let n = h / h[(2, 2)];
    [n[(0, 0)], n[(0, 1)], n[(0, 2)], n[(1, 0)], n[(1, 1)], n[(1, 2)], n[(2, 0)], n[(2, 1)], n[(2, 2)]]
}

pub fn from_row_major(m: &[f64; 9]) -> Mat3 {
    Mat3::new(m[0], m[1], m[2], m[3], m[4], m[5], m[6], m[7], m[8])
}

/// Escala local (unidades de salida por unidad de entrada) = sqrt(|det J|) en `p`.
pub fn local_scale(h: &Mat3, p: Pt) -> f64 {
    let e = 0.5;
    let a = apply(h, [p[0] + e, p[1]]);
    let b = apply(h, [p[0] - e, p[1]]);
    let c = apply(h, [p[0], p[1] + e]);
    let d = apply(h, [p[0], p[1] - e]);
    let (jxx, jyx) = ((a[0] - b[0]) / (2.0 * e), (a[1] - b[1]) / (2.0 * e));
    let (jxy, jyy) = ((c[0] - d[0]) / (2.0 * e), (c[1] - d[1]) / (2.0 * e));
    (jxx * jyy - jxy * jyx).abs().sqrt()
}

fn normalizer(pts: &[Pt]) -> Mat3 {
    let n = pts.len() as f64;
    let (mx, my) = pts.iter().fold((0.0, 0.0), |a, p| (a.0 + p[0] / n, a.1 + p[1] / n));
    let d = pts.iter().map(|p| ((p[0] - mx).powi(2) + (p[1] - my).powi(2)).sqrt()).sum::<f64>() / n;
    let s = if d > 1e-12 { std::f64::consts::SQRT_2 / d } else { 1.0 };
    Mat3::new(s, 0.0, -s * mx, 0.0, s, -s * my, 0.0, 0.0, 1.0)
}

/// DLT normalizado: `dst ≈ H · src`. Necesita ≥ 4 pares no degenerados.
pub fn dlt(src: &[Pt], dst: &[Pt]) -> Option<Mat3> {
    if src.len() < 4 || src.len() != dst.len() {
        return None;
    }
    let (ts, td) = (normalizer(src), normalizer(dst));
    let n = src.len();
    let mut a = DMatrix::<f64>::zeros(2 * n.max(5), 9);
    for i in 0..n {
        let s = apply(&ts, src[i]);
        let d = apply(&td, dst[i]);
        let (x, y, u, v) = (s[0], s[1], d[0], d[1]);
        let r = [
            [-x, -y, -1.0, 0.0, 0.0, 0.0, u * x, u * y, u],
            [0.0, 0.0, 0.0, -x, -y, -1.0, v * x, v * y, v],
        ];
        for (k, row) in r.iter().enumerate() {
            for (j, val) in row.iter().enumerate() {
                a[(2 * i + k, j)] = *val;
            }
        }
    }
    // Vector singular de menor valor: autovector de AᵀA con menor autovalor.
    let ata = a.transpose() * &a;
    let eig = nalgebra::SymmetricEigen::new(ata);
    let (mut best, mut idx) = (f64::INFINITY, 0);
    for (i, &ev) in eig.eigenvalues.iter().enumerate() {
        if ev < best {
            best = ev;
            idx = i;
        }
    }
    let h = eig.eigenvectors.column(idx);
    let hn = Mat3::new(h[0], h[1], h[2], h[3], h[4], h[5], h[6], h[7], h[8]);
    let out = td.try_inverse()? * hn * ts;
    if !out.iter().all(|v| v.is_finite()) || out[(2, 2)].abs() < 1e-15 {
        return None;
    }
    Some(out / out[(2, 2)])
}

/// Resultado de RANSAC: homografía refinada con todos los inliers y su máscara.
#[derive(Debug, Clone)]
pub struct RansacFit {
    pub h: Mat3,
    pub inliers: Vec<bool>,
    /// Residuo de cada par (unidades de `dst`).
    pub residuals: Vec<f64>,
}

/// RANSAC de 4 puntos con umbral `thr` en unidades de `dst`; refina con DLT sobre los inliers (2 rondas).
pub fn ransac(src: &[Pt], dst: &[Pt], thr: f64, iters: usize, seed: u64) -> Option<RansacFit> {
    let n = src.len();
    if n < 4 {
        return None;
    }
    let mut rng = Rng::new(seed);
    let residuals = |h: &Mat3| -> Vec<f64> {
        src.iter()
            .zip(dst)
            .map(|(s, d)| {
                let p = apply(h, *s);
                ((p[0] - d[0]).powi(2) + (p[1] - d[1]).powi(2)).sqrt()
            })
            .collect()
    };
    let mut best: Option<(usize, Mat3)> = None;
    let iters = if n == 4 { 1 } else { iters };
    for _ in 0..iters {
        let mut idx = [0usize; 4];
        if n == 4 {
            idx = [0, 1, 2, 3];
        } else {
            let mut k = 0;
            while k < 4 {
                let c = rng.below(n);
                if !idx[..k].contains(&c) {
                    idx[k] = c;
                    k += 1;
                }
            }
        }
        let s: Vec<Pt> = idx.iter().map(|&i| src[i]).collect();
        let d: Vec<Pt> = idx.iter().map(|&i| dst[i]).collect();
        let Some(h) = dlt(&s, &d) else { continue };
        let cnt = residuals(&h).iter().filter(|r| **r < thr).count();
        if best.as_ref().is_none_or(|b| cnt > b.0) {
            best = Some((cnt, h));
        }
    }
    let (_, mut h) = best?;
    for _ in 0..2 {
        let r = residuals(&h);
        let (s, d): (Vec<Pt>, Vec<Pt>) = r.iter().enumerate().filter(|(_, r)| **r < thr).map(|(i, _)| (src[i], dst[i])).unzip();
        if s.len() < 4 {
            break;
        }
        h = dlt(&s, &d)?;
    }
    let res = residuals(&h);
    let inliers = res.iter().map(|r| *r < thr).collect();
    Some(RansacFit { h, inliers, residuals: res })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dlt_recovers_known_homography() {
        let h = Mat3::new(1.2, 0.1, 30.0, -0.05, 0.9, 12.0, 1e-4, -2e-4, 1.0);
        let src: Vec<Pt> = (0..20).map(|i| [(i % 5) as f64 * 100.0, (i / 5) as f64 * 80.0]).collect();
        let dst: Vec<Pt> = src.iter().map(|p| apply(&h, *p)).collect();
        let e = dlt(&src, &dst).unwrap();
        for p in &src {
            let (a, b) = (apply(&h, *p), apply(&e, *p));
            assert!((a[0] - b[0]).abs() < 1e-6 && (a[1] - b[1]).abs() < 1e-6);
        }
    }

    #[test]
    fn ransac_rejects_outliers() {
        let h = Mat3::new(0.8, 0.05, 10.0, 0.02, 1.1, -5.0, 5e-5, 1e-4, 1.0);
        let src: Vec<Pt> = (0..60).map(|i| [(i % 10) as f64 * 50.0, (i / 10) as f64 * 40.0]).collect();
        let mut dst: Vec<Pt> = src.iter().map(|p| apply(&h, *p)).collect();
        for d in dst.iter_mut().step_by(7) {
            d[0] += 40.0;
        }
        let f = ransac(&src, &dst, 1.0, 200, 1).unwrap();
        assert_eq!(f.inliers.iter().filter(|x| !**x).count(), 9);
        let p = apply(&f.h, [123.0, 77.0]);
        let q = apply(&h, [123.0, 77.0]);
        assert!((p[0] - q[0]).abs() < 1e-6);
    }
}
