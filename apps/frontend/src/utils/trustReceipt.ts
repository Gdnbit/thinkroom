export type TrustTone = 'success' | 'warning' | 'neutral'

export type TrustChip = {
  label: string
  tone: TrustTone
}

export type TrustReceipt = {
  tone: TrustTone
  title: string
  subtitle: string
  mustReview: boolean
  chips: TrustChip[]
  answerRevision?: {
    qualityReportTarget?: string
    finalAnswerStage?: string
    guardrailStatus?: string
    finalAnswerModifiedByGuardrail: boolean
    finalAnswerDiffersFromQualityTarget: boolean
    evaluatedAnswerHash?: string
    finalAnswerHash?: string
  }
  quality: {
    decisionReady?: boolean
    warningCount: number
    referenceTime?: string
    durationMs?: number
    citationCount?: number
    usedCitationCount?: number
    unusedCitationCount?: number
    directionConflictCitationCount?: number
    uncitedMaterialClaimCount?: number
    uncitedMaterialClaims: string[]
    fieldEvidenceTotal?: number
    fieldEvidenceSupported?: number
    fieldEvidenceInsufficient?: number
    warnings: string[]
    warningCodes: string[]
    evidenceSufficiency?: string
    insufficientEvidenceReason?: string
  }
  guardrail?: {
    status: string
    originalAction?: string
    effectiveAction?: string
    reasons: string[]
    actionText: string
  }
  trace?: {
    callCount: number
    models: string[]
    requestedModels: string[]
    configuredFallbackModels: string[]
    durationMs?: number
    mock: boolean
    errorType?: string
    error?: string
    integrityRetryAttempted?: boolean
    integrityRetrySucceeded?: boolean
    integrityPruneSucceeded?: boolean
    integrityFallbackApplied?: boolean
  }
  timing?: {
    totalMs?: number
    retrievalMs?: number
    modelCallsMs?: number
    otherProcessingMs?: number
    phases: Array<{ key: string; label: string; durationMs: number }>
  }
}

