import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import vm from 'node:vm'
import { createRequire } from 'node:module'
import ts from 'typescript'

const root = process.cwd()
const require = createRequire(import.meta.url)
const sourcePath = path.join(root, 'src', 'utils', 'traceEvalReview.ts')
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
const {
  TRACE_EVAL_REVIEW_ACTIONS,
  canPrepareTraceEvalPromotionDraft,
  filterTraceEvalReviewItems,
  getTraceEvalReviewStatusLabel,
  summarizeTraceEvalReviewManifest,
} = { ...sandbox.exports, ...sandbox.module.exports }

assert.equal(getTraceEvalReviewStatusLabel('pending_review'), '待审')
assert.equal(getTraceEvalReviewStatusLabel('already_covered'), '已覆盖')
assert.equal(getTraceEvalReviewStatusLabel('needs_follow_up'), '需跟进')
assert.equal(getTraceEvalReviewStatusLabel('unknown_status'), 'unknown_status')
assert.equal(canPrepareTraceEvalPromotionDraft('approved_for_manifest'), true)
assert.equal(canPrepareTraceEvalPromotionDraft('pending_review'), false)
assert.equal(canPrepareTraceEvalPromotionDraft('already_covered'), false)

const summary = summarizeTraceEvalReviewManifest({
  status: 'review_required',
  total_candidates: 50,
  pending_candidates: 49,
  reviewed_candidates: 0,
  covered_candidates: 1,
})

assert.equal(summary.primaryLabel, '49 待审')
assert.equal(summary.secondaryLabel, '50 total · 1 covered · 0 reviewed')
assert.equal(summary.tone, 'warning')

assert.equal(
  JSON.stringify(TRACE_EVAL_REVIEW_ACTIONS.map((item) => item.status)),
  JSON.stringify(['needs_follow_up', 'approved_for_manifest', 'ignored', 'rejected']),
)

const items = [
  {
    review_status: 'pending_review',
    candidate: {
      id: 'trace-chat-message-a',
      workflow: 'chat',
      trigger_reasons: ['quality:available_citations_not_used'],
    },
  },
  {
    review_status: 'approved_for_manifest',
    candidate: {
      id: 'trace-committee-b',
      workflow: 'committee',
      trigger_reasons: ['quality:structured_quality_not_ready'],
    },
  },
  {
    review_status: 'already_covered',
    candidate: {
      id: 'trace-chat-covered',
      workflow: 'chat',
      trigger_reasons: ['llm:mock'],
    },
  },
]

assert.equal(
  JSON.stringify(filterTraceEvalReviewItems(items, { status: 'pending_review' }).map((item) => item.candidate.id)),
  JSON.stringify(['trace-chat-message-a']),
)
assert.equal(
  JSON.stringify(filterTraceEvalReviewItems(items, { workflow: 'chat' }).map((item) => item.candidate.id)),
  JSON.stringify(['trace-chat-message-a', 'trace-chat-covered']),
)
assert.equal(
  JSON.stringify(filterTraceEvalReviewItems(items, { query: 'structured' }).map((item) => item.candidate.id)),
  JSON.stringify(['trace-committee-b']),
)
assert.equal(
  JSON.stringify(filterTraceEvalReviewItems(items, { status: 'all', workflow: 'all', query: 'TRACE-CHAT' }).map((item) => item.candidate.id)),
  JSON.stringify(['trace-chat-message-a', 'trace-chat-covered']),
)

console.log('traceEvalReview tests passed')
