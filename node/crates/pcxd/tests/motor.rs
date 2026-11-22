//! `pcx motor` de punta a punta, sin hardware: un cliente WebSocket hace de web app (`hello`, `calib`, contesta
//! `blank` con `blanked`, recibe `objects`) y la cámara es sintética, de replay o ninguna (cuadros por `need_frame`).
//! Además: token, `Origin`, CORS y preflight de Private Network Access.

use std::path::PathBuf;
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::Arc;
use std::time::Duration;

use pcx_perception::pipeline_api::ObjectOut;
use pcx_perception::scene_api::{SceneCtx, SceneEvent, SceneOut, ScenePerceptor, SceneRegistry};

use futures::{SinkExt, StreamExt};
use pcxd::motor::clean::CleanSettings;
use pcxd::motor::frames::{decode_jpeg_b64, jpeg_b64, CameraSpec};
use pcxd::motor::synthetic::Synthetic;
use pcxd::motor::{start, MotorConfig, Running};
use serde_json::{json, Value};
use tokio::sync::mpsc;
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::Message;

const TOKEN: &str = "token-de-prueba-0123456789abcdef";
const GATEWAY: &str = "https://pcx-gateway.onrender.com";
const SIZE: (u32, u32) = (320, 180);

fn config(camera: CameraSpec) -> MotorConfig {
    let mut c = MotorConfig::new(TOKEN, camera);
    c.port = 0;
    c.size = SIZE;
    c.hello_timeout = Duration::from_millis(400);
    c.engine.calm = Duration::from_millis(60);
    c.engine.review_every = Duration::ZERO;
    c.engine.clean = CleanSettings::new(300, false);
    // el ciclo extra tras un cambio se prueba aparte (`tras_un_cambio_pide_otro_ciclo`)
    c.engine.settle_after = Duration::ZERO;
    c
}

async fn motor(camera: CameraSpec) -> Running {
    start(config(camera)).await.unwrap()
}

/// Qué hace la «web app» de prueba cuando el motor pide `blank`.
#[derive(Clone)]
enum OnBlank {
    /// Apaga el proyector simulado y contesta `blanked`; lo vuelve a prender con `unblank`.
    Answer(Option<Synthetic>),
    /// No contesta nunca.
    Ignore,
}

/// Cliente de prueba: una tarea lee el socket, contesta `blank`/`need_frame` y deja todo lo recibido en `rx`.
struct App {
    tx: mpsc::UnboundedSender<Value>,
    rx: mpsc::UnboundedReceiver<Value>,
    seen: Vec<Value>,
}

impl App {
    /// `browser_cam`: cámara de la web app, para contestar `need_frame` (motor sin cámara propia).
    async fn connect(addr: std::net::SocketAddr, on_blank: OnBlank, browser_cam: Option<Synthetic>) -> App {
        let mut req = format!("ws://{addr}/v1/ws").into_client_request().unwrap();
        req.headers_mut().insert("origin", GATEWAY.parse().unwrap());
        let (ws, _) = tokio_tungstenite::connect_async(req).await.unwrap();
        let (mut sink, mut stream) = ws.split();
        let (tx, mut out) = mpsc::unbounded_channel::<Value>();
        let (seen_tx, rx) = mpsc::unbounded_channel::<Value>();
        let reply = tx.clone();
        tokio::spawn(async move {
            loop {
                tokio::select! {
                    m = out.recv() => match m {
                        Some(m) => if sink.send(Message::Text(m.to_string().into())).await.is_err() { break },
                        None => break,
                    },
                    m = stream.next() => {
                        let Some(Ok(Message::Text(t))) = m else { break };
                        let v: Value = serde_json::from_str(t.as_str()).unwrap();
                        match (v["type"].as_str().unwrap_or(""), &on_blank) {
                            ("blank", OnBlank::Answer(cam)) => {
                                assert!(v["max_ms"].as_u64().unwrap() >= 300);
                                if let Some(c) = cam { c.set_projector(false) }
                                // como la consola: 2 cuadros de negro antes de avisar
                                tokio::time::sleep(Duration::from_millis(33)).await;
                                let _ = reply.send(json!({"type": "blanked", "id": v["id"], "t_ns": 123456789.5}));
                            }
                            ("unblank", OnBlank::Answer(cam)) => {
                                if let Some(c) = cam { c.set_projector(true) }
                                let _ = reply.send(json!({"type": "unblanked", "id": v["id"]}));
                            }
                            ("need_frame", _) => if let Some(c) = &browser_cam {
                                let img = c.render(std::time::Instant::now());
                                let _ = reply.send(json!({"type": "frame", "id": v["id"], "jpeg_b64": jpeg_b64(&img, 92).unwrap(),
                                    "w": img.width(), "h": img.height(), "t_ns": 1}));
                            },
                            _ => {}
                        }
                        if seen_tx.send(v).is_err() { break }
                    }
                }
            }
        });
        App { tx, rx, seen: Vec::new() }
    }

