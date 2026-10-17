import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'

const root = process.cwd()
const inboxPath = path.join(root, 'src', 'pages', 'Inbox.tsx')
const inboxSource = fs.readFileSync(inboxPath, 'utf8')
const knowledgeBasePath = path.join(root, 'src', 'pages', 'KnowledgeBase.tsx')
const knowledgeBaseSource = fs.readFileSync(knowledgeBasePath, 'utf8')

assert.match(inboxSource, /import\s+\{\s*uploadApi\b/)
assert.match(inboxSource, /uploadApi\.processSelectedSources\(result\.source_ids,\s*true\)/)
assert.doesNotMatch(inboxSource, /uploadApi\.processSource\(/)
assert.match(inboxSource, /uploadApi\.getProgress\(/)
assert.match(inboxSource, /uploadApi\.getBatchStatus\(/)
assert.doesNotMatch(inboxSource, /api\.post\(`\/api\/v1\/ingestion\/process\/\$\{sid\}`/)
assert.doesNotMatch(inboxSource, /api\.post\(`\/api\/v1\/ingestion\/process\/\$\{sourceId\}`/)

assert.match(knowledgeBaseSource, /rememberSessionJobIds\(/)
assert.match(knowledgeBaseSource, /uploadApi\.processSelectedSources\(sourceIds,\s*true\)/)
assert.doesNotMatch(knowledgeBaseSource, /uploadApi\.processSource\(/)
assert.match(knowledgeBaseSource, /uploadApi\.getProgress\(/)
assert.match(knowledgeBaseSource, /uploadApi\.getBatchStatus\(/)
assert.doesNotMatch(knowledgeBaseSource, /fetch\(`\/api\/v1\/ingestion\/process\/\$\{sid\}\?async_mode=true`/)
assert.doesNotMatch(knowledgeBaseSource, /fetch\(`\/api\/v1\/ingestion\/process\/\$\{sourceId\}\?async_mode=true`/)
