//! App HTTP (axum): `/mcp` (Streamable HTTP, Bearer), `/render` (WS), `/debug/*`, `/state`, `/console/confirm`,
//! `/catalog` (Bearer) y `/health` (abierto). Solo escucha en localhost por defecto.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;

use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::extract::{Request, State};
use axum::http::{header, HeaderValue, StatusCode};
use axum::middleware::{self, Next};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json as AxJson, Router};
use pcx_contracts::{FromRenderer, PointMM, ToRenderer, Trigger};
use rmcp::transport::streamable_http_server::session::local::LocalSessionManager;
use rmcp::transport::streamable_http_server::{StreamableHttpServerConfig, StreamableHttpService};
use serde_json::{json, Value};
use tokio::sync::broadcast::error::RecvError;
use tokio_util::sync::CancellationToken;

use crate::mcp::PcxMcp;
use crate::proto::*;
use crate::store::CatalogEntry;
use crate::tools::Tools;
use crate::world::WorldHandle;

#[derive(Clone)]
pub struct AppState {
    pub world: WorldHandle,
    pub tools: Tools,
    pub token: Option<String>,
    pub console_token: Option<String>,
    pub debug_token: Option<String>,
    pub debug: bool,
    pub host: String,
    pub port: u16,
    pub allowed_origins: Vec<String>,
    pub touch_n: Arc<AtomicU64>,
    pub zmq: bool,
    pub cloud_connected: Arc<std::sync::atomic::AtomicBool>,
}

type Res = Result<AxJson<Value>, (StatusCode, AxJson<Value>)>;

fn bad(e: impl ToString) -> (StatusCode, AxJson<Value>) {
    (StatusCode::BAD_REQUEST, AxJson(json!({"error": e.to_string()})))
}

/// `Host` admitido: loopback (o el `PCX_HOST` configurado) con el puerto del core. Es la defensa contra DNS
/// rebinding, también en GET: tras el rebinding el navegador sigue enviando el nombre del atacante en `Host`.
pub fn host_allowed(host: &str, bind_host: &str, port: u16) -> bool {
    let host = host.to_ascii_lowercase();
    let (name, p) = match host.strip_prefix('[').and_then(|r| r.split_once(']')) {
        Some((v6, rest)) => (format!("[{v6}]"), rest.strip_prefix(':')),
        None => match host.rsplit_once(':') {
            Some((n, p)) => (n.to_string(), Some(p)),
            None => (host.clone(), None),
        },
    };
    let port_ok = match p {
        Some(p) => p.parse::<u16>().is_ok_and(|p| port == 0 || p == port),
        None => port == 0 || port == 80,
    };
    let bind = bind_host.to_ascii_lowercase();
    let named = ["127.0.0.1", "localhost", "[::1]"].contains(&name.as_str())
        || (!matches!(bind.as_str(), "0.0.0.0" | "::" | "") && (name == bind || name == format!("[{bind}]")));
    named && port_ok
}

fn cors(mut r: Response, origin: &Option<HeaderValue>) -> Response {
    if let Some(o) = origin {
        let h = r.headers_mut();
        h.insert(header::ACCESS_CONTROL_ALLOW_ORIGIN, o.clone());
        h.insert(header::VARY, HeaderValue::from_static("Origin"));
        h.insert(header::ACCESS_CONTROL_ALLOW_METHODS, HeaderValue::from_static("GET, POST, DELETE, OPTIONS"));
        h.insert(
            header::ACCESS_CONTROL_ALLOW_HEADERS,
            HeaderValue::from_static(
                "authorization, content-type, accept, mcp-session-id, mcp-protocol-version, last-event-id",
            ),
        );
    }
    r
}

