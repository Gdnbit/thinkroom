import { useQuery } from '@tanstack/react-query'
import api from './api'
import { uploadApi, type IngestionProgress, type IngestionStatus, type UploadResponse } from './upload'
import {
  currentDecisionSession,
  type DecisionFile,
  type DecisionSession,
  type EvidenceItem,
  type IngestionStage,
} from '@/data/decisionSession'
import { summarizeParseDiagnostics, type ParseDiagnosticsSummary } from './parseDiagnostics'

const SOURCE_IDS_STORAGE_KEY = 'decision_session_source_ids'
const JOB_IDS_STORAGE_KEY = 'decision_session_job_ids'
const DECISION_RECORDS_STORAGE_KEY = 'decision_session_records'
const ACTIVE_CHAT_SESSION_STORAGE_KEY = 'decision_session_active_chat_id'

type UploadSessionOptions = {
  sessionId?: string
  speakerHint?: string
  themeHint?: string
  mergeImages?: boolean
  waitForCompletion?: boolean
  pollIntervalMs?: number
  maxPolls?: number
  signal?: AbortSignal
  onProgress?: (event: UploadProgressEvent) => void
}

type UploadProgressEvent = {
  stage: 'uploading' | 'uploaded' | 'queued' | 'processing' | 'stored' | 'failed' | 'timeout' | 'cancelled'
  sourceIds: string[]
  jobIds?: string[]
  jobProgress?: IngestionProgress[]
  statuses?: IngestionStatus[]
  parseSummaries?: ParseDiagnosticsSummary[]
  message: string
  completed: number
  total: number
  percent: number
}

type SessionBackgroundProgress = {
  sessionId?: string | null
  sourceIds: string[]
  jobIds: string[]
  statuses: IngestionStatus[]
  jobProgress: IngestionProgress[]
  parseSummaries: ParseDiagnosticsSummary[]
  stage: UploadProgressEvent['stage']
  message: string
  completed: number
  total: number
  percent: number
}

type SessionIngestionContextResponse = {
  source_ids?: unknown
  sourceIds?: unknown
  job_ids?: unknown
  jobIds?: unknown
}

type SessionUploadReceiptResponse = {
  session_id: string
  message: {
    id: string
    role: string
    content: string
    payload: Record<string, unknown>
    created_at?: string | null
  }
  upload_receipt: {
    batch_id?: string | null
    file_count?: number | null
    source_ids: string[]
    job_ids: string[]
    status: string
    message?: string
    timed_out?: boolean
    statuses?: IngestionStatus[]
  }
  scope: {
    mode: string
    source_ids: string[]
  }
}

type RoutingDecision = {
  route: 'chat' | 'briefing' | 'debate'
  reason?: string
  confidence?: number
  intent_mode?: string
  intent_confidence?: number
  assets?: string[]
  can_upgrade_to?: string[]
  can_downgrade_to?: string | null
}

type ChatCitation = {
  index?: number
  claim_id?: string
  source_id?: string
  evidence_span_id?: string
  span_type?: string
  char_start?: number | null
  char_end?: number | null
  page_or_timestamp?: string
  extraction_method?: string
  stance?: string
  summary?: string
  short_quote?: string
  trust_weight?: number
  confidence?: number
  time_horizon?: string
  speaker_id?: string
}

type DecisionQuestionResult = {
  id: string
  chatSessionId: string
  question: string
  answer: string
  createdAt: string
  routing: RoutingDecision | null
  cleanedMessage: string
  claimsUsed: number
  citations: ChatCitation[]
  modelUsed?: string
  sourceIds: string[]
  searchTier?: string
  intentMode?: string
  scopeMode?: string
}

