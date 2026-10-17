/**
 * G3 · 基金估值 EOD 对账页面
 *
 * 工作流：
 * 1. scheduler 每天 17:00 自动跑 mark_eod_finals 标记当日最后一条估算
 * 2. 用户在 / 基金对账 页面看到「待填实际 NAV」清单 → 手动填入
 * 3. 系统自动算 MAE / RMSE / MAPE / 系统性偏差，画 est vs actual 曲线
 *
 * 三大块：
 *  顶部摘要卡（按 fund 分组的误差指标）
 *  待填列表（点击行 → 弹出输入框 → POST 实际 NAV）
 *  时间序列图（est vs actual 双线）
 */
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid, ResponsiveContainer, Legend,
} from 'recharts'
import {
  Calculator, Clock, RefreshCw, Plus, AlertCircle, CheckCircle2, XCircle, Loader2,
} from 'lucide-react'
import api from '@/services/api'

// ── 类型 ──────────────────────────────────────────────

interface PendingItem {
  id: string
  fund_code: string
  ts: string
  est_nav: number | null
  est_pct_change_1d: number | null
  confidence_level: string
  ref_index: string | null
}

interface SummaryRow {
  fund_code: string
  sample_count: number
  pending_count: number
  mae_pct: number | null
  rmse_pct: number | null
  mape_pct: number | null
  bias_pct: number | null
  max_error_pct: number | null
  last_eod_at: string | null
  since_days: number
}

interface TimeseriesPoint {
  id: string
  ts: string
  est_nav: number
  actual_nav: number
  error_pct: number
  abs_error_pct: number
  ref_index: string | null
  confidence_level: string
}

// ── Hooks ─────────────────────────────────────────────

function usePending(fundCode?: string) {
  return useQuery({
    queryKey: ['fund-recon', 'pending', fundCode],
    queryFn: () =>
      api.get<PendingItem[]>('/api/v1/fund-reconciliation/pending', {
        params: fundCode ? { fund_code: fundCode } : {},
      }).then(r => r.data),
    refetchInterval: 60000,
  })
}

function useSummary(sinceDays: number) {
  return useQuery({
    queryKey: ['fund-recon', 'summary', sinceDays],
    queryFn: () =>
      api.get<SummaryRow[]>('/api/v1/fund-reconciliation/summary', {
        params: { since_days: sinceDays },
      }).then(r => r.data),
    refetchInterval: 60000,
  })
}

function useTimeseries(fundCode: string | null, sinceDays: number) {
  return useQuery({
    queryKey: ['fund-recon', 'timeseries', fundCode, sinceDays],
    queryFn: () =>
      api.get<TimeseriesPoint[]>('/api/v1/fund-reconciliation/timeseries', {
        params: { fund_code: fundCode, since_days: sinceDays },
      }).then(r => r.data),
    enabled: !!fundCode,
  })
}

function useFillActual() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ snapshotId, actualNav }: { snapshotId: string; actualNav: number }) =>
      api.post(`/api/v1/fund-reconciliation/${snapshotId}/actual`, {
        actual_nav: actualNav,
      }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['fund-recon'] })
    },
  })
}

function useRunEod() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.post('/api/v1/fund-reconciliation/run-eod').then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['fund-recon'] })
    },
  })
}

// ── Helpers ───────────────────────────────────────────

function formatTime(iso: string | null, brief = false): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return brief
    ? d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit' })
    : d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function biasColorClass(bias: number | null): string {
  if (bias === null) return 'text-muted-foreground'
  const abs = Math.abs(bias)
  if (abs < 0.5) return 'text-green-500'
  if (abs < 1.5) return 'text-amber-500'
  return 'text-red-500'
}

function maeColorClass(mae: number | null): string {
  if (mae === null) return 'text-muted-foreground'
  if (mae < 0.5) return 'text-green-500'
  if (mae < 1.5) return 'text-amber-500'
  return 'text-red-500'
}

// ── 主组件 ────────────────────────────────────────────

