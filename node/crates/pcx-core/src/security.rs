//! Credenciales locales e instancia exclusiva, sin secretos compartidos de fábrica.
use std::{
    fs::{File, OpenOptions},
    io::{Read, Write},
    path::Path,
};

pub fn random_token() -> String {
    use rand::RngCore;
    let mut bytes = [0u8; 32];
    rand::rng().fill_bytes(&mut bytes);
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// Compara secretos sin filtrar por tiempo dónde difieren (se comparan sus SHA-256 completos).
pub fn token_eq(a: &[u8], b: &[u8]) -> bool {
    use sha2::{Digest, Sha256};
    let (x, y) = (Sha256::digest(a), Sha256::digest(b));
    x.iter().zip(y.iter()).fold(0u8, |acc, (p, q)| acc | (p ^ q)) == 0
}

pub fn private_dir(dir: &Path) -> std::io::Result<()> {
    let mut builder = std::fs::DirBuilder::new();
    builder.recursive(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::{DirBuilderExt, MetadataExt, PermissionsExt};
        builder.mode(0o700);
        builder.create(dir)?;
        let meta = std::fs::symlink_metadata(dir)?;
        if !meta.is_dir() || meta.uid() != unsafe { libc::geteuid() } {
            return Err(std::io::Error::new(
                std::io::ErrorKind::PermissionDenied,
                "directorio pcx debe pertenecer al usuario y tener modo 0700",
            ));
        }
        if meta.permissions().mode() & 0o077 != 0 {
            std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o700))?;
        }
    }
    #[cfg(not(unix))]
    builder.create(dir)?;
    Ok(())
}

fn options() -> OpenOptions {
    let mut o = OpenOptions::new();
    o.read(true).write(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        o.mode(0o600).custom_flags(libc::O_NOFOLLOW);
    }
    o
}

pub fn instance_lock(dir: &Path) -> anyhow::Result<File> {
    private_dir(dir)?;
    let f = options().create(true).truncate(false).open(dir.join("core.lock"))?;
    f.try_lock().map_err(|e| anyhow::anyhow!("ya hay un core activo en {}: {e}", dir.display()))?;
    Ok(f)
}

/// Lee un secreto de un archivo privado. En Unix no sigue symlinks y rechaza archivos legibles por grupo u otros.
pub fn read_token_file(path: &Path) -> anyhow::Result<String> {
    let mut o = OpenOptions::new();
    o.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        o.custom_flags(libc::O_NOFOLLOW);
    }
    let mut f = o.open(path).map_err(|e| anyhow::anyhow!("{}: {e}", path.display()))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = f.metadata()?.permissions().mode();
        anyhow::ensure!(mode & 0o077 == 0, "{} debe tener permisos 0600 (tiene {:o})", path.display(), mode & 0o777);
    }
    let mut token = String::new();
    f.read_to_string(&mut token)?;
    let token = token.trim().to_owned();
    anyhow::ensure!(!token.is_empty(), "{} está vacío", path.display());
    Ok(token)
}

pub const HANDS_TOKEN_ENV: &str = "PCX_HANDS_TOKEN";
/// Ruta de un archivo 0600 con el token de manos: así lo entrega `pcx up` a sus hijos (nunca por argv).
pub const HANDS_TOKEN_FILE_ENV: &str = "PCX_HANDS_TOKEN_FILE";

/// Token del worker de manos según el entorno: `PCX_HANDS_TOKEN` o el archivo `PCX_HANDS_TOKEN_FILE`.
pub fn hands_token_from_env() -> anyhow::Result<Option<String>> {
    let var = |k: &str| std::env::var(k).ok().filter(|v| !v.is_empty());
    match (var(HANDS_TOKEN_ENV), var(HANDS_TOKEN_FILE_ENV)) {
        (Some(t), _) => Ok(Some(t)),
        (None, Some(f)) => read_token_file(Path::new(&f)).map(Some),
        (None, None) => Ok(None),
    }
}

/// Lo que usa el worker: el entorno o, corriendo suelto, el `hands.token` del directorio de datos del core.
pub fn hands_token_for_worker() -> anyhow::Result<String> {
    match hands_token_from_env()? {
        Some(t) => Ok(t),
        None => load_token(&crate::store::default_data_dir(), "hands"),
    }
}

pub fn load_token(dir: &Path, name: &str) -> anyhow::Result<String> {
    private_dir(dir)?;
    let mut f = options().create(true).truncate(false).open(dir.join(format!("{name}.token")))?;
    f.lock()?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        f.set_permissions(std::fs::Permissions::from_mode(0o600))?;
    }
    let mut token = String::new();
    f.read_to_string(&mut token)?;
    if token.trim().is_empty() {
        token = random_token();
        f.set_len(0)?;
        f.write_all(token.as_bytes())?;
        f.sync_all()?;
    }
    Ok(token.trim().to_owned())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn token_eq_compares_whole_secret() {
        assert!(token_eq(b"Bearer abc", b"Bearer abc"));
        for other in ["", "Bearer ab", "Bearer abd", "Bearer abcd", "bearer abc"] {
            assert!(!token_eq(b"Bearer abc", other.as_bytes()), "{other}");
        }
    }
    #[test]
    fn token_file_must_be_private_and_not_a_symlink() {
        let d = tempfile::tempdir().unwrap();
        let token = load_token(d.path(), "hands").unwrap();
        let file = d.path().join("hands.token");
        assert_eq!(read_token_file(&file).unwrap(), token);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let link = d.path().join("enlace.token");
            std::os::unix::fs::symlink(&file, &link).unwrap();
            assert!(read_token_file(&link).is_err());
            std::fs::set_permissions(&file, std::fs::Permissions::from_mode(0o644)).unwrap();
            assert!(read_token_file(&file).unwrap_err().to_string().contains("0600"));
        }
        assert!(read_token_file(&d.path().join("no-existe")).is_err());
    }
    #[test]
    fn tokens_persist_and_instance_is_exclusive() {
        let d = tempfile::tempdir().unwrap();
        let a = load_token(d.path(), "mcp").unwrap();
        assert_eq!(a.len(), 64);
        assert_eq!(a, load_token(d.path(), "mcp").unwrap());
        assert_ne!(a, load_token(d.path(), "debug").unwrap());
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(std::fs::metadata(d.path().join("mcp.token")).unwrap().permissions().mode() & 0o777, 0o600);
        }
        let lock = instance_lock(d.path()).unwrap();
        assert!(instance_lock(d.path()).is_err());
        drop(lock);
        assert!(instance_lock(d.path()).is_ok());
    }
}
