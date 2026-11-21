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
| 1 | Plugin y skills para agentes (Claude Code plugin + Agent Skills para Codex y OpenCode + instalador), rama `feat/plugin-agentes` | Claude Fable | en curso |
| — | **UI (landing y consola): NO** hasta que el equipo decida diseño | — | en pausa |

## Registro nocturno
- 05:45 · Disco llegó a 0,2 GB por 4 compilaciones en paralelo; limpieza de duplicados en `target/` (−9,7 GB). Monitor con limpieza automática (`ops/dedupe_target.py` fuera del repo) cuando el disco baja de 6 GB. Lección: máximo 2 frentes compilando Rust a la vez.
- 05:50 · CI listo → PR #2 (las pruebas corren en GitHub, no en el disco local). PR general #1 (borrador) hacia `main`.
- 05:54 · Codex alcanzó su límite de uso (se restablece 06:16). Seguridad (C) y despliegue quedaron con cambios sin commitear en sus worktrees; se reanudan solos a las 06:17.
- 05:55 · Render: el equipo tiene **USD 50 en créditos** (cuenta de Manu). Investigando precios y plan de gasto; **no desplegar** hasta que Manu lo apruebe.
- 06:00 · Paridad con la rama `mesa` lista → `docs/arquitectura/PARIDAD-MESA.md` (22 tareas en los bloques R, S, C y M). Integrada en `manu/plataforma`.
- 06:02 · Lanzadas **R1** (foto limpia, crate nuevo `pcx-perception`, rama `feat/foto-limpia`) y **R4+R5** (nivel N2b barato + rectificador polinomial, rama `feat/objetos-rapidos`), ambas con Claude Fable. Siguen en la cola, sin hardware: S1 (catálogo v2), M1 (modos `pcx mode`) y M5 (`free_rect`). Después: R2→R3, S2→S3, C1→C2 y M2–M4.
- 06:05 · Presupuesto de Render (USD 50) → `docs/RENDER-PRESUPUESTO.md`. Plan recomendado: USD 1,53 por 48 h y USD 23,30 al mes. Cambios al `render.yaml` pendientes de aplicar sobre el frente de Codex: `diskSizeGB: 1`, `autoDeployTrigger: checksPass`, static site, `numInstances: 1`.
- 06:00 · **B · cableado listo** → PR #3 (`feat/cableado`): captura nativa AVFoundation, workers aislados y supervisor `pcx up`; 129 tests. En revisión con Claude Fable; la revisión de Codex queda para cuando se restablezca su cuota. La CI de PR #2 está en curso (web, python y contracts en verde; rust pendiente).
