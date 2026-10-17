import { useState, useEffect } from 'react'
import { TrendingUp, Play, BarChart3, ArrowUpRight, ArrowDownRight } from 'lucide-react'
import { clsx } from 'clsx'
import api from '@/services/api'

// ── Types ───────────────────────────────────────────────
interface StrategyParam {
  key: string; label: string; type: string; default: number; min: number; max: number;
}
interface Strategy {
  id: string; name: string; description: string; params: StrategyParam[];
}
interface BacktestResult {
  asset: string; strategy_id: string; params: Record<string, number>;
  metrics: {
    total_return_pct: number; buy_hold_return_pct: number; alpha_pct: number;
    max_drawdown_pct: number; total_trades: number; win_rate_pct: number;
    final_equity: number; initial_capital: number;
  };
  trades: Array<{ type: string; date: string; price: number; units?: number; pnl_pct?: number }>;
  equity_curve: Array<{ date: string; equity: number; price: number }>;
  period_start: string; period_end: string; data_points: number;
  error?: string;
}

const CARD = { background: 'hsl(var(--card))', border: '1px solid hsl(var(--border))' } as const
const PERIODS = [
  { value: '3mo', label: '3 个月' },
  { value: '6mo', label: '6 个月' },
  { value: '1y', label: '1 年' },
  { value: '2y', label: '2 年' },
  { value: '5y', label: '5 年' },
]
const ASSETS = [
  { symbol: 'BTC', label: 'BTC', cat: '加密货币' },
  { symbol: 'ETH', label: 'ETH', cat: '加密货币' },
  { symbol: 'SPX', label: 'S&P 500', cat: '美股指数' },
  { symbol: 'NDX', label: 'NASDAQ', cat: '美股指数' },
  { symbol: 'GOLD', label: '黄金', cat: '大宗商品' },
  { symbol: 'SILVER', label: '白银', cat: '大宗商品' },
  { symbol: 'OIL', label: '原油', cat: '大宗商品' },
]

// ── Equity Chart (SVG) ──────────────────────────────────
function EquityChart({ data }: { data: Array<{ date: string; equity: number; price: number }> }) {
  if (data.length < 2) return null
  const W = 800, H = 200, PAD = 40

  const eqVals = data.map(d => d.equity)
  // Normalize price to same scale as equity for comparison
  const prVals = data.map(d => d.price)
  const initEq = eqVals[0], initPr = prVals[0]
  const eqNorm = eqVals.map(v => v / initEq)
  const prNorm = prVals.map(v => v / initPr)
  const allNorm = [...eqNorm, ...prNorm]
  const mn = Math.min(...allNorm), mx = Math.max(...allNorm)
  const rng = mx - mn || 0.01

  const toY = (v: number) => PAD + (H - 2 * PAD) * (1 - (v - mn) / rng)
  const toX = (i: number) => PAD + (i / (data.length - 1)) * (W - 2 * PAD)

  const eqPts = eqNorm.map((v, i) => `${toX(i).toFixed(1)},${toY(v).toFixed(1)}`).join(' ')
  const prPts = prNorm.map((v, i) => `${toX(i).toFixed(1)},${toY(v).toFixed(1)}`).join(' ')

  // Y-axis labels
  const yTicks = 5
  const yLabels = Array.from({ length: yTicks + 1 }, (_, i) => {
    const v = mn + (rng * i) / yTicks
    return { y: toY(v), label: `${((v - 1) * 100).toFixed(0)}%` }
  })

  // X-axis labels (show 5 dates)
  const xStep = Math.floor(data.length / 5)
  const xLabels = [0, xStep, xStep * 2, xStep * 3, xStep * 4, data.length - 1]
    .filter((v, i, a) => a.indexOf(v) === i && v < data.length)
    .map(i => ({ x: toX(i), label: data[i].date.slice(5) }))

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ maxHeight: 220 }}>
      {/* Grid */}
      {yLabels.map((t, i) => (
        <g key={i}>
          <line x1={PAD} y1={t.y} x2={W - PAD} y2={t.y} stroke="hsl(224 11% 17%)" strokeWidth="0.5" />
          <text x={PAD - 4} y={t.y + 3} textAnchor="end" fill="hsl(224 11% 35%)" fontSize="9">{t.label}</text>
        </g>
      ))}
      {xLabels.map((t, i) => (
        <text key={i} x={t.x} y={H - 8} textAnchor="middle" fill="hsl(224 11% 35%)" fontSize="9">{t.label}</text>
      ))}
      {/* Buy & Hold line */}
      <polyline points={prPts} fill="none" stroke="hsl(224 11% 35%)" strokeWidth="1" strokeDasharray="3,3" opacity={0.5} />
      {/* Strategy equity line */}
      <polyline points={eqPts} fill="none" stroke="#34d399" strokeWidth="1.5" />
      {/* Legend */}
      <line x1={W - PAD - 120} y1={12} x2={W - PAD - 100} y2={12} stroke="#34d399" strokeWidth="1.5" />
      <text x={W - PAD - 96} y={15} fill="#34d399" fontSize="10">策略收益</text>
      <line x1={W - PAD - 120} y1={26} x2={W - PAD - 100} y2={26} stroke="hsl(224 11% 35%)" strokeWidth="1" strokeDasharray="3,3" />
      <text x={W - PAD - 96} y={29} fill="hsl(224 11% 45%)" fontSize="10">持有不动</text>
    </svg>
  )
}

