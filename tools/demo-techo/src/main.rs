//! demo-techo: calibración real proyector↔C920 con pcx-calib y demo en vivo en el techo.
//!   demo-techo calib --port 9242 --out DIR
//!   demo-techo run   --port 9242 --calib DIR
mod calib;
mod demo;
mod ws;

fn arg(a: &[String], k: &str) -> Option<String> {
    a.iter().position(|x| x == k).and_then(|i| a.get(i + 1)).cloned()
}

fn main() {
    let a: Vec<String> = std::env::args().collect();
    let port: u16 = arg(&a, "--port").and_then(|p| p.parse().ok()).unwrap_or(9242);
    match a.get(1).map(String::as_str) {
        Some("calib") => calib::run(port, std::path::Path::new(&arg(&a, "--out").expect("--out"))),
        Some("run") => demo::run(
            port,
            std::path::Path::new(&arg(&a, "--assets").expect("--assets")),
            std::path::Path::new(&arg(&a, "--logs").expect("--logs")),
        ),
        _ => eprintln!("uso: demo-techo calib|run ..."),
    }
}
