//! Geometría: rotaciones, ROI rotado (cuadrado), homografía y el muestreo bilineal hacia tensores NHWC.
use crate::frame::Frame;
use crate::types::Point2;
use std::f64::consts::PI;

/// Rota `v` un ángulo `a` (rad) con la convención del zoo (`np.dot(v, getRotationMatrix2D((0,0), deg(a))[:, :2])`).
#[inline]
pub fn rotate(v: Point2, a: f64) -> Point2 {
    let (s, c) = a.sin_cos();
    [v[0] * c - v[1] * s, v[0] * s + v[1] * c]
}

/// Ángulo que deja vertical el vector `p1 → p2` (muñeca → base del dedo medio), normalizado a [-π, π).
pub fn hand_angle(p1: Point2, p2: Point2) -> f64 {
    let r = PI / 2.0 - (-(p2[1] - p1[1])).atan2(p2[0] - p1[0]);
    r - 2.0 * PI * ((r + PI) / (2.0 * PI)).floor()
}

/// Región cuadrada rotada en px de la imagen: centro, lado y ángulo (rad).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Roi {
    pub center: Point2,
    pub size: f64,
    pub angle: f64,
}

impl Roi {
    /// Px del recorte `n × n` → px de la imagen.
    #[inline]
    pub fn to_image(&self, p: Point2, n: f64) -> Point2 {
        let k = self.size / n;
        let r = rotate([(p[0] - n / 2.0) * k, (p[1] - n / 2.0) * k], self.angle);
        [self.center[0] + r[0], self.center[1] + r[1]]
    }

    /// Caja alineada a los ejes que contiene el cuadrado rotado: `[x1, y1, x2, y2]`.
    pub fn aabb(&self) -> [f64; 4] {
        let h = self.size / 2.0;
        let mut b = [f64::MAX, f64::MAX, f64::MIN, f64::MIN];
        for c in [[-h, -h], [h, -h], [h, h], [-h, h]] {
            let r = rotate(c, self.angle);
            let p = [self.center[0] + r[0], self.center[1] + r[1]];
            b = [b[0].min(p[0]), b[1].min(p[1]), b[2].max(p[0]), b[3].max(p[1])];
        }
        b
    }
}

/// Caja alineada a los ejes de un conjunto de puntos: `[x1, y1, x2, y2]`.
pub fn bounds(pts: impl IntoIterator<Item = Point2>) -> [f64; 4] {
    let mut b = [f64::MAX, f64::MAX, f64::MIN, f64::MIN];
    for p in pts {
        b = [b[0].min(p[0]), b[1].min(p[1]), b[2].max(p[0]), b[3].max(p[1])];
    }
    b
}

pub fn iou(a: [f64; 4], b: [f64; 4]) -> f64 {
    let iw = (a[2].min(b[2]) - a[0].max(b[0])).max(0.0);
    let ih = (a[3].min(b[3]) - a[1].max(b[1])).max(0.0);
    let inter = iw * ih;
    let ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter;
    if ua <= 0.0 {
        0.0
    } else {
        inter / ua
    }
}

