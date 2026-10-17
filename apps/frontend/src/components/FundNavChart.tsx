import { useRef, useEffect, useState } from 'react'
import {
  createChart,
  type IChartApi,
  type ISeriesApi,
  ColorType,
  CrosshairMode,
  type LineData,
  type Time,
  LineStyle,
} from 'lightweight-charts'
import { useFundHistory } from '@/services/hooks'

const PERIODS = [
  { key: '1m', label: '近1月' },
  { key: '3m', label: '近3月' },
  { key: '6m', label: '近6月' },
  { key: '1y', label: '近1年' },
  { key: '3y', label: '近3年' },
  { key: 'all', label: '成立来' },
] as const

const SERIES_COLORS = [
  { line: '#3b82f6', label: '本基金' },      // blue-500
  { line: '#94a3b8', label: '同类平均' },      // slate-400
  { line: '#f59e0b', label: '沪深300' },       // amber-500
]

function tsToDay(ms: number): string {
  const d = new Date(ms)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

interface Props {
  code: string
  name?: string
  height?: number
}

export default function FundNavChart({ code, name, height = 320 }: Props) {
  const [period, setPeriod] = useState<string>('1y')
  const { data: hist, isLoading } = useFundHistory(code, period)

  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRefs = useRef<ISeriesApi<'Line'>[]>([])

  const [hoverInfo, setHoverInfo] = useState<{ time: string; values: { name: string; value: string; color: string }[] } | null>(null)

  // Create chart once
  useEffect(() => {
    if (!containerRef.current) return
    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: 'hsl(215 20% 55%)',
        fontSize: 11,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: 'hsl(215 20% 20% / 0.3)', style: LineStyle.Dotted },
        horzLines: { color: 'hsl(215 20% 20% / 0.3)', style: LineStyle.Dotted },
      },
      crosshair: { mode: CrosshairMode.Magnet },
      rightPriceScale: {
        borderVisible: true,
        borderColor: 'hsl(215 20% 25%)',
        autoScale: true,
        scaleMargins: { top: 0.08, bottom: 0.08 },
      },
      localization: {
        priceFormatter: (p: number) => p.toFixed(4),
      },
      timeScale: {
        borderVisible: true,
        borderColor: 'hsl(215 20% 25%)',
        timeVisible: false,
        fixLeftEdge: true,
        fixRightEdge: true,
        tickMarkFormatter: (time: string) => {
          // time is 'YYYY-MM-DD'
          const parts = time.split('-')
          if (parts.length === 3) return `${parts[0]}-${parts[1]}`
          return time
        },
      },
      handleScroll: true,
      handleScale: true,
    })
    chartRef.current = chart

    // Resize observer
    const ro = new ResizeObserver(entries => {
      for (const e of entries) chart.applyOptions({ width: e.contentRect.width })
    })
    ro.observe(containerRef.current)

    return () => {
      ro.disconnect()
      chart.remove()
      chartRef.current = null
      seriesRefs.current = []
    }
  }, [height])

  // Update data
  useEffect(() => {
    const chart = chartRef.current
    if (!chart || !hist) return

    // Remove old series
    for (const s of seriesRefs.current) {
      try { chart.removeSeries(s) } catch { /* ignore */ }
    }
    seriesRefs.current = []

    // 1. Main NAV line
    if (hist.nav.length > 0) {
      const navSeries = chart.addLineSeries({
        color: SERIES_COLORS[0].line,
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: true,
        crosshairMarkerVisible: true,
      })
      const navData: LineData[] = hist.nav.map(p => ({
        time: tsToDay(p.t) as Time,
        value: p.nav,
      }))
      navSeries.setData(navData)
      seriesRefs.current.push(navSeries)
    }

    // 2. Compare series (percentage-based, convert to absolute NAV-like scale)
    // The compare data is percentage change from start, so we convert:
    // compare_value = start_nav * (1 + pct/100) to make it visually comparable
    const startNav = hist.nav.length > 0 ? hist.nav[0].nav : 1
    hist.compare.forEach((cs, i) => {
      if (i === 0) return // skip first series (it's the fund itself in grandTotal)
      const colorIdx = Math.min(i, SERIES_COLORS.length - 1)
      const lineSeries = chart.addLineSeries({
        color: SERIES_COLORS[colorIdx].line,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      })
      const lineData: LineData[] = cs.data
        .filter(d => d && d.length >= 2)
        .map(d => ({
          time: tsToDay(d[0]) as Time,
          value: +(startNav * (1 + d[1] / 100)).toFixed(4),
        }))
      lineSeries.setData(lineData)
      seriesRefs.current.push(lineSeries)
    })

    chart.timeScale().fitContent()

    // Crosshair handler
    chart.subscribeCrosshairMove(param => {
      if (!param.time || !param.seriesData) {
        setHoverInfo(null)
        return
      }
      const timeStr = param.time as string
      const values: { name: string; value: string; color: string }[] = []
      seriesRefs.current.forEach((s, idx) => {
        const d = param.seriesData.get(s)
        if (d && 'value' in d) {
          let seriesName: string
          if (idx === 0) {
            seriesName = SERIES_COLORS[0].label
          } else {
            const compareIdx = idx // compare series starts at index 1 in seriesRefs
            seriesName = hist.compare[compareIdx]?.name || SERIES_COLORS[Math.min(idx, SERIES_COLORS.length - 1)].label
          }
          values.push({
            name: seriesName,
            value: (d as { value: number }).value.toFixed(4),
            color: idx === 0 ? SERIES_COLORS[0].line : SERIES_COLORS[Math.min(idx, SERIES_COLORS.length - 1)].line,
          })
        }
      })
      setHoverInfo({ time: timeStr, values })
    })
  }, [hist])

  return (
    <div className="rounded-xl p-4" style={{ background: 'hsl(var(--card))', border: '1px solid hsl(var(--border))' }}>
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-foreground">{name || code}</h3>
          <span className="text-[10px] font-mono text-muted-foreground/50">{code}</span>
          {hist?.returns && Object.entries(hist.returns).map(([k, v]) => (
            <span key={k} className={`text-[10px] font-mono ${v >= 0 ? 'text-red-400' : 'text-emerald-400'}`}>
              {k} {v >= 0 ? '+' : ''}{v.toFixed(2)}%
            </span>
          ))}
        </div>
      </div>

      {/* Period pills */}
      <div className="flex gap-1 mb-3">
        {PERIODS.map(p => (
          <button
            key={p.key}
            onClick={() => setPeriod(p.key)}
            className={`px-2.5 py-1 rounded-lg text-[11px] font-medium transition-colors ${
              period === p.key
                ? 'bg-primary text-primary-foreground'
                : 'text-muted-foreground hover:text-foreground hover:bg-secondary'
            }`}
          >
            {p.label}
          </button>
        ))}
      </div>

      {/* Hover tooltip */}
      {hoverInfo && (
        <div className="flex items-center gap-4 mb-1 text-[11px]">
          <span className="text-muted-foreground font-mono">{hoverInfo.time}</span>
          {hoverInfo.values.map((v, i) => (
            <span key={i} className="font-mono" style={{ color: v.color }}>
              {v.name}: {v.value}
            </span>
          ))}
        </div>
      )}

      {/* Legend */}
      <div className="flex items-center gap-4 mb-2">
        {SERIES_COLORS.slice(0, 1 + (hist?.compare.length ? hist.compare.length - 1 : 0)).map((s, i) => (
          <div key={i} className="flex items-center gap-1">
            <div className="w-3 h-0.5 rounded-full" style={{
              background: s.line,
              borderStyle: i === 0 ? 'solid' : 'dashed',
            }} />
            <span className="text-[10px] text-muted-foreground">{
              i === 0 ? (name || s.label) : (hist?.compare[i]?.name || s.label)
            }</span>
          </div>
        ))}
      </div>

      {/* Chart */}
      {isLoading ? (
        <div className="flex items-center justify-center" style={{ height }}>
          <span className="text-[12px] text-muted-foreground/40">加载历史数据中...</span>
        </div>
      ) : (
        <div ref={containerRef} style={{ height }} />
      )}

      {hist && hist.total_points > 0 && (
        <p className="text-[9px] text-muted-foreground/30 mt-2 text-right">
          共 {hist.total_points} 个交易日数据 · 数据源: 天天基金
        </p>
      )}
    </div>
  )
}
