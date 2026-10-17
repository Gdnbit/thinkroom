import { useState } from 'react'
import {
  TrendingUp, TrendingDown, Minus, Loader2, Plus,
  CheckCircle2, AlertTriangle, BarChart3, Zap,
  ShieldCheck, DollarSign, Search, Scale,
} from 'lucide-react'
import { clsx } from 'clsx'
import {
  useDebates, useDebate, useRunCommittee,
  // DebateRecord type ref kept for future schema extensions, intentionally unused
  type DebateRecord, type DebateRound, type DebateAdjudication,
} from '@/services/hooks'
import TrustReceiptPanel from '@/components/TrustReceiptPanel'
import { buildTrustReceipt, getTrustTraceTarget } from '@/utils/trustReceipt'

// V2 全席位映射（兼容 V1 bull/bear/neutral）
// 新增席位会自动 fallback 到 _FALLBACK_PALETTE 中的样式，无需手动添加
const ROLE_META: Record<string, { label: string; color: string; bg: string; icon: JSX.Element }> = {
  // V2 席位
  baseline:    { label: '基准分析',   color: 'text-blue-400',    bg: 'bg-blue-400/10 border-blue-400/30',     icon: <BarChart3 size={13}/> },
  probability: { label: '概率评估',   color: 'text-violet-400',  bg: 'bg-violet-400/10 border-violet-400/30', icon: <Scale size={13}/> },
  risk:        { label: '风险哨兵',   color: 'text-red-400',     bg: 'bg-red-400/10 border-red-400/30',       icon: <AlertTriangle size={13}/> },
  structure:   { label: '结构分析',   color: 'text-cyan-400',    bg: 'bg-cyan-400/10 border-cyan-400/30',     icon: <Search size={13}/> },
  audit:       { label: '证据审计',   color: 'text-amber-400',   bg: 'bg-amber-400/10 border-amber-400/30',   icon: <ShieldCheck size={13}/> },
  pnl:         { label: '执行核算',   color: 'text-emerald-400', bg: 'bg-emerald-400/10 border-emerald-400/30', icon: <DollarSign size={13}/> },
  // V1 兼容
  bull:        { label: '多方 Bull',    color: 'text-emerald-400', bg: 'bg-emerald-400/10 border-emerald-400/30', icon: <TrendingUp size={13}/> },
  bear:        { label: '空方 Bear',    color: 'text-red-400',     bg: 'bg-red-400/10 border-red-400/30',         icon: <TrendingDown size={13}/> },
  neutral:     { label: '中立 Neutral', color: 'text-slate-300',   bg: 'bg-slate-400/10 border-slate-400/30',     icon: <Minus size={13}/> },
}

// 动态 fallback 调色板：新增席位自动分配颜色（按 role 名 hash 取余选色）
const _FALLBACK_PALETTE = [
  { color: 'text-pink-400',   bg: 'bg-pink-400/10 border-pink-400/30' },
  { color: 'text-indigo-400', bg: 'bg-indigo-400/10 border-indigo-400/30' },
  { color: 'text-teal-400',   bg: 'bg-teal-400/10 border-teal-400/30' },
  { color: 'text-orange-400', bg: 'bg-orange-400/10 border-orange-400/30' },
  { color: 'text-lime-400',   bg: 'bg-lime-400/10 border-lime-400/30' },
  { color: 'text-fuchsia-400', bg: 'bg-fuchsia-400/10 border-fuchsia-400/30' },
]
function getRoleMeta(role: string) {
  if (ROLE_META[role]) return ROLE_META[role]
  // 自动为未知席位生成样式
  const hash = Array.from(role).reduce((h, c) => h * 31 + c.charCodeAt(0), 0)
  const palette = _FALLBACK_PALETTE[Math.abs(hash) % _FALLBACK_PALETTE.length]
  return { label: role, ...palette, icon: <Zap size={13}/> }
}

const CONSENSUS_LABEL: Record<string, { label: string; color: string }> = {
  strong_consensus:   { label: '强共识',   color: 'text-emerald-400' },
  moderate_consensus: { label: '中等共识', color: 'text-amber-400' },
  weak_consensus:     { label: '弱共识',   color: 'text-orange-400' },
  split:              { label: '意见分裂', color: 'text-red-400' },
}

