import { useState } from 'react'
import {
  X, Clock, TrendingUp, TrendingDown, Minus, AlertTriangle,
  CheckCircle2, XCircle, BarChart3, Eye, Loader2, Send,
} from 'lucide-react'
import { clsx } from 'clsx'
import {
  useReplayDetail, useAnnotateOutcome, useReplayAnalysis,
  type ReplaySnapshot,
} from '@/services/hooks'
import { formatGeneratedAnswerText } from '@/utils/answerDisplay'

const DIR_ICON: Record<string, JSX.Element> = {
  bullish: <TrendingUp size={14} className="text-emerald-400" />,
  bearish: <TrendingDown size={14} className="text-red-400" />,
  neutral: <Minus size={14} className="text-slate-400" />,
}
const DIR_CLS: Record<string, string> = {
  bullish: 'text-emerald-400', bearish: 'text-red-400', neutral: 'text-slate-400',
}
const STATUS_BADGE: Record<string, { label: string; cls: string }> = {
  pending:  { label: '待回放', cls: 'text-amber-400 border-amber-400/30 bg-amber-400/5' },
  replayed: { label: '已回放', cls: 'text-emerald-400 border-emerald-400/30 bg-emerald-400/5' },
  skipped:  { label: '已跳过', cls: 'text-slate-400 border-slate-400/30 bg-slate-400/5' },
}

function directionLabel(direction: string | null) {
  if (direction === 'bullish') return '偏积极'
  if (direction === 'bearish') return '偏谨慎'
  if (direction === 'neutral') return '中性'
  return '未指定'
}

function actionLabel(action: string | null) {
  if (action === 'watch') return '观察'
  if (action === 'hold') return '保持'
  if (action === 'buy') return '增加'
  if (action === 'sell') return '减少'
  if (action === 'avoid') return '回避'
  return action
}

interface Props {
  snapshot: ReplaySnapshot
  onClose: () => void
}

