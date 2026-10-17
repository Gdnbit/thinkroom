import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'

const root = path.resolve(import.meta.dirname, '..', '..')
const scriptPath = path.join(root, 'scripts', 'verify_chat_upload_e2e.mjs')
const script = fs.readFileSync(scriptPath, 'utf8')

assert.ok(script.includes("path.join(ROOT_DIR, 'docs', 'verification')"), 'script should write reports under docs/verification by default')
assert.ok(script.includes('RESEARCH_OS_E2E_REPORT_PATH'), 'script should allow overriding the report path')
assert.ok(script.includes('chat-upload-e2e-latest.json'), 'script should maintain a stable latest report filename')
assert.ok(script.includes('chat-upload-e2e-${stamp()}.json'), 'default runs should preserve a timestamped report')
assert.ok(script.includes('toReportPath'), 'report metadata should use project-relative paths')
assert.ok(script.includes('fs.writeFileSync'), 'script should persist the full JSON report')
assert.ok(script.includes("scope_mode: 'selected_sources'"), 'script should ask with selected source scope')
assert.ok(script.includes('citation_sources_subset_of_upload'), 'script should verify citations stay within uploaded sources')

console.log('verify_chat_upload_e2e contract tests passed')
