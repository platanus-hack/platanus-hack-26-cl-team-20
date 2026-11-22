# Instala las skills de pcx y registra su servidor MCP en Claude Code, Codex y OpenCode (Windows, PowerShell 5.1+).
#
# Por defecto NO cambia nada: muestra lo que haría. Solo escribe con --yes.
# Nunca guarda el token: los tres clientes lo leen de una variable de entorno en cada sesión.
#
#   .\integrations\install.ps1                        # plan (no escribe)
#   .\integrations\install.ps1 --yes                  # aplica en los agentes detectados
#   .\integrations\install.ps1 --yes --agents codex   # solo Codex
#   .\integrations\install.ps1 --yes --url https://HOST/mcp
#   .\integrations\install.ps1 --uninstall --yes
#
# Acepta las mismas opciones que install.sh, con uno o dos guiones (-yes o --yes).

$ErrorActionPreference = 'Stop'

$Apply = $false
$Dry = $false
$Uninstall = $false
$Agents = ''
$Url = if ($env:PCX_MCP_URL) { $env:PCX_MCP_URL } else { 'http://127.0.0.1:9200/mcp' }
$TokenEnv = 'PCX_MCP_TOKEN'
$DoSkills = $true
$DoMcp = $true
$Server = 'pcx'
$OpenCodeConfig = ''
$Marketplace = 'pcx-agentes'

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = Split-Path -Parent $Here
$SkillsSrc = Join-Path $Here 'skills'
$SkillsDst = if ($env:PCX_SKILLS_DIR) { $env:PCX_SKILLS_DIR } else { Join-Path $HOME '.agents\skills' }

