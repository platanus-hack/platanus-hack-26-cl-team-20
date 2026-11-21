//! Salida de `FrameMeta`: un trait que el core conecta (closure o canal) y, opcional, ZeroMQ PUSH.
//! Ninguna salida bloquea la captura: si el consumidor no da abasto, se descarta.

use crate::contracts::Envelope;

pub trait MetaSink: Send {
    fn publish(&mut self, env: &Envelope);
}

impl<F: FnMut(&Envelope) + Send> MetaSink for F {
    fn publish(&mut self, env: &Envelope) {
        self(env)
    }
}

/// Canal tokio acotado (`try_send`: si está lleno, se descarta).
pub struct ChannelSink {
    pub tx: tokio::sync::mpsc::Sender<Envelope>,
    pub dropped: u64,
}

impl ChannelSink {
    pub fn new(tx: tokio::sync::mpsc::Sender<Envelope>) -> Self {
        Self { tx, dropped: 0 }
    }
}

impl MetaSink for ChannelSink {
    fn publish(&mut self, env: &Envelope) {
        if self.tx.try_send(env.clone()).is_err() {
            self.dropped += 1;
        }
    }
}

/// `ipc:///tmp/pcx/ingest.sock` en Unix, `tcp://127.0.0.1:9225` en Windows (`ipc://` no es confiable ahí).
/// Siempre configurable con `PCX_INGEST` / `--ingest`.
pub fn default_ingest() -> String {
    std::env::var("PCX_INGEST").unwrap_or_else(|_| {
        if cfg!(windows) { "tcp://127.0.0.1:9225" } else { "ipc:///tmp/pcx/ingest.sock" }.to_string()
    })
}

#[cfg(feature = "zmq")]
pub use zmq_push::ZmqPush;

#[cfg(feature = "zmq")]
mod zmq_push {
    use super::MetaSink;
    use crate::contracts::Envelope;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::sync::Arc;
    use std::time::Duration;
    use tokio::sync::mpsc;
    use zeromq::{PushSocket, Socket, SocketSend, ZmqMessage};

    /// PUSH hacia el core (que hace `bind` de un PULL). Una tarea tokio conecta (y reconecta cada 1 s) y envía;
    /// `publish` solo encola (hasta 8 en vuelo) y descarta si no hay core.
    pub struct ZmqPush {
        tx: mpsc::Sender<Vec<u8>>,
        pub sent: Arc<AtomicU64>,
        pub dropped: u64,
    }

    impl ZmqPush {
        /// Requiere un runtime tokio activo.
        pub fn spawn(endpoint: &str) -> Self {
            if let Some(path) = endpoint.strip_prefix("ipc://") {
                if let Some(dir) = std::path::Path::new(path).parent() {
                    let _ = std::fs::create_dir_all(dir);
                }
            }
            let (tx, mut rx) = mpsc::channel::<Vec<u8>>(8);
            let sent = Arc::new(AtomicU64::new(0));
            let (ep, sent2) = (endpoint.to_string(), sent.clone());
            tokio::spawn(async move {
                'outer: loop {
                    let mut sock = PushSocket::new();
                    if sock.connect(&ep).await.is_err() {
                        tokio::time::sleep(Duration::from_secs(1)).await;
                        while rx.try_recv().is_ok() {} // sin core: se descarta lo encolado
                        if rx.is_closed() {
                            break;
                        }
                        continue;
                    }
                    while let Some(msg) = rx.recv().await {
                        if sock.send(ZmqMessage::from(msg)).await.is_err() {
                            continue 'outer;
                        }
                        sent2.fetch_add(1, Ordering::Relaxed);
                    }
                    break;
                }
            });
            Self { tx, sent, dropped: 0 }
        }

        pub fn sent(&self) -> u64 {
            self.sent.load(Ordering::Relaxed)
        }
    }

    impl MetaSink for ZmqPush {
        fn publish(&mut self, env: &Envelope) {
            if self.tx.try_send(env.to_msgpack()).is_err() {
                self.dropped += 1;
            }
        }
    }
}
