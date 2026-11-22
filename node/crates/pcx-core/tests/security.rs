use axum::{
    body::Body,
    http::{Request, StatusCode},
};
use pcx_contracts::{PointMM, Trigger};
use pcx_core::{
    proto::*,
    store::Store,
    world::{World, WorldConfig, WorldHandle},
};
use pcx_core::{Core, Settings};
use serde_json::{json, Value};
use std::{sync::Arc, time::Duration};
use tower::ServiceExt;

fn cmd(v: Value) -> Command {
    serde_json::from_value(v).unwrap()
}
fn world() -> WorldHandle {
    World::spawn(
        WorldConfig {
            home_policy: HomePolicy {
                allow: vec!["lock.unlock".into(), "light.turn_on".into()],
                confirm: vec!["*".into()],
                ..Default::default()
            },
            ..Default::default()
        },
        Arc::new(Store::open(None).unwrap()),
    )
}
async fn pending(w: &WorldHandle) -> (String, PointMM) {
    let r = w
        .command(cmd(json!({"type":"home_call","request_id":"home","domain":"lock","service":"unlock"})))
        .await
        .unwrap();
    let id = format!("pcx:confirm:{}", r["confirmation_id"].as_str().unwrap());
    let point = w
        .call({
            let id = id.clone();
            move |w| {
                let (x, y) = pcx_core::geom::center(&w.scene[&id]).unwrap();
                PointMM(x, y)
            }
        })
        .await;
    (id, point)
}
/// `physical` = llega por el canal autenticado del worker de manos; si no, es un sobre que solo DICE ser `hands`.
async fn touch_phase(w: &WorldHandle, id: &str, phase: TouchPhase, point: PointMM, trigger: Trigger, physical: bool) {
    let touch = Touch { id: id.into(), phase, point_mm: point, widget_id: None, object_id: None, trigger };
    let ev = match phase {
        TouchPhase::Down => Event::TouchDown { touch },
        TouchPhase::Move => Event::TouchMove { touch },
        TouchPhase::Up => Event::TouchUp { touch },
    };
    let env = wrap(Payload::Event(ev), 0, "hands", "n", None);
    if physical {
        w.ingest_hands(env).await.unwrap()
    } else {
        w.ingest(env).await.unwrap()
    };
}
async fn touch(w: &WorldHandle, id: &str, point: PointMM, trigger: Trigger, physical: bool) {
    touch_phase(w, id, TouchPhase::Down, point, trigger, physical).await;
}
/// El renderer avisa que la escena actual ya está en la mesa; luego pasa el margen de gracia.
async fn present(w: &WorldHandle) {
    w.call(|w| w.on_presented(w.rev, 0.0)).await;
    tokio::time::sleep(Duration::from_millis(550)).await;
}
const HOLD: Duration = Duration::from_millis(1700);
/// `(request_id, action_digest)` de cada confirmación APROBADA por toque (no rechazos ni timeouts).
fn approved(w: &WorldHandle) -> Vec<(String, String)> {
    let evs = w.store.after(0, &["confirmation_resolved".into()], 50).0;
    evs.into_iter()
        .filter_map(|e| match e.payload {
            Payload::Event(Event::ConfirmationResolved {
                request_id,
                action_digest,
                approved: true,
                via: ResolvedVia::Touch,
                ..
            }) => Some((request_id, action_digest)),
            _ => None,
        })
        .collect()
}
fn resolved(w: &WorldHandle) -> bool {
    !approved(w).is_empty()
}
fn any_resolution(w: &WorldHandle) -> bool {
    !w.store.after(0, &["confirmation_resolved".into()], 10).0.is_empty()
}
fn panel_body(w: &WorldHandle) -> String {
    let bodies: Vec<String> = w
        .snapshot()
        .widgets
        .iter()
        .filter_map(|x| match x {
            pcx_contracts::Widget::Panel(p) if p.common.id.starts_with("pcx:confirm-q:") => Some(p.body.clone()),
            _ => None,
        })
        .collect();
    assert_eq!(bodies.len(), 1, "debe haber exactamente una confirmación proyectada");
    bodies[0].clone()
}
async fn home(w: &WorldHandle, rid: &str, domain: &str, service: &str, data: Value) -> Value {
    w.command(cmd(json!({"type":"home_call","request_id":rid,"domain":domain,"service":service,"data":data})))
        .await
        .unwrap()
}

