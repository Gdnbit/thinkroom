import { useState, useRef, useEffect, useMemo } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Send, Loader2, TrendingUp, TrendingDown, Minus,
         CheckCircle2, AlertTriangle, BarChart3, Plus, Trash2, ChevronDown, ChevronUp, Code2,
         ShieldCheck, DollarSign, Search, Scale, History, Zap, Paperclip, FileText, UploadCloud, XCircle } from 'lucide-react'
import { clsx } from 'clsx'
import { PieChart, Pie, Cell, Tooltip as RechartsTooltip, ResponsiveContainer } from 'recharts'
import {
  useGenerateBriefing, usePlanAndResearch, useRunCommittee, useDebate, type Briefing, type ResearchPlan,
  useChatSessions, useChatMessages, useCreateSession, useDeleteSession,
  useAddMessage, useSendChat, useReplaySnapshots, type ChatSession, type ChatMsg, type ReplaySnapshot,
}  from '@/services/hooks'
import AnswerReceipt from '@/components/AnswerReceipt'
import ReplayDrawer from '@/components/ReplayDrawer'
import RadarBar from '@/components/RadarBar'
import RefreshDataButton from '@/components/RefreshDataButton'
import CitationSources from '@/components/CitationSources'
import { withCitations } from '@/components/CitationText'
import type { CitationData } from '@/components/CitationChip'
import ModeSelector, { type ChatMode } from '@/components/ModeSelector'
import RoutingBanner from '@/components/RoutingBanner'
import { useAutoRoute, type RoutingDecision } from '@/services/hooks'
import { detectAsset } from '@/utils/assetAlias'
import { uploadApi, type IngestionProgress, type IngestionStatus } from '@/services/upload'
import { summarizeParseDiagnostics, type ParseDiagnosticsSummary } from '@/services/parseDiagnostics'
import { recordSessionUploadReceipt } from '@/services/decisionSession'

// 执行模式（存入 message.payload.mode）——不含 'auto'，auto 会被路由到其它三种之一
type Mode = 'chat' | 'briefing' | 'debate'

type UploadStage = 'uploading' | 'queued' | 'processing' | 'completed' | 'failed'
type UploadItem = {
  id: string
  fileName: string
  sourceId?: string
  jobId?: string
  stage: UploadStage
  statusText: string
  error?: string | null
  wordCount?: number
  displaySize?: string
  progressPct?: number
  progressText?: string
  stageElapsedText?: string
  parseSummary?: ParseDiagnosticsSummary | null
}

const UPLOAD_STAGE_LABEL: Record<UploadStage, string> = {
  uploading: '上传中',
  queued: '已接收',
  processing: '解析中',
  completed: '已入库',
  failed: '失败',
}

const ACTIVE_UPLOAD_STATUSES = new Set(['uploading', 'queued', 'processing', 'pending'])

function formatIngestionCount(status: IngestionStatus) {
  if (typeof status.word_count_est === 'number' && status.word_count_est > 0) {
    return `约 ${status.word_count_est.toLocaleString('zh-CN')} 词`
  }
  if (typeof status.char_count === 'number' && status.char_count > 0) {
    return `${status.char_count.toLocaleString('zh-CN')} 字符`
  }
  if (typeof status.word_count === 'number' && status.word_count > 0) {
    return `${status.word_count.toLocaleString('zh-CN')} 字符`
  }
  return undefined
}

function mapIngestionStatus(status?: string): UploadStage {
  if (status === 'completed') return 'completed'
  if (status === 'failed') return 'failed'
  if (status === 'processing') return 'processing'
  if (status === 'queued' || status === 'pending') return 'queued'
  return 'processing'
}

function formatFileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function isSystemTestSession(session: ChatSession) {
  const title = `${session.title || ''}`.toLowerCase()
  return title.includes('ro_e2e') || title.startsWith('e2e ') || title.includes('research-os-e2e')
}

function uniqueStrings(values: Array<string | null | undefined>) {
  return Array.from(new Set(values.filter((value): value is string => Boolean(value))))
}

function messageCostUsd(payload: any): number | undefined {
  const candidates = [
    payload?.total_cost_usd,
    payload?.llm_trace?.total_cost_usd,
    payload?.cost_usd,
  ]
  for (const value of candidates) {
    const numeric = Number(value)
    if (Number.isFinite(numeric)) return numeric
  }
  return undefined
}

function clampProgressPct(value?: number | null) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return undefined
  return Math.max(0, Math.min(100, Math.round(value)))
}

function formatElapsedMs(value?: number | null) {
  if (typeof value !== 'number' || value <= 0) return undefined
  return `${Math.max(1, Math.round(value / 1000))}s`
}

function summarizeJobProgress(progress?: IngestionProgress) {
  if (!progress) return null
  const pct = clampProgressPct(progress.progress_pct)
  const elapsed = formatElapsedMs(progress.current_stage_elapsed_ms ?? progress.elapsed_ms)
  const label = progress.current_stage_label || progress.message || progress.current_stage || '后台处理中'
  return {
    pct,
    elapsed,
    text: [label, pct != null ? `${pct}%` : undefined, elapsed].filter(Boolean).join(' · '),
  }
}

const DIR_COLOR: Record<string, string> = { bullish: 'text-emerald-400', bearish: 'text-red-400', neutral: 'text-slate-400' }
const ACTION_BADGE: Record<string, string> = {
  enter: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30',
  watch: 'bg-amber-500/20 text-amber-300 border-amber-500/30',
  avoid: 'bg-red-500/20 text-red-300 border-red-500/30',
  hold:  'bg-blue-500/20 text-blue-300 border-blue-500/30',
}
// V2 全席位映射（兼容 V1 bull/bear/neutral）
// 新增席位会自动 fallback 到调色板，无需手动添加
const ROLE_META: Record<string, { label: string; color: string; bg: string; icon: JSX.Element }> = {
  baseline:    { label: '基准分析',   color: 'text-blue-400',    bg: 'bg-blue-400/10 border-blue-400/30',     icon: <BarChart3 size={12}/> },
  probability: { label: '概率评估',   color: 'text-violet-400',  bg: 'bg-violet-400/10 border-violet-400/30', icon: <Scale size={12}/> },
  risk:        { label: '风险哨兵',   color: 'text-red-400',     bg: 'bg-red-400/10 border-red-400/30',       icon: <AlertTriangle size={12}/> },
  structure:   { label: '结构分析',   color: 'text-cyan-400',    bg: 'bg-cyan-400/10 border-cyan-400/30',     icon: <Search size={12}/> },
  audit:       { label: '证据审计',   color: 'text-amber-400',   bg: 'bg-amber-400/10 border-amber-400/30',   icon: <ShieldCheck size={12}/> },
  pnl:         { label: '执行核算',   color: 'text-emerald-400', bg: 'bg-emerald-400/10 border-emerald-400/30', icon: <DollarSign size={12}/> },
  bull:        { label: '多方 Bull',    color: 'text-emerald-400', bg: 'bg-emerald-400/10 border-emerald-400/30', icon: <TrendingUp size={12}/> },
  bear:        { label: '空方 Bear',    color: 'text-red-400',     bg: 'bg-red-400/10 border-red-400/30',         icon: <TrendingDown size={12}/> },
  neutral:     { label: '中立 Neutral', color: 'text-slate-300',   bg: 'bg-slate-400/10 border-slate-400/30',     icon: <Minus size={12}/> },
}
const _RC_FALLBACK_PALETTE = [
  { color: 'text-pink-400',   bg: 'bg-pink-400/10 border-pink-400/30' },
  { color: 'text-indigo-400', bg: 'bg-indigo-400/10 border-indigo-400/30' },
  { color: 'text-teal-400',   bg: 'bg-teal-400/10 border-teal-400/30' },
  { color: 'text-orange-400', bg: 'bg-orange-400/10 border-orange-400/30' },
  { color: 'text-lime-400',   bg: 'bg-lime-400/10 border-lime-400/30' },
  { color: 'text-fuchsia-400', bg: 'bg-fuchsia-400/10 border-fuchsia-400/30' },
]
function getRoleMeta(role: string) {
  if (ROLE_META[role]) return ROLE_META[role]
  const hash = Array.from(role).reduce((h, c) => h * 31 + c.charCodeAt(0), 0)
  const palette = _RC_FALLBACK_PALETTE[Math.abs(hash) % _RC_FALLBACK_PALETTE.length]
  return { label: role, ...palette, icon: <Zap size={12}/> }
}
const CONSENSUS_LABEL: Record<string, { label: string; color: string }> = {
  strong_consensus:   { label: '强共识',   color: 'text-emerald-400' },
  moderate_consensus: { label: '中等共识', color: 'text-amber-400' },
  weak_consensus:     { label: '弱共识',   color: 'text-orange-400' },
  split:              { label: '意见分裂', color: 'text-red-400' },
}


// ── Strategy Fit 辅助组件与解析器 ─────────────────────────────────────
// 用饼图 + 表格替代冗长文本，让多情景/期望值推导更直观
interface Scenario { id: string; prob: number; action: string; description: string }
interface EVRow { action: string; formula: string; result?: string }

const ACTION_HEX: Record<string, string> = {
  enter: '#34d399',  // emerald-400
  watch: '#fbbf24',  // amber-400
  avoid: '#f87171',  // red-400
  hold:  '#60a5fa',  // blue-400
}
const ACTION_CN: Record<string, string> = { enter: '入场', watch: '观望', avoid: '回避', hold: '持有' }

