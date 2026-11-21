//! Core del nodo pcx (ver `README.md`): actor del mundo de escritor único, log de eventos durable (SQLite),
//! tools MCP, WS `/render`, flujos locales, ingesta (canal + ZeroMQ), enlace a la nube y supervisor `pcx up`.

pub mod bus;
pub mod cloud;
pub mod flows;
pub mod frames;
pub mod geom;
pub mod mcp;
pub mod node;
pub mod proto;
pub mod server;
pub mod store;
pub mod supervisor;
pub mod tools;
pub mod world;

pub use node::{shutdown_signal, Core, Settings};
pub use world::{World, WorldHandle};