async fn auth(State(st): State<AppState>, req: Request, next: Next) -> Response {
    let p = req.uri().path();
    let host = req
        .headers()
        .get(header::HOST)
        .and_then(|v| v.to_str().ok())
        .map(str::to_owned)
        .or_else(|| req.uri().authority().map(|a| a.to_string()));
    let Some(host) = host.filter(|h| host_allowed(h, &st.host, st.port)) else {
        return StatusCode::MISDIRECTED_REQUEST.into_response();
    };
    // Sin `Origin` (clientes nativos): pasa. Con `Origin`: solo el propio o uno de la lista blanca exacta.
    let origin = req.headers().get(header::ORIGIN).cloned();
    if let Some(o) = &origin {
        let ok = o.to_str().is_ok_and(|o| o == format!("http://{host}") || st.allowed_origins.iter().any(|a| a == o));
        if !ok {
            return StatusCode::FORBIDDEN.into_response();
        }
        if req.method() == axum::http::Method::OPTIONS
            && req.headers().contains_key(header::ACCESS_CONTROL_REQUEST_METHOD)
        {
            return cors(StatusCode::NO_CONTENT.into_response(), &origin);
        }
    }
    let tok = if p.starts_with("/debug") {
        if !st.debug {
            return StatusCode::NOT_FOUND.into_response();
        }
        st.debug_token.as_ref()
    } else if p.starts_with("/console") {
        st.console_token.as_ref()
    } else if p == "/health" || p == "/render" {
        return cors(next.run(req).await, &origin);
    } else {
        st.token.as_ref()
    };
    let ok = tok.is_some_and(|tok| {
        req.headers()
            .get(header::AUTHORIZATION)
            .is_some_and(|v| crate::security::token_eq(v.as_bytes(), format!("Bearer {tok}").as_bytes()))
    });
    if !ok {
        return cors(StatusCode::UNAUTHORIZED.into_response(), &origin);
    }
    cors(next.run(req).await, &origin)
}

pub fn router(st: AppState, cancel: CancellationToken) -> Router {
    let tools = st.tools.clone();
    let cfg = StreamableHttpServerConfig::default()
        .with_sse_keep_alive(Some(Duration::from_secs(15)))
        .with_cancellation_token(cancel.child_token());
    let mcp = StreamableHttpService::new(
        move || Ok(PcxMcp::local(tools.clone())),
        Arc::new(LocalSessionManager::default()),
        cfg,
    );
    Router::new()
        .nest_service("/mcp", mcp)
        .route("/health", get(health))
        .route("/state", get(state))
        .route("/debug/touch", post(debug_touch))
        .route("/debug/object", post(debug_object))
        .route("/debug/look.png", get(look_png))
        .route("/console/confirm", post(console_confirm))
        .route("/catalog", get(catalog_get).post(catalog_post))
        .route("/render", get(render_ws))
        .layer(middleware::from_fn_with_state(st.clone(), auth))
        .with_state(st)
}

async fn health(State(st): State<AppState>) -> AxJson<Value> {
    let w = st.world.clone();
    let (rev, render, subs) = w.call(|w| (w.rev, w.render_stats.clone(), w.render_tx.receiver_count())).await;
    AxJson(json!({"ok": true, "node_id": w.node_id, "rev": rev, "cursor": w.last_seq(), "stream_id": w.store.stream_id,
                  "renderers": subs, "render": render, "zmq": st.zmq,
                  "cloud": st.cloud_connected.load(Ordering::Relaxed)}))
}

async fn state(State(st): State<AppState>) -> AxJson<Value> {
    let w = st.world.clone();
    let (cal, home, render) = w.call(|w| (w.calibrations.clone(), w.home_calls.clone(), w.render_stats.clone())).await;
    let mut v = serde_json::to_value(&*w.snapshot()).unwrap_or_default();
    if let Some(m) = v.as_object_mut() {
        m.insert("cursor".into(), w.last_seq().into());
        m.insert("render".into(), Value::Object(render));
        m.insert("db".into(), json!(w.store.path));
        m.insert("calibrations".into(), cal.len().into());
        m.insert("home_calls".into(), home.into());
        m.insert("audit".into(), json!(w.call(|w| w.store.audit_tail(20)).await));
    }
    AxJson(v)
}