    fn send(&self, v: Value) {
        self.tx.send(v).unwrap();
    }

    fn hello(&self) {
        self.send(json!({"type": "hello", "v": 0, "token": TOKEN, "session_id": "s-1", "app": "test"}));
    }

    /// 2 mm por px: la superficie de 640×360 mm cubre el cuadro de 320×180.
    fn calib(&self) {
        self.send(
            json!({"type": "calib", "camera_px": [SIZE.0, SIZE.1], "matrix_px_to_mm": [2, 0, 0, 0, 2, 0, 0, 0, 1],
            "lens": null, "surface_mm": [640, 360]}),
        );
    }

    /// Siguiente mensaje de ese `type` (los demás quedan en `seen`).
    async fn expect(&mut self, ty: &str) -> Value {
        self.wait(|v| v["type"] == ty).await
    }

    async fn wait(&mut self, pred: impl Fn(&Value) -> bool) -> Value {
        let deadline = tokio::time::Instant::now() + Duration::from_secs(10);
        loop {
            let v = tokio::time::timeout_at(deadline, self.rx.recv())
                .await
                .unwrap_or_else(|_| panic!("timeout; recibido: {:#?}", self.seen))
                .expect("el motor cerró la conexión");
            self.seen.push(v.clone());
            if pred(&v) {
                return v;
            }
        }
    }

    /// Espera el `status` idle que cierra un ciclo (trae `total` en `stages_ms`).
    async fn cycle_end(&mut self) -> Value {
        self.wait(|v| v["type"] == "status" && v["state"] == "idle" && v["last_cycle_ms"].is_number()).await
    }

    fn states(&self) -> Vec<String> {
        self.seen.iter().filter(|v| v["type"] == "status").map(|v| v["state"].as_str().unwrap().to_string()).collect()
    }

    fn count(&self, ty: &str) -> usize {
        self.seen.iter().filter(|v| v["type"] == ty).count()
    }
}

fn mean_rgb(img: &image::RgbImage, r: [u32; 4]) -> f64 {
    let (mut s, mut n) = (0u64, 0u64);
    for y in r[1]..r[3] {
        for x in r[0]..r[2] {
            s += img.get_pixel(x, y).0.iter().map(|&c| c as u64).sum::<u64>();
            n += 3;
        }
    }
    s as f64 / n as f64
}

// ------------------------------------------------------------------------------------------------ ciclo

