import { useState, useRef, useEffect } from 'react'
import { clsx } from 'clsx'
import { Sparkles, Bot, BarChart3, Swords, ChevronDown, Check } from 'lucide-react'

export type ChatMode = 'auto' | 'chat' | 'briefing' | 'debate'

interface ModeMeta {
  key: ChatMode
  label: string
  hint: string
  cost: string
  latency: string
  icon: JSX.Element
  color: string
}

const MODES: ModeMeta[] = [
  {
    key: 'auto',
    label: '智能',
    hint: '根据意图自动选择（推荐）',
    cost: '视路由而定',
    latency: '按路由',
    icon: <Sparkles size={13} />,
    color: 'text-primary',
  },
  {
    key: 'chat',
    label: '快速问答',
    hint: '秒级回答，私域知识库 RAG',
    cost: '~$0.002',
    latency: '5–15 秒',
    icon: <Bot size={13} />,
    color: 'text-sky-400',
  },
  {
    key: 'briefing',
    label: '深度研究',
    hint: '制定研究计划 + 结构化简报',
    cost: '~$0.08',
    latency: '30–90 秒',
    icon: <BarChart3 size={13} />,
    color: 'text-violet-400',
  },
  {
    key: 'debate',
    label: '委员会辩论',
    hint: '多席位独立分析 + 裁决官综合',
    cost: '~$0.50',
    latency: '60–180 秒',
    icon: <Swords size={13} />,
    color: 'text-amber-400',
  },
]

export const MODE_META = Object.fromEntries(MODES.map(m => [m.key, m])) as Record<ChatMode, ModeMeta>

interface Props {
  mode: ChatMode
  onChange: (m: ChatMode) => void
  disabled?: boolean
}

export default function ModeSelector({ mode, onChange, disabled }: Props) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const current = MODE_META[mode]

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div ref={ref} className="relative inline-block">
      <button
        type="button"
        onClick={() => !disabled && setOpen(v => !v)}
        disabled={disabled}
        className={clsx(
          'flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium transition-colors',
          'border border-border bg-secondary/60 hover:bg-secondary',
          'disabled:opacity-40 disabled:cursor-not-allowed',
          current.color,
        )}
        title={current.hint}
      >
        {current.icon}
        <span>{current.label}</span>
        <ChevronDown size={11} className={clsx('transition-transform text-muted-foreground/70', open && 'rotate-180')} />
      </button>
      {open && (
        <div
          className="absolute bottom-full left-0 mb-1.5 w-72 rounded-xl overflow-hidden z-50"
          style={{
            background: 'hsl(var(--card))',
            border: '1px solid hsl(var(--border))',
            boxShadow: '0 8px 32px rgba(0,0,0,0.35)',
          }}
        >
          <div className="px-3 py-2 border-b border-border/60 bg-secondary/30">
            <p className="text-[10px] text-muted-foreground/70 uppercase tracking-wider font-semibold">模式</p>
          </div>
          <div className="py-1">
            {MODES.map(m => {
              const active = m.key === mode
              return (
                <button
                  key={m.key}
                  type="button"
                  onClick={() => { onChange(m.key); setOpen(false) }}
                  className={clsx(
                    'w-full flex items-start gap-2.5 px-3 py-2 text-left transition-colors',
                    'hover:bg-secondary/60',
                    active && 'bg-secondary/40',
                  )}
                >
                  <span className={clsx('mt-0.5 shrink-0', m.color)}>{m.icon}</span>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5">
                      <span className="text-xs font-medium text-foreground">{m.label}</span>
                      {active && <Check size={10} className="text-primary shrink-0" />}
                    </div>
                    <p className="text-[10px] text-muted-foreground leading-snug mt-0.5">{m.hint}</p>
                    <div className="flex items-center gap-2 mt-1 text-[9px] text-muted-foreground/60 font-mono">
                      <span>{m.cost}</span>
                      <span>·</span>
                      <span>{m.latency}</span>
                    </div>
                  </div>
                </button>
              )
            })}
          </div>
          <div className="px-3 py-1.5 border-t border-border/60 bg-secondary/20">
            <p className="text-[9px] text-muted-foreground/60 leading-relaxed">
              提示：消息前加 <code className="text-primary/80">/chat</code>、<code className="text-primary/80">/briefing</code>、<code className="text-primary/80">/debate</code> 可临时强制
            </p>
          </div>
        </div>
      )}
    </div>
  )
}
