[CmdletBinding()]
param(
    [string]$Python = "",
    [string]$VenvPath = "",
    [switch]$Dev,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
if (-not $VenvPath) {
    $VenvPath = Join-Path $ProjectRoot ".venv"
}

function Resolve-BasePython {
    param([string]$Requested)

    if ($Requested) {
        if (-not (Test-Path -LiteralPath $Requested)) {
            throw "Requested Python path does not exist: $Requested"
        }
        return (Resolve-Path -LiteralPath $Requested).Path
    }

    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        return $launcher.Source
    }

    throw "No Python executable found. Pass -Python <path>."
}

$basePython = Resolve-BasePython -Requested $Python
$venvPython = Join-Path $VenvPath "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "Creating backend virtual environment: $VenvPath" -ForegroundColor Cyan
    & $basePython -m venv $VenvPath
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Virtual environment was not created correctly: $venvPython"
}

if (-not $SkipInstall) {
    $requirements = if ($Dev) {
        Join-Path $ProjectRoot "apps\backend\requirements-dev.txt"
    } else {
        Join-Path $ProjectRoot "apps\backend\requirements.txt"
    }

    if (-not (Test-Path -LiteralPath $requirements)) {
        throw "Requirements file does not exist: $requirements"
    }

    $requirementsText = Get-Content -LiteralPath $requirements -Raw -Encoding utf8
    $requiresPythonUlid = $requirementsText -match "(?m)^python-ulid=="

    & $venvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    if ($requiresPythonUlid) {
        & $venvPython -m pip uninstall -y ulid-py
    }

    & $venvPython -m pip install -r $requirements
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    if ($requiresPythonUlid) {
        & $venvPython -m pip install --force-reinstall python-ulid==3.1.0
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    }
}

# Runs: pip check
& $venvPython -m pip check
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

& $venvPython -c "from ulid import ULID; value = str(ULID()); assert len(value) == 26, value"
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host "Backend virtual environment is ready: $venvPython" -ForegroundColor Green
