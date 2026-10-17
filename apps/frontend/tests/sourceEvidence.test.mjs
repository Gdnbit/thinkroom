import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import vm from 'node:vm'
import { createRequire } from 'node:module'
import ts from 'typescript'

const root = process.cwd()
const require = createRequire(import.meta.url)
const sourcePath = path.join(root, 'src', 'utils', 'sourceEvidence.ts')
const source = fs.readFileSync(sourcePath, 'utf8')
const transpiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2020,
    esModuleInterop: true,
  },
}).outputText

const sandbox = {
  exports: {},
  module: { exports: {} },
  require,
}
vm.runInNewContext(transpiled, sandbox, { filename: sourcePath })
const { buildEvidenceCitations } = { ...sandbox.exports, ...sandbox.module.exports }

assert.equal(typeof buildEvidenceCitations, 'function')

const briefingCitations = buildEvidenceCitations({
  mode: 'briefing',
  scope: { mode: 'selected_sources', source_ids: ['source-1'] },
  data_sources: {
    claims: {
      count: 1,
      items: [{ summary: '资料追踪能让用户确认结论来自已上传文件。', stance: 'neutral', confidence: 0.82 }],
    },
    events: {
      count: 1,
      items: [{ title: '系统演示需要展示证据链', source: 'demo-event' }],
    },
  },
})

assert.equal(briefingCitations.length, 2)
assert.equal(briefingCitations[0].source_id, 'source-1')
assert.match(briefingCitations[0].summary, /资料追踪/)
assert.equal(briefingCitations[0].stance, 'neutral')
assert.equal(briefingCitations[1].claim_id, 'briefing-event-1')

const debateCitations = buildEvidenceCitations({
  mode: 'debate',
  scope: { mode: 'selected_sources', source_ids: ['source-2'] },
  data_sources: {
    claims: {
      count: 1,
      items: [{ summary: '委员会需要基于同一资料进行多角度审查。', stance: 'neutral', confidence: 0.76 }],
    },
    events: { count: 0, items: [] },
  },
})

assert.equal(debateCitations.length, 1)
assert.equal(debateCitations[0].claim_id, 'debate-claim-1')
assert.equal(debateCitations[0].source_id, 'source-2')
assert.match(debateCitations[0].short_quote, /委员会/)

const existingCitations = buildEvidenceCitations({
  citations: [{ index: 7, claim_id: 'claim-7', source_id: 'source-7', summary: 'already structured' }],
  data_sources: {
    claims: {
      count: 1,
      items: [{ summary: 'fallback should not duplicate', stance: 'neutral', confidence: 0.5 }],
    },
  },
})

assert.equal(existingCitations.length, 1)
assert.equal(existingCitations[0].claim_id, 'claim-7')

console.log('sourceEvidence tests passed')
