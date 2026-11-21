//! Tipos de pcx que usa el renderer: `geometry`, `widgets` y `render` (protocolo core ↔ renderer).
//!
//! Escritos a mano con serde, espejo de `contracts/src/pcx_contracts` (Python, fuente de verdad).
//! `tests/fixtures.rs` verifica que la serialización Rust de cada fixture coincide con la forma canónica
//! que produce Python (`fixtures/canonical/`). Opcionales se serializan como `null`, igual que en Python.

pub mod geometry;
pub mod render;
pub mod widgets;

pub use geometry::*;
pub use render::*;
pub use widgets::*;

/// Versión del contrato (`Envelope.v`).
pub const CONTRACT_VERSION: u32 = 1;
