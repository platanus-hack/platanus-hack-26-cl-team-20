//! Persistencia del nodo: un archivo SQLite en el directorio de datos del SO.
//!
//! Tablas: `meta` (stream_id, last_seq, política…), `events` (log append-only, `seq` durable), `world` (snapshot),
//! `catalog` (objetos conocidos), `calibrations` (versionadas; los LUT van como archivos), `flows`, `devices`,
//! `audit` (tool calls), `requests` (idempotencia por `request_id`) y `confirmations` (home pendientes).
//! Migraciones simples por `PRAGMA user_version`. Todo acceso pasa por un `Mutex` (operaciones cortas).

use std::path::{Path, PathBuf};
use std::sync::Mutex;

use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::proto::{now_ns, Envelope, HomePolicy, LogRetention};

const MIGRATIONS: &[&str] = &[
    // v1
    "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
     CREATE TABLE events (seq INTEGER PRIMARY KEY, type TEXT NOT NULL, t_pub_ns INTEGER NOT NULL, json TEXT NOT NULL);
     CREATE INDEX events_type ON events(type, seq);
     CREATE TABLE world (id INTEGER PRIMARY KEY CHECK (id = 1), cursor INTEGER NOT NULL, json TEXT NOT NULL, t_ns INTEGER NOT NULL);
     CREATE TABLE catalog (name TEXT PRIMARY KEY, prompt TEXT NOT NULL DEFAULT '', crops TEXT NOT NULL DEFAULT '[]', updated_ns INTEGER NOT NULL);
     CREATE TABLE calibrations (id INTEGER PRIMARY KEY AUTOINCREMENT, from_frame TEXT NOT NULL, to_frame TEXT NOT NULL,
        version INTEGER NOT NULL, kind TEXT NOT NULL, json TEXT NOT NULL, created_ns INTEGER NOT NULL);
     CREATE TABLE flows (id TEXT PRIMARY KEY, request_id TEXT NOT NULL, json TEXT NOT NULL, updated_ns INTEGER NOT NULL);
     CREATE TABLE devices (id TEXT PRIMARY KEY, json TEXT NOT NULL, updated_ns INTEGER NOT NULL);
     CREATE TABLE audit (id INTEGER PRIMARY KEY AUTOINCREMENT, t_ns INTEGER NOT NULL, origin TEXT NOT NULL, tool TEXT NOT NULL,
        call_id TEXT, request_id TEXT, args TEXT NOT NULL, ok INTEGER NOT NULL, ms REAL NOT NULL, result TEXT);
     CREATE TABLE requests (request_id TEXT PRIMARY KEY, tool TEXT NOT NULL, result TEXT NOT NULL, t_ns INTEGER NOT NULL);
     CREATE TABLE confirmations (id TEXT PRIMARY KEY, request_id TEXT NOT NULL, digest TEXT NOT NULL, action TEXT NOT NULL,
        expires_ns INTEGER NOT NULL, status TEXT NOT NULL, t_ns INTEGER NOT NULL);",
];

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct CatalogEntry {
    pub name: String,
    #[serde(default)]
    pub prompt: String,
    /// Rutas a recortes de apariencia (archivos en el directorio de datos).
    #[serde(default)]
    pub crops: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct AuditRow {
    pub t_ns: i64,
    pub origin: String,
    pub tool: String,
    pub call_id: Option<String>,
    pub request_id: Option<String>,
    pub ok: bool,
    pub ms: f64,
}

pub struct Store {
    conn: Mutex<Connection>,
    pub path: Option<PathBuf>,
    pub stream_id: String,
    pub retention: LogRetention,
}

fn lock(m: &Mutex<Connection>) -> std::sync::MutexGuard<'_, Connection> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

impl Store {
    /// Abre (o crea) `dir/pcx.db`. `None` = en memoria (tests).
    pub fn open(dir: Option<&Path>) -> anyhow::Result<Store> {
        let (conn, path) = match dir {
            Some(d) => {
                std::fs::create_dir_all(d)?;
                let p = d.join("pcx.db");
                (Connection::open(&p)?, Some(p))
            }
            None => (Connection::open_in_memory()?, None),
        };
        conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA busy_timeout=5000;")?;
        let ver: i64 = conn.query_row("PRAGMA user_version", [], |r| r.get(0))?;
        for (i, m) in MIGRATIONS.iter().enumerate().skip(ver as usize) {
            conn.execute_batch(&format!("BEGIN; {m}; PRAGMA user_version = {}; COMMIT;", i + 1))?;
        }
        let stream_id = match conn.query_row("SELECT value FROM meta WHERE key='stream_id'", [], |r| r.get::<_, String>(0)).optional()? {
            Some(s) => s,
            None => {
                let s = format!("st-{:016x}", rand::random::<u64>());
                conn.execute("INSERT INTO meta(key, value) VALUES ('stream_id', ?1)", [&s])?;
                s
            }
        };
        Ok(Store { conn: Mutex::new(conn), path, stream_id, retention: LogRetention { max_events: 100_000, max_age_s: 86_400.0 } })
    }

    fn meta_get(&self, key: &str) -> Option<String> {
        lock(&self.conn).query_row("SELECT value FROM meta WHERE key=?1", [key], |r| r.get(0)).optional().ok().flatten()
    }

    fn meta_set(&self, key: &str, value: &str) {
        let _ = lock(&self.conn).execute(
            "INSERT INTO meta(key, value) VALUES (?1, ?2) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            params![key, value],
        );
    }

    // ------------------------------------------------------------ log de eventos

    /// Último `seq` asignado (persistente aunque la retención borre eventos).
    pub fn last_seq(&self) -> i64 {
        self.meta_get("last_seq").and_then(|s| s.parse().ok()).unwrap_or(0)
    }

    /// Primer `seq` retenido (0 si el log está vacío).
    pub fn first_seq(&self) -> i64 {
        lock(&self.conn).query_row("SELECT COALESCE(MIN(seq), 0) FROM events", [], |r| r.get(0)).unwrap_or(0)
    }

    /// Agrega un sobre ya secuenciado y avanza `last_seq` en la misma transacción.
    pub fn append(&self, env: &Envelope) -> anyhow::Result<()> {
        let json = serde_json::to_string(env)?;
        let mut c = lock(&self.conn);
        let tx = c.transaction()?;
        tx.execute("INSERT INTO events(seq, type, t_pub_ns, json) VALUES (?1, ?2, ?3, ?4)", params![env.seq, env.type_, env.t_pub_ns, json])?;
        tx.execute(
            "INSERT INTO meta(key, value) VALUES ('last_seq', ?1) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            [env.seq.to_string()],
        )?;
        tx.commit()?;
        if env.seq % 500 == 0 {
            let cut = now_ns() - (self.retention.max_age_s * 1e9) as i64;
            let _ = c.execute("DELETE FROM events WHERE seq <= ?1 OR t_pub_ns < ?2", params![env.seq - self.retention.max_events, cut]);
        }
        Ok(())
    }

    /// Eventos con `seq > cursor` (filtrados por tipo). Devuelve (eventos, último seq examinado).
    pub fn after(&self, cursor: i64, types: &[String], limit: i64) -> (Vec<Envelope>, i64) {
        let c = lock(&self.conn);
        let mut out = Vec::new();
        let mut last = cursor;
        let sql = "SELECT seq, type, json FROM events WHERE seq > ?1 ORDER BY seq LIMIT ?2";
        let Ok(mut st) = c.prepare_cached(sql) else { return (out, cursor) };
        // se examinan en páginas para que el filtro no corte el cursor
        let page = 1000;
        let mut from = cursor;
        loop {
            let rows: Vec<(i64, String, String)> = st
                .query_map(params![from, page], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))
                .map(|it| it.filter_map(Result::ok).collect())
                .unwrap_or_default();
            let n = rows.len();
            for (seq, ty, json) in rows {
                last = seq;
                from = seq;
                if types.is_empty() || types.contains(&ty) {
                    if let Ok(e) = serde_json::from_str(&json) {
                        out.push(e);
                    }
                    if out.len() as i64 >= limit {
                        return (out, last);
                    }
                }
            }
            if n < page as usize {
                return (out, last);
            }
        }
    }

    // ------------------------------------------------------------ snapshot del mundo

    pub fn save_world(&self, cursor: i64, world: &Value) {
        let _ = lock(&self.conn).execute(
            "INSERT INTO world(id, cursor, json, t_ns) VALUES (1, ?1, ?2, ?3)
             ON CONFLICT(id) DO UPDATE SET cursor=excluded.cursor, json=excluded.json, t_ns=excluded.t_ns",
            params![cursor, world.to_string(), now_ns()],
        );
    }

    pub fn load_world(&self) -> Option<Value> {
        let s: Option<String> = lock(&self.conn).query_row("SELECT json FROM world WHERE id=1", [], |r| r.get(0)).optional().ok().flatten();
        s.and_then(|s| serde_json::from_str(&s).ok())
    }

    // ------------------------------------------------------------ catálogo, calibraciones, flujos, dispositivos

    pub fn catalog_upsert(&self, e: &CatalogEntry) -> anyhow::Result<()> {
        lock(&self.conn).execute(
            "INSERT INTO catalog(name, prompt, crops, updated_ns) VALUES (?1, ?2, ?3, ?4)
             ON CONFLICT(name) DO UPDATE SET prompt=excluded.prompt, crops=excluded.crops, updated_ns=excluded.updated_ns",
            params![e.name, e.prompt, serde_json::to_string(&e.crops)?, now_ns()],
        )?;
        Ok(())
    }

    pub fn catalog(&self) -> Vec<CatalogEntry> {
        let c = lock(&self.conn);
        let Ok(mut st) = c.prepare("SELECT name, prompt, crops FROM catalog ORDER BY name") else { return vec![] };
        st.query_map([], |r| {
            let crops: String = r.get(2)?;
            Ok(CatalogEntry { name: r.get(0)?, prompt: r.get(1)?, crops: serde_json::from_str(&crops).unwrap_or_default() })
        })
        .map(|it| it.filter_map(Result::ok).collect())
        .unwrap_or_default()
    }

    /// Guarda una calibración (transformación versionada). Devuelve el id de fila.
    pub fn calibration_add(&self, t: &pcx_contracts::Transform) -> anyhow::Result<i64> {
        let kind = match t {
            pcx_contracts::Transform::Homography(_) => "homography",
            pcx_contracts::Transform::Lut(_) => "lut",
        };
        let c = lock(&self.conn);
        c.execute(
            "INSERT INTO calibrations(from_frame, to_frame, version, kind, json, created_ns) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![t.from_frame(), t.to_frame(), t.version(), kind, serde_json::to_string(t)?, now_ns()],
        )?;
        Ok(c.last_insert_rowid())
    }

    /// Última versión de cada par (from, to).
    pub fn calibrations_latest(&self) -> Vec<pcx_contracts::Transform> {
        let c = lock(&self.conn);
        let Ok(mut st) = c.prepare(
            "SELECT json FROM calibrations c WHERE id = (SELECT id FROM calibrations d WHERE d.from_frame=c.from_frame
             AND d.to_frame=c.to_frame ORDER BY version DESC, id DESC LIMIT 1) ORDER BY id",
        ) else {
            return vec![];
        };
        st.query_map([], |r| r.get::<_, String>(0))
            .map(|it| it.filter_map(Result::ok).filter_map(|s| serde_json::from_str(&s).ok()).collect())
            .unwrap_or_default()
    }

    pub fn flow_save(&self, id: &str, request_id: &str, flow: &Value) {
        let _ = lock(&self.conn).execute(
            "INSERT INTO flows(id, request_id, json, updated_ns) VALUES (?1, ?2, ?3, ?4)
             ON CONFLICT(id) DO UPDATE SET request_id=excluded.request_id, json=excluded.json, updated_ns=excluded.updated_ns",
            params![id, request_id, flow.to_string(), now_ns()],
        );
    }

    pub fn flows(&self) -> Vec<(String, Value)> {
        let c = lock(&self.conn);
        let Ok(mut st) = c.prepare("SELECT id, json FROM flows ORDER BY id") else { return vec![] };
        st.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))
            .map(|it| it.filter_map(Result::ok).map(|(i, j)| (i, serde_json::from_str(&j).unwrap_or(Value::Null))).collect())
            .unwrap_or_default()
    }

    pub fn device_save(&self, id: &str, dev: &Value) {
        let _ = lock(&self.conn).execute(
            "INSERT INTO devices(id, json, updated_ns) VALUES (?1, ?2, ?3)
             ON CONFLICT(id) DO UPDATE SET json=excluded.json, updated_ns=excluded.updated_ns",
            params![id, dev.to_string(), now_ns()],
        );
    }

    pub fn devices(&self) -> Vec<Value> {
        let c = lock(&self.conn);
        let Ok(mut st) = c.prepare("SELECT json FROM devices ORDER BY id") else { return vec![] };
        st.query_map([], |r| r.get::<_, String>(0))
            .map(|it| it.filter_map(Result::ok).filter_map(|s| serde_json::from_str(&s).ok()).collect())
            .unwrap_or_default()
    }

    // ------------------------------------------------------------ auditoría e idempotencia

    #[allow(clippy::too_many_arguments)]
    pub fn audit(&self, origin: &str, tool: &str, call_id: Option<&str>, request_id: Option<&str>, args: &Value, ok: bool, ms: f64, result: &str) {
        let _ = lock(&self.conn).execute(
            "INSERT INTO audit(t_ns, origin, tool, call_id, request_id, args, ok, ms, result) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            params![now_ns(), origin, tool, call_id, request_id, args.to_string(), ok, ms, result],
        );
    }

    pub fn audit_tail(&self, n: i64) -> Vec<AuditRow> {
        let c = lock(&self.conn);
        let Ok(mut st) = c.prepare("SELECT t_ns, origin, tool, call_id, request_id, ok, ms FROM audit ORDER BY id DESC LIMIT ?1") else {
            return vec![];
        };
        st.query_map([n], |r| {
            Ok(AuditRow { t_ns: r.get(0)?, origin: r.get(1)?, tool: r.get(2)?, call_id: r.get(3)?, request_id: r.get(4)?, ok: r.get(5)?, ms: r.get(6)? })
        })
        .map(|it| it.filter_map(Result::ok).collect())
        .unwrap_or_default()
    }

    /// Resultado ya calculado para `request_id` dentro de la ventana de idempotencia.
    pub fn request_get(&self, request_id: &str, window_s: i64) -> Option<Value> {
        let cut = now_ns() - window_s * 1_000_000_000;
        let s: Option<String> = lock(&self.conn)
            .query_row("SELECT result FROM requests WHERE request_id=?1 AND t_ns >= ?2", params![request_id, cut], |r| r.get(0))
            .optional()
            .ok()
            .flatten();
        s.and_then(|s| serde_json::from_str(&s).ok())
    }

    pub fn request_put(&self, request_id: &str, tool: &str, result: &Value) {
        let c = lock(&self.conn);
        let _ = c.execute(
            "INSERT INTO requests(request_id, tool, result, t_ns) VALUES (?1, ?2, ?3, ?4)
             ON CONFLICT(request_id) DO UPDATE SET tool=excluded.tool, result=excluded.result, t_ns=excluded.t_ns",
            params![request_id, tool, result.to_string(), now_ns()],
        );
        let _ = c.execute("DELETE FROM requests WHERE t_ns < ?1", [now_ns() - 3_600 * 1_000_000_000]);
    }

    // ------------------------------------------------------------ política y confirmaciones

    pub fn home_policy(&self) -> Option<HomePolicy> {
        self.meta_get("home_policy").and_then(|s| serde_json::from_str(&s).ok())
    }

    pub fn set_home_policy(&self, p: &HomePolicy) {
        self.meta_set("home_policy", &serde_json::to_string(p).unwrap_or_default());
    }

    pub fn confirmation_put(&self, id: &str, request_id: &str, digest: &str, action: &Value, expires_ns: i64, status: &str) {
        let _ = lock(&self.conn).execute(
            "INSERT INTO confirmations(id, request_id, digest, action, expires_ns, status, t_ns) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)
             ON CONFLICT(id) DO UPDATE SET status=excluded.status, t_ns=excluded.t_ns",
            params![id, request_id, digest, action.to_string(), expires_ns, status, now_ns()],
        );
    }

    pub fn confirmations_pending(&self) -> Vec<(String, String, String, i64)> {
        let c = lock(&self.conn);
        let Ok(mut st) = c.prepare("SELECT id, request_id, digest, expires_ns FROM confirmations WHERE status='pending' ORDER BY t_ns") else {
            return vec![];
        };
        st.query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?))).map(|it| it.filter_map(Result::ok).collect()).unwrap_or_default()
    }
}

