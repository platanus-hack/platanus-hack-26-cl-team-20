//! LUT por píxel (mapa denso del gray code) para superficies no planas.
//!
//! Semántica: para cada píxel `(u, v)` del proyector, el punto `(x_mm, y_mm)` de `table_mm` que
//! ese píxel ilumina. NaN (o x < 0) = píxel sin cobertura (sale negro). Si la LUT no tiene la
//! resolución de salida, se escala al tamaño de salida con vecino más cercano.
//!
//! Formatos aceptados:
//! - `.npy` (NumPy v1/v2/v3): `float32` little-endian (`<f4`), C-order, forma `(H, W, 2)`.
//!   `np.save("lut.npy", lut.astype("<f4"))`.
//! - raw `.pcxlut`: encabezado de 16 bytes `b"PCXLUT01"` + `u32 LE ancho` + `u32 LE alto`,
//!   seguido de `ancho*alto*2` `f32 LE` en orden de filas (`x_mm, y_mm` por píxel).

#[derive(Debug, Clone, PartialEq)]
pub struct Lut {
    pub width: u32,
    pub height: u32,
    /// `width * height * 2` floats (x_mm, y_mm), fila mayor.
    pub data: Vec<f32>,
}

impl Lut {
    pub fn load(path: &str) -> Result<Lut, String> {
        let path = path.strip_prefix("file://").or_else(|| path.strip_prefix("file:")).unwrap_or(path);
        let bytes = std::fs::read(path).map_err(|e| format!("{path}: {e}"))?;
        Self::parse(&bytes)
    }

    pub fn parse(b: &[u8]) -> Result<Lut, String> {
        if b.starts_with(b"\x93NUMPY") {
            parse_npy(b)
        } else if b.starts_with(b"PCXLUT01") {
            if b.len() < 16 {
                return Err("encabezado raw corto".into());
            }
            let w = u32::from_le_bytes(b[8..12].try_into().unwrap());
            let h = u32::from_le_bytes(b[12..16].try_into().unwrap());
            floats(&b[16..], w, h)
        } else {
            Err("formato de LUT desconocido (se espera .npy o PCXLUT01)".into())
        }
    }

    /// Serializa en formato raw (útil para tests y herramientas).
    pub fn to_raw(&self) -> Vec<u8> {
        let mut v = b"PCXLUT01".to_vec();
        v.extend_from_slice(&self.width.to_le_bytes());
        v.extend_from_slice(&self.height.to_le_bytes());
        v.extend(self.data.iter().flat_map(|f| f.to_le_bytes()));
        v
    }

    /// Bytes para una textura `Rg32Float`, con NaN reemplazado por -1 (sin cobertura).
    pub fn texture_bytes(&self) -> Vec<u8> {
        self.data.iter().flat_map(|f| if f.is_finite() { *f } else { -1.0 }.to_le_bytes()).collect()
    }
}

fn floats(body: &[u8], w: u32, h: u32) -> Result<Lut, String> {
    let n = w as usize * h as usize * 2;
    if body.len() < n * 4 {
        return Err(format!("faltan datos: {} bytes para {w}x{h}x2 f32", body.len()));
    }
    if w == 0 || h == 0 || w > 8192 || h > 8192 {
        return Err(format!("tamaño inválido {w}x{h}"));
    }
    let data = body[..n * 4].chunks_exact(4).map(|c| f32::from_le_bytes(c.try_into().unwrap())).collect();
    Ok(Lut { width: w, height: h, data })
}

fn parse_npy(b: &[u8]) -> Result<Lut, String> {
    let major = *b.get(6).ok_or("npy corto")?;
    let (hlen, start) = if major == 1 {
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
    floats(&b[start + hlen..], w, h)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn npy(h: u32, w: u32, data: &[f32]) -> Vec<u8> {
        let mut header = format!("{{'descr': '<f4', 'fortran_order': False, 'shape': ({h}, {w}, 2), }}");
        while (10 + header.len() + 1) % 64 != 0 {
            header.push(' ');
        }
        header.push('\n');
        let mut v = b"\x93NUMPY\x01\x00".to_vec();
        v.extend_from_slice(&(header.len() as u16).to_le_bytes());
        v.extend_from_slice(header.as_bytes());
        v.extend(data.iter().flat_map(|f| f.to_le_bytes()));
        v
    }

    #[test]
    fn npy_y_raw() {
        let d = [1.0, 2.0, 3.0, 4.0, f32::NAN, 6.0];
        let l = Lut::parse(&npy(1, 3, &d)).unwrap();
        assert_eq!((l.width, l.height), (3, 1));
        assert_eq!(l.data[3], 4.0);
        let r = Lut::parse(&l.to_raw()).unwrap();
        assert_eq!(r.width, 3);
        assert!(r.data[4].is_nan());
        assert_eq!(f32::from_le_bytes(r.texture_bytes()[16..20].try_into().unwrap()), -1.0);
        assert!(Lut::parse(b"basura").is_err());
    }
}
