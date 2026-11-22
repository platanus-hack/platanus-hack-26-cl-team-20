# Instalar el motor local de pcx (instrucciones para agentes)

Eres un agente de código (Claude Code, Codex u OpenCode). La persona te pasó este archivo para que instales y dejes
corriendo el motor local de pcx: el binario `pcx` con los pesos de SAM y de manos incluidos. Sigue los pasos en orden
y corre la verificación de cada uno antes de seguir.

- No le pidas a la persona tokens, claves ni contraseñas: nada de esto los necesita.
- No compiles desde el código fuente ni desactives verificaciones de sha256.
- `LANDING` es el origen desde el que leíste este archivo. Si lo leíste en `https://pcx.dev/setup.md`, entonces
  `LANDING=https://pcx.dev`.

## 1. Detecta el sistema

| SO | Comando | Soportado |
|---|---|---|
| macOS, Linux | `uname -sm` | `Darwin arm64`, `Linux x86_64`, `Linux aarch64` |
| Windows | `$env:PROCESSOR_ARCHITECTURE` (PowerShell) | `AMD64` (`ARM64` corre emulado) |

Mac Intel (`Darwin x86_64`) todavía no tiene paquete: detente y díselo a la persona.

## 2. Instala y levanta el motor

macOS y Linux:

```sh
curl -fsSL "$LANDING/install.sh" | sh -s -- --yes --skills --start
```

Windows (PowerShell):

```powershell
& ([scriptblock]::Create((irm "$LANDING/install.ps1"))) --yes --skills --start
```

El instalador hace lo siguiente:

1. Baja el paquete de tu plataforma y verifica su sha256, y luego el de cada modelo.
2. Lo instala en `~/.pcx` (Windows: `%LOCALAPPDATA%\pcx`) y fija `PCX_MODELS_DIR` en `~/.pcx/models`.
3. Registra el plugin, las skills y el MCP en los agentes que encuentre (`integrations/install.sh`).
4. Levanta `pcx up` en segundo plano e imprime la URL de emparejamiento.

Es idempotente: si lo vuelves a correr, no descarga nada que ya esté instalado. El comando termina con código 0 solo si
el motor quedó respondiendo.

Si tu herramienta de shell mata los procesos en segundo plano al terminar, corre el comando como tarea de fondo (por
ejemplo, `run_in_background` en Claude Code) o pídele a la persona que corra `pcx up` en una terminal suya.

**Verifica:**

```sh
curl -fsSL "$LANDING/install.sh" | sh -s -- --check                  # Windows: ... (irm "$LANDING/install.ps1"))) --check
```

Todas las líneas deben decir `[ok]`. Un `[aviso]` de cámara es aceptable.

## 3. Comprueba cada pieza

| Qué | macOS y Linux | Esperado |
|---|---|---|
| Binario | `~/.pcx/bin/pcx --version` | imprime la versión |
| Modelos | `cd ~/.pcx/models && shasum -a 256 -c SHA256SUMS` (Linux: `sha256sum -c`) | 5 líneas `OK` |
| Motor | `curl -fsS http://127.0.0.1:9230/v1/health` | JSON con `"ok"` |
| Token del motor | `test -s ~/.pcx/motor.token && echo ok` | `ok` |
| Skills (Codex, OpenCode) | `ls ~/.agents/skills \| grep pcx-` | 6 skills `pcx-*` |
| Plugin (Claude Code) | `claude plugin list \| grep pcx` | `pcx@pcx-agentes` |
| MCP (Codex) | `codex mcp get pcx` | URL `http://127.0.0.1:9200/mcp` |

En Windows: `& "$env:LOCALAPPDATA\pcx\bin\pcx.exe" --version` e `irm http://127.0.0.1:9230/v1/health`. Para los
modelos, usa `--check`.

## 4. Entrégale la URL a la persona

El instalador imprime una URL de este tipo:

```
https://pcx-gateway.onrender.com/?motor=ws://127.0.0.1:9230&motor_token=…
```

Si la perdiste, reconstrúyela así (en Windows, el token está en `%USERPROFILE%\.pcx\motor.token` o en `%LOCALAPPDATA%\pcx`):

```sh
printf 'https://pcx-gateway.onrender.com/?motor=ws://127.0.0.1:9230&motor_token=%s\n' "$(cat ~/.pcx/motor.token)"
```

Pídele que la abra en Chrome o Edge en este mismo computador, porque la consola se conecta con el motor en
`127.0.0.1`. Ábrela tú solo si la persona te lo pide. El token solo sirve en este computador, pero da control del
motor: no lo pegues en otros sitios ni lo guardes en el repositorio.

## Si algo falla

| Síntoma | Qué hacer |
|---|---|
| `esta versión no trae pcx-x86_64-apple-darwin…` | Mac Intel sin paquete. Avísale a la persona; no compiles. |
| `no se pudo descargar …/SHA256SUMS` | No hay release público. Si la persona tiene un espejo, antepón `PCX_BASE_URL=<url-del-espejo>` al `sh`. Si no, detente y avisa. |
| `checksum … no coincide` | Descarga corrupta o alterada. Reintenta una vez; si se repite, detente y avisa. |
| `[falla] modelos` | Reinstala: agrega `--force` al comando del paso 2. |
| `puerto 9230 ocupado` | Mira quién lo usa: `lsof -nP -iTCP:9230 -sTCP:LISTEN` (Windows: `Get-NetTCPConnection -LocalPort 9230`). Si es un pcx anterior, ciérralo o corre el instalador con `--stop`. Si es otra aplicación, pregúntale a la persona. |
| `el motor no respondió … tras 60 s` | Lee `~/.pcx/logs/motor.log` (Windows: `logs\motor.err.log`). Si `~/.pcx/pcx.toml` es anterior, compáralo con `~/.pcx/pcx.toml.dist`. Reinicia con `--stop` y luego `--start`. |
| `[aviso] cámara` o el permiso de macOS | Conecta una cámara o sigue sin ella (la consola puede mandarle cuadros al motor). En macOS, la persona debe aceptar el permiso para la terminal en Ajustes → Privacidad y seguridad → Cámara. |
| Linux: falta `libfontconfig` o `libvulkan` | `sudo apt install libfontconfig1 libvulkan1 mesa-vulkan-drivers`. Pide confirmación antes de usar `sudo`. |
| Las skills no se instalaron | `bash ~/.pcx/agentes/integrations/install.sh --yes` |
| El MCP responde 401 | El token del MCP lo crea el nodo en `mcp.token` (macOS: `~/Library/Application Support/pcx/`; Linux: `~/.local/share/pcx/`; Windows: `%APPDATA%\pcx\data\`). No lo pidas: registra el MCP por stdio con `pcx mcp`, que lo lee solo (`claude mcp add pcx-local -- ~/.pcx/bin/pcx mcp`). |

Para desinstalar, corre el comando del paso 2 con `--uninstall --yes`. Esto conserva `pcx.toml` y los datos.