/// ROI de landmarks a partir de la palma, como `MPHandPose._preprocess` del zoo: se rota por muñeca→dedo medio
/// alrededor del centro de la caja de la palma, se toma la caja de los 7 puntos de la palma en ese marco, se
/// corre 0,4 alto hacia los dedos y se agranda ×3 (cuadrado del lado mayor).
pub fn roi_from_palm(palm_box: [f64; 4], kp: &[Point2; 7]) -> Roi {
    let angle = hand_angle(kp[0], kp[2]);
    let cb = [(palm_box[0] + palm_box[2]) / 2.0, (palm_box[1] + palm_box[3]) / 2.0];
    let b = bounds(kp.iter().map(|p| rotate([p[0] - cb[0], p[1] - cb[1]], -angle)));
    let (w, h) = (b[2] - b[0], b[3] - b[1]);
    let c_rel = [(b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0 - 0.4 * h];
    let c = rotate(c_rel, angle);
    Roi { center: [cb[0] + c[0], cb[1] + c[1]], size: 3.0 * w.max(h), angle }
}

/// Landmarks que usa MediaPipe para el ROI de seguimiento (sin las puntas de los dedos).
const TRACK_SUBSET: [usize; 12] = [0, 1, 2, 3, 5, 6, 9, 10, 13, 14, 17, 18];

/// ROI del cuadro siguiente a partir de los landmarks (`HandLandmarksToRectCalculator` + `RectTransformation` de
/// MediaPipe: rotación muñeca→dedo medio, caja en el marco rotado, corrimiento −0,1 alto, ×2, cuadrado).
pub fn roi_from_landmarks(lm: &[Point2]) -> Roi {
    let angle = hand_angle(lm[0], lm[9]);
    let sub: Vec<Point2> = TRACK_SUBSET.iter().map(|&i| lm[i]).collect();
    let ab = bounds(sub.iter().copied());
    let c0 = [(ab[0] + ab[2]) / 2.0, (ab[1] + ab[3]) / 2.0];
    let b = bounds(sub.iter().map(|p| rotate([p[0] - c0[0], p[1] - c0[1]], -angle)));
    let (w, h) = (b[2] - b[0], b[3] - b[1]);
    let c_rel = [(b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0 - 0.1 * h];
    let c = rotate(c_rel, angle);
    Roi { center: [c0[0] + c[0], c0[1] + c[1]], size: 2.0 * w.max(h), angle }
}

/// Aplica una homografía 3×3 (fila mayor) a un punto. `None` si cae al infinito.
pub fn apply_homography(m: &[f64; 9], p: Point2) -> Option<Point2> {
    let w = m[6] * p[0] + m[7] * p[1] + m[8];
    if w.abs() < 1e-12 {
        return None;
    }
    Some([(m[0] * p[0] + m[1] * p[1] + m[2]) / w, (m[3] * p[0] + m[4] * p[1] + m[5]) / w])
}

/// Afín de salida → imagen: `src = [a·u + b·v + c, d·u + e·v + f]` con (u, v) centros de píxel de salida.
#[derive(Debug, Clone, Copy)]
pub struct Affine(pub [f64; 6]);

impl Affine {
    /// Recorte `n × n` de un ROI rotado (convención de centros de píxel de OpenCV).
    pub fn from_roi(roi: &Roi, n: usize) -> Self {
        let k = roi.size / n as f64;
        let (s, c) = roi.angle.sin_cos();
        let h = n as f64 / 2.0;
        // src = center + R·((u+0.5 − h)·k, (v+0.5 − h)·k) − 0.5
        let (a, b, d, e) = (c * k, -s * k, s * k, c * k);
        let (u0, v0) = (0.5 - h, 0.5 - h);
        Affine([a, b, roi.center[0] + a * u0 + b * v0 - 0.5, d, e, roi.center[1] + d * u0 + e * v0 - 0.5])
    }
}

/// Muestrea `frame` con `map` hacia `out` (NHWC, `n_w × n_h × 3`, RGB en [0, 1]); fuera de la imagen = 0 (negro,
/// como el relleno del zoo). `ss` = muestras por eje (supermuestreo ≈ INTER_AREA al reducir).
/// `clamp`: fuera de la imagen repite el borde (como `cv.resize`) en vez de negro (como el relleno del zoo).
/// `window` = `[u0, v0, u1, v1]`: solo se escriben esos píxeles de salida (el resto queda como está).
#[allow(clippy::too_many_arguments)]
pub fn warp_to_tensor(
    frame: &Frame,
    map: &Affine,
    n_w: usize,
    n_h: usize,
    ss: usize,
    clamp: bool,
    window: Option<[usize; 4]>,
    out: &mut [f32],
) {
    let [a, b, c, d, e, f] = map.0;
    let ss = ss.max(1);
    let inv = 1.0 / (ss * ss) as f32 / 255.0;
    let (w, h) = (frame.width as isize, frame.height as isize);
    let (ri, bi) = frame.format.rgb_offsets();
    let px = |mut x: isize, mut y: isize| -> [f32; 3] {
        if clamp {
            x = x.clamp(0, w - 1);
            y = y.clamp(0, h - 1);
        } else if x < 0 || y < 0 || x >= w || y >= h {
            return [0.0; 3];
        }
        let o = y as usize * frame.stride + x as usize * 3;
        let s = &frame.data[o..o + 3];
        [s[ri] as f32, s[1] as f32, s[bi] as f32]
    };
    let [u0, v0, u1, v1] = window.unwrap_or([0, 0, n_w, n_h]);
    for v in v0..v1.min(n_h) {
        for u in u0..u1.min(n_w) {
            let mut acc = [0f32; 3];
            for sy in 0..ss {
                for sx in 0..ss {
                    let uu = u as f64 + (sx as f64 + 0.5) / ss as f64 - 0.5;
                    let vv = v as f64 + (sy as f64 + 0.5) / ss as f64 - 0.5;
                    let x = a * uu + b * vv + c;
                    let y = d * uu + e * vv + f;
                    let (x0, y0) = (x.floor(), y.floor());
                    let (fx, fy) = ((x - x0) as f32, (y - y0) as f32);
                    let (xi, yi) = (x0 as isize, y0 as isize);
                    let (p00, p10, p01, p11) = if xi >= 0 && yi >= 0 && xi + 1 < w && yi + 1 < h {
                        // camino rápido: los 4 vecinos dentro de la imagen
                        let o = yi as usize * frame.stride + xi as usize * 3;
                        let r0 = &frame.data[o..o + 6];
                        let r1 = &frame.data[o + frame.stride..o + frame.stride + 6];
                        let g = |s: &[u8], k: usize| [s[k + ri] as f32, s[k + 1] as f32, s[k + bi] as f32];
                        (g(r0, 0), g(r0, 3), g(r1, 0), g(r1, 3))
                    } else {
                        (px(xi, yi), px(xi + 1, yi), px(xi, yi + 1), px(xi + 1, yi + 1))
                    };
                    for k in 0..3 {
                        let top = p00[k] + (p10[k] - p00[k]) * fx;
                        let bot = p01[k] + (p11[k] - p01[k]) * fx;
                        acc[k] += top + (bot - top) * fy;
                    }
                }
            }
            let o = (v * n_w + u) * 3;
            out[o] = acc[0] * inv;
            out[o + 1] = acc[1] * inv;
            out[o + 2] = acc[2] * inv;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn angle_upright_is_zero() {
        assert!(hand_angle([0.0, 10.0], [0.0, 0.0]).abs() < 1e-12);
        // dedos hacia la derecha: hay que rotar −90° para dejarlos arriba
        let a = hand_angle([0.0, 0.0], [10.0, 0.0]);
        assert!((a - PI / 2.0).abs() < 1e-12, "{a}");
    }

    #[test]
    fn roi_roundtrip() {
        let roi = Roi { center: [100.0, 50.0], size: 224.0, angle: 0.3 };
        let p = roi.to_image([112.0, 112.0], 224.0);
        assert!((p[0] - 100.0).abs() < 1e-9 && (p[1] - 50.0).abs() < 1e-9);
    }

    #[test]
    fn homography_identity() {
        let m = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0];
        assert_eq!(apply_homography(&m, [3.0, 4.0]), Some([3.0, 4.0]));
    }
}
