/**
 * G7 · 决策快照（Decision Snapshots）
 *
 * 功能：
 * 1. 列出系统所有的 DecisionSnapshot（briefing/debate/chat 触发）
 * 2. 顶部 stats：总数 / pending / replayed / 已有 outcome / 方向准确率 / avg PnL
 * 3. 一键触发 G7 auto-track-pnl（自动算 PnL + 判方向）
 * 4. 点击行 → 右侧 drawer 展示完整快照
 * 5. drawer 里：手动标注 outcome / 触发 LLM 复盘 / 看回放结果
 */
import { useState, useRef, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  BookOpen,
  Brain, RefreshCw, Zap, ChevronRight, X, AlertCircle, CheckCircle2,
  ClipboardCheck,
  History,
  MessageSquare,
  Save,
  ShieldCheck,
  TrendingUp, TrendingDown, Minus, Loader2, Activity, Target, Bot,
} from 'lucide-react'
import api from '@/services/api'
import { useFocusTrap } from '@/hooks/useFocusTrap'
import { type DecisionSession } from '@/data/decisionSession'

// ── 类型 ──────────────────────────────────────────────

interface SnapshotListItem {
  id: string
  trigger_type: string
  trigger_id: string
  asset: string
  question: string
  snapshot_at: string | null
  final_direction: string | null
  final_conviction: number | null
  final_action: string | null
  activated_seats: string[]
  claims_count: number
  replay_status: string
  outcome_direction: string | null
  outcome_pnl_pct: number | null
  record_origin: string
  visibility_scope: 'user_primary' | 'workflow_history' | 'system_test' | string
  is_system_test: boolean
}

interface SnapshotDetail extends SnapshotListItem {
  claims_snapshot: Array<{ id?: string; summary?: string; stance?: string; trust_weight?: number; speaker_name?: string; time_horizon?: string }>
  data_coverage: Record<string, unknown>
  market_snapshot: Record<string, unknown>
  flow_snapshot: Record<string, unknown>
  events_snapshot: Array<{ title?: string; truth_status?: string; impact_direction?: string }>
  macro_snapshot: Record<string, unknown>
  signal_scorecard: Record<string, unknown>
  evidence_budget: Record<string, unknown>
  synthesis_summary: string | null
  outcome_observed_at: string | null
  replay_notes: string | null
  misleading_evidence: Array<{ type?: string; description?: string }>
  key_missed_signals: string[]
  created_at: string | null
}

interface ReplayStats {
  total_snapshots: number
  replayed: number
  pending: number
  with_outcome: number
  direction_accuracy: number | null
  avg_pnl_pct: number | null
  total_pnl_observations: number
  visible_snapshots?: number
  user_primary_snapshots?: number
  workflow_history_snapshots?: number
  system_test_snapshots?: number
}

interface AutoTrackResult {
  status: string
  summary: {
    checked: number
    evaluated: number
    skipped_too_early: number
    skipped_no_entry: number
    skipped_no_exit: number
    errors: number
    details: Array<{
      snapshot_id: string
      asset: string
      direction: string
      pnl_pct: number
      horizon_days: number
    }>
  }
}

function isSystemTestSnapshot(snapshot: SnapshotListItem) {
  return snapshot.is_system_test === true || snapshot.visibility_scope === 'system_test'
}

function isUserPrimarySnapshot(snapshot: SnapshotListItem) {
  return snapshot.visibility_scope === 'user_primary' || snapshot.record_origin === 'chat_user_confirmed'
}

function triggerLabel(snapshot: SnapshotListItem) {
  if (snapshot.visibility_scope === 'system_test' || snapshot.record_origin === 'system_test') return '系统测试'
  if (snapshot.record_origin === 'chat_user_confirmed') return '用户确认'
  if (snapshot.record_origin === 'briefing_workflow') return '简报生成'
  if (snapshot.record_origin === 'debate_workflow') return '委员会讨论'
  if (snapshot.record_origin === 'scout_workflow') return '观察扫描'
  return snapshot.trigger_type
}

// ── Hooks ─────────────────────────────────────────────

function useSnapshots(filter: { replay_status?: string; asset?: string }) {
  return useQuery({
    queryKey: ['decision-snapshots', 'list', filter],
    queryFn: () =>
      api.get<{ total: number; snapshots: SnapshotListItem[] }>('/api/v1/replay', {
        params: { ...filter, limit: 50 },
      }).then(r => r.data),
    refetchInterval: 60000,
  })
}