export default function ReplayDrawer({ snapshot, onClose }: Props) {
  const { data: detail, isLoading } = useReplayDetail(snapshot.id)
  const annotateMut = useAnnotateOutcome()
  const replayMut = useReplayAnalysis()

  const [outcomeDir, setOutcomeDir] = useState<string>('')
  const [outcomePnl, setOutcomePnl] = useState<string>('')

  const handleAnnotate = () => {
    if (!outcomeDir) return
    annotateMut.mutate({
      snapshotId: snapshot.id,
      outcome_direction: outcomeDir,
      outcome_pnl_pct: outcomePnl ? parseFloat(outcomePnl) : undefined,
    })
  }

  const handleReplay = () => {
    replayMut.mutate({ snapshotId: snapshot.id })
  }

  const sb = STATUS_BADGE[snapshot.replay_status] ?? STATUS_BADGE.pending

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      {/* backdrop */}
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />

      {/* drawer */}
      <div className="relative w-[480px] max-w-full bg-background border-l border-border flex flex-col h-full overflow-hidden">
        {/* header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
          <div className="flex items-center gap-2">
            <Eye size={14} className="text-primary" />
            <h3 className="text-sm font-semibold text-foreground">复盘详情</h3>
            <span className={clsx('text-[10px] border rounded px-1.5 py-0.5 font-medium', sb.cls)}>{sb.label}</span>
          </div>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground" aria-label="关闭复盘详情" title="关闭"><X size={16} /></button>
        </div>

        {/* content */}
        <div className="flex-1 overflow-auto p-4 space-y-4">
          {isLoading ? (
            <div className="flex items-center gap-2 text-muted-foreground py-12 justify-center">
              <Loader2 size={14} className="animate-spin" />加载中…
            </div>
          ) : detail ? (
            <>
              {/* ── 基本信息 ── */}
              <div className="bg-card border border-border rounded-xl p-3 space-y-2">
                <p className="text-xs font-semibold text-foreground">{detail.question}</p>
                <div className="flex items-center gap-3 text-[11px]">
                  <span className="font-medium text-primary">{detail.asset}</span>
                  {detail.snapshot_at && (
                    <span className="flex items-center gap-0.5 text-muted-foreground">
                      <Clock size={10} />
                      {new Date(detail.snapshot_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}
                    </span>
                  )}
                </div>
              </div>

              {/* ── 当时的判断 ── */}
              <div className="space-y-1.5">
                <p className="text-[10px] font-semibold text-muted-foreground/60 uppercase tracking-wider">当时的判断</p>
                <div className="bg-card border border-border rounded-xl p-3 space-y-2">
                  <div className="flex items-center gap-2">
                    {DIR_ICON[detail.final_direction ?? ''] ?? <Minus size={14} className="text-slate-400" />}
                    <span className={clsx('text-sm font-bold', DIR_CLS[detail.final_direction ?? ''] ?? 'text-slate-400')}>
                      {directionLabel(detail.final_direction)}
                    </span>
                    {detail.final_conviction != null && (
                      <span className="text-xs text-foreground/70">{Math.round(detail.final_conviction * 100)}% 确信</span>
                    )}
                    {detail.final_action && <span className="text-xs text-primary font-medium">{actionLabel(detail.final_action)}</span>}
                  </div>
                  {detail.synthesis_summary && (
                    <p className="whitespace-pre-wrap border-l-2 border-primary/30 pl-2 text-xs leading-relaxed text-foreground/70">{formatGeneratedAnswerText(detail.synthesis_summary)}</p>
                  )}
                </div>
              </div>

              <p className="text-xs text-muted-foreground">保存时包含 {detail.claims_count} 条可追溯证据。</p>

              {/* ── 后来发生了什么 ── */}
              <div className="space-y-1.5">
                <p className="text-[10px] font-semibold text-muted-foreground/60 uppercase tracking-wider">后来发生了什么</p>
                {detail.outcome_direction ? (
                  <div className="bg-card border border-border rounded-xl p-3 space-y-2">
                    <div className="flex items-center gap-2">
                      {DIR_ICON[detail.outcome_direction] ?? <Minus size={14} />}
                      <span className={clsx('text-sm font-bold', DIR_CLS[detail.outcome_direction] ?? 'text-slate-400')}>
                        实际结果：{directionLabel(detail.outcome_direction)}
                      </span>
                      {detail.outcome_pnl_pct != null && (
                        <span className={clsx('text-xs font-mono font-medium', detail.outcome_pnl_pct >= 0 ? 'text-emerald-400' : 'text-red-400')}>
                          {detail.outcome_pnl_pct >= 0 ? '+' : ''}{detail.outcome_pnl_pct.toFixed(1)}%
                        </span>
                      )}
                    </div>
                    {/* 判断是否一致 */}
                    {detail.final_direction && (
                      <div className="flex items-center gap-1.5">
                        {detail.final_direction === detail.outcome_direction ? (
                          <><CheckCircle2 size={12} className="text-emerald-400" /><span className="text-[11px] text-emerald-400">判断方向正确</span></>
                        ) : (
                          <><XCircle size={12} className="text-red-400" /><span className="text-[11px] text-red-400">判断方向不一致</span></>
                        )}
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="bg-card border border-border rounded-xl p-3 space-y-2">
                    <p className="text-[11px] text-muted-foreground">尚未标注实际结果</p>
                    <div className="flex items-center gap-2">
                      {['bullish', 'neutral', 'bearish'].map(d => (
                        <button
                          key={d}
                          onClick={() => setOutcomeDir(d)}
                          className={clsx('text-[11px] px-2.5 py-1 rounded border transition-colors',
                            outcomeDir === d ? 'border-primary bg-primary/10 text-primary' : 'border-border text-muted-foreground hover:border-primary/30'
                          )}
                        >
                          {d === 'bullish' ? '实际看多' : d === 'bearish' ? '实际看空' : '实际中性'}
                        </button>
                      ))}
                    </div>
                    {outcomeDir && (
                      <div className="flex items-center gap-2">
                        <input
                          type="number"
                          step="0.1"
                          value={outcomePnl}
                          onChange={e => setOutcomePnl(e.target.value)}
                          placeholder="盈亏 %（可选）"
                          className="text-[11px] bg-secondary border border-border rounded px-2 py-1 w-28 font-mono"
                        />
                        <button
                          onClick={handleAnnotate}
                          disabled={annotateMut.isPending}
                          className="text-[11px] px-3 py-1 rounded bg-primary/20 text-primary border border-primary/30 hover:bg-primary/30 transition-colors disabled:opacity-50"
                        >
                          {annotateMut.isPending ? '保存中…' : '保存结果'}
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* ── 回放分析 ── */}
              {detail.outcome_direction && (
                <div className="space-y-1.5">
                  <p className="text-[10px] font-semibold text-muted-foreground/60 uppercase tracking-wider">回放分析</p>
                  {detail.replay_status === 'replayed' && detail.replay_notes ? (
                    <div className="bg-card border border-border rounded-xl p-3 space-y-2">
                      <p className="text-xs text-foreground/80 leading-relaxed">{detail.replay_notes}</p>
                      {detail.misleading_evidence?.length > 0 && (
                        <div>
                          <p className="text-[10px] font-medium text-amber-400 mb-0.5">误导性证据</p>
                          {detail.misleading_evidence.map((m, i) => (
                            <p key={i} className="text-[11px] text-foreground/60 flex items-start gap-1.5">
                              <AlertTriangle size={10} className="text-amber-400 mt-0.5 shrink-0" />
                              {m.description}
                            </p>
                          ))}
                        </div>
                      )}
                      {detail.key_missed_signals?.length > 0 && (
                        <div>
                          <p className="text-[10px] font-medium text-cyan-400 mb-0.5">遗漏的信号</p>
                          {detail.key_missed_signals.map((s, i) => (
                            <p key={i} className="text-[11px] text-foreground/60 flex items-start gap-1.5">
                              <BarChart3 size={10} className="text-cyan-400 mt-0.5 shrink-0" />
                              {s}
                            </p>
                          ))}
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="bg-card border border-border rounded-xl p-3">
                      <button
                        onClick={handleReplay}
                        disabled={replayMut.isPending}
                        className="flex items-center gap-1.5 text-[11px] text-primary hover:text-primary/80 transition-colors disabled:opacity-50"
                      >
                        {replayMut.isPending ? (
                          <><Loader2 size={12} className="animate-spin" />AI 分析中…</>
                        ) : (
                          <><Send size={11} />触发 AI 回放分析</>
                        )}
                      </button>
                    </div>
                  )}
                </div>
              )}
            </>
          ) : (
            <p className="text-xs text-muted-foreground text-center py-12">加载失败</p>
          )}
        </div>
      </div>
    </div>
  )
}
