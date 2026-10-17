import { useMemo, useState, useCallback, useRef, memo } from 'react'
import {
  Play, CheckCircle2, XCircle, ChevronDown, ChevronRight,
  Loader2, Radio, Zap, Server, TrendingUp, Users,
  Database, DollarSign, Globe, Timer, Clipboard,
} from 'lucide-react'
import { clsx } from 'clsx'
import axios from 'axios'
import api from '@/services/api'
import { saveHistory, type TestHistoryRecord } from '@/services/testHistory'
import TestHistorySection from './TestHistorySection'

// ── Types ───────────────────────────────────────────────────

type EpStatus = 'idle' | 'loading' | 'success' | 'error'

interface EpState {
  status: EpStatus
  data: any
  error: string | null
  ms: number
  at: string | null
}

const EMPTY: EpState = { status: 'idle', data: null, error: null, ms: 0, at: null }

/** Flatten an object to a shallow preview — never recurse */
function safeShallow(d: any): Record<string, any> {
  if (typeof d !== 'object' || d === null) return d
  const keys = Object.keys(d)
  const small: Record<string, any> = { _truncated: true, _keys: keys.length }
  for (const k of keys.slice(0, 6)) {
    const v = d[k]
    if (v === null || v === undefined) small[k] = v
    else if (typeof v === 'string') small[k] = v.length > 120 ? v.slice(0, 120) + '…' : v
    else if (typeof v !== 'object') small[k] = v
    else if (Array.isArray(v)) small[k] = `[${v.length} items]`
    else small[k] = '{…}'
  }
  return small
}

/** Estimate rough byte-size of an object WITHOUT full JSON.stringify */
function roughSize(d: any, depth = 0): number {
  if (d === null || d === undefined) return 4
  if (typeof d === 'string') return d.length
  if (typeof d !== 'object') return 8
  if (depth > 2) return 500 // stop recursing deep objects
  if (Array.isArray(d)) {
    if (d.length > 20) return d.length * 200 // rough estimate
    let s = 0
    for (let i = 0; i < Math.min(d.length, 10); i++) s += roughSize(d[i], depth + 1)
    return s * (d.length / Math.min(d.length, 10))
  }
  const keys = Object.keys(d)
  if (keys.length > 30) return keys.length * 200
  let s = 0
  for (const k of keys.slice(0, 15)) s += k.length + roughSize(d[k], depth + 1)
  return s * (keys.length / Math.min(keys.length, 15))
}

/** Truncate large response data to avoid memory bloat / white screen.
 *  NEVER calls JSON.stringify on raw data for size-checking. */
function safeData(d: any): any {
  try {
    if (d === null || d === undefined) return d
    if (typeof d !== 'object') {
      if (typeof d === 'string' && d.length > 2000) return d.slice(0, 2000) + '…'
      return d
    }
    // Arrays: always limit
    if (Array.isArray(d)) {
      if (d.length > 5) return { _truncated: true, _type: 'array', length: d.length, preview: d.slice(0, 3).map(i => typeof i === 'object' && i !== null ? safeShallow(i) : i) }
      return d.map(item => typeof item === 'object' && item !== null ? safeShallow(item) : item)
    }
    // Objects: estimate size without stringify
    const est = roughSize(d)
    if (est > 3000) return safeShallow(d)
    // Moderate size: keep top-level but flatten nested
    const keys = Object.keys(d)
    const result: Record<string, any> = {}
    for (const k of keys) {
      const v = d[k]
      if (v === null || v === undefined) { result[k] = v; continue }
      if (typeof v === 'string') { result[k] = v.length > 200 ? v.slice(0, 200) + '…' : v; continue }
      if (typeof v !== 'object') { result[k] = v; continue }
      if (Array.isArray(v)) { result[k] = v.length <= 3 ? v : `[${v.length} items]`; continue }
      result[k] = '{…}'
    }
    return result
  } catch { return { _error: 'data too large to display' } }
}

function safeStringify(d: any): string {
  try { return JSON.stringify(d, null, 2) } catch { return '[无法序列化]' }
}

export interface EpDef {
  id: string
  name: string
  method: 'GET' | 'POST'
  path: string
  desc: string
  body?: any
  cat: string
}

// ── Endpoint Registry ───────────────────────────────────────

