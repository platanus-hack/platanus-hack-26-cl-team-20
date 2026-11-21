// Pasada final: lienzo de la superficie (mm × ppmm) → px del proyector.
// - Homografía: triángulo de pantalla completa; por píxel aplica H⁻¹ (px → mm) y muestrea el lienzo.
// - LUT (gray code): malla; cada vértice está en px de salida y lleva su punto en mm (vs_mesh/fs_mesh).

struct U {
    hinv0: vec4<f32>,
    hinv1: vec4<f32>,
    hinv2: vec4<f32>,
    surf: vec4<f32>,  // ancho_mm, alto_mm, 0, 0
    out: vec4<f32>,   // ancho_px, alto_px de la salida, 0, 0
};

@group(0) @binding(0) var canvas: texture_2d<f32>;
@group(0) @binding(1) var samp: sampler;
@group(0) @binding(2) var<uniform> u: U;

fn sample_mm(mm: vec2<f32>) -> vec4<f32> {
    let uv = mm / u.surf.xy;
    if (any(uv < vec2<f32>(0.0)) || any(uv > vec2<f32>(1.0))) {
        return vec4<f32>(0.0, 0.0, 0.0, 1.0);
    }
    let c = textureSampleLevel(canvas, samp, uv, 0.0);
    return vec4<f32>(c.rgb, 1.0);
}

@vertex
fn vs_main(@builtin(vertex_index) i: u32) -> @builtin(position) vec4<f32> {
    let x = f32((i << 1u) & 2u);
    let y = f32(i & 2u);
    return vec4<f32>(x * 2.0 - 1.0, 1.0 - y * 2.0, 0.0, 1.0);
}

@fragment
fn fs_main(@builtin(position) pos: vec4<f32>) -> @location(0) vec4<f32> {
    let p = vec3<f32>(pos.xy, 1.0);
    let q = vec3<f32>(dot(u.hinv0.xyz, p), dot(u.hinv1.xyz, p), dot(u.hinv2.xyz, p));
    if (q.z <= 0.0) {
        return vec4<f32>(0.0, 0.0, 0.0, 1.0);
    }
    return sample_mm(q.xy / q.z);
}

struct MeshOut {
    @builtin(position) pos: vec4<f32>,
    @location(0) mm: vec2<f32>,
};

@vertex
fn vs_mesh(@location(0) v: vec4<f32>) -> MeshOut {
    // v.xy: px de salida (centro del píxel (0,0) en (0,0)); v.zw: mm de la superficie.
    let px = v.xy + vec2<f32>(0.5);
    var o: MeshOut;
    o.pos = vec4<f32>(px.x / u.out.x * 2.0 - 1.0, 1.0 - px.y / u.out.y * 2.0, 0.0, 1.0);
    o.mm = v.zw;
    return o;
}

@fragment
fn fs_mesh(in: MeshOut) -> @location(0) vec4<f32> {
    return sample_mm(in.mm);
}
