//! Demo en bucle: (a) viga trazada con luz · (b) barrido que revela los planos · (c) foco que sigue la mano.
//! Core falso por WS (escenas completas a ~30 Hz) + pcx-hands sobre el anillo pcx.cam.c920 en el mismo proceso.

use std::collections::{HashMap, VecDeque};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use pcx_hands::{EngineConfig, Frame, FrameMeta, HandsEngine, PixelFormat};
use serde_json::{json, Value};
use tungstenite::Message;

use crate::ws::{now_ms, now_ns, Ws, PX_PER_MM, SURF_H, SURF_W};

const CW: usize = 1280;
const CH: usize = 720;
const CICLO_S: f64 = 22.0;
const FASE_A_S: f64 = 13.0;
const BARRIDO_S: f64 = 6.5;

fn circle(c: [f64; 2], r: f64, n: usize) -> Value {
    json!((0..n).map(|i| {
        let t = i as f64 / n as f64 * std::f64::consts::TAU;
        [c[0] + r * t.cos(), c[1] + r * t.sin()]
    }).collect::<Vec<_>>())
}

/// Línea de 2 puntos → polígono de 3 (el contrato exige ≥ 3 vértices).
fn line3(l: &Value) -> Value {
    let (a, b) = (&l[0], &l[1]);
    let m = |i: usize| (a[i].as_f64().unwrap() + b[i].as_f64().unwrap()) / 2.0;
    json!([a, [m(0), m(1)], b])
}

struct Map {
    inv: Vec<f32>,
    h: [f64; 9],
}

impl Map {
    fn load(dir: &Path, h: [f64; 9]) -> Self {
        let b = std::fs::read(dir.join("cam2proj.bin")).expect("cam2proj.bin");
        let inv = b.chunks_exact(4).map(|c| f32::from_le_bytes(c.try_into().unwrap())).collect();
        Self { inv, h }
    }
    /// px de cámara → mm del lienzo (LUT invertida; si no hay dato, homografía global).
    fn cam_to_mm(&self, x: f64, y: f64) -> Option<[f64; 2]> {
        let (xi, yi) = (x.round() as i64, y.round() as i64);
        if xi >= 0 && yi >= 0 && (xi as usize) < CW && (yi as usize) < CH {
            let k = (yi as usize * CW + xi as usize) * 2;
            let (u, v) = (self.inv[k], self.inv[k + 1]);
            if u.is_finite() && v.is_finite() {
                return Some([u as f64 / PX_PER_MM, v as f64 / PX_PER_MM]);
            }
        }
        let h = &self.h;
        let w = h[6] * x + h[7] * y + h[8];
        if w.abs() < 1e-9 {
            return None;
        }
        let (u, v) = ((h[0] * x + h[1] * y + h[2]) / w, (h[3] * x + h[4] * y + h[5]) / w);
        // Solo dentro del lienzo del proyector (revisión de Codex: el respaldo no debe dar focos fuera de 1920×1080).
        (u >= 0.0 && v >= 0.0 && u < 1920.0 && v < 1080.0).then_some([u / PX_PER_MM, v / PX_PER_MM])
    }
}

fn pct(v: &[f64], p: f64) -> f64 {
    if v.is_empty() {
        return f64::NAN;
    }
    let mut s = v.to_vec();
    s.sort_by(|a, b| a.partial_cmp(b).unwrap());
    s[((s.len() - 1) as f64 * p).round() as usize]
}

