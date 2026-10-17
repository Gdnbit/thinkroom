export type TraceEvalReviewStatus =
  | 'pending_review'
  | 'already_covered'
  | 'approved_for_manifest'
  | 'needs_follow_up'
  | 'rejected'
  | 'ignored'

export interface TraceEvalReviewManifestLike {
  status?: string
  total_candidates?: number
  pending_candidates?: number
  reviewed_candidates?: number
  covered_candidates?: number
}

export interface TraceEvalReviewFilterableItem {
  review_status: string
  recommended_next_step?: string
  candidate?: {
    id?: string
    workflow?: string
    mode?: string
    trigger_reasons?: string[]
  }
}

export interface TraceEvalReviewFilterOptions {
  status?: string
  workflow?: string
  query?: string
}

export const TRACE_EVAL_REVIEW_ACTIONS: Array<{
  status: Exclude<TraceEvalReviewStatus, 'pending_review' | 'already_covered'>
  label: string
  note: string
}> = [
  { status: 'needs_follow_up', label: '跟进', note: '需要进一步检查 trace 与 fixture。' },
  { status: 'approved_for_manifest', label: '批准', note: '可导出 fixture 并进入主验收清单。' },
  { status: 'ignored', label: '忽略', note: '保留记录，但不进入主验收。' },
  { status: 'rejected', label: '驳回', note: '不适合作为回归评测样本。' },
]

const STATUS_LABELS: Record<TraceEvalReviewStatus, string> = {
  pending_review: '待审',
  already_covered: '已覆盖',
  approved_for_manifest: '已批准',
  needs_follow_up: '需跟进',
  rejected: '已驳回',
  ignored: '已忽略',
}

export function getTraceEvalReviewStatusLabel(status: string): string {
  return STATUS_LABELS[status as TraceEvalReviewStatus] ?? status
}

export function canPrepareTraceEvalPromotionDraft(status: string): boolean {
  return status === 'approved_for_manifest'
}

function normalized(value: unknown): string {
  return String(value ?? '').trim().toLowerCase()
}

export function filterTraceEvalReviewItems<T extends TraceEvalReviewFilterableItem>(
  items: T[],
  filters: TraceEvalReviewFilterOptions,
): T[] {
  const status = normalized(filters.status || 'all')
  const workflow = normalized(filters.workflow || 'all')
  const query = normalized(filters.query)

  return items.filter((item) => {
    if (status !== 'all' && normalized(item.review_status) !== status) return false
    if (workflow !== 'all' && normalized(item.candidate?.workflow) !== workflow) return false
    if (!query) return true

    const haystack = [
      item.review_status,
      item.recommended_next_step,
      item.candidate?.id,
      item.candidate?.workflow,
      item.candidate?.mode,
      ...(item.candidate?.trigger_reasons ?? []),
    ].map(normalized).join(' ')
    return haystack.includes(query)
  })
}

export function summarizeTraceEvalReviewManifest(manifest: TraceEvalReviewManifestLike | null | undefined): {
  primaryLabel: string
  secondaryLabel: string
  tone: 'ok' | 'warning' | 'muted'
} {
  const total = manifest?.total_candidates ?? 0
  const pending = manifest?.pending_candidates ?? 0
  const reviewed = manifest?.reviewed_candidates ?? 0
  const covered = manifest?.covered_candidates ?? 0

  if (!total) {
    return {
      primaryLabel: '无候选',
      secondaryLabel: '0 total · 0 covered · 0 reviewed',
      tone: 'muted',
    }
  }

  if (pending > 0) {
    return {
      primaryLabel: `${pending} 待审`,
      secondaryLabel: `${total} total · ${covered} covered · ${reviewed} reviewed`,
      tone: 'warning',
    }
  }

  return {
    primaryLabel: '队列已处理',
    secondaryLabel: `${total} total · ${covered} covered · ${reviewed} reviewed`,
    tone: 'ok',
  }
}
