#!/usr/bin/env node

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const ROOT_DIR = path.resolve(__dirname, '..')
const VERIFICATION_DIR = path.join(ROOT_DIR, 'docs', 'verification')
const REPORT_PATH_OVERRIDE = process.env.RESEARCH_OS_E2E_REPORT_PATH || ''
const REPORT_PATH = REPORT_PATH_OVERRIDE || path.join(VERIFICATION_DIR, 'chat-upload-e2e-latest.json')
const API_BASE = (process.env.RESEARCH_OS_API_BASE || 'http://127.0.0.1:8000').replace(/\/+$/, '')
const TIMEOUT_MS = Number(process.env.RESEARCH_OS_E2E_TIMEOUT_MS || 180000)
const POLL_INTERVAL_MS = Number(process.env.RESEARCH_OS_E2E_POLL_INTERVAL_MS || 750)
const REQUIRE_DECISION_READY = process.env.RESEARCH_OS_E2E_REQUIRE_DECISION_READY === '1'
const REQUIRE_PROGRESS_STAGE_ADVANCE = process.env.RESEARCH_OS_E2E_REQUIRE_PROGRESS_STAGE_ADVANCE === '1'
const REQUIRED_INGESTION_STAGE_KEYS = ['load', 'parse', 'extraction', 'embedding', 'persist']

function stamp() {
  return new Date().toISOString().replace(/[-:.TZ]/g, '').slice(0, 14)
}

function fail(message, details = {}) {
  const error = new Error(message)
  error.details = details
  throw error
}

function toReportPath(value) {
  const relative = path.relative(ROOT_DIR, value)
  if (relative && !relative.startsWith('..') && !path.isAbsolute(relative)) {
    return relative.split(path.sep).join('/')
  }
  return path.basename(value)
}

function writeReport(payload) {
  const archivePath = REPORT_PATH_OVERRIDE
    ? REPORT_PATH
    : path.join(VERIFICATION_DIR, `chat-upload-e2e-${stamp()}.json`)
  const latestPath = REPORT_PATH
  payload.report_path = toReportPath(archivePath)
  payload.latest_path = toReportPath(latestPath)
  const serialized = `${JSON.stringify(payload, null, 2)}\n`
  fs.mkdirSync(path.dirname(archivePath), { recursive: true })
  fs.writeFileSync(archivePath, serialized, 'utf8')
  if (latestPath !== archivePath) {
    fs.writeFileSync(latestPath, serialized, 'utf8')
  }
  return archivePath
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
  if (!response.ok) {
    fail(`HTTP ${response.status} ${path}`, { status: response.status, body })
  }
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

  for (const sample of samples) {
    if (sample.stage) stageKeys.add(sample.stage)
    if (Number.isFinite(sample.pct)) pctValues.add(sample.pct)
    if (sample.status) statuses.add(sample.status)
    if (Number.isFinite(sample.current_stage_elapsed_ms)) currentStageElapsedSamples.push(sample.current_stage_elapsed_ms)
  }

  const finalStages = Array.isArray(finalProgress?.stages) ? finalProgress.stages : []
  const finalStageKeys = finalStages.map((stage) => stage && stage.key).filter(Boolean)
  const finalStageStatuses = Object.fromEntries(
    finalStages
      .filter((stage) => stage && stage.key)
      .map((stage) => [stage.key, stage.status || null]),
  )
  const missingRequiredStages = REQUIRED_INGESTION_STAGE_KEYS.filter((key) => !finalStageKeys.includes(key))
  const stageStructureComplete = missingRequiredStages.length === 0
  const hasRunningSample = samples.some((sample) => String(sample.status || '').toLowerCase() === 'running' && sample.stage)
  const observedStageAdvance = stageKeys.size >= 2 || pctValues.size >= 2
  const observedRunningStageElapsed = currentStageElapsedSamples.some((elapsedMs) => elapsedMs > 0)

  return {
    sample_count: samples.length,
    observed_statuses: Array.from(statuses),
    observed_stage_keys: Array.from(stageKeys),
    observed_pct_values: Array.from(pctValues),
    observed_current_stage_elapsed_ms: currentStageElapsedSamples,
    final_stage_keys: finalStageKeys,
    final_stage_statuses: finalStageStatuses,
    required_stage_keys: REQUIRED_INGESTION_STAGE_KEYS,
    missing_required_stage_keys: missingRequiredStages,
    stage_structure_complete: stageStructureComplete,
    has_running_stage_sample: hasRunningSample,
    observed_stage_advance: observedStageAdvance,
    observed_running_stage_elapsed: observedRunningStageElapsed,
    strict_stage_advance_required: REQUIRE_PROGRESS_STAGE_ADVANCE,
  }
}

