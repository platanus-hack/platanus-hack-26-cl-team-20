//! Foto limpia contra una cámara y un proyector simulados (reloj virtual: nada duerme ni abre hardware).

use std::cell::RefCell;
use std::collections::HashMap;
use std::rc::Rc;
use std::time::{Duration, Instant};

use pcx_perception::clean::*;
use pcx_shm::{PixelFormat, RingReader, RingWriter, SlotMeta};

const MS: u64 = 1_000_000;
const T0: u64 = 1_700_000_000_000 * MS;
const PERIOD: u64 = 1_000_000_000 / 30;
const W: usize = 192;
const H: usize = 108;

struct World {
    now: u64,
    phase: u64,
    next_k: u64,
    scene: Vec<u8>,
    proj: Vec<u8>,
    noise: f32,
    latency: u64,
    vsync: u64,
    exposure: u64,
    delivery: u64,
    /// Autoexposición: velocidad de ajuste por cuadro (0 = fija).
    ae_rate: f32,
    gain: f32,
    rng: u32,
    /// Intervalo negro en el reloj de presentación.
    blank: Option<(u64, u64)>,
    blanked_sent: bool,
    fail_blank: bool,
    /// Momento del pedido de `blank` y cambios de contenido relativos a él: (desde ms, hasta ms, factor de brillo).
    t_req: u64,
    content: Vec<(u64, u64, f32)>,
    /// Fracción de luz del proyector en cada cuadro entregado.
    lit: HashMap<u64, f32>,
}

fn mask() -> Mask {
    Mask::from_rect(W as u32, H as u32, 28, 16, 164, 92)
}

impl World {
    /// `proj_level`: brillo de la proyección (0 = nada proyectado).
    fn new(latency_ms: u64, phase_ms: u64, proj_level: f32) -> Rc<RefCell<World>> {
        let m = mask();
        let (mut scene, mut proj) = (vec![0u8; W * H * 3], vec![0u8; W * H * 3]);
        for y in 0..H {
            for x in 0..W {
                let i = (y * W + x) * 3;
                // Mesa con degradado y textura, más dos «objetos».
                let mut px = [40.0 + 50.0 * x as f32 / W as f32 + ((x * 7 + y * 13) % 11) as f32; 3];
                if (60..90).contains(&x) && (30..60).contains(&y) {
                    px = [150.0, 60.0, 40.0];
                }
                if (110..140).contains(&x) && (50..80).contains(&y) {
                    px = [30.0, 120.0, 160.0];
                }
                for c in 0..3 {
                    scene[i + c] = px[c] as u8;
                    if m.data[y * W + x] != 0 {
                        let stripes = if (x / 8 + y / 8) % 2 == 0 { 1.0 } else { 0.55 };
                        proj[i + c] = (proj_level * stripes * [1.0, 0.9, 0.7][c]) as u8;
                    }
                }
            }
        }
        Rc::new(RefCell::new(World {
            now: T0,
            phase: phase_ms * MS,
            next_k: 1,
            scene,
            proj,
            noise: 2.0,
            latency: latency_ms * MS,
            vsync: 8 * MS,
            exposure: 10 * MS,
            delivery: 3 * MS,
            ae_rate: 0.0,
            gain: 1.0,
            rng: 0x9E37_79B9 ^ (latency_ms as u32).wrapping_mul(2654435761).wrapping_add(phase_ms as u32 * 7919),
            blank: None,
            blanked_sent: false,
            fail_blank: false,
            t_req: u64::MAX,
            content: Vec::new(),
            lit: HashMap::new(),
        }))
    }

    /// Luz del proyector rebotada en la sala: también ilumina fuera de la máscara.
    fn with_bounce(world: Rc<RefCell<World>>, level: u8) -> Rc<RefCell<World>> {
        let m = mask();
        let mut w = world.borrow_mut();
        for (i, px) in w.proj.chunks_mut(3).enumerate() {
            if m.data[i] == 0 {
                px.fill(level);
            }
        }
        std::mem::drop(w);
        world
    }

    fn t_capture(&self, k: u64) -> u64 {
        T0 + self.phase + k * PERIOD
    }

    fn lit_fraction(&self, t: u64) -> f32 {
        let Some((s, e)) = self.blank else { return 1.0 };
        let (off_s, off_e) = (s + self.latency, e + self.latency);
        let (a, b) = (t - self.exposure, t);
        let overlap = b.min(off_e).saturating_sub(a.max(off_s));
        let content: f32 = self
            .content
            .iter()
            .filter(|c| t >= self.t_req + c.0 * MS && t < self.t_req + c.1 * MS)
            .map(|c| c.2)
            .product();
        content * (1.0 - overlap as f32 / self.exposure as f32)
    }