export const ENDPOINTS: EpDef[] = [
  // ── 系统基础 ──
  { id: 'health', name: '健康检查', method: 'GET', path: '/api/v1/health', desc: '后端服务运行状态', cat: '系统基础' },
  { id: 'progress', name: '活跃任务', method: 'GET', path: '/api/v1/progress/active', desc: '正在运行的 Job 列表', cat: '系统基础' },
  { id: 'alerts', name: '系统提醒', method: 'GET', path: '/api/v1/alerts', desc: '过期/低信心/数据缺失等提醒', cat: '系统基础' },
  { id: 'settings', name: '系统配置', method: 'GET', path: '/api/v1/settings', desc: 'API Key 状态、模型配置', cat: '系统基础' },
  { id: 'conn_test', name: '连接测试', method: 'POST', path: '/api/v1/settings/test-connection', desc: '测试 OpenRouter API Key 连通性', cat: '系统基础' },
  { id: 'models', name: '模型列表', method: 'GET', path: '/api/v1/models', desc: '已注册模型及可用状态', cat: '系统基础' },
  { id: 'keys', name: 'API Keys', method: 'GET', path: '/api/v1/keys', desc: 'API 密钥列表（脱敏）', cat: '系统基础' },

  // ── 行情数据 ──
  { id: 'mkt_btc', name: 'BTC 行情', method: 'GET', path: '/api/v1/signals/market/BTC', desc: 'Binance/CoinGecko 实时价格', cat: '行情数据' },
  { id: 'mkt_spx', name: 'SPX 行情', method: 'GET', path: '/api/v1/signals/market/SPX', desc: 'Yahoo Finance 指数状态', cat: '行情数据' },
  { id: 'macro', name: '宏观数据', method: 'GET', path: '/api/v1/signals/macro', desc: 'FRED 利率/CPI/美债收益率', cat: '行情数据' },
  { id: 'ind_btc', name: 'BTC 技术指标', method: 'GET', path: '/api/v1/signals/indicator/BTC', desc: 'RSI/MACD/BB/ATR 综合解读', cat: '行情数据' },
  { id: 'flow_btc', name: 'BTC 资金流', method: 'GET', path: '/api/v1/signals/flow/BTC/summary', desc: '资金费率/多空比/连续性', cat: '行情数据' },
  { id: 'snap_btc', name: 'BTC 快照', method: 'GET', path: '/api/v1/market-history/snapshots/point?symbol=BTC', desc: '历史行情快照点查询', cat: '行情数据' },

  // ── 委员会（V2 动态6席）──
  { id: 'cm_list', name: '辩论列表', method: 'GET', path: '/api/v1/committee?limit=5', desc: '最近辩论记录（V2 含6席模型）', cat: '委员会(V2·6席)' },
  { id: 'cm_run', name: '启动辩论', method: 'POST', path: '/api/v1/committee/run', desc: '6席动态激活：基准/概率/风控/结构/审计/PnL', body: { question: 'BTC短期走势如何？', asset: 'BTC', num_rounds: 1 }, cat: '委员会(V2·6席)' },

  // ── 决策回放 ──
  { id: 'rp_list', name: '快照列表', method: 'GET', path: '/api/v1/replay?limit=5', desc: '决策快照（含证据冻结）', cat: '决策回放' },
  { id: 'rp_stats', name: '回放统计', method: 'GET', path: '/api/v1/replay/stats/summary', desc: '准确率/平均盈亏/回放数', cat: '决策回放' },

  // ── 用户修正 ──
  { id: 'ov_list', name: '修正列表', method: 'GET', path: '/api/v1/overrides?limit=5', desc: '用户手动修正审计轨迹', cat: '用户修正' },

  // ── Daily Radar ──
  { id: 'radar', name: '雷达信号', method: 'GET', path: '/api/v1/radar', desc: '今日机会雷达卡片（缓存）', cat: 'Daily Radar' },
  { id: 'radar_scan', name: '手动扫描', method: 'POST', path: '/api/v1/radar/scan', desc: '触发 OpportunityScout 实时扫描', cat: 'Daily Radar' },

  // ── 知识库数据 ──
  { id: 'claims', name: '观点列表', method: 'GET', path: '/api/v1/claims?limit=5', desc: '最新观点（含质量评分 A/B/C/D）', cat: '知识库数据' },
  { id: 'events', name: '事件列表', method: 'GET', path: '/api/v1/events?limit=5', desc: '最新市场事件', cat: '知识库数据' },
  { id: 'briefs', name: '研究简报', method: 'GET', path: '/api/v1/briefings?limit=3', desc: '最新研究简报', cat: '知识库数据' },
  { id: 'strats', name: '策略列表', method: 'GET', path: '/api/v1/strategies?limit=5', desc: '策略模板库', cat: '知识库数据' },
  { id: 'speakers', name: '分析师', method: 'GET', path: '/api/v1/speakers?limit=5', desc: '已识别的信息源', cat: '知识库数据' },
  { id: 'watchlists', name: '观察列表', method: 'GET', path: '/api/v1/watchlists', desc: '用户自定义观察清单', cat: '知识库数据' },

  // ── 知识库管理 ──
  { id: 'kb_stats', name: '知识库统计', method: 'GET', path: '/api/v1/knowledge/stats', desc: '来源/观点/简报/策略/关系总量', cat: '知识库管理' },
  { id: 'kb_src', name: '来源列表', method: 'GET', path: '/api/v1/knowledge/sources?limit=10', desc: '原始文档及处理状态', cat: '知识库管理' },
  { id: 'kb_time', name: '知识时间线', method: 'GET', path: '/api/v1/knowledge/timeline?days=30', desc: '近30天知识入库时间分布', cat: '知识库管理' },
  { id: 'kb_taxo', name: '知识分类', method: 'GET', path: '/api/v1/knowledge/taxonomy', desc: '按资产/主题/类型的分类统计', cat: '知识库管理' },
  { id: 'kb_graph', name: '知识图谱', method: 'GET', path: '/api/v1/knowledge/graph?limit=100', desc: '实体关系图（节点+边）', cat: '知识库管理' },

  // ── 生命周期 & 结果 ──
  { id: 'lc_scan', name: '生命周期扫描', method: 'POST', path: '/api/v1/lifecycle/scan?dry_run=true', desc: '检查过期/需更新的观点（dry_run）', cat: '生命周期' },
  { id: 'oc_stats', name: '结果统计', method: 'GET', path: '/api/v1/outcomes/stats', desc: '命中率/正确率/平均盈亏', cat: '生命周期' },
  { id: 'oc_list', name: '结果列表', method: 'GET', path: '/api/v1/outcomes?limit=5', desc: '观点/简报的事后验证记录', cat: '生命周期' },

  // ── 对话系统 ──
  { id: 'chat_list', name: '会话列表', method: 'GET', path: '/api/v1/chat/sessions', desc: '所有对话会话', cat: '对话系统' },

  // ── 成本追踪 ──
  { id: 'c_over', name: '成本概览', method: 'GET', path: '/api/v1/costs/overview', desc: '今日/本月/总花费与预算', cat: '成本追踪' },
  { id: 'c_day', name: '今日明细', method: 'GET', path: '/api/v1/costs/daily', desc: '今日各模型调用成本', cat: '成本追踪' },
  { id: 'c_month', name: '本月明细', method: 'GET', path: '/api/v1/costs/monthly', desc: '本月各模型调用成本', cat: '成本追踪' },
  { id: 'c_alert', name: '预算告警', method: 'GET', path: '/api/v1/costs/alerts', desc: '预算超限告警', cat: '成本追踪' },
  { id: 'c_hist', name: '历史成本', method: 'GET', path: '/api/v1/costs/history?granularity=monthly&limit=6', desc: '按日/月/年的历史成本明细', cat: '成本追踪' },
  { id: 'c_all', name: '总花费', method: 'GET', path: '/api/v1/costs/all-time', desc: '全历史总花费（不受90天窗口限制）', cat: '成本追踪' },

  // ── 配置 & 路由 ──
  { id: 's_route', name: '模型路由表', method: 'GET', path: '/api/v1/settings/model-routing', desc: '动态模型角色→模型映射（自动发现）', cat: '配置 & 路由' },
  { id: 's_feat', name: '功能模块状态', method: 'GET', path: '/api/v1/settings/features', desc: '各功能模块启用/保留/模拟状态', cat: '配置 & 路由' },

  // ── 数据源 ──
  { id: 'ds_dash', name: '数据源面板', method: 'GET', path: '/api/v1/datasources/dashboard', desc: '数据源状态汇总', cat: '数据源' },
  { id: 'ds_list', name: '数据源列表', method: 'GET', path: '/api/v1/datasources/list', desc: '含 Polymarket/DeFiLlama/Glassnode/社媒', cat: '数据源' },
  { id: 'ds_rem', name: '限制提醒', method: 'GET', path: '/api/v1/datasources/reminders', desc: 'API Key 缺失/配额不足', cat: '数据源' },
  { id: 'ds_quota', name: '配额状态', method: 'GET', path: '/api/v1/datasources/quota', desc: '各数据源 API 配额', cat: '数据源' },

  // ── 社交媒体 ──
  { id: 'sm_feed', name: '社媒 Feed', method: 'GET', path: '/api/v1/social-media/feed?limit=5', desc: '最新社媒信息流', cat: '社交媒体' },
  { id: 'sm_health', name: '社媒健康', method: 'GET', path: '/api/v1/social-media/health', desc: '各平台连接状态', cat: '社交媒体' },

  // ── 基金估值 & 回测 ──
  { id: 'fund_est', name: '基金估值', method: 'GET', path: '/api/v1/fund/estimate', desc: '实时基金净值估算', cat: '基金 & 回测' },
  { id: 'fund_pop', name: '常用基金', method: 'GET', path: '/api/v1/fund/popular', desc: '常用基金列表', cat: '基金 & 回测' },
  { id: 'bt_strat', name: '回测策略', method: 'GET', path: '/api/v1/backtest/strategies', desc: '可用回测策略列表', cat: '基金 & 回测' },
  { id: 'bt_assets', name: '回测资产', method: 'GET', path: '/api/v1/backtest/assets', desc: '支持回测的资产列表', cat: '基金 & 回测' },
]