function Show-Usage {
@"
Uso: .\integrations\install.ps1 [opciones]

  --yes               aplica los cambios (sin esto solo se muestra el plan)
  --dry-run           muestra el plan aunque se pase --yes
  --agents LISTA      claude,codex,opencode (por defecto: los que estén instalados)
  --url URL           URL del MCP de pcx (por defecto `$env:PCX_MCP_URL o http://127.0.0.1:9200/mcp)
  --token-env NOMBRE  variable de entorno que tendrá el token (por defecto PCX_MCP_TOKEN)
  --skills-dir DIR    destino de las skills para Codex y OpenCode (por defecto ~\.agents\skills)
  --opencode-config F archivo de OpenCode a editar (por defecto ~\.config\opencode\opencode.json;
                      usa .\opencode.json para registrar el servidor solo en un proyecto)
  --no-skills         no instala skills
  --no-mcp            no registra el servidor MCP
  --uninstall         quita lo instalado por este script
  --help              esta ayuda
"@ | Write-Host
}

$i = 0
while ($i -lt $args.Count) {
  $a = ([string]$args[$i]).TrimStart('-').ToLowerInvariant()
  switch ($a) {
    'yes'        { $Apply = $true }
    'y'          { $Apply = $true }
    'dry-run'    { $Dry = $true }
    'dryrun'     { $Dry = $true }
    'agents'     { $i++; $Agents = [string]$args[$i] }
    'url'        { $i++; $Url = [string]$args[$i] }
    'token-env'  { $i++; $TokenEnv = [string]$args[$i] }
    'skills-dir' { $i++; $SkillsDst = [string]$args[$i] }
    'opencode-config' { $i++; $OpenCodeConfig = [string]$args[$i] }
    'no-skills'  { $DoSkills = $false }
    'no-mcp'     { $DoMcp = $false }
    'uninstall'  { $Uninstall = $true }
    'help'       { Show-Usage; exit 0 }
    'h'          { Show-Usage; exit 0 }
    default      { Write-Host "opción desconocida: $($args[$i])"; Show-Usage; exit 2 }
  }
  $i++
}
if ($Dry) { $Apply = $false }

if ($TokenEnv -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') { Write-Host '--token-env debe ser un nombre de variable (letras, dígitos y _)'; exit 2 }
if ($Url -notmatch '^https?://') { Write-Host '--url debe empezar con http:// o https://'; exit 2 }

function Test-Have([string]$Name) { return [bool](Get-Command $Name -ErrorAction SilentlyContinue) }
function Write-Step([string]$Text) { Write-Host ''; Write-Host "== $Text ==" }

# Imprime siempre; ejecuta solo con --yes.
function Invoke-Step([string]$Desc, [string]$Exe, [string[]]$Arguments) {
  if ($Apply) {
    Write-Host "  hago: $Desc"
    # Los CLIs escriben avisos por stderr: con 'Stop' PowerShell 5.1 los trataría como error fatal.
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { & $Exe @Arguments } finally { $ErrorActionPreference = $prev }
    if ($LASTEXITCODE -ne 0) { Write-Host "  aviso: '$Exe' terminó con código $LASTEXITCODE" }
  } else {
    Write-Host "  haría: $Desc"
    Write-Host "         > $Exe $($Arguments -join ' ')"
  }
}

if (-not $Agents) {
  $found = @()
  foreach ($a in 'claude', 'codex', 'opencode') { if (Test-Have $a) { $found += $a } }
  $Agents = $found -join ','
}
$AgentList = @($Agents.Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ })
function Test-Wants([string]$Name) { return $AgentList -contains $Name }

Write-Host 'pcx · instalador para agentes'
Write-Host ("  modo:      " + $(if ($Apply) { 'APLICAR (--yes)' } else { 'plan, no se escribe nada (agrega --yes para aplicar)' }))
Write-Host ("  acción:    " + $(if ($Uninstall) { 'desinstalar' } else { 'instalar' }))
Write-Host ("  agentes:   " + $(if ($Agents) { $Agents } else { 'ninguno detectado' }))
Write-Host "  MCP:       $Url  (token en `$env:$TokenEnv; no se guarda su valor)"
Write-Host "  skills:    $SkillsSrc -> $SkillsDst"

if ($AgentList.Count -eq 0) {
  Write-Host ''
  Write-Host 'No encontré claude, codex ni opencode en el PATH. Instala alguno o usa --agents.'
  exit 1
}
if (-not (Test-Path $SkillsSrc -PathType Container)) {
  Write-Host "no existe $SkillsSrc (¿corriste el script desde el repo de pcx?)"
  exit 1
}

function Get-SkillNames {
  Get-ChildItem -Path $SkillsSrc -Directory | Where-Object { Test-Path (Join-Path $_.FullName 'SKILL.md') } | ForEach-Object { $_.Name }
}

function Install-SharedSkills {
  Write-Step "Skills en $SkillsDst (Codex y OpenCode leen esta carpeta)"
  if ($Apply -and -not (Test-Path $SkillsDst)) { New-Item -ItemType Directory -Force -Path $SkillsDst | Out-Null }
  foreach ($name in Get-SkillNames) {
    $src = Join-Path $SkillsSrc $name
    $dst = Join-Path $SkillsDst $name
    if ($Uninstall) {
      if (Test-Path $dst) {
        if ($Apply) { Write-Host "  hago: quitar la skill $name"; Remove-Item -Recurse -Force $dst }
        else { Write-Host "  haría: quitar la skill $name ($dst)" }
      } else { Write-Host "  ${name}: no estaba" }
      continue
    }
    if ((Test-Path $dst) -and -not (Test-Path (Join-Path $dst 'SKILL.md'))) {
      Write-Host "  ${name}: $dst existe y no parece una skill; no la toco"
      continue
    }
    if ($Apply) {
      Write-Host "  hago: copiar la skill $name"
      if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
      Copy-Item -Recurse -Force $src $dst
    } else {
      Write-Host "  haría: copiar la skill $name"
      Write-Host "         > Copy-Item -Recurse $src $dst"
    }
  }
}

function Install-Claude {
  Write-Step 'Claude Code'
  if (-not (Test-Have 'claude')) { Write-Host '  claude no está en el PATH; lo salto'; return }
  if ($Uninstall) {
    Invoke-Step 'desinstalar el plugin pcx' 'claude' @('plugin', 'uninstall', "pcx@$Marketplace")
    Invoke-Step "quitar el marketplace $Marketplace" 'claude' @('plugin', 'marketplace', 'remove', $Marketplace)
    return
  }
  Invoke-Step "agregar este repo como marketplace ($Marketplace)" 'claude' @('plugin', 'marketplace', 'add', $Repo)
  Invoke-Step 'instalar el plugin pcx (skills + /pcx-estado + MCP)' 'claude' @('plugin', 'install', "pcx@$Marketplace")
  Write-Host '  El plugin lee PCX_MCP_URL (por defecto http://127.0.0.1:9200/mcp) y PCX_MCP_TOKEN del entorno.'
  if ($TokenEnv -ne 'PCX_MCP_TOKEN') {
    Write-Host '  aviso: el plugin de Claude Code siempre usa PCX_MCP_TOKEN; --token-env solo aplica a Codex y OpenCode.'
  }
}

function Install-Codex {
  Write-Step 'Codex'
  if (-not (Test-Have 'codex')) { Write-Host '  codex no está en el PATH; lo salto'; return }
  if (-not $DoMcp) { return }
  if ($Uninstall) {
    Invoke-Step "quitar el servidor MCP $Server" 'codex' @('mcp', 'remove', $Server)
    return
  }
  $exists = $false
  $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try { & codex mcp get $Server *> $null; $exists = ($LASTEXITCODE -eq 0) } catch { $exists = $false } finally { $ErrorActionPreference = $prev }
  if ($exists) {
    Write-Host "  ya existe un servidor MCP «$Server» en Codex; no lo piso (quítalo con: codex mcp remove $Server)"
  } else {
    Invoke-Step "registrar el servidor MCP $Server" 'codex' @('mcp', 'add', $Server, '--url', $Url, '--bearer-token-env-var', $TokenEnv)
  }
  Write-Host "  Sugerido en config.toml de Codex, bajo [mcp_servers.$Server]: tool_timeout_sec = 120"
  Write-Host '  (ask y events_wait esperan hasta 50 s; el plazo por defecto de Codex es 60 s).'
}

function Install-OpenCode {
  Write-Step 'OpenCode'
  if (-not (Test-Have 'opencode')) { Write-Host '  opencode no está en el PATH; lo salto'; return }
  if (-not $DoMcp) { return }
  $base = if ($env:XDG_CONFIG_HOME) { $env:XDG_CONFIG_HOME } else { Join-Path $HOME '.config' }
  $dir = Join-Path $base 'opencode'
  $cfg = Join-Path $dir 'opencode.json'
  $jsonc = Join-Path $dir 'opencode.jsonc'
  if ((Test-Path $jsonc) -and -not (Test-Path $cfg)) { $cfg = $jsonc }
  if ($OpenCodeConfig) { $cfg = $OpenCodeConfig; $dir = Split-Path -Parent ([System.IO.Path]::GetFullPath($cfg)) }

  $entry = [ordered]@{
    type    = 'remote'
    url     = $Url
    headers = [ordered]@{ Authorization = "Bearer {env:$TokenEnv}" }
    oauth   = $false
  }
  $snippet = (@{ $Server = $entry } | ConvertTo-Json -Depth 10 -Compress)
  $verb = if ($Apply) { 'hago' } else { 'haría' }

  $data = New-Object PSObject
  if (Test-Path $cfg) {
    $raw = Get-Content -Raw -Encoding UTF8 $cfg
    if ($raw -and $raw.Trim()) {
      try { $data = $raw | ConvertFrom-Json }
      catch {
        Write-Host "  $cfg tiene comentarios o no es JSON estricto; no lo toco. Agrega a mano en `"mcp`":"
        Write-Host "  $snippet"
        return
      }
    }
  }
  if ($data -isnot [System.Management.Automation.PSCustomObject]) { Write-Host "  $cfg no es un objeto JSON; no lo toco."; return }

  $mcp = $data.PSObject.Properties['mcp']
  if ($mcp -and ($mcp.Value -isnot [System.Management.Automation.PSCustomObject])) { Write-Host "  la clave `"mcp`" de $cfg no es un objeto; no lo toco."; return }
  # Dos formas según la versión de OpenCode: mcp.<nombre> (1.x) o mcp.servers.<nombre> (documentación nueva).
  $nested = $false
  if ($mcp -and $mcp.Value.PSObject.Properties['servers'] -and ($mcp.Value.servers -is [System.Management.Automation.PSCustomObject])) { $nested = $true }
  $where = if ($nested) { "mcp.servers.$Server" } else { "mcp.$Server" }
  $servers = if ($nested) { $mcp.Value.servers } elseif ($mcp) { $mcp.Value } else { $null }
  $has = [bool]($servers -and $servers.PSObject.Properties[$Server])

  if ($Uninstall) {
    if (-not $has) { Write-Host "  ${cfg}: no hay $where; nada que quitar"; return }
    Write-Host "  ${verb}: quitar $where de $cfg"
    if (-not $Apply) { return }
    $servers.PSObject.Properties.Remove($Server)
  } else {
    if ($has) {
      $same = (($servers.$Server | ConvertTo-Json -Depth 10 -Compress) -eq ($entry | ConvertTo-Json -Depth 10 -Compress))
      if ($same) { Write-Host "  $cfg ya tiene $where; nada que hacer" }
      else { Write-Host "  $cfg ya tiene un $where distinto; no lo piso. Para reemplazarlo, bórralo y vuelve a correr." }
      return
    }
    Write-Host "  ${verb}: agregar $where a $cfg"
    Write-Host "         $snippet"
    if (-not $Apply) { return }
    if (-not $data.PSObject.Properties['$schema']) { $data | Add-Member -NotePropertyName '$schema' -NotePropertyValue 'https://opencode.ai/config.json' }
    if (-not $mcp) {
      $data | Add-Member -NotePropertyName 'mcp' -NotePropertyValue (New-Object PSObject)
      $servers = $data.mcp
    }
    $servers | Add-Member -NotePropertyName $Server -NotePropertyValue ([PSCustomObject]$entry)
  }

  if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
  if (Test-Path $cfg) {
    $bak = "$cfg.bak-$(Get-Date -Format 'yyyyMMddHHmmss')"
    Copy-Item $cfg $bak
    Write-Host "  respaldo: $bak"
  }
  $json = ($data | ConvertTo-Json -Depth 30) + "`n"
  [System.IO.File]::WriteAllText($cfg, $json, (New-Object System.Text.UTF8Encoding $false))
  Write-Host "  escrito: $cfg"
}

if (Test-Wants 'claude') { Install-Claude }
if ($DoSkills -and ((Test-Wants 'codex') -or (Test-Wants 'opencode'))) { Install-SharedSkills }
if (Test-Wants 'codex') { Install-Codex }
if (Test-Wants 'opencode') { Install-OpenCode }

Write-Step 'Siguiente paso'
if ($Apply) {
  Write-Host 'Listo. Define en tu entorno (no en el repo) y reinicia el agente:'
} else {
  Write-Host 'No se cambió nada. Para aplicar: .\integrations\install.ps1 --yes'
  Write-Host 'Después define en tu entorno (no en el repo):'
}
Write-Host "  `$env:PCX_MCP_URL = '$Url'"
Write-Host "  `$env:$TokenEnv = '<token>'     # nodo local: el PCX_TOKEN del nodo · gateway: el PCX_USER_TOKEN"
Write-Host 'Prueba: pídele al agente «llama scene de pcx» o, en Claude Code, /pcx:pcx-estado'
