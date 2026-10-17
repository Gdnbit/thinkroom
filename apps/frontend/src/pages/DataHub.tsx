import { useState, useEffect, useCallback } from 'react'
import { Database, RefreshCw, Search, TrendingUp, TrendingDown, Minus, Plus, X, BarChart3 } from 'lucide-react'
import { clsx } from 'clsx'
import api from '@/services/api'
import FundNavChart from '@/components/FundNavChart'

// ── Types ───────────────────────────────────────────────
interface DataSource {
  id: string; name: string; category: string; provider: string;
  api_type: string; requires_key: boolean; key_configured: boolean;
  rate_limit: string; coverage: string[]; known_limitations: string[];
  documentation_url: string; fallback: string | null;
}
interface FundEstimate {
  code: string; name: string; nav_yesterday: number;
  nav_estimated: number; change_pct: number; estimate_time: string;
}
interface FundSearchResult {
  code: string; name: string; type: string;
}

const CARD = { background: 'hsl(var(--card))', border: '1px solid hsl(var(--border))' } as const
const CAT_COLORS: Record<string, string> = {
  market: 'text-emerald-400 bg-emerald-400/10',
  macro: 'text-violet-400 bg-violet-400/10',
  event: 'text-amber-400 bg-amber-400/10',
  flow: 'text-cyan-400 bg-cyan-400/10',
  prediction: 'text-pink-400 bg-pink-400/10',
  internal: 'text-slate-400 bg-slate-400/10',
}

