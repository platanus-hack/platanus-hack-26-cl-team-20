//! Servidor WS mínimo (core falso) para el renderer: una conexión, `scene` completos y espera de `presented`.

use std::net::{TcpListener, TcpStream};
use std::time::{Duration, Instant};

use serde_json::{json, Value};
use tungstenite::{Message, WebSocket};

pub fn now_ns() -> i64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos() as i64
}
pub fn now_ms() -> f64 {
    now_ns() as f64 / 1e6
}

/// Superficie 768×432 mm con escala 2,5 → 1920×1080 px del T749, 1:1 con el lienzo (2,5 px/mm).
pub const SURF_W: f64 = 768.0;
pub const SURF_H: f64 = 432.0;
pub const PX_PER_MM: f64 = 2.5;

pub struct Ws {
    pub sock: WebSocket<TcpStream>,
    pub rev: i64,
    pub last_presented: Option<(i64, f64)>,
}

impl Ws {
    pub fn accept(port: u16) -> Self {
        assert!((9240..=9249).contains(&port), "puerto fuera de 9240–9249");
        let l = TcpListener::bind(("127.0.0.1", port)).expect("bind");
        eprintln!("[ws] esperando al renderer en ws://127.0.0.1:{port}/render");
        let (s, _) = l.accept().expect("accept");
        s.set_nodelay(true).ok();
        let sock = tungstenite::accept(s).expect("handshake");
        sock.get_ref().set_read_timeout(Some(Duration::from_millis(2))).ok();
        eprintln!("[ws] renderer conectado");
        Self { sock, rev: 0, last_presented: None }
    }

    pub fn scene(&mut self, widgets: Value) -> i64 {
        self.rev += 1;
        let m = json!({"type": "scene", "rev": self.rev, "t_send_ms": now_ms(),
            "surface": {"width_mm": SURF_W, "height_mm": SURF_H},
            "transforms": [{"from_frame": "table_mm", "to_frame": "proj:main", "kind": "homography",
                            "matrix": [PX_PER_MM, 0, 0, 0, PX_PER_MM, 0, 0, 0, 1], "version": 1}],
            "widgets": widgets});
        self.sock.send(Message::text(m.to_string())).expect("ws send");
        self.rev
    }

    /// Lee lo pendiente sin bloquear (presented/stats).
    pub fn drain(&mut self) {
        while let Ok(m) = self.sock.read() {
            if let Message::Text(t) = m {
                if let Ok(v) = serde_json::from_str::<Value>(&t) {
                    if v["type"] == "presented" {
                        self.last_presented = Some((v["rev"].as_i64().unwrap_or(0), v["t_present_ms"].as_f64().unwrap_or(0.0)));
                    }
                }
            }
        }
    }

    /// Espera `presented{rev}`; devuelve t_present_ms (o el envío si no llega en `timeout`).
    pub fn wait_presented(&mut self, rev: i64, timeout: Duration) -> f64 {
        let t0 = Instant::now();
        while t0.elapsed() < timeout {
            self.drain();
            if let Some((r, t)) = self.last_presented {
                if r >= rev {
                    return t;
                }
            }
            std::thread::sleep(Duration::from_millis(1));
        }
        eprintln!("[ws] sin presented de rev {rev}");
        now_ms()
    }

    pub fn image_full(&mut self, path: &str) -> f64 {
        let rev = self.scene(json!([{"type": "image", "id": "full", "rect_mm": [0, 0, SURF_W, SURF_H], "src": path, "opacity": 1}]));
        self.wait_presented(rev, Duration::from_secs(3))
    }
}
