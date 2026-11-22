// Nombre de marca de cara al usuario.
export const NOMBRE = "WUI";
export const TAGLINE = "El mundo es tu interfaz.";

// El motor interno se llama PCX — aparece solo en contextos técnicos (comandos, tokens).
export const NOMBRE_ENGINE = "pcx";

// Dominio provisorio: cambia cuando exista el definitivo.
export const HOST = "wui.dev";

// Origen público de la landing.
export const LANDING = `https://${HOST}`;

// Motor local PCX (el engine de visión computacional — nombre técnico, no de marca).
export const MOTOR = {
  agente: `Instala el motor local de WUI siguiendo ${LANDING}/setup.md`,
  unix: `curl -fsSL ${LANDING}/install.sh | sh -s -- --yes --start`,
  windows: `& ([scriptblock]::Create((irm ${LANDING}/install.ps1))) --yes --start`,
} as const;

export const INSTALAR = {
  unix: `curl -fsSL https://${HOST}/install.sh | sh`,
  windows: `irm https://${HOST}/install.ps1 | iex`,
  vincular: "wui up",
} as const;

// Comandos MCP — el servidor usa el nombre técnico PCX en la URL y el token.
export const AGENTES = [
  {
    id: "claude",
    nombre: "Claude Code",
    donde: "En tu terminal",
    comando: `claude mcp add --transport http wui https://${HOST}/mcp --header "Authorization: Bearer $WUI_USER_TOKEN"`,
  },
  {
    id: "codex",
    nombre: "Codex",
    donde: "En tu terminal",
    comando: `codex mcp add wui --url https://${HOST}/mcp --bearer-token-env-var WUI_USER_TOKEN`,
  },
  {
    id: "opencode",
    nombre: "OpenCode",
    donde: "En opencode.json",
    comando: `{"mcp":{"wui":{"type":"remote","url":"https://${HOST}/mcp","headers":{"Authorization":"Bearer {env:WUI_USER_TOKEN}"},"oauth":false}}}`,
  },
] as const;

// Herramientas MCP del contrato (contracts/schema/tools.json).
export const HERRAMIENTAS = ["scene", "look", "project", "remove", "ask", "events_wait", "run_flow", "calibrate", "devices", "home"] as const;

// Token de entorno para los agentes.
export const TOKEN_VAR = "WUI_USER_TOKEN";

export const CIFRAS = {
  fps: 60,
  latencia: 215,
  latenciaAntes: 313,
  calibracionMm: "0,5",
  escaneoS: "4,4",
} as const;
