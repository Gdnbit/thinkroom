param(
    [int]$Port = 8000,
    [string]$HostAddress = "127.0.0.1",
    [string]$PidFile = "",
    [string]$LogPath = "",
    [string]$ErrorLogPath = "",
    [string]$PythonPath = "",
    [ValidateSet("user", "test", "legacy")]
    [string]$DataProfile = "user",
    [switch]$NoStopExisting,
    [switch]$UseLocalDependencies,
    [int]$HealthTimeoutSeconds = 60
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BackendDir = Join-Path $ProjectRoot "apps\backend"
if (-not $PidFile) {
    $PidFile = Join-Path $ProjectRoot ".pids\backend.pid"
}

function Resolve-BackendPython {
    param([string]$Requested)

    if ($Requested) {
        if (-not (Test-Path -LiteralPath $Requested)) {
            throw "Requested Python path does not exist: $Requested"
        }
        return (Resolve-Path -LiteralPath $Requested).Path
    }

    $candidates = @(
        (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
        (Join-Path $BackendDir "venv\Scripts\python.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    throw "No Python executable found. Run scripts\setup_backend_venv.ps1 first or pass -PythonPath."
}

function Get-PortListenerProcessIds {
    param([int]$TargetPort)

    @(Get-NetTCPConnection -LocalPort $TargetPort -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique)
}

function Get-ProcessCommandLine {
    param([int]$ProcessId)

    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if (-not $processInfo) {
        return ""
    }
    return [string]$processInfo.CommandLine
}

function Test-ProjectBackendProcess {
    param([int]$ProcessId)

    $commandLine = Get-ProcessCommandLine -ProcessId $ProcessId
    if (-not $commandLine) {
        return $false
    }

    $isBackend = $commandLine -match "uvicorn" -and $commandLine -match "app\.main:app"
    if (-not $isBackend) {
        return $false
    }

    return $commandLine.Contains($ProjectRoot) -or $commandLine.Contains($BackendDir)
}

function Stop-PidFileBackendProcess {
    param([string]$Path)

    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) {
        return
    }

    $rawPid = [System.IO.File]::ReadAllText($Path).Trim()
    if (-not $rawPid -or -not ($rawPid -match "^\d+$")) {
        return
    }

    $pidValue = [int]$rawPid
    if (Test-ProjectBackendProcess -ProcessId $pidValue) {
        Write-Host "Stopping previous backend process from pid file: $pidValue" -ForegroundColor Yellow
        Stop-Process -Id $pidValue -Force -ErrorAction SilentlyContinue
    }
}

function Stop-ExistingBackendListeners {
    param([int]$TargetPort)

    $listenerPids = @(Get-PortListenerProcessIds -TargetPort $TargetPort)
    foreach ($listenerPid in $listenerPids) {
        if (-not (Test-ProjectBackendProcess -ProcessId $listenerPid)) {
            $commandLine = Get-ProcessCommandLine -ProcessId $listenerPid
            throw "Port $TargetPort is already used by a non-project process (PID $listenerPid): $commandLine"
        }

        Write-Host "Stopping previous backend listener on port $TargetPort (PID $listenerPid)" -ForegroundColor Yellow
        Stop-Process -Id $listenerPid -Force -ErrorAction SilentlyContinue
    }

    $deadline = (Get-Date).AddSeconds(10)
    while ((Get-Date) -lt $deadline) {
        $remaining = @(Get-PortListenerProcessIds -TargetPort $TargetPort)
        if (-not $remaining.Count) {
            return
        }
        Start-Sleep -Milliseconds 250
    }

    $stillListening = @(Get-PortListenerProcessIds -TargetPort $TargetPort) -join ", "
    throw "Port $TargetPort is still occupied after stopping previous backend listeners: $stillListening"
}

function Wait-BackendHealth {
    param(
        [string]$HostAddress,
        [int]$TargetPort,
        [int]$TimeoutSeconds
    )

    $healthUrl = "http://$HostAddress`:$TargetPort/health"
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastError = ""

    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 3
            if ($response.StatusCode -eq 200) {
                return
            }
        } catch {
            $lastError = $_.Exception.Message
        }
        Start-Sleep -Milliseconds 500
    }

    throw "Backend did not become healthy at $healthUrl within $TimeoutSeconds seconds. Last error: $lastError"
}

function Wait-ProjectBackendListener {
    param(
        [int]$TargetPort,
        [int]$TimeoutSeconds
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $listenerPids = @(Get-PortListenerProcessIds -TargetPort $TargetPort)
        foreach ($listenerPid in $listenerPids) {
            if (Test-ProjectBackendProcess -ProcessId $listenerPid) {
                return $listenerPid
            }
        }
        Start-Sleep -Milliseconds 500
    }

    throw "No project backend listener appeared on port $TargetPort within $TimeoutSeconds seconds."
}

function Convert-ToCommandLineArgument {
    param([AllowNull()][string]$Value)

    if ($null -eq $Value) {
        return '""'
    }
    return '"' + $Value.Replace('"', '\"') + '"'
}

function Resolve-ProjectRelativePath {
    param([string]$Path)

    if (-not $Path) {
        return ""
    }
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return $Path
    }
    return Join-Path $ProjectRoot $Path
}

