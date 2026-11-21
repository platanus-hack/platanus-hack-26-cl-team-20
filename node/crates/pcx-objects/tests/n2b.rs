//! Nivel 2b (R4): re-ubicar por histograma HSV, sin DINO/SAM. Escenas sintéticas en el lienzo real de 1280×720:
//! «arrugado» (misma paleta, otra disposición), «girado sin textura» (color liso: ORB no tiene puntos) y el caso de
//! falso positivo (dos objetos de color parecido → ambiguo, no se confunden). Más los casos acromáticos (negro,
//! blanco, gris) sobre mesa gris y blanca, con ruido independiente por canal: retirado ≠ movido.

mod common;

use common::{scrambled, texture};
use image::{Rgb, RgbImage};
use pcx_objects::geom::{self, Canvas};
use pcx_objects::raster::{self, Mask};
use pcx_objects::{CleanFrame, HistMetric, MockLlm, ObjectEvent, Params, Report, Tracker};
use serde_json::json;

const W: u32 = 1280;
const H: u32 = 720;

/// Colores lisos lejos de los bordes de casillero HSV y con gris distinto al de la mesa.
const CAFE: [u8; 3] = [160, 84, 30];
const CAFE_PARECIDO: [u8; 3] = [150, 80, 28];
const AZUL: [u8; 3] = [40, 80, 200];
const AMARILLO: [u8; 3] = [250, 230, 70];
/// Sin color: celular negro, papel blanco y un gris que cae en el mismo casillero de valor que la mesa gris.
const NEGRO: [u8; 3] = [28, 28, 30];
const BLANCO: [u8; 3] = [240, 240, 236];
const GRIS_CLARO: [u8; 3] = [182, 182, 180];

/// Superficie de la mesa: con tinte cálido (la de `common::render`), gris neutra o blanca (de proyección).
#[derive(Clone, Copy, Debug, PartialEq)]
enum Mesa {
    Tinte,
    Gris,
    Blanca,
}

#[derive(Clone)]
struct Obj {
    tex: RgbImage,
    cx: f64,
    cy: f64,
    angle_deg: f64,
}

fn liso(color: [u8; 3], w: u32, h: u32, cx: f64, cy: f64) -> Obj {
    Obj { tex: RgbImage::from_pixel(w, h, Rgb(color)), cx, cy, angle_deg: 0.0 }
}

fn each_px(o: &Obj, mut f: impl FnMut(u32, u32, u32, u32)) {
    let (c, s) = (o.angle_deg.to_radians().cos(), o.angle_deg.to_radians().sin());
    let (tw, th) = (o.tex.width() as f64, o.tex.height() as f64);
    let r = (tw.hypot(th) / 2.0 + 2.0) as i64;
    for y in (o.cy as i64 - r).max(0)..(o.cy as i64 + r).min(H as i64) {
        for x in (o.cx as i64 - r).max(0)..(o.cx as i64 + r).min(W as i64) {
            let (dx, dy) = (x as f64 + 0.5 - o.cx, y as f64 + 0.5 - o.cy);
            let (u, v) = (c * dx + s * dy + tw / 2.0, -s * dx + c * dy + th / 2.0);
            if u >= 0.0 && v >= 0.0 && u < tw && v < th {
                f(x as u32, y as u32, u as u32, v as u32);
            }
        }
    }
}

/// Mesa con degradé + objetos + ruido de sensor ±2 **independiente por canal** (ruido de croma incluido), 1280×720.
fn frame_en(mesa: Mesa, objs: &[Obj], seed: u64) -> CleanFrame {
    let mut img = RgbImage::from_fn(W, H, |x, y| {
        let v = (130 + x * 20 / W + y * 10 / H) as u8;
        match mesa {
            Mesa::Tinte => Rgb([v + 15, v, v - 20]),
            Mesa::Gris => Rgb([v, v, v]),
            Mesa::Blanca => {
                let b = (232 + x * 12 / W + y * 6 / H) as u8;
                Rgb([b, b, b - 4])
            }
        }
    });
    for o in objs {
        each_px(o, |x, y, u, v| img.put_pixel(x, y, *o.tex.get_pixel(u, v)));
    }
    let mut s = seed;
    for p in img.pixels_mut() {
        p.0 = p.0.map(|q| {
            s = s.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
            (q as i32 + ((s >> 33) % 5) as i32 - 2).clamp(0, 255) as u8
        });
    }
    CleanFrame { rgb: img, t_ns: seed as i64 }
}

