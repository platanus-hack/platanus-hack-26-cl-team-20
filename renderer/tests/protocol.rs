//! Protocolo contra un core falso (servidor WS en Rust, puerto efímero): snapshot → presented,
//! diff → presented, hueco de rev → resync, stats cada 1 s, reconexión manteniendo la escena.

use std::net::TcpListener;
use std::sync::mpsc;
use std::time::{Duration, Instant};

use pcx_renderer::runtime::FrameRenderer;
use pcx_renderer::runtime::{Runtime, now_epoch_ms};
use pcx_renderer::scene::SceneState;
use serde_json::{Value, json};
use tungstenite::{Message, WebSocket};

// El protocolo se prueba independientemente de Metal/Vulkan; golden.rs verifica los cuadros GPU.
struct ProtocolRenderer;
impl FrameRenderer for ProtocolRenderer {
    type Target = ();
    fn scene(&mut self, _: &SceneState, _: f64, _: f64, _: &(), _: (u32, u32)) {}
    fn black(&mut self, _: &()) {}
    fn pattern(&mut self, _: u64, src: &str, _: &()) -> bool {
        pcx_renderer::images::ImageCache::new(std::path::PathBuf::from(".")).load(src).is_ok()
    }
}

type Ws = WebSocket<std::net::TcpStream>;

fn send(ws: &mut Ws, v: Value) {
    ws.send(Message::text(v.to_string())).unwrap();
}

/// Espera un mensaje del renderer que cumpla `pred` (descarta los demás).
fn wait_for(ws: &mut Ws, what: &str, pred: impl Fn(&Value) -> bool) -> Value {
    let t0 = Instant::now();
    loop {
        assert!(t0.elapsed() < Duration::from_secs(5), "timeout esperando {what}");
        match ws.read() {
            Ok(Message::Text(t)) => {
                let v: Value = serde_json::from_str(&t).unwrap();
                if pred(&v) {
                    return v;
                }
            }
            Ok(_) => {}
            Err(tungstenite::Error::Io(e))
                if matches!(e.kind(), std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut) => {}
            Err(e) => panic!("ws: {e}"),
        }
    }
}

fn presented(rev: i64) -> impl Fn(&Value) -> bool {
    move |v| v["type"] == "presented" && v["rev"] == rev && v["t_present_ms"].as_f64().is_some()
}

fn scene(rev: i64) -> Value {
    json!({"type": "scene", "rev": rev, "surface": {"width_mm": 400, "height_mm": 250},
           "widgets": [{"type": "label", "id": "a", "anchor": {"point_mm": [20, 20]}, "text": "hola ✓"},
                       {"type": "button", "id": "b", "rect_mm": [20, 60, 80, 30], "label": "OK", "progress": 0.3}]})
}

fn diff(rev: i64, base: i64) -> Value {
    json!({"type": "scene_diff", "rev": rev, "base_rev": base,
           "upsert": [{"type": "button", "id": "b", "rect_mm": [20, 60, 80, 30], "label": "OK", "progress": 0.9}],
           "remove": [], "transforms": null})
}

fn accept(l: &TcpListener) -> Ws {
    let (s, _) = l.accept().unwrap();
    s.set_read_timeout(Some(Duration::from_millis(20))).unwrap();
    tungstenite::accept(s).unwrap()
}

