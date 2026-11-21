# sl-scanner · q15: inicio de sesión automático de la cuenta actual + runtime del playground al iniciar sesión.
# Correr en el PC, en PowerShell como Administrador, con la sesión de Roberto. Pide la contraseña de Windows
# UNA vez (Sysinternals Autologon la guarda cifrada en LSA; este script no la escribe en ningún archivo).
& {
$ErrorActionPreference = "Stop"
$IsAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $IsAdmin) { throw "Abre PowerShell como Administrador y vuelve a pegar." }
$user = $env:USERNAME; $domain = $env:COMPUTERNAME
# 1. Autologon de Sysinternals (cifra la contraseña en LSA; no queda en texto plano en el registro)
winget install --id Microsoft.Sysinternals.Autologon -e --silent --accept-package-agreements --accept-source-agreements 2>&1 | Select-String -Pattern "Successfully|already" | ForEach-Object { Write-Host "  autologon: $_" }
$exe = (Get-Command Autologon64.exe -ErrorAction SilentlyContinue).Source
if (-not $exe) { $exe = (Get-Command Autologon.exe -ErrorAction SilentlyContinue).Source }
if (-not $exe) { throw "No encontré Autologon.exe; instálalo desde https://learn.microsoft.com/sysinternals/downloads/autologon y vuelve a correr." }
$sec = Read-Host "Contraseña de Windows de $user (se guarda cifrada, no se muestra)" -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
try { $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
& $exe /accepteula $user $domain $plain | Out-Null
$plain = $null; [GC]::Collect()
Write-Host "autologon configurado para $domain\$user"
# 2. Runtime del playground al iniciar sesión (sesión interactiva: ventanas de proyector con GPU)
$py = "C:\sl-scanner\.venv\Scripts\python.exe"
$action = New-ScheduledTaskAction -Execute $py -Argument "-m playground serve --host 0.0.0.0 --port 8790 --window" -WorkingDirectory "C:\sl-scanner"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$domain\$user"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName "sl-scanner-playground" -Action $action -Trigger $trigger -Settings $settings -User "$domain\$user" -RunLevel Limited -Force | Out-Null
Write-Host "tarea 'sl-scanner-playground' creada: arranca el runtime al iniciar sesión"
# 3. firewall del runtime: 8790 solo LAN y tailnet (el Mac le habla por aquí)
Get-NetFirewallRule -Name sl-scanner-playground -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -Name sl-scanner-playground -DisplayName "playground 8790 solo LAN y tailnet" -Direction Inbound -Protocol TCP -LocalPort 8790 -Action Allow -Profile Any -RemoteAddress @("LocalSubnet","100.64.0.0/10") | Out-Null
Start-ScheduledTask -TaskName "sl-scanner-playground"
Start-Sleep 5
Write-Host ("runtime: " + (try { (Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8790/api/schema -TimeoutSec 5).StatusCode } catch { "sin respuesta todavía" }))
Write-Host "LISTO. Reinicia cuando quieras: entra solo y el playground arranca."
}