export type TrustTraceTarget = {
  relatedObjectType: 'chat_message' | 'briefing' | 'debate'
  relatedObjectId: string
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function readString(value: unknown, fallback = '') {
  return typeof value === 'string' ? value : fallback
}

function readNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

function readDurationMs(value: unknown): number | undefined {
  const duration = readNumber(value)
  return duration !== undefined && duration >= 0 ? duration : undefined
}

function readStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

function unique(values: Array<string | undefined>) {
  return Array.from(new Set(values.map((item) => (item ?? '').trim()).filter(Boolean)))
}

export function llmErrorTypeLabel(errorType: string | undefined) {
  switch ((errorType ?? '').trim()) {
    case 'missing_api_key':
      return '未配置模型 Key'
    case 'client_unavailable':
      return '模型客户端不可用'
    case 'auth_error':
      return '模型认证失败'
    case 'billing_error':
      return '模型余额/额度不足'
    case 'permission_or_region':
      return '模型权限或区域受限'
    case 'timeout':
      return '模型调用超时'
    case 'rate_limited':
      return '模型限流'
    case 'upstream_unavailable':
      return '模型服务不可用'
    case 'bad_request':
      return '模型请求异常'
    case 'network_error':
      return '网络或代理异常'
    case 'empty_response':
      return '模型空响应'
    case 'response_parse_error':
      return '模型响应解析失败'
    case 'output_integrity_failed':
      return '输出完整性失败'
    case 'output_integrity_retry_failed':
      return '输出重写失败'
    case 'insufficient_evidence':
      return '证据不足未调用模型'
    default:
      return '模型异常'
  }
}

export function insufficientEvidenceReasonLabel(reason: string | undefined) {
  switch ((reason ?? '').trim()) {
    case 'selected_sources_query_mismatch':
      return '资料不匹配'
    case 'selected_sources_no_usable_evidence':
      return '资料尚未解析'
    case 'selected_sources_actionable_question_requires_directional_evidence':
      return '缺少方向性证据'
    case 'selected_sources_temporal_question_requires_time_window_evidence':
      return '缺少时间窗口证据'
    case 'selected_sources_temporal_evidence_is_stale_for_current_question':
      return '证据时效过旧'
    case 'selected_sources_temporal_horizon_mismatch':
      return '时间跨度不匹配'
    case 'selected_sources_claim_validity_window_mismatch':
      return '结构化观点未生效/已过期'
    case 'selected_sources_claim_temporal_horizon_mismatch':
      return '结构化观点周期不匹配'
    default:
      return '证据不足'
  }
}

export function insufficientEvidenceReasonDetail(reason: string | undefined) {
  switch ((reason ?? '').trim()) {
    case 'selected_sources_no_usable_evidence':
      return '选定资料尚未解析完成、尚未产生可用文本，或处理失败；系统不会把它包装成可引用证据。'
    case 'selected_sources_query_mismatch':
      return '选定资料与本次问题不匹配，不能仅凭这些资料给出方向性结论。'
    case 'selected_sources_actionable_question_requires_directional_evidence':
      return '选定资料提到了相关对象，但没有足够的涨跌方向、操作依据或交易建议证据。'
    case 'selected_sources_temporal_question_requires_time_window_evidence':
      return '选定资料提到了相关对象和方向，但没有明确日期、近期性或预测周期，不能支撑本次时间窗口。'
    case 'selected_sources_temporal_evidence_is_stale_for_current_question':
      return '选定资料有时间锚和方向性内容，但时间锚点过旧、时效性不足，不能支撑当前或下周的判断。'
    case 'selected_sources_temporal_horizon_mismatch':
      return '选定资料有时间锚和方向性内容，但资料中的预测周期与本次问题要求的时间跨度不一致。'
    case 'selected_sources_claim_validity_window_mismatch':
      return '选定资料已形成结构化观点，但结构化观点尚未生效或已经超过有效期，不能支撑本次判断。'
    case 'selected_sources_claim_temporal_horizon_mismatch':
      return '选定资料已形成结构化观点，但结构化观点的时间跨度与本次问题要求不一致。'
    default:
      return '当前选定资料不足以支撑该问题的判断。'
  }
}

function parseQuality(payload: Record<string, unknown>): TrustReceipt['quality'] {
  const raw = isRecord(payload.quality_report) ? payload.quality_report : {}
  const evidence = isRecord(raw.evidence) ? raw.evidence : {}
  const fieldEvidence = isRecord(evidence.field_evidence) ? evidence.field_evidence : {}
  const fieldEvidenceItems = Object.values(fieldEvidence).filter(isRecord)
  const attributableFieldStatuses = new Set([
    'supported',
    'inference_with_evidence',
    'inference_input_basis',
    'scope_assessment',
    'model_derived_assessment',
    'policy_controlled',
  ])
  const fieldEvidenceSupported = fieldEvidenceItems.filter((item) => (
    attributableFieldStatuses.has(readString(item.status))
  )).length
  const fieldEvidenceInsufficient = fieldEvidenceItems.filter((item) => readString(item.status) === 'insufficient').length
  const filters = isRecord(payload.quality_filters) ? payload.quality_filters : {}
  const warnings = Array.isArray(raw.warnings)
    ? raw.warnings
      .filter(isRecord)
      .map((item) => readString(item.message))
      .filter(Boolean)
    : []
  const warningCodes = Array.isArray(raw.warnings)
    ? raw.warnings
      .filter(isRecord)
      .map((item) => readString(item.code))
      .filter(Boolean)
    : []

  return {
    decisionReady: typeof raw.decision_ready === 'boolean' ? raw.decision_ready : undefined,
    warningCount: warnings.length,
    referenceTime: readString(raw.reference_time) || undefined,
    durationMs: readNumber(raw.duration_ms),
    citationCount: readNumber(evidence.citation_count),
    usedCitationCount: readNumber(evidence.used_citation_count),
    unusedCitationCount: readNumber(evidence.unused_citation_count),
    directionConflictCitationCount: readNumber(evidence.direction_conflict_citation_count),
    uncitedMaterialClaimCount: readNumber(evidence.uncited_material_claim_count),
    uncitedMaterialClaims: readStringArray(evidence.uncited_material_claims),
    fieldEvidenceTotal: fieldEvidenceItems.length || undefined,
    fieldEvidenceSupported: fieldEvidenceItems.length ? fieldEvidenceSupported : undefined,
    fieldEvidenceInsufficient: fieldEvidenceItems.length ? fieldEvidenceInsufficient : undefined,
    warnings,
    warningCodes,
    evidenceSufficiency: readString(filters.evidence_sufficiency) || undefined,
    insufficientEvidenceReason: readString(filters.insufficient_evidence_reason) || undefined,
  }
}

function parseGuardrail(payload: Record<string, unknown>): TrustReceipt['guardrail'] | undefined {
  if (!isRecord(payload.decision_guardrail)) return undefined
  const raw = payload.decision_guardrail
  const status = readString(raw.status, 'unknown')
  const originalAction = readString(raw.original_action) || undefined
  const effectiveAction = readString(raw.effective_action) || undefined
  const reasons = readStringArray(raw.reasons)
  const actionText = status === 'downgraded' && originalAction && effectiveAction
    ? `建议已从 ${originalAction} 降级为 ${effectiveAction}`
    : status === 'blocked_output'
      ? '异常输出已被阻断并替换'
    : effectiveAction
      ? `有效建议：${effectiveAction}`
      : status === 'passed'
        ? '未触发操作性建议'
        : '未触发操作性建议'

  return {
    status,
    originalAction,
    effectiveAction,
    reasons,
    actionText,
  }
}

function parseAnswerRevision(payload: Record<string, unknown>): TrustReceipt['answerRevision'] | undefined {
  const qualityReport = isRecord(payload.quality_report) ? payload.quality_report : {}
  if (!isRecord(qualityReport.answer_revision)) return undefined
  const raw = qualityReport.answer_revision
  return {
    qualityReportTarget: readString(raw.quality_report_target) || undefined,
    finalAnswerStage: readString(raw.final_answer_stage) || undefined,
    guardrailStatus: readString(raw.guardrail_status) || undefined,
    finalAnswerModifiedByGuardrail: raw.final_answer_modified_by_guardrail === true,
    finalAnswerDiffersFromQualityTarget: raw.final_answer_differs_from_quality_target === true,
    evaluatedAnswerHash: readString(raw.evaluated_answer_hash) || undefined,
    finalAnswerHash: readString(raw.final_answer_hash) || undefined,
  }
}

function parseTrace(payload: Record<string, unknown>): TrustReceipt['trace'] | undefined {
  if (!isRecord(payload.llm_trace)) return undefined
  const raw = payload.llm_trace
  const calls = Array.isArray(raw.calls) ? raw.calls.filter(isRecord) : []

  if (calls.length > 0) {
    const models = unique(calls.map((call) => readString(call.model)))
    const requestedModels = unique(calls.map((call) => readString(call.requested_model)))
    const configuredFallbackModels = unique(calls.flatMap((call) => readStringArray(call.configured_fallback_models)))
    const durationMs = calls.reduce((sum, call) => sum + (readNumber(call.duration_ms) ?? 0), 0)
    const integrityRetrySucceeded = raw.integrity_retry_succeeded === true
    const integrityPruneSucceeded = raw.integrity_prune_succeeded === true
    const integrityFallbackApplied = raw.integrity_fallback_applied === true
    const integrityRecovered = integrityRetrySucceeded || integrityPruneSucceeded || integrityFallbackApplied
    const callErrorTypes = calls.map((call) => readString(call.error_type)).filter(Boolean)
    const callErrors = calls.map((call) => readString(call.error)).filter(Boolean)
    const rawErrorType = readString(raw.error_type)
    const errorType = (
      callErrorTypes.find((item) => !(integrityRecovered && item === 'output_integrity_failed'))
      || (integrityRecovered && rawErrorType === 'output_integrity_failed' ? '' : rawErrorType)
    )
    const error = (
      callErrors.find((item) => !(integrityRecovered && item === 'output_integrity_failed'))
      || undefined
    )
    return {
      callCount: readNumber(raw.call_count) ?? calls.length,
      models,
      requestedModels,
      configuredFallbackModels,
      durationMs: durationMs || undefined,
      mock: calls.some((call) => call.mock === true),
      errorType: errorType || undefined,
      error: error || undefined,
      integrityRetryAttempted: raw.integrity_retry_attempted === true,
      integrityRetrySucceeded,
      integrityPruneSucceeded,
      integrityFallbackApplied,
    }
  }

  const model = readString(raw.model)
  const explicitCallCount = readNumber(raw.call_count)
  const integrityRetrySucceeded = raw.integrity_retry_succeeded === true
  const integrityPruneSucceeded = raw.integrity_prune_succeeded === true
  const integrityFallbackApplied = raw.integrity_fallback_applied === true
  const integrityRecovered = integrityRetrySucceeded || integrityPruneSucceeded || integrityFallbackApplied
  const rawErrorType = readString(raw.error_type)
  const rawError = readString(raw.error)
  return {
    callCount: explicitCallCount ?? (model ? 1 : 0),
    models: model ? [model] : [],
    requestedModels: unique([readString(raw.requested_model)]),
    configuredFallbackModels: readStringArray(raw.configured_fallback_models),
    durationMs: readNumber(raw.duration_ms),
    mock: raw.mock === true,
    errorType: integrityRecovered && rawErrorType === 'output_integrity_failed' ? undefined : rawErrorType || undefined,
    error: integrityRecovered && rawError === 'output_integrity_failed' ? undefined : rawError || undefined,
    integrityRetryAttempted: raw.integrity_retry_attempted === true,
    integrityRetrySucceeded,
    integrityPruneSucceeded,
    integrityFallbackApplied,
  }
}

const TIMING_PHASE_LABELS: Array<[string, string]> = [
  ['session_scope_ms', '会话与范围'],
  ['user_persist_ms', '保存问题'],
  ['intent_rewrite_ms', '意图与改写'],
  ['intent_ms', '意图识别'],
  ['query_rewrite_overhead_ms', '查询改写'],
  ['selected_scope_check_ms', '资料充分性'],
  ['retrieval_ms', '证据检索'],
  ['base_context_ms', '基础上下文'],
  ['orchestration_ms', '任务编排'],
  ['role_retrieval_ms', '角色检索'],
  ['quality_guardrail_ms', '质量与护栏'],
  ['persistence_setup_ms', '审计与持久化'],
]

function parseTiming(payload: Record<string, unknown>): TrustReceipt['timing'] | undefined {
  if (!isRecord(payload.timing_trace)) return undefined
  const raw = payload.timing_trace
  const phases = TIMING_PHASE_LABELS.flatMap(([key, label]) => {
    const durationMs = readDurationMs(raw[key])
    return durationMs === undefined ? [] : [{ key, label, durationMs }]
  })
  const totalMs = readDurationMs(raw.total_ms)
  const retrievalMs = readDurationMs(raw.retrieval_ms)
  const modelCallsMs = readDurationMs(raw.model_calls_ms)
  const otherProcessingMs = readDurationMs(raw.other_processing_ms)
  if (
    totalMs === undefined
    && modelCallsMs === undefined
    && otherProcessingMs === undefined
    && phases.length === 0
  ) return undefined
  return { totalMs, retrievalMs, modelCallsMs, otherProcessingMs, phases }
}

function citationCount(payload: Record<string, unknown>) {
  return Array.isArray(payload.citations) ? payload.citations.length : 0
}

export function getTrustTraceTarget(messageId: string | undefined, payload: Record<string, unknown>): TrustTraceTarget | undefined {
  if (payload.mode === 'briefing' && typeof payload.briefing_id === 'string' && payload.briefing_id.trim()) {
    return { relatedObjectType: 'briefing', relatedObjectId: payload.briefing_id }
  }
  if (payload.mode === 'debate' && typeof payload.debate_id === 'string' && payload.debate_id.trim()) {
    return { relatedObjectType: 'debate', relatedObjectId: payload.debate_id }
  }
  if (messageId?.trim()) {
    return { relatedObjectType: 'chat_message', relatedObjectId: messageId }
  }
  return undefined
}

export function buildTrustReceipt(payload: Record<string, unknown>): TrustReceipt {
  const quality = parseQuality(payload)
  const guardrail = parseGuardrail(payload)
  const answerRevision = parseAnswerRevision(payload)
  const trace = parseTrace(payload)
  const timing = parseTiming(payload)
  const citations = citationCount(payload)

  const downgraded = guardrail?.status === 'downgraded'
  const blockedOutput = guardrail?.status === 'blocked_output'
  const qualityFailed = quality.decisionReady === false
  const evidenceInsufficient = quality.evidenceSufficiency === 'insufficient'
  const noUsableEvidence = evidenceInsufficient
    && quality.insufficientEvidenceReason === 'selected_sources_no_usable_evidence'
  const hasTraceError = Boolean(trace?.error || trace?.errorType)
  const hasBlockingTraceError = Boolean(trace?.error || (trace?.errorType && trace.errorType !== 'insufficient_evidence'))
  const mustReview = blockedOutput || downgraded || qualityFailed || hasTraceError || evidenceInsufficient
  const tone: TrustTone = mustReview ? 'warning' : quality.decisionReady === true ? 'success' : 'neutral'
  const title = blockedOutput
    ? '异常输出已阻断'
    : downgraded
    ? '建议已被护栏降级'
    : evidenceInsufficient
      ? '选定资料不足'
      : qualityFailed
      ? '需要复核后再用于决策'
      : quality.decisionReady === true
        ? '可信度通过'
        : '可信度待确认'

  const chips: TrustChip[] = []
  if (evidenceInsufficient) chips.push({ label: '证据不足', tone: 'warning' })
  if (evidenceInsufficient) chips.push({ label: insufficientEvidenceReasonLabel(quality.insufficientEvidenceReason), tone: 'warning' })
  if (answerRevision?.finalAnswerModifiedByGuardrail) chips.push({ label: '已替换异常输出', tone: 'warning' })
  if (citations > 0) chips.push({ label: `引用 ${citations} 条`, tone: 'success' })
  if (quality.warningCodes.includes('citation_direction_conflict')) chips.push({ label: '引用方向冲突', tone: 'warning' })
  if (quality.warningCodes.includes('source_fallback_used')) chips.push({ label: '使用原文兜底引用', tone: 'warning' })
  if (quality.warningCodes.includes('source_fallback_only')) chips.push({ label: '仅原文兜底引用', tone: 'warning' })
  if (quality.warningCodes.includes('no_citations')) chips.push({ label: '无可核验引用', tone: 'warning' })
  if (quality.warningCount > 0) chips.push({ label: `质量提示 ${quality.warningCount}`, tone: 'warning' })
  if (downgraded) chips.push({ label: '护栏降级', tone: 'warning' })
  if (trace && trace.callCount > 0) chips.push({ label: `模型调用 ${trace.callCount} 次`, tone: trace.mock || trace.error || hasBlockingTraceError ? 'warning' : 'neutral' })
  if (trace?.integrityRetryAttempted) {
    const recoveryLabel = trace.integrityRetrySucceeded
      ? '已自动重写'
      : trace.integrityFallbackApplied
        ? '已安全兜底'
        : trace.integrityPruneSucceeded
          ? '已清理异常引用'
          : '重写失败'
    const recovered = trace.integrityRetrySucceeded || trace.integrityFallbackApplied || trace.integrityPruneSucceeded
    chips.push({
      label: recoveryLabel,
      tone: recovered ? 'neutral' : 'warning',
    })
  }
  if (trace?.mock) chips.push({ label: '模拟输出', tone: 'warning' })
  if (trace?.errorType) chips.push({ label: llmErrorTypeLabel(trace.errorType), tone: 'warning' })

  return {
    tone,
    title,
    subtitle: evidenceInsufficient
      ? noUsableEvidence
        ? '选定资料尚未解析完成或处理失败。'
        : '当前选定资料不足以支撑该问题的判断。'
      : guardrail?.actionText || (mustReview ? '本次回答只适合作为研究线索。' : '本次回答可作为研究参考。'),
    mustReview,
    chips,
    answerRevision,
    quality,
    guardrail,
    trace,
    timing,
  }
}
