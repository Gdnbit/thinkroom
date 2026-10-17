import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  RefreshCw,
  Loader2,
  CalendarDays,
  GitBranch,
  Network,
  Clock3,
  X,
  Maximize2,
  Minimize2,
  Upload,
  BookOpen,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react'
import { currentDecisionSession } from '@/data/decisionSession'
import { rememberSessionJobIds, rememberSessionSourceIds, useCurrentDecisionSession } from '@/services/decisionSession'
import { uploadApi, type IngestionProgress, type IngestionStatus } from '@/services/upload'

const API = '/api/v1/knowledge'
const GRAPH_LAYOUT_KEY = 'kb_graph_layout_v2'

type BoardTab = 'calendar' | 'timeline' | 'taxonomy' | 'graph'

type TimelineEvent = {
  id: string
  type: string
  title: string
  ts: string
  date: string
  status: string
  asset_scope: string[]
  theme_scope: string[]
  meta: Record<string, unknown>
}

type TaxonomyResp = {
  taxonomy: Record<string, { label: string; children: Record<string, string> }>
  mapped_items: Array<{
    claim_id: string
    summary: string
    category: string
    category_label: string
    subcategory: string
    subcategory_label: string
    reason: string
    stance: string
    status: string
    asset_scope: string[]
    theme_scope: string[]
    created_at?: string
  }>
}

type GraphNode = {
  id: string
  type: string
  label: string
  status: string
  stance: string
  asset_scope: string[]
  theme_scope: string[]
  created_at?: string
  merge_count?: number
  x?: number
  y?: number
  z?: number
  fx?: number
  fy?: number
  fz?: number
}

type GraphLink = {
  source: string
  target: string
  weight: number
  relation: string
}

type GraphResp = {
  nodes: GraphNode[]
  links: GraphLink[]
}

