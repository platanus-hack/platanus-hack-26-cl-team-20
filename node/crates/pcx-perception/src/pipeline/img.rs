//! Operaciones de imagen del pipeline: todo en enteros y por barrido, para que la cascada quepa en su presupuesto.
//! Las máscaras de lienzo completo son `Vec<u8>` (0 / 255); las de un objeto, [`Region`] (recorte + caja).

use image::{GrayImage, RgbImage};
use pcx_objects::raster::{self, Blob};

/// Gris (0.299 R + 0.587 G + 0.114 B) en enteros.
pub fn gray(rgb: &RgbImage) -> GrayImage {
    let mut out = vec![0u8; (rgb.width() * rgb.height()) as usize];
    for (o, p) in out.iter_mut().zip(rgb.as_raw().chunks_exact(3)) {
        *o = ((77 * p[0] as u32 + 150 * p[1] as u32 + 29 * p[2] as u32 + 128) >> 8) as u8;
    }
    GrayImage::from_raw(rgb.width(), rgb.height(), out).expect("tamaño")
}

/// Desenfoque binomial 5×5 (1 4 6 4 1), separable; casi igual al gaussiano σ = 1.1 de la cascada original.
pub fn blur5(g: &GrayImage) -> GrayImage {
    let (w, h) = (g.width() as usize, g.height() as usize);
    if w < 5 || h < 5 {
        return g.clone();
    }
    let src = g.as_raw();
    let mut tmp = vec![0u16; w * h];
    for y in 0..h {
        let row = &src[y * w..(y + 1) * w];
        let out = &mut tmp[y * w..(y + 1) * w];
        for x in 0..w {
            let at = |d: isize| row[(x as isize + d).clamp(0, w as isize - 1) as usize] as u16;
            out[x] = at(-2) + 4 * at(-1) + 6 * at(0) + 4 * at(1) + at(2);
        }
    }
    let mut out = vec![0u8; w * h];
    for y in 0..h {
        let r = |d: isize| (y as isize + d).clamp(0, h as isize - 1) as usize * w;
        let (a, b, c, d, e) = (r(-2), r(-1), r(0), r(1), r(2));
        for x in 0..w {
            let s = tmp[a + x] as u32
                + 4 * tmp[b + x] as u32
                + 6 * tmp[c + x] as u32
                + 4 * tmp[d + x] as u32
                + tmp[e + x] as u32;
            out[y * w + x] = ((s + 128) >> 8) as u8;
        }
    }
    GrayImage::from_raw(w as u32, h as u32, out).expect("tamaño")
}

/// Promedio 3×3 de un mapa de un canal (quita el ruido de un píxel sin comerse una línea de 3 px).
pub fn box3(src: &[u8], w: usize, h: usize) -> Vec<u8> {
    if w < 3 || h < 3 {
        return src.to_vec();
    }
    let mut tmp = vec![0u16; w * h];
    for y in 0..h {
        let row = &src[y * w..(y + 1) * w];
        let out = &mut tmp[y * w..(y + 1) * w];
        out[0] = 2 * row[0] as u16 + row[1] as u16;
        for x in 1..w - 1 {
            out[x] = row[x - 1] as u16 + row[x] as u16 + row[x + 1] as u16;
        }
        out[w - 1] = row[w - 2] as u16 + 2 * row[w - 1] as u16;
    }
    let mut out = vec![0u8; w * h];
    for y in 0..h {
        let (a, b, c) = (y.saturating_sub(1) * w, y * w, (y + 1).min(h - 1) * w);
        for x in 0..w {
            out[b + x] = ((tmp[a + x] as u32 + tmp[b + x] as u32 + tmp[c + x] as u32 + 4) / 9) as u8;
        }
    }
    out
}

