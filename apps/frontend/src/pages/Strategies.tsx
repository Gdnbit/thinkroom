import { useState } from 'react'
import {
  ChevronDown, ChevronRight, Loader2, CheckCircle2, XCircle,
  TrendingUp, BarChart3
} from 'lucide-react'
import { clsx } from 'clsx'
import {
  useStrategies, useStrategy, useEvaluateStrategy, useDistillStrategy,
  type SetupResult
} from '@/services/hooks'

// ── helpers ──────────────────────────────────────────────
const ACTION_COLOR: Record<string, string> = {
  enter: 'text-emerald-400 bg-emerald-400/10 border-emerald-400/30',
  watch: 'text-amber-400  bg-amber-400/10  border-amber-400/30',
  avoid: 'text-red-400    bg-red-400/10    border-red-400/30',
  wait:  'text-sky-400   bg-sky-400/10   border-sky-400/30',
}

function ScoreBar({ value, label }: { value: number; label: string }) {
  const pct = Math.round(value * 100)
  const color = pct >= 70 ? 'bg-emerald-400' : pct >= 40 ? 'bg-amber-400' : 'bg-red-400'
  return (
    <div>
      <div className="flex justify-between text-xs text-muted-foreground mb-1">
        <span>{label}</span><span className="font-mono">{pct}%</span>
      </div>
      <div className="h-1.5 bg-secondary rounded-full overflow-hidden">
        <div className={clsx('h-full rounded-full transition-all', color)} style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

// ── SetupCard panel ──────────────────────────────────────
function SetupPanel({ setup }: { setup: SetupResult }) {
  const actionCls = ACTION_COLOR[setup.recommended_action] ?? ACTION_COLOR.watch
  return (
    <div className="mt-4 border border-border rounded-xl p-4 bg-card space-y-4">
      <div className="flex items-center justify-between">
        <span className="text-xs text-muted-foreground">Setup 评估 · {setup.asset}</span>
        <span className={clsx('text-xs font-medium border rounded-full px-2 py-0.5', actionCls)}>
          {setup.recommended_action.toUpperCase()}
        </span>
      </div>
      <div className="space-y-2">
        <ScoreBar value={setup.strategy_fit_score} label="策略匹配度" />
        <ScoreBar value={setup.evidence_strength_score} label="证据强度" />
        <ScoreBar value={setup.overall_score} label="综合评分" />
      </div>
      {setup.action_notes && (
        <p className="text-xs text-muted-foreground border-l-2 border-primary pl-3">{setup.action_notes}</p>
      )}
      {setup.matched_conditions.length > 0 && (
        <div>
          <p className="text-xs font-medium text-foreground mb-1">已满足条件</p>
          <ul className="space-y-1">
            {setup.matched_conditions.map((c, i) => (
              <li key={i} className="flex items-start gap-1.5 text-xs text-emerald-400">
                <CheckCircle2 size={11} className="mt-0.5 shrink-0" />{c}
              </li>
            ))}
          </ul>
        </div>
      )}
      {setup.unmatched_conditions.length > 0 && (
        <div>
          <p className="text-xs font-medium text-foreground mb-1">未满足条件</p>
          <ul className="space-y-1">
            {setup.unmatched_conditions.map((c, i) => (
              <li key={i} className="flex items-start gap-1.5 text-xs text-muted-foreground">
                <XCircle size={11} className="mt-0.5 shrink-0" />{c}
              </li>
            ))}
          </ul>
        </div>
      )}
      {setup.disqualifying_flags.length > 0 && (
        <div className="border border-red-400/20 rounded-lg p-2 bg-red-400/5">
          <p className="text-xs font-medium text-red-400 mb-1">风险标记</p>
          {setup.disqualifying_flags.map((f, i) => (
            <p key={i} className="text-xs text-red-400/80">{f}</p>
          ))}
        </div>
      )}
      {setup.is_mock && (
        <p className="text-xs text-muted-foreground/50">* mock 模式，信号来自持久化种子数据</p>
      )}
    </div>
  )
}

// ── Strategy detail expandable panel ─────────────────────
function StrategyDetail({ id }: { id: string }) {
  const { data: s, isLoading } = useStrategy(id)
  const evaluateMutation = useEvaluateStrategy()
  const [evalAsset, setEvalAsset] = useState('BTC')
  const [setupResult, setSetupResult] = useState<SetupResult | null>(null)
  const [evalError, setEvalError] = useState('')

  if (isLoading) return <div className="p-4 text-xs text-muted-foreground">加载中...</div>
  if (!s) return <div className="p-4 text-xs text-red-400">加载失败</div>

  const handleEval = async () => {
    setEvalError('')
    try {
      const result = await evaluateMutation.mutateAsync({ strategyId: id, asset: evalAsset })
      setSetupResult(result)
    } catch (e: unknown) {
      setEvalError(e instanceof Error ? e.message : '评估失败')
    }
  }

  return (
    <div className="px-4 pb-5 pt-3 border-t border-border bg-secondary/20 space-y-4">
      {s.core_logic && (
        <div>
          <p className="text-xs font-medium text-foreground mb-1">核心逻辑</p>
          <p className="text-xs text-muted-foreground">{s.core_logic}</p>
        </div>
      )}

      {s.entry_conditions.length > 0 && (
        <div>
          <p className="text-xs font-medium text-foreground mb-1">入场条件</p>
          <ul className="space-y-1">
            {s.entry_conditions.map((c, i) => (
              <li key={i} className="flex items-start gap-1.5 text-xs text-muted-foreground">
                <span className="text-emerald-400 shrink-0 mt-0.5">▸</span>{c}
              </li>
            ))}
          </ul>
        </div>
      )}

      {s.exit_rules && s.exit_rules.length > 0 && (
        <div>
          <p className="text-xs font-medium text-foreground mb-1">出场规则</p>
          <ul className="space-y-1">
            {s.exit_rules.map((r, i) => (
              <li key={i} className="text-xs text-muted-foreground">• {r}</li>
            ))}
          </ul>
        </div>
      )}

      {s.risk_factors && s.risk_factors.length > 0 && (
        <div>
          <p className="text-xs font-medium text-amber-400 mb-1">风险因素</p>
          <ul className="space-y-1">
            {s.risk_factors.map((r, i) => (
              <li key={i} className="text-xs text-amber-400/70">⚠ {r}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Evaluate */}
      <div className="border border-border rounded-xl p-3 bg-card space-y-3">
        <p className="text-xs font-medium text-foreground flex items-center gap-1.5">
          <BarChart3 size={12} className="text-primary" />当前市场评估
        </p>
        <div className="flex gap-2">
          <select
            value={evalAsset}
            onChange={e => setEvalAsset(e.target.value)}
            className="flex-1 bg-secondary border border-border rounded-lg px-2 py-1.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
          >
            {['BTC', 'ETH', 'SPX', 'GOLD', 'DXY'].map(a => (
              <option key={a} value={a}>{a}</option>
            ))}
          </select>
          <button
            onClick={handleEval}
            disabled={evaluateMutation.isPending}
            className="flex items-center gap-1.5 bg-primary text-primary-foreground rounded-lg px-3 py-1.5 text-xs font-medium disabled:opacity-50 hover:opacity-90 transition-opacity"
          >
            {evaluateMutation.isPending
              ? <><Loader2 size={11} className="animate-spin" />评估中</>
              : <><ChevronRight size={11} />运行评估</>}
          </button>
        </div>
        {evalError && <p className="text-xs text-red-400">{evalError}</p>}
        {setupResult && <SetupPanel setup={setupResult} />}
      </div>
    </div>
  )
}

// ── Strategy card ─────────────────────────────────────────
const TYPE_LABEL: Record<string, string> = {
  momentum: '动量', mean_reversion: '均值回归', breakout: '突破',
  trend_following: '趋势跟踪', volatility: '波动率', fundamental: '基本面', custom: '自定义',
}

function StrategyCard({ s }: { s: ReturnType<typeof useStrategies>['data'] extends (infer U)[] | undefined ? U : never }) {
  const [open, setOpen] = useState(false)
  if (!s) return null
  const confPct = Math.round(s.confidence * 100)
  return (
    <div className="border border-border rounded-xl bg-card overflow-hidden hover:border-primary/40 transition-colors group">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-start gap-3 p-4 hover:bg-secondary/40 transition-colors text-left"
      >
        <div className="mt-0.5 shrink-0 text-muted-foreground">
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2 mb-1">
            <span className="text-sm font-medium text-foreground truncate">{s.strategy_name}</span>
            <span className="text-xs text-muted-foreground bg-secondary border border-border rounded-full px-2 py-0.5">
              {TYPE_LABEL[s.strategy_type] ?? s.strategy_type}
            </span>
            <span className="ml-auto text-xs text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity">
              {open ? '收起详情 ↑' : '展开查看详情 ↓'}
            </span>
          </div>
          <p className="text-xs text-muted-foreground line-clamp-2 mb-2">{s.summary}</p>
          <div className="flex flex-wrap gap-1.5 items-center">
            {s.applicable_assets.map(a => (
              <span key={a} className="text-xs bg-primary/10 text-primary border border-primary/20 rounded px-1.5 py-0.5">{a}</span>
            ))}
            <div className="ml-auto flex items-center gap-2">
              <div className="w-16 h-1.5 bg-secondary rounded-full overflow-hidden">
                <div className={clsx('h-full rounded-full',
                  confPct >= 70 ? 'bg-emerald-400' : confPct >= 50 ? 'bg-amber-400' : 'bg-red-400'
                )} style={{ width: `${confPct}%` }} />
              </div>
              <span className="text-xs text-muted-foreground">{confPct}%</span>
            </div>
          </div>
        </div>
      </button>
      {open && <StrategyDetail id={s.id} />}
    </div>
  )
}

// ── Distill from source panel ─────────────────────────────
function DistillPanel() {
  const [sourceId, setSourceId] = useState('')
  const [result, setResult] = useState<{ success: boolean; strategy_name?: string; error?: string } | null>(null)
  const distillMutation = useDistillStrategy()

  const handle = async () => {
    if (!sourceId.trim()) return
    setResult(null)
    try {
      const r = await distillMutation.mutateAsync(sourceId.trim())
      setResult(r)
    } catch (e: unknown) {
      setResult({ success: false, error: e instanceof Error ? e.message : '蒸馏失败' })
    }
  }

  return (
    <div className="border border-dashed border-border rounded-xl p-4 bg-card/50 space-y-3">
      <p className="text-xs font-medium text-foreground">从上传文档蒸馏策略</p>
      <p className="text-xs text-muted-foreground">输入已上传 RawSource 的 ID，系统将抽取策略规则并落库</p>
      <div className="flex gap-2">
        <input
          value={sourceId}
          onChange={e => setSourceId(e.target.value)}
          placeholder="RawSource ID…"
          className="flex-1 bg-secondary border border-border rounded-lg px-3 py-1.5 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary"
        />
        <button
          onClick={handle}
          disabled={distillMutation.isPending || !sourceId.trim()}
          className="flex items-center gap-1.5 bg-primary text-primary-foreground rounded-lg px-3 py-1.5 text-xs font-medium disabled:opacity-50 hover:opacity-90 transition-opacity"
        >
          {distillMutation.isPending ? <><Loader2 size={11} className="animate-spin" />蒸馏中</> : '蒸馏'}
        </button>
      </div>
      {result && (
        <div className={clsx('text-xs rounded-lg px-3 py-2',
          result.success ? 'bg-emerald-400/10 text-emerald-400' : 'bg-red-400/10 text-red-400'
        )}>
          {result.success ? `✓ 策略已生成：${result.strategy_name}` : `✗ ${result.error}`}
        </div>
      )}
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────
const ASSETS = ['', 'BTC', 'ETH', 'SPX', 'GOLD', 'DXY']
const TYPES  = ['', 'momentum', 'mean_reversion', 'breakout', 'trend_following', 'volatility', 'fundamental', 'custom']
const TYPE_ZH: Record<string, string> = {
  '': '全部类型', momentum: '动量', mean_reversion: '均值回归', breakout: '突破',
  trend_following: '趋势跟踪', volatility: '波动率', fundamental: '基本面', custom: '自定义',
}

export default function Strategies() {
  const [asset, setAsset] = useState('')
  const [strategyType, setStrategyType] = useState('')
  const { data: strategies, isLoading, refetch } = useStrategies({
    asset: asset || undefined,
    strategy_type: strategyType || undefined,
    limit: 50,
  })

  return (
    <div className="p-6 2xl:p-8 max-w-[900px] 2xl:max-w-[1100px] mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between page-header !mb-5 !pb-4">
        <div>
          <span className="section-label">Strategies</span>
          <h1 className="text-2xl font-semibold text-foreground">策略候选库</h1>
          <p className="text-sm text-muted-foreground mt-1">管理与评估待确认的策略模板，避免把自动蒸馏结果直接视为正式决策</p>
        </div>
        <button
          onClick={() => refetch()}
          className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground border border-border rounded-lg px-3 py-1.5 transition-colors"
        >
          <TrendingUp size={12} />刷新
        </button>
      </div>

      {/* Distill panel */}
      <DistillPanel />

      {/* Filters */}
      <div className="flex gap-2">
        <select
          value={asset}
          onChange={e => setAsset(e.target.value)}
          className="bg-secondary border border-border rounded-lg px-2 py-1.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
        >
          {ASSETS.map(a => <option key={a} value={a}>{a || '全部资产'}</option>)}
        </select>
        <select
          value={strategyType}
          onChange={e => setStrategyType(e.target.value)}
          className="bg-secondary border border-border rounded-lg px-2 py-1.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
        >
          {TYPES.map(t => <option key={t} value={t}>{TYPE_ZH[t]}</option>)}
        </select>
        <span className="ml-auto text-xs text-muted-foreground self-center">
          {strategies ? `${strategies.length} 条策略` : ''}
        </span>
      </div>

      {/* List */}
      {isLoading && (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 size={14} className="animate-spin" />加载策略库...
        </div>
      )}

      {!isLoading && strategies?.length === 0 && (
        <div className="border border-dashed border-border rounded-xl p-8 text-center">
          <p className="text-sm text-muted-foreground mb-1">策略库为空</p>
          <p className="text-xs text-muted-foreground">在上方输入 RawSource ID 进行蒸馏，或上传策略文章后在 Inbox 处理</p>
        </div>
      )}

      <div className="space-y-3">
        {strategies?.map(s => <StrategyCard key={s.id} s={s} />)}
      </div>
    </div>
  )
}
