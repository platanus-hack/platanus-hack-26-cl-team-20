//! Validaciones al deserializar, iguales a las restricciones de Python/JSON Schema
//! (`Meta(pattern|ge|le|min_length)`). Se usan con `#[serde(deserialize_with = "...")]`.

use serde::de::Error;
use serde::{Deserialize, Deserializer};

use crate::geometry::PointMM;

pub fn is_color(s: &str) -> bool {
    let b = s.as_bytes();
    (b.len() == 7 || b.len() == 9) && b[0] == b'#' && b[1..].iter().all(|c| c.is_ascii_hexdigit())
}

pub fn color<'de, D: Deserializer<'de>>(d: D) -> Result<String, D::Error> {
    let s = String::deserialize(d)?;
    if is_color(&s) {
        Ok(s)
    } else {
        Err(D::Error::custom(format!("color inválido {s:?} (se espera #RRGGBB o #RRGGBBAA)")))
    }
}

pub fn opt_color<'de, D: Deserializer<'de>>(d: D) -> Result<Option<String>, D::Error> {
    match Option::<String>::deserialize(d)? {
        Some(s) if !is_color(&s) => Err(D::Error::custom(format!("color inválido {s:?}"))),
        o => Ok(o),
    }
}

/// Número en [0, 1] (progress, opacity).
pub fn unit<'de, D: Deserializer<'de>>(d: D) -> Result<f64, D::Error> {
    let x = f64::deserialize(d)?;
    if (0.0..=1.0).contains(&x) {
        Ok(x)
    } else {
        Err(D::Error::custom(format!("{x} fuera de [0, 1]")))
    }
}

pub fn opt_polygon<'de, D: Deserializer<'de>>(d: D) -> Result<Option<Vec<PointMM>>, D::Error> {
    match Option::<Vec<PointMM>>::deserialize(d)? {
        Some(p) if p.len() < 3 => Err(D::Error::custom(format!("polígono con {} vértices (mínimo 3)", p.len()))),
        o => Ok(o),
    }
}

pub fn positive_u32<'de, D: Deserializer<'de>>(d: D) -> Result<u32, D::Error> {
    let x = u32::deserialize(d)?;
    if x >= 1 {
        Ok(x)
    } else {
        Err(D::Error::custom("debe ser ≥ 1"))
    }
}

pub fn positive_f64<'de, D: Deserializer<'de>>(d: D) -> Result<f64, D::Error> {
    let x = f64::deserialize(d)?;
    if x > 0.0 {
        Ok(x)
    } else {
        Err(D::Error::custom("debe ser > 0"))
    }
}

pub fn sha256<'de, D: Deserializer<'de>>(d: D) -> Result<String, D::Error> {
    let s = String::deserialize(d)?;
    if s.len() == 64 && s.bytes().all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c)) {
        Ok(s)
    } else {
        Err(D::Error::custom("sha256 debe ser 64 hex en minúscula"))
    }
}

/// `Blank.ms` en [1, 2000].
pub fn blank_ms<'de, D: Deserializer<'de>>(d: D) -> Result<u32, D::Error> {
    let x = u32::deserialize(d)?;
    if (1..=2000).contains(&x) {
        Ok(x)
    } else {
        Err(D::Error::custom(format!("ms {x} fuera de [1, 2000]")))
    }
}