pub fn run(port: u16, dir: &Path, logs: &Path) {
    let d: Value = serde_json::from_str(&std::fs::read_to_string(dir.join("demo.json")).unwrap()).unwrap();
    let h: Vec<f64> = d["homografia_cam_proj"].as_array().unwrap().iter().map(|v| v.as_f64().unwrap()).collect();
    let map = Map::load(dir, h.try_into().unwrap());
    let asset = |n: &str| -> String { dir.join(n).display().to_string() };
    let mut eng = HandsEngine::from_cache(EngineConfig::default()).expect("pcx-hands");
    let mut ring = pcx_shm::RingReader::open("c920", Duration::from_secs(10)).expect("anillo c920");
    let mut buf = Vec::new();
    let mut last_seq = ring.latest_seq();
    let mut lat_csv = std::fs::File::create(logs.join("latencia_mano.csv")).unwrap();
    writeln!(lat_csv, "rev,t_capture_ms,t_send_ms,t_present_ms,captura_a_present_ms,proceso_ms").unwrap();
    let fase_path: PathBuf = logs.join("fase.txt");
    let t0 = Instant::now();
    let mut trail: VecDeque<[f64; 2]> = VecDeque::new();
    let mut rev_cap: HashMap<i64, (f64, f64, f64)> = HashMap::new();
    let (mut lats, mut last_lat): (Vec<f64>, f64) = (vec![], f64::NAN);
    let mut hands_mm: Vec<([f64; 2], [f64; 2])> = vec![];
    let mut last_hand_t = Instant::now() - Duration::from_secs(10);
    let mut cap_ms = 0.0;
    let mut proc_ms = 0.0;
    let mut fase_prev = String::new();
    let mut t_report = Instant::now();
    let mut n_prev = 0usize;
    'outer: loop {
        let mut ws = Ws::accept(port);
        loop {
            // 1) cuadro de cámara → manos
            match ring.wait_newer(last_seq, Duration::from_millis(40), &mut buf) {
                Ok(Some(m)) => {
                    last_seq = m.seq;
                    let fr = Frame::packed(&buf, m.w as usize, m.h as usize, PixelFormat::Bgr8).unwrap();
                    let meta = FrameMeta {
                        camera_id: "c920".into(),
                        seq: m.seq as i64,
                        t_capture_ns: m.t_capture_ns as i64,
                        w: m.w as i64,
                        h: m.h as i64,
                        segment: Some("pcx.cam.c920".into()),
                        format: Default::default(),
                        stride: None,
                    };
                    if let Ok(o) = eng.process(&fr, &meta) {
                        proc_ms = o.total_ms;
                        cap_ms = m.t_capture_ns as f64 / 1e6;
                        hands_mm.clear();
                        // La LUT invertida es de 1280×720: con otro tamaño de cuadro el mapeo no vale.
                        let tracked: &[_] = if m.w as usize == CW && m.h as usize == CH { &o.tracked } else { &[] };
                        for t in tracked {
                            let p = t.xy();
                            let palm = [0, 5, 9, 13, 17].iter().fold([0.0, 0.0], |a, &i| [a[0] + p[i][0] / 5.0, a[1] + p[i][1] / 5.0]);
                            if let (Some(pm), Some(tm)) = (map.cam_to_mm(palm[0], palm[1]), map.cam_to_mm(p[8][0], p[8][1])) {
                                hands_mm.push((pm, tm));
                            }
                        }
                        if o.tracked.len() != n_prev {
                            eprintln!("[demo] {} manos (seq {}) → {} con posición en el techo", o.tracked.len(), m.seq, hands_mm.len());
                            n_prev = o.tracked.len();
                        }
                        if let Some((_, tip)) = hands_mm.first() {
                            trail.push_front(*tip);
                            trail.truncate(12);
                            last_hand_t = Instant::now();
                        } else if trail.len() > 0 {
                            trail.pop_back();
                        }
                    }
                }
                Ok(None) => {}
                Err(e) => {
                    eprintln!("[demo] anillo: {e}; reabro");
                    std::thread::sleep(Duration::from_millis(500));
                    if let Ok(r) = pcx_shm::RingReader::open("c920", Duration::from_secs(5)) {
                        ring = r;
                        last_seq = ring.latest_seq();
                    }
                    continue;
                }
            }
            // 2) presented → latencia mano→luz (captura → presentación)
            while let Ok(msg) = ws.sock.read() {
                if let Message::Text(t) = msg {
                    if let Ok(v) = serde_json::from_str::<Value>(&t) {
                        if v["type"] == "presented" {
                            let (r, tp) = (v["rev"].as_i64().unwrap_or(0), v["t_present_ms"].as_f64().unwrap_or(0.0));
                            if let Some((tc, ts, pm)) = rev_cap.remove(&r) {
                                last_lat = tp - tc;
                                lats.push(last_lat);
                                writeln!(lat_csv, "{r},{tc:.3},{ts:.3},{tp:.3},{last_lat:.2},{pm:.2}").unwrap();
                            }
                            rev_cap.retain(|k, _| *k > r);
                        }
                    }
                }
            }
            // 3) escena = fase (a | b) + manos (c)
            let ts = t0.elapsed().as_secs_f64();
            let tc = ts % CICLO_S;
            let mut w: Vec<Value> = vec![];
            let fase = if tc < FASE_A_S { "a_viga" } else { "b_barrido" };
            if fase != fase_prev {
                let _ = std::fs::write(&fase_path, format!("{fase} {:.3}\n", now_ms()));
                fase_prev = fase.to_string();
            }
            let mano = last_hand_t.elapsed() < Duration::from_millis(400);
            if fase == "a_viga" {
                let viga = &d["viga_mm"];
                w.push(json!({"type": "effect", "id": "viga-glow", "shader": "halo_glow", "polygon_mm": viga, "z": 0,
                    "params": {"pulse_hz": 0.7, "width_mm": 3.0, "glow_mm": 16.0, "intensity": 1.3, "margin_mm": 26.0, "color": [1.0, 0.72, 0.1]}}));
                w.push(json!({"type": "effect", "id": "viga-marco", "shader": "marco_respira", "polygon_mm": viga, "z": 1,
                    "params": {"band_mm": 7.0, "speed": 0.45, "breath_hz": 0.5, "tail": 0.7, "color": "#FFC23A"}}));
                w.push(json!({"type": "halo", "id": "viga-izq", "polygon_mm": line3(&d["viga_izq_mm"]), "color": "#FFE08A", "width_mm": 2.0, "pulse_hz": 0.0, "z": 2}));
                w.push(json!({"type": "halo", "id": "viga-der", "polygon_mm": line3(&d["viga_der_mm"]), "color": "#FFE08A", "width_mm": 2.0, "pulse_hz": 0.0, "z": 2}));
                w.push(json!({"type": "label", "id": "viga-txt", "anchor": {"point_mm": [215.0, 28.0]},
                    "text": format!("{}\n{}", d["texto_viga"].as_str().unwrap(), d["texto_viga2"].as_str().unwrap()),
                    "size_mm": 11.0, "color": "#FFD27A", "z": 3}));
                if let Some(r) = d.get("rejilla") {
                    let c = [r["centro_mm"][0].as_f64().unwrap(), r["centro_mm"][1].as_f64().unwrap()];
                    let rad = r["radio_mm"].as_f64().unwrap();
                    w.push(json!({"type": "effect", "id": "rejilla-ondas", "shader": "ripple", "polygon_mm": circle(c, rad * 0.95, 40), "z": 0,
                        "params": {"speed": 0.5, "wavelength_mm": 16.0, "color": [0.2, 0.85, 1.0]}}));
                    w.push(json!({"type": "halo", "id": "rejilla", "polygon_mm": circle(c, rad, 40), "color": "#33D6FF", "width_mm": 2.5, "pulse_hz": 0.4, "z": 2}));
                    w.push(json!({"type": "label", "id": "rejilla-txt", "anchor": {"point_mm": [c[0] + rad * 0.75, c[1] - rad - 4.0]},
                        "text": "rejilla de ventilación", "size_mm": 8.0, "color": "#7FE7FF", "z": 3}));
                }
                if !mano {
                    w.push(json!({"type": "label", "id": "hint", "anchor": {"point_mm": [230.0, 210.0]},
                        "text": "¡Pon la mano bajo la cámara y la luz te sigue!", "size_mm": 9.0, "color": "#FFFFFF", "z": 3}));
                }
            } else {
                let tb = tc - FASE_A_S;
                let f = (tb / BARRIDO_S).min(1.0);
                let k = (f * 20.0).floor() as usize;
                w.push(json!({"type": "image", "id": "revela", "rect_mm": [0, 0, SURF_W, SURF_H], "src": asset(&format!("revela_{k:02}.png")), "opacity": 1, "z": 0}));
                if f < 1.0 {
                    let x = f * SURF_W;
                    w.push(json!({"type": "image", "id": "barra", "rect_mm": [x - 14.0, 0, 28.0, SURF_H], "src": asset("barra.png"), "opacity": 1, "z": 1}));
                    let lx = if x < SURF_W - 230.0 { x + 18.0 } else { x - 230.0 };
                    w.push(json!({"type": "label", "id": "scan-txt", "anchor": {"point_mm": [lx, 14.0]},
                        "text": format!("escaneando geometría… {:.0} %", f * 100.0), "size_mm": 9.0, "color": "#FFFFFF", "z": 2}));
                } else {
                    let n = d["planos"].as_array().unwrap().len();
                    w.push(json!({"type": "label", "id": "scan-txt", "anchor": {"point_mm": [215.0, 28.0]},
                        "text": format!("{n} planos · viga de {:.0} px · error por plano {}", d["viga_ancho_px"].as_f64().unwrap(),
                            d["texto_viga2"].as_str().unwrap().split("por plano: ").nth(1).unwrap_or("")),
                        "size_mm": 10.0, "color": "#FFFFFF", "z": 2}));
                }
            }
            // (c) manos: siempre encima
            if mano {
                for (i, (palm, tip)) in hands_mm.iter().enumerate() {
                    w.push(json!({"type": "effect", "id": format!("foco-glow-{i}"), "shader": "halo_glow", "polygon_mm": circle(*palm, 26.0, 28), "z": 10,
                        "params": {"pulse_hz": 2.0, "width_mm": 5.0, "glow_mm": 22.0, "intensity": 1.6, "margin_mm": 30.0, "color": [1.0, 1.0, 1.0]}}));
                    w.push(json!({"type": "effect", "id": format!("foco-ondas-{i}"), "shader": "ripple", "polygon_mm": circle(*palm, 24.0, 28), "z": 9,
                        "params": {"speed": 1.6, "wavelength_mm": 8.0, "color": [1.0, 0.95, 0.6]}}));
                    w.push(json!({"type": "halo", "id": format!("punta-{i}"), "polygon_mm": circle(*tip, 7.0, 16), "color": "#FFFFFF", "width_mm": 5.0, "pulse_hz": 0.0, "z": 11}));
                    if i == 0 && last_lat.is_finite() {
                        w.push(json!({"type": "label", "id": "mano-txt", "anchor": {"point_mm": [palm[0] + 32.0, palm[1] - 6.0]},
                            "text": format!("mano · {:.0} ms", last_lat), "size_mm": 8.0, "color": "#FFFFFF", "z": 12}));
                    }
                }
                for (j, p) in trail.iter().enumerate().skip(1) {
                    let a = (220.0 * (1.0 - j as f64 / 12.0)) as u8;
                    w.push(json!({"type": "halo", "id": format!("estela-{j}"), "polygon_mm": circle(*p, 4.0 + j as f64 * 0.8, 12),
                        "color": format!("#00E5FF{a:02X}"), "width_mm": 3.0, "pulse_hz": 0.0, "z": 8}));
                }
            }
            ws.rev += 1;
            let msg = json!({"type": "scene", "rev": ws.rev, "t_send_ms": now_ms(), "surface": {"width_mm": SURF_W, "height_mm": SURF_H},
                "transforms": [{"from_frame": "table_mm", "to_frame": "proj:main", "kind": "homography",
                                "matrix": [PX_PER_MM, 0, 0, 0, PX_PER_MM, 0, 0, 0, 1], "version": 1}], "widgets": w});
            if mano {
                rev_cap.insert(ws.rev, (cap_ms, now_ms(), proc_ms));
            }
            if ws.sock.send(Message::text(msg.to_string())).is_err() {
                eprintln!("[demo] renderer desconectado; espero reconexión");
                continue 'outer;
            }
            if t_report.elapsed() > Duration::from_secs(10) && !lats.is_empty() {
                eprintln!("[demo] {} t={:.0}s mano→presentación p50 {:.1} p95 {:.1} ms (n={})", fase, ts, pct(&lats, 0.5), pct(&lats, 0.95), lats.len());
                t_report = Instant::now();
            }
            let _ = now_ns;
        }
    }
}
