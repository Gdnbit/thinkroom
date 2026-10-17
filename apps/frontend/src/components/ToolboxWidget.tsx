import { useState, useEffect, useCallback } from 'react'
import { Wrench, Calculator, ArrowLeftRight, Scale, X } from 'lucide-react'
import { clsx } from 'clsx'

// ── Calculator ──────────────────────────────────────────
function CalcTab() {
  const [display, setDisplay] = useState('0')
  const [prev, setPrev] = useState<number | null>(null)
  const [op, setOp] = useState<string | null>(null)
  const [fresh, setFresh] = useState(true)

  const input = (ch: string) => {
    if (fresh) { setDisplay(ch === '.' ? '0.' : ch); setFresh(false) }
    else {
      if (ch === '.' && display.includes('.')) return
      setDisplay(d => d === '0' && ch !== '.' ? ch : d + ch)
    }
  }
  const doOp = (nextOp: string) => {
    const cur = parseFloat(display)
    if (prev !== null && op && !fresh) {
      const r = op === '+' ? prev + cur : op === '-' ? prev - cur : op === '×' ? prev * cur : op === '÷' && cur !== 0 ? prev / cur : cur
      setPrev(r); setDisplay(String(parseFloat(r.toPrecision(12))))
    } else { setPrev(cur) }
    setOp(nextOp); setFresh(true)
  }
  const calc = () => { doOp('='); setOp(null) }
  const clear = () => { setDisplay('0'); setPrev(null); setOp(null); setFresh(true) }

  const BTN = 'h-8 rounded-lg text-[12px] font-medium transition-colors flex items-center justify-center'
  const NUM = `${BTN} bg-white/5 hover:bg-white/10 text-foreground`
  const OPR = `${BTN} bg-primary/15 hover:bg-primary/25 text-primary`

  return (
    <div className="space-y-2">
      <div className="h-10 rounded-lg px-3 flex items-center justify-end font-mono text-[16px] text-foreground tabular-nums"
        style={{ background: 'hsl(var(--secondary))' }}>
        {display}
      </div>
      <div className="grid grid-cols-4 gap-1">
        <button onClick={clear} className={`${BTN} bg-red-400/10 hover:bg-red-400/20 text-red-400 col-span-2`}>AC</button>
        <button onClick={() => setDisplay(d => String(parseFloat(d) * -1))} className={OPR}>±</button>
        <button onClick={() => doOp('÷')} className={OPR}>÷</button>
        {['7','8','9'].map(n => <button key={n} onClick={() => input(n)} className={NUM}>{n}</button>)}
        <button onClick={() => doOp('×')} className={OPR}>×</button>
        {['4','5','6'].map(n => <button key={n} onClick={() => input(n)} className={NUM}>{n}</button>)}
        <button onClick={() => doOp('-')} className={OPR}>−</button>
        {['1','2','3'].map(n => <button key={n} onClick={() => input(n)} className={NUM}>{n}</button>)}
        <button onClick={() => doOp('+')} className={OPR}>+</button>
        <button onClick={() => input('0')} className={`${NUM} col-span-2`}>0</button>
        <button onClick={() => input('.')} className={NUM}>.</button>
        <button onClick={calc} className={`${BTN} bg-primary hover:bg-primary/80 text-primary-foreground`}>=</button>
      </div>
    </div>
  )
}

// ── Currency Converter (multi-target) ───────────────────
const CURRENCIES = [
  { code: 'USD', symbol: '$', name: '美元' },
  { code: 'CNY', symbol: '¥', name: '人民币' },
  { code: 'HKD', symbol: 'HK$', name: '港币' },
  { code: 'EUR', symbol: '€', name: '欧元' },
  { code: 'GBP', symbol: '£', name: '英镑' },
  { code: 'JPY', symbol: '¥', name: '日元' },
]

type Rates = Record<string, number>

