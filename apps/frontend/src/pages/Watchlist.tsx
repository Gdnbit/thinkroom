import { useState } from 'react'
import { Plus, List, Loader2, AlertCircle, Trash2 } from 'lucide-react'
import { clsx } from 'clsx'
import { useWatchlists, useCreateWatchlist } from '@/services/hooks'
import api from '@/services/api'
import { useQueryClient } from '@tanstack/react-query'

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
  const { data: lists, isLoading, error } = useWatchlists()
  const createMutation = useCreateWatchlist()
  const qc = useQueryClient()
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ name: '', list_type: 'asset', description: '' })

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!form.name.trim()) return
    await createMutation.mutateAsync(form)
    setForm({ name: '', list_type: 'asset', description: '' })
    setShowForm(false)
  }

  const handleDelete = async (id: string) => {
    if (!confirm('确认删除该观察列表？')) return
    await api.delete(`/api/v1/watchlists/${id}`)
    qc.invalidateQueries({ queryKey: ['watchlists'] })
  }

  return (
    <div className="p-6 2xl:p-8 max-w-[900px] 2xl:max-w-[1100px] mx-auto">
      <div className="flex items-start justify-between page-header !mb-5 !pb-4">
        <div>
          <span className="section-label">Watchlist</span>
          <h1 className="text-2xl font-semibold text-foreground">观察池</h1>
          <p className="text-sm text-muted-foreground mt-1">跟踪资产、主题、来源和策略</p>
        </div>
        <button
          onClick={() => setShowForm(p => !p)}
          className="flex items-center gap-1.5 bg-primary text-primary-foreground rounded-lg px-3 py-2 text-sm hover:opacity-90 transition-opacity"
        >
          <Plus size={14} /> 新建
        </button>
      </div>

      {/* 新建表单 */}
      {showForm && (
        <form onSubmit={handleCreate} className="bg-card border border-border rounded-xl p-4 mb-4 space-y-3">
          <input
            value={form.name}
            onChange={e => setForm(p => ({ ...p, name: e.target.value }))}
            placeholder="列表名称（必填）"
            className="w-full bg-secondary border border-border rounded-lg px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary"
          />
          <div className="flex gap-2">
            <select
              value={form.list_type}
              onChange={e => setForm(p => ({ ...p, list_type: e.target.value }))}
              className="bg-secondary border border-border rounded-lg px-3 py-2 text-sm text-foreground focus:outline-none"
            >
              {Object.entries(LIST_TYPE_LABEL).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
            <input
              value={form.description}
              onChange={e => setForm(p => ({ ...p, description: e.target.value }))}
              placeholder="描述（可选）"
              className="flex-1 bg-secondary border border-border rounded-lg px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
          <div className="flex gap-2 justify-end">
            <button type="button" onClick={() => setShowForm(false)}
              className="px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors">
              取消
            </button>
            <button type="submit" disabled={createMutation.isPending || !form.name.trim()}
              className="px-4 py-1.5 bg-primary text-primary-foreground rounded-lg text-sm disabled:opacity-50">
              {createMutation.isPending ? '创建中...' : '创建'}
            </button>
          </div>
        </form>
      )}

      {/* 列表 */}
      {isLoading ? (
        <div className="flex items-center gap-2 text-muted-foreground text-sm py-8 justify-center">
          <Loader2 size={14} className="animate-spin" /> 加载中...
        </div>
      ) : error ? (
        <div className="flex items-center gap-2 text-red-400 text-sm py-8 justify-center">
          <AlertCircle size={14} /> 加载失败
        </div>
      ) : !lists?.length ? (
        <div className="flex flex-col items-center gap-2 text-muted-foreground py-12">
          <List size={28} />
          <p className="text-sm">还没有观察列表，点击"新建"创建一个</p>
        </div>
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
                {wl.description
                  ? <p className="text-xs text-muted-foreground truncate">{wl.description}</p>
                  : <p className="text-xs text-muted-foreground/40">无描述</p>
                }
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {wl.item_count != null && (
                  <span className="text-xs text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity">
                    {wl.item_count} 项
                  </span>
                )}
                <button onClick={() => handleDelete(wl.id)} title="删除此观察列表"
                  className="text-muted-foreground hover:text-red-400 transition-colors opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-red-400/10">
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
