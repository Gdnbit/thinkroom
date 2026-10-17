import { Outlet, NavLink, useLocation } from 'react-router-dom'
import {
  Activity,
  Archive,
  BarChart2,
  Bell,
  BookOpen,
  ChevronDown,
  ClipboardCheck,
  Database,
  FlaskConical,
  Gauge,
  LayoutDashboard,
  Menu,
  MessageSquare,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Settings,
  ShieldCheck,
  Sun,
  Target,
  TrendingUp,
  Users,
  Wallet,
} from 'lucide-react'
import { clsx } from 'clsx'
import { useEffect, useMemo, useState } from 'react'
import { useAlerts, type Alert } from '@/services/hooks'
import { useTheme } from '@/hooks/useTheme'
import ToolboxWidget from '@/components/ToolboxWidget'

type NavItemConfig = {
  to: string
  icon: React.ElementType
  label: string
  description: string
}

type NavGroupConfig = {
  id: string
  label: string
  description: string
  items: NavItemConfig[]
}

const severityTone: Record<string, string> = {
  info: 'text-sky-400 bg-sky-400/5 border-sky-400/20',
  warning: 'text-amber-400 bg-amber-400/5 border-amber-400/20',
  success: 'text-emerald-400 bg-emerald-400/5 border-emerald-400/20',
  error: 'text-red-400 bg-red-400/5 border-red-400/20',
}

const navGroups: NavGroupConfig[] = [
  {
    id: 'closed-loop',
    label: '主要功能',
    description: '提问、确认资料、记录结果',
    items: [
      { to: '/chat', icon: MessageSquare, label: '问答', description: '上传资料、提问、查看回答与引用' },
      { to: '/knowledge', icon: BookOpen, label: '资料', description: '确认资料是否接收并可用于问答' },
      { to: '/review', icon: BarChart2, label: '复盘', description: '记录已保存判断的实际结果并回看偏差' },
    ],
  },
  {
    id: 'support',
    label: '研究工具',
    description: '低频研究与记录工具',
    items: [
      { to: '/watchlist', icon: Target, label: '观察池', description: '持续关注标的、主题和触发条件' },
      { to: '/debate', icon: ShieldCheck, label: '审议记录', description: '多模型委员会或多角色 agent 的讨论、分歧点和保留意见' },
      { to: '/speakers', icon: Users, label: '来源画像', description: '查看信息源、说话人、机构和可信度画像' },
      { to: '/strategies', icon: Archive, label: '策略候选库', description: '从资料识别出的候选策略必须经用户确认后才能成为正式策略' },
      { to: '/backtest', icon: TrendingUp, label: '策略回测', description: '用历史数据批量验证策略规则；后续应并入“验证与复盘”' },
      { to: '/fund-profiles', icon: Wallet, label: '基金配置', description: '基金画像、组合 basket 和配置假设' },
      { to: '/fund-reconciliation', icon: Activity, label: '基金核对', description: '核对持仓、净值、成本和配置差异' },
    ],
  },
  {
    id: 'advanced',
    label: '系统维护',
    description: '证据、数据质量、诊断与设置',
    items: [
      { to: '/audit', icon: ClipboardCheck, label: '证据审计', description: '检查入库、引用、全文、claims 和异常处理链路' },
      { to: '/datahub', icon: Database, label: '数据中心', description: '查看数据源、质量、成本和管道状态' },
      { to: '/quality-monitor', icon: Activity, label: '质量监控', description: '查看运行质量、异常和趋势' },
      { to: '/quality-evaluator', icon: Gauge, label: '质量评估', description: '评估 RAG、回答质量和引用质量' },
      { to: '/verification', icon: FlaskConical, label: '验证中心', description: '验证 API、模型、规则和边界情况' },
      { to: '/verification/dataflow', icon: Database, label: '数据流诊断', description: '维护入口：查看处理链路、依赖关系和状态；普通用户在问答页看简化轨迹' },
      { to: '/settings', icon: Settings, label: '系统设置', description: '配置系统连接、模型和本地偏好' },
      { to: '/dashboard', icon: LayoutDashboard, label: '旧工作台', description: '原总览入口，仅作为重构对照和高级查看保留' },
      { to: '/legacy-chat', icon: MessageSquare, label: '旧问答页', description: '原 Research Chat 入口，仅作为兼容页保留' },
    ],
  },
]

