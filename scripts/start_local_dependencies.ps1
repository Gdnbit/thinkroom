[CmdletBinding()]
param(
    [string]$ComposeFile = "",
    [string]$Bucket = "agent-files",
    [string]$MinioEndpoint = "127.0.0.1:9000",
    [string]$MinioAccessKey = "minioadmin",
    [string]$MinioSecretKey = "minioadmin",
    [int]$TimeoutSeconds = 120,
    [switch]$StartDockerDesktop
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $ComposeFile) {
    $ComposeFile = Join-Path $ProjectRoot "docker-compose.yml"
}
$ReportDir = Join-Path $ProjectRoot "docs\verification"
$ReportPath = Join-Path $ReportDir "local-dependencies-latest.json"
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
New-Item -ItemType Directory -Path $ReportDir -Force | Out-Null

# Default backend env hint:
# REDIS_ENABLED=true
# REDIS_URL=redis://127.0.0.1:6379/0
# RATE_LIMIT_STORAGE_URI=redis://127.0.0.1:6379/1
# MINIO_ENDPOINT=127.0.0.1:9000
# MINIO_BUCKET=agent-files

function Test-DockerDaemon {
    try {
        $null = & docker info --format "{{.ServerVersion}}" 2>$null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Get-ProjectRelativePath {
    param([string]$Path)

    $resolved = (Resolve-Path -LiteralPath $Path).Path
    $root = $ProjectRoot.TrimEnd('\', '/')
    if ($resolved.StartsWith($root + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
        return $resolved.Substring($root.Length + 1).Replace("\", "/")
    }
    return Split-Path -Leaf $resolved
}

function Wait-DockerDaemon {
    param([int]$TimeoutSeconds)

    if (Test-DockerDaemon) {
        return
    }

    if ($StartDockerDesktop) {
        $dockerDesktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
        if (Test-Path -LiteralPath $dockerDesktop) {
            Start-Process -FilePath $dockerDesktop -WindowStyle Hidden
        }
    }

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-DockerDaemon) {
            return
        }
        Start-Sleep -Seconds 2
    }

    throw "Docker daemon is not running. Start Docker Desktop, or rerun with -StartDockerDesktop."
}

function Wait-ContainerHealthy {
    param(
        [string]$ContainerName,
        [int]$TimeoutSeconds
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $status = (& docker inspect -f "{{.State.Health.Status}}" $ContainerName 2>$null)
        if ($LASTEXITCODE -eq 0 -and $status -eq "healthy") {
            return
        }
        Start-Sleep -Seconds 2
    }

    throw "Container $ContainerName did not become healthy within $TimeoutSeconds seconds."
}

function Wait-HttpOk {
    param(
        [string]$Url,
        [int]$TimeoutSeconds
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                return
            }
        } catch {}
        Start-Sleep -Seconds 2
    }

    throw "Timed out waiting for $Url"
}

function Ensure-MinioBucket {
    param(
        [string]$PythonPath,
        [string]$Endpoint,
        [string]$AccessKey,
        [string]$SecretKey,
        [string]$BucketName
    )

    if (-not (Test-Path -LiteralPath $PythonPath)) {
        throw "Project venv Python not found: $PythonPath"
    }

    $env:LOCAL_MINIO_ENDPOINT = $Endpoint
    $env:LOCAL_MINIO_ACCESS_KEY = $AccessKey
    $env:LOCAL_MINIO_SECRET_KEY = $SecretKey
    $env:LOCAL_MINIO_BUCKET = $BucketName

    $code = @'
import os
from minio import Minio

endpoint = os.environ["LOCAL_MINIO_ENDPOINT"]
access_key = os.environ["LOCAL_MINIO_ACCESS_KEY"]
secret_key = os.environ["LOCAL_MINIO_SECRET_KEY"]
bucket = os.environ["LOCAL_MINIO_BUCKET"]

client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=False)
if not client.bucket_exists(bucket):
    client.make_bucket(bucket)
print(bucket)
'@

    $createdOrFound = $code | & $PythonPath -
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to ensure MinIO bucket $BucketName"
    }
    return [string]$createdOrFound
}

$result = [ordered]@{
    timestamp = (Get-Date).ToUniversalTime().ToString("o")
    status = "running"
    compose_file = Get-ProjectRelativePath -Path $ComposeFile
    services = @("redis", "minio")
    checks = [ordered]@{}
    backend_env_hint = [ordered]@{
        REDIS_ENABLED = "true"
        REDIS_URL = "redis://127.0.0.1:6379/0"
        RATE_LIMIT_STORAGE_URI = "redis://127.0.0.1:6379/1"
        MINIO_ENDPOINT = $MinioEndpoint
        MINIO_BUCKET = $Bucket
        MINIO_SECURE = "false"
    }
}

try {
    Wait-DockerDaemon -TimeoutSeconds $TimeoutSeconds
    $result.checks.docker = "ready"

    # Equivalent command: docker compose -f <compose-file> up -d redis minio
    & docker compose -f $ComposeFile up -d redis minio
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose up failed for redis/minio"
    }
    $result.checks.compose_up = "started"

    Wait-ContainerHealthy -ContainerName "agent_redis" -TimeoutSeconds $TimeoutSeconds
    Wait-ContainerHealthy -ContainerName "agent_minio" -TimeoutSeconds $TimeoutSeconds
    $result.checks.redis = "healthy"
    $result.checks.minio = "healthy"

    Wait-HttpOk -Url "http://$MinioEndpoint/minio/health/live" -TimeoutSeconds $TimeoutSeconds
    $result.checks.minio_http = "ok"

    $bucketName = Ensure-MinioBucket `
        -PythonPath $VenvPython `
        -Endpoint $MinioEndpoint `
        -AccessKey $MinioAccessKey `
        -SecretKey $MinioSecretKey `
        -BucketName $Bucket
    $result.checks.minio_bucket = [ordered]@{
        name = $bucketName.Trim()
        ensured = $true
    }

    $result.status = "passed"
} catch {
    $result.status = "failed"
    $result.error = $_.Exception.Message
    throw
} finally {
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
    $archiveName = "local-dependencies-$stamp.json"
    $archivePath = Join-Path $ReportDir $archiveName
    $result["report_path"] = "docs/verification/$archiveName"
    $result["latest_path"] = "docs/verification/local-dependencies-latest.json"
    $json = $result | ConvertTo-Json -Depth 10
    [System.IO.File]::WriteAllText($archivePath, $json, [System.Text.UTF8Encoding]::new($false))
    [System.IO.File]::WriteAllText($ReportPath, $json, [System.Text.UTF8Encoding]::new($false))
    Write-Host "Local dependency report: docs/verification/$archiveName"
    Write-Host "Backend env hint:"
    Write-Host "  REDIS_ENABLED=true"
    Write-Host "  REDIS_URL=redis://127.0.0.1:6379/0"
    Write-Host "  RATE_LIMIT_STORAGE_URI=redis://127.0.0.1:6379/1"
    Write-Host "  MINIO_ENDPOINT=$MinioEndpoint"
    Write-Host "  MINIO_BUCKET=$Bucket"
}