    fn render(&mut self, t: u64, buf: &mut Vec<u8>) -> f32 {
        let lit = self.lit_fraction(t);
        buf.clear();
        let mut raw_sum = 0.0f64;
        for i in 0..self.scene.len() {
            self.rng ^= self.rng << 13;
            self.rng ^= self.rng >> 17;
            self.rng ^= self.rng << 5;
            let n = ((self.rng & 0xff) + ((self.rng >> 8) & 0xff)) as f32 - 255.0;
            let raw = self.scene[i] as f32 + lit * self.proj[i] as f32;
            raw_sum += raw as f64;
            buf.push((raw * self.gain + n * self.noise / 104.0 + 0.5).clamp(0.0, 255.0) as u8);
        }
        if self.ae_rate > 0.0 {
            // La cámara busca la luminancia media que tenía con el proyector encendido.
            let lit_mean: f64 = self.scene.iter().zip(&self.proj).map(|(&s, &p)| s as f64 + p as f64).sum::<f64>();
            let want = ((lit_mean / raw_sum) as f32).clamp(1.0, 1.8);
            self.gain += self.ae_rate * (want - self.gain);
        }
        lit
    }
}

struct Cam(Rc<RefCell<World>>);
struct Ctl(Rc<RefCell<World>>);

impl FrameSource for Cam {
    fn now_ns(&self) -> u64 {
        self.0.borrow().now
    }
    fn next_frame(
        &mut self,
        after_seq: u64,
        timeout: Duration,
        buf: &mut Vec<u8>,
    ) -> Result<Option<SlotMeta>, CleanError> {
        let mut w = self.0.borrow_mut();
        if after_seq == 0 && w.next_k == 1 {
            // Primer cuadro: ya está en el anillo.
            w.now = w.t_capture(1) + w.delivery;
        }
        let k = w.next_k.max(after_seq + 1);
        let avail = w.t_capture(k) + w.delivery;
        if avail > w.now + timeout.as_nanos() as u64 {
            w.now += timeout.as_nanos() as u64;
            return Ok(None);
        }
        w.now = w.now.max(avail);
        w.next_k = k + 1;
        let t = w.t_capture(k);
        let lit = w.render(t, buf);
        w.lit.insert(k, lit);
        Ok(Some(SlotMeta {
            seq: k,
            t_capture_ns: t,
            w: W as u32,
            h: H as u32,
            stride: (W * 3) as u32,
            format: PixelFormat::Bgr8,
            slot: 0,
            gen: 0,
        }))
    }
}

impl BlankControl for Ctl {
    fn request_blank(&mut self, ms: u32) -> Result<String, String> {
        let mut w = self.0.borrow_mut();
        if w.fail_blank {
            return Err("core caído".into());
        }
        let s = w.now + w.vsync;
        w.t_req = w.now;
        w.blank = Some((s, s + ms as u64 * MS));
        Ok("sim-1".into())
    }
    fn poll_blanked(&mut self, request_id: &str) -> Option<BlankWindow> {
        assert_eq!(request_id, "sim-1");
        let mut w = self.0.borrow_mut();
        let (s, e) = w.blank?;
        if w.blanked_sent || w.now < e + 2 * MS {
            return None;
        }
        w.blanked_sent = true;
        Some(BlankWindow { t_start_ms: s as f64 / 1e6, t_end_ms: e as f64 / 1e6 })
    }
}

fn run(world: &Rc<RefCell<World>>, mask: Option<&Mask>, cfg: &CleanConfig) -> Result<CleanPhoto, CleanError> {
    capture_clean(&mut Cam(world.clone()), &mut Ctl(world.clone()), mask, cfg)
}

/// Error medio absoluto contra la escena sin proyección.
fn mae(photo: &CleanPhoto, world: &Rc<RefCell<World>>) -> f32 {
    let w = world.borrow();
    photo.data.iter().zip(&w.scene).map(|(&a, &b)| (a as f32 - b as f32).abs()).sum::<f32>() / w.scene.len() as f32
}

/// Igual, tras igualar la exposición global de la foto con la de la escena.
fn mae_sin_ganancia(photo: &CleanPhoto, world: &Rc<RefCell<World>>) -> f32 {
    let w = world.borrow();
    let sum = |v: &[u8]| v.iter().map(|&x| x as f32).sum::<f32>();
    let g = sum(&w.scene) / sum(&photo.data);
    photo.data.iter().zip(&w.scene).map(|(&a, &b)| (a as f32 * g - b as f32).abs()).sum::<f32>() / w.scene.len() as f32
}

