//! Port de `test_links.py` y `test_supervisor.py`: enlace a la nube (gateway WS falso, replay, tool_call,
//! reconexión), bus ZeroMQ (PULL msgpack/JSON + PUB multipart) y supervisor.

use std::sync::atomic::Ordering;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use futures::{SinkExt, StreamExt};
use pcx_core::bus;
use pcx_core::cloud::CloudLink;
use pcx_core::frames::SyntheticFrameSource;
use pcx_core::proto::*;
use pcx_core::store::Store;
use pcx_core::supervisor::{self, ProcSpec, Supervised};
use pcx_core::tools::Tools;
use pcx_core::world::{World, WorldConfig, WorldHandle};
use tokio_tungstenite::tungstenite::Message;
use tokio_util::sync::CancellationToken;
use zeromq::{PushSocket, Socket, SocketRecv, SocketSend, SubSocket, ZmqMessage};

const CLOUD_PORT: u16 = 9205;

fn world() -> WorldHandle {
    World::spawn(WorldConfig { tick_hz: 100.0, ..Default::default() }, Arc::new(Store::open(None).unwrap()))
}

async fn add_object(w: &WorldHandle, oid: &str) {
    let (oid, nid) = (oid.to_string(), w.node_id.clone());
    w.call(move |w| {
        let o = w.make_object(&oid, None, (100.0, 100.0), (80.0, 80.0));
        w.ingest(wrap(Payload::Event(Event::ObjectAppeared { object: o }), 0, "t", &nid, None)).unwrap();
    })
    .await;
}

