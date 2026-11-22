//! Orígenes que pueden hablar con el motor: el gateway desplegado y cualquier puerto de `http://localhost` y
//! `http://127.0.0.1` (la consola en desarrollo). La misma lista vale para CORS y para el `Origin` del WebSocket.

/// Gateway desplegado: origen permitido por defecto y base de la URL de emparejamiento.
pub const GATEWAY_URL: &str = "https://pcx-gateway.onrender.com";

#[derive(Debug, Clone)]
pub struct Origins {
    exact: Vec<String>,
}

impl Origins {
    /// `gateway` puede venir con ruta (`https://x.example/consola`): solo cuenta su origen. `extra`: orígenes exactos.
    pub fn new(gateway: &str, extra: &[String]) -> Self {
        let mut exact = vec![origin_of(gateway)];
        exact.extend(extra.iter().map(|o| origin_of(o.trim())).filter(|o| !o.is_empty()));
        Origins { exact }
    }

    pub fn allows(&self, origin: &str) -> bool {
        self.exact.iter().any(|a| a == origin) || loopback_http(origin)
    }
}

/// `esquema://host[:puerto]` de una URL (sin ruta, consulta ni barra final).
pub fn origin_of(url: &str) -> String {
    match url.split_once("://") {
        Some((scheme, rest)) => {
            let host = rest.split(['/', '?', '#']).next().unwrap_or("");
            format!("{}://{}", scheme.to_ascii_lowercase(), host.to_ascii_lowercase())
        }
        None => url.to_string(),
    }
}

/// `http://localhost[:puerto]` o `http://127.0.0.1[:puerto]`, y nada más (ni subdominios ni rutas).
fn loopback_http(origin: &str) -> bool {
    ["http://localhost", "http://127.0.0.1"].iter().any(|base| match origin.strip_prefix(base) {
        Some("") => true,
        Some(rest) => {
            rest.strip_prefix(':').is_some_and(|p| (1..=5).contains(&p.len()) && p.bytes().all(|b| b.is_ascii_digit()))
        }
        None => false,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lista_del_protocolo() {
        let o = Origins::new(GATEWAY_URL, &[]);
        for ok in [
            "https://pcx-gateway.onrender.com",
            "http://localhost",
            "http://localhost:5173",
            "http://127.0.0.1:8000",
            "http://127.0.0.1",
        ] {
            assert!(o.allows(ok), "{ok}");
        }
        for bad in [
            "https://evil.example",
            "http://pcx-gateway.onrender.com",
            "https://pcx-gateway.onrender.com.evil.example",
            "https://localhost:5173",
            "http://localhost.evil.example",
            "http://localhost:5173.evil.example",
            "http://localhost:",
            "http://localhost:abc",
            "http://localhost:123456",
            "http://127.0.0.1.evil.example",
            "http://127.0.0.10",
            "http://[::1]:5173",
            "null",
            "",
        ] {
            assert!(!o.allows(bad), "{bad}");
        }
    }

    #[test]
    fn gateway_propio_y_extras() {
        let o = Origins::new("https://Mi-Gateway.example/consola/?x=1", &["https://otra.example/".into(), " ".into()]);
        assert!(o.allows("https://mi-gateway.example"));
        assert!(o.allows("https://otra.example"));
        assert!(!o.allows("https://pcx-gateway.onrender.com"), "el gateway por defecto se reemplaza");
    }
}