#[test]
fn protocolo_con_core_falso() {
    let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
    let port = listener.local_addr().unwrap().port();
    let (done_tx, done_rx) = mpsc::channel::<Result<(), String>>();
    use base64::Engine as _;
    let mut png = std::io::Cursor::new(Vec::new());
    image::RgbaImage::from_pixel(1, 1, image::Rgba([255, 255, 255, 255]))
        .write_to(&mut png, image::ImageFormat::Png)
        .unwrap();
    let pattern_uri =
        format!("data:image/png;base64,{}", base64::engine::general_purpose::STANDARD.encode(png.into_inner()));
    let server = std::thread::spawn(move || {
        let r = std::panic::catch_unwind(move || {
            let mut ws = accept(&listener);
            send(&mut ws, scene(1));
            wait_for(&mut ws, "presented 1", presented(1));
            send(&mut ws, diff(2, 1));
            wait_for(&mut ws, "presented 2", presented(2));
            // v2.1: primer cuadro con patrón → pattern_shown, una sola vez.
            send(
                &mut ws,
                json!({"type": "pattern", "pattern_id": "calib-1",
                "image": {"data_uri": pattern_uri}, "until_ms": null}),
            );
            let shown = wait_for(&mut ws, "pattern_shown", |v| v["type"] == "pattern_shown");
            assert_eq!(shown["pattern_id"], "calib-1");
            assert!(shown["t_present_ms"].as_f64().unwrap() > 0.0);
            // Una nueva rev sigue pendiente mientras el patrón tapa los widgets.
            send(&mut ws, scene(3));
            let deadline = Instant::now() + Duration::from_millis(80);
            while Instant::now() < deadline {
                if let Ok(Message::Text(t)) = ws.read() {
                    let v: Value = serde_json::from_str(&t).unwrap();
                    assert_ne!(v["type"], "pattern_shown", "ack duplicado");
                    assert!(!presented(3)(&v), "no se mostró todavía la escena");
                }
            }
            send(&mut ws, json!({"type": "pattern_clear"}));
            wait_for(&mut ws, "presented 3 tras pattern_clear", presented(3));
            // Falta el rev 4: el renderer debe pedir snapshot y no aplicar el diff.
            send(&mut ws, diff(5, 4));
            let r = wait_for(&mut ws, "resync", |v| v["type"] == "resync");
            assert_eq!(r["have_rev"], 3);
            send(&mut ws, scene(5));
            wait_for(&mut ws, "presented 5", presented(5));
            let st = wait_for(&mut ws, "stats", |v| v["type"] == "stats");
            assert!(st["fps"].as_f64().unwrap() > 1.0, "{st}");
            assert!(st["frame_ms_p50"].as_f64().is_some() && st["frame_ms_p95"].as_f64().is_some());
            // blank: salida negra 150 ms y `blanked` con el intervalo real.
            send(&mut ws, json!({"type": "blank", "request_id": "req-foto-1", "ms": 150, "reason": "clean_photo"}));
            let b = wait_for(&mut ws, "blanked", |v| v["type"] == "blanked");
            assert_eq!(b["request_id"], "req-foto-1");
            let dur = b["t_end_ms"].as_f64().unwrap() - b["t_start_ms"].as_f64().unwrap();
            assert!((140.0..1000.0).contains(&dur), "duración del blank {dur} ms");
            // Corte: el renderer reconecta solo y conserva la escena.
            drop(ws);
            let mut ws = accept(&listener);
            send(&mut ws, diff(6, 5));
            wait_for(&mut ws, "presented 6 tras reconectar", presented(6));
            // Confirmación de domótica: el core solo acepta el toque tras `presented` del rev que la trae.
            let confirm = |rev: i64| {
                json!({"type": "scene_diff", "rev": rev, "base_rev": rev - 1, "remove": [], "transforms": null,
                       "upsert": [{"type": "panel", "id": "pcx:confirm-q:cf-1", "rect_mm": [150, 20, 230, 80],
                                   "title": "¿Confirmas?", "body": "lock.unlock → lock.puerta", "color": "#FFD400"},
                                  {"type": "button", "id": "pcx:confirm:cf-1", "rect_mm": [150, 110, 230, 50],
                                   "label": "Confirmar", "color": "#FFD400"}]})
            };
            send(&mut ws, confirm(7));
            wait_for(&mut ws, "presented 7 (confirmación dibujada)", presented(7));
            // Durante un apagón la mesa está en negro: la confirmación que llega ahí no se da por presentada
            // hasta que vuelve la escena (primero `blanked`, después `presented`).
            send(&mut ws, json!({"type": "blank", "request_id": "req-foto-2", "ms": 300, "reason": "clean_photo"}));
            send(&mut ws, confirm(8));
            let first = wait_for(&mut ws, "blanked o presented 8", |v| v["type"] == "blanked" || presented(8)(v));
            assert_eq!(first["type"], "blanked", "no se presenta una confirmación con la mesa en negro");
            wait_for(&mut ws, "presented 8 tras el apagón", presented(8));
        });
        let _ = done_tx.send(r.map_err(|_| "el core falso falló (ver panic arriba)".to_string()));
    });

    let mut eng = ProtocolRenderer;
    let view = ();
    let mut rt = Runtime::connect(&format!("ws://127.0.0.1:{port}/render"));
    let t0 = Instant::now();
    let mut saw_disconnect_with_scene = false;
    let result = loop {
        assert!(t0.elapsed() < Duration::from_secs(20), "timeout global");
        rt.draw(&mut eng, &view, 640, 360, None);
        if !rt.connected && rt.state.rev == Some(5) {
            // Desconectado: mantiene la última escena.
            assert_eq!(rt.state.entries.len(), 2);
            saw_disconnect_with_scene = true;
        }
        rt.after_present(now_epoch_ms());
        if let Ok(r) = done_rx.try_recv() {
            break r;
        }
        std::thread::sleep(Duration::from_millis(8));
    };
    server.join().unwrap();
    result.unwrap();
    assert_eq!(rt.state.rev, Some(8));
    assert!(saw_disconnect_with_scene, "no se observó la desconexión con la escena retenida");
}
