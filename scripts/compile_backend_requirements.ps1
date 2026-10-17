[CmdletBinding()]
param(
    [string]$PythonVersion = "3.11",
    [switch]$Upgrade
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepoRoot

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    throw "uv is required to compile backend requirements. Install uv or use an environment that provides it."
}

$backendDir = "apps/backend"

# Runs: uv pip compile <input> --output-file <locked requirements>
$commonArgs = @(
    "pip", "compile",
    "--universal",
    "--python-version", $PythonVersion,
    "--no-strip-markers"
)

if ($Upgrade) {
    $commonArgs += "--upgrade"
}

& $uv.Source @commonArgs `
    "$backendDir/requirements.in" `
    "--output-file", "$backendDir/requirements.txt" `
    "--custom-compile-command", "scripts/compile_backend_requirements.ps1"

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

& $uv.Source @commonArgs `
    "$backendDir/requirements-dev.in" `
    "--output-file", "$backendDir/requirements-dev.txt" `
    "--custom-compile-command", "scripts/compile_backend_requirements.ps1"

exit $LASTEXITCODE