type DecisionRecord = {
  id: string
  question: string
  answer: string
  createdAt: string
  status: 'saved'
  sourceIds: string[]
  evidence: EvidenceItem[]
  risks: DecisionSession['risks']
  routing: RoutingDecision | null
  chatSessionId?: string
  answerMessageId?: string
  modelUsed?: string
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function readString(value: unknown, fallback = '') {
  return typeof value === 'string' && value.trim() ? value : fallback
}

function readArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function uniqueSourceIds(sourceIds: string[]) {
  return Array.from(new Set(sourceIds.map((id) => id.trim()).filter(Boolean)))
}

function readIdList(value: unknown) {
  if (typeof value === 'string') return uniqueSourceIds([value])
  return uniqueSourceIds(Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [])
}

function sourceIdsStorageKey(sessionId?: string | null) {
  return sessionId ? `${SOURCE_IDS_STORAGE_KEY}:${sessionId}` : SOURCE_IDS_STORAGE_KEY
}

function jobIdsStorageKey(sessionId?: string | null) {
  return sessionId ? `${JOB_IDS_STORAGE_KEY}:${sessionId}` : JOB_IDS_STORAGE_KEY
}

function getActiveDecisionSessionId() {
  if (typeof window === 'undefined') return null
  return window.localStorage.getItem(ACTIVE_CHAT_SESSION_STORAGE_KEY)
}

function rememberActiveDecisionSessionId(sessionId?: string | null) {
  if (typeof window === 'undefined') return
  if (sessionId) window.localStorage.setItem(ACTIVE_CHAT_SESSION_STORAGE_KEY, sessionId)
  else window.localStorage.removeItem(ACTIVE_CHAT_SESSION_STORAGE_KEY)
}

function getStoredSessionSourceIds(sessionId?: string | null) {
  if (typeof window === 'undefined') return []
  const resolvedSessionId = sessionId || getActiveDecisionSessionId()
  const raw = window.localStorage.getItem(sourceIdsStorageKey(resolvedSessionId))
    || (!resolvedSessionId ? window.localStorage.getItem(SOURCE_IDS_STORAGE_KEY) : null)
  if (!raw) return []

  try {
    const parsed = JSON.parse(raw)
    return uniqueSourceIds(Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === 'string') : [])
  } catch {
    return []
  }
}

function rememberSessionSourceIds(sourceIds: string[], sessionId?: string | null) {
  if (typeof window === 'undefined') return []
  const merged = uniqueSourceIds([...getStoredSessionSourceIds(sessionId), ...sourceIds])
  window.localStorage.setItem(sourceIdsStorageKey(sessionId), JSON.stringify(merged))
  return merged
}

function getStoredSessionJobIds(sessionId?: string | null) {
  if (typeof window === 'undefined') return []
  const resolvedSessionId = sessionId || getActiveDecisionSessionId()
  const raw = window.localStorage.getItem(jobIdsStorageKey(resolvedSessionId))
    || (!resolvedSessionId ? window.localStorage.getItem(JOB_IDS_STORAGE_KEY) : null)
  if (!raw) return []

  try {
    const parsed = JSON.parse(raw)
    return uniqueSourceIds(Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === 'string') : [])
  } catch {
    return []
  }
}

function rememberSessionJobIds(jobIds: string[], sessionId?: string | null) {
  if (typeof window === 'undefined') return []
  const merged = uniqueSourceIds([...getStoredSessionJobIds(sessionId), ...jobIds])
  window.localStorage.setItem(jobIdsStorageKey(sessionId), JSON.stringify(merged))
  return merged
}

function getStoredDecisionRecords(): DecisionRecord[] {
  if (typeof window === 'undefined') return []
  const raw = window.localStorage.getItem(DECISION_RECORDS_STORAGE_KEY)
  if (!raw) return []

  try {
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.filter((item): item is DecisionRecord => isRecord(item) && typeof item.id === 'string') : []
  } catch {
    return []
  }
}

