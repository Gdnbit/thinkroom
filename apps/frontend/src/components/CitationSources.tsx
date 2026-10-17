import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { BookOpen, ChevronDown, ChevronUp, ExternalLink, TrendingUp, TrendingDown, Minus } from 'lucide-react'
import { clsx } from 'clsx'
import type { CitationData } from './CitationChip'

interface Props {
  citations: CitationData[]
  stanceDist?: Record<string, number>
  balancedApplied?: boolean
}

const STANCE_BADGE: Record<string, { color: string; bg: string; border: string; icon: JSX.Element; label: string }> = {
  bullish:     { color: 'text-emerald-300', bg: 'bg-emerald-500/15', border: 'border-emerald-500/40', icon: <TrendingUp size={9} />,   label: '看多' },
  bearish:     { color: 'text-rose-300',    bg: 'bg-rose-500/15',    border: 'border-rose-500/40',    icon: <TrendingDown size={9} />, label: '看空' },
  conditional: { color: 'text-amber-300',   bg: 'bg-amber-500/15',   border: 'border-amber-500/40',   icon: <Minus size={9} />,        label: '条件' },
  neutral:     { color: 'text-slate-300',   bg: 'bg-slate-500/15',   border: 'border-slate-500/40',   icon: <Minus size={9} />,        label: '中性' },
}

export default function CitationSources({ citations, stanceDist, balancedApplied }: Props) {
  const [expanded, setExpanded] = useState(false)
  const navigate = useNavigate()

  if (!citations || citations.length === 0) return null

  const dist = stanceDist ?? {}
  const bull = dist.bullish ?? 0
  const bear = dist.bearish ?? 0
  const neu = (dist.neutral ?? 0) + (dist.conditional ?? 0)

  return (
    <div className="mt-1.5 bg-secondary/20 border border-border/60 rounded-lg overflow-hidden">
      <button
        onClick={() => setExpanded(v => !v)}
        className="w-full flex items-center gap-2 px-3 py-1.5 text-[10px] hover:bg-secondary/40 transition-colors"
      >
        <BookOpen size={10} className="text-muted-foreground/60 shrink-0" />
        <span className="font-medium text-foreground/70">来源详情</span>
        <span className="text-muted-foreground/60">{citations.length} 条私域观点</span>
        {(bull > 0 || bear > 0 || neu > 0) && (
          <span className="flex items-center gap-1 text-muted-foreground/50">
            {bull > 0 && <span className="text-emerald-400/70">看多 {bull}</span>}
            {bear > 0 && <span className="text-rose-400/70">看空 {bear}</span>}
            {neu > 0 && <span className="text-slate-400/70">中性 {neu}</span>}
          </span>
        )}
        {balancedApplied && (
          <span className="text-amber-400/70 border border-amber-400/30 bg-amber-400/10 rounded px-1.5 py-0.5 text-[9px]">
            已强制保留对立观点
          </span>
        )}
        <span className="ml-auto text-muted-foreground/40">
          {expanded ? <ChevronUp size={10} /> : <ChevronDown size={10} />}
        </span>
      </button>

      {expanded && (
        <div className="px-3 pb-2 pt-1 space-y-1.5 border-t border-border/40">
          {citations.map(c => {
            const meta = STANCE_BADGE[c.stance] ?? STANCE_BADGE.neutral
            return (
              <div key={`${c.index}-${c.claim_id}`} className="group flex items-start gap-2 py-1">
                <span className="font-mono text-[10px] text-muted-foreground/50 w-6 shrink-0 text-right select-none">[{c.index}]</span>
                <span className={clsx(
                  'inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded border text-[9px] font-medium shrink-0 mt-px',
                  meta.color, meta.bg, meta.border,
                )}>
                  {meta.icon}{meta.label}
                </span>
                <div className="flex-1 min-w-0">
                  <p className="text-[11px] text-foreground/85 leading-snug">{c.summary}</p>
                  {c.short_quote && c.short_quote !== c.summary && (
                    <p className="text-[10px] text-muted-foreground/70 italic leading-relaxed mt-0.5 border-l-2 border-border/60 pl-2">
                      「{c.short_quote}」
                    </p>
                  )}
                  <p className="text-[10px] text-muted-foreground/50 font-mono mt-0.5 flex items-center gap-1.5 flex-wrap">
                    <span>信任 {c.trust_weight.toFixed(2)}</span>
                    <span>·</span>
                    <span>置信 {c.confidence.toFixed(2)}</span>
                    {c.time_horizon && <><span>·</span><span>{c.time_horizon}</span></>}
                    {c.speaker_id && <><span>·</span><span className="truncate max-w-[120px]">{c.speaker_id}</span></>}
                  </p>
                </div>
                <button
                  onClick={() => navigate(`/knowledge?claim=${encodeURIComponent(c.claim_id)}`)}
                  className="shrink-0 text-[10px] text-muted-foreground/40 group-hover:text-primary/90 transition-colors flex items-center gap-0.5 mt-px"
                  title="在知识库中查看"
                >
                  <ExternalLink size={10} />
                </button>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