/// Mediana de `cur / ref` (en 1/256) sobre una muestra de píxeles con `ok` encendido: la ganancia global entre
/// dos fotos (autoexposición, una nube). Acotada a 0,5–2.
pub fn gain(cur: &[u8], reference: &[u8], ok: impl Fn(usize) -> bool, step: usize) -> f32 {
    let mut hist = [0u32; 1024];
    let mut n = 0u32;
    let mut i = 0;
    while i < cur.len() {
        let r = reference[i] as u32;
        if r >= 24 && ok(i) {
            hist[((cur[i] as u32 * 256) / r).min(1023) as usize] += 1;
            n += 1;
        }
        i += step;
    }
    if n < 50 {
        return 1.0;
    }
    let (mut acc, mut k) = (0u32, 0usize);
    while k < 1023 && acc + hist[k] <= n / 2 {
        acc += hist[k];
        k += 1;
    }
    (k as f32 / 256.0).clamp(0.5, 2.0)
}

/// Tabla `v → min(255, v · g)`.
pub fn gain_lut(g: f32) -> [u8; 256] {
    let mut lut = [0u8; 256];
    for (v, o) in lut.iter_mut().enumerate() {
        *o = (v as f32 * g).round().min(255.0) as u8;
    }
    lut
}

/// Una pasada 1-D de erosión (todos) o dilatación (alguno) con ventana `2r+1`, por conteo acumulado.
fn pass(src: &[u8], dst: &mut [u8], (base, step, n): (usize, usize, usize), r: usize, erode: bool, acc: &mut [u32]) {
    acc[0] = 0;
    for i in 0..n {
        acc[i + 1] = acc[i] + (src[base + i * step] > 0) as u32;
    }
    for i in 0..n {
        let (a, b) = (i.saturating_sub(r), (i + r + 1).min(n));
        let c = acc[b] - acc[a];
        let v = if erode { c as usize == b - a } else { c > 0 };
        dst[base + i * step] = if v { 255 } else { 0 };
    }
}

fn morph(m: &[u8], w: usize, h: usize, r: usize, erode: bool) -> Vec<u8> {
    if r == 0 || w == 0 || h == 0 {
        return m.to_vec();
    }
    let mut a = vec![0u8; m.len()];
    let mut b = vec![0u8; m.len()];
    let mut acc = vec![0u32; w.max(h) + 1];
    for y in 0..h {
        pass(m, &mut a, (y * w, 1, w), r, erode, &mut acc);
    }
    for x in 0..w {
        pass(&a, &mut b, (x, w, h), r, erode, &mut acc);
    }
    b
}

/// Dilatación con cuadrado de radio `r` px.
pub fn dilate(m: &[u8], w: usize, h: usize, r: usize) -> Vec<u8> {
    morph(m, w, h, r, false)
}

/// Erosión con cuadrado de radio `r` px (fuera de la imagen cuenta como encendido).
pub fn erode(m: &[u8], w: usize, h: usize, r: usize) -> Vec<u8> {
    morph(m, w, h, r, true)
}

/// Cierre (dilatar + erosionar): une fragmentos separados por menos de `2r` px sin engordar el contorno.
pub fn close(m: &[u8], w: usize, h: usize, r: usize) -> Vec<u8> {
    erode(&dilate(m, w, h, r), w, h, r)
}

pub fn as_gray(m: Vec<u8>, w: usize, h: usize) -> GrayImage {
    GrayImage::from_raw(w as u32, h as u32, m).expect("tamaño")
}

/// Máscara de un objeto: recorte binario (0 / 255) con su posición en el lienzo.
#[derive(Clone, Debug)]
pub struct Region {
    pub x0: u32,
    pub y0: u32,
    pub w: u32,
    pub h: u32,
    pub m: Vec<u8>,
    pub area: u64,
}

impl Region {
    pub fn from_blob(b: &Blob) -> Region {
        let (x0, y0, x1, y1) = b.bbox;
        let (w, h) = (x1 - x0, y1 - y0);
        let mut m = vec![0u8; (w * h) as usize];
        for r in &b.runs {
            let o = ((r.y - y0) * w) as usize;
            m[o + (r.x0 - x0) as usize..o + (r.x1 - x0) as usize].fill(255);
        }
        Region { x0, y0, w, h, m, area: b.area }
    }