async function waitForIngestion(sourceId, jobId) {
  const startedAt = Date.now()
  const progressSamples = []
  let lastStatus = null

  while (Date.now() - startedAt < TIMEOUT_MS) {
    let progress = null
    if (jobId) {
      progress = await request(`/api/v1/progress/${encodeURIComponent(jobId)}`)
      progressSamples.push({
        status: progress.status,
        pct: progress.progress_pct,
        stage: progress.current_stage,
        label: progress.current_stage_label,
        current_stage_elapsed_ms: progress.current_stage_elapsed_ms,
      })
    }

    lastStatus = await request(`/api/v1/ingestion/status/${encodeURIComponent(sourceId)}`)
    const sourceDone = ['processed', 'completed', 'stored'].includes(String(lastStatus.status || '').toLowerCase())
    const jobDone = progress && String(progress.status || '').toLowerCase() === 'completed'
    const failed = ['failed', 'cancelled'].includes(String(lastStatus.status || '').toLowerCase()) ||
      (progress && ['failed', 'cancelled'].includes(String(progress.status || '').toLowerCase()))

    if (failed) {
      fail('Ingestion failed or was cancelled', { sourceId, jobId, progress, status: lastStatus, progressSamples })
    }
    if ((jobId && jobDone) || (!jobId && sourceDone)) {
      return { status: lastStatus, progress, progressSamples, progressSummary: summarizeProgress(progressSamples, progress) }
    }
    await sleep(POLL_INTERVAL_MS)
  }

  fail('Timed out waiting for ingestion', { sourceId, jobId, lastStatus, progressSamples })
}

