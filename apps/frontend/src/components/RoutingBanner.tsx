import { useState } from 'react'
import { Compass, ArrowDownToLine, ArrowUpToLine, Terminal, ThumbsUp, ThumbsDown, Check } from 'lucide-react'
import { clsx } from 'clsx'
import type { RoutingDecision } from '@/services/hooks'
import { useRoutingFeedback } from '@/services/hooks'
import { MODE_META } from './ModeSelector'

interface Props {
  routing: RoutingDecision
  messageId?: string                       // B6: 有 messageId 时显示 👍👎
  onDowngrade?: () => void
  onUpgrade?: (target: 'briefing' | 'debate') => void
  lastUserQuery?: string   // 展示升级按钮 tooltip 时用
}

/**
 * 路由决策横幅：显示在 auto 模式路由到 briefing / debate 时的消息上方，
 * 告诉用户"为什么我选了这个模式"以及一键降级入口。
 *
 * 路由到 chat 时不显示（chat 是默认降级路径，无需说明）。
 */
export default function RoutingBanner({ routing, messageId, onDowngrade, onUpgrade }: Props) {
  // B6: 反馈状态（仅本组件 session 内持久；刷新后重置，但服务端已留痕）
  const [feedback, setFeedback] = useState<'up' | 'down' | null>(null)
  const feedbackMut = useRoutingFeedback()

  const submitFeedback = (helpful: boolean) => {
    if (!messageId) return
    const next: 'up' | 'down' = helpful ? 'up' : 'down'
    // 再次点击同一方向 → 取消表态（is_helpful=null）
    const isHelpful: boolean | null = feedback === next ? null : helpful
    setFeedback(feedback === next ? null : next)
    feedbackMut.mutate({
      message_id: messageId,
      auto_route: routing.route,
      chosen_route: routing.route,
      is_helpful: isHelpful,
      intent_mode: routing.intent_mode,
      intent_confidence: routing.intent_confidence,
      reason: routing.reason,
      assets: routing.assets,
    })
  }

  // 显示规则：
  //   - 非 chat 路由：始终显示（解释路由决策）
  //   - 斜杠强制：始终显示
  //   - chat 路由：仅当有 messageId 时显示精简版（仅反馈按钮），用于收集"chat 是否够用"反馈
  //   - chat 路由无 messageId：整个不显示
  const isPlainChat = routing.route === 'chat' && routing.force_source !== 'slash'
  if (isPlainChat && !messageId) return null

  const target = MODE_META[routing.route as 'chat' | 'briefing' | 'debate']
  const bgByRoute: Record<string, string> = {
    chat: 'bg-sky-400/10 border-sky-400/30 text-sky-300',
    briefing: 'bg-violet-400/10 border-violet-400/30 text-violet-300',
    debate: 'bg-amber-400/10 border-amber-400/30 text-amber-300',
  }
  const cls = bgByRoute[routing.route] ?? 'bg-secondary border-border text-foreground'

  const sourceIcon = routing.force_source === 'slash'
    ? <Terminal size={10} className="shrink-0" />
    : <Compass size={10} className="shrink-0" />
  const sourceLabel = routing.force_source === 'slash'
    ? '斜杠命令'
    : routing.force_source === 'api'
      ? '手动指定'
      : '智能路由'

  return (
    <div className={clsx('flex items-center gap-2 text-[10px] px-2.5 py-1.5 rounded-lg border mb-1', cls)}>
      {sourceIcon}
      <span className="font-semibold shrink-0">{sourceLabel}</span>
      <span className="text-muted-foreground/60">→</span>
      <span className={clsx('inline-flex items-center gap-1 font-medium shrink-0', target.color)}>
        {target.icon}
        {target.label}
      </span>
      <span className="text-muted-foreground/70 truncate min-w-0" title={routing.reason}>
        · {routing.reason}
      </span>
      <span className="text-muted-foreground/50 font-mono ml-auto shrink-0">
        ~${routing.cost_estimate_usd.toFixed(3)}
      </span>
      {/* B6: 路由反馈按钮（仅当有 messageId 时） */}
      {messageId && (
        <div className="flex items-center gap-0.5 shrink-0 pl-1 border-l border-border/40 ml-1">
          <button
            type="button"
            onClick={() => submitFeedback(true)}
            disabled={feedbackMut.isPending}
            className={clsx(
              'flex items-center gap-0.5 transition-colors border rounded px-1 py-0.5',
              feedback === 'up'
                ? 'text-emerald-400 border-emerald-400/50 bg-emerald-400/10'
                : 'text-muted-foreground/70 hover:text-emerald-400 border-border/60 hover:border-emerald-400/40'
            )}
            title="这次路由选择合适"
          >
            {feedback === 'up' ? <Check size={9} /> : <ThumbsUp size={9} />}
          </button>
          <button
            type="button"
            onClick={() => submitFeedback(false)}
            disabled={feedbackMut.isPending}
            className={clsx(
              'flex items-center gap-0.5 transition-colors border rounded px-1 py-0.5',
              feedback === 'down'
                ? 'text-rose-400 border-rose-400/50 bg-rose-400/10'
                : 'text-muted-foreground/70 hover:text-rose-400 border-border/60 hover:border-rose-400/40'
            )}
            title="路由不合适（可点击旁边的升级/降级切换）"
          >
            <ThumbsDown size={9} />
          </button>
        </div>
      )}
      {onUpgrade && routing.can_upgrade_to.map(t => {
        const tgt = MODE_META[t as 'briefing' | 'debate']
        return (
          <button
            key={t}
            onClick={() => onUpgrade(t as 'briefing' | 'debate')}
            className={clsx(
              'shrink-0 flex items-center gap-0.5 transition-colors border rounded px-1.5 py-0.5',
              'text-muted-foreground/80 hover:text-foreground border-border/60 hover:border-primary/40'
            )}
            title={`重跑为 ${tgt.label}（${tgt.cost} · ${tgt.latency}）`}
          >
            <ArrowUpToLine size={9} />
            升级 {tgt.label}
          </button>
        )
      })}
      {onDowngrade && routing.can_downgrade_to && (
        <button
          onClick={onDowngrade}
          className="shrink-0 flex items-center gap-0.5 text-muted-foreground/70 hover:text-foreground transition-colors border border-border/60 rounded px-1.5 py-0.5"
          title={`降级为 ${MODE_META[routing.can_downgrade_to as 'chat' | 'briefing'].label}`}
        >
          <ArrowDownToLine size={9} />
          降级
        </button>
      )}
    </div>
  )
}
