import { useState } from 'react'
import {
  Radio, TrendingUp, TrendingDown, Minus, ChevronDown, ChevronUp,
  AlertTriangle, Zap, BarChart3, Activity, RefreshCw,
} from 'lucide-react'
import { clsx } from 'clsx'
import { useRadar, useRadarScan, type OpportunityCard } from '@/services/hooks'

const TYPE_ICON: Record<string, JSX.Element> = {
  divergence:      <Activity size={11} className="text-purple-400" />,
  flow_anomaly:    <Zap size={11} className="text-amber-400" />,
  event_catalyst:  <AlertTriangle size={11} className="text-orange-400" />,
  mean_reversion:  <BarChart3 size={11} className="text-cyan-400" />,
}
const TYPE_LABEL: Record<string, string> = {
  divergence: '背离', flow_anomaly: '资金异动', event_catalyst: '事件催化', mean_reversion: '超买超卖',
}
const URGENCY_CLS: Record<string, string> = {
  immediate: 'bg-red-500/20 text-red-300 border-red-500/30',
  today:     'bg-amber-500/20 text-amber-300 border-amber-500/30',
  this_week: 'bg-blue-500/20 text-blue-300 border-blue-500/30',
  low:       'bg-slate-500/20 text-slate-300 border-slate-500/30',
}
const URGENCY_LABEL: Record<string, string> = {
  immediate: '紧急', today: '今日', this_week: '本周', low: '低优',
}
const DIR_ICON: Record<string, JSX.Element> = {
  bullish: <TrendingUp size={10} className="text-emerald-400" />,
  bearish: <TrendingDown size={10} className="text-red-400" />,
  neutral: <Minus size={10} className="text-slate-400" />,
}

interface Props {
  compact?: boolean
}

function CardItem({ card }: { card: OpportunityCard }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="bg-card border border-border rounded-lg p-2.5">
      <button onClick={() => setOpen(v => !v)} className="w-full text-left flex items-center gap-2">
        {TYPE_ICON[card.opportunity_type] ?? <Radio size={11} className="text-muted-foreground" />}
        <span className="flex-1 text-xs text-foreground truncate">{card.title}</span>
        <span className={clsx('text-[9px] border rounded px-1 py-0.5 font-medium shrink-0', URGENCY_CLS[card.urgency] ?? URGENCY_CLS.low)}>
          {URGENCY_LABEL[card.urgency] ?? card.urgency}
        </span>
        {DIR_ICON[card.direction_hint]}
        {open ? <ChevronUp size={10} className="text-muted-foreground" /> : <ChevronDown size={10} className="text-muted-foreground" />}
      </button>
      {open && (
        <div className="mt-2 space-y-1.5 text-[11px]">
          <p className="text-foreground/70">{card.evidence_summary}</p>
          {card.key_factors.length > 0 && (
            <div>
              <p className="text-muted-foreground/50 text-[10px] mb-0.5">关键因素</p>
              {card.key_factors.map((f, i) => (
                <p key={i} className="text-foreground/60 pl-2">• {f}</p>
              ))}
            </div>
          )}
          {card.risks.length > 0 && (
            <div>
              <p className="text-muted-foreground/50 text-[10px] mb-0.5">风险</p>
              {card.risks.map((r, i) => (
                <p key={i} className="text-foreground/50 pl-2 flex items-start gap-1">
                  <AlertTriangle size={9} className="text-amber-400/50 mt-0.5 shrink-0" />{r}
                </p>
              ))}
            </div>
          )}
          <div className="flex items-center gap-2 text-[10px] text-muted-foreground/40">
            <span>类型: {TYPE_LABEL[card.opportunity_type] ?? card.opportunity_type}</span>
            <span>置信: {Math.round(card.confidence * 100)}%</span>
            <span>建议: {card.suggested_action === 'investigate' ? '深入调查' : card.suggested_action === 'watch' ? '持续关注' : card.suggested_action === 'committee_review' ? '委员会评审' : card.suggested_action}</span>
          </div>
        </div>
      )}
    </div>
  )
}

export default function RadarBar({ compact }: Props) {
  const { data, isLoading } = useRadar()
  const scanMut = useRadarScan()
  const [expanded, setExpanded] = useState(false)

  const cards = data?.cards ?? []
  const scannedAt = data?.scanned_at

  if (isLoading) return null

  if (cards.length === 0 && compact) return null

  const urgentCount = cards.filter(c => c.urgency === 'immediate' || c.urgency === 'today').length

  return (
    <div className={clsx('border rounded-xl', compact ? 'border-border/50 bg-card/50' : 'border-border bg-card')}>
      {/* summary row */}
      <button
        onClick={() => setExpanded(v => !v)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left"
      >
        <Radio size={12} className={urgentCount > 0 ? 'text-amber-400' : 'text-muted-foreground/50'} />
        <span className="text-xs font-medium text-foreground">
          {cards.length > 0
            ? `今日雷达：${cards.length} 个信号${urgentCount > 0 ? `（${urgentCount} 项需关注）` : ''}`
            : '今日雷达：暂无显著信号'}
        </span>
        {scannedAt && (
          <span className="text-[10px] text-muted-foreground/40 ml-auto shrink-0">
            {new Date(scannedAt).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })} 更新
          </span>
        )}
        <button
          onClick={e => { e.stopPropagation(); scanMut.mutate(undefined) }}
          className="text-muted-foreground/40 hover:text-primary transition-colors shrink-0"
          title="手动刷新"
        >
          <RefreshCw size={11} className={scanMut.isPending ? 'animate-spin' : ''} />
        </button>
        {cards.length > 0 && (
          expanded ? <ChevronUp size={12} className="text-muted-foreground shrink-0" /> : <ChevronDown size={12} className="text-muted-foreground shrink-0" />
        )}
      </button>

      {/* expanded cards */}
      {expanded && cards.length > 0 && (
        <div className="px-3 pb-3 space-y-1.5">
          {cards.map((c, i) => <CardItem key={i} card={c} />)}
        </div>
      )}
    </div>
  )
}
