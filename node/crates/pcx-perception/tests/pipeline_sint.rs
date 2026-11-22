//! El pipeline sobre escenas sintéticas, sin modelos ni red: ids estables, histéresis, objetos finos, lente y el
//! contrato con SAM (caja sola, un encoder por foto) con un segmentador simulado.
mod common;

use common::{calib_plana, escena, Cosa};
use image::RgbImage;
use pcx_objects::models::{BoxErr, SamOutput, SamPoint, Segmenter};
use pcx_perception::pipeline::{Pipeline, PipelineConfig};
use pcx_perception::pipeline_api::{CalibIn, Lens, ObjectOut, PerceptionOut, Perceptor};
use std::sync::{Arc, Mutex};

const W: u32 = 800;
const H: u32 = 450;

fn calib() -> CalibIn {
    calib_plana((W, H), (W as f64, H as f64), 1.0, (0.0, 0.0), None)
}

fn pipeline() -> Pipeline {
    Pipeline::cascade_only(PipelineConfig::default())
}

fn tarjeta() -> Cosa {
    Cosa::caja(200.0, 150.0, 55.0, 35.0, [200, 60, 60]).con_textura(7)
}
fn disco() -> Cosa {
    Cosa::disco(520.0, 300.0, 32.0, [40, 90, 210])
}
/// 5 mm de ancho y 110 de largo, en diagonal: lo que la apertura 5×5 de la cascada original borraba.
fn cuchara() -> Cosa {
    Cosa::caja(620.0, 110.0, 55.0, 2.5, [225, 228, 235]).girada(35.0)
}

fn foto(cosas: &[Cosa], seed: u64) -> RgbImage {
    escena(W, H, cosas, 0, seed)
}

fn cerca(o: &ObjectOut, x: f64, y: f64, tol: f64) -> bool {
    (o.center_mm[0] - x).hypot(o.center_mm[1] - y) <= tol
}

fn el_de(out: &PerceptionOut, x: f64, y: f64) -> &ObjectOut {
    out.upsert.iter().find(|o| cerca(o, x, y, 6.0)).unwrap_or_else(|| {
        panic!("nadie cerca de ({x}, {y}): {:?}", out.upsert.iter().map(|o| (&o.id, o.center_mm)).collect::<Vec<_>>())
    })
}

/// Registro con los tres objetos; la cuchara (fina) se confirma en la segunda foto si quedó en espera.
fn registrar(p: &mut Pipeline) -> (String, String, String) {
    let cosas = [tarjeta(), disco(), cuchara()];
    let mut vistos: Vec<ObjectOut> = p.on_clean(&foto(&cosas, 1), &calib(), 1).unwrap().upsert;
    vistos.extend(p.on_clean(&foto(&cosas, 2), &calib(), 2).unwrap().upsert);
    let id = |x: f64, y: f64| vistos.iter().find(|o| cerca(o, x, y, 6.0)).expect("objeto registrado").id.clone();
    (id(200.0, 150.0), id(520.0, 300.0), id(620.0, 110.0))
}

#[test]
fn registra_lo_que_hay_y_no_borra_la_cuchara() {
    let mut p = pipeline();
    assert!(!p.has_sam() && p.sam_status().contains("solo la cascada"));
    let (t, d, c) = registrar(&mut p);
    assert!(t != d && d != c && t != c);
    let snap = p.snapshot();
    assert_eq!(snap.len(), 3, "{:?}", p.log());
    for o in &snap {
        assert!(o.polygon_mm.len() >= 3 && o.polygon_mm.len() <= 64);
        assert!(o.label.starts_with("objeto-") && o.id.starts_with("obj-"));
        assert!(o.polygon_mm.iter().all(|q| q[0] >= -1.0 && q[0] <= 801.0 && q[1] >= -1.0 && q[1] <= 451.0));
    }
    // la cuchara conserva su largo: el polígono mide más de 90 mm de punta a punta
    let poly = &snap.iter().find(|o| o.id == c).unwrap().polygon_mm;
    let largo =
        poly.iter().flat_map(|a| poly.iter().map(move |b| (a[0] - b[0]).hypot(a[1] - b[1]))).fold(0.0, f64::max);
    assert!(largo > 90.0, "la cuchara quedó de {largo:.0} mm");
}

