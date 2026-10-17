import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Compass, Play, CheckCircle2, XCircle, Loader2, Sparkles } from 'lucide-react'
import { clsx } from 'clsx'
import api from '@/services/api'

interface AuditCase {
  message: string
  description: string
  expected: string
  actual: string
  passed: boolean
  reason: string
  confidence: number
  cost_estimate_usd: number
  intent_mode: string
  intent_confidence: number
  assets: string[]
}

interface AuditResponse {
  total: number
  passed: number
  failed: number
  pass_rate: number
  duration_ms: number
  cases: AuditCase[]
}

interface PreviewResponse {
  routing: {
    route: string
    reason: string
    confidence: number
    cost_estimate_usd: number
    intent_mode: string
    intent_confidence: number
    assets: string[]
    force_source: string | null
    can_upgrade_to: string[]
    can_downgrade_to: string | null
  }
  cleaned_message: string
  intent: {
    mode: string
    assets: string[]
    themes: string[]
    time_horizon: string
    needs_private_knowledge: boolean
    needs_debate: boolean
    confidence: number
    source: string
  }
}

const ROUTE_COLOR: Record<string, string> = {
  chat: 'text-sky-400 bg-sky-400/10 border-sky-400/30',
  briefing: 'text-violet-400 bg-violet-400/10 border-violet-400/30',
  debate: 'text-amber-400 bg-amber-400/10 border-amber-400/30',
}

const ROUTE_LABEL: Record<string, string> = {
  chat: '快速问答',
  briefing: '深度研究',
  debate: '委员会辩论',
}

function RouteBadge({ route }: { route: string }) {
  return (
    <span className={clsx('text-[10px] px-2 py-0.5 rounded border font-medium', ROUTE_COLOR[route] ?? 'text-muted-foreground border-border')}>
      {ROUTE_LABEL[route] ?? route}
    </span>
  )
}

