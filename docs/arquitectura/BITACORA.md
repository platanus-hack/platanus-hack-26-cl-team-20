# Bitácora de pcx (orquestador de Manu)

Registro cronológico para retomar el trabajo después de una compactación de contexto o en otra sesión. Lo más reciente va abajo. Estado consolidado en [ESTADO.md](ESTADO.md).

## Cómo trabajamos
- Rama de integración: `manu/plataforma`. Cada frente va en su propia rama o worktree → PR hacia `manu/plataforma` → revisión cruzada (Codex revisa lo de Claude y viceversa) → merge si los tests pasan. `main` no se toca sin Manu (PR general en borrador).
- Modelos: Claude Fable 5.2 para lo complejo; Codex `-m gpt-6-astra`. Todo pasa por el proxy de la hackathon (leaderboard).
- Rust compila en `CARGO_TARGET_DIR=<repo>/target`, compartido. El disco se llenó 3 veces: hay que deduplicar `target/debug/deps` (dejar el hash más nuevo de cada crate) y borrar `incremental`.
- Codex: `codex exec ... < /dev/null`. Sin cerrar stdin se cuelga esperando entrada.

## Sáb 21-nov
- 00:30–03:00 · Lectura del engine (8 informes), arquitectura, prototipos (renderer Rust/web, MCP), revisiones independientes.
- 03:00–04:30 · Fase 1: contratos v2 → v2.1, core Rust, captura, manos, objetos, calibración, gateway, renderer. 112 tests.
- 04:00–05:30 · Hardware real (T749 + C920): 60 fps, 215 ms. Demo del techo: 5 planos, 0,8 px. Landing exploratoria. Diagramas UML. Push.
- 05:36 · Manu se va a dormir; sin hardware hasta la mañana. Plan nocturno abajo.

## Plan nocturno (05:40 →)
| Ola | Frente | Quién | Estado |
|---|---|---|---|
| 1 | B · cableado: captura nativa, worker de manos aislado, `pcx up` | Claude | en curso |
| 1 | C · seguridad y robustez | Codex | en curso |
| 1 | Paridad con la rama `mesa` y estado de pruebas Max/Daniel | Claude Fable | en curso |
| 1 | Despliegue Render listo (Docker, Blueprint, DEPLOY.md, smoke, otras infras) | Codex astra | en curso |
| 1 | CI con GitHub Actions | Claude | en curso |
| 2 | DB: módulo de modelos (SQLAlchemy 2 + Alembic en el gateway; sqlx + migraciones en el nodo) | Codex / Claude Fable | pendiente |
| 2 | Objetos en vivo en menos de 1 s + catálogo pre-escaneado (setup en ~3 s) | Claude Fable | pendiente |
| 2 | Multicámara (lateral: libreta y nivel de líquido) | Codex | pendiente |
| 2 | Modos de las 3 escenas (Vibecoders Desk, Edu, Lab) + pcx-agent | Claude Fable | pendiente |
| 3 | E2E simulado completo (gateway + nodo + Claude Code por MCP) | Claude | pendiente |
| 3 | Distribución: instaladores y workflow de release | Codex | pendiente |
| — | **UI (landing y consola): NO** hasta que el equipo decida diseño | — | en pausa |
