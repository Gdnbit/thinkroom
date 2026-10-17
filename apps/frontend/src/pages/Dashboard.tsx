import { Link } from 'react-router-dom'
import {
  ArrowRight,
  Bell,
  BookOpen,
  Brain,
  CheckCircle2,
  FileText,
  Gauge,
  MessageSquare,
  RefreshCw,
  Target,
  TrendingUp,
} from 'lucide-react'
import { clsx } from 'clsx'
import {
  useAlerts,
  useBriefings,
  useRadar,
  useReplaySnapshots,
  useSnapshotStatus,
  useSocialHealth,
  type Briefing,
  type OpportunityCard,
  type ReplaySnapshot,
} from '@/services/hooks'

const actionTone: Record<string, string> = {
  enter: 'text-emerald-400 bg-emerald-400/10 border-emerald-400/20',
  hold: 'text-sky-400 bg-sky-400/10 border-sky-400/20',
  watch: 'text-amber-400 bg-amber-400/10 border-amber-400/20',
  avoid: 'text-red-400 bg-red-400/10 border-red-400/20',
}

const directionTone: Record<string, string> = {
  bullish: 'text-emerald-400',
  bearish: 'text-red-400',
  neutral: 'text-slate-400',
}

const quickActions = [
  { label: '决策', href: '/chat', icon: MessageSquare },
  { label: '信息源', href: '/knowledge', icon: BookOpen },
  { label: '快照', href: '/decision-snapshots', icon: Brain },
  { label: '评估', href: '/quality-evaluator', icon: Gauge },
]