/// Todos los cuadros usados son del apagón (a lo sumo un 3 % de luz en uno de borde).
fn assert_dark(photo: &CleanPhoto, world: &Rc<RefCell<World>>, ctx: &str) {
    let w = world.borrow();
    for s in &photo.seqs {
        assert!(w.lit[s] <= 0.03, "{ctx}: el cuadro {s} tenía {:.0} % de proyección", w.lit[s] * 100.0);
    }
    assert!(photo.seqs.iter().filter(|s| w.lit[s] > 0.0).count() <= 1, "{ctx}: más de un cuadro de borde");
}

#[test]
fn latencia_variable_siempre_elige_el_apagon() {
    let (m, cfg) = (mask(), CleanConfig::default());
    let mut peor = (0.0f64, 0.0f32);
    for latency in (100..=400).step_by(10) {
        for phase in [0, 7, 15, 23, 31] {
            let world = World::new(latency, phase, 150.0);
            let ctx = format!("latencia {latency} ms, fase {phase} ms");
            let photo = run(&world, Some(&m), &cfg).unwrap_or_else(|e| panic!("{ctx}: {e}"));
            assert_dark(&photo, &world, &ctx);
            let mt = &photo.metrics;
            assert_eq!(mt.selection, Selection::Luminance, "{ctx}");
            assert_eq!(mt.frames_used, 3, "{ctx}");
            assert!(mt.frame_spread < 4.0, "{ctx}: dispersión {}", mt.frame_spread);
            assert!(mt.luminance_drop > 60.0, "{ctx}: caída {}", mt.luminance_drop);
            let err = mae(&photo, &world);
            assert!(err < 1.5, "{ctx}: error {err}");
            // Tres cuadros a 30 fps después de que llega la oscuridad: latencia + ≤ 125 ms.
            let extra = mt.blank_to_photo_ms - mt.median_ms - latency as f64;
            assert!(extra <= 125.0, "{ctx}: {extra:.0} ms sobre la latencia");
            peor = (peor.0.max(extra), peor.1.max(err));
        }
    }
    println!("peor caso: latencia + {:.0} ms, error medio {:.2} niveles", peor.0, peor.1);
}

#[test]
fn bench_blank_a_foto_a_30_fps() {
    // De `blank` a foto ≤ 350 ms con latencias de hasta 225 ms; con la latencia medida (215–260 ms) el techo
    // físico a 30 fps es latencia + ~120 ms.
    let (m, cfg) = (mask(), CleanConfig::default());
    for (latency, limite) in [(100, 350.0), (200, 350.0), (225, 350.0), (260, 385.0)] {
        let mut peor = 0.0f64;
        for phase in 0..34 {
            let world = World::new(latency, phase, 150.0);
            let photo = run(&world, Some(&m), &cfg).unwrap();
            assert_dark(&photo, &world, "bench");
            peor = peor.max(photo.metrics.blank_to_photo_ms - photo.metrics.median_ms);
        }
        println!("latencia {latency} ms: blank → foto ≤ {peor:.0} ms (simulado, 30 fps)");
        assert!(peor <= limite, "latencia {latency}: {peor:.0} ms > {limite}");
    }
}

#[test]
fn sin_mascara_tambien_funciona() {
    for latency in [100, 240, 400] {
        let world = World::new(latency, 11, 150.0);
        let photo = run(&world, None, &CleanConfig::default()).unwrap();
        assert_dark(&photo, &world, "sin máscara");
        assert!(mae(&photo, &world) < 1.5);
    }
}

#[test]
fn autoexposicion() {
    let m = mask();
    let igualar = CleanConfig { compensate_exposure: true, ..Default::default() };
    for latency in [120, 240, 380] {
        for (mask, cfg, bounce) in
            [(Some(&m), CleanConfig::default(), 0), (Some(&m), igualar.clone(), 15), (None, CleanConfig::default(), 0)]
        {
            let world = World::with_bounce(World::new(latency, 5, 150.0), bounce);
            world.borrow_mut().ae_rate = 0.15;
            let ctx = format!("AE, latencia {latency}, máscara {}, rebote {bounce}", mask.is_some());
            let photo = run(&world, mask, &cfg).unwrap_or_else(|e| panic!("{ctx}: {e}"));
            assert_dark(&photo, &world, &ctx);
            // La foto queda con la ganancia que tenía la cámara en el apagón: misma escena salvo ese factor.
            let err = mae_sin_ganancia(&photo, &world);
            assert!(err < 3.0, "{ctx}: error {err} (sin ganancia)");
        }
    }
}

