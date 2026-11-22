//! pcx · captura. Fuentes de cámara (`uvc`, `replay`, `mjpeg`, `phone`) → anillo `pcx.cam.<id>` (`pcx-shm`,
//! legible desde Python) → `FrameMeta` por `MetaSink` (closure, canal tokio o ZeroMQ PUSH).
//!
//! ```no_run
//! # async fn demo() -> Result<(), pcx_capture::BoxError> {
//! use pcx_capture::{open_source, run, RunConfig, SourceOpts, MetaSink, Envelope};
//! let src = open_source(&"replay:testdata/frames".parse()?, &SourceOpts::default()).await?;
//! let mut sinks: Vec<Box<dyn MetaSink>> = vec![Box::new(|env: &Envelope| println!("{env:?}"))];
//! run(RunConfig::new("cam0"), src, &mut sinks, tokio::signal::ctrl_c().map(|_| ())).await?;
//! # Ok(()) }
//! # use futures_util::FutureExt;
//! ```

pub mod bench;
pub mod cli;
pub mod contracts;
pub mod frame;
pub mod runner;
pub mod sink;
pub mod sources;

pub use contracts::{CaptureEvent, Device, DeviceKind, DeviceStatus, Envelope, Event, FrameMeta, Payload, ToMsgpack};
pub use frame::{BoxError, Frame};
pub use runner::{run, Publisher, RunConfig, RunStats};
#[cfg(feature = "zmq")]
pub use sink::ZmqPush;
pub use sink::{default_ingest, ChannelSink, MetaSink};
pub use sources::{open_source, RunningSource, SourceOpts, SourceSpec};
