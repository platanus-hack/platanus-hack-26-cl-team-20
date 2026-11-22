//! `pcx motor` (el binario) con el `Pipeline` real sobre fotos de la mesa, de punta a punta y sin hardware.
//!
//! La cámara es `--camera replay:<carpeta>`; un cliente WebSocket hace de web app (`hello`, `calib`, contesta `blank`
//! con `blanked`) y recibe `objects` con polígonos en mm. Imprime las latencias de cada ciclo. Dos fuentes:
//!
//! - `PCX_FOTOS_DIR` con `mesa-limpia-1.jpg` (`origin/daniel/consola:data/mesa/2026-11-21`): 8 s de la foto y 8 s de
//!   la misma foto sin las cucharas, en bucle.
//! - `PCX_FIXTURES_DIR` con `secuencia.json` + fotos (`origin/max/motor:motor/datos/fixtures-perceptor`): cada paso
//!   dura 7 s y al final de cada uno se compara la tabla del motor con la verdad del paso.
//!
//! ```sh
//! PCX_FOTOS_DIR=/tmp/pcx-fotos PCX_MODELS_DIR=/tmp/pcx-models \
//!   cargo test -p pcxd --release --features onnx --test motor_fotos -- --ignored --nocapture
//! # sin SAM: PCX_E2E_SAM=0 · con los fixtures: PCX_FIXTURES_DIR=/tmp/pcx-fixtures en vez de PCX_FOTOS_DIR
//! ```
#![cfg(unix)]

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::{Duration, Instant};

use futures::{SinkExt, StreamExt};
use image::RgbImage;
use serde_json::{json, Value};
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::Message;

/// Homografía px → mm de `data/mesa/2026-11-21` (4 puntos del README; la misma de `pcx-perception/tests/common`).
const MESA: [f64; 9] = [
    -1.6723079372204779,
    -0.26792988279050545,
    1543.671282801911,
    0.26786752635553684,
    -1.6432430946721492,
    875.7345539375533,
    -5.955931426470539e-05,
    -3.953327417197485e-05,
    1.0,
];
const FPS: u32 = 30;
/// Un objeto del motor «es» el de la verdad si su centro queda a menos de esto.
const MATCH_MM: f64 = 60.0;

/// Un tramo del replay: una foto fija durante `secs`, y la verdad de ese paso si se conoce.
struct Step {
    photo: PathBuf,
    secs: u32,
    what: String,
    truth: Vec<(String, [f64; 2])>,
}

struct Scenario {
    steps: Vec<Step>,
    calib: Value,
    surface: [f64; 2],
    run_s: u32,
}

/// Tapa `src` (x, y, w, h en px de cámara) con un trozo de mesa tomado de `fill`.
fn quitar(img: &RgbImage, src: (u32, u32, u32, u32), fill: (u32, u32)) -> RgbImage {
    let mut out = img.clone();
    let table = image::imageops::crop_imm(img, fill.0, fill.1, src.2, src.3).to_image();
    image::imageops::replace(&mut out, &table, src.0 as i64, src.1 as i64);
    out
}

fn save_jpeg(img: &RgbImage, path: &Path) {
    let mut f = std::fs::File::create(path).unwrap();
    image::codecs::jpeg::JpegEncoder::new_with_quality(&mut f, 95).encode_image(img).unwrap();
}

fn fotos(dir: &Path, tmp: &Path) -> Scenario {
    let f1 = image::open(dir.join("mesa-limpia-1.jpg")).expect("mesa-limpia-1.jpg").to_rgb8();
    let (a, b) = (tmp.join("a.jpg"), tmp.join("b.jpg"));
    save_jpeg(&f1, &a);
    save_jpeg(&quitar(&f1, (728, 358, 88, 76), (490, 500)), &b);
    let step = |photo, what: &str| Step { photo, secs: 8, what: what.into(), truth: vec![] };
    Scenario {
        steps: vec![step(a, "la mesa como en la foto"), step(b, "sin las cucharas")],
        calib: json!({"type": "calib", "camera_px": [1280, 720], "matrix_px_to_mm": MESA, "lens": null,
            "surface_mm": [1000.0, 563.0]}),
        surface: [1000.0, 563.0],
        run_s: 38,
    }
}

