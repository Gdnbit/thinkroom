import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import vm from 'node:vm'
import { createRequire } from 'node:module'
import ts from 'typescript'

const root = process.cwd()
const nodeRequire = createRequire(import.meta.url)
const sourcePath = path.join(root, 'src', 'services', 'decisionSession.ts')
const source = fs.readFileSync(sourcePath, 'utf8')
const transpiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2020,
    esModuleInterop: true,
  },
}).outputText

const storage = new Map()
const progressEvents = []
const apiPosts = []
const uploadCalls = []
const processSelectedCalls = []
let processSourceCallCount = 0
const mockUploadApi = {
  upload: async (...args) => {
    uploadCalls.push(args)
    return {
      data: {
        batch_id: 'batch-timeout-1',
        file_count: 1,
        source_ids: ['source-timeout-1'],
        session_id: args[4],
        status: 'uploaded',
        message: 'uploaded',
      },
    }
  },
  processSelectedSources: async (sourceIds, asyncMode, sessionId) => {
    processSelectedCalls.push({ sourceIds, asyncMode, sessionId })
    return {
      data: {
        queued: true,
        source_ids: sourceIds,
        job_ids: ['job-timeout-1'],
        jobs: sourceIds.map((sourceId, index) => ({
          source_id: sourceId,
          job_id: `job-timeout-${index + 1}`,
          progress_url: `/api/v1/progress/job-timeout-${index + 1}`,
        })),
      },
    }
  },
  processSource: async () => {
    processSourceCallCount += 1
    throw new Error('uploadSessionFiles must use processSelectedSources for uploaded sources')
  },
  getBatchStatus: async (sourceIds) => sourceIds.includes('source-local-only')
    ? []
    : sourceIds.map((sourceId) => ({
      source_id: sourceId,
      title: sourceId === 'backend-source-1' ? 'backend.pdf' : 'slow.pdf',
      status: 'processing',
      processing_stage: 'chunking',
    })),
  getProgress: async (jobId) => ({
    job_id: jobId,
    source_id: jobId === 'backend-job-1'
      ? 'backend-source-1'
      : jobId === 'job-foreign-1'
        ? 'source-foreign-1'
        : 'source-timeout-1',
    workflow: 'ingestion',
    status: 'running',
    current_stage: 'chunking',
    current_stage_label: '正在切片',
    stages: jobId === 'backend-job-1'
      ? [{ key: 'parse', label: '解析内容', status: 'completed', elapsed_ms: 9, detail: '10 chars' }]
      : [],
    progress_pct: 41,
    elapsed_ms: 1234,
  }),
}

function sandboxRequire(id) {
  if (id === './api') {
    return {
      __esModule: true,
      default: {
        get: async (url) => {
          if (url === '/api/v1/chat/sessions/server-session-1/ingestion-context') {
            return {
              data: {
                source_ids: ['backend-source-1'],
                job_ids: ['backend-job-1'],
                progress_urls: ['/api/v1/progress/backend-job-1'],
              },
            }
          }
          return { data: [] }
        },
        post: async (url, body) => {
          apiPosts.push({ url, body })
          if (url === '/api/v1/chat/sessions/session-timeout-1/upload-receipt') {
            return {
              data: {
                session_id: 'session-timeout-1',
                upload_receipt: body,
                scope: { mode: 'selected_sources', source_ids: body.source_ids },
                message: { id: 'receipt-message-1', role: 'assistant', content: 'receipt', payload: {} },
              },
            }
          }
          return { data: {} }
        },
      },
    }
  }
  if (id === './upload') return { uploadApi: mockUploadApi }
  if (id === './parseDiagnostics') {
    return {
      summarizeParseDiagnostics: (diagnostics) => diagnostics ? {
        method: '文本读取',
        text: '解析方式：文本读取 · 10 字符',
        attemptCount: 1,
      } : null,
    }
  }
  if (id === '@/data/decisionSession') {
    return {
      currentDecisionSession: {
        evidence: [],
        ingestionStages: [],
        risks: [],
      },
    }
  }
  return nodeRequire(id)
}

const sandbox = {
  exports: {},
  module: { exports: {} },
  require: sandboxRequire,
  DOMException,
  window: {
    localStorage: {
      getItem: (key) => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value),
      removeItem: (key) => storage.delete(key),
    },
    setTimeout,
    dispatchEvent: () => {},
  },
  Event,
}

