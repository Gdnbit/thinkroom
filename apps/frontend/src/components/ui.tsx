/**
 * 通用 UI 组件
 * - EmptyState     空状态占位
 * - ErrorState     错误状态
 * - LoadingRows    骨架屏行
 * - Pagination     分页控件
 * - StatusBadge    状态徽章
 */
import { clsx } from 'clsx'
import { Loader2, AlertTriangle, Inbox, ChevronLeft, ChevronRight } from 'lucide-react'

// ── EmptyState ────────────────────────────────────────────
export function EmptyState({
  icon: Icon = Inbox,
  title = '暂无数据',
  description,
  action,
}: {
  icon?: React.ElementType
  title?: string
  description?: string
  action?: React.ReactNode
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border bg-card px-5 py-12 text-center">
      <span className="mb-3 grid h-10 w-10 place-items-center rounded-lg bg-secondary text-muted-foreground">
        <Icon size={20} aria-hidden="true" />
      </span>
      <p className="text-sm font-medium text-foreground mb-1">{title}</p>
      {description && <p className="text-xs text-muted-foreground max-w-xs">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}

// ── ErrorState ────────────────────────────────────────────
export function ErrorState({
  message = '加载失败，请刷新重试',
  onRetry,
}: {
  message?: string
  onRetry?: () => void
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-border bg-card px-5 py-10 text-center">
      <AlertTriangle size={20} className="status-error mb-3" aria-hidden="true" />
      <p className="status-error mb-3 text-sm" role="alert">{message}</p>
      {onRetry && (
        <button type="button" onClick={onRetry} className="btn-outline">
          重试
        </button>
      )}
    </div>
  )
}

// ── LoadingRows ───────────────────────────────────────────
export function LoadingRows({ rows = 4, className }: { rows?: number; className?: string }) {
  return (
    <div role="status" aria-label="加载中" className={clsx('space-y-2 animate-pulse', className)}>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} aria-hidden="true" className="h-14 rounded-lg bg-secondary" />
      ))}
    </div>
  )
}

// ── InlineLoader ──────────────────────────────────────────
export function InlineLoader({ text = '加载中…' }: { text?: string }) {
  return (
    <div role="status" className="flex items-center justify-center gap-2 py-10 text-xs text-muted-foreground">
      <Loader2 size={14} className="animate-spin" />{text}
    </div>
  )
}

// ── Pagination ────────────────────────────────────────────
export function Pagination({
  page,
  pageSize,
  total,
  onChange,
}: {
  page: number
  pageSize: number
  total: number
  onChange: (page: number) => void
}) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  if (totalPages <= 1) return null

  return (
    <nav aria-label="分页" className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3">
      <p className="text-xs text-muted-foreground">
        共 {total} 条，第 {page}/{totalPages} 页
      </p>
      <div className="flex items-center gap-1">
        <button
          type="button"
          aria-label="上一页"
          onClick={() => onChange(page - 1)}
          disabled={page <= 1}
          className="w-7 h-7 flex items-center justify-center rounded-lg border border-border text-muted-foreground disabled:opacity-40 hover:text-foreground hover:border-primary transition-colors">
          <ChevronLeft size={13} />
        </button>
        {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
          let p: number
          if (totalPages <= 5) { p = i + 1 }
          else if (page <= 3) { p = i + 1 }
          else if (page >= totalPages - 2) { p = totalPages - 4 + i }
          else { p = page - 2 + i }
          return (
            <button key={p} type="button" aria-label={`第 ${p} 页`} aria-current={p === page ? 'page' : undefined} onClick={() => onChange(p)}
              className={clsx(
                'w-7 h-7 flex items-center justify-center rounded-lg text-xs transition-colors',
                p === page
                  ? 'bg-primary text-primary-foreground'
                  : 'border border-border text-muted-foreground hover:text-foreground hover:border-primary'
              )}>
              {p}
            </button>
          )
        })}
        <button
          type="button"
          aria-label="下一页"
          onClick={() => onChange(page + 1)}
          disabled={page >= totalPages}
          className="w-7 h-7 flex items-center justify-center rounded-lg border border-border text-muted-foreground disabled:opacity-40 hover:text-foreground hover:border-primary transition-colors">
          <ChevronRight size={13} />
        </button>
      </div>
    </nav>
  )
}

// ── StatusBadge ───────────────────────────────────────────
const STATUS_STYLES: Record<string, string> = {
  active:    'text-emerald-400 bg-emerald-400/10 border-emerald-400/30',
  expired:   'text-slate-400  bg-slate-400/10  border-slate-400/30',
  invalidated:'text-red-400   bg-red-400/10    border-red-400/30',
  pending:   'text-amber-400  bg-amber-400/10  border-amber-400/30',
  completed: 'text-sky-400    bg-sky-400/10    border-sky-400/30',
  failed:    'text-red-400    bg-red-400/10    border-red-400/30',
  running:   'text-blue-400   bg-blue-400/10   border-blue-400/30',
}

export function StatusBadge({ status, label }: { status: string; label?: string }) {
  return (
    <span className={clsx(
      'inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border',
      STATUS_STYLES[status] ?? 'text-muted-foreground bg-secondary border-border'
    )}>
      {label ?? status}
    </span>
  )
}