function rememberDecisionRecord(record: DecisionRecord) {
  if (typeof window === 'undefined') return record
  const records = [record, ...getStoredDecisionRecords().filter((item) => item.id !== record.id)].slice(0, 50)
  window.localStorage.setItem(DECISION_RECORDS_STORAGE_KEY, JSON.stringify(records))
  window.dispatchEvent(new Event('decision-records-updated'))
  return record
}

function isReadableCleanedMessage(cleaned: string, original: string) {
  const value = cleaned.trim()
  if (!value) return false
  const questionMarks = (value.match(/\?/g) ?? []).length
  return questionMarks < Math.max(3, value.length / 3) && value !== original.replace(/[^\x00-\x7F]/g, '?')
}

function mapCitationsToEvidence(citations: ChatCitation[]): EvidenceItem[] {
  return citations.slice(0, 6).map((citation, index) => ({
    title: citation.summary ? `引用 ${citation.index ?? index + 1}` : `证据 ${citation.index ?? index + 1}`,
    source: citation.source_id || citation.claim_id || citation.speaker_id || '知识库引用',
    strength: typeof citation.trust_weight === 'number' && citation.trust_weight >= 0.75 ? 'strong' : 'medium',
    summary: citation.summary || citation.short_quote || '后端已返回引用，但摘要字段为空。',
  }))
}

function mapFileStatus(status: string): DecisionFile['status'] {
  const normalized = status.toLowerCase()
  if (['completed', 'done', 'success', 'stored', 'indexed'].some((item) => normalized.includes(item))) return 'stored'
  if (['failed', 'error'].some((item) => normalized.includes(item))) return 'pending'
  return 'processing'
}

function isIngestionDone(status: IngestionStatus) {
  const normalized = status.status.toLowerCase()
  return ['completed', 'done', 'success', 'stored', 'indexed'].some((item) => normalized.includes(item))
}

function isIngestionFailed(status: IngestionStatus) {
  const normalized = status.status.toLowerCase()
  return ['failed', 'error'].some((item) => normalized.includes(item))
}

function formatPositiveCount(value?: number | null, unit = '字符') {
  return typeof value === 'number' && value > 0 ? `${value.toLocaleString('zh-CN')} ${unit}` : ''
}

function fallbackStatusParseSummary(status: IngestionStatus): ParseDiagnosticsSummary | null {
  if (!isIngestionDone(status)) return null
  const count =
    formatPositiveCount(status.char_count) ||
    formatPositiveCount(status.word_count) ||
    formatPositiveCount(status.word_count_est, '词')
  if (!count) return null
  return {
    method: '文本解析',
    text: `已提取 ${count}`,
    attemptCount: 0,
  }
}

function fallbackProgressParseSummary(progress: IngestionProgress): ParseDiagnosticsSummary | null {
  const parseStage = progress.stages?.find((stage) => stage.key === 'parse' && stage.status === 'completed')
  const detail = typeof parseStage?.detail === 'string' ? parseStage.detail : ''
  const chars = detail.match(/([\d,]+)\s*chars?/i)?.[1]?.replace(/,/g, '')
  const count = chars ? Number(chars) : undefined
  const formatted = formatPositiveCount(count)
  if (!formatted) return null
  return {
    method: '文本解析',
    text: `已提取 ${formatted}`,
    attemptCount: 0,
  }
}

function buildParseSummaries(statuses: IngestionStatus[] = [], jobProgress: IngestionProgress[] = []) {
  const summaries = [
    ...statuses.map((item) => summarizeParseDiagnostics(item.parse_diagnostics) || fallbackStatusParseSummary(item)),
    ...jobProgress.map((item) => summarizeParseDiagnostics(item.result_summary?.parse_diagnostics) || fallbackProgressParseSummary(item)),
  ].filter((item): item is ParseDiagnosticsSummary => Boolean(item))
  const seen = new Set<string>()
  return summaries.filter((item) => {
    if (seen.has(item.text)) return false
    seen.add(item.text)
    return true
  })
}

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

