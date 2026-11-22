#!/bin/sh
# Instalador del motor local de pcx (macOS y Linux): pcx, pcx-renderer y pcx-capture en ~/.pcx/bin y los pesos
# de los modelos (SAM y manos) en ~/.pcx/models.
#
#   curl -fsSL <url>/install.sh | sh
#   curl -fsSL <url>/install.sh | sh -s -- --yes --start     # instala sin preguntas y deja el motor corriendo
#   curl -fsSL <url>/install.sh | sh -s -- --check           # verifica una instalación (binario, modelos, cámara, puerto)
#   curl -fsSL <url>/install.sh | sh -s -- --version v0.2.0
#   curl -fsSL <url>/install.sh | sh -s -- --dry-run
#   curl -fsSL <url>/install.sh | sh -s -- --uninstall
#
# Descarga el paquete del GitHub Release que corresponde al SO y la arquitectura, verifica su SHA-256 contra
# SHA256SUMS, verifica cada modelo contra models/SHA256SUMS y lo instala. Es idempotente: si ya está instalado el
# mismo paquete no lo vuelve a bajar. Pide confirmación antes de tocar el archivo de arranque del shell.
# POSIX sh: corre también con dash (Ubuntu) cuando se usa `| sh`.
set -eu

REPO="${PCX_REPO:-satelerd/platanus-hack-26}"
PCX_HOME="${PCX_HOME:-$HOME/.pcx}"
BIN_DIR="$PCX_HOME/bin"
MODELS_DIR="$PCX_HOME/models"
VERSION="${PCX_VERSION:-latest}"
# Carpeta con los archivos de un release (pcx-<target>.tar.gz, SHA256SUMS...). Sirve para espejos y pruebas.
BASE_URL="${PCX_BASE_URL:-}"
# Motor local (docs del protocolo motor ↔ web app) y consola que se empareja con él.
MOTOR_ADDR="${PCX_MOTOR_ADDR:-127.0.0.1:9230}"
CONSOLE_URL="${PCX_CONSOLE_URL:-https://pcx-gateway.onrender.com/}"
LOG_FILE="$PCX_HOME/logs/motor.log"
PID_FILE="$PCX_HOME/motor.pid"
STAMP_FILE="$PCX_HOME/.paquete"
DRY=0
UNINSTALL=0
YES=0
NO_MODIFY_PATH=0
SKILLS="ask"
START=0
STOP=0
CHECK=0
FORCE=0
TMP=""

usage() {
  cat <<EOF
Uso: install.sh [opciones]   (con curl: curl -fsSL URL/install.sh | sh -s -- [opciones])

  --version VER      versión a instalar (p. ej. v0.2.0; por defecto la última)
  -y, --yes          responde que sí a las preguntas (shell y skills)
  --start            al terminar, levanta el motor (pcx up) y muestra la URL para emparejar la consola
  --check            no instala: verifica la instalación (binario, modelos, cámara, puerto del motor)
  --stop             detiene el motor que levantó --start
  --force            reinstala aunque ya esté instalado el mismo paquete
  --dry-run          detecta la plataforma, descarga y verifica el paquete, y muestra lo que haría sin instalar
  --uninstall        quita ~/.pcx/bin, ~/.pcx/models y la línea del shell (deja ~/.pcx/pcx.toml y los datos)
  --no-modify-path   no toca los archivos de arranque del shell
  --skills / --no-skills  instala (o no) las skills y el MCP para agentes al final, sin preguntar
  -h, --help         esta ayuda

Variables: PCX_HOME (por defecto ~/.pcx), PCX_VERSION, PCX_REPO (dueño/repo en GitHub),
           PCX_BASE_URL (carpeta con los archivos de un release; reemplaza a GitHub),
           PCX_MOTOR_ADDR (por defecto 127.0.0.1:9230), PCX_CONSOLE_URL (consola que se empareja con el motor).
EOF
}

say() { printf '%s\n' "pcx: $*"; }
err() { printf '%s\n' "pcx: error: $*" >&2; exit 1; }
plan() { printf '%s\n' "  [dry-run] $*"; }