#[tokio::test]
async fn debug_is_opt_in_and_roles_are_separate() {
    for debug in [false, true] {
        let c = Core::start(
            Settings {
                data_dir: None,
                zmq_enabled: false,
                debug,
                token: Some("agent".into()),
                console_token: Some("console".into()),
                debug_token: Some("debug".into()),
                ..Default::default()
            },
            None,
        )
        .await
        .unwrap();
        for (path, token, expected) in [
            ("/debug/object", "agent", if debug { StatusCode::UNAUTHORIZED } else { StatusCode::NOT_FOUND }),
            ("/console/confirm", "agent", StatusCode::UNAUTHORIZED),
            ("/state", "debug", StatusCode::UNAUTHORIZED),
            ("/state", "agent", StatusCode::OK),
            ("/mcp", "", StatusCode::UNAUTHORIZED),
        ] {
            let r = c
                .router()
                .oneshot(
                    Request::builder()
                        .uri(path)
                        .header("Host", "127.0.0.1:9200")
                        .header("Authorization", format!("Bearer {token}"))
                        .body(Body::empty())
                        .unwrap(),
                )
                .await
                .unwrap();
            assert_eq!(r.status(), expected, "{path}");
        }
        let r = c
            .router()
            .oneshot(
                Request::builder()
                    .uri("/render")
                    .header("Host", "localhost:9200")
                    .header("Origin", "https://evil.example")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(r.status(), StatusCode::FORBIDDEN);
        if debug {
            let response = c
                .router()
                .oneshot(
                    Request::builder()
                        .method("POST")
                        .uri("/debug/touch")
                        .header("Host", "[::1]:9200")
                        .header("Authorization", "Bearer debug")
                        .header("Content-Type", "application/json")
                        .body(Body::from(r#"{"x_mm":0,"y_mm":0}"#))
                        .unwrap(),
                )
                .await
                .unwrap();
            assert_eq!(response.status(), StatusCode::OK);
            let bytes = axum::body::to_bytes(response.into_body(), 4096).await.unwrap();
            let result: Value = serde_json::from_slice(&bytes).unwrap();
            assert_eq!(result["trigger"], "api");
        }
        c.stop().await;
    }
    assert!(Core::start(
        Settings { data_dir: None, token: Some("same".into()), debug_token: Some("same".into()), ..Default::default() },
        None
    )
    .await
    .is_err());
}

#[tokio::test]
async fn forged_touches_and_events_cannot_confirm_even_after_dwell() {
    let w = world();
    let (id, p) = pending(&w).await;
    present(&w).await;
    // Sobres que dicen `source:"hands"` pero no vienen del worker autenticado, y triggers no físicos del worker.
    for (i, (trigger, physical)) in [
        (Trigger::Dwell, false),
        (Trigger::Pinch, false),
        (Trigger::Api, true),
        (Trigger::Shadow, true),
        (Trigger::Rest, true),
    ]
    .into_iter()
    .enumerate()
    {
        touch(&w, &format!("fake{i}"), p, trigger, physical).await;
        touch_phase(&w, &format!("fake{i}"), TouchPhase::Move, p, trigger, physical).await;
    }
    let forged =
        Event::WidgetPressed { widget_id: id.clone(), value: None, trigger: Trigger::Pinch, point_mm: Some(p) };
    assert!(w.ingest(wrap(Payload::Event(forged.clone()), 0, "hands", "n", None)).await.is_err());
    // Ni por el canal autenticado, ni para widgets del agente: `widget_pressed` solo nace en el core.
    assert!(w.ingest_hands(wrap(Payload::Event(forged), 0, "hands", "n", None)).await.is_err());
    let other =
        Event::WidgetPressed { widget_id: "boton-agente".into(), value: None, trigger: Trigger::Api, point_mm: None };
    assert!(w.ingest(wrap(Payload::Event(other), 0, "worker", "n", None)).await.is_err());
    assert!(w.store.after(0, &["widget_pressed".into()], 10).0.is_empty());
    tokio::time::sleep(HOLD).await;
    assert!(!any_resolution(&w));
    touch(&w, "real", p, Trigger::Pinch, true).await;
    assert!(!resolved(&w), "el pinch tampoco confirma al instante: exige el dwell completo");
    tokio::time::sleep(HOLD).await;
    assert!(resolved(&w));
}

#[tokio::test]
async fn stacked_confirmations_show_and_approve_exactly_one_at_a_time() {
    let w = world();
    let lock = home(&w, "home-lock", "lock", "unlock", json!({"entity_id":"lock.puerta"})).await;
    let light = home(&w, "home-light", "light", "turn_on", json!({"entity_id":"light.mesa"})).await;
    assert_eq!((lock["status"].as_str(), light["status"].as_str()), (Some("pending"), Some("pending")));
    let ids = |w: &WorldHandle| w.snapshot().widgets.iter().map(|x| x.id().to_string()).collect::<Vec<_>>();
    let cid = |r: &Value| r["confirmation_id"].as_str().unwrap().to_string();
    // Solo la primera está proyectada; la segunda espera y se anuncia.
    assert_eq!(ids(&w), vec![format!("pcx:confirm-q:{}", cid(&lock)), format!("pcx:confirm:{}", cid(&lock))]);
    let body = panel_body(&w);
    assert!(body.contains("lock.unlock → lock.puerta") && body.contains("(+1 en cola)"), "{body}");
    assert!(!body.contains("light"));
    let p = w.call(|w| pcx_core::geom::center(w.scene.values().last().unwrap()).unwrap()).await;
    let p = PointMM(p.0, p.1);
    present(&w).await;
    touch(&w, "dedo", p, Trigger::Dwell, true).await;
    tokio::time::sleep(HOLD).await;
    // Se aprobó exactamente lo proyectado: mismo request y mismo digest.
    assert_eq!(approved(&w), vec![("home-lock".to_string(), lock["action_digest"].as_str().unwrap().to_string())]);
    // Ahora se proyecta la segunda; el dedo que sigue apoyado no la aprueba, ni siquiera ya presentada.
    assert!(panel_body(&w).contains("light.turn_on → light.mesa"));
    assert_eq!(ids(&w), vec![format!("pcx:confirm-q:{}", cid(&light)), format!("pcx:confirm:{}", cid(&light))]);
    present(&w).await;
    for _ in 0..4 {
        touch_phase(&w, "dedo", TouchPhase::Move, p, Trigger::Dwell, true).await;
        touch(&w, "dedo", p, Trigger::Dwell, true).await; // un `down` repetido del mismo contacto no es nuevo
        tokio::time::sleep(Duration::from_millis(450)).await;
    }
    assert_eq!(approved(&w).len(), 1);
    touch_phase(&w, "dedo", TouchPhase::Up, p, Trigger::Dwell, true).await;
    touch(&w, "dedo2", p, Trigger::Dwell, true).await;
    tokio::time::sleep(HOLD).await;
    assert_eq!(approved(&w)[1], ("home-light".to_string(), light["action_digest"].as_str().unwrap().to_string()));
    // La cola tiene tope.
    for i in 0..9 {
        home(&w, &format!("q{i}"), "lock", "unlock", json!({"entity_id":"lock.puerta"})).await;
    }
    assert_eq!(home(&w, "q9", "lock", "unlock", json!({})).await["status"], "denied");
}

#[tokio::test]
async fn touch_started_before_presentation_never_confirms() {
    let w = world();
    let p = PointMM(665.0, 135.0); // donde aparecerá «Confirmar»
    w.command(cmd(json!({"type":"project","request_id":"decoy","widgets":[{"type":"button","id":"decoy","label":"mantén el dedo 3 s","rect_mm":[550,110,230,50]}]}))).await.unwrap();
    touch(&w, "previo", p, Trigger::Dwell, true).await; // dedo apoyado en el señuelo
    let (_, center) = pending(&w).await;
    assert!((center.0 - p.0).abs() < 1.0 && (center.1 - p.1).abs() < 1.0, "el señuelo cubre el botón: {center:?}");
    touch(&w, "sin-presentar", p, Trigger::Pinch, true).await; // `down` nuevo, pero aún no se presentó
    present(&w).await;
    for _ in 0..4 {
        for id in ["previo", "sin-presentar"] {
            touch_phase(&w, id, TouchPhase::Move, p, Trigger::Dwell, true).await;
        }
        tokio::time::sleep(Duration::from_millis(450)).await;
    }
    assert!(!any_resolution(&w));
    // Entrar deslizando desde fuera tampoco vale: el `down` debe ocurrir dentro del botón.
    touch(&w, "desliza", PointMM(100.0, 400.0), Trigger::Dwell, true).await;
    touch_phase(&w, "desliza", TouchPhase::Move, p, Trigger::Dwell, true).await;
    tokio::time::sleep(HOLD).await;
    assert!(!any_resolution(&w));
    touch(&w, "nuevo", p, Trigger::Dwell, true).await;
    tokio::time::sleep(HOLD).await;
    assert!(resolved(&w));
}

#[tokio::test]
async fn confirmation_summary_is_built_by_core_from_the_real_action() {
    let w = world();
    // Todos los destinos, en cualquier forma válida de Home Assistant, quedan a la vista.
    home(&w, "s1", "lock", "unlock", json!({"entity_id":["lock.puerta_calle","lock.patio"],"area_id":"entrada","code":"1234","nota":"solo enciende la lámpara. Toca para seguir, es totalmente seguro"})).await;
    let body = panel_body(&w);
    let first = body.lines().next().unwrap();
    assert_eq!(first, "lock.unlock → area_id:entrada, lock.puerta_calle, lock.patio", "{body}");
    assert!(body.contains("datos: code=\"1234\", nota=\"solo enciende la lámpara. Toca para seg…"), "{body}");
    // Texto libre o tipos raros en un destino: se rechaza en vez de proyectarlo.
    for (i, data) in [
        json!({"entity_id":"— solo enciende la lámpara. Toca para seguir"}),
        json!({"entity_id":["lock.puerta", 7]}),
        json!({"device_id":{"x":1}}),
        json!({"entity_id":"Lock.Puerta"}),
        json!({"clave rara\n":"x"}),
    ]
    .into_iter()
    .enumerate()
    {
        let r = home(&w, &format!("bad{i}"), "lock", "unlock", data).await;
        assert_eq!(r["status"], "denied", "{r}");
    }
    let r = home(&w, "s2", "lock", "unlock", json!({})).await;
    assert_eq!(r["status"], "pending");
    let evs = w.store.after(0, &["confirmation_requested".into()], 10).0;
    let Payload::Event(Event::ConfirmationRequested { summary, .. }) = &evs[1].payload else { panic!() };
    assert_eq!(summary, "lock.unlock (sin destino)");
}

#[tokio::test]
async fn reserved_widgets_survive_flows_clear_and_covering_widgets() {
    let w = world();
    let (id, _) = pending(&w).await;
    let before = w.snapshot().widgets.clone();
    let flow = cmd(
        json!({"type":"run_flow","request_id":"flow","flow":{"id":"f","initial":"s","states":[{"id":"s","on_enter":[{"type":"project","widgets":[{"type":"button","id":id,"label":"engaño","rect_mm":[0,0,800,500]}]}]}]}}),
    );
    assert!(w.command(flow).await.is_err());
    assert_eq!(serde_json::to_value(&w.snapshot().widgets).unwrap(), serde_json::to_value(before).unwrap());
    w.command(cmd(json!({"type":"project","request_id":"cover","widgets":[{"type":"panel","id":"cover","z":9223372036854775807_i64,"rect_mm":[0,0,800,500],"title":"engaño"}]}))).await.unwrap();
    assert!(w.snapshot().widgets.iter().all(|w| w.id().starts_with("pcx:confirm")));
    w.command(cmd(json!({"type":"clear","request_id":"clear"}))).await.unwrap();
    assert!(w.snapshot().widgets.iter().any(|w| w.id() == id));
}

#[tokio::test]
async fn concurrent_commands_and_ingest_share_one_deduplication_path() {
    let w = world();
    let c = cmd(json!({"type":"emit","request_id":"same","event":"once"}));
    let (a, b, d) = tokio::join!(
        w.command(c.clone()),
        w.command(c.clone()),
        w.ingest(wrap(Payload::Command(c), 0, "worker", "n", None))
    );
    assert_eq!(a.unwrap(), b.unwrap());
    assert!(d.is_ok());
    assert_eq!(w.store.after(0, &["flow_emitted".into()], 10).0.len(), 1);
    let c = cmd(json!({"type":"ask","request_id":"ask","question":"?","options":["ok"],"timeout_s":1}));
    let (a, b) = tokio::join!(w.command(c.clone()), w.command(c));
    assert_eq!(a.unwrap(), b.unwrap());
    assert_eq!(w.store.after(0, &["ask_answered".into()], 10).0.len(), 1);
}

#[tokio::test]
async fn rejects_extreme_ttl_and_images_outside_assets() {
    let outside = tempfile::tempdir().unwrap();
    std::fs::write(outside.path().join("secreto.png"), b"x").unwrap();
    let assets = tempfile::tempdir().unwrap();
    std::fs::write(assets.path().join("logo.png"), b"x").unwrap();
    #[cfg(unix)]
    std::os::unix::fs::symlink(outside.path().join("secreto.png"), assets.path().join("enlace.png")).unwrap();
    let inside = assets.path().canonicalize().unwrap().join("logo.png");
    let with_assets = World::spawn(
        WorldConfig { assets_dir: Some(assets.path().into()), ..Default::default() },
        Arc::new(Store::open(None).unwrap()),
    );
    let plain = world();
    let image = |src: &str, n: usize| {
        cmd(
            json!({"type":"project","request_id":format!("img{n}"),"widgets":[{"type":"image","id":format!("i{n}"),"src":src,"rect_mm":[0,0,10,10]}]}),
        )
    };
    assert!(plain
        .command(cmd(json!({"type":"project","request_id":"ttl","widgets":[{"type":"label","id":"x","ttl_s":1e20}]})))
        .await
        .is_err());
    let secret = outside.path().join("secreto.png");
    let bad = [
        "https://evil.example/p.png",
        "/etc/passwd",
        "../../../../etc/hosts",
        "asset:../x",
        "asset:/etc/passwd",
        "~/.ssh/id_rsa",
        "file:///etc/passwd",
        "data:image/svg+xml,<svg/>",
        "data:image/svg+xml;base64,AAAA",
        "data:text/html;base64,AAAA",
        "C:\\Users\\x\\a.png",
        secret.to_str().unwrap(),
        #[cfg(unix)]
        "enlace.png",
        #[cfg(unix)]
        "asset:enlace",
    ];
    for (n, src) in bad.iter().enumerate() {
        assert!(with_assets.command(image(src, n)).await.is_err(), "debió rechazar {src}");
    }
    for (n, src) in bad.iter().enumerate().filter(|(_, s)| !s.contains("enlace")) {
        assert!(plain.command(image(src, n)).await.is_err(), "sin assets debió rechazar {src}");
    }
    let good =
        ["asset:logo", "asset:iconos/casa.png", "logo.png", "data:image/png;base64,AAAA", inside.to_str().unwrap()];
    for (n, src) in good.iter().enumerate() {
        with_assets.command(image(src, 100 + n)).await.unwrap_or_else(|e| panic!("debió aceptar {src}: {e}"));
    }
    assert!(plain.command(image(inside.to_str().unwrap(), 200)).await.is_err(), "absoluta sin PCX_ASSETS_DIR");
    plain.command(image("asset:logo", 201)).await.unwrap();
    assert!(plain
        .command(cmd(json!({"type":"update","request_id":"remote","id":"i201","set":{"src":"/etc/passwd"}})))
        .await
        .is_err());
    assert_eq!(plain.snapshot().widgets.len(), 1);
}

#[tokio::test]
async fn sqlite_wait_does_not_block_tokio_timers_and_queue_is_bounded() {
    let w = world();
    let thread = w.call(|_| std::thread::current().name().unwrap().to_string()).await;
    assert_eq!(thread, "pcx-world-sqlite");
    for _ in 0..1000 {
        w.post(|_| std::thread::sleep(Duration::from_millis(1)));
    }
    tokio::time::timeout(Duration::from_millis(100), tokio::time::sleep(Duration::from_millis(5))).await.unwrap();
}

#[test]
fn actor_panic_child() {
    if std::env::var_os("PCX_PANIC_CHILD").is_none() {
        return;
    }
    tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap().block_on(async {
        world().call(|_| panic!("injected actor panic")).await;
    });
}

#[test]
fn actor_panic_fails_process_instead_of_leaving_a_live_zombie() {
    let status = std::process::Command::new(std::env::current_exe().unwrap())
        .args(["actor_panic_child", "--exact"])
        .env("PCX_PANIC_CHILD", "1")
        .status()
        .unwrap();
    assert_eq!(status.code(), Some(70));
}

#[tokio::test]
async fn console_message_cannot_claim_touch_provenance() {
    let w = world();
    let _ = pending(&w).await;
    let r = w
        .command(cmd(json!({"type":"home_call","request_id":"other-home","domain":"lock","service":"unlock"})))
        .await
        .unwrap();
    let action = ConfirmAction {
        confirmation_id: r["confirmation_id"].as_str().unwrap().into(),
        approved: true,
        by: ConfirmVia::Touch,
        action_digest: r["action_digest"].as_str().unwrap().into(),
        operator: None,
    };
    assert!(w.confirm(action.clone()).await.is_err());
    assert!(!any_resolution(&w));
    w.confirm(ConfirmAction { by: ConfirmVia::Console, ..action }).await.unwrap();
    assert!(any_resolution(&w));
}

#[tokio::test]
async fn core_cannot_start_twice_on_one_data_directory() {
    let dir = tempfile::tempdir().unwrap();
    let settings = Settings { data_dir: Some(dir.path().into()), zmq_enabled: false, ..Default::default() };
    let core = Core::start(settings.clone(), None).await.unwrap();
    assert!(Core::start(settings, None).await.is_err());
    assert_eq!(core.world.call(|w| w.node_id.clone()).await, "mesa-1");
    core.stop().await;
}

#[tokio::test]
async fn physical_dwell_is_accepted_and_release_cancels_it() {
    let w = world();
    let (_, p) = pending(&w).await;
    present(&w).await;
    touch(&w, "real", p, Trigger::Dwell, true).await;
    let up = Touch {
        id: "real".into(),
        phase: TouchPhase::Up,
        point_mm: p,
        widget_id: None,
        object_id: None,
        trigger: Trigger::Pinch,
    };
    w.ingest_hands(wrap(Payload::Event(Event::TouchUp { touch: up }), 0, "hands", "n", None)).await.unwrap();
    tokio::time::sleep(HOLD).await;
    assert!(!any_resolution(&w));
    touch(&w, "real2", p, Trigger::Dwell, true).await;
    tokio::time::sleep(HOLD).await;
    assert!(resolved(&w));
}

#[tokio::test]
async fn invalid_future_flow_actions_are_rejected_before_effects() {
    let w = world();
    for (i, action) in [
        json!({"type":"project","widgets":[{"type":"label","id":"pcx:confirm:fake"}]}),
        json!({"type":"project","widgets":[{"type":"image","id":"remote","src":"https://evil.example/a","rect_mm":[0,0,20,20]}]}),
        json!({"type":"remove","ids":["pcx:confirm:fake"]}),
        json!({"type":"update","id":"pcx:confirm:fake","set":{"label":"fake"}}),
    ].into_iter().enumerate() {
        let flow = cmd(json!({"type":"run_flow","request_id":format!("future-{i}"),"flow":{
            "id":"future","initial":"start","states":[
                {"id":"start","on_enter":[{"type":"emit","event":"must-not-run"}],
                 "transitions":[{"to":"later","after_s":1,"actions":[action]}]},
                {"id":"later"}
            ]
        }}));
        assert!(w.command(flow).await.is_err());
    }
    assert!(w.snapshot().active_flows.is_empty());
    assert!(w.store.after(0, &["flow_emitted".into()], 10).0.is_empty());
}

#[tokio::test]
async fn host_is_validated_and_origin_uses_an_exact_allowlist() {
    let c = Core::start(
        Settings {
            data_dir: None,
            zmq_enabled: false,
            token: Some("agent".into()),
            console_token: Some("console".into()),
            allowed_origins: vec!["http://localhost:5173".into()],
            ..Default::default()
        },
        None,
    )
    .await
    .unwrap();
    let send = |method: &str, path: &str, host: Option<&str>, origin: Option<&str>, token: &str| {
        let mut b = Request::builder().method(method).uri(path).header("Authorization", format!("Bearer {token}"));
        if let Some(h) = host {
            b = b.header("Host", h);
        }
        if let Some(o) = origin {
            b = b.header("Origin", o);
        }
        if method == "OPTIONS" {
            b = b.header("Access-Control-Request-Method", "POST");
        }
        c.router().oneshot(b.body(Body::empty()).unwrap())
    };
    // DNS rebinding: el GET llega sin Origin pero con el Host del atacante → 421, también en /health.
    for host in [
        Some("evil.example:9200"),
        Some("127.0.0.1:9999"),
        Some("127.0.0.1.evil.example:9200"),
        Some("localhost"),
        None,
    ] {
        for path in ["/health", "/state"] {
            assert_eq!(
                send("GET", path, host, None, "agent").await.unwrap().status(),
                StatusCode::MISDIRECTED_REQUEST,
                "{host:?}"
            );
        }
    }
    for host in ["127.0.0.1:9200", "localhost:9200", "LOCALHOST:9200", "[::1]:9200"] {
        assert_eq!(send("GET", "/health", Some(host), None, "").await.unwrap().status(), StatusCode::OK, "{host}");
        assert_eq!(send("GET", "/state", Some(host), None, "agent").await.unwrap().status(), StatusCode::OK);
    }
    // Clientes web legítimos: mismo origen y lista blanca pasan (con CORS); el resto, 403.
    let h = Some("127.0.0.1:9200");
    for (origin, ok) in [
        ("http://127.0.0.1:9200", true),
        ("http://localhost:5173", true),
        ("http://localhost:9200", false), // otro origen que el Host usado y fuera de la lista
        ("https://evil.example", false),
        ("http://localhost:5173.evil.example", false),
        ("null", false),
    ] {
        let r = send("GET", "/state", h, Some(origin), "agent").await.unwrap();
        assert_eq!(r.status(), if ok { StatusCode::OK } else { StatusCode::FORBIDDEN }, "{origin}");
        if ok {
            assert_eq!(r.headers()["access-control-allow-origin"], origin);
        }
        let pre = send("OPTIONS", "/console/confirm", h, Some(origin), "").await.unwrap();
        assert_eq!(pre.status(), if ok { StatusCode::NO_CONTENT } else { StatusCode::FORBIDDEN }, "{origin}");
    }
    // El origen permitido no reemplaza al token.
    assert_eq!(
        send("GET", "/state", h, Some("http://localhost:5173"), "nope").await.unwrap().status(),
        StatusCode::UNAUTHORIZED
    );
    c.stop().await;
}

#[tokio::test]
async fn full_queue_drops_only_high_frequency_moves() {
    let w = world();
    let nid = w.node_id.clone();
    w.call(move |w| {
        let o = w.make_object("taza", Some("taza"), (100.0, 100.0), (40.0, 40.0));
        w.ingest(wrap(Payload::Event(Event::ObjectAppeared { object: o }), 0, "t", &nid, None)).unwrap();
    })
    .await;
    let p = PointMM(300.0, 300.0);
    let ev = |phase| {
        let touch =
            Touch { id: "t1".into(), phase, point_mm: p, widget_id: None, object_id: None, trigger: Trigger::Rest };
        let ev = match phase {
            TouchPhase::Down => Event::TouchDown { touch },
            TouchPhase::Move => Event::TouchMove { touch },
            TouchPhase::Up => Event::TouchUp { touch },
        };
        wrap(Payload::Event(ev), 0, "hands", "n", None)
    };
    w.ingest_bus(ev(TouchPhase::Down), true).await;
    // Actor ocupado (disco lento) mientras llega una ráfaga de manos y movimientos a 30 Hz o más.
    w.post_critical(|_| std::thread::sleep(Duration::from_millis(400))).await;
    for _ in 0..2000 {
        w.set_hands(vec![]);
        w.ingest_bus(ev(TouchPhase::Move), true).await;
    }
    w.ingest_bus(ev(TouchPhase::Up), true).await;
    let gone = Event::ObjectRemoved { object_id: "taza".into(), label: None };
    w.ingest_bus(wrap(Payload::Event(gone), 0, "objects", "n", None), false).await;
    let (touches, objects) = w.call(|w| (w.touches.len(), w.objects.len())).await;
    assert_eq!((touches, objects), (0, 0), "TouchUp y ObjectRemoved no se descartan");
    let moves = w.store.after(0, &["touch_move".into()], 5000).0.len();
    assert!(moves < 2000, "la ráfaga de move sí se descarta ({moves})");
    assert_eq!(w.store.after(0, &["touch_up".into()], 10).0.len(), 1);
    assert_eq!(w.store.after(0, &["object_removed".into()], 10).0.len(), 1);
}

#[test]
fn only_the_hands_worker_token_marks_bus_messages_as_physical() {
    use bytes::Bytes;
    use pcx_core::bus::{authenticate, AUTH_FRAME};
    let f = |parts: &[&[u8]]| parts.iter().map(|p| Bytes::copy_from_slice(p)).collect::<Vec<_>>();
    assert_eq!(authenticate(&f(&[b"{}"]), Some("secreto")), Some((false, b"{}".to_vec())));
    assert_eq!(authenticate(&f(&[AUTH_FRAME, b"secreto", b"{}"]), Some("secreto")), Some((true, b"{}".to_vec())));
    for wrong in [&b"otro"[..], b"", b"secret", b"secreto "] {
        assert_eq!(authenticate(&f(&[AUTH_FRAME, wrong, b"{}"]), Some("secreto")), None);
    }
    assert_eq!(authenticate(&f(&[AUTH_FRAME, b"", b"{}"]), None), None);
}

#[tokio::test]
async fn data_dir_is_private_and_hands_token_is_separate() {
    let dir = tempfile::tempdir().unwrap();
    let data = dir.path().join("pcx");
    let mut s = Settings { data_dir: Some(data.clone()), zmq_enabled: false, ..Default::default() };
    s.prepare_tokens().unwrap();
    let all = [s.token.clone(), s.console_token.clone(), s.debug_token.clone(), s.hands_token.clone()];
    assert_eq!(all.iter().collect::<std::collections::HashSet<_>>().len(), 4);
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(std::fs::metadata(&data).unwrap().permissions().mode() & 0o777, 0o700);
        for name in ["mcp", "console", "debug", "hands"] {
            let m = std::fs::metadata(data.join(format!("{name}.token"))).unwrap().permissions().mode();
            assert_eq!(m & 0o777, 0o600, "{name}");
        }
    }
    let same =
        Settings { data_dir: None, token: Some("x".into()), hands_token: Some("x".into()), ..Default::default() };
    assert!(Core::start(same, None).await.is_err());
}

/// Arranca un core con `--debug`, deja una confirmación pendiente y ya presentada, y devuelve su botón.
async fn core_with_pending() -> (Core, Value, String) {
    let c = Core::start(
        Settings {
            data_dir: None,
            zmq_enabled: false,
            debug: true,
            token: Some("agent-secret".into()),
            console_token: Some("console-secret".into()),
            debug_token: Some("debug-secret".into()),
            hands_token: Some("hands-secret".into()),
            home_policy: HomePolicy {
                allow: vec!["lock.unlock".into()],
                confirm: vec!["*".into()],
                ..Default::default()
            },
            ..Default::default()
        },
        None,
    )
    .await
    .unwrap();
    let r = home(&c.world, "puerta", "lock", "unlock", json!({"entity_id":"lock.puerta"})).await;
    present(&c.world).await;
    let bid = format!("pcx:confirm:{}", r["confirmation_id"].as_str().unwrap());
    (c, r, bid)
}
async fn post_json(c: &Core, path: &str, token: &str, body: Value) -> (StatusCode, String) {
    let req = Request::builder()
        .method("POST")
        .uri(path)
        .header("Host", "127.0.0.1:9200")
        .header("Authorization", format!("Bearer {token}"))
        .header("Content-Type", "application/json")
        .body(Body::from(body.to_string()))
        .unwrap();
    let r = c.router().oneshot(req).await.unwrap();
    let status = r.status();
    let bytes = axum::body::to_bytes(r.into_body(), 1 << 20).await.unwrap();
    (status, String::from_utf8_lossy(&bytes).into_owned())
}

#[tokio::test]
async fn debug_touch_with_long_hold_never_confirms() {
    let (c, _, bid) = core_with_pending().await;
    for body in [json!({"widget_id": bid, "hold_ms": 2500}), json!({"x_mm": 665, "y_mm": 135, "hold_ms": 2500})] {
        let (status, out) = post_json(&c, "/debug/touch", "debug-secret", body).await;
        assert_eq!(status, StatusCode::OK, "{out}");
    }
    assert!(!any_resolution(&c.world), "un toque de debug sostenido no aprueba");
    assert!(c.world.store.after(0, &["widget_pressed".into()], 10).0.is_empty());
    c.stop().await;
}

#[tokio::test]
async fn agent_token_cannot_confirm_and_never_sees_other_tokens() {
    let (c, r, _) = core_with_pending().await;
    let confirm = json!({"confirmation_id": r["confirmation_id"], "approved": true, "by": "console",
                         "action_digest": r["action_digest"], "operator": "agente"});
    // El token del agente (y los de debug y manos) no sirven en /console/*; el del agente tampoco en /debug/*.
    for token in ["agent-secret", "debug-secret", "hands-secret", ""] {
        let (status, _) = post_json(&c, "/console/confirm", token, confirm.clone()).await;
        assert_eq!(status, StatusCode::UNAUTHORIZED, "{token}");
    }
    for (path, body) in
        [("/debug/touch", json!({"x_mm": 665, "y_mm": 135, "hold_ms": 2000})), ("/debug/object", json!({"id": "x"}))]
    {
        for token in ["agent-secret", "console-secret", "hands-secret"] {
            assert_eq!(post_json(&c, path, token, body.clone()).await.0, StatusCode::UNAUTHORIZED, "{path} {token}");
        }
    }
    assert!(!any_resolution(&c.world));
    // Nada de lo que el agente puede leer contiene los otros secretos: /state, /health y todas las tools de lectura.
    let mut seen = String::new();
    for path in ["/state", "/health", "/catalog"] {
        let req = Request::builder()
            .uri(path)
            .header("Host", "127.0.0.1:9200")
            .header("Authorization", "Bearer agent-secret");
        let resp = c.router().oneshot(req.body(Body::empty()).unwrap()).await.unwrap();
        assert_eq!(resp.status(), StatusCode::OK, "{path}");
        seen += &String::from_utf8_lossy(&axum::body::to_bytes(resp.into_body(), 1 << 22).await.unwrap());
    }
    for (tool, args) in [
        ("scene", json!({})),
        ("look", json!({})),
        ("events_wait", json!({"cursor": 0, "timeout_s": 0})),
        ("history", json!({})),
    ] {
        let out = c.tools.call(tool, args, "mcp", None).await;
        seen += &out.text;
    }
    seen += &serde_json::to_string(&c.world.call(|w| w.store.audit_tail(100)).await).unwrap();
    for secret in ["console-secret", "debug-secret", "hands-secret"] {
        assert!(!seen.contains(secret), "{secret} quedó a la vista del agente");
    }
    // El de consola sí confirma.
    assert_eq!(post_json(&c, "/console/confirm", "console-secret", confirm).await.0, StatusCode::OK);
    assert!(any_resolution(&c.world));
    c.stop().await;
}
