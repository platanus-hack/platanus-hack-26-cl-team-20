// Pasada final: lienzo de la superficie (mm × ppmm) → px del proyector.
// modo 0: homografía inversa por píxel (px → mm). modo 1: LUT por píxel (px → mm, del gray code).

struct U {
    hinv0: vec4<f32>,
    hinv1: vec4<f32>,
    hinv2: vec4<f32>,
    surf: vec4<f32>,  // ancho_mm, alto_mm, modo, 0
    lut: vec4<f32>,   // lut_w, lut_h, out_w, out_h
};

@group(0) @binding(0) var canvas: texture_2d<f32>;
@group(0) @binding(1) var samp: sampler;
@group(0) @binding(2) var<uniform> u: U;
@group(0) @binding(3) var lut: texture_2d<f32>;

@vertex
fn vs_main(@builtin(vertex_index) i: u32) -> @builtin(position) vec4<f32> {
    let x = f32((i << 1u) & 2u);
    let y = f32(i & 2u);
    return vec4<f32>(x * 2.0 - 1.0, 1.0 - y * 2.0, 0.0, 1.0);
}

@fragment
fn fs_main(@builtin(position) pos: vec4<f32>) -> @location(0) vec4<f32> {
    var mm: vec2<f32>;
    if (u.surf.z > 0.5) {
        let lp = vec2<i32>(floor(pos.xy * u.lut.xy / u.lut.zw));
        let m = textureLoad(lut, clamp(lp, vec2<i32>(0), vec2<i32>(u.lut.xy) - 1), 0).xy;
        if (m.x < 0.0) {
            return vec4<f32>(0.0, 0.0, 0.0, 1.0);
        }
        mm = m;
    } else {
        let p = vec3<f32>(pos.xy, 1.0);
        let q = vec3<f32>(dot(u.hinv0.xyz, p), dot(u.hinv1.xyz, p), dot(u.hinv2.xyz, p));
        if (q.z <= 0.0) {
            return vec4<f32>(0.0, 0.0, 0.0, 1.0);
        }
        mm = q.xy / q.z;
    }
    let uv = mm / u.surf.xy;
    if (any(uv < vec2<f32>(0.0)) || any(uv > vec2<f32>(1.0))) {
        return vec4<f32>(0.0, 0.0, 0.0, 1.0);
    }
    let c = textureSampleLevel(canvas, samp, uv, 0.0);
    return vec4<f32>(c.rgb, 1.0);
}