function useSnapshotDetail(id: string | null) {
  return useQuery({
    queryKey: ['decision-snapshots', 'detail', id],
    queryFn: () => api.get<SnapshotDetail>(`/api/v1/replay/${id}`).then(r => r.data),
    enabled: !!id,
  })
}

function useReplayStats() {
  return useQuery({
    queryKey: ['decision-snapshots', 'stats'],
    queryFn: () => api.get<ReplayStats>('/api/v1/replay/stats/summary').then(r => r.data),
    refetchInterval: 60000,
  })
}

function useAutoTrackPnl() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (dry_run: boolean) =>
      api.post<AutoTrackResult>('/api/v1/replay/auto-track-pnl', null, {
        params: { dry_run },
      }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['decision-snapshots'] })
    },
  })
}

function useAnnotateOutcome() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (params: { id: string; outcome_direction: string; outcome_pnl_pct?: number; notes?: string }) =>
      api.post(`/api/v1/replay/${params.id}/outcome`, {
        outcome_direction: params.outcome_direction,
        outcome_pnl_pct: params.outcome_pnl_pct,
        notes: params.notes,
      }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['decision-snapshots'] })
    },
  })
}

function useTriggerReplay() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) =>
      api.post(`/api/v1/replay/${id}/replay`, { max_tokens: 3000 }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['decision-snapshots'] })
    },
  })
}

// ── Helpers ───────────────────────────────────────────

function formatTime(iso: string | null, brief = false): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return brief
    ? d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
    : d.toLocaleString('zh-CN')
}

function directionIcon(d: string | null) {
  if (d === 'bullish') return <TrendingUp className="w-3 h-3 text-emerald-500" />
  if (d === 'bearish') return <TrendingDown className="w-3 h-3 text-red-500" />
  if (d === 'neutral') return <Minus className="w-3 h-3 text-slate-400" />
  return <span className="text-muted-foreground text-xs">—</span>
}

function directionBadge(d: string | null, label?: string): JSX.Element {
  if (!d) return <span className="text-xs text-muted-foreground">—</span>
  const cls =
    d === 'bullish' ? 'bg-emerald-500/15 text-emerald-500'
    : d === 'bearish' ? 'bg-red-500/15 text-red-500'
    : 'bg-slate-500/15 text-slate-400'
  return (
    <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium uppercase ${cls}`}>
      {label ?? d}
    </span>
  )
}

function statusBadge(status: string): JSX.Element {
  const map: Record<string, string> = {
    pending: 'bg-amber-500/15 text-amber-500',
    replayed: 'bg-sky-500/15 text-sky-500',
    skipped: 'bg-slate-500/15 text-slate-400',
  }
  const cls = map[status] ?? 'bg-secondary text-muted-foreground'
  const label: Record<string, string> = {
    pending: '待回放',
    replayed: '已回放',
    skipped: '已跳过',
  }
  return (
    <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${cls}`}>
      {label[status] ?? status}
    </span>
  )
}

function pnlClass(pnl: number | null): string {
  if (pnl === null) return 'text-muted-foreground'
  if (pnl > 1) return 'text-emerald-500'
  if (pnl < -1) return 'text-red-500'
  return 'text-slate-400'
}

