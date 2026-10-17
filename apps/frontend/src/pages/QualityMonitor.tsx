/**
 * G5 · 数据源质量监控
 *
 * 基于持久化的 source_health_logs 表，可视化数据源历史质量：
 * - 顶部统计卡（总调用 / 成功率 / P95 延迟 / 失败次数）
 * - 时间序列图（成功率 + 延迟趋势）
 * - 按数据源分组的表格
 * - 错误类型饼图 + 最近错误清单
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid, ResponsiveContainer,
  PieChart, Pie, Cell, Legend,
} from 'recharts'
import { Activity, AlertTriangle, CheckCircle2, Clock, RefreshCw } from 'lucide-react'
import api from '@/services/api'

// ── 类型 ──────────────────────────────────────────────

interface QualitySummary {
  total_calls: number
  success_count: number
  failure_count: number
  success_rate_pct: number | null
  latency_p50_ms: number | null
  latency_p95_ms: number | null
  latency_p99_ms: number | null
  latency_avg_ms: number | null
  since_days: number
  since_iso: string
}

interface BySource {
  source_name: string
  total_calls: number
  success_count: number
  failure_count: number
  success_rate_pct: number
  latency_p50_ms: number | null
  latency_p95_ms: number | null
  latency_avg_ms: number | null
  last_checked_at: string | null
  last_success: boolean
  last_error_message: string | null
}

interface BySymbol {
  symbol: string
  total_calls: number
  success_count: number
  failure_count: number
  success_rate_pct: number
}

interface TimeseriesBucket {
  bucket_start_iso: string
  total_calls: number
  success_count: number
  failure_count: number
  success_rate_pct: number
  latency_avg_ms: number | null
  latency_p95_ms: number | null
}

interface ErrorRecord {
  id: string
  source_name: string
  symbol: string | null
  checked_at: string
  error_message: string | null
  http_status: number | null
}

interface ErrorCategory {
  category: string
  count: number
  share_pct: number
  example: string | null
}

// ── 配色 ──────────────────────────────────────────────

const PIE_COLORS = [
  '#ef4444', '#f97316', '#eab308', '#84cc16',
  '#06b6d4', '#3b82f6', '#a855f7', '#ec4899',
]

const CATEGORY_LABELS: Record<string, string> = {
  timeout: '超时',
  connection: '连接拒绝',
  dns: 'DNS 解析',
  auth: '认证失败',
  rate_limit: '限流',
  '5xx': '服务端 5xx',
  empty_response: '空响应',
  parse_error: '解析错误',
  other: '其他',
}

// ── Hook ──────────────────────────────────────────────

function useQualityData(sinceDays: number, bucketHours: number) {
  const summary = useQuery({
    queryKey: ['quality', 'summary', sinceDays],
    queryFn: () =>
      api.get<QualitySummary>('/api/v1/quality-monitor/summary', { params: { since_days: sinceDays } }).then(r => r.data),
    refetchInterval: 60000,
  })
  const bySource = useQuery({
    queryKey: ['quality', 'by-source', sinceDays],
    queryFn: () =>
      api.get<BySource[]>('/api/v1/quality-monitor/by-source', { params: { since_days: sinceDays } }).then(r => r.data),
    refetchInterval: 60000,
  })
  const bySymbol = useQuery({
    queryKey: ['quality', 'by-symbol', sinceDays],
    queryFn: () =>
      api.get<BySymbol[]>('/api/v1/quality-monitor/by-symbol', { params: { since_days: sinceDays, limit: 20 } }).then(r => r.data),
    refetchInterval: 60000,
  })
  const timeseries = useQuery({
    queryKey: ['quality', 'timeseries', sinceDays, bucketHours],
    queryFn: () =>
      api.get<TimeseriesBucket[]>('/api/v1/quality-monitor/timeseries', { params: { since_days: sinceDays, bucket_hours: bucketHours } }).then(r => r.data),
    refetchInterval: 60000,
  })
  const errors = useQuery({
    queryKey: ['quality', 'errors'],
    queryFn: () =>
      api.get<ErrorRecord[]>('/api/v1/quality-monitor/errors', { params: { limit: 30 } }).then(r => r.data),
    refetchInterval: 60000,
  })
  const errorCategories = useQuery({
    queryKey: ['quality', 'error-categories', sinceDays],
    queryFn: () =>
      api.get<ErrorCategory[]>('/api/v1/quality-monitor/error-categories', { params: { since_days: sinceDays } }).then(r => r.data),
    refetchInterval: 60000,
  })
  return { summary, bySource, bySymbol, timeseries, errors, errorCategories }
}

// ── Helpers ───────────────────────────────────────────

function formatTime(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function formatBucket(iso: string, sinceDays: number): string {
  const d = new Date(iso)
  if (sinceDays <= 2) {
    return d.toLocaleString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  }
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit' })
}

function rateColorClass(rate: number | null): string {
  if (rate === null) return 'text-muted-foreground'
  if (rate >= 95) return 'text-green-500'
  if (rate >= 80) return 'text-amber-500'
  return 'text-red-500'
}

// ── 主组件 ────────────────────────────────────────────

export default function QualityMonitor() {
  const [sinceDays, setSinceDays] = useState(7)
  const bucketHours = sinceDays <= 2 ? 1 : sinceDays <= 7 ? 4 : 24
  const { summary, bySource, bySymbol, timeseries, errors, errorCategories } = useQualityData(sinceDays, bucketHours)

  const refetchAll = () => {
    summary.refetch(); bySource.refetch(); bySymbol.refetch()
    timeseries.refetch(); errors.refetch(); errorCategories.refetch()
  }

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold flex items-center gap-2">
            <Activity className="w-5 h-5 text-primary" />
            质量监控
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            source_health_logs 历史聚合 · 与 DataHub 实时面板互补
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={sinceDays}
            onChange={e => setSinceDays(Number(e.target.value))}
            className="px-3 py-1.5 rounded-md bg-background border border-border text-sm"
          >
            <option value={1}>过去 1 天</option>
            <option value={2}>过去 2 天</option>
            <option value={7}>过去 7 天</option>
            <option value={30}>过去 30 天</option>
            <option value={90}>过去 90 天</option>
          </select>
          <button
            onClick={refetchAll}
            className="p-1.5 rounded-md hover:bg-secondary text-muted-foreground hover:text-foreground transition-colors"
            title="立即刷新"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* 顶部统计卡 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard
          icon={<Activity className="w-4 h-4 text-blue-500" />}
          label="总调用次数"
          value={summary.data ? summary.data.total_calls.toLocaleString() : '—'}
          loading={summary.isLoading}
        />
        <StatCard
          icon={<CheckCircle2 className={`w-4 h-4 ${rateColorClass(summary.data?.success_rate_pct ?? null)}`} />}
          label="成功率"
          value={summary.data?.success_rate_pct !== null && summary.data?.success_rate_pct !== undefined ? `${summary.data.success_rate_pct}%` : '—'}
          colorClass={rateColorClass(summary.data?.success_rate_pct ?? null)}
          loading={summary.isLoading}
        />
        <StatCard
          icon={<Clock className="w-4 h-4 text-purple-500" />}
          label="P95 延迟"
          value={summary.data?.latency_p95_ms ? `${Math.round(summary.data.latency_p95_ms)} ms` : '—'}
          loading={summary.isLoading}
        />
        <StatCard
          icon={<AlertTriangle className="w-4 h-4 text-red-500" />}
          label="失败次数"
          value={summary.data ? summary.data.failure_count.toLocaleString() : '—'}
          colorClass={summary.data && summary.data.failure_count > 0 ? 'text-red-500' : ''}
          loading={summary.isLoading}
        />
      </div>

      {/* 时间序列图 */}
      <Card title="成功率与延迟趋势" subtitle={`时间桶 = ${bucketHours} 小时`}>
        {timeseries.isLoading && <Skeleton h={260} />}
        {timeseries.data && timeseries.data.length === 0 && <EmptyState />}
        {timeseries.data && timeseries.data.length > 0 && (
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={timeseries.data.map(b => ({
              ...b,
              label: formatBucket(b.bucket_start_iso, sinceDays),
            }))}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
              <XAxis dataKey="label" tick={{ fontSize: 11 }} />
              <YAxis yAxisId="left" tick={{ fontSize: 11 }} domain={[0, 100]} unit="%" />
              <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} unit="ms" />
              <Tooltip />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <Line yAxisId="left" type="monotone" dataKey="success_rate_pct" name="成功率" stroke="#10b981" strokeWidth={2} dot={false} />
              <Line yAxisId="right" type="monotone" dataKey="latency_avg_ms" name="平均延迟" stroke="#a855f7" strokeWidth={1.5} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        )}
      </Card>

      {/* 二行：按 source 表格 + 错误类型饼图 */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <Card title="按数据源分组" className="lg:col-span-2">
          {bySource.isLoading && <Skeleton h={260} />}
          {bySource.data && bySource.data.length === 0 && <EmptyState />}
          {bySource.data && bySource.data.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="text-muted-foreground border-b border-border">
                  <tr>
                    <th className="text-left py-2 pr-3">数据源</th>
                    <th className="text-right py-2 pr-3">调用</th>
                    <th className="text-right py-2 pr-3">成功率</th>
                    <th className="text-right py-2 pr-3">P50 / P95</th>
                    <th className="text-left py-2 pl-3">最后状态</th>
                  </tr>
                </thead>
                <tbody>
                  {bySource.data.map(row => (
                    <tr key={row.source_name} className="border-b border-border/40 hover:bg-secondary/30">
                      <td className="py-2 pr-3 font-mono">{row.source_name}</td>
                      <td className="py-2 pr-3 text-right tabular-nums">{row.total_calls}</td>
                      <td className={`py-2 pr-3 text-right tabular-nums font-medium ${rateColorClass(row.success_rate_pct)}`}>
                        {row.success_rate_pct}%
                      </td>
                      <td className="py-2 pr-3 text-right tabular-nums text-muted-foreground">
                        {row.latency_p50_ms ? Math.round(row.latency_p50_ms) : '—'} / {row.latency_p95_ms ? Math.round(row.latency_p95_ms) : '—'} ms
                      </td>
                      <td className="py-2 pl-3 text-muted-foreground max-w-[280px] truncate">
                        {row.last_success ? (
                          <span className="text-green-500">✓ {formatTime(row.last_checked_at)}</span>
                        ) : (
                          <span className="text-red-500" title={row.last_error_message ?? ''}>
                            ✗ {row.last_error_message?.slice(0, 60) ?? 'unknown error'}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card title="错误类型分布">
          {errorCategories.isLoading && <Skeleton h={260} />}
          {errorCategories.data && errorCategories.data.length === 0 && (
            <p className="text-xs text-muted-foreground py-12 text-center">该窗口内无失败记录</p>
          )}
          {errorCategories.data && errorCategories.data.length > 0 && (
            <ResponsiveContainer width="100%" height={260}>
              <PieChart>
                <Pie
                  data={errorCategories.data.map(c => ({ ...c, name: CATEGORY_LABELS[c.category] || c.category }))}
                  dataKey="count"
                  nameKey="name"
                  cx="50%"
                  cy="50%"
                  outerRadius={80}
                  label={({ name, share_pct }) => `${name} ${share_pct}%`}
                  labelLine={false}
                  fontSize={10}
                >
                  {errorCategories.data.map((_, i) => (
                    <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip />
              </PieChart>
            </ResponsiveContainer>
          )}
        </Card>
      </div>

      {/* 三行：异常 symbol + 最近错误清单 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card title="问题 symbol（按失败率）">
          {bySymbol.isLoading && <Skeleton h={200} />}
          {bySymbol.data && bySymbol.data.length === 0 && <EmptyState />}
          {bySymbol.data && bySymbol.data.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="text-muted-foreground border-b border-border">
                  <tr>
                    <th className="text-left py-2 pr-3">Symbol</th>
                    <th className="text-right py-2 pr-3">调用</th>
                    <th className="text-right py-2 pr-3">失败</th>
                    <th className="text-right py-2">成功率</th>
                  </tr>
                </thead>
                <tbody>
                  {bySymbol.data.slice(0, 12).map(row => (
                    <tr key={row.symbol} className="border-b border-border/40 hover:bg-secondary/30">
                      <td className="py-1.5 pr-3 font-mono">{row.symbol}</td>
                      <td className="py-1.5 pr-3 text-right tabular-nums">{row.total_calls}</td>
                      <td className="py-1.5 pr-3 text-right tabular-nums text-red-500">{row.failure_count}</td>
                      <td className={`py-1.5 text-right tabular-nums font-medium ${rateColorClass(row.success_rate_pct)}`}>
                        {row.success_rate_pct}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card title="最近错误（最多 30 条）">
          {errors.isLoading && <Skeleton h={200} />}
          {errors.data && errors.data.length === 0 && (
            <p className="text-xs text-muted-foreground py-12 text-center">暂无错误记录</p>
          )}
          {errors.data && errors.data.length > 0 && (
            <div className="space-y-1.5 max-h-[280px] overflow-y-auto">
              {errors.data.map(err => (
                <div key={err.id} className="text-xs border-l-2 border-red-500/60 pl-3 py-1">
                  <div className="flex items-center justify-between text-muted-foreground">
                    <span className="font-mono">{err.source_name}{err.symbol ? ` · ${err.symbol}` : ''}</span>
                    <span>{formatTime(err.checked_at)}</span>
                  </div>
                  <p className="text-foreground mt-0.5 line-clamp-2">{err.error_message ?? 'unknown error'}</p>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
    </div>
  )
}

// ── 子组件 ─────────────────────────────────────────────

function StatCard({ icon, label, value, colorClass = '', loading = false }: {
  icon: React.ReactNode
  label: string
  value: string
  colorClass?: string
  loading?: boolean
}) {
  return (
    <div className="rounded-xl bg-card border border-border p-4">
      <div className="flex items-center gap-2 text-muted-foreground text-xs mb-2">
        {icon}
        <span>{label}</span>
      </div>
      {loading ? (
        <div className="h-7 w-20 bg-secondary/50 rounded animate-pulse" />
      ) : (
        <p className={`text-2xl font-semibold tabular-nums ${colorClass}`}>{value}</p>
      )}
    </div>
  )
}

function Card({ title, subtitle, children, className = '' }: {
  title: string
  subtitle?: string
  children: React.ReactNode
  className?: string
}) {
  return (
    <div className={`rounded-xl bg-card border border-border p-4 ${className}`}>
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

function EmptyState() {
  return <p className="text-xs text-muted-foreground py-12 text-center">该窗口内暂无数据</p>
}
