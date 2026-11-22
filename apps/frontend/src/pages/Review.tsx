import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { CheckCircle2, ChevronRight, Clock3, Loader2, RefreshCw } from 'lucide-react'
import { clsx } from 'clsx'
import ReplayDrawer from '@/components/ReplayDrawer'
import { EmptyState, ErrorState, LoadingRows } from '@/components/ui'
import { useReplaySnapshots, type ReplaySnapshot } from '@/services/hooks'

type ReviewFilter = 'all' | 'pending' | 'completed'

const filterOptions: Array<{ id: ReviewFilter; label: string }> = [
  { id: 'all', label: '全部' },
  { id: 'pending', label: '待记录结果' },
  { id: 'completed', label: '已有结果' },
]

function formatTime(value: string | null) {
  if (!value) return '时间未记录'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function directionLabel(value: string | null) {
  if (value === 'bullish') return '偏积极'
  if (value === 'bearish') return '偏谨慎'
  if (value === 'neutral') return '中性'
  if (value === 'conditional') return '有条件'
  return '未指定方向'
}

function originLabel(snapshot: ReplaySnapshot) {
  if (snapshot.record_origin === 'chat_user_confirmed') return '用户保存'
  if (snapshot.record_origin === 'briefing_workflow') return '简报'
  if (snapshot.record_origin === 'debate_workflow') return '委员会'
  return '决策记录'
}

function isSystemRecord(snapshot: ReplaySnapshot) {
  if (snapshot.is_system_test === true || snapshot.visibility_scope === 'system_test') return true
  return [/【ui-pass/i, /mvp_acceptance/i, /local_v0?1/i, /scope_acceptance/i, /\bro_e2e\b/i, /decision quality eval/i]
    .some((pattern) => pattern.test(snapshot.question || ''))
}

export default function Review() {
  const [filter, setFilter] = useState<ReviewFilter>('all')
  const [selected, setSelected] = useState<ReplaySnapshot | null>(null)
  const snapshotsQuery = useReplaySnapshots(undefined, { includeSystem: false, limit: 500 })

  const records = useMemo(
    () => (snapshotsQuery.data ?? []).filter((snapshot) => !isSystemRecord(snapshot) && (snapshot.visibility_scope === 'user_primary' || snapshot.record_origin === 'chat_user_confirmed')),
    [snapshotsQuery.data],
  )
  const visibleRecords = records.filter((snapshot) => {
    if (filter === 'pending') return !snapshot.outcome_direction
    if (filter === 'completed') return Boolean(snapshot.outcome_direction)
    return true
  })

  return (
    <div className="workspace-page workspace-page--reading space-y-5">
      <header className="flex items-center justify-between gap-3 border-b border-border pb-4">
        <h1 className="workspace-heading text-foreground">复盘</h1>
        <button type="button" onClick={() => void snapshotsQuery.refetch()} className="inline-flex h-9 w-9 items-center justify-center rounded-lg text-muted-foreground hover:bg-secondary hover:text-foreground" aria-label="刷新复盘记录" title="刷新">
          {snapshotsQuery.isFetching ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
        </button>
      </header>

      {records.length > 0 && (
        <div className="flex items-center gap-1" role="group" aria-label="复盘记录筛选">
          {filterOptions.map((option) => (
            <button key={option.id} type="button" onClick={() => setFilter(option.id)} aria-pressed={filter === option.id} className={clsx('rounded-lg px-3 py-1.5 text-xs font-medium transition-colors', filter === option.id ? 'bg-secondary text-foreground' : 'text-muted-foreground hover:text-foreground')}>
              {option.label}
            </button>
          ))}
        </div>
      )}

      {snapshotsQuery.isLoading ? (
        <LoadingRows rows={3} />
      ) : snapshotsQuery.isError && records.length === 0 ? (
        <ErrorState message="复盘记录读取失败" onRetry={() => void snapshotsQuery.refetch()} />
      ) : visibleRecords.length === 0 ? (
        <EmptyState
          icon={Clock3}
          title={records.length === 0 ? '暂无保存的判断' : '当前筛选下没有记录'}
          action={records.length === 0
            ? <Link to="/chat" className="btn-primary">去问答并保存判断</Link>
            : <button type="button" className="btn-outline" onClick={() => setFilter('all')}>显示全部记录</button>}
        />
      ) : (
        <section className="overflow-hidden rounded-lg border border-border bg-card">
          {visibleRecords.map((snapshot) => {
            const hasOutcome = Boolean(snapshot.outcome_direction)
            return (
              <button key={snapshot.id} type="button" onClick={() => setSelected(snapshot)} className="flex w-full items-center gap-4 border-b border-border px-4 py-4 text-left transition-colors last:border-b-0 hover:bg-secondary/50">
                <span className={clsx('grid h-8 w-8 shrink-0 place-items-center rounded-lg', hasOutcome ? 'bg-emerald-500/10 text-emerald-400' : 'bg-secondary text-muted-foreground')}>
                  {hasOutcome ? <CheckCircle2 className="h-4 w-4" /> : <Clock3 className="h-4 w-4" />}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block break-words text-sm font-medium text-foreground">{snapshot.question || `${snapshot.asset || '未命名'}判断`}</span>
                  <span className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground"><span>{formatTime(snapshot.snapshot_at)}</span><span>{originLabel(snapshot)}</span><span>{directionLabel(snapshot.final_direction)}</span></span>
                </span>
                <span className="shrink-0 text-xs font-medium text-primary">{hasOutcome ? '查看结果' : '记录结果'}</span>
                <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
              </button>
            )
          })}
        </section>
      )}

      {snapshotsQuery.isError && records.length > 0 && <p role="alert" className="status-error text-sm">刷新失败，当前显示已有记录。</p>}
      {selected && <ReplayDrawer snapshot={selected} onClose={() => setSelected(null)} />}
    </div>
  )
}
