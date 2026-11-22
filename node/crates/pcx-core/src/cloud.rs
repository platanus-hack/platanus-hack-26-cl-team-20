//! Enlace opcional a la nube (gateway en Render) por una WS(S) saliente con el protocolo `cloud` v2.
//!
//! hello{stream_id, log_first/last_cursor} → welcome{last_stream_id, last_cursor} (o hello_rejected) →
//! state_snapshot{cursor, world} → [history_gap] → event_batch{replay:true}… → replay_done → en vivo.
//! La nube manda tool_call; el nodo ejecuta la tool localmente (idempotente por `request_id`) y responde tool_result
//! con el mismo `call_id`. Heartbeat cada ~10 s; sin mensajes 35 s se corta. Reconexión con backoff (0,5 s → 30 s).

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;

use futures::{SinkExt, StreamExt};
use tokio::sync::mpsc;
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::Message;
use tokio_util::sync::CancellationToken;

use crate::proto::*;
use crate::tools::{tool_names, Tools};

pub struct CloudLink {
    pub url: String,
    pub token: String,
    pub heartbeat_s: f64,
    pub backoff_max_s: f64,
    pub batch_max: i64,
    pub connected: Arc<AtomicBool>,
    pub connects: Arc<AtomicU64>,
}

impl CloudLink {
    pub fn new(url: impl Into<String>, token: impl Into<String>) -> Self {
        CloudLink {
            url: url.into(),
            token: token.into(),
            heartbeat_s: 10.0,
            backoff_max_s: 30.0,
            batch_max: 200,
            connected: Arc::new(AtomicBool::new(false)),
            connects: Arc::new(AtomicU64::new(0)),
        }
    }

    pub fn spawn(self: Arc<Self>, tools: Tools, cancel: CancellationToken) -> tokio::task::JoinHandle<()> {
        tokio::spawn(async move {
            let mut delay = 0.5f64;
            loop {
                let r = tokio::select! {
                    _ = cancel.cancelled() => break,
                    r = self.session(&tools) => r,
                };
                self.connected.store(false, Ordering::Relaxed);
                match r {
                    Ok(()) => delay = 0.5,
                    Err(e) => tracing::info!("enlace a la nube caído: {e}"),
                }
                let jitter = 0.8 + 0.4 * rand::random::<f64>();
                tokio::select! {
                    _ = cancel.cancelled() => break,
                    _ = tokio::time::sleep(Duration::from_secs_f64(delay * jitter)) => {}
                }
                delay = (delay * 2.0).min(self.backoff_max_s);
            }
        })
    }

