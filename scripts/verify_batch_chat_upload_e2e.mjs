#!/usr/bin/env node

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const ROOT_DIR = path.resolve(__dirname, '..')
const VERIFICATION_DIR = path.join(ROOT_DIR, 'docs', 'verification')
const REPORT_PATH = process.env.RESEARCH_OS_BATCH_E2E_REPORT_PATH || path.join(VERIFICATION_DIR, 'batch-chat-upload-e2e-latest.json')
const API_BASE = (process.env.RESEARCH_OS_API_BASE || 'http://127.0.0.1:8000').replace(/\/+$/, '')
const TIMEOUT_MS = Number(process.env.RESEARCH_OS_E2E_TIMEOUT_MS || 240000)
const POLL_INTERVAL_MS = Number(process.env.RESEARCH_OS_E2E_POLL_INTERVAL_MS || 750)
const BATCH_FILE_COUNT = Number(process.env.RESEARCH_OS_BATCH_E2E_FILE_COUNT || 3)
const REQUIRE_DECISION_READY = process.env.RESEARCH_OS_E2E_REQUIRE_DECISION_READY === '1'
const REQUIRED_INGESTION_STAGE_KEYS = ['load', 'parse', 'extraction', 'embedding', 'persist']

function stamp() {
  return new Date().toISOString().replace(/[-:.TZ]/g, '').slice(0, 14)
}

function fail(message, details = {}) {
  const error = new Error(message)
  error.details = details
  throw error
}

function writeReport(payload) {
  fs.mkdirSync(path.dirname(REPORT_PATH), { recursive: true })
  fs.writeFileSync(REPORT_PATH, `${JSON.stringify(payload, null, 2)}\n`, 'utf8')
  return REPORT_PATH
}

async function readJson(response) {
  const text = await response.text()
  try {
    return text ? JSON.parse(text) : null
  } catch {
    return { raw: text }
  }
}

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, options)
  const body = await readJson(response)
  if (!response.ok) fail(`HTTP ${response.status} ${path}`, { status: response.status, body })
  return body
}

async function sleep(ms) {
  await new Promise((resolve) => setTimeout(resolve, ms))
}

function summarizeProgress(progressSamples, finalProgress) {
  const samples = Array.isArray(progressSamples) ? progressSamples : []
  const stageKeys = new Set()
  const pctValues = new Set()
  const statuses = new Set()
  const currentStageElapsedSamples = []
  const nullRunningSamples = []

  for (const sample of samples) {
    if (sample.stage) stageKeys.add(sample.stage)
    if (Number.isFinite(sample.pct)) pctValues.add(sample.pct)
    if (sample.status) statuses.add(sample.status)
    if (Number.isFinite(sample.current_stage_elapsed_ms)) currentStageElapsedSamples.push(sample.current_stage_elapsed_ms)
    if (sample.status === 'running' && sample.stage && sample.current_stage_elapsed_ms == null) nullRunningSamples.push(sample)
  }

  const finalStages = Array.isArray(finalProgress?.stages) ? finalProgress.stages : []
  const finalStageKeys = finalStages.map((stage) => stage && stage.key).filter(Boolean)
  const missingRequiredStages = REQUIRED_INGESTION_STAGE_KEYS.filter((key) => !finalStageKeys.includes(key))

  return {
    sample_count: samples.length,
    observed_statuses: Array.from(statuses),
    observed_stage_keys: Array.from(stageKeys),
    observed_pct_values: Array.from(pctValues),
    observed_current_stage_elapsed_ms: currentStageElapsedSamples,
    final_stage_keys: finalStageKeys,
    missing_required_stage_keys: missingRequiredStages,
    stage_structure_complete: missingRequiredStages.length === 0,
    observed_stage_advance: stageKeys.size >= 2 || pctValues.size >= 2,
    observed_running_stage_elapsed: currentStageElapsedSamples.some((elapsedMs) => elapsedMs > 0),
    null_running_elapsed_count: nullRunningSamples.length,
  }
}

async function waitForIngestion(sourceId, jobId) {
  const startedAt = Date.now()
  const progressSamples = []
  let lastStatus = null

  while (Date.now() - startedAt < TIMEOUT_MS) {
    const progress = await request(`/api/v1/progress/${encodeURIComponent(jobId)}`)
    progressSamples.push({
      status: progress.status,
      pct: progress.progress_pct,
      stage: progress.current_stage,
      label: progress.current_stage_label,
      current_stage_elapsed_ms: progress.current_stage_elapsed_ms,
    })

    lastStatus = await request(`/api/v1/ingestion/status/${encodeURIComponent(sourceId)}`)
    const progressStatus = String(progress.status || '').toLowerCase()
    const sourceStatus = String(lastStatus.status || '').toLowerCase()
    const failed = ['failed', 'cancelled'].includes(progressStatus) || ['failed', 'cancelled'].includes(sourceStatus)
    if (failed) fail('Batch ingestion failed or was cancelled', { sourceId, jobId, progress, status: lastStatus, progressSamples })
    if (progressStatus === 'completed' || ['processed', 'completed', 'stored'].includes(sourceStatus)) {
      return {
        status: lastStatus,
        progress,
        progressSamples,
        progressSummary: summarizeProgress(progressSamples, progress),
      }
    }
    await sleep(POLL_INTERVAL_MS)
  }

  fail('Timed out waiting for batch ingestion', { sourceId, jobId, lastStatus, progressSamples })
}

