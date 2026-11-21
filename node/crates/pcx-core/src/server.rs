//! App HTTP (axum): `/mcp` (Streamable HTTP, Bearer), `/render` (WS), `/debug/*`, `/state`, `/console/confirm`,
//! `/catalog` (Bearer) y `/health` (abierto). Solo escucha en localhost por defecto.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;

use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::extract::{Request, State};
use axum::http::{header, StatusCode};
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
    pub touch_n: Arc<AtomicU64>,
    pub zmq: bool,
    pub cloud_connected: Arc<std::sync::atomic::AtomicBool>,
}

type Res = Result<AxJson<Value>, (StatusCode, AxJson<Value>)>;

fn bad(e: impl ToString) -> (StatusCode, AxJson<Value>) {
    (StatusCode::BAD_REQUEST, AxJson(json!({"error": e.to_string()})))
}

async fn auth(State(st): State<AppState>, req: Request, next: Next) -> Response {
    let p = req.uri().path();
    let guarded = ["/mcp", "/debug", "/state", "/console", "/catalog"].iter().any(|g| p.starts_with(g));
    if let (true, Some(tok)) = (guarded, st.token.as_ref()) {
        let ok = req.headers().get(header::AUTHORIZATION).and_then(|v| v.to_str().ok()) == Some(&format!("Bearer {tok}"));
        if !ok {
            return (
                StatusCode::UNAUTHORIZED,
                [(header::WWW_AUTHENTICATE, "Bearer realm=\"pcx\"")],
                AxJson(json!({"error": "unauthorized", "hint": "Authorization: Bearer $PCX_TOKEN"})),
            )
                .into_response();
        }
    }
    next.run(req).await
}

pub fn router(st: AppState, cancel: CancellationToken) -> Router {
    let tools = st.tools.clone();
    let cfg = StreamableHttpServerConfig::default()
        .with_sse_keep_alive(Some(Duration::from_secs(15)))
        .with_cancellation_token(cancel.child_token());
    let mcp = StreamableHttpService::new(move || Ok(PcxMcp::local(tools.clone())), Arc::new(LocalSessionManager::default()), cfg);
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
        m.insert("audit".into(), json!(w.store.audit_tail(20)));
    }
    AxJson(v)
}

/// `{widget_id}` o `{x_mm, y_mm}`; toque instantáneo (`api`). Con `hold_ms` simula permanencia real (dwell).
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
    let trig = if hold > 0.0 { Trigger::Dwell } else { Trigger::Api };
    let mut t = Touch { id: tid.clone(), phase: TouchPhase::Down, point_mm: PointMM(x, y), widget_id: None, object_id: None, trigger: trig };
    let down = w.ingest(wrap(Payload::Event(Event::TouchDown { touch: t.clone() }), 0, "debug", &w.node_id, None)).await.map_err(bad)?;
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
        w.ingest(wrap(Payload::Event(Event::ObjectRemoved { object_id: oid.clone(), label: None }), 0, "debug", &w.node_id, None)).await.map_err(bad)?;
        return Ok(AxJson(json!({"removed": oid})));
    }
    let c: (f64, f64) = serde_json::from_value(b.get("center_mm").cloned().unwrap_or_default()).map_err(bad)?;
    let size: (f64, f64) = b.get("size_mm").and_then(|v| serde_json::from_value(v.clone()).ok()).unwrap_or((80.0, 80.0));
    let label = b.get("label").and_then(Value::as_str).map(String::from);
    let (o2, nid) = (oid.clone(), w.node_id.clone());
    let obj = w
        .call(move |w| {
            let obj = w.make_object(&o2, label.as_deref(), c, size);
            let ev = if w.objects.contains_key(&o2) {
                Event::ObjectMoved { object_id: o2.clone(), center_mm: obj.center_mm, polygon_mm: Some(obj.polygon_mm.clone()) }
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
    match st.tools.frames.latest(None, true) {
        Some(f) => ([(header::CONTENT_TYPE, f.mime_type)], f.data).into_response(),
        None => StatusCode::NOT_FOUND.into_response(),
    }
}

async fn console_confirm(State(st): State<AppState>, AxJson(c): AxJson<ConfirmAction>) -> Res {
    st.world.confirm(c).await.map_err(bad)?;
    Ok(AxJson(json!({"ok": true})))
}

async fn catalog_get(State(st): State<AppState>) -> AxJson<Value> {
    AxJson(json!({"objects": st.world.store.catalog()}))
}

async fn catalog_post(State(st): State<AppState>, AxJson(e): AxJson<CatalogEntry>) -> Res {
    st.world.store.catalog_upsert(&e).map_err(bad)?;
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
                    Ok(FromRenderer::Presented(p)) => w.post(move |w| w.on_presented(p.rev, p.t_present_ms)),
                    Ok(FromRenderer::Stats(s)) => w.post(move |w| { w.render_stats.insert("stats".into(), serde_json::to_value(s).unwrap_or_default()); }),
                    Ok(FromRenderer::Blanked(b)) => w.post(move |w| w.on_blanked(b)),
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