# Conserva el código de salida del script: el trap no debe convertir un `exit 0` en error.
cleanup() {
  rc=$?
  if [ -n "$TMP" ]; then rm -rf "$TMP"; fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

while [ $# -gt 0 ]; do
  case "$1" in
    --version) [ $# -ge 2 ] || err "falta el valor de --version"; VERSION="$2"; shift ;;
    --version=*) VERSION="${1#*=}" ;;
    --dry-run) DRY=1 ;;
    --uninstall) UNINSTALL=1 ;;
    -y|--yes) YES=1 ;;
    --no-modify-path) NO_MODIFY_PATH=1 ;;
    --skills) SKILLS="yes" ;;
    --no-skills) SKILLS="no" ;;
    --start) START=1 ;;
    --stop) STOP=1 ;;
    --check|--doctor) CHECK=1 ;;
    --force) FORCE=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; err "opción desconocida: $1" ;;
  esac
  shift
done

# Con `curl | sh` la entrada estándar es el script: las preguntas se leen de la terminal (/dev/tty).
confirm() {
  [ "$YES" = 1 ] && return 0
  if [ -r /dev/tty ] && [ -w /dev/tty ]; then
    printf '%s [s/N] ' "pcx: $1" > /dev/tty
    read -r ans < /dev/tty || return 1
    case "$ans" in s|S|si|SI|sí|Sí|y|Y|yes) return 0 ;; esac
  fi
  return 1
}

need() { command -v "$1" >/dev/null 2>&1 || err "hace falta '$1' y no está instalado"; }

# ---------- plataforma ----------
detect_target() {
  os="$(uname -s)"
  arch="$(uname -m)"
  case "$arch" in
    x86_64|amd64) arch="x86_64" ;;
    arm64|aarch64) arch="aarch64" ;;
    *) err "arquitectura no soportada: $arch (hay paquetes para x86_64 y aarch64)" ;;
  esac
  case "$os" in
    Darwin)
      # Una terminal bajo Rosetta dice x86_64 en un Mac con Apple Silicon: se prefiere el binario nativo.
      if [ "$arch" = x86_64 ] && [ "$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)" = 1 ]; then
        arch="aarch64"
      fi
      TARGET="$arch-apple-darwin" ;;
    Linux)
      if ls /lib/ld-musl-* >/dev/null 2>&1 && ! ls /lib*/ld-linux* >/dev/null 2>&1; then
        err "esta distro usa musl (Alpine): los paquetes de pcx son para glibc"
      fi
      TARGET="$arch-unknown-linux-gnu" ;;
    MINGW*|MSYS*|CYGWIN*) err "en Windows usa PowerShell: irm <url>/install.ps1 | iex" ;;
    *) err "sistema operativo no soportado: $os" ;;
  esac
}

asset_url() {
  if [ -n "$BASE_URL" ]; then
    printf '%s/%s' "${BASE_URL%/}" "$1"
  elif [ "$VERSION" = latest ]; then
    printf 'https://github.com/%s/releases/latest/download/%s' "$REPO" "$1"
  else
    printf 'https://github.com/%s/releases/download/%s/%s' "$REPO" "$VERSION" "$1"
  fi
}

download() {
  # $1 url, $2 destino
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL --proto '=https,http' --retry 3 -o "$2" "$1"
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O "$2" "$1"
  else
    err "hace falta curl o wget"
  fi
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | cut -d' ' -f1
  else err "hace falta sha256sum o shasum para verificar la descarga"
  fi
}

# sha256 esperado de $1 según SHA256SUMS (ya descargado en $TMP).
sums_entry() {
  awk -v f="$1" '{n=$2; sub(/^\*/, "", n)} n==f {print $1; exit}' "$TMP/SHA256SUMS"
}

# Descarga $1 y lo verifica contra SHA256SUMS (ya descargado en $TMP).
fetch_verified() {
  file="$1"
  want="$(sums_entry "$file")"
  [ -n "$want" ] || err "esta versión no trae $file (no aparece en SHA256SUMS); prueba otra con --version"
  download "$(asset_url "$file")" "$TMP/$file" || err "no se pudo descargar $(asset_url "$file")"
  got="$(sha256_of "$TMP/$file")"
  [ "$want" = "$got" ] || err "checksum de $file no coincide (esperado $want, obtenido $got)"
  say "sha256 ok: $file"
}

