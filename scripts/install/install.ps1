# Instalador del motor local de pcx para Windows (PowerShell 5.1+): pcx, pcx-renderer y pcx-capture en
# %LOCALAPPDATA%\pcx\bin y los pesos de los modelos (SAM y manos) en %LOCALAPPDATA%\pcx\models.
#
#   irm <url>/install.ps1 | iex
#   & ([scriptblock]::Create((irm <url>/install.ps1))) --yes --start    # sin preguntas y deja el motor corriendo
#   & ([scriptblock]::Create((irm <url>/install.ps1))) --check          # verifica (binario, modelos, cámara, puerto)
#   $env:PCX_VERSION = 'v0.2.0'; irm <url>/install.ps1 | iex
#   & ([scriptblock]::Create((irm <url>/install.ps1))) --dry-run
#   & ([scriptblock]::Create((irm <url>/install.ps1))) --uninstall
#
# Descarga el zip del GitHub Release, verifica su SHA-256 contra SHA256SUMS y cada modelo contra models\SHA256SUMS,
# y lo instala. Es idempotente: si ya está instalado el mismo paquete no lo vuelve a bajar. Pide confirmación antes
# de agregar la carpeta al PATH del usuario. Acepta las opciones con uno o dos guiones (-dry-run o --dry-run).
# Todo va dentro de una función y los errores usan throw: con `| iex`, un `exit` cerraría la ventana del usuario.

