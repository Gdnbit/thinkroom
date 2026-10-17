import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'

const root = process.cwd()
const sourcePath = path.join(root, 'src', 'pages', 'ResearchChat.tsx')
const source = fs.readFileSync(sourcePath, 'utf8')

assert.match(source, /jobId\?:\s*string/)
assert.match(source, /progressPct\?:\s*number/)
assert.match(source, /progressText\?:\s*string/)
assert.match(source, /uploadApi\.getProgress\(/)
assert.match(source, /current_stage_elapsed_ms/)
assert.match(source, /progress_pct/)
assert.match(source, /refreshUploadStatuses\(sourceIds,\s*jobIds\)/)

console.log('researchChat upload progress wiring tests passed')
