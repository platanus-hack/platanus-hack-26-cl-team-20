//! Fuentes con datos y pares falsos (sin cámaras): replay, mjpeg (servidor falso), phone (cliente WS falso) y
//! ZeroMQ PUSH → PULL. Puertos 9221–9224.

use pcx_capture::{open_source, run, Envelope, Event, MetaSink, Payload, RunConfig, SourceOpts, SourceSpec};
use pcx_shm::{PixelFormat, RingReader};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tokio::io::AsyncWriteExt;

fn testdata(p: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("testdata").join(p)
}

fn jpegs() -> Vec<Vec<u8>> {
    let mut v: Vec<_> = std::fs::read_dir(testdata("frames")).unwrap().map(|e| e.unwrap().path()).collect();
    v.sort();
    v.iter().map(|p| std::fs::read(p).unwrap()).collect()
}

type Log = Arc<Mutex<Vec<Envelope>>>;

fn collector() -> (Log, Vec<Box<dyn MetaSink>>) {
    let log: Log = Arc::default();
    let l2 = log.clone();
    (log, vec![Box::new(move |e: &Envelope| l2.lock().unwrap().push(e.clone()))])
}

fn frame_metas(log: &Log) -> Vec<pcx_capture::FrameMeta> {
    log.lock()
        .unwrap()
        .iter()
        .filter_map(|e| match &e.payload {
            Payload::Event(Event::FrameMeta(f)) => Some(f.clone()),
            _ => None,
        })
        .collect()
}

fn cfg(id: &str, size: (u32, u32), max: u64) -> RunConfig {
    RunConfig { size, max_frames: Some(max), node_id: "test".into(), ..RunConfig::new(id) }
}

fn cid(tag: &str) -> String {
    format!("{tag}{}", std::process::id() % 100_000)
}

#[tokio::test(flavor = "multi_thread")]
async fn replay_folder_real_time_writes_bgr_to_ring() {
    let id = cid("rf");
    let src = open_source(&SourceSpec::Replay(testdata("frames")), &SourceOpts { size: Some((640, 360)), fps: Some(30.0), ..Default::default() })
        .await
        .unwrap();
    let (log, mut sinks) = collector();
    let id2 = id.clone();
    // Lector en paralelo: toma un cuadro del anillo y lo compara con el JPEG decodificado (RGB → BGR).
    let reader = std::thread::spawn(move || {
        let mut r = RingReader::open(&id2, Duration::from_secs(5)).unwrap();
        let mut buf = Vec::new();
        let m = r.wait_newer(0, Duration::from_secs(5), &mut buf).unwrap().unwrap();
        (m, buf)
    });
    let t0 = Instant::now();
    let stats = run(cfg(&id, (640, 360), 8), src, &mut sinks, std::future::pending()).await.unwrap();
    let dt = t0.elapsed();
    assert_eq!(stats.frames, 8);
    assert!(dt >= Duration::from_millis(200), "replay no respetó 30 fps: {dt:?}");
    let fm = frame_metas(&log);
    assert_eq!(fm.iter().map(|f| f.seq).collect::<Vec<_>>(), (1..=8).collect::<Vec<_>>());
    assert!(fm.iter().all(|f| (f.w, f.h, f.camera_id.as_str()) == (640, 360, id.as_str())));
    let seg = pcx_shm::segment_name(&id).unwrap();
    assert!(fm.iter().all(|f| f.segment.as_deref() == Some(seg.as_str()) && f.stride == Some(640 * 3)));
    let all = log.lock().unwrap();
    assert!(matches!(&all[0].payload, Payload::Event(Event::DeviceChanged { device }) if device.status == pcx_capture::DeviceStatus::Online));
    assert!(matches!(&all.last().unwrap().payload, Payload::Event(Event::DeviceChanged { device }) if device.status == pcx_capture::DeviceStatus::Offline));
    assert!(all.windows(2).all(|w| w[1].seq == w[0].seq + 1) && all.iter().all(|e| e.v == 2 && e.source == format!("capture-{id}")));

    let (m, bgr) = reader.join().unwrap();
    assert_eq!((m.w, m.h, m.format), (640, 360, PixelFormat::Bgr8));
    let file = &jpegs()[((m.seq - 1) % 6) as usize];
    let rgb = image::load_from_memory(file).unwrap().into_rgb8();
    let mut expect = vec![0u8; bgr.len()];
    pcx_capture::frame::rgb_to_bgr(rgb.as_raw(), &mut expect);
    assert!(bgr == expect, "el cuadro del anillo no es el JPEG en BGR");
}