/// `{widget_id}` o `{x_mm, y_mm}`; siempre `api`, incluso si `hold_ms` retrasa el Up.
async fn debug_touch(State(st): State<AppState>, AxJson(b): AxJson<Value>) -> Res {
    let w = &st.world;
    let (x, y) = if let Some(wid) = b.get("widget_id").and_then(Value::as_str) {
        let wid = wid.to_string();
        let c = w.call(move |w| w.scene.get(&wid).and_then(crate::geom::center)).await;
        c.ok_or_else(|| bad(format!("widget desconocido o sin posición: {}", b["widget_id"])))?
    } else {
        let f = |k: &str| b.get(k).and_then(Value::as_f64).ok_or_else(|| bad(format!("falta {k}")));
        (f("x_mm")?, f("y_mm")?)
    };
    let hold = b.get("hold_ms").and_then(Value::as_f64).unwrap_or(0.0);
    let tid = format!("dbg-{}", st.touch_n.fetch_add(1, Ordering::Relaxed) + 1);
    if !hold.is_finite() || !(0.0..=5000.0).contains(&hold) {
        return Err(bad("hold_ms fuera de rango"));
    }
    let trig = Trigger::Api;
    let mut t = Touch {
        id: tid.clone(),
        phase: TouchPhase::Down,
        point_mm: PointMM(x, y),
        widget_id: None,
        object_id: None,
        trigger: trig,
    };
    let down = w
        .ingest(wrap(Payload::Event(Event::TouchDown { touch: t.clone() }), 0, "debug", &w.node_id, None))
        .await
        .map_err(bad)?;
    if hold > 0.0 {
        tokio::time::sleep(Duration::from_secs_f64(hold / 1000.0)).await;
    }
    t.phase = TouchPhase::Up;
    w.ingest(wrap(Payload::Event(Event::TouchUp { touch: t }), 0, "debug", &w.node_id, None)).await.map_err(bad)?;
    Ok(AxJson(json!({"touch_id": tid, "x_mm": x, "y_mm": y, "trigger": trig, "widget_id": down.get("widget_id"),
                     "object_id": down.get("object_id"), "cursor": w.last_seq()})))
}

/// `{id, label?, center_mm:[x,y], size_mm?:[w,h]}` crea o mueve; `{id, remove:true}` lo quita.
async fn debug_object(State(st): State<AppState>, AxJson(b): AxJson<Value>) -> Res {
    let w = &st.world;
    let oid = b.get("id").and_then(Value::as_str).ok_or_else(|| bad("falta id"))?.to_string();
    if b.get("remove").and_then(Value::as_bool) == Some(true) {
        w.ingest(wrap(
            Payload::Event(Event::ObjectRemoved { object_id: oid.clone(), label: None }),
            0,
            "debug",
            &w.node_id,
            None,
        ))
        .await
        .map_err(bad)?;
        return Ok(AxJson(json!({"removed": oid})));
    }
    let c: (f64, f64) = serde_json::from_value(b.get("center_mm").cloned().unwrap_or_default()).map_err(bad)?;
    let size: (f64, f64) =
        b.get("size_mm").and_then(|v| serde_json::from_value(v.clone()).ok()).unwrap_or((80.0, 80.0));
    let label = b.get("label").and_then(Value::as_str).map(String::from);
    let (o2, nid) = (oid.clone(), w.node_id.clone());
    let obj = w
        .call(move |w| {
            let obj = w.make_object(&o2, label.as_deref(), c, size);
            let ev = if w.objects.contains_key(&o2) {
                Event::ObjectMoved {
                    object_id: o2.clone(),
                    center_mm: obj.center_mm,
                    polygon_mm: Some(obj.polygon_mm.clone()),
                }
            } else {
                Event::ObjectAppeared { object: obj }
            };
            let _ = w.ingest(wrap(Payload::Event(ev), 0, "debug", &nid, None));
            w.objects.get(&o2).cloned()
        })
        .await;
    Ok(AxJson(serde_json::to_value(obj).unwrap_or_default()))
}