const DIR_COLOR: Record<string, string> = {
  bullish: 'text-emerald-400', bearish: 'text-red-400', neutral: 'text-slate-400',
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function sourceIdsFromScope(scope: unknown) {
  if (!isRecord(scope)) return []
  return Array.isArray(scope.source_ids)
    ? scope.source_ids.filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
    : []
}

function hasTrustMetadata(debate: DebateRecord) {
  return isRecord(debate.quality_filters) || isRecord(debate.decision_guardrail) || isRecord(debate.llm_trace)
}

function DebateTrustScopeSummary({ debate }: { debate: DebateRecord }) {
  const sourceIds = sourceIdsFromScope(debate.scope)
  const scopeLabel = sourceIds.length > 0 ? `限定 ${sourceIds.length} 个资料源` : '全局知识库'
  const claimsUsed = typeof debate.claims_used === 'number'
    ? debate.claims_used
    : Array.isArray(debate.source_claim_ids) ? debate.source_claim_ids.length : 0
  const llmCalls = typeof debate.llm_call_count === 'number'
    ? debate.llm_call_count
    : isRecord(debate.llm_trace) && typeof debate.llm_trace.call_count === 'number'
    ? debate.llm_trace.call_count
    : null

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-secondary/20 px-3 py-2 text-xs text-muted-foreground">
      <span className="font-medium text-foreground">资料范围</span>
      <span className="rounded border border-border bg-card px-2 py-0.5">{scopeLabel}</span>
      <span className="rounded border border-border bg-card px-2 py-0.5">引用观点 {claimsUsed} 条</span>
      {llmCalls !== null && <span className="rounded border border-border bg-card px-2 py-0.5">模型调用 {llmCalls} 次</span>}
    </div>
  )
}

function NewDebateForm({ onClose, onDone }: { onClose: () => void; onDone: (id: string) => void }) {
  const run = useRunCommittee()
  const [question, setQuestion] = useState('')
  const [asset, setAsset] = useState('BTC')
  const [rounds, setRounds] = useState(2)
  const [error, setError] = useState('')
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault(); setError('')
    if (!question.trim()) return setError('请输入辩论问题')
    try { const res = await run.mutateAsync({ question, asset, num_rounds: rounds }); onDone(res.debate_id) }
    catch (e: unknown) { setError(e instanceof Error ? e.message : '启动失败') }
  }
  return (
    <form onSubmit={handleSubmit} className="bg-card border border-border rounded-xl p-5 space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium text-foreground">启动委员会辩论</p>
        <button type="button" onClick={onClose} className="text-xs text-muted-foreground hover:text-foreground">取消</button>
      </div>
      <div>
        <label className="text-xs text-muted-foreground block mb-1">辩论问题 *</label>
        <textarea value={question} onChange={e => setQuestion(e.target.value)} rows={3}
          placeholder="例：BTC 在未来 30 天内能否突破前高？"
          className="w-full bg-secondary border border-border rounded-lg px-3 py-2 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary resize-none" />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="text-xs text-muted-foreground block mb-1">资产</label>
          <select value={asset} onChange={e => setAsset(e.target.value)}
            className="w-full bg-secondary border border-border rounded-lg px-3 py-1.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-primary">
            {['BTC','ETH','SPX','GOLD','SOL'].map(a => <option key={a} value={a}>{a}</option>)}
          </select>
        </div>
        <div>
          <label className="text-xs text-muted-foreground block mb-1">辩论轮数</label>
          <select value={rounds} onChange={e => setRounds(Number(e.target.value))}
            className="w-full bg-secondary border border-border rounded-lg px-3 py-1.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-primary">
            <option value={1}>1 轮</option><option value={2}>2 轮</option><option value={3}>3 轮</option>
          </select>
        </div>
      </div>
      {error && <p className="text-xs text-red-400">{error}</p>}
      <button type="submit" disabled={run.isPending}
        className="w-full flex items-center justify-center gap-2 bg-primary text-primary-foreground rounded-lg py-2 text-xs font-medium disabled:opacity-50 hover:opacity-90 transition-opacity">
        {run.isPending ? <><Loader2 size={13} className="animate-spin"/>辩论进行中…</> : <><Zap size={13}/>启动辩论</>}
      </button>
    </form>
  )
}