function ScenarioDonut({ scenarios }: { scenarios: Scenario[] }) {
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null)
  const data = scenarios.map(s => ({
    name: `情景${s.id}`,
    value: s.prob,
    action: s.action,
    description: s.description,
  }))
  const hovered = hoveredIdx != null ? data[hoveredIdx] : null
  return (
    <div className="flex flex-col lg:flex-row gap-5 items-center">
      {/* ── Donut ── */}
      <div className="relative shrink-0" style={{ width: 180, height: 180 }}>
        <ResponsiveContainer>
          <PieChart>
            <Pie
              data={data}
              dataKey="value"
              cx="50%"
              cy="50%"
              innerRadius={52}
              outerRadius={82}
              paddingAngle={2}
              stroke="none"
              isAnimationActive={false}
              onMouseEnter={(_, idx) => setHoveredIdx(idx)}
              onMouseLeave={() => setHoveredIdx(null)}
            >
              {data.map((d, i) => (
                <Cell
                  key={i}
                  fill={ACTION_HEX[d.action] || '#64748b'}
                  fillOpacity={hoveredIdx == null || hoveredIdx === i ? 1 : 0.35}
                  style={{ transition: 'fill-opacity 120ms' }}
                />
              ))}
            </Pie>
            <RechartsTooltip
              cursor={false}
              content={({ active, payload }) => {
                if (!active || !payload?.[0]?.payload) return null
                const d: any = payload[0].payload
                return (
                  <div className="bg-card border border-border rounded-lg px-3 py-2 shadow-2xl max-w-[260px]">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="inline-block w-2 h-2 rounded-full" style={{ background: ACTION_HEX[d.action] }} />
                      <span className="text-sm font-semibold text-foreground">{d.name}</span>
                      <span className="ml-auto text-sm font-mono tabular-nums font-bold" style={{ color: ACTION_HEX[d.action] }}>{d.value}%</span>
                    </div>
                    <p className="text-xs font-medium uppercase tracking-wider mb-1" style={{ color: ACTION_HEX[d.action] }}>
                      {ACTION_CN[d.action] || d.action}
                    </p>
                    <p className="text-xs text-foreground/80 leading-relaxed">{d.description}</p>
                  </div>
                )
              }}
            />
          </PieChart>
        </ResponsiveContainer>
        {/* 中心标签：hover 时留空（避免与 tooltip 重叠），非 hover 显示情景总数 */}
        <div
          className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none transition-opacity duration-150"
          style={{ opacity: hovered ? 0 : 1 }}
        >
          <p className="text-2xl font-bold text-foreground tabular-nums">{scenarios.length}</p>
          <p className="text-[10px] text-muted-foreground uppercase tracking-[0.15em]">情景</p>
        </div>
      </div>
      {/* ── Legend Table ── */}
      <div className="flex-1 min-w-0 w-full space-y-1">
        {scenarios.map((s, i) => (
          <div key={i} className="flex items-baseline gap-2.5 py-1.5 px-2 -mx-2 rounded hover:bg-secondary/40 transition-colors">
            <span className="inline-block w-2.5 h-2.5 rounded-full shrink-0 self-center" style={{ background: ACTION_HEX[s.action] || '#64748b' }} />
            <span className="shrink-0 text-sm font-mono text-muted-foreground w-14">情景{s.id}</span>
            <span className="shrink-0 text-sm font-mono tabular-nums font-bold w-12 text-right" style={{ color: ACTION_HEX[s.action] }}>{s.prob}%</span>
            <span className="shrink-0 text-xs font-semibold uppercase tracking-wider w-12" style={{ color: ACTION_HEX[s.action] }}>{s.action}</span>
            <span className="flex-1 text-sm text-foreground/80 leading-relaxed break-words">{s.description}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function ExpectedValueTable({ rows }: { rows: EVRow[] }) {
  return (
    <div className="overflow-x-auto -mx-1">
      <table className="w-full text-sm border-collapse">
        <thead>
          <tr className="border-b border-border/50">
            <th className="text-left py-2 px-2 text-[11px] font-semibold text-muted-foreground/80 uppercase tracking-wider w-14">操作</th>
            <th className="text-left py-2 px-2 text-[11px] font-semibold text-muted-foreground/80 uppercase tracking-wider">期望值公式</th>
            <th className="text-right py-2 px-2 text-[11px] font-semibold text-muted-foreground/80 uppercase tracking-wider w-20">结果</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            const isPos = r.result && /^[+]?[\d.]+%?$/.test(r.result) && !r.result.startsWith('-')
            const isNeg = r.result && r.result.startsWith('-')
            const resultColor = isPos ? 'text-emerald-300' : isNeg ? 'text-red-300' : 'text-foreground/80'
            return (
              <tr key={i} className="border-b border-border/30 last:border-0 hover:bg-secondary/20 transition-colors">
                <td className="py-2 px-2 align-top">
                  <span className="text-xs font-semibold uppercase tracking-wider" style={{ color: ACTION_HEX[r.action] }}>{r.action}</span>
                </td>
                <td className="py-2 px-2 font-mono text-xs text-foreground/65 leading-relaxed break-all">{r.formula}</td>
                <td className="py-2 px-2 text-right align-top whitespace-nowrap">
                  <span className={clsx('font-mono text-sm font-bold tabular-nums', resultColor)}>{r.result || '—'}</span>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function MatchResultInline({ kvs }: { kvs: Array<[string, string]> }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-5 gap-y-2">
      {kvs.map(([k, v], i) => (
        <div key={i} className="flex items-baseline gap-2">
          <span className="text-xs text-muted-foreground/70">{k}</span>
          <span className="text-sm font-semibold text-foreground break-words">{v}</span>
        </div>
      ))}
    </div>
  )
}

// 解析 strategy_fit_summary 为结构化段落
type FitSectionKind = 'scenarios' | 'ev_table' | 'match_result' | 'prose'
interface FitSection {
  kind: FitSectionKind
  title: string
  scenarios?: Scenario[]
  evRows?: EVRow[]
  kvs?: Array<[string, string]>
  proseLines?: string[]
}

function parseStrategyFit(raw: string): FitSection[] {
  // 1. 按 【xxx】 切段
  const rough: Array<{ title: string; lines: string[] }> = []
  let current: { title: string; lines: string[] } | null = null
  raw.split('\n').forEach(line => {
    const t = line.trim()
    if (!t) return
    const h = t.match(/^【(.+?)】(.*)/)
    if (h) {
      if (current) rough.push(current)
      current = { title: h[1].trim(), lines: [] }
      const rest = h[2].trim()
      if (rest) current.lines.push(rest)
    } else if (current) {
      current.lines.push(t)
    } else {
      if (!rough[0]) rough.push({ title: '', lines: [] })
      rough[0].lines.push(t)
    }
  })
  if (current) rough.push(current)

  // 2. 逐段分类
  return rough.map<FitSection>(({ title, lines }) => {
    // 策略匹配结果：key | key | key
    if (/策略匹配|匹配结果/.test(title)) {
      const kvs: Array<[string, string]> = []
      lines.forEach(l => {
        l.split(/[｜|]/).forEach(part => {
          const m = part.trim().match(/^(.+?)[：:]\s*(.+)$/)
          if (m) kvs.push([m[1].trim(), m[2].trim()])
        })
      })
      if (kvs.length) return { kind: 'match_result', title, kvs }
    }

    // 情景清单（至少 2 条）
    const scenarios: Scenario[] = []
    const evRows: EVRow[] = []
    const leftover: string[] = []
    lines.forEach(l => {
      // 情景：情景①a (8%)：watch, 等待反弹 / 情景[A+B] (12%): avoid, ...
      const m = l.match(/^情景\s*(\S+?)\s*(?:[\(（](\d+)%?[\)）])?\s*[：:]\s*([A-Za-z]+)[,，]?\s*(.*)$/)
      if (m) {
        const [, id, prob, action, desc] = m
        scenarios.push({
          id,
          prob: prob ? parseInt(prob) : 0,
          action: action.toLowerCase(),
          description: desc.trim(),
        })
        return
      }
      // 期望值公式：enter 期望值 = 14%×12% + ... = +0.32%
      const ev = l.match(/^(enter|watch|avoid|hold)\s*(?:期望值|期望)\s*[=≈]\s*(.+)$/i)
      if (ev) {
        const [, action, rhs] = ev
        // 提取末尾结果（= +0.32% 或 ≈ 0%）
        const tail = rhs.match(/(?:^|[=≈])\s*([+-]?\s*[\d.]+%?)\s*$/)
        let formula = rhs
        let result: string | undefined
        if (tail) {
          result = tail[1].replace(/\s+/g, '')
          const cut = rhs.lastIndexOf('=')
          if (cut > 0) formula = rhs.slice(0, cut).trim()
        }
        evRows.push({ action: action.toLowerCase(), formula, result })
        return
      }
      // avoid/空仓期望值 ≈ 0%
      const simpleEv = l.match(/^([\u4e00-\u9fa5A-Za-z\/]+?)期望值?\s*[=≈]\s*([+-]?[\d.]+%?)/)
      if (simpleEv) {
        evRows.push({ action: simpleEv[1].toLowerCase(), formula: '', result: simpleEv[2] })
        return
      }
      leftover.push(l)
    })

    if (scenarios.length >= 2) return { kind: 'scenarios', title, scenarios, proseLines: leftover }
    if (evRows.length >= 2) return { kind: 'ev_table', title, evRows, proseLines: leftover }
    return { kind: 'prose', title, proseLines: lines }
  })
}

function BriefingCard({ briefing }: { briefing: Briefing }) {
  const dc = DIR_COLOR[briefing.direction] || 'text-slate-400'
  const ac = ACTION_BADGE[briefing.recommended_action] || ACTION_BADGE.watch
  const sourceMode = briefing.source_mode ?? (briefing.is_mock ? 'mock' : 'live')
  const llmMock = !!briefing.is_mock
  const sourceBadge = sourceMode === 'live' && !llmMock
    ? { label: '实时数据', cls: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30' }
    : sourceMode === 'mixed'
    ? { label: '混合数据', cls: 'bg-blue-500/15 text-blue-400 border-blue-500/30' }
    : { label: '模拟数据', cls: 'bg-amber-500/15 text-amber-400 border-amber-500/30' }
  const [showSources, setShowSources] = useState(false)
  const [showRaw, setShowRaw] = useState(false)
  const ds = briefing.data_sources
  // UI-2 重构：单层卡片，使用轻分隔符替代嵌套框；小标签统一风格；防溢出 min-w-0 + break-words
  const dirLabel = briefing.direction === 'bullish' ? '看涨' : briefing.direction === 'bearish' ? '看跌' : '中性'
  const actionLabel = briefing.recommended_action === 'enter' ? '入场' : briefing.recommended_action === 'watch' ? '观望' : briefing.recommended_action === 'avoid' ? '回避' : '持有'
  return (
    <div className="bg-card border border-border rounded-xl p-5 space-y-4 min-w-0 max-w-full overflow-hidden break-words">
      {/* ── Header ── 一行式：标题 + 右侧动作徽章 */}
      <div className="flex items-start justify-between gap-3 min-w-0">
        <div className="flex-1 min-w-0">
          <p className="text-base font-semibold text-foreground leading-snug break-words">{briefing.title}</p>
          <div className="flex items-center gap-x-2 gap-y-1 mt-1.5 flex-wrap text-xs">
            <span className={clsx('font-medium', dc)}>{dirLabel}</span>
            <span className="text-muted-foreground/50">·</span>
            <span className="text-muted-foreground">信号质量 {briefing.setup_quality}</span>
            <span className="text-muted-foreground/50">·</span>
            <span className={clsx('text-[10px] border rounded px-1.5 py-0.5', sourceBadge.cls)}>{sourceBadge.label}</span>
            {llmMock && sourceMode !== 'mock' && <span className="text-[10px] text-amber-400/70">LLM回退</span>}
          </div>
        </div>
        <span className={clsx('text-sm px-3 py-1 rounded-full border shrink-0 font-medium', ac)}>
          {actionLabel}
        </span>
      </div>
      {/* ── Executive summary ── 无边框，行距松 */}
      {(() => {
        let summary = briefing.executive_summary
        if (summary && summary.trim().startsWith('{')) {
          try {
            const parsed = JSON.parse(summary)
            summary = parsed.executive_summary || parsed.synthesis_narrative || summary
          } catch {}
        }
        return <p className="text-sm text-foreground/90 leading-[1.75] break-words">{summary}</p>
      })()}
      {/* ── Reasoning chain ── 放大到 text-sm，更易读 */}
      {(briefing.supporting_points.length > 0 || briefing.contrary_points.length > 0) && (
        <div className="pt-4 border-t border-border/50 grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-4">
          {briefing.supporting_points.length > 0 && (
            <div className="min-w-0">
              <p className="text-xs font-semibold text-emerald-400/90 mb-2 flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />支撑因素
              </p>
              <ul className="space-y-1.5">
                {briefing.supporting_points.map((p, i) =>
                  <li key={i} className="text-sm text-foreground/80 leading-[1.75] break-words pl-3.5 relative before:content-['·'] before:absolute before:left-0 before:text-emerald-400/70">{p}</li>
                )}
              </ul>
            </div>
          )}
          {briefing.contrary_points.length > 0 && (
            <div className="min-w-0">
              <p className="text-xs font-semibold text-red-400/90 mb-2 flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-red-400" />反驳因素
              </p>
              <ul className="space-y-1.5">
                {briefing.contrary_points.map((p, i) =>
                  <li key={i} className="text-sm text-foreground/80 leading-[1.75] break-words pl-3.5 relative before:content-['·'] before:absolute before:left-0 before:text-red-400/70">{p}</li>
                )}
              </ul>
            </div>
          )}
        </div>
      )}
      {/* ── Risks ── 放大到 text-sm */}
      {briefing.key_risks.length > 0 && (
        <div className="pt-4 border-t border-border/50">
          <p className="text-xs font-semibold text-amber-400/90 mb-2 flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-amber-400" />关键风险
          </p>
          <ul className="space-y-1.5">
            {briefing.key_risks.map((r, i) =>
              <li key={i} className="text-sm text-foreground/80 leading-[1.75] break-words pl-4 relative before:content-['⚠'] before:absolute before:left-0 before:text-amber-400/80 before:text-xs">{r}</li>
            )}
          </ul>
        </div>
      )}
      {/* ── Strategy fit ── 结构化展示：饼图(情景概率) + 表格(期望值) + KV(匹配) */}
      {(briefing as any).strategy_fit_summary && (
        <div className="pt-4 border-t border-border/50 space-y-5">
          <p className="text-xs font-semibold text-primary/90 flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-primary" />策略适配分析
          </p>
          {parseStrategyFit((briefing as any).strategy_fit_summary as string).map((sec, si) => (
            <div key={si} className="space-y-2.5">
              {sec.title && (
                <h4 className="text-sm font-semibold text-foreground/90 flex items-baseline gap-2">
                  {sec.title}
                  {sec.kind === 'scenarios' && (
                    <span className="text-[10px] font-normal text-muted-foreground/60 uppercase tracking-wider">
                      · 概率分布 · 悬浮查看详情
                    </span>
                  )}
                  {sec.kind === 'ev_table' && (
                    <span className="text-[10px] font-normal text-muted-foreground/60 uppercase tracking-wider">
                      · 各操作加权收益
                    </span>
                  )}
                </h4>
              )}
              {sec.kind === 'scenarios' && sec.scenarios && <ScenarioDonut scenarios={sec.scenarios} />}
              {sec.kind === 'ev_table' && sec.evRows && <ExpectedValueTable rows={sec.evRows} />}
              {sec.kind === 'match_result' && sec.kvs && <MatchResultInline kvs={sec.kvs} />}
              {/* 非结构化或辅助说明行：用 prose 补充渲染 */}
              {sec.proseLines && sec.proseLines.length > 0 && (
                <div className="space-y-1.5 text-sm leading-[1.8]">
                  {sec.proseLines.map((line, li) => {
                    // 结论式语句：粗体强调
                    if (line.startsWith('当前最优选择') || line.startsWith('「') || line.startsWith('\u300c')) {
                      return <p key={li} className="font-medium text-foreground">{line}</p>
                    }
                    // 步骤小标题：灰色 uppercase
                    if (/^步骤[一二三四五\d]/.test(line)) {
                      return <p key={li} className="text-[11px] font-semibold text-muted-foreground/70 uppercase tracking-wider mt-2">{line}</p>
                    }
                    // 因素说明：粗体变量名
                    const factor = line.match(/^因素[一二三四五\d]+[：:]\s*([^—]+?)(?:——|—)\s*(.*)$/)
                    if (factor) {
                      return (
                        <p key={li} className="text-foreground/80">
                          <span className="font-medium text-foreground">{factor[1].trim()}</span>
                          {factor[2] && <span className="text-muted-foreground"> · {factor[2]}</span>}
                        </p>
                      )
                    }
                    return <p key={li} className="text-foreground/75">{line}</p>
                  })}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      {/* ── Invalidation ── 放大到 text-sm */}
      {(briefing as any).invalidation_conditions?.length > 0 && (
        <div className="pt-4 border-t border-border/50">
          <p className="text-xs font-semibold text-muted-foreground/80 mb-2 flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground/60" />失效条件
          </p>
          <ul className="space-y-1.5">
            {(briefing as any).invalidation_conditions.map((c: string, i: number) =>
              <li key={i} className="text-sm text-muted-foreground leading-[1.75] break-words pl-4 relative before:content-['✕'] before:absolute before:left-0 before:text-muted-foreground/60 before:text-xs">{c}</li>)}
          </ul>
        </div>
      )}
      {/* Footer */}
      <div className="border-t border-border/50 pt-3 flex items-center justify-between gap-2 flex-wrap">
        <span className="text-xs text-muted-foreground/60">
          {briefing.model_used ? `模型: ${briefing.model_used}` : sourceMode === 'mock' ? '模型: mock' : ''}
          {briefing.duration_ms ? ` · ${(briefing.duration_ms / 1000).toFixed(1)}s` : ''}
        </span>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowRaw(v => !v)}
            className="flex items-center gap-1 text-xs text-muted-foreground/60 hover:text-foreground/80 border border-border rounded px-2 py-1 transition-colors"
          >
            <Code2 size={11} />
            <span>{showRaw ? '隐藏原始' : '原始数据'}</span>
            {showRaw ? <ChevronUp size={10} /> : <ChevronDown size={10} />}
          </button>
          {ds && (
            <button
              onClick={() => setShowSources(v => !v)}
              className="text-xs text-muted-foreground/60 hover:text-foreground/80 border border-border rounded px-2 py-1 transition-colors"
            >
              {showSources ? '收起来源' : '数据来源'}
            </button>
          )}
          <span className="text-xs text-muted-foreground/60">
            {briefing.created_at ? new Date(briefing.created_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : ''}
          </span>
        </div>
      </div>
      {/* 原始数据面板 */}
      {showRaw && (
        <div className="border border-border rounded-lg bg-muted/20 p-3 space-y-3">
          <p className="text-xs font-semibold text-foreground/80">原始输入 / 输出</p>
          <div className="space-y-2">
            <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider">输入摘要</p>
            <pre className="text-[11px] text-foreground/70 bg-muted/40 rounded p-2 overflow-x-auto whitespace-pre-wrap break-words leading-relaxed">
              {JSON.stringify({
                question: (briefing as any).question,
                asset: briefing.topic,
                direction: briefing.direction,
                setup_quality: briefing.setup_quality,
                model_used: briefing.model_used,
                is_mock: briefing.is_mock,
                source_mode: (briefing as any).source_mode,
              }, null, 2)}
            </pre>
            <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider">原始输出 (JSON)</p>
            <pre className="text-[11px] text-foreground/70 bg-muted/40 rounded p-2 overflow-x-auto whitespace-pre-wrap break-words leading-relaxed max-h-80">
              {JSON.stringify({
                title: briefing.title,
                executive_summary: briefing.executive_summary,
                supporting_points: briefing.supporting_points,
                contrary_points: briefing.contrary_points,
                key_risks: briefing.key_risks,
                strategy_fit_summary: (briefing as any).strategy_fit_summary,
                invalidation_conditions: (briefing as any).invalidation_conditions,
                recommended_action: briefing.recommended_action,
                conviction: (briefing as any).conviction,  // optional debug field
              }, null, 2)}
            </pre>
          </div>
        </div>
      )}
      {/* 数据溯源面板 */}
      {showSources && ds && (
        <div className="border border-border rounded-lg bg-muted/30 p-3 space-y-2 text-xs">
          <p className="font-semibold text-foreground/80 mb-1">数据来源溯源</p>
          {/* 私域知识库 */}
          <div className="space-y-1">
            <div className="flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-purple-400 shrink-0" />
              <span className="font-medium text-foreground/70">私域知识库</span>
              <span className={clsx('ml-auto text-[10px] rounded px-1', ds.claims.count > 0 ? 'text-emerald-400' : 'text-muted-foreground/50')}>
                {ds.claims.count} 条观点
              </span>
            </div>
            {ds.claims.items.map((c, i) => (
              <p key={i} className="pl-3 text-muted-foreground/70 leading-relaxed">
                · <span className={c.stance === 'bullish' ? 'text-emerald-400' : c.stance === 'bearish' ? 'text-red-400' : 'text-slate-400'}>[{c.stance}]</span> {c.summary}
              </p>
            ))}
            {ds.claims.count === 0 && <p className="pl-3 text-muted-foreground/40">· 暂无私域观点</p>}
          </div>
          {/* 市场事件 */}
          <div className="space-y-1">
            <div className="flex items-center gap-1.5">
              <span className={clsx('w-1.5 h-1.5 rounded-full shrink-0', ds.events.is_mock ? 'bg-amber-400' : 'bg-emerald-400')} />
              <span className="font-medium text-foreground/70">市场事件</span>
              <span className="ml-1 text-[10px] text-muted-foreground/50">{ds.events.is_mock ? '模拟' : '实时'}</span>
              <span className="ml-auto text-[10px] text-muted-foreground/50">{ds.events.count} 条</span>
            </div>
            {ds.events.items.map((e, i) => (
              <p key={i} className="pl-3 text-muted-foreground/70 leading-relaxed">· {e.title}{e.source ? <span className="text-muted-foreground/40"> ({e.source})</span> : ''}</p>
            ))}
            {ds.events.count === 0 && <p className="pl-3 text-muted-foreground/40">· 暂无事件</p>}
          </div>
          {/* 市场行情 */}
          <div className="space-y-1">
            <div className="flex items-center gap-1.5">
              <span className={clsx('w-1.5 h-1.5 rounded-full shrink-0', ds.market.is_mock ? 'bg-amber-400' : 'bg-emerald-400')} />
              <span className="font-medium text-foreground/70">市场行情 ({ds.market.asset})</span>
              <span className="ml-1 text-[10px] text-muted-foreground/50">{ds.market.is_mock ? '模拟' : '实时'}</span>
            </div>
            <div className="pl-3 flex flex-wrap gap-x-3 gap-y-0.5 text-muted-foreground/70">
              {Object.entries(ds.market.snapshot).map(([k, v]) => (
                <span key={k}>{k}: <span className="text-foreground/80">{typeof v === 'number' ? v.toLocaleString() : String(v)}</span></span>
              ))}
              {Object.keys(ds.market.snapshot).length === 0 && <span className="text-muted-foreground/40">无快照数据</span>}
            </div>
          </div>
          {/* 资金流向 */}
          <div className="space-y-1">
            <div className="flex items-center gap-1.5">
              <span className={clsx('w-1.5 h-1.5 rounded-full shrink-0', ds.flow.is_mock ? 'bg-amber-400' : 'bg-emerald-400')} />
              <span className="font-medium text-foreground/70">资金流向信号</span>
              <span className="ml-1 text-[10px] text-muted-foreground/50">{ds.flow.is_mock ? '模拟' : '实时'}</span>
              <span className="ml-auto text-[10px] text-muted-foreground/50">{ds.flow.signals_count} 个信号</span>
            </div>
            <div className="pl-3 flex flex-wrap gap-x-3 gap-y-0.5 text-muted-foreground/70">
              {Object.entries(ds.flow.snapshot).map(([k, v]) => (
                <span key={k}>{k}: <span className="text-foreground/80">{typeof v === 'number' ? Number(v).toFixed(4) : String(v)}</span></span>
              ))}
              {Object.keys(ds.flow.snapshot).length === 0 && <span className="text-muted-foreground/40">无快照数据</span>}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}


function DebateResultCard({ debateId }: { debateId: string }) {
  const { data, isLoading } = useDebate(debateId)
  if (isLoading) return (
    <div className="flex items-center gap-2 text-muted-foreground text-sm bg-card border border-border rounded-xl p-4">
      <Loader2 size={14} className="animate-spin" /><span>委员会辩论进行中…</span>
    </div>
  )
  if (!data) return <p className="text-xs text-red-400">辩论数据加载失败</p>
  const rounds = data.rounds ?? []
  const maxRound = Math.max(0, ...rounds.map((r: any) => r.round_number))
  const adj = data.adjudication
  const consensus = adj ? (CONSENSUS_LABEL[adj.consensus_level] ?? { label: adj.consensus_level, color: 'text-slate-400' }) : null
  return (
    <div className="space-y-3 min-w-0 max-w-full">
      {adj && (() => {
        const baselineScore = adj.baseline_score ?? adj.bull_score ?? 0
        const riskScore = adj.risk_score ?? adj.bear_score ?? 0
        const hasV2 = adj.baseline_score != null || adj.risk_score != null
        return (
          <div className="bg-card border-2 border-primary/30 rounded-xl p-4 space-y-2 min-w-0 overflow-hidden break-words">
            <div className="flex items-center gap-2"><BarChart3 size={13} className="text-primary" />
              <p className="text-sm font-semibold text-foreground">委员会裁决</p>
              {adj.is_mock && <span className="text-xs text-muted-foreground border border-border rounded px-1.5">模拟</span>}</div>
            <div className="flex flex-wrap items-baseline gap-3">
              <span className={clsx('text-lg font-bold', DIR_COLOR[adj.direction] ?? 'text-foreground')}>{adj.direction.toUpperCase()}</span>
              <span className="text-base font-bold text-foreground">{Math.round(adj.conviction*100)}% 确信</span>
              {consensus && <span className={clsx('text-sm font-medium', consensus.color)}>{consensus.label}</span>}
              <span className="text-sm font-medium text-primary">→ {adj.recommended_action.toUpperCase()}</span>
            </div>
            <p className="text-xs text-foreground/80 leading-relaxed border-l-2 border-primary/40 pl-3">{adj.synthesis}</p>
            {hasV2 ? (
              <div className="space-y-1 bg-secondary/30 rounded-lg p-2">
                {[{l:'基准',s:baselineScore,c:'bg-blue-400'},{l:'概率',s:adj.probability_score??0.5,c:'bg-violet-400'},{l:'风控',s:riskScore,c:'bg-red-400'},{l:'结构',s:adj.structure_score??0.5,c:'bg-cyan-400'}].map(b=><div key={b.l} className="flex items-center gap-2"><span className="text-[10px] text-muted-foreground w-8">{b.l}</span><div className="flex-1 h-1 bg-border rounded-full overflow-hidden"><div className={clsx('h-full rounded-full',b.c)} style={{width:`${Math.round(b.s*100)}%`}}/></div><span className="text-[10px] font-mono text-muted-foreground">{Math.round(b.s*100)}%</span></div>)}
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-2 text-xs">
                <div className="bg-emerald-400/5 border border-emerald-400/20 rounded-lg p-2"><span className="text-emerald-400">多方 {Math.round(baselineScore*100)}%</span></div>
                <div className="bg-red-400/5 border border-red-400/20 rounded-lg p-2"><span className="text-red-400">空方 {Math.round(riskScore*100)}%</span></div>
              </div>
            )}
            {(adj.risk_warnings as string[] | undefined)?.length ? <div>{(adj.risk_warnings as string[]).slice(0,3).map((w:string,i:number)=><p key={i} className="text-xs text-amber-400/90 flex gap-1.5"><AlertTriangle size={10} className="mt-0.5 shrink-0"/>{w}</p>)}</div> : null}
            {(adj.hedging_advice as string | undefined) && <p className="text-xs text-foreground/70 bg-amber-400/5 border border-amber-400/15 rounded px-2 py-1"><span className="font-medium text-amber-400">对冲：</span>{adj.hedging_advice as string}</p>}
            {adj.key_agreements?.length > 0 && (<div><p className="text-xs font-medium text-muted-foreground mb-1">共同认可</p>
              {adj.key_agreements.map((a:string,i:number) => (<p key={i} className="text-xs text-foreground/80 flex gap-1.5"><CheckCircle2 size={10} className="text-emerald-400 mt-0.5 shrink-0"/>{a}</p>))}</div>)}
            {adj.key_disagreements?.length > 0 && (<div><p className="text-xs font-medium text-muted-foreground mb-1">核心分歧</p>
              {adj.key_disagreements.map((d:string,i:number) => (<p key={i} className="text-xs text-foreground/80 flex gap-1.5"><AlertTriangle size={10} className="text-amber-400 mt-0.5 shrink-0"/>{d}</p>))}</div>)}
          </div>
        )
      })()}
      {Array.from({ length: maxRound }, (_,i) => i+1).map(rn => {
        const roundRoles = rounds.filter((x:any) => x.round_number===rn).map((x:any)=>x.role as string).filter((v:string,i:number,a:string[])=>a.indexOf(v)===i)
        const cols = Math.min(roundRoles.length, 3)
        return (
          <div key={rn}><p className="text-xs font-medium text-muted-foreground mb-2">第 {rn} 轮辩论</p>
            <div className={clsx('grid gap-2', cols<=2?'grid-cols-1 md:grid-cols-2':cols<=3?'grid-cols-1 md:grid-cols-3':'grid-cols-1 md:grid-cols-2 lg:grid-cols-3')}>
              {roundRoles.map(role => {
                const r = rounds.find((x:any) => x.round_number===rn && x.role===role)
                if (!r) return null
                const meta = getRoleMeta(role)
                return (<div key={role} className={clsx('border rounded-xl p-3 space-y-1.5', meta.bg)}>
                  <span className={clsx('flex items-center gap-1 text-xs font-semibold', meta.color)}>{meta.icon}{meta.label}</span>
                  <p className="text-xs text-foreground/80 leading-relaxed">{r.position}</p>
                  {r.key_arguments?.slice(0,2).map((a:string,i:number) => (<p key={i} className={clsx('text-xs flex gap-1.5',meta.color)}><span>▸</span><span className="text-foreground/70">{a}</span></p>))}
                </div>)
              })}
            </div></div>
        )
      })}
    </div>
  )
}


function copyBriefingText(briefing: any): string {
  const dir = briefing.direction === 'bullish' ? '看涨' : briefing.direction === 'bearish' ? '看跌' : '中性'
  const action = briefing.recommended_action === 'enter' ? '入场' : briefing.recommended_action === 'watch' ? '观望' : briefing.recommended_action === 'avoid' ? '回避' : '持有'
  const src = briefing.is_mock ? '[模拟数据]' : '[实时数据]'
  const parts = [
    `${briefing.title} ${src}`,
    `方向：${dir} | 信号质量：${briefing.setup_quality} | 建议：${action}`,
    '',
    '摘要：',
    briefing.executive_summary,
  ]
  if (briefing.supporting_points?.length) {
    parts.push('', '支撇因素：')
    briefing.supporting_points.forEach((p: string) => parts.push(`  - ${p}`))
  }
  if (briefing.contrary_points?.length) {
    parts.push('', '反驳因素：')
    briefing.contrary_points.forEach((p: string) => parts.push(`  - ${p}`))
  }
  if (briefing.key_risks?.length) {
    parts.push('', '关键风险：')
    briefing.key_risks.forEach((r: string) => parts.push(`  - ${r}`))
  }
  if (briefing.strategy_fit_summary) {
    parts.push('', `策略适配：${briefing.strategy_fit_summary}`)
  }
  if (briefing.invalidation_conditions?.length) {
    parts.push('', '失效条件：')
    briefing.invalidation_conditions.forEach((c: string) => parts.push(`  - ${c}`))
  }
  return parts.join('\n')
}

function copyDebateText(data: any): string {
  if (!data) return ''
  const adj = data.adjudication
  const rounds = data.rounds ?? []
  const parts: string[] = []
  parts.push(`委员会辩论 — ${data.question || ''} [${data.asset || ''}]`)
  if (adj) {
    const dir = adj.direction === 'bullish' ? '看涨' : adj.direction === 'bearish' ? '看跌' : '中性'
    const consensus = CONSENSUS_LABEL[adj.consensus_level]?.label ?? adj.consensus_level
    parts.push(`\n裁决：${dir} | 确信度：${Math.round(adj.conviction * 100)}% | ${consensus} | 建议：${adj.recommended_action.toUpperCase()}`)
    parts.push(`多方 ${Math.round((adj.bull_score ?? 0) * 100)}% / 空方 ${Math.round((adj.bear_score ?? 0) * 100)}%`)
    if (adj.synthesis) parts.push(`\n综合判断：\n${adj.synthesis}`)
    if (adj.key_agreements?.length) {
      parts.push('\n共同认可：')
      adj.key_agreements.forEach((a: string) => parts.push(`  ✓ ${a}`))
    }
    if (adj.key_disagreements?.length) {
      parts.push('\n核心分歧：')
      adj.key_disagreements.forEach((d: string) => parts.push(`  △ ${d}`))
    }
  }
  const maxRound = Math.max(0, ...rounds.map((r: any) => r.round_number))
  for (let rn = 1; rn <= maxRound; rn++) {
    parts.push(`\n第 ${rn} 轮辩论：`)
    const seen = new Set<string>()
    for (const r of rounds.filter((x: any) => x.round_number === rn)) {
      if (seen.has(r.role)) continue
      seen.add(r.role)
      const roleLabel = getRoleMeta(r.role).label
      parts.push(`  [${roleLabel}] ${r.position}`)
      r.key_arguments?.slice(0, 2).forEach((a: string) => parts.push(`    ▸ ${a}`))
    }
  }
  return parts.join('\n')
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  const handleCopy = () => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }
  return (
    <button
      onClick={handleCopy}
      className="mt-1 flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors px-2 py-0.5 rounded border border-border hover:border-primary/40"
    >
      {copied ? '✓ 已复制' : '复制'}
    </button>
  )
}

const MODE_LABEL: Record<string, { label: string; cls: string }> = {
  chat:     { label: '智能对话', cls: 'text-sky-400 border-sky-400/30 bg-sky-400/5' },
  briefing: { label: '研究简报', cls: 'text-violet-400 border-violet-400/30 bg-violet-400/5' },
  debate:   { label: '辩论增强', cls: 'text-amber-400 border-amber-400/30 bg-amber-400/5' },
}

// ═════════ Sub-components (each owns its own hooks, unconditional) ═════════

function ChatBubble({ msg, modeInfo }: { msg: ChatMsg; modeInfo?: { label: string; cls: string } }) {
  const claimsUsed = msg.payload?.claims_used
  const modelUsed = msg.payload?.model_used
  const rawInput = msg.payload?.raw_input
  const rawOutput = msg.payload?.raw_output
  // 旧字段兼容：早期消息用 context_injected，新消息用 context_block
  const contextInjected: string[] = msg.payload?.context_injected ?? []
  const contextBlock: string = msg.payload?.context_block ?? ''
  const citations: CitationData[] = msg.payload?.citations ?? []
  const qualityFilters = msg.payload?.quality_filters ?? {}
  const evidenceSufficiency = typeof qualityFilters.evidence_sufficiency === 'string' ? qualityFilters.evidence_sufficiency : undefined
  const insufficientEvidenceReason = typeof qualityFilters.insufficient_evidence_reason === 'string' ? qualityFilters.insufficient_evidence_reason : undefined
  const evidenceInsufficient = evidenceSufficiency === 'insufficient'
  const stanceDist = msg.payload?.stance_distribution
  const balancedApplied = !!msg.payload?.balanced_applied
  const costUsd = messageCostUsd(msg.payload)
  const [showRaw, setShowRaw] = useState(false)
  const withC = (children: any) => withCitations(children, citations)
  return (
      <div className="flex flex-col gap-1 min-w-0 max-w-full">
        <div className="bg-card border border-border rounded-xl px-4 py-3 text-sm text-foreground/90 leading-relaxed prose prose-sm prose-invert max-w-none break-words overflow-hidden">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              p: ({children}) => <p className="mb-2 last:mb-0">{withC(children)}</p>,
              h3: ({children}) => <h3 className="text-sm font-semibold text-foreground mt-3 mb-1">{withC(children)}</h3>,
              h4: ({children}) => <h4 className="text-sm font-semibold text-foreground/80 mt-2 mb-1">{withC(children)}</h4>,
              ul: ({children}) => <ul className="list-disc pl-4 space-y-0.5 mb-2">{children}</ul>,
              ol: ({children}) => <ol className="list-decimal pl-4 space-y-0.5 mb-2">{children}</ol>,
              li: ({children}) => <li className="text-foreground/80">{withC(children)}</li>,
              strong: ({children}) => <strong className="font-semibold text-foreground">{withC(children)}</strong>,
              em: ({children}) => <em className="italic">{withC(children)}</em>,
              code: ({children}) => <code className="font-mono text-xs bg-muted/60 px-1 py-0.5 rounded text-primary/90">{children}</code>,
              hr: () => <hr className="border-border my-2" />,
              table: ({children}) => <div className="overflow-x-auto my-2"><table className="w-full text-xs border-collapse border border-border rounded-lg overflow-hidden">{children}</table></div>,
              thead: ({children}) => <thead className="bg-muted/60">{children}</thead>,
              tbody: ({children}) => <tbody>{children}</tbody>,
              tr: ({children}) => <tr className="border-b border-border last:border-0">{children}</tr>,
              th: ({children}) => <th className="px-3 py-2 text-left font-semibold text-foreground/80 border-r border-border last:border-0">{children}</th>,
              td: ({children}) => <td className="px-3 py-1.5 text-foreground/70 border-r border-border last:border-0">{withC(children)}</td>,
            }}
          >
            {msg.content}
          </ReactMarkdown>
        </div>
        {/* 来源详情：折叠式 + [N] 跳转知识库 */}
        <CitationSources citations={citations} stanceDist={stanceDist} balancedApplied={balancedApplied} />
        <div className="flex items-center gap-2 flex-wrap">
          <CopyButton text={msg.content} />
          {modelUsed && modelUsed !== 'mock' && <span className="text-[10px] font-mono text-muted-foreground/60 bg-muted/40 border border-border rounded px-1.5 py-0.5">{modelUsed}</span>}
          {claimsUsed > 0 && <span className="text-[10px] text-muted-foreground/50">引用 {claimsUsed} 条私域观点</span>}
          {costUsd != null && <span className="text-[10px] font-mono text-muted-foreground/40">~${costUsd.toFixed(5)}</span>}
          {modeInfo && <span className={clsx('text-[10px] border rounded px-1.5 py-0.5 font-medium', modeInfo.cls)}>{modeInfo.label}</span>}
          {(rawInput || rawOutput) && (
            <button onClick={() => setShowRaw(v => !v)} className="text-[10px] text-muted-foreground/50 hover:text-muted-foreground flex items-center gap-0.5 transition-colors">
              <Code2 size={10}/> {showRaw ? '隐藏' : '开发者视图'}
            </button>
          )}
        </div>
        {showRaw && (
          <div className="mt-1 space-y-2 text-[11px] font-mono">
            {contextBlock ? (
              <div className="bg-blue-400/5 border border-blue-400/20 rounded-lg p-2.5">
                <p className="text-blue-300/70 font-semibold mb-1">Harness 注入上下文</p>
                <pre className="text-foreground/50 whitespace-pre-wrap leading-relaxed">{contextBlock}</pre>
              </div>
            ) : contextInjected.length > 0 ? (
              <div className="bg-blue-400/5 border border-blue-400/20 rounded-lg p-2.5">
                <p className="text-blue-300/70 font-semibold mb-1">私域上下文注入</p>
                {contextInjected.map((c, i) => <p key={i} className="text-foreground/50 leading-relaxed">{c}</p>)}
              </div>
            ) : null}
            {rawInput && (
              <div className="bg-muted/30 border border-border rounded-lg p-2.5">
                <div className="flex items-center justify-between mb-1">
                  <p className="text-muted-foreground/60 font-semibold">INPUT →</p>
                  <CopyButton text={rawInput} />
                </div>
                <pre className="text-foreground/50 whitespace-pre-wrap leading-relaxed">{rawInput}</pre>
              </div>
            )}
            {rawOutput && (
              <div className="bg-muted/30 border border-border rounded-lg p-2.5">
                <div className="flex items-center justify-between mb-1">
                  <p className="text-muted-foreground/60 font-semibold">OUTPUT ←</p>
                  <CopyButton text={rawOutput} />
                </div>
                <pre className="text-foreground/50 whitespace-pre-wrap leading-relaxed">{rawOutput}</pre>
              </div>
            )}
          </div>
        )}
        <AnswerReceipt data={{
          mode: 'chat',
          dataHits: { claims: claimsUsed ?? 0 },
          dataTime: msg.payload?.reference_time,
          model: modelUsed,
          costUsd,
          isMock: modelUsed === 'mock',
          evidenceSufficiency,
          insufficientEvidenceReason,
          stanceMismatches: msg.payload?.stance_mismatches,
          processSteps: evidenceInsufficient
            ? [
              '限定在当前会话选定资料内检索',
              '未找到足够支撑该问题的证据',
              '返回保守提示，避免生成无依据结论',
            ]
            : [
              claimsUsed > 0 ? `在知识库中找到 ${claimsUsed} 条相关观点并注入上下文` : '在知识库中搜索相关内容',
              msg.payload?.intent_assets?.length ? `识别到资产：${msg.payload.intent_assets.join('、')}` : '结合上下文生成回答',
              '综合生成回答',
            ].filter(Boolean) as string[],
        }} />
      </div>
    )
}

function BriefingBubble({ msg, modeInfo }: { msg: ChatMsg; modeInfo?: { label: string; cls: string } }) {
  const copyText = copyBriefingText(msg.payload.briefing)
  const b = msg.payload.briefing
  const modelUsed = b.model_used
  const durationMs = b.duration_ms
  const plan: ResearchPlan | undefined = msg.payload.research_plan
  const dataFetched = msg.payload.data_fetched
  const qualityReport = msg.payload?.quality_report ?? b.quality_report
  const qualityFilters = msg.payload?.quality_filters ?? b.quality_filters ?? {}
  const decisionGuardrail = msg.payload?.decision_guardrail ?? b.decision_guardrail
  const llmTrace = msg.payload?.llm_trace ?? b.llm_trace
  const costUsd = messageCostUsd(msg.payload)
  const evidenceSufficiency = typeof qualityFilters.evidence_sufficiency === 'string' ? qualityFilters.evidence_sufficiency : undefined
  const insufficientEvidenceReason = typeof qualityFilters.insufficient_evidence_reason === 'string' ? qualityFilters.insufficient_evidence_reason : undefined
  const [showRaw, setShowRaw] = useState(false)
  const [showPlan, setShowPlan] = useState(false)
  return (
      <div>
        {/* 研究计划摘要 */}
        {plan && (
          <div className="mb-2 bg-blue-400/5 border border-blue-400/20 rounded-lg overflow-hidden">
            <button onClick={() => setShowPlan(v => !v)} className="w-full flex items-center justify-between px-3 py-2 text-xs text-blue-300/80 hover:text-blue-200 transition-colors">
              <span className="flex items-center gap-1.5">
                <BarChart3 size={11}/>
                <span className="font-medium">研究计划</span>
                {plan.is_open_ended
                  ? <span className="text-violet-400/60">宏观开放式 · 全库RAG</span>
                  : <span className="text-blue-400/50">{plan.asset}{plan.secondary_assets?.length > 0 ? ` + ${plan.secondary_assets.join('/')}` : ''} · 初步方向：{plan.preliminary_direction} ({Math.round(plan.preliminary_conviction * 100)}%)</span>
                }
              </span>
              {showPlan ? <ChevronUp size={12}/> : <ChevronDown size={12}/>}
            </button>
            {showPlan && (
              <div className="px-3 pb-3 space-y-2 text-xs">
                {plan.reasoning && <p className="text-blue-200/70 leading-relaxed">{plan.reasoning}</p>}
                {plan.key_hypotheses?.length > 0 && (
                  <div><p className="text-muted-foreground/60 font-semibold mb-1">初步假设</p>
                    {plan.key_hypotheses.map((h, i) => <p key={i} className="text-foreground/60 mb-0.5">· {h}</p>)}</div>
                )}
                {plan.open_questions?.length > 0 && (
                  <div><p className="text-muted-foreground/60 font-semibold mb-1">待验证问题</p>
                    {plan.open_questions.map((q, i) => <p key={i} className="text-amber-300/60 mb-0.5">? {q}</p>)}</div>
                )}
                {dataFetched && (
                  <div className="space-y-2 pt-1">
                    <div className="flex gap-2 flex-wrap">
                      <span className={clsx('px-1.5 py-0.5 rounded border text-[10px]', dataFetched.market_data ? 'text-emerald-400 border-emerald-400/30 bg-emerald-400/5' : 'text-muted-foreground/40 border-border')}>行情数据</span>
                      <span className={clsx('px-1.5 py-0.5 rounded border text-[10px]', dataFetched.flow_signals > 0 ? 'text-emerald-400 border-emerald-400/30 bg-emerald-400/5' : 'text-muted-foreground/40 border-border')}>资金流 {dataFetched.flow_signals > 0 ? `×${dataFetched.flow_signals}` : '无'}</span>
                      <span className={clsx('px-1.5 py-0.5 rounded border text-[10px]', dataFetched.events > 0 ? 'text-emerald-400 border-emerald-400/30 bg-emerald-400/5' : 'text-muted-foreground/40 border-border')}>事件 {dataFetched.events > 0 ? `×${dataFetched.events}` : '无'}</span>
                      <span className={clsx('px-1.5 py-0.5 rounded border text-[10px]', dataFetched.claims > 0 ? 'text-emerald-400 border-emerald-400/30 bg-emerald-400/5' : 'text-muted-foreground/40 border-border')}>私域观点 ×{dataFetched.claims}</span>
                      {(dataFetched as any).web_results > 0 && (
                        <span className="px-1.5 py-0.5 rounded border text-[10px] text-sky-400 border-sky-400/30 bg-sky-400/5">Web搜索 ×{(dataFetched as any).web_results}</span>
                      )}
                    </div>
                    {/* 详细溯源清单 */}
                    {dataFetched.sources && (
                      <div className="space-y-1.5 border-t border-border/40 pt-1.5">
                        <p className="text-[10px] font-semibold text-muted-foreground/50 uppercase tracking-wider">数据溯源明细</p>
                        {/* 私域观点 */}
                        {dataFetched.sources.private_claims?.length > 0 && (
                          <div>
                            <p className="text-[10px] text-emerald-400/70 font-medium mb-0.5">私域知识库 ({dataFetched.sources.private_claims.length}条)</p>
                            {dataFetched.sources.private_claims.slice(0,3).map((c: any, i: number) => (
                              <p key={i} className="text-[10px] text-foreground/50 pl-2 border-l border-emerald-400/20 mb-0.5">
                                [{c.stance}] {c.summary} <span className="text-muted-foreground/40">(信任{(c.trust_score*100).toFixed(0)}%)</span>
                              </p>
                            ))}
                            {dataFetched.sources.private_claims.length > 3 && <p className="text-[10px] text-muted-foreground/30 pl-2">...还有 {dataFetched.sources.private_claims.length - 3} 条</p>}
                          </div>
                        )}
                        {/* 新闻事件 */}
                        {dataFetched.sources.news_events?.length > 0 && (
                          <div>
                            <p className="text-[10px] text-amber-400/70 font-medium mb-0.5">订阅新闻源 ({dataFetched.sources.news_events.length}条)</p>
                            {dataFetched.sources.news_events.slice(0,3).map((e: any, i: number) => (
                              <p key={i} className="text-[10px] text-foreground/50 pl-2 border-l border-amber-400/20 mb-0.5">
                                {e.title} <span className="text-muted-foreground/40">({e.source})</span>
                              </p>
                            ))}
                          </div>
                        )}
                        {/* Web搜索 */}
                        {dataFetched.sources.web_search?.length > 0 && (
                          <div>
                            <p className="text-[10px] text-sky-400/70 font-medium mb-0.5">Web搜索 ({dataFetched.sources.web_search.length}条)</p>
                            {dataFetched.sources.web_search.slice(0,3).map((w: any, i: number) => (
                              <p key={i} className="text-[10px] text-foreground/50 pl-2 border-l border-sky-400/20 mb-0.5">
                                {w.title} {w.url && <a href={w.url} target="_blank" rel="noreferrer" className="text-sky-400/50 hover:text-sky-400 underline ml-1">↗</a>}
                              </p>
                            ))}
                          </div>
                        )}
                        {/* 行情快照 */}
                        {dataFetched.sources.market_snapshot && (
                          <div>
                            <p className="text-[10px] text-violet-400/70 font-medium mb-0.5">实时行情快照</p>
                            <pre className="text-[10px] text-foreground/40 pl-2 border-l border-violet-400/20 font-mono leading-relaxed">{JSON.stringify(dataFetched.sources.market_snapshot, null, 2).slice(0, 300)}</pre>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
        <BriefingCard briefing={b} />
        <div className="flex items-center gap-2 mt-1 flex-wrap">
          <CopyButton text={copyText} />
          {modelUsed && <span className="text-[10px] font-mono text-muted-foreground/60 bg-muted/40 border border-border rounded px-1.5 py-0.5">{modelUsed}</span>}
          {durationMs && <span className="text-[10px] text-muted-foreground/50">{(durationMs/1000).toFixed(1)}s</span>}
          {costUsd != null && <span className="text-[10px] font-mono text-muted-foreground/40">~${costUsd.toFixed(5)}</span>}
          {modeInfo && <span className={clsx('text-[10px] border rounded px-1.5 py-0.5 font-medium', modeInfo.cls)}>{modeInfo.label}</span>}
          <button onClick={() => setShowRaw(v => !v)} className="text-[10px] text-muted-foreground/50 hover:text-muted-foreground flex items-center gap-0.5 transition-colors">
            <Code2 size={10}/> {showRaw ? '隐藏' : '开发者视图'}
          </button>
        </div>
        {showRaw && (
          <div className="mt-1 space-y-2 text-[11px] font-mono">
            <div className="bg-muted/30 border border-border rounded-lg p-2.5">
              <div className="flex items-center justify-between mb-1">
                <p className="text-muted-foreground/60 font-semibold">INPUT →</p>
                <CopyButton text={JSON.stringify({ question: b.topic, asset_scope: b.asset_scope, direction: b.direction }, null, 2)} />
              </div>
              <pre className="text-foreground/50 whitespace-pre-wrap leading-relaxed">{JSON.stringify({ question: b.topic, asset_scope: b.asset_scope, direction: b.direction }, null, 2)}</pre>
            </div>
            <div className="bg-muted/30 border border-border rounded-lg p-2.5">
              <div className="flex items-center justify-between mb-1">
                <p className="text-muted-foreground/60 font-semibold">OUTPUT ←</p>
                <CopyButton text={JSON.stringify({ direction: b.direction, setup_quality: b.setup_quality, recommended_action: b.recommended_action, executive_summary: b.executive_summary }, null, 2)} />
              </div>
              <pre className="text-foreground/50 whitespace-pre-wrap leading-relaxed">{JSON.stringify({ direction: b.direction, setup_quality: b.setup_quality, recommended_action: b.recommended_action, executive_summary: b.executive_summary?.slice(0,300), decision_guardrail: decisionGuardrail, quality_report: qualityReport, quality_filters: qualityFilters, llm_trace: llmTrace }, null, 2)}</pre>
            </div>
          </div>
        )}
        <AnswerReceipt data={{
          mode: 'briefing',
          dataHits: dataFetched ? {
            claims: dataFetched.claims ?? 0,
            market: !!dataFetched.market_data,
            events: dataFetched.events ?? 0,
            flow: dataFetched.flow_signals ?? 0,
            web: (dataFetched as any).web_results ?? 0,
            macro: !!(dataFetched as any).macro_data,
          } : undefined,
          dataTime: b.created_at ? new Date(b.created_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : undefined,
          isMock: !!b.is_mock,
          model: modelUsed,
          costUsd,
          durationMs: durationMs,
          evidenceSufficiency,
          insufficientEvidenceReason,
          warnings: Array.isArray((qualityReport as any)?.warnings)
            ? (qualityReport as any).warnings.map((item: any) => typeof item === 'string' ? item : item?.message).filter(Boolean)
            : undefined,
          processSteps: plan ? [
            `制定研究计划：${plan.key_hypotheses?.length ?? 0} 个核心假设`,
            '调取实时市场数据和相关新闻',
            '深度推演，生成结构化研究简报',
          ] : undefined,
        }} />
      </div>
    )
}

function DebateBubble({ msg, modeInfo }: { msg: ChatMsg; modeInfo?: { label: string; cls: string } }) {
  const [showRaw, setShowRaw] = useState(false)
  const { data: debateData } = useDebate(msg.payload.debate_id)
  const costUsd = messageCostUsd(msg.payload)
  return (
      <div>
        <DebateResultCard debateId={msg.payload.debate_id} />
        <div className="flex items-center gap-2 mt-1 flex-wrap">
          <CopyButton text={copyDebateText(debateData)} />
          {costUsd != null && <span className="text-[10px] font-mono text-muted-foreground/40">~${costUsd.toFixed(5)}</span>}
          {msg.payload?.duration_ms != null && <span className="text-[10px] text-muted-foreground/50">{(msg.payload.duration_ms/1000).toFixed(1)}s</span>}
          {modeInfo && <span className={clsx('text-[10px] border rounded px-1.5 py-0.5 font-medium', modeInfo.cls)}>{modeInfo.label}</span>}
          <button onClick={() => setShowRaw(v => !v)} className="text-[10px] text-muted-foreground/50 hover:text-muted-foreground flex items-center gap-0.5 transition-colors">
            <Code2 size={10}/> {showRaw ? '隐藏' : '开发者视图'}
          </button>
        </div>
        {showRaw && (
          <div className="mt-1 space-y-2 bg-muted/30 border border-border rounded-lg p-2.5 text-[11px] font-mono">
            <div className="flex items-center justify-between mb-1">
              <p className="text-muted-foreground/60 font-semibold">INPUT →</p>
              <CopyButton text={JSON.stringify({ debate_id: msg.payload.debate_id, question: debateData?.question, asset: debateData?.asset }, null, 2)} />
            </div>
            <pre className="text-foreground/50 whitespace-pre-wrap">{JSON.stringify({ debate_id: msg.payload.debate_id, question: debateData?.question, asset: debateData?.asset, num_rounds: debateData?.rounds ? Math.max(0, ...debateData.rounds.map((r: any) => r.round_number)) : undefined }, null, 2)}</pre>
            {debateData && (
              <>
                <div className="flex items-center justify-between mt-2 mb-1">
                  <p className="text-muted-foreground/60 font-semibold">OUTPUT ←</p>
                  <CopyButton text={copyDebateText(debateData)} />
                </div>
                <pre className="text-foreground/50 whitespace-pre-wrap">{JSON.stringify({ direction: debateData.final_direction, conviction: debateData.final_conviction, action: debateData.final_action, consensus: debateData.consensus_level, adjudication: debateData.adjudication ? { synthesis: debateData.adjudication.synthesis, baseline_score: debateData.adjudication.baseline_score, probability_score: debateData.adjudication.probability_score, risk_score: debateData.adjudication.risk_score, structure_score: debateData.adjudication.structure_score, risk_warnings: debateData.adjudication.risk_warnings, structure_warnings: debateData.adjudication.structure_warnings, hedging_advice: debateData.adjudication.hedging_advice } : null, rounds_count: debateData.rounds?.length, activated_seats: msg.payload?.activated_seats }, null, 2)}</pre>
              </>
            )}
          </div>
        )}
        <AnswerReceipt data={{
          mode: 'debate',
          isMock: debateData?.adjudication?.is_mock,
          costUsd,
          durationMs: msg.payload?.duration_ms != null ? Number(msg.payload.duration_ms) : debateData?.duration_ms ?? undefined,
          warnings: msg.payload?.risk_warnings ?? debateData?.adjudication?.risk_warnings,
          structureWarnings: msg.payload?.structure_warnings ?? debateData?.adjudication?.structure_warnings,
          evidenceWarnings: debateData?.adjudication?.evidence_quality_warnings,
          activatedSeats: msg.payload?.activated_seats,
          consensusLevel: debateData?.adjudication?.consensus_level,
        }} />
      </div>
    )
}

function FallbackBubble({ msg, modeInfo }: { msg: ChatMsg; modeInfo?: { label: string; cls: string } }) {
  return (
    <div className="min-w-0 max-w-full">
      <p className="text-sm text-foreground/90 bg-card border border-border rounded-xl px-4 py-3 leading-relaxed whitespace-pre-wrap break-words">{msg.content}</p>
      <div className="flex items-center gap-2 mt-1">
        <CopyButton text={msg.content} />
        {modeInfo && <span className={clsx('text-[10px] border rounded px-1.5 py-0.5 font-medium', modeInfo.cls)}>{modeInfo.label}</span>}
      </div>
    </div>
  )
}

// Pure dispatcher — NO hooks here, each branch rendered via sub-component with its own hooks
function MessageBubble({ msg, mode, onUpgrade }: {
  msg: ChatMsg
  mode: Mode
  onUpgrade?: (target: 'briefing' | 'debate') => void
}) {
  const msgMode = (msg.payload?.mode as Mode) || mode
  const modeInfo = MODE_LABEL[msgMode]
  const userModeInfo = msg.payload?.mode ? MODE_LABEL[msg.payload.mode as Mode] : undefined

  if (msg.role === 'user') {
    return (
      <div className="flex justify-end flex-col items-end gap-1 min-w-0">
        <div className="max-w-[min(42rem,100%)] bg-primary text-primary-foreground rounded-xl px-4 py-2 text-sm whitespace-pre-wrap break-words">{msg.content}</div>
        <div className="flex items-center gap-2">
          <CopyButton text={msg.content} />
          {userModeInfo && <span className={clsx('text-[10px] border rounded px-1.5 py-0.5 font-medium', userModeInfo.cls)}>{userModeInfo.label}</span>}
        </div>
      </div>
    )
  }

  // 若本条 assistant 消息来自智能路由，头部展示决策横幅
  const routing = msg.payload?.routing as RoutingDecision | undefined
  const banner = routing ? <RoutingBanner routing={routing} messageId={msg.id} onUpgrade={onUpgrade} /> : null

  if (msgMode === 'chat' || msg.payload?.mode === 'chat') {
    return <div className="min-w-0 max-w-full">{banner}<ChatBubble msg={msg} modeInfo={modeInfo} /></div>
  }
  if (msgMode === 'briefing' && msg.payload?.briefing) {
    return <div className="min-w-0 max-w-full">{banner}<BriefingBubble msg={msg} modeInfo={modeInfo} /></div>
  }
  if (msgMode === 'debate' && msg.payload?.debate_id) {
    return <div className="min-w-0 max-w-full">{banner}<DebateBubble msg={msg} modeInfo={modeInfo} /></div>
  }
  return <div className="min-w-0 max-w-full">{banner}<FallbackBubble msg={msg} modeInfo={modeInfo} /></div>
}


export default function ResearchChat() {
  const queryClient = useQueryClient()
  const [activeSessionId, setActiveSessionId] = useState<string|null>(null)
  // uiMode — 用户在选择器里的选择（可含 'auto'）
  const [uiMode, setUiMode] = useState<ChatMode>('auto')
  const [rounds, setRounds] = useState(2)
  const [input, setInput] = useState('')
  const [uploadItems, setUploadItems] = useState<UploadItem[]>([])
  const [isDragActive, setIsDragActive] = useState(false)
  const [uploadingFiles, setUploadingFiles] = useState(false)
  const [showSystemTestSessions, setShowSystemTestSessions] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const { data: sessions = [] } = useChatSessions()
  const { data: messages = [] } = useChatMessages(activeSessionId)
  const createSession = useCreateSession()
  const deleteSession = useDeleteSession()
  const addMessage = useAddMessage()
  const briefingMutation = useGenerateBriefing()
  const planResearchMutation = usePlanAndResearch()
  const debateMutation = useRunCommittee()
  const chatMutation = useSendChat()
  const autoRouteMutation = useAutoRoute()
  // 实际执行的 mode（供 UI hint / placeholder 使用）
  const mode: Mode = (uiMode === 'auto' ? 'chat' : uiMode)
  const { data: replaySnapshots = [] } = useReplaySnapshots()
  const [replayTarget, setReplayTarget] = useState<ReplaySnapshot | null>(null)
  const [researchPhase, setResearchPhase] = useState<'idle'|'planning'|'fetching'|'analyzing'|'done'>('idle')
  const isPending = mode === 'briefing' ? (briefingMutation.isPending || planResearchMutation.isPending) : mode === 'debate' ? debateMutation.isPending : chatMutation.isPending
  const [elapsedSec, setElapsedSec] = useState(0)
  const systemTestSessions = useMemo(() => sessions.filter(isSystemTestSession), [sessions])
  const visibleSessions = useMemo(
    () => showSystemTestSessions ? sessions : sessions.filter((s) => !isSystemTestSession(s)),
    [sessions, showSystemTestSessions],
  )
  useEffect(() => {
    if (!isPending) { setElapsedSec(0); return }
    setElapsedSec(0)
    const t = setInterval(() => setElapsedSec(s => s + 1), 1000)
    return () => clearInterval(t)
  }, [isPending])

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages])

  useEffect(() => {
    const activeSession = sessions.find((s) => s.id === activeSessionId)
    if (
      activeSessionId &&
      !showSystemTestSessions &&
      activeSession &&
      isSystemTestSession(activeSession) &&
      visibleSessions.length > 0
    ) {
      const s = visibleSessions[0]
      setActiveSessionId(s.id)
      if (s.mode && ['auto', 'chat', 'briefing', 'debate'].includes(s.mode)) {
        setUiMode(s.mode as ChatMode)
      }
      return
    }
    if (
      activeSessionId &&
      !showSystemTestSessions &&
      activeSession &&
      isSystemTestSession(activeSession) &&
      visibleSessions.length === 0
    ) {
      setActiveSessionId(null)
      return
    }

    if (!activeSessionId && visibleSessions.length > 0) {
      const s = visibleSessions[0]
      setActiveSessionId(s.id)
      // 跟随 session 持久化的 mode（auto / chat / briefing / debate）
      if (s.mode && ['auto', 'chat', 'briefing', 'debate'].includes(s.mode)) {
        setUiMode(s.mode as ChatMode)
      }
    }
  }, [activeSessionId, sessions, showSystemTestSessions, visibleSessions])

  const handleNewSession = async () => {
    // 将当前 uiMode 持久化到 session（含 'auto'）
    const s = await createSession.mutateAsync({ title: '新对话', mode: uiMode, asset: 'BTC' })
    setActiveSessionId(s.id)
  }

  const handleSelectSession = (s: ChatSession) => {
    setActiveSessionId(s.id)
    if (s.mode && ['auto', 'chat', 'briefing', 'debate'].includes(s.mode)) {
      setUiMode(s.mode as ChatMode)
    }
  }

  const refreshUploadStatuses = async (sourceIds: string[], jobIds: string[] = []) => {
    if (sourceIds.length === 0 && jobIds.length === 0) return
    try {
      const [statusResult, progressResult] = await Promise.allSettled([
        sourceIds.length ? uploadApi.getBatchStatus(sourceIds) : Promise.resolve([]),
        jobIds.length
          ? Promise.allSettled(jobIds.map((jobId) => uploadApi.getProgress(jobId))).then((settled) =>
              settled
                .filter((item): item is PromiseFulfilledResult<IngestionProgress> => item.status === 'fulfilled')
                .map((item) => item.value),
            )
          : Promise.resolve([]),
      ])
      const statuses = statusResult.status === 'fulfilled' ? statusResult.value : []
      const progressRows = progressResult.status === 'fulfilled' ? progressResult.value : []
      const byId = new Map(statuses.map((x: IngestionStatus) => [x.source_id, x]))
      const progressByJob = new Map(progressRows.map((x: IngestionProgress) => [x.job_id, x]))
      setUploadItems((items) => items.map((item) => {
        const next = item.sourceId ? byId.get(item.sourceId) : undefined
        const progress = item.jobId ? progressByJob.get(item.jobId) : undefined
        if (!next && !progress) return item
        const progressSummary = summarizeJobProgress(progress)
        const stage = next
          ? mapIngestionStatus(next.status)
          : progress?.status === 'completed'
            ? 'completed'
            : progress?.status === 'failed'
              ? 'failed'
              : item.stage
        const parseSummary =
          summarizeParseDiagnostics(next?.parse_diagnostics) ||
          summarizeParseDiagnostics(progress?.result_summary?.parse_diagnostics)
        return {
          ...item,
          fileName: next?.title || item.fileName,
          stage,
          statusText: next?.error || progressSummary?.text || next?.status || item.statusText,
          error: next?.error ?? item.error,
          wordCount: next?.word_count ?? item.wordCount,
          displaySize: next ? formatIngestionCount(next) ?? item.displaySize : item.displaySize,
          progressPct: progressSummary?.pct ?? item.progressPct,
          progressText: progressSummary?.text ?? item.progressText,
          stageElapsedText: progressSummary?.elapsed ?? item.stageElapsedText,
          parseSummary: parseSummary ?? item.parseSummary,
        }
      }))
    } catch {
      // Status polling is best-effort; upload receipt remains visible even if polling fails.
    }
  }

  useEffect(() => {
    const activeItems = uploadItems.filter((item) =>
      ACTIVE_UPLOAD_STATUSES.has(item.stage) || (item.jobId && item.progressPct !== 100),
    )
    const sourceIds = uniqueStrings(activeItems.map((item) => item.sourceId))
    const jobIds = uniqueStrings(activeItems.map((item) => item.jobId))
    if (sourceIds.length === 0 && jobIds.length === 0) return
    const timer = window.setInterval(() => { refreshUploadStatuses(sourceIds, jobIds) }, 3000)
    return () => window.clearInterval(timer)
  }, [uploadItems])

  const handleFiles = async (fileList: FileList | File[]) => {
    const files = Array.from(fileList).filter(Boolean)
    if (files.length === 0 || uploadingFiles) return

    let sid = activeSessionId
    if (!sid) {
      const s = await createSession.mutateAsync({ title: '资料投料', mode: uiMode, asset: 'BTC' })
      sid = s.id
      setActiveSessionId(s.id)
    }

    const pendingItems: UploadItem[] = files.map((file, idx) => ({
      id: `local-${Date.now()}-${idx}`,
      fileName: file.name,
      stage: 'uploading',
      statusText: formatFileSize(file.size),
    }))
    setUploadItems((items) => [...pendingItems, ...items].slice(0, 12))
    setUploadingFiles(true)

    try {
      const response = await uploadApi.upload(files, undefined, undefined, undefined, sid)
      const sourceIds = response.data.source_ids || []
      const receiptJobIds = response.data.job_ids || []
      const statusBySource = new Map((response.data.statuses || []).map((x) => [x.source_id, x]))
      const uploadedItems: UploadItem[] = sourceIds.length > 0
        ? sourceIds.map((sourceId, idx) => {
            const receiptStatus = statusBySource.get(sourceId)
            return {
              id: sourceId,
              sourceId,
              jobId: receiptJobIds[idx],
              fileName: files[idx]?.name || receiptStatus?.title || sourceId,
              stage: mapIngestionStatus(receiptStatus?.status || response.data.status),
              statusText: receiptStatus?.status || response.data.status || 'queued',
              displaySize: receiptStatus ? formatIngestionCount(receiptStatus) : undefined,
              parseSummary: summarizeParseDiagnostics(receiptStatus?.parse_diagnostics),
            }
          })
        : pendingItems.map((item) => ({ ...item, stage: 'queued', statusText: response.data.status || 'queued' }))

      setUploadItems((items) => [...uploadedItems, ...items.filter((item) => !pendingItems.some((p) => p.id === item.id))].slice(0, 12))

      if (response.data.receipt_message_id) {
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ['chat-messages', sid] }),
          queryClient.invalidateQueries({ queryKey: ['chat-sessions'] }),
        ])
      } else {
        await addMessage.mutateAsync({
          sessionId: sid,
          role: 'assistant',
          content: `已接收 ${files.length} 个文件，正在保存并解析。你可以继续提问，我会尽量将这些资料纳入后续分析。`,
          payload: {
            mode: 'upload_receipt',
            upload_receipt: response.data,
            scope: { mode: 'selected_sources', source_ids: sourceIds },
          },
        })
      }

      const queuedBatch = sourceIds.length
        ? await uploadApi.processSelectedSources(sourceIds, true, sid)
        : null
      const queuedJobBySource = new Map<string, string>()
      ;(queuedBatch?.data.jobs || []).forEach((item) => {
        const jobId = item.job_id
        const sourceId = item.source_id
        if (typeof sourceId === 'string' && typeof jobId === 'string' && jobId) queuedJobBySource.set(sourceId, jobId)
      })
      const jobIds = uniqueStrings(sourceIds.map((sourceId, idx) => queuedJobBySource.get(sourceId) || receiptJobIds[idx]))
      if (sourceIds.length > 0) {
        try {
          await recordSessionUploadReceipt(sid, {
            ...response.data,
            job_ids: jobIds,
            status: jobIds.length > 0 ? 'background_processing' : response.data.status,
            message: jobIds.length > 0 ? '后台解析已启动' : response.data.message,
          })
          await Promise.all([
            queryClient.invalidateQueries({ queryKey: ['chat-messages', sid] }),
            queryClient.invalidateQueries({ queryKey: ['chat-sessions'] }),
          ])
        } catch {
          // 资料和解析任务已经启动；回执补写失败不阻断上传流程。
        }
      }
      setUploadItems((items) => items.map((item) => sourceIds.includes(item.sourceId || '')
        ? {
            ...item,
            jobId: item.sourceId ? queuedJobBySource.get(item.sourceId) || item.jobId : item.jobId,
            stage: 'processing',
            statusText: '后台解析已启动',
            progressPct: item.progressPct ?? 1,
            progressText: '后台解析已启动',
          }
        : item))
      window.setTimeout(() => { refreshUploadStatuses(sourceIds, jobIds) }, 1200)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : '上传失败'
      setUploadItems((items) => items.map((item) => pendingItems.some((p) => p.id === item.id)
        ? { ...item, stage: 'failed', statusText: msg, error: msg }
        : item))
      await addMessage.mutateAsync({ sessionId: sid, role: 'assistant', content: msg, payload: { mode: 'chat' } })
    } finally {
      setUploadingFiles(false)
    }
  }

  const submit = async (q: string, forceRouteOverride?: Mode) => {
    if (!q.trim() || isPending) return
    if (!forceRouteOverride) setInput('')
    let sid = activeSessionId
    if (!sid) {
      const s = await createSession.mutateAsync({ title: q.slice(0, 40), mode, asset: 'BTC' })
      sid = s.id
      setActiveSessionId(s.id)
    }

    // === 三模式融合路由 ===
    // 统一调用 /auto（含 force_route），获取决策 + 去斜杠后的 cleaned_message
    // forceRouteOverride：一键升级/降级场景，跳过 uiMode 按钮状态
    let routing: RoutingDecision | null = null
    let effectiveMode: Mode = mode
    let messageToSend = q
    try {
      const forceRoute = forceRouteOverride ?? (uiMode === 'auto' ? undefined : uiMode)
      const res = await autoRouteMutation.mutateAsync({ sessionId: sid, message: q, forceRoute })
      routing = res.routing
      effectiveMode = res.routing.route as Mode
      messageToSend = res.cleaned_message || q
    } catch (e) {
      // 路由器失败 → 降级到用户选择的模式（或 chat）
      effectiveMode = forceRouteOverride ?? mode
    }

    await addMessage.mutateAsync({
      sessionId: sid,
      role: 'user',
      content: q,
      payload: { mode: effectiveMode, ui_mode: uiMode, routing: routing ?? undefined },
    })
    const history = messages.map(m => ({ role: m.role, content: m.content }))

    if (effectiveMode === 'chat') {
      try {
        await chatMutation.mutateAsync({ sessionId: sid, message: messageToSend, history })
        // chat endpoint stores both user+assistant messages server-side
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : 'Error'
        await addMessage.mutateAsync({ sessionId: sid, role: 'assistant', content: msg, payload: { mode: 'chat', routing: routing ?? undefined } })
      }
    } else if (effectiveMode === 'briefing') {
      try {
        // A1: 统一用 detectAsset（后端 asset_alias.py 同构）
        const detectedAsset = detectAsset(q, 'BTC')
        // 三阶段研究流程
        setResearchPhase('planning')
        const result = await planResearchMutation.mutateAsync({ question: messageToSend, asset_scope: [detectedAsset], committee_level: 'single', history })
        setResearchPhase('fetching')
        await new Promise(r => setTimeout(r, 300)) // 短暂显示 fetching 阶段
        setResearchPhase('analyzing')
        await new Promise(r => setTimeout(r, 300)) // 短暂显示 analyzing 阶段
        setResearchPhase('done')
        const b = result.briefing
        await addMessage.mutateAsync({
          sessionId: sid,
          role: 'assistant',
          content: b.executive_summary,
          payload: {
            briefing: b,
            mode: 'briefing',
            routing: routing ?? undefined,
            research_plan: result.plan,
            data_fetched: result.data_fetched,
            decision_guardrail: b.decision_guardrail,
            quality_report: b.quality_report,
            quality_filters: b.quality_filters,
            llm_trace: b.llm_trace,
            timing_trace: b.timing_trace,
            scope: b.scope,
            cost_usd: b.cost_usd,
            duration_ms: b.duration_ms,
          }
        })
      } catch (e: unknown) {
        setResearchPhase('idle')
        const msg = e instanceof Error ? e.message : 'Error'
        await addMessage.mutateAsync({ sessionId: sid, role: 'assistant', content: msg, payload: { mode: 'briefing', routing: routing ?? undefined } })
      } finally {
        setResearchPhase('idle')
      }
    } else {
      try {
        // A1: 统一用 detectAsset（后端 asset_alias.py 同构）
        const detectedAsset = detectAsset(q, 'BTC')
        const res = await debateMutation.mutateAsync({ question: messageToSend, asset: detectedAsset, num_rounds: rounds })
        await addMessage.mutateAsync({ sessionId: sid, role: 'assistant', content: '委员会辩论已完成', payload: { debate_id: res.debate_id, mode: 'debate', routing: routing ?? undefined, cost_usd: res.cost_usd, duration_ms: res.duration_ms, timing_trace: res.timing_trace, activated_seats: res.activated_seats, risk_warnings: res.risk_warnings, structure_warnings: res.structure_warnings } })
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : 'Error'
        await addMessage.mutateAsync({ sessionId: sid, role: 'assistant', content: msg, payload: { routing: routing ?? undefined } })
      }
    }
  }

  const modeGuide = {
    chat: {
      title: '快速研究',
      hints: ['资金费率目前说明了什么？', '美联储降息对黄金有什么影响？', 'BTC 当前最关键的风险是什么？'],
    },
    briefing: {
      title: '完整简报',
      hints: ['基于我的信息源，BTC 现在是否值得加仓？', 'ETH 未来 30 天的主要风险和机会是什么？', '黄金当前是否适合作为防御配置？'],
    },
    debate: {
      title: '审议增强',
      hints: ['BTC 未来 30 天能否突破前高？请做多空审议。', 'ETH 当前是否应该回避？请重点审查反方证据。', '这个交易计划有哪些容易被忽略的失效条件？'],
    },
  } as const

  const activeGuide = modeGuide[mode]

  return (
    <div className="flex h-full overflow-hidden">
      <aside className="flex w-60 shrink-0 flex-col border-r border-border bg-secondary/20">
        <div className="border-b border-border p-3">
          <div className="flex items-center justify-between">
            <div>
              <span className="section-label mb-1 block text-[10px]">Cases</span>
              <p className="font-serif text-sm font-semibold text-foreground">决策会话</p>
            </div>
            <button
              onClick={handleNewSession}
              className="flex h-7 w-7 items-center justify-center rounded-lg border border-border text-muted-foreground transition-colors hover:border-primary hover:text-primary"
              aria-label="新建会话"
            >
              <Plus size={14} />
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-auto py-2">
          {visibleSessions.length === 0 && (
            <p className="px-4 py-8 text-center text-xs leading-relaxed text-muted-foreground">暂无普通会话，点击右上角新建。</p>
          )}
          {systemTestSessions.length > 0 && (
            <button
              onClick={() => setShowSystemTestSessions((value) => !value)}
              className="mx-2 mb-2 flex w-[calc(100%-1rem)] items-center justify-between rounded-lg border border-border/70 px-2.5 py-2 text-[11px] text-muted-foreground transition-colors hover:border-primary/50 hover:text-foreground"
            >
              <span>{showSystemTestSessions ? '隐藏系统测试记录' : '显示系统测试记录'}</span>
              <span>{systemTestSessions.length} 条</span>
            </button>
          )}
          {visibleSessions.map((s) => (
            <div
              key={s.id}
              role="button"
              tabIndex={0}
              onClick={() => handleSelectSession(s)}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') handleSelectSession(s) }}
              className={clsx(
                'group mx-2 flex w-[calc(100%-1rem)] cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2 text-left transition-colors',
                activeSessionId === s.id ? 'bg-primary/15 text-foreground' : 'text-muted-foreground hover:bg-secondary hover:text-foreground',
              )}
            >
              <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-primary/70" />
              <span className="min-w-0 flex-1 truncate text-xs">{s.title}</span>
              <button
                onClick={(e) => { e.stopPropagation(); deleteSession.mutate(s.id) }}
                className="opacity-0 transition-opacity hover:text-red-400 group-hover:opacity-100"
                aria-label="删除会话"
              >
                <Trash2 size={11} />
              </button>
            </div>
          ))}
        </div>

        {replaySnapshots.length > 0 && (
          <div className="shrink-0 border-t border-border px-3 py-3">
            <p className="mb-2 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.16em] text-muted-foreground/70">
              <History size={10} />
              Replay
            </p>
            <div className="space-y-1">
              {replaySnapshots.slice(0, 4).map((snap) => (
                <button
                  key={snap.id}
                  onClick={() => setReplayTarget(snap)}
                  className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[11px] text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
                >
                  <span className={clsx('h-1.5 w-1.5 shrink-0 rounded-full',
                    snap.final_direction === 'bullish' ? 'bg-emerald-400' : snap.final_direction === 'bearish' ? 'bg-red-400' : 'bg-slate-400',
                  )} />
                  <span className="min-w-0 flex-1 truncate">{snap.asset} / {snap.question.slice(0, 18)}</span>
                  <span className="shrink-0 text-[10px] text-muted-foreground/50">{snap.outcome_direction ? '已验' : '待验'}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </aside>

      <section className="flex min-w-0 flex-1 flex-col">
        <header className="shrink-0 border-b border-border px-5 py-4">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
            <div className="min-w-0">
              <span className="section-label mb-2 block">Decision Case</span>
              <h1 className="font-serif text-2xl font-semibold tracking-tight text-foreground">旧问答页</h1>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <span className="tag">{activeGuide.title}</span>
              <span className="tag">{rounds} 轮审议</span>
            </div>
          </div>
        </header>

        <div className="flex min-h-0 flex-1">
          <main className="flex min-w-0 flex-1 flex-col">
            <div className="flex-1 space-y-4 overflow-y-auto overflow-x-hidden p-5">
              {!activeSessionId && (
                <div className="flex h-full flex-col items-center justify-center gap-4 text-center">
                  <div>
                    <h2 className="font-serif text-xl font-semibold text-foreground">从一个明确问题开始</h2>
                    <p className="mt-2 max-w-lg text-sm leading-relaxed text-muted-foreground">
                      建议把问题写成“资产 + 时间窗口 + 决策动作 + 约束条件”，系统会围绕证据和风险展开。
                    </p>
                  </div>
                  <button onClick={handleNewSession} className="btn-primary inline-flex items-center gap-2">
                    <Plus size={15} />
                    新建决策会话
                  </button>
                </div>
              )}

              {activeSessionId && messages.length === 0 && (
                <div className="flex h-full flex-col items-center justify-center gap-4 text-center">
                  <div>
                    <h2 className="font-serif text-xl font-semibold text-foreground">{activeGuide.title}</h2>
                  </div>
                  <div className="flex max-w-2xl flex-wrap justify-center gap-2">
                    {activeGuide.hints.map((q) => (
                      <button
                        key={q}
                        onClick={() => setInput(q)}
                        className="rounded-lg border border-border px-3 py-2 text-xs text-muted-foreground transition-colors hover:border-primary/50 hover:text-foreground"
                      >
                        {q}
                      </button>
                    ))}
                  </div>
                </div>
              )}
          {messages.map((m, idx) => {
            // 对 assistant 消息：找前一条 user 消息作为「升级重跑」的 query
            const prevUser = m.role === 'assistant'
              ? [...messages.slice(0, idx)].reverse().find(x => x.role === 'user')
              : undefined
            const onUpgradeHandler = prevUser
              ? (target: 'briefing' | 'debate') => { submit(prevUser.content, target) }
              : undefined
            return <MessageBubble key={m.id} msg={m} mode={mode} onUpgrade={onUpgradeHandler} />
          })}
          {isPending && (
            <div className="flex flex-col gap-1.5 bg-card border border-border rounded-xl p-3">
              <div className="flex items-center gap-2 text-muted-foreground text-sm">
                <Loader2 size={14} className="animate-spin text-primary" />
                <span className="text-foreground/80">
                  {mode === 'briefing'
                    ? researchPhase === 'planning' ? '阶段一：AI 分析问题，制定研究计划…'
                      : researchPhase === 'fetching' ? '阶段二：调取实时市场数据…'
                      : researchPhase === 'analyzing' ? '阶段三：深度推演，生成研究简报…'
                      : '正在生成研究简报…'
                    : mode === 'debate' ? '委员会辩论进行中…' : '正在思考…'}
                </span>
                {mode === 'briefing' && researchPhase !== 'idle' && (
                  <div className="flex items-center gap-1 mt-1">
                    {(['planning', 'fetching', 'analyzing'] as const).map((ph, i) => (
                      <div key={ph} className={clsx(
                        'h-1 rounded-full transition-all duration-500',
                        researchPhase === ph ? 'w-8 bg-primary' :
                        (['planning', 'fetching', 'analyzing'].indexOf(researchPhase) > i) ? 'w-4 bg-primary/50' : 'w-4 bg-border'
                      )} />
                    ))}
                  </div>
                )}
                <span className="ml-auto font-mono text-xs text-primary">{elapsedSec}s</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="flex-1 h-1 bg-border rounded-full overflow-hidden">
                  <div
                    className="h-full bg-primary/60 rounded-full transition-all"
                    style={{ width: `${Math.min(100, (elapsedSec / (mode === 'briefing' ? 90 : mode === 'debate' ? 180 : 15)) * 100)}%` }}
                  />
                </div>
                <span className="text-[10px] text-muted-foreground/60 shrink-0">
                  {mode === 'briefing' ? '通常 30–90 秒' : mode === 'debate' ? '通常 60–180 秒' : '通常 5–15 秒'}
                </span>
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>
        <div className="border-t border-border px-4 pt-2 shrink-0 flex items-center gap-2">
          <div className="flex-1 min-w-0">
            <RadarBar compact />
          </div>
          <RefreshDataButton compact className="shrink-0" />
        </div>
          {uploadItems.length > 0 && (
            <div className="border-t border-border bg-secondary/10 px-4 py-2">
              <div className="mb-1 flex items-center justify-between gap-3 text-[10px] uppercase tracking-[0.16em] text-muted-foreground/70">
                <span className="inline-flex items-center gap-1.5"><UploadCloud size={11} /> 资料处理</span>
                <a href="/knowledge" className="text-primary/80 hover:text-primary">查看资料库</a>
              </div>
              <div className="flex gap-2 overflow-x-auto pb-1">
                {uploadItems.slice(0, 6).map((item) => (
                  <div key={item.id} className="min-w-[180px] rounded-lg border border-border bg-card/70 px-2.5 py-2">
                    <div className="mb-1 flex items-center gap-1.5 text-xs text-foreground">
                      {item.stage === 'failed' ? <XCircle size={13} className="text-red-400" /> : item.stage === 'completed' ? <CheckCircle2 size={13} className="text-emerald-400" /> : <FileText size={13} className="text-primary" />}
                      <span className="truncate">{item.fileName}</span>
                    </div>
                    <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
                      <span className={clsx('h-1.5 w-1.5 rounded-full', item.stage === 'failed' ? 'bg-red-400' : item.stage === 'completed' ? 'bg-emerald-400' : 'bg-primary animate-pulse')} />
                      <span>{UPLOAD_STAGE_LABEL[item.stage]}</span>
                      {(item.displaySize || item.wordCount != null)
                        ? <span className="ml-auto font-mono">{item.displaySize || item.wordCount}</span>
                        : <span className="ml-auto truncate font-mono">{item.progressPct != null ? `${item.progressPct}%` : item.statusText}</span>}
                    </div>
                    {item.progressPct != null && item.stage !== 'completed' && (
                      <div className="mt-1 h-1 overflow-hidden rounded-full bg-border">
                        <div
                          className="h-full rounded-full bg-primary/70 transition-all"
                          style={{ width: `${Math.max(1, Math.min(100, item.progressPct))}%` }}
                        />
                      </div>
                    )}
                    {item.progressText && <p className="mt-1 truncate text-[10px] text-muted-foreground/70">{item.progressText}</p>}
                    {item.parseSummary?.text && <p className="mt-1 truncate text-[10px] text-muted-foreground/70">{item.parseSummary.text}</p>}
                    {item.error && <p className="mt-1 truncate text-[10px] text-red-300">{item.error}</p>}
                  </div>
                ))}
              </div>
            </div>
          )}
        <form
          onSubmit={e => {e.preventDefault();submit(input)}}
          onDragOver={(e) => { e.preventDefault(); setIsDragActive(true) }}
          onDragLeave={() => setIsDragActive(false)}
          onDrop={(e) => { e.preventDefault(); setIsDragActive(false); handleFiles(e.dataTransfer.files) }}
          className={clsx('border-t border-border p-3 shrink-0 flex flex-col gap-2 transition-colors', isDragActive && 'bg-primary/10 ring-1 ring-primary/40')}
        >
          <input value={input} onChange={e => setInput(e.target.value)} disabled={isPending}
            placeholder={uiMode === 'auto'
              ? '拖入资料或输入问题，系统自动选择快速/研究/辩论模式…'
              : mode === 'chat' ? '拖入资料或直接提问金融市场问题…'
              : mode === 'briefing' ? '输入研究问题，系统自动识别资产…'
              : '输入辩论议题…'}
            className="w-full bg-secondary border border-border rounded-lg px-4 py-2.5 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary disabled:opacity-50" />
          <div className="flex items-center gap-2">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept=".txt,.md,.pdf,.docx,.jpg,.jpeg,.png,.webp"
              className="hidden"
              onChange={(e) => { if (e.target.files) handleFiles(e.target.files); e.currentTarget.value = '' }}
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploadingFiles}
              className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-xs text-muted-foreground transition-colors hover:border-primary/50 hover:text-foreground disabled:opacity-50"
            >
              {uploadingFiles ? <Loader2 size={14} className="animate-spin" /> : <Paperclip size={14} />}
              资料
            </button>
            <ModeSelector mode={uiMode} onChange={setUiMode} disabled={isPending} />
            {uiMode === 'debate' && (
              <div className="flex items-center gap-1 text-[10px] text-muted-foreground">
                <span>轮数</span>
                {[1,2,3].map(n => (
                  <button
                    key={n}
                    type="button"
                    onClick={() => setRounds(n)}
                    className={clsx(
                      'px-1.5 py-0.5 rounded border transition-colors',
                      rounds === n
                        ? 'bg-primary/20 border-primary/40 text-primary'
                        : 'border-border hover:border-primary/30'
                    )}
                  >{n}</button>
                ))}
              </div>
            )}
            <span className="text-[10px] text-muted-foreground/50 ml-auto">拖入资料 / Enter 发送</span>
            <button type="submit" disabled={!input.trim()||isPending} className="bg-primary text-primary-foreground rounded-lg px-4 py-2 disabled:opacity-40 hover:opacity-90 transition-opacity">
              {isPending ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
            </button>
          </div>
        </form>
          </main>
        </div>
      </section>
      {replayTarget && <ReplayDrawer snapshot={replayTarget} onClose={() => setReplayTarget(null)} />}
    </div>
  )
}