// ── Data Sources Tab ────────────────────────────────────
function DataSourcesTab() {
  const [sources, setSources] = useState<DataSource[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.get('/api/v1/datasources/list').then(r => {
      setSources(r.data?.sources ?? r.data ?? [])
    }).catch(() => {}).finally(() => setLoading(false))
  }, [])

  const byCategory: Record<string, DataSource[]> = {}
  sources.forEach(s => {
    (byCategory[s.category] = byCategory[s.category] || []).push(s)
  })
  const catOrder = ['market', 'macro', 'event', 'flow', 'prediction', 'internal']
  const catLabels: Record<string, string> = {
    market: '行情数据', macro: '宏观指标', event: '新闻/事件',
    flow: '资金流/链上', prediction: '预测市场', internal: '内部数据',
  }

  if (loading) return <div className="text-center py-12 text-muted-foreground text-[12px]">加载中...</div>

  return (
    <div className="space-y-5">
      {catOrder.filter(c => byCategory[c]).map(cat => (
        <div key={cat}>
          <div className="flex items-center gap-2 mb-2.5">
            <span className={clsx('text-[10px] font-semibold px-2 py-0.5 rounded-full', CAT_COLORS[cat] || CAT_COLORS.internal)}>
              {catLabels[cat] || cat}
            </span>
            <span className="text-[10px] text-muted-foreground/40">{byCategory[cat].length} 个数据源</span>
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-2">
            {byCategory[cat].map(src => (
              <div key={src.id} className="rounded-xl p-4" style={CARD}>
                <div className="flex items-start justify-between mb-2">
                  <div>
                    <p className="text-sm font-semibold text-foreground">{src.name}</p>
                    <p className="text-xs text-muted-foreground/60 mt-0.5">{src.provider} · {src.api_type}</p>
                  </div>
                  <div className="flex items-center gap-1.5">
                    {src.requires_key && (
                      <span className={clsx('text-[9px] px-1.5 py-0.5 rounded-full font-medium',
                        src.key_configured ? 'text-emerald-400 bg-emerald-400/10' : 'text-red-400 bg-red-400/10')}>
                        {src.key_configured ? 'Key ✓' : 'Key ✗'}
                      </span>
                    )}
                    {!src.requires_key && (
                      <span className="text-[9px] px-1.5 py-0.5 rounded-full font-medium text-emerald-400 bg-emerald-400/10">免费</span>
                    )}
                  </div>
                </div>
                {src.coverage.length > 0 && (
                  <div className="flex flex-wrap gap-1 mb-2">
                    {src.coverage.slice(0, 6).map((c, i) => (
                      <span key={i} className="text-[10px] px-1.5 py-0.5 rounded bg-secondary text-muted-foreground">{c}</span>
                    ))}
                    {src.coverage.length > 6 && <span className="text-[9px] text-muted-foreground/30">+{src.coverage.length - 6}</span>}
                  </div>
                )}
                <p className="text-[10px] text-muted-foreground/40">{src.rate_limit}</p>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

// ── Fund Valuation Tab ──────────────────────────────────
const DEFAULT_CODES = '110011,161725,003834,005827,320007,000961,001549'

function FundValuationTab() {
  const [codes, setCodes] = useState(DEFAULT_CODES)
  const [funds, setFunds] = useState<FundEstimate[]>([])
  const [loading, setLoading] = useState(false)
  const [lastFetch, setLastFetch] = useState('')
  const [searchKey, setSearchKey] = useState('')
  const [searchResults, setSearchResults] = useState<FundSearchResult[]>([])
  const [searching, setSearching] = useState(false)
  const [selectedFund, setSelectedFund] = useState<{ code: string; name: string } | null>(null)

  const fetchEstimates = useCallback(async () => {
    if (!codes.trim()) return
    setLoading(true)
    try {
      const r = await api.get('/api/v1/fund/estimate', { params: { codes } })
      setFunds(r.data?.funds ?? [])
      setLastFetch(new Date().toLocaleTimeString('zh-CN'))
    } catch { /* ignore */ }
    setLoading(false)
  }, [codes])

  useEffect(() => { fetchEstimates() }, [fetchEstimates])

  // Auto-refresh every 2 minutes during trading hours
  useEffect(() => {
    const timer = setInterval(() => {
      const now = new Date()
      const bjHour = (now.getUTCHours() + 8) % 24
      const bjMin = now.getUTCMinutes()
      const minutes = bjHour * 60 + bjMin
      // Trading hours: 9:30-11:30, 13:00-15:00 Beijing time
      const isTrading = (minutes >= 570 && minutes <= 690) || (minutes >= 780 && minutes <= 900)
      if (isTrading) fetchEstimates()
    }, 120000) // 2 min
    return () => clearInterval(timer)
  }, [fetchEstimates])

  const doSearch = async () => {
    if (!searchKey.trim()) return
    setSearching(true)
    try {
      const r = await api.get('/api/v1/fund/search', { params: { keyword: searchKey } })
      setSearchResults(r.data?.results ?? [])
    } catch { /* ignore */ }
    setSearching(false)
  }

  const addFund = (code: string) => {
    const existing = codes.split(',').map(c => c.trim())
    if (!existing.includes(code)) {
      setCodes(prev => prev ? `${prev},${code}` : code)
    }
    setSearchResults([])
    setSearchKey('')
  }

  const removeFund = (code: string) => {
    setCodes(prev => prev.split(',').map(c => c.trim()).filter(c => c !== code).join(','))
    setFunds(prev => prev.filter(f => f.code !== code))
  }

  return (
    <div className="space-y-4">
      {/* Search + Controls */}
      <div className="flex gap-2">
        <div className="relative flex-1">
          <input
            value={searchKey} onChange={e => setSearchKey(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && doSearch()}
            placeholder="搜索基金名称或代码..."
            className="w-full h-9 rounded-lg pl-8 pr-3 text-[12px] text-foreground outline-none"
            style={{ background: 'hsl(var(--secondary))', border: '1px solid hsl(var(--border))' }}
          />
          <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground/40" />
          {searchResults.length > 0 && (
            <div className="absolute top-full left-0 right-0 mt-1 z-50 rounded-xl py-1 max-h-48 overflow-y-auto"
              style={{ background: 'hsl(var(--card))', border: '1px solid hsl(var(--border))', boxShadow: '0 8px 24px rgba(0,0,0,0.3)' }}>
              {searchResults.map(r => (
                <button key={r.code} onClick={() => addFund(r.code)}
                  className="w-full text-left px-3 py-1.5 text-[12px] hover:bg-secondary/50 flex items-center gap-2">
                  <span className="font-mono text-muted-foreground w-16">{r.code}</span>
                  <span className="text-foreground truncate">{r.name}</span>
                  <Plus size={12} className="ml-auto text-muted-foreground/40" />
                </button>
              ))}
            </div>
          )}
        </div>
        <button onClick={doSearch} disabled={searching}
          className="btn-outline !px-3 !py-1.5">
          {searching ? '搜索中...' : '搜索'}
        </button>
        <button onClick={fetchEstimates} disabled={loading}
          className="btn-outline !px-3 !py-1.5 flex items-center gap-1.5">
          <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
          刷新
        </button>
      </div>

      {lastFetch && <p className="text-[10px] text-muted-foreground/35">上次更新: {lastFetch} · 交易时段每 2 分钟自动刷新</p>}

      {/* Fund Table */}
      <div className="rounded-xl overflow-hidden" style={CARD}>
        <table className="w-full">
          <thead>
            <tr className="text-xs text-muted-foreground font-medium" style={{ borderBottom: '1px solid hsl(var(--border))' }}>
              <th className="text-left px-4 py-2.5">代码</th>
              <th className="text-left px-4 py-2.5">基金名称</th>
              <th className="text-right px-4 py-2.5">昨日净值</th>
              <th className="text-right px-4 py-2.5">实时估值</th>
              <th className="text-right px-4 py-2.5">估算涨跌</th>
              <th className="text-right px-4 py-2.5">估值时间</th>
              <th className="w-8"></th>
            </tr>
          </thead>
          <tbody>
            {funds.map(f => {
              const up = f.change_pct > 0
              const down = f.change_pct < 0
              return (
                <tr key={f.code} onClick={() => setSelectedFund(s => s?.code === f.code ? null : { code: f.code, name: f.name })}
                  className={clsx('hover:bg-secondary/30 transition-colors cursor-pointer', selectedFund?.code === f.code && 'bg-primary/5')}
                  style={{ borderBottom: '1px solid hsl(var(--border) / 0.5)' }}>
                  <td className="px-4 py-2.5 text-[12px] font-mono text-muted-foreground">{f.code}</td>
                  <td className="px-4 py-2.5 text-[12px] text-foreground font-medium">{f.name}</td>
                  <td className="px-4 py-2.5 text-[12px] font-mono text-muted-foreground text-right">{f.nav_yesterday.toFixed(4)}</td>
                  <td className="px-4 py-2.5 text-[13px] font-mono font-semibold text-right text-foreground">{f.nav_estimated.toFixed(4)}</td>
                  <td className={clsx('px-4 py-2.5 text-[12px] font-mono font-semibold text-right',
                    up ? 'text-red-400' : down ? 'text-emerald-400' : 'text-slate-400')}>
                    <span className="inline-flex items-center gap-0.5">
                      {up ? <TrendingUp size={11} /> : down ? <TrendingDown size={11} /> : <Minus size={11} />}
                      {up ? '+' : ''}{f.change_pct.toFixed(2)}%
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-[10px] font-mono text-muted-foreground/40 text-right">{f.estimate_time}</td>
                  <td className="px-2 py-2.5 flex items-center gap-1">
                    <button onClick={e => { e.stopPropagation(); setSelectedFund(s => s?.code === f.code ? null : { code: f.code, name: f.name }) }}
                      className={clsx('text-muted-foreground/30 hover:text-primary transition-colors', selectedFund?.code === f.code && 'text-primary')}>
                      <BarChart3 size={12} />
                    </button>
                    <button onClick={e => { e.stopPropagation(); removeFund(f.code) }} className="text-muted-foreground/30 hover:text-red-400 transition-colors">
                      <X size={12} />
                    </button>
                  </td>
                </tr>
              )
            })}
            {funds.length === 0 && !loading && (
              <tr><td colSpan={7} className="text-center py-8 text-[12px] text-muted-foreground/40">暂无基金数据，请搜索添加基金</td></tr>
            )}
            {loading && funds.length === 0 && (
              <tr><td colSpan={7} className="text-center py-8 text-[12px] text-muted-foreground/40">获取估值数据中...</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Fund NAV Chart */}
      {selectedFund && (
        <FundNavChart code={selectedFund.code} name={selectedFund.name} />
      )}

      {/* Note */}
      <div className="rounded-xl p-3" style={{ background: 'hsl(var(--muted))', border: '1px solid hsl(var(--border))' }}>
        <p className="text-[11px] text-muted-foreground/50 leading-relaxed">
          <span className="font-semibold text-muted-foreground/70">说明：</span>
          估值数据来源于天天基金网，交易时段（9:30-11:30, 13:00-15:00）每 1-3 分钟更新。
          基金涨跌采用 A 股惯例：<span className="text-red-400">红涨</span> <span className="text-emerald-400">绿跌</span>。
          估值仅供参考，实际净值以基金公司公布为准。
        </p>
      </div>
    </div>
  )
}

// ── Main Page ───────────────────────────────────────────
export default function DataHub() {
  const [tab, setTab] = useState<'sources' | 'fund'>('sources')

  return (
    <div className="p-6 2xl:p-8 space-y-5 max-w-[1400px] 2xl:max-w-[1760px] mx-auto">
      <div className="flex items-start justify-between page-header !mb-5 !pb-4">
        <div>
          <span className="section-label">Data Hub</span>
          <h1 className="text-2xl font-semibold text-foreground flex items-center gap-2">
            <Database size={20} className="text-primary" />
            数据中心
          </h1>
          <p className="text-sm text-muted-foreground mt-1">数据源总览 · 基金实时估值</p>
        </div>
        <div className="flex gap-1 mt-6">
          <button onClick={() => setTab('sources')}
            className={tab === 'sources' ? 'filter-btn-active' : 'filter-btn'}>
            数据源
          </button>
          <button onClick={() => setTab('fund')}
            className={tab === 'fund' ? 'filter-btn-active' : 'filter-btn'}>
            基金估值
          </button>
        </div>
      </div>

      {tab === 'sources' && <DataSourcesTab />}
      {tab === 'fund' && <FundValuationTab />}
    </div>
  )
}
