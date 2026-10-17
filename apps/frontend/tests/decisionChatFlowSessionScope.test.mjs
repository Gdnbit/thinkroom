import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'

const root = process.cwd()
const decisionChatFlowPath = path.join(root, 'src', 'pages', 'DecisionChatFlow.tsx')
const source = fs.readFileSync(decisionChatFlowPath, 'utf8')

assert.match(source, /const effectiveSourceIds = getStoredSessionSourceIds\(sessionId as string\)/)
assert.match(source, /scopeMode: effectiveSourceIds\.length \? 'selected_sources' : undefined/)
assert.match(source, /sourceIds: effectiveSourceIds/)
assert.doesNotMatch(source, /scopeMode: sourceIds\.length \? 'selected_sources' : undefined,\s*sourceIds,/)
assert.match(source, /source_ids: effectiveSourceIds,/)
assert.doesNotMatch(source, /source_ids: sourceIds,\s*\}/)
assert.match(source, /formatGeneratedAnswerText\(briefing\.executive_summary \|\| briefing\.title/)
assert.match(source, /formatGeneratedAnswerText\(result\.synthesis/)
assert.match(source, /risk_warnings\?\.length\s*\n\s*\? result\.risk_warnings\.map\(\(item\) => `- \$\{formatGeneratedAnswerText\(item\)\}`\)/)
assert.doesNotMatch(source, /SYSTEM_SESSION_TITLE_PATTERNS/)
assert.doesNotMatch(source, /isSystemTestSession/)
assert.match(source, /const userFacingSessions = sessions/)
assert.match(source, /const visibleSessions = sessions/)

console.log('decisionChatFlow session scope tests passed')
