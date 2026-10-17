import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'

const root = path.resolve(import.meta.dirname, '..', '..')
const scriptPath = path.join(root, 'stop-all.ps1')
const script = fs.readFileSync(scriptPath, 'utf8')

assert.match(script, /function\s+Test-ProjectBackendProcess/, 'stop-all should identify project backend processes')
assert.match(script, /function\s+Test-ProjectFrontendProcess/, 'stop-all should identify project frontend processes')
assert.match(script, /Skipping non-project process/, 'stop-all should skip non-project port owners')
assert.doesNotMatch(
  script,
  /foreach\s*\(\$port in 8000,\s*5173\)[\s\S]*?Stop-Process -Id \$c\.OwningProcess/,
  'stop-all should not blindly stop every process listening on 8000/5173',
)

console.log('stop-all contract tests passed')
