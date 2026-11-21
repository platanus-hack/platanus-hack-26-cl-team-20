//! Humo del benchmark (pocos cuadros). El benchmark completo: `cargo run --release -- --bench`.

#[test]
fn bench_corre() {
    let mut eng = pcx_renderer::engine::Engine::headless(Default::default());
    for n in [5, 50, 200] {
        let r = pcx_renderer::bench::run(&mut eng, n, 5);
        assert!(r.p50_ms > 0.0 && r.p95_ms >= r.p50_ms, "{r:?}");
    }
}
