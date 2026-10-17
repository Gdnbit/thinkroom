import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import vm from 'node:vm'
import { createRequire } from 'node:module'
import ts from 'typescript'

const root = process.cwd()
const require = createRequire(import.meta.url)
const sourcePath = path.join(root, 'src', 'utils', 'trustReceipt.ts')
const source = fs.readFileSync(sourcePath, 'utf8')
const transpiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2020,
    esModuleInterop: true,
  },
}).outputText

const sandbox = {
  exports: {},
  module: { exports: {} },
  require,
}
vm.runInNewContext(transpiled, sandbox, { filename: sourcePath })
const {
  buildTrustReceipt,
  getTrustTraceTarget,
  insufficientEvidenceReasonDetail,
  insufficientEvidenceReasonLabel,
  llmErrorTypeLabel,
} = { ...sandbox.exports, ...sandbox.module.exports }

assert.equal(typeof buildTrustReceipt, 'function')
assert.equal(typeof getTrustTraceTarget, 'function')
assert.equal(typeof insufficientEvidenceReasonDetail, 'function')
assert.equal(typeof insufficientEvidenceReasonLabel, 'function')
assert.equal(typeof llmErrorTypeLabel, 'function')

const downgraded = buildTrustReceipt({
  quality_report: {
    decision_ready: false,
    warnings: [{ message: '本次回答没有可交叉引用的结构化证据。' }],
    reference_time: '2026年06月20日 02:00:00 CST',
    duration_ms: 4321,
  },
  decision_guardrail: {
    status: 'downgraded',
    original_action: 'enter',
    effective_action: 'watch',
    reasons: ['no_evidence'],
  },
  llm_trace: {
    call_count: 2,
    calls: [
      { model: 'model-a', fallback_chain: ['model-a', 'model-b'], duration_ms: 100 },
      { model: 'model-b', duration_ms: 200, mock: false },
    ],
  },
  timing_trace: {
    session_scope_ms: 30,
    retrieval_ms: 120,
    quality_guardrail_ms: 70,
    model_calls_ms: 300,
    other_processing_ms: 80,
    total_ms: 600,
  },
  citations: [],
})

assert.equal(downgraded.tone, 'warning')
assert.equal(downgraded.mustReview, true)
assert.equal(downgraded.guardrail?.status, 'downgraded')
assert.match(downgraded.guardrail?.actionText ?? '', /enter.*watch/)
assert.ok(downgraded.chips.some((chip) => chip.label === '护栏降级'))
assert.equal(downgraded.trace?.callCount, 2)
assert.deepEqual(Array.from(downgraded.trace?.models ?? []), ['model-a', 'model-b'])
assert.equal(downgraded.trace?.durationMs, 300)
assert.equal(downgraded.timing?.totalMs, 600)
assert.equal(downgraded.timing?.retrievalMs, 120)
assert.equal(downgraded.timing?.modelCallsMs, 300)
assert.equal(downgraded.timing?.otherProcessingMs, 80)
assert.deepEqual(
  Array.from(downgraded.timing?.phases ?? [], (phase) => phase.label),
  ['会话与范围', '证据检索', '质量与护栏'],
)

const ready = buildTrustReceipt({
  quality_report: {
    decision_ready: true,
    warnings: [],
    duration_ms: 1200,
  },
  decision_guardrail: {
    status: 'passed',
    original_action: 'watch',
    effective_action: 'watch',
    reasons: [],
  },
  llm_trace: {
    model: 'model-c',
    duration_ms: 1200,
    mock: false,
  },
  citations: [{ index: 1 }, { index: 2 }],
})

assert.equal(ready.tone, 'success')
assert.equal(ready.mustReview, false)
assert.equal(ready.title, '可信度通过')
assert.ok(ready.chips.some((chip) => chip.label === '引用 2 条'))
assert.equal(ready.trace?.callCount, 1)
assert.deepEqual(Array.from(ready.trace?.models ?? []), ['model-c'])

const noAction = buildTrustReceipt({
  quality_report: {
    decision_ready: true,
    warnings: [],
  },
  decision_guardrail: {
    status: 'passed',
    reasons: [],
  },
  llm_trace: {
    model: 'model-d',
    mock: false,
  },
})

assert.equal(noAction.tone, 'success')
assert.equal(noAction.guardrail?.actionText, '未触发操作性建议')
assert.equal(noAction.subtitle, '未触发操作性建议')

const warnOnlyNoAction = buildTrustReceipt({
  quality_report: {
    decision_ready: false,
    warnings: [{ message: '需要复核' }],
  },
  decision_guardrail: {
    status: 'warn_only',
    reasons: [],
  },
  llm_trace: {
    model: 'model-e',
    mock: true,
  },
})

