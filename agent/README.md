# Agente ↔ engine

Responsables: **Manu** (con Daniel).

Primero: ordenar `engine/` (el código de Max) para que se entienda y se pueda extender. Después: que un agente lo controle.

## Punto de partida
- El engine ya expone API HTTP y MCP: ver `docs/CONTRATO.md`.
- Cómo correrlo: `engine/README.md` (Python 3.12 o 3.13).
- El original de Max (`MAXMARDONES/sl-scanner`) no se toca: es el que corre con el proyector. Los cambios van acá.

## Metas
1. `engine/` ordenado: qué se usa en la demo y qué es experimento viejo; un README corto de cómo levantarlo.
2. Un agente (Claude Code) conectado al MCP que prenda y ajuste efectos sobre objetos reales.
3. Que el agente **cree** efectos nuevos (escribiendo en `engine/playground/effects/`) a partir de un pedido en lenguaje natural.