function formatDate(value?: string | null) {
  if (!value) return '暂无'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value.slice(0, 16).replace('T', ' ')
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function EmptyLine({ text }: { text: string }) {
  return (
    <div className="rounded-xl border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
      {text}
    </div>
  )
}

function SectionHeader({ label, title, action }: { label: string; title: string; action?: React.ReactNode }) {
  return (
    <div className="mb-4 flex items-end justify-between gap-4 border-b border-border pb-3">
      <div>
        <span className="section-label mb-2 block">{label}</span>
        <h2 className="font-serif text-xl font-semibold tracking-tight text-foreground">{title}</h2>
      </div>
      {action}
    </div>
  )
}

function StatusCard({
  icon: Icon,
  label,
  value,
  description,
  tone = 'text-muted-foreground',
}: {
  icon: React.ElementType
  label: string
  value: string
  description: string
  tone?: string
}) {
  return (
    <div className="card p-4">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-xs font-medium text-muted-foreground">{label}</span>
        <Icon size={15} className={tone} />
      </div>
      <p className="font-serif text-2xl font-semibold tracking-tight text-foreground">{value}</p>
      <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{description}</p>
    </div>
  )
}

function BriefingRow({ briefing }: { briefing: Briefing }) {
  const tone = actionTone[briefing.recommended_action] ?? 'text-muted-foreground bg-secondary border-border'

  return (
    <Link to="/chat" className="block rounded-lg px-3 py-3 transition-colors hover:bg-secondary/50">
      <div className="mb-1 flex items-start gap-3">
        <div className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-foreground">{briefing.title || briefing.topic}</p>
          <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-muted-foreground">{briefing.executive_summary}</p>
        </div>
        <span className={clsx('shrink-0 rounded-md border px-2 py-0.5 text-[11px] font-medium uppercase', tone)}>
          {briefing.recommended_action}
        </span>
      </div>
      <div className="ml-4 flex items-center gap-2 text-[11px] text-muted-foreground/70">
        <span className={directionTone[briefing.direction] ?? 'text-muted-foreground'}>{briefing.direction || 'unknown'}</span>
        <span>/</span>
        <span>{briefing.setup_quality || 'unrated'}</span>
        <span className="ml-auto font-mono">{formatDate(briefing.created_at)}</span>
      </div>
    </Link>
  )
}

function SnapshotRow({ snapshot }: { snapshot: ReplaySnapshot }) {
  const pnl = snapshot.outcome_pnl_pct
  const pnlTone = pnl == null ? 'text-muted-foreground' : pnl > 0 ? 'text-emerald-400' : pnl < 0 ? 'text-red-400' : 'text-muted-foreground'

  return (
    <Link to="/decision-snapshots" className="block rounded-lg px-3 py-3 transition-colors hover:bg-secondary/50">
      <div className="flex items-start gap-3">
        <Target size={14} className="mt-0.5 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="shrink-0 text-xs font-semibold text-primary">{snapshot.asset}</p>
            <p className="truncate text-sm text-foreground">{snapshot.question}</p>
          </div>
          <div className="mt-1 flex items-center gap-2 text-[11px] text-muted-foreground">
            <span className={directionTone[snapshot.final_direction ?? ''] ?? 'text-muted-foreground'}>
              {snapshot.final_direction ?? 'no direction'}
            </span>
            <span>/</span>
            <span>{snapshot.final_action ?? 'no action'}</span>
            <span>/</span>
            <span>{snapshot.replay_status}</span>
          </div>
        </div>
        <span className={clsx('shrink-0 text-xs font-mono', pnlTone)}>
          {pnl == null ? '待复盘' : `${pnl > 0 ? '+' : ''}${pnl.toFixed(2)}%`}
        </span>
      </div>
    </Link>
  )
}

function OpportunityRow({ card }: { card: OpportunityCard }) {
  return (
    <Link to="/watchlist" className="block rounded-lg px-3 py-3 transition-colors hover:bg-secondary/50">
      <div className="mb-1 flex items-center gap-2">
        <span className="rounded-md bg-secondary px-2 py-0.5 text-[11px] font-mono text-primary">{card.asset}</span>
        <p className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">{card.title}</p>
        <span className="text-[11px] text-muted-foreground">{Math.round(card.confidence * 100)}%</span>
      </div>
      <p className="line-clamp-2 text-xs leading-relaxed text-muted-foreground">{card.evidence_summary}</p>
    </Link>
  )
}

export default function Dashboard() {
  const alerts = useAlerts()
  const briefings = useBriefings(4)
  const replaySnapshots = useReplaySnapshots()
  const radar = useRadar()
  const socialHealth = useSocialHealth()
  const snapshotStatus = useSnapshotStatus()

  const visibleAlerts = alerts.data?.alerts ?? []
  const recentBriefings = briefings.data ?? []
  const recentSnapshots = replaySnapshots.data ?? []
  const radarCards = radar.data?.cards ?? []
  const needsAttention = visibleAlerts.filter((alert) => alert.severity === 'error' || alert.severity === 'warning').length
  const unresolvedSnapshots = recentSnapshots.filter((snapshot) => !snapshot.outcome_direction).length

  return (
    <div className="mx-auto max-w-6xl px-5 py-10 sm:px-8 lg:py-12">
      <div className="page-header !mb-8">
        <span className="section-label">Decision Desk</span>
        <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <h1 className="font-serif text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">旧工作台</h1>
            <p className="mt-3 text-sm text-muted-foreground">状态、入口与待处理事项。</p>
          </div>
          <div className="flex flex-wrap gap-2">
            {quickActions.map((action) => {
              const Icon = action.icon
              return (
                <Link key={action.href} to={action.href} className="btn-outline inline-flex items-center justify-center gap-2">
                  <Icon size={15} />
                  {action.label}
                </Link>
              )
            })}
          </div>
        </div>
      </div>

      <section className="mb-8 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatusCard
          icon={Bell}
          label="提醒"
          value={String(needsAttention)}
          description="错误 / 过期 / 人工确认"
          tone={needsAttention > 0 ? 'text-amber-400' : 'text-emerald-400'}
        />
        <StatusCard
          icon={Brain}
          label="快照"
          value={String(recentSnapshots.length)}
          description={`${unresolvedSnapshots} 条待复盘`}
          tone="text-primary"
        />
        <StatusCard
          icon={RefreshCw}
          label="刷新"
          value={snapshotStatus.data?.ts ? '已记录' : '待确认'}
          description={snapshotStatus.data?.ts ? formatDate(snapshotStatus.data.ts) : '暂无记录'}
          tone="text-sky-400"
        />
        <StatusCard
          icon={socialHealth.data?.needs_attention ? Bell : CheckCircle2}
          label="来源"
          value={socialHealth.data?.needs_attention ? '需维护' : '正常'}
          description={socialHealth.data?.needs_attention ? `${socialHealth.data.expired_count} 过期 / ${socialHealth.data.expiring_soon_count} 临期` : '登录态稳定'}
          tone={socialHealth.data?.needs_attention ? 'text-amber-400' : 'text-emerald-400'}
        />
      </section>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        <section className="card xl:col-span-2">
          <SectionHeader
            label="Recent"
            title="近期简报"
            action={<Link to="/chat" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">打开 <ArrowRight size={14} /></Link>}
          />
          {briefings.isLoading ? (
            <EmptyLine text="加载中..." />
          ) : recentBriefings.length > 0 ? (
            <div className="divide-y divide-border">
              {recentBriefings.map((briefing) => <BriefingRow key={briefing.id} briefing={briefing} />)}
            </div>
          ) : (
            <EmptyLine text="暂无简报" />
          )}
        </section>

        <section className="card">
          <SectionHeader
            label="Radar"
            title="机会雷达"
            action={<Link to="/watchlist" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">打开 <ArrowRight size={14} /></Link>}
          />
          {radar.isLoading ? (
            <EmptyLine text="加载中..." />
          ) : radarCards.length > 0 ? (
            <div className="divide-y divide-border">
              {radarCards.slice(0, 4).map((card) => <OpportunityRow key={`${card.asset}-${card.detected_at}-${card.title}`} card={card} />)}
            </div>
          ) : (
            <EmptyLine text="暂无机会" />
          )}
        </section>

        <section className="card xl:col-span-2">
          <SectionHeader
            label="Replay"
            title="待复盘"
            action={<Link to="/decision-snapshots" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">打开 <ArrowRight size={14} /></Link>}
          />
          {replaySnapshots.isLoading ? (
            <EmptyLine text="加载中..." />
          ) : recentSnapshots.length > 0 ? (
            <div className="divide-y divide-border">
              {recentSnapshots.slice(0, 5).map((snapshot) => <SnapshotRow key={snapshot.id} snapshot={snapshot} />)}
            </div>
          ) : (
            <EmptyLine text="暂无快照" />
          )}
        </section>

        <section className="card">
          <SectionHeader label="Quality" title="质量" />
          <div className="space-y-2">
            <Link to="/quality-evaluator" className="flex items-center gap-3 rounded-lg px-3 py-3 text-sm text-muted-foreground transition-colors hover:bg-secondary/50 hover:text-foreground">
              <Gauge size={15} className="text-primary" />
              评估实验室
              <ArrowRight size={14} className="ml-auto" />
            </Link>
            <Link to="/quality-monitor" className="flex items-center gap-3 rounded-lg px-3 py-3 text-sm text-muted-foreground transition-colors hover:bg-secondary/50 hover:text-foreground">
              <TrendingUp size={15} className="text-primary" />
              质量监控
              <ArrowRight size={14} className="ml-auto" />
            </Link>
            <Link to="/verification" className="flex items-center gap-3 rounded-lg px-3 py-3 text-sm text-muted-foreground transition-colors hover:bg-secondary/50 hover:text-foreground">
              <FileText size={15} className="text-primary" />
              验证中心
              <ArrowRight size={14} className="ml-auto" />
            </Link>
          </div>
        </section>
      </div>
    </div>
  )
}