fn frame(objs: &[Obj], seed: u64) -> CleanFrame {
    frame_en(Mesa::Tinte, objs, seed)
}

fn mask_of(o: &Obj) -> Mask {
    let mut m = raster::empty(W, H);
    each_px(o, |x, y, _, _| m.put_pixel(x, y, raster::ON));
    m
}

fn tracker(objs: &[(&str, &Obj)], params: Params) -> Tracker {
    tracker_en(Mesa::Tinte, objs, params)
}

fn tracker_en(mesa: Mesa, objs: &[(&str, &Obj)], params: Params) -> Tracker {
    let mut t = Tracker::new(Canvas::covering(0.0, 0.0, 1280.0, 720.0, W), params); // 1 mm por px
    let f0 = frame_en(mesa, &objs.iter().map(|(_, o)| (*o).clone()).collect::<Vec<_>>(), 1);
    for (id, o) in objs {
        t.add_object(id, &format!("objeto {id}"), "a thing", mask_of(o), &f0);
    }
    t
}

fn moved<'a>(r: &'a Report, id: &str) -> Option<(&'a str, (f64, f64), geom::Affine)> {
    r.events.iter().find_map(|e| match e {
        ObjectEvent::ObjectMoved { object_id, level, center_mm, aff, .. } if object_id == id => {
            Some((level.as_deref().unwrap_or(""), (center_mm.0, center_mm.1), aff.unwrap()))
        }
        _ => None,
    })
}

/// Corre la misma revisión 3 veces (seguidor nuevo cada vez) y devuelve el último reporte + el mejor tiempo de N2b.
/// Los tests normales solo informan el tiempo; el presupuesto se exige en `cronometro_n2b` (`#[ignore]`).
fn mejor_de_3(objs: &[(&str, &Obj)], despues: &CleanFrame) -> (Tracker, Report, f64) {
    let mut best = f64::MAX;
    let mut last = None;
    for _ in 0..3 {
        let mut t = tracker(objs, Params::default());
        let r = t.update(despues);
        best = best.min(r.n2b_ms);
        last = Some((t, r));
    }
    let (t, r) = last.unwrap();
    (t, r, best)
}

#[test]
fn n2b_arrugado_sin_modelos() {
    let a = Obj { tex: texture(1, 160, 112), cx: 360.0, cy: 280.0, angle_deg: 0.0 };
    let b = Obj { tex: texture(2, 144, 144), cx: 900.0, cy: 440.0, angle_deg: 0.0 };
    // arrugado: misma paleta, bloques revueltos, en otro lugar y girado → ORB no calza y no hay DINO ni SAM
    let a2 = Obj { tex: scrambled(&a.tex, 7), cx: 660.0, cy: 180.0, angle_deg: 10.0 };
    let (t, r, ms) = mejor_de_3(&[("a", &a), ("b", &b)], &frame(&[a2.clone(), b.clone()], 10));
    let (level, c, _) = moved(&r, "a").unwrap_or_else(|| panic!("{:?} {:?}", r.events, r.log));
    assert!(level.starts_with("N2b hist"), "{level}");
    assert!((c.0 - 660.0).abs() < 4.0 && (c.1 - 180.0).abs() < 4.0, "{c:?}");
    assert!(moved(&r, "b").is_none() && r.ambiguous.is_empty());
    // máscara nueva por contorno: cubre casi toda la tarjeta arrugada y casi nada de mesa
    let (m, truth) = (&t.object("a").unwrap().mask, mask_of(&a2));
    let inter = raster::count(&raster::and(m, &truth)) as f64;
    assert!(inter / raster::count(&truth) as f64 > 0.9 && inter / raster::count(m) as f64 > 0.95);
    println!("N2b arrugado 1280x720: {ms:.1} ms · {level}");
}