assert.equal(warnOnlyNoAction.guardrail?.actionText, '未触发操作性建议')
assert.equal(warnOnlyNoAction.subtitle, '未触发操作性建议')

const uncitedMaterialClaim = buildTrustReceipt({
  quality_report: {
    decision_ready: false,
    warnings: [{ message: '有 1 个关键判断句未标注引用来源，需要补充引用或人工复核。' }],
    evidence: {
      citation_count: 1,
      used_citation_count: 1,
      unused_citation_count: 0,
      uncited_material_claim_count: 1,
      uncited_material_claims: ['因此下周可以加仓原油多单'],
      field_evidence: {
        direction: { status: 'supported', claim_ids: ['claim-1'], source_ids: ['source-1'] },
        recommended_action: { status: 'insufficient', claim_ids: [], source_ids: ['source-1'] },
      },
    },
  },
  decision_guardrail: {
    status: 'passed',
    reasons: [],
  },
  citations: [{ index: 1 }],
})

assert.equal(uncitedMaterialClaim.title, '需要复核后再用于决策')
assert.equal(uncitedMaterialClaim.quality.uncitedMaterialClaimCount, 1)
assert.deepEqual(uncitedMaterialClaim.quality.uncitedMaterialClaims, ['因此下周可以加仓原油多单'])
assert.equal(uncitedMaterialClaim.quality.fieldEvidenceTotal, 2)
assert.equal(uncitedMaterialClaim.quality.fieldEvidenceSupported, 1)
assert.equal(uncitedMaterialClaim.quality.fieldEvidenceInsufficient, 1)

const structuredAttribution = buildTrustReceipt({
  quality_report: {
    decision_ready: true,
    warnings: [],
    evidence: {
      field_evidence: {
        direct_fact: { status: 'supported' },
        synthesis: { status: 'inference_input_basis' },
        supporting_points: { status: 'inference_with_evidence' },
        contrary_points: { status: 'scope_assessment' },
        risk_warnings: { status: 'model_derived_assessment' },
        recommended_action: { status: 'policy_controlled' },
        empty_optional_field: { status: 'not_applicable' },
      },
    },
  },
  decision_guardrail: {
    status: 'passed',
    reasons: [],
  },
})

assert.equal(structuredAttribution.quality.fieldEvidenceTotal, 7)
assert.equal(structuredAttribution.quality.fieldEvidenceSupported, 6)
assert.equal(structuredAttribution.quality.fieldEvidenceInsufficient, 0)

const directionConflict = buildTrustReceipt({
  quality_report: {
    decision_ready: false,
    warnings: [{
      code: 'citation_direction_conflict',
      message: '引用 [1] 的方向与回答句方向相反，需要人工复核。',
    }],
    evidence: {
      citation_count: 1,
      used_citation_count: 1,
      direction_conflict_citation_count: 1,
      direction_conflict_citations: [1],
    },
  },
  decision_guardrail: {
    status: 'warn_only',
    reasons: [],
  },
  citations: [{ index: 1 }],
})

assert.equal(directionConflict.mustReview, true)
assert.equal(directionConflict.quality.directionConflictCitationCount, 1)
assert.ok(directionConflict.chips.some((chip) => chip.label === '引用方向冲突'))

const providerFailure = buildTrustReceipt({
  quality_report: {
    decision_ready: true,
    warnings: [],
  },
  llm_trace: {
    call_count: 2,
    calls: [
      { model: 'primary-model', duration_ms: 1000, error_type: 'rate_limited', error: 'HTTP 429' },
      { model: 'fallback-model', duration_ms: 1500, mock: false },
    ],
  },
  citations: [{ index: 1 }],
})

assert.equal(providerFailure.tone, 'warning')
assert.equal(providerFailure.mustReview, true)
assert.equal(providerFailure.trace?.errorType, 'rate_limited')
assert.equal(providerFailure.trace?.error, 'HTTP 429')
assert.ok(providerFailure.chips.some((chip) => chip.label === '模型限流'))
assert.equal(llmErrorTypeLabel('missing_api_key'), '未配置模型 Key')
assert.equal(llmErrorTypeLabel('output_integrity_failed'), '输出完整性失败')
assert.equal(llmErrorTypeLabel('unexpected_new_error'), '模型异常')

const recoveredIntegrityFailure = buildTrustReceipt({
  quality_report: {
    decision_ready: true,
    warnings: [],
  },
  llm_trace: {
    call_count: 2,
    integrity_retry_attempted: true,
    integrity_retry_succeeded: true,
    calls: [
      { model: 'primary-model', duration_ms: 400, error_type: 'output_integrity_failed', error: 'output_integrity_failed' },
      { model: 'retry-model', duration_ms: 600, mock: false },
    ],
  },
  citations: [{ index: 1 }],
})