function RoundCard({ round }: { round: DebateRound }) {
  const meta = getRoleMeta(round.role)
  return (
    <div className={clsx('border rounded-xl p-4 space-y-2', meta.bg)}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className={clsx('flex items-center gap-1 text-xs font-semibold', meta.color)}>{meta.icon}{meta.label}</span>
          <span className="text-xs text-muted-foreground">第 {round.round_number} 轮</span>
        </div>
        <div className="flex items-center gap-2">
          {round.direction && <span className={clsx('text-xs font-medium', DIR_COLOR[round.direction] ?? 'text-slate-400')}>{round.direction}</span>}
          {round.confidence != null && <span className="text-xs text-muted-foreground">{Math.round(round.confidence*100)}%</span>}
        </div>
      </div>
      <p className="text-xs text-foreground leading-relaxed">{round.position}</p>
      {round.key_arguments.length > 0 && (
        <div>
          <p className="text-xs font-medium text-muted-foreground mb-1">核心论点</p>
          {round.key_arguments.map((a,i)=><p key={i} className={clsx('text-xs flex gap-1.5', meta.color)}><span>▸</span><span className="text-foreground/80">{a}</span></p>)}
        </div>
      )}
      {round.challenges.length > 0 && (
        <div>
          <p className="text-xs font-medium text-muted-foreground mb-1">质疑对方</p>
          {round.challenges.map((c,i)=><p key={i} className="text-xs text-amber-400/80">? {c}</p>)}
        </div>
      )}
      {round.concessions.length > 0 && (
        <div>
          <p className="text-xs font-medium text-muted-foreground mb-1">承认观点</p>
          {round.concessions.map((c,i)=><p key={i} className="text-xs text-sky-400/80">✓ {c}</p>)}
        </div>
      )}
    </div>
  )
}

function ScoreBar({ label, score, color }: { label: string; score: number; color: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-[10px] text-muted-foreground w-16 shrink-0 truncate">{label}</span>
      <div className="flex-1 h-1.5 bg-border rounded-full overflow-hidden">
        <div className={clsx('h-full rounded-full', color)} style={{ width: `${Math.round(score * 100)}%` }} />
      </div>
      <span className="text-[10px] font-mono text-muted-foreground w-8 text-right">{Math.round(score * 100)}%</span>
    </div>
  )
}

