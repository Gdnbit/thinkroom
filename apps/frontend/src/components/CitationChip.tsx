import { useState, useRef, useEffect } from 'react'
import { clsx } from 'clsx'
import { TrendingUp, TrendingDown, Minus, BookOpen } from 'lucide-react'
import CitationModal from './CitationModal'

export interface CitationData {
  index: number
  claim_id: string
  stance: string
  summary: string
  short_quote: string
  trust_weight: number
  confidence: number
  time_horizon?: string | null
  speaker_id?: string | null
}

const STANCE_META: Record<string, { color: string; bg: string; border: string; icon: JSX.Element; label: string }> = {
  bullish:     { color: 'text-emerald-300', bg: 'bg-emerald-500/15 hover:bg-emerald-500/25', border: 'border-emerald-500/40', icon: <TrendingUp size={8} />, label: '看多' },
  bearish:     { color: 'text-rose-300',    bg: 'bg-rose-500/15 hover:bg-rose-500/25',       border: 'border-rose-500/40',    icon: <TrendingDown size={8} />, label: '看空' },
  conditional: { color: 'text-amber-300',   bg: 'bg-amber-500/15 hover:bg-amber-500/25',     border: 'border-amber-500/40',   icon: <Minus size={8} />,        label: '条件' },
  neutral:     { color: 'text-slate-300',   bg: 'bg-slate-500/15 hover:bg-slate-500/25',     border: 'border-slate-500/40',   icon: <Minus size={8} />,        label: '中性' },
}

function timeHorizonLabel(value?: string | null) {
  if (!value) return ''
  return ({ short: '短期', medium: '中期', long: '长期' } as Record<string, string>)[value] ?? value
}

export default function CitationChip({ citation }: { citation: CitationData }) {
  const [hover, setHover] = useState(false)
  const [above, setAbove] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const anchorRef = useRef<HTMLButtonElement>(null)
  const meta = STANCE_META[citation.stance] ?? STANCE_META.neutral

  // 根据锚点位置决定 tooltip 显示在上/下，避免被视口边缘截断
  useEffect(() => {
    if (!hover || !anchorRef.current) return
    const rect = anchorRef.current.getBoundingClientRect()
    setAbove(rect.top > window.innerHeight - 260)
  }, [hover])

  const handleClick = () => {
    setModalOpen(true)
  }

  return (
    <span className="inline-block relative align-baseline" onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
      <button
        ref={anchorRef}
        type="button"
        onClick={handleClick}
        aria-label={`引用 ${citation.index}：${meta.label}观点 - ${citation.summary}（点击查看来源）`}
        title={`[${meta.label}] ${citation.summary}`}
        className={clsx(
          'inline-flex items-center gap-0.5 align-baseline mx-0.5',
          'px-1.5 py-px rounded border text-[10px] font-mono leading-none',
          'transition-colors cursor-pointer select-none',
          meta.color, meta.bg, meta.border,
        )}
      >
        <span aria-hidden="true">{meta.icon}</span>
        <span aria-hidden="true">{citation.index}</span>
      </button>
      {hover && (
        <span
          className={clsx(
            'absolute left-1/2 -translate-x-1/2 z-50 pointer-events-none',
            above ? 'bottom-full mb-1.5' : 'top-full mt-1.5',
          )}
        >
          <span
            className="block w-64 rounded-lg p-2.5 text-left shadow-lg"
            style={{ background: 'hsl(var(--card))', border: '1px solid hsl(var(--border))', boxShadow: '0 6px 24px rgba(0,0,0,0.35)' }}
          >
            <span className="flex items-center gap-1.5 mb-1.5">
              <span className={clsx('inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded border text-[10px] font-medium', meta.color, meta.bg, meta.border)}>
                {meta.icon}{meta.label}
              </span>
              <span className="text-[10px] text-muted-foreground ml-auto font-mono">#{citation.index}</span>
            </span>
            <span className="block text-[11px] text-foreground leading-snug mb-1.5">{citation.summary}</span>
            {citation.short_quote && citation.short_quote !== citation.summary && (
              <span className="block text-[10px] text-muted-foreground leading-relaxed italic mb-1.5 border-l-2 border-border pl-2">
                「{citation.short_quote}」
              </span>
            )}
            {citation.time_horizon && (
              <span className="block text-[10px] text-muted-foreground/80">适用时间：{timeHorizonLabel(citation.time_horizon)}</span>
            )}
            <span className="flex items-center gap-1 mt-1.5 pt-1.5 border-t border-border/60 text-[10px] text-primary/80">
              <BookOpen size={9} />
              <span>查看来源和原文</span>
            </span>
          </span>
        </span>
      )}
      <CitationModal
        claimId={modalOpen ? citation.claim_id : null}
        onClose={() => setModalOpen(false)}
      />
    </span>
  )
}
