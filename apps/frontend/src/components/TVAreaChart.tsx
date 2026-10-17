import { useRef, useEffect, useCallback } from 'react'
import {
  createChart,
  type IChartApi,
  type ISeriesApi,
  ColorType,
  CrosshairMode,
  type AreaData,
  type HistogramData,
  type Time,
  type TickMarkType,
  LineStyle,
} from 'lightweight-charts'

export interface Kline {
  t: number
  o: number | string
  h: number | string
  l: number | string
  c: number | string
  v: number | string
}

export interface CrosshairData {
  time: string | null
  open: number | null
  high: number | null
  low: number | null
  close: number | null
  volume: number | null
  change: number | null
  changePct: number | null
}

interface TVChartProps {
  klines: Kline[]
  up: boolean
  height?: number
  volumeHeight?: number
  timeframeKey?: string
  onCrosshairMove?: (data: CrosshairData) => void
}

const LINE_COLOR = 'rgba(99,140,203,0.9)'
const TOP_GRADIENT = 'rgba(99,140,203,0.18)'
const BOT_GRADIENT = 'rgba(99,140,203,0.01)'
const VOL_UP = 'rgba(52,211,153,0.35)'
const VOL_DOWN = 'rgba(248,113,113,0.30)'
const VOL_NEUTRAL = 'rgba(130,150,180,0.20)'

const fmt = (v: number) =>
  v >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 2 })
    : v >= 1 ? v.toFixed(2) : v.toFixed(6)

