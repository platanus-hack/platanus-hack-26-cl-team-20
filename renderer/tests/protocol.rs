//! Protocolo contra un core falso (servidor WS en Rust, puerto 9231): snapshot → presented,
//! diff → presented, hueco de rev → resync, stats cada 1 s, reconexión manteniendo la escena.

use std::net::TcpListener;
use std::sync::mpsc;
use std::time::{Duration, Instant};

use pcx_renderer::engine::{Engine, EngineConfig};
use pcx_renderer::runtime::{Runtime, now_epoch_ms};
use serde_json::{Value, json};
use tungstenite::{Message, WebSocket};

const PORT: u16 = 9231;

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
            Err(tungstenite::Error::Io(e)) if matches!(e.kind(), std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut) => {}
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
    let listener = TcpListener::bind(("127.0.0.1", PORT)).unwrap();
    let (done_tx, done_rx) = mpsc::channel::<Result<(), String>>();
    let server = std::thread::spawn(move || {
        let r = std::panic::catch_unwind(move || {
            let mut ws = accept(&listener);
            send(&mut ws, scene(1));
            wait_for(&mut ws, "presented 1", presented(1));
            send(&mut ws, diff(2, 1));
            wait_for(&mut ws, "presented 2", presented(2));
            // Falta el rev 3: el renderer debe pedir snapshot y no aplicar el diff.
            send(&mut ws, diff(4, 3));
            let r = wait_for(&mut ws, "resync", |v| v["type"] == "resync");
            assert_eq!(r["have_rev"], 2);
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
        });
        let _ = done_tx.send(r.map_err(|_| "el core falso falló (ver panic arriba)".to_string()));
    });

    let mut eng = Engine::headless(EngineConfig::default());
    let target = eng.create_target(640, 360);
    let view = target.create_view(&Default::default());
    let mut rt = Runtime::connect(&format!("ws://127.0.0.1:{PORT}/render"));
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
        eng.wait_idle();
        rt.after_present(now_epoch_ms());
        if let Ok(r) = done_rx.try_recv() {
            break r;
        }
        std::thread::sleep(Duration::from_millis(8));
    };
    server.join().unwrap();
    result.unwrap();
    assert_eq!(rt.state.rev, Some(6));
    assert!(saw_disconnect_with_scene, "no se observó la desconexión con la escena retenida");
}