#[test]
fn escena_quieta_no_publica_nada_aunque_cambie_la_luz() {
    let mut p = pipeline();
    registrar(&mut p);
    let cosas = [tarjeta(), disco(), cuchara()];
    for (k, luz) in [0, 6, -5, 3].into_iter().enumerate() {
        let out = p.on_clean(&escena(W, H, &cosas, luz, 10 + k as u64), &calib(), 10 + k as u64).unwrap();
        assert!(out.upsert.is_empty() && out.remove.is_empty(), "luz {luz}: {:?} {:?}", out.upsert, p.log());
        assert!(out.stages_ms.iter().any(|(n, _)| n == "cascade") && out.stages_ms.iter().any(|(n, _)| n == "total"));
    }
}

#[test]
fn ids_estables_al_mover_con_y_sin_textura() {
    let mut p = pipeline();
    let (t, d, c) = registrar(&mut p);
    // la tarjeta (con textura) se va lejos y girada; el disco (liso) también se mueve
    let cosas = [tarjeta().en(430.0, 110.0).girada(25.0), disco().en(150.0, 340.0), cuchara()];
    let out = p.on_clean(&foto(&cosas, 3), &calib(), 3).unwrap();
    assert!(out.remove.is_empty(), "{:?}", p.log());
    assert_eq!(el_de(&out, 430.0, 110.0).id, t, "{:?}", p.log());
    assert_eq!(el_de(&out, 150.0, 340.0).id, d, "{:?}", p.log());
    assert!(out.upsert.iter().all(|o| o.id != c), "la cuchara no se movió: no se vuelve a publicar");
    assert_eq!(p.snapshot().len(), 3);
}

#[test]
fn un_corrimiento_de_pocos_mm_lo_resuelve_n1() {
    let mut p = pipeline();
    let (_, d, _) = registrar(&mut p);
    let cosas = [tarjeta(), disco().en(524.0, 303.0), cuchara()];
    let out = p.on_clean(&foto(&cosas, 3), &calib(), 3).unwrap();
    assert_eq!(out.upsert.len(), 1, "{:?}", p.log());
    assert_eq!(out.upsert[0].id, d);
    assert!(cerca(&out.upsert[0], 524.0, 303.0, 1.5), "{:?}", out.upsert[0].center_mm);
    assert!(p.log().iter().any(|l| l.starts_with("N1")), "{:?}", p.log());
}

#[test]
fn histeresis_se_retira_recien_al_segundo_ciclo_y_vuelve_con_su_id() {
    let mut p = pipeline();
    let (t, d, _) = registrar(&mut p);
    let sin_disco = [tarjeta(), cuchara()];
    // primer ciclo sin verlo: sigue publicado, con menos confianza
    let out = p.on_clean(&foto(&sin_disco, 3), &calib(), 3).unwrap();
    assert!(out.remove.is_empty(), "{:?}", p.log());
    let dudoso = out.upsert.iter().find(|o| o.id == d).expect("se republica con menos confianza");
    assert!(dudoso.confidence < 0.4);
    // vuelve antes del segundo: nunca se retiró
    let out = p.on_clean(&foto(&[tarjeta(), disco(), cuchara()], 4), &calib(), 4).unwrap();
    assert!(out.remove.is_empty());
    assert!(out.upsert.iter().any(|o| o.id == d && o.confidence >= 0.5), "{:?}", p.log());
    // ahora sí se va dos ciclos seguidos (aunque el cambio global del segundo sea cero)
    assert!(p.on_clean(&foto(&sin_disco, 5), &calib(), 5).unwrap().remove.is_empty());
    let out = p.on_clean(&foto(&sin_disco, 6), &calib(), 6).unwrap();
    assert_eq!(out.remove, vec![d.clone()], "{:?}", p.log());
    assert_eq!(p.snapshot().len(), 2);
    // y si vuelve más tarde, en otro lugar, recupera su id
    let out = p.on_clean(&foto(&[tarjeta(), cuchara(), disco().en(330.0, 360.0)], 7), &calib(), 7).unwrap();
    assert_eq!(el_de(&out, 330.0, 360.0).id, d, "{:?}", p.log());
    assert!(out.upsert.iter().all(|o| o.id != t));
}

