//! Baja los modelos ONNX a la caché del SO (o `$PCX_MODELS_DIR`) y verifica su sha256.
//! `cargo run -p pcx-hands --example fetch_models`
fn main() -> Result<(), pcx_hands::HandsError> {
    let (palm, lm) = pcx_hands::models::ensure_all()?;
    println!("{}\n{}", palm.display(), lm.display());
    Ok(())
}
