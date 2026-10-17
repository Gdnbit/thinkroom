import { useState, useEffect } from 'react'
import { ChevronDown, ChevronRight, AlertTriangle, CheckCircle, Info, XCircle, FileText, MessageSquare, Eye } from 'lucide-react'
import { clsx } from 'clsx'
import api from '@/services/api'
import DecisionSessionStepPanel from '@/components/DecisionSessionStepPanel'
import { currentDecisionSession } from '@/data/decisionSession'
import { useCurrentDecisionSession } from '@/services/decisionSession'

/* ── Types ── */
interface AuditIssue { severity: string; message: string }
interface ClaimInfo { summary: string; full_quote: string | null; full_quote_length: number; claim_type: string; stance: string; confidence: number; asset_scope: string[] }
interface ChatAnalysis { is_chat: boolean; total_messages: number; speakers: Record<string, number>; main_speaker: string | null }
interface TextQuality { total_chars: number; total_lines: number; unique_lines: number; duplicate_lines: number; duplicate_examples: string[]; has_ocr_preamble: boolean }
interface SourceReport {
  source_id: string; title: string; source_type: string; status: string
  processing_error?: string | null
  speaker_hint: string | null; theme_hint: string | null; word_count: number; created_at: string | null
  raw_text_preview: string; raw_text_length: number
  article_summary_preview: string; article_summary_length: number
  attachments: { file_name: string; attachment_type: string; file_size_bytes: number; file_exists: boolean }[]
  claims: ClaimInfo[]; claims_total: number; claims_with_full_quote: number
  chat_analysis: ChatAnalysis | null; text_quality: TextQuality; issues: AuditIssue[]
}
interface BatchReport {
  total_sources: number; total_claims: number; total_claims_with_quote: number
  total_issues: number; critical_issues: number; sources: SourceReport[]; summary: string
}

/* ── Severity badge ── */
const sevIcon = (s: string) => {
  if (s === 'CRITICAL') return <XCircle size={12} className="text-red-400" />
  if (s === 'WARNING') return <AlertTriangle size={12} className="text-amber-400" />
  return <Info size={12} className="text-sky-400" />
}
const stanceBg: Record<string, string> = {
  bullish: 'bg-emerald-500/15 text-emerald-400', bearish: 'bg-red-500/15 text-red-400',
  neutral: 'bg-slate-500/15 text-slate-400', conditional: 'bg-amber-500/15 text-amber-400',
}
const statusBg: Record<string, string> = {
  completed: 'bg-emerald-500/15 text-emerald-500',
  stored: 'bg-emerald-500/15 text-emerald-500',
  pending: 'bg-sky-500/15 text-sky-500',
  processing: 'bg-amber-500/15 text-amber-500',
  failed: 'bg-red-500/15 text-red-500',
}