#[test]
fn objeto_nuevo_recibe_id_nuevo_y_los_demas_no_se_republican() {
    let mut p = pipeline();
    let (t, d, c) = registrar(&mut p);
    let nuevo = Cosa::caja(350.0, 250.0, 30.0, 30.0, [30, 160, 70]);
    let out = p.on_clean(&foto(&[tarjeta(), disco(), cuchara(), nuevo], 3), &calib(), 3).unwrap();
    assert_eq!(out.upsert.len(), 1, "{:?}", p.log());
    let o = &out.upsert[0];
    assert!(cerca(o, 350.0, 250.0, 3.0) && ![t, d, c].contains(&o.id));
    assert_eq!(o.label, format!("objeto-{}", o.id.trim_start_matches("obj-")));
}

#[test]
fn reset_olvida_y_no_reutiliza_ids() {
    let mut p = pipeline();
    let (t, d, c) = registrar(&mut p);
    p.reset();
    assert!(p.snapshot().is_empty());
    let out = p.on_clean(&foto(&[tarjeta(), disco()], 3), &calib(), 3).unwrap();
    let mut gone = out.remove.clone();
    gone.sort();
    let mut expected = vec![t.clone(), d.clone(), c.clone()];
    expected.sort();
    assert_eq!(gone, expected);
    assert!(out.upsert.len() == 2 && out.upsert.iter().all(|o| ![&t, &d, &c].contains(&&o.id)));
}

#[test]
fn primera_foto_como_fondo_si_se_pide() {
    let cfg = PipelineConfig { bootstrap: false, ..Default::default() };
    let mut p = Pipeline::cascade_only(cfg);
    assert!(p.on_clean(&foto(&[tarjeta()], 1), &calib(), 1).unwrap().upsert.is_empty());
    // lo que ya estaba es fondo; lo que se agrega después, objeto
    let out = p.on_clean(&foto(&[tarjeta(), disco()], 2), &calib(), 2).unwrap();
    assert_eq!(out.upsert.len(), 1, "{:?}", p.log());
    assert!(cerca(&out.upsert[0], 520.0, 300.0, 3.0));
    // y si lo que estaba se va, no aparece un fantasma en su lugar
    let out = p.on_clean(&foto(&[disco()], 3), &calib(), 3).unwrap();
    assert!(out.upsert.is_empty() && out.remove.is_empty(), "{:?} {:?}", out.upsert, p.log());
}

#[test]
fn calibracion_invalida_es_error_y_calibracion_nueva_reinicia() {
    let mut p = pipeline();
    let mut mala = calib();
    mala.matrix_px_to_mm = [0.0; 9];
    assert!(p.on_clean(&foto(&[], 1), &mala, 1).is_err());
    mala = calib();
    mala.surface_mm = (0.0, 450.0);
    assert!(p.on_clean(&foto(&[], 1), &mala, 1).is_err());
    let otra_proporcion = CalibIn { camera_px: (800, 600), ..calib() };
    assert!(p.on_clean(&foto(&[], 1), &otra_proporcion, 1).unwrap_err().contains("proporción"));
    let (t, _, _) = registrar(&mut p);
    // la superficie cambia de tamaño: se olvida todo y se avisa qué retirar
    let nueva = calib_plana((W, H), (780.0, 440.0), 1.0, (0.0, 0.0), None);
    let out = p.on_clean(&foto(&[tarjeta()], 9), &nueva, 9).unwrap();
    assert!(out.remove.contains(&t));
    assert_eq!(out.upsert.len(), 1);
}

/// Modelo de la consola: px corregido → px de la foto.
fn distort(l: &Lens, (u, v): (f64, f64)) -> (f64, f64) {
    let (x, y) = ((u - l.cx) / l.f, (v - l.cy) / l.f);
    let r2 = x * x + y * y;
    let k = 1.0 + l.k1 * r2 + l.k2 * r2 * r2;
    (x * k * l.f + l.cx, y * k * l.f + l.cy)
}

