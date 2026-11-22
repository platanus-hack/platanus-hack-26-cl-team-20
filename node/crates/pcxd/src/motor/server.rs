//! Servidor HTTP/WS del motor: `GET /v1/health` y el WebSocket `/v1/ws`.
//!
//! Defensas (las mismas del core, ver `docs/arquitectura/SEGURIDAD.md`):
//! - `Host` debe ser loopback con el puerto del motor (421 si no): corta el DNS rebinding también en GET.
//! - `Origin`, si viene, debe estar en la lista permitida (403 si no), en HTTP y en el upgrade del WebSocket. Sin
//!   `Origin` (clientes nativos) se pasa: lo que autentica es el token.
//! - CORS solo hacia esos orígenes; el preflight responde `Access-Control-Allow-Private-Network: true`.
//! - En el WS el primer mensaje debe ser `hello` con el token (comparación en tiempo constante); si no, se cierra.

use std::sync::Arc;
use std::time::Duration;

use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::extract::{Request, State};
use axum::http::{header, HeaderName, HeaderValue, Method, StatusCode};
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::{Json, Router};
use serde_json::json;
use tokio::sync::mpsc;

use super::engine::{EngineHandle, CLIENT_QUEUE};
use super::frames::{decode_jpeg_b64, FrameHub};
use super::origin::Origins;
use super::proto::{id_text, In, Models, Out, PROTO_V};

/// Tope de un mensaje del WS (un `frame` 1080p en base64 pesa menos de 1 MiB).
const MAX_MESSAGE: usize = 8 << 20;
const MAX_HELLO: usize = 8 << 10;

pub struct ServerState {
    pub engine: EngineHandle,
    pub hub: Arc<FrameHub>,
    pub origins: Origins,
    pub token: String,
    pub host: String,
    pub port: u16,
    pub models: Models,
    pub hello_timeout: Duration,
}

pub fn router(st: Arc<ServerState>) -> Router {
    Router::new()
        .route("/v1/health", get(health))
        .route("/v1/ws", get(ws))
        .fallback(|| async { StatusCode::NOT_FOUND })
        .layer(axum::middleware::from_fn_with_state(st.clone(), guard))
        .with_state(st)
}

const ALLOW_PRIVATE_NETWORK: HeaderName = HeaderName::from_static("access-control-allow-private-network");

async fn guard(State(st): State<Arc<ServerState>>, req: Request, next: Next) -> Response {
    let host = req
        .headers()
        .get(header::HOST)
        .and_then(|v| v.to_str().ok())
        .map(str::to_owned)
        .or_else(|| req.uri().authority().map(|a| a.to_string()));
    if !host.is_some_and(|h| pcx_core::server::host_allowed(&h, &st.host, st.port)) {
        return StatusCode::MISDIRECTED_REQUEST.into_response();
    }
    let origin = req.headers().get(header::ORIGIN).cloned();
    if let Some(o) = &origin {
        if !o.to_str().is_ok_and(|o| st.origins.allows(o)) {
            return StatusCode::FORBIDDEN.into_response();
        }
        if req.method() == Method::OPTIONS && req.headers().contains_key(header::ACCESS_CONTROL_REQUEST_METHOD) {
            let mut r = StatusCode::NO_CONTENT.into_response();
            let h = r.headers_mut();
            h.insert(header::ACCESS_CONTROL_ALLOW_METHODS, HeaderValue::from_static("GET, OPTIONS"));
            let asked = req.headers().get(header::ACCESS_CONTROL_REQUEST_HEADERS).cloned();
            h.insert(header::ACCESS_CONTROL_ALLOW_HEADERS, asked.unwrap_or(HeaderValue::from_static("content-type")));
            h.insert(ALLOW_PRIVATE_NETWORK, HeaderValue::from_static("true"));
            h.insert(header::ACCESS_CONTROL_MAX_AGE, HeaderValue::from_static("600"));
            return cors(r, o);
        }
    }
    let r = next.run(req).await;
    match &origin {
        Some(o) => cors(r, o),
        None => r,
    }
}

fn cors(mut r: Response, origin: &HeaderValue) -> Response {
    let h = r.headers_mut();
    h.insert(header::ACCESS_CONTROL_ALLOW_ORIGIN, origin.clone());
    h.insert(header::VARY, HeaderValue::from_static("Origin"));
    r
}