export default function ModeRouterPanel() {
  const [audit, setAudit] = useState<AuditResponse | null>(null)
  const [previewInput, setPreviewInput] = useState('')
  const [preview, setPreview] = useState<PreviewResponse | null>(null)

  const auditMutation = useMutation({
    mutationFn: () => api.get<AuditResponse>('/api/v1/chat/mode-router/audit').then(r => r.data),
    onSuccess: setAudit,
  })

  const previewMutation = useMutation({
    mutationFn: (message: string) =>
      api.post<PreviewResponse>('/api/v1/chat/mode-router/preview', { message }).then(r => r.data),
    onSuccess: setPreview,
  })

  const handlePreview = () => {
    if (!previewInput.trim()) return
    previewMutation.mutate(previewInput.trim())
  }

  return (
    <div className="space-y-4">
      {/* ── 头部说明 ── */}
      <div className="rounded-2xl border border-border bg-card p-4">
        <div className="flex items-center gap-2 mb-2">
          <Compass size={16} className="text-primary" />
          <h2 className="text-sm font-semibold text-foreground">G20 · 三模式融合路由审计</h2>
        </div>
        <p className="text-xs text-muted-foreground leading-relaxed">
          验证 <code className="text-primary/80">mode_router</code> 的决策行为：10 条覆盖所有 7 档路径的用例，
          以及任意消息的实时路由预览。
          路由优先级：<code className="text-foreground/70">force</code> → <code className="text-foreground/70">斜杠</code> → <code className="text-foreground/70">needs_debate</code> → <code className="text-foreground/70">决策词+资产</code> → <code className="text-foreground/70">深度关键词</code> → <code className="text-foreground/70">intent.mode 高置信</code> → <code className="text-foreground/70">默认 chat</code>。
        </p>
      </div>

      {/* ── 一键审计 ── */}
      <div className="rounded-2xl border border-border bg-card p-4 space-y-3">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-medium text-foreground">一键审计 · 10 条预置用例</h3>
            <p className="text-[11px] text-muted-foreground mt-0.5">覆盖 chat / briefing / debate / 斜杠 / 低置信兜底</p>
          </div>
          <button
            onClick={() => auditMutation.mutate()}
            disabled={auditMutation.isPending}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-primary text-primary-foreground text-xs font-medium disabled:opacity-50 hover:opacity-90 transition-opacity"
          >
            {auditMutation.isPending ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
            {auditMutation.isPending ? '审计中…' : '运行审计'}
          </button>
        </div>

        {auditMutation.isError && (
          <div className="text-xs text-red-400 border border-red-400/30 rounded-lg px-3 py-2">
            审计请求失败：{String(auditMutation.error)}
          </div>
        )}

        {audit && (
          <>
            <div className="grid grid-cols-4 gap-2">
              <Metric label="总用例" value={`${audit.total}`} />
              <Metric label="通过" value={`${audit.passed}`} color="text-emerald-400" />
              <Metric label="失败" value={`${audit.failed}`} color={audit.failed > 0 ? 'text-red-400' : 'text-muted-foreground'} />
              <Metric label="通过率" value={`${Math.round(audit.pass_rate * 100)}%`} color={audit.pass_rate === 1 ? 'text-emerald-400' : 'text-amber-400'} />
            </div>
            <p className="text-[10px] text-muted-foreground">耗时 {audit.duration_ms}ms（包含 LLM 意图识别）</p>
            <div className="space-y-1.5">
              {audit.cases.map((c, i) => (
                <div
                  key={i}
                  className={clsx(
                    'rounded-lg border p-2.5',
                    c.passed ? 'border-emerald-400/20 bg-emerald-400/5' : 'border-red-400/40 bg-red-400/5'
                  )}
                >
                  <div className="flex items-center gap-2 mb-1.5">
                    {c.passed
                      ? <CheckCircle2 size={12} className="text-emerald-400 shrink-0" />
                      : <XCircle size={12} className="text-red-400 shrink-0" />}
                    <span className="text-xs text-foreground truncate font-mono">{c.message}</span>
                    <span className="text-[10px] text-muted-foreground ml-auto shrink-0">{c.description}</span>
                  </div>
                  <div className="flex items-center gap-2 text-[10px]">
                    <span className="text-muted-foreground">预期</span>
                    <RouteBadge route={c.expected} />
                    <span className="text-muted-foreground">实际</span>
                    <RouteBadge route={c.actual} />
                    <span className="text-muted-foreground ml-auto">
                      intent=<span className="text-foreground/80">{c.intent_mode}</span>
                      {' '}conf=<span className="text-foreground/80">{c.intent_confidence.toFixed(2)}</span>
                      {' '}~${c.cost_estimate_usd.toFixed(3)}
                    </span>
                  </div>
                  {c.reason && (
                    <p className="text-[10px] text-muted-foreground/80 mt-1 pl-4 italic">{c.reason}</p>
                  )}
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      {/* ── 自定义预览 ── */}
      <div className="rounded-2xl border border-border bg-card p-4 space-y-3">
        <div className="flex items-center gap-2">
          <Sparkles size={14} className="text-primary" />
          <h3 className="text-sm font-medium text-foreground">自定义预览</h3>
          <span className="text-[10px] text-muted-foreground">输入任意消息，查看决策过程（不落库）</span>
        </div>

        <div className="flex gap-2">
          <input
            value={previewInput}
            onChange={e => setPreviewInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') handlePreview() }}
            placeholder="例如：BTC 可以重仓吗、/debate ETH、美联储下次加息概率"
            className="flex-1 bg-secondary border border-border rounded-lg px-3 py-2 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary"
          />
          <button
            onClick={handlePreview}
            disabled={previewMutation.isPending || !previewInput.trim()}
            className="px-3 py-2 rounded-lg bg-primary text-primary-foreground text-xs font-medium disabled:opacity-50 hover:opacity-90 transition-opacity"
          >
            {previewMutation.isPending ? <Loader2 size={12} className="animate-spin" /> : '预览'}
          </button>
        </div>

        {previewMutation.isError && (
          <div className="text-xs text-red-400">预览失败：{String(previewMutation.error)}</div>
        )}

        {preview && (
          <div className="rounded-lg border border-border bg-background p-3 space-y-2">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-[10px] text-muted-foreground">路由</span>
              <RouteBadge route={preview.routing.route} />
              <span className="text-[10px] text-muted-foreground ml-2">成本</span>
              <span className="text-[10px] font-mono text-foreground/80">~${preview.routing.cost_estimate_usd.toFixed(3)}</span>
              <span className="text-[10px] text-muted-foreground ml-2">置信</span>
              <span className="text-[10px] font-mono text-foreground/80">{preview.routing.confidence.toFixed(2)}</span>
              {preview.routing.force_source && (
                <span className="text-[10px] px-1.5 py-0.5 rounded border border-primary/40 text-primary bg-primary/5">
                  {preview.routing.force_source === 'slash' ? '斜杠强制' : '手动指定'}
                </span>
              )}
            </div>

            <p className="text-[11px] text-foreground/80">{preview.routing.reason}</p>

            {preview.cleaned_message !== previewInput && (
              <p className="text-[10px] text-muted-foreground">
                去斜杠后：<code className="text-foreground/70">{preview.cleaned_message}</code>
              </p>
            )}

            <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[10px] pt-2 border-t border-border">
              <InfoRow label="intent.mode" value={preview.intent.mode} />
              <InfoRow label="intent.confidence" value={preview.intent.confidence.toFixed(2)} />
              <InfoRow label="intent.source" value={preview.intent.source} />
              <InfoRow label="time_horizon" value={preview.intent.time_horizon} />
              <InfoRow label="needs_debate" value={preview.intent.needs_debate ? '是' : '否'} />
              <InfoRow label="needs_private" value={preview.intent.needs_private_knowledge ? '是' : '否'} />
              <InfoRow label="资产" value={preview.intent.assets.join(', ') || '—'} />
              <InfoRow label="主题" value={preview.intent.themes.join(', ') || '—'} />
            </div>

            {(preview.routing.can_upgrade_to.length > 0 || preview.routing.can_downgrade_to) && (
              <div className="flex items-center gap-2 text-[10px] text-muted-foreground pt-2 border-t border-border">
                {preview.routing.can_upgrade_to.length > 0 && (
                  <span>可升级到：{preview.routing.can_upgrade_to.map(r => ROUTE_LABEL[r] ?? r).join(' / ')}</span>
                )}
                {preview.routing.can_downgrade_to && (
                  <span className="ml-auto">可降级到：{ROUTE_LABEL[preview.routing.can_downgrade_to] ?? preview.routing.can_downgrade_to}</span>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function Metric({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="rounded-lg border border-border bg-background p-2.5">
      <p className="text-[10px] text-muted-foreground">{label}</p>
      <p className={clsx('text-lg font-semibold font-mono mt-0.5', color ?? 'text-foreground')}>{value}</p>
    </div>
  )
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-muted-foreground shrink-0">{label}</span>
      <span className="text-foreground/80 font-mono truncate">{value}</span>
    </div>
  )
}