#[tokio::test(flavor = "multi_thread")]
async fn ciclo_completo_con_camara_sintetica() {
    let cam = Synthetic::new(SIZE, 60.0);
    let m = motor(CameraSpec::Synthetic(cam.clone())).await;
    let mut app = App::connect(m.addr, OnBlank::Answer(Some(cam.clone())), None).await;
    app.hello();
    let w = app.expect("welcome").await;
    assert_eq!(w["v"], 0);
    assert_eq!(w["needs_frames"], false);
    assert_eq!(w["camera"]["px"], json!([320, 180]));
    assert_eq!(w["models"], json!({"sam": false, "hands": false}));
    // justo después de `welcome`: todos los objetos vigentes (ninguno todavía)
    let snap = app.expect("objects").await;
    assert_eq!((&snap["upsert"], &snap["remove"]), (&json!([]), &json!([])));

    // la calibración dispara el primer ciclo: la primera foto limpia es la referencia, sin objetos
    app.calib();
    let st = app.cycle_end().await;
    for k in ["calm", "blank", "capture", "median", "perceive", "total"] {
        assert!(st["stages_ms"][k].is_number(), "falta la etapa {k}: {st}");
    }
    assert!(st["stages_ms"]["blank"].as_f64().unwrap() >= 30.0, "esperó a `blanked`: {st}");
    // (`calm` aparece solo si la calibración llegó antes de cumplirse la calma inicial)
    let states: Vec<String> = app.states().into_iter().filter(|s| s != "calm").collect();
    assert_eq!(states, ["idle", "blank", "capturing", "perceiving", "idle"]);
    assert_eq!((app.count("blank"), app.count("unblank"), app.count("objects")), (1, 1, 1));

    // alguien deja un objeto: manos → manos fuera → calma → ciclo → `objects` incremental
    app.send(json!({"type": "hands", "present": true, "t_ns": 1}));
    cam.put_object("taza", [100, 60, 140, 100], [210, 60, 40]);
    app.send(json!({"type": "hands", "present": false, "t_ns": 2}));
    let o = app.expect("objects").await;
    assert_eq!((o["rev"].as_u64(), o["remove"].as_array().unwrap().len()), (Some(1), 0));
    let obj = &o["upsert"][0];
    assert_eq!((obj["id"].as_str(), obj["label"].as_str()), (Some("objeto-1"), Some("objeto-1")));
    // centro del rectángulo en px (120, 80) × 2 mm/px
    let (cx, cy) = (obj["center_mm"][0].as_f64().unwrap(), obj["center_mm"][1].as_f64().unwrap());
    assert!((cx - 240.0).abs() < 6.0 && (cy - 160.0).abs() < 6.0, "{obj}");
    let poly = obj["polygon_mm"].as_array().unwrap();
    assert!((3..=64).contains(&poly.len()));
    assert!(poly.iter().all(|p| (190.0..=290.0).contains(&p[0].as_f64().unwrap())), "{poly:?}");
    assert!(obj["confidence"].as_f64().unwrap() > 0.0);
    app.cycle_end().await;
    assert!(
        app.states().ends_with(&["calm", "blank", "capturing", "perceiving", "idle"].map(String::from)),
        "{:?}",
        app.states()
    );

    // una consola que se recarga recibe todos los objetos justo después de `welcome`
    let mut app2 = App::connect(m.addr, OnBlank::Ignore, None).await;
    app2.hello();
    app2.expect("welcome").await;
    let snap = app2.expect("objects").await;
    assert_eq!((snap["rev"].as_u64(), snap["upsert"][0]["id"].as_str()), (Some(1), Some("objeto-1")));
    drop(app2);

    // `look{clean:true}`: foto tomada a oscuras (sin las franjas del proyector) y con el objeto
    app.send(json!({"type": "look", "id": "foto-1", "clean": true}));
    let lf = app.expect("look_frame").await;
    assert_eq!((lf["id"].as_str(), lf["w"].as_u64(), lf["h"].as_u64()), (Some("foto-1"), Some(320), Some(180)));
    let clean = decode_jpeg_b64(lf["jpeg_b64"].as_str().unwrap()).unwrap();
    let table = [200, 120, 280, 160];
    tokio::time::sleep(Duration::from_millis(300)).await; // la proyección simulada vuelve tras su latencia
    let lit = mean_rgb(&cam.render(std::time::Instant::now()), table);
    let dark = mean_rgb(&clean, table);
    assert!(lit - dark > 50.0, "la foto limpia no tiene proyección: {lit} vs {dark}");
    let px = clean.get_pixel(120, 80).0;
    assert!(px[0] > 170 && px[1] < 100, "el objeto se ve en la foto limpia: {px:?}");
    // `look` normal: lo que ve la cámara ahora, con la proyección encima
    app.send(json!({"type": "look", "id": 7, "clean": false}));
    let lf = app.expect("look_frame").await;
    assert_eq!(lf["id"], 7);
    let live = decode_jpeg_b64(lf["jpeg_b64"].as_str().unwrap()).unwrap();
    assert!(mean_rgb(&live, table) - dark > 50.0);

    // se llevan el objeto y se fuerza un ciclo
    cam.remove_object("taza");
    app.send(json!({"type": "perceive"}));
    let o = app.expect("objects").await;
    assert_eq!((o["rev"].as_u64(), &o["upsert"], &o["remove"]), (Some(2), &json!([]), &json!(["objeto-1"])));
    app.cycle_end().await;

    // `reset` olvida lo que haya
    cam.put_object("libro", [200, 40, 260, 90], [40, 90, 200]);
    app.send(json!({"type": "perceive"}));
    assert_eq!(app.expect("objects").await["upsert"][0]["id"], "objeto-2");
    app.cycle_end().await;
    app.send(json!({"type": "reset"}));
    let o = app.expect("objects").await;
    assert_eq!((o["rev"].as_u64(), &o["remove"]), (Some(4), &json!(["objeto-2"])));
    assert_eq!(app.count("blank"), app.count("unblank"), "cada apagón termina con su unblank");
    assert_eq!(app.count("error"), 0, "{:#?}", app.seen);
    m.shutdown().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn sin_blanked_no_hay_foto_limpia() {
    let cam = Synthetic::new(SIZE, 60.0);
    let m = motor(CameraSpec::Synthetic(cam.clone())).await;
    let mut app = App::connect(m.addr, OnBlank::Ignore, None).await;
    app.hello();
    app.expect("welcome").await;
    app.calib();
    // el proyector se apaga igual (la cámara vería oscuro), pero la web app no avisa `blanked`
    let blank = app.expect("blank").await;
    cam.set_projector(false);
    let err = app.expect("error").await;
    assert_eq!(err["code"], "blanked_timeout", "{err}");
    let unblank = app.seen.iter().find(|v| v["type"] == "unblank").expect("unblank aunque falle");
    assert_eq!(unblank["id"], blank["id"]);
    let st = app.cycle_end().await;
    assert!(st["stages_ms"]["perceive"].is_null(), "sin foto no hay percepción: {st}");
    assert!(!app.states().contains(&"capturing".to_string()));
    // un `blanked` de otro apagón (o tardío) tampoco sirve
    app.send(json!({"type": "blanked", "id": blank["id"], "t_ns": 1}));
    app.send(json!({"type": "look", "id": "x", "clean": true}));
    let err = app.wait(|v| v["type"] == "error" && v["id"] == "x").await;
    assert_eq!(err["code"], "blanked_timeout");
    assert_eq!(app.count("look_frame"), 0);
    assert_eq!(app.seen.iter().filter(|v| v["type"] == "objects" && v["rev"] != 0).count(), 0);
    m.shutdown().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn sin_camara_pide_cuadros_a_la_web_app() {
    let m = motor(CameraSpec::None).await;
    // la «cámara del navegador»: la web app contesta `need_frame` con sus cuadros
    let cam = Synthetic::new(SIZE, 30.0);
    let mut app = App::connect(m.addr, OnBlank::Answer(Some(cam.clone())), Some(cam.clone())).await;
    app.hello();
    let w = app.expect("welcome").await;
    assert_eq!((&w["camera"], &w["needs_frames"]), (&Value::Null, &json!(true)));
    app.calib();
    app.expect("need_frame").await;
    app.cycle_end().await;
    assert_eq!(app.count("error"), 0, "{:#?}", app.seen);
    cam.put_object("taza", [100, 60, 140, 100], [210, 60, 40]);
    app.send(json!({"type": "perceive"}));
    let o = app.wait(|v| v["type"] == "objects" && v["rev"] == 1).await;
    assert_eq!(o["upsert"][0]["id"], "objeto-1");
    // un `frame` que nadie pidió se ignora sin romper nada
    app.send(json!({"type": "frame", "id": "inventado", "jpeg_b64": "////", "w": 1, "h": 1, "t_ns": 1}));
    app.send(json!({"type": "otra_cosa"}));
    assert_eq!(app.expect("error").await["code"], "bad_message");
    m.shutdown().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn replay_y_ciclo_periodico() {
    let frames = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../pcx-capture/testdata/frames");
    let camera = CameraSpec::parse(Some(&format!("replay:{}", frames.display())), SIZE).unwrap();
    let mut cfg = config(camera);
    cfg.engine.review_every = Duration::from_millis(700);
    // el replay no cambia entre cuadros salvo por el bucle de imágenes: no se descarta por movimiento
    cfg.engine.max_spread = 255.0;
    let m = start(cfg).await.unwrap();
    let health: Value = serde_json::from_str(
        &reqwest::get(format!("http://{}/v1/health", m.addr)).await.unwrap().text().await.unwrap(),
    )
    .unwrap();
    assert_eq!((health["ok"].as_bool(), health["camera"]["id"].as_str()), (Some(true), Some("frames")));
    assert_eq!(health["version"], env!("CARGO_PKG_VERSION"));
    assert_eq!(health["models"], json!({"sam": false, "hands": false}));

    let mut app = App::connect(m.addr, OnBlank::Answer(None), None).await;
    app.hello();
    assert_eq!(app.expect("welcome").await["camera"]["px"], json!([320, 180]));
    app.send(json!({"type": "perceive"}));
    assert_eq!(app.expect("error").await["code"], "no_calib");
    app.send(json!({"type": "look", "id": "a", "clean": false}));
    let lf = app.expect("look_frame").await;
    assert_eq!((lf["w"].as_u64(), lf["h"].as_u64()), (Some(320), Some(180)));
    // sin proyección que apagar la luminancia no cae: con `blanked` recibido se elige por tiempos
    app.calib();
    app.cycle_end().await;
    // y sin que nadie lo pida llega otro ciclo
    app.cycle_end().await;
    assert!(app.count("blank") >= 2, "{:?}", app.states());
    // otra calibración con otro tamaño: la cámara se reabre a `camera_px`
    app.send(json!({"type": "calib", "camera_px": [160, 90], "matrix_px_to_mm": [4, 0, 0, 0, 4, 0, 0, 0, 1],
        "lens": {"f": 150, "cx": 80, "cy": 45, "k1": -0.05, "k2": 0.0}, "surface_mm": [640, 360]}));
    app.send(json!({"type": "calib", "camera_px": [0, 90], "matrix_px_to_mm": [4, 0, 0, 0, 4, 0, 0, 0, 1], "surface_mm": [1, 1]}));
    app.wait(|v| v["type"] == "error" && v["code"] == "bad_calib").await;
    let mut size = (0, 0);
    for i in 0..40 {
        app.send(json!({"type": "look", "id": i, "clean": false}));
        let lf = app.wait(|v| v["type"] == "look_frame" && v["id"] == i).await;
        size = (lf["w"].as_u64().unwrap(), lf["h"].as_u64().unwrap());
        if size == (160, 90) {
            break;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    assert_eq!(size, (160, 90));
    m.shutdown().await;
}

// ------------------------------------------------------------------------------------------------ seguridad

#[tokio::test(flavor = "multi_thread")]
async fn tras_un_cambio_pide_otro_ciclo() {
    let cam = Synthetic::new(SIZE, 60.0);
    let mut cfg = config(CameraSpec::Synthetic(cam.clone()));
    cfg.engine.settle_after = Duration::from_millis(300);
    let m = start(cfg).await.unwrap();
    let mut app = App::connect(m.addr, OnBlank::Answer(Some(cam.clone())), None).await;
    app.hello();
    app.calib();
    app.cycle_end().await;
    // mesa vacía, nada cambió: no hay ciclo extra
    tokio::time::sleep(Duration::from_millis(700)).await;
    assert_eq!(app.count("blank"), 1);
    // aparece un objeto: al ciclo que lo publica le sigue otro sin que nadie lo pida, y ahí termina
    cam.put_object("taza", [100, 60, 140, 100], [210, 60, 40]);
    app.send(json!({"type": "perceive"}));
    app.wait(|v| v["type"] == "objects" && v["rev"] == 1).await;
    app.cycle_end().await;
    let t = std::time::Instant::now();
    app.cycle_end().await;
    assert!(t.elapsed() >= Duration::from_millis(300), "el ciclo extra espera su plazo: {:?}", t.elapsed());
    tokio::time::sleep(Duration::from_millis(900)).await;
    while let Ok(v) = app.rx.try_recv() {
        app.seen.push(v);
    }
    assert_eq!(app.count("blank"), 3, "{:?}", app.states());
    m.shutdown().await;
}

// ------------------------------------------------------------------------------------------------ escenas

/// Módulo falso de escena: un objeto y un evento por foto limpia; con la cámara lateral, un evento cada 5 cuadros.
struct Lab {
    resets: Arc<AtomicU32>,
}

fn scene_obj(id: &str) -> ObjectOut {
    ObjectOut {
        id: id.into(),
        label: "Vaso B".into(),
        polygon_mm: vec![[90.0, 90.0], [110.0, 90.0], [110.0, 110.0], [90.0, 110.0]],
        center_mm: [100.0, 100.0],
        confidence: 0.9,
    }
}

impl ScenePerceptor for Lab {
    fn name(&self) -> &str {
        "lab"
    }
    fn live_camera(&self) -> Option<&str> {
        Some("lateral")
    }
    fn on_clean(&mut self, frame: &image::RgbImage, ctx: &mut SceneCtx) -> Result<SceneOut, String> {
        let n = ctx.memory["fotos"].as_u64().unwrap_or(0) + 1;
        ctx.memory["fotos"] = n.into();
        let data = json!({"n": n, "objetos": ctx.objects.len(), "w": frame.width(), "mm": ctx.calib.surface_mm.0});
        Ok(SceneOut {
            upsert: vec![scene_obj("lab:vaso-b")],
            remove: vec![],
            events: vec![SceneEvent { event: "foto".into(), data }],
        })
    }
    fn on_live(&mut self, frame: &image::RgbImage, ctx: &mut SceneCtx) -> Result<SceneOut, String> {
        let n = ctx.memory["cuadros"].as_u64().unwrap_or(0) + 1;
        ctx.memory["cuadros"] = n.into();
        let mut out = SceneOut::default();
        if n == 1 {
            out.upsert.push(scene_obj("lab:nivel"));
        }
        if n % 5 == 1 {
            let data = json!({"cuadro": n, "w": frame.width(), "ml": 42.5});
            out.events.push(SceneEvent { event: "nivel".into(), data });
        }
        Ok(out)
    }
    fn reset(&mut self) {
        self.resets.fetch_add(1, Ordering::Relaxed);
    }
}

/// Módulo que publica un id sin el prefijo de su escena: el motor no lo deja pasar.
struct Malo;
impl ScenePerceptor for Malo {
    fn name(&self) -> &str {
        "malo"
    }
    fn on_clean(&mut self, _: &image::RgbImage, _: &mut SceneCtx) -> Result<SceneOut, String> {
        Ok(SceneOut { upsert: vec![scene_obj("vaso-sin-prefijo")], ..Default::default() })
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn escenas_eventos_y_camara_lateral() {
    let cam = Synthetic::new(SIZE, 60.0);
    let resets = Arc::new(AtomicU32::new(0));
    let mut cfg = config(CameraSpec::Synthetic(cam.clone()));
    cfg.lateral = CameraSpec::Synthetic(Synthetic::new((160, 90), 30.0));
    cfg.lateral_size = (160, 90);
    cfg.scenes = SceneRegistry::default();
    cfg.scenes.register(Box::new(Lab { resets: resets.clone() }));
    cfg.scenes.register(Box::new(Malo));
    let m = start(cfg).await.unwrap();
    let mut app = App::connect(m.addr, OnBlank::Answer(Some(cam.clone())), None).await;
    app.hello();
    let w = app.expect("welcome").await;
    assert_eq!(w["scenes"], json!(["lab", "malo"]));
    assert_eq!(
        w["cameras"],
        json!([{"id": "synthetic", "role": "cenital", "name": "cámara sintética"},
               {"id": "synthetic", "role": "lateral", "name": "cámara sintética"}])
    );
    // sin escena activa nadie recibe los cuadros de la lateral
    tokio::time::sleep(Duration::from_millis(200)).await;

    // `scene lab`: la lateral alimenta `on_live` aunque todavía no haya calibración
    app.send(json!({"type": "scene", "name": "lab"}));
    let ev = app.expect("event").await;
    assert_eq!((ev["scene"].as_str(), ev["event"].as_str()), (Some("lab"), Some("nivel")));
    assert_eq!(ev["data"], json!({"cuadro": 1, "w": 160, "ml": 42.5}));
    // (el `objects` de esa misma llamada sale antes que sus eventos)
    let o = app.seen.iter().find(|v| v["type"] == "objects" && v["rev"] != 0).expect("objects de on_live");
    assert_eq!(o["upsert"][0]["id"], "lab:nivel");
    assert_eq!(app.wait(|v| v["type"] == "event").await["data"]["cuadro"], 6);

    // con calibración corre el ciclo cenital: `on_clean` del módulo después del `Perceptor` base
    app.calib();
    let foto = app.wait(|v| v["type"] == "event" && v["event"] == "foto").await;
    assert_eq!(foto["data"], json!({"n": 1, "objetos": 0, "w": 320, "mm": 640.0}));
    let st = app.cycle_end().await;
    assert!(st["stages_ms"]["scene"].is_number(), "{st}");
    assert!(app.seen.iter().any(|v| v["type"] == "objects" && v["upsert"][0]["id"] == "lab:vaso-b"));
    // un objeto del `Perceptor` base llega al módulo en `ctx.objects` (los de la escena no)
    cam.put_object("taza", [100, 60, 140, 100], [210, 60, 40]);
    app.send(json!({"type": "perceive"}));
    let foto = app.wait(|v| v["type"] == "event" && v["event"] == "foto").await;
    assert_eq!((foto["data"]["n"].as_u64(), foto["data"]["objetos"].as_u64()), (Some(2), Some(1)));
    app.cycle_end().await;

    // cambio de escena: `reset` al módulo anterior, sus objetos se retiran y el nuevo ve la mesa
    let before = resets.load(Ordering::Relaxed);
    app.send(json!({"type": "scene", "name": "malo"}));
    let rm = app.wait(|v| v["type"] == "objects" && !v["remove"].as_array().unwrap().is_empty()).await;
    assert_eq!(rm["remove"], json!(["lab:nivel", "lab:vaso-b"]));
    assert!(resets.load(Ordering::Relaxed) > before);
    // ids sin prefijo: error y nada publicado
    let err = app.wait(|v| v["type"] == "error").await;
    assert_eq!(err["code"], "scene_failed", "{err}");
    assert!(err["message"].as_str().unwrap().contains("vaso-sin-prefijo"), "{err}");
    app.cycle_end().await;
    // y la lateral ya no llega a nadie (`malo` no la pidió)
    let events = app.count("event");
    tokio::time::sleep(Duration::from_millis(300)).await;
    app.send(json!({"type": "look", "id": "fin", "clean": false}));
    app.wait(|v| v["type"] == "look_frame" && v["id"] == "fin").await;
    assert_eq!(app.count("event"), events);
    assert!(!app.seen.iter().any(
        |v| v["type"] == "objects" && v["upsert"].as_array().unwrap().iter().any(|o| o["id"] == "vaso-sin-prefijo")
    ));

    // de vuelta a `lab`: memoria nueva (parte del cuadro 1 y de la foto 1)
    app.send(json!({"type": "scene", "name": "lab"}));
    assert_eq!(app.wait(|v| v["type"] == "event" && v["event"] == "nivel").await["data"]["cuadro"], 1);
    assert_eq!(app.wait(|v| v["type"] == "event" && v["event"] == "foto").await["data"]["n"], 1);
    app.cycle_end().await;
    // una consola que se conecta ahora recibe también los objetos de la escena
    let mut otra = App::connect(m.addr, OnBlank::Ignore, None).await;
    otra.hello();
    let ids: Vec<String> =
        otra.expect("objects").await["upsert"].as_array().unwrap().iter().map(|o| o["id"].to_string()).collect();
    assert_eq!(ids, ["\"lab:nivel\"", "\"lab:vaso-b\"", "\"objeto-1\""]);
    // `scene null` (o una escena sin módulo): se retiran los objetos de la escena y quedan los del catálogo
    app.send(json!({"type": "scene", "name": "no-existe"}));
    let rm = app.wait(|v| v["type"] == "objects" && !v["remove"].as_array().unwrap().is_empty()).await;
    assert_eq!(rm["remove"], json!(["lab:nivel", "lab:vaso-b"]));
    app.send(json!({"type": "scene", "name": null}));
    app.send(json!({"type": "perceive"}));
    app.cycle_end().await;
    m.shutdown().await;
}

async fn ws_connect(addr: std::net::SocketAddr, origin: Option<&str>) -> Result<App, u16> {
    let mut req = format!("ws://{addr}/v1/ws").into_client_request().unwrap();
    if let Some(o) = origin {
        req.headers_mut().insert("origin", o.parse().unwrap());
    }
    match tokio_tungstenite::connect_async(req).await {
        Ok((ws, _)) => {
            let (mut sink, mut stream) = ws.split();
            let (tx, mut out) = mpsc::unbounded_channel::<Value>();
            let (seen_tx, rx) = mpsc::unbounded_channel::<Value>();
            tokio::spawn(async move {
                loop {
                    tokio::select! {
                        Some(m) = out.recv() => { let _ = sink.send(Message::Text(m.to_string().into())).await; }
                        m = stream.next() => match m {
                            Some(Ok(Message::Text(t))) => { let _ = seen_tx.send(serde_json::from_str(t.as_str()).unwrap()); }
                            Some(Ok(Message::Close(c))) => {
                                let _ = seen_tx.send(json!({"type": "closed", "code": c.map(|c| u16::from(c.code))}));
                                break;
                            }
                            Some(Ok(_)) => {}
                            _ => { let _ = seen_tx.send(json!({"type": "closed"})); break }
                        }
                    }
                }
            });
            Ok(App { tx, rx, seen: Vec::new() })
        }
        Err(tokio_tungstenite::tungstenite::Error::Http(r)) => Err(r.status().as_u16()),
        Err(e) => panic!("{e}"),
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn el_ws_valida_origin_y_exige_el_token() {
    let m = motor(CameraSpec::None).await;
    // Origin: solo la lista del protocolo
    for bad in [
        "https://evil.example",
        "http://localhost.evil.example",
        "https://pcx-gateway.onrender.com.evil.example",
        "null",
    ] {
        assert_eq!(ws_connect(m.addr, Some(bad)).await.err(), Some(403), "{bad}");
    }
    for ok in [Some(GATEWAY), Some("http://localhost:5173"), Some("http://127.0.0.1:8000"), None] {
        // token malo: `error` y cierre, sin `welcome`
        let mut app = ws_connect(m.addr, ok).await.unwrap_or_else(|s| panic!("{ok:?} → {s}"));
        app.send(json!({"type": "hello", "v": 0, "token": "no-es-el-token", "session_id": "s", "app": "x"}));
        assert_eq!(app.expect("error").await["code"], "unauthorized");
        assert_eq!(app.expect("closed").await["code"], 4401);
        assert_eq!(app.count("welcome"), 0);
    }
    // sin token, con otra versión, o hablando antes del `hello`
    for (first, code) in [
        (json!({"type": "hello", "v": 0}), "unauthorized"),
        (json!({"type": "hello", "v": 1, "token": TOKEN}), "bad_version"),
        (json!({"type": "perceive"}), "hello_required"),
        (json!({"type": "look", "id": 1, "clean": false}), "hello_required"),
    ] {
        let mut app = ws_connect(m.addr, Some(GATEWAY)).await.unwrap();
        app.send(first);
        assert_eq!(app.expect("error").await["code"], code);
        app.expect("closed").await;
        assert_eq!(app.count("welcome") + app.count("look_frame") + app.count("blank"), 0);
    }
    // mudo: se cierra solo
    let mut app = ws_connect(m.addr, Some(GATEWAY)).await.unwrap();
    assert_eq!(app.expect("error").await["code"], "hello_required");
    app.expect("closed").await;
    // con el token correcto entra
    let mut app = ws_connect(m.addr, Some(GATEWAY)).await.unwrap();
    app.send(json!({"type": "hello", "v": 0, "token": TOKEN, "session_id": null, "app": "x"}));
    assert_eq!(app.expect("welcome").await["needs_frames"], true);
    m.shutdown().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn cors_preflight_de_red_privada_y_host() {
    let m = motor(CameraSpec::None).await;
    let url = format!("http://{}/v1/health", m.addr);
    let http = reqwest::Client::new();
    let h = |r: &reqwest::Response, k: &str| r.headers().get(k).map(|v| v.to_str().unwrap().to_string());

    // sin Origin (curl, health check de `pcx up`): 200 sin cabeceras CORS
    let r = http.get(&url).send().await.unwrap();
    assert_eq!((r.status().as_u16(), h(&r, "access-control-allow-origin")), (200, None));
    for origin in [GATEWAY, "http://localhost:5173", "http://127.0.0.1:9230"] {
        let r = http.get(&url).header("origin", origin).send().await.unwrap();
        assert_eq!((r.status().as_u16(), h(&r, "access-control-allow-origin")), (200, Some(origin.to_string())));
        assert_eq!(h(&r, "vary").as_deref(), Some("Origin"));
        // preflight de Private Network Access
        let r = http
            .request(reqwest::Method::OPTIONS, &url)
            .header("origin", origin)
            .header("access-control-request-method", "GET")
            .header("access-control-request-private-network", "true")
            .send()
            .await
            .unwrap();
        assert_eq!(r.status().as_u16(), 204);
        assert_eq!(h(&r, "access-control-allow-origin").as_deref(), Some(origin));
        assert_eq!(h(&r, "access-control-allow-private-network").as_deref(), Some("true"));
        assert!(h(&r, "access-control-allow-methods").unwrap().contains("GET"));
    }
    for origin in ["https://evil.example", "http://pcx-gateway.onrender.com", "http://localhost:5173.evil.example"] {
        let r = http.get(&url).header("origin", origin).send().await.unwrap();
        assert_eq!((r.status().as_u16(), h(&r, "access-control-allow-origin")), (403, None), "{origin}");
        let r = http
            .request(reqwest::Method::OPTIONS, &url)
            .header("origin", origin)
            .header("access-control-request-method", "GET")
            .header("access-control-request-private-network", "true")
            .send()
            .await
            .unwrap();
        assert_eq!((r.status().as_u16(), h(&r, "access-control-allow-private-network")), (403, None), "{origin}");
    }
    // DNS rebinding: otro `Host` → 421, también en el upgrade del WebSocket
    let r = http.get(&url).header("host", "evil.example:9230").send().await.unwrap();
    assert_eq!(r.status().as_u16(), 421);
    let r = http.get(&url).header("host", format!("localhost:{}", m.addr.port())).send().await.unwrap();
    assert_eq!(r.status().as_u16(), 200);
    m.shutdown().await;
}

// ------------------------------------------------------------------------------------------------ binario

fn free_port() -> u16 {
    std::net::TcpListener::bind("127.0.0.1:0").unwrap().local_addr().unwrap().port()
}

async fn until_healthy(port: u16) -> Value {
    for _ in 0..200 {
        if let Ok(r) = reqwest::get(format!("http://127.0.0.1:{port}/v1/health")).await {
            return serde_json::from_str(&r.text().await.unwrap()).unwrap();
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    panic!("el motor no respondió");
}

/// `pcx motor` suelto y bajo `pcx up`: token 0600 en el directorio pedido y URL de emparejamiento impresa.
#[cfg(unix)]
#[tokio::test(flavor = "multi_thread")]
async fn binario_genera_token_e_imprime_la_url() {
    use std::os::unix::fs::PermissionsExt;
    use std::process::Stdio;
    let tmp = tempfile::tempdir().unwrap();
    let pcx = env!("CARGO_BIN_EXE_pcx");

    // suelto
    let (dir, port) = (tmp.path().join("a"), free_port());
    let child = tokio::process::Command::new(pcx)
        .args(["motor", "--port", &port.to_string(), "--camera", "none", "--dir"])
        .arg(&dir)
        .env_remove("PCX_SUPERVISOR_PID")
        .stderr(Stdio::piped())
        .kill_on_drop(true)
        .spawn()
        .unwrap();
    let health = until_healthy(port).await;
    assert_eq!((health["ok"].as_bool(), &health["camera"]), (Some(true), &Value::Null));
    let token = std::fs::read_to_string(dir.join("motor.token")).unwrap();
    assert_eq!(token.len(), 64);
    assert_eq!(std::fs::metadata(dir.join("motor.token")).unwrap().permissions().mode() & 0o777, 0o600);
    unsafe { libc::kill(child.id().unwrap() as i32, libc::SIGTERM) };
    let out = tokio::time::timeout(Duration::from_secs(10), child.wait_with_output()).await.unwrap().unwrap();
    let err = String::from_utf8_lossy(&out.stderr);
    let url = format!("{GATEWAY}/?motor=ws://127.0.0.1:{port}&motor_token={token}");
    assert!(err.contains(&url), "{err}");

    // bajo `pcx up`: el supervisor genera el token, imprime la URL y levanta el motor
    let (dir, port) = (tmp.path().join("b"), free_port());
    let cfg = tmp.path().join("pcx.toml");
    std::fs::write(
        &cfg,
        format!(
            "[env]\nPCX_MOTOR_DIR = \"{0}\"\nPCX_DATA_DIR = \"{0}\"\n[process.motor]\ncmd = [\"pcx\", \"motor\", \"--port\", \"{port}\"]\nhealth = \"http://127.0.0.1:{port}/v1/health\"\n",
            dir.display()
        ),
    )
    .unwrap();
    let up = tokio::process::Command::new(pcx)
        .args(["up", "--config"])
        .arg(&cfg)
        .env("PCX_RUNTIME_DIR", tmp.path().join("run"))
        .env("PCX_BIN", pcx)
        .stderr(Stdio::piped())
        .kill_on_drop(true)
        .spawn()
        .unwrap();
    until_healthy(port).await;
    let token = std::fs::read_to_string(dir.join("motor.token")).unwrap();
    // el token del archivo es el que acepta el motor
    let mut app = ws_connect(format!("127.0.0.1:{port}").parse().unwrap(), Some(GATEWAY)).await.unwrap();
    app.send(json!({"type": "hello", "v": 0, "token": token, "session_id": "s", "app": "x"}));
    app.expect("welcome").await;
    unsafe { libc::kill(up.id().unwrap() as i32, libc::SIGTERM) };
    let out = tokio::time::timeout(Duration::from_secs(15), up.wait_with_output()).await.unwrap().unwrap();
    let err = String::from_utf8_lossy(&out.stderr);
    let url = format!("{GATEWAY}/?motor=ws://127.0.0.1:{port}&motor_token={token}");
    assert_eq!(err.matches(&url).count(), 1, "la URL se imprime una vez: {err}");
}