#[test]
fn n2b_girado_sin_textura() {
    let a = liso(CAFE, 160, 112, 360.0, 280.0);
    let b = liso(AZUL, 144, 144, 900.0, 440.0);
    // (1) movido y girado 40°
    let a2 = Obj { cx: 700.0, cy: 200.0, angle_deg: 40.0, ..a.clone() };
    let (_, r, ms) = mejor_de_3(&[("a", &a), ("b", &b)], &frame(&[a2, b.clone()], 11));
    let (level, c, aff) = moved(&r, "a").unwrap_or_else(|| panic!("{:?} {:?}", r.events, r.log));
    assert!(level.starts_with("N2b hist"), "{level}");
    assert!((c.0 - 700.0).abs() < 3.0 && (c.1 - 200.0).abs() < 3.0, "{c:?}");
    let ang = geom::angle_deg(&aff);
    assert!((ang - 40.0).abs() < 3.0, "ángulo {ang}");
    // la afín lleva el centro viejo al nuevo
    let p = geom::apply(&aff, (360.0, 280.0));
    assert!((p.0 - 700.0).abs() < 3.0 && (p.1 - 200.0).abs() < 3.0, "{p:?}");
    println!("N2b girado sin textura 1280x720: {ms:.1} ms · {level} · {ang:.1}°");
    // (2) girado en el lugar −60° (con menos de ~45° cambia < 20 % de la máscara y el nivel 0 ni sospecha): el centro no cambia en el diff (se tapa a sí mismo) y aun así sale la máscara completa
    let a3 = Obj { angle_deg: -60.0, ..a.clone() };
    let (t, r, ms) = mejor_de_3(&[("a", &a), ("b", &b)], &frame(&[a3.clone(), b.clone()], 12));
    let (level, c, aff) = moved(&r, "a").unwrap_or_else(|| panic!("{:?} {:?}", r.events, r.log));
    assert!(level.starts_with("N2b hist"), "{level}");
    assert!((c.0 - 360.0).abs() < 3.0 && (c.1 - 280.0).abs() < 3.0, "{c:?}");
    let ang = geom::angle_deg(&aff);
    assert!((ang + 60.0).abs() < 3.0, "ángulo {ang}");
    let (m, truth) = (&t.object("a").unwrap().mask, mask_of(&a3));
    let inter = raster::count(&raster::and(m, &truth)) as f64;
    assert!(inter / raster::count(&truth) as f64 > 0.95 && inter / raster::count(m) as f64 > 0.95);
    println!("N2b girado en el lugar 1280x720: {ms:.1} ms · {ang:.1}°");
}

#[test]
fn n2b_colores_parecidos_no_confunde() {
    let a = liso(CAFE, 160, 112, 360.0, 280.0);
    let b = liso(CAFE_PARECIDO, 144, 144, 900.0, 440.0);
    let a2 = Obj { cx: 660.0, cy: 180.0, angle_deg: 20.0, ..a.clone() };
    let b2 = Obj { cx: 600.0, cy: 560.0, angle_deg: -15.0, ..b.clone() };
    let despues = frame(&[a2, b2], 13);
    // sin juez: los dos quedan ambiguos y ocultos; ninguno se asigna al lugar del otro
    let mut t = tracker(&[("a", &a), ("b", &b)], Params::default());
    let r = t.update(&despues);
    assert_eq!(r.ambiguous, vec!["a".to_string(), "b".to_string()], "{:?}", r.log);
    assert!(moved(&r, "a").is_none() && moved(&r, "b").is_none(), "{:?}", r.events);
    assert_eq!(r.events.iter().filter(|e| matches!(e, ObjectEvent::ObjectRemoved { .. })).count(), 2);
    assert!(t.objects().is_empty());
    // con juez (N4): decide el primero; el segundo ya tiene un solo candidato libre y lo resuelve N2b
    let mut t = tracker(&[("a", &a), ("b", &b)], Params::default()).with_llm(Box::new(MockLlm::new(json!({"n": 1}))));
    let r = t.update(&despues);
    let (la, ca, _) = moved(&r, "a").unwrap_or_else(|| panic!("{:?} {:?}", r.events, r.log));
    let (lb, cb, _) = moved(&r, "b").unwrap_or_else(|| panic!("{:?} {:?}", r.events, r.log));
    assert_eq!(la, "N4 llm");
    assert!(lb.starts_with("N2b hist"), "{lb}");
    assert!((ca.0 - 660.0).abs() < 4.0 && (cb.0 - 600.0).abs() < 4.0, "{ca:?} {cb:?}");
    assert_eq!(t.llm().unwrap().usage().len(), 1);
}

