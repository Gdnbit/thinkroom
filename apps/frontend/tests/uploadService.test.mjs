import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import vm from 'node:vm'
import { createRequire } from 'node:module'
import ts from 'typescript'

const root = process.cwd()
const nodeRequire = createRequire(import.meta.url)
const sourcePath = path.join(root, 'src', 'services', 'upload.ts')
const source = fs.readFileSync(sourcePath, 'utf8')
const transpiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2020,
    esModuleInterop: true,
  },
}).outputText

let capturedRequest = null

function sandboxRequire(id) {
  if (id === './api') {
    return {
      __esModule: true,
      default: {
        post: async (url, body, options) => {
          capturedRequest = { url, body, options }
          return { data: { batch_id: 'batch-1', file_count: 1, source_ids: ['source-1'], status: 'queued', message: 'ok' } }
        },
      },
    }
  }
  return nodeRequire(id)
}

const sandbox = {
  exports: {},
  module: { exports: {} },
  require: sandboxRequire,
  FormData,
  Blob,
  File,
  AbortSignal,
}

vm.runInNewContext(transpiled, sandbox, { filename: sourcePath })
const { uploadApi } = {
  ...sandbox.exports,
  ...sandbox.module.exports,
}

await uploadApi.upload(
  [new File(['hello'], 'session-bound.txt', { type: 'text/plain' })],
  'speaker',
  'theme',
  false,
  'session-upload-1',
)

assert.equal(capturedRequest.url, '/api/v1/upload')
assert.equal(capturedRequest.body.get('session_id'), 'session-upload-1')
assert.equal(capturedRequest.body.get('speaker_hint'), 'speaker')
assert.equal(capturedRequest.body.get('theme_hint'), 'theme')

await uploadApi.processSource('source-1', true, 'session-upload-1')

assert.equal(capturedRequest.url, '/api/v1/ingestion/process/source-1')
assert.equal(capturedRequest.options.params.async_mode, true)
assert.equal(capturedRequest.options.params.session_id, 'session-upload-1')

await uploadApi.processSelectedSources(['source-a', 'source-b'], true, 'session-upload-1')

assert.equal(capturedRequest.url, '/api/v1/ingestion/process-selected')
assert.deepEqual(JSON.parse(JSON.stringify(capturedRequest.body)), {
  source_ids: ['source-a', 'source-b'],
  async_mode: true,
  session_id: 'session-upload-1',
})

console.log('upload service tests passed')