async function main() {
  const marker = `RO_E2E_${stamp()}_${Math.random().toString(36).slice(2, 8).toUpperCase()}`
  const fileName = `research-os-e2e-${marker}.txt`
  const text = [
    `Research OS E2E 验收标记：${marker}`,
    '这是一份确定性的中文测试资料，用于验证上传、解析、会话回执、限定资料源检索、引用证据和聊天回答渲染。',
    '决策事实：用户上传资料后，系统应该展示五个可见阶段：接收、解析、切片、向量化、入库。',
    '边界说明：这份资料只用于系统验收，不应被视为任何投资建议。',
    '预期回答：当用户询问本文件时，回答必须原样写出验收标记，并列出五个阶段，证据必须限定在本次上传的资料源。',
  ].join('\n')

  const startedAt = Date.now()
  const result = {
    api_base: API_BASE,
    report_path: REPORT_PATH,
    marker,
    file_name: fileName,
    started_at: new Date(startedAt).toISOString(),
    checks: {},
  }

  const health = await request('/health')
  result.checks.health = health

  const session = await request('/api/v1/chat/sessions', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ title: `E2E ${marker}`, mode: 'chat', asset: 'GENERAL' }),
  })
  const sessionId = session.id
  if (!sessionId) fail('Session creation did not return an id', { session })
  result.session_id = sessionId

  const form = new FormData()
  form.append('files', new Blob([text], { type: 'text/plain' }), fileName)
  form.append('speaker_hint', 'Research OS E2E')
  form.append('theme_hint', 'MVP upload scoped retrieval verification')
  form.append('session_id', sessionId)

  const upload = await request('/api/v1/upload', { method: 'POST', body: form })
  const sourceIds = Array.isArray(upload.source_ids) ? upload.source_ids.filter(Boolean) : []
  if (!sourceIds.length) fail('Upload did not return source_ids', { upload })
  if (upload.session_id !== sessionId) fail('Upload did not bind source scope to the target session', { sessionId, upload })
  result.upload = upload
  result.source_ids = sourceIds

  const initialContext = await request(`/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/ingestion-context`)
  result.initial_ingestion_context = initialContext
  const initialContextSourceIds = Array.isArray(initialContext.source_ids) ? initialContext.source_ids : []
  if (!sourceIds.every((sourceId) => initialContextSourceIds.includes(sourceId))) {
    fail('Session ingestion context was not bound immediately after upload', { sourceIds, initialContext })
  }

  const processResults = []
  for (const sourceId of sourceIds) {
    const queued = await request(
      `/api/v1/ingestion/process/${encodeURIComponent(sourceId)}?async_mode=true&session_id=${encodeURIComponent(sessionId)}`,
      { method: 'POST' },
    )
    const jobId = queued.job_id || null
    const ingestion = await waitForIngestion(sourceId, jobId)
    if (jobId && ingestion.progress?.session_id !== sessionId) {
      fail('Ingestion progress did not preserve the chat session scope', { sourceId, jobId, sessionId, queued, ingestion })
    }
    if (jobId && !ingestion.progressSummary.stage_structure_complete) {
      fail('Ingestion progress did not expose the required stage structure', { sourceId, jobId, queued, ingestion })
    }
    if (jobId && REQUIRE_PROGRESS_STAGE_ADVANCE && !ingestion.progressSummary.observed_stage_advance) {
      fail('Ingestion progress did not show observable stage or percentage advancement while polling', { sourceId, jobId, queued, ingestion })
    }
    if (jobId && REQUIRE_PROGRESS_STAGE_ADVANCE && !ingestion.progressSummary.observed_running_stage_elapsed) {
      fail('Ingestion progress did not expose live elapsed time for the running stage', { sourceId, jobId, queued, ingestion })
    }
    processResults.push({ source_id: sourceId, job_id: jobId, queued, ingestion })
  }
  result.process_results = processResults

  const receipt = await request(`/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/upload-receipt`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      batch_id: upload.batch_id,
      file_count: upload.file_count,
      source_ids: sourceIds,
      job_ids: processResults.map((item) => item.job_id).filter(Boolean),
      status: 'stored',
      message: 'E2E upload processed and stored',
      statuses: processResults.map((item) => item.ingestion.status),
    }),
  })
  result.receipt = receipt

  const context = await request(`/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/ingestion-context`)
  result.ingestion_context = context
  const contextSourceIds = Array.isArray(context.source_ids) ? context.source_ids : []
  if (!sourceIds.every((sourceId) => contextSourceIds.includes(sourceId))) {
    fail('Session ingestion context did not preserve uploaded source scope', { sourceIds, context })
  }

  const question = `请严格只根据刚上传的 E2E 文件回答：本次验收标记是什么？系统上传后需要展示哪五个阶段？请在回答中原样写出验收标记：${marker}`
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
  result.answer = {
    id: answer.id,
    content: answer.content,
    claims_used: answer.claims_used,
    citation_count: Array.isArray(answer.citations) ? answer.citations.length : 0,
    citation_source_ids: Array.isArray(answer.citations)
      ? answer.citations.map((item) => item && item.source_id).filter(Boolean)
      : [],
    scope: answer.scope,
    quality_report: answer.quality_report,
    llm_trace: answer.llm_trace,
    reference_time: answer.reference_time,
    duration_ms: answer.duration_ms,
  }

  const answerText = String(answer.content || '')
  const answerCitations = Array.isArray(answer.citations) ? answer.citations : []
  const actualSourceIds = Array.isArray(answer.scope?.actual_source_ids) ? answer.scope.actual_source_ids : []
  const citationSourceIds = answerCitations.map((item) => item && item.source_id).filter(Boolean)
  const uploadedSourceSet = new Set(sourceIds)
  const hasMarker = answerText.includes(marker)
  const hasCitation = answerCitations.length > 0
  const scopedToUpload = sourceIds.some((sourceId) => actualSourceIds.includes(sourceId) || citationSourceIds.includes(sourceId))
  const actualSourcesSubsetOfUpload = actualSourceIds.every((sourceId) => uploadedSourceSet.has(sourceId))
  const citationSourcesSubsetOfUpload = citationSourceIds.every((sourceId) => uploadedSourceSet.has(sourceId))
  const hasReferenceTime = Boolean(answer.reference_time) || /20\d{2}[-年/]\d{1,2}[-月/]\d{1,2}/.test(answerText)
  const noModelError = !answerText.includes('无模型可回答') && answer.llm_trace?.model
  const hasQualityReport = answer.quality_report && typeof answer.quality_report === 'object'
  const decisionReady = hasQualityReport && answer.quality_report.decision_ready === true

  result.acceptance = {
    has_marker_in_answer: hasMarker,
    has_citation: hasCitation,
    scoped_to_uploaded_source: scopedToUpload,
    actual_sources_subset_of_upload: actualSourcesSubsetOfUpload,
    citation_sources_subset_of_upload: citationSourcesSubsetOfUpload,
    has_reference_time: hasReferenceTime,
    no_model_error: Boolean(noModelError),
    has_quality_report: Boolean(hasQualityReport),
    decision_ready: Boolean(decisionReady),
    strict_decision_ready_required: REQUIRE_DECISION_READY,
    ingestion_progress_stage_structure_complete: processResults.every((item) => item.ingestion.progressSummary?.stage_structure_complete !== false),
    ingestion_progress_stage_advance_observed: processResults.every((item) => item.ingestion.progressSummary?.observed_stage_advance === true),
    ingestion_running_stage_elapsed_observed: processResults.every((item) => item.ingestion.progressSummary?.observed_running_stage_elapsed === true),
    strict_progress_stage_advance_required: REQUIRE_PROGRESS_STAGE_ADVANCE,
  }

  for (const [key, passed] of Object.entries(result.acceptance)) {
    if (key === 'decision_ready' && !REQUIRE_DECISION_READY) continue
    if (key === 'ingestion_progress_stage_advance_observed' && !REQUIRE_PROGRESS_STAGE_ADVANCE) continue
    if (key === 'ingestion_running_stage_elapsed_observed' && !REQUIRE_PROGRESS_STAGE_ADVANCE) continue
    if (key === 'strict_decision_ready_required') continue
    if (key === 'strict_progress_stage_advance_required') continue
    if (!passed) fail(`Acceptance check failed: ${key}`, result)
  }

  const messages = await request(`/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/messages`)
  result.message_count = Array.isArray(messages) ? messages.length : null
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
