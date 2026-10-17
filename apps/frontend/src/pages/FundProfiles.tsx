/**
 * G2 · 基金配置页面
 *
 * 让用户在 UI 上管理 FundProfile（无需直接改数据库）：
 *  列出全部 active 基金
 *  点击 ➕ 添加新基金 → 弹出表单
 *  点击行打开"编辑"模态（含 basket 数组动态增删）
 *  点击 🗑 软删（status=archived）
 *  顶部显示 archived 计数 + 切换按钮看归档列表
 */
import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Wallet, Plus, Edit3, Archive, ArchiveRestore, Save, X, RefreshCw, AlertCircle,
  Trash2, Loader2, Info,
} from 'lucide-react'
import api from '@/services/api'

// ── 类型 ──────────────────────────────────────────────

interface BasketItem { symbol: string; weight: number }

interface FundProfile {
  id: string
  fund_code: string
  fund_name: string
  market: string
  last_nav: number | null
  last_nav_date: string | null
  basket: BasketItem[]
  beta: number
  status: 'active' | 'archived'
  notes: string | null
  created_at: string | null
  updated_at: string | null
}

type FundProfileForm = Omit<FundProfile, 'id' | 'created_at' | 'updated_at'>

const EMPTY_FORM: FundProfileForm = {
  fund_code: '',
  fund_name: '',
  market: 'CN',
  last_nav: null,
  last_nav_date: null,
  basket: [],
  beta: 1.0,
  status: 'active',
  notes: null,
}

// ── Hooks ──────────────────────────────────────────────

function useProfiles(status: 'active' | 'archived') {
  return useQuery({
    queryKey: ['fund-profiles', status],
    queryFn: () =>
      api.get<FundProfile[]>('/api/v1/fund-profiles', { params: { status } })
        .then(r => r.data),
    refetchInterval: 60000,
  })
}

function useCreateProfile() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (form: FundProfileForm) => api.post('/api/v1/fund-profiles', form).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['fund-profiles'] }),
  })
}

function useUpdateProfile() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ code, form }: { code: string; form: Omit<FundProfileForm, 'fund_code'> }) =>
      api.put(`/api/v1/fund-profiles/${code}`, form).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['fund-profiles'] }),
  })
}