fn undistort(l: &Lens, (u, v): (f64, f64)) -> (f64, f64) {
    let (xd, yd) = ((u - l.cx) / l.f, (v - l.cy) / l.f);
    let (mut x, mut y) = (xd, yd);
    for _ in 0..20 {
        let r2 = x * x + y * y;
        let k = 1.0 + l.k1 * r2 + l.k2 * r2 * r2;
        (x, y) = (xd / k, yd / k);
    }
    (x * l.f + l.cx, y * l.f + l.cy)
}

#[test]
fn lente_y_foto_a_otra_resolucion_dan_los_mismos_mm() {
    // cámara de 960×540 con gran angular; la superficie (800×450 mm) ocupa el centro a 1 px/mm
    let lens = Lens { f: 420.0, cx: 480.0, cy: 270.0, k1: -0.12, k2: 0.02 };
    let off = (80.0, 45.0);
    let cosas = [tarjeta(), disco(), cuchara()];
    let ideal = |seed: u64| {
        let corridas: Vec<Cosa> = cosas.iter().map(|c| c.en(c.cx + off.0, c.cy + off.1)).collect();
        escena(960, 540, &corridas, 0, seed)
    };
    // la foto real: cada píxel mira donde el modelo dice que cae al corregirlo; y al doble de resolución
    let foto_lente = |seed: u64, k: u32| {
        let src = ideal(seed);
        RgbImage::from_fn(960 * k, 540 * k, |x, y| {
            let p = ((x as f64 + 0.5) / k as f64 - 0.5, (y as f64 + 0.5) / k as f64 - 0.5);
            let (u, v) = undistort(&lens, p);
            let (ui, vi) = (u.round().clamp(0.0, 959.0) as u32, v.round().clamp(0.0, 539.0) as u32);
            *src.get_pixel(ui, vi)
        })
    };
    assert!((distort(&lens, undistort(&lens, (100.0, 60.0))).0 - 100.0).abs() < 1e-6);
    let calib = calib_plana((960, 540), (800.0, 450.0), 1.0, off, Some(lens.clone()));
    for k in [1u32, 2] {
        let mut p = pipeline();
        let mut vistos = p.on_clean(&foto_lente(1, k), &calib, 1).unwrap().upsert;
        vistos.extend(p.on_clean(&foto_lente(2, k), &calib, 2).unwrap().upsert);
        for (x, y) in [(200.0, 150.0), (520.0, 300.0), (620.0, 110.0)] {
            assert!(vistos.iter().any(|o| cerca(o, x, y, 2.5)), "×{k}: nadie en ({x}, {y}): {:?}", p.log());
        }
        // sin el lente, la tarjeta (lejos del centro) cae a más de 5 mm de su lugar
        let sin = CalibIn { lens: None, ..calib.clone() };
        let mut q = pipeline();
        let v = q.on_clean(&foto_lente(1, k), &sin, 1).unwrap().upsert;
        assert!(!v.iter().any(|o| cerca(o, 200.0, 150.0, 5.0)), "×{k}: el lente no cambió nada");
    }
}

// ---------------------------------------------------------------- SAM simulado

#[derive(Default)]
struct Uso {
    encoder: usize,
    prompts: Vec<Vec<SamPoint>>,
}

/// «SAM» de juguete: dentro de la caja, todo lo que no tiene el color de la esquina de la caja. Con `falla`,
/// el decoder devuelve error.
struct SamSimulado {
    uso: Arc<Mutex<Uso>>,
    img: Option<RgbImage>,
    falla: bool,
}

impl Segmenter for SamSimulado {
    fn set_image(&mut self, img: &RgbImage) -> Result<(), BoxErr> {
        self.uso.lock().unwrap().encoder += 1;
        self.img = Some(img.clone());
        Ok(())
    }
    fn predict(&mut self, pts: &[SamPoint]) -> Result<SamOutput, BoxErr> {
        self.uso.lock().unwrap().prompts.push(pts.to_vec());
        if self.falla {
            return Err("decoder caído".into());
        }
        let img = self.img.as_ref().ok_or("sin imagen")?;
        let (w, h) = img.dimensions();
        let a = pts.iter().find(|p| p.2 == 2.0).ok_or("sin caja")?;
        let b = pts.iter().find(|p| p.2 == 3.0).ok_or("sin caja")?;
        let (x0, y0) = (a.0.max(0.0) as u32, a.1.max(0.0) as u32);
        let (x1, y1) = ((b.0 as u32).min(w), (b.1 as u32).min(h));
        let fondo = img.get_pixel(x0.min(w - 1), y0.min(h - 1)).0;
        let mut logits = vec![-10f32; (w * h) as usize];
        for y in y0..y1 {
            for x in x0..x1 {
                let p = img.get_pixel(x, y).0;
                if (0..3).any(|c| p[c].abs_diff(fondo[c]) > 30) {
                    logits[(y * w + x) as usize] = 10.0;
                }
            }
        }
        Ok(SamOutput { width: w, height: h, logits, score: 0.93 })
    }
}

