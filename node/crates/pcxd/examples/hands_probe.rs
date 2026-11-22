//! Emisor de toques para `tests/confirmacion.rs`: usa el mismo `IngestSender` que `pcx worker hands`.
//!
//! `hands_probe <x_mm> <y_mm> <hold_ms> <id> <archivo-go> [--no-auth]`: espera a que exista `archivo-go`, manda un
//! `touch_down` (pinch) en el punto, lo sostiene con `touch_move` y termina con `touch_up`. Con `--no-auth` actúa
//! como un proceso local cualquiera que dice ser `hands` pero no tiene el token.

use std::time::{Duration, Instant};

use pcx_contracts::{wrap, Event, Payload, PointMM, Touch, TouchPhase, Trigger};
use pcxd::worker::IngestSender;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let a: Vec<String> = std::env::args().skip(1).collect();
    anyhow::ensure!(a.len() >= 5, "uso: hands_probe <x> <y> <hold_ms> <id> <archivo-go> [--no-auth]");
    let (x, y, hold): (f64, f64, u64) = (a[0].parse()?, a[1].parse()?, a[2].parse()?);
    let ingest = std::env::var("PCX_INGEST")?;
    let mut tx = if a.iter().any(|s| s == "--no-auth") {
        IngestSender::connect_with(&ingest, None).await?
    } else {
        IngestSender::connect(&ingest).await?
    };
    while !std::path::Path::new(&a[4]).exists() {
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    let env = |phase| {
        let touch = Touch {
            id: a[3].clone(),
            phase,
            point_mm: PointMM(x, y),
            widget_id: None,
            object_id: None,
            trigger: Trigger::Pinch,
        };
        let ev = match phase {
            TouchPhase::Down => Event::TouchDown { touch },
            TouchPhase::Move => Event::TouchMove { touch },
            TouchPhase::Up => Event::TouchUp { touch },
        };
        serde_json::to_vec(&wrap(Payload::Event(ev), 0, "hands", "probe", None)).unwrap()
    };
    tx.send(env(TouchPhase::Down)).await?;
    let t0 = Instant::now();
    while t0.elapsed() < Duration::from_millis(hold) {
        tokio::time::sleep(Duration::from_millis(100)).await;
        tx.send(env(TouchPhase::Move)).await?;
    }
    tx.send(env(TouchPhase::Up)).await?;
    tokio::time::sleep(Duration::from_millis(200)).await; // deja salir el último mensaje
    std::fs::write(format!("{}.done", a[4]), b"ok")?;
    Ok(())
}
