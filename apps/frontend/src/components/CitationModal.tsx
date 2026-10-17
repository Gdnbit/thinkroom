/**
 * G10 · Chat 引用 [N] 反查弹窗.
 *
 * 用户在 chat 消息里点击 [N] → 调用 /api/v1/claims/{id}/citation
 *  → 弹窗展示：claim 详情 + 来源 raw_source（含 excerpt + URL + 发布人） + evidence_span 精确定位 + speaker 信息.
 *
 * 设计原则：
 * - 单 modal 文件自包含（types / hook / 组件）
 * - 数据获取由 react-query 缓存，避免重复点击重复 fetch
 * - 不破坏既有 chat 渲染（CitationChip 触发，原 navigate 行为也保留作为「查看完整知识库条目」入口）
 */
import { useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { clsx } from 'clsx'
import {
  X, ExternalLink, FileText, User, AlertTriangle, Clock, TrendingUp, TrendingDown, Minus,
  Quote, BookOpen, Loader2,
} from 'lucide-react'
import api from '@/services/api'
import { useFocusTrap } from '@/hooks/useFocusTrap'

// ── 类型 ──────────────────────────────────────────────

interface CitationClaim {
  id: string
  summary: string
  full_quote: string | null
  claim_type: string
  stance: string
  time_horizon: string | null
  asset_scope: string[] | null
  theme_scope: string[] | null
  confidence: number
  source_trust_weight: number
  is_contradicted: boolean
  contradiction_notes: string | null
  status: string
  valid_from: string | null
  valid_until: string | null
  time_sensitive: boolean
  expiry_hint: string | null
  version: number
  created_at: string | null
}

interface CitationSpeaker {
  id: string
  name: string
  trust_weight: number
  domain_tags: string[] | null
  affiliation: string | null
}

interface CitationSource {
  id: string
  source_type: string
  title: string | null
  url: string | null
  publisher: string | null
  speaker_hint: string | null
  published_at: string | null
  language: string | null
  status: string
  excerpt: string
  excerpt_truncated: boolean
  raw_text_length: number
  link_type: string | null
}

interface CitationEvidence {
  id: string
  source_id: string
  span_type: string
  content: string
  page_or_timestamp: string | null
  char_start: number | null
  char_end: number | null
  confidence: number
  extraction_method: string
  relevance_score: number
}

interface CitationResponse {
  claim: CitationClaim
  speaker: CitationSpeaker | null
  sources: CitationSource[]
  evidence_spans: CitationEvidence[]
}

// ── Hook ──────────────────────────────────────────────

export function useClaimCitation(claimId: string | null) {
  return useQuery({
    queryKey: ['claim-citation', claimId],
    queryFn: () =>
      api.get<CitationResponse>(`/api/v1/claims/${claimId}/citation`).then(r => r.data),
    enabled: !!claimId,
    staleTime: 5 * 60 * 1000, // 5min: 引用反查内容稳定，缓存避免重复 fetch
  })
}

// ── Helpers ───────────────────────────────────────────

const STANCE_META: Record<string, { color: string; bg: string; icon: JSX.Element; label: string }> = {
  bullish:     { color: 'text-emerald-400', bg: 'bg-emerald-500/15 border-emerald-500/30', icon: <TrendingUp size={11} />,   label: '看多' },
  bearish:     { color: 'text-rose-400',    bg: 'bg-rose-500/15 border-rose-500/30',       icon: <TrendingDown size={11} />, label: '看空' },
  conditional: { color: 'text-amber-400',   bg: 'bg-amber-500/15 border-amber-500/30',     icon: <Minus size={11} />,        label: '条件' },
  neutral:     { color: 'text-slate-300',   bg: 'bg-slate-500/15 border-slate-500/30',     icon: <Minus size={11} />,        label: '中性' },
}

const SOURCE_TYPE_LABEL: Record<string, string> = {
  pdf: 'PDF 报告',
  url: '网页',
  audio: '音频',
  video: '视频',
  text: '文本',
  note: '笔记',
  markdown: 'Markdown',
  docx: 'Word 文档',
  image: '图片',
  rss: 'RSS',
  twitter: 'X (Twitter)',
  weibo: '微博',
  xhs: '小红书',
  douyin: '抖音',
  bilibili: 'B 站',
  zhihu: '知乎',
  social_media: '社媒',
  notion: 'Notion',
  email: '邮件',
}

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

function isExpired(c: CitationClaim): boolean {
  if (!c.valid_until) return false
  return new Date(c.valid_until).getTime() < Date.now()
}

function timeHorizonLabel(value?: string | null): string {
  if (!value) return ''
  return ({ short: '短期', medium: '中期', long: '长期' } as Record<string, string>)[value] ?? value
}

// ── 主组件 ────────────────────────────────────────────

interface CitationModalProps {
  claimId: string | null
  onClose: () => void
}

export default function CitationModal({ claimId, onClose }: CitationModalProps) {
  const navigate = useNavigate()
  const { data, isLoading, error } = useClaimCitation(claimId)
  const cardRef = useRef<HTMLDivElement>(null)
  useFocusTrap(cardRef, !!claimId)

  // ESC 关闭
  useEffect(() => {
    if (!claimId) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [claimId, onClose])

  if (!claimId) return null

  const claim = data?.claim
  const stance = claim ? (STANCE_META[claim.stance] ?? STANCE_META.neutral) : STANCE_META.neutral
  const expired = claim ? isExpired(claim) : false

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* backdrop */}
      <div
        onClick={onClose}
        className="absolute inset-0 bg-background/60 backdrop-blur-sm"
      />

      {/* modal card */}
      <div
        ref={cardRef}
        className="relative w-full max-w-3xl max-h-[88vh] flex flex-col rounded-xl bg-card border border-border shadow-2xl overflow-hidden"
        role="dialog"
        aria-modal="true"
        aria-labelledby="citation-modal-title"
      >
        {/* Header */}
        <div className="sticky top-0 z-10 flex items-start justify-between gap-3 px-5 py-3.5 border-b border-border bg-card">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 mb-1">
              <BookOpen size={14} className="text-primary shrink-0" aria-hidden="true" />
              <h2 id="citation-modal-title" className="text-sm font-semibold">引用来源</h2>
              {claim && (
                <>
                  <span className={clsx('inline-flex items-center gap-1 px-1.5 py-0.5 rounded border text-[10px] font-medium', stance.color, stance.bg)}>
                    {stance.icon}{stance.label}
                  </span>
                  {claim.is_contradicted && (
                    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded border border-amber-500/30 bg-amber-500/15 text-amber-400 text-[10px]">
                      <AlertTriangle size={10} />存在反驳
                    </span>
                  )}
                  {expired && (
                    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded border border-rose-500/30 bg-rose-500/15 text-rose-400 text-[10px]">
                      <Clock size={10} />已过期
                    </span>
                  )}
                </>
              )}
            </div>
            {claim && <p className="text-xs text-foreground/85 leading-relaxed">{claim.summary}</p>}
          </div>
          <button
            onClick={onClose}
            className="shrink-0 p-1.5 rounded-md hover:bg-secondary text-muted-foreground hover:text-foreground transition-colors"
            aria-label="关闭"
          >
            <X size={16} />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {isLoading && (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
              <span className="ml-2 text-xs text-muted-foreground">加载引用反查...</span>
            </div>
          )}

          {error && (
            <div className="rounded-lg bg-rose-500/10 border border-rose-500/30 p-3 text-xs text-rose-400 flex items-start gap-2">
              <AlertTriangle size={14} className="shrink-0 mt-0.5" />
              <div>
                <div className="font-medium mb-0.5">加载失败</div>
                <div className="opacity-85">{error instanceof Error ? error.message : '未知错误'}</div>
              </div>
            </div>
          )}

          {data && (
            <>
              <Section title="观点与原文">
                {claim?.full_quote && (
                  <div className="mb-3 rounded-md bg-muted/40 border-l-2 border-primary/50 px-3 py-2">
                    <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground/80 mb-1 uppercase tracking-wide">
                      <Quote size={10} />原文引用
                    </div>
                    <p className="text-xs text-foreground/85 leading-relaxed italic">
                      「{claim.full_quote}」
                    </p>
                  </div>
                )}
                <div className="flex flex-wrap gap-2 text-[11px] text-muted-foreground">
                  {claim?.time_horizon && <span className="rounded bg-secondary px-2 py-1">适用时间：{timeHorizonLabel(claim.time_horizon)}</span>}
                  {claim?.asset_scope && claim.asset_scope.length > 0 && <span className="rounded bg-secondary px-2 py-1">涉及：{claim.asset_scope.join('、')}</span>}
                  {claim?.valid_until && (
                    <span className={clsx('rounded bg-secondary px-2 py-1', expired && 'text-rose-400')}>
                      有效至：{formatDate(claim.valid_until)}
                    </span>
                  )}
                </div>
                {claim?.contradiction_notes && (
                  <div className="mt-3 rounded-md bg-amber-500/10 border border-amber-500/30 p-2.5 text-[11px] text-amber-300/90 leading-relaxed">
                    <div className="flex items-center gap-1.5 mb-1 font-medium">
                      <AlertTriangle size={11} />反驳记录
                    </div>
                    {claim.contradiction_notes}
                  </div>
                )}
                {claim?.expiry_hint && (
                  <div className="mt-2 rounded-md bg-secondary/60 px-2.5 py-2 text-[11px] text-muted-foreground">
                    时效提示：{claim.expiry_hint}
                  </div>
                )}
              </Section>

              {/* Speaker */}
              {data.speaker && (
                <Section title="发言人">
                  <div className="flex items-start gap-3">
                    <div className="w-8 h-8 rounded-full bg-primary/20 flex items-center justify-center shrink-0">
                      <User size={14} className="text-primary" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-sm font-medium">{data.speaker.name}</span>
                      </div>
                      {data.speaker.affiliation && (
                        <p className="text-[11px] text-muted-foreground mt-0.5">{data.speaker.affiliation}</p>
                      )}
                      {data.speaker.domain_tags && data.speaker.domain_tags.length > 0 && (
                        <div className="flex flex-wrap gap-1 mt-1.5">
                          {data.speaker.domain_tags.map(t => (
                            <span key={t} className="px-1.5 py-0.5 rounded bg-secondary text-[10px]">{t}</span>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                </Section>
              )}

              {data.sources.length > 0 && (
                <Section title={`来源资料（${data.sources.length}）`}>
                  <div className="space-y-2.5">
                    {data.sources.map(src => (
                      <SourceCard key={src.id} source={src} />
                    ))}
                  </div>
                </Section>
              )}

              {data.evidence_spans.length > 0 && (
                <Section title={`原文位置（${data.evidence_spans.length}）`}>
                  <div className="space-y-2">
                    {data.evidence_spans.map(ev => (
                      <EvidenceCard key={ev.id} evidence={ev} />
                    ))}
                  </div>
                </Section>
              )}

              {data.sources.length === 0 && data.evidence_spans.length === 0 && (
                <div className="text-center py-8 text-xs text-muted-foreground">
                  暂无可追溯的来源或原文位置
                </div>
              )}
            </>
          )}
        </div>

        {/* Footer */}
        <div className="shrink-0 flex items-center justify-between gap-2 px-5 py-3 border-t border-border bg-muted/20">
          <div className="text-[10px] text-muted-foreground/60">
            按 <kbd className="px-1 py-0.5 rounded border border-border font-mono">ESC</kbd> 关闭
          </div>
          {claim && (
            <button
              onClick={() => {
                onClose()
                navigate(`/knowledge?claim=${encodeURIComponent(claim.id)}`)
              }}
              className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-[11px] text-primary hover:bg-primary/10 transition-colors"
            >
              <ExternalLink size={11} />
              在资料库中查看
            </button>
          )}
        </div>
      </div>
    </div>,
    document.body,
  )
}

// ── 子组件 ─────────────────────────────────────────────

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground/70 mb-2">
        {title}
      </h3>
      {children}
    </div>
  )
}

function SourceCard({ source }: { source: CitationSource }) {
  const typeLabel = SOURCE_TYPE_LABEL[source.source_type] ?? source.source_type
  return (
    <div className="rounded-lg border border-border bg-secondary/20 p-3">
      <div className="flex items-start gap-2 mb-2">
        <FileText size={13} className="text-primary shrink-0 mt-0.5" />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 flex-wrap mb-0.5">
            <span className="px-1.5 py-0.5 rounded bg-secondary text-[10px] font-medium">{typeLabel}</span>
          </div>
          {source.title && (
            <p className="text-xs font-medium text-foreground leading-snug">{source.title}</p>
          )}
          <div className="flex items-center gap-2 flex-wrap text-[10px] text-muted-foreground mt-1">
            {source.publisher && <span>{source.publisher}</span>}
            {source.speaker_hint && <span>· {source.speaker_hint}</span>}
            {source.published_at && <span>· {formatDate(source.published_at)}</span>}
          </div>
        </div>
        {source.url && (
          <a
            href={source.url}
            target="_blank"
            rel="noreferrer noopener"
            className="shrink-0 inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px] text-primary hover:bg-primary/10 transition-colors"
            title="打开原始链接"
          >
            <ExternalLink size={11} />
            打开
          </a>
        )}
      </div>
      {source.excerpt && (
        <details className="mt-2">
          <summary className="cursor-pointer text-[10px] text-muted-foreground/80 hover:text-foreground select-none">
            查看原文摘录
          </summary>
          <div className="mt-1.5 rounded-md bg-background/40 border border-border/40 p-2.5 text-[11px] leading-relaxed text-foreground/80 whitespace-pre-wrap font-sans max-h-60 overflow-y-auto">
            {source.excerpt}
            {source.excerpt_truncated && (
              <span className="text-muted-foreground/60 italic"> ...（已截断）</span>
            )}
          </div>
        </details>
      )}
    </div>
  )
}

function EvidenceCard({ evidence }: { evidence: CitationEvidence }) {
  return (
    <div className="rounded-md bg-primary/5 border-l-2 border-primary/40 px-3 py-2">
      {evidence.page_or_timestamp && (
        <div className="mb-1 text-[10px] text-muted-foreground/80">
          原文位置：{evidence.page_or_timestamp}
        </div>
      )}
      <p className="text-[11px] text-foreground/85 leading-relaxed">{evidence.content}</p>
    </div>
  )
}