    async fn session(&self, tools: &Tools) -> anyhow::Result<()> {
        let world = &tools.world;
        let store = &world.store;
        let mut req = self.url.as_str().into_client_request()?;
        req.headers_mut().insert("Authorization", format!("Bearer {}", self.token).parse()?);
        let (ws, _) = tokio::time::timeout(Duration::from_secs(10), tokio_tungstenite::connect_async(req)).await??;
        let (mut tx, mut rx) = ws.split();
        let enc = |m: &CloudMessage| Message::Text(serde_json::to_string(m).unwrap_or_default().into());
        let stream_id = store.stream_id.clone();
        let hello = CloudMessage::Hello {
            node_id: world.node_id.clone(),
            token: self.token.clone(),
            capabilities: tool_names(),
            version: env!("CARGO_PKG_VERSION").into(),
            contract_versions: SUPPORTED_VERSIONS.to_vec(),
            stream_id: stream_id.clone(),
            log_first_cursor: world.call(|w| w.store.first_seq()).await,
            log_last_cursor: world.last_seq(),
            retention: store.retention.clone(),
        };
        tx.send(enc(&hello)).await?;
        let welcome = loop {
            let m = tokio::time::timeout(Duration::from_secs(15), rx.next())
                .await?
                .ok_or_else(|| anyhow::anyhow!("cerrado antes de welcome"))??;
            if let Message::Text(t) = m {
                break serde_json::from_str::<CloudMessage>(&t)?;
            }
        };
        let (last_stream, last_cursor) = match welcome {
            CloudMessage::Welcome { last_stream_id, last_cursor, .. } => (last_stream_id, last_cursor.unwrap_or(0)),
            CloudMessage::HelloRejected { reason, detail, .. } => anyhow::bail!("hello rechazado: {reason} {detail:?}"),
            other => anyhow::bail!("se esperaba welcome, llegó {}", tag_of(&other)),
        };
        self.connected.store(true, Ordering::Relaxed);
        self.connects.fetch_add(1, Ordering::Relaxed);
        // estado recuperado + historial desde last_cursor
        let (snap_cursor, snapshot) = world.call(|w| (w.seq, w.snapshot())).await;
        tx.send(enc(&CloudMessage::StateSnapshot {
            stream_id: stream_id.clone(),
            cursor: snap_cursor,
            world: snapshot,
        }))
        .await?;
        let first = world.call(|w| w.store.first_seq()).await;
        let mut from = last_cursor;
        let mut complete = true;
        if let Some(ls) = last_stream.filter(|s| *s != stream_id) {
            complete = false;
            from = 0;
            tx.send(enc(&CloudMessage::HistoryGap(HistoryGap {
                stream_id: ls,
                from_: last_cursor + 1,
                to: None,
                reason: GapReason::StreamReset,
            })))
            .await?;
            if first > 1 {
                tx.send(enc(&CloudMessage::HistoryGap(HistoryGap {
                    stream_id: stream_id.clone(),
                    from_: 1,
                    to: Some(first - 1),
                    reason: GapReason::StreamReset,
                })))
                .await?;
            }
        } else if first > 0 && last_cursor + 1 < first {
            complete = false;
            tx.send(enc(&CloudMessage::HistoryGap(HistoryGap {
                stream_id: stream_id.clone(),
                from_: last_cursor + 1,
                to: Some(first - 1),
                reason: GapReason::Retention,
            })))
            .await?;
        }
        while from < snap_cursor {
            let batch = self.batch_max;
            let (evs, _) = world.call(move |w| w.store.after(from, &[], batch)).await;
            let evs: Vec<Envelope> = evs.into_iter().filter(|e| e.seq <= snap_cursor).collect();
            let Some(last) = evs.last().map(|e| e.seq) else { break };
            tx.send(enc(&CloudMessage::EventBatch {
                stream_id: stream_id.clone(),
                cursor: last,
                events: evs,
                replay: true,
            }))
            .await?;
            from = last;
        }
        tx.send(enc(&CloudMessage::ReplayDone {
            stream_id: stream_id.clone(),
            cursor: snap_cursor,
            history_complete: complete,
        }))
        .await?;

        // en vivo: un escritor (canal), subidor de eventos, heartbeat, lector de tool_call
        let (out_tx, mut out_rx) = mpsc::channel::<CloudMessage>(256);
        let mut seq_rx = world.seq.clone();
        let mut state_rx = world.state.clone();
        let permits = Arc::new(tokio::sync::Semaphore::new(32));
        let mut sent = snap_cursor;
        let mut hb = tokio::time::interval(Duration::from_secs_f64(self.heartbeat_s));
        hb.tick().await;
        let mut last_rx = tokio::time::Instant::now();
        let mut idle = tokio::time::interval(Duration::from_secs(5));
        loop {
            tokio::select! {
                m = out_rx.recv() => if let Some(m) = m { tx.send(enc(&m)).await?; },
                _ = seq_rx.changed() => {
                    tokio::time::sleep(Duration::from_millis(20)).await; // agrupa ráfagas
                    loop {
                        let batch = self.batch_max;
                        let (evs, _) = world.call(move |w| w.store.after(sent, &[], batch)).await;
                        let Some(last) = evs.last().map(|e| e.seq) else { break };
                        tx.send(enc(&CloudMessage::EventBatch { stream_id: stream_id.clone(), cursor: last, events: evs, replay: false })).await?;
                        sent = last;
                    }
                }
                _ = state_rx.changed() => {
                    let (cursor, snapshot) = world.call(|w| (w.seq, w.snapshot())).await;
                    tx.send(enc(&CloudMessage::StateSnapshot { stream_id: stream_id.clone(), cursor, world: snapshot })).await?;
                }
                _ = hb.tick() => tx.send(enc(&CloudMessage::Heartbeat { t_ms: now_ms(), cursor: Some(sent) })).await?,
                _ = idle.tick() => if last_rx.elapsed() > Duration::from_secs(35) { anyhow::bail!("sin mensajes de la nube en 35 s"); },
                m = rx.next() => {
                    let Some(m) = m else { return Ok(()) };
                    last_rx = tokio::time::Instant::now();
                    match m? {
                        Message::Text(t) => match serde_json::from_str::<CloudMessage>(&t) {
                            Ok(CloudMessage::ToolCall { call_id, tool, args, timeout_s }) => {
                                let Ok(permit) = permits.clone().try_acquire_owned() else {
                                    tx.send(enc(&CloudMessage::ToolResult { call_id, request_id: None, ok: false, content: vec![], structured: None })).await?;
                                    continue;
                                };
                                let (tools, out) = (tools.clone(), out_tx.clone());
                                tokio::spawn(async move {
                                    let _permit = permit;
                                    let fut = tools.call(&tool, serde_json::Value::Object(args), "cloud", Some(&call_id));
                                    let res = match timeout_s {
                                        Some(s) if s > 0.0 => tokio::time::timeout(Duration::from_secs_f64(s), fut).await.ok(),
                                        _ => Some(fut.await),
                                    };
                                    let msg = match res {
                                        Some(o) => o.to_cloud(&call_id),
                                        None => CloudMessage::ToolResult { call_id, request_id: None, ok: false, content: vec![],
                                            structured: serde_json::json!({"error": "timeout"}).as_object().cloned() },
                                    };
                                    let _ = out.send(msg).await;
                                });
                            }
                            // v2.1: consola de la nube → confirmación (el nodo verifica digest y política)
                            Ok(CloudMessage::ConfirmAction(c)) => {
                                if let Err(e) = world.confirm(c).await {
                                    tracing::warn!("nube: confirm_action rechazado ({e})");
                                }
                            }
                            Ok(_) => {}
                            Err(e) => tracing::debug!("nube: mensaje desconocido ({e})"),
                        },
                        Message::Close(_) => return Ok(()),
                        _ => {}
                    }
                }
            }
        }
    }
}
