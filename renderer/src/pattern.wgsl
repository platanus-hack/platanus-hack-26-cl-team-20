@group(0) @binding(0) var pattern: texture_2d<f32>;

@vertex
fn vs_main(@builtin(vertex_index) i: u32) -> @builtin(position) vec4<f32> {
    let x = f32((i << 1u) & 2u);
    let y = f32(i & 2u);
    return vec4<f32>(x * 2.0 - 1.0, 1.0 - y * 2.0, 0.0, 1.0);
}

@fragment
fn fs_main(@builtin(position) pos: vec4<f32>) -> @location(0) vec4<f32> {
    if any(vec2<u32>(pos.xy) >= textureDimensions(pattern)) {
        return vec4<f32>(0.0, 0.0, 0.0, 1.0);
    }
    let rgba = textureLoad(pattern, vec2<i32>(pos.xy), 0);
    return vec4<f32>(rgba.rgb * rgba.a, 1.0);
}