function CurrencyTab() {
  const [amount, setAmount] = useState('1000')
  const [from, setFrom] = useState('USD')
  const [rates, setRates] = useState<Rates | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const fetchRates = useCallback(async () => {
    setLoading(true); setError('')
    try {
      // Free exchange rate API (no key needed)
      const r = await fetch(`https://api.exchangerate-api.com/v4/latest/${from}`)
      if (!r.ok) throw new Error('API error')
      const data = await r.json()
      setRates(data.rates)
    } catch {
      setError('汇率获取失败，请检查网络')
      // Fallback approximate rates (USD base)
      const fallback: Rates = { USD: 1, CNY: 7.24, HKD: 7.81, EUR: 0.92, GBP: 0.79, JPY: 151.5 }
      if (from === 'USD') setRates(fallback)
      else {
        const base = fallback[from] || 1
        const r: Rates = {}
        for (const [k, v] of Object.entries(fallback)) r[k] = v / base
        setRates(r)
      }
    }
    setLoading(false)
  }, [from])

  useEffect(() => { fetchRates() }, [fetchRates])

  const val = parseFloat(amount) || 0

  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <input
          type="number" value={amount}
          onChange={e => setAmount(e.target.value)}
          className="flex-1 h-9 rounded-lg px-3 text-[13px] font-mono text-foreground outline-none"
          style={{ background: 'hsl(var(--secondary))', border: '1px solid hsl(var(--border))' }}
        />
        <select value={from} onChange={e => setFrom(e.target.value)}
          className="h-9 rounded-lg px-2 text-[12px] font-medium text-foreground outline-none cursor-pointer"
          style={{ background: 'hsl(var(--secondary))', border: '1px solid hsl(var(--border))' }}>
          {CURRENCIES.map(c => <option key={c.code} value={c.code}>{c.code}</option>)}
        </select>
      </div>
      {error && <p className="text-[10px] text-amber-400/70">{error}（使用离线汇率）</p>}
      <div className="space-y-1">
        {rates && CURRENCIES.filter(c => c.code !== from).map(c => {
          const rate = rates[c.code]
          if (!rate) return null
          const converted = val * rate
          return (
            <div key={c.code} className="flex items-center justify-between px-2.5 py-1.5 rounded-lg hover:bg-white/[0.03]">
              <div className="flex items-center gap-2">
                <span className="text-[11px] font-semibold text-foreground w-8">{c.code}</span>
                <span className="text-[10px] text-muted-foreground/50">{c.name}</span>
              </div>
              <span className="text-[13px] font-mono font-medium text-foreground tabular-nums">
                {c.symbol}{converted.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </span>
            </div>
          )
        })}
        {loading && <p className="text-[11px] text-muted-foreground text-center py-2">获取汇率中...</p>}
      </div>
      <p className="text-[9px] text-muted-foreground/30 text-center">汇率来源: exchangerate-api.com</p>
    </div>
  )
}

// ── Asset Unit Converter ────────────────────────────────
// Gold: CNY/g, USD/oz, USD/g, HKD/tael
// Silver: CNY/g, USD/oz
// Oil: USD/barrel, CNY/L (approximate)
const ASSET_UNITS: Record<string, { name: string; units: { key: string; label: string; factor: number; symbol: string }[] }> = {
  gold: {
    name: '黄金',
    units: [
      { key: 'usd_oz',  label: '美元/盎司', factor: 1,           symbol: '$' },
      { key: 'usd_g',   label: '美元/克',   factor: 1 / 31.1035, symbol: '$' },
      { key: 'cny_g',   label: '人民币/克', factor: 7.24 / 31.1035, symbol: '¥' },
      { key: 'hkd_tael', label: '港币/两',  factor: 7.81 * 1.20337, symbol: 'HK$' }, // 1 tael ≈ 37.429g ≈ 1.20337 oz
    ],
  },
  silver: {
    name: '白银',
    units: [
      { key: 'usd_oz', label: '美元/盎司', factor: 1,             symbol: '$' },
      { key: 'usd_g',  label: '美元/克',   factor: 1 / 31.1035,   symbol: '$' },
      { key: 'cny_g',  label: '人民币/克', factor: 7.24 / 31.1035, symbol: '¥' },
    ],
  },
  oil: {
    name: '原油',
    units: [
      { key: 'usd_bbl', label: '美元/桶',   factor: 1,      symbol: '$' },
      { key: 'cny_bbl', label: '人民币/桶', factor: 7.24,    symbol: '¥' },
      { key: 'usd_l',   label: '美元/升',   factor: 1 / 158.987, symbol: '$' }, // 1 barrel ≈ 158.987 liters
    ],
  },
}