/* ── Full-text modal ── */
function FullTextModal({ sourceId, title, onClose }: { sourceId: string; title: string; onClose: () => void }) {
  const [data, setData] = useState<{ raw_text: string; article_summary: string } | null>(null)
  const [claims, setClaims] = useState<any[]>([])
  const [tab, setTab] = useState<'text' | 'claims'>('text')

  useEffect(() => {
    api.get(`/api/v1/ingestion-audit/source/${sourceId}/full_text`).then(r => setData(r.data))
    api.get(`/api/v1/ingestion-audit/source/${sourceId}/claims`).then(r => setClaims(r.data.claims || []))
  }, [sourceId])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div className="bg-card border border-border rounded-2xl shadow-2xl flex flex-col"
        style={{ width: '90vw', maxWidth: '1400px', height: '85vh' }}
        onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-border shrink-0">
          <div>
            <h2 className="text-base font-semibold text-foreground">{title}</h2>
            <p className="text-xs text-muted-foreground mt-0.5">{sourceId}</p>
          </div>
          <div className="flex items-center gap-3">
            <div className="flex bg-muted rounded-lg p-0.5">
              <button onClick={() => setTab('text')} className={clsx('px-3 py-1 text-xs rounded-md transition-colors', tab === 'text' ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground')}>
                <FileText size={12} className="inline mr-1" />原始文本
              </button>
              <button onClick={() => setTab('claims')} className={clsx('px-3 py-1 text-xs rounded-md transition-colors', tab === 'claims' ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground')}>
                <MessageSquare size={12} className="inline mr-1" />提取观点 ({claims.length})
              </button>
            </div>
            <button onClick={onClose} className="w-7 h-7 rounded-lg flex items-center justify-center bg-muted text-muted-foreground hover:text-foreground">✕</button>
          </div>
        </div>
        {/* Body */}
        <div className="flex-1 overflow-hidden">
          {tab === 'text' ? (
            <div className="h-full flex">
              {/* Left: raw text */}
              <div className="flex-1 flex flex-col border-r border-border">
                <div className="px-4 py-2 bg-muted/50 text-xs text-muted-foreground font-medium shrink-0">原始文本 ({data?.raw_text.length ?? 0} chars)</div>
                <div className="flex-1 overflow-auto p-4">
                  <pre className="text-xs text-foreground/90 whitespace-pre-wrap font-sans leading-relaxed">{data?.raw_text || '加载中...'}</pre>
                </div>
              </div>
              {/* Right: article summary */}
              <div className="w-[35%] flex flex-col">
                <div className="px-4 py-2 bg-muted/50 text-xs text-muted-foreground font-medium shrink-0">文章摘要 ({data?.article_summary?.length ?? 0} chars)</div>
                <div className="flex-1 overflow-auto p-4">
                  <pre className="text-xs text-foreground/90 whitespace-pre-wrap font-sans leading-relaxed">{data?.article_summary || '(无摘要)'}</pre>
                </div>
              </div>
            </div>
          ) : (
            <div className="h-full flex">
              {/* Left: raw text for reference */}
              <div className="w-[40%] flex flex-col border-r border-border">
                <div className="px-4 py-2 bg-muted/50 text-xs text-muted-foreground font-medium shrink-0">原始文本（参考对照）</div>
                <div className="flex-1 overflow-auto p-4">
                  <pre className="text-xs text-foreground/90 whitespace-pre-wrap font-sans leading-relaxed">{data?.raw_text || '加载中...'}</pre>
                </div>
              </div>
              {/* Right: claims */}
              <div className="flex-1 flex flex-col">
                <div className="px-4 py-2 bg-muted/50 text-xs text-muted-foreground font-medium shrink-0">提取的观点 ({claims.length})</div>
                <div className="flex-1 overflow-auto p-4 space-y-3">
                  {claims.map((c, i) => (
                    <div key={i} className="rounded-xl border border-border p-4 space-y-2">
                      <div className="flex items-start justify-between gap-2">
                        <p className="text-sm font-medium text-foreground leading-snug flex-1">{c.summary}</p>
                        <span className={clsx('shrink-0 text-[10px] px-2 py-0.5 rounded-full font-medium', stanceBg[c.stance] || stanceBg.neutral)}>{c.stance}</span>
                      </div>
                      {c.full_quote && (
                        <div className="bg-muted/50 rounded-lg p-3 border-l-2 border-primary/30">
                          <p className="text-xs text-muted-foreground mb-1 font-medium">原文引用</p>
                          <p className="text-xs text-foreground/80 leading-relaxed">{c.full_quote}</p>
                        </div>
                      )}
                      <div className="flex flex-wrap gap-2 text-[10px]">
                        <span className="px-1.5 py-0.5 rounded bg-muted text-muted-foreground">{c.claim_type}</span>
                        <span className="px-1.5 py-0.5 rounded bg-muted text-muted-foreground">{c.time_horizon || 'unspecified'}</span>
                        <span className="px-1.5 py-0.5 rounded bg-muted text-muted-foreground">置信 {(c.confidence * 100).toFixed(0)}%</span>
                        {(c.asset_scope || []).slice(0, 5).map((a: string, j: number) => (
                          <span key={j} className="px-1.5 py-0.5 rounded bg-primary/10 text-primary">{a}</span>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

/* ── Source card ── */
function SourceCard({ r }: { r: SourceReport }) {
  const [open, setOpen] = useState(false)
  const [showModal, setShowModal] = useState(false)
  const critical = r.issues.filter(i => i.severity === 'CRITICAL').length
  const warnings = r.issues.filter(i => i.severity === 'WARNING').length
  const statusIcon = critical > 0 ? <XCircle size={14} className="text-red-400" />
    : warnings > 0 ? <AlertTriangle size={14} className="text-amber-400" />
    : <CheckCircle size={14} className="text-emerald-400" />

  return (
    <>
      <div className="rounded-xl border border-border bg-card overflow-hidden">
        {/* Header row */}
        <div
          role="button"
          tabIndex={0}
          onClick={() => setOpen(p => !p)}
          onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setOpen(p => !p) }}
          className="w-full flex cursor-pointer items-center gap-3 px-4 py-3 hover:bg-muted/30 transition-colors text-left">
          {open ? <ChevronDown size={14} className="text-muted-foreground shrink-0" /> : <ChevronRight size={14} className="text-muted-foreground shrink-0" />}
          {statusIcon}
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-foreground truncate">{r.title}</p>
            <div className="flex items-center gap-3 mt-0.5 text-[11px] text-muted-foreground">
              <span className="px-1.5 py-0.5 rounded bg-muted">{r.source_type}</span>
              <span className={clsx('px-1.5 py-0.5 rounded', statusBg[r.status] || 'bg-muted text-muted-foreground')}>{r.status}</span>
              {r.speaker_hint && <span>🗣 {r.speaker_hint}</span>}
              <span>{r.raw_text_length.toLocaleString()} chars</span>
              <span>{r.claims_total} claims</span>
              <span className={r.claims_with_full_quote === r.claims_total ? 'text-emerald-400' : 'text-amber-400'}>
                引用 {r.claims_with_full_quote}/{r.claims_total}
              </span>
            </div>
          </div>
          <button onClick={e => { e.stopPropagation(); setShowModal(true) }}
            className="shrink-0 flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-primary/10 text-primary text-xs hover:bg-primary/20 transition-colors">
            <Eye size={12} /> 查看全文
          </button>
        </div>

        {/* Expanded detail */}
        {open && (
          <div className="px-4 pb-4 space-y-3 border-t border-border pt-3">
            {/* Issues */}
            {r.issues.length > 0 && (
              <div className="space-y-1">
                {r.issues.map((iss, i) => (
                  <div key={i} className="flex items-start gap-2 text-xs">
                    {sevIcon(iss.severity)}
                    <span className="text-foreground/80">{iss.message}</span>
                  </div>
                ))}
              </div>
            )}
            {r.processing_error && (
              <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
                处理记录：{r.processing_error}
              </div>
            )}

            {/* Text quality */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
              {[
                { label: '总行数', value: r.text_quality.total_lines },
                { label: '唯一行', value: r.text_quality.unique_lines },
                { label: '重复行', value: r.text_quality.duplicate_lines, warn: r.text_quality.duplicate_lines > 5 },
                { label: 'OCR前言', value: r.text_quality.has_ocr_preamble ? '有' : '无', warn: r.text_quality.has_ocr_preamble },
              ].map((m, i) => (
                <div key={i} className="rounded-lg bg-muted/50 px-3 py-2">
                  <p className="text-[10px] text-muted-foreground">{m.label}</p>
                  <p className={clsx('text-sm font-medium', m.warn ? 'text-amber-400' : 'text-foreground')}>{m.value}</p>
                </div>
              ))}
            </div>

            {/* Chat analysis */}
            {r.chat_analysis?.is_chat && (
              <div className="rounded-lg bg-muted/50 px-3 py-2">
                <p className="text-[10px] text-muted-foreground mb-1">聊天结构</p>
                <p className="text-xs text-foreground">{r.chat_analysis.total_messages} 条消息，主发言人: {r.chat_analysis.main_speaker || '未知'}</p>
                <div className="flex flex-wrap gap-1.5 mt-1">
                  {Object.entries(r.chat_analysis.speakers).map(([name, count]) => (
                    <span key={name} className="text-[10px] px-1.5 py-0.5 rounded bg-card border border-border">{name}: {count as number}</span>
                  ))}
                </div>
              </div>
            )}

            {/* Claims preview */}
            {r.claims.length > 0 && (
              <div className="space-y-1.5">
                <p className="text-[10px] text-muted-foreground font-medium">观点摘要</p>
                {r.claims.slice(0, 5).map((c, i) => (
                  <div key={i} className="flex items-start gap-2 text-xs">
                    <span className={clsx('shrink-0 mt-0.5 px-1.5 py-0.5 rounded text-[9px] font-medium', stanceBg[c.stance] || stanceBg.neutral)}>{c.stance}</span>
                    <span className="text-foreground/80 leading-snug">{c.summary}</span>
                    {c.full_quote ? <CheckCircle size={10} className="text-emerald-400 shrink-0 mt-0.5" /> : <XCircle size={10} className="text-red-400 shrink-0 mt-0.5" />}
                  </div>
                ))}
                {r.claims.length > 5 && <p className="text-[10px] text-muted-foreground">...还有 {r.claims.length - 5} 条</p>}
              </div>
            )}

            {/* Attachments */}
            {r.attachments.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {r.attachments.map((a, i) => (
                  <span key={i} className={clsx('text-[10px] px-2 py-0.5 rounded border', a.file_exists ? 'border-border text-muted-foreground' : 'border-red-500/30 text-red-400')}>
                    {a.file_name} ({(a.file_size_bytes / 1024).toFixed(0)}KB)
                  </span>
                ))}
              </div>
            )}

            {/* Raw text preview */}
            <div className="rounded-lg bg-muted/30 p-3">
              <p className="text-[10px] text-muted-foreground mb-1">文本预览 (前1000字)</p>
              <pre className="text-[11px] text-foreground/70 whitespace-pre-wrap font-sans leading-relaxed max-h-40 overflow-auto">{r.raw_text_preview}</pre>
            </div>
          </div>
        )}
      </div>
      {showModal && <FullTextModal sourceId={r.source_id} title={r.title} onClose={() => setShowModal(false)} />}
    </>
  )
}

/* ── Main page ── */
export default function IngestionAudit() {
  const decisionSessionQuery = useCurrentDecisionSession()
  const decisionSession = decisionSessionQuery.data ?? currentDecisionSession
  const [report, setReport] = useState<BatchReport | null>(null)
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState<'all' | 'issues'>('all')
  const [typeFilter, setTypeFilter] = useState('')

  const fetchReport = () => {
    setLoading(true)
    const params: Record<string, any> = { limit: 100 }
    if (filter === 'issues') params.only_issues = true
    if (typeFilter) params.source_type = typeFilter
    api.get('/api/v1/ingestion-audit/sources', { params })
      .then(r => setReport(r.data))
      .finally(() => setLoading(false))
  }

  useEffect(() => { fetchReport() }, [filter, typeFilter])

  const types = [...new Set(report?.sources.map(s => s.source_type) || [])]

  return (
    <div className="p-6 2xl:p-8 max-w-[1400px] 2xl:max-w-[1760px] mx-auto space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-xl font-bold text-foreground">Ingestion 审计</h1>
        <p className="text-sm text-muted-foreground mt-1">对比原始文件与处理结果，检查信息完整性和提取质量</p>
      </div>

      <DecisionSessionStepPanel step="audit" session={decisionSession} />

      <section className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="rounded-xl border border-border bg-card p-4">
          <div className="mb-3 flex items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-foreground">当前回答的证据链</h2>
              <p className="mt-1 text-xs text-muted-foreground">这些证据来自当前上传资料，保存快照前应确认强弱和覆盖边界。</p>
            </div>
            <span className="rounded-lg border border-primary/25 bg-primary/10 px-2 py-1 text-[11px] text-primary">
              {decisionSession.evidence.length} 条证据
            </span>
          </div>
          <div className="space-y-2">
            {decisionSession.evidence.map((item) => (
              <div key={item.title} className="rounded-lg border border-border bg-secondary/25 p-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-xs font-semibold text-foreground">{item.title}</p>
                    <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">{item.summary}</p>
                  </div>
                  <span className="rounded-md bg-background px-1.5 py-0.5 text-[10px] text-muted-foreground">{item.strength}</span>
                </div>
                <p className="mt-2 text-[10px] text-primary">{item.source}</p>
              </div>
            ))}
          </div>
        </div>
        <div className="rounded-xl border border-border bg-card p-4">
          <h2 className="text-sm font-semibold text-foreground">审计结论</h2>
          <div className="mt-3 space-y-2">
            {decisionSession.risks.map((risk) => (
              <div key={risk.title} className="rounded-lg border border-border bg-secondary/25 p-3">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-xs font-semibold text-foreground">{risk.title}</p>
                  <span className="rounded-md bg-background px-1.5 py-0.5 text-[10px] text-muted-foreground">{risk.level}</span>
                </div>
                <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">{risk.mitigation}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Stats bar */}
      {report && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          {[
            { label: '数据源', value: report.total_sources, icon: '📄' },
            { label: '总观点', value: report.total_claims, icon: '💡' },
            { label: '有引用', value: `${report.total_claims_with_quote}/${report.total_claims}`, icon: '📝',
              pct: report.total_claims > 0 ? Math.round(report.total_claims_with_quote / report.total_claims * 100) : 0 },
            { label: '总问题', value: report.total_issues, icon: '⚠️', warn: report.total_issues > 0 },
            { label: '严重问题', value: report.critical_issues, icon: '🔴', warn: report.critical_issues > 0 },
          ].map((s, i) => (
            <div key={i} className="rounded-xl border border-border bg-card px-4 py-3">
              <p className="text-[10px] text-muted-foreground">{s.icon} {s.label}</p>
              <p className={clsx('text-lg font-bold mt-0.5', s.warn ? 'text-amber-400' : 'text-foreground')}>
                {s.value}
                {'pct' in s && <span className="text-xs font-normal text-muted-foreground ml-1">({s.pct}%)</span>}
              </p>
            </div>
          ))}
        </div>
      )}

      {/* Filters */}
      <div className="flex items-center gap-3">
        <div className="flex bg-muted rounded-lg p-0.5">
          {(['all', 'issues'] as const).map(f => (
            <button key={f} onClick={() => setFilter(f)}
              className={clsx('px-3 py-1.5 text-xs rounded-md transition-colors', filter === f ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground')}>
              {f === 'all' ? '全部' : '仅有问题'}
            </button>
          ))}
        </div>
        {types.length > 1 && (
          <select value={typeFilter} onChange={e => setTypeFilter(e.target.value)}
            className="text-xs bg-muted border border-border rounded-lg px-2 py-1.5 text-foreground">
            <option value="">全部类型</option>
            {types.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        )}
        <button onClick={fetchReport} className="ml-auto text-xs px-3 py-1.5 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors">
          刷新
        </button>
      </div>

      {/* Source list */}
      {loading ? (
        <div className="text-center py-12 text-muted-foreground text-sm">加载中...</div>
      ) : (
        <div className="space-y-2">
          {(report?.sources || []).map(r => <SourceCard key={r.source_id} r={r} />)}
          {report?.sources.length === 0 && (
            <div className="text-center py-12 text-muted-foreground text-sm">暂无数据</div>
          )}
        </div>
      )}
    </div>
  )
}
