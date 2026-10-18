# ────────────────────────────────────────────────────────────────
#  ThinkRoom — 一键启动脚本
#
#  功能：
#    1. 检查 Docker 是否运行，确保 rsshub + we-mp-rss 容器启动
#    2. 检查 MediaCrawler API (localhost:8080) 是否运行，提示手动启动
#    3. 启动后端 (uvicorn) 到后台（日志: logs/backend.log）
#    4. 通过受控 Vite 启动器运行前端（日志: logs/frontend.log）
#    5. 全部就绪后自动打开决策问答 http://localhost:5173/chat
#
#  用法：  .\start-all.ps1
#  停止：  .\stop-all.ps1
# ────────────────────────────────────────────────────────────────

$ErrorActionPreference = "Continue"
$ProjectRoot = $PSScriptRoot
$LogDir = Join-Path $ProjectRoot "logs"
$PidDir = Join-Path $ProjectRoot ".pids"
New-Item -Path $LogDir -ItemType Directory -Force | Out-Null
New-Item -Path $PidDir -ItemType Directory -Force | Out-Null

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    [OK] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    [--] $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "    [!!] $msg" -ForegroundColor Red }

function Test-Port($port) {
    try {
        $tcp = New-Object Net.Sockets.TcpClient
        $tcp.Connect("127.0.0.1", $port)
        $tcp.Close()
        return $true
    } catch { return $false }
}

# ── 1. Docker 容器 ─────────────────────────────────────────
Write-Step "Step 1/4: Docker 中间件 (RSSHub + we-mp-rss)"
$dockerOk = $false
try {
    docker ps --format "{{.Names}}" 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $dockerOk = $true }
} catch {}

if (-not $dockerOk) {
    Write-Err "Docker Desktop 未运行，请先启动 Docker Desktop。"
    Write-Host "     手动：启动 Docker Desktop，等右下角 'Engine running' 绿灯，然后重跑本脚本。"
    exit 1
}

$running = docker ps --format "{{.Names}}" 2>$null
if ($running -notcontains "rsshub" -or $running -notcontains "we-mp-rss") {
    Write-Host "     启动缺失的容器..."
    docker compose -f "$ProjectRoot\infra\docker-compose.social.yml" up -d rsshub we-mp-rss 2>&1 | Out-Null
    Start-Sleep -Seconds 3
}

if (Test-Port 1200) { Write-Ok "RSSHub         http://localhost:1200" } else { Write-Warn "RSSHub  端口 1200 无响应" }
if (Test-Port 8090) { Write-Ok "we-mp-rss      http://localhost:8090" } else { Write-Warn "we-mp-rss 端口 8090 无响应" }

# ── 2. MediaCrawler API ─────────────────────────────────────
Write-Step "Step 2/4: MediaCrawler API (小红书 + 抖音)"
if (Test-Port 8080) {
    Write-Ok "MediaCrawler   http://localhost:8080"
} else {
    Write-Warn "MediaCrawler 未运行 (端口 8080 无响应)"
    Write-Host "     请在【新终端】手动启动："
    Write-Host '       cd C:\example\MediaCrawler' -ForegroundColor Gray
    Write-Host '       $env:PLAYWRIGHT_BROWSERS_PATH = "E:\ms-playwright"' -ForegroundColor Gray
    Write-Host '       uv run uvicorn api.main:app --port 8080 --host 0.0.0.0' -ForegroundColor Gray
    Write-Host "     （小红书/抖音同步需要此服务，若暂不需要可忽略）"
}

# ── 3. 后端 Backend ────────────────────────────────────────
Write-Step "Step 3/4: Backend (FastAPI)"
$backendLog = Join-Path $LogDir "backend.log"
$backendStarter = Join-Path $ProjectRoot "scripts\start_backend_sops.ps1"
$backendVenv = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $backendVenv)) {
    Write-Host "     准备项目隔离 Python 环境 (.venv) ..."
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $ProjectRoot "scripts\setup_backend_venv.ps1")
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Backend .venv 准备失败，请查看依赖安装输出。"
        exit $LASTEXITCODE
    }
}
$backendPidFile = Join-Path $PidDir "backend.pid"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $backendStarter `
    -Port 8000 `
    -HostAddress "127.0.0.1" `
    -PidFile $backendPidFile `
    -LogPath $backendLog `
    -ErrorLogPath "$backendLog.err" `
    -HealthTimeoutSeconds 60
if ($LASTEXITCODE -ne 0) {
    Write-Err "Backend 启动失败，请查看 $backendLog 和 $backendLog.err"
    exit $LASTEXITCODE
}
$backendPid = if (Test-Path $backendPidFile) { Get-Content $backendPidFile -ErrorAction SilentlyContinue } else { "unknown" }
Write-Ok "Backend        http://localhost:8000  (PID $backendPid, 日志: logs/backend.log)"

# ── 4. 前端 Frontend ───────────────────────────────────────
Write-Step "Step 4/4: Frontend (Vite)"
$frontendLog = Join-Path $LogDir "frontend.log"
$frontendPidFile = Join-Path $PidDir "frontend.pid"
$frontendStarter = Join-Path $ProjectRoot "scripts\start_frontend.ps1"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $frontendStarter `
    -Port 5173 `
    -HostAddress "127.0.0.1" `
    -PidFile $frontendPidFile `
    -LogPath $frontendLog `
    -ErrorLogPath "$frontendLog.err" `
    -HealthTimeoutSeconds 60
if ($LASTEXITCODE -ne 0) {
    Write-Err "Frontend 启动失败，请查看 $frontendLog 和 $frontendLog.err"
    exit $LASTEXITCODE
}
$frontendPid = if (Test-Path $frontendPidFile) { Get-Content $frontendPidFile -ErrorAction SilentlyContinue } else { "unknown" }
Write-Ok "Frontend       http://localhost:5173/chat (PID $frontendPid, 日志: logs/frontend.log)"

# ── 打开浏览器 ─────────────────────────────────────────────
Write-Step "全部就绪"
Write-Host ""
Write-Host "  决策问答:   " -NoNewline; Write-Host "http://localhost:5173/chat"       -ForegroundColor Green
Write-Host "  设置页面:   " -NoNewline; Write-Host "http://localhost:5173/settings"   -ForegroundColor Green
Write-Host "  后端 API:   " -NoNewline; Write-Host "http://localhost:8000/docs"       -ForegroundColor Green
Write-Host "  RSSHub:     " -NoNewline; Write-Host "http://localhost:1200"            -ForegroundColor Green
Write-Host "  we-mp-rss:  " -NoNewline; Write-Host "http://localhost:8090"            -ForegroundColor Green
Write-Host ""
Write-Host "  停止所有服务：.\stop-all.ps1"
Write-Host ""

Start-Process "http://localhost:5173/chat"
