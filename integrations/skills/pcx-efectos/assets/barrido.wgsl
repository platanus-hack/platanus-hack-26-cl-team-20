// Franja de luz que barre el polígono de izquierda a derecha y vuelve a empezar (plantilla de pcx-efectos).
// params: speed=0.35, width_mm=18.0, intensity=1.0, floor=0.08
fn effect(p: vec2<f32>) -> vec4<f32> {
    let speed = fx_param(0);
    let width = fx_param(1);
    let intensity = fx_param(2);
    let floor_a = fx_param(3);
    // Distancia con signo al polígono en mm: negativa adentro. Afuera no se pinta nada (negro = sin luz).
    let d = poly_sdf(p);
    if (d > 0.0) { return vec4<f32>(0.0); }
    // Posición de la franja: recorre el ancho del bbox una vez cada 1/speed segundos.
    let head = fx.rect.x + fract(speed * fx_time()) * fx.rect.z;
    let band = 1.0 - smoothstep(0.0, max(width, 0.5), abs(p.x - head));
    // Se apaga en los últimos 3 mm hacia el borde: sin corte duro.
    let edge = 1.0 - smoothstep(-3.0, 0.0, d);
    let a = clamp((floor_a + band) * intensity, 0.0, 1.0) * edge;
    return vec4<f32>(fx.color.rgb, a * fx.color.a);
}