async fn until(mut pred: impl FnMut() -> bool) {
    for _ in 0..500 {
        if pred() {
            return;
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    panic!("condición no se cumplió a tiempo");
}

type Sessions = Arc<Mutex<Vec<Vec<CloudMessage>>>>;

/// Gateway falso: Welcome con el último cursor que ya recibió; en la 1.ª sesión manda un tool_call y corta.
async fn fake_gateway(port: u16, sessions: Sessions) {
    let l = tokio::net::TcpListener::bind(("127.0.0.1", port)).await.unwrap();
    let durable = Arc::new(Mutex::new(0i64));
    loop {
        let (sock, _) = l.accept().await.unwrap();
        let (sessions, durable) = (sessions.clone(), durable.clone());
        tokio::spawn(async move {
            let mut ws = tokio_tungstenite::accept_async(sock).await.unwrap();
            let n = {
                let mut s = sessions.lock().unwrap();
                s.push(vec![]);
                s.len() - 1
            };
            let push = |m: CloudMessage| sessions.lock().unwrap()[n].push(m);
            let mut sent_call = false;
            while let Some(Ok(m)) = ws.next().await {
                let Message::Text(t) = m else { continue };
                let msg: CloudMessage = serde_json::from_str(&t).unwrap();
                push(msg.clone());
                match msg {
                    CloudMessage::Hello { .. } => {
                        let last = *durable.lock().unwrap();
                        let w = CloudMessage::Welcome {
                            session_id: format!("s{n}"),
                            contract_version: 2,
                            last_stream_id: None,
                            last_cursor: Some(last),
                        };
                        ws.send(Message::Text(serde_json::to_string(&w).unwrap().into())).await.unwrap();
                    }
                    CloudMessage::EventBatch { cursor, .. } => {
                        let mut d = durable.lock().unwrap();
                        *d = (*d).max(cursor);
                    }
                    CloudMessage::ReplayDone { .. } if n == 0 && !sent_call => {
                        sent_call = true;
                        let c = CloudMessage::ToolCall {
                            call_id: "c1".into(),
                            tool: "scene".into(),
                            args: Json::new(),
                            timeout_s: Some(5.0),
                        };
                        ws.send(Message::Text(serde_json::to_string(&c).unwrap().into())).await.unwrap();
                    }
                    CloudMessage::ToolResult { .. } if n == 0 => {
                        let _ = ws.close(None).await; // corte: el nodo debe reconectar
                        break;
                    }
                    _ => {}
                }
            }
        });
    }
}

#[tokio::test]
async fn enlace_nube_snapshot_replay_toolcall_y_reconexion() {
    let w = world();
    add_object(&w, "taza").await; // seq 1, antes de conectar
    let sessions: Sessions = Default::default();
    tokio::spawn(fake_gateway(CLOUD_PORT, sessions.clone()));
    tokio::time::sleep(Duration::from_millis(50)).await;
    let tools = Tools::new(w.clone(), Arc::new(SyntheticFrameSource::new(w.state.clone())));
    let mut link = CloudLink::new(format!("ws://127.0.0.1:{CLOUD_PORT}"), "tok");
    link.backoff_max_s = 0.2;
    let link = Arc::new(link);
    let cancel = CancellationToken::new();
    link.clone().spawn(tools, cancel.clone());
    let s = sessions.clone();
    until(|| s.lock().unwrap().first().is_some_and(|m| m.iter().any(|x| matches!(x, CloudMessage::ToolResult { .. }))))
        .await;
    {
        let s0 = sessions.lock().unwrap()[0].clone();
        let CloudMessage::Hello { token, capabilities, contract_versions, log_last_cursor, stream_id, .. } = &s0[0]
        else {
            panic!("hello primero")
        };
        assert_eq!((token.as_str(), contract_versions.as_slice(), *log_last_cursor), ("tok", &[2][..], 1));
        assert_eq!(capabilities.len(), 10);
        let CloudMessage::StateSnapshot { world, cursor, stream_id: sid, .. } = &s0[1] else { panic!("snapshot") };
        assert_eq!((world.objects[0].id.as_str(), *cursor, sid), ("taza", 1, stream_id));
        let CloudMessage::EventBatch { replay, events, .. } = &s0[2] else { panic!("replay") };
        assert!(*replay && events[0].type_ == "object_appeared" && events[0].node_id == "mesa-1");
        assert!(matches!(&s0[3], CloudMessage::ReplayDone { history_complete: true, cursor: 1, .. }));
        let r = s0
            .iter()
            .find_map(|m| {
                if let CloudMessage::ToolResult { call_id, ok, content, .. } = m {
                    Some((call_id.clone(), *ok, content.clone()))
                } else {
                    None
                }
            })
            .unwrap();
        assert!(r.0 == "c1" && r.1 && matches!(r.2.last(), Some(ContentItem::Text { text }) if text.contains("taza")));
    }
    add_object(&w, "frasco").await; // mientras está desconectado o reconectando
    until(|| s.lock().unwrap().get(1).is_some_and(|m| m.iter().any(|x| matches!(x, CloudMessage::ReplayDone { .. }))))
        .await;
    assert!(link.connects.load(Ordering::Relaxed) >= 2);
    let s1 = sessions.lock().unwrap()[1].clone();
    let CloudMessage::StateSnapshot { cursor, .. } = &s1[1] else { panic!() };
    assert_eq!(*cursor, w.last_seq());
    // el replay de la 2.ª sesión solo trae lo posterior al cursor durable de la nube
    let replayed: Vec<i64> = s1
        .iter()
        .filter_map(|m| {
            if let CloudMessage::EventBatch { events, replay: true, .. } = m {
                Some(events.iter().map(|e| e.seq).collect::<Vec<_>>())
            } else {
                None
            }
        })
        .flatten()
        .collect();
    assert!(replayed.iter().all(|s| *s > 1), "{replayed:?}");
    cancel.cancel();
}

fn endpoints(tag: &str) -> (String, String) {
    let dir = std::env::temp_dir().join(format!("pcx-{tag}-{}", std::process::id()));
    (bus::zmq_endpoint("ingest", 9208, Some(&dir)), bus::zmq_endpoint("events", 9209, Some(&dir)))
}

#[tokio::test]
async fn bus_zmq_ingesta_y_pub() {
    let w = world();
    let (ingest, events) = endpoints("unit");
    let cancel = CancellationToken::new();
    let stats = bus::start(w.clone(), &ingest, &events, cancel.clone()).await.unwrap();
    let mut sub = SubSocket::new();
    sub.connect(&events).await.unwrap();
    sub.subscribe("object_").await.unwrap();
    let mut push = PushSocket::new();
    push.connect(&ingest).await.unwrap();
    tokio::time::sleep(Duration::from_millis(200)).await; // unión lenta de SUB
    let obj = w.call(|w| w.make_object("taza", None, (100.0, 100.0), (80.0, 80.0))).await;
    let env = wrap(Payload::Event(Event::ObjectAppeared { object: obj }), 7, "perception-objects", "mesa-1", None);
    push.send(ZmqMessage::from(encode_msgpack(&env))).await.unwrap();
    push.send(ZmqMessage::from("basura")).await.unwrap();
    let hands = r#"{"type":"hands","hands":[{"id":"h1","camera_id":"cam0","t_capture_ns":1,"pinch":true}]}"#;
    push.send(ZmqMessage::from(hands)).await.unwrap();
    until(|| w.snapshot().objects.iter().any(|o| o.id == "taza")).await;
    let m = tokio::time::timeout(Duration::from_secs(2), sub.recv()).await.unwrap().unwrap();
    assert_eq!(m.get(0).unwrap().as_ref(), b"object_appeared");
    let got = decode_envelope(m.get(1).unwrap()).unwrap();
    assert_eq!((got.source.as_str(), got.seq), ("perception-objects", w.last_seq())); // re-secuenciado
    until(|| stats.errors.load(Ordering::Relaxed) == 1).await;
    until(|| w.snapshot().hands.len() == 1).await;
    // JSON también entra
    let env = wrap(Payload::Event(Event::ObjectRemoved { object_id: "taza".into(), label: None }), 0, "p", "n", None);
    push.send(ZmqMessage::from(serde_json::to_string(&env).unwrap())).await.unwrap();
    until(|| w.snapshot().objects.is_empty()).await;
    cancel.cancel();
}

fn short_cmd() -> Vec<&'static str> {
    if cfg!(windows) {
        vec!["cmd", "/C", "exit 0"]
    } else {
        vec!["sh", "-c", "sleep 0.2"]
    }
}

fn long_cmd() -> Vec<&'static str> {
    if cfg!(windows) {
        vec!["ping", "-n", "60", "127.0.0.1"]
    } else {
        vec!["sleep", "60"]
    }
}

