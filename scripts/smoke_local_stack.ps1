[CmdletBinding()]
param(
    [int]$BackendPort = 8000,
    [switch]$SkipInstall,
    [switch]$SkipFrontendBuild,
    [switch]$KeepBackend
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$BackendDir = Join-Path $ProjectRoot "apps\backend"
$FrontendDir = Join-Path $ProjectRoot "apps\frontend"
$RuntimeLogDir = Join-Path $ProjectRoot ".runtime_logs"
$ReportPath = Join-Path $ProjectRoot "docs\verification\local-stack-smoke-latest.json"
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
New-Item -ItemType Directory -Path $RuntimeLogDir -Force | Out-Null
New-Item -ItemType Directory -Path (Split-Path -Parent $ReportPath) -Force | Out-Null

function Test-PortOpen {
    param([int]$Port)
    try {
        $tcp = New-Object Net.Sockets.TcpClient
        $tcp.Connect("127.0.0.1", $Port)
        $tcp.Close()
        return $true
    } catch {
        return $false
    }
}

function Wait-HttpOk {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 60
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                return $response
            }
        } catch {}
        Start-Sleep -Seconds 1
    }
    throw "Timed out waiting for $Url"
}

$startedBackend = $false
$backendProcess = $null
$result = [ordered]@{
    timestamp = (Get-Date).ToUniversalTime().ToString("o")
    backend_port = $BackendPort
    checks = [ordered]@{}
}

try {
    if (-not $SkipInstall) {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $ProjectRoot "scripts\setup_backend_venv.ps1") -Dev
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    }

    if (-not (Test-Path -LiteralPath $VenvPython)) {
        throw "Project venv Python not found: $VenvPython"
    }

    & $VenvPython -m pip check
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
    $result.checks.pip_check = "passed"

    if (-not (Test-PortOpen -Port $BackendPort)) {
        $backendLog = Join-Path $RuntimeLogDir "smoke_backend.log"
        $backendErr = Join-Path $RuntimeLogDir "smoke_backend.log.err"

        $env:OPENROUTER_API_KEY = ""
        $env:OPENROUTER_MOCK = "true"
        $env:APP_SCHEDULER_ENABLED = "false"
        $env:PYTHONIOENCODING = "utf-8"

        $backendProcess = Start-Process -FilePath $VenvPython `
            -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$BackendPort") `
            -WorkingDirectory $BackendDir `
            -RedirectStandardOutput $backendLog `
            -RedirectStandardError $backendErr `
            -WindowStyle Hidden `
            -PassThru
        $startedBackend = $true
    }

    $live = Wait-HttpOk -Url "http://127.0.0.1:$BackendPort/health/live" -TimeoutSeconds 90
    $ready = Wait-HttpOk -Url "http://127.0.0.1:$BackendPort/health/ready" -TimeoutSeconds 90
    $result.checks.backend_live = [ordered]@{ status_code = $live.StatusCode }
    $result.checks.backend_ready = [ordered]@{ status_code = $ready.StatusCode; body = $ready.Content }

    if (-not $SkipFrontendBuild) {
        Push-Location $FrontendDir
        try {
            # Runs: npm run build
            & npm.cmd run build
            if ($LASTEXITCODE -ne 0) {
                exit $LASTEXITCODE
            }
        } finally {
            Pop-Location
        }
        $result.checks.frontend_build = "passed"
    }

    $result.status = "passed"
} catch {
    $result.status = "failed"
    $result.error = $_.Exception.Message
    throw
} finally {
    if ($startedBackend -and -not $KeepBackend -and $backendProcess) {
        Stop-Process -Id $backendProcess.Id -Force -ErrorAction SilentlyContinue
    }
    $json = $result | ConvertTo-Json -Depth 10
    [System.IO.File]::WriteAllText($ReportPath, $json, [System.Text.UTF8Encoding]::new($false))
    Write-Host "Smoke report: $ReportPath" -ForegroundColor Cyan
}