# Verifica cada archivo listado en $1/SHA256SUMS. Imprime los que fallan; devuelve 1 si alguno falla.
verify_sums() {
  dir="$1"
  [ -f "$dir/SHA256SUMS" ] || return 1
  bad=0
  while read -r sum name; do
    name="${name#\*}"
    [ -n "$name" ] || continue
    case "$name" in /*|*..*) say "nombre inválido en $dir/SHA256SUMS: $name"; bad=1; continue ;; esac
    if [ ! -f "$dir/$name" ]; then say "falta $dir/$name"; bad=1; continue; fi
    if [ "$(sha256_of "$dir/$name")" != "$sum" ]; then say "sha256 de $dir/$name no coincide"; bad=1; fi
  done < "$dir/SHA256SUMS"
  return "$bad"
}

# ---------- librerías del sistema (Linux) ----------
# El paquete trae sus librerías propias; estas vienen de la distro. Solo avisa: el core y el MCP funcionan sin ellas.
lib_present() {
  { ldconfig -p 2>/dev/null || /sbin/ldconfig -p 2>/dev/null; } | grep -qF "$1" && return 0
  for d in /lib /lib64 /usr/lib /usr/lib64 /lib/*-linux-gnu /usr/lib/*-linux-gnu; do
    [ -e "$d/$1" ] && return 0
  done
  return 1
}

pkg_hint() {
  case "$1" in
    libfontconfig.so.1) echo "libfontconfig1 (Debian/Ubuntu) o fontconfig (Fedora/Arch)" ;;
    libvulkan.so.1) echo "libvulkan1 + mesa-vulkan-drivers (Debian/Ubuntu), vulkan-loader (Fedora) o vulkan-icd-loader (Arch)" ;;
    *) echo "el paquete de tu distro que traiga $1" ;;
  esac
}

# $1: carpeta bin/ del paquete. ldd para las enlazadas; libvulkan se carga con dlopen (wgpu) y se busca aparte.
check_linux_libs() {
  [ "$(uname -s)" = Linux ] || return 0
  missing=""
  if command -v ldd >/dev/null 2>&1; then
    missing="$(for f in "$1"/*; do ldd "$f" 2>/dev/null || true; done | awk '/not found/ {print $1}' | sort -u)"
  fi
  lib_present libvulkan.so.1 || missing="$missing libvulkan.so.1"
  for lib in $missing; do
    say "aviso: falta la librería del sistema $lib. Instala: $(pkg_hint "$lib")"
  done
}

# ---------- shell: PATH y PCX_MODELS_DIR ----------
# Las rutas se escriben en los archivos de arranque como literales: un PCX_HOME con $, comillas o backticks no
# debe ejecutar nada al abrir otra terminal.
case "$PCX_HOME" in *"
"*) err "PCX_HOME no puede contener saltos de línea" ;; esac
# 'texto' para sh, bash y zsh: cada ' se escribe como '\''.
sh_quote() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }
# 'texto' para fish: dentro de comillas simples solo \ y ' llevan escape.
fish_quote() { printf "'%s'" "$(printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e "s/'/\\\\'/g")"; }
ENV_FILE="$PCX_HOME/env"
SOURCE_LINE=". $(sh_quote "$ENV_FILE") # pcx"
FISH_FILE="$HOME/.config/fish/conf.d/pcx.fish"

rc_files() {
  case "$(basename "${SHELL:-sh}")" in
    zsh) echo "${ZDOTDIR:-$HOME}/.zshrc" ;;
    bash)
      if [ "$(uname -s)" = Darwin ]; then echo "$HOME/.bash_profile"; else echo "$HOME/.bashrc"; fi ;;
    fish) echo "$FISH_FILE" ;;
    *) echo "$HOME/.profile" ;;
  esac
}

# ~/.pcx/env: PATH y PCX_MODELS_DIR. Vive dentro de ~/.pcx, así que se reescribe siempre (idempotente).
write_env_file() {
  q="$(sh_quote "$BIN_DIR")"
  # shellcheck disable=SC2016  # $PATH se expande al cargar env, no ahora
  printf 'case ":$PATH:" in *:%s:*) ;; *) export PATH=%s":$PATH" ;; esac\nexport PCX_MODELS_DIR=%s\n' \
    "$q" "$q" "$(sh_quote "$MODELS_DIR")" > "$ENV_FILE"
}

add_to_shell() {
  rc="$(rc_files)"
  case "$rc" in
    *.fish) grep -q 'PCX_MODELS_DIR' "$rc" 2>/dev/null && { say "el shell ya carga pcx ($rc)"; return 0; } ;;
    *) grep -qxF "$SOURCE_LINE" "$rc" 2>/dev/null && { say "el shell ya carga $ENV_FILE ($rc)"; return 0; } ;;
  esac
  if [ "$NO_MODIFY_PATH" = 1 ]; then
    say "para usar pcx en tu shell: . \"$ENV_FILE\" (o agrégalo a $rc)"; return 0
  fi
  if [ "$DRY" = 1 ]; then
    plan "preguntaría si agregar $BIN_DIR al PATH y PCX_MODELS_DIR en $rc"; return 0
  fi
  if ! confirm "¿Agrego $BIN_DIR al PATH y PCX_MODELS_DIR en $rc?"; then
    say "no toqué $rc. Para esta terminal: . \"$ENV_FILE\""; return 0
  fi
  case "$rc" in
    *.fish)
      mkdir -p "$(dirname "$rc")"
      printf 'fish_add_path -g %s # pcx\nset -gx PCX_MODELS_DIR %s # pcx\n' \
        "$(fish_quote "$BIN_DIR")" "$(fish_quote "$MODELS_DIR")" > "$rc" ;;
    *)
      touch "$rc"
      printf '\n%s\n' "$SOURCE_LINE" >> "$rc" ;;
  esac
  say "shell configurado en $rc (abre una terminal nueva o corre: . \"$ENV_FILE\")"
}

remove_from_shell() {
  for rc in "$HOME/.zshrc" "${ZDOTDIR:-$HOME}/.zshrc" "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.profile"; do
    if [ ! -f "$rc" ] || ! grep -qxF "$SOURCE_LINE" "$rc"; then continue; fi
    if [ "$DRY" = 1 ]; then plan "quitaría la línea de pcx de $rc"; continue; fi
    tmp_rc="$rc.pcx-tmp"
    grep -vxF "$SOURCE_LINE" "$rc" > "$tmp_rc" || true
    cat "$tmp_rc" > "$rc" && rm -f "$tmp_rc"
    say "quité la línea de pcx de $rc"
  done
  if [ -f "$FISH_FILE" ]; then
    if [ "$DRY" = 1 ]; then plan "borraría $FISH_FILE"; else rm -f "$FISH_FILE"; fi
  fi
}

# ---------- pcx.toml: PCX_MODELS_DIR para los procesos de `pcx up` ----------
# Solo agrega la variable si falta (no pisa un valor elegido por la persona). Cadena literal de TOML: '...'.
set_models_in_config() {
  cfg="$PCX_HOME/pcx.toml"
  [ -f "$cfg" ] || return 0
  grep -q '^[[:space:]]*PCX_MODELS_DIR[[:space:]]*=' "$cfg" && return 0
  case "$MODELS_DIR" in *"'"*) say "aviso: la ruta de modelos tiene comillas; agrega PCX_MODELS_DIR a mano en $cfg"; return 0 ;; esac
  line="PCX_MODELS_DIR = '$MODELS_DIR'"
  if grep -q '^[[:space:]]*\[env\][[:space:]]*$' "$cfg"; then
    LINE="$line" awk '{print} !done && /^[[:space:]]*\[env\][[:space:]]*$/ {print ENVIRON["LINE"]; done=1}' "$cfg" > "$cfg.pcx-tmp"
    cat "$cfg.pcx-tmp" > "$cfg" && rm -f "$cfg.pcx-tmp"
  else
    printf '\n[env]\n%s\n' "$line" >> "$cfg"
  fi
  say "PCX_MODELS_DIR agregado a $cfg"
}

# ---------- modelos ----------
install_models() {
  src="$1/models"
  if [ ! -f "$src/SHA256SUMS" ]; then
    say "aviso: este paquete no trae modelos; el motor los bajará la primera vez que los necesite"
    return 0
  fi
  verify_sums "$src" || err "los modelos del paquete no pasan la verificación sha256"
  mkdir -p "$MODELS_DIR"
  while read -r sum name; do
    name="${name#\*}"
    [ -n "$name" ] || continue
    dst="$MODELS_DIR/$name"
    if [ -f "$dst" ] && [ "$(sha256_of "$dst")" = "$sum" ]; then continue; fi
    mkdir -p "$(dirname "$dst")"
    cp "$src/$name" "$dst.new" && mv -f "$dst.new" "$dst"
  done < "$src/SHA256SUMS"
  [ -f "$src/manifest.json" ] && cp "$src/manifest.json" "$MODELS_DIR/manifest.json"
  cp "$src/SHA256SUMS" "$MODELS_DIR/SHA256SUMS"
  verify_sums "$MODELS_DIR" || err "los modelos instalados en $MODELS_DIR no pasan la verificación sha256"
  say "modelos verificados en $MODELS_DIR ($(grep -c . "$MODELS_DIR/SHA256SUMS") archivos, sha256 ok)"
}

# ---------- motor: estado, arranque y parada ----------
http_ok() {
  if command -v curl >/dev/null 2>&1; then curl -fsS --noproxy '*' --max-time 2 -o /dev/null "$1" 2>/dev/null
  elif command -v wget >/dev/null 2>&1; then wget -q --no-proxy -T 2 -O /dev/null "$1" 2>/dev/null
  else return 1
  fi
}
motor_up() { http_ok "http://$MOTOR_ADDR/v1/health"; }

port_busy() {
  port="${MOTOR_ADDR##*:}"
  if command -v lsof >/dev/null 2>&1; then lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
  elif command -v ss >/dev/null 2>&1; then ss -ltn "sport = :$port" 2>/dev/null | grep -q LISTEN
  elif command -v nc >/dev/null 2>&1; then nc -z "${MOTOR_ADDR%:*}" "$port" >/dev/null 2>&1
  else return 1
  fi
}

# pid de `pcx up` lanzado por --start, solo si sigue vivo y es pcx (un pid reciclado no se toca).
motor_pid() {
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  case "$pid" in ''|*[!0-9]*) return 1 ;; esac
  kill -0 "$pid" 2>/dev/null || return 1
  # args y no comm: recién lanzado figura aún como `nohup …/pcx up`, antes de su exec.
  ps -p "$pid" -o args= 2>/dev/null | grep -q pcx || return 1
  printf '%s' "$pid"
}

token_file() {
  for f in "$PCX_HOME/motor.token" "$HOME/.pcx/motor.token"; do
    if [ -s "$f" ]; then printf '%s' "$f"; return 0; fi
  done
  return 1
}

pairing_url() {
  if f="$(token_file)"; then
    printf '%s?motor=ws://%s&motor_token=%s' "$CONSOLE_URL" "$MOTOR_ADDR" "$(tr -d ' \r\n' < "$f")"
  elif [ -f "$LOG_FILE" ]; then
    grep -o 'https://[^[:space:]"]*motor_token=[^[:space:]"]*' "$LOG_FILE" | tail -n 1
  fi
}

stop_motor() {
  if ! pid="$(motor_pid)"; then
    rm -f "$PID_FILE"
    say "no hay un motor lanzado por este instalador"
    return 0
  fi
  kill "$pid" 2>/dev/null || true
  i=0
  while kill -0 "$pid" 2>/dev/null && [ "$i" -lt 10 ]; do sleep 1; i=$((i + 1)); done
  kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true
  rm -f "$PID_FILE"
  say "motor detenido (pid $pid)"
}

start_motor() {
  pcx="$BIN_DIR/pcx"
  [ -x "$pcx" ] || err "pcx no está instalado en $BIN_DIR"
  [ -f "$PCX_HOME/pcx.toml" ] || err "falta $PCX_HOME/pcx.toml"
  if motor_up; then
    say "el motor ya responde en $MOTOR_ADDR"
  else
    if pid="$(motor_pid)"; then
      say "pcx up ya corre (pid $pid); espero a que el motor responda"
    else
      port_busy && err "el puerto ${MOTOR_ADDR##*:} está ocupado por otro programa: ciérralo y vuelve a correr con --start"
      mkdir -p "$(dirname "$LOG_FILE")"
      printf '\n== pcx up %s ==\n' "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
      # Proceso aparte (setsid si existe) para que siga vivo cuando termine la terminal o el agente que corrió esto.
      launcher=""
      command -v setsid >/dev/null 2>&1 && launcher="setsid"
      # shellcheck disable=SC2086  # $launcher vacío no debe convertirse en un argumento ''
      PATH="$BIN_DIR:$PATH" PCX_MODELS_DIR="$MODELS_DIR" \
        $launcher nohup "$pcx" up --config "$PCX_HOME/pcx.toml" >> "$LOG_FILE" 2>&1 < /dev/null &
      echo "$!" > "$PID_FILE"
      say "arranqué pcx up (pid $!, log en $LOG_FILE)"
      sleep 1
    fi
    i=0
    while ! motor_up; do
      if ! motor_pid >/dev/null; then
        say "pcx up terminó antes de levantar el motor. Últimas líneas de $LOG_FILE:"
        tail -n 20 "$LOG_FILE" >&2 || true
        return 1
      fi
      if [ "$i" -ge 60 ]; then
        say "el motor no respondió en http://$MOTOR_ADDR/v1/health tras 60 s (¿esta versión trae el motor?)."
        say "pcx up sigue corriendo; revisa $LOG_FILE o detenlo con --stop"
        return 1
      fi
      sleep 1; i=$((i + 1))
    done
    say "el motor responde en $MOTOR_ADDR"
  fi
  i=0
  while ! token_file >/dev/null && [ "$i" -lt 10 ]; do sleep 1; i=$((i + 1)); done
  url="$(pairing_url || true)"
  [ -n "$url" ] || { say "no encontré ~/.pcx/motor.token ni la URL en $LOG_FILE"; return 1; }
  cat <<EOF

Motor local listo. Abre esta URL en Chrome o Edge, en este mismo computador, para emparejar la consola:

  $url

EOF
  if [ "$(uname -s)" = Darwin ]; then
    say "macOS pedirá permiso de cámara para la terminal que lanzó pcx la primera vez (Ajustes > Privacidad > Cámara)."
  fi
}

# ---------- verificación (pcx doctor si existe; si no, chequeo mínimo) ----------
FAILS=0
ok() { say "[ok] $*"; }
bad() { say "[falla] $*"; FAILS=$((FAILS + 1)); }
warn() { say "[aviso] $*"; }

doctor() {
  FAILS=0
  pcx="$BIN_DIR/pcx"
  if [ ! -x "$pcx" ]; then bad "no está $pcx (instala primero)"; return 1; fi
  if v="$("$pcx" --version 2>&1)"; then ok "binario: $v"; else bad "binario: $pcx no corre: $v"; return 1; fi
  if "$pcx" doctor --help >/dev/null 2>&1; then
    say "uso pcx doctor"
    PATH="$BIN_DIR:$PATH" PCX_MODELS_DIR="$MODELS_DIR" "$pcx" doctor
    return $?
  fi
  if verify_sums "$MODELS_DIR"; then
    ok "modelos: $(grep -c . "$MODELS_DIR/SHA256SUMS") archivos con sha256 correcto en $MODELS_DIR"
  else
    bad "modelos: faltan o no calzan en $MODELS_DIR (reinstala con --force)"
  fi
  cams="$("$pcx" cameras 2>/dev/null | grep -c . || true)"
  if [ "${cams:-0}" -gt 0 ]; then ok "cámara: $cams detectada(s) (pcx cameras)"
  else warn "cámara: no se detectó ninguna; el motor pedirá cuadros a la consola"
  fi
  if motor_up; then ok "motor: responde en http://$MOTOR_ADDR/v1/health"
  elif port_busy; then bad "puerto ${MOTOR_ADDR##*:}: ocupado por otro programa (el motor no podrá escuchar)"
  else ok "puerto ${MOTOR_ADDR##*:}: libre (motor detenido; arráncalo con --start)"
  fi
  [ "$FAILS" = 0 ]
}

# ---------- skills y MCP para agentes ----------
# No se duplica nada: se baja pcx-integrations.tar.gz del mismo release y se corre su integrations/install.sh.
offer_skills() {
  [ "$SKILLS" = no ] && return 0
  dir="$PCX_HOME/agentes"
  if [ "$DRY" = 1 ]; then
    plan "ofrecería instalar las skills y el MCP para agentes (pcx-integrations.tar.gz -> $dir, integrations/install.sh --yes)"
    return 0
  fi
  if ! grep -q ' \*\{0,1\}pcx-integrations.tar.gz$' "$TMP/SHA256SUMS"; then
    return 0  # release sin integraciones
  fi
  if [ "$SKILLS" = ask ] && ! confirm "¿Instalo también las skills y el MCP de pcx para Claude Code, Codex y OpenCode?"; then
    say "luego puedes correr: $dir/integrations/install.sh"
    return 0
  fi
  fetch_verified pcx-integrations.tar.gz
  rm -rf "$dir" && mkdir -p "$dir"
  tar xzf "$TMP/pcx-integrations.tar.gz" -C "$dir"
  if command -v bash >/dev/null 2>&1; then
    bash "$dir/integrations/install.sh" --yes || say "las skills no se instalaron; reintenta con: $dir/integrations/install.sh --yes"
  else
    say "integrations/install.sh necesita bash; instálalo y corre: $dir/integrations/install.sh --yes"
  fi
}

# ---------- solo verificar o detener ----------
if [ "$CHECK" = 1 ]; then
  doctor && exit 0
  exit 1
fi
if [ "$STOP" = 1 ] && [ "$UNINSTALL" = 0 ]; then
  stop_motor
  exit 0
fi

# ---------- desinstalar ----------
if [ "$UNINSTALL" = 1 ]; then
  if [ "$DRY" = 1 ]; then
    plan "detendría el motor y borraría $BIN_DIR, $MODELS_DIR y $ENV_FILE"
  else
    stop_motor
    rm -rf "$BIN_DIR" "$MODELS_DIR" "$ENV_FILE" "$STAMP_FILE" "$PCX_HOME/pcx.toml.dist"
  fi
  remove_from_shell
  if [ -f "$PCX_HOME/agentes/integrations/install.sh" ]; then
    if [ "$DRY" = 1 ]; then
      plan "preguntaría si quitar las skills y el MCP (integrations/install.sh --uninstall --yes)"
    elif confirm "¿Quito también las skills y el MCP de pcx de tus agentes?"; then
      bash "$PCX_HOME/agentes/integrations/install.sh" --uninstall --yes || true
      rm -rf "$PCX_HOME/agentes"
    fi
  fi
  [ "$DRY" = 1 ] && { say "dry-run: no se borró nada"; exit 0; }
  say "pcx desinstalado. Se conservan $PCX_HOME/pcx.toml y los datos; bórralos con: rm -rf $(sh_quote "$PCX_HOME")"
  exit 0
fi

# ---------- instalar ----------
need uname
need tar
detect_target
ARCHIVE="pcx-$TARGET.tar.gz"
say "plataforma: $TARGET · versión: $VERSION"
say "origen: $(asset_url "$ARCHIVE")"

TMP="$(mktemp -d 2>/dev/null || mktemp -d -t pcx)"
download "$(asset_url SHA256SUMS)" "$TMP/SHA256SUMS" || err "no se pudo descargar $(asset_url SHA256SUMS) (¿existe esa versión?)"
PKG_SHA="$(sums_entry "$ARCHIVE")"
[ -n "$PKG_SHA" ] || err "esta versión no trae $ARCHIVE (no aparece en SHA256SUMS); prueba otra con --version"

# Idempotente: mismo paquete ya instalado y modelos intactos → no se vuelve a bajar.
CURRENT=0
if [ "$FORCE" = 0 ] && [ -x "$BIN_DIR/pcx" ] && [ "$(cat "$STAMP_FILE" 2>/dev/null || true)" = "$ARCHIVE $PKG_SHA" ] \
  && verify_sums "$MODELS_DIR" >/dev/null 2>&1; then
  CURRENT=1
  say "ya está instalado este paquete ($ARCHIVE, sha256 ok); no lo vuelvo a bajar (--force para reinstalar)"
fi

if [ "$CURRENT" = 0 ]; then
  fetch_verified "$ARCHIVE"
  tar xzf "$TMP/$ARCHIVE" -C "$TMP"
  PKG="$TMP/pcx-$TARGET"
  [ -x "$PKG/bin/pcx" ] || err "el paquete no trae bin/pcx"
  check_linux_libs "$PKG/bin"

  if [ "$DRY" = 1 ]; then
    say "dry-run: el paquete está verificado; no se instaló nada"
    plan "copiaría a $BIN_DIR: $(for f in "$PKG/bin/"*; do printf '%s ' "${f##*/}"; done)"
    if [ -f "$PKG/models/SHA256SUMS" ]; then
      verify_sums "$PKG/models" || err "los modelos del paquete no pasan la verificación sha256"
      plan "copiaría $(grep -c . "$PKG/models/SHA256SUMS") archivos de modelos verificados a $MODELS_DIR"
    else
      plan "el paquete no trae modelos"
    fi
    if [ -f "$PCX_HOME/pcx.toml" ]; then plan "mantendría $PCX_HOME/pcx.toml (ya existe) y le agregaría PCX_MODELS_DIR si falta"
    else plan "copiaría pcx.toml a $PCX_HOME/pcx.toml con PCX_MODELS_DIR"; fi
    add_to_shell
    offer_skills
    [ "$START" = 1 ] && plan "levantaría el motor (pcx up) y mostraría la URL de emparejamiento"
    exit 0
  fi

  mkdir -p "$BIN_DIR"
  # Reemplazo con mv: un binario en ejecución (pcx up) sigue funcionando con su inodo viejo.
  for f in "$PKG/bin/"*; do
    name="$(basename "$f")"
    cp "$f" "$BIN_DIR/.$name.new"
    chmod 755 "$BIN_DIR/.$name.new"
    mv -f "$BIN_DIR/.$name.new" "$BIN_DIR/$name"
  done
  install_models "$PKG"
  [ -f "$PCX_HOME/pcx.toml" ] || cp "$PKG/pcx.toml" "$PCX_HOME/pcx.toml"
  # Copia de referencia del pcx.toml de esta versión: el tuyo no se pisa, compáralo si el motor no arranca.
  cp "$PKG/pcx.toml" "$PCX_HOME/pcx.toml.dist"
  cp "$PKG/VERSION" "$PCX_HOME/VERSION" 2>/dev/null || true
  printf '%s %s\n' "$ARCHIVE" "$PKG_SHA" > "$STAMP_FILE"
  say "instalado en $PCX_HOME ($(cat "$PCX_HOME/VERSION" 2>/dev/null || echo "$VERSION"))"
elif [ "$DRY" = 1 ]; then
  say "dry-run: nada que hacer"
  exit 0
fi

set_models_in_config
write_env_file
add_to_shell
offer_skills

say "verificación:"
doctor || say "la verificación encontró problemas (arriba); detalles en docs/SETUP-AGENTE.md"

if [ "$START" = 1 ]; then
  start_motor || exit 1
else
  cat <<EOF

Listo. Para levantar el motor y emparejar la consola, vuelve a correr este instalador con --start
(no baja nada si ya está instalado), o en una terminal nueva: pcx up --config "$PCX_HOME/pcx.toml"
EOF
  if [ "$(uname -s)" = Darwin ]; then
    say "macOS pedirá permiso de cámara la primera vez que pcx-capture la abra (Ajustes > Privacidad > Cámara)."
  fi
fi