/// Directorio de datos del SO (`directories`): macOS `~/Library/Application Support/pcx`, Linux `~/.local/share/pcx`,
/// Windows `%APPDATA%\pcx\data`. `PCX_DATA_DIR` lo cambia.
pub fn default_data_dir() -> PathBuf {
    if let Ok(d) = std::env::var("PCX_DATA_DIR") {
        if !d.is_empty() {
            return PathBuf::from(d);
        }
    }
    directories::ProjectDirs::from("", "", "pcx").map(|p| p.data_dir().to_path_buf()).unwrap_or_else(|| std::env::temp_dir().join("pcx"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::proto::{wrap, Event, Payload};

    #[test]
    fn seq_y_stream_persisten_y_tablas() {
        let dir = tempfile::tempdir().unwrap();
        let st = Store::open(Some(dir.path())).unwrap();
        let sid = st.stream_id.clone();
        for seq in 1..=3 {
            let ev = Event::ObjectRemoved { object_id: format!("o{seq}"), label: None };
            st.append(&wrap(Payload::Event(ev), seq, "t", "n", None)).unwrap();
        }
        let (evs, cur) = st.after(1, &["object_removed".into()], 10);
        assert_eq!((evs.len(), cur), (2, 3));
        st.catalog_upsert(&CatalogEntry { name: "taza".into(), prompt: "taza roja".into(), crops: vec!["crops/taza-1.png".into()] }).unwrap();
        st.request_put("r1", "project", &serde_json::json!({"ok": 1}));
        drop(st);
        let st = Store::open(Some(dir.path())).unwrap();
        assert_eq!((st.last_seq(), st.first_seq(), st.stream_id.clone()), (3, 1, sid));
        assert_eq!(st.catalog()[0].crops, vec!["crops/taza-1.png".to_string()]);
        assert_eq!(st.request_get("r1", 600), Some(serde_json::json!({"ok": 1})));
    }
}
