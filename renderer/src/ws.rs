//! Cliente WebSocket del core, en un hilo propio: parsea `ToRenderer` fuera del hilo de render,
//! envía lo que manda el renderer (`presented`, `stats`, `resync`) y se reconecta con backoff
//! exponencial (100 ms → 2 s). Mientras está desconectado, el renderer mantiene la última escena.

use std::io::ErrorKind;
use std::sync::mpsc::{Receiver, Sender};
use std::time::Duration;

use pcx_contracts::ToRenderer;
use tungstenite::{Message, stream::MaybeTlsStream};

#[derive(Debug)]
pub enum WsEvent {
    Connected,
    Disconnected,
    Msg(ToRenderer),
}

pub fn spawn(url: String, to_render: Sender<WsEvent>, from_render: Receiver<String>) {
    std::thread::Builder::new()
        .name("ws".into())
        .spawn(move || run(url, to_render, from_render))
        .expect("hilo ws");
}

fn run(url: String, to_render: Sender<WsEvent>, from_render: Receiver<String>) {
    let mut backoff = Duration::from_millis(100);
    loop {
        match tungstenite::connect(&url) {
            Ok((mut sock, _)) => {
                backoff = Duration::from_millis(100);
                eprintln!("[ws] conectado a {url}");
                if let MaybeTlsStream::Plain(s) = sock.get_mut() {
                    let _ = s.set_nodelay(true);
                    let _ = s.set_read_timeout(Some(Duration::from_millis(2)));
                }
                // Lo acumulado sin conexión (presented/stats viejos) ya no sirve.
                while from_render.try_recv().is_ok() {}
                if to_render.send(WsEvent::Connected).is_err() {
                    return;
                }
                'conn: loop {
                    while let Ok(m) = from_render.try_recv() {
                        if let Err(e) = sock.send(Message::text(m)) {
                            eprintln!("[ws] error al enviar: {e}");
                            break 'conn;
                        }
                    }
                    match sock.read() {
                        Ok(Message::Text(t)) => match serde_json::from_str::<ToRenderer>(&t) {
                            Ok(msg) => {
                                if to_render.send(WsEvent::Msg(msg)).is_err() {
                                    return;
                                }
                            }
                            Err(e) => eprintln!("[ws] mensaje inválido: {e}"),
                        },
                        Ok(Message::Close(_)) => break 'conn,
                        Ok(_) => {}
                        Err(tungstenite::Error::Io(e))
                            if matches!(e.kind(), ErrorKind::WouldBlock | ErrorKind::TimedOut) => {}
                        Err(e) => {
                            eprintln!("[ws] desconectado: {e}");
                            break 'conn;
                        }
                    }
                }
                if to_render.send(WsEvent::Disconnected).is_err() {
                    return;
                }
            }
            Err(_) => {
                while from_render.try_recv().is_ok() {}
            }
        }
        std::thread::sleep(backoff);
        backoff = (backoff * 2).min(Duration::from_secs(2));
    }
}