async fn health(State(st): State<Arc<ServerState>>) -> Json<serde_json::Value> {
    Json(json!({
        "ok": true,
        "version": env!("CARGO_PKG_VERSION"),
        "camera": st.hub.camera(),
        "models": st.models,
    }))
}

async fn ws(State(st): State<Arc<ServerState>>, up: WebSocketUpgrade) -> Response {
    up.max_message_size(MAX_MESSAGE).max_frame_size(MAX_MESSAGE).on_upgrade(move |sock| session(st, sock))
}

async fn say(sock: &mut WebSocket, out: Out) -> bool {
    sock.send(Message::Text(out.json().into())).await.is_ok()
}

/// Primer mensaje: `hello{v, token}`. Devuelve el error con que se rechaza.
async fn hello(st: &ServerState, sock: &mut WebSocket) -> Result<(), Box<Out>> {
    let required = || Box::new(Out::error("hello_required", "el primer mensaje debe ser `hello` con el token"));
    let first = tokio::time::timeout(st.hello_timeout, sock.recv()).await;
    let Ok(Some(Ok(Message::Text(text)))) = first else { return Err(required()) };
    if text.len() > MAX_HELLO {
        return Err(required());
    }
    let Ok(In::Hello { v, token, .. }) = serde_json::from_str::<In>(text.as_str()) else { return Err(required()) };
    if v != Some(PROTO_V as f64) {
        return Err(Box::new(Out::error(
            "bad_version",
            format!("este motor habla la versión {PROTO_V} del protocolo"),
        )));
    }
    if !pcx_core::security::token_eq(token.as_bytes(), st.token.as_bytes()) {
        return Err(Box::new(Out::error("unauthorized", "token inválido")));
    }
    Ok(())
}

async fn session(st: Arc<ServerState>, mut sock: WebSocket) {
    if let Err(e) = hello(&st, &mut sock).await {
        // 4401: la consola (`motor.js`) no reintenta sola una conexión rechazada.
        let _ = say(&mut sock, *e).await;
        let close = axum::extract::ws::CloseFrame { code: 4401, reason: "hello rechazado".into() };
        let _ = sock.send(Message::Close(Some(close))).await;
        return;
    }
    let (tx, mut rx) = mpsc::channel::<String>(CLIENT_QUEUE);
    let Some(conn) = st.engine.attach(tx.clone()).await else {
        let _ = say(&mut sock, Out::error("busy", "demasiados clientes conectados al motor")).await;
        return;
    };
    loop {
        tokio::select! {
            out = rx.recv() => match out {
                Some(text) => if sock.send(Message::Text(text.into())).await.is_err() { break },
                None => break,
            },
            msg = sock.recv() => match msg {
                Some(Ok(Message::Text(text))) => match serde_json::from_str::<In>(text.as_str()) {
                    Ok(In::Frame { id, jpeg_b64, .. }) => on_frame(&st, &tx, id_text(&id), jpeg_b64),
                    Ok(In::Hello { .. }) => {}
                    Ok(m) => st.engine.send(conn, m).await,
                    Err(e) => {
                        let _ = tx.try_send(Out::error("bad_message", format!("mensaje no reconocido: {e}")).json());
                    }
                },
                Some(Ok(Message::Close(_))) | Some(Err(_)) | None => break,
                Some(Ok(_)) => {}
            },
        }
    }
    st.engine.detach(conn).await;
}

/// `frame{id}`: solo se decodifica si responde a un `need_frame` pendiente (lo demás se descarta sin costo).
fn on_frame(st: &Arc<ServerState>, tx: &mpsc::Sender<String>, id: String, jpeg_b64: String) {
    if !st.hub.is_pending(&id) {
        return;
    }
    let (hub, tx) = (st.hub.clone(), tx.clone());
    tokio::task::spawn_blocking(move || match decode_jpeg_b64(&jpeg_b64) {
        Ok(img) => {
            hub.accept_remote(&id, img);
        }
        Err(e) => {
            let _ = tx.try_send(Out::error("bad_frame", format!("frame {id}: {e}")).json());
        }
    });
}
