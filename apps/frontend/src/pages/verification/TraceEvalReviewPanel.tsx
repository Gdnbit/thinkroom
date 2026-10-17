import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import {
  AlertCircle,
  CheckCircle2,
  CircleSlash,
  ClipboardCheck,
  FileCheck2,
  Loader2,
  RefreshCw,
  Search,
  ShieldCheck,
} from 'lucide-react'
import { clsx } from 'clsx'
import api from '@/services/api'
import {
  TRACE_EVAL_REVIEW_ACTIONS,
  canPrepareTraceEvalPromotionDraft,
  filterTraceEvalReviewItems,
  getTraceEvalReviewStatusLabel,
  summarizeTraceEvalReviewManifest,
  type TraceEvalReviewStatus,
} from '@/utils/traceEvalReview'

interface TraceEvalCandidate {
  id: string
  workflow: string
  mode: string
  coverage_status: string
  trigger_reasons?: string[]
  trace_event_count?: number
  recommended_next_step?: string
}

interface TraceEvalReviewDecision {
  candidate_id: string
  review_status: string
  reviewer?: string | null
  decision_note?: string | null
  promoted_case_id?: string | null
  review_metadata?: Record<string, unknown>
  updated_at?: string | null
}

interface TraceEvalReviewItem {
  review_status: string
  recommended_next_step: string
  main_manifest_case_id?: string
  review_decision?: TraceEvalReviewDecision
  candidate: TraceEvalCandidate
}

interface TraceEvalReviewManifest {
  status: string
  total_candidates: number
  pending_candidates: number
  reviewed_candidates: number
  covered_candidates: number
  filtered_candidates?: number
  returned_candidates?: number
  limit?: number
  offset?: number
  filters?: {
    review_status?: string | null
    workflow?: string | null
    query?: string | null
  }
  status_counts?: Record<string, number>
  workflow_counts?: Record<string, number>
  main_manifest_coverage_loaded: boolean
  main_manifest_coverage_error?: string | null
  candidates: TraceEvalReviewItem[]
}

interface ReviewActionPayload {
  candidateId: string
  status: Exclude<TraceEvalReviewStatus, 'pending_review' | 'already_covered'>
  note: string
}

interface PromotionDraftSummary {
  output_path: string
  replay_fixture_path: string
  review_manifest_path?: string
  manifest_validation_status?: string
}

interface TraceEvalPromotionDraftReport {
  status: string
  output_path: string
  replay_fixture_path: string
  manifest_validation?: { status?: string }
  replay_fixture_validation?: { status?: string }
  review_decision?: TraceEvalReviewDecision
}

interface PromotionDraftPayload {
  candidateId: string
  reviewerNote: string
}

const STATUS_FILTERS = [
  { key: 'all', label: '全部' },
  { key: 'pending_review', label: '待审' },
  { key: 'approved_for_manifest', label: '已批准' },
  { key: 'needs_follow_up', label: '跟进' },
  { key: 'already_covered', label: '已覆盖' },
  { key: 'ignored', label: '忽略' },
  { key: 'rejected', label: '驳回' },
]

function statusTone(status: string) {
  if (status === 'already_covered') return 'border-emerald-400/30 bg-emerald-400/10 text-emerald-300'
  if (status === 'pending_review') return 'border-amber-400/30 bg-amber-400/10 text-amber-300'
  if (status === 'rejected') return 'border-red-400/30 bg-red-400/10 text-red-300'
  return 'border-border bg-secondary text-foreground'
}

function actionIcon(status: ReviewActionPayload['status']) {
  if (status === 'approved_for_manifest') return <CheckCircle2 size={11} />
  if (status === 'needs_follow_up') return <AlertCircle size={11} />
  if (status === 'ignored') return <CircleSlash size={11} />
  return <ClipboardCheck size={11} />
}

function promotionDraftFromDecision(decision?: TraceEvalReviewDecision): PromotionDraftSummary | null {
  const metadata = decision?.review_metadata
  if (!metadata || typeof metadata !== 'object') return null
  const promotionDraft = (metadata as { promotion_draft?: Partial<PromotionDraftSummary> }).promotion_draft
  if (!promotionDraft || typeof promotionDraft.output_path !== 'string' || typeof promotionDraft.replay_fixture_path !== 'string') {
    return null
  }
  return {
    output_path: promotionDraft.output_path,
    replay_fixture_path: promotionDraft.replay_fixture_path,
    review_manifest_path: promotionDraft.review_manifest_path,
    manifest_validation_status: promotionDraft.manifest_validation_status,
  }
}

