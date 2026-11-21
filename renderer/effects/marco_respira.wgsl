// Banda interior con gradiente que recorre el perímetro (inspirado en el «marco que respira» de Max).
// params: band_mm=12.0, speed=0.25, breath_hz=0.4, tail=0.6
fn effect(p: vec2<f32>) -> vec4<f32> {
    let band = fx_param(0);
    let speed = fx_param(1);
    let breath_hz = fx_param(2);
    let tail = fx_param(3);
    let d = poly_sdf(p);
    if (d > 0.0) { return vec4<f32>(0.0); }
    let t = fx_time();
    let breath = 0.75 + 0.25 * sin(6.2831853 * breath_hz * t);
    // Banda interior: plena en el borde, se desvanece hacia adentro.
    let inner = 1.0 - smoothstep(0.0, band * breath, -d);
    // Gradiente que recorre el perímetro: cabeza brillante con cola.
    let s = fract(poly_perimeter_pos(p) - speed * t);
    let run = pow(s, 1.0 / max(tail, 0.05));
    return vec4<f32>(fx.color.rgb * (0.35 + 0.65 * run), inner * (0.25 + 0.75 * run) * fx.color.a);
}