function AdjudicationPanel({ adj }: { adj: DebateAdjudication }) {
  const consensus = CONSENSUS_LABEL[adj.consensus_level] ?? { label: adj.consensus_level, color: 'text-slate-400' }
  // V2 分数：优先用新字段，降级到旧字段
  const baselineScore = adj.baseline_score ?? adj.bull_score ?? 0
  const riskScore = adj.risk_score ?? adj.bear_score ?? 0
  const probScore = adj.probability_score ?? 0.5
  const structScore = adj.structure_score ?? 0.5
  const hasV2Scores = adj.baseline_score != null || adj.risk_score != null
  return (
    <div className="bg-card border-2 border-primary/30 rounded-xl p-5 space-y-3">
      <div className="flex items-center gap-2 mb-1">
        <BarChart3 size={14} className="text-primary" />
        <p className="text-sm font-semibold text-foreground">最终裁决</p>
        {adj.is_mock && <span className="text-xs text-muted-foreground border border-border rounded px-1.5">模拟</span>}
      </div>
      <div className="flex flex-wrap items-baseline gap-3">
        <span className={clsx('text-xl font-bold', DIR_COLOR[adj.direction] ?? 'text-foreground')}>{adj.direction.toUpperCase()}</span>
        <span className="text-lg font-bold text-foreground">{Math.round(adj.conviction*100)}% 确信</span>
        <span className={clsx('text-sm font-medium', consensus.color)}>{consensus.label}</span>
        <span className="text-sm font-medium text-primary">→ {adj.recommended_action.toUpperCase()}</span>
      </div>
      <p className="text-xs text-foreground/80 leading-relaxed border-l-2 border-primary/40 pl-3">{adj.synthesis}</p>
      {/* 席位评分 */}
      <div className="space-y-1.5 bg-secondary/30 rounded-lg p-3">
        <p className="text-[10px] font-semibold text-muted-foreground/60 uppercase tracking-wider mb-1">席位评分</p>
        {hasV2Scores ? (
          <>
            <ScoreBar label="基准分析" score={baselineScore} color="bg-blue-400" />
            <ScoreBar label="概率评估" score={probScore} color="bg-violet-400" />
            <ScoreBar label="风险哨兵" score={riskScore} color="bg-red-400" />
            <ScoreBar label="结构分析" score={structScore} color="bg-cyan-400" />
          </>
        ) : (
          <div className="grid grid-cols-2 gap-3">
            <div className="bg-emerald-400/5 border border-emerald-400/20 rounded-lg p-2">
              <p className="text-xs font-medium text-emerald-400">多方 {Math.round(baselineScore*100)}%</p>
            </div>
            <div className="bg-red-400/5 border border-red-400/20 rounded-lg p-2">
              <p className="text-xs font-medium text-red-400">空方 {Math.round(riskScore*100)}%</p>
            </div>
          </div>
        )}
      </div>
      {/* 风险警告 */}
      {(adj.risk_warnings?.length ?? 0) > 0 && (
        <div>
          <p className="text-xs font-medium text-muted-foreground mb-1">风险提醒</p>
          {adj.risk_warnings!.map((w,i) => <p key={i} className="text-xs text-amber-400/90 flex gap-1.5"><AlertTriangle size={10} className="mt-0.5 shrink-0"/>{w}</p>)}
        </div>
      )}
      {/* 对冲建议 */}
      {adj.hedging_advice && (
        <div className="text-xs text-foreground/70 bg-amber-400/5 border border-amber-400/15 rounded-lg px-3 py-2">
          <span className="font-medium text-amber-400">对冲建议：</span>{adj.hedging_advice}
        </div>
      )}
      {/* 执行经济性 */}
      {adj.action_economics && Object.keys(adj.action_economics).length > 0 && (() => {
        const ae = adj.action_economics as Record<string, unknown>
        const ACTION_PREF: Record<string, string> = { enter_now: '立即进场', wait: '等待更好时机', scale_in: '分批建仓', avoid: '不建议操作' }
        const VOL_REGIME: Record<string, string> = { low: '低波动', normal: '正常波动', high: '高波动', extreme: '极端波动' }
        return (
          <div className="space-y-1.5">
            <p className="text-xs font-medium text-muted-foreground mb-1">执行建议</p>
            {ae.action_preference ? (
              <p className="text-xs text-foreground/80">
                <span className="font-medium text-primary">{ACTION_PREF[String(ae.action_preference)] ?? String(ae.action_preference)}</span>
                {ae.action_timing ? <span className="text-muted-foreground">{' · 时机：'}{String(ae.action_timing)}</span> : null}
              </p>
            ) : null}
            <div className="flex flex-wrap gap-2">
              {ae.funding_cost_annualized != null && (
                <span className="text-[10px] bg-secondary/60 border border-border rounded px-2 py-1 text-foreground/70">
                  持仓年化费率 {typeof ae.funding_cost_annualized === 'number' ? `${(ae.funding_cost_annualized * 100).toFixed(2)}%` : String(ae.funding_cost_annualized)}
                </span>
              )}
              {ae.volatility_regime ? (
                <span className="text-[10px] bg-secondary/60 border border-border rounded px-2 py-1 text-foreground/70">
                  {'波动环境：'}{VOL_REGIME[String(ae.volatility_regime)] ?? String(ae.volatility_regime)}
                </span>
              ) : null}
              {ae.waiting_value_window ? (
                <span className="text-[10px] bg-secondary/60 border border-border rounded px-2 py-1 text-foreground/70">
                  {'等待窗口：'}{String(ae.waiting_value_window)}
                </span>
              ) : null}
            </div>
            {ae.hold_risk_description ? (
              <p className="text-[11px] text-foreground/60 border-l-2 border-amber-400/30 pl-2">{String(ae.hold_risk_description)}</p>
            ) : null}
            {ae.reasoning ? (
              <p className="text-[11px] text-foreground/50 italic">{String(ae.reasoning)}</p>
            ) : null}
          </div>
        )
      })()}
      {adj.key_agreements.length > 0 && <div>
        <p className="text-xs font-medium text-muted-foreground mb-1">共同认可</p>
        {adj.key_agreements.map((a,i)=><p key={i} className="text-xs text-foreground/80 flex gap-1.5"><CheckCircle2 size={10} className="text-emerald-400 mt-0.5 shrink-0"/>{a}</p>)}
      </div>}
      {adj.key_disagreements.length > 0 && <div>
        <p className="text-xs font-medium text-muted-foreground mb-1">核心分歧</p>
        {adj.key_disagreements.map((d,i)=><p key={i} className="text-xs text-foreground/80 flex gap-1.5"><AlertTriangle size={10} className="text-amber-400 mt-0.5 shrink-0"/>{d}</p>)}
      </div>}
      {/* 结构性警告 */}
      {(adj.structure_warnings?.length ?? 0) > 0 && (
        <div>
          <p className="text-xs font-medium text-muted-foreground mb-1">结构性警告</p>
          {adj.structure_warnings!.map((w,i) => <p key={i} className="text-xs text-cyan-400/80">△ {w}</p>)}
        </div>
      )}
      {adj.minority_view && <p className="text-xs text-muted-foreground italic">少数意见：{adj.minority_view}</p>}
      {adj.invalidation_conditions.length > 0 && <div>
        <p className="text-xs font-medium text-muted-foreground mb-1">裁决失效条件</p>
        {adj.invalidation_conditions.map((c,i)=><p key={i} className="text-xs text-red-400/80">✗ {c}</p>)}
      </div>}
    </div>
  )
}

