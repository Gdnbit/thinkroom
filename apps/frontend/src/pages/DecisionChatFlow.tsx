import { Children, cloneElement, isValidElement, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  AlertCircle,
  Bot,
  BookOpen,
  ChevronDown,
  ChevronUp,
  CheckCircle2,
  Clock3,
  Database,
  FileText,
  Loader2,
  MessageSquarePlus,
  Paperclip,
  Plus,
  RefreshCw,
  Search,
  Send,
  ShieldCheck,
  Trash2,
  UploadCloud,
  UserRound,
  XCircle,
} from 'lucide-react'
import { clsx } from 'clsx'
import {
  useChatMessages,
  useChatSessions,
  useAddMessage,
  useCreateSession,
  useDeleteSession,
  useGenerateBriefing,
  useRunCommittee,
  useSaveChatSnapshot,
  useSendChat,
  type Briefing,
  type ChatMsg,
  type ChatSession,
  type CommitteeRunResult,
} from '@/services/hooks'
import api from '@/services/api'
import { uploadApi } from '@/services/upload'
import {
  getSessionBackgroundProgress,
  getStoredSessionSourceIds,
  rememberActiveDecisionSessionId,
  recordSessionUploadReceipt,
  rememberSessionSourceIds,
  uploadSessionFiles,
  type SessionBackgroundProgress,
  type UploadProgressEvent,
} from '@/services/decisionSession'
import type { ParseDiagnosticsSummary } from '@/services/parseDiagnostics'
import CitationChip from '@/components/CitationChip'
import { buildEvidenceCitations, type EvidenceCitation } from '@/utils/sourceEvidence'
import {
  displayReadableText,
  formatAssistantAnswerForDisplay,
  formatGeneratedAnswerText,
  readDisplayString as readString,
} from '@/utils/answerDisplay'

type ComposerStatus = {
  tone: 'idle' | 'working' | 'success' | 'warning' | 'error'
  text: string
  sessionId?: string
} | null

type AnswerMode = 'chat' | 'briefing' | 'debate'

type PendingAnswerState = {
  sessionId: string
  message: string
  mode: AnswerMode
  startedAt: number
  nowMs: number
  stepIndex: number
  userMessagePersisted: boolean
}

type UploadProgressState = {
  sessionId: string
  fileNames: string[]
  startedAt: number
  nowMs: number
  stage: UploadProgressEvent['stage']
  stageIndex: number
  percent: number
  message: string
  completed: number
  total: number
  sourceIds: string[]
  jobIds: string[]
  parseSummaries: ParseDiagnosticsSummary[]
  done: boolean
  failed: boolean
  timedOut: boolean
  cancelled: boolean
}

const ANSWER_MODES: Array<{ id: AnswerMode; label: string; description: string }> = [
  { id: 'chat', label: '普通问答', description: '快速回答，保留引用' },
  { id: 'briefing', label: '简报生成', description: '结构化研究结论' },
  { id: 'debate', label: '委员会讨论', description: '多角色审议后回答' },
]

function formatTime(value?: string) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatReferenceTime(date = new Date()) {
  return date.toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }) + ' CST'
}

function formatTimeHorizon(value?: string | null) {
  if (!value) return ''
  return ({ short: '短期', medium: '中期', long: '长期' } as Record<string, string>)[value] ?? value
}

function sessionTitle(session: ChatSession) {
  const title = displayReadableText(session.title?.trim() || '')
  if ((title.match(/�/g) ?? []).length >= 3) return '历史会话（旧记录）'
  return title || '未命名会话'
}

function inferAssetFromQuestion(question: string) {
  const normalized = question.toUpperCase()
  const known = [
    ['石油', 'OIL'],
    ['原油', 'OIL'],
    ['黄金', 'GOLD'],
    ['美股', 'SPX'],
    ['纳指', 'NASDAQ'],
    ['比特币', 'BTC'],
    ['以太坊', 'ETH'],
  ] as const
  for (const [keyword, asset] of known) {
    if (question.includes(keyword)) return asset
  }
  const symbol = normalized.match(/\b(BTC|ETH|SOL|BNB|XRP|DOGE|GOLD|OIL|SPX|NASDAQ|NDX)\b/)
  return symbol?.[1] ?? 'GENERAL'
}

function formatBriefingAnswer(briefing: Briefing) {
  const support = briefing.supporting_points?.length
    ? briefing.supporting_points.map((item) => `- ${formatGeneratedAnswerText(item)}`).join('\n')
    : '- 暂无明确支撑点返回。'
  const contrary = briefing.contrary_points?.length
    ? briefing.contrary_points.map((item) => `- ${formatGeneratedAnswerText(item)}`).join('\n')
    : '- 暂无明确反向点返回。'
  const risks = briefing.key_risks?.length
    ? briefing.key_risks.map((item) => `- ${formatGeneratedAnswerText(item)}`).join('\n')
    : '- 暂无明确风险点返回。'
  const invalidations = briefing.invalidation_conditions?.length
    ? briefing.invalidation_conditions.map((item) => `- ${formatGeneratedAnswerText(item)}`).join('\n')
    : '- 暂无明确失效条件返回。'
  const summary = formatGeneratedAnswerText(briefing.executive_summary || briefing.title, '后端未返回摘要。')
  const action = formatGeneratedAnswerText(briefing.recommended_action, '暂不形成明确动作建议。')
  const direction = formatGeneratedAnswerText(briefing.direction, '未判定')
  const setupQuality = formatGeneratedAnswerText(briefing.setup_quality, '未判定')

  return [
    `截至 ${formatReferenceTime()}，简报结论：${summary}`,
    `**建议动作**：${action}`,
    `**方向与质量**：${direction} / ${setupQuality}`,
    `**主要依据**\n${support}`,
    `**反向因素**\n${contrary}`,
    `**风险与失效条件**\n${risks}\n${invalidations}`,
  ].join('\n\n')
}

function formatDebateAnswer(result: CommitteeRunResult) {
  const risks = result.risk_warnings?.length
    ? result.risk_warnings.map((item) => `- ${formatGeneratedAnswerText(item)}`).join('\n')
    : '- 暂无明确风险警告返回。'
  const seats = result.activated_seats?.length ? result.activated_seats.map((item) => formatGeneratedAnswerText(item)).join('、') : '未返回席位'
  const synthesis = formatGeneratedAnswerText(result.synthesis, '后端未返回综合裁决。')
  const action = formatGeneratedAnswerText(result.recommended_action, '暂不形成明确动作建议。')
  const direction = formatGeneratedAnswerText(result.direction, '未判定')
  const consensus = formatGeneratedAnswerText(result.consensus_level, '未返回')
  return [
    `截至 ${formatReferenceTime()}，委员会裁决：${synthesis}`,
    `**建议动作**：${action}`,
    `**方向 / 置信度 / 共识**：${direction} / ${Math.round((result.conviction ?? 0) * 100)}% / ${consensus}`,
    `**参与席位**：${seats}`,
    `**主要风险**\n${risks}`,
  ].join('\n\n')
}


function buildHistory(messages: ChatMsg[]) {
  return messages
    .filter((item) => item.content?.trim())
    .slice(-12)
    .map((item) => ({ role: item.role, content: item.content }))
}

function uniqueSourceIds(sourceIds: string[]) {
  return Array.from(new Set(sourceIds.map((id) => id.trim()).filter(Boolean)))
}

function extractSourceIdsFromMessages(messages: ChatMsg[]) {
  const ids: string[] = []
  for (const message of messages) {
    const payload = message.payload
    if (!payload || typeof payload !== 'object') continue
    const scopeIds = payload.scope?.source_ids
    const uploadIds = payload.upload_receipt?.source_ids
    if (Array.isArray(scopeIds)) ids.push(...scopeIds.filter((id): id is string => typeof id === 'string'))
    if (Array.isArray(uploadIds)) ids.push(...uploadIds.filter((id): id is string => typeof id === 'string'))
  }
  return uniqueSourceIds(ids)
}

type CitationItem = EvidenceCitation

type ChatQualityWarning = {
  code: string
  message: string
  severity: 'info' | 'warning' | 'error'
}

type ChatQualityReport = {
  run_id?: string
  generated_at?: string
  reference_time?: string
  duration_ms?: number
  decision_ready?: boolean
  severity?: 'ok' | 'info' | 'warning' | 'error'
  warnings?: ChatQualityWarning[]
  evidence?: {
    citation_count?: number
    evidence_span_count?: number
    source_fallback_count?: number
    invalid_citation_count?: number
    stance_mismatch_count?: number
  }
  scope?: {
    mode?: string
    source_count?: number
    matched_source_count?: number
  }
}

