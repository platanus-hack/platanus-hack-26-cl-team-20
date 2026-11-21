//! Port de `test_world.py`: actor, anclas, dwell, ask, flujos, events_wait, idempotencia, domótica, blank y tools.

use std::sync::Arc;
use std::time::Duration;

use pcx_contracts::{ToRenderer, Widget};
use pcx_core::frames::SyntheticFrameSource;
use pcx_core::proto::*;
use pcx_core::store::Store;
use pcx_core::tools::Tools;
use pcx_core::world::{World, WorldConfig, WorldHandle};
use serde_json::{json, Value};

fn world_with(policy: HomePolicy) -> WorldHandle {
    let st = Arc::new(Store::open(None).unwrap());
    World::spawn(WorldConfig { tick_hz: 100.0, home_policy: policy, ..Default::default() }, st)
}

fn world() -> WorldHandle {
    world_with(HomePolicy::default())
}

async fn add_object(w: &WorldHandle, oid: &str, center: (f64, f64)) {
    let oid = oid.to_string();
    let nid = w.node_id.clone();
    w.call(move |w| {
        let o = w.make_object(&oid, Some(&oid), center, (80.0, 80.0));
        w.ingest(wrap(Payload::Event(Event::ObjectAppeared { object: o }), 0, "test", &nid, None)).unwrap();
    })
    .await;
}

async fn touch(w: &WorldHandle, tid: &str, phase: TouchPhase, x: f64, y: f64, trigger: pcx_contracts::Trigger) {
    let t = Touch { id: tid.into(), phase, point_mm: pcx_contracts::PointMM(x, y), widget_id: None, object_id: None, trigger };
    let ev = match phase {
        TouchPhase::Down => Event::TouchDown { touch: t },
        TouchPhase::Move => Event::TouchMove { touch: t },
        TouchPhase::Up => Event::TouchUp { touch: t },
    };
    w.ingest(wrap(Payload::Event(ev), 0, "test", "n", None)).await.unwrap();
}

fn events(w: &WorldHandle, ty: &str) -> Vec<Envelope> {
    w.store.after(0, &[ty.to_string()], 100_000).0
}