function throwIfUploadAborted(signal?: AbortSignal) {
  if (!signal?.aborted) return
  throw new DOMException('用户已取消资料解析', 'AbortError')
}

function formatIngestionSize(status: IngestionStatus) {
  if (typeof status.word_count_est === 'number' && status.word_count_est > 0) {
    return `约 ${status.word_count_est.toLocaleString('zh-CN')} 词`
  }
  if (typeof status.char_count === 'number' && status.char_count > 0) {
    return `${status.char_count.toLocaleString('zh-CN')} 字符`
  }
  if (typeof status.word_count === 'number' && status.word_count > 0) {
    return `${status.word_count.toLocaleString('zh-CN')} 字符`
  }
  return '已上传'
}

function mapStatusToFile(status: IngestionStatus): DecisionFile {
  return {
    name: status.title || status.source_id,
    type: '用户资料',
    size: formatIngestionSize(status),
    status: mapFileStatus(status.status),
  }
}

function buildStages(statuses: IngestionStatus[]): IngestionStage[] {
  if (!statuses.length) return currentDecisionSession.ingestionStages

  const total = statuses.length
  const stored = statuses.filter((item) => mapFileStatus(item.status) === 'stored').length
  const failed = statuses.filter((item) => mapFileStatus(item.status) === 'pending').length
  const allStored = stored === total

  return [
    {
      title: '已接收',
      description: `${total} 份资料已进入当前决策任务`,
      state: 'done',
    },
    {
      title: allStored ? '已解析' : '正在解析',
      description: failed ? `已完成 ${stored}/${total}，${failed} 份需要检查` : `已完成 ${stored}/${total}`,
      state: allStored ? 'done' : 'active',
    },
    {
      title: allStored ? '已切片' : '等待切片',
      description: '按主题、来源和证据粒度组织为可引用片段',
      state: allStored ? 'done' : 'pending',
    },
    {
      title: allStored ? '已向量化' : '等待向量化',
      description: '写入可检索的证据索引',
      state: allStored ? 'done' : 'pending',
    },
    {
      title: allStored ? '可引用' : '等待入库完成',
      description: allStored ? '资料可进入回答、审计和快照流程' : '完成后可被回答和审计引用',
      state: allStored ? 'done' : 'pending',
    },
  ]
}

async function readBatchStatus(sourceIds: string[]) {
  if (!sourceIds.length) return []
  return uploadApi.getBatchStatus(sourceIds)
}

async function readJobProgress(jobIds: string[]) {
  if (!jobIds.length) return []
  const settled = await Promise.allSettled(jobIds.map((jobId) => uploadApi.getProgress(jobId)))
  return settled
    .filter((item): item is PromiseFulfilledResult<IngestionProgress> => item.status === 'fulfilled')
    .map((item) => item.value)
}

function filterJobProgressBySource(progress: IngestionProgress[], sourceIds: string[]) {
  if (!sourceIds.length) return progress
  const allowedSourceIds = new Set(sourceIds)
  return progress.filter((item) => !item.source_id || allowedSourceIds.has(item.source_id))
}

async function resolveSessionIngestionIds(sessionId?: string | null) {
  const localSourceIds = getStoredSessionSourceIds(sessionId)
  const localJobIds = getStoredSessionJobIds(sessionId)
  if (!sessionId) return { sourceIds: localSourceIds, jobIds: localJobIds }

  try {
    const response = await api.get<SessionIngestionContextResponse>(`/api/v1/chat/sessions/${sessionId}/ingestion-context`)
    const serverSourceIds = readIdList(response.data.source_ids ?? response.data.sourceIds)
    const serverJobIds = readIdList(response.data.job_ids ?? response.data.jobIds)
    const sourceIds = uniqueSourceIds([...serverSourceIds, ...localSourceIds])
    const jobIds = uniqueSourceIds([...serverJobIds, ...localJobIds])
    if (serverSourceIds.length) rememberSessionSourceIds(serverSourceIds, sessionId)
    if (serverJobIds.length) rememberSessionJobIds(serverJobIds, sessionId)
    return { sourceIds, jobIds }
  } catch {
    return { sourceIds: localSourceIds, jobIds: localJobIds }
  }
}