function UnitTab() {
  const [assetType, setAssetType] = useState('gold')
  const [value, setValue] = useState('2350')
  const [fromUnit, setFromUnit] = useState('usd_oz')

  const asset = ASSET_UNITS[assetType]
  const val = parseFloat(value) || 0
  const fromFactor = asset.units.find(u => u.key === fromUnit)?.factor ?? 1
  // Convert input to base unit (usd_oz or usd_bbl), then to all others
  const baseValue = val / fromFactor

  return (
    <div className="space-y-3">
      <div className="flex gap-1.5">
        {Object.entries(ASSET_UNITS).map(([k, a]) => (
          <button key={k}
            onClick={() => { setAssetType(k); setFromUnit(ASSET_UNITS[k].units[0].key); setValue(k === 'gold' ? '2350' : k === 'silver' ? '29' : '75') }}
            className={clsx('flex-1 h-7 rounded-lg text-[11px] font-medium transition-colors',
              assetType === k ? 'bg-primary/15 text-primary' : 'text-muted-foreground hover:text-foreground hover:bg-white/5')}
          >{a.name}</button>
        ))}
      </div>
      <div className="flex gap-2">
        <input type="number" value={value} onChange={e => setValue(e.target.value)}
          className="flex-1 h-9 rounded-lg px-3 text-[13px] font-mono text-foreground outline-none"
          style={{ background: 'hsl(var(--secondary))', border: '1px solid hsl(var(--border))' }}
        />
        <select value={fromUnit} onChange={e => setFromUnit(e.target.value)}
          className="h-9 rounded-lg px-2 text-[11px] font-medium text-foreground outline-none cursor-pointer"
          style={{ background: 'hsl(var(--secondary))', border: '1px solid hsl(var(--border))' }}>
          {asset.units.map(u => <option key={u.key} value={u.key}>{u.label}</option>)}
        </select>
      </div>
      <div className="space-y-1">
        {asset.units.filter(u => u.key !== fromUnit).map(u => {
          const converted = baseValue * u.factor
          return (
            <div key={u.key} className="flex items-center justify-between px-2.5 py-1.5 rounded-lg hover:bg-white/[0.03]">
              <span className="text-[11px] text-muted-foreground/60">{u.label}</span>
              <span className="text-[13px] font-mono font-medium text-foreground tabular-nums">
                {u.symbol}{converted.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 })}
              </span>
            </div>
          )
        })}
      </div>
      <p className="text-[9px] text-muted-foreground/30 text-center">使用近似汇率换算，仅供参考</p>
    </div>
  )
}

// ── Main Toolbox Widget ─────────────────────────────────
const TABS = [
  { key: 'calc',     icon: Calculator,     label: '计算器' },
  { key: 'currency', icon: ArrowLeftRight, label: '汇率转换' },
  { key: 'unit',     icon: Scale,          label: '单位换算' },
] as const

export default function ToolboxWidget({ expanded = false }: { expanded?: boolean }) {
  const [open, setOpen] = useState(false)
  const [tab, setTab] = useState<string>('calc')
  const [hovered, setHovered] = useState(false)

  return (
    <div className="relative" onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}>
      <button
        onClick={() => setOpen(o => !o)}
        className={clsx(
          'flex items-center h-9 rounded-lg transition-colors duration-200 w-full',
          open
            ? 'bg-secondary text-foreground font-medium'
            : 'text-muted-foreground hover:text-foreground hover:bg-secondary/50'
        )}
      >
        <div className="w-9 h-9 flex items-center justify-center shrink-0">
          <Wrench size={16} />
        </div>
        {expanded && <span className="text-sm truncate pr-2">小工具</span>}
      </button>

      {/* Tooltip (collapsed only) */}
      {hovered && !open && !expanded && (
        <div className="absolute left-full top-1/2 -translate-y-1/2 z-50 pl-2.5 pointer-events-none">
          <div className="rounded-lg px-3 py-2 min-w-max"
            style={{ background: 'hsl(var(--card))', border: '1px solid hsl(var(--border))', boxShadow: '0 4px 16px rgba(0,0,0,0.15)' }}>
            <p className="text-xs font-semibold text-foreground">小工具</p>
            <p className="text-[11px] text-muted-foreground mt-0.5">计算器、汇率转换、资产单位换算</p>
          </div>
        </div>
      )}

      {/* Panel */}
      {open && (
        <div
          role="dialog"
          aria-label="小工具"
          className="absolute left-full bottom-0 z-50 w-[280px] rounded-xl overflow-hidden ml-2.5"
          style={{ background: 'hsl(var(--card))', border: '1px solid hsl(var(--border))', boxShadow: '0 8px 32px rgba(0,0,0,0.25)' }}>
          {/* Header */}
          <div className="flex items-center justify-between px-3 pt-3 pb-2">
            <div className="flex gap-0.5">
              {TABS.map(t => (
                <button key={t.key}
                  onClick={() => setTab(t.key)}
                  className={clsx('flex items-center gap-1 px-2.5 py-1 rounded-md text-[11px] font-medium transition-colors',
                    tab === t.key ? 'bg-secondary text-foreground' : 'text-muted-foreground hover:text-foreground hover:bg-secondary/50')}>
                  <t.icon size={12} />
                  {t.label}
                </button>
              ))}
            </div>
            <button onClick={() => setOpen(false)} className="text-muted-foreground hover:text-foreground p-0.5">
              <X size={14} />
            </button>
          </div>
          {/* Content */}
          <div className="px-3 pb-3">
            {tab === 'calc' && <CalcTab />}
            {tab === 'currency' && <CurrencyTab />}
            {tab === 'unit' && <UnitTab />}
          </div>
        </div>
      )}
    </div>
  )
}
