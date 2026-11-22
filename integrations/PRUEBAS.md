# Pruebas del plugin y las skills (sáb 21-nov-2026, ~06:00–06:25)

MacBook Pro (macOS 26). Claude Code 2.1.330 · Codex CLI 0.163.0 · OpenCode 1.14.41. Sin cámara, sin proyector y sin abrir ventanas. No se modificó la configuración global de ningún agente: todo corrió con `--plugin-dir`, un `CODEX_HOME` temporal y carpetas de proyecto desechables dentro del worktree, borradas al terminar.

## Resumen

| # | Prueba | Resultado |
|---|---|---|
| 1 | `claude plugin validate` del plugin y del marketplace | Pasa (solo consejos para publicar en el directorio de Anthropic: ícono y URL de privacidad) |
| 2 | Claude Code carga el plugin efímero y lista las skills | Pasa: 6 skills (`pcx:pcx-operar`…) y el comando `pcx:pcx-estado` |
| 3 | Claude Code + MCP del plugin contra `pcx serve`: proyectar etiqueta y crear flujo | Pasa: `plugin:pcx:pcx` conectado; cargó `pcx-operar` y `pcx-flujos`; `project` y `run_flow` sin errores; verificado con `scene` |
| 4 | `/pcx:pcx-estado` | Pasa: reporta superficie, objetos, widgets, flujos, eventos recientes y las tres alertas correctas |
| 5 | Codex descubre las skills y usa el MCP | Pasa: lista las 6; `scene` + `project` → `{"ids":["hola-desde-codex"],"rev":38}`; carga el cuerpo de `$pcx-flujos` y responde desde él |
| 6 | OpenCode descubre las skills y conecta el MCP | Pasa: `opencode debug skill` lista las 6; `opencode mcp list` → `pcx connected`. No se corrió un modelo |
| 7 | Los 3 flujos de ejemplo contra los contratos (Python) | Pasa: `validar_flujo.py` OK en los 4 archivos; `contracts`: 103 tests en verde |
| 8 | Los 3 flujos de ejemplo en el core real | Pasa: Vibecoders (toque sostenido → `decision: si`; sin toque → `sin_respuesta`), Lab (señal → `paso3`), Edu (Marte → corrige → Júpiter → `reto_superado`) |
| 9 | Efecto de plantilla `barrido.wgsl` headless | Pasa: `[fx] barrido cargado`; PNG revisado a ojo |
| 10 | Worker de ejemplo por ZeroMQ | Pasa: el objeto aparece en `scene`; recibe los eventos por SUB |
| 11 | `install.sh` en modo plan, aplicar, repetir y desinstalar | Pasa (destinos temporales). Encontró y se corrigió un bug |
| 12 | `install.ps1` | **No probado** (no hay PowerShell en esta máquina) |

## Detalle

### Nodo de prueba

`pcx serve` en `127.0.0.1:9200` con `PCX_TOKEN` de prueba, `PCX_DATA_DIR` y sockets ZeroMQ temporales, `PCX_HOME_POLICY='{"allow":["light.*"]}'`. Objetos simulados con `POST /debug/object` y toques con `POST /debug/touch`.

No se compiló nada: se usó el binario `target/debug/pcx` que ya existía, construido por otro frente a las 05:51 desde un árbol más nuevo que esta rama (trae además los subcomandos `worker` y `cameras`). Su `tools/list` son las mismas 10 tools de `contracts/schema/tools.json`. Las skills documentan lo que existe en el código de esta rama (`serve`, `mcp`, `up`, `replay`).

### Claude Code

```sh
claude plugin validate ./integrations/claude-plugin     # ✔ Validation passed
claude plugin validate .                                # ✔ Validation passed (marketplace)
claude -p --plugin-dir integrations/claude-plugin "lista las skills de pcx disponibles y resume cuándo usar cada una"
```

Respondió con las 6 skills y `pcx:pcx-estado`, cada una con su cuándo.

```sh
PCX_MCP_URL=http://127.0.0.1:9200/mcp PCX_MCP_TOKEN=… claude -p --plugin-dir integrations/claude-plugin \
  --output-format stream-json --verbose \
  --allowedTools "Skill,Read,mcp__plugin_pcx_pcx__scene,mcp__plugin_pcx_pcx__project,mcp__plugin_pcx_pcx__run_flow,…" \
  -- "Usa pcx… proyecta una etiqueta 'Hola Platanus' anclada al mouse… crea un flujo de decisión sí/no junto al mouse…"
```

Secuencia observada en el stream: `Skill pcx:pcx-operar` → `scene` → `Skill pcx:pcx-flujos` → leyó `assets/vibecoders-desk.json` → `project` → `run_flow` → `scene`. Cero errores de tools. Verificación independiente con un cliente MCP propio:

