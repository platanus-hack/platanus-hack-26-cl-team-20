<#
.SYNOPSIS
  Deja el PC Windows listo para ser el motor del escáner SL, controlado por SSH desde el Mac.

.DESCRIPTION
  Correr UNA vez en PowerShell como Administrador:
    Set-ExecutionPolicy -Scope Process Bypass -Force
    .\setup.ps1 -PublicKey "ssh-ed25519 AAAA... max@mac"

  Hace, en orden (cada paso se puede saltar con -Skip*):
   1. OpenSSH Server: instala, arranca, firewall SOLO para la tailnet (100.64.0.0/10) y la red local,
      PowerShell como shell por defecto, autoriza la llave pública del Mac (administrators_authorized_keys
      con ACL correcta) y, si vino la llave, apaga el login por contraseña (PasswordAuthentication no).
   2. Herramientas: Git, uv (Python), Tailscale, vía winget.
   3. Carpeta C:\sl-scanner con venv Python 3.12 y dependencias (GPU: torch CUDA 12.x).
   4. Energía: sin suspensión ni apagado de pantalla (un proyector dormido rompe la captura).
   5. Imprime el chequeo final (windows\doctor.py) y la IP de Tailscale.

  No instala drivers NVIDIA ni DaVinci/Osmo: eso lo hace Max desde los instaladores oficiales.
#>
param(
  [string]$PublicKey = "",
  [string]$Root = "C:\sl-scanner",
  [switch]$SkipSsh, [switch]$SkipTools, [switch]$SkipPython, [switch]$SkipPower, [switch]$Cpu
)
$ErrorActionPreference = "Stop"
function Step($m) { Write-Host "`n== $m" -ForegroundColor Cyan }

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  throw "Ábrelo como Administrador (clic derecho en PowerShell > Ejecutar como administrador)."
}

# ---------------------------------------------------------------- 1. SSH
if (-not $SkipSsh) {
  Step "OpenSSH Server"
  $cap = Get-WindowsCapability -Online | Where-Object Name -like "OpenSSH.Server*"
  if ($cap.State -ne "Installed") { Add-WindowsCapability -Online -Name $cap.Name | Out-Null }
  Set-Service sshd -StartupType Automatic
  Start-Service sshd
  # puerto 22 solo desde la tailnet (100.64.0.0/10) y la red local; la regla abierta que crea Windows se deshabilita
  Get-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -ErrorAction SilentlyContinue | Disable-NetFirewallRule
  if (Get-NetFirewallRule -Name "sl-scanner-sshd-tailnet" -ErrorAction SilentlyContinue) { Remove-NetFirewallRule -Name "sl-scanner-sshd-tailnet" }
  New-NetFirewallRule -Name "sl-scanner-sshd-tailnet" -DisplayName "sshd solo tailnet y red local" -Enabled True `
    -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow -Profile Any -RemoteAddress @("100.64.0.0/10", "LocalSubnet") | Out-Null
  # PowerShell como shell por defecto (los comandos remotos se escriben para PowerShell)
  $pwsh = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
  if (-not $pwsh) { $pwsh = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" }
  New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name DefaultShell -Value $pwsh -PropertyType String -Force | Out-Null

  if ($PublicKey) {
    $authFile = "$env:ProgramData\ssh\administrators_authorized_keys"
    $existing = if (Test-Path $authFile) { Get-Content $authFile } else { @() }
    if ($existing -notcontains $PublicKey) { Add-Content -Path $authFile -Value $PublicKey }
    # ACL exigida por sshd: solo SYSTEM y Administradores
    icacls $authFile /inheritance:r /grant "SYSTEM:F" /grant "BUILTIN\Administrators:F" | Out-Null
    Write-Host "llave autorizada en $authFile"
    # con llave autorizada, sin login por contraseña
    $cfg = "$env:ProgramData\ssh\sshd_config"
    # va ANTES del primer bloque 'Match' (sshd_config termina con 'Match Group administrators';
    # lo que se agrega después solo aplicaría a ese grupo)
    $lines = @(Get-Content $cfg | Where-Object { $_ -notmatch '^\s*#?\s*PasswordAuthentication\s' })
    $out = New-Object System.Collections.Generic.List[string]
    $inserted = $false
    foreach ($l in $lines) {
      if (-not $inserted -and $l -match '^\s*Match\s') { $out.Add("PasswordAuthentication no"); $inserted = $true }
      $out.Add($l)
    }
    if (-not $inserted) { $out.Insert(0, "PasswordAuthentication no") }
    Set-Content -Path $cfg -Value $out
    Write-Host "PasswordAuthentication no en sshd_config"
  } else {
    Write-Warning "sin -PublicKey: agrega la llave del Mac a $env:ProgramData\ssh\administrators_authorized_keys"
  }
  Restart-Service sshd
}

# ---------------------------------------------------------------- 2. herramientas
if (-not $SkipTools) {
  Step "Git, uv, Tailscale (winget)"
  foreach ($id in @("Git.Git", "astral-sh.uv", "Tailscale.Tailscale")) {
    winget install --id $id -e --silent --accept-package-agreements --accept-source-agreements 2>&1 | Select-String -Pattern "Successfully|already installed|No newer" | ForEach-Object { Write-Host "  ${id}: $_" }
  }
  $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}

# ---------------------------------------------------------------- 3. python
if (-not $SkipPython) {
  Step "Python en $Root"
  New-Item -ItemType Directory -Force -Path $Root, "$Root\captures", "$Root\reports" | Out-Null
  Push-Location $Root
  uv python install 3.12
  if (-not (Test-Path ".venv")) { uv venv --python 3.12 .venv }
  $req = Join-Path $PSScriptRoot "requirements-gpu.txt"
  if ($Cpu) { $req = Join-Path $PSScriptRoot "requirements-cpu.txt" }
  if (Test-Path $req) {
    uv pip install --python .venv\Scripts\python.exe -r $req
  } else {
    Write-Warning "no encontré $req (copia la carpeta windows\ junto al script); instalo lo mínimo"
    uv pip install --python .venv\Scripts\python.exe numpy opencv-python scipy pyserial
  }
  Pop-Location
}

# ---------------------------------------------------------------- 4. energía
if (-not $SkipPower) {
  Step "Energía: sin suspensión"
  powercfg /change standby-timeout-ac 0
  powercfg /change monitor-timeout-ac 0
  powercfg /change hibernate-timeout-ac 0
}

# ---------------------------------------------------------------- 5. chequeo
Step "Chequeo"
$doctor = Join-Path $PSScriptRoot "doctor.py"
if (Test-Path $doctor) { & "$Root\.venv\Scripts\python.exe" $doctor } else { Write-Warning "falta doctor.py" }
$ts = "C:\Program Files\Tailscale\tailscale.exe"
if (Test-Path $ts) {
  Write-Host "`nTailscale: si aún no está en la tailnet de SmartUp, corre:  & '$ts' up"
  & $ts ip -4 2>$null | ForEach-Object { Write-Host "IP tailnet: $_" }
}
Write-Host "`nListo. Desde el Mac:  .venv/bin/python -m slscan remote doctor --host <nombre-tailscale>" -ForegroundColor Green
