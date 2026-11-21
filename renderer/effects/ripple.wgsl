// Ondas concéntricas dentro del polígono (lo usa contracts/fixtures/scene.json).
// params: speed=1.0, wavelength_mm=10.0
fn effect(p: vec2<f32>) -> vec4<f32> {
    let d = poly_sdf(p);
    if (d > 0.0) { return vec4<f32>(0.0); }
    let c = fx.rect.xy + fx.rect.zw * 0.5;
    let r = length(p - c);
    let w = 0.5 + 0.5 * sin(6.2831853 * (r / max(fx_param(1), 0.5) - fx_param(0) * fx_time()));
    let fade = 1.0 - smoothstep(-4.0, 0.0, d);
    return vec4<f32>(fx.color.rgb, w * w * 0.8 * fade * fx.color.a);
}