#[tokio::test(flavor = "multi_thread")]
async fn replay_mjpeg_file_loops_and_resizes() {
    let id = cid("rm");
    let opts = SourceOpts { size: Some((320, 180)), max_speed: true, ..Default::default() };
    let src = open_source(&SourceSpec::Replay(testdata("mesa.mjpeg")), &opts).await.unwrap();
    let (log, mut sinks) = collector();
    let stats = run(cfg(&id, (320, 180), 20), src, &mut sinks, std::future::pending()).await.unwrap();
    assert_eq!(stats.frames, 20); // 6 cuadros en bucle (o más por el «último gana»)
    assert!(frame_metas(&log).iter().all(|f| (f.w, f.h) == (320, 180)));
}

/// Servidor MJPEG falso: partes con y sin Content-Length, ~50 fps.
async fn fake_mjpeg_server(port: u16) {
    let listener = tokio::net::TcpListener::bind(("127.0.0.1", port)).await.unwrap();
    let frames = jpegs();
    tokio::spawn(async move {
        loop {
            let (mut s, _) = listener.accept().await.unwrap();
            let frames = frames.clone();
            tokio::spawn(async move {
                let mut req = [0u8; 1024];
                let _ = tokio::io::AsyncReadExt::read(&mut s, &mut req).await;
                let head = "HTTP/1.1 200 OK\r\nContent-Type: multipart/x-mixed-replace; boundary=frame\r\n\r\n";
                if s.write_all(head.as_bytes()).await.is_err() {
                    return;
                }
                for (i, f) in frames.iter().cycle().enumerate().take(400) {
                    let hdr = if i % 2 == 0 {
                        format!("--frame\r\nContent-Type: image/jpeg\r\nContent-Length: {}\r\n\r\n", f.len())
                    } else {
                        "--frame\r\nContent-Type: image/jpeg\r\n\r\n".to_string()
                    };
                    let mut part = hdr.into_bytes();
                    part.extend_from_slice(f);
                    part.extend_from_slice(b"\r\n");
                    if s.write_all(&part).await.is_err() {
                        return;
                    }
                    tokio::time::sleep(Duration::from_millis(20)).await;
                }
            });
        }
    });
}

#[tokio::test(flavor = "multi_thread")]
async fn mjpeg_against_fake_server() {
    fake_mjpeg_server(9222).await;
    let id = cid("mj");
    let spec: SourceSpec = "mjpeg:http://127.0.0.1:9222/cam.mjpg".parse().unwrap();
    let src = open_source(&spec, &SourceOpts { size: Some((640, 360)), ..Default::default() }).await.unwrap();
    let (log, mut sinks) = collector();
    let stop = tokio::time::sleep(Duration::from_secs(10));
    let stats = run(cfg(&id, (640, 360), 10), src, &mut sinks, stop).await.unwrap();
    assert_eq!(stats.frames, 10);
    assert_eq!(frame_metas(&log).len(), 10);
}