function batchText(marker, index) {
  return [
    `Research OS 批量 E2E 验收标记：${marker}`,
    `批量文件编号：BATCH_FILE_${index}`,
    '这是一份确定性的中文批量测试资料，用于验证多文件上传、同时排队、受控 dispatcher、独立进度、限定资料源检索和引用证据。',
    '决策事实：用户上传多份资料后，系统仍应展示接收、解析、切片、向量化、入库这些可见阶段。',
    '边界说明：这份资料只用于系统验收，不应被视为任何投资建议。',
    `预期回答：当用户询问批量文件时，回答必须写出批量验收标记 ${marker}，并说明这是多文件批量链路验收。`,
  ].join('\n')
}

async function main() {
  if (!Number.isFinite(BATCH_FILE_COUNT) || BATCH_FILE_COUNT < 2) {
    fail('RESEARCH_OS_BATCH_E2E_FILE_COUNT must be at least 2', { BATCH_FILE_COUNT })
  }

  const marker = `RO_BATCH_E2E_${stamp()}_${Math.random().toString(36).slice(2, 8).toUpperCase()}`
  const startedAt = Date.now()
  const result = {
    api_base: API_BASE,
    report_path: REPORT_PATH,
    marker,
    file_count: BATCH_FILE_COUNT,
    started_at: new Date(startedAt).toISOString(),
    checks: {},
  }

  result.checks.health = await request('/health')
  result.dispatcher_before = await request('/api/v1/ingestion/dispatcher/status')

  const session = await request('/api/v1/chat/sessions', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ title: `Batch E2E ${marker}`, mode: 'chat', asset: 'GENERAL' }),
  })
  const sessionId = session.id
  if (!sessionId) fail('Session creation did not return an id', { session })
  result.session_id = sessionId

  const form = new FormData()
  for (let i = 1; i <= BATCH_FILE_COUNT; i += 1) {
    form.append('files', new Blob([batchText(marker, i)], { type: 'text/plain' }), `research-os-batch-${marker}-${i}.txt`)
  }
  form.append('speaker_hint', 'Research OS Batch E2E')
  form.append('theme_hint', 'MVP batch upload dispatcher verification')
  form.append('session_id', sessionId)

  const upload = await request('/api/v1/upload', { method: 'POST', body: form })
  const sourceIds = Array.isArray(upload.source_ids) ? upload.source_ids.filter(Boolean) : []
  if (sourceIds.length !== BATCH_FILE_COUNT) fail('Batch upload did not return the expected source count', { upload, sourceIds })
  if (upload.session_id !== sessionId) fail('Batch upload did not bind to the target session', { sessionId, upload })
  result.upload = upload
  result.source_ids = sourceIds

  const initialContext = await request(`/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/ingestion-context`)
  const initialContextSourceIds = Array.isArray(initialContext.source_ids) ? initialContext.source_ids : []
  if (!sourceIds.every((sourceId) => initialContextSourceIds.includes(sourceId))) {
    fail('Session ingestion context was not bound immediately after batch upload', { sourceIds, initialContext })
  }
  result.initial_ingestion_context = initialContext

  const queuedBatch = await request('/api/v1/ingestion/process-selected', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ source_ids: sourceIds, async_mode: true, session_id: sessionId }),
  })
  if (queuedBatch.queued_count !== sourceIds.length) fail('Selected batch ingestion did not queue every uploaded source', { sourceIds, queuedBatch })
  if (!sourceIds.every((sourceId, index) => queuedBatch.source_ids?.[index] === sourceId)) {
    fail('Selected batch ingestion changed source order', { sourceIds, queuedBatch })
  }
  const processResults = queuedBatch.jobs.map((queued) => {
    if (!queued.job_id) fail('Async ingestion did not return job_id', { queued })
    if (!['bounded_thread_pool', 'celery'].includes(queued.dispatch_status?.dispatcher)) {
      fail('Async ingestion did not use a supported dispatcher', { queued })
    }
    return { source_id: queued.source_id, job_id: queued.job_id, queued }
  })
  result.process_selected = queuedBatch
  result.dispatcher_after_queue = await request('/api/v1/ingestion/dispatcher/status')

  const ingestionResults = await Promise.all(processResults.map(async (item) => {
    const ingestion = await waitForIngestion(item.source_id, item.job_id)
    return { ...item, ingestion }
  }))
  result.process_results = ingestionResults
  result.dispatcher_after_completion = await request('/api/v1/ingestion/dispatcher/status')

  const nullRunningElapsedCount = ingestionResults.reduce(
    (total, item) => total + Number(item.ingestion.progressSummary?.null_running_elapsed_count || 0),
    0,
  )
  result.null_running_elapsed_count = nullRunningElapsedCount

  const receipt = await request(`/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/upload-receipt`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      batch_id: upload.batch_id,
      file_count: upload.file_count,
      source_ids: sourceIds,
      job_ids: ingestionResults.map((item) => item.job_id),
      status: 'stored',
      message: 'Batch E2E upload processed and stored',
      statuses: ingestionResults.map((item) => item.ingestion.status),
    }),
  })
  result.receipt = receipt

  const question = `请严格只根据刚上传的 ${BATCH_FILE_COUNT} 份批量 E2E 文件回答：批量验收标记是什么？这些文件共同验证了什么？请原样写出标记 ${marker}`
  const answer = await request(`/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/chat`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      message: question,
      history: [],
      scope_mode: 'selected_sources',
      source_ids: sourceIds,
      batch_id: upload.batch_id,
    }),
  })

  const answerText = String(answer.content || '')
  const answerCitations = Array.isArray(answer.citations) ? answer.citations : []
  const actualSourceIds = Array.isArray(answer.scope?.actual_source_ids) ? answer.scope.actual_source_ids : []
  const citationSourceIds = answerCitations.map((item) => item && item.source_id).filter(Boolean)
  const uploadedSourceSet = new Set(sourceIds)

  result.answer = {
    id: answer.id,
    content: answer.content,
    citation_count: answerCitations.length,
    citation_source_ids: citationSourceIds,
    scope: answer.scope,
    quality_report: answer.quality_report,
    llm_trace: answer.llm_trace,
    reference_time: answer.reference_time,
    duration_ms: answer.duration_ms,
  }

  result.acceptance = {
    has_marker_in_answer: answerText.includes(marker),
    has_citation: answerCitations.length > 0,
    scoped_to_uploaded_source: sourceIds.some((sourceId) => actualSourceIds.includes(sourceId) || citationSourceIds.includes(sourceId)),
    actual_sources_subset_of_upload: actualSourceIds.every((sourceId) => uploadedSourceSet.has(sourceId)),
    citation_sources_subset_of_upload: citationSourceIds.every((sourceId) => uploadedSourceSet.has(sourceId)),
    has_reference_time: Boolean(answer.reference_time) || /20\d{2}[-年/]\d{1,2}[-月/]\d{1,2}/.test(answerText),
    no_model_error: !answerText.includes('无模型可回答') && Boolean(answer.llm_trace?.model),
    has_quality_report: answer.quality_report && typeof answer.quality_report === 'object',
    decision_ready: answer.quality_report?.decision_ready === true,
    strict_decision_ready_required: REQUIRE_DECISION_READY,
    all_sources_completed: ingestionResults.every((item) => String(item.ingestion.status?.status || '').toLowerCase() === 'completed'),
    all_progress_stage_structure_complete: ingestionResults.every((item) => item.ingestion.progressSummary?.stage_structure_complete === true),
    all_running_elapsed_non_null: nullRunningElapsedCount === 0,
    dispatcher_reported_supported_mode: ['bounded_thread_pool', 'celery'].includes(result.dispatcher_after_queue?.dispatcher),
    dispatcher_contract_matches_mode: (
      result.dispatcher_after_queue?.dispatcher === 'bounded_thread_pool'
      && result.dispatcher_after_queue?.durable === false
    ) || (
      result.dispatcher_after_queue?.dispatcher === 'celery'
      && result.dispatcher_after_queue?.durable === true
    ),
    dispatcher_max_workers_visible: Number.isFinite(result.dispatcher_after_queue?.max_workers),
  }
  result.dispatcher_profile = {
    mode: result.dispatcher_after_queue?.mode || result.dispatcher_after_queue?.dispatcher || null,
    durable: result.dispatcher_after_queue?.durable === true,
    worker_count: result.dispatcher_after_queue?.worker_count ?? null,
    max_outstanding: result.dispatcher_after_queue?.max_outstanding ?? null,
  }

  for (const [key, passed] of Object.entries(result.acceptance)) {
    if (key === 'decision_ready' && !REQUIRE_DECISION_READY) continue
    if (key === 'strict_decision_ready_required') continue
    if (!passed) fail(`Batch acceptance check failed: ${key}`, result)
  }

  result.completed_at = new Date().toISOString()
  result.elapsed_ms = Date.now() - startedAt
  writeReport(result)
  console.log(JSON.stringify(result, null, 2))
}

main().catch((error) => {
  const payload = {
    ok: false,
    report_path: REPORT_PATH,
    error: error.message,
    details: error.details || null,
  }
  try {
    writeReport(payload)
  } catch (writeError) {
    payload.report_write_error = writeError.message
  }
  console.error(JSON.stringify(payload, null, 2))
  process.exit(1)
})
