//! `phone`: servidor HTTP + WS (puerto 9220). La página usa `getUserMedia` y manda JPEG por WebSocket con
//! contrapresión de un cuadro en vuelo. `t_capture_ns` = llegada al nodo (el reloj del celular no es confiable).
//! Los navegadores solo dan cámara en contexto seguro (HTTPS o localhost): ver README.

use crate::contracts::now_ns;
use crate::frame::{decode, fit, BoxError, Frame, LatestTx};
use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::extract::State;
use axum::response::{Html, IntoResponse};
use axum::routing::get;
use axum::{Json, Router};
use std::net::SocketAddr;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

pub const DEFAULT_PORT: u16 = 9220;

const PAGE: &str = r#"<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>pcx · cámara</title>
<style>body{margin:0;background:#111;color:#eee;font:16px system-ui;text-align:center}
video{width:100%;max-height:70vh;background:#000}button{font-size:20px;padding:12px 24px;margin:12px}</style>
</head><body><video id="v" playsinline muted autoplay></video>
<div><button id="b">Compartir cámara</button></div><div id="s">listo</div>
<script>
const W=__W__, Q=0.7, FPS=__FPS__;
const v=document.getElementById('v'), s=document.getElementById('s'), c=document.createElement('canvas');
let ws, n=0, t0=performance.now();
function connect(){
  ws=new WebSocket((location.protocol==='https:'?'wss':'ws')+'://'+location.host+'/ws');
  ws.binaryType='arraybuffer'; ws.onclose=()=>setTimeout(connect,1000);
}
async function start(){
  const st=await navigator.mediaDevices.getUserMedia({audio:false,
    video:{facingMode:{ideal:'environment'},width:{ideal:W},frameRate:{ideal:FPS}}});
  v.srcObject=st; await v.play(); connect(); tick();
}
function tick(){
  setTimeout(tick,1000/FPS);
  if(!ws||ws.readyState!==1||ws.bufferedAmount>0||!v.videoWidth) return;   // contrapresión: 1 cuadro en vuelo
  const w=Math.min(W,v.videoWidth), h=Math.round(v.videoHeight*w/v.videoWidth);
  c.width=w; c.height=h; c.getContext('2d').drawImage(v,0,0,w,h);
  c.toBlob(b=>{ if(b&&ws.readyState===1){ ws.send(b); n++; } },'image/jpeg',Q);
  const t=performance.now(); if(t-t0>1000){ s.textContent=(n*1000/(t-t0)).toFixed(1)+' fps · '+w+'x'+h; n=0; t0=t; }
}
document.getElementById('b').onclick=()=>start().catch(e=>s.textContent='error: '+e+
  (isSecureContext?'':' (la página no es HTTPS: el navegador no da cámara)'));
</script></body></html>"#;

#[derive(Default)]
pub struct PhoneStats {
    pub clients: AtomicU64,
    pub received: AtomicU64,
}

struct Ctx {
    tx: LatestTx,
    size: Option<(u32, u32)>,
    page: String,
    stats: Arc<PhoneStats>,
}

pub struct PhoneServer {
    pub addr: SocketAddr,
    pub stats: Arc<PhoneStats>,
    pub task: tokio::task::JoinHandle<()>,
}

impl PhoneServer {
    /// URL para el celular (con la IP de la LAN si se escucha en 0.0.0.0).
    pub fn url(&self) -> String {
        let host = if self.addr.ip().is_unspecified() { lan_ip() } else { self.addr.ip().to_string() };
        format!("http://{host}:{}/", self.addr.port())
    }
}

/// IP de salida (UDP sin enviar nada: solo elige la interfaz).
pub fn lan_ip() -> String {
    std::net::UdpSocket::bind("0.0.0.0:0")
        .and_then(|s| s.connect("10.255.255.255:1").and_then(|_| s.local_addr()))
        .map(|a| a.ip().to_string())
        .unwrap_or_else(|_| "127.0.0.1".into())
}

pub async fn spawn(
    bind: SocketAddr,
    size: Option<(u32, u32)>,
    fps: u32,
    tx: LatestTx,
) -> Result<PhoneServer, BoxError> {
    let w = size.map(|s| s.0).unwrap_or(1280);
    let page = PAGE.replace("__W__", &w.to_string()).replace("__FPS__", &fps.to_string());
    let stats = Arc::new(PhoneStats::default());
    let ctx = Arc::new(Ctx { tx: tx.clone(), size, page, stats: stats.clone() });
    let app = Router::new()
        .route("/", get(|State(c): State<Arc<Ctx>>| async move { Html(c.page.clone()) }))
        .route(
            "/health",
            get(|State(c): State<Arc<Ctx>>| async move {
                Json(serde_json::json!({"clients": c.stats.clients.load(Ordering::Relaxed),
                    "received": c.stats.received.load(Ordering::Relaxed)}))
            }),
        )
        .route("/ws", get(ws_upgrade))
        .with_state(ctx);
    let listener = tokio::net::TcpListener::bind(bind).await?;
    let addr = listener.local_addr()?;
    let task = tokio::spawn(async move {
        let _ = axum::serve(listener, app).with_graceful_shutdown(async move { tx.closed().await }).await;
    });
    Ok(PhoneServer { addr, stats, task })
}

async fn ws_upgrade(ws: WebSocketUpgrade, State(c): State<Arc<Ctx>>) -> impl IntoResponse {
    ws.max_message_size(8 * 1024 * 1024).on_upgrade(move |sock| handle(sock, c))
}

async fn handle(mut sock: WebSocket, c: Arc<Ctx>) {
    c.stats.clients.fetch_add(1, Ordering::Relaxed);
    while let Some(Ok(msg)) = sock.recv().await {
        if let Message::Binary(data) = msg {
            let t = now_ns();
            if let Ok(img) = decode(&data) {
                c.stats.received.fetch_add(1, Ordering::Relaxed);
                c.tx.put(Frame { img: fit(&img, c.size).into_owned(), t_capture_ns: t });
            }
        }
    }
    c.stats.clients.fetch_sub(1, Ordering::Relaxed);
}