#[test]
fn rebote_en_la_referencia_no_aclara_la_foto() {
    // Exposición fija; al apagar el proyector la zona de referencia también pierde luz (rebote en la sala). La foto
    // tiene que salir al nivel real de la escena, con o sin igualación de exposición.
    let m = mask();
    for compensate_exposure in [false, true] {
        for latency in [120, 240, 380] {
            let world = World::with_bounce(World::new(latency, 5, 150.0), 15);
            let cfg = CleanConfig { compensate_exposure, ..Default::default() };
            let photo = run(&world, Some(&m), &cfg).unwrap();
            assert_dark(&photo, &world, "rebote");
            let err = mae(&photo, &world);
            assert!(err < 1.5, "rebote, latencia {latency}, igualar {compensate_exposure}: error {err}");
        }
    }
}

#[test]
fn contenido_que_se_atenua_no_es_apagon() {
    // Una animación termina después del pedido: el área proyectada baja ~13 niveles y se queda ahí. No es el apagón.
    let (m, cfg) = (mask(), CleanConfig::default());
    for dim_at in [20, 60, 120, 200] {
        for latency in [240, 400, 500] {
            let world = World::new(latency, 4, 150.0);
            world.borrow_mut().content.push((dim_at, u64::MAX / MS / 2, 0.87));
            let ctx = format!("atenuación a los {dim_at} ms, latencia {latency}");
            let photo = run(&world, Some(&m), &cfg).unwrap_or_else(|e| panic!("{ctx}: {e}"));
            assert_dark(&photo, &world, &ctx);
            assert!(
                photo.metrics.first_dark_ms >= latency as f64,
                "{ctx}: primer cuadro a {} ms",
                photo.metrics.first_dark_ms
            );
            assert!(mae(&photo, &world) < 1.5, "{ctx}");
        }
        // Y si el apagón nunca se ve, la atenuación sola no alcanza para una foto.
        let world = World::new(900, 4, 150.0);
        world.borrow_mut().content.push((dim_at, u64::MAX / MS / 2, 0.87));
        let err = run(&world, Some(&m), &cfg).unwrap_err();
        assert!(matches!(err, CleanError::BlackoutNotSeen { .. }), "atenuación a los {dim_at} ms: {err}");
    }
}

#[test]
fn parpadeo_de_un_cuadro_no_aborta() {
    // Un cuadro más oscuro antes del apagón real (leve o profundo): se sigue leyendo hasta la meseta de verdad.
    let (m, cfg) = (mask(), CleanConfig::default());
    for factor in [0.85, 0.3, 0.05] {
        for at in [60, 100, 150] {
            let world = World::new(240, 4, 150.0);
            world.borrow_mut().content.push((at, at + 34, factor));
            let ctx = format!("parpadeo ×{factor} a los {at} ms");
            let photo = run(&world, Some(&m), &cfg).unwrap_or_else(|e| panic!("{ctx}: {e}"));
            assert_dark(&photo, &world, &ctx);
            assert!(mae(&photo, &world) < 1.5, "{ctx}");
        }
    }
}

#[test]
fn foto_a_rgb() {
    let world = World::new(240, 0, 150.0);
    let photo = run(&world, Some(&mask()), &CleanConfig::default()).unwrap();
    let (bgr, n) = (photo.data.clone(), W * H * 3);
    let rgb = photo.into_rgb();
    assert_eq!(rgb.len(), n);
    assert!(bgr.chunks(3).zip(rgb.chunks(3)).all(|(a, b)| a[0] == b[2] && a[1] == b[1] && a[2] == b[0]));
}

#[test]
fn latencia_mayor_que_la_ventana_es_error() {
    let (m, cfg) = (mask(), CleanConfig::default());
    let world = World::new(900, 3, 150.0);
    let err = run(&world, Some(&m), &cfg).unwrap_err();
    assert!(matches!(err, CleanError::BlackoutNotSeen { .. }), "{err}");
    assert!(err.to_string().contains("no se vio el apagón"), "{err}");

    // Cerca del borde de la ventana: o hay foto limpia o hay error, nunca foto contaminada.
    for latency in (410..=900).step_by(10) {
        let world = World::new(latency, 9, 150.0);
        match run(&world, Some(&m), &cfg) {
            Ok(photo) => {
                assert_dark(&photo, &world, &format!("latencia {latency}"));
                assert!(mae(&photo, &world) < 1.5);
            }
            Err(e) => assert!(matches!(e, CleanError::BlackoutNotSeen { .. } | CleanError::TooFewFrames { .. }), "{e}"),
        }
    }
}