async fn until(mut pred: impl FnMut() -> bool) {
    for _ in 0..300 {
        if pred() {
            return;
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    panic!("condición no se cumplió a tiempo");
}

fn cmd(v: Value) -> Command {
    serde_json::from_value(v).unwrap()
}

async fn scene_center(w: &WorldHandle, id: &str) -> (f64, f64) {
    let id = id.to_string();
    w.call(move |w| w.scene.get(&id).and_then(pcx_core::geom::center)).await.expect("widget en escena")
}

use pcx_contracts::Trigger::{Api, Dwell, Pinch};

#[tokio::test]
async fn actor_resuelve_anclas_y_manda_diffs() {
    let w = world();
    let (snap, mut rx) = w.subscribe_render().await;
    assert_eq!(snap.rev, 0);
    add_object(&w, "taza", (300.0, 250.0)).await;
    let r = w
        .command(cmd(json!({"type": "project", "request_id": "r1", "widgets": [{"type": "label", "id": "l1", "text": "Hola", "anchor": {"object_id": "taza"}}]})))
        .await
        .unwrap();
    assert_eq!(r, json!({"ids": ["l1"], "rev": 1}));
    let ToRenderer::SceneDiff(d) = &*rx.recv().await.unwrap() else { panic!("diff") };
    assert_eq!((d.base_rev, d.rev), (0, 1));
    let Widget::Label(l) = &d.upsert[0] else { panic!() };
    let p = l.common.anchor.as_ref().unwrap().point_mm.unwrap();
    assert!(p.0 == 300.0 && p.1 < 210.0); // encima del objeto
    w.ingest(wrap(Payload::Event(Event::ObjectMoved { object_id: "taza".into(), center_mm: pcx_contracts::PointMM(500.0, 300.0), polygon_mm: None }), 0, "t", "n", None))
        .await
        .unwrap();
    let ToRenderer::SceneDiff(d2) = &*rx.recv().await.unwrap() else { panic!() };
    let Widget::Label(l) = &d2.upsert[0] else { panic!() };
    assert_eq!((d2.base_rev, l.common.anchor.as_ref().unwrap().point_mm.unwrap().0), (1, 500.0));
    // objeto desconocido → error claro, el actor sigue vivo
    let e = w.command(cmd(json!({"type": "project", "request_id": "r2", "widgets": [{"type": "label", "id": "x", "anchor": {"object_id": "nada"}}]}))).await;
    assert!(e.unwrap_err().contains("objeto desconocido"));
    w.command(cmd(json!({"type": "remove", "request_id": "r3", "ids": ["l1"]}))).await.unwrap();
    let ToRenderer::SceneDiff(d3) = &*rx.recv().await.unwrap() else { panic!() };
    assert_eq!(d3.remove, vec!["l1".to_string()]);
    assert_eq!(w.snapshot().rev, 3);
}

#[tokio::test]
async fn dwell_llena_progress_y_emite_pressed() {
    let w = world();
    let (_, mut rx) = w.subscribe_render().await;
    w.command(cmd(json!({"type": "project", "request_id": "r", "widgets": [{"type": "button", "id": "ok", "rect_mm": [100, 100, 100, 50],
        "label": "OK", "on_press": {"value": "si", "dwell_ms": 300}}]})))
        .await
        .unwrap();
    touch(&w, "t1", TouchPhase::Down, 150.0, 125.0, Dwell).await;
    until(|| !events(&w, "widget_pressed").is_empty()).await;
    let Payload::Event(Event::WidgetPressed { widget_id, value, trigger, .. }) = &events(&w, "widget_pressed")[0].payload else { panic!() };
    assert_eq!((widget_id.as_str(), value.as_deref(), *trigger), ("ok", Some("si"), Dwell));
    let mut progresses = vec![];
    while let Ok(m) = rx.try_recv() {
        if let ToRenderer::SceneDiff(d) = &*m {
            progresses.extend(d.upsert.iter().filter_map(|x| if let Widget::Button(b) = x { Some(b.progress) } else { None }));
        }
    }
    assert!(progresses.iter().any(|p| *p > 0.0 && *p < 1.0), "{progresses:?}");
    let prog = w.call(|w| match w.widgets.get("ok") { Some(Widget::Button(b)) => b.progress, _ => -1.0 }).await;
    assert_eq!(prog, 0.0); // se reinicia tras confirmar
    // salir del botón antes de tiempo no confirma
    touch(&w, "t1", TouchPhase::Up, 150.0, 125.0, Dwell).await;
    let n = events(&w, "widget_pressed").len();
    touch(&w, "t2", TouchPhase::Down, 150.0, 125.0, Dwell).await;
    tokio::time::sleep(Duration::from_millis(100)).await;
    touch(&w, "t2", TouchPhase::Move, 600.0, 400.0, Dwell).await;
    tokio::time::sleep(Duration::from_millis(400)).await;
    assert_eq!(events(&w, "widget_pressed").len(), n);
}

#[tokio::test]
async fn pinch_confirma_al_instante() {
    let w = world();
    w.command(cmd(json!({"type": "project", "request_id": "r", "widgets": [{"type": "button", "id": "b", "rect_mm": [0, 0, 100, 50]}]}))).await.unwrap();
    touch(&w, "p1", TouchPhase::Down, 50.0, 25.0, Pinch).await;
    assert_eq!(events(&w, "widget_pressed").len(), 1);
}

#[tokio::test]
async fn ask_responde_con_toque() {
    let w = world();
    let w2 = w.clone();
    let task = tokio::spawn(async move { w2.command(cmd(json!({"type": "ask", "request_id": "q1", "question": "¿Café o té?", "options": ["Café", "Té"], "timeout_s": 5}))).await });
    until(|| w.snapshot().widgets.iter().any(|x| x.id() == "ask-1-b1")).await;
    let (x, y) = scene_center(&w, "ask-1-b1").await;
    touch(&w, "a", TouchPhase::Down, x, y, Api).await;
    let r = tokio::time::timeout(Duration::from_secs(2), task).await.unwrap().unwrap().unwrap();
    assert_eq!((r["status"].as_str(), r["answer"].as_str(), r["index"].as_i64()), (Some("answered"), Some("Té"), Some(1)));
    assert!(!w.snapshot().widgets.iter().any(|x| x.id().starts_with("ask-1")));
    let Payload::Event(Event::AskAnswered { request_id, answer, timed_out, .. }) = &events(&w, "ask_answered")[0].payload else { panic!() };
    assert_eq!((request_id.as_str(), answer.as_deref(), *timed_out), ("q1", Some("Té"), false));
}

#[tokio::test]
async fn ask_vence() {
    let w = world();
    let r = tokio::time::timeout(Duration::from_secs(3), w.command(cmd(json!({"type": "ask", "request_id": "q", "question": "?", "options": ["a"], "timeout_s": 1}))))
        .await
        .unwrap()
        .unwrap();
    assert_eq!(r["status"], "timeout");
    assert!(w.call(|w| w.widgets.is_empty()).await);
}

#[tokio::test]
async fn flujo_guia_3_pasos() {
    let w = world();
    add_object(&w, "taza", (300.0, 250.0)).await;
    add_object(&w, "frasco", (150.0, 300.0)).await;
    let raw = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/../../../contracts/fixtures/flow_guia_3_pasos.json")).unwrap();
    let c: Command = serde_json::from_str(&raw).unwrap();
    let r = w.command(c).await.unwrap();
    assert_eq!(r["state"], "paso1");
    let ids = |w: &WorldHandle| w.snapshot().widgets.iter().map(|x| x.id().to_string()).collect::<Vec<_>>();
    assert!(["g-halo", "g-panel", "g-next"].iter().all(|i| ids(&w).contains(&i.to_string())));
    async fn press(w: &WorldHandle, n: &str) {
        let (x, y) = scene_center(w, "g-next").await;
        touch(w, n, TouchPhase::Down, x, y, Api).await;
    }
    press(&w, "n1").await;
    let title = |w: &WorldHandle| {
        let s = w.snapshot();
        s.widgets.iter().find_map(|x| if let Widget::Panel(p) = x { Some(p.title.clone()) } else { None })
    };
    assert_eq!(title(&w).as_deref(), Some("Paso 2 de 3"));
    assert!(ids(&w).contains(&"g-arrow".to_string()));
    press(&w, "n2").await;
    assert!(!ids(&w).contains(&"g-arrow".to_string()));
    let calls = w.call(|w| w.home_calls.clone()).await;
    assert_eq!(calls[0]["service"], "turn_on");
    assert_eq!(calls[0]["request_id"], "req-guia-cafe-1/paso3/1"); // request_id derivado
    assert_eq!(calls[0]["decision"], "deny"); // política por defecto: nada permitido
    press(&w, "n3").await;
    assert!(w.snapshot().active_flows.is_empty());
    assert!(!ids(&w).iter().any(|i| i.starts_with("g-"))); // cleanup
    let changes: Vec<(Option<String>, Option<String>)> = events(&w, "flow_state_changed")
        .into_iter()
        .map(|e| match e.payload {
            Payload::Event(Event::FlowStateChanged { from_state, to_state, request_id, .. }) => {
                assert_eq!(request_id, "req-guia-cafe-1");
                (from_state, to_state)
            }
            _ => panic!(),
        })
        .collect();
    let s = |x: &str| Some(x.to_string());
    assert_eq!(changes, vec![(None, s("paso1")), (s("paso1"), s("paso2")), (s("paso2"), s("paso3")), (s("paso3"), s("fin")), (s("fin"), None)]);
    let Payload::Event(Event::FlowEmitted { event, flow_id, .. }) = &events(&w, "flow_emitted")[0].payload else { panic!() };
    assert_eq!((event.as_str(), flow_id.as_deref()), ("guia_terminada", Some("guia-cafe")));
}

#[tokio::test]
async fn flujo_por_tiempo_y_objeto() {
    let w = world();
    add_object(&w, "taza", (300.0, 250.0)).await;
    let flow = json!({"id": "f", "initial": "a", "states": [
        {"id": "a", "transitions": [{"on": {"event": "object_moved", "where": {"object_id": "taza"}}, "to": "b"}]},
        {"id": "b", "transitions": [{"after_s": 0.1, "to": "c"}]},
        {"id": "c", "final": true}]});
    w.command(cmd(json!({"type": "run_flow", "request_id": "rf", "flow": flow}))).await.unwrap();
    w.ingest(wrap(Payload::Event(Event::ObjectMoved { object_id: "taza".into(), center_mm: pcx_contracts::PointMM(310.0, 250.0), polygon_mm: None }), 0, "t", "n", None))
        .await
        .unwrap();
    let last = events(&w, "flow_state_changed").last().cloned().unwrap();
    assert!(matches!(last.payload, Payload::Event(Event::FlowStateChanged { ref to_state, .. }) if to_state.as_deref() == Some("b")));
    until(|| w.snapshot().active_flows.is_empty()).await;
}

#[tokio::test]
async fn events_wait_long_poll_y_filtro() {
    let w = world();
    let cur = w.last_seq();
    let w2 = w.clone();
    let waiter = tokio::spawn(async move { w2.events_wait(cur, vec!["object_appeared".into()], 2.0, 50).await });
    tokio::time::sleep(Duration::from_millis(50)).await;
    assert!(!waiter.is_finished());
    touch(&w, "x", TouchPhase::Down, 5.0, 5.0, Dwell).await; // no pasa el filtro
    add_object(&w, "taza", (300.0, 250.0)).await;
    let r = tokio::time::timeout(Duration::from_secs(1), waiter).await.unwrap().unwrap();
    assert_eq!(r.events.iter().map(|e| e.type_.as_str()).collect::<Vec<_>>(), vec!["object_appeared"]);
    assert_eq!(r.cursor, w.last_seq());
    let r2 = w.events_wait(r.cursor, vec![], 0.1, 50).await;
    assert!(r2.events.is_empty() && r2.cursor == r.cursor);
}

#[tokio::test]
async fn idempotencia_por_request_id() {
    let w = world();
    let c = json!({"type": "project", "request_id": "same", "widgets": [{"type": "label", "id": "a", "text": "x", "anchor": {"point_mm": [10, 10]}}]});
    let r1 = w.command(cmd(c.clone())).await.unwrap();
    w.command(cmd(json!({"type": "remove", "request_id": "rm", "ids": ["a"]}))).await.unwrap();
    let r2 = w.command(cmd(c)).await.unwrap(); // reintento: no re-proyecta
    assert_eq!(r1, r2);
    assert!(w.snapshot().widgets.is_empty());
}

#[tokio::test]
async fn home_confirmacion_fisica() {
    let policy = HomePolicy { allow: vec!["light.*".into(), "lock.*".into()], confirm: vec!["lock.*".into()], ..Default::default() };
    let w = world_with(policy);
    let r = w.command(cmd(json!({"type": "home_call", "request_id": "h1", "domain": "light", "service": "turn_on"}))).await.unwrap();
    assert_eq!(r["status"], "failed"); // permitido, pero no hay Home Assistant (stub)
    let r = w.command(cmd(json!({"type": "home_call", "request_id": "h2", "domain": "lock", "service": "unlock", "data": {"entity_id": "lock.puerta"}}))).await.unwrap();
    assert_eq!(r["status"], "pending");
    let cid = r["confirmation_id"].as_str().unwrap().to_string();
    let bid = format!("pcx:confirm:{cid}");
    let (x, y) = scene_center(&w, &bid).await;
    touch(&w, "api", TouchPhase::Down, x, y, Api).await; // `api` no confirma
    assert!(events(&w, "confirmation_resolved").is_empty());
    touch(&w, "pz", TouchPhase::Down, x, y, Pinch).await;
    let Payload::Event(Event::ConfirmationResolved { approved, via, .. }) = &events(&w, "confirmation_resolved")[0].payload else { panic!() };
    assert!(*approved && *via == ResolvedVia::Touch);
    let done = events(&w, "home_call_done");
    assert!(matches!(&done.last().unwrap().payload, Payload::Event(Event::HomeCallDone { request_id, .. }) if request_id == "h2"));
    assert!(!w.snapshot().widgets.iter().any(|x| x.id() == bid));
    // digest equivocado por consola → rechazado
    let r = w.command(cmd(json!({"type": "home_call", "request_id": "h3", "domain": "lock", "service": "lock"}))).await.unwrap();
    let c = ConfirmAction { confirmation_id: r["confirmation_id"].as_str().unwrap().into(), action_digest: "0".repeat(64), approve: true, operator: "t".into() };
    assert!(w.confirm(c.clone()).await.is_err());
    w.confirm(ConfirmAction { action_digest: r["action_digest"].as_str().unwrap().into(), approve: false, ..c }).await.unwrap();
    assert_eq!(events(&w, "confirmation_resolved").len(), 2);
}

#[tokio::test]
async fn blank_va_al_renderer_y_vuelve_como_evento() {
    let w = world();
    let (_, mut rx) = w.subscribe_render().await;
    w.ingest(wrap(Payload::Command(cmd(json!({"type": "blank", "request_id": "b1", "ms": 300, "reason": "foto"}))), 0, "perception", "n", None)).await.unwrap();
    let ToRenderer::Blank(b) = &*rx.recv().await.unwrap() else { panic!("blank") };
    assert_eq!((b.request_id.as_str(), b.ms), ("b1", 300));
    w.post(|w| w.on_blanked(pcx_contracts::Blanked { request_id: "b1".into(), t_start_ms: 1.0, t_end_ms: 301.0 }));
    until(|| !events(&w, "blanked").is_empty()).await;
}

#[tokio::test]
async fn tools_scene_look_calibrate_home() {
    let w = world();
    let tools = Tools::new(w.clone(), Arc::new(SyntheticFrameSource::new(w.state.clone())));
    add_object(&w, "taza", (300.0, 250.0)).await;
    let out = tools.call("scene", json!({}), "test", None).await;
    let v: Value = serde_json::from_str(&out.text).unwrap();
    assert_eq!(v["objects"][0]["id"], "taza");
    let look = tools.call("look", json!({}), "test", None).await;
    assert!(look.image.as_ref().unwrap().0.starts_with(b"\x89PNG") && look.text.contains("taza"));
    let cal = tools.call("calibrate", json!({"request_id": "c1", "mode": "quick"}), "test", None).await;
    assert!(!cal.is_error && cal.text.contains("encolada"));
    let home = tools.call("home", json!({"request_id": "h", "domain": "light", "service": "turn_on"}), "test", None).await;
    assert_eq!(home.structured.unwrap()["status"], "denied");
    assert!(tools.call("project", json!({"request_id": "p", "widgets": [{"type": "nope", "id": "x"}]}), "test", None).await.is_error);
    assert!(tools.call("project", json!({"widgets": []}), "test", None).await.is_error); // falta request_id
    let bad = tools.call("run_flow", json!({"request_id": "f", "flow": {"id": "f", "initial": "zzz", "states": [{"id": "a"}]}}), "test", None).await;
    assert!(bad.is_error && bad.text.contains("initial"));
    assert!(tools.call("project", json!({"request_id": "p2", "widgets": [{"type": "label", "id": "pcx:x", "anchor": {"point_mm": [1, 1]}}]}), "test", None).await.is_error);
    assert!(w.store.audit_tail(100).len() >= 7);
}
