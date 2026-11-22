//! Bus local ZeroMQ (crate `zeromq`, Rust puro, compatible con pyzmq):
//! - PULL de ingesta (percepción/captura/calibración → core): `Envelope` v2 en msgpack o JSON (eventos o comandos;
//!   el core re-secuencia con su propio `seq`), o `{"type":"hands","hands":[HandState]}` (extensión local).
//!   Autenticación del worker de manos: multipart `["pcx-auth", <hands.token>, <sobre>]`. Solo esos sobres cuentan
//!   como toque físico (pueden confirmar domótica); el campo `source` del sobre no concede nada.
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
        // Conserva el endpoint de los workers; private_dir exige propietario y modo 0700 antes de enlazar.
        let base = base.map(|b| b.to_path_buf()).unwrap_or_else(|| std::path::PathBuf::from("/tmp/pcx"));
        format!("ipc://{}", base.join(format!("{name}.sock")).display())
    }
}

fn ensure_ipc(url: &str) {
    if let Some(p) = url.strip_prefix("ipc://") {
        let p = std::path::Path::new(p);
        if let Some(d) = p.parent() {
            let _ = crate::security::private_dir(d);
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
    _locks: Vec<std::fs::File>,
}

pub const AUTH_FRAME: &[u8] = b"pcx-auth";

/// Separa `["pcx-auth", token, sobre…]`. `Some((físico, sobre))`; `None` si trae credencial y no es la del worker.
pub fn authenticate(frames: &[bytes::Bytes], hands_token: Option<&str>) -> Option<(bool, Vec<u8>)> {
    let join = |f: &[bytes::Bytes]| f.iter().flat_map(|b| b.to_vec()).collect::<Vec<u8>>();
    if frames.len() >= 3 && frames[0].as_ref() == AUTH_FRAME {
        return match hands_token {
            Some(t) if crate::security::token_eq(&frames[1], t.as_bytes()) => Some((true, join(&frames[2..]))),
            _ => None,
        };
    }
    Some((false, join(frames)))
}

/// Mensaje autenticado del worker de manos: `["pcx-auth", token, sobre]`.
pub fn auth_message(token: &str, payload: Vec<u8>) -> ZmqMessage {
    let mut m = ZmqMessage::from(AUTH_FRAME.to_vec());
    m.push_back(token.as_bytes().to_vec().into());
    m.push_back(payload.into());
    m
}

pub async fn handle_raw(world: &WorldHandle, raw: &[u8], physical: bool, stats: &BusStats) {
    match decode_envelope(raw) {
        Ok(env) => {
            stats.received.fetch_add(1, Ordering::Relaxed);
            world.ingest_bus(env, physical).await;
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
    hands_token: Option<String>,
    cancel: CancellationToken,
) -> anyhow::Result<Arc<BusStats>> {
    let mut locks = vec![];
    for url in [ingest_url, events_url] {
        if let Some(path) = url.strip_prefix("ipc://") {
            let path = std::path::Path::new(path);
            if let Some(dir) = path.parent() {
                crate::security::private_dir(dir)?;
            }
            let f = std::fs::OpenOptions::new()
                .read(true)
                .write(true)
                .create(true)
                .truncate(false)
                .open(path.with_extension("lock"))?;
            f.try_lock().map_err(|e| anyhow::anyhow!("bus ocupado: {url}: {e}"))?;
            locks.push(f);
        }
    }
    ensure_ipc(ingest_url);
    ensure_ipc(events_url);
    let mut pull = PullSocket::new();
    pull.bind(ingest_url).await?;
    let mut publ = PubSocket::new();
    publ.bind(events_url).await?;
    let stats = Arc::new(BusStats { _locks: locks, ..Default::default() });
    let (s2, w2, c2) = (stats.clone(), world.clone(), cancel.clone());
    tokio::spawn(async move {
        loop {
            tokio::select! {
                _ = c2.cancelled() => break,
                m = pull.recv() => match m {
                    Ok(msg) => {
                        match authenticate(&msg.into_vec(), hands_token.as_deref()) {
                            Some((physical, raw)) => handle_raw(&w2, &raw, physical, &s2).await,
                            None => {
                                s2.errors.fetch_add(1, Ordering::Relaxed);
                                tracing::warn!("ingesta: credencial de worker inválida, mensaje descartado");
                            }
                        }
                    }
                    Err(e) => { tracing::warn!("zmq pull: {e}"); break; }
                }
            }
        }
        let _ = pull.close().await;
    });
    let mut rx = world.events.subscribe();
    let pub_stats = stats.clone();
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
        drop(pub_stats);
    });
    Ok(stats)
}
