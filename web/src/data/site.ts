// Un solo lugar para nombre, dominio y comandos. El nombre «pcx» y el dominio son provisorios.
export const NOMBRE = "pcx";

// Dominio de ejemplo para los instaladores: se reemplaza cuando exista el definitivo.
export const HOST = "pcx.dev";

export const INSTALAR = {
  unix: `curl -fsSL https://${HOST}/install.sh | sh`,
  windows: `irm https://${HOST}/install.ps1 | iex`,
  vincular: "pcx up",
} as const;

// Comandos reales de cloud/gateway/README.md (MCP remoto por URL + token).
export const AGENTES = [
  {
    id: "claude",
    nombre: "Claude Code",
    donde: "En tu terminal",
    comando: `claude mcp add --transport http pcx https://${HOST}/mcp --header "Authorization: Bearer $PCX_USER_TOKEN"`,
  },
  {
    id: "codex",
    nombre: "Codex",
    donde: "En tu terminal",
    comando: `codex mcp add pcx --url https://${HOST}/mcp --bearer-token-env-var PCX_USER_TOKEN`,
  },
  {
    id: "opencode",
    nombre: "OpenCode",
    donde: "En opencode.json",
    comando: `{"mcp":{"pcx":{"type":"remote","url":"https://${HOST}/mcp","headers":{"Authorization":"Bearer {env:PCX_USER_TOKEN}"},"oauth":false}}}`,
  },
] as const;

// Herramientas MCP del contrato (contracts/schema/tools.json).
export const HERRAMIENTAS = ["scene", "look", "project", "remove", "ask", "events_wait", "run_flow", "calibrate", "devices", "home"] as const;

export const CIFRAS = {
  fps: 60,
  latencia: 215,
  latenciaAntes: 313,
  calibracionMm: "0,5",
  escaneoS: "4,4",
} as const;
