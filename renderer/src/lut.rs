//! LUT densa del gray code para superficies no planas (contrato v2, `LutTransform` / `LutDescriptor`).
//!
//! Convención (`npy_f32_hw2`, `output_px`, `integer_centers`): archivo `.npy` float32 little-endian (`<f4`),
//! C-order, forma `(height, width, 2)`. `lut[v, u]` es la muestra de `table_mm` con centro en
//! `(u * grid_step, v * grid_step)` mm y vale `(x, y)` en **px de salida** del proyector. NaN = sin dato.
//! Antes de usarla se verifica el `sha256` del archivo completo.
//!
//! El renderer la dibuja como una malla: cada celda de la grilla es un quad cuyos vértices van a `lut[v, u]`
//! (px de salida) y muestrean el lienzo en `(u, v) * grid_step` mm. Las celdas con algún NaN no se dibujan.

#[derive(Debug, Clone, PartialEq)]
pub struct Lut {
    pub width: u32,
    pub height: u32,
    /// `width * height * 2` floats (x_px, y_px), fila mayor.
    pub data: Vec<f32>,
}

impl Lut {
    /// Lee `path` (ruta local, `file:` opcional) y verifica `sha256` (hex) si viene.
    pub fn load(path: &str, sha256: Option<&str>) -> Result<Lut, String> {
        let path = path.strip_prefix("file://").or_else(|| path.strip_prefix("file:")).unwrap_or(path);
        let bytes = std::fs::read(path).map_err(|e| format!("{path}: {e}"))?;
        if let Some(want) = sha256 {
            let got = sha256_hex(&bytes);
            if !got.eq_ignore_ascii_case(want) {
                return Err(format!("{path}: sha256 {got} ≠ {want}"));
            }
        }
        Self::parse_npy(&bytes)
    }

    pub fn parse_npy(b: &[u8]) -> Result<Lut, String> {
        if !b.starts_with(b"\x93NUMPY") || b.len() < 10 {
            return Err("no es un .npy".into());
        }
        let (hlen, start) = if b[6] == 1 {
            (u16::from_le_bytes([b[8], b[9]]) as usize, 10)
        } else {
            (u32::from_le_bytes(b.get(8..12).ok_or("npy corto")?.try_into().unwrap()) as usize, 12)
        };
        let header = std::str::from_utf8(b.get(start..start + hlen).ok_or("npy corto")?).map_err(|e| e.to_string())?;
        if !header.contains("'<f4'") {
            return Err(format!("npy: se espera dtype '<f4' (float32 LE): {header}"));
        }
        if header.contains("'fortran_order': True") {
            return Err("npy: se espera fortran_order False".into());
        }
        let shape = header
            .split("'shape':")
            .nth(1)
            .and_then(|s| s.split('(').nth(1))
            .and_then(|s| s.split(')').next())
            .ok_or("npy: sin shape")?;
        let dims: Vec<u32> = shape.split(',').filter_map(|s| s.trim().parse().ok()).collect();
        let [h, w, 2] = dims[..] else { return Err(format!("npy: forma ({shape}) en vez de (H, W, 2)")) };
        if w == 0 || h == 0 || w > 8192 || h > 8192 {
            return Err(format!("tamaño inválido {w}x{h}"));
        }
        let n = w as usize * h as usize * 2;
        let body = &b[start + hlen..];
        if body.len() < n * 4 {
            return Err(format!("faltan datos: {} bytes para {h}x{w}x2 f32", body.len()));
        }
        let data = body[..n * 4].chunks_exact(4).map(|c| f32::from_le_bytes(c.try_into().unwrap())).collect();
        Ok(Lut { width: w, height: h, data })
    }

    /// Serializa a `.npy` v1 (herramientas y tests).
    pub fn to_npy(&self) -> Vec<u8> {
        let mut header =
            format!("{{'descr': '<f4', 'fortran_order': False, 'shape': ({}, {}, 2), }}", self.height, self.width);
        while (10 + header.len() + 1) % 64 != 0 {
            header.push(' ');
        }
        header.push('\n');
        let mut v = b"\x93NUMPY\x01\x00".to_vec();
        v.extend_from_slice(&(header.len() as u16).to_le_bytes());
        v.extend_from_slice(header.as_bytes());
        v.extend(self.data.iter().flat_map(|f| f.to_le_bytes()));
        v
    }