async function getSessionBackgroundProgress(sessionId?: string | null): Promise<SessionBackgroundProgress | null> {
  const { sourceIds, jobIds } = await resolveSessionIngestionIds(sessionId)
  if (!sourceIds.length && !jobIds.length) return null

  const [statusResult, progressResult] = await Promise.allSettled([
    readBatchStatus(sourceIds),
    readJobProgress(jobIds),
  ])
  const statuses = statusResult.status === 'fulfilled' ? statusResult.value : []
  const rawJobProgress = progressResult.status === 'fulfilled' ? progressResult.value : []
  const jobProgress = filterJobProgressBySource(rawJobProgress, sourceIds)
  if (!statuses.length && !jobProgress.length) return null
  const parseSummaries = buildParseSummaries(statuses, jobProgress)
  const completedCount = statuses.filter(isIngestionDone).length
  const failedCount = statuses.filter(isIngestionFailed).length
  const terminalCount = completedCount + failedCount
  const total = sourceIds.length || statuses.length || jobIds.length
  const terminal = Boolean(statuses.length && total && terminalCount >= total)
  const backendPercent = jobProgress.length
    ? Math.round(jobProgress.reduce((sum, item) => sum + (item.progress_pct || 0), 0) / jobProgress.length)
    : 0
  const percent = terminal
    ? failedCount
      ? 96
      : 100
    : Math.min(96, Math.max(36 + Math.round((terminalCount / Math.max(1, total)) * 60), backendPercent || 0))
  const backendMessage = formatJobProgressMessage(jobProgress)
  const stage: UploadProgressEvent['stage'] = failedCount && terminal ? 'failed' : terminal ? 'stored' : 'processing'
  const message = terminal
    ? failedCount
      ? `处理完成，其中 ${failedCount} 份资料需要检查`
      : `处理完成，${completedCount} 份资料已可引用`
    : backendMessage || `正在解析、切片、向量化并入库：${terminalCount}/${Math.max(1, total)}`

  return {
    sessionId,
    sourceIds,
    jobIds,
    statuses,
    jobProgress,
    parseSummaries,
    stage,
    message,
    completed: terminalCount,
    total,
    percent,
  }
}

function formatJobProgressMessage(progress: IngestionProgress[]) {
  const running = progress.find((item) => item.status === 'running')
  const latest = running || progress[0]
  if (!latest) return ''
  const label = latest.current_stage_label || latest.current_stage || latest.message || '后台处理中'
  const elapsed = Math.max(0, Math.floor((latest.elapsed_ms || 0) / 1000))
  return `${label} · ${Math.max(0, Math.min(100, latest.progress_pct || 0))}% · ${elapsed}s`
}

function mapAuditEvidence(raw: unknown, sourceIds: string[]): EvidenceItem[] {
  const records = readArray(isRecord(raw) ? raw.sources : raw)
  const sourceIdSet = new Set(sourceIds)

  return records
    .filter((item): item is Record<string, unknown> => isRecord(item))
    .filter((item) => {
      const sourceId = readString(item.source_id)
      return !sourceIdSet.size || !sourceId || sourceIdSet.has(sourceId)
    })
    .slice(0, 6)
    .map((item) => {
      const title = readString(item.title, readString(item.name, '已入库资料'))
      const status = readString(item.status, 'stored')
      const claimCount = typeof item.claim_count === 'number' ? item.claim_count : undefined

      return {
        title,
        source: title,
        strength: status.toLowerCase().includes('complete') || claimCount ? 'strong' : 'medium',
        summary: claimCount ? `已抽取 ${claimCount} 条可审计证据` : `资料状态：${status}`,
      }
    })
}