fn fixtures(dir: &Path) -> Scenario {
    let seq: Value = serde_json::from_slice(&std::fs::read(dir.join("secuencia.json")).unwrap()).unwrap();
    let c = &seq["calib"];
    let steps: Vec<Step> = seq["pasos"]
        .as_array()
        .unwrap()
        .iter()
        .enumerate()
        .map(|(i, p)| Step {
            photo: dir.join(p["foto"].as_str().unwrap()),
            // el primero dura más: ahí cae el registro inicial
            secs: if i == 0 { 16 } else { 7 },
            what: p["que"].as_str().unwrap_or("").to_string(),
            truth: p["verdad"]
                .as_object()
                .unwrap()
                .iter()
                .filter(|(_, o)| o["en_la_mesa"] == true)
                .map(|(id, o)| {
                    let label = format!("{id} {}", o["label"].as_str().unwrap_or(""));
                    (label, [o["center_mm"][0].as_f64().unwrap(), o["center_mm"][1].as_f64().unwrap()])
                })
                .collect(),
        })
        .collect();
    let surface = [c["surface_mm"][0].as_f64().unwrap(), c["surface_mm"][1].as_f64().unwrap()];
    Scenario {
        run_s: steps.iter().map(|s| s.secs).sum(),
        steps,
        calib: json!({"type": "calib", "camera_px": c["image_px"], "matrix_px_to_mm": c["matrix_px_to_mm"],
            "lens": c["lens"], "surface_mm": c["surface_mm"]}),
        surface,
    }
}

/// Carpeta de replay: cada paso, `secs` segundos de su foto (enlaces duros: una copia por foto en disco).
fn replay_dir(dir: &Path, steps: &[Step]) {
    std::fs::create_dir_all(dir).unwrap();
    let mut n = 0;
    for s in steps {
        let first = dir.join(format!("{n:05}.jpg"));
        std::fs::copy(&s.photo, &first).unwrap();
        for i in 1..s.secs * FPS {
            std::fs::hard_link(&first, dir.join(format!("{:05}.jpg", n + i))).unwrap();
        }
        n += s.secs * FPS;
    }
}

fn free_port() -> u16 {
    std::net::TcpListener::bind("127.0.0.1:0").unwrap().local_addr().unwrap().port()
}

fn stages(st: &Value, keys: &[&str]) -> String {
    let part = |k: &&str| st["stages_ms"][*k].as_f64().map(|v| format!("{k} {v:.0}"));
    keys.iter().filter_map(part).collect::<Vec<_>>().join(" · ")
}

fn median(mut v: Vec<f64>) -> f64 {
    v.retain(|x| x.is_finite());
    v.sort_by(f64::total_cmp);
    if v.is_empty() {
        f64::NAN
    } else {
        v[v.len() / 2]
    }
}

/// Cuántos objetos de la verdad tienen cerca un objeto del motor, y cuáles no.
fn score(truth: &[(String, [f64; 2])], table: &BTreeMap<String, [f64; 2]>) -> (usize, Vec<String>) {
    let near = |c: &[f64; 2]| table.values().any(|o| (o[0] - c[0]).hypot(o[1] - c[1]) < MATCH_MM);
    let missing: Vec<String> = truth.iter().filter(|(_, c)| !near(c)).map(|(l, _)| l.clone()).collect();
    (truth.len() - missing.len(), missing)
}