function DebateDetail({ debateId, onBack }: { debateId: string; onBack: () => void }) {
  const { data, isLoading } = useDebate(debateId)
  if (isLoading) return <div className="flex justify-center py-12"><Loader2 size={16} className="animate-spin text-muted-foreground"/></div>
  if (!data) return <p className="text-xs text-muted-foreground text-center py-8">辩论记录不存在</p>

  const rounds = data.rounds ?? []
  const maxRound = Math.max(0, ...rounds.map(r => r.round_number))
  const trustPayload = { ...data, mode: 'debate', debate_id: data.id } as Record<string, unknown>
  const trustReceipt = hasTrustMetadata(data) ? buildTrustReceipt(trustPayload) : null
  const traceTarget = trustReceipt ? getTrustTraceTarget(undefined, trustPayload) : undefined
  const scopeSourceIds = sourceIdsFromScope(data.scope)

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-3">
        <button onClick={onBack} className="text-xs text-muted-foreground hover:text-foreground border border-border rounded px-2 py-1 shrink-0">← 返回</button>
        <div>
          <p className="text-sm font-medium text-foreground">{data.question}</p>
          <p className="text-xs text-muted-foreground mt-0.5">{data.asset} · {data.created_at?.slice(0,10)} · {data.duration_ms ? `${(data.duration_ms/1000).toFixed(1)}s` : ''}</p>
        </div>
      </div>

      {data.adjudication && <AdjudicationPanel adj={data.adjudication} />}

      {trustReceipt && (
        <div className="space-y-2">
          <TrustReceiptPanel receipt={trustReceipt} traceTarget={traceTarget} scopeSourceIds={scopeSourceIds} />
          <DebateTrustScopeSummary debate={data} />
        </div>
      )}

      {Array.from({ length: maxRound }, (_, i) => i+1).map(rn => {
        // 动态获取本轮所有角色（兼容 V1 和 V2）
        const roundRoles = rounds
          .filter(x => x.round_number === rn)
          .map(x => x.role)
          .filter((v, i, a) => a.indexOf(v) === i)  // unique
        const colCount = Math.min(roundRoles.length, 3)
        return (
          <div key={rn}>
            <p className="text-xs font-medium text-muted-foreground mb-2">第 {rn} 轮辩论</p>
            <div className={clsx('grid gap-3', colCount <= 2 ? 'grid-cols-1 md:grid-cols-2' : colCount <= 3 ? 'grid-cols-1 md:grid-cols-3' : 'grid-cols-1 md:grid-cols-2 lg:grid-cols-3')}>
              {roundRoles.map(role => {
                const r = rounds.find(x => x.round_number === rn && x.role === role)
                return r ? <RoundCard key={role} round={r} /> : null
              })}
            </div>
          </div>
        )
      })}
    </div>
  )
}

