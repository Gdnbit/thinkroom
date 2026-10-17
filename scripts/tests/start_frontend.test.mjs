import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'

const root = path.resolve(import.meta.dirname, '..', '..')
const scriptPath = path.join(root, 'scripts', 'start_frontend.ps1')
const startAllPath = path.join(root, 'start-all.ps1')
const script = fs.readFileSync(scriptPath, 'utf8')
const startAll = fs.readFileSync(startAllPath, 'utf8')

assert.match(script, /\[switch\]\$NoStopExisting/, 'frontend starter should support a no-replacement safety mode')
assert.match(script, /function\s+Test-ProjectFrontendProcess/, 'frontend starter should identify project-owned Vite processes')
assert.match(script, /escapedProjectRoot/, 'frontend starter should recognize escaped Windows command-line paths without broad process matching')
assert.match(script, /function\s+Stop-ExistingFrontendListeners/, 'frontend starter should replace only verified project listeners')
assert.match(script, /Get-NetTCPConnection/, 'frontend starter should inspect the actual port listener')
assert.match(script, /node_modules\\vite\\bin\\vite\.js/, 'frontend starter should launch Vite directly instead of tracking an npm wrapper')
assert.match(script, /function\s+Archive-ExistingLog/, 'frontend starter should archive previous logs before redirecting output')
assert.match(script, /Move-Item\s+-LiteralPath\s+\$Path/, 'frontend starter should preserve old logs by moving them into the archive')
assert.match(script, /\$healthUrl\s*=\s*"http:\/\/\$HostAddress`:\$TargetPort\/chat"/, 'frontend health should validate the real default page')
assert.match(script, /Invoke-WebRequest\s+-Uri\s+\$healthUrl/, 'frontend starter should wait for HTTP readiness')
assert.match(script, /WriteAllText\(\$ResolvedPidFile,\s*"\$listenerPid`n"/, 'frontend starter should persist the actual listener PID')
assert.doesNotMatch(script, /Start-Process\s+-FilePath\s+"npm\.cmd"/, 'frontend starter should not persist only the npm wrapper PID')

assert.match(startAll, /scripts\\start_frontend\.ps1/, 'start-all should delegate frontend lifecycle management to the dedicated starter')
assert.match(startAll, /http:\/\/localhost:5173\/chat/, 'start-all should expose Decision Chat as the default user entry')
assert.doesNotMatch(startAll, /Start-Process\s+-FilePath\s+"npm\.cmd"/, 'start-all should not duplicate the old npm process launcher')

console.log('start_frontend contract tests passed')
