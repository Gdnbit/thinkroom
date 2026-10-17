import { useState, useEffect, useCallback } from 'react'
import { Users, ChevronDown, ChevronRight, Merge, Plus, X, Search, MessageSquare, Tag, Sparkles } from 'lucide-react'
import { clsx } from 'clsx'
import api from '@/services/api'

/* ── Types ── */
interface Speaker {
  id: string; name: string; aliases: string[]; affiliation: string | null; role: string | null
  domain_tags: string[]; trust_weight: number; total_claims: number; verified_claims: number
  track_record_summary: string | null; style_notes: string | null; hit_rate: number | null
  perspective_value: number; logic_robustness: number; applicable_domains: string[]
  usage_recommendation: string | null; status: string; created_at: string | null
}
interface Claim {
  id: string; summary: string; full_quote: string | null; claim_type: string; stance: string
  confidence: number; asset_scope: string[]; time_horizon: string; valid_until: string | null
  status: string; created_at: string | null
}

const stanceBg: Record<string, string> = {
  bullish: 'bg-emerald-500/15 text-emerald-400', bearish: 'bg-red-500/15 text-red-400',
  neutral: 'bg-slate-500/15 text-slate-400', conditional: 'bg-amber-500/15 text-amber-400',
}

/* ── Merge Modal ── */
function MergeModal({ target, allSpeakers, onClose, onMerged }: {
  target: Speaker; allSpeakers: Speaker[]; onClose: () => void; onMerged: () => void
}) {
  const [sourceId, setSourceId] = useState('')
  const [loading, setLoading] = useState(false)
  const others = allSpeakers.filter(s => s.id !== target.id)

  const doMerge = async () => {
    if (!sourceId) return
    setLoading(true)
    try {
      await api.post(`/api/v1/speakers/${target.id}/merge`, { source_speaker_id: sourceId })
      onMerged()
    } catch (e: any) {
      alert(e.response?.data?.detail || '合并失败')
    } finally { setLoading(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div className="bg-card border border-border rounded-2xl shadow-2xl p-6 w-[420px]" onClick={e => e.stopPropagation()}>
        <h3 className="text-base font-semibold text-foreground mb-1">合并发言人</h3>
        <p className="text-xs text-muted-foreground mb-4">将另一个发言人的别名和观点合并到 <strong>{target.name}</strong></p>
        <select value={sourceId} onChange={e => setSourceId(e.target.value)}
          className="w-full text-sm bg-muted border border-border rounded-lg px-3 py-2 text-foreground mb-4">
          <option value="">选择要合并的发言人...</option>
          {others.map(s => (
            <option key={s.id} value={s.id}>{s.name} ({s.total_claims} claims){s.aliases.length > 0 ? ` — 别名: ${s.aliases.join(', ')}` : ''}</option>
          ))}
        </select>
        <div className="flex gap-2 justify-end">
          <button onClick={onClose} className="px-3 py-1.5 text-xs rounded-lg bg-muted text-muted-foreground hover:text-foreground">取消</button>
          <button onClick={doMerge} disabled={!sourceId || loading}
            className="px-3 py-1.5 text-xs rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
            {loading ? '合并中...' : '确认合并'}
          </button>
        </div>
      </div>
    </div>
  )
}

/* ── Edit Aliases Modal ── */
function AliasModal({ speaker, onClose, onSaved }: { speaker: Speaker; onClose: () => void; onSaved: () => void }) {
  const [aliases, setAliases] = useState<string[]>(speaker.aliases || [])
  const [newAlias, setNewAlias] = useState('')
  const [loading, setLoading] = useState(false)

  const addAlias = () => {
    const a = newAlias.trim()
    if (a && !aliases.includes(a)) { setAliases([...aliases, a]); setNewAlias('') }
  }
  const removeAlias = (a: string) => setAliases(aliases.filter(x => x !== a))

  const save = async () => {
    setLoading(true)
    try {
      await api.patch(`/api/v1/speakers/${speaker.id}`, { aliases })
      onSaved()
    } catch (e: any) {
      alert(e.response?.data?.detail || '保存失败')
    } finally { setLoading(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div className="bg-card border border-border rounded-2xl shadow-2xl p-6 w-[420px]" onClick={e => e.stopPropagation()}>
        <h3 className="text-base font-semibold text-foreground mb-1">管理别名</h3>
        <p className="text-xs text-muted-foreground mb-3">为 <strong>{speaker.name}</strong> 添加或移除别名（不同平台的 ID）</p>
        <div className="flex flex-wrap gap-1.5 mb-3 min-h-[2rem]">
          {aliases.map(a => (
            <span key={a} className="flex items-center gap-1 text-xs px-2 py-1 rounded-lg bg-primary/10 text-primary">
              {a} <button onClick={() => removeAlias(a)} className="hover:text-red-400"><X size={10} /></button>
            </span>
          ))}
          {aliases.length === 0 && <span className="text-xs text-muted-foreground">暂无别名</span>}
        </div>
        <div className="flex gap-2 mb-4">
          <input value={newAlias} onChange={e => setNewAlias(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && addAlias()}
            placeholder="输入新别名然后回车..."
            className="flex-1 text-sm bg-muted border border-border rounded-lg px-3 py-1.5 text-foreground" />
          <button onClick={addAlias} className="px-3 py-1.5 text-xs rounded-lg bg-muted text-muted-foreground hover:text-foreground">
            <Plus size={14} />
          </button>
        </div>
        <div className="flex gap-2 justify-end">
          <button onClick={onClose} className="px-3 py-1.5 text-xs rounded-lg bg-muted text-muted-foreground hover:text-foreground">取消</button>
          <button onClick={save} disabled={loading}
            className="px-3 py-1.5 text-xs rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
            {loading ? '保存中...' : '保存'}
          </button>
        </div>
      </div>
    </div>
  )
}

/* ── Claims Modal ── */
function ClaimsModal({ speaker, onClose }: { speaker: Speaker; onClose: () => void }) {
  const [claims, setClaims] = useState<Claim[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.get(`/api/v1/speakers/${speaker.id}/claims`, { params: { limit: 100 } })
      .then(r => { setClaims(r.data.claims || []); setTotal(r.data.total_claims || 0) })
      .finally(() => setLoading(false))
  }, [speaker.id])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div className="bg-card border border-border rounded-2xl shadow-2xl flex flex-col"
        style={{ width: '700px', maxHeight: '80vh' }}
        onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-6 py-4 border-b border-border shrink-0">
          <div>
            <h3 className="text-base font-semibold text-foreground">{speaker.name} 的观点</h3>
            <p className="text-xs text-muted-foreground mt-0.5">共 {total} 条</p>
          </div>
          <button onClick={onClose} className="w-7 h-7 rounded-lg flex items-center justify-center bg-muted text-muted-foreground hover:text-foreground">✕</button>
        </div>
        <div className="flex-1 overflow-auto p-4 space-y-2">
          {loading && <p className="text-sm text-muted-foreground text-center py-8">加载中...</p>}
          {claims.map(c => (
            <div key={c.id} className="rounded-xl border border-border p-3 space-y-1.5">
              <div className="flex items-start gap-2">
                <span className={clsx('shrink-0 text-[10px] px-2 py-0.5 rounded-full font-medium mt-0.5', stanceBg[c.stance] || stanceBg.neutral)}>{c.stance}</span>
                <p className="text-sm text-foreground leading-snug flex-1">{c.summary}</p>
              </div>
              {c.full_quote && (
                <div className="bg-muted/50 rounded-lg p-2.5 border-l-2 border-primary/30 ml-12">
                  <p className="text-[11px] text-foreground/70 leading-relaxed">{c.full_quote}</p>
                </div>
              )}
              <div className="flex flex-wrap gap-1.5 ml-12 text-[10px]">
                <span className="px-1.5 py-0.5 rounded bg-muted text-muted-foreground">{c.claim_type}</span>
                <span className="px-1.5 py-0.5 rounded bg-muted text-muted-foreground">{c.time_horizon}</span>
                <span className="px-1.5 py-0.5 rounded bg-muted text-muted-foreground">置信 {(c.confidence * 100).toFixed(0)}%</span>
                {c.valid_until && <span className="px-1.5 py-0.5 rounded bg-muted text-muted-foreground">有效至 {c.valid_until.slice(0, 10)}</span>}
                {!c.valid_until && <span className="px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400">长期有效</span>}
                {(c.asset_scope).slice(0, 4).map((a, j) => (
                  <span key={j} className="px-1.5 py-0.5 rounded bg-primary/10 text-primary">{a}</span>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

/* ── Speaker card ── */
function SpeakerCard({ s, allSpeakers, onRefresh }: { s: Speaker; allSpeakers: Speaker[]; onRefresh: () => void }) {
  const [open, setOpen] = useState(false)
  const [showMerge, setShowMerge] = useState(false)
  const [showAlias, setShowAlias] = useState(false)
  const [showClaims, setShowClaims] = useState(false)
  const [reviewing, setReviewing] = useState(false)

  const triggerReview = async (e: React.MouseEvent) => {
    e.stopPropagation()
    if (s.total_claims === 0) { alert('该发言人暂无观点，无法生成点评'); return }
    setReviewing(true)
    try {
      await api.post(`/api/v1/speakers/${s.id}/review`)
      onRefresh()
    } catch (err: any) {
      alert(err.response?.data?.detail || '点评生成失败')
    } finally { setReviewing(false) }
  }

  return (
    <>
      <div className="rounded-xl border border-border bg-card overflow-hidden">
        <div
          role="button"
          tabIndex={0}
          onClick={() => setOpen(p => !p)}
          onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setOpen(p => !p) }}
          className="w-full flex cursor-pointer items-center gap-3 px-4 py-3 hover:bg-muted/30 transition-colors text-left">
          {open ? <ChevronDown size={14} className="text-muted-foreground shrink-0" /> : <ChevronRight size={14} className="text-muted-foreground shrink-0" />}
          <div className="w-8 h-8 rounded-full bg-primary/15 flex items-center justify-center shrink-0">
            <span className="text-xs font-bold text-primary">{s.name.charAt(0)}</span>
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <p className="text-sm font-medium text-foreground truncate">{s.name}</p>
              {s.aliases.length > 0 && (
                <span className="text-[10px] text-muted-foreground">({s.aliases.join(', ')})</span>
              )}
            </div>
            <div className="flex items-center gap-3 mt-0.5 text-[11px] text-muted-foreground">
              <span>{s.total_claims} 条观点</span>
              {s.domain_tags.length > 0 && s.domain_tags.slice(0, 3).map(t => (
                <span key={t} className="px-1.5 py-0.5 rounded bg-muted">{t}</span>
              ))}
              {s.trust_weight != null && <span>可信度 {(s.trust_weight * 100).toFixed(0)}%</span>}
            </div>
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            <button onClick={e => { e.stopPropagation(); setShowClaims(true) }}
              className="p-1.5 rounded-lg bg-muted text-muted-foreground hover:text-foreground transition-colors" title="查看观点">
              <MessageSquare size={13} />
            </button>
            <button onClick={e => { e.stopPropagation(); setShowAlias(true) }}
              className="p-1.5 rounded-lg bg-muted text-muted-foreground hover:text-foreground transition-colors" title="管理别名">
              <Tag size={13} />
            </button>
            <button onClick={triggerReview} disabled={reviewing}
              className={clsx("p-1.5 rounded-lg bg-muted text-muted-foreground hover:text-foreground transition-colors", reviewing && "animate-pulse")} title="生成 AI 点评">
              <Sparkles size={13} />
            </button>
            <button onClick={e => { e.stopPropagation(); setShowMerge(true) }}
              className="p-1.5 rounded-lg bg-muted text-muted-foreground hover:text-foreground transition-colors" title="合并发言人">
              <Merge size={13} />
            </button>
          </div>
        </div>

        {open && (
          <div className="px-4 pb-4 space-y-2 border-t border-border pt-3">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
              {[
                { label: '总观点', value: s.total_claims },
                { label: '已验证', value: s.verified_claims },
                { label: '角度价值', value: `${(s.perspective_value * 100).toFixed(0)}%` },
                { label: '逻辑稳健性', value: `${(s.logic_robustness * 100).toFixed(0)}%` },
              ].map((m, i) => (
                <div key={i} className="rounded-lg bg-muted/50 px-3 py-2">
                  <p className="text-[10px] text-muted-foreground">{m.label}</p>
                  <p className="text-sm font-medium text-foreground">{m.value}</p>
                </div>
              ))}
            </div>
            {s.track_record_summary && (
              <div className="rounded-lg bg-muted/30 p-3">
                <p className="text-[10px] text-muted-foreground mb-1">综合评价</p>
                <p className="text-xs text-foreground/80 leading-relaxed">{s.track_record_summary}</p>
              </div>
            )}
            {s.style_notes && (
              <div className="rounded-lg bg-muted/30 p-3">
                <p className="text-[10px] text-muted-foreground mb-1">AI 画像详情</p>
                <pre className="text-[11px] text-foreground/70 whitespace-pre-wrap font-sans leading-relaxed">{s.style_notes}</pre>
              </div>
            )}
            {s.usage_recommendation && (
              <div className="rounded-lg bg-primary/5 p-3 border border-primary/10">
                <p className="text-[10px] text-primary mb-1">使用建议</p>
                <p className="text-xs text-foreground/80">{s.usage_recommendation}</p>
              </div>
            )}
            {s.aliases.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                <span className="text-[10px] text-muted-foreground mr-1">别名:</span>
                {s.aliases.map(a => (
                  <span key={a} className="text-[10px] px-2 py-0.5 rounded-lg bg-primary/10 text-primary">{a}</span>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
      {showMerge && <MergeModal target={s} allSpeakers={allSpeakers} onClose={() => setShowMerge(false)} onMerged={() => { setShowMerge(false); onRefresh() }} />}
      {showAlias && <AliasModal speaker={s} onClose={() => setShowAlias(false)} onSaved={() => { setShowAlias(false); onRefresh() }} />}
      {showClaims && <ClaimsModal speaker={s} onClose={() => setShowClaims(false)} />}
    </>
  )
}

/* ── Main page ── */
export default function Speakers() {
  const [speakers, setSpeakers] = useState<Speaker[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')

  const fetchSpeakers = useCallback(() => {
    setLoading(true)
    const params: Record<string, any> = { limit: 100 }
    if (search) params.name = search
    api.get('/api/v1/speakers', { params })
      .then(r => setSpeakers(r.data.speakers || []))
      .finally(() => setLoading(false))
  }, [search])

  useEffect(() => { fetchSpeakers() }, [fetchSpeakers])

  const totalClaims = speakers.reduce((sum, s) => sum + s.total_claims, 0)
  const totalAliases = speakers.reduce((sum, s) => sum + s.aliases.length, 0)

  return (
    <div className="p-6 2xl:p-8 max-w-[1200px] 2xl:max-w-[1500px] mx-auto space-y-6">
      <div>
        <h1 className="text-xl font-bold text-foreground flex items-center gap-2"><Users size={20} /> 来源画像</h1>
        <p className="text-sm text-muted-foreground mt-1">管理发言人画像、别名映射和观点汇总</p>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-3">
        {[
          { label: '发言人', value: speakers.length },
          { label: '总观点', value: totalClaims },
          { label: '已建别名', value: totalAliases },
        ].map((s, i) => (
          <div key={i} className="rounded-xl border border-border bg-card px-4 py-3">
            <p className="text-[10px] text-muted-foreground">{s.label}</p>
            <p className="text-lg font-bold text-foreground">{s.value}</p>
          </div>
        ))}
      </div>

      {/* Search */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <input value={search} onChange={e => setSearch(e.target.value)}
            placeholder="搜索发言人..."
            className="w-full text-sm bg-muted border border-border rounded-lg pl-9 pr-3 py-2 text-foreground" />
        </div>
        <button onClick={fetchSpeakers} className="text-xs px-3 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors">
          刷新
        </button>
      </div>

      {/* Speaker list */}
      {loading ? (
        <div className="text-center py-12 text-muted-foreground text-sm">加载中...</div>
      ) : (
        <div className="space-y-2">
          {speakers.map(s => <SpeakerCard key={s.id} s={s} allSpeakers={speakers} onRefresh={fetchSpeakers} />)}
          {speakers.length === 0 && (
            <div className="text-center py-12 text-muted-foreground text-sm">暂无发言人数据</div>
          )}
        </div>
      )}
    </div>
  )
}