    /// Recorta al contenido de una máscara de `w × h` puesta en `(ox, oy)` del lienzo. `None` si está vacía.
    pub fn from_mask(m: &[u8], w: usize, h: usize, ox: u32, oy: u32) -> Option<Region> {
        let (mut x0, mut y0, mut x1, mut y1) = (usize::MAX, usize::MAX, 0usize, 0usize);
        let mut area = 0u64;
        for y in 0..h {
            let row = &m[y * w..(y + 1) * w];
            let Some(a) = row.iter().position(|&v| v > 0) else { continue };
            let b = row.iter().rposition(|&v| v > 0).expect("hay al menos uno");
            x0 = x0.min(a);
            x1 = x1.max(b + 1);
            y0 = y0.min(y);
            y1 = y + 1;
            area += row[a..=b].iter().filter(|&&v| v > 0).count() as u64;
        }
        if area == 0 {
            return None;
        }
        let (rw, rh) = (x1 - x0, y1 - y0);
        let mut out = vec![0u8; rw * rh];
        for y in 0..rh {
            for x in 0..rw {
                out[y * rw + x] = if m[(y0 + y) * w + x0 + x] > 0 { 255 } else { 0 };
            }
        }
        Some(Region { x0: ox + x0 as u32, y0: oy + y0 as u32, w: rw as u32, h: rh as u32, m: out, area })
    }

    /// Rectángulo lleno `[x0, x1) × [y0, y1)` (se ignora la forma de `self`).
    pub fn boxed(&self, x0: u32, y0: u32, x1: u32, y1: u32) -> Region {
        let (w, h) = (x1.saturating_sub(x0).max(1), y1.saturating_sub(y0).max(1));
        Region { x0, y0, w, h, m: vec![255; (w * h) as usize], area: (w * h) as u64 }
    }

    pub fn x1(&self) -> u32 {
        self.x0 + self.w
    }
    pub fn y1(&self) -> u32 {
        self.y0 + self.h
    }

    #[inline]
    pub fn at(&self, x: u32, y: u32) -> bool {
        x >= self.x0
            && y >= self.y0
            && x < self.x1()
            && y < self.y1()
            && self.m[((y - self.y0) * self.w + (x - self.x0)) as usize] > 0
    }

    /// Recorre los píxeles encendidos como índice en un lienzo de ancho `cw`.
    pub fn for_each(&self, cw: usize, mut f: impl FnMut(usize)) {
        for y in 0..self.h as usize {
            let row = &self.m[y * self.w as usize..(y + 1) * self.w as usize];
            let base = (self.y0 as usize + y) * cw + self.x0 as usize;
            for (x, &v) in row.iter().enumerate() {
                if v > 0 {
                    f(base + x);
                }
            }
        }
    }

    /// Escribe `v` en `dst` (lienzo de ancho `cw`) donde la región está encendida.
    pub fn paint<T: Copy>(&self, dst: &mut [T], cw: usize, v: T) {
        self.for_each(cw, |i| dst[i] = v);
    }

    /// Píxeles de la región donde `full` (lienzo de ancho `cw`) está encendido.
    pub fn count_in(&self, full: &[u8], cw: usize) -> u64 {
        let mut n = 0;
        self.for_each(cw, |i| n += (full[i] > 0) as u64);
        n
    }

