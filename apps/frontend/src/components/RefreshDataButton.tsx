import { RefreshCw, CheckCircle2, AlertTriangle } from 'lucide-react'
import { clsx } from 'clsx'
import { useSnapshotStatus, useTriggerSnapshot } from '@/services/hooks'

interface Props {
  compact?: boolean
  className?: string
}

function formatRelativeTime(iso?: string | null): string {
  if (!iso) return '尚未抓取'
  try {
    const dt = new Date(iso)
    const diffMs = Date.now() - dt.getTime()
    const diffMin = Math.floor(diffMs / 60000)
    if (diffMin < 1) return '刚刚更新'
    if (diffMin < 60) return `${diffMin} 分钟前`
    const diffH = Math.floor(diffMin / 60)
    if (diffH < 24) return `${diffH} 小时前`
    const diffD = Math.floor(diffH / 24)
    return `${diffD} 天前`
  } catch {
    return '—'
  }
}

const TRIGGER_LABEL: Record<string, string> = {
  startup: '服务启动',
  daily_noon: '每日 12:00',
  manual: '手动刷新',
  a_share_intraday: 'A股盘中',
  scheduler: '自动',
}

/**
 * 手动刷新市场数据按钮。
 *
 * 点击后调用 POST /api/v1/snapshots/trigger：
 *   - 抓取全部默认 symbol（SPX/NDX/VIX/HSI/HSTECH/CSI300/ZZ500/CYB/GOLD/SILVER/OIL/DXY/BTC/ETH/BNB/SOL）
 *   - 同时抓取衍生指标（Pizza Index / Fear&Greed / Polymarket Top 10）
 *
 * 同步阻塞直到完成（通常 30-60 秒）。显示上次抓取时间和耗时状态。
 */
export default function RefreshDataButton({ compact, className }: Props) {
  const { data: status } = useSnapshotStatus()
  const triggerMut = useTriggerSnapshot()

  const lastTs = status?.ts
  const triggeredBy = status?.triggered_by || undefined
  const saved = status?.saved ?? 0
  const failed = status?.failed ?? 0
  const indSaved = status?.indicators?.saved ?? 0
  const isRunning = triggerMut.isPending

  const handleClick = () => {
    if (isRunning) return
    triggerMut.mutate(undefined)
  }

  if (compact) {
    return (
      <button
        onClick={handleClick}
        disabled={isRunning}
        className={clsx(
          'inline-flex items-center gap-1.5 text-[11px] px-2 py-1 rounded border transition-colors',
          'border-border text-muted-foreground hover:text-foreground hover:bg-secondary/50',
          isRunning && 'opacity-60 cursor-wait',
          className,
        )}
        title={lastTs ? `上次更新：${formatRelativeTime(lastTs)}（${TRIGGER_LABEL[triggeredBy ?? ''] || triggeredBy || '—'}）` : '尚未抓取'}
      >
        <RefreshCw size={11} className={isRunning ? 'animate-spin' : ''} />
        <span>{isRunning ? '刷新中…' : '刷新数据'}</span>
      </button>
    )
  }

  return (
    <div className={clsx('flex items-center gap-3 px-3 py-2 bg-card border border-border rounded-lg', className)}>
      <button
        onClick={handleClick}
        disabled={isRunning}
        className={clsx(
          'inline-flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded font-medium transition-colors shrink-0',
          'bg-primary/10 text-primary border border-primary/30 hover:bg-primary/20',
          isRunning && 'opacity-60 cursor-wait',
        )}
      >
        <RefreshCw size={12} className={isRunning ? 'animate-spin' : ''} />
        {isRunning ? '抓取中…' : '一键刷新数据'}
      </button>

      <div className="flex-1 min-w-0 text-[11px] text-muted-foreground/70 flex items-center gap-3 flex-wrap">
        <span className="flex items-center gap-1">
          上次更新：
          <span className="text-foreground/80">{formatRelativeTime(lastTs)}</span>
          {triggeredBy && (
            <span className="text-muted-foreground/40">
              （{TRIGGER_LABEL[triggeredBy] || triggeredBy}）
            </span>
          )}
        </span>

        {saved > 0 && (
          <span className="flex items-center gap-1 text-emerald-400/70">
            <CheckCircle2 size={10} />
            行情 {saved} 条
          </span>
        )}
        {indSaved > 0 && (
          <span className="flex items-center gap-1 text-sky-400/70">
            <CheckCircle2 size={10} />
            指标 {indSaved} 条
          </span>
        )}
        {failed > 0 && (
          <span className="flex items-center gap-1 text-amber-400/70">
            <AlertTriangle size={10} />
            失败 {failed} 条
          </span>
        )}

        {triggerMut.isError && (
          <span className="text-rose-400/80">抓取失败，请稍后重试</span>
        )}
      </div>
    </div>
  )
}