const CAT_ICONS: Record<string, any> = {
  '系统基础': Server, '行情数据': TrendingUp, '委员会(V2·6席)': Users,
  '知识库数据': Database, '知识库管理': Database, '生命周期': Radio,
  '对话系统': Users, '成本追踪': DollarSign, '数据源': Globe,
  '决策回放': Timer, '用户修正': Database, 'Daily Radar': Radio,
  '配置 & 路由': Server, '社交媒体': Globe, '基金 & 回测': TrendingUp,
}

// ── CopyBtn ─────────────────────────────────────────────────

function CopyBtn({ getText, label = '复制' }: { getText: () => string; label?: string }) {
  const [ok, setOk] = useState(false)
  return (
    <button onClick={() => { navigator.clipboard.writeText(getText()); setOk(true); setTimeout(() => setOk(false), 1500) }}
      className={clsx('flex items-center gap-1 px-2 py-1 rounded text-xs font-mono border transition-colors',
        ok ? 'border-emerald-400/50 text-emerald-400 bg-emerald-400/10' : 'border-border text-muted-foreground hover:text-foreground hover:bg-secondary')}>
      {ok ? <CheckCircle2 size={11} /> : <Clipboard size={11} />}{ok ? '已复制' : label}
    </button>
  )
}

// ── Endpoint Card ───────────────────────────────────────────