async function getSessionEvidence(sourceIds = getStoredSessionSourceIds()) {
  if (!sourceIds.length) return currentDecisionSession.evidence

  try {
    const response = await api.get<unknown>('/api/v1/ingestion-audit/sources')
    const evidence = mapAuditEvidence(response.data, sourceIds)
    return evidence.length ? evidence : currentDecisionSession.evidence
  } catch {
    return currentDecisionSession.evidence
  }
}

async function getSessionIngestionStatus(sourceIds = getStoredSessionSourceIds()) {
  try {
    return await readBatchStatus(sourceIds)
  } catch {
    return []
  }
}

async function getCurrentDecisionSession(): Promise<DecisionSession> {
  const sourceIds = getStoredSessionSourceIds()
  const statuses = await getSessionIngestionStatus(sourceIds)
  const evidence = await getSessionEvidence(sourceIds)

  if (!sourceIds.length || !statuses.length) {
    return currentDecisionSession
  }

  return {
    ...currentDecisionSession,
    title: `当前会话已接收 ${sourceIds.length} 份资料`,
    files: statuses.map(mapStatusToFile),
    ingestionStages: buildStages(statuses),
    evidence,
  }
}

async function uploadSessionFiles(files: File[], options: UploadSessionOptions = {}) {
  throwIfUploadAborted(options.signal)

  options.onProgress?.({
    stage: 'uploading',
    sourceIds: [],
    message: `正在接收 ${files.length} 份资料`,
    completed: 0,
    total: files.length,
    percent: 8,
  })

  const response = await uploadApi.upload(
    files,
    options.speakerHint,
    options.themeHint,
    options.mergeImages,
    options.sessionId,
    options.signal,
  )
  throwIfUploadAborted(options.signal)
  const sourceIds = response.data.source_ids || []
  rememberSessionSourceIds(sourceIds, options.sessionId)

  options.onProgress?.({
    stage: 'uploaded',
    sourceIds,
    message: `已接收 ${sourceIds.length || files.length} 份资料，正在触发解析`,
    completed: 0,
    total: sourceIds.length || files.length,
    percent: 22,
  })

  throwIfUploadAborted(options.signal)
  const processResult = sourceIds.length
    ? await uploadApi.processSelectedSources(sourceIds, true, options.sessionId, options.signal)
    : null
  const jobIds = uniqueSourceIds([
    ...(processResult?.data.job_ids || []),
    ...((processResult?.data.jobs || []).flatMap((item) => {
      const jobId = item.job_id
      return typeof jobId === 'string' && jobId.trim() ? [jobId] : []
    })),
  ])
  rememberSessionJobIds(jobIds, options.sessionId)

  options.onProgress?.({
    stage: 'queued',
    sourceIds,
    jobIds,
    message: jobIds.length ? `解析任务已进入后台队列（${jobIds.length} 个任务）` : '解析任务已进入后台队列',
    completed: 0,
    total: sourceIds.length,
    percent: 36,
  })

  let latestStatuses: IngestionStatus[] = []
  let timedOut = false
  if (sourceIds.length && options.waitForCompletion !== false) {
    const maxPolls = options.maxPolls ?? 90
    const pollIntervalMs = options.pollIntervalMs ?? 1500

    for (let index = 0; index < maxPolls; index += 1) {
      throwIfUploadAborted(options.signal)
      const [statusResult, progressResult] = await Promise.allSettled([
        readBatchStatus(sourceIds),
        readJobProgress(jobIds),
      ])
      latestStatuses = statusResult.status === 'fulfilled' ? statusResult.value : []
      const jobProgress = progressResult.status === 'fulfilled' ? progressResult.value : []
      const parseSummaries = buildParseSummaries(latestStatuses, jobProgress)
      const completed = latestStatuses.filter(isIngestionDone).length
      const failed = latestStatuses.filter(isIngestionFailed).length
      const total = sourceIds.length
      const terminal = completed + failed
      const backendPercent = jobProgress.length
        ? Math.round(jobProgress.reduce((sum, item) => sum + (item.progress_pct || 0), 0) / jobProgress.length)
        : 0
      const percent = Math.min(96, Math.max(36 + Math.round((terminal / Math.max(1, total)) * 60), backendPercent || 0))
      const backendMessage = formatJobProgressMessage(jobProgress)

      options.onProgress?.({
        stage: failed && terminal === total ? 'failed' : terminal === total ? 'stored' : 'processing',
        sourceIds,
        jobIds,
        jobProgress,
        statuses: latestStatuses,
        parseSummaries,
        message:
          terminal === total
            ? failed
              ? `处理完成，其中 ${failed} 份资料需要检查`
              : `处理完成，${completed} 份资料已可引用`
            : backendMessage || `正在解析、切片、向量化并入库：${terminal}/${total}`,
        completed: terminal,
        total,
        percent,
      })

      if (terminal === total) break
      throwIfUploadAborted(options.signal)
      await sleep(pollIntervalMs)
      throwIfUploadAborted(options.signal)
    }

    if (latestStatuses.length && latestStatuses.filter((item) => isIngestionDone(item) || isIngestionFailed(item)).length < sourceIds.length) {
      timedOut = true
      options.onProgress?.({
        stage: 'timeout',
        sourceIds,
        jobIds,
        statuses: latestStatuses,
        parseSummaries: buildParseSummaries(latestStatuses),
        message: '资料仍在后台处理，稍后可刷新查看结果',
        completed: latestStatuses.filter(isIngestionDone).length,
        total: sourceIds.length,
        percent: 96,
      })
    }
  }

  response.data.statuses = latestStatuses
  response.data.job_ids = jobIds
  response.data.timed_out = timedOut
  if (timedOut) response.data.status = 'background_processing'
  return response.data
}