function Install-Pcx {
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest es muy lento con la barra de progreso en 5.1
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

    $Repo = if ($env:PCX_REPO) { $env:PCX_REPO } else { 'satelerd/platanus-hack-26' }
    $PcxHome = if ($env:PCX_HOME) { $env:PCX_HOME } else { Join-Path $env:LOCALAPPDATA 'pcx' }
    $BinDir = Join-Path $PcxHome 'bin'
    $ModelsDir = Join-Path $PcxHome 'models'
    $Version = if ($env:PCX_VERSION) { $env:PCX_VERSION } else { 'latest' }
    $BaseUrl = $env:PCX_BASE_URL
    $MotorAddr = if ($env:PCX_MOTOR_ADDR) { $env:PCX_MOTOR_ADDR } else { '127.0.0.1:9230' }
    $ConsoleUrl = if ($env:PCX_CONSOLE_URL) { $env:PCX_CONSOLE_URL } else { 'https://pcx-gateway.onrender.com/' }
    $LogFile = Join-Path $PcxHome 'logs\motor.log'
    $ErrFile = Join-Path $PcxHome 'logs\motor.err.log'
    $PidFile = Join-Path $PcxHome 'motor.pid'
    $StampFile = Join-Path $PcxHome '.paquete'
    $Dry = $false; $Uninstall = $false; $Yes = $false; $NoModifyPath = $false; $Skills = 'ask'
    $Start = $false; $Stop = $false; $Check = $false; $Force = $false

    for ($i = 0; $i -lt $args.Count; $i++) {
        switch -Regex ($args[$i]) {
            '^--?version$' { $i++; if ($i -ge $args.Count) { throw 'pcx: falta el valor de --version' }; $Version = $args[$i] }
            '^--?dry-run$' { $Dry = $true }
            '^--?uninstall$' { $Uninstall = $true }
            '^(--?yes|-y)$' { $Yes = $true }
            '^--?no-modify-path$' { $NoModifyPath = $true }
            '^--?skills$' { $Skills = 'yes' }
            '^--?no-skills$' { $Skills = 'no' }
            '^--?start$' { $Start = $true }
            '^--?stop$' { $Stop = $true }
            '^--?(check|doctor)$' { $Check = $true }
            '^--?force$' { $Force = $true }
            '^(--?help|-h)$' {
                Write-Host @'
Uso: install.ps1 [opciones]   (con irm: & ([scriptblock]::Create((irm URL/install.ps1))) [opciones])

  --version VER      versión a instalar (p. ej. v0.2.0; por defecto la última)
  --yes              responde que sí a las preguntas (PATH y skills)
  --start            al terminar, levanta el motor (pcx up) y muestra la URL para emparejar la consola
  --check            no instala: verifica la instalación (binario, modelos, cámara, puerto del motor)
  --stop             detiene el motor que levantó --start
  --force            reinstala aunque ya esté instalado el mismo paquete
  --dry-run          detecta la plataforma, descarga y verifica el paquete, y muestra lo que haría sin instalar
  --uninstall        quita bin, models y su entrada del PATH (deja pcx.toml y los datos)
  --no-modify-path   no toca el PATH del usuario
  --skills / --no-skills  instala (o no) las skills y el MCP para agentes, sin preguntar

Variables: PCX_HOME, PCX_VERSION, PCX_REPO (dueño/repo en GitHub), PCX_BASE_URL (carpeta con un release),
           PCX_MOTOR_ADDR (por defecto 127.0.0.1:9230), PCX_CONSOLE_URL (consola que se empareja con el motor).
'@
                return
            }
            default { throw "pcx: opción desconocida: $($args[$i])" }
        }
    }

    function Say([string]$m) { Write-Host "pcx: $m" }
    function Plan([string]$m) { Write-Host "  [dry-run] $m" }
    function Confirm-Pcx([string]$q) {
        if ($Yes) { return $true }
        if (-not [Environment]::UserInteractive) { return $false }
        try { $a = Read-Host "pcx: $q [s/N]" } catch { return $false }
        return $a -match '^(s|si|sí|y|yes)$'
    }
    function Get-AssetUrl([string]$file) {
        if ($BaseUrl) { return "$($BaseUrl.TrimEnd('/'))/$file" }
        if ($Version -eq 'latest') { return "https://github.com/$Repo/releases/latest/download/$file" }
        return "https://github.com/$Repo/releases/download/$Version/$file"
    }
    function Get-SumEntry([string]$file, [string]$sums) {
        foreach ($line in (Get-Content $sums)) {
            $parts = $line -split '\s+', 2
            if ($parts.Count -eq 2 -and $parts[1].TrimStart('*') -eq $file) { return $parts[0].ToLower() }
        }
        return $null
    }
    function Get-Verified([string]$file, [string]$dir, [string]$sums) {
        $url = Get-AssetUrl $file
        $dest = Join-Path $dir $file
        $want = Get-SumEntry $file $sums
        if (-not $want) { throw "pcx: $file no aparece en SHA256SUMS" }
        try { Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $dest } catch { throw "pcx: no se pudo descargar $url" }
        $got = (Get-FileHash -Algorithm SHA256 -Path $dest).Hash.ToLower()
        if ($want -ne $got) { throw "pcx: checksum de $file no coincide (esperado $want, obtenido $got)" }
        Say "sha256 ok: $file"
        return $dest
    }
    # Verifica cada archivo listado en $dir\SHA256SUMS. Imprime los que fallan.
    function Test-Sums([string]$dir, [switch]$Quiet) {
        $sums = Join-Path $dir 'SHA256SUMS'
        if (-not (Test-Path $sums)) { return $false }
        $ok = $true
        foreach ($line in (Get-Content $sums)) {
            $parts = $line.Trim() -split '\s+', 2
            if ($parts.Count -ne 2) { continue }
            $name = $parts[1].TrimStart('*')
            if ($name -match '(^[\\/])|(\.\.)|:') { if (-not $Quiet) { Say "nombre inválido en ${sums}: $name" }; $ok = $false; continue }
            $p = Join-Path $dir ($name -replace '/', '\')
            if (-not (Test-Path $p)) { if (-not $Quiet) { Say "falta $p" }; $ok = $false; continue }
            if ((Get-FileHash -Algorithm SHA256 -Path $p).Hash.ToLower() -ne $parts[0].ToLower()) {
                if (-not $Quiet) { Say "sha256 de $p no coincide" }; $ok = $false
            }
        }
        return $ok
    }
    function Get-SumsCount([string]$dir) { @(Get-Content (Join-Path $dir 'SHA256SUMS') | Where-Object { $_.Trim() }).Count }

    # PATH del usuario leído y escrito en crudo (REG_EXPAND_SZ) para no expandir %VARIABLES% ya guardadas.
    $EnvKey = 'HKCU:\Environment'
    function Get-UserPath { (Get-Item $EnvKey).GetValue('Path', '', 'DoNotExpandEnvironmentNames') }
    function Set-UserPath([string]$value) {
        Set-ItemProperty -Path $EnvKey -Name 'Path' -Value $value -Type ExpandString
        # Avisa a las demás ventanas (WM_SETTINGCHANGE): .NET lo hace al escribir cualquier variable de usuario.
        [Environment]::SetEnvironmentVariable('PCX_INSTALL_TMP', '1', 'User')
        [Environment]::SetEnvironmentVariable('PCX_INSTALL_TMP', $null, 'User')
    }
    function Test-InPath([string]$p) {
        (Get-UserPath) -split ';' | Where-Object { $_.TrimEnd('\') -ieq $p.TrimEnd('\') }
    }

    # ---------- modelos y configuración ----------
    function Install-Models([string]$pkg) {
        $src = Join-Path $pkg 'models'
        if (-not (Test-Path (Join-Path $src 'SHA256SUMS'))) {
            Say 'aviso: este paquete no trae modelos; el motor los bajará la primera vez que los necesite'
            return
        }
        if (-not (Test-Sums $src)) { throw 'pcx: los modelos del paquete no pasan la verificación sha256' }
        New-Item -ItemType Directory -Force -Path $ModelsDir | Out-Null
        foreach ($line in (Get-Content (Join-Path $src 'SHA256SUMS'))) {
            $parts = $line.Trim() -split '\s+', 2
            if ($parts.Count -ne 2) { continue }
            $rel = $parts[1].TrimStart('*') -replace '/', '\'
            $dst = Join-Path $ModelsDir $rel
            if ((Test-Path $dst) -and ((Get-FileHash -Algorithm SHA256 -Path $dst).Hash.ToLower() -eq $parts[0].ToLower())) { continue }
            New-Item -ItemType Directory -Force -Path (Split-Path $dst) | Out-Null
            try { Copy-Item -Force (Join-Path $src $rel) $dst }
            catch { throw "pcx: no se pudo copiar $rel (¿el motor está corriendo? detenlo con --stop): $_" }
        }
        foreach ($f in 'manifest.json', 'SHA256SUMS') {
            if (Test-Path (Join-Path $src $f)) { Copy-Item -Force (Join-Path $src $f) (Join-Path $ModelsDir $f) }
        }
        if (-not (Test-Sums $ModelsDir)) { throw "pcx: los modelos instalados en $ModelsDir no pasan la verificación sha256" }
        Say "modelos verificados en $ModelsDir ($(Get-SumsCount $ModelsDir) archivos, sha256 ok)"
    }

    # PCX_MODELS_DIR en [env] de pcx.toml (para los procesos de `pcx up`), solo si falta. Cadena literal de TOML.
    # UTF-8 sin BOM: [IO.File] y no Set-Content, que en 5.1 escribe BOM.
    function Set-ModelsInConfig {
        $cfg = Join-Path $PcxHome 'pcx.toml'
        if (-not (Test-Path $cfg)) { return }
        $text = [IO.File]::ReadAllText($cfg)
        if ($text -match '(?m)^\s*PCX_MODELS_DIR\s*=') { return }
        if ($ModelsDir.Contains("'")) { Say "aviso: la ruta de modelos tiene comillas; agrega PCX_MODELS_DIR a mano en $cfg"; return }
        $line = "PCX_MODELS_DIR = '$ModelsDir'"
        $m = [regex]::Match($text, '(?m)^[ \t]*\[env\][ \t]*\r?$')
        if ($m.Success) { $text = $text.Insert($m.Index + $m.Length, "`n$line") }
        else { $text = $text.TrimEnd() + "`n`n[env]`n$line`n" }
        [IO.File]::WriteAllText($cfg, $text)
        Say "PCX_MODELS_DIR agregado a $cfg"
    }

    # ---------- motor ----------
    $MotorPort = [int]($MotorAddr.Split(':')[-1])
    function Test-MotorUp {
        try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri "http://$MotorAddr/v1/health"; return $r.StatusCode -eq 200 }
        catch { return $false }
    }
    function Test-PortBusy {
        $c = New-Object Net.Sockets.TcpClient
        try { $c.Connect('127.0.0.1', $MotorPort); return $true } catch { return $false } finally { $c.Close() }
    }
    # Proceso de `pcx up` lanzado por --start, solo si sigue vivo y es pcx (un pid reciclado no se toca).
    function Get-MotorProcess {
        if (-not (Test-Path $PidFile)) { return $null }
        $id = 0
        if (-not [int]::TryParse((Get-Content -Raw $PidFile).Trim(), [ref]$id)) { return $null }
        $p = Get-Process -Id $id -ErrorAction SilentlyContinue
        if ($p -and $p.ProcessName -like 'pcx*') { return $p }
        return $null
    }
    function Get-TokenFile {
        foreach ($f in (Join-Path $PcxHome 'motor.token'), (Join-Path $HOME '.pcx\motor.token')) {
            if ((Test-Path $f) -and (Get-Item $f).Length -gt 0) { return $f }
        }
        return $null
    }
    function Get-PairingUrl {
        $f = Get-TokenFile
        if ($f) { return "$($ConsoleUrl)?motor=ws://$MotorAddr&motor_token=$((Get-Content -Raw $f).Trim())" }
        foreach ($log in $LogFile, $ErrFile) {
            if (Test-Path $log) {
                $m = Select-String -Path $log -Pattern 'https://\S*motor_token=\S*' -AllMatches | Select-Object -Last 1
                if ($m) { return $m.Matches[-1].Value }
            }
        }
        return $null
    }
    function Stop-Motor {
        $p = Get-MotorProcess
        if (-not $p) { Remove-Item -Force $PidFile -ErrorAction SilentlyContinue; Say 'no hay un motor lanzado por este instalador'; return }
        Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        Remove-Item -Force $PidFile -ErrorAction SilentlyContinue
        Say "motor detenido (pid $($p.Id))"
    }
    function Start-Motor {
        $pcx = Join-Path $BinDir 'pcx.exe'
        $cfg = Join-Path $PcxHome 'pcx.toml'
        if (-not (Test-Path $pcx)) { throw "pcx: pcx no está instalado en $BinDir" }
        if (-not (Test-Path $cfg)) { throw "pcx: falta $cfg" }
        if (Test-MotorUp) { Say "el motor ya responde en $MotorAddr" }
        else {
            $p = Get-MotorProcess
            if ($p) { Say "pcx up ya corre (pid $($p.Id)); espero a que el motor responda" }
            else {
                if (Test-PortBusy) { throw "pcx: el puerto $MotorPort está ocupado por otro programa: ciérralo y vuelve a correr con --start" }
                New-Item -ItemType Directory -Force -Path (Split-Path $LogFile) | Out-Null
                $env:PCX_MODELS_DIR = $ModelsDir
                if (-not (($env:Path -split ';') -contains $BinDir)) { $env:Path = "$BinDir;$env:Path" }
                # Consola propia y oculta: sigue viva cuando se cierra la terminal o el agente que corrió esto.
                $p = Start-Process -FilePath $pcx -ArgumentList @('up', '--config', "`"$cfg`"") -WorkingDirectory $PcxHome `
                    -WindowStyle Hidden -RedirectStandardOutput $LogFile -RedirectStandardError $ErrFile -PassThru
                Set-Content -Path $PidFile -Value $p.Id -Encoding ASCII
                Say "arranqué pcx up (pid $($p.Id), log en $ErrFile)"
                Start-Sleep -Seconds 1
            }
            $n = 0
            while (-not (Test-MotorUp)) {
                if (-not (Get-MotorProcess)) {
                    Say "pcx up terminó antes de levantar el motor. Últimas líneas de ${ErrFile}:"
                    if (Test-Path $ErrFile) { Get-Content $ErrFile -Tail 20 | Write-Host }
                    return $false
                }
                if ($n -ge 60) {
                    Say "el motor no respondió en http://$MotorAddr/v1/health tras 60 s (¿esta versión trae el motor?)."
                    Say "pcx up sigue corriendo; revisa $ErrFile o detenlo con --stop"
                    return $false
                }
                Start-Sleep -Seconds 1; $n++
            }
            Say "el motor responde en $MotorAddr"
        }
        $n = 0
        while (-not (Get-TokenFile) -and $n -lt 10) { Start-Sleep -Seconds 1; $n++ }
        $url = Get-PairingUrl
        if (-not $url) { Say 'no encontré motor.token ni la URL en el log'; return $false }
        Write-Host ''
        Write-Host 'Motor local listo. Abre esta URL en Chrome o Edge, en este mismo computador, para emparejar la consola:'
        Write-Host ''
        Write-Host "  $url"
        Write-Host ''
        return $true
    }

    # ---------- verificación (pcx doctor si existe; si no, chequeo mínimo) ----------
    # Los comandos nativos corren con ErrorActionPreference=Continue: en 5.1, su stderr con Stop lanza una excepción.
    function Invoke-Pcx([string[]]$a) {
        $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
        try { $out = & (Join-Path $BinDir 'pcx.exe') @a 2>$null; return @{ code = $LASTEXITCODE; out = @($out) } }
        catch { return @{ code = 1; out = @() } }
        finally { $ErrorActionPreference = $old }
    }
    function Invoke-Doctor {
        $fails = 0
        if (-not (Test-Path (Join-Path $BinDir 'pcx.exe'))) { Say "[falla] no está $BinDir\pcx.exe (instala primero)"; return $false }
        $v = Invoke-Pcx @('--version')
        if ($v.code -eq 0) { Say "[ok] binario: $($v.out -join ' ')" } else { Say '[falla] binario: pcx.exe no corre'; return $false }
        $env:PCX_MODELS_DIR = $ModelsDir
        if ((Invoke-Pcx @('doctor', '--help')).code -eq 0) {
            Say 'uso pcx doctor'
            $d = Invoke-Pcx @('doctor')
            $d.out | Write-Host
            return $d.code -eq 0
        }
        if (Test-Sums $ModelsDir) { Say "[ok] modelos: $(Get-SumsCount $ModelsDir) archivos con sha256 correcto en $ModelsDir" }
        else { Say "[falla] modelos: faltan o no calzan en $ModelsDir (reinstala con --force)"; $fails++ }
        $cams = @((Invoke-Pcx @('cameras')).out | Where-Object { "$_".Trim() }).Count
        if ($cams -gt 0) { Say "[ok] cámara: $cams detectada(s) (pcx cameras)" }
        else { Say '[aviso] cámara: no se detectó ninguna; el motor pedirá cuadros a la consola' }
        if (Test-MotorUp) { Say "[ok] motor: responde en http://$MotorAddr/v1/health" }
        elseif (Test-PortBusy) { Say "[falla] puerto ${MotorPort}: ocupado por otro programa (el motor no podrá escuchar)"; $fails++ }
        else { Say "[ok] puerto ${MotorPort}: libre (motor detenido; arráncalo con --start)" }
        return $fails -eq 0
    }

    $AgentesDir = Join-Path $PcxHome 'agentes'
    $AgentesScript = Join-Path $AgentesDir 'integrations\install.ps1'

    # ---------- solo verificar o detener ----------
    if ($Check) {
        if (-not (Invoke-Doctor)) { throw 'pcx: la verificación encontró problemas (arriba)' }
        return
    }
    if ($Stop -and -not $Uninstall) { Stop-Motor; return }

    # ---------- desinstalar ----------
    if ($Uninstall) {
        if ($Dry) { Plan "detendría el motor y borraría $BinDir y $ModelsDir" }
        else {
            Stop-Motor
            foreach ($d in $BinDir, $ModelsDir, $StampFile, (Join-Path $PcxHome 'pcx.toml.dist')) {
                if (Test-Path $d) { Remove-Item -Recurse -Force $d }
            }
            [Environment]::SetEnvironmentVariable('PCX_MODELS_DIR', $null, 'User')
        }
        if (Test-InPath $BinDir) {
            if ($Dry) { Plan "quitaría $BinDir del PATH del usuario" }
            else {
                $rest = ((Get-UserPath) -split ';' | Where-Object { $_ -and ($_.TrimEnd('\') -ine $BinDir.TrimEnd('\')) }) -join ';'
                Set-UserPath $rest
                Say "quité $BinDir del PATH del usuario"
            }
        }
        if (Test-Path $AgentesScript) {
            if ($Dry) { Plan 'preguntaría si quitar las skills y el MCP (integrations\install.ps1 --uninstall --yes)' }
            elseif (Confirm-Pcx '¿Quito también las skills y el MCP de pcx de tus agentes?') {
                & powershell -NoProfile -ExecutionPolicy Bypass -File $AgentesScript --uninstall --yes
                Remove-Item -Recurse -Force $AgentesDir
            }
        }
        if ($Dry) { Say 'dry-run: no se borró nada'; return }
        Say "pcx desinstalado. Se conservan $PcxHome\pcx.toml y los datos; bórralos con: Remove-Item -Recurse '$PcxHome'"
        return
    }

    # ---------- plataforma ----------
    $arch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    switch ($arch) {
        'AMD64' { $Target = 'x86_64-pc-windows-msvc' }
        'ARM64' { $Target = 'x86_64-pc-windows-msvc'; Say 'Windows ARM64: se instala el binario x64 (corre emulado)' }
        default { throw "pcx: arquitectura no soportada: $arch" }
    }
    $Archive = "pcx-$Target.zip"
    Say "plataforma: $Target · versión: $Version"
    Say "origen: $(Get-AssetUrl $Archive)"

    $Tmp = Join-Path ([IO.Path]::GetTempPath()) ("pcx-" + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $Tmp | Out-Null
    try {
        $Sums = Join-Path $Tmp 'SHA256SUMS'
        try { Invoke-WebRequest -UseBasicParsing -Uri (Get-AssetUrl 'SHA256SUMS') -OutFile $Sums }
        catch { throw "pcx: no se pudo descargar $(Get-AssetUrl 'SHA256SUMS') (¿existe esa versión?)" }
        $PkgSha = Get-SumEntry $Archive $Sums
        if (-not $PkgSha) { throw "pcx: esta versión no trae $Archive (no aparece en SHA256SUMS); prueba otra con --version" }

        # Idempotente: mismo paquete ya instalado y modelos intactos → no se vuelve a bajar.
        $Current = $false
        if (-not $Force -and (Test-Path (Join-Path $BinDir 'pcx.exe')) -and (Test-Path $StampFile) -and
            ((Get-Content -Raw $StampFile).Trim() -eq "$Archive $PkgSha") -and (Test-Sums $ModelsDir -Quiet)) {
            $Current = $true
            Say "ya está instalado este paquete ($Archive, sha256 ok); no lo vuelvo a bajar (--force para reinstalar)"
        }

        if (-not $Current) {
            $zip = Get-Verified $Archive $Tmp $Sums
            Expand-Archive -Path $zip -DestinationPath $Tmp -Force
            $Pkg = Join-Path $Tmp "pcx-$Target"
            if (-not (Test-Path (Join-Path $Pkg 'bin\pcx.exe'))) { throw 'pcx: el paquete no trae bin\pcx.exe' }
            $files = Get-ChildItem (Join-Path $Pkg 'bin')

            if ($Dry) {
                Say 'dry-run: el paquete está verificado; no se instaló nada'
                Plan "copiaría a ${BinDir}: $(($files | ForEach-Object Name) -join ' ')"
                $pm = Join-Path $Pkg 'models'
                if (Test-Path (Join-Path $pm 'SHA256SUMS')) {
                    if (-not (Test-Sums $pm)) { throw 'pcx: los modelos del paquete no pasan la verificación sha256' }
                    Plan "copiaría $(Get-SumsCount $pm) archivos de modelos verificados a $ModelsDir y fijaría PCX_MODELS_DIR"
                }
                else { Plan 'el paquete no trae modelos' }
                if (Test-Path "$PcxHome\pcx.toml") { Plan "mantendría $PcxHome\pcx.toml (ya existe) y le agregaría PCX_MODELS_DIR si falta" }
                else { Plan "copiaría pcx.toml a $PcxHome\pcx.toml con PCX_MODELS_DIR" }
                if (-not $NoModifyPath -and -not (Test-InPath $BinDir)) { Plan "preguntaría si agregar $BinDir al PATH del usuario" }
                if ($Skills -ne 'no') { Plan "ofrecería instalar las skills y el MCP (pcx-integrations.tar.gz -> $AgentesDir)" }
                if ($Start) { Plan 'levantaría el motor (pcx up) y mostraría la URL de emparejamiento' }
                return
            }

            New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
            foreach ($f in $files) {
                $dest = Join-Path $BinDir $f.Name
                # Un .exe en uso no se puede sobrescribir pero sí renombrar: el viejo queda como .old.
                if (Test-Path $dest) {
                    Remove-Item -Force "$dest.old" -ErrorAction SilentlyContinue
                    Rename-Item -Path $dest -NewName "$($f.Name).old" -ErrorAction SilentlyContinue
                }
                Copy-Item -Force $f.FullName $dest
            }
            Get-ChildItem $BinDir -Filter '*.old' | Remove-Item -Force -ErrorAction SilentlyContinue
            Install-Models $Pkg
            if (-not (Test-Path "$PcxHome\pcx.toml")) { Copy-Item (Join-Path $Pkg 'pcx.toml') "$PcxHome\pcx.toml" }
            # Copia de referencia del pcx.toml de esta versión: el tuyo no se pisa, compáralo si el motor no arranca.
            Copy-Item -Force (Join-Path $Pkg 'pcx.toml') "$PcxHome\pcx.toml.dist"
            Copy-Item -Force (Join-Path $Pkg 'VERSION') "$PcxHome\VERSION" -ErrorAction SilentlyContinue
            Set-Content -Path $StampFile -Value "$Archive $PkgSha" -Encoding ASCII
            Say "instalado en $PcxHome"
        }
        elseif ($Dry) { Say 'dry-run: nada que hacer'; return }

        Set-ModelsInConfig
        if ([Environment]::GetEnvironmentVariable('PCX_MODELS_DIR', 'User') -ne $ModelsDir) {
            [Environment]::SetEnvironmentVariable('PCX_MODELS_DIR', $ModelsDir, 'User')
            Say "PCX_MODELS_DIR = $ModelsDir (variable de usuario)"
        }
        $env:PCX_MODELS_DIR = $ModelsDir

        # ---------- PATH ----------
        if (Test-InPath $BinDir) { Say "$BinDir ya está en el PATH" }
        elseif ($NoModifyPath) { Say "agrega $BinDir al PATH para usar pcx" }
        elseif (Confirm-Pcx "¿Agrego $BinDir al PATH del usuario?") {
            $cur = Get-UserPath
            Set-UserPath ($(if ($cur) { "$BinDir;$cur" } else { $BinDir }))
            $env:Path = "$BinDir;$env:Path"
            Say 'PATH actualizado (las terminales nuevas ya lo ven)'
        }
        else { Say "no toqué el PATH. Para usar pcx en esta sesión: `$env:Path = '$BinDir;' + `$env:Path" }

        # ---------- skills y MCP para agentes (integrations\install.ps1 del mismo release) ----------
        $hasIntegrations = (Get-Content $Sums) -match '\s\*?pcx-integrations\.tar\.gz$'
        if ($Skills -ne 'no' -and $hasIntegrations) {
            if ($Skills -eq 'yes' -or (Confirm-Pcx '¿Instalo también las skills y el MCP de pcx para Claude Code, Codex y OpenCode?')) {
                $tgz = Get-Verified 'pcx-integrations.tar.gz' $Tmp $Sums
                if (Test-Path $AgentesDir) { Remove-Item -Recurse -Force $AgentesDir }
                New-Item -ItemType Directory -Path $AgentesDir | Out-Null
                tar -xzf $tgz -C $AgentesDir
                & powershell -NoProfile -ExecutionPolicy Bypass -File $AgentesScript --yes
            }
            else { Say "luego puedes correr: $AgentesScript" }
        }

        Say 'verificación:'
        if (-not (Invoke-Doctor)) { Say 'la verificación encontró problemas (arriba); detalles en docs/SETUP-AGENTE.md' }

        if ($Start) {
            if (-not (Start-Motor)) { throw 'pcx: el motor no quedó corriendo (arriba el detalle)' }
        }
        else {
            Write-Host ''
            Write-Host 'Listo. Para levantar el motor y emparejar la consola, vuelve a correr este instalador con --start'
            Write-Host "(no baja nada si ya está instalado), o en una terminal nueva: pcx up --config `"$PcxHome\pcx.toml`""
        }
    }
    finally {
        Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue
    }
}

Install-Pcx @args
