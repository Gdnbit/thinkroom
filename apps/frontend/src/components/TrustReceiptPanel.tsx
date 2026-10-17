import { useState } from 'react'
import {
  AlertCircle,
  ChevronDown,
  ChevronUp,
  Database,
  Loader2,
  ShieldCheck,
  Link2,
} from 'lucide-react'
import { clsx } from 'clsx'
import { useDecisionTraces, useDecisionTraceTrustReceipt } from '@/services/hooks'
import {
  insufficientEvidenceReasonDetail,
  llmErrorTypeLabel,
  type TrustChip,
  type TrustReceipt,
  type TrustTraceTarget,
} from '@/utils/trustReceipt'

function trustToneClasses(tone: TrustChip['tone']) {
  if (tone === 'success') return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
  if (tone === 'warning') return 'border-amber-500/30 bg-amber-500/10 text-amber-300'
  return 'border-border bg-secondary text-muted-foreground'
}

function formatDuration(durationMs: number) {
  if (durationMs < 1000) return `${Math.round(durationMs)}ms`
  return `${(durationMs / 1000).toFixed(1)}s`
}

export default function TrustReceiptPanel({
  receipt,
  traceTarget,
  citationSourceIds = [],
  scopeSourceIds = [],
}: {
  receipt: TrustReceipt
  traceTarget?: TrustTraceTarget
  citationSourceIds?: string[]
  scopeSourceIds?: string[]
}) {
  const [open, setOpen] = useState(false)
  const citationSources = Array.from(new Set(citationSourceIds.map((item) => item.trim()).filter(Boolean)))
  const scopeSources = Array.from(new Set(scopeSourceIds.map((item) => item.trim()).filter(Boolean)))
  const expectedSources = citationSources.length > 0 ? citationSources : scopeSources
  const trustSummary = useDecisionTraceTrustReceipt({
    relatedObjectType: traceTarget?.relatedObjectType,
    relatedObjectId: traceTarget?.relatedObjectId,
    expectedSourceIds: expectedSources,
    limit: 20,
    enabled: open && Boolean(traceTarget),
  })
  const persistedTrace = useDecisionTraces({
    relatedObjectType: traceTarget?.relatedObjectType,
    relatedObjectId: traceTarget?.relatedObjectId,
    limit: 20,
    enabled: open && Boolean(traceTarget),
  })
  const traceItems = persistedTrace.data?.items ?? []
  const summary = trustSummary.data
  const traceEventTypes = (summary?.event_types?.length ? summary.event_types : Array.from(new Set(traceItems.map((item) => item.event_type)))).filter(Boolean)
  const traceSourceIds = (summary?.trace_source_ids?.length ? summary.trace_source_ids : Array.from(new Set(traceItems.flatMap((item) => item.source_ids ?? [])))).filter(Boolean)
  const traceLlmCalls = traceItems.filter((item) => item.event_type === 'llm_call')
  const traceSourceSet = new Set(traceSourceIds)
  const coveredExpectedSources = summary ? summary.covered_expected_source_ids : expectedSources.filter((sourceId) => traceSourceSet.has(sourceId))
  const missingExpectedSources = summary ? summary.missing_expected_source_ids : expectedSources.filter((sourceId) => !traceSourceSet.has(sourceId))
  const llmSourceIds = (summary?.llm_source_ids?.length ? summary.llm_source_ids : Array.from(new Set(traceLlmCalls.flatMap((item) => item.source_ids ?? [])))).filter(Boolean)
  const actualModels = receipt.trace?.models.length ? receipt.trace.models : summary?.models ?? []
  const requestedModels = receipt.trace?.requestedModels.length ? receipt.trace.requestedModels : summary?.requested_models ?? []
  const configuredFallbackModels = receipt.trace?.configuredFallbackModels.length
    ? receipt.trace.configuredFallbackModels
    : summary?.configured_fallback_models ?? []
  const Icon = receipt.tone === 'success' ? ShieldCheck : AlertCircle

  return (
    <div className="mt-3 overflow-hidden rounded-lg border border-border bg-secondary/30">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-foreground transition hover:bg-secondary"
      >
        <Icon className={clsx('h-3.5 w-3.5', receipt.tone === 'success' ? 'text-emerald-400' : 'text-amber-400')} />
        <span className="font-medium">{receipt.title}</span>
        <span className="min-w-0 flex-1 truncate text-muted-foreground">{receipt.subtitle}</span>
        <div className="hidden max-w-[44%] flex-wrap gap-1 md:flex">
          {receipt.chips.slice(0, 4).map((chip) => (
            <span key={`${chip.label}-${chip.tone}`} className={clsx('rounded border px-1.5 py-0.5 text-[11px]', trustToneClasses(chip.tone))}>
              {chip.label}
            </span>
          ))}
        </div>
        <span className="ml-auto inline-flex items-center gap-1 text-muted-foreground">
          {open ? '收起' : '详情'}
          {open ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
        </span>
      </button>
      {open && (
        <div className="space-y-2 border-t border-border p-3 text-xs text-muted-foreground">
          <div className="flex flex-wrap gap-1.5 md:hidden">
            {receipt.chips.map((chip) => (
              <span key={`${chip.label}-${chip.tone}`} className={clsx('rounded border px-1.5 py-0.5 text-[11px]', trustToneClasses(chip.tone))}>
                {chip.label}
              </span>
            ))}
          </div>
          <div className="grid gap-2 md:grid-cols-3">
            <div className="rounded-lg border border-border bg-card px-3 py-2">
              <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">质量</div>
              <div className="mt-1 text-foreground">{receipt.quality.decisionReady === false ? '需要复核' : receipt.quality.decisionReady === true ? '质量回执通过' : '未返回明确回执'}</div>
              {receipt.quality.referenceTime && <div className="mt-1 text-[11px]">参考时间：{receipt.quality.referenceTime}</div>}
              {typeof receipt.quality.durationMs === 'number' && <div className="text-[11px]">耗时：{(receipt.quality.durationMs / 1000).toFixed(1)}s</div>}
              {typeof receipt.quality.usedCitationCount === 'number' && typeof receipt.quality.citationCount === 'number' && (
                <div className="text-[11px]">引用使用：{receipt.quality.usedCitationCount}/{receipt.quality.citationCount}</div>
              )}
              {typeof receipt.quality.uncitedMaterialClaimCount === 'number' && receipt.quality.uncitedMaterialClaimCount > 0 && (
                <div className="text-[11px] text-amber-300">关键句未引用：{receipt.quality.uncitedMaterialClaimCount}</div>
              )}
              {typeof receipt.quality.fieldEvidenceTotal === 'number' && typeof receipt.quality.fieldEvidenceSupported === 'number' && (
                <div className="text-[11px]">字段归因：{receipt.quality.fieldEvidenceSupported}/{receipt.quality.fieldEvidenceTotal}</div>
              )}
              {typeof receipt.quality.fieldEvidenceInsufficient === 'number' && receipt.quality.fieldEvidenceInsufficient > 0 && (
                <div className="text-[11px] text-amber-300">字段证据不足：{receipt.quality.fieldEvidenceInsufficient}</div>
              )}
            </div>
            <div className="rounded-lg border border-border bg-card px-3 py-2">
              <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">护栏</div>
              <div className="mt-1 text-foreground">{receipt.guardrail?.actionText ?? '未触发结构化护栏'}</div>
              {receipt.guardrail?.reasons.length ? <div className="mt-1 text-[11px]">原因：{receipt.guardrail.reasons.join('、')}</div> : null}
              {receipt.answerRevision?.finalAnswerModifiedByGuardrail && (
                <div className="mt-1 text-[11px] text-amber-300">
                  最终回答已由护栏替换，质量回执评估的是替换前模型输出。
                </div>
              )}
            </div>
            <div className="rounded-lg border border-border bg-card px-3 py-2">
              <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">模型调用</div>
              {receipt.trace ? (
                <>
                  <div className="mt-1 text-foreground">{receipt.trace.callCount} 次调用{receipt.trace.mock ? ' / 模拟输出' : ''}</div>
                  {actualModels.length > 0 && <div className="mt-1 truncate text-[11px]" title={actualModels.join(' -> ')}>实际模型：{actualModels.join(' -> ')}</div>}
                  {requestedModels.length > 0 && (
                    <div className="mt-1 truncate text-[11px]" title={requestedModels.join(' -> ')}>
                      请求模型：{requestedModels.join(' -> ')}
                    </div>
                  )}
                  {configuredFallbackModels.length > 0 && (
                    <div className="mt-1 truncate text-[11px]" title={configuredFallbackModels.join(' -> ')}>
                      备用模型：{configuredFallbackModels.join(' -> ')}
                    </div>
                  )}
                  {typeof receipt.trace.durationMs === 'number' && <div className="text-[11px]">模型耗时：{formatDuration(receipt.trace.durationMs)}</div>}
                  {receipt.trace.errorType && <div className="mt-1 text-[11px] text-amber-300">状态：{llmErrorTypeLabel(receipt.trace.errorType)}</div>}
                  {receipt.trace.error && <div className="mt-1 text-[11px] text-amber-300">异常：{receipt.trace.error}</div>}
                </>
              ) : (
                <div className="mt-1 text-foreground">未返回 trace</div>
              )}
              {receipt.timing && (
                <div className="mt-2 border-t border-border pt-2 text-[11px]">
                  {typeof receipt.timing.totalMs === 'number' && (
                    <div className="font-medium text-foreground">全流程 {formatDuration(receipt.timing.totalMs)}</div>
                  )}
                  <div className="mt-1 grid grid-cols-2 gap-x-3 gap-y-1">
                    {receipt.timing.phases.map((phase) => (
                      <div key={phase.key} className="flex items-center justify-between gap-2">
                        <span>{phase.label}</span>
                        <span className="font-mono text-foreground">{formatDuration(phase.durationMs)}</span>
                      </div>
                    ))}
                    {typeof receipt.timing.modelCallsMs === 'number' && (
                      <div className="flex items-center justify-between gap-2">
                        <span>模型调用</span>
                        <span className="font-mono text-foreground">{formatDuration(receipt.timing.modelCallsMs)}</span>
                      </div>
                    )}
                    {typeof receipt.timing.otherProcessingMs === 'number' && (
                      <div className="flex items-center justify-between gap-2">
                        <span>其他处理</span>
                        <span className="font-mono text-foreground">{formatDuration(receipt.timing.otherProcessingMs)}</span>
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>
          {receipt.quality.evidenceSufficiency === 'insufficient' && (
            <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-2">
              <div className="text-[11px] font-semibold text-amber-300">证据不足说明</div>
              <div className="mt-1 space-y-1 leading-5">
                <div>{insufficientEvidenceReasonDetail(receipt.quality.insufficientEvidenceReason)}</div>
                <div>系统已停止模型生成方向性判断，并返回保守提示。</div>
              </div>
            </div>
          )}
          {traceTarget && (
            <div className="rounded-lg border border-border bg-card px-3 py-2">
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <Database className="h-3.5 w-3.5 text-muted-foreground" />
                <span className="font-medium text-foreground">持久化追踪</span>
                <span className="rounded border border-border px-1.5 py-0.5 text-[11px] text-muted-foreground">
                  {traceTarget.relatedObjectType}
                </span>
                {(persistedTrace.isLoading || trustSummary.isLoading) && (
                  <span className="inline-flex items-center gap-1 text-muted-foreground">
                    <Loader2 className="h-3 w-3 animate-spin" />
                    读取中
                  </span>
                )}
                {persistedTrace.isError && <span className="text-amber-300">读取失败</span>}
                {persistedTrace.data && (
                  <>
                    <span className="text-muted-foreground">{persistedTrace.data.count} 条事件</span>
                    <span className="text-muted-foreground">LLM {traceLlmCalls.length} 次</span>
                    <span className="text-muted-foreground">资料源 {traceSourceIds.length} 个</span>
                  </>
                )}
              </div>
              {traceEventTypes.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {traceEventTypes.map((eventType) => (
                    <span key={eventType} className="rounded border border-border bg-secondary px-1.5 py-0.5 text-[11px] text-muted-foreground">
                      {eventType}
                    </span>
                  ))}
                </div>
              )}
              {(expectedSources.length > 0 || traceSourceIds.length > 0) && (
                <div className="mt-2 rounded-lg border border-border bg-secondary/40 px-3 py-2">
                  <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                    <Link2 className="h-3.5 w-3.5 text-muted-foreground" />
                    <span className="font-medium text-foreground">来源链</span>
                    {expectedSources.length > 0 ? (
                      <span className={clsx(missingExpectedSources.length ? 'text-amber-300' : 'text-emerald-300')}>
                        {citationSources.length > 0 ? '引用来源' : '资料范围'} {coveredExpectedSources.length}/{expectedSources.length} 已被 trace 覆盖
                      </span>
                    ) : (
                      <span>本次回答未返回可交叉检查的引用来源</span>
                    )}
                    {llmSourceIds.length > 0 && <span>模型调用关联 {llmSourceIds.length} 个资料源</span>}
                    {traceSourceIds.length > 0 && <span>trace 共记录 {traceSourceIds.length} 个资料源</span>}
                  </div>
                  {missingExpectedSources.length > 0 && (
                    <div className="mt-1 truncate text-[11px] text-amber-300" title={missingExpectedSources.join('、')}>
                      未覆盖：{missingExpectedSources.slice(0, 4).join('、')}{missingExpectedSources.length > 4 ? ` 等 ${missingExpectedSources.length} 个` : ''}
                    </div>
                  )}
                </div>
              )}
              {traceLlmCalls.length > 0 && (
                <div className="mt-2 space-y-1">
                  {traceLlmCalls.slice(0, 4).map((event) => (
                    <div key={event.id} className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                      <span className="rounded border border-border px-1.5 py-0.5">{event.status}</span>
                      <span>{event.skill_role || event.step_name || 'llm'}</span>
                      {event.model && <span className="max-w-[360px] truncate" title={event.model}>{event.model}</span>}
                      {event.error_type && <span className="rounded border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-amber-300">{llmErrorTypeLabel(event.error_type)}</span>}
                      {typeof event.duration_ms === 'number' && <span>{(event.duration_ms / 1000).toFixed(1)}s</span>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          {receipt.quality.warnings.length > 0 && (
            <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-2">
              <div className="text-[11px] font-semibold text-amber-300">质量提示</div>
              {receipt.quality.uncitedMaterialClaims.length > 0 && (
                <div className="mt-1 space-y-1 rounded border border-amber-500/10 bg-background/30 px-2 py-1 text-[11px] text-amber-100">
                  {receipt.quality.uncitedMaterialClaims.slice(0, 2).map((claim) => (
                    <div key={claim} className="leading-5">未引用关键句：{claim}</div>
                  ))}
                </div>
              )}
              <div className="mt-1 space-y-1">
                {receipt.quality.warnings.slice(0, 4).map((warning) => (
                  <div key={warning} className="leading-5">{warning}</div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