function Tooltip({ label, description }: { label: string; description: string }) {
  return (
    <div className="absolute left-full top-1/2 z-50 -translate-y-1/2 pl-2.5 pointer-events-none">
      <div className="min-w-56 rounded-lg border border-border bg-card px-3 py-2 shadow-sm">
        <p className="text-xs font-semibold text-foreground">{label}</p>
        <p className="mt-1 max-w-60 text-[11px] leading-relaxed text-muted-foreground">{description}</p>
      </div>
    </div>
  )
}

function AlertPanel({ onClose }: { onClose: () => void }) {
  const { data, isLoading, refetch } = useAlerts()
  const alerts = data?.alerts ?? []
  const storageKey = 'dismissed_alert_ids'
  const [localDismissed, setLocalDismissed] = useState<Set<string>>(() => {
    try {
      return new Set(JSON.parse(localStorage.getItem(storageKey) || '[]'))
    } catch {
      return new Set()
    }
  })

  const persist = (ids: Set<string>) => {
    setLocalDismissed(new Set(ids))
    localStorage.setItem(storageKey, JSON.stringify([...ids]))
    window.dispatchEvent(new StorageEvent('storage', { key: storageKey, newValue: JSON.stringify([...ids]) }))
  }

  const dismissAlert = async (id: string) => {
    const ids = new Set(localDismissed)
    ids.add(id)
    persist(ids)
    const baseUrl = import.meta.env.VITE_API_URL ?? ''
    await fetch(`${baseUrl}/api/v1/alerts/dismiss/${id}`, { method: 'POST' })
    refetch()
  }

  const dismissAll = async () => {
    const ids = new Set(localDismissed)
    alerts.forEach((alert) => ids.add(alert.id))
    persist(ids)
    const baseUrl = import.meta.env.VITE_API_URL ?? ''
    await fetch(`${baseUrl}/api/v1/alerts/dismiss-all`, { method: 'POST' })
    refetch()
  }

  const visibleAlerts = alerts.filter((alert) => !localDismissed.has(alert.id))
  const sortedAlerts = ['error', 'warning', 'info', 'success']
    .flatMap((severity) => visibleAlerts.filter((alert) => alert.severity === severity))

  return (
    <div
      role="dialog"
      aria-label="系统提醒"
      className="absolute bottom-4 left-14 z-50 flex max-h-[calc(100vh-5rem)] w-[22rem] flex-col overflow-hidden rounded-xl border border-border bg-card shadow-sm"
    >
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <div className="flex items-center gap-2">
          <Bell size={13} className="text-muted-foreground" />
          <p className="text-xs font-semibold text-foreground">系统提醒</p>
          {sortedAlerts.length > 0 && (
            <span className="rounded-full bg-primary/10 px-1.5 py-0.5 text-[10px] font-mono text-primary">
              {sortedAlerts.length}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          {sortedAlerts.length > 0 && (
            <button onClick={dismissAll} className="text-[11px] text-muted-foreground hover:text-foreground">
              全部忽略
            </button>
          )}
          <button onClick={onClose} className="rounded-md px-1.5 py-1 text-[11px] text-muted-foreground hover:bg-secondary hover:text-foreground">
            关闭
          </button>
        </div>
      </div>
      <div className="flex-1 space-y-2 overflow-y-auto p-3">
        {isLoading && <p className="py-8 text-center text-xs text-muted-foreground">正在加载提醒...</p>}
        {!isLoading && sortedAlerts.length === 0 && (
          <div className="flex flex-col items-center gap-2 py-10">
            <Bell size={18} className="text-muted-foreground/35" />
            <p className="text-xs text-muted-foreground">当前没有需要处理的提醒</p>
          </div>
        )}
        {sortedAlerts.map((alert: Alert) => (
          <div key={alert.id} className={clsx('relative rounded-lg border p-3', severityTone[alert.severity] ?? severityTone.info)}>
            <button
              onClick={() => dismissAlert(alert.id)}
              className="absolute right-2 top-2 rounded px-1.5 py-0.5 text-[10px] opacity-50 hover:bg-current/10 hover:opacity-80"
            >
              忽略
            </button>
            <div className="pr-10">
              <p className="text-xs font-semibold leading-snug">{alert.title}</p>
              <p className="mt-1 text-[11px] leading-relaxed opacity-75">{alert.message}</p>
              <p className="mt-2 text-[10px] font-mono opacity-40">{alert.created_at.slice(0, 16).replace('T', ' ')}</p>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

const itemBase = 'flex h-9 w-full items-center rounded-lg transition-colors duration-200'
const itemActive = `${itemBase} bg-secondary text-foreground font-medium`
const itemIdle = `${itemBase} text-muted-foreground hover:bg-secondary/70 hover:text-foreground`

function NavItem({ item, expanded }: { item: NavItemConfig; expanded: boolean }) {
  const [hovered, setHovered] = useState(false)
  const Icon = item.icon

  return (
    <div className="relative" onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}>
      <NavLink
        to={item.to}
        aria-label={!expanded ? item.label : undefined}
        title={!expanded ? item.label : undefined}
        className={({ isActive }) => (isActive ? itemActive : itemIdle)}
      >
        <div className="flex h-9 w-9 shrink-0 items-center justify-center">
          <Icon size={16} />
        </div>
        {expanded && <span className="truncate pr-2 text-sm">{item.label}</span>}
      </NavLink>
      {hovered && !expanded && <Tooltip label={item.label} description={item.description} />}
    </div>
  )
}

function NavGroup({ group, expanded, defaultOpen }: { group: NavGroupConfig; expanded: boolean; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen)

  if (!expanded) {
    return (
      <div className="space-y-0.5 border-t border-border/50 pt-1">
        {group.items.map((item) => (
          <NavItem key={item.to} item={item} expanded={false} />
        ))}
      </div>
    )
  }

  return (
    <section className="border-t border-border/50 pt-2">
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        className="mb-1 flex w-full items-center gap-2 rounded-lg px-2 py-1 text-left text-[11px] font-medium uppercase tracking-[0.16em] text-muted-foreground/70 hover:bg-secondary/50 hover:text-muted-foreground"
      >
        <ChevronDown size={12} className={clsx('transition-transform', open && 'rotate-180')} />
        <span className="truncate">{group.label}</span>
      </button>
      {open && (
        <div className="space-y-0.5">
          {group.items.map((item) => (
            <NavItem key={item.to} item={item} expanded={expanded} />
          ))}
        </div>
      )}
    </section>
  )
}

export default function Layout() {
  const location = useLocation()
  const [expanded, setExpanded] = useState(() => {
    try {
      return localStorage.getItem('sidebar_expanded') !== 'false'
    } catch {
      return true
    }
  })
  const [showAlerts, setShowAlerts] = useState(false)
  const [showMoreNav, setShowMoreNav] = useState(false)
  const { theme, toggle: toggleTheme } = useTheme()
  const { data: alertData } = useAlerts()
  const [dismissedIds, setDismissedIds] = useState<Set<string>>(() => {
    try {
      return new Set(JSON.parse(localStorage.getItem('dismissed_alert_ids') || '[]'))
    } catch {
      return new Set()
    }
  })

  useEffect(() => {
    const syncDismissed = () => {
      try {
        setDismissedIds(new Set(JSON.parse(localStorage.getItem('dismissed_alert_ids') || '[]')))
      } catch {
        setDismissedIds(new Set())
      }
    }
    window.addEventListener('storage', syncDismissed)
    return () => window.removeEventListener('storage', syncDismissed)
  }, [])

  useEffect(() => {
    const collapseForViewport = () => {
      if (window.innerWidth < 1024 || (location.pathname === '/chat' && window.innerWidth < 1440)) {
        setExpanded(false)
      }
    }
    collapseForViewport()
    window.addEventListener('resize', collapseForViewport)
    return () => window.removeEventListener('resize', collapseForViewport)
  }, [location.pathname])

  const alertCount = useMemo(
    () => (alertData?.alerts ?? []).filter((alert) => !dismissedIds.has(alert.id)).length,
    [alertData?.alerts, dismissedIds],
  )

  const sideWidth = expanded ? 'w-[12rem]' : 'w-[3.25rem]'

  const toggleSidebar = () => {
    setExpanded((prev) => {
      const next = !prev
      localStorage.setItem('sidebar_expanded', String(next))
      return next
    })
  }

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      <a href="#main-content" className="skip-link">跳到主内容</a>

      <nav
        aria-label="主导航"
        className={clsx(sideWidth, 'flex shrink-0 flex-col px-2 py-3 transition-[width] duration-200')}
        style={{ background: 'hsl(var(--muted))', borderRight: '1px solid hsl(var(--border))' }}
      >
        <div className="mb-3 flex h-10 items-center">
          <NavLink to="/chat" className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground">
            <span className="font-serif text-sm font-semibold">RO</span>
          </NavLink>
          {expanded && (
            <div className="ml-2.5 min-w-0">
              <p className="truncate font-serif text-sm font-semibold leading-none text-foreground">私承 Sicheng</p>
              <p className="mt-1 truncate text-[11px] leading-none text-muted-foreground">证据驱动的决策工作台</p>
            </div>
          )}
          {expanded && (
            <button
              onClick={toggleTheme}
              className="ml-auto grid h-8 w-8 place-items-center rounded-lg border border-border text-muted-foreground hover:bg-secondary hover:text-foreground"
              aria-label={theme === 'dark' ? '切换到亮色模式' : '切换到暗色模式'}
              title={theme === 'dark' ? '切换到亮色模式' : '切换到暗色模式'}
            >
              {theme === 'dark' ? <Sun size={15} className="text-amber-400/80" /> : <Moon size={15} className="text-slate-500" />}
            </button>
          )}
        </div>

        <div className="flex-1 space-y-2 overflow-y-auto">
          <div className="space-y-0.5">
            {navGroups[0].items.map((item) => (
              <NavItem key={item.to} item={item} expanded={expanded} />
            ))}
          </div>

          {expanded && (
            <section className="border-t border-border/50 pt-2">
              <button
                type="button"
                onClick={() => setShowMoreNav((prev) => !prev)}
                className="flex h-9 w-full items-center rounded-lg text-muted-foreground transition-colors duration-200 hover:bg-secondary/70 hover:text-foreground"
              >
                <div className="flex h-9 w-9 shrink-0 items-center justify-center">
                  <Menu size={16} />
                </div>
                <span className="truncate pr-2 text-sm">更多功能</span>
                <ChevronDown size={12} className={clsx('ml-auto mr-2 transition-transform', showMoreNav && 'rotate-180')} />
              </button>
              {showMoreNav && (
                <div className="mt-2 space-y-2">
                  {navGroups.slice(1).map((group) => (
                    <NavGroup key={group.id} group={group} expanded={expanded} defaultOpen={false} />
                  ))}
                  <div className="space-y-0.5 border-t border-border/50 pt-2">
                    <div className="relative">
                      <button onClick={() => setShowAlerts((prev) => !prev)} className={clsx(showAlerts ? itemActive : itemIdle, 'relative')}>
                        <div className="flex h-9 w-9 shrink-0 items-center justify-center"><Bell size={16} /></div>
                        <span className="truncate pr-2 text-sm">系统提醒</span>
                        {alertCount > 0 && <span className="ml-auto mr-2 h-4 min-w-4 rounded-full bg-red-500 px-1 text-center text-[9px] font-bold leading-4 text-white">{alertCount > 9 ? '9+' : alertCount}</span>}
                      </button>
                      {showAlerts && <AlertPanel onClose={() => setShowAlerts(false)} />}
                    </div>
                    <ToolboxWidget expanded />
                  </div>
                </div>
              )}
            </section>
          )}
        </div>

        <div className="mt-3 space-y-0.5 border-t border-border pt-2">
          <button onClick={toggleSidebar} className={itemIdle} aria-label={expanded ? '收起侧边栏' : '展开侧边栏'}>
            <div className="flex h-9 w-9 shrink-0 items-center justify-center">
              {expanded ? <PanelLeftClose size={16} /> : <PanelLeftOpen size={16} />}
            </div>
            {expanded && <span className="truncate pr-2 text-sm">收起侧边栏</span>}
          </button>

        </div>
      </nav>

      <main id="main-content" tabIndex={-1} className="flex-1 overflow-auto bg-background">
        <Outlet />
      </main>
    </div>
  )
}