vm.runInNewContext(transpiled, sandbox, { filename: sourcePath })
const {
  uploadSessionFiles,
  getStoredSessionSourceIds,
  getStoredSessionJobIds,
  getSessionBackgroundProgress,
  recordSessionUploadReceipt,
  rememberActiveDecisionSessionId,
} = {
  ...sandbox.exports,
  ...sandbox.module.exports,
}

assert.equal(typeof uploadSessionFiles, 'function')

const result = await uploadSessionFiles([{ name: 'slow.pdf' }], {
  sessionId: 'session-timeout-1',
  maxPolls: 1,
  pollIntervalMs: 0,
  onProgress: (event) => progressEvents.push(event),
})

assert.equal(result.timed_out, true)
assert.equal(result.status, 'background_processing')
assert.deepEqual(Array.from(result.source_ids), ['source-timeout-1'])
assert.deepEqual(Array.from(result.job_ids), ['job-timeout-1'])
assert.equal(uploadCalls[0][4], 'session-timeout-1')
assert.deepEqual(processSelectedCalls, [{ sourceIds: ['source-timeout-1'], asyncMode: true, sessionId: 'session-timeout-1' }])
assert.equal(processSourceCallCount, 0)
assert.deepEqual(Array.from(getStoredSessionSourceIds('session-timeout-1')), ['source-timeout-1'])
assert.deepEqual(Array.from(getStoredSessionJobIds('session-timeout-1')), ['job-timeout-1'])
rememberActiveDecisionSessionId('session-timeout-1')
assert.deepEqual(Array.from(getStoredSessionSourceIds()), ['source-timeout-1'])
assert.deepEqual(Array.from(getStoredSessionJobIds()), ['job-timeout-1'])
const timeoutEvent = progressEvents.find((event) => event.stage === 'timeout')
assert.ok(timeoutEvent)
assert.deepEqual(Array.from(timeoutEvent.jobIds), ['job-timeout-1'])
assert.ok(!progressEvents.some((event) => event.stage === 'stored'))

const restoredProgress = await getSessionBackgroundProgress('session-timeout-1')
assert.equal(restoredProgress.stage, 'processing')
assert.deepEqual(Array.from(restoredProgress.jobIds), ['job-timeout-1'])
assert.deepEqual(Array.from(restoredProgress.sourceIds), ['source-timeout-1'])
assert.equal(restoredProgress.percent, 41)
assert.match(restoredProgress.message, /正在切片/)
assert.deepEqual(Array.from(restoredProgress.parseSummaries), [])

const restoredFromBackend = await getSessionBackgroundProgress('server-session-1')
assert.equal(restoredFromBackend.stage, 'processing')
assert.deepEqual(Array.from(restoredFromBackend.sourceIds), ['backend-source-1'])
assert.deepEqual(Array.from(restoredFromBackend.jobIds), ['backend-job-1'])
assert.equal(restoredFromBackend.parseSummaries[0].text, '已提取 10 字符')
assert.deepEqual(Array.from(getStoredSessionSourceIds('server-session-1')), ['backend-source-1'])
assert.deepEqual(Array.from(getStoredSessionJobIds('server-session-1')), ['backend-job-1'])

storage.set('decision_session_source_ids:local-only-session', JSON.stringify(['source-local-only']))
const localOnlyProgress = await getSessionBackgroundProgress('local-only-session')
assert.equal(localOnlyProgress, null)

storage.set('decision_session_source_ids:foreign-job-session', JSON.stringify(['source-local-only']))
storage.set('decision_session_job_ids:foreign-job-session', JSON.stringify(['job-foreign-1']))
const foreignJobProgress = await getSessionBackgroundProgress('foreign-job-session')
assert.equal(foreignJobProgress, null)

const receipt = await recordSessionUploadReceipt('session-timeout-1', result)
assert.equal(receipt.session_id, 'session-timeout-1')
assert.deepEqual(JSON.parse(JSON.stringify(apiPosts.at(-1))), {
  url: '/api/v1/chat/sessions/session-timeout-1/upload-receipt',
  body: {
    batch_id: 'batch-timeout-1',
    file_count: 1,
    source_ids: ['source-timeout-1'],
    job_ids: ['job-timeout-1'],
    status: 'background_processing',
    message: 'uploaded',
    timed_out: true,
    statuses: [
      {
        source_id: 'source-timeout-1',
        title: 'slow.pdf',
        status: 'processing',
        processing_stage: 'chunking',
      },
    ],
  },
})
assert.deepEqual(Array.from(getStoredSessionSourceIds('session-timeout-1')), ['source-timeout-1'])
assert.deepEqual(Array.from(getStoredSessionJobIds('session-timeout-1')), ['job-timeout-1'])

console.log('decisionSession tests passed')
