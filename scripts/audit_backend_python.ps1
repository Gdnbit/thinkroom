[CmdletBinding()]
param(
    [string]$RequirementsPath = "apps/backend/requirements.txt",
    [string]$OutputPath = "docs/verification/backend-python-audit-latest.json",
    [int]$TimeoutSeconds = 30,
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepoRoot

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

function Resolve-PythonCommand {
    param([string]$RequestedPython)

    if ($RequestedPython) {
        return $RequestedPython
    }

    if ($env:PYTHON) {
        return $env:PYTHON
    }

    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) {
        return $pythonCmd.Source
    }

    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        return $pyLauncher.Source
    }

    $venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        return $venvPython
    }

    throw "No Python executable found. Set PYTHON or pass -Python."
}

if (-not (Test-Path -LiteralPath $RequirementsPath)) {
    throw "Requirements file not found: $RequirementsPath"
}

$outputDir = Split-Path -Parent $OutputPath
if ($outputDir -and -not (Test-Path -LiteralPath $outputDir)) {
    New-Item -ItemType Directory -Path $outputDir | Out-Null
}

$pythonExe = Resolve-PythonCommand -RequestedPython $Python
$cacheDir = Join-Path $RepoRoot ".runtime_logs\pip-audit-cache"
New-Item -ItemType Directory -Path $cacheDir -Force | Out-Null
$auditArgs = @(
    "-r", $RequirementsPath,
    "--format", "json",
    "--output", $OutputPath,
    "--progress-spinner", "off",
    "--cache-dir", $cacheDir,
    "--desc", "off",
    "--timeout", "$TimeoutSeconds"
)

& $pythonExe -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('pip_audit') else 1)"
$hasPipAuditModule = ($LASTEXITCODE -eq 0)

if ($hasPipAuditModule) {
    & $pythonExe -m pip_audit @auditArgs
} else {
    $uvx = Get-Command uvx -ErrorAction SilentlyContinue
    if (-not $uvx) {
        throw "pip-audit is not installed and uvx is unavailable. Install with: python -m pip install pip-audit"
    }
    & $uvx.Source pip-audit @auditArgs
}

$auditExitCode = $LASTEXITCODE

if (Test-Path -LiteralPath $OutputPath) {
    $audit = Get-Content -Raw -Encoding UTF8 -LiteralPath $OutputPath | ConvertFrom-Json
    $dependencies = @($audit.dependencies)
    $affected = @($dependencies | Where-Object { @($_.vulns).Count -gt 0 })
    $uniqueVulns = New-Object "System.Collections.Generic.HashSet[string]"

    foreach ($dep in $dependencies) {
        foreach ($vuln in @($dep.vulns)) {
            [void]$uniqueVulns.Add("$($dep.name):$($vuln.id)")
        }
    }

    Write-Host "Python dependency audit report: $OutputPath"
    Write-Host "Total dependencies: $($dependencies.Count)"
    Write-Host "Dependencies with vulnerabilities: $($affected.Count)"
    Write-Host "Unique package vulnerabilities: $($uniqueVulns.Count)"

    foreach ($dep in $affected) {
        $ids = @($dep.vulns | ForEach-Object { $_.id } | Sort-Object -Unique)
        Write-Host "- $($dep.name) $($dep.version): $($ids -join ', ')"
    }
}

exit $auditExitCode