async fn look_png(State(st): State<AppState>) -> Response {
    let frames = st.tools.frames.clone();
    match tokio::task::spawn_blocking(move || frames.latest(None, true)).await.ok().flatten() {
        Some(f) => ([(header::CONTENT_TYPE, f.mime_type)], f.data).into_response(),
        None => StatusCode::NOT_FOUND.into_response(),
    }
}

async fn console_confirm(State(st): State<AppState>, AxJson(c): AxJson<ConfirmAction>) -> Res {
    st.world.confirm(c).await.map_err(bad)?;
    Ok(AxJson(json!({"ok": true})))
}

async fn catalog_get(State(st): State<AppState>) -> AxJson<Value> {
    AxJson(json!({"objects": st.world.call(|w| w.store.catalog()).await}))
}

async fn catalog_post(State(st): State<AppState>, AxJson(e): AxJson<CatalogEntry>) -> Res {
    let entry = e.clone();
    st.world.call(move |w| w.store.catalog_upsert(&entry).map_err(|e| e.to_string())).await.map_err(bad)?;
    Ok(AxJson(json!({"ok": true, "name": e.name})))
}

async fn render_ws(ws: WebSocketUpgrade, State(st): State<AppState>) -> Response {
    ws.on_upgrade(move |sock| render_session(sock, st.world))
}

/// `scene` al conectar, luego `scene_diff`/`blank`. Si el renderer se atrasa, recibe un `scene` completo.
async fn render_session(mut sock: WebSocket, w: WorldHandle) {
    let (snap, mut rx) = w.subscribe_render().await;
    let mut base = snap.rev;
    if send(&mut sock, &ToRenderer::Scene(snap)).await.is_err() {
        return;
    }
    loop {
        tokio::select! {
            m = rx.recv() => match m {
                Ok(msg) => {
                    if let ToRenderer::SceneDiff(d) = &*msg {
                        if d.rev <= base { continue; }
                        base = d.rev;
                    }
                    if send(&mut sock, &msg).await.is_err() { break; }
                }
                Err(RecvError::Lagged(_)) => {
                    let (snap, nrx) = w.subscribe_render().await;
                    rx = nrx;
                    base = snap.rev;
                    if send(&mut sock, &ToRenderer::Scene(snap)).await.is_err() { break; }
                }
                Err(RecvError::Closed) => break,
            },
            inc = sock.recv() => match inc {
                Some(Ok(Message::Text(t))) => match serde_json::from_str::<FromRenderer>(&t) {
                    // `presented` habilita el toque de confirmación: no se descarta
                    Ok(FromRenderer::Presented(p)) => w.post_critical(move |w| w.on_presented(p.rev, p.t_present_ms)).await,
                    Ok(FromRenderer::Stats(s)) => w.post(move |w| { w.render_stats.insert("stats".into(), serde_json::to_value(s).unwrap_or_default()); }),
                    Ok(FromRenderer::Blanked(b)) => w.post_critical(move |w| w.on_blanked(b)).await,
                    // v2.1: el renderer pide la escena completa
                    Ok(FromRenderer::Resync(_)) => {
                        let (snap, nrx) = w.subscribe_render().await;
                        rx = nrx;
                        base = snap.rev;
                        if send(&mut sock, &ToRenderer::Scene(snap)).await.is_err() { break; }
                    }
                    Ok(FromRenderer::PatternShown(p)) => w.post(move |w| { w.render_stats.insert("pattern_shown".into(), serde_json::to_value(p).unwrap_or_default()); }),
                    Err(e) => tracing::debug!("render: mensaje desconocido ({e})"),
                },
                Some(Ok(Message::Close(_))) | None | Some(Err(_)) => break,
                _ => {}
            },
        }
    }
}

async fn send(sock: &mut WebSocket, m: &ToRenderer) -> Result<(), axum::Error> {
    sock.send(Message::Text(serde_json::to_string(m).unwrap_or_default().into())).await
}
