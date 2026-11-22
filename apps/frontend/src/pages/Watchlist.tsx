import { useState } from 'react'
import { Plus, List, Trash2 } from 'lucide-react'
import { clsx } from 'clsx'
import { useWatchlists, useCreateWatchlist } from '@/services/hooks'
import api from '@/services/api'
import { useQueryClient } from '@tanstack/react-query'
import { EmptyState, ErrorState, LoadingRows } from '@/components/ui'

const LIST_TYPE_LABEL: Record<string, string> = {
  asset: '资产',
  theme: '主题',
  speaker: '来源',
  strategy: '策略',
}

const LIST_TYPE_COLOR: Record<string, string> = {
  asset: 'text-blue-400 bg-blue-500/10 border-blue-500/20',
  theme: 'text-purple-400 bg-purple-500/10 border-purple-500/20',
  speaker: 'text-amber-400 bg-amber-500/10 border-amber-500/20',
  strategy: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20',
}

export default function Watchlist() {
  const { data: lists, isLoading, error, refetch } = useWatchlists()
  const createMutation = useCreateWatchlist()
  const qc = useQueryClient()
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ name: '', list_type: 'asset', description: '' })
  const [actionError, setActionError] = useState('')

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!form.name.trim()) return
    setActionError('')
    try {
      await createMutation.mutateAsync(form)
    } catch {
      setActionError('创建失败，请重试。')
      return
    }
    setForm({ name: '', list_type: 'asset', description: '' })
    setShowForm(false)
  }

  const handleDelete = async (id: string, name: string) => {
    if (!window.confirm(`删除「${name}」及其中的观察项？此操作无法撤销。`)) return
    setActionError('')
    try {
      await api.delete(`/api/v1/watchlists/${id}`)
      await qc.invalidateQueries({ queryKey: ['watchlists'] })
    } catch {
      setActionError('删除失败，请重试。')
    }
  }

  return (
    <div className="workspace-page workspace-page--reading">
      <div className="flex items-start justify-between page-header">
        <div>
          <h1 className="workspace-heading text-foreground">观察池</h1>
        </div>
        <button
          type="button"
          aria-expanded={showForm}
          onClick={() => setShowForm(p => !p)}
          className="btn-primary"
        >
          <Plus size={16} aria-hidden="true" /> 新建观察列表
        </button>
      </div>

      {/* 新建表单 */}
      {showForm && (
        <form onSubmit={handleCreate} className="bg-card border border-border rounded-xl p-4 mb-4 space-y-3">
          <label className="block text-sm font-medium">
            列表名称
            <input required
            value={form.name}
            onChange={e => setForm(p => ({ ...p, name: e.target.value }))}
            className="field-control mt-1"
            />
          </label>
          <div className="grid gap-3 sm:grid-cols-[10rem_minmax(0,1fr)]">
            <label className="block text-sm font-medium">
              类型
            <select
              value={form.list_type}
              onChange={e => setForm(p => ({ ...p, list_type: e.target.value }))}
              className="field-control mt-1"
            >
              {Object.entries(LIST_TYPE_LABEL).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
            </label>
            <label className="block text-sm font-medium">
              描述（可选）
            <input
              value={form.description}
              onChange={e => setForm(p => ({ ...p, description: e.target.value }))}
              className="field-control mt-1"
            />
            </label>
          </div>
          <div className="flex gap-2 justify-end">
            <button type="button" onClick={() => setShowForm(false)}
              className="px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors">
              取消
            </button>
            <button type="submit" disabled={createMutation.isPending || !form.name.trim()}
              className="btn-primary disabled:opacity-50">
              {createMutation.isPending ? '创建中...' : '创建'}
            </button>
          </div>
        </form>
      )}
      {actionError && <p role="alert" className="status-error mb-3 text-sm">{actionError}</p>}

      {/* 列表 */}
      {isLoading ? (
        <LoadingRows rows={3} />
      ) : error ? (
        <ErrorState message="观察列表读取失败" onRetry={() => void refetch()} />
      ) : !lists?.length ? (
        <EmptyState icon={List} title="暂无观察列表" />
      ) : (
        <div className="space-y-2">
          {lists.map(wl => (
            <div key={wl.id} className="flex items-center gap-3 bg-card border border-border rounded-xl px-4 py-3 hover:border-primary/40 transition-colors group">
              <span className={clsx('text-xs px-2 py-0.5 rounded-full border shrink-0',
                LIST_TYPE_COLOR[wl.list_type] || 'text-slate-400 bg-slate-500/10 border-slate-500/20'
              )}>
                {LIST_TYPE_LABEL[wl.list_type] || wl.list_type}
              </span>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-foreground">{wl.name}</p>
                {wl.description && <p className="text-xs text-muted-foreground truncate">{wl.description}</p>}
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {wl.item_count != null && (
                  <span className="hidden text-xs text-muted-foreground sm:inline">
                    {wl.item_count} 项
                  </span>
                )}
                <button type="button" onClick={() => void handleDelete(wl.id, wl.name)} aria-label={`删除观察列表：${wl.name}`}
                  className="icon-button hover:text-[hsl(var(--status-error))]">
                  <Trash2 size={14} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
