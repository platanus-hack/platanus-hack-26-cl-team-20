#!/usr/bin/env bash
# Instala las skills de pcx y registra su servidor MCP en Claude Code, Codex y OpenCode.
#
# Por defecto NO cambia nada: muestra lo que haría. Solo escribe con --yes.
# Nunca guarda el token: los tres clientes lo leen de una variable de entorno en cada sesión.
#
#   integrations/install.sh                      # plan (no escribe)
#   integrations/install.sh --yes                # aplica en los agentes detectados
#   integrations/install.sh --yes --agents codex # solo Codex
#   integrations/install.sh --yes --url https://HOST/mcp
#   integrations/install.sh --uninstall --yes
set -euo pipefail

APPLY=0
DRY=0
UNINSTALL=0
AGENTS=""
URL="${PCX_MCP_URL:-http://127.0.0.1:9200/mcp}"
TOKEN_ENV="PCX_MCP_TOKEN"
LINK=0
DO_SKILLS=1
DO_MCP=1
SERVER="pcx"
OPENCODE_CFG=""
MARKETPLACE="pcx-agentes"

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/.." && pwd)"
SKILLS_SRC="$here/skills"
SKILLS_DST="${PCX_SKILLS_DIR:-$HOME/.agents/skills}"

usage() {
  cat <<EOF
Uso: integrations/install.sh [opciones]

  --yes               aplica los cambios (sin esto solo se muestra el plan)
  --dry-run           muestra el plan aunque se pase --yes
  --agents LISTA      claude,codex,opencode (por defecto: los que estén instalados)
  --url URL           URL del MCP de pcx (por defecto \$PCX_MCP_URL o http://127.0.0.1:9200/mcp)
  --token-env NOMBRE  variable de entorno que tendrá el token (por defecto PCX_MCP_TOKEN)
  --skills-dir DIR    destino de las skills para Codex y OpenCode (por defecto ~/.agents/skills)
  --opencode-config F archivo de OpenCode a editar (por defecto ~/.config/opencode/opencode.json;
                      usa ./opencode.json para registrar el servidor solo en un proyecto)
  --link              enlaza las skills (symlink) en vez de copiarlas: se actualizan con git pull
  --no-skills         no instala skills
  --no-mcp            no registra el servidor MCP
  --uninstall         quita lo instalado por este script
  -h, --help          esta ayuda

Qué hace en cada agente:
  Claude Code  agrega este repo como marketplace e instala el plugin pcx (skills, /pcx-estado y MCP).
  Codex        copia las skills a ~/.agents/skills y corre: codex mcp add $SERVER --url URL --bearer-token-env-var NOMBRE
  OpenCode     usa las mismas skills de ~/.agents/skills y agrega el servidor a ~/.config/opencode/opencode.json
               (con respaldo del archivo original).
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --yes|-y) APPLY=1 ;;
    --dry-run) DRY=1 ;;
    --agents) AGENTS="${2:?falta la lista}"; shift ;;
    --url) URL="${2:?falta la URL}"; shift ;;
    --token-env) TOKEN_ENV="${2:?falta el nombre}"; shift ;;
    --skills-dir) SKILLS_DST="${2:?falta el directorio}"; shift ;;
    --opencode-config) OPENCODE_CFG="${2:?falta el archivo}"; shift ;;
    --link) LINK=1 ;;
    --no-skills) DO_SKILLS=0 ;;
    --no-mcp) DO_MCP=0 ;;
    --uninstall) UNINSTALL=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "opción desconocida: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done
[ "$DRY" = 1 ] && APPLY=0

case "$TOKEN_ENV" in
  ''|*[!A-Za-z0-9_]*|[0-9]*) echo "--token-env debe ser un nombre de variable (letras, dígitos y _)" >&2; exit 2 ;;
esac
case "$URL" in
  http://*|https://*) ;;
  *) echo "--url debe empezar con http:// o https://" >&2; exit 2 ;;
esac

have() { command -v "$1" >/dev/null 2>&1; }
say() { printf '%s\n' "$*"; }
step() { printf '\n== %s ==\n' "$*"; }