const EpCard = memo(function EpCard({ def, state, onRun }: { def: EpDef; state: EpState; onRun: (d: EpDef) => void }) {
  const [exp, setExp] = useState(false)
  const icon = { idle: <Radio size={10} className="text-muted-foreground" />, loading: <Loader2 size={10} className="text-primary animate-spin" />, success: <CheckCircle2 size={10} className="text-emerald-400" />, error: <XCircle size={10} className="text-red-400" /> }[state.status]
  const border = { idle: 'border-border', loading: 'border-primary/30', success: 'border-emerald-400/30', error: 'border-red-400/30' }[state.status]

  return (
    <div className={clsx('rounded-xl border p-3 transition-colors', border)}>
      <div className="flex items-center gap-2">
        {icon}
        <span className="text-xs font-medium text-foreground flex-1 truncate">{def.name}</span>
        <span className={clsx('text-[10px] font-mono px-1.5 py-0.5 rounded', def.method === 'POST' ? 'bg-amber-400/10 text-amber-400' : 'bg-sky-400/10 text-sky-400')}>{def.method}</span>
        <button onClick={() => onRun(def)} disabled={state.status === 'loading'} className="px-2 py-1 rounded bg-secondary text-xs hover:bg-border disabled:opacity-50 flex items-center gap-1">
          {state.status === 'loading' ? <Loader2 size={10} className="animate-spin" /> : <Play size={10} />}测试
        </button>
      </div>
      <p className="text-[10px] text-muted-foreground mt-1 font-mono truncate">{def.path}</p>
      <p className="text-[11px] text-muted-foreground mt-0.5">{def.desc}</p>
      {state.status !== 'idle' && (
        <div className="mt-2 flex items-center gap-3 text-[10px] flex-wrap">
          {state.ms > 0 && <span className="flex items-center gap-1 text-muted-foreground"><Timer size={9} />{state.ms}ms</span>}
          {state.at && <span className="text-muted-foreground">{state.at}</span>}
          {state.error && <span className="text-red-400 truncate max-w-[200px]">{state.error}</span>}
          {state.data != null && (
            <button onClick={() => setExp(e => !e)} className="text-primary hover:underline flex items-center gap-0.5 ml-auto">
              {exp ? <ChevronDown size={9} /> : <ChevronRight size={9} />}{exp ? '收起' : 'JSON'}
            </button>
          )}
        </div>
      )}
      {exp && state.data != null && (
        <pre className="mt-2 p-2 rounded bg-background border border-border text-[10px] text-muted-foreground overflow-auto max-h-52 font-mono whitespace-pre-wrap break-all">
          {safeStringify(state.data)}
        </pre>
      )}
    </div>
  )
})

