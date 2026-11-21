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
| `agent/` | Puente para que un agente controle el engine: API, tools/MCP, ejemplos | Manu, Daniel |
| `experiences/` | Casos de uso y escenas que se muestran en la demo | Pablo, Matilda |
| `hardware/` | Rig de proyector y cámara, piezas 3D (fuentes y STL), lista de equipos | 3D |
| `docs/` | Rúbrica, decisiones, pitch, guion de la demo | todos |

## Reglas mínimas

- `main` siempre corre. Trabajo en ramas cortas o commits chicos directos si no rompen nada.
- Nada de secretos en el repo: llaves en `.env` local (ver `.env.example`).
- El contrato entre `engine/` y `agent/` vive en `docs/CONTRATO.md`: si cambia, se avisa en el grupo.
- Cada carpeta tiene su propio README con cómo correrla.

## Fechas

- Fin del hacking: domingo 22-nov, 08:30–09:00 (por confirmar en el dashboard).
- Demo: 5 minutos.
