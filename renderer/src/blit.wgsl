// Mezcla una capa de vello (alfa sin premultiplicar) sobre el lienzo, píxel a píxel.
@group(0) @binding(0) var layer: texture_2d<f32>;

@vertex
fn vs_main(@builtin(vertex_index) i: u32) -> @builtin(position) vec4<f32> {
    let x = f32((i << 1u) & 2u);
    let y = f32(i & 2u);
    return vec4<f32>(x * 2.0 - 1.0, 1.0 - y * 2.0, 0.0, 1.0);
}

@fragment
fn fs_main(@builtin(position) pos: vec4<f32>) -> @location(0) vec4<f32> {
    return textureLoad(layer, vec2<i32>(pos.xy), 0);
}