// ── Main Panel ──────────────────────────────────────────────

export default function ApiTestPanel() {
  const [states, setStates] = useState<Record<string, EpState>>({})
  const [batch, setBatch] = useState(false)
  const [replayRecord, setReplayRecord] = useState<TestHistoryRecord | null>(null)
  const batchBuf = useRef<Record<string, EpState>>({})
  const get = (id: string) => states[id] || EMPTY

  const handleReplay = useCallback((rec: TestHistoryRecord | null) => {
    if (!rec) { setReplayRecord(null); return }
    setReplayRecord(rec)
    // 将历史日志解析为状态
    const newStates: Record<string, EpState> = {}
    const lines = (rec.logText || '').split('\n').filter(Boolean)
    for (const line of lines) {
      const ok = line.startsWith('✓')
      const fail = line.startsWith('✗')
      if (!ok && !fail) continue
      // 格式: ✓ GET /api/v1/xxx (123ms) 名称
      const m = line.match(/^[✓✗]\s+(GET|POST)\s+(\S+)\s+\((\d+)ms\)\s+(.+?)(?:\s+—\s+(.+))?$/)
      if (!m) continue
      const [, , path, ms, , err] = m
      const ep = ENDPOINTS.find(e => e.path === path || path.startsWith(e.path.split('?')[0]))
      if (ep) {
        newStates[ep.id] = { status: ok ? 'success' : 'error', data: null, error: err || null, ms: parseInt(ms) || 0, at: new Date(rec.timestamp).toLocaleTimeString('zh-CN', { hour12: false }) }
      }
    }
    setStates(newStates)
  }, [])

  const run = useCallback(async (d: EpDef) => {
    setStates(p => ({ ...p, [d.id]: { ...EMPTY, status: 'loading' } }))
    const t0 = performance.now()
    // POST 类如委员会启动需要较长超时（多轮 LLM 调用）
    const timeout = d.method === 'POST' ? 120000 : 30000
    const source = axios.CancelToken.source()
    const timer = setTimeout(() => source.cancel('timeout'), timeout)
    try {
      const cfg = { cancelToken: source.token }
      const r = d.method === 'POST' ? await api.post(d.path, d.body ?? {}, cfg) : await api.get(d.path, cfg)
      setStates(p => ({ ...p, [d.id]: { status: 'success', data: safeData(r.data), error: null, ms: Math.round(performance.now() - t0), at: new Date().toLocaleTimeString('zh-CN', { hour12: false }) } }))
    } catch (e: any) {
      const isTimeout = axios.isCancel(e)
      const errMsg = isTimeout ? `超时 (${timeout / 1000}s)` : (e?.response?.data?.detail || e?.message || String(e))
      setStates(p => ({ ...p, [d.id]: { status: 'error', data: safeData(e?.response?.data ?? null), error: errMsg, ms: Math.round(performance.now() - t0), at: new Date().toLocaleTimeString('zh-CN', { hour12: false }) } }))
    } finally {
      clearTimeout(timer)
    }
  }, [])

  const runAll = useCallback(async () => {
    const t0 = performance.now()
    setBatch(true)
    batchBuf.current = {}
    for (let i = 0; i < ENDPOINTS.length; i++) {
      const d = ENDPOINTS[i]
      try {
        const t1 = performance.now()
        const timeout = d.method === 'POST' ? 120000 : 30000
        const source = axios.CancelToken.source()
        const timer = setTimeout(() => source.cancel('timeout'), timeout)
        try {
          const cfg = { cancelToken: source.token }
          const r = d.method === 'POST' ? await api.post(d.path, d.body ?? {}, cfg) : await api.get(d.path, cfg)
          batchBuf.current[d.id] = { status: 'success', data: safeData(r.data), error: null, ms: Math.round(performance.now() - t1), at: new Date().toLocaleTimeString('zh-CN', { hour12: false }) }
        } catch (e: any) {
          const isTO = axios.isCancel(e)
          batchBuf.current[d.id] = { status: 'error', data: safeData(e?.response?.data ?? null), error: isTO ? `超时 (${timeout / 1000}s)` : (e?.response?.data?.detail || e?.message || String(e)), ms: Math.round(performance.now() - t1), at: new Date().toLocaleTimeString('zh-CN', { hour12: false }) }
        } finally { clearTimeout(timer) }
      } catch { batchBuf.current[d.id] = { status: 'error', data: null, error: '未知异常', ms: 0, at: null } }
      // flush to React state every 5 endpoints (not every 1) to reduce re-renders
      if ((i + 1) % 5 === 0 || i === ENDPOINTS.length - 1) {
        const snap = { ...batchBuf.current }
        setStates(prev => ({ ...prev, ...snap }))
        await new Promise(r => setTimeout(r, 80))
      } else {
        await new Promise(r => setTimeout(r, 50))
      }
    }
    // final flush
    setStates(prev => ({ ...prev, ...batchBuf.current }))
    setBatch(false)
    // 保存到历史记录
    setTimeout(() => {
      setStates(cur => {
        const vals = Object.values(cur)
        const ok = vals.filter(s => s.status === 'success').length
        const err = vals.filter(s => s.status === 'error').length
        const logLines = ENDPOINTS.map(d => {
          const s = cur[d.id]
          if (!s || s.status === 'idle') return null
          return `${s.status === 'success' ? '✓' : '✗'} ${d.method} ${d.path} (${s.ms}ms) ${d.name}${s.error ? ' — ' + s.error : ''}`
        }).filter(Boolean)
        saveHistory({
          category: 'api',
          label: '接口面板一键测试',
          timestamp: new Date().toISOString(),
          durationMs: Math.round(performance.now() - t0),
          summary: { total: ENDPOINTS.length, passed: ok, failed: err },
          logText: logLines.join('\n'),
        })
        return cur
      })
    }, 100)
  }, [run])
  const runCat = useCallback(async (c: string) => {
    setBatch(true)
    for (const d of ENDPOINTS.filter(x => x.cat === c)) {
      await run(d)
      await new Promise(r => setTimeout(r, 150))
    }
    setBatch(false)
  }, [run])

  const grouped = useMemo(() => {
    const m = new Map<string, EpDef[]>()
    ENDPOINTS.forEach(d => { if (!m.has(d.cat)) m.set(d.cat, []); m.get(d.cat)!.push(d) })
    return m
  }, [])

  const stats = useMemo(() => {
    const v = Object.values(states)
    const tested = v.filter(s => s.status !== 'idle')
    return { total: ENDPOINTS.length, tested: tested.length, ok: v.filter(s => s.status === 'success').length, err: v.filter(s => s.status === 'error').length, avg: tested.length ? Math.round(tested.reduce((a, b) => a + b.ms, 0) / tested.length) : 0 }
  }, [states])

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4 flex-wrap">
        <button onClick={() => { setReplayRecord(null); runAll() }} disabled={batch} className="btn-primary flex items-center gap-1.5 disabled:opacity-50">
          {batch ? <Loader2 size={12} className="animate-spin" /> : <Zap size={12} />}
          一键测试全部接口（{ENDPOINTS.length}个）
        </button>
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          <span>{stats.tested}/{stats.total} 已测试</span>
          {stats.ok > 0 && <span className="text-emerald-400">✓ {stats.ok}</span>}
          {stats.err > 0 && <span className="text-red-400">✗ {stats.err}</span>}
          {stats.avg > 0 && <span>均 {stats.avg}ms</span>}
        </div>
        {stats.tested > 0 && <CopyBtn getText={() => {
          const l = [`=== 接口面板测试报告 ${new Date().toLocaleString('zh-CN')} ===`, '']
          l.push(`总计: ${stats.total} 个接口 | 已测: ${stats.tested} | ✓ ${stats.ok} | ✗ ${stats.err} | 平均 ${stats.avg}ms`)
          l.push('')
          let lastCat = ''
          ENDPOINTS.forEach(d => {
            if (d.cat !== lastCat) { if (lastCat) l.push(''); l.push(`--- ${d.cat} ---`); lastCat = d.cat }
            const s = states[d.id] || EMPTY
            const icon = s.status === 'success' ? '✓' : s.status === 'error' ? '✗' : s.status === 'loading' ? '…' : '-'
            l.push(`  ${icon} ${d.method} ${d.path}  (${s.ms || 0}ms)  ${d.name}`)
            if (s.error) l.push(`    错误: ${s.error}`)
            if (s.data != null) {
              const preview = safeStringify(s.data)
              if (preview.length <= 200) {
                l.push(`    响应: ${preview}`)
              } else {
                // 展示关键字段摘要
                const keys = Object.keys(s.data)
                const summary = keys.slice(0, 8).map(k => {
                  const v = s.data[k]
                  if (v === null || v === undefined) return `${k}=null`
                  if (typeof v === 'string') return `${k}="${v.slice(0, 40)}${v.length > 40 ? '…' : ''}"`
                  if (typeof v === 'number' || typeof v === 'boolean') return `${k}=${v}`
                  if (Array.isArray(v)) return `${k}=[${v.length}项]`
                  return `${k}={…}`
                }).join(', ')
                l.push(`    响应: {${summary}}${keys.length > 8 ? ` …共${keys.length}字段` : ''}`)
              }
            }
          })
          l.push('', `通过: ${stats.ok}/${stats.total}  失败: ${stats.err}  平均响应: ${stats.avg}ms`)
          return l.join('\n')
        }} label="复制详细结果" />}
      </div>

      {batch && <div className="h-1.5 bg-muted rounded-full overflow-hidden"><div className="h-full bg-primary rounded-full transition-all duration-300" style={{ width: `${(stats.tested / stats.total) * 100}%` }} /></div>}

      {replayRecord && (
        <div className="rounded-xl border border-amber-400/40 bg-amber-400/5 px-4 py-2 flex items-center gap-3">
          <Zap size={14} className="text-amber-400" />
          <div className="flex-1">
            <span className="text-xs font-medium text-amber-400">沉浸式回放模式</span>
            <span className="text-[10px] text-muted-foreground ml-2">{new Date(replayRecord.timestamp).toLocaleString('zh-CN')} · ✓{replayRecord.summary.passed} ✗{replayRecord.summary.failed}</span>
          </div>
          <button onClick={() => handleReplay(null)} className="px-2 py-1 rounded text-xs text-amber-400 hover:bg-amber-400/10 border border-amber-400/30">退出回放</button>
        </div>
      )}

      <TestHistorySection category="api" onReplay={handleReplay} replayId={replayRecord?.id} />

      {Array.from(grouped.entries()).map(([cat, defs]) => {
        const CI = CAT_ICONS[cat] || Server
        const cs = defs.map(d => states[d.id] || EMPTY)
        const ok = cs.filter(s => s.status === 'success').length
        const er = cs.filter(s => s.status === 'error').length
        const te = cs.filter(s => s.status !== 'idle').length
        return (
          <div key={cat} className="rounded-2xl border border-border bg-card p-4 space-y-3">
            <div className="flex items-center gap-2 justify-between">
              <div className="flex items-center gap-2 text-sm font-medium text-foreground">
                <CI size={14} className="text-primary" />{cat}
                {te > 0 && <span className="text-[10px] font-normal ml-1">{ok > 0 && <span className="text-emerald-400">✓{ok}</span>}{er > 0 && <span className="text-red-400 ml-1">✗{er}</span>}</span>}
              </div>
              <button onClick={() => runCat(cat)} disabled={batch} className="px-2 py-1 rounded bg-secondary text-xs hover:bg-border disabled:opacity-50 flex items-center gap-1"><Play size={9} />运行本组</button>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
              {defs.map(d => <EpCard key={d.id} def={d} state={get(d.id)} onRun={run} />)}
            </div>
          </div>
        )
      })}
    </div>
  )
}