#[tokio::test]
async fn supervisor_reinicia_y_detiene() {
    let dir = tempfile::tempdir().unwrap();
    let s = Supervised::new(ProcSpec::new("corto", &short_cmd(), dir.path()));
    let stop = CancellationToken::new();
    let task = tokio::spawn(s.clone().watch(stop.clone()));
    until(|| s.restarts.load(Ordering::Relaxed) >= 1).await;
    stop.cancel();
    tokio::time::timeout(Duration::from_secs(10), task).await.unwrap().unwrap();
}

#[tokio::test]
async fn supervisor_termina_proceso_largo() {
    let dir = tempfile::tempdir().unwrap();
    let mut child = supervisor::spawn_child(&ProcSpec::new("largo", &long_cmd(), dir.path())).unwrap();
    let t0 = std::time::Instant::now();
    supervisor::terminate(&mut child, Duration::from_secs(3)).await;
    assert!(child.try_wait().unwrap().is_some() && t0.elapsed() < Duration::from_secs(4));
}

#[test]
fn pcx_toml_carga_solo_habilitados() {
    let specs = supervisor::load(&std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../pcx.toml")).unwrap();
    assert_eq!(specs.iter().map(|p| p.name.as_str()).collect::<Vec<_>>(), vec!["core"]);
    assert!(specs[0].health.as_deref().unwrap().ends_with(":9200/health"));
    assert_eq!(specs[0].cmd, vec!["pcx", "serve"]);
}