assert.equal(recoveredIntegrityFailure.tone, 'success')
assert.equal(recoveredIntegrityFailure.mustReview, false)
assert.equal(recoveredIntegrityFailure.trace?.errorType, undefined)
assert.equal(recoveredIntegrityFailure.trace?.integrityRetryAttempted, true)
assert.equal(recoveredIntegrityFailure.trace?.integrityRetrySucceeded, true)
assert.ok(recoveredIntegrityFailure.chips.some((chip) => chip.label === '已自动重写'))

const recoveredByGroundedFallback = buildTrustReceipt({
  quality_report: {
    decision_ready: true,
    warnings: [],
  },
  llm_trace: {
    call_count: 2,
    error_type: 'output_integrity_failed',
    error: 'output_integrity_failed',
    integrity_retry_attempted: true,
    integrity_retry_succeeded: false,
    integrity_prune_succeeded: false,
    integrity_fallback_applied: true,
    calls: [
      { model: 'primary-model', duration_ms: 400, error_type: 'output_integrity_failed', error: 'output_integrity_failed' },
      { model: 'retry-model', duration_ms: 600, error_type: 'output_integrity_failed', error: 'output_integrity_failed' },
    ],
  },
  citations: [{ index: 1 }, { index: 2 }],
})

assert.equal(recoveredByGroundedFallback.tone, 'success')
assert.equal(recoveredByGroundedFallback.mustReview, false)
assert.equal(recoveredByGroundedFallback.trace?.errorType, undefined)
assert.equal(recoveredByGroundedFallback.trace?.error, undefined)
assert.equal(recoveredByGroundedFallback.trace?.integrityFallbackApplied, true)
assert.ok(recoveredByGroundedFallback.chips.some((chip) => chip.label === '已安全兜底'))
assert.ok(!recoveredByGroundedFallback.chips.some((chip) => chip.label === '输出完整性失败'))
assert.ok(!recoveredByGroundedFallback.chips.some((chip) => chip.label === '重写失败'))

const unrecoveredIntegrityFailure = buildTrustReceipt({
  quality_report: {
    decision_ready: false,
    warnings: [{ message: '输出完整性检查未通过。' }],
  },
  llm_trace: {
    call_count: 2,
    integrity_retry_attempted: true,
    integrity_retry_succeeded: false,
    integrity_prune_succeeded: false,
    integrity_fallback_applied: false,
    calls: [
      { model: 'primary-model', error_type: 'output_integrity_failed', error: 'output_integrity_failed' },
      { model: 'retry-model', error_type: 'output_integrity_failed', error: 'output_integrity_failed' },
    ],
  },
  citations: [{ index: 1 }],
})

assert.equal(unrecoveredIntegrityFailure.mustReview, true)
assert.equal(unrecoveredIntegrityFailure.trace?.errorType, 'output_integrity_failed')
assert.ok(unrecoveredIntegrityFailure.chips.some((chip) => chip.label === '输出完整性失败'))
assert.ok(unrecoveredIntegrityFailure.chips.some((chip) => chip.label === '重写失败'))

const insufficientEvidence = buildTrustReceipt({
  quality_report: {
    decision_ready: false,
    warnings: [{ message: '选定资料不足，不能给出方向性结论。' }],
  },
  quality_filters: {
    evidence_sufficiency: 'insufficient',
    insufficient_evidence_reason: 'selected_sources_query_mismatch',
  },
  llm_trace: {
    call_count: 0,
    model: 'not_called_insufficient_evidence',
    error_type: 'insufficient_evidence',
  },
  citations: [],
})

assert.equal(insufficientEvidence.title, '选定资料不足')
assert.equal(insufficientEvidence.mustReview, true)
assert.equal(insufficientEvidence.trace?.callCount, 0)
assert.ok(insufficientEvidence.chips.some((chip) => chip.label === '证据不足'))
assert.ok(insufficientEvidence.chips.some((chip) => chip.label === '资料不匹配'))
assert.ok(insufficientEvidence.chips.some((chip) => chip.label === '证据不足未调用模型'))
assert.equal(llmErrorTypeLabel('insufficient_evidence'), '证据不足未调用模型')

const noUsableEvidence = buildTrustReceipt({
  quality_report: {
    decision_ready: false,
    warnings: [{ message: '选定资料尚未产生可用文本。' }],
  },
  quality_filters: {
    evidence_sufficiency: 'insufficient',
    insufficient_evidence_reason: 'selected_sources_no_usable_evidence',
  },
  llm_trace: {
    call_count: 0,
    model: 'not_called_insufficient_evidence',
    error_type: 'insufficient_evidence',
  },
  citations: [],
})