    /// Malla para la GPU: vértices `[x_px, y_px, x_mm, y_mm]` e índices de triángulos (sin celdas con NaN).
    pub fn mesh(&self, grid_step: f64) -> (Vec<[f32; 4]>, Vec<u32>) {
        let (w, h) = (self.width as usize, self.height as usize);
        let mut verts = Vec::with_capacity(w * h);
        for v in 0..h {
            for u in 0..w {
                let i = (v * w + u) * 2;
                verts.push([self.data[i], self.data[i + 1], (u as f64 * grid_step) as f32, (v as f64 * grid_step) as f32]);
            }
        }
        let ok = |i: usize| verts[i][0].is_finite() && verts[i][1].is_finite();
        let mut idx = Vec::with_capacity(w.saturating_sub(1) * h.saturating_sub(1) * 6);
        for v in 0..h.saturating_sub(1) {
            for u in 0..w.saturating_sub(1) {
                let (a, b, c, d) = (v * w + u, v * w + u + 1, (v + 1) * w + u, (v + 1) * w + u + 1);
                if ok(a) && ok(b) && ok(c) && ok(d) {
                    idx.extend_from_slice(&[a as u32, b as u32, c as u32, c as u32, b as u32, d as u32]);
                }
            }
        }
        (verts, idx)
    }
}

/// SHA-256 en hex (sin dependencias; los archivos de LUT son de pocos MB).
pub fn sha256_hex(data: &[u8]) -> String {
    const K: [u32; 64] = [
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98,
        0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
        0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8,
        0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
        0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819,
        0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
        0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
        0xc67178f2,
    ];
    let mut hs: [u32; 8] =
        [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];
    let mut msg = data.to_vec();
    msg.push(0x80);
    while msg.len() % 64 != 56 {
        msg.push(0);
    }
    msg.extend_from_slice(&((data.len() as u64) * 8).to_be_bytes());
    for chunk in msg.chunks_exact(64) {
        let mut w = [0u32; 64];
        for i in 0..16 {
            w[i] = u32::from_be_bytes(chunk[i * 4..i * 4 + 4].try_into().unwrap());
        }
        for i in 16..64 {
            let s0 = w[i - 15].rotate_right(7) ^ w[i - 15].rotate_right(18) ^ (w[i - 15] >> 3);
            let s1 = w[i - 2].rotate_right(17) ^ w[i - 2].rotate_right(19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16].wrapping_add(s0).wrapping_add(w[i - 7]).wrapping_add(s1);
        }
        let [mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut h] = hs;
        for i in 0..64 {
            let s1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
            let ch = (e & f) ^ (!e & g);
            let t1 = h.wrapping_add(s1).wrapping_add(ch).wrapping_add(K[i]).wrapping_add(w[i]);
            let s0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
            let maj = (a & b) ^ (a & c) ^ (b & c);
            let t2 = s0.wrapping_add(maj);
            h = g;
            g = f;
            f = e;
            e = d.wrapping_add(t1);
            d = c;
            c = b;
            b = a;
            a = t1.wrapping_add(t2);
        }
        for (x, y) in hs.iter_mut().zip([a, b, c, d, e, f, g, h]) {
            *x = x.wrapping_add(y);
        }
    }
    hs.iter().map(|x| format!("{x:08x}")).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sha256_vectores_conocidos() {
        assert_eq!(sha256_hex(b"abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
        assert_eq!(sha256_hex(b""), "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
        let long = vec![b'a'; 1_000_000];
        assert_eq!(sha256_hex(&long), "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0");
    }

    #[test]
    fn npy_ida_y_vuelta_y_malla() {
        let l = Lut { width: 3, height: 2, data: vec![0., 0., 10., 0., 20., 0., 0., 10., 10., 10., f32::NAN, 10.] };
        let r = Lut::parse_npy(&l.to_npy()).unwrap();
        assert_eq!((r.width, r.height), (3, 2));
        assert!(r.data[10].is_nan());
        let (v, i) = r.mesh(5.0);
        assert_eq!(v.len(), 6);
        assert_eq!(v[4], [10.0, 10.0, 5.0, 5.0]);
        assert_eq!(i.len(), 6, "la celda con NaN no se dibuja");
        assert!(Lut::parse_npy(b"basura").is_err());
    }
}