// ── Metric Card ─────────────────────────────────────────
function Metric({ label, value, suffix = '', positive }: { label: string; value: string; suffix?: string; positive?: boolean | null }) {
  return (
    <div className="rounded-xl p-3" style={{ background: 'hsl(var(--secondary))' }}>
      <p className="text-[10px] text-muted-foreground/60 font-medium">{label}</p>
      <p className={clsx('text-[16px] font-semibold font-mono tabular-nums mt-1',
        positive === true ? 'text-emerald-400' : positive === false ? 'text-red-400' : 'text-foreground')}>
        {value}<span className="text-[10px] text-muted-foreground/40 font-normal ml-0.5">{suffix}</span>
      </p>
    </div>
  )
}

// ── Main Page ───────────────────────────────────────────
export default function Backtest() {
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [strategyId, setStrategyId] = useState('ma_crossover')
  const [asset, setAsset] = useState('BTC')
  const [period, setPeriod] = useState('1y')
  const [capital, setCapital] = useState('100000')
  const [params, setParams] = useState<Record<string, number>>({})
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<BacktestResult | null>(null)

  // Load strategies
  useEffect(() => {
    api.get('/api/v1/backtest/strategies').then(r => {
      const strats = r.data?.strategies ?? []
      setStrategies(strats)
      if (strats.length > 0) {
        const defaults: Record<string, number> = {}
        strats[0].params.forEach((p: StrategyParam) => { defaults[p.key] = p.default })
        setParams(defaults)
      }
    }).catch(() => {})
  }, [])

  // Update params when strategy changes
  useEffect(() => {
    const strat = strategies.find(s => s.id === strategyId)
    if (strat) {
      const defaults: Record<string, number> = {}
      strat.params.forEach(p => { defaults[p.key] = p.default })
      setParams(defaults)
    }
  }, [strategyId, strategies])

  const runBacktest = async () => {
    setRunning(true)
    setResult(null)
    try {
      const r = await api.post('/api/v1/backtest/run', {
        asset, strategy_id: strategyId, params, period,
        initial_capital: parseFloat(capital) || 100000,
      })
      setResult(r.data)
    } catch (e) {
      setResult({ error: '回测请求失败' } as any)
    }
    setRunning(false)
  }

  const curStrategy = strategies.find(s => s.id === strategyId)
  const m = result?.metrics

  return (
    <div className="p-6 2xl:p-8 space-y-5 max-w-[1400px] 2xl:max-w-[1760px] mx-auto">
      <div className="page-header !mb-5 !pb-4">
        <span className="section-label">Backtest</span>
        <h1 className="text-2xl font-semibold text-foreground flex items-center gap-2">
          <TrendingUp size={20} className="text-primary" />
          策略回测
        </h1>
        <p className="text-sm text-muted-foreground mt-1">选择策略与参数，基于历史数据回测并查看绩效</p>
      </div>

      {/* ═══════ Config Panel ═══════ */}
      <div className="grid grid-cols-4 gap-3">
        {/* Strategy Selection */}
        <div className="col-span-2 rounded-xl p-4" style={CARD}>
          <p className="text-xs font-semibold text-muted-foreground mb-3">策略选择</p>
          <div className="space-y-1.5">
            {strategies.map(s => (
              <button key={s.id} onClick={() => setStrategyId(s.id)}
                className={clsx('w-full text-left px-3 py-2 rounded-lg transition-colors',
                  s.id === strategyId ? 'bg-secondary text-foreground' : 'text-muted-foreground hover:text-foreground hover:bg-secondary/50')}>
                <p className="text-sm font-medium">{s.name}</p>
                <p className="text-xs opacity-50 mt-0.5">{s.description}</p>
              </button>
            ))}
          </div>
        </div>

        {/* Parameters */}
        <div className="col-span-2 rounded-xl p-4 space-y-4" style={CARD}>
          <p className="text-xs font-semibold text-muted-foreground">参数配置</p>

          {/* Asset */}
          <div>
            <label className="text-xs text-muted-foreground block mb-1">资产</label>
            <div className="flex flex-wrap gap-1">
              {ASSETS.map(a => (
                <button key={a.symbol} onClick={() => setAsset(a.symbol)}
                  className={asset === a.symbol ? 'filter-btn-active !text-xs !px-2.5 !py-1' : 'filter-btn !text-xs !px-2.5 !py-1'}>
                  {a.symbol}
                </button>
              ))}
            </div>
          </div>

          {/* Period */}
          <div>
            <label className="text-xs text-muted-foreground block mb-1">回测周期</label>
            <div className="flex gap-1">
              {PERIODS.map(p => (
                <button key={p.value} onClick={() => setPeriod(p.value)}
                  className={period === p.value ? 'filter-btn-active !text-xs !px-2.5 !py-1' : 'filter-btn !text-xs !px-2.5 !py-1'}>
                  {p.label}
                </button>
              ))}
            </div>
          </div>

          {/* Initial Capital */}
          <div>
            <label className="text-xs text-muted-foreground block mb-1">初始资金</label>
            <input type="number" value={capital} onChange={e => setCapital(e.target.value)}
              className="w-full h-8 rounded-lg px-3 text-[12px] font-mono text-foreground outline-none"
              style={{ background: 'hsl(var(--secondary))', border: '1px solid hsl(var(--border))' }} />
          </div>

          {/* Strategy-specific params */}
          {curStrategy && curStrategy.params.map(p => (
            <div key={p.key}>
              <label className="text-xs text-muted-foreground block mb-1">{p.label} ({p.min}-{p.max})</label>
              <input type="number" value={params[p.key] ?? p.default}
                min={p.min} max={p.max} step={p.type === 'float' ? 0.1 : 1}
                onChange={e => setParams(prev => ({ ...prev, [p.key]: parseFloat(e.target.value) || p.default }))}
                className="w-full h-8 rounded-lg px-3 text-[12px] font-mono text-foreground outline-none"
                style={{ background: 'hsl(var(--secondary))', border: '1px solid hsl(var(--border))' }} />
            </div>
          ))}

          {/* Run */}
          <button onClick={runBacktest} disabled={running}
            className={clsx('w-full h-10 rounded-lg text-sm font-semibold flex items-center justify-center gap-2 transition-colors',
              running ? 'opacity-50 cursor-not-allowed' : 'hover:opacity-90')}
            style={{ background: 'hsl(var(--primary))', color: 'hsl(var(--primary-foreground))' }}>
            <Play size={14} />
            {running ? '回测运行中...' : '运行回测'}
          </button>
        </div>
      </div>

      {/* ═══════ Results ═══════ */}
      {result?.error && (
        <div className="rounded-xl p-4 text-sm text-red-400" style={{ background: 'hsl(0 50% 12% / 0.5)', border: '1px solid hsl(0 50% 20% / 0.5)' }}>
          {result.error}
        </div>
      )}

      {m && !result?.error && (
        <>
          {/* Metrics Grid */}
          <div className="rounded-xl p-4" style={CARD}>
            <div className="flex items-center gap-2 mb-3">
              <BarChart3 size={14} className="text-primary" />
              <h3 className="text-sm font-semibold text-foreground">回测结果</h3>
              <span className="text-xs text-muted-foreground ml-auto">{result.period_start} → {result.period_end} · {result.data_points} 交易日</span>
            </div>
            <div className="grid grid-cols-6 gap-2">
              <Metric label="策略收益" value={`${m.total_return_pct >= 0 ? '+' : ''}${m.total_return_pct.toFixed(1)}`} suffix="%" positive={m.total_return_pct >= 0} />
              <Metric label="持有收益" value={`${m.buy_hold_return_pct >= 0 ? '+' : ''}${m.buy_hold_return_pct.toFixed(1)}`} suffix="%" positive={m.buy_hold_return_pct >= 0} />
              <Metric label="Alpha" value={`${m.alpha_pct >= 0 ? '+' : ''}${m.alpha_pct.toFixed(1)}`} suffix="%" positive={m.alpha_pct >= 0} />
              <Metric label="最大回撤" value={m.max_drawdown_pct.toFixed(1)} suffix="%" positive={false} />
              <Metric label="交易次数" value={String(m.total_trades)} />
              <Metric label="胜率" value={m.win_rate_pct.toFixed(0)} suffix="%" positive={m.win_rate_pct >= 50 ? true : m.win_rate_pct < 40 ? false : null} />
            </div>
          </div>

          {/* Equity Curve Chart */}
          {result.equity_curve && result.equity_curve.length > 0 && (
            <div className="rounded-xl p-4" style={CARD}>
              <p className="text-xs font-semibold text-muted-foreground mb-2">收益曲线 vs 持有不动</p>
              <EquityChart data={result.equity_curve} />
            </div>
          )}

          {/* Trade List */}
          {result.trades && result.trades.length > 0 && (
            <div className="rounded-xl p-4" style={CARD}>
              <p className="text-xs font-semibold text-muted-foreground mb-2">交易记录（近 {result.trades.length} 笔）</p>
              <div className="max-h-[300px] overflow-y-auto">
                <table className="w-full">
                  <thead>
                    <tr className="text-xs text-muted-foreground font-medium" style={{ borderBottom: '1px solid hsl(var(--border))' }}>
                      <th className="text-left px-3 py-2">方向</th>
                      <th className="text-left px-3 py-2">日期</th>
                      <th className="text-right px-3 py-2">价格</th>
                      <th className="text-right px-3 py-2">盈亏</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.trades.map((t, i) => (
                      <tr key={i} className="hover:bg-secondary/30 transition-colors" style={{ borderBottom: '1px solid hsl(var(--border) / 0.5)' }}>
                        <td className="px-3 py-1.5">
                          <span className={clsx('inline-flex items-center gap-1 text-[11px] font-medium',
                            t.type === 'buy' ? 'text-emerald-400' : 'text-red-400')}>
                            {t.type === 'buy' ? <ArrowUpRight size={11} /> : <ArrowDownRight size={11} />}
                            {t.type === 'buy' ? '买入' : '卖出'}
                          </span>
                        </td>
                        <td className="px-3 py-1.5 text-[11px] font-mono text-muted-foreground">{t.date}</td>
                        <td className="px-3 py-1.5 text-[11px] font-mono text-foreground text-right">{t.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}</td>
                        <td className={clsx('px-3 py-1.5 text-[11px] font-mono text-right',
                          t.pnl_pct != null ? (t.pnl_pct >= 0 ? 'text-emerald-400' : 'text-red-400') : 'text-muted-foreground/30')}>
                          {t.pnl_pct != null ? `${t.pnl_pct >= 0 ? '+' : ''}${t.pnl_pct.toFixed(2)}%` : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}

      {/* Empty state */}
      {!result && !running && (
        <div className="rounded-xl p-12 text-center" style={CARD}>
          <BarChart3 size={32} className="text-muted-foreground/20 mx-auto mb-3" />
          <p className="text-sm text-muted-foreground">选择策略和参数，点击「运行回测」查看结果</p>
          <p className="text-xs text-muted-foreground/50 mt-1">支持 BTC、ETH、美股指数、黄金、原油等资产</p>
        </div>
      )}
    </div>
  )
}