fn con_sam(falla: bool) -> (Pipeline, Arc<Mutex<Uso>>) {
    let uso = Arc::new(Mutex::new(Uso::default()));
    let sam = SamSimulado { uso: uso.clone(), img: None, falla };
    (pipeline().with_segmenter(Box::new(sam)), uso)
}

#[test]
fn sam_caja_sola_un_encoder_por_foto_y_solo_en_regiones_nuevas() {
    let (mut p, uso) = con_sam(false);
    let cosas = [tarjeta(), disco(), cuchara()];
    let out = p.on_clean(&foto(&cosas, 1), &calib(), 1).unwrap();
    assert_eq!(out.upsert.len(), 3, "{:?}", p.log());
    assert!(out.stages_ms.iter().any(|(n, _)| n == "sam_encoder"));
    {
        let u = uso.lock().unwrap();
        assert_eq!(u.encoder, 1, "un encoder por foto");
        assert!(!u.prompts.is_empty());
        for pr in &u.prompts {
            // caja sola: dos esquinas (2 y 3) y ningún punto positivo
            assert_eq!(pr.len(), 2, "{pr:?}");
            assert!(pr[0].2 == 2.0 && pr[1].2 == 3.0 && pr[0].0 < pr[1].0 && pr[0].1 < pr[1].1);
        }
    }
    // escena quieta: SAM no corre
    let out = p.on_clean(&foto(&cosas, 2), &calib(), 2).unwrap();
    assert!(out.upsert.is_empty() && !out.stages_ms.iter().any(|(n, _)| n == "sam_encoder"));
    assert_eq!(uso.lock().unwrap().encoder, 1);
    // un objeto nuevo: un encoder más y pocas cajas (solo la región nueva)
    let antes = uso.lock().unwrap().prompts.len();
    let nuevo = Cosa::caja(350.0, 250.0, 30.0, 30.0, [30, 160, 70]);
    let con_nuevo = foto(&[tarjeta(), disco(), cuchara(), nuevo], 3);
    let out = p.on_clean(&con_nuevo, &calib(), 3).unwrap();
    assert_eq!(out.upsert.len(), 1, "{:?}", p.log());
    assert!(cerca(&out.upsert[0], 350.0, 250.0, 3.0) && out.upsert[0].confidence > 0.9);
    let u = uso.lock().unwrap();
    assert_eq!(u.encoder, 2);
    assert!(u.prompts.len() - antes <= 2, "{} cajas para una región", u.prompts.len() - antes);
}

#[test]
fn un_fallo_de_sam_no_retira_objetos() {
    let (mut p, _) = con_sam(false);
    let cosas = [tarjeta(), disco(), cuchara()];
    let ids: Vec<String> =
        p.on_clean(&foto(&cosas, 1), &calib(), 1).unwrap().upsert.into_iter().map(|o| o.id).collect();
    assert_eq!(ids.len(), 3);
    let (roto, _) = con_sam(true);
    // mismo estado, pero ahora el decoder falla: el disco se mueve y SAM no responde
    let mut p = p.with_segmenter(Box::new(SamSimulado { uso: Default::default(), img: None, falla: true }));
    drop(roto);
    let movido = [tarjeta(), disco().en(150.0, 340.0), cuchara()];
    for k in 0..3 {
        let out = p.on_clean(&foto(&movido, 10 + k), &calib(), 10 + k).unwrap();
        assert!(out.remove.is_empty(), "ciclo {k}: {:?}", p.log());
        assert!(p.log().iter().any(|l| l.contains("SAM")) || k > 0, "{:?}", p.log());
    }
    assert_eq!(p.snapshot().len(), 3);
}