    pub fn inter(&self, o: &Region) -> u64 {
        let (x0, y0) = (self.x0.max(o.x0), self.y0.max(o.y0));
        let (x1, y1) = (self.x1().min(o.x1()), self.y1().min(o.y1()));
        if x0 >= x1 || y0 >= y1 {
            return 0;
        }
        let mut n = 0;
        for y in y0..y1 {
            let a = &self.m[((y - self.y0) * self.w + (x0 - self.x0)) as usize..][..(x1 - x0) as usize];
            let b = &o.m[((y - o.y0) * o.w + (x0 - o.x0)) as usize..][..(x1 - x0) as usize];
            n += a.iter().zip(b).filter(|(&p, &q)| p > 0 && q > 0).count() as u64;
        }
        n
    }

    pub fn iou(&self, o: &Region) -> f64 {
        let i = self.inter(o);
        let u = self.area + o.area - i;
        if u == 0 {
            0.0
        } else {
            i as f64 / u as f64
        }
    }

    /// Centroide en coordenadas de centro de píxel.
    pub fn centroid(&self) -> (f64, f64) {
        let (mut sx, mut sy) = (0f64, 0f64);
        for y in 0..self.h {
            for x in 0..self.w {
                if self.m[(y * self.w + x) as usize] > 0 {
                    sx += x as f64;
                    sy += y as f64;
                }
            }
        }
        let n = self.area.max(1) as f64;
        (self.x0 as f64 + sx / n + 0.5, self.y0 as f64 + sy / n + 0.5)
    }

    /// La misma región corrida `(dx, dy)` px, recortada al lienzo `cw × ch`.
    pub fn shifted(&self, dx: i32, dy: i32, cw: u32, ch: u32) -> Option<Region> {
        let (nx, ny) = (self.x0 as i64 + dx as i64, self.y0 as i64 + dy as i64);
        if nx >= 0 && ny >= 0 && nx + self.w as i64 <= cw as i64 && ny + self.h as i64 <= ch as i64 {
            return Some(Region { x0: nx as u32, y0: ny as u32, ..self.clone() });
        }
        let mut full = vec![0u8; (cw * ch) as usize];
        for y in 0..self.h as i64 {
            for x in 0..self.w as i64 {
                let (tx, ty) = (nx + x, ny + y);
                if tx >= 0
                    && ty >= 0
                    && tx < cw as i64
                    && ty < ch as i64
                    && self.m[(y * self.w as i64 + x) as usize] > 0
                {
                    full[(ty * cw as i64 + tx) as usize] = 255;
                }
            }
        }
        Region::from_mask(&full, cw as usize, ch as usize, 0, 0)
    }

    /// Región engordada `r` px (recortada al lienzo).
    pub fn dilated(&self, r: u32, cw: u32, ch: u32) -> Region {
        let (x0, y0) = (self.x0.saturating_sub(r), self.y0.saturating_sub(r));
        let (x1, y1) = ((self.x1() + r).min(cw), (self.y1() + r).min(ch));
        let (w, h) = ((x1 - x0) as usize, (y1 - y0) as usize);
        let mut m = vec![0u8; w * h];
        for y in 0..self.h as usize {
            let o = (y + (self.y0 - y0) as usize) * w + (self.x0 - x0) as usize;
            m[o..o + self.w as usize].copy_from_slice(&self.m[y * self.w as usize..(y + 1) * self.w as usize]);
        }
        let m = dilate(&m, w, h, r as usize);
        let area = m.iter().filter(|&&v| v > 0).count() as u64;
        Region { x0, y0, w: w as u32, h: h as u32, m, area }
    }

    /// Esta región sin los píxeles de `o`. `None` si no queda nada.
    pub fn minus(&self, o: &Region) -> Option<Region> {
        let mut m = self.m.clone();
        for y in 0..self.h {
            for x in 0..self.w {
                if o.at(self.x0 + x, self.y0 + y) {
                    m[(y * self.w + x) as usize] = 0;
                }
            }
        }
        Region::from_mask(&m, self.w as usize, self.h as usize, self.x0, self.y0)
    }

    /// Recorte como imagen (para `raster`/`ncc`).
    pub fn mask_image(&self) -> GrayImage {
        as_gray(self.m.clone(), self.w as usize, self.h as usize)
    }