#[test]
fn n2b_colores_distintos_intercambiados() {
    // mismo movimiento difícil (cada uno cae donde estaba el otro) pero con colores distintos: N2b los separa solo
    // (amarillo y no azul: el nivel 0 compara grises, y café→azul en el mismo lugar cambia menos que el umbral 24)
    let a = liso(CAFE, 160, 112, 360.0, 280.0);
    let b = liso(AMARILLO, 144, 144, 900.0, 440.0);
    let a2 = Obj { cx: b.cx, cy: b.cy, ..a.clone() };
    let b2 = Obj { cx: a.cx, cy: a.cy, ..b.clone() };
    for metric in [HistMetric::Correl, HistMetric::Bhattacharyya] {
        let mut t = tracker(&[("a", &a), ("b", &b)], Params { n2b_metric: metric, ..Params::default() });
        let r = t.update(&frame(&[a2.clone(), b2.clone()], 14));
        let (la, ca, _) = moved(&r, "a").unwrap_or_else(|| panic!("{metric:?} {:?} {:?}", r.events, r.log));
        let (lb, cb, _) = moved(&r, "b").unwrap_or_else(|| panic!("{metric:?} {:?} {:?}", r.events, r.log));
        assert!(la.starts_with("N2b") && lb.starts_with("N2b"), "{la} {lb}");
        assert!((ca.0 - 900.0).abs() < 4.0 && (ca.1 - 440.0).abs() < 4.0, "{ca:?}");
        assert!((cb.0 - 360.0).abs() < 4.0 && (cb.1 - 280.0).abs() < 4.0, "{cb:?}");
        assert!(r.ambiguous.is_empty());
    }
}

#[test]
fn n2b_apagado_es_la_cascada_original() {
    let a = liso(CAFE, 160, 112, 360.0, 280.0);
    let a2 = Obj { cx: 700.0, cy: 200.0, angle_deg: 40.0, ..a.clone() };
    let mut t = tracker(&[("a", &a)], Params { n2b_enabled: false, ..Params::default() });
    let r = t.update(&frame(&[a2], 15));
    assert!(moved(&r, "a").is_none() && r.n2b_ms == 0.0);
    assert!(r.events.iter().any(|e| matches!(e, ObjectEvent::ObjectRemoved { .. })), "{:?}", r.log);
}

fn removed(r: &Report, id: &str) -> bool {
    r.events.iter().any(|e| matches!(e, ObjectEvent::ObjectRemoved { object_id, .. } if object_id == id))
}

/// P0 de la revisión: un objeto sin color que se retira de una mesa neutra NO es «movido en el lugar».
#[test]
fn n2b_acromatico_retirado_no_queda_fantasma() {
    let casos = [
        (Mesa::Gris, NEGRO),
        (Mesa::Gris, BLANCO),
        (Mesa::Gris, GRIS_CLARO), // mismo casillero que la mesa: lo descarta el contraste con el entorno
        (Mesa::Blanca, NEGRO),
        (Mesa::Blanca, CAFE),
        (Mesa::Tinte, NEGRO),
        (Mesa::Tinte, BLANCO),
    ];
    for (mesa, color) in casos {
        let a = liso(color, 160, 112, 360.0, 280.0);
        let mut t = tracker_en(mesa, &[("a", &a)], Params::default());
        let r = t.update(&frame_en(mesa, &[], 20));
        assert!(moved(&r, "a").is_none(), "{mesa:?} {color:?}: fantasma {:?} {:?}", r.events, r.log);
        assert!(removed(&r, "a"), "{mesa:?} {color:?}: {:?} {:?}", r.events, r.log);
        assert!(t.objects().is_empty() && r.ambiguous.is_empty());
    }
    // control: sin la prueba de contraste con el entorno, el gris sobre mesa gris vuelve a ser el fantasma
    let a = liso(GRIS_CLARO, 160, 112, 360.0, 280.0);
    let mut t = tracker_en(Mesa::Gris, &[("a", &a)], Params { n2b_min_contrast_frac: 0.0, ..Params::default() });
    let r = t.update(&frame_en(Mesa::Gris, &[], 20));
    assert!(moved(&r, "a").is_some(), "el caso ya no ejercita la prueba de contraste: {:?}", r.log);
}

