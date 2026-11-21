# Revisión cruzada del gateway (Claude → Codex, `4fa22f6`) · estado tras la adaptación a contratos v2
Prioridad: **C** crítico · **A** alto · **M** medio. [✔] arreglado en esta rama · [·] pendiente.

1. **C** [✔] Domótica saltable: `home{requires_confirmation:false}` se ejecutaba (el test lo hacía). v2: el nodo decide
   con `HomePolicy`; el gateway reenvía, verifica `action_digest` del `pending` y solo un token **de consola** (scope
   distinto del MCP) puede aprobar en `POST /api/confirmations/{id}`; el toque físico llega como `confirmation_resolved`.
2. **C** [✔] Incompatible con v2: `Hello/Welcome` sin versión ni `stream_id`, sin `hello_rejected`, `history_gap`,
   `replay_done` ni `request_id`; `ObjectState.since` → el fake ni arrancaba. Todo adaptado y probado.
3. **A** [✔] Cursor de `events_wait` = `seq` del nodo: tras un `stream_reset` (seq vuelve a 1) el agente quedaba ciego.
   Ahora es `events.pos` global del gateway; los huecos viajan como `gap`/`gaps` y `history_complete`.
4. **A** [✔] Huecos silenciosos: un lote que saltaba seqs se aceptaba sin rastro. Ahora se guardan los `history_gap`
   declarados (filas `type=history_gap`); un cursor inventado/futuro se reubica al final en vez de bloquear.
5. **A** [✔] Nodo caído o deploy: `events_wait` y `scene` fallaban sin nodo. Ahora salen de la DB (`connected:false`).
   Una acción en vuelo falla con `NodeOffline` + `request_id` para reintentar (el nodo deduplica).
6. **A** [✔] Sin límites de tamaño: HTTP 1 MiB (413), frame WS 8 MiB (uvicorn + chequeo), ≤1000 eventos por lote,
   miniaturas ≤512 KiB validadas por magic bytes, ≤32 llamadas pendientes por nodo, args de auditoría truncados.
7. **A** [✔] Sin migraciones ni índices: `create_all` no altera tablas. Ahora `schema_meta.version` (v2; versión ajena
   detiene el arranque) e índices por (space, tiempo|tipo|objeto) y (node, pos). Retención horaria (30 d).
8. **M** [✔] Render: `uvicorn` sin `timeout_graceful_shutdown` esperaba los WSS para siempre; ahora 20 s (<30 s de
   Render) y 1012 a los nodos. `/health` comprueba la DB. Blueprint movido a la raíz (`render.yaml`).
9. **M** [✔] Cerrar el socket viejo de un nodo podía tirar la conexión nueva (RuntimeError); alias evento↔espejo en
   `apply_reported`; carrera insert de `objects` (evento + miniatura) → upsert `ON CONFLICT`.
10. **M** [✔] Tokens: ya iban en SHA-256 (bien); nodo ahora con `hmac.compare_digest`; tokens deben ser distintos.
11. **M** [·] Zero-downtime deploy de Render: dos procesos ~segundos; el nuevo marca `interrupted` llamadas aún vivas
    del viejo. Inocuo (solo auditoría), pero el estado real lo da `home_call_done`.
12. **M** [·] Un solo usuario/espacio/nodo por env; multi-tenant necesita alta de tokens por API. Sin OAuth en MCP.
13. **M** [·] `ToolResult.request_id` ausente se acepta (se rechaza si difiere). Sin dedupe en el gateway (lo hace el nodo).
SQL: todo con parámetros de SQLAlchemy; `LIKE` de `history` escapado. Aislamiento: toda consulta filtra por `space_id`.

## Problemas en `contracts/` (no tocados)
- `ConfirmAction` no está en `CloudMessage`: el gateway lo manda por la WSS como `{"type":"confirm_action",…}` y el
  nodo debe decodificarlo aparte (el fake lo hace). Proponer `CloudMessage += ConfirmAction` (nube → nodo).
- `EventsWaitArgs` no trae `stream_id`: un `seq` no sirve de cursor tras `stream_reset`. El gateway usa posición propia.
- La tool `history` vive en el gateway; debería pasar a `tools.py`. Falta un mensaje para subir miniaturas por WSS
  (hoy `PUT /api/node/objects/{id}/thumbnail` con el token del nodo).
- ¿`seq` contiguo hacia la nube? Si el nodo filtra eventos (p. ej. `frame_meta`), no se puede detectar huecos.