# run "descripción" comando args…  → imprime siempre; ejecuta solo con --yes
run() {
  local desc="$1"; shift
  if [ "$APPLY" = 1 ]; then
    say "  hago: $desc"
    "$@"
  else
    say "  haría: $desc"
    say "         \$ $*"
  fi
}

# Igual que run, pero un fallo del comando no aborta: se avisa y se sigue (para desinstalar lo que quizá no estaba).
try_run() {
  local desc="$1"; shift
  if [ "$APPLY" = 1 ]; then
    say "  hago: $desc"
    "$@" || say "  (no estaba o no se pudo: sigo con lo demás)"
  else
    say "  haría: $desc"
    say "         \$ $*"
  fi
}

if [ -z "$AGENTS" ]; then
  for a in claude codex opencode; do
    if have "$a"; then AGENTS="${AGENTS:+$AGENTS,}$a"; fi
  done
fi
wants() { case ",$AGENTS," in *",$1,"*) return 0 ;; *) return 1 ;; esac; }

say "pcx · instalador para agentes"
say "  modo:      $([ "$APPLY" = 1 ] && echo 'APLICAR (--yes)' || echo 'plan, no se escribe nada (agrega --yes para aplicar)')"
say "  acción:    $([ "$UNINSTALL" = 1 ] && echo desinstalar || echo instalar)"
say "  agentes:   ${AGENTS:-ninguno detectado}"
say "  MCP:       $URL  (token en \$$TOKEN_ENV; no se guarda su valor)"
say "  skills:    $SKILLS_SRC → $SKILLS_DST"

if [ -z "$AGENTS" ]; then
  say ""
  say "No encontré claude, codex ni opencode en el PATH. Instala alguno o usa --agents."
  exit 1
fi
if [ ! -d "$SKILLS_SRC" ]; then
  echo "no existe $SKILLS_SRC (¿corriste el script desde el repo de pcx?)" >&2
  exit 1
fi