async function recordSessionUploadReceipt(sessionId: string, receipt: UploadResponse): Promise<SessionUploadReceiptResponse> {
  const sourceIds = uniqueSourceIds(receipt.source_ids || [])
  const jobIds = uniqueSourceIds(receipt.job_ids || [])
  const response = await api.post<SessionUploadReceiptResponse>(`/api/v1/chat/sessions/${sessionId}/upload-receipt`, {
    batch_id: receipt.batch_id,
    file_count: receipt.file_count,
    source_ids: sourceIds,
    job_ids: jobIds,
    status: receipt.status,
    message: receipt.message,
    timed_out: receipt.timed_out === true,
    statuses: receipt.statuses || [],
  })
  rememberSessionSourceIds(response.data.upload_receipt.source_ids || sourceIds, sessionId)
  rememberSessionJobIds(response.data.upload_receipt.job_ids || jobIds, sessionId)
  return response.data
}

async function saveSessionSnapshot(session: DecisionSession = currentDecisionSession) {
  return saveDecisionRecord(session)
}

async function askDecisionQuestion(question: string): Promise<DecisionQuestionResult> {
  const sourceIds = getStoredSessionSourceIds()
  const createdSession = await api
    .post<{ id: string; title: string; mode: string; asset?: string }>('/api/v1/chat/sessions', {
      title: question.trim().slice(0, 40) || 'MVP decision question',
      mode: 'auto',
    })
    .then((response) => response.data)

  let routing: RoutingDecision | null = null
  let cleanedMessage = question
  try {
    const routed = await api
      .post<{ routing: RoutingDecision; cleaned_message: string; original_message: string }>(
        `/api/v1/chat/sessions/${createdSession.id}/auto`,
        { message: question, force_route: null },
      )
      .then((response) => response.data)

    routing = routed.routing
    cleanedMessage = isReadableCleanedMessage(routed.cleaned_message, question) ? routed.cleaned_message : question
  } catch {
    routing = null
    cleanedMessage = question
  }

  const answer = await api
    .post<{
      id: string
      content: string
      claims_used?: number
      citations?: ChatCitation[]
      model_used?: string
      search_tier?: string
      intent_mode?: string
      scope?: { mode?: string }
    }>(`/api/v1/chat/sessions/${createdSession.id}/chat`, {
      message: cleanedMessage,
      history: [],
      scope_mode: sourceIds.length ? 'selected_sources' : 'global',
      source_ids: sourceIds,
    })
    .then((response) => response.data)

  return {
    id: answer.id,
    chatSessionId: createdSession.id,
    question,
    answer: answer.content,
    createdAt: new Date().toLocaleString('zh-CN'),
    routing,
    cleanedMessage,
    claimsUsed: answer.claims_used ?? 0,
    citations: answer.citations ?? [],
    modelUsed: answer.model_used,
    sourceIds,
    searchTier: answer.search_tier,
    intentMode: answer.intent_mode,
    scopeMode: answer.scope?.mode,
  }
}