#[tokio::test(flavor = "multi_thread")]
async fn phone_page_and_fake_ws_client() {
    use futures_util::SinkExt;
    let id = cid("ph");
    let opts = SourceOpts { size: Some((640, 360)), bind_ip: [127, 0, 0, 1].into(), ..Default::default() };
    let src = open_source(&SourceSpec::Phone(9221), &opts).await.unwrap();
    assert!(src.info.as_deref().unwrap().contains("http://127.0.0.1:9221/"));
    let page = reqwest::get("http://127.0.0.1:9221/").await.unwrap().text().await.unwrap();
    assert!(page.contains("getUserMedia") && page.contains("const W=640"));
    let client = tokio::spawn(async move {
        let (mut ws, _) = tokio_tungstenite::connect_async("ws://127.0.0.1:9221/ws").await.unwrap();
        for f in jpegs().iter().cycle().take(40) {
            if ws.send(tokio_tungstenite::tungstenite::Message::Binary(f.clone().into())).await.is_err() {
                break;
            }
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
    });
    let (log, mut sinks) = collector();
    let stats = run(cfg(&id, (640, 360), 5), src, &mut sinks, tokio::time::sleep(Duration::from_secs(10))).await.unwrap();
    assert_eq!(stats.frames, 5);
    assert!(frame_metas(&log).iter().all(|f| (f.w, f.h) == (640, 360)));
    client.abort();
}

#[cfg(feature = "zmq")]
#[tokio::test(flavor = "multi_thread")]
async fn zmq_push_reaches_pull_as_msgpack_envelope() {
    use zeromq::{Socket, SocketRecv};
    let mut pull = zeromq::PullSocket::new();
    pull.bind("tcp://127.0.0.1:9224").await.unwrap();
    let id = cid("zq");
    let src = open_source(&SourceSpec::Replay(testdata("frames")), &SourceOpts { size: Some((320, 180)), fps: Some(60.0), ..Default::default() })
        .await
        .unwrap();
    let mut sinks: Vec<Box<dyn MetaSink>> = vec![Box::new(pcx_capture::ZmqPush::spawn("tcp://127.0.0.1:9224"))];
    let runner = tokio::spawn(async move { run(cfg(&id, (320, 180), 60), src, &mut sinks, std::future::pending()).await.unwrap() });
    let msg = tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            let m = pull.recv().await.unwrap();
            let env: Envelope = rmp_serde::from_slice(m.get(0).unwrap()).unwrap();
            if env.type_ == "frame_meta" {
                return env;
            }
        }
    })
    .await
    .expect("no llegó FrameMeta por ZeroMQ");
    assert_eq!((msg.v, msg.node_id.as_str()), (2, "test"));
    runner.abort();
}

/// Manual: requiere una webcam real. `PCX_CAMERA=0 cargo test -p pcx-capture --features uvc -- --ignored uvc`
#[cfg(feature = "uvc")]
#[ignore]
#[tokio::test(flavor = "multi_thread")]
async fn uvc_manual() {
    let idx: u32 = std::env::var("PCX_CAMERA").ok().and_then(|s| s.parse().ok()).unwrap_or(0);
    let src = open_source(&SourceSpec::Uvc(idx), &SourceOpts::default()).await.unwrap();
    let (log, mut sinks) = collector();
    let t0 = Instant::now();
    let stats = run(cfg("uvctest", (1280, 720), 60), src, &mut sinks, tokio::time::sleep(Duration::from_secs(20))).await.unwrap();
    eprintln!("uvc:{idx} {} cuadros en {:.1} s", stats.frames, t0.elapsed().as_secs_f64());
    assert_eq!(frame_metas(&log).len(), 60);
}

/// Cámara que deja de entregar (desconexión): `run` devuelve error para que el supervisor reinicie la captura.
#[tokio::test]
async fn camara_que_se_corta_es_error() {
    use pcx_capture::frame::{latest_box, Frame};
    let (tx, rx) = latest_box();
    let src = pcx_capture::RunningSource { name: "cam:prueba".into(), spec: SourceSpec::Cam("prueba".into()), rx, info: None, phone: None };
    let id = format!("cut{}", std::process::id());
    let cfg = RunConfig { size: (32, 24), verbose: false, ..RunConfig::new(&id) };
    let feeder = tokio::spawn(async move {
        for i in 0..3u64 {
            tx.put(Frame { img: image::RgbImage::new(32, 24), t_capture_ns: i + 1 });
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
        drop(tx); // «desenchufada»
    });
    let (_log, mut sinks) = collector();
    let r = tokio::time::timeout(Duration::from_secs(10), run(cfg, src, &mut sinks, std::future::pending())).await.unwrap();
    feeder.await.unwrap();
    let e = r.expect_err("debe ser error").to_string();
    assert!(e.contains("cámara desconectada"), "{e}");
}