```json
{"widgets": [["hola-platanus","label"],["vd-panel","panel"],["vd-halo","halo"],["vd-si","button"],["vd-no","button"]],
 "active_flows": ["vibecoders-decision"]}
```

Al mover el objeto `mouse`, la etiqueta lo siguió (`point_mm` pasó a `[420, 276]`).

Ojo con `claude -p`: `--allowedTools` es variádica y se come el prompt si va después. Pon el prompt tras `--`.

No se ejecutó `claude plugin marketplace add` ni `claude plugin install`: escriben en la configuración del usuario. Quedaron validados por `claude plugin validate .` y por el plan del instalador.

### Codex

`CODEX_HOME` temporal con copia de `auth.json` y de `config.toml` sin sus tablas `mcp_servers`, `projects`, `marketplaces`, `plugins` ni `hooks` (para no lanzar servidores ajenos). Los originales no se tocaron.

```sh
CODEX_HOME=<tmp> integrations/install.sh --yes --agents codex --skills-dir <proyecto>/.agents/skills
#   → Added global MCP server 'pcx'.   ([mcp_servers.pcx] url + bearer_token_env_var = "PCX_MCP_TOKEN")
CODEX_HOME=<tmp> codex mcp list            # pcx  http://127.0.0.1:9200/mcp  PCX_MCP_TOKEN  enabled  Bearer token
CODEX_HOME=<tmp> codex debug prompt-input "hola"   # las 6 skills pcx-* aparecen en el prompt visible por el modelo
CODEX_HOME=<tmp> PCX_MCP_TOKEN=… codex exec -m gpt-6-astra -s read-only --skip-git-repo-check \
  -c 'mcp_servers.pcx.default_tools_approval_mode="approve"' "…" < /dev/null
```

- Listó las 6 skills con su uso, llamó `scene` y `project`; `scene` confirmó `hola-desde-codex` anclada a `libreta`.
- Con `$pcx-flujos` cargó el `SKILL.md` y respondió bien qué asset es el de Lab, cómo mandar una señal y cómo cancelar un flujo.
- Las tres primeras corridas fallaron por límite de uso de la cuenta (se liberó a las 06:16); no fue un problema del plugin.
- Codex pide `resources/list` al conectar y el servidor de pcx responde `-32601`: queda un `ERROR` en el log de Codex, inofensivo.
- Las skills se probaron en `<proyecto>/.agents/skills` (ubicación documentada de repo). La de usuario, `~/.agents/skills`, es donde escribe el instalador por defecto; no se probó ahí para no tocar el home real.

### OpenCode

Proyecto desechable con `opencode.json` (generado por el instalador) y `.agents/skills/`:

- `opencode mcp list` → `✓ pcx connected http://127.0.0.1:9200/mcp` (cabecera `Bearer {env:PCX_MCP_TOKEN}`).
- `opencode debug skill` → las 6 `pcx-*`, con `location` en `.agents/skills/…/SKILL.md`.

No se corrió una conversación con modelo. La versión instalada (1.14.41) usa `mcp.<nombre>`; la documentación vigente describe `mcp.servers.<nombre>`. El instalador escribe la forma que ya tenga el archivo y, si no hay ninguna, `mcp.<nombre>`: **la forma nueva no se probó**.

### Flujos de ejemplo

```sh
uv run --project contracts python integrations/skills/pcx-flujos/scripts/validar_flujo.py integrations/skills/pcx-flujos/assets/*.json
# OK ×4 (edu-sistema-solar 7 estados, lab-guia 9, senal 1, vibecoders-desk 5); un flujo inválido da salida 1
cd contracts && uv run pytest -q     # 103 passed
```

En el core, trazas reales (`events_wait`):

- **Vibecoders**: `start → pregunta`; toque sostenido 1,3 s en `vd-si` → `widget_pressed{trigger: dwell, value: si}` → `si` → `flow_emitted decision {respuesta: si}`. Sin toque: a los 60 s `sin_respuesta` → `fin` y limpieza.
- **Lab**: `paso1 —object_moved→ paso2 —widget_pressed→ verifica2`, emite `verificar {paso: "2", camara: lateral}`; se lanza `senal.json` → `paso_verificado {ok: true}` → `paso3`. Cancelación relanzando el mismo `flow.id` con un estado final: el flujo se detuvo y sus widgets se borraron.
- **Edu**: `intro → busca_jupiter —object_moved (marte)→ ese_es_marte —after_s→ busca_jupiter —touch_down (jupiter)→ jupiter_ok`, emite `reto_superado`; termina solo y limpia.

### Comportamientos del nodo confirmados (las skills los documentan)

