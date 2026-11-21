// Prelude de los efectos (lo antepone el renderer a cada effects/*.wgsl).
// Un efecto define:   fn effect(p: vec2<f32>) -> vec4<f32>
// donde p es el punto en mm de la superficie y el resultado es RGBA sin premultiplicar
// (se mezcla con alfa sobre el lienzo). Se dibuja sobre el bbox del polígono/rect + margin_mm.

struct Fx {
    rect: vec4<f32>,      // bbox dibujado en mm: x, y, w, h (ya incluye el margen)
    color: vec4<f32>,     // params.color (o #00E5FF), RGBA 0..1
    p0: vec4<f32>,        // params declarados en el encabezado `// params:` (8 floats)
    p1: vec4<f32>,
    misc: vec4<f32>,      // t_s, ppmm, n_puntos, margin_mm
    canvas: vec4<f32>,    // ancho_px, alto_px del lienzo, 0, 0
    pts: array<vec4<f32>, 16>, // polígono en mm, 2 puntos por vec4 (máx. 32)
};

@group(0) @binding(0) var<uniform> fx: Fx;

fn fx_time() -> f32 { return fx.misc.x; }
fn fx_n() -> i32 { return i32(fx.misc.z); }
fn fx_param(i: i32) -> f32 {
    if (i < 4) { return fx.p0[i]; }
    return fx.p1[i - 4];
}
fn fx_point(i: i32) -> vec2<f32> {
    let v = fx.pts[i / 2];
    if (i % 2 == 0) { return v.xy; }
    return v.zw;
}

// Distancia con signo al polígono (negativa adentro), en mm.
fn poly_sdf(p: vec2<f32>) -> f32 {
    let n = fx_n();
    var d = 1e9;
    var s = 1.0;
    var j = n - 1;
    for (var i = 0; i < n; i = i + 1) {
        let a = fx_point(i);
        let b = fx_point(j);
        let e = b - a;
        let w = p - a;
        let h = clamp(dot(w, e) / max(dot(e, e), 1e-6), 0.0, 1.0);
        let q = w - e * h;
        d = min(d, dot(q, q));
        let c1 = p.y >= a.y;
        let c2 = p.y < b.y;
        let c3 = e.x * w.y > e.y * w.x;
        if ((c1 && c2 && c3) || (!c1 && !c2 && !c3)) { s = -s; }
        j = i;
    }
    return s * sqrt(d);
}

// Posición 0..1 a lo largo del perímetro del punto del borde más cercano a p.
fn poly_perimeter_pos(p: vec2<f32>) -> f32 {
    let n = fx_n();
    var total = 0.0;
    for (var i = 0; i < n; i = i + 1) {
        total = total + length(fx_point((i + 1) % n) - fx_point(i));
    }
    var best = 1e9;
    var pos = 0.0;
    var acc = 0.0;
    for (var i = 0; i < n; i = i + 1) {
        let a = fx_point(i);
        let b = fx_point((i + 1) % n);
        let e = b - a;
        let l = length(e);
        let h = clamp(dot(p - a, e) / max(l * l, 1e-6), 0.0, 1.0);
        let dd = length(p - a - e * h);
        if (dd < best) { best = dd; pos = acc + h * l; }
        acc = acc + l;
    }
    return pos / max(total, 1e-6);
}

struct VsOut {
    @builtin(position) pos: vec4<f32>,
    @location(0) p_mm: vec2<f32>,
};

@vertex
fn vs_main(@builtin(vertex_index) vi: u32) -> VsOut {
    var corners = array<vec2<f32>, 6>(
        vec2<f32>(0.0, 0.0), vec2<f32>(1.0, 0.0), vec2<f32>(0.0, 1.0),
        vec2<f32>(0.0, 1.0), vec2<f32>(1.0, 0.0), vec2<f32>(1.0, 1.0));
    let c = corners[vi];
    let p_mm = fx.rect.xy + c * fx.rect.zw;
    let px = p_mm * fx.misc.y;
    let ndc = vec2<f32>(px.x / fx.canvas.x * 2.0 - 1.0, 1.0 - px.y / fx.canvas.y * 2.0);
    var o: VsOut;
    o.pos = vec4<f32>(ndc, 0.0, 1.0);
    o.p_mm = p_mm;
    return o;
}

@fragment
fn fs_main(in: VsOut) -> @location(0) vec4<f32> {
    return clamp(effect(in.p_mm), vec4<f32>(0.0), vec4<f32>(1.0));
}

// ---- efecto ----