    /// Componentes conexas de la región con al menos `min_area` px, de mayor a menor.
    pub fn parts(&self, min_area: u64) -> Vec<Region> {
        let mut out: Vec<Region> = raster::blobs(&self.mask_image(), min_area.max(1))
            .iter()
            .map(|b| {
                let mut r = Region::from_blob(b);
                r.x0 += self.x0;
                r.y0 += self.y0;
                r
            })
            .collect();
        out.sort_by_key(|r| std::cmp::Reverse(r.area));
        out
    }

    /// ¿Queda algo al erosionar `r` px? Una astilla de 1–2 px de ancho (el borde de otra máscara) no sobrevive;
    /// el mango de una cuchara sí.
    pub fn survives_erosion(&self, r: u32) -> bool {
        if r == 0 {
            return self.area > 0;
        }
        // con un marco apagado, para que el borde del recorte no cuente como encendido
        let (w, h) = (self.w as usize + 2, self.h as usize + 2);
        let mut m = vec![0u8; w * h];
        for y in 0..self.h as usize {
            m[(y + 1) * w + 1..(y + 1) * w + 1 + self.w as usize]
                .copy_from_slice(&self.m[y * self.w as usize..(y + 1) * self.w as usize]);
        }
        erode(&m, w, h, r as usize).iter().any(|&v| v > 0)
    }
}

/// Componentes de `weak` (0 / 255, lienzo `w × h`) con al menos `min_area` px que además tienen `min_strong` px
/// de `strong`: umbral con histéresis. Una parte tenue y delgada se conserva si cuelga de una parte clara.
pub fn hysteresis_regions(
    weak: &[u8],
    strong: &[u8],
    w: usize,
    h: usize,
    min_area: u64,
    min_strong: u64,
) -> Vec<Region> {
    let img = as_gray(weak.to_vec(), w, h);
    raster::blobs(&img, min_area.max(1))
        .iter()
        .filter(|b| {
            let mut n = 0u64;
            for r in &b.runs {
                let o = r.y as usize * w;
                n += strong[o + r.x0 as usize..o + r.x1 as usize].iter().filter(|&&v| v > 0).count() as u64;
                if n >= min_strong {
                    return true;
                }
            }
            false
        })
        .map(Region::from_blob)
        .collect()
}

/// Contorno externo de la región como polígono en px del lienzo, simplificado (Douglas–Peucker) hasta quedar en
/// `max_points` o menos. Con varias partes, primero se unen con un cierre de `bridge` px; si siguen separadas se
/// usa la mayor.
pub fn polygon(region: &Region, eps: f64, max_points: usize, bridge: u32) -> Vec<(f64, f64)> {
    // margen: 1 px apagado alrededor (el contorno no toca el borde) + lo que necesite el cierre
    let pad = bridge as usize + 1;
    let (w, h) = (region.w as usize + 2 * pad, region.h as usize + 2 * pad);
    let mut m = vec![0u8; w * h];
    for y in 0..region.h as usize {
        m[(y + pad) * w + pad..(y + pad) * w + pad + region.w as usize]
            .copy_from_slice(&region.m[y * region.w as usize..(y + 1) * region.w as usize]);
    }
    let img = as_gray(m, w, h);
    let img = if bridge > 0 && raster::blobs(&img, 1).len() > 1 {
        as_gray(close(img.as_raw(), w, h, bridge as usize), w, h)
    } else {
        img
    };
    let contours = imageproc::contours::find_contours::<i32>(&img);
    let best = contours
        .iter()
        .filter(|c| c.border_type == imageproc::contours::BorderType::Outer && c.points.len() >= 3)
        .max_by(|a, b| ring_area(&a.points).total_cmp(&ring_area(&b.points)));
    let Some(c) = best else { return vec![] };
    let mut eps = eps.max(0.3);
    let mut pts = imageproc::geometry::approximate_polygon_dp(&c.points, eps, true);
    while pts.len() > max_points.max(3) {
        eps *= 1.4;
        pts = imageproc::geometry::approximate_polygon_dp(&c.points, eps, true);
    }
    let (ox, oy) = (region.x0 as f64 - pad as f64, region.y0 as f64 - pad as f64);
    pts.iter().map(|p| (ox + p.x as f64 + 0.5, oy + p.y as f64 + 0.5)).collect()
}

