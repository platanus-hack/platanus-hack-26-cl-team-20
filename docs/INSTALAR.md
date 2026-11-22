# Instalar pcx

El nodo de pcx son tres binarios: `pcx` (core, MCP y supervisor), `pcx-renderer` (proyector) y `pcx-capture` (cámara).
Se instalan en `~/.pcx/bin` (macOS y Linux) o `%LOCALAPPDATA%\pcx\bin` (Windows), con un `pcx.toml` al lado.

## Requisitos

- macOS 11+ (Apple Silicon; Intel cuando haya paquete), Windows 10/11 x64 o Linux con glibc 2.35+ (x86_64 o aarch64).
- Una cámara (USB o integrada) y un proyector conectado como segunda pantalla. Sin ellos, el core y el MCP funcionan igual.
- GPU con Metal, Vulkan o DirectX 12 para el renderer. En Linux, además: `sudo apt install libfontconfig1 libvulkan1
  mesa-vulkan-drivers` (Fedora: `fontconfig vulkan-loader`); el instalador avisa si faltan.

## Instalación

| SO | Comando |
|---|---|
| macOS y Linux | `curl -fsSL <url>/install.sh \| sh` |
| Windows (PowerShell) | `irm <url>/install.ps1 \| iex` |

El instalador detecta el SO y la arquitectura, baja el paquete del último release, verifica su SHA-256 contra
`SHA256SUMS` y pregunta antes de agregar la carpeta al PATH. Al final ofrece instalar las skills y el MCP de pcx
para Claude Code, Codex y OpenCode (usa `integrations/install.sh` del mismo release; no hace falta clonar el repo).

Opciones (con `curl`: `| sh -s -- OPCIÓN`; en Windows: `& ([scriptblock]::Create((irm <url>/install.ps1))) OPCIÓN`):

- `--version v0.2.0` versión concreta · `--dry-run` descarga y verifica sin instalar · `--yes` sin preguntas
- `--no-modify-path` no toca el PATH · `--no-skills` no ofrece las skills · `--uninstall` desinstala
- `--start` levanta el motor (`pcx up`) e imprime la URL para emparejar la consola · `--stop` lo detiene
- `--check` verifica sin instalar (binario, modelos, cámara, puerto 9230; usa `pcx doctor` si existe) · `--force` reinstala

**Modelos incluidos.** El paquete trae los pesos de SAM (MobileSAM) y de manos (MediaPipe, OpenCV Zoo) en `models/`,
con `manifest.json` (fuente, licencia y sha256). El instalador los verifica uno a uno, los deja en `~/.pcx/models` y
fija `PCX_MODELS_DIR` (en `~/.pcx/env`, en `[env]` de `pcx.toml` y, en Windows, como variable de usuario). No hay que
bajar nada a mano. Es idempotente: si ya está instalado el mismo paquete, no lo vuelve a bajar.

**Con un agente.** La landing sirve `/setup.md` (copia de [SETUP-AGENTE.md](SETUP-AGENTE.md)): basta con decirle al
agente «Instala el motor de pcx siguiendo https://<landing>/setup.md».

## Actualizar y desinstalar

- **Actualizar:** el mismo comando de instalación. Reemplaza los binarios y conserva tu `pcx.toml`.
- **Desinstalar:** el mismo comando con `--uninstall`. Quita los binarios y la entrada del PATH (y, si dices que sí,
  las skills); conserva `pcx.toml` y los datos. Para borrar todo: `rm -rf ~/.pcx` o `%LOCALAPPDATA%\pcx`.

## Primer uso

```sh
pcx up --config ~/.pcx/pcx.toml        # Windows: pcx up --config $env:LOCALAPPDATA\pcx\pcx.toml
```

`pcx up` lanza y vigila los procesos de `pcx.toml`. Por defecto el core (MCP en `http://127.0.0.1:9200/mcp`) y el motor
local para la web app (`ws://127.0.0.1:9230`; imprime la URL de emparejamiento, ver `docs/arquitectura/MOTOR-LOCAL.md`);
pon `enabled = true` en `[process.capture]` y `[process.renderer]` para usar la cámara y el proyector.

**Permiso de cámara.** macOS pide permiso la primera vez que `pcx-capture` abre la cámara: acepta el diálogo (se lo
da a la terminal que lanzó `pcx up`). Si lo negaste: Ajustes del Sistema → Privacidad y seguridad → Cámara.
En Windows: Configuración → Privacidad → Cámara → «Permitir que las apps de escritorio accedan a la cámara».

**Vincular a la nube.** Agrega en `[env]` de `pcx.toml` la URL del gateway y el token del nodo, y reinicia `pcx up`:

```toml
PCX_CLOUD_URL = "wss://<gateway>/node"
PCX_NODE_TOKEN = "<token que te da el gateway>"
```

El core muestra `nube=sí` al arrancar si la URL está configurada; si el enlace cae, reintenta solo. Firma de macOS, Gatekeeper y cómo se publica un
release: [DISTRIBUCION.md](DISTRIBUCION.md).