type ClaimCitationDetail = {
  claim: {
    id: string
    summary?: string | null
    full_quote?: string | null
    claim_type?: string | null
    stance?: string | null
    time_horizon?: string | null
    asset_scope?: string | null
    theme_scope?: string | null
    confidence?: number | null
    source_trust_weight?: number | null
    is_contradicted?: boolean | null
    contradiction_notes?: string | null
    status?: string | null
    valid_from?: string | null
    valid_until?: string | null
    time_sensitive?: boolean | null
    expiry_hint?: string | null
    created_at?: string | null
  }
  speaker?: {
    id: string
    name?: string | null
    trust_weight?: number | null
    affiliation?: string | null
    domain_tags?: string[] | null
  } | null
  sources: Array<{
    id: string
    source_type?: string | null
    title?: string | null
    url?: string | null
    publisher?: string | null
    speaker_hint?: string | null
    published_at?: string | null
    language?: string | null
    status?: string | null
    excerpt?: string | null
    excerpt_truncated?: boolean | null
    raw_text_length?: number | null
    link_type?: string | null
  }>
  evidence_spans: Array<{
    id: string
    source_id?: string | null
    span_type?: string | null
    content?: string | null
    page_or_timestamp?: string | null
    char_start?: number | null
    char_end?: number | null
    confidence?: number | null
    extraction_method?: string | null
    relevance_score?: number | null
  }>
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function getQualityReport(payload: Record<string, unknown>): ChatQualityReport | null {
  if (!isRecord(payload.quality_report)) return null
  const raw = payload.quality_report
  const warnings = Array.isArray(raw.warnings)
    ? raw.warnings.filter(isRecord).map((item) => ({
      code: readString(item.code, 'unknown'),
      message: readString(item.message, '质量提示不可读'),
      severity: (['info', 'warning', 'error'].includes(readString(item.severity))
        ? readString(item.severity)
        : 'warning') as ChatQualityWarning['severity'],
    }))
    : []
  return {
    run_id: readString(raw.run_id),
    generated_at: readString(raw.generated_at),
    reference_time: readString(raw.reference_time),
    duration_ms: typeof raw.duration_ms === 'number' ? raw.duration_ms : undefined,
    decision_ready: typeof raw.decision_ready === 'boolean' ? raw.decision_ready : undefined,
    severity: (['ok', 'info', 'warning', 'error'].includes(readString(raw.severity))
      ? readString(raw.severity)
      : 'ok') as ChatQualityReport['severity'],
    warnings,
    evidence: isRecord(raw.evidence) ? {
      citation_count: typeof raw.evidence.citation_count === 'number' ? raw.evidence.citation_count : undefined,
      evidence_span_count: typeof raw.evidence.evidence_span_count === 'number' ? raw.evidence.evidence_span_count : undefined,
      source_fallback_count: typeof raw.evidence.source_fallback_count === 'number' ? raw.evidence.source_fallback_count : undefined,
      invalid_citation_count: typeof raw.evidence.invalid_citation_count === 'number' ? raw.evidence.invalid_citation_count : undefined,
      stance_mismatch_count: typeof raw.evidence.stance_mismatch_count === 'number' ? raw.evidence.stance_mismatch_count : undefined,
    } : undefined,
    scope: isRecord(raw.scope) ? {
      mode: readString(raw.scope.mode),
      source_count: typeof raw.scope.source_count === 'number' ? raw.scope.source_count : undefined,
      matched_source_count: typeof raw.scope.matched_source_count === 'number' ? raw.scope.matched_source_count : undefined,
    } : undefined,
  }
}

function canOpenCitationDetail(citation: CitationItem) {
  return citation.detail_available === true
}

function sourceIdsFromScope(scope: unknown) {
  if (!isRecord(scope)) return []
  const raw = scope.source_ids
  return Array.isArray(raw) ? raw.filter((item): item is string => typeof item === 'string' && item.trim().length > 0) : []
}

function scopeModeLabel(scope: unknown) {
  if (!isRecord(scope)) return '全局知识库'
  const ids = sourceIdsFromScope(scope)
  if (scope.mode === 'selected_sources' || ids.length > 0) return `限定 ${ids.length} 个资料源`
  return '全局知识库'
}

function renderTextWithCitations(text: string, citationsByIndex: Map<number, CitationItem>): ReactNode[] {
  const parts: ReactNode[] = []
  const regex = /\[(\d+)\]/g
  let lastIndex = 0
  let match: RegExpExecArray | null
  let key = 0

  while ((match = regex.exec(text)) !== null) {
    const index = Number(match[1])
    const citation = citationsByIndex.get(index)
    if (!citation) continue
    if (match.index > lastIndex) parts.push(text.slice(lastIndex, match.index))
    parts.push(<CitationChip key={`citation-${index}-${key++}`} citation={citation} />)
    lastIndex = match.index + match[0].length
  }

  if (lastIndex < text.length) parts.push(text.slice(lastIndex))
  return parts.length ? parts : [text]
}

function withLocalCitations(children: ReactNode, citations: CitationItem[]): ReactNode {
  if (!citations.length) return children
  const citationsByIndex = new Map(citations.map((citation) => [citation.index, citation]))

  const walk = (node: ReactNode): ReactNode => {
    if (typeof node === 'string') {
      const replaced = renderTextWithCitations(node, citationsByIndex)
      return replaced.length === 1 ? replaced[0] : <>{replaced.map((item, index) => <span key={index}>{item}</span>)}</>
    }
    if (Array.isArray(node)) return node.map((item, index) => <span key={index}>{walk(item)}</span>)
    if (isValidElement(node)) {
      const element = node as any
      const inner = element.props?.children
      if (inner == null) return node
      return cloneElement(element, element.props, Children.map(inner, walk))
    }
    return node
  }

  return Children.map(children, walk)
}

function EvidencePanel({
  citations,
  claimsUsed,
  invalidCitations,
  stanceMismatches,
  scope,
  qualityReport,
}: {
  citations: CitationItem[]
  claimsUsed?: number
  invalidCitations?: unknown
  stanceMismatches?: unknown
  scope?: unknown
  qualityReport?: ChatQualityReport | null
}) {
  const invalidCount = Array.isArray(invalidCitations) ? invalidCitations.length : 0
  const mismatchCount = Array.isArray(stanceMismatches) ? stanceMismatches.length : 0
  const scopeLabel = scopeModeLabel(scope)
  const sourceIds = sourceIdsFromScope(scope)
  const qualityWarnings = qualityReport?.warnings ?? []
  const isDecisionReady = qualityReport?.decision_ready !== false
  const [open, setOpen] = useState(false)
  const [selectedCitation, setSelectedCitation] = useState<CitationItem | null>(null)
  const [citationDetail, setCitationDetail] = useState<ClaimCitationDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)

  const handleOpenCitationDetail = async (citation: CitationItem) => {
    setOpen(true)
    setSelectedCitation(citation)
    setCitationDetail(null)
    setDetailError(null)
    if (!canOpenCitationDetail(citation)) {
      setDetailError('该引用来自汇总结果，暂无更详细的原文定位。')
      return
    }
    setDetailLoading(true)
    try {
      const response = await api.get<ClaimCitationDetail>(`/api/v1/claims/${encodeURIComponent(citation.claim_id)}/citation`)
      setCitationDetail(response.data)
    } catch (error) {
      setDetailError(error instanceof Error ? error.message : '引用详情读取失败')
    } finally {
      setDetailLoading(false)
    }
  }

  if (!citations.length) {
    return (
      <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-border bg-secondary/40 px-3 py-2 text-xs text-muted-foreground">
        <AlertCircle className="h-3.5 w-3.5 text-amber-400" />
        <span className="font-medium text-foreground">未找到可核验来源</span>
        <span>{scopeLabel}</span>
        {typeof claimsUsed === 'number' && claimsUsed > 0 && <span>检索到 {claimsUsed} 条候选证据</span>}
      </div>
    )
  }

  return (
    <div className="mt-3 overflow-hidden rounded-lg border border-border bg-secondary/40">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full flex-wrap items-center gap-x-2 gap-y-1 px-3 py-2 text-left text-xs text-foreground transition hover:bg-secondary"
      >
        <BookOpen className="h-3.5 w-3.5 text-amber-600" />
        <span className="font-medium">参考资料</span>
        <span className="text-muted-foreground">{citations.length} 条</span>
        <span className={clsx(
          'rounded border px-1.5 py-0.5 text-[11px]',
          isDecisionReady
            ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
            : 'border-amber-500/30 bg-amber-500/10 text-amber-300',
        )}>
          {isDecisionReady ? '可作为参考' : '需复核'}
        </span>
        {qualityWarnings.length > 0 && (
          <span className="inline-flex shrink-0 items-center gap-1 text-amber-400" aria-label={`${qualityWarnings.length} 项需留意`}>
            <AlertCircle className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">{qualityWarnings.length} 项需留意</span>
          </span>
        )}
        {invalidCount > 0 && <span className="rounded bg-red-100 px-1.5 py-0.5 text-red-700">无效引用 {invalidCount}</span>}
        {mismatchCount > 0 && <span className="rounded bg-amber-100 px-1.5 py-0.5 text-amber-700">方向待核验 {mismatchCount}</span>}
        <span className="ml-auto inline-flex items-center gap-1 text-muted-foreground">
          <span className="hidden sm:inline">{open ? '收起' : '展开'}</span>
          {open ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
        </span>
      </button>
      {open && (
        <div className="space-y-2 border-t border-border p-3">
          {qualityReport && (
            <div className="rounded-lg border border-border bg-card px-3 py-2 text-[11px] text-muted-foreground">
              <div className="flex flex-wrap items-center gap-2">
                <ShieldCheck className={clsx('h-3.5 w-3.5', isDecisionReady ? 'text-emerald-400' : 'text-amber-400')} />
                <span className="font-medium text-foreground">{isDecisionReady ? '资料可作为参考' : '使用前需要复核'}</span>
                {qualityReport.reference_time && <span>参考时间 {qualityReport.reference_time}</span>}
              </div>
              {qualityWarnings.length > 0 && (
                <div className="mt-2 space-y-1">
                  {qualityWarnings.slice(0, 5).map((warning) => (
                    <div key={`${warning.code}-${warning.message}`} className="flex items-start gap-1.5 leading-5">
                      <AlertCircle className={clsx('mt-0.5 h-3 w-3 shrink-0', warning.severity === 'info' ? 'text-muted-foreground' : 'text-amber-400')} />
                      <span>{warning.message}</span>
                    </div>
                  ))}
                  {qualityWarnings.length > 5 && <div>另有 {qualityWarnings.length - 5} 项提示已折叠。</div>}
                </div>
              )}
            </div>
          )}
          {sourceIds.length > 0 && (
            <div className="rounded-lg border border-border bg-card px-3 py-2 text-[11px] text-muted-foreground">
              本次回答仅使用当前会话选定的 {sourceIds.length} 份资料。
            </div>
          )}
          {citations.map((citation) => {
            const selected = selectedCitation?.claim_id === citation.claim_id && selectedCitation?.index === citation.index
            const canOpenDetail = canOpenCitationDetail(citation)
            return (
              <div
                key={`${citation.index}-${citation.claim_id}`}
                className={clsx(
                  'rounded-lg border bg-card p-3 transition',
                  selected ? 'border-amber-500/60 ring-1 ring-amber-500/20' : 'border-border',
                )}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="rounded bg-amber-500/10 px-2 py-0.5 font-mono text-[11px] font-semibold text-amber-300">
                    [{citation.index}]
                  </span>
                  <span className="min-w-0 flex-1 text-xs font-medium text-foreground">{citation.summary}</span>
                  {canOpenDetail ? (
                    <button
                      type="button"
                      onClick={() => void handleOpenCitationDetail(citation)}
                      className="inline-flex items-center gap-1 rounded-md border border-border bg-secondary px-2 py-1 text-[11px] text-muted-foreground hover:border-amber-400 hover:text-amber-400"
                      title="查看这条引用对应的观点、来源和证据片段"
                    >
                      <BookOpen className="h-3 w-3" />
                      详情
                    </button>
                  ) : (
                    <span
                      className="inline-flex items-center gap-1 rounded-md border border-border bg-secondary px-2 py-1 text-[11px] text-muted-foreground"
                      title="该条目来自简报或委员会的来源摘要，不对应可展开的结构化观点"
                    >
                      <BookOpen className="h-3 w-3" />
                      摘要来源
                    </span>
                  )}
                </div>
                {citation.short_quote && citation.short_quote !== citation.summary && (
                  <p className="mt-2 border-l-2 border-border pl-2 text-xs leading-5 text-muted-foreground">
                    {citation.short_quote}
                  </p>
                )}
                <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-muted-foreground">
                  {citation.page_or_timestamp && <span>{citation.page_or_timestamp}</span>}
                  {citation.time_horizon && <span>适用时间 {formatTimeHorizon(citation.time_horizon)}</span>}
                </div>
              </div>
            )
          })}
          {selectedCitation && (
            <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="text-xs font-semibold text-foreground">来源详情 [{selectedCitation.index}]</div>
                {detailLoading && (
                  <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
                    <Loader2 className="h-3 w-3 animate-spin" />
                    读取中
                  </span>
                )}
              </div>
              {detailError && <div className="mt-2 rounded-md border border-red-500/20 bg-red-500/10 px-2 py-1 text-xs text-red-300">{detailError}</div>}
              {citationDetail && (
                <div className="mt-3 space-y-3">
                  <div className="rounded-md border border-border bg-card px-3 py-2">
                    <div className="text-xs font-medium text-foreground">{citationDetail.claim.summary || selectedCitation.summary}</div>
                    {citationDetail.claim.full_quote && (
                      <p className="mt-2 border-l-2 border-border pl-2 text-xs leading-5 text-muted-foreground">
                        {citationDetail.claim.full_quote}
                      </p>
                    )}
                    <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-muted-foreground">
                      {citationDetail.claim.time_horizon && <span>适用时间 {formatTimeHorizon(citationDetail.claim.time_horizon)}</span>}
                      {citationDetail.claim.valid_until && <span>有效期至 {formatTime(citationDetail.claim.valid_until)}</span>}
                      {citationDetail.speaker?.name && <span>来源 {citationDetail.speaker.name}</span>}
                    </div>
                  </div>
                  {citationDetail.sources.length > 0 && (
                    <div className="rounded-md border border-border bg-card px-3 py-2">
                      <div className="text-[11px] font-semibold text-muted-foreground">原始资料</div>
                      <div className="mt-2 space-y-2">
                        {citationDetail.sources.map((source) => (
                          <div key={source.id} className="text-xs text-muted-foreground">
                            <div className="font-medium text-foreground">{source.title || source.id}</div>
                            <div className="mt-1 flex flex-wrap gap-2 text-[11px]">
                              {source.source_type && <span>{source.source_type}</span>}
                            </div>
                            {source.excerpt && <p className="mt-1 line-clamp-3 leading-5">{source.excerpt}</p>}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {citationDetail.evidence_spans.length > 0 && (
                    <div className="rounded-md border border-border bg-card px-3 py-2">
                      <div className="text-[11px] font-semibold text-muted-foreground">对应原文</div>
                      <div className="mt-2 space-y-2">
                        {citationDetail.evidence_spans.map((span) => {
                          const current = span.id === selectedCitation.evidence_span_id
                          return (
                            <div key={span.id} className={clsx('rounded-md border px-2 py-2 text-xs', current ? 'border-amber-500/40 bg-amber-500/10' : 'border-border bg-secondary/30')}>
                              <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                                {current && <span className="rounded bg-amber-500 px-1.5 py-0.5 font-semibold text-white">当前引用</span>}
                                {span.page_or_timestamp && <span>{span.page_or_timestamp}</span>}
                              </div>
                              {span.content && <p className="mt-2 leading-5 text-muted-foreground">{span.content}</p>}
                            </div>
                          )
                        })}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

const ANSWER_PROGRESS_STEPS = [
  { label: '接收问题', description: '问题已写入当前会话', icon: CheckCircle2 },
  { label: '识别意图', description: '判断是否需要资料检索、证据引用或直接回答', icon: Search },
  { label: '检索证据', description: '从当前资料范围和知识库中寻找相关内容', icon: Database },
  { label: '生成回答', description: '调用模型组织结论、理由和风险提示', icon: Bot },
  { label: '校验引用', description: '检查引用编号和可追溯证据是否匹配', icon: ShieldCheck },
]

const UPLOAD_PROGRESS_STEPS = [
  { id: 'uploading', label: '接收资料', description: '保存原始文件并登记资料源', icon: UploadCloud },
  { id: 'uploaded', label: '触发解析', description: '启动文档解析或视觉识别任务', icon: FileText },
  { id: 'queued', label: '切片整理', description: '按来源、段落和证据粒度组织内容', icon: Clock3 },
  { id: 'processing', label: '向量化与入库', description: '写入可检索索引和证据库', icon: Database },
  { id: 'stored', label: '可引用', description: '资料已进入当前问答检索范围', icon: CheckCircle2 },
] as const

function uploadStageIndex(stage: UploadProgressEvent['stage']) {
  if (stage === 'failed' || stage === 'timeout' || stage === 'cancelled') return UPLOAD_PROGRESS_STEPS.length - 1
  const index = UPLOAD_PROGRESS_STEPS.findIndex((item) => item.id === stage)
  return index >= 0 ? index : 0
}

function uploadStateFromBackgroundProgress(
  sessionId: string,
  progress: SessionBackgroundProgress,
  existing?: UploadProgressState,
): UploadProgressState {
  const now = Date.now()
  const elapsedMs = Math.max(0, ...progress.jobProgress.map((item) => item.elapsed_ms || 0))
  const fileNames = progress.statuses
    .map((item) => item.title || item.source_id)
    .filter(Boolean)

  return {
    sessionId,
    fileNames: fileNames.length ? fileNames : existing?.fileNames ?? progress.sourceIds,
    startedAt: existing?.startedAt ?? now - elapsedMs,
    nowMs: now,
    stage: progress.stage,
    stageIndex: uploadStageIndex(progress.stage),
    percent: progress.percent,
    message: progress.message,
    completed: progress.completed,
    total: progress.total,
    sourceIds: progress.sourceIds,
    jobIds: progress.jobIds,
    parseSummaries: progress.parseSummaries,
    done: progress.stage === 'stored',
    failed: progress.stage === 'failed',
    timedOut: false,
    cancelled: false,
  }
}

function PendingAnswerProgress({
  stepIndex,
  startedAt,
  nowMs,
  mode,
}: {
  stepIndex: number
  startedAt: number
  nowMs: number
  mode: AnswerMode
}) {
  const elapsed = Math.max(0, Math.round((nowMs - startedAt) / 1000))
  const activeStepStartedAt = startedAt + stepIndex * 2600
  const activeStepElapsed = Math.max(0, Math.round((nowMs - activeStepStartedAt) / 1000))
  const modeLabel = ANSWER_MODES.find((item) => item.id === mode)?.label ?? '回答'
  const slow = elapsed >= 90
  const activeStep = ANSWER_PROGRESS_STEPS[stepIndex] ?? ANSWER_PROGRESS_STEPS[0]
  const progress = Math.max(12, Math.round(((stepIndex + 1) / ANSWER_PROGRESS_STEPS.length) * 100))

  return (
    <div className="w-full max-w-full rounded-lg border border-border bg-card px-4 py-3 text-sm text-muted-foreground shadow-sm sm:max-w-[min(980px,calc(100%-3rem))]">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 font-medium text-foreground">
          <Loader2 className="h-4 w-4 animate-spin text-amber-600" />
          正在生成{modeLabel}
        </div>
        <span className="text-xs text-muted-foreground">{elapsed}s</span>
      </div>
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-secondary">
        <div className="h-full rounded-full bg-amber-500 transition-all" style={{ width: `${progress}%` }} />
      </div>
      <div className="mt-2 flex items-center justify-between gap-3 text-xs">
        <span className="font-medium text-foreground">{activeStep.label}</span>
        <span>{activeStepElapsed}s</span>
      </div>
      {slow && (
        <div className="mt-2 text-xs leading-5 text-amber-300">
          处理时间较长，请保持当前会话开启；结果完成后会自动显示。
        </div>
      )}
    </div>
  )
}

function UploadProgressCard({
  upload,
  onCancel,
  cancelling = false,
}: {
  upload: UploadProgressState
  onCancel?: (upload: UploadProgressState) => void
  cancelling?: boolean
}) {
  const elapsed = Math.max(0, Math.floor((upload.nowMs - upload.startedAt) / 1000))
  const canCancel = !upload.done && !upload.failed && !upload.timedOut && !upload.cancelled
  const [detailsOpen, setDetailsOpen] = useState(false)
  const activeStep = UPLOAD_PROGRESS_STEPS[upload.stageIndex] ?? UPLOAD_PROGRESS_STEPS[0]
  return (
    <div className="w-full max-w-full rounded-lg border border-border bg-card px-4 py-3 text-sm shadow-sm sm:max-w-[min(980px,calc(100%-3rem))]">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 font-medium text-foreground">
          {upload.done ? (
            <CheckCircle2 className="h-4 w-4 text-emerald-400" />
          ) : upload.cancelled ? (
            <XCircle className="h-4 w-4 text-muted-foreground" />
          ) : upload.timedOut ? (
            <AlertCircle className="h-4 w-4 text-amber-400" />
          ) : upload.failed ? (
            <AlertCircle className="h-4 w-4 text-red-400" />
          ) : (
            <Loader2 className="h-4 w-4 animate-spin text-amber-400" />
          )}
          {upload.done ? `${upload.completed} 份资料可用` : upload.message}
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          {!upload.done && <span>{Math.round(upload.percent)}%</span>}
          <span>{elapsed}s</span>
          {canCancel && (
            <button
              type="button"
              onClick={() => onCancel?.(upload)}
              disabled={cancelling}
              className="inline-flex items-center gap-1 rounded-md border border-border bg-secondary px-2 py-1 text-[11px] text-muted-foreground hover:border-red-400 hover:text-red-300 disabled:cursor-not-allowed disabled:opacity-50"
              title={upload.jobIds.length ? '取消当前资料解析任务' : '取消当前上传或等待中的解析'}
            >
              {cancelling ? <Loader2 className="h-3 w-3 animate-spin" /> : <XCircle className="h-3 w-3" />}
              取消解析
            </button>
          )}
        </div>
      </div>
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-secondary">
        <div
          className={clsx(
            'h-full rounded-full transition-all',
            upload.failed ? 'bg-red-500' : upload.cancelled ? 'bg-muted-foreground' : upload.timedOut ? 'bg-amber-400' : 'bg-amber-500',
          )}
          style={{ width: `${Math.max(6, Math.min(100, upload.percent))}%` }}
        />
      </div>
      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <span className="font-medium text-foreground">{upload.done ? '处理完成' : activeStep.label}</span>
        <button type="button" onClick={() => setDetailsOpen((value) => !value)} className="inline-flex items-center gap-1 hover:text-foreground" aria-expanded={detailsOpen}>
          处理信息
          {detailsOpen ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
        </button>
      </div>
      {detailsOpen && (
        <div className="mt-3 space-y-3 border-t border-border pt-3">
          <div className="flex flex-wrap gap-x-4 gap-y-2 text-xs text-muted-foreground">
            {UPLOAD_PROGRESS_STEPS.map((step, index) => {
              const done = index < upload.stageIndex || (upload.done && index <= upload.stageIndex)
              const active = index === upload.stageIndex && !upload.done
              return (
                <span key={step.id} className={clsx('inline-flex items-center gap-1.5', done && 'text-emerald-400', active && 'text-amber-400')}>
                  {done ? <CheckCircle2 className="h-3 w-3" /> : <span className="h-1.5 w-1.5 rounded-full bg-current" />}
                  {step.label}
                </span>
              )
            })}
          </div>
          <div className="flex flex-wrap gap-2 text-[11px] text-muted-foreground">
            {upload.fileNames.slice(0, 4).map((name) => <span key={name} className="max-w-[220px] truncate rounded-md border border-border bg-secondary px-2 py-1">{name}</span>)}
            {upload.fileNames.length > 4 && <span>另有 {upload.fileNames.length - 4} 份</span>}
          </div>
          {upload.parseSummaries.length > 0 && <div className="flex flex-wrap gap-2 text-[11px] text-muted-foreground">
            {upload.parseSummaries.slice(0, 3).map((summary) => <span key={summary.text} className="max-w-[360px] truncate" title={summary.text}>{summary.text}</span>)}
          </div>}
        </div>
      )}
    </div>
  )
}

function getApiErrorData(error: unknown): Record<string, unknown> | null {
  if (typeof error === 'object' && error !== null && 'response' in error) {
    const response = (error as { response?: { data?: unknown } }).response
    const data = response?.data
    if (typeof data === 'object' && data !== null && !Array.isArray(data)) return data as Record<string, unknown>
  }
  return null
}

function getApiErrorDetail(error: unknown): string {
  const data = getApiErrorData(error)
  if (data && 'detail' in data) {
    const detail = data.detail
    if (typeof detail === 'string') return detail
  }
  return error instanceof Error ? error.message : ''
}

function getChatGenerationErrorMessage(error: unknown): string {
  const data = getApiErrorData(error)
  if (data?.error_code === 'llm_provider_unavailable') {
    return '模型服务不可用：请检查 OpenRouter 密钥、余额、代理或稍后重试。'
  }
  const detail = getApiErrorDetail(error)
  return detail || '回答生成失败'
}

function getDecisionSaveErrorMessage(error: unknown): string {
  const detail = getApiErrorDetail(error)
  if (detail.includes('quality_report.decision_ready')) {
    return '当前回答的证据仍需复核，暂时无法保存。'
  }
  if (detail.includes('decision_guardrail.status')) {
    return '当前回答尚未达到保存条件，请补充资料或复核后重试。'
  }
  return detail ? `保存失败：${detail}` : '保存失败：请稍后重试。'
}

type GeneratedResultKind = 'briefing' | 'debate'

class GeneratedResultPersistenceError extends Error {
  resultKind: GeneratedResultKind
  resultId: string
  detail: string

  constructor(resultKind: GeneratedResultKind, resultId: string, error: unknown) {
    super('generated_result_persistence_failed')
    this.name = 'GeneratedResultPersistenceError'
    this.resultKind = resultKind
    this.resultId = resultId
    this.detail = getApiErrorDetail(error)
  }
}

function generatedResultLabel(kind: GeneratedResultKind) {
  return kind === 'briefing' ? '简报' : '委员会结果'
}

function getGeneratedResultPersistenceMessage(error: GeneratedResultPersistenceError) {
  const label = generatedResultLabel(error.resultKind)
  return `${label}已生成，但暂未保存到当前会话。请刷新后核对，避免重复提交。`
}

function MessageBubble({ message }: { message: ChatMsg }) {
  const isAssistant = message.role === 'assistant'
  const payload = message.payload && typeof message.payload === 'object' ? message.payload : {}
  const claimsUsed = typeof payload.claims_used === 'number' ? payload.claims_used : undefined
  const citations = isAssistant ? buildEvidenceCitations(payload) : []
  const qualityReport = isAssistant ? getQualityReport(payload) : null
  const displayContent = isAssistant
    ? formatAssistantAnswerForDisplay({ content: message.content, payload, generatedAt: message.created_at })
    : displayReadableText(message.content)
  const [savedSnapshotId, setSavedSnapshotId] = useState<string | null>(null)
  const [saveDecisionError, setSaveDecisionError] = useState<string | null>(null)
  const saveChatSnapshot = useSaveChatSnapshot()
  const intentAssets = Array.isArray(payload.intent_assets) ? payload.intent_assets.filter((item: unknown) => typeof item === 'string') as string[] : []
  const snapshotAsset = intentAssets[0] || (typeof payload.asset_detected === 'string' ? payload.asset_detected : undefined)
  const canSaveDecisionRecord = qualityReport?.decision_ready === true
  const saveDecisionDisabled = saveChatSnapshot.isPending || Boolean(savedSnapshotId) || !canSaveDecisionRecord
  const saveDecisionLabel = savedSnapshotId
    ? '已保存判断'
    : saveChatSnapshot.isPending
      ? '正在保存'
      : canSaveDecisionRecord
        ? '保存判断'
        : '暂不可保存'

  return (
    <div className={clsx('flex gap-2 sm:gap-3', isAssistant ? 'justify-start' : 'justify-end')}>
      {isAssistant && (
        <div className="mt-1 hidden h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-amber-500/30 bg-amber-500/10 text-amber-300 sm:flex">
          <Bot className="h-4 w-4" />
        </div>
      )}
      <article
                  className={clsx(
          'rounded-lg border px-4 py-3 text-sm shadow-sm',
          isAssistant
            ? 'w-full max-w-full border-border bg-card text-foreground sm:max-w-[min(980px,calc(100%-3rem))]'
            : 'max-w-[92%] border-amber-500 bg-amber-500 text-white sm:max-w-[min(720px,calc(100%-3rem))]',
        )}
      >
        <div
          className={clsx(
            'prose prose-sm max-w-none leading-6',
            isAssistant
            ? 'text-foreground'
              : 'prose-p:my-0 prose-strong:text-white prose-a:text-white prose-code:text-white',
          )}
        >
          {isAssistant ? (
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                p: ({ children }) => <p className="mb-3 last:mb-0">{withLocalCitations(children, citations)}</p>,
                li: ({ children }) => <li>{withLocalCitations(children, citations)}</li>,
                h2: ({ children }) => <h2 className="mt-4 text-base font-semibold">{withLocalCitations(children, citations)}</h2>,
                h3: ({ children }) => <h3 className="mt-3 text-sm font-semibold">{withLocalCitations(children, citations)}</h3>,
                strong: ({ children }) => <strong>{withLocalCitations(children, citations)}</strong>,
              }}
            >
              {displayContent}
            </ReactMarkdown>
          ) : (
            <p className="m-0 whitespace-pre-wrap">{displayContent}</p>
          )}
        </div>
        {isAssistant && (
          <EvidencePanel
            citations={citations}
            claimsUsed={claimsUsed}
            invalidCitations={payload.invalid_citations}
            stanceMismatches={payload.stance_mismatches}
            scope={payload.scope}
            qualityReport={qualityReport}
          />
        )}
        <div
          className={clsx(
            'mt-3 flex flex-wrap items-center gap-2 text-[11px]',
            isAssistant ? 'text-muted-foreground' : 'text-white/80',
          )}
        >
          <span>{formatTime(message.created_at)}</span>
          {isAssistant && typeof claimsUsed === 'number' && <span>引用证据 {claimsUsed} 条</span>}
          {isAssistant && typeof qualityReport?.duration_ms === 'number' && <span>耗时 {(qualityReport.duration_ms / 1000).toFixed(1)}s</span>}
          {isAssistant && qualityReport?.decision_ready === false && <span className="text-amber-400">需要复核</span>}
          {isAssistant && qualityReport?.decision_ready === true && <span className="text-emerald-400">可作为参考</span>}
          {isAssistant && (
            <button
              type="button"
              onClick={async () => {
                if (!canSaveDecisionRecord) return
                setSaveDecisionError(null)
                try {
                  const saved = await saveChatSnapshot.mutateAsync({ messageId: message.id, asset: snapshotAsset })
                  setSavedSnapshotId(saved.snapshot_id)
                } catch (error) {
                  setSaveDecisionError(getDecisionSaveErrorMessage(error))
                }
              }}
              disabled={saveDecisionDisabled}
              title={canSaveDecisionRecord ? '保存当前回答' : '当前回答证据不足，暂不能保存'}
              className="inline-flex items-center gap-1 rounded border border-border px-1.5 py-0.5 hover:text-amber-400 disabled:opacity-60"
            >
              <ShieldCheck className="h-3 w-3" />
              {saveDecisionLabel}
            </button>
          )}
          {isAssistant && saveDecisionError && <span className="text-red-400">{saveDecisionError}</span>}
          {isAssistant && savedSnapshotId && (
            <Link to="/review" className="inline-flex items-center gap-1 rounded border border-border px-1.5 py-0.5 hover:text-amber-400">
              进入复盘
            </Link>
          )}
        </div>
      </article>
      {!isAssistant && (
        <div className="mt-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-border bg-card text-foreground">
          <UserRound className="h-4 w-4" />
        </div>
      )}
    </div>
  )
}

function EmptyConversation() {
  return (
    <div className="mx-auto flex max-w-2xl flex-col items-center justify-center py-16 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-xl border border-amber-500/30 bg-amber-500/10 text-amber-300">
        <MessageSquarePlus className="h-5 w-5" />
      </div>
      <h2 className="mt-4 text-base font-semibold text-foreground">开始新的问答</h2>
    </div>
  )
}

export function DecisionChatFlow() {
  const [searchParams, setSearchParams] = useSearchParams()
  const requestedSessionId = searchParams.get('session')
  const { data: sessions = [], isLoading: sessionsLoading, refetch: refetchSessions } = useChatSessions(requestedSessionId)
  const [activeSessionId, setActiveSessionId] = useState<string | null>(() => searchParams.get('session'))
  const { data: messages = [], isLoading: messagesLoading, refetch: refetchMessages } = useChatMessages(activeSessionId)
  const createSession = useCreateSession()
  const deleteSession = useDeleteSession()
  const addMessage = useAddMessage()
  const sendChat = useSendChat()
  const generateBriefing = useGenerateBriefing()
  const runCommittee = useRunCommittee()
  const [draft, setDraft] = useState('')
  const [answerMode, setAnswerMode] = useState<AnswerMode>('chat')
  const [sourceIds, setSourceIds] = useState<string[]>([])
  const [status, setStatus] = useState<ComposerStatus>(null)
  const [isUploading, setIsUploading] = useState(false)
  const [showCompactHistory, setShowCompactHistory] = useState(false)
  const [pendingBySession, setPendingBySession] = useState<Record<string, PendingAnswerState>>({})
  const [uploadBySession, setUploadBySession] = useState<Record<string, UploadProgressState>>({})
  const [cancellingUploadBySession, setCancellingUploadBySession] = useState<Record<string, boolean>>({})
  const uploadAbortBySessionRef = useRef<Record<string, AbortController>>({})
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const latestMessageRef = useRef<HTMLDivElement | null>(null)
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const conversationRef = useRef<HTMLElement | null>(null)
  const hasPendingAnswer = Object.keys(pendingBySession).length > 0
  const activePending = activeSessionId ? pendingBySession[activeSessionId] : undefined
  const activeUpload = activeSessionId ? uploadBySession[activeSessionId] : undefined
  const activeUploadRunning = Boolean(activeUpload && !activeUpload.done && !activeUpload.failed && !activeUpload.timedOut && !activeUpload.cancelled)
  const isAnswerRunning = Boolean(activePending) || activeUploadRunning
  const visibleStatus = !status?.sessionId || status.sessionId === activeSessionId ? status : null

  const activeSession = useMemo(
    () => sessions.find((item) => item.id === activeSessionId) ?? null,
    [activeSessionId, sessions],
  )

  const userFacingSessions = sessions
  const visibleSessions = sessions

  const visibleMessages = useMemo(() => {
    if (!activePending || activePending.userMessagePersisted) return messages
    const optimisticUser: ChatMsg = {
      id: `pending-user-${activePending.sessionId}`,
      role: 'user',
      content: activePending.message,
      payload: { pending: true, mode: activePending.mode },
      created_at: new Date(activePending.startedAt).toISOString(),
    }
    return [...messages, optimisticUser]
  }, [activePending, messages])

  function selectSession(sessionId: string, options: { scrollToConversation?: boolean } = {}) {
    setActiveSessionId(sessionId)
    rememberActiveDecisionSessionId(sessionId)
    setSearchParams({ session: sessionId }, { replace: false })
    if (options.scrollToConversation) {
      window.requestAnimationFrame(() => {
        conversationRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      })
    }
  }

  useEffect(() => {
    const sessionFromUrl = searchParams.get('session')
    if (sessionFromUrl && sessions.some((item) => item.id === sessionFromUrl) && sessionFromUrl !== activeSessionId) {
      setActiveSessionId(sessionFromUrl)
      rememberActiveDecisionSessionId(sessionFromUrl)
      return
    }
    if (!sessionsLoading && activeSessionId && !sessions.some((item) => item.id === activeSessionId)) {
      const fallbackSession = userFacingSessions[0]
      if (fallbackSession) {
        selectSession(fallbackSession.id)
      } else {
        setActiveSessionId(null)
        rememberActiveDecisionSessionId(null)
        if (sessionFromUrl) setSearchParams({}, { replace: true })
      }
      return
    }
    if ((!activeSessionId || !sessions.some((item) => item.id === activeSessionId)) && userFacingSessions.length) {
      selectSession(userFacingSessions[0].id)
    }
  }, [activeSessionId, searchParams, sessions, sessionsLoading, userFacingSessions])

  useEffect(() => {
    window.requestAnimationFrame(() => {
      if (activePending || activeUpload) {
        scrollRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
        return
      }
      latestMessageRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  }, [visibleMessages.length, Boolean(activePending), Boolean(activeUpload)])

  useEffect(() => {
    setSourceIds(activeSessionId ? getStoredSessionSourceIds(activeSessionId) : [])
    rememberActiveDecisionSessionId(activeSessionId)
  }, [activeSessionId])

  useEffect(() => {
    if (!activeSessionId) return
    let cancelled = false

    getSessionBackgroundProgress(activeSessionId).then((progress) => {
      if (cancelled || !progress) return
      setUploadBySession((current) => ({
        ...current,
        [activeSessionId]: uploadStateFromBackgroundProgress(activeSessionId, progress, current[activeSessionId]),
      }))
    })

    return () => {
      cancelled = true
    }
  }, [activeSessionId])

  useEffect(() => {
    if (!activeSessionId || !messages.length) return
    const idsFromMessages = extractSourceIdsFromMessages(messages)
    if (!idsFromMessages.length) return
    const merged = rememberSessionSourceIds(idsFromMessages, activeSessionId)
    setSourceIds(merged)
  }, [activeSessionId, messages])

  useEffect(() => {
    if (!hasPendingAnswer) return
    const timer = window.setInterval(() => {
      const now = Date.now()
      setPendingBySession((current) =>
        Object.fromEntries(
          Object.entries(current).map(([sessionId, pending]) => [
            sessionId,
            {
              ...pending,
              nowMs: now,
              stepIndex: Math.min(Math.floor((now - pending.startedAt) / 2600), ANSWER_PROGRESS_STEPS.length - 1),
            },
          ]),
        ),
      )
    }, 1000)
    return () => window.clearInterval(timer)
  }, [hasPendingAnswer])

  useEffect(() => {
    const hasActiveUpload = Object.values(uploadBySession).some((item) => !item.done && !item.failed && !item.timedOut && !item.cancelled)
    if (!hasActiveUpload) return
    const timer = window.setInterval(() => {
      const now = Date.now()
      setUploadBySession((current) =>
        Object.fromEntries(
          Object.entries(current).map(([sessionId, upload]) => [
            sessionId,
            {
              ...upload,
              nowMs: now,
            },
          ]),
        ),
      )
    }, 1000)
    return () => window.clearInterval(timer)
  }, [uploadBySession])

  useEffect(() => {
    const pollableUploads = Object.values(uploadBySession).filter(
      (item) => item.jobIds.length && !item.done && !item.failed && !item.cancelled,
    )
    if (!pollableUploads.length) return
    let cancelled = false

    const refreshProgress = async () => {
      await Promise.allSettled(
        pollableUploads.map(async (upload) => {
          const progress = await getSessionBackgroundProgress(upload.sessionId)
          if (cancelled || !progress) return
          setUploadBySession((current) => {
            const existing = current[upload.sessionId]
            if (!existing) return current
            return {
              ...current,
              [upload.sessionId]: uploadStateFromBackgroundProgress(upload.sessionId, progress, existing),
            }
          })
        }),
      )
    }

    const timer = window.setInterval(() => {
      void refreshProgress()
    }, 5000)

    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [uploadBySession])

  async function ensureSession(seed?: string) {
    if (activeSessionId) return activeSessionId
    const title = seed?.trim().slice(0, 36) || '新的决策问答'
    const created = await createSession.mutateAsync({ title, mode: 'auto', asset: '' })
    selectSession(created.id)
    await refetchSessions()
    return created.id
  }

  async function handleNewSession() {
    setStatus(null)
    const created = await createSession.mutateAsync({ title: '新的决策问答', mode: 'auto', asset: '' })
    selectSession(created.id, { scrollToConversation: true })
    await refetchSessions()
  }

  async function handleDeleteSession(sessionId: string) {
    await deleteSession.mutateAsync(sessionId)
    if (activeSessionId === sessionId) {
      const next = sessions.find((item) => item.id !== sessionId)
      if (next) {
        selectSession(next.id)
      } else {
        setActiveSessionId(null)
        rememberActiveDecisionSessionId(null)
        setSearchParams({}, { replace: true })
      }
    }
    await refetchSessions()
  }

  async function handleCancelUpload(upload: UploadProgressState) {
    setCancellingUploadBySession((current) => ({ ...current, [upload.sessionId]: true }))
    try {
      const jobIds = upload.jobIds.filter(Boolean)
      if (jobIds.length) {
        await Promise.allSettled(jobIds.map((jobId) => uploadApi.cancelJob(jobId, 'user_cancelled_from_chat_upload')))
      }
    } finally {
      uploadAbortBySessionRef.current[upload.sessionId]?.abort()
      setUploadBySession((current) => ({
        ...current,
        [upload.sessionId]: {
          ...upload,
          nowMs: Date.now(),
          stage: 'cancelled',
          stageIndex: uploadStageIndex('cancelled'),
          percent: 100,
          message: '已取消资料解析，原始上传记录已保留',
          done: false,
          failed: false,
          cancelled: true,
        },
      }))
      setCancellingUploadBySession((current) => ({ ...current, [upload.sessionId]: false }))
      setStatus({ tone: 'idle', sessionId: upload.sessionId, text: '已取消资料解析；原始记录保留，可重新上传或稍后到维护页查看。' })
    }
  }

  async function handleFiles(files: FileList | File[]) {
    const selected = Array.from(files)
    if (!selected.length) return
    setIsUploading(true)
    const startedAt = Date.now()
    let sessionId = activeSessionId
    try {
      sessionId = await ensureSession(selected[0]?.name)
      const fileNames = selected.map((file) => file.name || '未命名文件')
      setUploadBySession((current) => ({
        ...current,
        [sessionId as string]: {
          sessionId: sessionId as string,
          fileNames,
          startedAt,
          nowMs: startedAt,
          stage: 'uploading',
          stageIndex: 0,
          percent: 8,
          message: `正在接收 ${selected.length} 份资料`,
          completed: 0,
          total: selected.length,
          sourceIds: [],
          jobIds: [],
          parseSummaries: [],
          done: false,
          failed: false,
          timedOut: false,
          cancelled: false,
        },
      }))
      setStatus({ tone: 'working', sessionId, text: `正在上传并解析 ${selected.length} 个文件...` })
      const controller = new AbortController()
      uploadAbortBySessionRef.current[sessionId as string] = controller

      const result = await uploadSessionFiles(selected, {
        sessionId,
        signal: controller.signal,
        waitForCompletion: true,
        onProgress: (progress) => {
          setUploadBySession((current) => ({
            ...current,
            [sessionId as string]: {
              ...(current[sessionId as string] ?? {
                sessionId: sessionId as string,
                fileNames,
                startedAt,
                nowMs: Date.now(),
                sourceIds: [],
                jobIds: [],
                parseSummaries: [],
                completed: 0,
                total: selected.length,
                failed: false,
                timedOut: false,
                cancelled: false,
              }),
              fileNames,
              stage: progress.stage,
              stageIndex: uploadStageIndex(progress.stage),
              percent: progress.percent,
              message: progress.message,
              completed: progress.completed,
              total: progress.total || selected.length,
              sourceIds: progress.sourceIds,
              jobIds: progress.jobIds ?? current[sessionId as string]?.jobIds ?? [],
              parseSummaries: progress.parseSummaries ?? current[sessionId as string]?.parseSummaries ?? [],
              nowMs: Date.now(),
              done: progress.stage === 'stored',
              failed: progress.stage === 'failed',
              timedOut: progress.stage === 'timeout',
              cancelled: progress.stage === 'cancelled',
            },
          }))
        },
      })
      const nextSourceIds = getStoredSessionSourceIds(sessionId)
      setSourceIds(nextSourceIds)
      const finalTimedOut = result.timed_out === true || result.status === 'background_processing'
      const finalFailed = result.statuses?.some((item) => {
        const normalized = item.status.toLowerCase()
        return normalized.includes('failed') || normalized.includes('error')
      })
      if (finalTimedOut) {
        setUploadBySession((current) => ({
          ...current,
          [sessionId as string]: {
            ...(current[sessionId as string] ?? {
              sessionId: sessionId as string,
              fileNames,
              startedAt,
              nowMs: Date.now(),
              stage: 'timeout',
              stageIndex: uploadStageIndex('timeout'),
              percent: 96,
              message: '资料仍在后台处理，稍后可刷新查看结果',
              completed: 0,
              total: selected.length,
              sourceIds: nextSourceIds,
              jobIds: [],
              parseSummaries: current[sessionId as string]?.parseSummaries ?? [],
              done: false,
              failed: false,
              cancelled: false,
            }),
            stage: 'timeout',
            stageIndex: uploadStageIndex('timeout'),
            percent: 96,
            message: '资料仍在后台处理，稍后可刷新查看结果',
            nowMs: Date.now(),
            done: false,
            failed: false,
            timedOut: true,
            cancelled: false,
          },
        }))
      }
      await recordSessionUploadReceipt(sessionId, result)
      await Promise.allSettled([refetchMessages(), refetchSessions()])
      setStatus({
        tone: finalFailed ? 'error' : finalTimedOut ? 'warning' : 'success',
        sessionId,
        text: finalFailed
          ? '资料已接收，但部分文件处理失败，请展开审计页查看。'
          : finalTimedOut
            ? '资料已登记并转入后台处理，当前等待超时；可以稍后刷新查看入库结果。'
          : `资料已解析、切片、向量化并入库，本次新增 ${result.source_ids?.length ?? selected.length} 个资料源。`,
      })
    } catch (error) {
      const cancelled = typeof error === 'object' && error !== null && (
        ('name' in error && (error.name === 'AbortError' || error.name === 'CanceledError')) ||
        ('code' in error && error.code === 'ERR_CANCELED')
      )
      const detail = cancelled ? '已取消资料解析，原始上传记录已保留' : error instanceof Error ? error.message : '上传或解析失败'
      if (sessionId) {
        setUploadBySession((current) => ({
          ...current,
          [sessionId as string]: {
            ...(current[sessionId as string] ?? {
              sessionId: sessionId as string,
              fileNames: selected.map((file) => file.name || '未命名文件'),
              startedAt,
              nowMs: Date.now(),
              sourceIds: [],
              jobIds: [],
              parseSummaries: [],
              completed: 0,
              total: selected.length,
              timedOut: false,
              cancelled: false,
            }),
            stage: cancelled ? 'cancelled' : 'failed',
            stageIndex: uploadStageIndex(cancelled ? 'cancelled' : 'failed'),
            percent: 100,
            message: detail,
            done: false,
            failed: !cancelled,
            cancelled,
          },
        }))
      }
      setStatus({ tone: cancelled ? 'idle' : 'error', sessionId: sessionId ?? undefined, text: detail })
    } finally {
      if (sessionId) {
        delete uploadAbortBySessionRef.current[sessionId]
        setCancellingUploadBySession((current) => ({ ...current, [sessionId as string]: false }))
      }
      setIsUploading(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  async function handleSend() {
    const message = draft.trim()
    if (!message || isAnswerRunning) return

    const mode = answerMode
    let sessionId = activeSessionId
    try {
      sessionId = await ensureSession(message)
      const effectiveSourceIds = getStoredSessionSourceIds(sessionId as string)
      const now = Date.now()
      setDraft('')
      setPendingBySession((current) => ({
        ...current,
        [sessionId as string]: {
          sessionId: sessionId as string,
          message,
          mode,
          startedAt: now,
          nowMs: now,
          stepIndex: 0,
          userMessagePersisted: false,
        },
      }))
      setStatus({
        tone: 'working',
        sessionId,
        text:
          mode === 'chat'
            ? '正在检索证据并生成普通问答...'
            : mode === 'briefing'
              ? '正在生成研究简报...'
              : '正在组织委员会讨论...',
      })

      if (mode === 'chat') {
        await sendChat.mutateAsync({
          sessionId,
          message,
          history: buildHistory(messages),
          scopeMode: effectiveSourceIds.length ? 'selected_sources' : undefined,
          sourceIds: effectiveSourceIds,
        })
      } else if (mode === 'briefing') {
        await addMessage.mutateAsync({
          sessionId,
          role: 'user',
          content: message,
          payload: { mode },
        })
        setPendingBySession((current) => ({
          ...current,
          [sessionId as string]: {
            ...current[sessionId as string],
            userMessagePersisted: true,
          },
        }))
        await refetchMessages()
        const briefing = await generateBriefing.mutateAsync({
          question: message,
          history: buildHistory(messages),
          source_ids: effectiveSourceIds,
        })
        try {
          await addMessage.mutateAsync({
            sessionId,
            role: 'assistant',
            content: formatBriefingAnswer(briefing),
            payload: {
              mode,
              briefing_id: briefing.id,
              model_used: briefing.model_used,
              cost_usd: briefing.cost_usd,
              duration_ms: briefing.duration_ms,
              decision_guardrail: briefing.decision_guardrail,
              quality_report: briefing.quality_report,
              quality_filters: briefing.quality_filters,
              llm_trace: briefing.llm_trace,
              timing_trace: briefing.timing_trace,
              data_sources: briefing.data_sources,
              scope: briefing.scope ?? {
                mode: effectiveSourceIds.length ? 'selected_sources' : 'global',
                source_ids: effectiveSourceIds,
                resolved_from: effectiveSourceIds.length ? 'session_history' : 'default_global',
              },
            },
          })
        } catch (error) {
          throw new GeneratedResultPersistenceError('briefing', briefing.id, error)
        }
      } else {
        await addMessage.mutateAsync({
          sessionId,
          role: 'user',
          content: message,
          payload: { mode },
        })
        setPendingBySession((current) => ({
          ...current,
          [sessionId as string]: {
            ...current[sessionId as string],
            userMessagePersisted: true,
          },
        }))
        await refetchMessages()
        const result = await runCommittee.mutateAsync({
          question: message,
          asset: inferAssetFromQuestion(message),
          num_rounds: 1,
          source_ids: effectiveSourceIds,
        })
        try {
          await addMessage.mutateAsync({
            sessionId,
            role: 'assistant',
            content: formatDebateAnswer(result),
            payload: {
              mode,
              debate_id: result.debate_id,
              cost_usd: result.cost_usd,
              duration_ms: result.duration_ms,
              activated_seats: result.activated_seats,
              decision_guardrail: result.decision_guardrail,
              quality_report: result.quality_report,
              quality_filters: result.quality_filters,
              llm_trace: result.llm_trace,
              timing_trace: result.timing_trace,
              data_sources: result.data_sources,
              scope: result.scope ?? {
                mode: effectiveSourceIds.length ? 'selected_sources' : 'global',
                source_ids: effectiveSourceIds,
                resolved_from: effectiveSourceIds.length ? 'session_history' : 'default_global',
              },
            },
          })
        } catch (error) {
          throw new GeneratedResultPersistenceError('debate', result.debate_id, error)
        }
      }

      await Promise.all([refetchMessages(), refetchSessions()])
      setStatus({ tone: 'success', sessionId, text: '回答已生成并写入当前会话。' })
    } catch (error) {
      if (error instanceof GeneratedResultPersistenceError) {
        await Promise.allSettled([refetchMessages(), refetchSessions()])
        setStatus({
          tone: 'warning',
          sessionId: sessionId ?? undefined,
          text: getGeneratedResultPersistenceMessage(error),
        })
        return
      }
      const detail = getChatGenerationErrorMessage(error)
      setStatus({ tone: 'error', sessionId: sessionId ?? undefined, text: detail })
      setDraft(message)
    } finally {
      if (sessionId) {
        setPendingBySession((current) => {
          const next = { ...current }
          delete next[sessionId as string]
          return next
        })
      }
    }
  }

  function handleDrop(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault()
    if (event.dataTransfer.files?.length) {
      void handleFiles(event.dataTransfer.files)
    }
  }

  return (
    <div className="decision-chat-flow flex h-full min-h-0 flex-col bg-background text-foreground">
      <header className="shrink-0 border-b border-border bg-card/95 px-4 py-2 backdrop-blur">
        <div className="flex items-center justify-between gap-3">
          <h1 className="text-base font-semibold text-foreground">问答</h1>
          <Link to="/knowledge" className="inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs font-medium text-muted-foreground hover:bg-secondary hover:text-foreground">
            <BookOpen className="h-3.5 w-3.5" />
            {sourceIds.length ? `${sourceIds.length} 份资料` : '资料'}
          </Link>
        </div>
      </header>

      <main className="grid min-h-0 flex-1 grid-cols-1 bg-background xl:grid-cols-[240px_minmax(0,1fr)]">
        <aside className="hidden min-h-0 border-border bg-secondary/40 p-3 xl:block xl:border-r">
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-sm font-semibold text-foreground">对话</h2>
            <button
              type="button"
              onClick={() => void handleNewSession()}
              className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-border bg-card text-foreground hover:border-amber-400 hover:text-amber-400 disabled:opacity-50"
              disabled={createSession.isPending}
              title="新建会话"
            >
              {createSession.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
            </button>
          </div>

          <div className="mt-3 max-h-[calc(100vh-210px)] space-y-2 overflow-y-auto pr-1">
            {sessionsLoading && (
              <div className="flex items-center gap-2 rounded-lg border border-border bg-card p-3 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                正在读取历史
              </div>
            )}
            {!sessionsLoading && !visibleSessions.length && (
              <div className="rounded-lg border border-dashed border-border bg-card p-4 text-sm text-muted-foreground">
                暂无历史。发送第一条问题后会自动保存。
              </div>
            )}
            {visibleSessions.map((session) => {
              const active = session.id === activeSessionId
              return (
                <div
                  key={session.id}
                  className={clsx(
                    'group relative rounded-lg border bg-card shadow-sm transition',
                    active
                      ? 'border-amber-500/60 bg-amber-500/10 ring-1 ring-amber-500/20'
                      : 'border-border hover:border-amber-500/30',
                  )}
                >
                  <button
                    type="button"
                    className="block w-full rounded-lg px-3 py-3 pr-10 text-left"
                    onClick={() => selectSession(session.id, { scrollToConversation: true })}
                    aria-current={active ? 'page' : undefined}
                  >
                    <div className="flex items-center justify-between gap-3">
                      <span className="truncate text-sm font-medium text-foreground">
                        {sessionTitle(session)}
                      </span>
                      {active ? (
                        <span className="shrink-0 rounded-full bg-amber-500 px-2 py-0.5 text-[11px] font-semibold text-white">
                          当前
                        </span>
                      ) : (
                        <span className="shrink-0 text-[11px] text-muted-foreground">{session.message_count ?? 0} 条</span>
                      )}
                    </div>
                    <div className="mt-1 flex items-center justify-between gap-2 text-xs text-muted-foreground">
                      <span>{formatTime(session.updated_at)}</span>
                      <span>{active ? `${session.message_count ?? 0} 条消息` : '打开会话'}</span>
                    </div>
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleDeleteSession(session.id)}
                    className="absolute right-2 top-2 inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground opacity-0 hover:bg-red-500/10 hover:text-red-300 group-hover:opacity-100 focus:opacity-100"
                    title="删除当前会话"
                    aria-label="删除当前会话"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              )
            })}
          </div>
        </aside>

        <section
          ref={conversationRef}
          data-testid="decision-chat-conversation"
          className="flex min-h-0 flex-col"
          onDragOver={(event) => event.preventDefault()}
          onDrop={handleDrop}
        >
          <div className="flex shrink-0 items-center justify-between gap-3 border-b border-border bg-card px-5 py-2.5">
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold text-foreground">
                {activeSession ? sessionTitle(activeSession) : '新的决策问答'}
              </div>
              <div className="text-xs text-muted-foreground">
                {activeSession ? `更新于 ${formatTime(activeSession.updated_at)}` : '发送消息后自动创建会话'}
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                data-testid="decision-chat-history-toggle"
                onClick={() => setShowCompactHistory((value) => !value)}
                aria-expanded={showCompactHistory}
                className="inline-flex h-9 items-center gap-2 rounded-lg border border-border bg-secondary px-3 text-xs font-medium text-muted-foreground hover:border-amber-400 hover:text-amber-400 xl:hidden"
                title="历史对话"
              >
                <MessageSquarePlus className="h-4 w-4" />
                历史
              </button>
              <button
                type="button"
                onClick={() => {
                  void refetchSessions()
                  void refetchMessages()
                  setSourceIds(activeSessionId ? getStoredSessionSourceIds(activeSessionId) : [])
                }}
                className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-border bg-secondary text-muted-foreground hover:border-amber-400 hover:text-amber-400"
                title="刷新"
              >
                <RefreshCw className="h-4 w-4" />
              </button>
            </div>
          </div>

          {showCompactHistory && (
            <div data-testid="decision-chat-history-compact" className="border-b border-border bg-secondary/50 p-3 xl:hidden">
              <div className="mb-2 flex items-center justify-between gap-2">
                <div className="text-xs font-semibold text-foreground">对话</div>
                <button
                  type="button"
                  onClick={() => void handleNewSession()}
                  className="inline-flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-1.5 text-xs font-medium text-foreground hover:border-amber-400 hover:text-amber-400 disabled:opacity-50"
                  disabled={createSession.isPending}
                >
                  {createSession.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}
                  新会话
                </button>
              </div>
              <div className="max-h-48 space-y-2 overflow-y-auto pr-1">
                {sessionsLoading && (
                  <div className="rounded-lg border border-border bg-card px-3 py-2 text-xs text-muted-foreground">正在读取历史</div>
                )}
                {!sessionsLoading && !visibleSessions.length && (
                  <div className="rounded-lg border border-dashed border-border bg-card px-3 py-2 text-xs text-muted-foreground">
                    暂无历史。发送第一条问题后会自动保存。
                  </div>
                )}
                {visibleSessions.slice(0, 8).map((session) => {
                  const active = session.id === activeSessionId
                  return (
                    <button
                      key={session.id}
                      type="button"
                      onClick={() => {
                        setShowCompactHistory(false)
                        selectSession(session.id, { scrollToConversation: true })
                      }}
                      className={clsx(
                        'block w-full rounded-lg border bg-card px-3 py-2 text-left transition',
                        active
                          ? 'border-amber-500/60 bg-amber-500/10'
                          : 'border-border hover:border-amber-500/30',
                      )}
                    >
                      <div className="flex items-center justify-between gap-3">
                        <span className="truncate text-sm font-medium text-foreground">{sessionTitle(session)}</span>
                        <span className="shrink-0 text-[11px] text-muted-foreground">{session.message_count ?? 0} 条</span>
                      </div>
                      <div className="mt-1 text-xs text-muted-foreground">{formatTime(session.updated_at)}</div>
                    </button>
                  )
                })}
              </div>
            </div>
          )}

          <div className="min-h-0 flex-1 overflow-y-auto bg-background px-4 py-4 xl:px-8">
            {messagesLoading && activeSessionId && (
              <div className="flex items-center gap-2 rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                正在读取当前会话
              </div>
            )}
            {!messagesLoading && !visibleMessages.length && <EmptyConversation />}
            <div className="space-y-5">
              {visibleMessages.map((message, index) => (
                <div
                  key={message.id}
                  ref={index === visibleMessages.length - 1 ? latestMessageRef : undefined}
                  data-testid="decision-chat-message"
                  data-message-role={message.role}
                >
                  <MessageBubble message={message} />
                </div>
              ))}
              {activeUpload && (
                <div className="flex gap-3">
                  <div className="mt-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-amber-500/30 bg-amber-500/10 text-amber-300">
                    <UploadCloud className="h-4 w-4" />
                  </div>
                  <UploadProgressCard
                    upload={activeUpload}
                    onCancel={handleCancelUpload}
                    cancelling={Boolean(cancellingUploadBySession[activeUpload.sessionId])}
                  />
                </div>
              )}
              {activePending && (
                <div className="flex gap-3">
                  <div className="mt-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-amber-500/30 bg-amber-500/10 text-amber-300">
                    <Bot className="h-4 w-4" />
                  </div>
                  <PendingAnswerProgress
                    stepIndex={activePending.stepIndex}
                    startedAt={activePending.startedAt}
                    nowMs={activePending.nowMs}
                    mode={activePending.mode}
                  />
                </div>
              )}
              <div ref={scrollRef} />
            </div>
          </div>

          <div className="shrink-0 border-t border-border bg-card p-3">
            {visibleStatus && (
              <div
                className={clsx(
                  'mb-2 flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs',
                  visibleStatus.tone === 'working' &&
                    'border-blue-500/30 bg-blue-500/10 text-blue-200',
                visibleStatus.tone === 'success' &&
                    'border-emerald-500/30 bg-emerald-500/10 text-emerald-200',
                  visibleStatus.tone === 'warning' &&
                    'border-amber-500/30 bg-amber-500/10 text-amber-100',
                  visibleStatus.tone === 'error' &&
                    'border-red-500/30 bg-red-500/10 text-red-200',
                  visibleStatus.tone === 'idle' &&
                    'border-border bg-secondary text-muted-foreground',
                )}
              >
                {visibleStatus.tone === 'working' && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                {visibleStatus.tone === 'success' && <CheckCircle2 className="h-3.5 w-3.5" />}
                {visibleStatus.tone === 'warning' && <AlertCircle className="h-3.5 w-3.5" />}
                {visibleStatus.tone === 'error' && <AlertCircle className="h-3.5 w-3.5" />}
                {visibleStatus.text}
              </div>
            )}

            <div className="rounded-lg border border-border bg-secondary/40 p-2">
              <div
                role="group"
                aria-label="回答模式"
                className="mb-1.5 flex flex-wrap items-center gap-2 border-b border-border px-1 pb-1.5"
              >
                {ANSWER_MODES.map((mode) => {
                  const selected = answerMode === mode.id
                  return (
                    <button
                      key={mode.id}
                       type="button"
                       data-testid={`decision-chat-mode-${mode.id}`}
                       aria-pressed={selected}
                       onClick={() => setAnswerMode(mode.id)}
                      disabled={isAnswerRunning}
                      title={mode.description}
                      className={clsx(
                        'inline-flex items-center rounded-lg border px-3 py-1.5 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-60',
                        selected
                          ? 'border-amber-400 bg-amber-500 text-white'
                          : 'border-border bg-card text-muted-foreground hover:border-amber-400 hover:text-amber-400',
                      )}
                    >
                      {mode.label}
                    </button>
                  )
                })}
                <span className="sr-only">
                  当前由用户手动选择回答模式；普通问答仍会做资产、时间和检索范围识别。
                </span>
              </div>
              <textarea
                data-testid="decision-chat-input"
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault()
                    void handleSend()
                  }
                }}
                placeholder="输入问题，或把文件拖到这里后再提问..."
                rows={2}
                className="block max-h-32 min-h-[58px] w-full resize-none border-0 bg-transparent px-2 py-1.5 text-sm leading-6 text-foreground outline-none placeholder:text-muted-foreground"
              />
              <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border px-1 pt-1.5">
                <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <input
                    ref={fileInputRef}
                    type="file"
                    multiple
                    className="hidden"
                    onChange={(event) => {
                      if (event.target.files) void handleFiles(event.target.files)
                    }}
                  />
                  <button
                    type="button"
                    data-testid="decision-chat-upload"
                    aria-label="上传资料"
                    title="上传资料"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={isUploading || isAnswerRunning}
                    className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-border bg-card text-foreground hover:border-amber-400 hover:text-amber-400 disabled:opacity-50"
                  >
                    {isUploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Paperclip className="h-4 w-4" />}
                  </button>
                </div>
                <button
                  type="button"
                  data-testid="decision-chat-send"
                  aria-label="发送"
                  title="发送"
                  onClick={() => void handleSend()}
                  disabled={!draft.trim() || isAnswerRunning}
                  className="inline-flex h-9 w-9 items-center justify-center rounded-lg bg-amber-500 text-white shadow-sm hover:bg-amber-600 disabled:cursor-not-allowed disabled:bg-secondary disabled:text-muted-foreground"
                >
                  {isAnswerRunning ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                </button>
              </div>
            </div>
          </div>
        </section>
      </main>
    </div>
  )
}

export default DecisionChatFlow
