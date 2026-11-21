# Platanus Hack 26 — equipo

Track: **Physical World**. Proyector + cámara que entiende el espacio y lo vuelve interactivo, controlado por agentes.

Repo privado de trabajo mientras la organización entrega el oficial. Nombre de producto por definir.

## Dos versiones del engine

| Versión | Dónde | Para qué |
|---|---|---|
| **Original de Max** | `github.com/MAXMARDONES/sl-scanner` @ `b8fecff` | Es la que corre con el proyector y la cámara. No se toca. |
| **Copia del equipo** | `engine/` de este repo (subtree del mismo commit) | La que se ordena, limpia y extiende. |

Para traer cambios nuevos de Max: `git subtree pull --prefix=engine git@github.com:MAXMARDONES/sl-scanner.git master --squash`.

Cómo correr el engine: `engine/README.md` (Python 3.12 o 3.13, no 3.14).

## Estructura

| Carpeta | Qué va | Responsable |
|---|---|---|
| `engine/` | sl-scanner de Max: calibración, escena, efectos, API y MCP | Max (original), Manu (orden) |
| `agent/` | Ordenar el engine y conectar agentes a su API/MCP | Manu, Daniel |
| `experiences/` | Dolor elegido, casos de uso, guion de la demo, nombre y marca | Pablo, Matilda |
| `hardware/` | Rig de proyector y cámara, piezas 3D (fuentes y STL), lista de equipos | 3D |
| `docs/` | `PLAN.md` (plan vivo), `RUBRICA.md`, `CONTRATO.md`, `transcripciones/` de las conversaciones, `investigacion/` | orquestador de Daniel + todos |

## Empezar

1. Lee `docs/PLAN.md` (5 min): idea, reparto, rúbrica, feedback del mentor.
2. Lee el README de tu carpeta.

## Reglas mínimas

- `main` siempre corre. Trabajo en ramas cortas o commits chicos directos si no rompen nada.
- Nada de secretos en el repo: llaves en `.env` local (ver `.env.example`).
- El contrato entre `engine/` y `agent/` vive en `docs/CONTRATO.md`: si cambia, se avisa en el grupo.
- Cada carpeta tiene su propio README con cómo correrla.

## Fechas

- Fin del hacking: domingo 22-nov, 08:30–09:00 (por confirmar en el dashboard).
- Demo: 5 minutos.

## Dónde está cada cosa (cierre viernes → sábado 04:30)

| Qué | Dónde |
|---|---|
| Plan vivo y prioridades del sábado | `docs/PLAN.md` |
| Tracks y rúbrica | `docs/TRACKS.md`, `docs/RUBRICA.md` |
| Transcripciones curadas de reuniones | `docs/transcripciones/` |
| Investigación (arte previo, dolor, ICP) | `docs/investigacion/` |
| Estado de cada frente (engine, impresión, riemann) | `docs/estado/` |
| Bitácora del orquestador y encargos a agentes | `docs/orquestador/` |
| Cases 3D (badge, soporte pantalla Waveshare) | `hardware/3d/` (el `.stp` de referencia de Waveshare, 25 MB, se baja del sitio del fabricante) |
| Engine (copia) | `engine/` — original en `MAXMARDONES/sl-scanner`; efectos de la mesa de Daniel en la rama `hack/daniel-mesa` |
| Mesa (anfitrión, micrófono, objetos) | rama `mesa` |
| Plataforma pcx de Manu | rama `manu/plataforma` |
| Scripts auxiliares (dashboard hacker, proxy de tokens) | `herramientas/` |
