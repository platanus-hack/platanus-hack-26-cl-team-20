//! Bus local ZeroMQ (crate `zeromq`, Rust puro, compatible con pyzmq):
//! - PULL de ingesta (percepción/captura/calibración → core): `Envelope` v2 en msgpack o JSON (eventos o comandos;
//!   el core re-secuencia con su propio `seq`), o `{"type":"hands","hands":[HandState]}` (extensión local).
//! - PUB de eventos (core → todos): multipart `[type, Envelope msgpack]`; filtra con SUBSCRIBE por prefijo de tipo.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use serde::Deserialize;
use tokio_util::sync::CancellationToken;
use zeromq::{PubSocket, PullSocket, Socket, SocketRecv, SocketSend, ZmqMessage};

use crate::proto::{decode_envelope, encode_msgpack, HandState};
use crate::world::WorldHandle;

pub const ZMQ_PORTS: (u16, u16) = (9206, 9207);

/// `tcp://127.0.0.1:<puerto>` en Windows; `ipc://<base>/<name>.sock` en Unix (por defecto `/tmp/pcx`).
pub fn zmq_endpoint(name: &str, port: u16, base: Option<&std::path::Path>) -> String {
    if cfg!(windows) {
        format!("tcp://127.0.0.1:{port}")
    } else {
        let base = base.map(|b| b.to_path_buf()).unwrap_or_else(|| std::path::PathBuf::from("/tmp/pcx"));
        format!("ipc://{}", base.join(format!("{name}.sock")).display())
    }
}

fn ensure_ipc(url: &str) {
    if let Some(p) = url.strip_prefix("ipc://") {
        let p = std::path::Path::new(p);
        if let Some(d) = p.parent() {
            let _ = std::fs::create_dir_all(d);
        }
        let _ = std::fs::remove_file(p); // socket huérfano de una corrida anterior
    }
}

#[derive(Deserialize)]
struct HandsMsg {
    #[serde(rename = "type")]
    type_: String,
    hands: Vec<HandState>,
}

#[derive(Default)]
pub struct BusStats {
    pub received: AtomicU64,
    pub errors: AtomicU64,
}

pub fn handle_raw(world: &WorldHandle, raw: &[u8], stats: &BusStats) {
    match decode_envelope(raw) {
        Ok(env) => {
            stats.received.fetch_add(1, Ordering::Relaxed);
            world.ingest_nowait(env);
        }
        Err(e) => {
            let hands: Option<HandsMsg> = serde_json::from_slice(raw).ok().or_else(|| rmp_serde::from_slice(raw).ok());
            match hands {
                Some(h) if h.type_ == "hands" => {
                    stats.received.fetch_add(1, Ordering::Relaxed);
                    world.set_hands(h.hands);
                }
                _ => {
                    stats.errors.fetch_add(1, Ordering::Relaxed);
                    tracing::warn!("ingesta: mensaje inválido ({e})");
                }
            }
        }
    }
}

/// Arranca PULL + PUB. Devuelve estadísticas; se detiene con `cancel`.
pub async fn start(
    world: WorldHandle,
    ingest_url: &str,
    events_url: &str,
    cancel: CancellationToken,
) -> anyhow::Result<Arc<BusStats>> {
    ensure_ipc(ingest_url);
    ensure_ipc(events_url);
    let mut pull = PullSocket::new();
    pull.bind(ingest_url).await?;
    let mut publ = PubSocket::new();
    publ.bind(events_url).await?;
    let stats = Arc::new(BusStats::default());
    let (s2, w2, c2) = (stats.clone(), world.clone(), cancel.clone());
    tokio::spawn(async move {
        loop {
            tokio::select! {
                _ = c2.cancelled() => break,
                m = pull.recv() => match m {
                    Ok(msg) => {
                        let raw: Vec<u8> = msg.into_vec().into_iter().flat_map(|b| b.to_vec()).collect();
                        handle_raw(&w2, &raw, &s2);
                    }
                    Err(e) => { tracing::warn!("zmq pull: {e}"); break; }
                }
            }
        }
        let _ = pull.close().await;
    });
    let mut rx = world.events.subscribe();
    tokio::spawn(async move {
        loop {
            tokio::select! {
                _ = cancel.cancelled() => break,
                ev = rx.recv() => match ev {
                    Ok(env) => {
                        let mut m = ZmqMessage::from(env.type_.clone());
                        m.push_back(bytes::Bytes::from(encode_msgpack(&*env)));
                        let _ = publ.send(m).await; // PUB descarta si un SUB está lento
                    }
                    Err(tokio::sync::broadcast::error::RecvError::Lagged(n)) => tracing::warn!("zmq pub: {n} eventos descartados"),
                    Err(_) => break,
                }
            }
        }
        let _ = publ.close().await;
    });
    Ok(stats)
}