function useDeleteProfile() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (code: string) => api.delete(`/api/v1/fund-profiles/${code}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['fund-profiles'] }),
  })
}

// ── 主组件 ────────────────────────────────────────────

export default function FundProfiles() {
  const [showArchived, setShowArchived] = useState(false)
  const [editing, setEditing] = useState<FundProfile | null>(null)
  const [creating, setCreating] = useState(false)

  const active = useProfiles('active')
  const archived = useProfiles('archived')

  const list = showArchived ? archived.data : active.data
  const refetchAll = () => { active.refetch(); archived.refetch() }

  const totalBasketWeight = (basket: BasketItem[]) =>
    basket.reduce((sum, item) => sum + item.weight, 0)

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <h1 className="text-2xl font-semibold flex items-center gap-2">
            <Wallet className="w-5 h-5 text-primary" />
            基金配置
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            管理你持有的场外基金的成分指数权重 / beta / 锚点净值；scheduler 用这些字段做日内估值
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowArchived(!showArchived)}
            className="px-3 py-1.5 rounded-md bg-secondary hover:bg-secondary/80 text-sm font-medium flex items-center gap-1.5"
          >
            {showArchived ? <ArchiveRestore className="w-3.5 h-3.5" /> : <Archive className="w-3.5 h-3.5" />}
            {showArchived ? `归档 (${archived.data?.length ?? 0})` : `运行中 (${active.data?.length ?? 0})`}
          </button>
          <button
            onClick={() => { setCreating(true); setEditing(null) }}
            className="px-3 py-1.5 rounded-md bg-primary hover:bg-primary/90 text-primary-foreground text-sm font-medium flex items-center gap-1.5"
          >
            <Plus className="w-3.5 h-3.5" />
            新增基金
          </button>
          <button
            onClick={refetchAll}
            className="p-1.5 rounded-md hover:bg-secondary text-muted-foreground hover:text-foreground"
            title="立即刷新"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* 提示 */}
      <div className="rounded-lg bg-blue-500/5 border border-blue-500/20 px-4 py-2.5 text-xs text-muted-foreground flex items-start gap-2">
        <Info className="w-4 h-4 text-blue-400 shrink-0 mt-0.5" />
        <div className="space-y-1">
          <p>
            <strong className="text-foreground">basket 权重总和</strong> 应在 <code className="text-blue-400">[0.5, 1.5]</code> 范围（基金通常能 80-120% 跟踪指数组合）；
            <strong className="text-foreground"> beta</strong> 一般在 <code className="text-blue-400">[0.5, 1.5]</code>，少数高弹性基金可达 2+
          </p>
          <p>
            <strong className="text-foreground">last_nav 锚点</strong> 从基金公司官网/天天基金抄一次，估值会基于此 + 当日指数变化推算；
            <strong className="text-foreground"> EOD 对账</strong> 自动同步最新真实净值，长期跟着走
          </p>
        </div>
      </div>

      {/* 列表 */}
      {(active.isLoading || archived.isLoading) && <Skeleton h={300} />}
      {list && list.length === 0 && (
        <div className="rounded-xl bg-card border border-dashed border-border p-12 text-center">
          <Wallet className="w-12 h-12 text-muted-foreground/40 mx-auto mb-3" />
          <p className="text-sm text-muted-foreground mb-4">
            {showArchived ? '没有归档的基金' : '还没添加基金。点击右上角"新增基金"开始'}
          </p>
        </div>
      )}
      {list && list.length > 0 && (
        <div className="rounded-xl bg-card border border-border overflow-hidden">
          <table className="w-full text-xs">
            <thead className="bg-secondary/30 text-muted-foreground border-b border-border">
              <tr>
                <th className="text-left py-2.5 px-3">代码</th>
                <th className="text-left py-2.5 px-3">名称</th>
                <th className="text-left py-2.5 px-3">市场</th>
                <th className="text-right py-2.5 px-3">锚点净值</th>
                <th className="text-right py-2.5 px-3">beta</th>
                <th className="text-left py-2.5 px-3">basket（权重总和）</th>
                <th className="text-right py-2.5 px-3">操作</th>
              </tr>
            </thead>
            <tbody>
              {list.map(p => (
                <tr key={p.id} className="border-b border-border/40 hover:bg-secondary/20 transition-colors">
                  <td className="py-2.5 px-3 font-mono">{p.fund_code}</td>
                  <td className="py-2.5 px-3 max-w-xs truncate" title={p.fund_name}>{p.fund_name}</td>
                  <td className="py-2.5 px-3">{p.market}</td>
                  <td className="py-2.5 px-3 text-right tabular-nums">
                    {p.last_nav?.toFixed(4) ?? '—'}
                    {p.last_nav_date && (
                      <span className="text-[10px] text-muted-foreground block">{p.last_nav_date}</span>
                    )}
                  </td>
                  <td className="py-2.5 px-3 text-right tabular-nums">{p.beta.toFixed(2)}</td>
                  <td className="py-2.5 px-3">
                    <div className="flex flex-wrap gap-1 mb-0.5">
                      {p.basket.slice(0, 3).map((b, i) => (
                        <span key={i} className="inline-flex items-baseline gap-0.5 px-1.5 py-0.5 rounded-md bg-secondary/60 text-[10px]">
                          <span className="font-mono">{b.symbol}</span>
                          <span className="text-muted-foreground">{(b.weight * 100).toFixed(0)}%</span>
                        </span>
                      ))}
                      {p.basket.length > 3 && (
                        <span className="text-[10px] text-muted-foreground">+{p.basket.length - 3}</span>
                      )}
                      {p.basket.length === 0 && (
                        <span className="text-[10px] text-amber-500">⚠ 未配置</span>
                      )}
                    </div>
                    <span className="text-[10px] text-muted-foreground">
                      Σ = {(totalBasketWeight(p.basket) * 100).toFixed(1)}%
                    </span>
                  </td>
                  <td className="py-2.5 px-3 text-right">
                    <div className="flex items-center justify-end gap-1">
                      <button
                        onClick={() => { setEditing(p); setCreating(false) }}
                        className="p-1 rounded hover:bg-secondary text-muted-foreground hover:text-foreground"
                        title="编辑"
                      >
                        <Edit3 className="w-3.5 h-3.5" />
                      </button>
                      {p.status === 'active' && (
                        <DeleteButton fundCode={p.fund_code} fundName={p.fund_name} />
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* 创建 / 编辑模态 */}
      {(creating || editing) && (
        <ProfileModal
          initial={editing ? { ...editing } : null}
          onClose={() => { setCreating(false); setEditing(null) }}
        />
      )}
    </div>
  )
}

// ── 删除按钮（含确认） ──────────────────────────────

function DeleteButton({ fundCode, fundName }: { fundCode: string; fundName: string }) {
  const [confirming, setConfirming] = useState(false)
  const del = useDeleteProfile()

  if (confirming) {
    return (
      <div className="flex items-center gap-1">
        <span className="text-[10px] text-amber-500">归档 "{fundName}"?</span>
        <button
          onClick={() => del.mutate(fundCode, { onSuccess: () => setConfirming(false) })}
          disabled={del.isPending}
          className="px-1.5 py-0.5 rounded bg-red-500/15 hover:bg-red-500/25 text-red-500 text-[10px] disabled:opacity-50"
        >
          {del.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : '确认'}
        </button>
        <button
          onClick={() => setConfirming(false)}
          className="px-1.5 py-0.5 rounded hover:bg-secondary text-muted-foreground text-[10px]"
        >
          取消
        </button>
      </div>
    )
  }

  return (
    <button
      onClick={() => setConfirming(true)}
      className="p-1 rounded hover:bg-red-500/15 text-muted-foreground hover:text-red-500"
      title="归档（软删）"
    >
      <Trash2 className="w-3.5 h-3.5" />
    </button>
  )
}

// ── 模态：创建 / 编辑 ───────────────────────────────

function ProfileModal({ initial, onClose }: { initial: FundProfile | null; onClose: () => void }) {
  const isEdit = !!initial
  const [form, setForm] = useState<FundProfileForm>(
    initial
      ? {
          fund_code: initial.fund_code,
          fund_name: initial.fund_name,
          market: initial.market,
          last_nav: initial.last_nav,
          last_nav_date: initial.last_nav_date,
          basket: [...initial.basket],
          beta: initial.beta,
          status: initial.status,
          notes: initial.notes,
        }
      : { ...EMPTY_FORM }
  )
  const [error, setError] = useState<string | null>(null)
  const create = useCreateProfile()
  const update = useUpdateProfile()
  const isPending = create.isPending || update.isPending

  const totalWeight = useMemo(
    () => form.basket.reduce((sum, item) => sum + item.weight, 0),
    [form.basket]
  )
  const weightStatus =
    form.basket.length === 0 ? 'empty'
    : totalWeight < 0.5 || totalWeight > 1.5 ? 'invalid'
    : 'ok'

  const updateBasketItem = (index: number, patch: Partial<BasketItem>) => {
    const next = [...form.basket]
    next[index] = { ...next[index], ...patch }
    setForm({ ...form, basket: next })
  }

  const addBasketItem = () =>
    setForm({ ...form, basket: [...form.basket, { symbol: '', weight: 0 }] })

  const removeBasketItem = (index: number) =>
    setForm({ ...form, basket: form.basket.filter((_, i) => i !== index) })

  const handleSubmit = async () => {
    setError(null)
    if (!form.fund_code) { setError('基金代码必填'); return }
    if (!form.fund_name) { setError('基金名称必填'); return }
    if (weightStatus === 'invalid') { setError('basket 权重总和应在 [0.5, 1.5]'); return }

    try {
      if (isEdit) {
        // 排除 fund_code（PUT body 不含）
        const { fund_code: _ignore, ...formNoCode } = form
        void _ignore
        await update.mutateAsync({ code: form.fund_code, form: formNoCode })
      } else {
        await create.mutateAsync(form)
      }
      onClose()
    } catch (e: unknown) {
      const errMsg = e instanceof Error ? e.message : '保存失败'
      setError(errMsg)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4">
      <div className="bg-card border border-border rounded-xl w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <div className="sticky top-0 bg-card border-b border-border px-5 py-3 flex items-center justify-between">
          <h2 className="text-base font-semibold">
            {isEdit ? `编辑 ${initial!.fund_code}` : '新增基金'}
          </h2>
          <button onClick={onClose} className="p-1 rounded hover:bg-secondary">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-5 space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <Field label="基金代码 *">
              <input
                value={form.fund_code}
                onChange={e => setForm({ ...form, fund_code: e.target.value.trim() })}
                disabled={isEdit}
                placeholder="如 110011"
                className="w-full px-2.5 py-1.5 rounded-md bg-background border border-border text-sm font-mono disabled:opacity-50"
              />
            </Field>
            <Field label="市场">
              <select
                value={form.market}
                onChange={e => setForm({ ...form, market: e.target.value })}
                className="w-full px-2.5 py-1.5 rounded-md bg-background border border-border text-sm"
              >
                <option value="CN">CN（A 股 / 场外基金）</option>
                <option value="HK">HK（港股）</option>
                <option value="US">US（美股）</option>
              </select>
            </Field>
          </div>

          <Field label="基金名称 *">
            <input
              value={form.fund_name}
              onChange={e => setForm({ ...form, fund_name: e.target.value })}
              placeholder="如 易方达中小盘混合"
              className="w-full px-2.5 py-1.5 rounded-md bg-background border border-border text-sm"
            />
          </Field>

          <div className="grid grid-cols-3 gap-4">
            <Field label="锚点净值 (last_nav)">
              <input
                type="number" step="0.0001" min="0"
                value={form.last_nav ?? ''}
                onChange={e => setForm({ ...form, last_nav: e.target.value ? parseFloat(e.target.value) : null })}
                placeholder="如 1.5234"
                className="w-full px-2.5 py-1.5 rounded-md bg-background border border-border text-sm tabular-nums"
              />
            </Field>
            <Field label="锚点日期">
              <input
                type="date"
                value={form.last_nav_date ?? ''}
                onChange={e => setForm({ ...form, last_nav_date: e.target.value || null })}
                className="w-full px-2.5 py-1.5 rounded-md bg-background border border-border text-sm"
              />
            </Field>
            <Field label="beta（[0, 5]）">
              <input
                type="number" step="0.01" min="0" max="5"
                value={form.beta}
                onChange={e => setForm({ ...form, beta: parseFloat(e.target.value) || 1.0 })}
                className="w-full px-2.5 py-1.5 rounded-md bg-background border border-border text-sm tabular-nums"
              />
            </Field>
          </div>

          <Field label="basket（成分指数 + 权重）">
            <div className="space-y-1.5">
              {form.basket.map((item, i) => (
                <div key={i} className="flex items-center gap-2">
                  <input
                    value={item.symbol}
                    onChange={e => updateBasketItem(i, { symbol: e.target.value.trim() })}
                    placeholder="如 CSI300, SPX, HSI"
                    className="flex-1 px-2.5 py-1.5 rounded-md bg-background border border-border text-sm font-mono"
                  />
                  <input
                    type="number" step="0.01" min="0" max="1"
                    value={item.weight}
                    onChange={e => updateBasketItem(i, { weight: parseFloat(e.target.value) || 0 })}
                    placeholder="0.6"
                    className="w-24 px-2.5 py-1.5 rounded-md bg-background border border-border text-sm tabular-nums"
                  />
                  <button
                    onClick={() => removeBasketItem(i)}
                    className="p-1.5 rounded hover:bg-red-500/15 text-muted-foreground hover:text-red-500"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              ))}
              <button
                onClick={addBasketItem}
                className="w-full px-2.5 py-1.5 rounded-md border border-dashed border-border hover:bg-secondary/50 text-xs text-muted-foreground hover:text-foreground flex items-center justify-center gap-1"
              >
                <Plus className="w-3 h-3" /> 添加成分
              </button>
              <p className={`text-[10px] ${
                weightStatus === 'ok' ? 'text-green-500'
                : weightStatus === 'invalid' ? 'text-red-500'
                : 'text-muted-foreground'
              }`}>
                Σ = {(totalWeight * 100).toFixed(1)}%
                {weightStatus === 'invalid' && ' （需在 50%–150% 之间）'}
                {weightStatus === 'ok' && ' ✓'}
              </p>
            </div>
          </Field>

          <Field label="状态">
            <div className="flex gap-2 text-xs">
              {(['active', 'archived'] as const).map(s => (
                <label key={s} className="flex items-center gap-1.5 cursor-pointer">
                  <input
                    type="radio"
                    checked={form.status === s}
                    onChange={() => setForm({ ...form, status: s })}
                  />
                  <span className={form.status === s ? 'text-foreground' : 'text-muted-foreground'}>
                    {s === 'active' ? '运行中' : '归档'}
                  </span>
                </label>
              ))}
            </div>
          </Field>

          <Field label="备注">
            <textarea
              value={form.notes ?? ''}
              onChange={e => setForm({ ...form, notes: e.target.value || null })}
              placeholder="可选：策略类型、配置依据、注意事项等"
              rows={2}
              className="w-full px-2.5 py-1.5 rounded-md bg-background border border-border text-sm resize-none"
            />
          </Field>

          {error && (
            <div className="rounded-md bg-red-500/10 border border-red-500/30 px-3 py-2 text-xs flex items-center gap-2 text-red-500">
              <AlertCircle className="w-3.5 h-3.5" />
              {error}
            </div>
          )}
        </div>

        <div className="sticky bottom-0 bg-card border-t border-border px-5 py-3 flex items-center justify-end gap-2">
          <button onClick={onClose} className="px-3 py-1.5 rounded-md hover:bg-secondary text-sm">
            取消
          </button>
          <button
            onClick={handleSubmit}
            disabled={isPending}
            className="px-3 py-1.5 rounded-md bg-primary hover:bg-primary/90 text-primary-foreground text-sm font-medium flex items-center gap-1.5 disabled:opacity-50"
          >
            {isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
            保存
          </button>
        </div>
      </div>
    </div>
  )
}

// ── 子组件 ─────────────────────────────────────────────

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="text-xs text-muted-foreground mb-1 block">{label}</label>
      {children}
    </div>
  )
}

function Skeleton({ h }: { h: number }) {
  return <div className="bg-secondary/40 rounded-md animate-pulse" style={{ height: `${h}px` }} />
}
