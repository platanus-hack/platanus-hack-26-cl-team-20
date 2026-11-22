//! Cámara sintética: una mesa con proyección encima y objetos que se ponen y se quitan, sin hardware.
//!
//! Sirve para los tests (el cliente de prueba hace de web app y de física: apaga el «proyector» antes de contestar
//! `blanked`) y para probar el motor a mano con `pcx motor --camera synthetic`, donde el propio motor apaga la
//! proyección simulada al recibir `blanked` y un objeto de demostración entra y sale cada 20 s.

use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use image::RgbImage;
use pcx_capture::frame::LatestTx;
use pcx_capture::Frame;

use super::frames::now_ns;

#[derive(Clone, Debug)]
struct Obj {
    name: String,
    /// `[x0, y0, x1, y1)` en px.
    rect: [u32; 4],
    rgb: [u8; 3],
}

#[derive(Debug)]
struct State {
    objects: Vec<Obj>,
    /// Cambios del proyector (cuándo llega la luz a la cámara, encendido).
    projector: Vec<(Instant, bool)>,
}

#[derive(Debug)]
struct Shared {
    size: (u32, u32),
    fps: f64,
    /// Latencia proyector → cámara.
    latency: Duration,
    /// El motor maneja el proyector simulado (`blanked` lo apaga, `unblank` lo prende).
    auto: bool,
    /// Objeto de demostración que entra y sale con este período.
    demo_period: Option<Duration>,
    born: Instant,
    state: Mutex<State>,
}

#[derive(Clone, Debug)]
pub struct Synthetic(Arc<Shared>);

impl Synthetic {
    /// Mesa vacía con la proyección encendida. El proyector lo maneja quien la crea ([`Synthetic::set_projector`]).
    pub fn new(size: (u32, u32), fps: f64) -> Self {
        Self::build(size, fps, false, None)
    }

    /// Para `--camera synthetic`: el motor apaga la proyección y un objeto entra y sale cada 20 s.
    pub fn demo(size: (u32, u32)) -> Self {
        Self::build(size, 30.0, true, Some(Duration::from_secs(20)))
    }

    fn build(size: (u32, u32), fps: f64, auto: bool, demo_period: Option<Duration>) -> Self {
        Synthetic(Arc::new(Shared {
            size: (size.0.max(16), size.1.max(16)),
            fps: fps.clamp(1.0, 240.0),
            latency: Duration::from_millis(120),
            auto,
            demo_period,
            born: Instant::now(),
            state: Mutex::new(State { objects: Vec::new(), projector: vec![(Instant::now(), true)] }),
        }))
    }

    pub fn size(&self) -> (u32, u32) {
        self.0.size
    }

    pub fn auto(&self) -> bool {
        self.0.auto
    }

    /// Prende o apaga la proyección; la cámara lo ve tras la latencia simulada (120 ms).
    pub fn set_projector(&self, on: bool) {
        let mut st = self.0.state.lock().unwrap();
        let cut = st.projector.len().saturating_sub(8);
        st.projector.drain(..cut);
        st.projector.push((Instant::now() + self.0.latency, on));
    }

    /// Pone (o mueve) un objeto: rectángulo `[x0, y0, x1, y1)` en px de la cámara.
    pub fn put_object(&self, name: &str, rect: [u32; 4], rgb: [u8; 3]) {
        let mut st = self.0.state.lock().unwrap();
        st.objects.retain(|o| o.name != name);
        st.objects.push(Obj { name: name.into(), rect, rgb });
    }

    pub fn remove_object(&self, name: &str) {
        self.0.state.lock().unwrap().objects.retain(|o| o.name != name);
    }

    /// Zona que ilumina el proyector: el 80 % central del cuadro, `[x0, y0, x1, y1)`.
    pub fn projected_rect(&self) -> [u32; 4] {
        let (w, h) = self.0.size;
        [w / 10, h / 10, w - w / 10, h - h / 10]
    }

    /// Cuadro que vería la cámara en `at`.
    pub fn render(&self, at: Instant) -> RgbImage {
        let (w, h) = self.0.size;
        let st = self.0.state.lock().unwrap();
        let lit = st.projector.iter().rev().find(|(t, _)| *t <= at).is_none_or(|(_, on)| *on);
        let mut objects = st.objects.clone();
        drop(st);
        if let Some(p) = self.0.demo_period {
            let phase = at.duration_since(self.0.born).as_secs_f64() / p.as_secs_f64();
            if phase.fract() >= 0.5 {
                objects.push(Obj {
                    name: "demo".into(),
                    rect: [w * 2 / 5, h * 2 / 5, w * 3 / 5, h * 3 / 5],
                    rgb: [200, 70, 40],
                });
            }
        }
        let [px0, py0, px1, py1] = self.projected_rect();
        let mut img = RgbImage::new(w, h);
        for (x, y, p) in img.enumerate_pixels_mut() {
            // mesa con degradado y algo de textura
            let base = 40 + 50 * x / w + (x * 7 + y * 13) % 9;
            let mut rgb = [base as u8, base as u8, (base as f32 * 0.9) as u8];
            if let Some(o) =
                objects.iter().rev().find(|o| x >= o.rect[0] && x < o.rect[2] && y >= o.rect[1] && y < o.rect[3])
            {
                rgb = o.rgb;
            }
            if lit && x >= px0 && x < px1 && y >= py0 && y < py1 {
                // proyección: franjas claras sobre lo que haya
                let add = if (x / 16 + y / 16) % 2 == 0 { 95 } else { 55 };
                rgb = rgb.map(|c| c.saturating_add(add));
            }
            p.0 = rgb;
        }
        img
    }

    /// Hilo que emite cuadros al ritmo de la cámara hasta que se suelte el receptor.
    pub fn spawn(&self, tx: LatestTx) -> std::io::Result<std::thread::JoinHandle<()>> {
        let me = self.clone();
        std::thread::Builder::new().name("synthetic".into()).spawn(move || {
            let period = Duration::from_secs_f64(1.0 / me.0.fps);
            let mut next = Instant::now();
            while !tx.is_closed() {
                let now = Instant::now();
                if next > now {
                    std::thread::sleep(next - now);
                }
                next = (next + period).max(Instant::now());
                tx.put(Frame { img: me.render(Instant::now()), t_capture_ns: now_ns() });
            }
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn mean(img: &RgbImage, r: [u32; 4]) -> f64 {
        let (mut s, mut n) = (0u64, 0u64);
        for y in r[1]..r[3] {
            for x in r[0]..r[2] {
                s += img.get_pixel(x, y).0.iter().map(|&c| c as u64).sum::<u64>();
                n += 3;
            }
        }
        s as f64 / n as f64
    }

    #[test]
    fn el_proyector_se_apaga_con_latencia_y_los_objetos_se_ven() {
        let s = Synthetic::new((160, 90), 30.0);
        let t0 = Instant::now();
        let on = mean(&s.render(t0), s.projected_rect());
        s.set_projector(false);
        assert_eq!(mean(&s.render(Instant::now()), s.projected_rect()), on, "antes de la latencia sigue encendido");
        let off = mean(&s.render(Instant::now() + Duration::from_millis(200)), s.projected_rect());
        assert!(on - off > 50.0, "caída {on} → {off}");
        s.put_object("taza", [40, 30, 60, 50], [200, 60, 40]);
        let later = Instant::now() + Duration::from_millis(200);
        assert_eq!(s.render(later).get_pixel(50, 40).0, [200, 60, 40]);
        s.remove_object("taza");
        assert_ne!(s.render(later).get_pixel(50, 40).0, [200, 60, 40]);
    }
}