const fmtVol = (v: number) => {
  if (v >= 1e9) return `${(v / 1e9).toFixed(1)}B`
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`
  if (v >= 1e3) return `${(v / 1e3).toFixed(0)}K`
  return v.toFixed(0)
}

export default function TVChart({
  klines,
  up: _up,
  height = 280,
  volumeHeight = 60,
  timeframeKey,
  onCrosshairMove,
}: TVChartProps) {
  void _up
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const areaRef = useRef<ISeriesApi<'Area'> | null>(null)
  const volRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const klinesMapRef = useRef<Map<number, Kline>>(new Map())

  const totalHeight = height + volumeHeight

  const stableCrosshair = useCallback(
    (data: CrosshairData) => { onCrosshairMove?.(data) },
    [],
  )

  // Create chart once
  useEffect(() => {
    if (!containerRef.current) return

    // Detect theme: .light on <html> = light mode
    const isLight = document.documentElement.classList.contains('light')
    const textColor    = isLight ? 'rgba(0,0,0,0.6)'   : 'rgba(255,255,255,0.7)'
    const borderColor  = isLight ? 'rgba(0,0,0,0.15)'   : 'rgba(255,255,255,0.3)'
    const gridVert     = isLight ? 'rgba(0,0,0,0.04)'   : 'rgba(255,255,255,0.04)'
    const gridHorz     = isLight ? 'rgba(0,0,0,0.07)'   : 'rgba(255,255,255,0.06)'
    const crossColor   = isLight ? 'rgba(0,0,0,0.25)'   : 'rgba(255,255,255,0.25)'
    const labelBg      = isLight ? 'rgba(255,255,255,0.95)' : 'rgba(30,30,45,0.95)'

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: totalHeight,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor,
        fontSize: 11,
        fontFamily: "'JetBrains Mono', 'SF Mono', 'Cascadia Code', monospace",
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: gridVert, style: LineStyle.Dotted },
        horzLines: { color: gridHorz, style: LineStyle.Dotted },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: {
          color: crossColor,
          width: 1,
          style: LineStyle.Dashed,
          labelVisible: true,
          labelBackgroundColor: labelBg,
        },
        horzLine: {
          color: crossColor,
          width: 1,
          style: LineStyle.Dashed,
          labelVisible: true,
          labelBackgroundColor: labelBg,
        },
      },
      rightPriceScale: {
        visible: false,
      },
      leftPriceScale: {
        visible: true,
        borderVisible: true,
        borderColor,
        scaleMargins: { top: 0.05, bottom: 0.25 },
        entireTextOnly: true,
        autoScale: true,
      },
      timeScale: {
        borderVisible: true,
        borderColor,
        timeVisible: true,
        secondsVisible: false,
        fixLeftEdge: true,
        fixRightEdge: true,
        tickMarkMaxCharacterLength: 18,
        minBarSpacing: 4,
        tickMarkFormatter: (time: number, tickMarkType: TickMarkType, _locale: string) => {
          const d = new Date(time * 1000)
          const yyyy = d.getFullYear()
          const mm = String(d.getMonth() + 1).padStart(2, '0')
          const dd = String(d.getDate()).padStart(2, '0')
          const HH = String(d.getHours()).padStart(2, '0')
          const MM = String(d.getMinutes()).padStart(2, '0')
          const now = new Date()
          const showYear = yyyy !== now.getFullYear()
          const yPrefix = showYear ? `${yyyy}/` : ''
          // Year-level or month-level tick → always show year
          if (tickMarkType <= 1) return `${yyyy}/${mm}`
          // Day-level bar (hour=0, min=0)
          if (HH === '00' && MM === '00') return `${yPrefix}${mm}/${dd}`
          // Intraday bar
          return `${yPrefix}${mm}/${dd} ${HH}:${MM}`
        },
      },
      handleScroll: { mouseWheel: true, pressedMouseMove: true },
      handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true },
    })
    chartRef.current = chart

    // Price area series (main)
    const area = chart.addAreaSeries({
      priceScaleId: 'left',
      lineWidth: 2,
      lineColor: LINE_COLOR,
      topColor: TOP_GRADIENT,
      bottomColor: BOT_GRADIENT,
      crosshairMarkerRadius: 5,
      crosshairMarkerBorderWidth: 2,
      crosshairMarkerBorderColor: isLight ? 'rgba(255,255,255,0.9)' : 'rgba(30,30,45,0.9)',
      crosshairMarkerBackgroundColor: LINE_COLOR,
      lastValueVisible: true,
      priceLineVisible: true,
      priceLineStyle: LineStyle.Dotted,
      priceLineWidth: 1,
      priceLineColor: 'rgba(99,140,203,0.4)',
    })
    areaRef.current = area

    // Volume histogram series (bottom)
    const vol = chart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
      lastValueVisible: false,
    })
    chart.priceScale('volume').applyOptions({
      scaleMargins: { top: 0.82, bottom: 0 },
      borderVisible: false,
      autoScale: true,
      visible: false,
    })
    volRef.current = vol

    // Crosshair move — emit OHLCV data
    chart.subscribeCrosshairMove((param) => {
      if (!param.time || !param.seriesData.size) {
        stableCrosshair({ time: null, open: null, high: null, low: null, close: null, volume: null, change: null, changePct: null })
        return
      }
      const ad = param.seriesData.get(area) as AreaData<Time> | undefined
      if (!ad) return
      const ts = param.time as number
      const d = new Date(ts * 1000)
      const timeStr = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
      const kl = klinesMapRef.current.get(ts)
      const o = kl ? Number(kl.o) : null
      const h = kl ? Number(kl.h) : null
      const l = kl ? Number(kl.l) : null
      const c = ad.value
      const v = kl ? Number(kl.v) : null
      const change = o != null ? c - o : null
      const changePct = o != null && o !== 0 ? ((c - o) / o) : null
      stableCrosshair({ time: timeStr, open: o, high: h, low: l, close: c, volume: v, change, changePct })
    })

    // Resize observer
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width
      if (w) chart.applyOptions({ width: w })
    })
    ro.observe(containerRef.current)

    return () => {
      ro.disconnect()
      chart.remove()
      chartRef.current = null
      areaRef.current = null
      volRef.current = null
    }
  }, [totalHeight])

  // Update data when klines change
  useEffect(() => {
    const area = areaRef.current
    const vol = volRef.current
    const chart = chartRef.current
    if (!area || !vol || !chart || !klines?.length) return

    const sorted = [...klines]
      .filter(k => k.t && k.c != null)
      .sort((a, b) => Number(a.t) - Number(b.t))

    // Build lookup map
    const map = new Map<number, Kline>()
    const areaData: AreaData<Time>[] = []
    const volData: HistogramData<Time>[] = []

    sorted.forEach((k, i) => {
      const ts = Math.floor(Number(k.t) / 1000) as Time
      const c = Number(k.c)
      const o = Number(k.o)
      const v = Number(k.v)
      map.set(ts as number, k)
      areaData.push({ time: ts, value: c })

      let color = VOL_NEUTRAL
      if (i > 0) {
        const prevC = Number(sorted[i - 1].c)
        color = c >= prevC ? VOL_UP : VOL_DOWN
      } else {
        color = c >= o ? VOL_UP : VOL_DOWN
      }
      volData.push({ time: ts, value: v, color })
    })

    klinesMapRef.current = map

    if (areaData.length > 0) {
      area.setData(areaData)
      vol.setData(volData)

      // High/Low markers
      let maxIdx = 0, minIdx = 0
      areaData.forEach((d, i) => {
        if (d.value > areaData[maxIdx].value) maxIdx = i
        if (d.value < areaData[minIdx].value) minIdx = i
      })
      if (areaData.length >= 3 && maxIdx !== minIdx) {
        area.setMarkers([
          ...(maxIdx < minIdx ? [maxIdx, minIdx] : [minIdx, maxIdx]).map(idx => ({
            time: areaData[idx].time,
            position: idx === maxIdx ? 'aboveBar' as const : 'belowBar' as const,
            color: idx === maxIdx ? 'rgba(52,211,153,0.7)' : 'rgba(248,113,113,0.7)',
            shape: idx === maxIdx ? 'arrowUp' as const : 'arrowDown' as const,
            text: `${idx === maxIdx ? '▲' : '▼'} ${fmt(areaData[idx].value)}`,
          })),
        ])
      }

      chart.timeScale().fitContent()
    }
  }, [klines, timeframeKey])

  return (
    <div ref={containerRef} className="w-full relative" style={{ height: totalHeight }} />
  )
}

export { fmt, fmtVol }