skill_names() { for d in "$SKILLS_SRC"/*/; do [ -f "$d/SKILL.md" ] && basename "$d"; done; }

install_shared_skills() {
  step "Skills en $SKILLS_DST (Codex y OpenCode leen esta carpeta)"
  if [ "$APPLY" = 1 ]; then mkdir -p "$SKILLS_DST"; fi
  local name src dst
  for name in $(skill_names); do
    src="$SKILLS_SRC/$name"; dst="$SKILLS_DST/$name"
    if [ "$UNINSTALL" = 1 ]; then
      if [ -e "$dst" ] || [ -L "$dst" ]; then try_run "quitar la skill $name" rm -rf "$dst"; else say "  $name: no estaba"; fi
      continue
    fi
    if [ -e "$dst" ] && [ ! -L "$dst" ] && [ ! -f "$dst/SKILL.md" ]; then
      say "  $name: $dst existe y no parece una skill; no la toco"
      continue
    fi
    if [ "$LINK" = 1 ]; then
      run "enlazar la skill $name" ln -sfn "$src" "$dst"
    else
      if [ "$APPLY" = 1 ]; then
        say "  hago: copiar la skill $name"
        rm -rf "$dst"; cp -R "$src" "$dst"
      else
        say "  haría: copiar la skill $name"
        say "         \$ rm -rf $dst && cp -R $src $dst"
      fi
    fi
  done
}

claude_step() {
  step "Claude Code"
  if ! have claude; then say "  claude no está en el PATH; lo salto"; return 0; fi
  if [ "$UNINSTALL" = 1 ]; then
    try_run "desinstalar el plugin pcx" claude plugin uninstall "pcx@$MARKETPLACE"
    try_run "quitar el marketplace $MARKETPLACE" claude plugin marketplace remove "$MARKETPLACE"
    return 0
  fi
  bash "$here/sync.sh" --check >/dev/null 2>&1 || say "  aviso: integrations/claude-plugin/skills está desactualizado; corre integrations/sync.sh"
  run "agregar este repo como marketplace ($MARKETPLACE)" claude plugin marketplace add "$repo"
  run "instalar el plugin pcx (skills + /pcx-estado + MCP)" claude plugin install "pcx@$MARKETPLACE"
  say "  El plugin lee PCX_MCP_URL (por defecto http://127.0.0.1:9200/mcp) y PCX_MCP_TOKEN del entorno."
  if [ "$TOKEN_ENV" != "PCX_MCP_TOKEN" ]; then
    say "  aviso: el plugin de Claude Code siempre usa PCX_MCP_TOKEN; --token-env solo aplica a Codex y OpenCode."
  fi
}

codex_step() {
  step "Codex"
  if ! have codex; then say "  codex no está en el PATH; lo salto"; return 0; fi
  [ "$DO_MCP" = 1 ] || return 0
  if [ "$UNINSTALL" = 1 ]; then
    try_run "quitar el servidor MCP $SERVER" codex mcp remove "$SERVER"
    return 0
  fi
  if codex mcp get "$SERVER" >/dev/null 2>&1; then
    say "  ya existe un servidor MCP «${SERVER}» en Codex; no lo piso (quítalo con: codex mcp remove $SERVER)"
  else
    run "registrar el servidor MCP $SERVER" codex mcp add "$SERVER" --url "$URL" --bearer-token-env-var "$TOKEN_ENV"
  fi
  say "  Sugerido en ${CODEX_HOME:-~/.codex}/config.toml, bajo [mcp_servers.$SERVER]: tool_timeout_sec = 120"
  say "  (ask y events_wait esperan hasta 50 s; el plazo por defecto de Codex es 60 s)."
}

opencode_config() {
  if [ -n "$OPENCODE_CFG" ]; then echo "$OPENCODE_CFG"; return 0; fi
  local dir="${XDG_CONFIG_HOME:-$HOME/.config}/opencode"
  if [ -f "$dir/opencode.jsonc" ] && [ ! -f "$dir/opencode.json" ]; then echo "$dir/opencode.jsonc"; else echo "$dir/opencode.json"; fi
}

opencode_snippet() {
  cat <<EOF
  {"mcp": {"$SERVER": {"type": "remote", "url": "$URL",
                    "headers": {"Authorization": "Bearer {env:$TOKEN_ENV}"}, "oauth": false}}}
EOF
}

opencode_step() {
  step "OpenCode"
  if ! have opencode; then say "  opencode no está en el PATH; lo salto"; return 0; fi
  [ "$DO_MCP" = 1 ] || return 0
  local cfg; cfg="$(opencode_config)"
  local py=""
  if have python3; then py=python3; elif have python; then py=python; fi
  if [ -z "$py" ]; then
    say "  No hay python para editar $cfg con seguridad. Agrega a mano:"
    opencode_snippet
    return 0
  fi
  local mode=add
  [ "$UNINSTALL" = 1 ] && mode=remove
  local apply_flag=0
  [ "$APPLY" = 1 ] && apply_flag=1
  # El JSON se edita con un parser (no con sed) y se respalda antes. Si el archivo tiene comentarios
  # (JSONC) no se toca: se muestra el fragmento para pegarlo a mano.
  PCX_CFG="$cfg" PCX_SERVER="$SERVER" PCX_URL="$URL" PCX_TOKEN_ENV="$TOKEN_ENV" PCX_MODE="$mode" PCX_APPLY="$apply_flag" \
  "$py" - <<'PY'
import json, os, shutil, stat, sys, time

cfg, name, url, env = (os.environ[k] for k in ("PCX_CFG", "PCX_SERVER", "PCX_URL", "PCX_TOKEN_ENV"))
cfg = os.path.realpath(cfg)  # si es un symlink (dotfiles) se edita el archivo real, no se reemplaza el enlace
mode, apply = os.environ["PCX_MODE"], os.environ["PCX_APPLY"] == "1"
entry = {"type": "remote", "url": url, "headers": {"Authorization": "Bearer {env:%s}" % env}, "oauth": False}
verb = "hago" if apply else "haría"

data = {}
if os.path.exists(cfg):
    raw = open(cfg, encoding="utf-8").read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        print("  %s tiene comentarios o no es JSON estricto; no lo toco. Agrega a mano en \"mcp\":" % cfg)
        print("  " + json.dumps({name: entry}))
        sys.exit(0)
if not isinstance(data, dict):
    print("  %s no es un objeto JSON; no lo toco." % cfg)
    sys.exit(0)

mcp = data.get("mcp")
if mcp is not None and not isinstance(mcp, dict):
    print("  la clave \"mcp\" de %s no es un objeto; no lo toco." % cfg)
    sys.exit(0)
# Dos formas según la versión de OpenCode: mcp.<nombre> (1.x) o mcp.servers.<nombre> (documentación nueva).
nested = isinstance(mcp, dict) and isinstance(mcp.get("servers"), dict)
where = "mcp.servers.%s" % name if nested else "mcp.%s" % name
servers = (mcp or {}).get("servers", {}) if nested else (mcp or {})

if mode == "remove":
    if name not in servers:
        print("  %s: no hay %s; nada que quitar" % (cfg, where))
        sys.exit(0)
    print("  %s: quitar %s de %s" % (verb, where, cfg))
    if not apply:
        sys.exit(0)
    del servers[name]
else:
    if name in servers:
        if servers[name] == entry:
            print("  %s ya tiene %s; nada que hacer" % (cfg, where))
        else:
            print("  %s ya tiene un %s distinto; no lo piso. Para reemplazarlo, bórralo y vuelve a correr." % (cfg, where))
        sys.exit(0)
    print("  %s: agregar %s a %s" % (verb, where, cfg))
    print("         " + json.dumps({name: entry}))
    if not apply:
        sys.exit(0)
    data.setdefault("$schema", "https://opencode.ai/config.json")
    if nested:
        data["mcp"]["servers"][name] = entry
    else:
        data.setdefault("mcp", {})[name] = entry

os.makedirs(os.path.dirname(os.path.abspath(cfg)), exist_ok=True)
# Permisos: se conservan los del archivo original (suele ser 0600 porque lleva apiKey de proveedores);
# un archivo nuevo nace 0600. El respaldo queda con el mismo modo.
mode = stat.S_IMODE(os.stat(cfg).st_mode) if os.path.exists(cfg) else 0o600
if os.path.exists(cfg):
    bak = "%s.bak-%s" % (cfg, time.strftime("%Y%m%d%H%M%S"))
    shutil.copy2(cfg, bak)
    os.chmod(bak, mode)
    print("  respaldo: %s" % bak)
tmp = cfg + ".tmp"
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write("\n")
os.chmod(tmp, mode)
os.replace(tmp, cfg)
print("  escrito: %s" % cfg)
PY
}

# Cada cliente por separado: si uno falla se anota y los demás siguen. (Un subshell con `set -e` propio: dentro de
# `cmd || …` bash ignoraría los errores intermedios.)
FAILED=""
step_isolated() {
  local name="$1" fn="$2" rc=0
  set +e
  ( set -e; "$fn" )
  rc=$?
  set -e
  if [ "$rc" -ne 0 ]; then
    say "  aviso: el paso de $name falló (código $rc); sigo con los demás"
    FAILED="${FAILED:+$FAILED, }$name"
  fi
}
if wants claude; then step_isolated "Claude Code" claude_step; fi
if [ "$DO_SKILLS" = 1 ] && { wants codex || wants opencode; }; then step_isolated "skills" install_shared_skills; fi
if wants codex; then step_isolated "Codex" codex_step; fi
if wants opencode; then step_isolated "OpenCode" opencode_step; fi

step "Siguiente paso"
if [ -n "$FAILED" ]; then
  say "Terminó con fallos en: $FAILED. Revisa los avisos de arriba."
elif [ "$UNINSTALL" = 1 ]; then
  if [ "$APPLY" = 1 ]; then say "Desinstalado."; else say "No se cambió nada. Para aplicar: integrations/install.sh --uninstall --yes"; fi
  exit 0
elif [ "$APPLY" = 1 ]; then
  say "Listo. Define en tu shell (no en el repo) y reinicia el agente:"
else
  say "No se cambió nada. Para aplicar: integrations/install.sh --yes"
  say "Después define en tu shell (no en el repo):"
fi
say "  export PCX_MCP_URL=$URL"
say "  export $TOKEN_ENV=<token>     # nodo local: el PCX_TOKEN del nodo · gateway: el PCX_USER_TOKEN"
say "Prueba: pídele al agente «llama scene de pcx» o, en Claude Code, /pcx:pcx-estado"
[ -z "$FAILED" ] || exit 1
