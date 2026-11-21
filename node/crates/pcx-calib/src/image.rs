//! Imágenes en escala de grises y utilidades mínimas (sin OpenCV): muestreo, componentes conexas, percentiles.

/// Imagen de 8 bits, fila mayor.
#[derive(Debug, Clone, PartialEq)]
pub struct GrayImage {
    pub width: u32,
    pub height: u32,
    pub data: Vec<u8>,
}

impl GrayImage {
    pub fn new(width: u32, height: u32, fill: u8) -> Self {
        Self { width, height, data: vec![fill; (width * height) as usize] }
    }
    #[inline]
    pub fn get(&self, x: u32, y: u32) -> u8 {
        self.data[(y * self.width + x) as usize]
    }
    #[inline]
    pub fn set(&mut self, x: u32, y: u32, v: u8) {
        let w = self.width;
        self.data[(y * w + x) as usize] = v;
    }
    /// Rellena el rectángulo `[x0, x1) × [y0, y1)` (recortado a la imagen).
    pub fn fill_rect(&mut self, x0: i64, y0: i64, x1: i64, y1: i64, v: u8) {
        let (w, h) = (self.width as i64, self.height as i64);
        for y in y0.max(0)..y1.min(h) {
            for x in x0.max(0)..x1.min(w) {
                self.data[(y * w + x) as usize] = v;
            }
        }
    }
}

/// Mapa de flotantes (diferencias, contraste, coordenadas).
#[derive(Debug, Clone, PartialEq)]
pub struct FloatMap {
    pub width: u32,
    pub height: u32,
    pub data: Vec<f32>,
}

impl FloatMap {
    pub fn new(width: u32, height: u32, fill: f32) -> Self {
        Self { width, height, data: vec![fill; (width * height) as usize] }
    }
    #[inline]
    pub fn get(&self, x: u32, y: u32) -> f32 {
        self.data[(y * self.width + x) as usize]
    }
    /// Bilineal con borde recortado; `None` fuera de la imagen.
    pub fn sample(&self, x: f64, y: f64) -> Option<f64> {
        if !(x >= 0.0 && y >= 0.0 && x <= (self.width - 1) as f64 && y <= (self.height - 1) as f64) {
            return None;
        }
        let (x0, y0) = (x.floor() as u32, y.floor() as u32);
        let (x1, y1) = ((x0 + 1).min(self.width - 1), (y0 + 1).min(self.height - 1));
        let (fx, fy) = (x - x0 as f64, y - y0 as f64);
        let a = self.get(x0, y0) as f64 * (1.0 - fx) + self.get(x1, y0) as f64 * fx;
        let b = self.get(x0, y1) as f64 * (1.0 - fx) + self.get(x1, y1) as f64 * fx;
        Some(a * (1.0 - fy) + b * fy)
    }
}

/// `a − b` por píxel.
pub fn diff(a: &GrayImage, b: &GrayImage) -> FloatMap {
    let data = a.data.iter().zip(&b.data).map(|(&p, &q)| p as f32 - q as f32).collect();
    FloatMap { width: a.width, height: a.height, data }
}

/// Percentil `q` en [0, 100] (copia y ordena parcialmente).
pub fn percentile(values: &[f32], q: f64) -> f32 {
    if values.is_empty() {
        return f32::NAN;
    }
    let mut v: Vec<f32> = values.iter().copied().filter(|x| x.is_finite()).collect();
    if v.is_empty() {
        return f32::NAN;
    }
    let k = ((q / 100.0) * (v.len() - 1) as f64).round() as usize;
    let (_, m, _) = v.select_nth_unstable_by(k, |a, b| a.total_cmp(b));
    *m
}

pub fn percentile_f64(values: &[f64], q: f64) -> f64 {
    let v: Vec<f32> = values.iter().map(|&x| x as f32).collect();
    percentile(&v, q) as f64
}

/// Componente conexa (8-vecindad) de una máscara binaria.
#[derive(Debug, Clone)]
pub struct Component {
    pub pixels: Vec<(u32, u32)>,
    pub min: (u32, u32),
    pub max: (u32, u32),
}

/// Componentes conexas con al menos `min_area` píxeles (BFS iterativo).
pub fn components(mask: &[bool], width: u32, height: u32, min_area: usize) -> Vec<Component> {
    let (w, h) = (width as i64, height as i64);
    let mut seen = vec![false; mask.len()];
    let mut out = Vec::new();
    let mut stack = Vec::new();
    for start in 0..mask.len() {
        if !mask[start] || seen[start] {
            continue;
        }
        seen[start] = true;
        stack.push(start);
        let mut px = Vec::new();
        let (mut mn, mut mx) = ((u32::MAX, u32::MAX), (0u32, 0u32));
        while let Some(i) = stack.pop() {
            let (x, y) = ((i as i64 % w), (i as i64 / w));
            px.push((x as u32, y as u32));
            mn = (mn.0.min(x as u32), mn.1.min(y as u32));
            mx = (mx.0.max(x as u32), mx.1.max(y as u32));
            for dy in -1..=1 {
                for dx in -1..=1 {
                    let (nx, ny) = (x + dx, y + dy);
                    if nx < 0 || ny < 0 || nx >= w || ny >= h {
                        continue;
                    }
                    let j = (ny * w + nx) as usize;
                    if mask[j] && !seen[j] {
                        seen[j] = true;
                        stack.push(j);
                    }
                }
            }
        }
        if px.len() >= min_area {
            out.push(Component { pixels: px, min: mn, max: mx });
        }
    }
    out
}

/// Generador pseudoaleatorio determinista (xorshift64*), para RANSAC y el simulador.
#[derive(Debug, Clone)]
pub struct Rng(u64);

impl Rng {
    pub fn new(seed: u64) -> Self {
        Self(seed.wrapping_mul(0x9E37_79B9_7F4A_7C15) | 1)
    }
    pub fn next_u64(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        self.0 = x;
        x.wrapping_mul(0x2545_F491_4F6C_DD1D)
    }
    /// Uniforme en [0, 1).
    pub fn uniform(&mut self) -> f64 {
        (self.next_u64() >> 11) as f64 / (1u64 << 53) as f64
    }
    pub fn below(&mut self, n: usize) -> usize {
        (self.next_u64() % n as u64) as usize
    }
    /// Normal estándar (Box-Muller).
    pub fn normal(&mut self) -> f64 {
        let u1 = self.uniform().max(1e-12);
        let u2 = self.uniform();
        (-2.0 * u1.ln()).sqrt() * (2.0 * std::f64::consts::PI * u2).cos()
    }
}

/// Estadísticos de error: mediana, p95, máximo y n.
#[derive(Debug, Clone, Copy, Default, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct ErrStats {
    pub n: usize,
    pub p50: f64,
    pub p95: f64,
    pub max: f64,
}

impl ErrStats {
    pub fn of(v: &[f64]) -> Self {
        let f: Vec<f64> = v.iter().copied().filter(|x| x.is_finite()).collect();
        if f.is_empty() {
            return Self { n: 0, p50: f64::NAN, p95: f64::NAN, max: f64::NAN };
        }
        Self {
            n: f.len(),
            p50: percentile_f64(&f, 50.0),
            p95: percentile_f64(&f, 95.0),
            max: f.iter().copied().fold(0.0, f64::max),
        }
    }
}