fn ring_area(pts: &[imageproc::point::Point<i32>]) -> f64 {
    let n = pts.len();
    let mut s = 0.0;
    for i in 0..n {
        let (a, b) = (pts[i], pts[(i + 1) % n]);
        s += a.x as f64 * b.y as f64 - b.x as f64 * a.y as f64;
    }
    (s / 2.0).abs()
}

/// ZNCC de `a` (recorte de referencia, con soporte `mask`) contra `cur` en la misma caja corrida `(dx, dy)`.
/// Devuelve también la diferencia absoluta media tras quitar las medias. `None` si la caja corrida se sale.
pub fn zncc_at(
    a: &GrayImage,
    mask: &GrayImage,
    origin: (u32, u32),
    cur: &GrayImage,
    (dx, dy): (i32, i32),
) -> Option<(f64, f64)> {
    let (w, h) = (a.width() as i64, a.height() as i64);
    let (x0, y0) = (origin.0 as i64 + dx as i64, origin.1 as i64 + dy as i64);
    if x0 < 0 || y0 < 0 || x0 + w > cur.width() as i64 || y0 + h > cur.height() as i64 {
        return None;
    }
    let (cw, w) = (cur.width() as usize, w as usize);
    let (ar, mr, cr) = (a.as_raw(), mask.as_raw(), cur.as_raw());
    let (mut n, mut sa, mut sb, mut saa, mut sbb, mut sab) = (0u64, 0u64, 0u64, 0u64, 0u64, 0u64);
    for y in 0..h as usize {
        let row_c = &cr[(y0 as usize + y) * cw + x0 as usize..][..w];
        let (row_a, row_m) = (&ar[y * w..(y + 1) * w], &mr[y * w..(y + 1) * w]);
        for x in 0..w {
            if row_m[x] > 0 {
                let (p, q) = (row_a[x] as u64, row_c[x] as u64);
                n += 1;
                sa += p;
                sb += q;
                saa += p * p;
                sbb += q * q;
                sab += p * q;
            }
        }
    }
    if n < 8 {
        return None;
    }
    let nf = n as f64;
    let (ma, mb) = (sa as f64 / nf, sb as f64 / nf);
    let (va, vb) = (saa as f64 / nf - ma * ma, sbb as f64 / nf - mb * mb);
    let cov = sab as f64 / nf - ma * mb;
    let zncc = if va < 1e-6 || vb < 1e-6 { 0.0 } else { cov / (va * vb).sqrt() };
    let mut mad = 0f64;
    for y in 0..h as usize {
        let row_c = &cr[(y0 as usize + y) * cw + x0 as usize..][..w];
        let (row_a, row_m) = (&ar[y * w..(y + 1) * w], &mr[y * w..(y + 1) * w]);
        for x in 0..w {
            if row_m[x] > 0 {
                mad += ((row_a[x] as f64 - ma) - (row_c[x] as f64 - mb)).abs();
            }
        }
    }
    Some((zncc, mad / nf))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn disc(w: usize, h: usize, cx: f64, cy: f64, r: f64) -> Vec<u8> {
        (0..w * h)
            .map(|i| {
                let (x, y) = ((i % w) as f64 + 0.5, (i / w) as f64 + 0.5);
                if (x - cx).hypot(y - cy) <= r {
                    255
                } else {
                    0
                }
            })
            .collect()
    }

    #[test]
    fn region_recorta_y_cuenta() {
        let m = disc(60, 40, 30.0, 20.0, 8.0);
        let r = Region::from_mask(&m, 60, 40, 100, 200).unwrap();
        assert_eq!(r.area, m.iter().filter(|&&v| v > 0).count() as u64);
        assert_eq!((r.w, r.h), (16, 16));
        let (cx, cy) = r.centroid();
        assert!((cx - 130.0).abs() < 0.6 && (cy - 220.0).abs() < 0.6, "{cx} {cy}");
        assert!((r.iou(&r) - 1.0).abs() < 1e-9);
        let s = r.shifted(8, 0, 400, 400).unwrap();
        assert!(r.iou(&s) > 0.2 && r.iou(&s) < 0.5);
        assert_eq!(r.minus(&s).unwrap().area + r.inter(&s), r.area);
    }

    #[test]
    fn una_linea_de_tres_px_no_se_borra() {
        // «cuchara»: 3 px de ancho, 60 de largo. La apertura 5×5 de la cascada original la borraba entera.
        let (w, h) = (100usize, 40usize);
        let mut m = vec![0u8; w * h];
        for y in 18..21 {
            m[y * w + 20..y * w + 80].fill(255);
        }
        let regs = hysteresis_regions(&m, &m, w, h, 60, 10);
        assert_eq!(regs.len(), 1);
        assert_eq!(regs[0].area, 180);
        assert!(regs[0].survives_erosion(1));
        let poly = polygon(&regs[0], 0.8, 64, 0);
        assert!(poly.len() >= 4 && poly.len() <= 64);
        // y una astilla de 2 px sí se descarta como borde
        let mut s = vec![0u8; w * h];
        for y in 10..12 {
            s[y * w + 20..y * w + 80].fill(255);
        }
        assert!(!hysteresis_regions(&s, &s, w, h, 60, 10)[0].survives_erosion(1));
    }

    #[test]
    fn poligono_respeta_el_maximo_de_puntos() {
        // borde dentado: muchos vértices antes de simplificar
        let (w, h) = (300usize, 300usize);
        let m: Vec<u8> = (0..w * h)
            .map(|i| {
                let (x, y) = ((i % w) as f64 - 150.0, (i / w) as f64 - 150.0);
                let a = y.atan2(x);
                if x.hypot(y) <= 100.0 + 12.0 * (40.0 * a).sin() {
                    255
                } else {
                    0
                }
            })
            .collect();
        let r = Region::from_mask(&m, w, h, 0, 0).unwrap();
        assert!(polygon(&r, 0.5, 400, 0).len() > 64);
        let p = polygon(&r, 0.5, 64, 0);
        assert!(p.len() <= 64 && p.len() >= 16, "{}", p.len());
    }

    #[test]
    fn zncc_encuentra_el_corrimiento() {
        let cur = GrayImage::from_fn(80, 60, |x, y| image::Luma([((x * 7 + y * 13) % 200) as u8 + 20]));
        let a = image::imageops::crop_imm(&cur, 23, 14, 20, 16).to_image();
        let mask = GrayImage::from_pixel(20, 16, image::Luma([255]));
        let (z, mad) = zncc_at(&a, &mask, (20, 12), &cur, (3, 2)).unwrap();
        assert!(z > 0.999 && mad < 0.01);
        assert!(zncc_at(&a, &mask, (20, 12), &cur, (0, 0)).unwrap().0 < 0.9);
        assert!(zncc_at(&a, &mask, (70, 50), &cur, (0, 0)).is_none());
    }

    #[test]
    fn ganancia_global() {
        let a: Vec<u8> = (0..10_000).map(|i| 60 + (i % 100) as u8).collect();
        let b: Vec<u8> = a.iter().map(|&v| (v as f32 * 1.2) as u8).collect();
        assert!((gain(&b, &a, |_| true, 3) - 1.2).abs() < 0.02);
        assert_eq!(gain_lut(1.2)[100], 120);
    }
}