function CurrentDecisionVersionPanel({ session }: { session: DecisionSession }) {
  return (
    <section className="rounded-xl border border-primary/25 bg-card p-4 shadow-sm">
      <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
        <div className="min-w-0">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <span className="inline-flex h-7 items-center gap-1.5 rounded-lg border border-primary/25 bg-primary/10 px-2 text-[11px] font-semibold text-primary">
              <Save className="h-3.5 w-3.5" />
              待确认决策记录
            </span>
            <span className="rounded-lg border border-border bg-secondary/40 px-2 py-1 text-[11px] text-muted-foreground">
              {session.snapshot.version}
            </span>
            <span className="rounded-lg border border-amber-400/25 bg-amber-400/10 px-2 py-1 text-[11px] text-amber-300">
              {session.snapshot.confidence}
            </span>
          </div>
          <h2 className="text-xl font-semibold text-foreground">{session.title}</h2>
          <p className="mt-2 max-w-3xl text-sm leading-relaxed text-muted-foreground">{session.answer.conclusion}</p>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          <Link to="/chat" className="inline-flex h-9 items-center gap-2 rounded-lg border border-border bg-secondary px-3 text-xs font-semibold text-foreground hover:bg-surface-3">
            <MessageSquare className="h-3.5 w-3.5" />
            回到问答
          </Link>
          <button className="inline-flex h-9 items-center gap-2 rounded-lg border border-primary bg-primary px-3 text-xs font-semibold text-primary-foreground">
            <Save className="h-3.5 w-3.5" />
            确认保存记录
          </button>
          <Link to="/review" className="inline-flex h-9 items-center gap-2 rounded-lg border border-border bg-card px-3 text-xs font-semibold text-foreground hover:bg-secondary">
            <History className="h-3.5 w-3.5" />
            进入复盘
          </Link>
        </div>
      </div>

      <div className="mt-4 grid gap-3 lg:grid-cols-4">
        <div className="rounded-lg border border-border bg-secondary/25 p-3">
          <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-foreground">
            <ShieldCheck className="h-3.5 w-3.5 text-primary" />
            结论边界
          </p>
          <p className="text-[11px] leading-relaxed text-muted-foreground">{session.answer.limitation}</p>
        </div>
        <div className="rounded-lg border border-border bg-secondary/25 p-3">
          <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-foreground">
            <BookOpen className="h-3.5 w-3.5 text-primary" />
            已纳入资料
          </p>
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            {session.files.length} 个文件，{session.ingestionStages.filter((stage) => stage.state === 'done').length} 个处理步骤完成。
          </p>
        </div>
        <div className="rounded-lg border border-border bg-secondary/25 p-3">
          <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-foreground">
            <ClipboardCheck className="h-3.5 w-3.5 text-primary" />
            证据覆盖
          </p>
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            {session.evidence.length} 条证据，{session.evidence.filter((item) => item.strength === 'strong').length} 条强支撑。
          </p>
        </div>
        <div className="rounded-lg border border-border bg-secondary/25 p-3">
          <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-foreground">
            <AlertCircle className="h-3.5 w-3.5 text-amber-300" />
            待确认风险
          </p>
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            {session.risks.length} 项风险，保存前建议确认是否补充外部实时数据。
          </p>
        </div>
      </div>

      <div className="mt-4 rounded-lg border border-border bg-background/40 p-3">
        <p className="text-xs font-semibold text-foreground">保存这条决策记录的意义</p>
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
          决策记录只应保存用户明确确认过的建议版本。它会锁定本次问答的结论、证据、风险和操作逻辑，后续复盘时用于对比实际结果，避免把聊天中的调侃、假设或被否决方案误当作正式策略。
        </p>
      </div>
    </section>
  )
}

// ── 主组件 ────────────────────────────────────────────

