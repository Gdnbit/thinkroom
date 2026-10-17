[CmdletBinding()]
param(
    [string]$ReportPath = ""
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$BackendDir = Join-Path $ProjectRoot "apps\backend"
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$VerificationDir = Join-Path $ProjectRoot "docs\verification"
if (-not $ReportPath) {
    $ReportPath = Join-Path $VerificationDir "core-modes-smoke-latest.json"
}
$CommandLogPath = [System.IO.Path]::ChangeExtension($ReportPath, ".log")

$requiredAssertions = @(
    "chat_evidence_payload_ready",
    "briefing_evidence_payload_ready",
    "committee_evidence_payload_ready",
    "ui_chat_session_contains_chat_briefing_committee_messages",
    "chat_decision_trace_persisted",
    "briefing_decision_trace_persisted",
    "committee_decision_trace_persisted"
)

if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "Project venv Python not found. Run scripts\setup_backend_venv.ps1 -Dev first."
}

New-Item -ItemType Directory -Path $VerificationDir -Force | Out-Null
$startedAt = Get-Date

$oldMock = [Environment]::GetEnvironmentVariable("OPENROUTER_MOCK", "Process")
$oldMockLatency = [Environment]::GetEnvironmentVariable("OPENROUTER_MOCK_LATENCY_MS", "Process")
$oldPythonUtf8 = [Environment]::GetEnvironmentVariable("PYTHONUTF8", "Process")
$env:OPENROUTER_MOCK = "true"
$env:OPENROUTER_MOCK_LATENCY_MS = "0"
$env:PYTHONUTF8 = "1"

try {
    Push-Location $BackendDir
    try {
        $oldErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            & $VenvPython -m app.verification.scope_acceptance *> $CommandLogPath
            $acceptanceExitCode = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $oldErrorActionPreference
        }
        if ($acceptanceExitCode -ne 0) {
            throw "Core modes acceptance command failed. See log: $CommandLogPath"
        }
    } finally {
        Pop-Location
    }

    $scopeReport = Get-ChildItem -Path $VerificationDir -Filter "scope_acceptance_*.json" |
        Where-Object { $_.LastWriteTimeUtc -ge $startedAt.ToUniversalTime().AddMinutes(-1) } |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1
    if (-not $scopeReport) {
        throw "No fresh scope acceptance report found."
    }

    $report = Get-Content -Raw -Encoding UTF8 -LiteralPath $scopeReport.FullName | ConvertFrom-Json
    if ($report.status -ne "passed") {
        throw "Core modes smoke failed. Source report: $($scopeReport.FullName)"
    }

    $assertions = @{}
    foreach ($item in @($report.assertions)) {
        if ($item.name) {
            $assertions[$item.name] = $item.status
        }
    }
    foreach ($name in $requiredAssertions) {
        if (-not $assertions.ContainsKey($name)) {
            throw "Core modes smoke missing assertion: $name"
        }
        if ($assertions[$name] -ne "passed") {
            throw "Core modes smoke assertion failed: $name"
        }
    }

    $summary = [ordered]@{
        status = "passed"
        created_at = (Get-Date).ToUniversalTime().ToString("o")
        source_report = $scopeReport.FullName
        command_log = $CommandLogPath
        run_id = $report.fixture.run_id
        marker = $report.fixture.marker_a
        session_id = $report.snapshots.ui_session.session_id
        assistant_modes = $report.snapshots.ui_session.assistant_modes
        required_assertions = $requiredAssertions
        notes = @(
            "Deterministic low-cost smoke using OPENROUTER_MOCK=true.",
            "Covers chat, briefing, committee/debate, selected-source evidence, and persisted decision traces.",
            "Created acceptance records are append-only and are not deleted."
        )
    }
    $json = $summary | ConvertTo-Json -Depth 20
    [System.IO.File]::WriteAllText($ReportPath, $json, [System.Text.UTF8Encoding]::new($false))
    Write-Host "Core modes smoke report: $ReportPath" -ForegroundColor Cyan
} finally {
    if ($null -eq $oldMock) { Remove-Item Env:OPENROUTER_MOCK -ErrorAction SilentlyContinue } else { $env:OPENROUTER_MOCK = $oldMock }
    if ($null -eq $oldMockLatency) { Remove-Item Env:OPENROUTER_MOCK_LATENCY_MS -ErrorAction SilentlyContinue } else { $env:OPENROUTER_MOCK_LATENCY_MS = $oldMockLatency }
    if ($null -eq $oldPythonUtf8) { Remove-Item Env:PYTHONUTF8 -ErrorAction SilentlyContinue } else { $env:PYTHONUTF8 = $oldPythonUtf8 }
}
