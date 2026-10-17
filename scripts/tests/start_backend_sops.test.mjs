import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'

const root = path.resolve(import.meta.dirname, '..', '..')
const scriptPath = path.join(root, 'scripts', 'start_backend_sops.ps1')
const script = fs.readFileSync(scriptPath, 'utf8')
const launcherPath = path.join(root, 'scripts', 'backend_uvicorn_launcher.py')
const launcher = fs.readFileSync(launcherPath, 'utf8')

assert.match(script, /\[switch\]\$NoStopExisting/, 'script should allow opting out of stopping existing backend listeners')
assert.match(script, /\[ValidateSet\("user", "test", "legacy"\)\]/, 'script should expose explicit data profiles')
assert.match(script, /\[string\]\$DataProfile\s*=\s*"user"/, 'user data profile should be the local default')
assert.match(script, /\.runtime\\data\\user\\local-v0\.1\.db/, 'user profile should use the isolated local v0.1 database')
assert.match(script, /\.runtime_test_dbs/, 'test profile should isolate live verification writes')
assert.match(script, /function\s+Test-ProjectBackendProcess/, 'script should identify project-owned backend processes before stopping them')
assert.match(script, /function\s+Stop-ExistingBackendListeners/, 'script should stop old project backend listeners before starting a new one')
assert.match(script, /Get-NetTCPConnection/, 'script should inspect the actual listener on the target port')
assert.match(script, /\$healthUrl\s*=\s*"http:\/\/\$HostAddress`:\$TargetPort\/health"/, 'script should target the backend health endpoint')
assert.match(script, /Invoke-WebRequest\s+-Uri\s+\$healthUrl/, 'script should wait for the backend health endpoint')
assert.ok(
  script.includes('[System.IO.File]::WriteAllText($PidFile, "$($listenerPid)`n"'),
  'script should write the actual listener PID to the pid file',
)

assert.doesNotMatch(
  script,
  /-Source\s+"env:/,
  'script should not emit env: source provenance because production validation rejects plain environment origins',
)
assert.doesNotMatch(
  script,
  /\$env:SECRETS_SOURCE\s*=/,
  'script should not set global SECRETS_SOURCE because it cannot prove every inherited secret came from SOPS',
)
assert.doesNotMatch(script, /&\s+secrets\s+env/, 'launcher must not import an entire secret store')
assert.doesNotMatch(script, /\$env:OPENROUTER_API_KEY_SOURCE\s*=/, 'launcher must preserve caller provenance')
assert.match(
  script,
  /"--use-local-dependencies"/,
  'starter should pass the local dependency profile explicitly to the Python launcher',
)
assert.match(
  launcher,
  /--use-local-dependencies/,
  'Python launcher should own the final non-secret local dependency environment applied to uvicorn',
)

console.log('start_backend_sops contract tests passed')
