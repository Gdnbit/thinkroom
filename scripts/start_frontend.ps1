param(
    [int]$Port = 5173,
    [string]$HostAddress = "127.0.0.1",
    [string]$PidFile = "",
    [string]$LogPath = "",
    [string]$ErrorLogPath = "",
    [string]$NodePath = "",
    [switch]$NoStopExisting,
    [int]$HealthTimeoutSeconds = 60
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$FrontendDir = Join-Path $ProjectRoot "apps\frontend"
$ViteCli = Join-Path $FrontendDir "node_modules\vite\bin\vite.js"

if (-not $PidFile) {
    $PidFile = Join-Path $ProjectRoot ".pids\frontend.pid"
}
if (-not $LogPath) {
    $LogPath = Join-Path $ProjectRoot "logs\frontend.log"
}
if (-not $ErrorLogPath) {
    $ErrorLogPath = Join-Path $ProjectRoot "logs\frontend.log.err"
}

function Resolve-ProjectOwnedPath {
    param([string]$Path)

    $candidate = if ([System.IO.Path]::IsPathRooted($Path)) {
        [System.IO.Path]::GetFullPath($Path)
    } else {
        [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $Path))
    }
    $rootPrefix = $ProjectRoot.TrimEnd("\") + "\"
    if (-not $candidate.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Frontend runtime output must stay inside the project directory: $candidate"
    }
    return $candidate
}

function Resolve-FrontendNode {
    param([string]$Requested)

    if ($Requested) {
        if (-not (Test-Path -LiteralPath $Requested)) {
            throw "Requested Node path does not exist: $Requested"
        }
        return (Resolve-Path -LiteralPath $Requested).Path
    }
    $command = Get-Command node -ErrorAction SilentlyContinue
    if (-not $command) {
        throw "Node.js was not found in PATH. Install the project Node.js runtime or pass -NodePath."
    }
    return $command.Source
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

function Test-ProjectFrontendProcess {
    param([int]$ProcessId)

    $commandLine = Get-ProcessCommandLine -ProcessId $ProcessId
    if (-not $commandLine) {
        return $false
    }
    $isFrontendRuntime = (
        $commandLine -match "vite(?:\.js)?" -or
        $commandLine -match "npm(?:\.cmd)?\s+run\s+dev"
    )
    if (-not $isFrontendRuntime) {
        return $false
    }
    $escapedProjectRoot = $ProjectRoot.Replace('\', '\\')
    $escapedFrontendDir = $FrontendDir.Replace('\', '\\')
    return (
        $commandLine.Contains($ProjectRoot) -or
        $commandLine.Contains($FrontendDir) -or
        $commandLine.Contains($escapedProjectRoot) -or
        $commandLine.Contains($escapedFrontendDir)
    )
}

function Stop-ProjectFrontendProcessTree {
    param([int]$ProcessId)

    Get-CimInstance Win32_Process -Filter "ParentProcessId=$ProcessId" -ErrorAction SilentlyContinue | ForEach-Object {
        if (Test-ProjectFrontendProcess -ProcessId $_.ProcessId) {
            Stop-ProjectFrontendProcessTree -ProcessId $_.ProcessId
        }
    }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

function Stop-PidFileFrontendProcess {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    $rawPid = [System.IO.File]::ReadAllText($Path).Trim()
    if (-not $rawPid -or -not ($rawPid -match "^\d+$")) {
        return
    }
    $pidValue = [int]$rawPid
    if (Test-ProjectFrontendProcess -ProcessId $pidValue) {
        Write-Host "Stopping previous frontend process from pid file: $pidValue" -ForegroundColor Yellow
        Stop-ProjectFrontendProcessTree -ProcessId $pidValue
    }
}

function Stop-ExistingFrontendListeners {
    param([int]$TargetPort)

    foreach ($listenerPid in @(Get-PortListenerProcessIds -TargetPort $TargetPort)) {
        if (-not (Test-ProjectFrontendProcess -ProcessId $listenerPid)) {
            $commandLine = Get-ProcessCommandLine -ProcessId $listenerPid
            throw "Port $TargetPort is already used by a non-project process (PID $listenerPid): $commandLine"
        }
        Write-Host "Stopping previous frontend listener on port $TargetPort (PID $listenerPid)" -ForegroundColor Yellow
        Stop-ProjectFrontendProcessTree -ProcessId $listenerPid
    }

    $deadline = (Get-Date).AddSeconds(10)
    while ((Get-Date) -lt $deadline) {
        if (-not @(Get-PortListenerProcessIds -TargetPort $TargetPort).Count) {
            return
        }
        Start-Sleep -Milliseconds 250
    }
    $remaining = @(Get-PortListenerProcessIds -TargetPort $TargetPort) -join ", "
    throw "Port $TargetPort is still occupied after stopping project frontend listeners: $remaining"
}

function Archive-ExistingLog {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    $archiveDir = Join-Path $ProjectRoot "logs\_archive\frontend"
    if (-not (Test-Path -LiteralPath $archiveDir)) {
        New-Item -ItemType Directory -Path $archiveDir -Force | Out-Null
    }
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
    $archivePath = Join-Path $archiveDir "$stamp-$(Split-Path -Leaf $Path)"
    Move-Item -LiteralPath $Path -Destination $archivePath
    Write-Host "Archived previous frontend log: $archivePath" -ForegroundColor DarkGray
}

function Wait-ProjectFrontendListener {
    param(
        [int]$TargetPort,
        [int]$TimeoutSeconds
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        foreach ($listenerPid in @(Get-PortListenerProcessIds -TargetPort $TargetPort)) {
            if (Test-ProjectFrontendProcess -ProcessId $listenerPid) {
                return $listenerPid
            }
        }
        Start-Sleep -Milliseconds 500
    }
    throw "No project Vite listener appeared on port $TargetPort within $TimeoutSeconds seconds."
}

function Wait-FrontendHealth {
    param(
        [string]$HostAddress,
        [int]$TargetPort,
        [int]$TimeoutSeconds
    )

    $healthUrl = "http://$HostAddress`:$TargetPort/chat"
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
    throw "Frontend did not become healthy at $healthUrl within $TimeoutSeconds seconds. Last error: $lastError"
}

$ResolvedPidFile = Resolve-ProjectOwnedPath -Path $PidFile
$ResolvedLogPath = Resolve-ProjectOwnedPath -Path $LogPath
$ResolvedErrorLogPath = Resolve-ProjectOwnedPath -Path $ErrorLogPath
$Node = Resolve-FrontendNode -Requested $NodePath

if (-not (Test-Path -LiteralPath $ViteCli)) {
    throw "Vite CLI was not found: $ViteCli. Run npm install in apps/frontend first."
}

foreach ($runtimePath in @($ResolvedPidFile, $ResolvedLogPath, $ResolvedErrorLogPath)) {
    $parent = Split-Path -Parent $runtimePath
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
}

if ($NoStopExisting) {
    $existingListeners = @(Get-PortListenerProcessIds -TargetPort $Port)
    if ($existingListeners.Count) {
        throw "Port $Port is already occupied. Re-run without -NoStopExisting to replace project frontend listeners."
    }
} else {
    Stop-PidFileFrontendProcess -Path $ResolvedPidFile
    Stop-ExistingFrontendListeners -TargetPort $Port
}

Archive-ExistingLog -Path $ResolvedLogPath
Archive-ExistingLog -Path $ResolvedErrorLogPath

$viteArgument = '"' + $ViteCli.Replace('"', '\"') + '"'
$arguments = @($viteArgument, "--host", $HostAddress, "--port", ([string]$Port), "--strictPort")
$process = Start-Process -FilePath $Node `
    -ArgumentList $arguments `
    -WorkingDirectory $FrontendDir `
    -RedirectStandardOutput $ResolvedLogPath `
    -RedirectStandardError $ResolvedErrorLogPath `
    -WindowStyle Hidden `
    -PassThru

try {
    $listenerPid = Wait-ProjectFrontendListener -TargetPort $Port -TimeoutSeconds $HealthTimeoutSeconds
    Wait-FrontendHealth -HostAddress $HostAddress -TargetPort $Port -TimeoutSeconds $HealthTimeoutSeconds
} catch {
    if (Test-ProjectFrontendProcess -ProcessId $process.Id) {
        Stop-ProjectFrontendProcessTree -ProcessId $process.Id
    }
    throw
}

[System.IO.File]::WriteAllText($ResolvedPidFile, "$listenerPid`n", [System.Text.Encoding]::ASCII)
Write-Host "Frontend started on http://$HostAddress`:$Port/chat (PID $listenerPid)" -ForegroundColor Green