function Archive-LogFileIfContainsNul {
    param([string]$Path)

    $resolvedPath = Resolve-ProjectRelativePath -Path $Path
    if (-not $resolvedPath -or -not (Test-Path -LiteralPath $resolvedPath)) {
        return
    }

    $bytes = [System.IO.File]::ReadAllBytes($resolvedPath)
    $containsNul = $false
    foreach ($byte in $bytes) {
        if ($byte -eq 0) {
            $containsNul = $true
            break
        }
    }
    if (-not $containsNul) {
        return
    }

    $parent = Split-Path -Parent $resolvedPath
    $archiveDir = Join-Path $parent "_archive_encoding_review"
    if (-not (Test-Path -LiteralPath $archiveDir)) {
        New-Item -ItemType Directory -Path $archiveDir -Force | Out-Null
    }
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $leaf = Split-Path -Leaf $resolvedPath
    $archivePath = Join-Path $archiveDir "$stamp-$leaf"
    Move-Item -LiteralPath $resolvedPath -Destination $archivePath
    Write-Host "Archived legacy encoded log for review: $archivePath" -ForegroundColor Yellow
}

function Set-SourceMarkerIfPresent {
    param(
        [string]$Name,
        [string]$Source
    )

    $value = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ($value) {
        [Environment]::SetEnvironmentVariable("${Name}_SOURCE", $Source, "Process")
    }
}

function Set-SourceMarker {
    param(
        [string]$Name,
        [string]$Source
    )

    [Environment]::SetEnvironmentVariable("${Name}_SOURCE", $Source, "Process")
}

function Enable-LocalDependencyEnvironment {
    $env:REDIS_ENABLED = "true"
    $env:REDIS_URL = "redis://127.0.0.1:6379/0"
    $env:RATE_LIMIT_STORAGE_URI = "redis://127.0.0.1:6379/1"
    $env:MINIO_ENDPOINT = "127.0.0.1:9000"
    $env:MINIO_BUCKET = "agent-files"
    $env:MINIO_SECURE = "false"

    Set-SourceMarker -Name "REDIS_URL" -Source "local-dependencies"
    Set-SourceMarker -Name "RATE_LIMIT_STORAGE_URI" -Source "local-dependencies"
    Set-SourceMarker -Name "MINIO_ENDPOINT" -Source "local-dependencies"
    Set-SourceMarker -Name "MINIO_BUCKET" -Source "local-dependencies"
}

function Start-BackendProcess {
    param(
        [string]$PythonExecutable,
        [string]$HostAddress,
        [int]$TargetPort,
        [string]$StdoutPath,
        [string]$StderrPath,
        [switch]$UseLocalDependencies
    )

    $resolvedStdoutPath = Resolve-ProjectRelativePath -Path $StdoutPath
    $resolvedStderrPath = Resolve-ProjectRelativePath -Path $StderrPath
    foreach ($path in @($resolvedStdoutPath, $resolvedStderrPath)) {
        if (-not $path) {
            continue
        }
        $parent = Split-Path -Parent $path
        if ($parent -and -not (Test-Path -LiteralPath $parent)) {
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
        }
    }

    $launcherPath = Join-Path $PSScriptRoot "backend_uvicorn_launcher.py"
    $launcherArgs = @(
        $launcherPath,
        "--python", $PythonExecutable,
        "--backend-dir", $BackendDir,
        "--host", $HostAddress,
        "--port", ([string]$TargetPort)
    )
    if ($resolvedStdoutPath) {
        $launcherArgs += @("--stdout", $resolvedStdoutPath)
    }
    if ($resolvedStderrPath) {
        $launcherArgs += @("--stderr", $resolvedStderrPath)
    }
    if ($UseLocalDependencies) {
        $launcherArgs += "--use-local-dependencies"
    }

    $command = (Convert-ToCommandLineArgument $PythonExecutable) + " " + (($launcherArgs | ForEach-Object { Convert-ToCommandLineArgument $_ }) -join " ")
    $shell = New-Object -ComObject WScript.Shell
    [void]$shell.Run($command, 0, $false)
    return [pscustomobject]@{ Id = "wscript-hidden" }
}

