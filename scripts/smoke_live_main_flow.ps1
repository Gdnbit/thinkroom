[CmdletBinding()]
param(
    [int]$BackendPort = 8012,
    [string]$ChatModel = "google/gemini-3-flash-preview",
    [switch]$KeepBackend
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$BackendDir = Join-Path $ProjectRoot "apps\backend"
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$PidDir = Join-Path $ProjectRoot ".pids"
$RuntimeLogDir = Join-Path $ProjectRoot ".runtime_logs"
$VerificationDir = Join-Path $ProjectRoot "docs\verification"
$ReportPath = Join-Path $VerificationDir "live-main-flow-smoke-latest.json"
New-Item -ItemType Directory -Path $PidDir -Force | Out-Null
New-Item -ItemType Directory -Path $RuntimeLogDir -Force | Out-Null
New-Item -ItemType Directory -Path $VerificationDir -Force | Out-Null

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
        [int]$TimeoutSeconds = 90
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

function Invoke-Json {
    param(
        [string]$Method,
        [string]$Url,
        [object]$Body = $null,
        [int]$TimeoutSeconds = 120
    )

    $args = @{
        Method = $Method
        Uri = $Url
        TimeoutSec = $TimeoutSeconds
        ContentType = "application/json; charset=utf-8"
        UseBasicParsing = $true
        Headers = @{ Accept = "application/json" }
    }
    if ($null -ne $Body) {
        $args.Body = ($Body | ConvertTo-Json -Depth 20)
    }
    $response = Invoke-WebRequest @args
    $stream = $response.RawContentStream
    if ($stream) {
        if ($stream.CanSeek) {
            $stream.Position = 0
        }
        $reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8)
        $text = $reader.ReadToEnd()
    } else {
        $text = [string]$response.Content
    }
    if ([string]::IsNullOrWhiteSpace($text)) {
        return $null
    }
    return $text | ConvertFrom-Json
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "Project venv Python not found. Run scripts\setup_backend_venv.ps1 -Dev first."
}

if (Test-PortOpen -Port $BackendPort) {
    throw "Port $BackendPort is already in use. Pass -BackendPort <free port>."
}

$startedBackend = $false
$pidFile = Join-Path $PidDir "live-smoke-backend-$BackendPort.pid"
$backendLog = Join-Path $RuntimeLogDir "live_smoke_backend_$BackendPort.log"
$backendErr = Join-Path $RuntimeLogDir "live_smoke_backend_$BackendPort.log.err"
$baseUrl = "http://127.0.0.1:$BackendPort"
$startedAt = Get-Date
$result = [ordered]@{
    status = "running"
    started_at = $startedAt.ToUniversalTime().ToString("o")
    backend_port = $BackendPort
    chat_model = $ChatModel
    checks = [ordered]@{}
}