function DebateList({ onSelect }: { onSelect: (id: string) => void }) {
  const { data, isLoading } = useDebates({ limit: 30 })
  const debates = data?.debates ?? []
  if (isLoading) return <div className="flex justify-center py-8"><Loader2 size={16} className="animate-spin text-muted-foreground"/></div>
  if (debates.length === 0) return (
    <div className="border border-dashed border-border rounded-xl p-10 text-center">
      <p className="text-sm text-muted-foreground">暂无辩论记录</p>
      <p className="text-xs text-muted-foreground mt-1">点击「启动辩论」开始首次委员会辩论</p>
    </div>
  )
  return (
    <div className="space-y-2">
      {debates.map(d => (
        <button key={d.id} onClick={()=>onSelect(d.id)}
          className="w-full text-left bg-card border border-border rounded-xl p-4 hover:border-primary/50 transition-colors">
          <div className="flex items-start justify-between gap-3">
            <p className="text-xs text-foreground leading-relaxed flex-1">{d.question}</p>
            <div className="flex flex-col items-end gap-1 shrink-0">
              {d.final_direction && <span className={clsx('text-xs font-medium', DIR_COLOR[d.final_direction] ?? 'text-slate-400')}>{d.final_direction}</span>}
              {d.consensus_level && <span className={clsx('text-xs', CONSENSUS_LABEL[d.consensus_level]?.color ?? 'text-slate-400')}>{CONSENSUS_LABEL[d.consensus_level]?.label}</span>}
            </div>
          </div>
          <div className="flex gap-3 mt-2 text-xs text-muted-foreground">
            <span>{d.asset}</span>
            <span>{d.created_at?.slice(0,10)}</span>
            {d.final_conviction && <span>确信 {Math.round(d.final_conviction*100)}%</span>}
            <span className={d.status === 'completed' ? 'text-emerald-400' : 'text-amber-400'}>{d.status}</span>
          </div>
        </button>
      ))}
    </div>
  )
}

export default function DebateViewer() {
  const [showForm, setShowForm] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const handleDone = (id: string) => {
    setShowForm(false)
    setSelectedId(id)
  }

  return (
    <div className="p-6 2xl:p-8 max-w-[1100px] 2xl:max-w-[1300px] mx-auto space-y-5">
      <div className="flex items-start justify-between page-header !mb-5 !pb-4">
        <div>
          <span className="section-label">Debate</span>
          <h1 className="text-2xl font-semibold text-foreground">审议记录</h1>
          <p className="text-sm text-muted-foreground mt-1">V2 六席动态激活 · 多模型辩论 · 独立裁决</p>
        </div>
        {!selectedId && (
          <button onClick={()=>setShowForm(p=>!p)}
            className="flex items-center gap-1.5 bg-primary text-primary-foreground rounded-lg px-3 py-1.5 text-xs font-medium hover:opacity-90 transition-opacity">
            <Plus size={13}/>启动辩论
          </button>
        )}
      </div>

      {showForm && <NewDebateForm onClose={()=>setShowForm(false)} onDone={handleDone} />}

      {selectedId
        ? <DebateDetail debateId={selectedId} onBack={()=>setSelectedId(null)} />
        : <DebateList onSelect={setSelectedId} />
      }
    </div>
  )
}
