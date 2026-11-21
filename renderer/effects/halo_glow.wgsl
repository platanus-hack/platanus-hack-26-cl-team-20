// Halo que late alrededor de un polígono (inspirado en los halos de Max).
// params: pulse_hz=1.2, width_mm=6.0, glow_mm=18.0, intensity=1.0, margin_mm=24.0
fn effect(p: vec2<f32>) -> vec4<f32> {
    let pulse_hz = fx_param(0);
    let width = fx_param(1);
    let glow = fx_param(2);
    let intensity = fx_param(3);
    let d = poly_sdf(p);
    let pulse = 0.5 + 0.5 * sin(6.2831853 * pulse_hz * fx_time());
    // Núcleo: banda sobre el borde. Resplandor: decae hacia afuera y late con el pulso.
    let core = 1.0 - smoothstep(width * 0.5, width * 0.5 + 1.0, abs(d));
    let outside = max(d, 0.0);
    // Se apaga antes del borde del bbox dibujado (margin_mm) para que no se note el recorte.
    let fade = 1.0 - smoothstep(fx.misc.w * 0.5, fx.misc.w, outside);
    let halo = exp(-outside / max(glow * (0.6 + 0.4 * pulse), 0.1)) * step(0.0, d) * fade;
    let a = clamp((core * (0.7 + 0.3 * pulse) + 0.55 * halo * pulse) * intensity, 0.0, 1.0);
    return vec4<f32>(fx.color.rgb, a * fx.color.a);
}
