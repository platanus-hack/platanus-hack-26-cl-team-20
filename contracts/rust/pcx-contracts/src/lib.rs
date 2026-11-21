//! Tipos de pcx en Rust: `geometry`, `widgets`, `render` (core ↔ renderer), `world`, `events`, `commands`,
//! `envelope`, `home` y `cloud` (nodo ↔ nube).
//!
//! Escritos a mano con serde, espejo de `contracts/src/pcx_contracts` (Python, fuente de verdad).
//! `tests/fixtures.rs` verifica que la serialización Rust de cada fixture coincide con la forma canónica
//! que produce Python (`fixtures/canonical/`). Opcionales se serializan como `null`, igual que en Python.
//! Las restricciones (rangos, colores, largos) se validan al deserializar; `fixtures/invalid/cases.json` las prueba
//! igual en Python, en ambos JSON Schema y aquí. `*_ns` son i64 de época.

pub mod cloud;
pub mod commands;
pub mod envelope;
pub mod events;
pub mod geometry;
pub mod home;
pub mod render;
pub mod validate;
pub mod widgets;
pub mod world;

pub use cloud::*;
pub use commands::*;
pub use envelope::*;
pub use events::*;
pub use geometry::*;
pub use home::*;
pub use render::*;
pub use widgets::*;
pub use world::*;

/// Objeto JSON libre (`dict[str, Any]` en Python).
pub type Json = serde_json::Map<String, serde_json::Value>;

/// Versión del contrato (`Envelope.v`). v2.1 = cambios aditivos sobre v2 (ver README).
pub const CONTRACT_VERSION: u32 = 2;
