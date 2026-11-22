# integrations · pcx para agentes de código

Lo que una persona instala para que su agente (Claude Code, Codex, OpenCode) sepa usar pcx: seis skills, el servidor MCP ya configurado y un comando de estado.

| Carpeta | Qué es |
|---|---|
| `skills/` | **Fuente única** de las skills, en formato [Agent Skills](https://agentskills.io/specification). Las leen Codex y OpenCode tal cual |
| `claude-plugin/` | Plugin de Claude Code: `plugin.json`, `.mcp.json`, `commands/pcx-estado.md` y una **copia** de `skills/` |
| `sync.sh` | Copia `skills/` → `claude-plugin/skills/`. `--check` falla si están desfasadas |
| `install.sh`, `install.ps1` | Instalan todo en los agentes que detecten. Por defecto solo muestran el plan |
| `../.claude-plugin/marketplace.json` | Marketplace `pcx-agentes` (en la raíz del repo) |
| `PRUEBAS.md` | Qué se probó de verdad y qué no |

## Skills

| Skill | Para qué |
|---|---|
| `pcx-operar` | Ver y actuar: `scene`, `look`, `project`/`remove`, anclar a objetos, capas, `ttl_s`, `ask`, `events_wait`, latencias, privacidad |
| `pcx-flujos` | Modos con `run_flow`. Ejemplos validados de las 3 escenas (Vibecoders Desk, Edu, Lab) y validador contra los contratos |
| `pcx-efectos` | Efectos nuevos como shaders WGSL: contrato de uniforms, recarga en caliente, naga, prueba headless, estética |
| `pcx-extender` | Workers de percepción, adaptadores de cámara, tools MCP nuevas; contratos, ZeroMQ, `pcx-shm`, `FrameSource` |
| `pcx-calibrar` | Rápida, precisa o verificar; aviso de deriva; luz y contraste; diagnóstico |
| `pcx-domotica` | `home`, `HomePolicy`, confirmación física, niveles de riesgo R0–R3 |

## Antes de instalar: URL y token

El servidor MCP se configura con dos variables de entorno del agente. El token **nunca** se escribe en un archivo: cada cliente lo lee del entorno en cada sesión.

```sh
# Nodo local (pcx serve):
export PCX_MCP_URL=http://127.0.0.1:9200/mcp
export PCX_MCP_TOKEN="$PCX_TOKEN"            # el token MCP del nodo

# Gateway remoto (Render):
export PCX_MCP_URL=https://HOST/mcp
export PCX_MCP_TOKEN="$PCX_USER_TOKEN"
```

Al agente solo se le da el token del MCP. El de consola (`PCX_CONSOLE_TOKEN`, para que una persona apruebe domótica) y el de depuración (`PCX_DEBUG_TOKEN`, rutas `/debug/*` que solo existen con `pcx serve --debug`) son distintos y no van en el entorno del agente. Esa separación llega con el PR #9 (`codex/seguridad`); en un nodo anterior, el token del MCP también abre `/debug` y `/console`, así que no conectes domótica real a ese nodo.

Ponlas en tu perfil de shell (`~/.zshrc`, `~/.bashrc`) o en tu gestor de secretos. En Windows: `$env:PCX_MCP_URL = '…'`.

## Instalar

### Con el instalador (los tres agentes)

```sh
integrations/install.sh            # muestra lo que haría; no escribe nada
integrations/install.sh --yes      # aplica en los agentes que encuentre
```

```powershell
.\integrations\install.ps1          # Windows: igual, plan primero
.\integrations\install.ps1 --yes
```

Opciones: `--agents claude,codex,opencode`, `--url URL`, `--token-env NOMBRE`, `--skills-dir DIR`, `--opencode-config ARCHIVO` (por ejemplo `./opencode.json` para un solo proyecto), `--link` (solo `.sh`: enlaza en vez de copiar), `--no-skills`, `--no-mcp`, `--uninstall`, `--dry-run`.

Qué toca con `--yes`, y nada más:

| Agente | Skills | MCP |
|---|---|---|
| Claude Code | Plugin `pcx@pcx-agentes` (`claude plugin marketplace add` + `claude plugin install`) | Lo trae el plugin |
| Codex | `~/.agents/skills/pcx-*` | `codex mcp add pcx --url … --bearer-token-env-var PCX_MCP_TOKEN` |
| OpenCode | Las mismas de `~/.agents/skills/` | Entrada `mcp.pcx` en `~/.config/opencode/opencode.json`, con respaldo `.bak-<fecha>` |

No pisa un servidor `pcx` que ya exista ni un `opencode.json` con comentarios (en ese caso imprime el fragmento para pegarlo).

### Claude Code, a mano

Dentro de una sesión:

```
/plugin marketplace add <owner>/<repo>        # o la ruta local del repo
/plugin install pcx@pcx-agentes
```

O desde el shell: `claude plugin marketplace add <owner>/<repo>` y `claude plugin install pcx@pcx-agentes`.

Para probar sin instalar nada: `claude --plugin-dir integrations/claude-plugin`.

El plugin registra el servidor `pcx` con `url: ${PCX_MCP_URL:-http://127.0.0.1:9200/mcp}` y `Authorization: Bearer ${PCX_MCP_TOKEN}`. Las skills quedan como `pcx:pcx-operar`, etc., y el comando como `/pcx:pcx-estado` (agrega `mirar` para que además tome una imagen).

### Codex, a mano

```sh
mkdir -p ~/.agents/skills && cp -R integrations/skills/pcx-* ~/.agents/skills/
codex mcp add pcx --url "$PCX_MCP_URL" --bearer-token-env-var PCX_MCP_TOKEN
```

Para un solo proyecto, las skills van en `<repo>/.agents/skills/`. Recomendado en `~/.codex/config.toml`, porque `ask` y `events_wait` esperan hasta 50 s y el plazo por defecto de Codex es 60 s:

```toml
[mcp_servers.pcx]
url = "http://127.0.0.1:9200/mcp"
bearer_token_env_var = "PCX_MCP_TOKEN"
tool_timeout_sec = 120
```

Se invocan solas por su descripción, o con `$pcx-operar` en el prompt.

### OpenCode, a mano

OpenCode lee `~/.agents/skills/` (y `.agents/skills/` del proyecto), así que la copia de arriba sirve. El servidor, en `opencode.json` del proyecto o en `~/.config/opencode/opencode.json`:

```json
{
  "mcp": {
    "pcx": {
      "type": "remote",
      "url": "http://127.0.0.1:9200/mcp",
      "headers": { "Authorization": "Bearer {env:PCX_MCP_TOKEN}" },
      "oauth": false
    }
  }
}
```

Esa es la forma que acepta OpenCode 1.x (probada con 1.14.41). La documentación actual describe una forma nueva con los servidores bajo `mcp.servers.<nombre>`; el instalador respeta la que ya use tu archivo.

### Sin HTTP: stdio

`pcx mcp` habla MCP por stdio (proxy al nodo si está corriendo; si no, levanta un core en el mismo proceso):

```sh
claude mcp add pcx -- pcx mcp
codex mcp add pcx -- pcx mcp
```

## Comprobar

```sh
curl -s http://127.0.0.1:9200/health      # {"ok": true, …} sin token
```

En el agente: «llama `scene` de pcx» o `/pcx:pcx-estado`. Un 401 es token ausente o distinto del del nodo.

## Contribuir una skill

1. **Créala en `integrations/skills/<nombre>/SKILL.md`**. Nunca edites `claude-plugin/skills/`: es una copia generada.
2. **Frontmatter** (estándar Agent Skills, lo único portable entre los tres agentes):
   ```yaml
   ---
   name: pcx-<nombre>          # igual al nombre de la carpeta; minúsculas, dígitos y guiones; máx. 64
   description: "Qué hace y cuándo usarla, con las palabras que diría la persona. Máx. 1024 caracteres."
   license: MIT
   compatibility: Qué necesita (MCP conectado, el repo, uv…)
   metadata:
     author: pcx
     version: "0.1.0"
   ---
   ```
   La descripción es lo único que el agente ve antes de cargar la skill: di **qué hace y cuándo**, incluye las tools y los síntomas que deberían activarla. Escríbela en una sola línea entre comillas dobles. No uses campos propios de Claude Code (`allowed-tools` con formato de Claude, `context`, `paths`…): rompen la portabilidad.
3. **Cuerpo**: menos de 500 líneas, en imperativo, explicando el porqué. Ejemplos reales (JSON que el nodo acepte), buenas prácticas y una tabla de errores comunes. Lo largo va en `references/`, las plantillas en `assets/` y el código ejecutable en `scripts/`, enlazados desde `SKILL.md` con rutas relativas de un solo nivel.
4. **No inventes**: cada tool, parámetro, comando y cifra debe existir en el código o en un README del repo. Si algo es una convención de la skill y no del contrato, dilo.
5. **Verifica**:
   ```sh
   integrations/sync.sh                                   # actualiza la copia del plugin
   claude plugin validate integrations/claude-plugin      # manifiesto y MCP
   claude plugin validate .                               # marketplace
   uv run --project contracts python integrations/skills/pcx-flujos/scripts/validar_flujo.py <tus flujos>.json
   claude -p --plugin-dir integrations/claude-plugin "lista las skills de pcx y cuándo usar cada una"
   ```
   Si trae ejemplos de `run_flow`, valídalos; si trae un shader, córrelo headless; si describe una tool, pruébala contra `pcx serve`.
6. **Commit** con la skill y la copia sincronizada. `integrations/sync.sh --check` debe pasar.

Cuando cambie una tool o un contrato, actualiza la skill que lo documenta en el mismo PR (ver `pcx-extender`).