export default function FundReconciliation() {
  const [sinceDays, setSinceDays] = useState(30)
  const [selectedFund, setSelectedFund] = useState<string | null>(null)
  const [fillingId, setFillingId] = useState<string | null>(null)
  const [fillValue, setFillValue] = useState('')
  const [fillError, setFillError] = useState<string | null>(null)

  const pending = usePending()
  const summary = useSummary(sinceDays)
  const timeseries = useTimeseries(selectedFund, sinceDays)
  const fillActual = useFillActual()
  const runEod = useRunEod()

  // 自动选第一只 fund 用于时间序列
  if (!selectedFund && summary.data && summary.data.length > 0) {
    const firstWithSamples = summary.data.find(s => s.sample_count > 0) ?? summary.data[0]
    setSelectedFund(firstWithSamples.fund_code)
  }

  const handleFill = async (id: string) => {
    setFillError(null)
    const v = parseFloat(fillValue)
    if (!v || v <= 0) {
      setFillError('请输入大于 0 的真实净值')
      return
    }
    try {
      await fillActual.mutateAsync({ snapshotId: id, actualNav: v })
      setFillingId(null)
      setFillValue('')
    } catch (e: unknown) {
      const errMsg = e instanceof Error ? e.message : '保存失败'
      setFillError(errMsg)
    }
  }

  const refetchAll = () => {
    pending.refetch(); summary.refetch(); timeseries.refetch()
  }

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <h1 className="text-2xl font-semibold flex items-center gap-2">
            <Calculator className="w-5 h-5 text-primary" />
            基金核对
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            scheduler 每天 17:00（北京时间）自动标记当日最后一次估算 →
            你手动填入真实净值 → 系统计算误差统计
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={sinceDays}
            onChange={e => setSinceDays(Number(e.target.value))}
            className="px-3 py-1.5 rounded-md bg-background border border-border text-sm"
          >
            <option value={7}>近 7 天</option>
            <option value={30}>近 30 天</option>
            <option value={90}>近 90 天</option>
            <option value={180}>近 180 天</option>
            <option value={365}>近 1 年</option>
          </select>
          <button
            onClick={() => runEod.mutate()}
            disabled={runEod.isPending}
            className="px-3 py-1.5 rounded-md bg-secondary hover:bg-secondary/80 text-sm font-medium flex items-center gap-1.5 disabled:opacity-50"
            title="手动触发 EOD 标记（一般由 scheduler 自动跑）"
          >
            {runEod.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Clock className="w-3.5 h-3.5" />}
            手动 EOD
          </button>
          <button
            onClick={refetchAll}
            className="p-1.5 rounded-md hover:bg-secondary text-muted-foreground hover:text-foreground transition-colors"
            title="立即刷新"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* runEod result toast */}
      {runEod.data && (
        <div className="rounded-lg bg-green-500/10 border border-green-500/30 px-4 py-2 text-sm flex items-center gap-2">
          <CheckCircle2 className="w-4 h-4 text-green-500" />
          EOD 标记完成：marked={runEod.data.marked} ·
          skipped_already={runEod.data.skipped_already} ·
          skipped_no_data={runEod.data.skipped_no_data}
        </div>
      )}

      {/* 误差统计表 */}
      <Card title="按基金分组的误差统计" subtitle={`窗口：${sinceDays} 天`}>
        {summary.isLoading && <Skeleton h={120} />}
        {summary.data && summary.data.length === 0 && (
          <p className="text-sm text-muted-foreground py-8 text-center">
            还没有 active fund。先去「基金配置」页添加基金。
          </p>
        )}
        {summary.data && summary.data.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-muted-foreground border-b border-border">
                <tr>
                  <th className="text-left py-2 pr-3">基金代码</th>
                  <th className="text-right py-2 pr-3">样本</th>
                  <th className="text-right py-2 pr-3">待填</th>
                  <th className="text-right py-2 pr-3">MAE</th>
                  <th className="text-right py-2 pr-3">RMSE</th>
                  <th className="text-right py-2 pr-3">系统性偏差</th>
                  <th className="text-right py-2 pr-3">最大单日</th>
                  <th className="text-left py-2 pl-3">最近对账</th>
                </tr>
              </thead>
              <tbody>
                {summary.data.map(row => (
                  <tr
                    key={row.fund_code}
                    onClick={() => setSelectedFund(row.fund_code)}
                    className={`border-b border-border/40 hover:bg-secondary/30 cursor-pointer transition-colors ${
                      selectedFund === row.fund_code ? 'bg-secondary/40' : ''
                    }`}
                  >
                    <td className="py-2 pr-3 font-mono font-medium">{row.fund_code}</td>
                    <td className="py-2 pr-3 text-right tabular-nums">{row.sample_count}</td>
                    <td className="py-2 pr-3 text-right tabular-nums">
                      {row.pending_count > 0 ? (
                        <span className="text-amber-500 font-medium">{row.pending_count}</span>
                      ) : (
                        <span className="text-muted-foreground">0</span>
                      )}
                    </td>
                    <td className={`py-2 pr-3 text-right tabular-nums ${maeColorClass(row.mae_pct)}`}>
                      {row.mae_pct !== null ? `${row.mae_pct.toFixed(2)}%` : '—'}
                    </td>
                    <td className="py-2 pr-3 text-right tabular-nums text-muted-foreground">
                      {row.rmse_pct !== null ? `${row.rmse_pct.toFixed(2)}%` : '—'}
                    </td>
                    <td className={`py-2 pr-3 text-right tabular-nums ${biasColorClass(row.bias_pct)}`}>
                      {row.bias_pct !== null ? (
                        <>
                          {row.bias_pct > 0 ? '+' : ''}{row.bias_pct.toFixed(2)}%
                          <span className="text-[10px] ml-1 text-muted-foreground">
                            {Math.abs(row.bias_pct) < 0.1 ? '' : row.bias_pct > 0 ? '高估' : '低估'}
                          </span>
                        </>
                      ) : '—'}
                    </td>
                    <td className="py-2 pr-3 text-right tabular-nums text-red-500/80">
                      {row.max_error_pct !== null ? `${row.max_error_pct.toFixed(2)}%` : '—'}
                    </td>
                    <td className="py-2 pl-3 text-muted-foreground">{formatTime(row.last_eod_at, true)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="text-[10px] text-muted-foreground mt-2">
              💡 点击某行 → 下方显示该基金的 est vs actual 时间曲线
            </p>
          </div>
        )}
      </Card>

      {/* 时间序列对比图 */}
      {selectedFund && (
        <Card
          title={`${selectedFund} · 估值 vs 实际`}
          subtitle="蓝=估值 / 绿=实际 / 红=误差%"
        >
          {timeseries.isLoading && <Skeleton h={280} />}
          {timeseries.data && timeseries.data.length === 0 && (
            <p className="text-sm text-muted-foreground py-12 text-center">
              该基金在选定窗口内没有已对账的样本。
              <br />
              填写下面「待填实际 NAV」表格即可生成数据。
            </p>
          )}
          {timeseries.data && timeseries.data.length > 0 && (
            <ResponsiveContainer width="100%" height={280}>
              <LineChart data={timeseries.data.map(p => ({
                ...p,
                label: formatTime(p.ts, true),
              }))}>
                <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                <XAxis dataKey="label" tick={{ fontSize: 11 }} />
                <YAxis yAxisId="left" tick={{ fontSize: 11 }} domain={['auto', 'auto']} />
                <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} unit="%" />
                <Tooltip />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Line
                  yAxisId="left"
                  type="monotone"
                  dataKey="est_nav"
                  name="估值"
                  stroke="#3b82f6"
                  strokeWidth={2}
                  dot={{ r: 3 }}
                />
                <Line
                  yAxisId="left"
                  type="monotone"
                  dataKey="actual_nav"
                  name="实际"
                  stroke="#10b981"
                  strokeWidth={2}
                  dot={{ r: 3 }}
                />
                <Line
                  yAxisId="right"
                  type="monotone"
                  dataKey="error_pct"
                  name="误差%"
                  stroke="#ef4444"
                  strokeWidth={1.5}
                  dot={false}
                  strokeDasharray="4 2"
                />
              </LineChart>
            </ResponsiveContainer>
          )}
        </Card>
      )}

      {/* 待填实际值列表 */}
      <Card
        title={`待填实际净值${pending.data ? `（共 ${pending.data.length} 条）` : ''}`}
        subtitle="scheduler 已标记为 EOD final，等你填入真实日终净值"
      >
        {pending.isLoading && <Skeleton h={200} />}
        {pending.data && pending.data.length === 0 && (
          <div className="py-8 text-center">
            <CheckCircle2 className="w-8 h-8 text-green-500/60 mx-auto mb-2" />
            <p className="text-sm text-muted-foreground">所有 EOD 快照都已对账，🎉</p>
          </div>
        )}
        {pending.data && pending.data.length > 0 && (
          <div className="space-y-1.5">
            {pending.data.map(p => (
              <div
                key={p.id}
                className="flex items-center gap-3 p-2.5 rounded-md border border-border/40 hover:border-border transition-colors"
              >
                <div className="flex-1 min-w-0 grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
                  <div>
                    <p className="text-muted-foreground">基金</p>
                    <p className="font-mono font-medium">{p.fund_code}</p>
                  </div>
                  <div>
                    <p className="text-muted-foreground">日期</p>
                    <p>{formatTime(p.ts, true)}</p>
                  </div>
                  <div>
                    <p className="text-muted-foreground">估值</p>
                    <p className="tabular-nums">
                      {p.est_nav?.toFixed(4) ?? '—'}
                      {p.est_pct_change_1d !== null && (
                        <span className="text-[10px] ml-1 text-muted-foreground">
                          ({(p.est_pct_change_1d * 100).toFixed(2)}%)
                        </span>
                      )}
                    </p>
                  </div>
                  <div>
                    <p className="text-muted-foreground">置信度</p>
                    <p className="capitalize">{p.confidence_level}</p>
                  </div>
                </div>
                {fillingId === p.id ? (
                  <div className="flex items-center gap-1.5 shrink-0">
                    <input
                      type="number"
                      step="0.0001"
                      autoFocus
                      value={fillValue}
                      onChange={e => setFillValue(e.target.value)}
                      placeholder="真实净值"
                      className="w-32 px-2 py-1 rounded-md bg-background border border-border text-xs"
                      onKeyDown={e => {
                        if (e.key === 'Enter') handleFill(p.id)
                        if (e.key === 'Escape') { setFillingId(null); setFillError(null) }
                      }}
                    />
                    <button
                      onClick={() => handleFill(p.id)}
                      disabled={fillActual.isPending}
                      className="px-2 py-1 rounded-md bg-green-500/15 hover:bg-green-500/25 text-green-500 text-xs disabled:opacity-50"
                    >
                      {fillActual.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : <CheckCircle2 className="w-3 h-3" />}
                    </button>
                    <button
                      onClick={() => { setFillingId(null); setFillError(null) }}
                      className="px-2 py-1 rounded-md hover:bg-secondary text-muted-foreground text-xs"
                    >
                      <XCircle className="w-3 h-3" />
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={() => { setFillingId(p.id); setFillValue('') }}
                    className="px-2.5 py-1 rounded-md bg-primary/15 hover:bg-primary/25 text-primary text-xs flex items-center gap-1 shrink-0"
                  >
                    <Plus className="w-3 h-3" />
                    填入实际净值
                  </button>
                )}
              </div>
            ))}
            {fillError && (
              <div className="rounded-md bg-red-500/10 border border-red-500/30 px-3 py-1.5 text-xs flex items-center gap-2 text-red-500">
                <AlertCircle className="w-3.5 h-3.5" />
                {fillError}
              </div>
            )}
          </div>
        )}
      </Card>
    </div>
  )
}

// ── 子组件 ─────────────────────────────────────────────

function Card({ title, subtitle, children }: {
  title: string
  subtitle?: string
  children: React.ReactNode
}) {
  return (
    <div className="rounded-xl bg-card border border-border p-4">
      <div className="flex items-baseline justify-between mb-3">
        <h2 className="text-sm font-semibold">{title}</h2>
        {subtitle && <span className="text-xs text-muted-foreground">{subtitle}</span>}
      </div>
      {children}
    </div>
  )
}

function Skeleton({ h }: { h: number }) {
  return <div className="bg-secondary/40 rounded-md animate-pulse" style={{ height: `${h}px` }} />
}
