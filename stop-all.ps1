# ────────────────────────────────────────────────────────────────
#  Research OS — 停止后台服务
#  停止 start-all.ps1 启动的 backend + frontend 进程
#  Docker 容器默认保留运行（它们占用很少资源，首次启动较慢）
#
#  用法：  .\stop-all.ps1              （只停 backend + frontend）
#         .\stop-all.ps1 -All          （含 Docker 容器）
# ────────────────────────────────────────────────────────────────

param(
    [switch]$All
)

$ProjectRoot = $PSScriptRoot.TrimEnd("\")
$BackendDir = Join-Path $ProjectRoot "apps\backend"
$FrontendDir = Join-Path $ProjectRoot "apps\frontend"
$PidDir = Join-Path $ProjectRoot ".pids"

function Get-ProcessCommandLine {
    param([int]$ProcessId)

    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if (-not $processInfo) {
        return ""
    }
    return [string]$processInfo.CommandLine
}

function Test-ProjectPathProcess {
    param([int]$ProcessId)

    $commandLine = Get-ProcessCommandLine -ProcessId $ProcessId
    if (-not $commandLine) {
        return $false
    }
    return $commandLine.Contains($ProjectRoot)
}

function Test-ProjectBackendProcess {
    param([int]$ProcessId)

    $commandLine = Get-ProcessCommandLine -ProcessId $ProcessId
    if (-not $commandLine) {
        return $false
    }
    $isBackend = $commandLine -match "uvicorn" -and $commandLine -match "app\.main:app"
    return $isBackend -and ($commandLine.Contains($ProjectRoot) -or $commandLine.Contains($BackendDir))
}

function Test-ProjectFrontendProcess {
    param([int]$ProcessId)

    $commandLine = Get-ProcessCommandLine -ProcessId $ProcessId
    if (-not $commandLine) {
        return $false
    }
    $isFrontendRuntime = $commandLine -match "npm|node|vite"
    return $isFrontendRuntime -and ($commandLine.Contains($ProjectRoot) -or $commandLine.Contains($FrontendDir))
}

function Stop-ProjectProcessTree {
    param(
        [int]$ProcessId,
        [string]$Name
    )

    Get-CimInstance Win32_Process -Filter "ParentProcessId=$ProcessId" -ErrorAction SilentlyContinue | ForEach-Object {
        if (Test-ProjectPathProcess -ProcessId $_.ProcessId) {
            Stop-ProjectProcessTree -ProcessId $_.ProcessId -Name "$Name child"
        } else {
            Write-Host "  [-] Skipping non-project child process for $Name (PID $($_.ProcessId))" -ForegroundColor Yellow
        }
    }

    try {
        Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "  [OK] $Name stopped (PID $ProcessId)" -ForegroundColor Green
    } catch {
        Write-Host "  [-] ${Name}: 无法停止 PID $ProcessId" -ForegroundColor Yellow
    }
}

function Test-ExpectedProjectProcess {
    param(
        [string]$Name,
        [int]$ProcessId
    )

    if ($Name -eq "backend") {
        return Test-ProjectBackendProcess -ProcessId $ProcessId
    }
    if ($Name -eq "frontend") {
        return Test-ProjectFrontendProcess -ProcessId $ProcessId
    }
    return $false
}

function Stop-ByPidFile($name) {
    $pidFile = Join-Path $PidDir "$name.pid"
    if (-not (Test-Path $pidFile)) {
        Write-Host "  [-] $name 未在运行 (无 PID 文件)" -ForegroundColor Gray
        return
    }

    $rawPid = [System.IO.File]::ReadAllText($pidFile).Trim()
    if ($rawPid -and ($rawPid -match "^\d+$")) {
        $pidValue = [int]$rawPid
        if (Test-ExpectedProjectProcess -Name $name -ProcessId $pidValue) {
            Stop-ProjectProcessTree -ProcessId $pidValue -Name $name
        } else {
            Write-Host "  [-] Skipping non-project process from $name pid file (PID $pidValue)" -ForegroundColor Yellow
        }
    }
    Remove-Item $pidFile -ErrorAction SilentlyContinue
}

function Stop-ProjectPortListener {
    param(
        [int]$Port,
        [string]$Name
    )

    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        $pidValue = [int]$c.OwningProcess
        if (Test-ExpectedProjectProcess -Name $Name -ProcessId $pidValue) {
            Stop-ProjectProcessTree -ProcessId $pidValue -Name "$Name port $Port"
        } else {
            Write-Host "  [-] Skipping non-project process on port $Port (PID $pidValue)" -ForegroundColor Yellow
        }
    }
}

Write-Host "`n==> 停止 Backend + Frontend" -ForegroundColor Cyan
Stop-ByPidFile "backend"
Stop-ByPidFile "frontend"

Stop-ProjectPortListener -Port 8000 -Name "backend"
Stop-ProjectPortListener -Port 5173 -Name "frontend"

if ($All) {
    Write-Host "`n==> 停止 Docker 容器" -ForegroundColor Cyan
    docker stop rsshub we-mp-rss 2>&1 | Out-Null
    Write-Host "  [OK] rsshub, we-mp-rss 已停止" -ForegroundColor Green
}

Write-Host "`n全部停止完毕。`n"
