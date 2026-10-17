import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'

const root = path.resolve(import.meta.dirname, '..', '..')
const scriptPath = path.join(root, 'start-all.ps1')
const script = fs.readFileSync(scriptPath, 'utf8')

assert.match(script, /scripts\\start_backend_sops\.ps1/, 'start-all should delegate backend startup to start_backend_sops.ps1')
assert.doesNotMatch(
  script,
  /if\s*\(Test-Port 8000\)\s*\{[\s\S]*?Backend 已在运行/,
  'start-all should not skip backend startup just because port 8000 is open',
)
assert.match(script, /-HealthTimeoutSeconds\s+60/, 'start-all should rely on backend health waiting from start_backend_sops.ps1')

console.log('start-all backend contract tests passed')
