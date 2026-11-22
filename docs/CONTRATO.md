# Contrato engine ↔ agente

**Ya existe.** El engine de Max (`engine/playground/`) trae una API HTTP y un servidor MCP para que un agente controle lo que se proyecta. Partimos de ahí, no de cero.

## API del runtime (puerto 8790)
- `GET /api/state` — estado vivo: objetos de la escena, efectos activos.
- `POST /api/command {cmd, args}` — comandos al runtime.
- `GET /api/events` — eventos (SSE).
- `GET /api/frame.png` — lo que está dibujando el proyector.
- `GET /api/schema` — esquema de comandos.

## Herramientas MCP (`engine/playground/mcp_server.py`)
`pg_runtime`, `pg_state`, `pg_scene_objects`, `pg_find`, `pg_geometry`, `pg_list_effects`, `pg_spawn(effect, target, params)`, `pg_kill`, `pg_set_param`, y más (ver el archivo). Hay una skill en `engine/playground/skill/SKILL.md`.

## Efectos
Un archivo en `engine/playground/effects/` con una clase `Effect`; recarga en caliente al guardar. Un error no tumba el runtime.

## Lo que falta (trabajo de Manu y Daniel)
- Conectar un agente (Claude Code) al MCP y que itere probando efectos sobre objetos reales.
- Que el agente pueda **crear** efectos nuevos (escribir archivos en `effects/`) y no solo prender los existentes.
- Toque y manos: revisar `engine/playground/hands` y `touchsynth`.
- Escena automática (reconocer objetos sin preparar a mano): `engine/docs/ESCENA-AUTOMATICA.md`; requiere modelos locales.