function TabButton({ active, label, onClick, icon }: { active: boolean; label: string; onClick: () => void; icon: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-1.5 px-3 py-2 text-xs font-mono border-b-2 -mb-px transition-colors ${
        active ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'
      }`}
    >
      {icon}
      {label}
    </button>
  )
}

function toDateKey(d: Date) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function eventTypeLabel(t: string) {
  if (t === 'claim_created') return '观点'
  if (t === 'source_created') return '来源'
  if (t === 'briefing_generated') return '简报'
  if (t === 'setup_evaluated') return '机会'
  if (t === 'claim_expiry') return '到期'
  return t
}

function eventTypeClass(t: string) {
  if (t === 'claim_created') return 'bg-sky-500/20 text-sky-300 border-sky-400/50'
  if (t === 'source_created') return 'bg-emerald-500/20 text-emerald-300 border-emerald-400/50'
  if (t === 'briefing_generated') return 'bg-violet-500/20 text-violet-300 border-violet-400/50'
  if (t === 'setup_evaluated') return 'bg-amber-500/20 text-amber-300 border-amber-400/50'
  if (t === 'claim_expiry') return 'bg-rose-500/20 text-rose-300 border-rose-400/50'
  return 'bg-secondary text-muted-foreground border-border'
}

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))

function summarizeUploadProgress(progress: IngestionProgress[], statuses: IngestionStatus[]) {
  const total = Math.max(statuses.length, progress.length)
  const done = statuses.filter(s => s.status === 'completed').length
  const failed = statuses.filter(s => s.status === 'failed').length
  const active = progress.find(p => p.status === 'running')
  const pctValues = progress.map(p => Number(p.progress_pct || 0)).filter(Number.isFinite)
  const pct = pctValues.length ? Math.max(...pctValues) : (total ? Math.round((done / total) * 100) : 0)
  const elapsedMs = active?.current_stage_elapsed_ms ?? active?.elapsed_ms
  const elapsed = typeof elapsedMs === 'number' && elapsedMs > 0 ? `，当前环节 ${Math.round(elapsedMs / 1000)}s` : ''

  if (total > 0 && done + failed >= total) {
    return {
      complete: true,
      type: failed > 0 ? 'error' as const : 'done' as const,
      msg: failed > 0 ? `完成 ${done}，失败 ${failed}` : `${done} 份资料全部解析完成！`,
    }
  }

  return {
    complete: false,
    type: 'uploading' as const,
    msg: `${active?.current_stage_label || active?.message || 'AI 解析中'}…已完成 ${done}/${Math.max(total, 1)}，进度 ${Math.min(99, pct)}%${elapsed}${failed ? `，失败 ${failed}` : ''}`,
  }
}

const EVENT_DOT_COLORS: Record<string, string> = {
  source_created: 'bg-emerald-400',
  claim_created: 'bg-sky-400',
  briefing_generated: 'bg-violet-400',
  setup_evaluated: 'bg-amber-400',
  claim_expiry: 'bg-rose-400',
}

function CalendarPanel({ events, onSelect }: { events: TimelineEvent[]; onSelect: (e: TimelineEvent) => void }) {
  const today = new Date()
  const [year, setYear] = useState(today.getFullYear())
  const [month, setMonth] = useState(today.getMonth())
  const [selectedDate, setSelectedDate] = useState(toDateKey(today))
  const [typeFilter, setTypeFilter] = useState('')

  const byDate = useMemo(() => {
    const m = new Map<string, TimelineEvent[]>()
    events.forEach((e) => {
      const dk = e.date || toDateKey(new Date(e.ts))
      const hitType = typeFilter ? e.type === typeFilter : true
      if (!hitType) return
      if (!m.has(dk)) m.set(dk, [])
      m.get(dk)!.push(e)
    })
    return m
  }, [events, typeFilter])

  const first = new Date(year, month, 1)
  const last = new Date(year, month + 1, 0)
  const startWeekday = (first.getDay() + 6) % 7
  const daysInMonth = last.getDate()

  const cells: Array<{ day?: number; key: string }> = []
  for (let i = 0; i < startWeekday; i += 1) cells.push({ key: `e-${i}` })
  for (let d = 1; d <= daysInMonth; d += 1) cells.push({ day: d, key: `d-${d}` })

  const switchMonth = (delta: number) => {
    const next = new Date(year, month + delta, 1)
    setYear(next.getFullYear())
    setMonth(next.getMonth())
  }

  const selectedList = byDate.get(selectedDate) || []

  return (
    <div className="rounded-xl border border-border bg-card overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border">
        <div className="flex items-center gap-2">
          <button className="w-7 h-7 rounded-lg bg-secondary hover:bg-border flex items-center justify-center transition-colors" onClick={() => switchMonth(-1)}><ChevronLeft size={14} /></button>
          <div className="text-sm font-semibold min-w-[100px] text-center">{year} 年 {month + 1} 月</div>
          <button className="w-7 h-7 rounded-lg bg-secondary hover:bg-border flex items-center justify-center transition-colors" onClick={() => switchMonth(1)}><ChevronRight size={14} /></button>
          <button className="px-2.5 py-1 rounded-md bg-primary/10 text-primary text-[11px] font-medium hover:bg-primary/20 transition-colors" onClick={() => { setYear(today.getFullYear()); setMonth(today.getMonth()); setSelectedDate(toDateKey(today)) }}>今天</button>
        </div>
        <select className="bg-secondary border border-border rounded px-2 py-1 text-[11px]" value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
          <option value="">全部类型</option>
          <option value="source_created">来源</option>
          <option value="claim_created">观点</option>
          <option value="briefing_generated">简报</option>
          <option value="setup_evaluated">机会</option>
          <option value="claim_expiry">到期</option>
        </select>
      </div>

      {/* Weekday headers */}
      <div className="grid grid-cols-7 border-b border-border/60">
        {['一', '二', '三', '四', '五', '六', '日'].map((x) => (
          <div className="text-center py-1.5 text-[11px] font-medium text-muted-foreground" key={x}>{x}</div>
        ))}
      </div>

      {/* Compact calendar grid */}
      <div className="grid grid-cols-7">
        {cells.map((c) => {
          if (!c.day) return <div key={c.key} className="h-11 border-b border-r border-border/30" />
          const dk = `${year}-${String(month + 1).padStart(2, '0')}-${String(c.day).padStart(2, '0')}`
          const list = byDate.get(dk) || []
          const selected = dk === selectedDate
          const isToday = dk === toDateKey(today)
          const typesPresent = new Set(list.map((e) => e.type))

          return (
            <button
              key={c.key}
              onClick={() => setSelectedDate(dk)}
              className={`h-11 border-b border-r border-border/30 flex flex-col items-center justify-center gap-0.5 transition-colors relative ${
                selected ? 'bg-primary/12' : 'hover:bg-secondary/60'
              }`}
            >
              <span className={`text-[13px] leading-none ${
                isToday
                  ? 'w-6 h-6 rounded-full bg-primary text-primary-foreground flex items-center justify-center font-semibold text-[12px]'
                  : selected
                    ? 'text-foreground font-medium'
                    : 'text-foreground/80'
              }`}>{c.day}</span>
              {list.length > 0 && (
                <div className="flex gap-[3px]">
                  {Object.entries(EVENT_DOT_COLORS).map(([type, color]) =>
                    typesPresent.has(type) ? <span key={type} className={`w-1 h-1 rounded-full ${color}`} /> : null
                  )}
                </div>
              )}
              {list.length > 3 && (
                <span className="absolute top-0.5 right-1 text-[9px] text-muted-foreground">{list.length}</span>
              )}
            </button>
          )
        })}
      </div>

      {/* Legend */}
      <div className="flex items-center gap-3 px-4 py-1.5 border-t border-border text-[10px] text-muted-foreground">
        <span className="inline-flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />来源</span>
        <span className="inline-flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-sky-400" />观点</span>
        <span className="inline-flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-violet-400" />简报</span>
        <span className="inline-flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-amber-400" />机会</span>
        <span className="inline-flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-rose-400" />到期</span>
        <span className="ml-auto text-foreground/50">共 {events.length} 事件</span>
      </div>

      {/* Selected date event list */}
      <div className="border-t border-border px-4 py-3 bg-background/50">
        <div className="flex items-center gap-2 mb-2">
          <span className="text-xs font-semibold text-foreground">{selectedDate}</span>
          <span className="text-[11px] text-muted-foreground">{selectedList.length} 个事件</span>
        </div>
        {selectedList.length === 0 ? (
          <div className="text-xs text-muted-foreground py-1">当日无事件</div>
        ) : (
          <div className="space-y-1.5 max-h-48 overflow-auto">
            {selectedList.map((e) => (
              <button key={e.id} onClick={() => onSelect(e)} className="w-full text-left rounded-lg border border-border p-2 hover:bg-secondary/40 transition-colors">
                <div className="flex items-center gap-2 mb-0.5">
                  <span className={`px-1.5 py-0.5 rounded text-[10px] ${eventTypeClass(e.type)}`}>{eventTypeLabel(e.type)}</span>
                  <span className="text-[10px] text-muted-foreground">{new Date(e.ts).toLocaleTimeString()}</span>
                </div>
                <div className="text-xs text-foreground leading-snug">{e.title}</div>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function TimelinePanel({
  events,
  onSelect,
  focusTs,
  nodeMode,
  onToggleNodeMode,
  highlightIds,
}: {
  events: TimelineEvent[]
  onSelect: (e: TimelineEvent) => void
  focusTs?: number | null
  nodeMode: boolean
  onToggleNodeMode: () => void
  highlightIds: Set<string>
}) {
  const [cursorTs, setCursorTs] = useState(Date.now())
  const [dayPerScreen, setDayPerScreen] = useState(40)
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  const boxRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (focusTs) setCursorTs(focusTs)
  }, [focusTs])

  const sorted = useMemo(() => [...events].sort((a, b) => new Date(a.ts).getTime() - new Date(b.ts).getTime()), [events])
  const rangeMs = dayPerScreen * 24 * 3600_000
  const start = cursorTs - rangeMs / 2
  const end = cursorTs + rangeMs / 2

  // Deduplicate: merge events with same title on same date
  const visible = useMemo(() => {
    const raw = sorted.filter((e) => {
      const t = new Date(e.ts).getTime()
      return t >= start && t <= end
    })
    const seen = new Map<string, TimelineEvent>()
    const counts = new Map<string, number>()
    raw.forEach((e) => {
      const dk = e.date || toDateKey(new Date(e.ts))
      const normTitle = (e.title || '').replace(/\s+/g, '').slice(0, 40).toLowerCase()
      const key = `${dk}||${normTitle}`
      if (!seen.has(key)) {
        seen.set(key, e)
        counts.set(key, 1)
      } else {
        counts.set(key, (counts.get(key) || 1) + 1)
      }
    })
    return Array.from(seen.values()).map((e) => {
      const dk = e.date || toDateKey(new Date(e.ts))
      const normTitle = (e.title || '').replace(/\s+/g, '').slice(0, 40).toLowerCase()
      const key = `${dk}||${normTitle}`
      const mc = counts.get(key) || 1
      return mc > 1 ? { ...e, title: `${e.title} (×${mc})` } : e
    })
  }, [sorted, start, end])

  const byDate = useMemo(() => {
    const m = new Map<string, TimelineEvent[]>()
    visible.forEach((e) => {
      const dk = e.date || toDateKey(new Date(e.ts))
      if (!m.has(dk)) m.set(dk, [])
      m.get(dk)!.push(e)
    })
    return m
  }, [visible])


  const tickUnit = useMemo(() => {
    if (dayPerScreen <= 45) return 'day'
    if (dayPerScreen <= 540) return 'month'
    if (dayPerScreen <= 3650) return 'year'
    return 'decade'
  }, [dayPerScreen])

  const ticks = useMemo(() => {
    const out: Array<{ ts: number; label: string }> = []
    const s = new Date(start)
    const e = new Date(end)

    const pushTick = (d: Date, label: string) => {
      const ts = d.getTime()
      if (ts >= start && ts <= end) out.push({ ts, label })
    }

    if (tickUnit === 'day') {
      const cur = new Date(s.getFullYear(), s.getMonth(), s.getDate())
      while (cur <= e) {
        const label = cur.getDate() === 1
          ? `${cur.getFullYear()}/${cur.getMonth() + 1}月`
          : `${cur.getMonth() + 1}/${cur.getDate()}`
        pushTick(new Date(cur), label)
        cur.setDate(cur.getDate() + 1)
      }
    } else if (tickUnit === 'month') {
      const cur = new Date(s.getFullYear(), s.getMonth(), 1)
      while (cur <= e) {
        pushTick(new Date(cur), `${cur.getFullYear()}-${String(cur.getMonth() + 1).padStart(2, '0')}`)
        cur.setMonth(cur.getMonth() + 1)
      }
    } else if (tickUnit === 'year') {
      const cur = new Date(s.getFullYear(), 0, 1)
      while (cur <= e) {
        pushTick(new Date(cur), `${cur.getFullYear()}`)
        cur.setFullYear(cur.getFullYear() + 1)
      }
    } else {
      const base = Math.floor(s.getFullYear() / 10) * 10
      const cur = new Date(base, 0, 1)
      while (cur <= e) {
        pushTick(new Date(cur), `${cur.getFullYear()}s`)
        cur.setFullYear(cur.getFullYear() + 10)
      }
    }

    return out
  }, [start, end, tickUnit])

  useEffect(() => {
    const el = boxRef.current
    if (!el) return
    const wheel = (ev: WheelEvent) => {
      ev.preventDefault()
      ev.stopPropagation()
      if (ev.ctrlKey) {
        setDayPerScreen((d) => Math.max(1, Math.min(36500, ev.deltaY < 0 ? d * 0.82 : d * 1.22)))
      } else {
        const move = Math.max(6 * 3600_000, rangeMs * 0.08)
        setCursorTs((t) => t + (ev.deltaY > 0 ? move : -move))
      }
    }
    el.addEventListener('wheel', wheel, { passive: false })
    return () => el.removeEventListener('wheel', wheel)
  }, [rangeMs])

  const compact = dayPerScreen > 90
  const semi = dayPerScreen > 45 && dayPerScreen <= 90

  const selectedEvents = selectedDate ? byDate.get(selectedDate) || [] : []
  const selectedX = useMemo(() => {
    if (!selectedDate || !selectedEvents.length) return null
    const t = new Date(selectedEvents[0].ts).getTime()
    return ((t - start) / (end - start)) * 100
  }, [selectedDate, selectedEvents, start, end])

  const cursorDate = new Date(cursorTs)
  const ctxYear = cursorDate.getFullYear()
  const ctxMonth = cursorDate.getMonth() + 1

  return (
    <div className="space-y-3" ref={boxRef}>
      <div className="flex items-center gap-2 text-xs font-mono text-muted-foreground">
        时间轴（滚轮平移；Ctrl+滚轮缩放）
        <button
          className={`ml-2 px-2 py-0.5 rounded border text-[11px] transition-colors ${nodeMode ? 'bg-fuchsia-500/20 text-fuchsia-300 border-fuchsia-400/50' : 'bg-secondary border-border hover:bg-border'}`}
          onClick={onToggleNodeMode}
        >
          {nodeMode ? '节点模式 ON' : '节点模式'}
        </button>
        <span className="ml-auto">视窗 {Math.round(dayPerScreen)} 天 · {tickUnit}</span>
      </div>

      <div className="relative w-full h-[64vh] min-h-[460px] overflow-hidden">
        <div className="absolute left-4 right-4 top-1/2 h-[2px] bg-border" />
        <div className="absolute right-4 top-1/2 -translate-y-1/2 w-0 h-0 border-t-[6px] border-t-transparent border-b-[6px] border-b-transparent border-l-[10px] border-l-border" />

        {ticks.map((t, idx) => {
          const x = ((t.ts - start) / (end - start)) * 100
          if (x < 0 || x > 100) return null
          const down = idx % 2 === 0
          const isMonthBoundary = t.label.includes('月')
          return (
            <div
              key={`${t.ts}-${t.label}`}
              className="absolute transition-all duration-300"
              style={{ left: `${x}%`, top: '50%', transform: down ? 'translateX(-50%)' : 'translate(-50%, -100%)' }}
            >
              {down ? (
                <>
                  <div className={`w-[1px] ${isMonthBoundary ? 'h-5 bg-primary/40' : 'h-3 bg-border'} mx-auto`} />
                  <div className={`mt-0.5 font-mono whitespace-nowrap text-center ${isMonthBoundary ? 'text-[11px] text-primary font-semibold' : 'text-[10px] text-muted-foreground'}`}>{t.label}</div>
                </>
              ) : (
                <>
                  <div className={`mb-0.5 font-mono whitespace-nowrap text-center ${isMonthBoundary ? 'text-[11px] text-primary font-semibold' : 'text-[10px] text-muted-foreground'}`}>{t.label}</div>
                  <div className={`w-[1px] ${isMonthBoundary ? 'h-5 bg-primary/40' : 'h-3 bg-border'} mx-auto`} />
                </>
              )}
            </div>
          )
        })}

        {/* Floating year/month context */}
        <div className="absolute left-3 top-2 px-2.5 py-1.5 rounded-md bg-card/90 border border-border shadow-sm backdrop-blur-sm z-30 pointer-events-none">
          <div className="text-[9px] text-muted-foreground/70 leading-none mb-0.5">当前视窗</div>
          <div className="text-sm font-semibold text-foreground leading-tight">{ctxYear} 年 {ctxMonth} 月</div>
        </div>

        {(() => {
          const dateEntries = Array.from(byDate.entries())
          // Sort by timestamp for consistent visual order
          dateEntries.sort((a, b) => new Date(a[1][0].ts).getTime() - new Date(b[1][0].ts).getTime())
          // Row positions: 2 rows above axis, 2 rows below — spread cards across 4 lanes
          const rowTops = ['4%', '20%', '60%', '76%']
          // Track occupied x-ranges per row to prevent overlap
          const occupied: number[][] = [[], [], [], []]
          const cardW = semi ? 9 : 12 // approximate card width in %

          return dateEntries.map(([date, list]) => {
            const t = new Date(list[0].ts).getTime()
            const x = ((t - start) / (end - start)) * 100
            if (x < 0 || x > 100) return null

            const hasHighlight = list.some((e) => highlightIds.has(e.id))
            const markerClass = hasHighlight
              ? 'bg-fuchsia-400 border-fuchsia-200 ring-2 ring-fuchsia-300/60'
              : selectedDate === date
                ? 'bg-amber-400 border-amber-300'
                : 'bg-primary border-primary/40'

            if (compact || nodeMode) {
              return (
                <button
                  key={date}
                  className="absolute transition-all duration-300 z-20"
                  style={{ left: `${x}%`, top: '50%', transform: 'translate(-50%, -50%)' }}
                  onClick={() => setSelectedDate(date)}
                >
                  <div className={`w-3 h-3 rounded-full border shadow-sm ${markerClass}`} />
                </button>
              )
            }

            // Find best row: prefer alternating, fallback to least-occupied
            let bestRow = 0
            let minConflict = Infinity
            for (let r = 0; r < 4; r++) {
              const conflicts = occupied[r].filter((ox) => Math.abs(ox - x) < cardW).length
              if (conflicts < minConflict) { minConflict = conflicts; bestRow = r }
              if (conflicts === 0) break
            }
            occupied[bestRow].push(x)

            // If all rows are too crowded, collapse to dot
            if (minConflict >= 2) {
              return (
                <button
                  key={date}
                  className="absolute transition-all duration-300 z-20"
                  style={{ left: `${x}%`, top: '50%', transform: 'translate(-50%, -50%)' }}
                  onClick={() => setSelectedDate(date)}
                >
                  <div className={`w-2.5 h-2.5 rounded-full border shadow-sm ${markerClass}`} title={`${date}: ${list.length} 条`} />
                </button>
              )
            }

            const isAbove = bestRow < 2
            return (
              <button
                key={date}
                onClick={() => setSelectedDate(date)}
                className="absolute text-left transition-all duration-300 z-10"
                style={{ left: `${x}%`, top: rowTops[bestRow], transform: 'translateX(-50%)' }}
              >
                <div className={`rounded border shadow-sm ${semi ? 'w-[130px] p-1.5' : 'w-[170px] p-2'} ${hasHighlight ? 'border-fuchsia-300 bg-fuchsia-500/10' : 'border-border bg-card'}`}>
                  <div className={`text-[10px] ${hasHighlight ? 'text-fuchsia-300' : 'text-primary'}`}>{date} · {list.length}</div>
                  <div className="text-[10px] text-foreground truncate">{list[0].title}</div>
                </div>
                <div className={`absolute left-1/2 -translate-x-1/2 w-[1px] ${hasHighlight ? 'bg-fuchsia-300' : 'bg-border'}`} style={isAbove ? { top: '100%', height: 24 } : { bottom: '100%', height: 24 }} />
              </button>
            )
          })
        })()}

        {selectedX != null && selectedX >= 0 && selectedX <= 100 && (
          <div className="absolute top-0 bottom-0 pointer-events-none" style={{ left: `${selectedX}%`, transform: 'translateX(-50%)' }}>
            <div className="w-[2px] h-full bg-amber-300/70" />
          </div>
        )}
      </div>

      {/* 底部弹出事件窗 */}
      <div className={`fixed inset-0 z-40 ${selectedDate ? '' : 'pointer-events-none'}`}>
        <div
          className={`absolute inset-0 bg-black/35 transition-opacity duration-300 ${selectedDate ? 'opacity-100' : 'opacity-0'}`}
          onClick={() => setSelectedDate(null)}
        />
        <div
          className={`absolute left-0 right-0 bottom-0 bg-card border-t border-border rounded-t-2xl shadow-2xl transition-transform duration-300 ${
            selectedDate ? 'translate-y-0' : 'translate-y-full'
          }`}
          style={{ maxHeight: '52vh' }}
        >
          <div className="flex items-center justify-between px-4 py-3 border-b border-border">
            <div className="text-sm font-mono">{selectedDate || ''} 事件列表（{selectedEvents.length}）</div>
            <button className="p-1 rounded bg-secondary" onClick={() => setSelectedDate(null)}><X size={14} /></button>
          </div>
          <div className="p-3 space-y-2 overflow-auto" style={{ maxHeight: 'calc(52vh - 48px)' }}>
            {selectedEvents.map((e) => (
              <button key={e.id} onClick={() => onSelect(e)} className="w-full text-left rounded border border-border p-2 hover:bg-secondary/40">
                <div className="text-[10px] text-primary">{new Date(e.ts).toLocaleString()}</div>
                <div className="text-xs text-foreground">{e.title}</div>
                <div className="text-[10px] text-muted-foreground">{e.type} · {e.status}</div>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

function signalText(stance: string, status: string) {
  if (status === 'invalidated') return '信号已被反证'
  if (status === 'expired') return '信号已过期'
  if (stance === 'bullish') return '偏多'
  if (stance === 'bearish') return '偏空'
  return '中性观察'
}

function TaxonomyPanel({ data, onSelectClaim }: { data: TaxonomyResp | null; onSelectClaim: (claimId: string) => void }) {
  const [expandedCats, setExpandedCats] = useState<Set<string>>(new Set())
  const [selectedPath, setSelectedPath] = useState<{ cat: string; sub: string } | null>(null)

  const categories = useMemo(() => {
    if (!data) return []
    return Object.entries(data.taxonomy).map(([k, v]) => ({ key: k, label: v.label, children: v.children }))
  }, [data])

  const countMap = useMemo(() => {
    if (!data) return { cat: new Map<string, number>(), sub: new Map<string, number>() }
    const catM = new Map<string, number>()
    const subM = new Map<string, number>()
    data.mapped_items.forEach((item) => {
      catM.set(item.category, (catM.get(item.category) || 0) + 1)
      const subKey = `${item.category}:${item.subcategory}`
      subM.set(subKey, (subM.get(subKey) || 0) + 1)
    })
    return { cat: catM, sub: subM }
  }, [data])

  useEffect(() => {
    if (categories.length && !expandedCats.size) {
      setExpandedCats(new Set([categories[0].key]))
      const firstSubs = Object.keys(categories[0].children)
      if (firstSubs.length) setSelectedPath({ cat: categories[0].key, sub: firstSubs[0] })
    }
  }, [categories])

  if (!data) return null

  const toggleCat = (key: string) => {
    setExpandedCats((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const leaves = selectedPath
    ? data.mapped_items.filter((x) => x.category === selectedPath.cat && x.subcategory === selectedPath.sub)
    : []

  const totalItems = data.mapped_items.length

  return (
    <div className="rounded-xl border border-border bg-card overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-border">
        <GitBranch size={13} className="text-primary" />
        <span className="text-xs font-semibold text-foreground">分类体系</span>
        <span className="text-[10px] text-muted-foreground">共 {totalItems} 条目</span>
      </div>
      <div className="grid grid-cols-5 min-h-[360px]">
        {/* Tree panel */}
        <div className="col-span-2 border-r border-border p-3 overflow-auto max-h-[520px]">
          {categories.map((cat) => {
            const expanded = expandedCats.has(cat.key)
            const catCount = countMap.cat.get(cat.key) || 0
            const subs = Object.entries(cat.children)
            return (
              <div key={cat.key} className="mb-0.5">
                <button
                  onClick={() => toggleCat(cat.key)}
                  className="w-full flex items-center gap-1.5 px-2 py-1.5 rounded-md text-xs hover:bg-secondary/60 transition-colors group"
                >
                  <span className={`text-[10px] transition-transform ${expanded ? 'rotate-90' : ''}`}>&#9654;</span>
                  <span className="font-medium text-foreground truncate">{cat.label}</span>
                  <span className="ml-auto text-[10px] text-muted-foreground bg-secondary rounded px-1.5 py-0.5">{catCount}</span>
                </button>
                {expanded && (
                  <div className="ml-3 pl-2.5 border-l border-border/60">
                    {subs.map(([subKey, subLabel]) => {
                      const subCount = countMap.sub.get(`${cat.key}:${subKey}`) || 0
                      const isActive = selectedPath?.cat === cat.key && selectedPath?.sub === subKey
                      return (
                        <button
                          key={subKey}
                          onClick={() => setSelectedPath({ cat: cat.key, sub: subKey })}
                          className={`w-full flex items-center gap-1.5 px-2 py-1 rounded text-[11px] transition-colors ${
                            isActive ? 'bg-primary/15 text-foreground' : 'text-muted-foreground hover:bg-secondary/50 hover:text-foreground'
                          }`}
                        >
                          <span className={`w-1.5 h-1.5 rounded-full ${isActive ? 'bg-primary' : 'bg-border'}`} />
                          <span className="truncate">{subLabel}</span>
                          {subCount > 0 && <span className="ml-auto text-[9px] text-muted-foreground">{subCount}</span>}
                        </button>
                      )
                    })}
                  </div>
                )}
              </div>
            )
          })}
        </div>
        {/* Detail panel */}
        <div className="col-span-3 p-3 overflow-auto max-h-[520px]">
          {selectedPath ? (
            <>
              <div className="flex items-center gap-2 mb-3">
                <span className="text-xs font-semibold text-foreground">
                  {categories.find((c) => c.key === selectedPath.cat)?.label}
                </span>
                <span className="text-muted-foreground text-[10px]">/</span>
                <span className="text-xs text-primary">
                  {categories.find((c) => c.key === selectedPath.cat)?.children[selectedPath.sub] || selectedPath.sub}
                </span>
                <span className="text-[10px] text-muted-foreground ml-auto">{leaves.length} 条</span>
              </div>
              {leaves.length === 0 ? (
                <div className="text-xs text-muted-foreground py-8 text-center">该分类下暂无条目</div>
              ) : (
                <div className="space-y-1.5">
                  {leaves.map((x) => (
                    <button key={`${x.claim_id}-${x.reason}`} onClick={() => onSelectClaim(x.claim_id)} className="w-full text-left rounded-lg border border-border p-2.5 hover:bg-secondary/40 transition-colors">
                      <div className="text-xs text-foreground leading-snug mb-1">{x.summary}</div>
                      <div className="flex items-center gap-2">
                        <span className="text-[10px] text-muted-foreground">{x.created_at ? new Date(x.created_at).toLocaleString() : '未知时间'}</span>
                        <span className={`px-1.5 py-0.5 rounded text-[9px] ${
                          x.stance === 'bullish' ? 'bg-emerald-500/15 text-emerald-400' :
                          x.stance === 'bearish' ? 'bg-rose-500/15 text-rose-400' :
                          'bg-secondary text-muted-foreground'
                        }`}>{signalText(x.stance, x.status)}</span>
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </>
          ) : (
            <div className="text-xs text-muted-foreground py-8 text-center">请在左侧选择分类</div>
          )}
        </div>
      </div>
    </div>
  )
}

function GraphPanel({
  data,
  selectedNodeIds,
  onSelectNode,
  relationFilter,
  onRelationFilter,
  uploadHighlight,
  onUploadHighlight,
  uploadOptions,
  selectedUploadId,
  onSelectUploadId,
}: {
  data: GraphResp | null
  selectedNodeIds: Set<string>
  onSelectNode: (id: string, append: boolean) => void
  relationFilter: string
  onRelationFilter: (v: string) => void
  uploadHighlight: string
  onUploadHighlight: (v: string) => void
  uploadOptions: Array<{ id: string; label: string }>
  selectedUploadId: string
  onSelectUploadId: (id: string) => void
}) {
  const [ForceGraph3DComp, setForceGraph3DComp] = useState<any>(null)
  const [threeModule, setThreeModule] = useState<any>(null)
  const [hoveredNodeDisplay, setHoveredNodeDisplay] = useState<any>(null)
  const [fullscreen, setFullscreen] = useState(false)
  const [clusterMode, setClusterMode] = useState<'theme' | 'asset'>('theme')
  // edgeDensity removed — always show all links
  const [activeCluster, setActiveCluster] = useState<number>(-1)
  const [clusterSearch, setClusterSearch] = useState('')
  const [clusterPage, setClusterPage] = useState(0)
  const fgRef = useRef<any>(null)
  const nodeColorRef = useRef<(n: any) => string>(() => '#93c5fd')
  const degreeMapRef = useRef<Map<string, number>>(new Map())
  const graphDataRef = useRef<any>({ nodes: [], links: [] })
  const hoveredNodeRef = useRef<any>(null)
  const hoverTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const selectedIdsRef = useRef<Set<string>>(new Set())
  const relatedRef = useRef<Set<string> | null>(null)
  const initialFitDone = useRef(false)
  const clustersRef = useRef<Array<{ ids: Set<string>; name: string }>>([])

  useEffect(() => {
    let alive = true
    Promise.all([
      import('react-force-graph-3d'),
      import('three'),
    ]).then(([fg, three]) => {
      if (!alive) return
      setForceGraph3DComp(() => fg.default)
      setThreeModule(three)
    }).catch((err) => console.error('[KnowledgeGraph] load error:', err))
    return () => { alive = false }
  }, [])

  const glowTexture = useMemo(() => {
    if (!threeModule) return null
    const size = 128
    const canvas = document.createElement('canvas')
    canvas.width = size
    canvas.height = size
    const ctx = canvas.getContext('2d')!
    const c = size / 2
    const grad = ctx.createRadialGradient(c, c, 0, c, c, c)
    grad.addColorStop(0, 'rgba(255,255,255,1)')
    grad.addColorStop(0.08, 'rgba(255,255,255,0.85)')
    grad.addColorStop(0.22, 'rgba(255,255,255,0.35)')
    grad.addColorStop(0.45, 'rgba(255,255,255,0.08)')
    grad.addColorStop(1, 'rgba(255,255,255,0)')
    ctx.fillStyle = grad
    ctx.fillRect(0, 0, size, size)
    const tex = new threeModule.CanvasTexture(canvas)
    tex.needsUpdate = true
    return tex
  }, [threeModule])

  const relationTypes = useMemo(() => {
    const s = new Set<string>()
    ;(data?.links || []).forEach((l) => s.add((l.relation || '关联').split(':')[0] || '关联'))
    return Array.from(s)
  }, [data])

  const graphData = useMemo(() => {
    const nodes = (data?.nodes || []).slice(0, 200).map((n) => ({ ...n }))

    // Spherical layout — fibonacci lattice for even distribution on a sphere
    const groupOf = (nd: any) => clusterMode === 'theme'
      ? ((nd.theme_scope || [])[0] || '未分组主题')
      : ((nd.asset_scope || [])[0] || '未分组标的')
    const groupNames = [...new Set(nodes.map(groupOf))]
    const totalGroups = Math.max(1, groupNames.length)
    const goldenAngle = Math.PI * (3 - Math.sqrt(5))
    const sphereR = 260
    const spreadByGroup = new Map<string, { x: number; y: number; z: number }>()
    groupNames.forEach((g, idx) => {
      const y = 1 - (idx / totalGroups) * 2  // -1 to 1
      const r = Math.sqrt(Math.max(0, 1 - y * y))
      const theta = goldenAngle * idx
      spreadByGroup.set(g, {
        x: Math.cos(theta) * r * sphereR,
        y: y * sphereR,
        z: Math.sin(theta) * r * sphereR,
      })
    })
    nodes.forEach((n: any) => {
      const base = spreadByGroup.get(groupOf(n))!
      if (typeof n.x !== 'number') n.x = base.x + (Math.random() - 0.5) * 70
      if (typeof n.y !== 'number') n.y = base.y + (Math.random() - 0.5) * 70
      if (typeof n.z !== 'number') n.z = base.z + (Math.random() - 0.5) * 70
    })
    try {
      const saved = JSON.parse(localStorage.getItem(GRAPH_LAYOUT_KEY) || '{}')
      nodes.forEach((n: any) => {
        const p = saved[n.id]
        if (p) Object.assign(n, p)
      })
    } catch {}

    const idSet = new Set(nodes.map((n) => String(n.id)))
    // force-graph mutates link objects: source/target may be string or object with .id
    const sid = (v: any) => String(v?.id ?? v)
    const links = (data?.links || [])
      .filter((l) => idSet.has(sid(l.source)) && idSet.has(sid(l.target)))
      .filter((l) => (relationFilter ? (l.relation || '').startsWith(relationFilter) : true))

    return { nodes, links }
  }, [data, relationFilter, clusterMode])

  // Reset fit flag when graph data changes so new layouts get an initial zoomToFit
  useEffect(() => {
    initialFitDone.current = false
    // Unpin all nodes so the new simulation can run freely
    ;(graphData.nodes as any[]).forEach((n: any) => { n.fx = undefined; n.fy = undefined; n.fz = undefined })
  }, [graphData])

  const adjacency = useMemo(() => {
    const m = new Map<string, Set<string>>()
    graphData.nodes.forEach((n: any) => m.set(String(n.id), new Set()))
    graphData.links.forEach((l: any) => {
      const s = String(l.source?.id ?? l.source)
      const t = String(l.target?.id ?? l.target)
      if (!m.has(s)) m.set(s, new Set())
      if (!m.has(t)) m.set(t, new Set())
      m.get(s)!.add(t)
      m.get(t)!.add(s)
    })
    return m
  }, [graphData])

  const degreeMap = useMemo(() => {
    const m = new Map<string, number>()
    graphData.nodes.forEach((n: any) => m.set(String(n.id), 0))
    graphData.links.forEach((l: any) => {
      const s = String(l.source?.id ?? l.source)
      const t = String(l.target?.id ?? l.target)
      m.set(s, (m.get(s) || 0) + 1)
      m.set(t, (m.get(t) || 0) + 1)
    })
    return m
  }, [graphData])

  const related = useMemo(() => {
    if (!selectedNodeIds.size) return null
    const r = new Set<string>()
    selectedNodeIds.forEach((sid) => {
      r.add(sid)
      ;(adjacency.get(sid) || new Set<string>()).forEach((x) => r.add(x))
    })
    return r
  }, [adjacency, selectedNodeIds])

  const nodeColor = (n: any) => {
    const text = `${n.label || ''} ${(n.theme_scope || []).join(' ')} ${(n.asset_scope || []).join(' ')}`.toLowerCase()
    const uploadHit = uploadHighlight && text.includes(uploadHighlight.toLowerCase())
    const byType = (() => {
      if (n.type === 'event' || text.includes('event')) return '#38bdf8'
      if (n.type === 'policy' || text.includes('policy') || text.includes('政策')) return '#a78bfa'
      if (n.type === 'outcome' || text.includes('result') || text.includes('结果')) return '#f59e0b'
      if ((n.asset_scope || []).length > 0) return '#34d399'
      return '#93c5fd'
    })()
    const base = uploadHit ? '#f472b6' : byType
    if (!related) return base
    if (!related.has(String(n.id))) return '#334155'
    if (selectedNodeIds.has(String(n.id))) return '#fbbf24'
    return base
  }

  nodeColorRef.current = nodeColor
  degreeMapRef.current = degreeMap
  graphDataRef.current = graphData
  selectedIdsRef.current = selectedNodeIds
  relatedRef.current = related

  const makeNodeObj = useCallback((n: any) => {
    if (!threeModule || !glowTexture) return undefined
    const T = threeModule
    const color = nodeColorRef.current(n)
    const deg = degreeMapRef.current.get(String(n.id)) || 0
    const mc = Math.min(5, (n.merge_count || 1) - 1)  // 0-4 bonus
    const baseSize = Math.max(7, Math.min(28, 7 + deg * 1.5 + mc * 1.2))

    const group = new T.Group()
    const coreMat = new T.SpriteMaterial({
      map: glowTexture, color: new T.Color(color),
      transparent: true, opacity: 1.0,
      blending: T.AdditiveBlending, depthWrite: false,
    })
    const core = new T.Sprite(coreMat)
    core.scale.set(baseSize * 0.5, baseSize * 0.5, 1)
    group.add(core)

    const haloMat = new T.SpriteMaterial({
      map: glowTexture, color: new T.Color(color),
      transparent: true, opacity: 0.5,
      blending: T.AdditiveBlending, depthWrite: false,
    })
    const halo = new T.Sprite(haloMat)
    halo.scale.set(baseSize * 1.2, baseSize * 1.2, 1)
    group.add(halo)

    // Outer glow for far-zoom visibility
    const outerMat = new T.SpriteMaterial({
      map: glowTexture, color: new T.Color(color),
      transparent: true, opacity: 0.15,
      blending: T.AdditiveBlending, depthWrite: false,
    })
    const outer = new T.Sprite(outerMat)
    outer.scale.set(baseSize * 2.2, baseSize * 2.2, 1)
    group.add(outer)

    return group
  }, [threeModule, glowTexture])

  const updateNodeVisuals = useCallback(() => {
    if (!threeModule || !glowTexture) return
    const T = threeModule
    const gd = graphDataRef.current
    const colorFn = nodeColorRef.current
    const dm = degreeMapRef.current
    const selIds = selectedIdsRef.current
    gd.nodes.forEach((n: any) => {
      const obj = n.__threeObj
      if (!obj?.children) return
      const color = colorFn(n)
      const deg = dm.get(String(n.id)) || 0
      const mc = Math.min(5, ((n as any).merge_count || 1) - 1)
      const baseSize = Math.max(7, Math.min(28, 7 + deg * 1.5 + mc * 1.2))
      const sel = selIds.has(String(n.id))
      const size = sel ? baseSize * 1.2 : baseSize

      if (obj.children[0]?.material) {
        obj.children[0].material.color.set(new T.Color(color))
        obj.children[0].scale.set(size * 0.5, size * 0.5, 1)
      }
      if (obj.children[1]?.material) {
        obj.children[1].material.color.set(new T.Color(color))
        obj.children[1].scale.set(size * 1.2, size * 1.2, 1)
      }
      if (obj.children[2]?.material && !sel) {
        obj.children[2].material.color.set(new T.Color(color))
        obj.children[2].scale.set(size * 2.2, size * 2.2, 1)
      }
      if (sel && obj.children.length < 4) {
        const ringMat = new T.SpriteMaterial({
          map: glowTexture, color: new T.Color('#fbbf24'),
          transparent: true, opacity: 0.22,
          blending: T.AdditiveBlending, depthWrite: false,
        })
        const ring = new T.Sprite(ringMat)
        ring.scale.set(size * 2.5, size * 2.5, 1)
        obj.add(ring)
      } else if (!sel && obj.children.length > 3) {
        const extra = obj.children[3]
        obj.remove(extra)
        if (extra.material) extra.material.dispose()
      }
    })
  }, [threeModule, glowTexture])

  useEffect(() => {
    updateNodeVisuals()
  }, [selectedNodeIds, uploadHighlight, updateNodeVisuals])

  const onSelectNodeRef = useRef(onSelectNode)
  onSelectNodeRef.current = onSelectNode

  const stableNodeLabel = useCallback((n: any) => n.label || n.id, [])
  const stableNodeColor = useCallback((n: any) => nodeColorRef.current(n), [])
  const stableNodeVal = useCallback((n: any) => {
    const deg = degreeMapRef.current.get(String(n.id)) || 0
    return Math.max(2, Math.min(10, 2 + deg * 0.8))
  }, [])
  const _linkSel = (l: any) => {
    const ids = selectedIdsRef.current
    if (!ids.size) return 'none'
    const s = String(l.source?.id ?? l.source)
    const t = String(l.target?.id ?? l.target)
    return (ids.has(s) || ids.has(t)) ? 'active' : 'dim'
  }
  const stableLinkWidth = useCallback((l: any) => {
    const sel = _linkSel(l)
    const w = Math.min(1, (l.weight || 0.3))
    if (sel === 'none') return 0.4 + w * 0.6           // default: 0.4 ~ 1.0
    if (sel === 'active') return 2.0 + w * 2.5          // selected: 2.0 ~ 4.5  (very thick)
    return 0.1                                           // dim: nearly invisible
  }, [])
  const stableLinkColor = useCallback((l: any) => {
    const sel = _linkSel(l)
    if (sel === 'none') return '#5a7ba8'
    if (sel === 'active') return '#93c5fd'
    return '#1e293b'
  }, [])
  // Per-link material with individual opacity — linkOpacity is a global constant,
  // so we must use linkMaterial accessor for per-link transparency
  const stableLinkMaterial = useCallback((l: any) => {
    if (!threeModule) return undefined
    const T = threeModule
    const sel = _linkSel(l)
    let color: string, opacity: number
    if (sel === 'active') {
      color = '#93c5fd'; opacity = 0.92
    } else if (sel === 'dim') {
      color = '#1e293b'; opacity = 0.04
    } else {
      color = '#5a7ba8'; opacity = 0.2
    }
    return new T.LineBasicMaterial({
      color: new T.Color(color),
      transparent: true,
      opacity,
      depthWrite: false,
    })
  }, [threeModule])
  const stableLinkParticles = useCallback((l: any) => {
    return _linkSel(l) === 'active' ? 4 : 0
  }, [])
  const stableLinkParticleWidth = useCallback((l: any) => {
    return _linkSel(l) === 'active' ? 2.5 : 0
  }, [])
  const stableLinkLabel = useCallback((l: any) => l.relation || '关联', [])

  // Force ForceGraph to re-evaluate link appearance after selection change
  // IMPORTANT: wrap in new arrow functions so force-graph sees a new reference
  // and actually re-computes (it skips update if the function ref is unchanged)
  useEffect(() => {
    if (!fgRef.current) return
    try {
      fgRef.current.linkColor((l: any) => stableLinkColor(l))
      fgRef.current.linkWidth((l: any) => stableLinkWidth(l))
      fgRef.current.linkMaterial((l: any) => stableLinkMaterial(l))
      fgRef.current.linkDirectionalParticles((l: any) => stableLinkParticles(l))
      fgRef.current.linkDirectionalParticleWidth((l: any) => stableLinkParticleWidth(l))
    } catch {}
  }, [selectedNodeIds, stableLinkColor, stableLinkWidth, stableLinkMaterial, stableLinkParticles, stableLinkParticleWidth])

  const stableOnNodeClick = useCallback((n: any, ev: MouseEvent) => {
    onSelectNodeRef.current(String(n.id), !!ev.ctrlKey)
  }, [])
  const stableOnNodeHover = useCallback((n: any) => {
    hoveredNodeRef.current = n || null
    if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current)
    hoverTimerRef.current = setTimeout(() => {
      setHoveredNodeDisplay(hoveredNodeRef.current)
    }, 80)
  }, [])
  const stableOnEngineStop = useCallback(() => {
    const gd = graphDataRef.current
    try {
      const save: Record<string, { x: number; y: number; z: number }> = {}
      ;(gd.nodes as any[]).forEach((n: any) => {
        if (typeof n.x === 'number' && typeof n.y === 'number' && typeof n.z === 'number') save[String(n.id)] = { x: n.x, y: n.y, z: n.z }
      })
      localStorage.setItem(GRAPH_LAYOUT_KEY, JSON.stringify(save))
    } catch {}
    // Pin all nodes at their current positions to prevent reheat drift
    ;(gd.nodes as any[]).forEach((n: any) => {
      if (typeof n.x === 'number') { n.fx = n.x; n.fy = n.y; n.fz = n.z }
    })
    if (!initialFitDone.current && fgRef.current && gd.nodes.length > 0) {
      initialFitDone.current = true
      // Focus the largest cluster on initial load for a clean default view
      const cls = clustersRef.current
      if (cls.length > 0) {
        const biggest = cls[0]
        const cNodes = gd.nodes.filter((nd: any) => biggest.ids.has(String(nd.id)))
        if (cNodes.length > 0) {
          let cx = 0, cy = 0, cz = 0
          cNodes.forEach((nd: any) => { cx += (nd.x || 0); cy += (nd.y || 0); cz += (nd.z || 0) })
          cx /= cNodes.length; cy /= cNodes.length; cz /= cNodes.length
          let maxR = 0
          cNodes.forEach((nd: any) => {
            const dx = (nd.x || 0) - cx, dy = (nd.y || 0) - cy, dz = (nd.z || 0) - cz
            maxR = Math.max(maxR, Math.sqrt(dx * dx + dy * dy + dz * dz))
          })
          const dist = Math.max(250, maxR * 2.8)
          fgRef.current.cameraPosition(
            { x: cx, y: cy, z: cz + dist },
            { x: cx, y: cy, z: cz },
            1000
          )
          // Set orbit center to cluster centroid so rotation is centered
          const ctl = fgRef.current.controls?.()
          if (ctl?.target) ctl.target.set(cx, cy, cz)
          return
        }
      }
      fgRef.current.zoomToFit(600, 40)
    }
  }, [])
  const stableOnNodeDragEnd = useCallback((n: any) => {
    // Pin dragged node at its final position
    n.fx = n.x; n.fy = n.y; n.fz = n.z
  }, [])
  const d3Config = useMemo(() => ({ strength: -420 }), [])
  const emptyArr = useMemo(() => [] as any[], [])

  const clusters = useMemo(() => {
    const visited = new Set<string>()
    const components: Array<{ ids: Set<string>; name: string }> = []
    graphData.nodes.forEach((n: any) => {
      const nid = String(n.id)
      if (visited.has(nid)) return
      const queue = [nid]
      const comp = new Set<string>()
      while (queue.length) {
        const cur = queue.shift()!
        if (visited.has(cur)) continue
        visited.add(cur)
        comp.add(cur)
        ;(adjacency.get(cur) || new Set<string>()).forEach((nb) => {
          if (!visited.has(nb)) queue.push(nb)
        })
      }
      if (comp.size === 0) return
      const compNodes = graphData.nodes.filter((nd: any) => comp.has(String(nd.id)))
      const themes = new Map<string, number>()
      const assets = new Map<string, number>()
      compNodes.forEach((nd: any) => {
        ;(nd.theme_scope || []).forEach((t: string) => themes.set(t, (themes.get(t) || 0) + 1))
        ;(nd.asset_scope || []).forEach((a: string) => assets.set(a, (assets.get(a) || 0) + 1))
      })
      const topTheme = [...themes.entries()].sort((a, b) => b[1] - a[1])[0]?.[0]
      const topAsset = [...assets.entries()].sort((a, b) => b[1] - a[1])[0]?.[0]
      const label = topTheme || topAsset || `星团 ${components.length + 1}`
      components.push({ ids: comp, name: `${label} (${comp.size})` })
    })
    components.sort((a, b) => b.ids.size - a.ids.size)
    return components
  }, [graphData, adjacency])
  clustersRef.current = clusters

  useEffect(() => { setActiveCluster(-1) }, [clusters.length])

  useEffect(() => {
    if (!fgRef.current) return
    const controls = fgRef.current.controls?.()
    if (controls) {
      controls.minDistance = 80
      controls.maxDistance = 1800
      controls.enableDamping = true
      controls.dampingFactor = 0.12
      controls.rotateSpeed = 0.6
      controls.zoomSpeed = 0.8
      // Gentle idle rotation for a living feel
      controls.autoRotate = true
      controls.autoRotateSpeed = 0.3
    }
    // Spherical d3 forces: radial pull keeps nodes on a sphere shell
    const fg = fgRef.current
    import('d3-force-3d').then((d3: any) => {
      if (!fg || !d3?.forceRadial) return
      fg.d3Force('radial', d3.forceRadial(220).strength(0.05))
      // Weaken center force to avoid collapsing
      const center = fg.d3Force('center')
      if (center?.strength) center.strength(0.03)
      // Soften charge slightly
      const charge = fg.d3Force('charge')
      if (charge?.strength) charge.strength(-350)
    }).catch(() => {
      // d3-force-3d not available as separate module — use API directly
      if (!fg) return
      try {
        const charge = fg.d3Force('charge')
        if (charge?.strength) charge.strength(-350)
        const center = fg.d3Force('center')
        if (center?.strength) center.strength(0.03)
      } catch {}
    })
  }, [ForceGraph3DComp])

  const focusCluster = useCallback((idx: number) => {
    // Toggle: click again to deselect
    if (activeCluster === idx) {
      setActiveCluster(-1)
      if (fgRef.current) {
        fgRef.current.zoomToFit(400)
        const controls = fgRef.current.controls?.()
        if (controls?.target) controls.target.set(0, 0, 0)
      }
      return
    }
    setActiveCluster(idx)
    const c = clusters[idx]
    if (!c || !fgRef.current) return
    const cNodes = graphData.nodes.filter((n: any) => c.ids.has(String(n.id)))
    if (!cNodes.length) return
    let cx = 0, cy = 0, cz = 0
    cNodes.forEach((n: any) => { cx += (n.x || 0); cy += (n.y || 0); cz += (n.z || 0) })
    cx /= cNodes.length; cy /= cNodes.length; cz /= cNodes.length
    let maxR = 0
    cNodes.forEach((n: any) => {
      const dx = (n.x || 0) - cx, dy = (n.y || 0) - cy, dz = (n.z || 0) - cz
      maxR = Math.max(maxR, Math.sqrt(dx * dx + dy * dy + dz * dz))
    })
    const dist = Math.max(200, maxR * 2.5)
    fgRef.current.cameraPosition({ x: cx, y: cy, z: cz + dist }, { x: cx, y: cy, z: cz }, 800)
    // Update orbit center so rotation feels natural around the cluster
    const controls = fgRef.current.controls?.()
    if (controls?.target) {
      controls.target.set(cx, cy, cz)
    }
  }, [clusters, graphData, activeCluster])

  const zoomToFit = () => {
    if (!fgRef.current) return
    fgRef.current.zoomToFit(400)
    // Reset orbit center to origin
    const controls = fgRef.current.controls?.()
    if (controls?.target) controls.target.set(0, 0, 0)
  }

  const selectedNode = useMemo(() => {
    if (selectedNodeIds.size !== 1) return null
    const id = Array.from(selectedNodeIds)[0]
    return graphData.nodes.find((n: any) => String(n.id) === id) || null
  }, [selectedNodeIds, graphData])

  const selectedNeighbors = useMemo(() => {
    if (!selectedNode) return []
    const nbSet = adjacency.get(String(selectedNode.id)) || new Set()
    return Array.from(nbSet).map(id => graphData.nodes.find((n: any) => String(n.id) === id)).filter(Boolean)
  }, [selectedNode, adjacency, graphData])


  const boardClass = fullscreen ? 'fixed inset-0 z-[120] bg-background flex flex-col' : 'rounded-xl border border-border bg-card overflow-hidden'

  // Display data: always density-filtered links only — stable layout on click
  const displayGraphData = useMemo(() => ({ nodes: graphData.nodes, links: graphData.links }), [graphData])
  const threeReady = !!(threeModule && glowTexture)

  return (
    <div className={boardClass}>
      <div className="px-4 pt-3 pb-2 space-y-2 shrink-0">
        <div className="flex items-center gap-2">
          <Network size={14} className="text-primary" />
          <span className="text-xs font-semibold text-foreground">知识图谱</span>
          <span className="text-[10px] text-muted-foreground">{graphData.nodes.length} 节点 · {graphData.links.length} 边{clusters.length > 1 ? ` · ${clusters.length} 星团` : ''}</span>
          <div className="ml-auto flex items-center gap-1.5">
            <button className="px-2 py-1 rounded bg-secondary text-[11px] hover:bg-border transition-colors" onClick={zoomToFit}>居中</button>
            <button className="px-2 py-1 rounded bg-secondary text-[11px] hover:bg-border transition-colors" onClick={() => onSelectNode('', false)}>清空</button>
            <button className="px-2 py-1 rounded bg-secondary flex items-center gap-1 text-[11px] hover:bg-border transition-colors" onClick={() => setFullscreen((v) => !v)}>
              {fullscreen ? <Minimize2 size={11} /> : <Maximize2 size={11} />}
              {fullscreen ? '退出' : '全屏'}
            </button>
          </div>
        </div>
        {clusters.length > 1 && (() => {
          const PAGE_SIZE = 8
          const filtered = clusterSearch
            ? clusters.map((c, i) => ({ ...c, _idx: i })).filter(x => x.name.toLowerCase().includes(clusterSearch.toLowerCase()))
            : clusters.map((c, i) => ({ ...c, _idx: i }))
          const totalPages = Math.ceil(filtered.length / PAGE_SIZE)
          const safePage = Math.min(clusterPage, totalPages - 1)
          const visible = filtered.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE)
          return (
            <div className="space-y-1.5">
              <div className="flex items-center gap-1.5">
                <span className="text-[10px] text-muted-foreground shrink-0">星团</span>
                <input
                  value={clusterSearch}
                  onChange={(e) => { setClusterSearch(e.target.value); setClusterPage(0) }}
                  placeholder="搜索星团…"
                  className="bg-secondary border border-border rounded px-1.5 py-0.5 text-[10px] w-28"
                />
                {totalPages > 1 && (
                  <div className="flex items-center gap-0.5 ml-auto">
                    <button
                      disabled={safePage <= 0}
                      onClick={() => setClusterPage(p => Math.max(0, p - 1))}
                      className="px-1.5 py-0.5 rounded bg-secondary text-[10px] disabled:opacity-30 hover:bg-border transition-colors"
                    >
                      <ChevronLeft size={10} />
                    </button>
                    <span className="text-[9px] text-muted-foreground min-w-[32px] text-center">{safePage + 1}/{totalPages}</span>
                    <button
                      disabled={safePage >= totalPages - 1}
                      onClick={() => setClusterPage(p => Math.min(totalPages - 1, p + 1))}
                      className="px-1.5 py-0.5 rounded bg-secondary text-[10px] disabled:opacity-30 hover:bg-border transition-colors"
                    >
                      <ChevronRight size={10} />
                    </button>
                  </div>
                )}
              </div>
              <div className="flex items-center gap-1 flex-wrap">
                {visible.map((c) => (
                  <button
                    key={c._idx}
                    onClick={() => focusCluster(c._idx)}
                    className={`px-2 py-0.5 rounded text-[10px] transition-colors truncate max-w-[140px] ${
                      activeCluster === c._idx
                        ? 'bg-primary/20 text-primary border border-primary/40'
                        : 'bg-secondary text-muted-foreground hover:bg-border border border-transparent'
                    }`}
                    title={c.name}
                  >
                    {c.name}
                  </button>
                ))}
                {filtered.length === 0 && <span className="text-[10px] text-muted-foreground">无匹配星团</span>}
              </div>
            </div>
          )
        })()}
        <div className="flex items-center gap-2 flex-wrap">
          <select className="bg-secondary border border-border rounded px-1.5 py-1 text-[11px]" value={clusterMode} onChange={(e) => setClusterMode(e.target.value as any)}>
            <option value="theme">按主题聚类</option>
            <option value="asset">按标的聚类</option>
          </select>
          <select className="bg-secondary border border-border rounded px-1.5 py-1 text-[11px]" value={relationFilter} onChange={(e) => onRelationFilter(e.target.value)}>
            <option value="">全部关系</option>
            {relationTypes.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
          <select value={selectedUploadId} onChange={(e) => onSelectUploadId(e.target.value)} className="bg-secondary border border-border rounded px-1.5 py-1 text-[11px] max-w-[180px] truncate">
            <option value="">上传记录：全部</option>
            {uploadOptions.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
          </select>
          <input value={uploadHighlight} onChange={(e) => onUploadHighlight(e.target.value)} placeholder="关键词高亮…" className="bg-secondary border border-border rounded px-1.5 py-1 text-[11px] w-28" />
        </div>
      </div>

      <div className="relative" style={fullscreen ? { flex: 1 } : { height: '680px' }}>
        <div className="absolute inset-0 overflow-hidden rounded-b-xl" style={{ background: 'linear-gradient(180deg, #080c18 0%, #0a0f1f 40%, #060a14 100%)' }}>
          {ForceGraph3DComp ? (
            <ForceGraph3DComp
              ref={fgRef}
              graphData={displayGraphData as any}
              backgroundColor="rgba(0,0,0,0)"
              nodeLabel={stableNodeLabel}
              nodeColor={stableNodeColor}
              {...(threeReady
                ? { nodeThreeObject: makeNodeObj, nodeThreeObjectExtend: false }
                : { nodeVal: stableNodeVal, nodeOpacity: 0.92 }
              )}
              linkWidth={stableLinkWidth}
              linkOpacity={0.2}
              linkColor={stableLinkColor}
              linkMaterial={stableLinkMaterial}
              linkLabel={stableLinkLabel}
              linkDirectionalParticles={stableLinkParticles}
              linkDirectionalParticleWidth={stableLinkParticleWidth}
              linkDirectionalParticleSpeed={0.004}
              linkDirectionalParticleColor={stableLinkColor}
              onNodeClick={stableOnNodeClick}
              onNodeHover={stableOnNodeHover}
              d3AlphaDecay={0.04}
              d3VelocityDecay={0.35}
              d3AlphaMin={0.05}
              d3Force="charge"
              d3ForceConfig={d3Config}
              onEngineStop={stableOnEngineStop}
              extraRenderers={emptyArr}
              cooldownTicks={60}
              cooldownTime={8000}
              warmupTicks={30}
              showNavInfo={false}
              enableNodeDrag
              onNodeDragEnd={stableOnNodeDragEnd}
            />
          ) : (
            <div className="h-full flex flex-col items-center justify-center gap-2">
              <Loader2 size={20} className="animate-spin text-muted-foreground" />
              <span className="text-xs text-muted-foreground">3D 引擎加载中…</span>
            </div>
          )}
        </div>

        <div className="absolute left-3 top-3 px-2.5 py-2 rounded-lg bg-black/60 backdrop-blur-sm border border-white/10 z-10 pointer-events-none select-none">
          <div className="space-y-1 text-[10px]">
            <div className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-sky-400 shadow-[0_0_4px_#38bdf8]" /><span className="text-white/70">事件</span></div>
            <div className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-violet-400 shadow-[0_0_4px_#a78bfa]" /><span className="text-white/70">政策</span></div>
            <div className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-amber-400 shadow-[0_0_4px_#f59e0b]" /><span className="text-white/70">结果</span></div>
            <div className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-emerald-400 shadow-[0_0_4px_#34d399]" /><span className="text-white/70">市场</span></div>
            <div className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-fuchsia-400 shadow-[0_0_4px_#f472b6]" /><span className="text-white/70">高亮</span></div>
          </div>
        </div>

        <div className="absolute bottom-3 left-1/2 -translate-x-1/2 px-3 py-1 rounded-full bg-black/40 backdrop-blur-sm text-[10px] text-white/40 pointer-events-none z-10 select-none whitespace-nowrap">
          拖拽旋转 · 滚轮缩放 · 点击选中 · Ctrl+点击多选
        </div>

        {hoveredNodeDisplay && !selectedNode && (
          <div className="absolute right-3 top-3 px-2.5 py-1.5 rounded-md bg-black/60 backdrop-blur-sm border border-white/10 z-10 pointer-events-none max-w-[220px]">
            <div className="text-[11px] text-white/90 truncate">{hoveredNodeDisplay.label || hoveredNodeDisplay.id}</div>
            <div className="text-[10px] text-white/50">{hoveredNodeDisplay.type || '节点'} · 连接 {degreeMap.get(String(hoveredNodeDisplay.id)) || 0}{(hoveredNodeDisplay.merge_count || 0) > 1 ? ` · 合并${hoveredNodeDisplay.merge_count}条` : ''}</div>
          </div>
        )}

        {selectedNode && (
          <div className="absolute right-3 bottom-12 w-64 rounded-lg bg-black/70 backdrop-blur-md border border-white/10 p-3 z-10">
            <div className="flex items-center gap-2 mb-2">
              <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ backgroundColor: nodeColor(selectedNode), boxShadow: `0 0 6px ${nodeColor(selectedNode)}` }} />
              <span className="text-xs font-medium text-white truncate">{selectedNode.label || selectedNode.id}</span>
              <button className="ml-auto text-white/40 hover:text-white/80 shrink-0" onClick={() => onSelectNode('', false)}><X size={12} /></button>
            </div>
            {selectedNode.type && <div className="text-[10px] text-white/50 mb-0.5">类型：{selectedNode.type}</div>}
            {(selectedNode.theme_scope || []).length > 0 && <div className="text-[10px] text-white/50 mb-0.5">主题：{(selectedNode.theme_scope || []).join(', ')}</div>}
            {(selectedNode.asset_scope || []).length > 0 && <div className="text-[10px] text-white/50 mb-0.5">标的：{(selectedNode.asset_scope || []).join(', ')}</div>}
            <div className="text-[10px] text-white/50">连接数：{degreeMap.get(String(selectedNode.id)) || 0}</div>
            {(selectedNode.merge_count || 0) > 1 && <div className="text-[10px] text-amber-400/80">已合并 {selectedNode.merge_count} 条相似观点</div>}
            {selectedNeighbors.length > 0 && (
              <div className="mt-2 pt-2 border-t border-white/10">
                <div className="text-[10px] text-white/40 mb-1">关联节点：</div>
                <div className="space-y-0.5 max-h-28 overflow-auto">
                  {selectedNeighbors.slice(0, 10).map((nb: any) => (
                    <button key={nb.id} onClick={() => onSelectNode(String(nb.id), false)} className="w-full text-left text-[10px] text-white/60 hover:text-white/90 truncate py-0.5">
                      {nb.label || nb.id}
                    </button>
                  ))}
                  {selectedNeighbors.length > 10 && <div className="text-[9px] text-white/30">还有 {selectedNeighbors.length - 10} 个…</div>}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

export default function KnowledgeBase() {
  const decisionSessionQuery = useCurrentDecisionSession()
  const decisionSession = decisionSessionQuery.data ?? currentDecisionSession
  const [tab, setTab] = useState<BoardTab>('calendar')
  const [loading, setLoading] = useState(false)
  const [timeline, setTimeline] = useState<TimelineEvent[]>([])
  const [taxonomy, setTaxonomy] = useState<TaxonomyResp | null>(null)
  const [graph, setGraph] = useState<GraphResp | null>(null)
  const [selectedNodeIds, setSelectedNodeIds] = useState<Set<string>>(new Set())
  const [relationFilter, setRelationFilter] = useState('')
  const [timelineFocusTs, setTimelineFocusTs] = useState<number | null>(null)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [nodeMode, setNodeMode] = useState(false)
  const [uploadHighlight, setUploadHighlight] = useState('')
  const [selectedUploadId, setSelectedUploadId] = useState('')
  const [showUpload, setShowUpload] = useState(false)
  const [uploadStatus, setUploadStatus] = useState<{ type: 'uploading' | 'done' | 'error'; msg: string } | null>(null)
  const [uploadSourceType, setUploadSourceType] = useState('')
  const [uploadTrust, setUploadTrust] = useState('')
  const [uploadNote, setUploadNote] = useState('')
  const [uploadSpeaker, setUploadSpeaker] = useState('')
  const [showLibrary, setShowLibrary] = useState(false)

  const [assetFilter, setAssetFilter] = useState('')
  const [themeFilter, setThemeFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [keyword, setKeyword] = useState('')

  const load = async () => {
    setLoading(true)
    setErrorMsg(null)
    try {
      const [t, tx, g] = await Promise.all([
        fetch(`${API}/timeline?days=365&limit=2200`).then((r) => r.json()),
        fetch(`${API}/taxonomy?limit=2600`).then((r) => r.json()),
        fetch(`${API}/graph?limit=600`).then((r) => r.json()),
      ])
      setTimeline(t.events || [])
      setTaxonomy(tx)
      setGraph(g)
    } catch (e: any) {
      setErrorMsg(String(e?.message || e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (showLibrary && timeline.length === 0 && !loading) void load()
  }, [showLibrary])

  // ?claim=xxx URL 参数 → 聚焦对应 claim（来自 Chat citation 跳转）
  const [searchParams, setSearchParams] = useSearchParams()
  useEffect(() => {
    const claimId = searchParams.get('claim')
    if (!claimId) return
    // 数据未就绪时不动作（等 load 完成后重新触发）
    if (loading) return
    if (timeline.length === 0 && !graph) return
    setSelectedNodeIds(new Set([claimId]))
    // 清理 URL 保留干净历史
    const next = new URLSearchParams(searchParams)
    next.delete('claim')
    setSearchParams(next, { replace: true })
  }, [searchParams, loading, timeline.length, graph])

  // 选中节点后，仅在非图谱Tab内自动定位到时间轴
  // 用 ref 读取当前 tab，避免 tab 切换本身触发跳转
  const tabRef = useRef(tab)
  tabRef.current = tab
  const prevSelectedRef = useRef(selectedNodeIds)
  useEffect(() => {
    // 只在 selectedNodeIds 真正改变时才可能跳转
    if (prevSelectedRef.current === selectedNodeIds) return
    prevSelectedRef.current = selectedNodeIds
    if (!selectedNodeIds.size || tabRef.current === 'graph') return
    const first = Array.from(selectedNodeIds)[0]
    const hit = timeline.find((e) => e.id === first || (e.type === 'claim_created' && e.id === first))
    if (hit?.ts) {
      setTimelineFocusTs(new Date(hit.ts).getTime())
      setTab('timeline')
    }
  }, [selectedNodeIds, timeline])

  const filteredTimeline = useMemo(() => {
    return timeline.filter((e) => {
      const okAsset = assetFilter ? (e.asset_scope || []).includes(assetFilter) : true
      const okTheme = themeFilter ? (e.theme_scope || []).includes(themeFilter) : true
      const okStatus = statusFilter ? e.status === statusFilter : true
      const text = `${e.title} ${e.type}`.toLowerCase()
      const okKw = keyword ? text.includes(keyword.toLowerCase()) : true
      const okUpload = selectedUploadId ? String((e.meta as any)?.source_id || '') === selectedUploadId : true
      return okAsset && okTheme && okStatus && okKw && okUpload
    })
  }, [timeline, assetFilter, themeFilter, statusFilter, keyword, selectedUploadId])

  const filteredTaxonomy = useMemo(() => {
    if (!taxonomy) return null
    return {
      ...taxonomy,
      mapped_items: taxonomy.mapped_items.filter((x) => {
        const okAsset = assetFilter ? (x.asset_scope || []).includes(assetFilter) : true
        const okTheme = themeFilter ? (x.theme_scope || []).includes(themeFilter) : true
        const okStatus = statusFilter ? x.status === statusFilter : true
        const okKw = keyword ? x.summary.toLowerCase().includes(keyword.toLowerCase()) : true
        return okAsset && okTheme && okStatus && okKw
      }),
    }
  }, [taxonomy, assetFilter, themeFilter, statusFilter, keyword])

  const filteredGraph = useMemo(() => {
    if (!graph) return null
    const nodes = graph.nodes.filter((n) => {
      const okAsset = assetFilter ? (n.asset_scope || []).includes(assetFilter) : true
      const okTheme = themeFilter ? (n.theme_scope || []).includes(themeFilter) : true
      const okStatus = statusFilter ? n.status === statusFilter : true
      const okKw = keyword ? n.label.toLowerCase().includes(keyword.toLowerCase()) : true
      return okAsset && okTheme && okStatus && okKw
    })
    const idSet = new Set(nodes.map((n) => String(n.id)))
    // force-graph mutates link objects: source/target may be string or object with .id
    const sid = (v: any) => String(v?.id ?? v)
    const links = graph.links.filter((l) => idSet.has(sid(l.source)) && idSet.has(sid(l.target)))
    return { nodes, links }
  }, [graph, assetFilter, themeFilter, statusFilter, keyword])

  const assets = useMemo(() => {
    const s = new Set<string>()
    timeline.forEach((e) => (e.asset_scope || []).forEach((a) => a && s.add(a)))
    return Array.from(s).slice(0, 30)
  }, [timeline])

  const themes = useMemo(() => {
    const s = new Set<string>()
    timeline.forEach((e) => (e.theme_scope || []).forEach((t) => t && s.add(t)))
    return Array.from(s).slice(0, 30)
  }, [timeline])

  const uploadOptions = useMemo(() => {
    const m = new Map<string, string>()
    timeline.forEach((e) => {
      const sid = String((e.meta as any)?.source_id || '')
      if (!sid) return
      if (!m.has(sid)) {
        const ttl = (e.meta as any)?.source_title || e.title || sid
        m.set(sid, `${ttl}`)
      }
    })
    return Array.from(m.entries()).map(([id, label]) => ({ id, label }))
  }, [timeline])

  const highlightIds = useMemo(() => {
    const s = new Set<string>()
    if (!nodeMode) return s

    if (selectedNodeIds.size) {
      selectedNodeIds.forEach((id) => s.add(id))
      return s
    }

    const now = Date.now()
    filteredTimeline.forEach((e) => {
      const t = new Date(e.ts).getTime()
      const uploadHitById = selectedUploadId ? String((e.meta as any)?.source_id || '') === selectedUploadId : false
      const uploadHitByKw = uploadHighlight && `${e.title} ${e.type}`.toLowerCase().includes(uploadHighlight.toLowerCase())
      if (uploadHitById || uploadHitByKw || now - t <= 7 * 24 * 3600_000) s.add(e.id)
    })

    return s
  }, [nodeMode, selectedNodeIds, filteredTimeline, uploadHighlight, selectedUploadId])

  const completedStages = decisionSession.ingestionStages.filter((stage) => stage.state === 'done').length
  const activeStage = decisionSession.ingestionStages.find((stage) => stage.state === 'active')
  const sessionReady = decisionSession.files.length > 0 && completedStages === decisionSession.ingestionStages.length

  return (
    <div className="mx-auto max-w-[1200px] flex-1 overflow-auto p-6 2xl:p-8">
      <div className="page-header !mb-5 !pb-4">
        <div className="flex items-center justify-between gap-3">
          <h1 className="text-xl font-semibold tracking-tight text-foreground">资料</h1>
          <a href="/chat" className="inline-flex h-9 items-center gap-2 rounded-lg border border-border bg-secondary px-3 text-xs font-medium text-foreground hover:border-primary/40">返回问答</a>
        </div>
      </div>

      <section className="mb-5 overflow-hidden rounded-lg border border-border bg-card">
        <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
          <div className="flex min-w-0 items-center gap-3">
            {sessionReady ? <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" /> : <BookOpen className="h-4 w-4 shrink-0 text-primary" />}
            <div className="min-w-0">
              <h2 className="text-sm font-semibold text-foreground">当前会话</h2>
              <p className="truncate text-xs text-muted-foreground">
                {decisionSession.files.length === 0 ? '还没有资料' : sessionReady ? `${decisionSession.files.length} 份资料已可用于问答` : `${decisionSession.files.length} 份资料 · ${activeStage?.title ?? '处理中'}`}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            {decisionSession.evidence.length > 0 && <span>{decisionSession.evidence.length} 条证据</span>}
            {decisionSession.files.length > 0 && <span>{completedStages}/{decisionSession.ingestionStages.length}</span>}
          </div>
        </div>
        {decisionSession.files.length === 0 ? (
          <div className="border-t border-border px-4 py-8 text-center"><p className="text-sm text-muted-foreground">在问答中上传文件后，这里会显示处理结果。</p></div>
        ) : (
          <div className="border-t border-border">
            {decisionSession.files.map((file) => (
              <div key={file.name} className="flex items-center justify-between gap-3 border-b border-border px-4 py-3 last:border-b-0">
                <div className="min-w-0"><p className="truncate text-sm font-medium text-foreground">{file.name}</p><p className="mt-0.5 text-xs text-muted-foreground">{file.type} · {file.size}</p></div>
                <span className={file.status === 'stored' ? 'text-xs text-emerald-400' : 'text-xs text-amber-400'}>{file.status === 'stored' ? '可用' : '处理中'}</span>
              </div>
            ))}
          </div>
        )}
        {decisionSession.files.length > 0 && (
          <details className="border-t border-border px-4 py-3">
            <summary className="flex cursor-pointer list-none items-center gap-2 text-xs font-medium text-muted-foreground hover:text-foreground">处理信息 <ChevronDown className="h-3.5 w-3.5" /></summary>
            <div className="mt-3 flex flex-wrap gap-x-4 gap-y-2 text-xs text-muted-foreground">
              {decisionSession.ingestionStages.map((stage) => (
                <span key={stage.title} className={stage.state === 'done' ? 'inline-flex items-center gap-1.5 text-emerald-400' : stage.state === 'active' ? 'inline-flex items-center gap-1.5 text-amber-400' : 'inline-flex items-center gap-1.5'}><span className="h-1.5 w-1.5 rounded-full bg-current" />{stage.title}</span>
              ))}
            </div>
          </details>
        )}
      </section>

      <button type="button" onClick={() => setShowLibrary((value) => !value)} className="mb-4 inline-flex items-center gap-2 text-xs font-medium text-muted-foreground hover:text-foreground" aria-expanded={showLibrary}>
        {showLibrary ? '收起资料库' : '浏览资料库'}
        <ChevronDown className={`h-3.5 w-3.5 transition-transform ${showLibrary ? 'rotate-180' : ''}`} />
      </button>

      {showLibrary && (
        <div>
          <div className="mb-4 flex flex-wrap items-center justify-end gap-2">
            <button onClick={() => setShowUpload(v => !v)} className="flex items-center gap-1.5 rounded border border-border bg-secondary px-3 py-1.5 text-xs font-medium hover:bg-border"><Upload size={12} /> 维护导入</button>
            <button onClick={load} className="flex items-center gap-1.5 rounded bg-secondary px-3 py-1.5 text-xs hover:bg-border">{loading ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />} 刷新</button>
          </div>

      {showUpload && (
        <div className="mb-4 border border-border/60 rounded-xl p-4 bg-secondary/20">
          <div className="flex items-center justify-between mb-2">
            <p className="text-xs font-semibold text-foreground">手动补充资料</p>
            <button onClick={() => setShowUpload(false)} className="text-muted-foreground hover:text-foreground"><X size={14} /></button>
          </div>
          <p className="text-[11px] text-muted-foreground mb-2">支持 txt / md / pdf / docx / jpg/png/webp</p>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 mb-2">
            <div className="relative">
              <input list="dl-source-type" value={uploadSourceType} onChange={e => setUploadSourceType(e.target.value)} placeholder="来源类型" className="bg-secondary border border-border rounded px-2 py-1 text-[11px] w-full" />
              <datalist id="dl-source-type">
                <option value="专业文章" /><option value="群聊记录" /><option value="视频文字稿" />
                <option value="个人笔记" /><option value="研究报告" /><option value="社交媒体" />
              </datalist>
            </div>
            <input value={uploadSpeaker} onChange={e => setUploadSpeaker(e.target.value)} placeholder="来源/作者" className="bg-secondary border border-border rounded px-2 py-1 text-[11px]" />
            <div className="relative">
              <input list="dl-trust" value={uploadTrust} onChange={e => setUploadTrust(e.target.value)} placeholder="可信度" className="bg-secondary border border-border rounded px-2 py-1 text-[11px] w-full" />
              <datalist id="dl-trust">
                <option value="高 — 长期准确/权威来源" /><option value="中 — 一般参考" /><option value="低 — 仅供了解" />
              </datalist>
            </div>
          </div>
          <input value={uploadNote} onChange={e => setUploadNote(e.target.value)} placeholder="备注（可选）" className="w-full bg-secondary border border-border rounded px-2 py-1.5 text-[11px] mb-2" />
          {uploadStatus && (
            <div className={`mb-2 text-[11px] rounded px-2 py-1.5 ${uploadStatus.type === 'error' ? 'bg-red-500/10 text-red-400 border border-red-400/30' : uploadStatus.type === 'done' ? 'bg-green-500/10 text-green-400 border border-green-400/30' : 'bg-blue-500/10 text-blue-300 border border-blue-400/30'}`}>
              {uploadStatus.type === 'uploading' && <Loader2 size={12} className="inline animate-spin mr-1" />}
              {uploadStatus.msg}
            </div>
          )}
          <label className="flex items-center justify-center gap-2 py-4 border border-dashed border-border rounded-lg cursor-pointer hover:bg-secondary/50 transition-colors">
            <Upload size={16} className="text-muted-foreground" />
            <span className="text-xs text-muted-foreground">点击选择文件（支持多选）</span>
            <input
              type="file"
              multiple
              accept=".txt,.md,.pdf,.docx,.jpg,.jpeg,.png,.webp"
              className="hidden"
              onChange={async (e) => {
                if (!e.target.files?.length) return
                const fileList = Array.from(e.target.files)
                // 前端拦截 .doc（旧版 Word），提示用户转换
                const docFiles = fileList.filter(f => f.name.toLowerCase().endsWith('.doc') && !f.name.toLowerCase().endsWith('.docx'))
                if (docFiles.length) {
                  setUploadStatus({ type: 'error', msg: `不支持旧版 .doc 格式（${docFiles.map(f => f.name).join(', ')}），请用 Word 另存为 .docx 后重新上传` })
                  e.target.value = ''
                  return
                }
                setUploadStatus({ type: 'uploading', msg: `正在上传 ${fileList.length} 个文件…` })
                const formData = new FormData()
                fileList.forEach(f => formData.append('files', f))
                if (uploadSpeaker) formData.append('speaker_hint', uploadSpeaker)
                if (uploadSourceType) formData.append('source_type_override', uploadSourceType)
                if (uploadTrust) formData.append('user_trust_level', uploadTrust)
                if (uploadNote) formData.append('user_note', uploadNote)
                try {
                  const resp = await fetch('/api/v1/upload', { method: 'POST', body: formData })
                  if (!resp.ok) {
                    const err = await resp.json().catch(() => ({ detail: resp.statusText }))
                    setUploadStatus({ type: 'error', msg: `上传失败：${err.detail || resp.statusText}` })
                    e.target.value = ''
                    return
                  }
                  const data = await resp.json()
                  const sourceIds: string[] = data.source_ids || []
                  rememberSessionSourceIds(sourceIds)
                  setUploadStatus({ type: 'uploading', msg: `上传成功，正在触发 AI 解析（${sourceIds.length} 份）…` })
                  const queued = sourceIds.length > 0
                    ? await uploadApi.processSelectedSources(sourceIds, true)
                    : null
                  const jobIds = queued
                    ? [
                        ...((queued.data.job_ids || []).filter((jobId) => typeof jobId === 'string' && jobId)),
                        ...((queued.data.jobs || []).flatMap((item) => {
                          const jobId = item.job_id
                          return typeof jobId === 'string' && jobId ? [jobId] : []
                        })),
                      ].filter((jobId, index, list) => list.indexOf(jobId) === index)
                    : []
                  if (sourceIds.length > 0 && !jobIds.length) {
                    setUploadStatus({ type: 'error', msg: '后台解析任务创建失败，请稍后重试' })
                    e.target.value = ''
                    return
                  }
                  rememberSessionJobIds(jobIds)
                  setUploadStatus({
                    type: 'uploading',
                    msg: jobIds.length
                      ? `后台解析已排队（${jobIds.length} 个任务），正在读取阶段进度…`
                      : `后台解析已提交，正在确认 ${sourceIds.length} 份资料状态…`,
                  })
                  // 轮询状态直到全部完成或超时
                  const pollStatus = async () => {
                    const maxPollMs = 120_000
                    const intervalMs = 2_500
                    const start = Date.now()
                    while (Date.now() - start < maxPollMs) {
                      await sleep(intervalMs)
                      try {
                        const [statuses, progressResults] = await Promise.all([
                          uploadApi.getBatchStatus(sourceIds),
                          Promise.allSettled(jobIds.map(jobId => uploadApi.getProgress(jobId))),
                        ])
                        const progress = progressResults.flatMap(item => item.status === 'fulfilled' ? [item.value] : [])
                        const summary = summarizeUploadProgress(progress, statuses)
                        setUploadStatus({ type: summary.type, msg: summary.msg })
                        if (!summary.complete) continue

                        if (summary.type === 'done') {
                          setTimeout(() => { setShowUpload(false); setUploadStatus(null); setUploadSourceType(''); setUploadTrust(''); setUploadNote(''); setUploadSpeaker('') }, 3000)
                        }
                        decisionSessionQuery.refetch()
                        load()
                        return
                      } catch { /* retry */ }
                    }
                    // 超时
                    setUploadStatus({ type: 'done', msg: `已提交 ${sourceIds.length} 份资料，后台仍在解析中…` })
                    decisionSessionQuery.refetch()
                    load()
                  }
                  pollStatus()
                } catch (err: any) {
                  setUploadStatus({ type: 'error', msg: `上传失败：${err.message || '网络错误'}` })
                }
                e.target.value = ''
              }}
            />
          </label>
        </div>
      )}

      {errorMsg && <div className="mb-3 text-xs text-red-400 border border-red-400/30 rounded p-2">加载失败：{errorMsg}</div>}

      {/* ── 筛选区 Filters ── */}
      <div className="zone-header">
        <span className="zone-eyebrow">Filters</span>
        <h2 className="zone-title">筛选</h2>
      </div>
      <div className="flex items-center gap-2 mb-4 flex-wrap">
        <select value={assetFilter} onChange={(e) => setAssetFilter(e.target.value)} className="bg-secondary border border-border rounded px-2 py-1.5 text-xs min-w-[100px]">
          <option value="">全部资产</option>
          {assets.map((a) => <option key={a} value={a}>{a}</option>)}
        </select>
        <select value={themeFilter} onChange={(e) => setThemeFilter(e.target.value)} className="bg-secondary border border-border rounded px-2 py-1.5 text-xs min-w-[100px]">
          <option value="">全部主题</option>
          {themes.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className="bg-secondary border border-border rounded px-2 py-1.5 text-xs min-w-[100px]">
          <option value="">全部状态</option>
          <option value="active">active</option>
          <option value="expired">expired</option>
          <option value="invalidated">invalidated</option>
          <option value="pending">pending</option>
          <option value="completed">completed</option>
        </select>
        <input value={keyword} onChange={(e) => setKeyword(e.target.value)} placeholder="关键词搜索…" className="bg-secondary border border-border rounded px-2 py-1.5 text-xs w-40" />
        <button className="ml-auto bg-secondary border border-border rounded px-3 py-1.5 text-xs hover:bg-border transition-colors" onClick={() => { setAssetFilter(''); setThemeFilter(''); setStatusFilter(''); setKeyword(''); setRelationFilter(''); setNodeMode(false) }}>清空筛选</button>
      </div>

      {/* ── 视图区 Views ── */}
      <div className="zone-header mt-6">
        <span className="zone-eyebrow">Views</span>
        <h2 className="zone-title">视图</h2>
      </div>
      <div className="flex gap-1 mb-6 border-b border-border">
        <TabButton active={tab === 'calendar'} label="日历" icon={<CalendarDays size={13} />} onClick={() => setTab('calendar')} />
        <TabButton active={tab === 'timeline'} label="时间线" icon={<Clock3 size={13} />} onClick={() => setTab('timeline')} />
        <TabButton active={tab === 'taxonomy'} label="分类体系" icon={<GitBranch size={13} />} onClick={() => setTab('taxonomy')} />
        <TabButton active={tab === 'graph'} label="知识图谱" icon={<Network size={13} />} onClick={() => setTab('graph')} />
      </div>

      {loading && timeline.length === 0 ? (
        <div className="flex justify-center py-16"><Loader2 size={20} className="animate-spin text-muted-foreground" /></div>
      ) : (
        <>
          {tab === 'calendar' && <CalendarPanel events={filteredTimeline} onSelect={(e) => {
            if (e.type === 'claim_created') setSelectedNodeIds(new Set([e.id]))
          }} />}
          {tab === 'timeline' && (
            <TimelinePanel
              events={filteredTimeline}
              onSelect={(e) => {
                if (e.type === 'claim_created') setSelectedNodeIds(new Set([e.id]))
              }}
              focusTs={timelineFocusTs}
              nodeMode={nodeMode}
              onToggleNodeMode={() => setNodeMode((v) => !v)}
              highlightIds={highlightIds}
            />
          )}
          {tab === 'taxonomy' && <TaxonomyPanel data={filteredTaxonomy} onSelectClaim={(id) => setSelectedNodeIds(new Set([id]))} />}
          {tab === 'graph' && (
            <GraphPanel
              data={filteredGraph}
              selectedNodeIds={selectedNodeIds}
              onSelectNode={(id, append) => {
                if (!id) {
                  setSelectedNodeIds(new Set())
                  return
                }
                setSelectedNodeIds((prev) => {
                  if (!append) {
                    if (prev.size === 1 && prev.has(id)) return new Set()
                    return new Set([id])
                  }
                  const next = new Set(prev)
                  if (next.has(id)) next.delete(id)
                  else next.add(id)
                  return next
                })
              }}
              relationFilter={relationFilter}
              onRelationFilter={setRelationFilter}
              uploadHighlight={uploadHighlight}
              onUploadHighlight={setUploadHighlight}
              uploadOptions={uploadOptions}
              selectedUploadId={selectedUploadId}
              onSelectUploadId={setSelectedUploadId}
            />
          )}
        </>
      )}

        </div>
      )}
    </div>
  )
}