/// Los mismos objetos sin color, movidos y girados, sí se re-ubican (por valor, no por tono).
#[test]
fn n2b_acromatico_movido_se_reubica() {
    for (mesa, color) in [(Mesa::Gris, NEGRO), (Mesa::Gris, BLANCO), (Mesa::Gris, GRIS_CLARO), (Mesa::Blanca, NEGRO)] {
        let a = liso(color, 160, 112, 360.0, 280.0);
        let a2 = Obj { cx: 700.0, cy: 200.0, angle_deg: 30.0, ..a.clone() };
        let mut t = tracker_en(mesa, &[("a", &a)], Params::default());
        let r = t.update(&frame_en(mesa, &[a2], 21));
        let (level, c, aff) =
            moved(&r, "a").unwrap_or_else(|| panic!("{mesa:?} {color:?}: {:?} {:?}", r.events, r.log));
        assert!(level.starts_with("N2b hist"), "{mesa:?} {color:?}: {level}");
        assert!((c.0 - 700.0).abs() < 3.0 && (c.1 - 200.0).abs() < 3.0, "{mesa:?} {color:?}: {c:?}");
        let ang = geom::angle_deg(&aff);
        assert!((ang - 30.0).abs() < 3.0, "{mesa:?} {color:?}: ángulo {ang}");
    }
}

/// Uno se retira mientras otro se mueve: el retirado no se queda en su lugar ni se roba el lugar nuevo del otro.
#[test]
fn n2b_retirado_mientras_otro_se_mueve() {
    // (retirado, movido): el segundo caso es el difícil, el lugar que deja `b` se parece a `a` (gris como la mesa)
    for (ca, cb) in [(NEGRO, BLANCO), (GRIS_CLARO, NEGRO), (BLANCO, GRIS_CLARO)] {
        let a = liso(ca, 160, 112, 360.0, 280.0);
        let b = liso(cb, 144, 144, 900.0, 440.0);
        let b2 = Obj { cx: 640.0, cy: 520.0, angle_deg: 15.0, ..b.clone() };
        let mut t = tracker_en(Mesa::Gris, &[("a", &a), ("b", &b)], Params::default());
        let r = t.update(&frame_en(Mesa::Gris, &[b2], 22));
        assert!(moved(&r, "a").is_none() && removed(&r, "a"), "{ca:?}/{cb:?}: {:?} {:?}", r.events, r.log);
        let (level, c, _) = moved(&r, "b").unwrap_or_else(|| panic!("{ca:?}/{cb:?}: {:?} {:?}", r.events, r.log));
        assert!(level.starts_with("N2b hist"), "{ca:?}/{cb:?}: {level}");
        assert!((c.0 - 640.0).abs() < 3.0 && (c.1 - 520.0).abs() < 3.0, "{ca:?}/{cb:?}: {c:?}");
        assert_eq!(t.objects().len(), 1);
    }
}

/// Cronómetro (presupuesto R4: < 30 ms por nivel). `#[ignore]`: depende de la carga de la máquina; correr a mano con
/// `cargo test -p pcx-objects -- --ignored --nocapture cronometro` (y en release para la cifra de la mesa).
#[test]
#[ignore]
fn cronometro_n2b() {
    let a = Obj { tex: texture(1, 160, 112), cx: 360.0, cy: 280.0, angle_deg: 0.0 };
    let b = Obj { tex: texture(2, 144, 144), cx: 900.0, cy: 440.0, angle_deg: 0.0 };
    let a2 = Obj { tex: scrambled(&a.tex, 7), cx: 660.0, cy: 180.0, angle_deg: 10.0 };
    let (_, _, arrugado) = mejor_de_3(&[("a", &a), ("b", &b)], &frame(&[a2, b.clone()], 10));
    let l = liso(CAFE, 160, 112, 360.0, 280.0);
    let l2 = Obj { cx: 700.0, cy: 200.0, angle_deg: 40.0, ..l.clone() };
    let (_, _, girado) = mejor_de_3(&[("a", &l)], &frame(&[l2], 11));
    let n = liso(NEGRO, 160, 112, 360.0, 280.0);
    let (_, _, retirado) = mejor_de_3(&[("a", &n)], &frame(&[], 12));
    println!(
        "N2b 1280x720: arrugado {arrugado:.1} ms · girado sin textura {girado:.1} ms · negro retirado {retirado:.1} ms"
    );
    assert!(arrugado < 30.0 && girado < 30.0 && retirado < 30.0);
}