assert.equal(insufficientEvidenceReasonLabel('selected_sources_no_usable_evidence'), '资料尚未解析')
assert.match(insufficientEvidenceReasonDetail('selected_sources_no_usable_evidence'), /尚未解析|处理失败/)
assert.ok(noUsableEvidence.chips.some((chip) => chip.label === '资料尚未解析'))
assert.equal(noUsableEvidence.subtitle, '选定资料尚未解析完成或处理失败。')

const blockedOutputRevision = buildTrustReceipt({
  quality_report: {
    decision_ready: false,
    warnings: [
      { code: 'prompt_leakage', message: '回答疑似包含内部提示词。' },
    ],
    answer_revision: {
      quality_report_target: 'model_output_before_guardrail',
      final_answer_stage: 'guardrail_output',
      guardrail_status: 'blocked_output',
      final_answer_modified_by_guardrail: true,
      final_answer_differs_from_quality_target: true,
      evaluated_answer_hash: 'bad-hash',
      final_answer_hash: 'safe-hash',
    },
  },
  decision_guardrail: {
    status: 'blocked_output',
    reason: 'output_integrity_failed',
  },
  llm_trace: {
    call_count: 2,
    calls: [
      { model: 'primary-model', duration_ms: 100, error_type: 'output_integrity_failed' },
      { model: 'retry-model', duration_ms: 200, error_type: 'output_integrity_failed' },
    ],
  },
  citations: [{ index: 1 }],
})

assert.equal(blockedOutputRevision.title, '异常输出已阻断')
assert.equal(blockedOutputRevision.guardrail?.actionText, '异常输出已被阻断并替换')
assert.equal(blockedOutputRevision.answerRevision?.finalAnswerModifiedByGuardrail, true)
assert.ok(blockedOutputRevision.chips.some((chip) => chip.label === '已替换异常输出'))

const temporalInsufficientEvidence = buildTrustReceipt({
  quality_report: {
    decision_ready: false,
    warnings: [{ message: '选定资料缺少时间窗口证据。' }],
  },
  quality_filters: {
    evidence_sufficiency: 'insufficient',
    insufficient_evidence_reason: 'selected_sources_temporal_question_requires_time_window_evidence',
  },
  llm_trace: {
    call_count: 0,
    model: 'not_called_insufficient_evidence',
    error_type: 'insufficient_evidence',
  },
  citations: [],
})

assert.ok(temporalInsufficientEvidence.chips.some((chip) => chip.label === '缺少时间窗口证据'))
assert.match(
  insufficientEvidenceReasonDetail('selected_sources_temporal_question_requires_time_window_evidence'),
  /日期|预测周期|时间窗口/,
)
assert.equal(
  insufficientEvidenceReasonLabel('selected_sources_actionable_question_requires_directional_evidence'),
  '缺少方向性证据',
)
assert.equal(
  insufficientEvidenceReasonLabel('selected_sources_temporal_evidence_is_stale_for_current_question'),
  '证据时效过旧',
)
assert.match(
  insufficientEvidenceReasonDetail('selected_sources_temporal_evidence_is_stale_for_current_question'),
  /过旧|时效/,
)
assert.equal(
  insufficientEvidenceReasonLabel('selected_sources_temporal_horizon_mismatch'),
  '时间跨度不匹配',
)
assert.match(
  insufficientEvidenceReasonDetail('selected_sources_temporal_horizon_mismatch'),
  /预测周期|时间跨度/,
)
assert.equal(
  insufficientEvidenceReasonLabel('selected_sources_claim_temporal_horizon_mismatch'),
  '结构化观点周期不匹配',
)
assert.match(
  insufficientEvidenceReasonDetail('selected_sources_claim_temporal_horizon_mismatch'),
  /结构化观点|时间跨度/,
)
assert.equal(
  insufficientEvidenceReasonLabel('selected_sources_claim_validity_window_mismatch'),
  '结构化观点未生效/已过期',
)
assert.match(
  insufficientEvidenceReasonDetail('selected_sources_claim_validity_window_mismatch'),
  /尚未生效|有效期/,
)

assert.equal(
  JSON.stringify(getTrustTraceTarget('msg-1', { mode: 'chat' })),
  JSON.stringify({ relatedObjectType: 'chat_message', relatedObjectId: 'msg-1' }),
)
assert.equal(
  JSON.stringify(getTrustTraceTarget('msg-2', { mode: 'briefing', briefing_id: 'briefing-1' })),
  JSON.stringify({ relatedObjectType: 'briefing', relatedObjectId: 'briefing-1' }),
)
assert.equal(
  JSON.stringify(getTrustTraceTarget('msg-3', { mode: 'debate', debate_id: 'debate-1' })),
  JSON.stringify({ relatedObjectType: 'debate', relatedObjectId: 'debate-1' }),
)
assert.equal(getTrustTraceTarget(undefined, { mode: 'chat' }), undefined)

console.log('trustReceipt tests passed')
