# Conectar el MCP de pcx

Dos formas de llegar al mismo nodo. En ambas el token va por variable de entorno: no lo escribas en archivos del repo.

| | URL | Token |
|---|---|---|
| Nodo local (`pcx serve`) | `http://127.0.0.1:9200/mcp` | el `PCX_TOKEN` con que se lanzó el nodo |
| Gateway remoto (Render) | `https://HOST/mcp` | el `PCX_USER_TOKEN` del gateway |

Convención de estas skills y del instalador: `PCX_MCP_URL` y `PCX_MCP_TOKEN` en el entorno del agente.

```sh
export PCX_MCP_URL=http://127.0.0.1:9200/mcp     # o https://HOST/mcp
export PCX_MCP_TOKEN=…                           # nunca en el repo
```

## Claude Code

Con el plugin (trae el MCP, las skills y `/pcx-estado`):

```sh
claude plugin marketplace add <ruta o owner/repo de pcx>
claude plugin install pcx@pcx-agentes
```

Sin plugin:

```sh
claude mcp add --transport http pcx "$PCX_MCP_URL" --header "Authorization: Bearer $PCX_MCP_TOKEN"
```

Ojo: esta segunda forma guarda el token expandido en la configuración de Claude Code. El plugin lo lee del entorno en cada sesión.

## Codex

```sh
codex mcp add pcx --url "$PCX_MCP_URL" --bearer-token-env-var PCX_MCP_TOKEN
```

Equivale a esto en `~/.codex/config.toml`:

```toml
[mcp_servers.pcx]
url = "http://127.0.0.1:9200/mcp"
bearer_token_env_var = "PCX_MCP_TOKEN"
tool_timeout_sec = 120   # opcional: ask y events_wait esperan hasta 50 s; el default de Codex es 60
```

## OpenCode

En `opencode.json` del proyecto o en `~/.config/opencode/opencode.json`:

```json
{"mcp": {"pcx": {"type": "remote", "url": "http://127.0.0.1:9200/mcp",
                 "headers": {"Authorization": "Bearer {env:PCX_MCP_TOKEN}"}, "oauth": false}}}
```

La documentación más reciente de OpenCode anida los servidores en `mcp.servers.<nombre>`; si tu versión no reconoce la forma de arriba, usa esa.

## Por stdio (sin HTTP)

`pcx mcp` habla MCP por stdio: hace de proxy al nodo si está corriendo en `127.0.0.1:9200` (lee `PCX_TOKEN`) y, si no, levanta un core en el mismo proceso.

```sh
claude mcp add pcx -- pcx mcp
codex mcp add pcx -- pcx mcp
```

## Comprobar

- `curl -s http://127.0.0.1:9200/health` → `{"ok": true, "node_id": …, "renderers": N, "cloud": false, …}` (sin token).
- En el agente: llamar `scene`. Un 401 significa token ausente o distinto del del nodo.
- Puertos 8787–8799 están reservados: el nodo se niega a usarlos.

## Qué abre cada token

El token del MCP (`PCX_TOKEN` en el nodo, `PCX_MCP_TOKEN` en el agente) es el único que necesita un agente. Con el modelo del PR #9 (`codex/seguridad`) el nodo usa además `PCX_CONSOLE_TOKEN` (consola humana: aprobar domótica) y `PCX_DEBUG_TOKEN` (rutas `/debug/*`, que solo existen con `pcx serve --debug`); los tres deben ser distintos y, si no se definen, el nodo los genera en su directorio de datos (`mcp.token`, `console.token`, `debug.token`, modo 0600). No le des al agente los de consola ni de depuración. En un nodo anterior a ese PR el token del MCP también abría `/debug` y `/console`: no expongas ese nodo ni conectes su domótica.