| Afirmación | Evidencia |
|---|---|
| `request_id` repetido no ejecuta | Segundo `project` con el mismo id devolvió el resultado anterior y el widget nuevo no se creó |
| Reemplazar sin `ttl_s` un widget que tenía `ttl_s` no cancela el vencimiento | El widget desapareció a los ~2 s igual |
| Confirmación de domótica en la esquina superior derecha | `pcx:confirm-q:*` en `[550, 20, 230, 60]` y `pcx:confirm:*` en `[550, 90, 230, 50]`, `z` 50/51, `triggers: [dwell, pinch]` |
| `home` con política `allow: ["light.*"]` | `light.turn_on` → `pending` con `confirmation_id` y `action_digest`; venció a los 30 s. `lock.unlock` → `denied` |
| `requires_confirmation` se rechaza | `unknown field requires_confirmation` |
| Ids `pcx:` reservados | `ids reservados del nodo (pcx:*)` |
| `calibrate` solo encola | `{"status": "queued", …}` y `scene.transforms` sigue vacío |
| `look` sin cámara real | `Cámara sim (1060×685 px)` + resumen de texto + 1 imagen |
| `object_id` inexistente en `project` | `objeto desconocido "nohay" (usa scene)` |

### Efecto y worker

- `pcx-renderer --headless --no-ws --frames 1 --time 0.7 --scene …/escena-prueba.json --effects <dir temporal con barrido.wgsl>`: `[fx] barrido cargado`, 3 s, PNG con la franja sobre fondo negro dentro del rectángulo y del polígono.
- `uv run --no-project --with pyzmq python …/worker_ejemplo.py`: publicó `object_appeared` + `object_moved` en JSON por PUSH; `scene` mostró `caja` en `[640, 420]`; recibió ambos eventos por SUB.

### Instaladores

- `install.sh` sin argumentos: imprime el plan para los 3 agentes detectados y no escribe nada.
- `--yes` sobre destinos temporales (`CODEX_HOME`, `--skills-dir`, `--opencode-config`): copió 6 skills, `codex mcp add` y fusionó `opencode.json` conservando otra entrada existente y dejando respaldo `.bak-<fecha>`.
- Segunda corrida: no pisa nada (`ya existe un servidor MCP «pcx»`, `ya tiene mcp.pcx; nada que hacer`).
- `--uninstall --yes`: quitó skills, servidor de Codex y la entrada de OpenCode (la otra quedó intacta).
- Bug encontrado y corregido: en bash 3.2 de macOS, `«$SERVER»` se leía como una variable de nombre `SERVER»` y abortaba con `set -u`. Ahora `${SERVER}`.

## Lo que no se probó

- `install.ps1` (Windows): escrito como espejo de `install.sh`, sin ejecutar.
- Instalación real del plugin por marketplace en Claude Code y la rama `claude` del instalador con `--yes`.
- OpenCode con un modelo, y su forma de configuración `mcp.servers`.
- El gateway remoto en Render (URL y `PCX_USER_TOKEN`): mismas tools más `history`.
- Hardware real: cámara, proyector, calibración de verdad, toque con la mano.
- `ask` desde un agente (necesita un toque durante la llamada).

## Hallazgos para otros frentes

1. **`pcx-core`, TTL**: `project` no limpia `expires_ns` cuando la nueva versión del widget no trae `ttl_s`; el widget muere con el plazo viejo. Probable bug.
2. **`calibrate`** responde `queued` y nadie atiende la cola en `pcx serve`: la skill `pcx-calibrar` lo dice y manda a verificar con `scene.transforms`.
3. **`home`**: el ejecutor de Home Assistant es un stub (`failed`). La política y la confirmación funcionan.
4. **Puertos ZeroMQ en Windows**: `pcx-core` usa 9206 para la ingesta y el README de `pcx-capture` documenta 9225.
5. **R0–R3** es una convención de la skill `pcx-domotica`; no existe en los contratos ni en la documentación del repo. Conviene que el equipo la valide o la reemplace por su definición.
6. Nadie escribe la política en SQLite (`set_home_policy` no tiene llamadas): la `HomePolicy` efectiva es la de `PCX_HOME_POLICY`.

## Correcciones tras la revisión del PR #6

- `install.sh --uninstall --yes` ya no aborta si un cliente no tenía nada instalado: cada cliente corre aislado y un fallo se avisa sin cortar a los demás. Probado con un `claude` de mentira que devuelve 1, un `CODEX_HOME` vacío y un `opencode.json` temporal: Codex y OpenCode se limpiaron igual y la salida fue 0.
- `opencode.json` conserva sus permisos (0600 antes y después, también el respaldo); un archivo nuevo nace 0600 y un symlink se edita en su destino real.
- Skills ajustadas al modelo de seguridad del PR #9 (`codex/seguridad`, leído en `a01e8d5`): tokens separados para MCP, consola y depuración; `/debug/*` solo con `pcx serve --debug`; la confirmación de domótica solo la aprueba una mano real o la consola humana. Ese comportamiento **no se ejecutó aquí**: se describió a partir del código de esa rama.
- Las pruebas de flujos de arriba usaron `/debug/touch` con `hold_ms` y el token del MCP, que es el comportamiento anterior a ese PR.
