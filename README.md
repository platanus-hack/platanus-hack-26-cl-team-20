# Platanus Hack 26 — equipo

Track: **Physical World**. Proyector + cámara que entiende el espacio y lo vuelve interactivo, controlado por agentes.

Repo privado de trabajo mientras la organización entrega el oficial. Nombre de producto por definir.

## Estructura

| Carpeta | Qué va | Responsable |
|---|---|---|
| `engine/` | Calibración proyector-cámara, mapeo 3D, segmentación, tracking, salida al proyector | Max |
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