#[test]
fn apagon_mas_corto_que_tres_cuadros_es_error() {
    let cfg = CleanConfig { blank_ms: 60, ..Default::default() };
    let world = World::new(240, 4, 150.0);
    let err = run(&world, Some(&mask()), &cfg).unwrap_err();
    assert!(matches!(err, CleanError::TooFewFrames { .. }), "{err}");
}

#[test]
fn caida_ambigua_usa_los_tiempos() {
    // Proyección tenue: la caída (~4 niveles) no alcanza para decidir sola.
    let (m, cfg) = (mask(), CleanConfig::default());
    for latency in [215, 240, 260] {
        let world = World::new(latency, 6, 6.0);
        let photo = run(&world, Some(&m), &cfg).unwrap();
        assert_eq!(photo.metrics.selection, Selection::Times);
        assert!(photo.metrics.blanked.is_some());
        assert_dark(&photo, &world, "ambigua");
        assert!(mae(&photo, &world) < 1.5);
    }
}

#[test]
fn sin_proyeccion_falla_salvo_que_se_asuma_oscuro() {
    let m = mask();
    let world = World::new(240, 6, 0.0);
    assert!(matches!(run(&world, Some(&m), &CleanConfig::default()), Err(CleanError::BlackoutNotSeen { .. })));

    let world = World::new(240, 6, 0.0);
    let cfg = CleanConfig { assume_dark_when_flat: true, ..Default::default() };
    let photo = run(&world, Some(&m), &cfg).unwrap();
    assert_eq!(photo.metrics.selection, Selection::Times);
    assert!(mae(&photo, &world) < 1.5);
}

#[test]
fn errores_de_entrada() {
    let world = World::new(240, 0, 150.0);
    world.borrow_mut().fail_blank = true;
    assert!(matches!(run(&world, None, &CleanConfig::default()), Err(CleanError::Blank(_))));

    let world = World::new(240, 0, 150.0);
    let chica = Mask::from_rect(10, 10, 0, 0, 5, 5);
    assert!(matches!(run(&world, Some(&chica), &CleanConfig::default()), Err(CleanError::MaskSize { .. })));
}

#[test]
fn mediana_por_pixel() {
    let f: Vec<Vec<u8>> = vec![vec![10, 200, 7], vec![30, 0, 7], vec![20, 100, 9], vec![90, 50, 1], vec![15, 60, 8]];
    let refs = |n: usize| f[..n].iter().map(|v| v.as_slice()).collect::<Vec<_>>();
    assert_eq!(median_stack(&refs(1)), vec![10, 200, 7]);
    assert_eq!(median_stack(&refs(3)), vec![20, 100, 7]);
    assert_eq!(median_stack(&refs(4)), vec![25, 75, 7]);
    assert_eq!(median_stack(&refs(5)), vec![20, 60, 7]);
}

/// Depende del reloj de pared: `cargo test -p pcx-perception -- --ignored --nocapture` en una máquina libre.
#[test]
#[ignore = "benchmark de reloj de pared"]
fn bench_mediana_720p() {
    let n = 1280 * 720 * 3;
    let frames: Vec<Vec<u8>> = (0..3u32)
        .map(|k| (0..n as u32).map(|i| (i.wrapping_mul(2654435761).wrapping_add(k * 977) >> 13) as u8).collect())
        .collect();
    let refs: Vec<&[u8]> = frames.iter().map(|v| v.as_slice()).collect();
    let mut best = f64::INFINITY;
    for _ in 0..7 {
        let t = Instant::now();
        let out = median_stack(&refs);
        best = best.min(t.elapsed().as_secs_f64() * 1e3);
        assert_eq!(out.len(), n);
    }
    println!("mediana de 3 cuadros 1280×720×3: {best:.2} ms (mejor de 7, perfil de test)");
    assert!(best < 15.0, "mediana {best:.2} ms");
}

#[test]
fn ring_reader_es_frame_source() {
    let id = format!("cl{}", std::process::id() % 100_000);
    let mut wr = RingWriter::create(&id, 8, 4, 3, 4).unwrap();
    let mut rd = RingReader::open(&id, Duration::from_secs(1)).unwrap();
    let t = FrameSource::now_ns(&rd);
    wr.write(&[7u8; 96], 8, 4, PixelFormat::Bgr8, Some(t)).unwrap();
    let mut buf = Vec::new();
    let meta = rd.next_frame(0, Duration::from_millis(50), &mut buf).unwrap().unwrap();
    assert_eq!((meta.seq, meta.t_capture_ns, buf.len()), (1, t, 96));
    assert!(rd.next_frame(1, Duration::from_millis(5), &mut buf).unwrap().is_none());
}