export default function DecisionSnapshots() {
  const [statusFilter, setStatusFilter] = useState<string>('')
  const [assetFilter, setAssetFilter] = useState<string>('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [showSystemSnapshots, setShowSystemSnapshots] = useState(false)

  const list = useSnapshots({
    replay_status: statusFilter || undefined,
    asset: assetFilter || undefined,
  })
  const stats = useReplayStats()
  const autoTrack = useAutoTrackPnl()
  const allSnapshots = list.data?.snapshots ?? []
  const systemSnapshots = allSnapshots.filter(isSystemTestSnapshot)
  const visibleSnapshots = showSystemSnapshots
    ? allSnapshots
    : allSnapshots.filter((snapshot) => !isSystemTestSnapshot(snapshot))
  const chatSnapshots = visibleSnapshots.filter(isUserPrimarySnapshot)

  const refetchAll = () => {
    list.refetch(); stats.refetch()
  }

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <h1 className="text-2xl font-semibold flex items-center gap-2">
            <Brain className="w-5 h-5 text-primary" />
            决策记录
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            先由用户确认当前建议，再在历史记录中追踪复盘与 PnL
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => autoTrack.mutate(true)}
            disabled={autoTrack.isPending}
            className="px-3 py-1.5 rounded-md bg-secondary hover:bg-secondary/80 text-sm flex items-center gap-1.5 disabled:opacity-50"
            title="只统计不写入字段"
          >
            <Target className="w-3.5 h-3.5" />
            预演（dry-run）
          </button>
          <button
            onClick={() => autoTrack.mutate(false)}
            disabled={autoTrack.isPending}
            className="px-3 py-1.5 rounded-md bg-primary/15 hover:bg-primary/25 text-primary text-sm font-medium flex items-center gap-1.5 disabled:opacity-50"
            title="立刻跑一次 G7 auto-track-pnl，扫所有到期 snapshot"
          >
            {autoTrack.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Zap className="w-3.5 h-3.5" />}
            手动追踪 PnL
          </button>
          <button
            onClick={refetchAll}
            aria-label="立即刷新快照列表和统计"
            title="立即刷新"
            className="p-1.5 rounded-md hover:bg-secondary text-muted-foreground hover:text-foreground"
          >
            <RefreshCw className="w-4 h-4" aria-hidden="true" />
          </button>
        </div>
      </div>

      <section className="rounded-xl border border-primary/20 bg-card p-4 shadow-sm">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
              <Save className="h-4 w-4 text-primary" />
              已保存决策记录
            </h2>
            <p className="mt-1 text-xs text-muted-foreground">
              这里直接承接 /chat 中用户主动保存的回答，点击任一记录可查看完整快照。
            </p>
          </div>
          <span className="rounded-lg border border-border bg-secondary px-2 py-1 text-[11px] text-muted-foreground">
            {chatSnapshots.length} 条
          </span>
        </div>
        {!chatSnapshots.length ? (
          <div className="rounded-lg border border-dashed border-border bg-background/40 p-4 text-xs text-muted-foreground">
            暂无用户确认记录。请回到决策问答页生成回答后点击“保存为决策记录”。
          </div>
        ) : (
          <div className="space-y-2">
            {chatSnapshots.slice(0, 5).map((record) => (
              <button
                key={record.id}
                type="button"
                onClick={() => setSelectedId(record.id)}
                className="block w-full rounded-lg border border-border bg-background/50 p-3 text-left transition hover:border-primary/50 hover:bg-secondary/40"
              >
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                  <p className="min-w-0 flex-1 truncate text-sm font-semibold text-foreground">{record.question}</p>
                  <span className="text-[11px] text-muted-foreground">{formatTime(record.snapshot_at, true)}</span>
                </div>
                <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-muted-foreground">
                  <span className="rounded-md bg-secondary px-2 py-1">资产 {record.asset}</span>
                  <span className="rounded-md bg-secondary px-2 py-1">证据 {record.claims_count}</span>
                  <span className="rounded-md bg-secondary px-2 py-1">动作 {record.final_action ?? 'watch'}</span>
                  <span className="rounded-md bg-secondary px-2 py-1">{statusBadge(record.replay_status)}</span>
                </div>
              </button>
            ))}
          </div>
        )}
      </section>

      {/* Auto-track 结果 toast */}
      {autoTrack.data && (
        <div className="rounded-lg bg-emerald-500/10 border border-emerald-500/30 px-4 py-2 text-sm">
          <div className="flex items-center gap-2 mb-1">
            <CheckCircle2 className="w-4 h-4 text-emerald-500" />
            <span className="font-medium">
              auto-track-pnl 完成：扫描 {autoTrack.data.summary.checked} 条，评估 {autoTrack.data.summary.evaluated} 条
            </span>
          </div>
          <p className="text-xs text-muted-foreground ml-6">
            跳过：太早 {autoTrack.data.summary.skipped_too_early} ·
            缺 entry 价 {autoTrack.data.summary.skipped_no_entry} ·
            缺 exit 价 {autoTrack.data.summary.skipped_no_exit}
            {autoTrack.data.summary.errors > 0 && ` · 错误 ${autoTrack.data.summary.errors}`}
          </p>
        </div>
      )}

      {/* Stats cards */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <StatCard
          label="业务记录"
          value={stats.data?.visible_snapshots ?? stats.data?.total_snapshots ?? '—'}
          icon={<Activity className="w-4 h-4" />}
          subtitle={stats.data?.system_test_snapshots ? `系统测试 ${stats.data.system_test_snapshots} 条已折叠` : undefined}
        />
        <StatCard label="待回放" value={stats.data?.pending ?? '—'} accent="amber" />
        <StatCard label="已回放" value={stats.data?.replayed ?? '—'} accent="sky" />
        <StatCard
          label="方向准确率"
          value={stats.data?.direction_accuracy !== null && stats.data?.direction_accuracy !== undefined
            ? `${(stats.data.direction_accuracy * 100).toFixed(1)}%`
            : '—'}
          accent="emerald"
        />
        <StatCard
          label="平均 PnL"
          value={stats.data?.avg_pnl_pct !== null && stats.data?.avg_pnl_pct !== undefined
            ? `${stats.data.avg_pnl_pct > 0 ? '+' : ''}${stats.data.avg_pnl_pct.toFixed(2)}%`
            : '—'}
          accent={
            stats.data?.avg_pnl_pct === null || stats.data?.avg_pnl_pct === undefined
              ? undefined
              : stats.data.avg_pnl_pct > 0 ? 'emerald' : 'red'
          }
          subtitle={stats.data?.total_pnl_observations ? `n=${stats.data.total_pnl_observations}` : undefined}
        />
      </div>

      {/* Filters */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs text-muted-foreground">筛选：</span>
        <select
          value={statusFilter}
          onChange={e => setStatusFilter(e.target.value)}
          className="px-2.5 py-1 rounded-md bg-background border border-border text-xs"
        >
          <option value="">全部状态</option>
          <option value="pending">待回放</option>
          <option value="replayed">已回放</option>
          <option value="skipped">已跳过</option>
        </select>
        <input
          type="text"
          placeholder="按资产代码（如 BTC）"
          value={assetFilter}
          onChange={e => setAssetFilter(e.target.value.toUpperCase())}
          className="px-2.5 py-1 rounded-md bg-background border border-border text-xs w-40"
        />
        {(statusFilter || assetFilter) && (
          <button
            onClick={() => { setStatusFilter(''); setAssetFilter('') }}
            className="text-xs text-muted-foreground hover:text-foreground"
          >
            清除
          </button>
        )}
        {systemSnapshots.length > 0 && (
          <button
            type="button"
            onClick={() => setShowSystemSnapshots((value) => !value)}
            className="rounded-md border border-dashed border-border bg-card px-2.5 py-1 text-xs text-muted-foreground hover:border-primary/40 hover:text-foreground"
          >
            {showSystemSnapshots ? '隐藏系统测试记录' : '显示系统测试记录'} {systemSnapshots.length} 条
          </button>
        )}
      </div>

      {/* Snapshots table */}
      <div className="rounded-xl bg-card border border-border overflow-hidden">
        {list.isLoading && <div className="p-8"><Skeleton h={200} /></div>}
        {list.data && visibleSnapshots.length === 0 && (
          <div className="p-12 text-center">
            <Brain className="w-10 h-10 text-muted-foreground/40 mx-auto mb-3" />
            <p className="text-sm text-muted-foreground">
              {systemSnapshots.length ? '暂无默认展示的业务决策记录' : '还没有决策快照'}
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              {systemSnapshots.length
                ? '系统测试记录已折叠，可通过上方开关查看。'
                : '生成 briefing 或开启委员会辩论时，系统会自动冻结快照'}
            </p>
          </div>
        )}
        {list.data && visibleSnapshots.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-muted-foreground border-b border-border bg-secondary/30">
                <tr>
                  <th className="text-left py-2 px-3">资产</th>
                  <th className="text-left py-2 px-3">问题</th>
                  <th className="text-left py-2 px-3">触发</th>
                  <th className="text-left py-2 px-3">时间</th>
                  <th className="text-center py-2 px-3">系统判断</th>
                  <th className="text-center py-2 px-3">实际结果</th>
                  <th className="text-right py-2 px-3">PnL%</th>
                  <th className="text-center py-2 px-3">状态</th>
                  <th className="text-center py-2 px-3 w-8"></th>
                </tr>
              </thead>
              <tbody>
                {visibleSnapshots.map(s => (
                  <tr
                    key={s.id}
                    onClick={() => setSelectedId(s.id)}
                    className={`border-b border-border/40 hover:bg-secondary/30 cursor-pointer transition-colors ${
                      selectedId === s.id ? 'bg-secondary/50' : ''
                    }`}
                  >
                    <td className="py-2 px-3 font-mono font-medium">{s.asset}</td>
                    <td className="py-2 px-3 max-w-[280px] truncate" title={s.question}>{s.question}</td>
                    <td className="py-2 px-3 text-muted-foreground">{triggerLabel(s)}</td>
                    <td className="py-2 px-3 text-muted-foreground tabular-nums">{formatTime(s.snapshot_at, true)}</td>
                    <td className="py-2 px-3">
                      <div className="flex items-center justify-center gap-1">
                        {directionIcon(s.final_direction)}
                        {s.final_conviction !== null && (
                          <span className="text-[10px] text-muted-foreground tabular-nums">
                            {(s.final_conviction * 100).toFixed(0)}%
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="py-2 px-3 text-center">
                      {s.outcome_direction
                        ? directionBadge(s.outcome_direction)
                        : <span className="text-muted-foreground text-[10px]">未评估</span>}
                    </td>
                    <td className={`py-2 px-3 text-right tabular-nums ${pnlClass(s.outcome_pnl_pct)}`}>
                      {s.outcome_pnl_pct !== null
                        ? `${s.outcome_pnl_pct > 0 ? '+' : ''}${s.outcome_pnl_pct.toFixed(2)}%`
                        : '—'}
                    </td>
                    <td className="py-2 px-3 text-center">{statusBadge(s.replay_status)}</td>
                    <td className="py-2 px-3">
                      <ChevronRight className="w-4 h-4 text-muted-foreground" />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="text-[10px] text-muted-foreground p-2 text-center">
              点击任意行查看快照、标注结果并触发复盘。
            </p>
          </div>
        )}
      </div>

      {/* Detail Drawer */}
      {selectedId && (
        <SnapshotDrawer
          id={selectedId}
          onClose={() => setSelectedId(null)}
        />
      )}
    </div>
  )
}

// Prototype-only panel retained out of the default flow until it is backed by live records.
void CurrentDecisionVersionPanel

// ── Stats card ───────────────────────────────────────

function StatCard({
  label, value, icon, accent, subtitle,
}: {
  label: string
  value: string | number
  icon?: React.ReactNode
  accent?: 'amber' | 'sky' | 'emerald' | 'red'
  subtitle?: string
}) {
  const accentCls = {
    amber: 'text-amber-500',
    sky: 'text-sky-500',
    emerald: 'text-emerald-500',
    red: 'text-red-500',
  }[accent ?? 'amber']
  return (
    <div className="rounded-xl bg-card border border-border p-3">
      <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground mb-1">
        {icon}
        <span>{label}</span>
      </div>
      <div className={`text-xl font-semibold tabular-nums ${accent ? accentCls : 'text-foreground'}`}>
        {value}
      </div>
      {subtitle && <div className="text-[10px] text-muted-foreground mt-0.5">{subtitle}</div>}
    </div>
  )
}

function Skeleton({ h }: { h: number }) {
  return <div className="bg-secondary/40 rounded-md animate-pulse" style={{ height: `${h}px` }} />
}

// ── Detail Drawer ────────────────────────────────────

interface DrawerProps {
  id: string
  onClose: () => void
}

function SnapshotDrawer({ id, onClose }: DrawerProps) {
  const detail = useSnapshotDetail(id)
  const annotate = useAnnotateOutcome()
  const triggerReplay = useTriggerReplay()
  const drawerRef = useRef<HTMLElement>(null)
  useFocusTrap(drawerRef, true)

  // ESC 关闭
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // 标注表单 state
  const [outcomeDir, setOutcomeDir] = useState('')
  const [outcomePnl, setOutcomePnl] = useState('')
  const [outcomeNotes, setOutcomeNotes] = useState('')
  const [annotateError, setAnnotateError] = useState<string | null>(null)
  const [replayError, setReplayError] = useState<string | null>(null)

  const submitAnnotate = async () => {
    setAnnotateError(null)
    if (!outcomeDir) {
      setAnnotateError('请选择实际方向')
      return
    }
    try {
      await annotate.mutateAsync({
        id,
        outcome_direction: outcomeDir,
        outcome_pnl_pct: outcomePnl ? parseFloat(outcomePnl) : undefined,
        notes: outcomeNotes || undefined,
      })
      setOutcomeDir(''); setOutcomePnl(''); setOutcomeNotes('')
    } catch (e: unknown) {
      setAnnotateError(e instanceof Error ? e.message : '保存失败')
    }
  }

  const runReplay = async () => {
    setReplayError(null)
    try {
      await triggerReplay.mutateAsync(id)
    } catch (e: unknown) {
      setReplayError(e instanceof Error ? e.message : '触发失败')
    }
  }

  const s = detail.data

  return (
    <div className="fixed inset-0 z-40 flex pointer-events-none">
      {/* backdrop */}
      <div
        onClick={onClose}
        aria-hidden="true"
        className="flex-1 bg-background/60 backdrop-blur-sm pointer-events-auto"
      />
      {/* drawer */}
      <aside
        ref={drawerRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="snapshot-drawer-title"
        className="w-full max-w-xl bg-card border-l border-border overflow-y-auto pointer-events-auto shadow-2xl"
      >
        <div className="sticky top-0 z-10 flex items-center justify-between p-4 border-b border-border bg-card">
          <div className="min-w-0">
            <h2 id="snapshot-drawer-title" className="text-base font-semibold flex items-center gap-2 truncate">
              <Brain className="w-4 h-4 text-primary shrink-0" aria-hidden="true" />
              决策快照详情
            </h2>
            {s && <p className="text-xs text-muted-foreground mt-0.5">{s.asset} · {formatTime(s.snapshot_at)}</p>}
          </div>
          <button
            onClick={onClose}
            aria-label="关闭决策快照详情"
            className="p-1.5 rounded-md hover:bg-secondary text-muted-foreground hover:text-foreground shrink-0"
          >
            <X className="w-4 h-4" aria-hidden="true" />
          </button>
        </div>

        <div className="p-4 space-y-4">
          {detail.isLoading && <Skeleton h={400} />}

          {s && (
            <>
              {/* 问题 + 系统判断 */}
              <Section title="问题与系统判断">
                <p className="text-sm leading-relaxed">{s.question}</p>
                <div className="grid grid-cols-3 gap-2 mt-3 text-xs">
                  <Field label="方向">{directionBadge(s.final_direction)}</Field>
                  <Field label="确信度">
                    {s.final_conviction !== null
                      ? <span className="tabular-nums">{(s.final_conviction * 100).toFixed(0)}%</span>
                      : '—'}
                  </Field>
                  <Field label="建议操作">{s.final_action ?? '—'}</Field>
                </div>
                {s.synthesis_summary && (
                  <p className="text-xs text-muted-foreground mt-2 leading-relaxed">
                    {s.synthesis_summary}
                  </p>
                )}
                {s.activated_seats.length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-2">
                    {s.activated_seats.map(seat => (
                      <span key={seat} className="px-1.5 py-0.5 rounded text-[10px] bg-secondary text-muted-foreground">
                        {seat}
                      </span>
                    ))}
                  </div>
                )}
              </Section>

              {/* 决策时点证据 */}
              <Section title="决策时点的已知信息（冻结）">
                <div className="grid grid-cols-2 gap-2 text-xs">
                  <Field label="私域观点数">{s.claims_count}</Field>
                  <Field label="事件数">{s.events_snapshot.length}</Field>
                  {s.market_snapshot?.price !== undefined && (
                    <Field label="行情价格"><span className="tabular-nums">{String(s.market_snapshot.price)}</span></Field>
                  )}
                  {s.market_snapshot?.trend_direction !== undefined && (
                    <Field label="趋势">{String(s.market_snapshot.trend_direction)}</Field>
                  )}
                </div>
                {s.claims_snapshot.length > 0 && (
                  <details className="mt-3">
                    <summary className="text-xs text-muted-foreground cursor-pointer hover:text-foreground">
                      展开私域观点（{s.claims_snapshot.length} 条）
                    </summary>
                    <div className="mt-2 space-y-1.5">
                      {s.claims_snapshot.slice(0, 8).map((c, i) => (
                        <div key={c.id ?? i} className="text-[11px] p-2 rounded bg-secondary/30 border border-border/40">
                          <div className="flex items-center gap-1.5 mb-1">
                            {directionBadge(c.stance ?? null)}
                            {c.trust_weight !== undefined && (
                              <span className="text-muted-foreground">信任 {c.trust_weight.toFixed(1)}</span>
                            )}
                            {c.speaker_name && <span className="text-muted-foreground">· {c.speaker_name}</span>}
                          </div>
                          <p className="leading-snug">{c.summary}</p>
                        </div>
                      ))}
                    </div>
                  </details>
                )}
              </Section>

              {/* 后验结果 */}
              <Section title="事后实际结果（outcome）">
                {s.outcome_direction ? (
                  <div className="space-y-2">
                    <div className="grid grid-cols-3 gap-2 text-xs">
                      <Field label="实际方向">{directionBadge(s.outcome_direction)}</Field>
                      <Field label="PnL%">
                        <span className={`tabular-nums ${pnlClass(s.outcome_pnl_pct)}`}>
                          {s.outcome_pnl_pct !== null
                            ? `${s.outcome_pnl_pct > 0 ? '+' : ''}${s.outcome_pnl_pct.toFixed(2)}%`
                            : '—'}
                        </span>
                      </Field>
                      <Field label="观察时间">{formatTime(s.outcome_observed_at, true)}</Field>
                    </div>
                    {s.replay_notes && (
                      <details className="mt-2">
                        <summary className="text-xs text-muted-foreground cursor-pointer hover:text-foreground">
                          回放笔记
                        </summary>
                        <pre className="text-[11px] text-muted-foreground mt-1 whitespace-pre-wrap leading-relaxed font-mono bg-secondary/20 p-2 rounded">
                          {s.replay_notes}
                        </pre>
                      </details>
                    )}
                  </div>
                ) : (
                  <div className="space-y-2">
                    <p className="text-xs text-muted-foreground">
                      还没有标注实际结果。可以等 G7 scheduler 自动算（每天 03:00），或现在手动填：
                    </p>
                    <div className="grid grid-cols-2 gap-2">
                      <select
                        value={outcomeDir}
                        onChange={e => setOutcomeDir(e.target.value)}
                        className="px-2 py-1.5 rounded-md bg-background border border-border text-xs"
                      >
                        <option value="">选择实际方向</option>
                        <option value="bullish">bullish 看涨</option>
                        <option value="bearish">bearish 看跌</option>
                        <option value="neutral">neutral 横盘</option>
                      </select>
                      <input
                        type="number"
                        step="0.01"
                        value={outcomePnl}
                        onChange={e => setOutcomePnl(e.target.value)}
                        placeholder="PnL% (可选)"
                        className="px-2 py-1.5 rounded-md bg-background border border-border text-xs"
                      />
                    </div>
                    <textarea
                      value={outcomeNotes}
                      onChange={e => setOutcomeNotes(e.target.value)}
                      placeholder="复盘笔记（可选）"
                      rows={2}
                      className="w-full px-2 py-1.5 rounded-md bg-background border border-border text-xs resize-none"
                    />
                    <button
                      onClick={submitAnnotate}
                      disabled={annotate.isPending || !outcomeDir}
                      className="w-full px-3 py-1.5 rounded-md bg-primary/15 hover:bg-primary/25 text-primary text-xs font-medium disabled:opacity-50 flex items-center justify-center gap-1.5"
                    >
                      {annotate.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : <CheckCircle2 className="w-3 h-3" />}
                      保存 outcome
                    </button>
                    {annotateError && (
                      <div className="rounded-md bg-red-500/10 border border-red-500/30 px-2.5 py-1.5 text-[11px] text-red-500 flex items-center gap-1.5">
                        <AlertCircle className="w-3 h-3" />
                        {annotateError}
                      </div>
                    )}
                  </div>
                )}
              </Section>

              {/* LLM 复盘 */}
              {s.outcome_direction && (
                <Section title="LLM 复盘">
                  {s.replay_status === 'replayed' && (s.misleading_evidence.length > 0 || s.key_missed_signals.length > 0) ? (
                    <div className="space-y-3">
                      {s.misleading_evidence.length > 0 && (
                        <div>
                          <p className="text-xs font-medium text-amber-500 mb-1">⚠ 误导性证据</p>
                          <ul className="space-y-1">
                            {s.misleading_evidence.map((m, i) => (
                              <li key={i} className="text-[11px] text-muted-foreground leading-snug">
                                <span className="text-amber-500">[{m.type ?? '?'}]</span> {m.description}
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                      {s.key_missed_signals.length > 0 && (
                        <div>
                          <p className="text-xs font-medium text-sky-500 mb-1">遗漏的关键信号</p>
                          <ul className="space-y-1">
                            {s.key_missed_signals.map((sig, i) => (
                              <li key={i} className="text-[11px] text-muted-foreground leading-snug">• {sig}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                      <button
                        onClick={runReplay}
                        disabled={triggerReplay.isPending}
                        className="text-[11px] text-muted-foreground hover:text-foreground"
                      >
                        重新触发回放
                      </button>
                    </div>
                  ) : (
                    <div className="space-y-2">
                      <p className="text-xs text-muted-foreground">
                        让 LLM 对比【决策时已知证据】与【实际结果】，找出误导性证据 + 遗漏信号。
                      </p>
                      <button
                        onClick={runReplay}
                        disabled={triggerReplay.isPending}
                        className="w-full px-3 py-1.5 rounded-md bg-sky-500/15 hover:bg-sky-500/25 text-sky-500 text-xs font-medium disabled:opacity-50 flex items-center justify-center gap-1.5"
                      >
                        {triggerReplay.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : <Bot className="w-3 h-3" />}
                        触发 LLM 复盘
                      </button>
                      {replayError && (
                        <div className="rounded-md bg-red-500/10 border border-red-500/30 px-2.5 py-1.5 text-[11px] text-red-500 flex items-center gap-1.5">
                          <AlertCircle className="w-3 h-3" />
                          {replayError}
                        </div>
                      )}
                    </div>
                  )}
                </Section>
              )}
            </>
          )}
        </div>
      </aside>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-border bg-secondary/10 p-3">
      <h3 className="text-xs font-medium text-muted-foreground mb-2 uppercase tracking-wide">{title}</h3>
      {children}
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="text-[10px] text-muted-foreground uppercase tracking-wide">{label}</p>
      <div className="mt-0.5">{children}</div>
    </div>
  )
}