#[tokio::test(flavor = "multi_thread")]
#[ignore = "necesita PCX_FOTOS_DIR o PCX_FIXTURES_DIR (y PCX_MODELS_DIR para SAM)"]
async fn motor_real_con_fotos_de_la_mesa() {
    let tmp = tempfile::tempdir().unwrap();
    let env_dir = |k: &str| std::env::var_os(k).map(PathBuf::from);
    let sc = match (env_dir("PCX_FIXTURES_DIR"), env_dir("PCX_FOTOS_DIR")) {
        (Some(d), _) => fixtures(&d),
        (None, Some(d)) => fotos(&d, tmp.path()),
        (None, None) => {
            println!("sin PCX_FOTOS_DIR ni PCX_FIXTURES_DIR: salto");
            return;
        }
    };
    let frames = tmp.path().join("cuadros");
    replay_dir(&frames, &sc.steps);

    let with_sam = std::env::var("PCX_E2E_SAM").map_or(true, |v| v != "0");
    let port = free_port();
    let mut cmd = tokio::process::Command::new(env!("CARGO_BIN_EXE_pcx"));
    cmd.args(["motor", "--port", &port.to_string(), "--review-every-s", "5", "--camera"])
        .arg(format!("replay:{}", frames.display()))
        .arg("--dir")
        .arg(tmp.path().join("pcx"))
        .env_remove("PCX_SUPERVISOR_PID")
        .env_remove("PCX_MOTOR_PERCEPTOR")
        .stderr(Stdio::inherit())
        .kill_on_drop(true);
    if !with_sam {
        // sin pesos a la vista: ni PCX_MODELS_DIR ni ~/.pcx/models
        cmd.env_remove("PCX_MODELS_DIR").env("HOME", tmp.path());
    }
    let mut child = cmd.spawn().unwrap();

    let mut health = Value::Null;
    for _ in 0..200 {
        if let Ok(r) = reqwest::get(format!("http://127.0.0.1:{port}/v1/health")).await {
            health = serde_json::from_str(&r.text().await.unwrap()).unwrap();
            break;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    // el replay empieza a correr cuando el motor abre la cámara, después de cargar los modelos: más o menos ahora
    let born = Instant::now();
    println!("health: {health}");
    assert_eq!(health["ok"], true);
    let token = std::fs::read_to_string(tmp.path().join("pcx/motor.token")).unwrap();

    // la «web app»
    let mut req = format!("ws://127.0.0.1:{port}/v1/ws").into_client_request().unwrap();
    req.headers_mut().insert("origin", "https://pcx-gateway.onrender.com".parse().unwrap());
    let (mut ws, _) = tokio_tungstenite::connect_async(req).await.unwrap();
    let send = |v: Value| Message::Text(v.to_string().into());
    ws.send(send(json!({"type": "hello", "v": 0, "token": token.trim(), "session_id": "e2e", "app": "test"})))
        .await
        .unwrap();
    ws.send(send(json!({"type": "scene", "name": null}))).await.unwrap();
    ws.send(send(sc.calib.clone())).await.unwrap();

    let mut blank_at: Option<Instant> = None;
    // (hubo cambios, ciclo, percepción, captura, encoder de SAM)
    let mut cycles: Vec<(bool, f64, f64, f64, f64)> = Vec::new();
    let mut table: BTreeMap<String, [f64; 2]> = BTreeMap::new();
    let (mut removed, mut first_upsert, mut errors) = (0usize, 0usize, Vec::new());
    // de `blank` al `objects` de ese ciclo (lo que ve la persona) y esperas de los ciclos que siguieron a un cambio
    let (mut blank_to_objects, mut changed_in_cycle, mut settle_gaps) = (Vec::new(), false, Vec::new());
    let mut last_change_end: Option<Instant> = None;
    // fin de cada paso (desde `born`), para comparar con su verdad un instante antes
    let mut ends = Vec::new();
    let mut acc = 0.0;
    for s in &sc.steps {
        acc += s.secs as f64;
        ends.push(acc - 0.4);
    }
    let (mut step, mut hits, mut total) = (0usize, 0usize, 0usize);
    while born.elapsed().as_secs() < sc.run_s as u64 {
        let t = born.elapsed().as_secs_f64();
        if step < ends.len() && t >= ends[step] {
            let s = &sc.steps[step];
            if s.truth.is_empty() {
                println!("[{t:5.1} s] fin del paso {step} «{}»: {} objetos del motor", s.what, table.len());
            } else {
                let (ok, missing) = score(&s.truth, &table);
                hits += ok;
                total += s.truth.len();
                println!(
                    "[{t:5.1} s] fin del paso {step} «{}»: verdad {ok}/{} · objetos del motor {} · faltan {missing:?}",
                    s.what,
                    s.truth.len(),
                    table.len()
                );
            }
            step += 1;
        }
        let next = tokio::time::timeout(Duration::from_millis(200), ws.next()).await;
        let Ok(Some(Ok(Message::Text(text)))) = next else { continue };
        let v: Value = serde_json::from_str(text.as_str()).unwrap();
        match v["type"].as_str().unwrap_or("") {
            "welcome" => {
                println!("welcome: models {} · camera {} · scenes {}", v["models"], v["camera"], v["scenes"]);
                assert_eq!(v["models"]["sam"], with_sam, "{}", v["models"]);
                assert!(!v["models"]["note"].as_str().unwrap_or("").is_empty());
            }
            "blank" => {
                if let Some(end) = last_change_end.take() {
                    settle_gaps.push(end.elapsed().as_secs_f64() * 1e3);
                }
                blank_at = Some(Instant::now());
                ws.send(send(json!({"type": "blanked", "id": v["id"], "t_ns": 1}))).await.unwrap();
            }
            "unblank" => ws.send(send(json!({"type": "unblanked", "id": v["id"]}))).await.unwrap(),
            "objects" if v["rev"] != 0 => {
                let (up, rm) = (v["upsert"].as_array().unwrap(), v["remove"].as_array().unwrap());
                for o in up {
                    let poly = o["polygon_mm"].as_array().unwrap();
                    assert!((3..=64).contains(&poly.len()), "{o}");
                    for p in poly {
                        let (x, y) = (p[0].as_f64().unwrap(), p[1].as_f64().unwrap());
                        let inside =
                            (-5.0..=sc.surface[0] + 5.0).contains(&x) && (-5.0..=sc.surface[1] + 5.0).contains(&y);
                        assert!(inside, "polígono fuera de la superficie: {o}");
                    }
                    let c = [o["center_mm"][0].as_f64().unwrap(), o["center_mm"][1].as_f64().unwrap()];
                    table.insert(o["id"].as_str().unwrap().to_string(), c);
                }
                for id in rm {
                    table.remove(id.as_str().unwrap());
                }
                removed += rm.len();
                if first_upsert == 0 {
                    first_upsert = up.len();
                }
                changed_in_cycle = true;
                let ms = blank_at.map_or(f64::NAN, |b| b.elapsed().as_secs_f64() * 1e3);
                blank_to_objects.push(ms);
                let ids: Vec<&str> = up.iter().map(|o| o["id"].as_str().unwrap()).collect();
                println!(
                    "[{t:5.1} s] objects rev {} · upsert {} {:?} · remove {} · {ms:.0} ms desde blank · {} en la mesa",
                    v["rev"],
                    up.len(),
                    &ids[..ids.len().min(6)],
                    v["remove"],
                    table.len()
                );
            }
            "status" if v["state"] == "idle" && v["last_cycle_ms"].is_number() => {
                println!(
                    "[{t:5.1} s] ciclo {:.0} ms · {} | {}",
                    v["last_cycle_ms"].as_f64().unwrap(),
                    stages(&v, &["calm", "blank", "capture", "median", "perceive"]),
                    stages(&v, &["rectify", "cascade", "sam_encoder", "sam_decoder"]),
                );
                assert_eq!(v["models"]["sam"], with_sam);
                let p = |k: &str| v["stages_ms"][k].as_f64().unwrap_or(f64::NAN);
                let total = v["last_cycle_ms"].as_f64().unwrap();
                cycles.push((changed_in_cycle, total, p("perceive"), p("capture"), p("sam_encoder")));
                last_change_end = changed_in_cycle.then(Instant::now);
                changed_in_cycle = false;
                blank_at = None;
            }
            "error" => {
                println!("[{t:5.1} s] error {} · {}", v["code"], v["message"]);
                errors.push(v["code"].as_str().unwrap_or("").to_string());
            }
            _ => {}
        }
    }
    unsafe { libc::kill(child.id().unwrap() as i32, libc::SIGTERM) };
    let _ = tokio::time::timeout(Duration::from_secs(10), child.wait()).await;

    type Cycle = (bool, f64, f64, f64, f64);
    let pick =
        |changed: bool, f: fn(&Cycle) -> f64| median(cycles.iter().skip(1).filter(|c| c.0 == changed).map(f).collect());
    println!("\n== {} · {} ciclos ==", if with_sam { "con SAM" } else { "solo cascada" }, cycles.len());
    if let Some(c) = cycles.first() {
        println!("registro inicial ({first_upsert} objetos): ciclo {:.0} ms · percepción {:.0} ms", c.1, c.2);
    }
    println!("ciclo quieto (mediana): {:.0} ms · percepción {:.0} ms", pick(false, |c| c.1), pick(false, |c| c.2));
    println!("ciclo con cambio (mediana): {:.0} ms · percepción {:.0} ms", pick(true, |c| c.1), pick(true, |c| c.2));
    let with_enc: Vec<&Cycle> = cycles.iter().skip(1).filter(|c| c.4 > 1.0).collect();
    println!(
        "ciclo que usó el encoder de SAM (mediana, {} ciclos): {:.0} ms · percepción {:.0} ms · encoder {:.0} ms",
        with_enc.len(),
        median(with_enc.iter().map(|c| c.1).collect()),
        median(with_enc.iter().map(|c| c.2).collect()),
        median(with_enc.iter().map(|c| c.4).collect())
    );
    println!("captura de la foto limpia (mediana): {:.0} ms", median(cycles.iter().map(|c| c.3).collect()));
    println!("de `blank` a `objects` (mediana): {:.0} ms", median(blank_to_objects.clone()));
    let gaps = settle_gaps.len();
    println!("ciclo extra tras un cambio, espera (mediana): {:.0} ms · {gaps} veces", median(settle_gaps.clone()));
    println!("retirados: {removed} · errores: {errors:?}");
    if total > 0 {
        println!("verdad: {hits}/{total} objetos con un objeto del motor a menos de {MATCH_MM} mm, sumando los pasos");
    }

    assert!(first_upsert >= 3, "esperaba los objetos de la mesa en el primer `objects`: {first_upsert}");
    assert!(removed >= 1, "algo sale de la mesa: debería llegar algún `remove`");
    assert!(!settle_gaps.is_empty(), "tras un cambio el motor pide otro ciclo");
    assert!(median(settle_gaps) < 2500.0, "el ciclo extra llega ~1 s después del cambio, no con el periódico (5 s)");
}