export default function TraceEvalReviewPanel() {
  const [notes, setNotes] = useState<Record<string, string>>({})
  const [draftReports, setDraftReports] = useState<Record<string, TraceEvalPromotionDraftReport>>({})
  const [statusFilter, setStatusFilter] = useState('all')
  const [workflowFilter, setWorkflowFilter] = useState('all')
  const [query, setQuery] = useState('')

  const reviewQ = useQuery({
    queryKey: ['trace-eval-review-manifest', statusFilter, workflowFilter, query.trim()],
    queryFn: () =>
      api
        .get<TraceEvalReviewManifest>('/api/v1/decision-traces/eval-review-manifest', {
          params: {
            limit: 200,
            scan_limit: 1000,
            ...(statusFilter !== 'all' ? { review_status: statusFilter } : {}),
            ...(workflowFilter !== 'all' ? { workflow: workflowFilter } : {}),
            ...(query.trim() ? { query: query.trim() } : {}),
          },
        })
        .then((r) => r.data),
    refetchInterval: 60_000,
    retry: 1,
  })

  const reviewMutation = useMutation({
    mutationFn: (payload: ReviewActionPayload) =>
      api
        .post<TraceEvalReviewDecision>(
          `/api/v1/decision-traces/eval-reviews/${encodeURIComponent(payload.candidateId)}`,
          {
            review_status: payload.status,
            reviewer: 'frontend-maintenance',
            decision_note: payload.note,
            promoted_case_id:
              payload.status === 'approved_for_manifest' ? payload.candidateId : undefined,
            review_metadata: { surface: 'verification_system_status' },
          },
        )
        .then((r) => r.data),
    onSuccess: () => {
      reviewQ.refetch()
    },
  })

  const promotionDraftMutation = useMutation({
    mutationFn: (payload: PromotionDraftPayload) =>
      api
        .post<TraceEvalPromotionDraftReport>(
          `/api/v1/decision-traces/eval-reviews/${encodeURIComponent(payload.candidateId)}/promotion-draft`,
          { reviewer_note: payload.reviewerNote },
        )
        .then((r) => r.data),
    onSuccess: (report, payload) => {
      setDraftReports((current) => ({ ...current, [payload.candidateId]: report }))
      reviewQ.refetch()
    },
  })

  const manifest = reviewQ.data
  const summary = summarizeTraceEvalReviewManifest(manifest)
  const candidates = manifest?.candidates ?? []
  const workflows = Object.keys(manifest?.workflow_counts ?? {}).length
    ? Object.keys(manifest?.workflow_counts ?? {}).sort()
    : Array.from(
        new Set(candidates.map((item) => item.candidate.workflow).filter(Boolean)),
      ).sort()
  const serverFiltered = Boolean(manifest?.filters)
  const filteredCandidates = serverFiltered
    ? candidates
    : filterTraceEvalReviewItems(candidates, {
        status: statusFilter,
        workflow: workflowFilter,
        query,
      })
  const visibleCandidates = filteredCandidates.slice(0, 12)
  const localStatusCounts = candidates.reduce<Record<string, number>>(
    (acc, item) => {
      acc.all += 1
      acc[item.review_status] = (acc[item.review_status] ?? 0) + 1
      return acc
    },
    { all: 0 },
  )
  const statusCounts: Record<string, number> = {
    all: manifest?.total_candidates ?? localStatusCounts.all,
    ...(manifest?.status_counts ?? localStatusCounts),
  }
  const filteredTotal = manifest?.filtered_candidates ?? filteredCandidates.length
  const queueTotal = manifest?.total_candidates ?? candidates.length

  return (
    <div className="rounded-2xl border border-border bg-card p-4 space-y-3">
      <div className="flex items-start gap-3">
        <div
          className={clsx(
            'h-10 w-10 shrink-0 rounded-xl flex items-center justify-center',
            summary.tone === 'warning'
              ? 'bg-amber-400/10'
              : summary.tone === 'ok'
                ? 'bg-emerald-400/10'
                : 'bg-muted',
          )}
        >
          {reviewQ.isLoading ? (
            <Loader2 size={18} className="text-muted-foreground animate-spin" />
          ) : summary.tone === 'warning' ? (
            <AlertCircle size={18} className="text-amber-400" />
          ) : (
            <ShieldCheck size={18} className="text-emerald-400" />
          )}
        </div>

        <div className="min-w-0 flex-1 space-y-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-sm font-medium text-foreground">Trace 评测审查队列</p>
              <p className="text-[11px] text-muted-foreground font-mono truncate">
                {summary.primaryLabel} · {summary.secondaryLabel}
              </p>
            </div>
            <button
              onClick={() => reviewQ.refetch()}
              className="px-2 py-1 rounded bg-secondary text-xs hover:bg-border flex items-center gap-1 shrink-0"
            >
              <RefreshCw size={10} className={reviewQ.isFetching ? 'animate-spin' : undefined} />
              刷新
            </button>
          </div>

          {!manifest?.main_manifest_coverage_loaded && manifest?.main_manifest_coverage_error && (
            <div className="rounded-lg border border-amber-400/30 bg-amber-400/5 p-2 text-[11px] text-amber-300">
              {manifest.main_manifest_coverage_error}
            </div>
          )}

          <div className="space-y-2">
            <div className="flex flex-wrap gap-1.5">
              {STATUS_FILTERS.map((filter) => (
                <button
                  key={filter.key}
                  onClick={() => setStatusFilter(filter.key)}
                  className={clsx(
                    'inline-flex items-center gap-1 rounded border px-2 py-1 text-[11px] transition-colors',
                    statusFilter === filter.key
                      ? 'border-primary/40 bg-primary/10 text-primary'
                      : 'border-border bg-secondary text-muted-foreground hover:text-foreground hover:bg-border',
                  )}
                >
                  {filter.label}
                  <span className="font-mono text-[10px] opacity-80">{statusCounts[filter.key] ?? 0}</span>
                </button>
              ))}
            </div>

            <div className="grid grid-cols-1 gap-2 md:grid-cols-[minmax(0,1fr)_160px]">
              <label className="relative block">
                <Search size={12} className="absolute left-2 top-2.5 text-muted-foreground" />
                <input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="搜索 candidate、workflow、trigger reason"
                  className="h-8 w-full rounded-lg border border-border bg-background pl-7 pr-2 text-[11px] text-foreground outline-none focus:border-primary"
                />
              </label>
              <select
                value={workflowFilter}
                onChange={(event) => setWorkflowFilter(event.target.value)}
                className="h-8 rounded-lg border border-border bg-background px-2 text-[11px] text-foreground outline-none focus:border-primary"
              >
                <option value="all">全部 workflow</option>
                {workflows.map((workflow) => (
                  <option key={workflow} value={workflow}>{workflow}</option>
                ))}
              </select>
            </div>

            <div className="flex items-center justify-between gap-2 text-[10px] text-muted-foreground">
              <span>
                显示 {visibleCandidates.length} / {filteredTotal}，队列 {queueTotal}
              </span>
              {(statusFilter !== 'all' || workflowFilter !== 'all' || query.trim()) && (
                <button
                  onClick={() => {
                    setStatusFilter('all')
                    setWorkflowFilter('all')
                    setQuery('')
                  }}
                  className="rounded border border-border px-2 py-0.5 hover:bg-secondary hover:text-foreground"
                >
                  清除筛选
                </button>
              )}
            </div>
          </div>

          {reviewQ.isError ? (
            <p className="text-xs text-red-300">审查队列加载失败</p>
          ) : visibleCandidates.length === 0 ? (
            <p className="text-xs text-muted-foreground">当前筛选条件下没有 trace 候选。</p>
          ) : (
            <div className="space-y-2">
              {visibleCandidates.map((item) => {
                const candidate = item.candidate
                const note = notes[candidate.id] ?? item.review_decision?.decision_note ?? ''
                const busy = reviewMutation.isPending && reviewMutation.variables?.candidateId === candidate.id
                const draftBusy = promotionDraftMutation.isPending && promotionDraftMutation.variables?.candidateId === candidate.id
                const draftReport = draftReports[candidate.id]
                const persistedDraft = promotionDraftFromDecision(item.review_decision)
                const draftSummary = draftReport
                  ? {
                      output_path: draftReport.output_path,
                      replay_fixture_path: draftReport.replay_fixture_path,
                      manifest_validation_status: draftReport.manifest_validation?.status,
                    }
                  : persistedDraft
                return (
                  <div key={candidate.id} className="rounded-lg border border-border bg-background p-3 space-y-2">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0 space-y-1">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span
                            className={clsx(
                              'inline-flex items-center rounded-full border px-2 py-0.5 text-[10px]',
                              statusTone(item.review_status),
                            )}
                          >
                            {getTraceEvalReviewStatusLabel(item.review_status)}
                          </span>
                          <span className="text-[10px] text-muted-foreground font-mono">
                            {candidate.workflow} · {candidate.mode}
                          </span>
                          {typeof candidate.trace_event_count === 'number' && (
                            <span className="text-[10px] text-muted-foreground">
                              {candidate.trace_event_count} events
                            </span>
                          )}
                        </div>
                        <p className="text-xs text-foreground font-mono break-all">{candidate.id}</p>
                        <p className="text-[11px] text-muted-foreground">
                          {item.recommended_next_step}
                        </p>
                      </div>
                      {(busy || draftBusy) && <Loader2 size={14} className="text-primary animate-spin shrink-0" />}
                    </div>

                    {(candidate.trigger_reasons?.length ?? 0) > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {candidate.trigger_reasons?.slice(0, 4).map((reason) => (
                          <span
                            key={reason}
                            className="rounded border border-border bg-secondary px-1.5 py-0.5 text-[10px] text-muted-foreground"
                          >
                            {reason}
                          </span>
                        ))}
                      </div>
                    )}

                    <textarea
                      value={note}
                      onChange={(event) =>
                        setNotes((current) => ({ ...current, [candidate.id]: event.target.value }))
                      }
                      placeholder="审查备注"
                      className="min-h-9 w-full resize-y rounded-lg border border-border bg-card px-2 py-1.5 text-[11px] text-foreground outline-none focus:border-primary"
                    />

                    <div className="flex flex-wrap gap-1.5">
                      {TRACE_EVAL_REVIEW_ACTIONS.map((action) => (
                        <button
                          key={action.status}
                          disabled={busy || draftBusy}
                          onClick={() =>
                            reviewMutation.mutate({
                              candidateId: candidate.id,
                              status: action.status,
                              note: note.trim() || action.note,
                            })
                          }
                          className="inline-flex items-center gap-1 rounded border border-border bg-secondary px-2 py-1 text-[11px] text-muted-foreground hover:text-foreground hover:bg-border disabled:cursor-wait disabled:opacity-60"
                          title={action.note}
                        >
                          {actionIcon(action.status)}
                          {action.label}
                        </button>
                      ))}
                      {canPrepareTraceEvalPromotionDraft(item.review_status) && (
                        <button
                          disabled={busy || draftBusy}
                          onClick={() =>
                            promotionDraftMutation.mutate({
                              candidateId: candidate.id,
                              reviewerNote: note.trim() || 'Approved from verification maintenance panel.',
                            })
                          }
                          className="inline-flex items-center gap-1 rounded border border-primary/30 bg-primary/10 px-2 py-1 text-[11px] text-primary hover:bg-primary/15 disabled:cursor-wait disabled:opacity-60"
                          title="导出 replay fixture 并生成 implemented manifest draft，不改正式清单。"
                        >
                          <FileCheck2 size={11} />
                          准备草稿
                        </button>
                      )}
                    </div>

                    {draftSummary && (
                      <div className="rounded-lg border border-emerald-400/30 bg-emerald-400/5 p-2 space-y-1">
                        <div className="flex items-center gap-1 text-[11px] text-emerald-300">
                          <FileCheck2 size={11} />
                          验收草稿已准备
                          {draftSummary.manifest_validation_status && (
                            <span className="font-mono">· {draftSummary.manifest_validation_status}</span>
                          )}
                        </div>
                        <p className="text-[10px] text-muted-foreground font-mono break-all">
                          manifest: {draftSummary.output_path}
                        </p>
                        <p className="text-[10px] text-muted-foreground font-mono break-all">
                          fixture: {draftSummary.replay_fixture_path}
                        </p>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
