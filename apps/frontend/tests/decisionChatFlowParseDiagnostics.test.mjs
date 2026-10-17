import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'

const root = process.cwd()
const decisionSessionPath = path.join(root, 'src', 'services', 'decisionSession.ts')
const decisionChatFlowPath = path.join(root, 'src', 'pages', 'DecisionChatFlow.tsx')
const decisionSessionSource = fs.readFileSync(decisionSessionPath, 'utf8')
const decisionChatFlowSource = fs.readFileSync(decisionChatFlowPath, 'utf8')

assert.match(decisionSessionSource, /summarizeParseDiagnostics/)
assert.match(decisionSessionSource, /parseSummaries\?:\s*ParseDiagnosticsSummary\[\]/)
assert.match(decisionSessionSource, /buildParseSummaries\(/)
assert.match(decisionSessionSource, /parseSummaries:\s*buildParseSummaries\(/)

assert.match(decisionChatFlowSource, /parseSummaries:\s*ParseDiagnosticsSummary\[\]/)
assert.match(decisionChatFlowSource, /upload\.parseSummaries/)
assert.match(decisionChatFlowSource, /处理信息/)
assert.match(decisionChatFlowSource, /detailsOpen/)
assert.match(decisionChatFlowSource, /summary\.text/)
assert.doesNotMatch(decisionChatFlowSource, /getStoredSessionJobIds\(activeSessionId\)\.length\) return/)

console.log('decisionChatFlow parse diagnostics wiring tests passed')