try {
    $oldFusionModel = [Environment]::GetEnvironmentVariable("FUSION_MODEL", "Process")
    $env:FUSION_MODEL = $ChatModel

    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $ProjectRoot "scripts\start_backend_sops.ps1") `
        -Port $BackendPort `
        -HostAddress "127.0.0.1" `
        -PidFile $pidFile `
        -LogPath $backendLog `
        -ErrorLogPath $backendErr `
        -PythonPath $VenvPython
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
    $startedBackend = $true

    Wait-HttpOk -Url "$baseUrl/health/live" -TimeoutSeconds 120 | Out-Null
    $result.checks.health_live = "passed"

    $connection = Invoke-Json -Method "POST" -Url "$baseUrl/api/v1/settings/test-connection" -TimeoutSeconds 90
    $result.checks.model_connection = $connection
    if (-not $connection.success) {
        throw "Model connection failed: $($connection.error)"
    }

    $oldMock = [Environment]::GetEnvironmentVariable("OPENROUTER_MOCK", "Process")
    $oldMockLatency = [Environment]::GetEnvironmentVariable("OPENROUTER_MOCK_LATENCY_MS", "Process")
    $env:OPENROUTER_MOCK = "true"
    $env:OPENROUTER_MOCK_LATENCY_MS = "0"
    try {
        Push-Location $BackendDir
        try {
            & $VenvPython -m app.verification.scope_acceptance | Out-Null
            if ($LASTEXITCODE -ne 0) {
                exit $LASTEXITCODE
            }
        } finally {
            Pop-Location
        }
    } finally {
        if ($null -eq $oldMock) { Remove-Item Env:OPENROUTER_MOCK -ErrorAction SilentlyContinue } else { $env:OPENROUTER_MOCK = $oldMock }
        if ($null -eq $oldMockLatency) { Remove-Item Env:OPENROUTER_MOCK_LATENCY_MS -ErrorAction SilentlyContinue } else { $env:OPENROUTER_MOCK_LATENCY_MS = $oldMockLatency }
    }

    $scopeReport = Get-ChildItem -Path $VerificationDir -Filter "scope_acceptance_*.json" |
        Where-Object { $_.LastWriteTimeUtc -ge $startedAt.ToUniversalTime().AddMinutes(-1) } |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1
    if (-not $scopeReport) {
        throw "No fresh scope acceptance report found."
    }
    $scope = Get-Content -Raw -Encoding UTF8 -LiteralPath $scopeReport.FullName | ConvertFrom-Json
    $fixture = $scope.fixture
    if (-not $fixture.source_a_id -or -not $fixture.marker_a) {
        throw "Scope acceptance fixture missing source_a_id or marker_a."
    }
    $result.checks.scope_fixture = @{
        report = $scopeReport.FullName
        source_id = $fixture.source_a_id
        marker = $fixture.marker_a
    }

    $session = Invoke-Json -Method "POST" -Url "$baseUrl/api/v1/chat/sessions" -Body @{
        title = "live smoke $($fixture.run_id)"
        mode = "chat"
        asset = "GENERAL"
    }
    if (-not $session.id) {
        throw "Session creation returned no id."
    }

    $chat = Invoke-Json -Method "POST" -Url "$baseUrl/api/v1/chat/sessions/$($session.id)/chat" -TimeoutSeconds 180 -Body @{
        message = "Use only the selected source. In 2-3 concise sentences, explain the conclusion for marker $($fixture.marker_a), and include citation markers."
        history = @()
        scope_mode = "selected_sources"
        source_ids = @($fixture.source_a_id)
    }

    $citations = @($chat.citations)
    $content = [string]$chat.content
    $modelUsed = [string]$chat.model_used
    $trace = $chat.llm_trace
    if ($content.Trim().Length -lt 40) {
        throw "Live chat content is too short."
    }
    if ($content -match "Mock" -or $modelUsed -in @("mock", "error")) {
        throw "Live chat did not use a real model. model_used=$modelUsed"
    }
    if ($trace -and $trace.mock) {
        throw "Live chat trace reports mock=true."
    }
    if (-not $trace) {
        throw "Live chat returned no llm_trace."
    }
    $traceCallCount = 0
    if ($trace.call_count) {
        $traceCallCount = [int]$trace.call_count
    } elseif ($trace.calls) {
        $traceCallCount = @($trace.calls).Count
    }
    if ($traceCallCount -lt 1) {
        throw "Live chat llm_trace has no recorded model calls."
    }
    if ($citations.Count -lt 1) {
        throw "Live chat returned no citations."
    }
    $referenceTime = [string]$chat.reference_time
    if (-not $referenceTime) {
        throw "Live chat returned no reference_time."
    }
    if (-not $content.Contains($referenceTime)) {
        throw "Live chat content does not include reference_time: $referenceTime"
    }

    $messages = Invoke-Json -Method "GET" -Url "$baseUrl/api/v1/chat/sessions/$($session.id)/messages" -TimeoutSeconds 60
    $persisted = @($messages)
    $userCount = @($persisted | Where-Object { $_.role -eq "user" }).Count
    $assistantCount = @($persisted | Where-Object { $_.role -eq "assistant" }).Count
    if ($userCount -lt 1 -or $assistantCount -lt 1) {
        throw "Persisted messages incomplete: user=$userCount assistant=$assistantCount"
    }

    $result.checks.live_chat = @{
        session_id = $session.id
        message_id = $chat.id
        model_used = $modelUsed
        citations_count = $citations.Count
        claims_used = $chat.claims_used
        reference_time = $referenceTime
        duration_ms = $chat.duration_ms
        cost_usd = $chat.cost_usd
        llm_trace = @{
            model = $trace.model
            call_count = $traceCallCount
            duration_ms = $trace.duration_ms
            input_tokens = $trace.input_tokens
            output_tokens = $trace.output_tokens
            cost_usd = $trace.cost_usd
            calls = $trace.calls
        }
        content_preview = $content.Substring(0, [Math]::Min(500, $content.Length))
    }
    $result.checks.persistence = @{
        user_messages = $userCount
        assistant_messages = $assistantCount
    }
    $result.status = "passed"
} catch {
    $result.status = "failed"
    $result.error = $_.Exception.Message
    throw
} finally {
    if ($null -eq $oldFusionModel) { Remove-Item Env:FUSION_MODEL -ErrorAction SilentlyContinue } else { $env:FUSION_MODEL = $oldFusionModel }
    if ($startedBackend -and -not $KeepBackend -and (Test-Path -LiteralPath $pidFile)) {
        $pidValue = Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue
        if ($pidValue) {
            Stop-Process -Id $pidValue -Force -ErrorAction SilentlyContinue
        }
        Remove-Item -LiteralPath $pidFile -ErrorAction SilentlyContinue
    }
    $result.finished_at = (Get-Date).ToUniversalTime().ToString("o")
    $json = $result | ConvertTo-Json -Depth 20
    [System.IO.File]::WriteAllText($ReportPath, $json, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "Live main-flow smoke report: $ReportPath" -ForegroundColor Cyan
}