$Python = Resolve-BackendPython -Requested $PythonPath

# Compatibility launcher: credentials and truthful provenance must be supplied
# by the caller. Never import an entire secret store or invent SOPS provenance.
if (-not $env:OPENROUTER_API_KEY -and $env:OPENROUTER_MOCK -ne "true") {
    throw "Inject OPENROUTER_API_KEY for this process, or use scripts/start_backend.py --mock."
}
$env:PYTHONIOENCODING = "utf-8"
$env:APP_SCHEDULER_ENABLED = "false"
$env:DATA_PROFILE = $DataProfile

switch ($DataProfile) {
    "user" {
        $databasePath = Join-Path $ProjectRoot ".runtime\data\user\local-v0.1.db"
        if (-not (Test-Path -LiteralPath $databasePath)) {
            throw "User database not found: $databasePath. Build it with scripts\maintenance\build_local_v01_database.py first."
        }
        $env:DATABASE_URL = "sqlite:///$($databasePath.Replace('\', '/'))"
        $env:DATABASE_URL_SOURCE = "local-profile:user"
        $env:APP_ENV = "development"
    }
    "test" {
        $databaseDir = Join-Path $ProjectRoot ".runtime_test_dbs"
        if (-not (Test-Path -LiteralPath $databaseDir)) {
            New-Item -ItemType Directory -Path $databaseDir -Force | Out-Null
        }
        $databasePath = Join-Path $databaseDir "live-profile-$Port.db"
        $env:DATABASE_URL = "sqlite:///$($databasePath.Replace('\', '/'))"
        $env:DATABASE_URL_SOURCE = "local-profile:test"
        $env:APP_ENV = "test"
    }
    "legacy" {
        # Preserve caller configuration and source metadata.
    }
}

if ($UseLocalDependencies) {
    Enable-LocalDependencyEnvironment
}

if ($NoStopExisting) {
    $existingListeners = @(Get-PortListenerProcessIds -TargetPort $Port)
    if ($existingListeners.Count) {
        throw "Port $Port is already occupied. Re-run without -NoStopExisting to replace project backend listeners."
    }
} else {
    Stop-PidFileBackendProcess -Path $PidFile
    Stop-ExistingBackendListeners -TargetPort $Port
}

Archive-LogFileIfContainsNul -Path $LogPath
Archive-LogFileIfContainsNul -Path $ErrorLogPath

$proc = Start-BackendProcess `
    -PythonExecutable $Python `
    -HostAddress $HostAddress `
    -TargetPort $Port `
    -StdoutPath $LogPath `
    -StderrPath $ErrorLogPath `
    -UseLocalDependencies:$UseLocalDependencies
$listenerPid = Wait-ProjectBackendListener -TargetPort $Port -TimeoutSeconds $HealthTimeoutSeconds
Wait-BackendHealth -HostAddress $HostAddress -TargetPort $Port -TimeoutSeconds $HealthTimeoutSeconds

if ($PidFile) {
    $pidDir = Split-Path -Parent $PidFile
    if ($pidDir -and -not (Test-Path $pidDir)) {
        New-Item -ItemType Directory -Path $pidDir -Force | Out-Null
    }
    [System.IO.File]::WriteAllText($PidFile, "$($listenerPid)`n", [System.Text.Encoding]::ASCII)
}

Write-Host "Backend started with caller-provided configuration on http://$HostAddress`:$Port (profile $DataProfile, PID $listenerPid, launcher PID $($proc.Id))" -ForegroundColor Green