function saveDecisionRecord(
  session: DecisionSession = currentDecisionSession,
  meta: Partial<Pick<DecisionRecord, 'chatSessionId' | 'answerMessageId' | 'routing' | 'modelUsed' | 'sourceIds'>> = {},
) {
  const record: DecisionRecord = {
    id: `decision-record-${Date.now()}`,
    question: session.userQuestion,
    answer: [session.answer.conclusion, session.answer.rationale].filter(Boolean).join('\n\n'),
    createdAt: new Date().toLocaleString('zh-CN'),
    status: 'saved',
    sourceIds: meta.sourceIds ?? getStoredSessionSourceIds(),
    evidence: session.evidence,
    risks: session.risks,
    routing: meta.routing ?? null,
    chatSessionId: meta.chatSessionId,
    answerMessageId: meta.answerMessageId,
    modelUsed: meta.modelUsed,
  }
  return rememberDecisionRecord(record)
}

async function createSessionReview(snapshotId: string, payload: Record<string, unknown>) {
  return api.post(`/api/v1/replay/${snapshotId}/outcome`, payload).then((response) => response.data)
}

function useCurrentDecisionSession() {
  return useQuery({
    queryKey: ['decision-session', 'current', getStoredSessionSourceIds().join(',')],
    queryFn: getCurrentDecisionSession,
    initialData: currentDecisionSession,
    refetchOnWindowFocus: false,
    staleTime: 10_000,
  })
}

function useDecisionRecords() {
  return useQuery({
    queryKey: ['decision-records', 'local'],
    queryFn: getStoredDecisionRecords,
    initialData: getStoredDecisionRecords,
    refetchOnWindowFocus: false,
    staleTime: 5_000,
  })
}

export {
  DECISION_RECORDS_STORAGE_KEY,
  JOB_IDS_STORAGE_KEY,
  SOURCE_IDS_STORAGE_KEY,
  askDecisionQuestion,
  createSessionReview,
  getCurrentDecisionSession,
  getActiveDecisionSessionId,
  getStoredDecisionRecords,
  getSessionEvidence,
  getSessionBackgroundProgress,
  getSessionIngestionStatus,
  getStoredSessionJobIds,
  getStoredSessionSourceIds,
  mapCitationsToEvidence,
  recordSessionUploadReceipt,
  rememberSessionJobIds,
  rememberSessionSourceIds,
  rememberActiveDecisionSessionId,
  saveDecisionRecord,
  saveSessionSnapshot,
  uploadSessionFiles,
  useCurrentDecisionSession,
  useDecisionRecords,
}
export type {
  DecisionQuestionResult,
  DecisionRecord,
  RoutingDecision,
  SessionBackgroundProgress,
  SessionUploadReceiptResponse,
  UploadProgressEvent,
}
