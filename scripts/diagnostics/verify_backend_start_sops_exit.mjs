import { execFile } from 'node:child_process'
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { promisify } from 'node:util'

const execFileAsync = promisify(execFile)
const root = process.cwd()
const port = Number(process.env.BACKEND_START_VERIFY_PORT || '8021')
const maxExitMs = Number(process.env.BACKEND_START_VERIFY_MAX_EXIT_MS || '45000')
const pidFile = `.pids/backend-start-verify-${port}.pid`
const logFile = `logs/backend-start-verify-${port}.log`
const errFile = `logs/backend-start-verify-${port}.err`

async function run(command, args, options = {}) {
  const startedAt = Date.now()
  try {
    const result = await execFileAsync(command, args, {
      cwd: root,
      timeout: options.timeout ?? maxExitMs,
      maxBuffer: 1024 * 1024 * 10,
      windowsHide: true,
    })
    return { ok: true, elapsed_ms: Date.now() - startedAt, ...result }
  } catch (error) {
    return {
      ok: false,
      elapsed_ms: Date.now() - startedAt,
      stdout: error.stdout || '',
      stderr: error.stderr || '',
      error: error.message,
      signal: error.signal,
      code: error.code,
    }
  }
}

async function stopVerifyBackend() {
  const pidPath = join(root, pidFile)
  const pid = existsSync(pidPath) ? readFileSync(pidPath, 'utf8').trim() : ''
  const script = [
    '$pids = @()',
    pid.match(/^\d+$/) ? `$pids += [int]'${pid}'` : '',
    `$pids += @(Get-NetTCPConnection -LocalPort ${port} -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)`,
    '$pids | Sort-Object -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }',
    'Start-Sleep -Milliseconds 500',
  ].filter(Boolean).join('; ')
  return run('powershell.exe', ['-NoProfile', '-Command', script], { timeout: 15000 })
}

async function startOnce(label) {
  const result = await run('powershell.exe', [
    '-NoProfile',
    '-ExecutionPolicy',
    'Bypass',
    '-File',
    'scripts/start_backend_sops.ps1',
    '-Port',
    String(port),
    '-HostAddress',
    '127.0.0.1',
    '-PidFile',
    pidFile,
    '-LogPath',
    logFile,
    '-ErrorLogPath',
    errFile,
    '-UseLocalDependencies',
    '-HealthTimeoutSeconds',
    '30',
  ])
  if (!result.ok) {
    throw new Error(`${label} start did not exit within ${maxExitMs}ms: ${JSON.stringify(result, null, 2)}`)
  }
  if (result.elapsed_ms > maxExitMs) {
    throw new Error(`${label} start exited too slowly: ${result.elapsed_ms}ms > ${maxExitMs}ms`)
  }
  return result
}

async function health() {
  return run('powershell.exe', [
    '-NoProfile',
    '-Command',
    `try { (Invoke-WebRequest -UseBasicParsing http://127.0.0.1:${port}/health -TimeoutSec 5).Content } catch { $_.Exception.Message }`,
  ], { timeout: 15000 })
}

async function readiness() {
  return run('powershell.exe', [
    '-NoProfile',
    '-Command',
    `try { (Invoke-WebRequest -UseBasicParsing http://127.0.0.1:${port}/api/v1/health/ready -TimeoutSec 10).Content } catch { $_.Exception.Message }`,
  ], { timeout: 20000 })
}

await stopVerifyBackend()
const first = await startOnce('first')
const second = await startOnce('second')
const healthResult = await health()
const readinessResult = await readiness()
const stop = await stopVerifyBackend()

if (!healthResult.ok || !healthResult.stdout.includes('"status":"ok"')) {
  throw new Error(`backend health check failed after restart: ${JSON.stringify(healthResult, null, 2)}`)
}
if (!readinessResult.ok) {
  throw new Error(`backend readiness check failed after restart: ${JSON.stringify(readinessResult, null, 2)}`)
}
const readinessPayload = JSON.parse(readinessResult.stdout)
if (readinessPayload.checks?.redis?.error === 'not configured') {
  throw new Error(`local dependency environment was not inherited by the backend: ${readinessResult.stdout}`)
}

console.log(JSON.stringify({
  port,
  max_exit_ms: maxExitMs,
  first: { elapsed_ms: first.elapsed_ms, stdout: first.stdout.trim() },
  second: { elapsed_ms: second.elapsed_ms, stdout: second.stdout.trim() },
  health: healthResult.stdout.trim(),
  redis: readinessPayload.checks?.redis,
  stop: { ok: stop.ok, elapsed_ms: stop.elapsed_ms },
}, null, 2))
