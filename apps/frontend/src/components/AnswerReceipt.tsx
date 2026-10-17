import { useState } from 'react'
import {
  Database, Clock, Activity, AlertTriangle, ChevronDown, ChevronUp,
  CheckCircle2, XCircle, ShieldCheck, Eye, Search, Layers,
  Info, Lightbulb,
} from 'lucide-react'
import { clsx } from 'clsx'

// ── 类型定义 ─────────────────────────────────────────────────────

export interface StanceMismatch {
  index: number
  claim_stance: string          // 被引用 claim 的立场
  text_direction: string        // 引用文本的方向
  sentence: string              // 引用所在的句子
}

export interface ReceiptData {
  mode: 'chat' | 'briefing' | 'debate'
  /** 数据类型命中情况 */
  dataHits?: {
    claims?: number       // 私域观点条数 (0 = 无)
    market?: boolean      // 行情数据
    events?: number       // 事件数
    flow?: number         // 资金流信号数
    web?: number          // Web搜索数
    macro?: boolean       // 宏观数据
  }
  /** 数据时间戳 */
  dataTime?: string       // ISO 格式或人类可读
  /** 是否模拟数据 */
  isMock?: boolean
  /** 模型名称 */
  model?: string
  /** 花费 */
  costUsd?: number
  /** 耗时 ms */
  durationMs?: number
  /** 不确定性 / 风险提示 */
  warnings?: string[]
  /** 结构性警告 */
  structureWarnings?: string[]
  /** 证据质量警告 */
  evidenceWarnings?: string[]
  /** 证据充分性（后端 quality_filters.evidence_sufficiency） */
  evidenceSufficiency?: string
  /** 证据不足原因（后端 quality_filters.insufficient_evidence_reason） */
  insufficientEvidenceReason?: string
  /** B4: 引用立场方向校验结果（warn-only） */
  stanceMismatches?: StanceMismatch[]
  /** 参与席位列表 */
  activatedSeats?: string[]
  /** 共识水平（debate 模式） */
  consensusLevel?: string
  /** 判断过程描述（可选，由调用方提供） */
  processSteps?: string[]
}

// ── 翻译层：后端字段 → 用户自然语言 ──────────────────────────────

const SEAT_LABELS: Record<string, string> = {
  baseline: '基准分析', probability: '概率评估', risk: '风险评估',
  structure: '结构分析', audit: '证据审计', pnl: '执行核算',
  bull: '多方分析', bear: '空方分析', neutral: '中立分析',
}

const CONSENSUS_TEXT: Record<string, { text: string; cls: string }> = {
  strong_consensus:   { text: '观点一致', cls: 'text-emerald-400/70' },
  moderate_consensus: { text: '基本一致', cls: 'text-foreground/50' },
  weak_consensus:     { text: '存在分歧', cls: 'text-amber-400/70' },
  split:              { text: '严重分歧', cls: 'text-red-400/70' },
}

// ── 模式标签 ──────────────────────────────────────────────────────

const MODE_LABELS: Record<string, { label: string; cls: string }> = {
  chat:     { label: '快速问答', cls: 'text-sky-400 border-sky-400/30 bg-sky-400/5' },
  briefing: { label: '深度研究', cls: 'text-violet-400 border-violet-400/30 bg-violet-400/5' },
  debate:   { label: '委员会分析', cls: 'text-amber-400 border-amber-400/30 bg-amber-400/5' },
}

function insufficientEvidenceReasonText(reason?: string): string {
  switch (reason) {
    case 'selected_sources_query_mismatch':
      return '选定资料与本次问题不匹配，不能仅凭这些资料给出方向性结论'
    case 'selected_sources_actionable_question_requires_directional_evidence':
      return '选定资料提到了相关对象，但没有足够的涨跌方向、操作依据或交易建议证据'
    case 'selected_sources_temporal_question_requires_time_window_evidence':
      return '选定资料提到了相关对象和方向，但没有明确日期、近期性或预测周期，不能支撑本次时间窗口'
    case 'selected_sources_temporal_evidence_is_stale_for_current_question':
      return '选定资料有时间锚和方向性内容，但时间锚点过旧、时效性不足，不能支撑当前或下周的判断'
    case 'selected_sources_temporal_horizon_mismatch':
      return '选定资料有时间锚和方向性内容，但资料中的预测周期与本次问题要求的时间跨度不一致'
    case 'selected_sources_claim_validity_window_mismatch':
      return '选定资料已形成结构化观点，但结构化观点尚未生效或已经超过有效期'
    case 'selected_sources_claim_temporal_horizon_mismatch':
      return '选定资料已形成结构化观点，但结构化观点的时间跨度与本次问题要求不一致'
    default:
      return '现有证据不足以支撑可执行结论'
  }
}

// ── 辅助：构建人类可读的数据源摘要 ──────────────────────────────

function buildDataSummary(dh: ReceiptData['dataHits']): string {
  if (!dh) return '未查外部数据'
  const parts: string[] = []
  if ((dh.claims ?? 0) > 0) parts.push(`${dh.claims} 条历史观点`)
  if (dh.market) parts.push('市场行情')
  if ((dh.events ?? 0) > 0) parts.push(`${dh.events} 条相关新闻`)
  if ((dh.flow ?? 0) > 0) parts.push(`${dh.flow} 条资金流信号`)
  if ((dh.web ?? 0) > 0) parts.push(`${dh.web} 条网络搜索`)
  if (dh.macro) parts.push('宏观指标')
  if (parts.length === 0) return '未查外部数据'
  return `已参考 ${parts.join(' + ')}`
}

// ── 辅助：构建"这次用了什么"列表 ────────────────────────────────

function buildUsedList(data: ReceiptData): string[] {
  if (data.evidenceSufficiency === 'insufficient') {
    return ['已检查用户限定的资料范围', insufficientEvidenceReasonText(data.insufficientEvidenceReason)]
  }
  const dh = data.dataHits ?? {}
  const items: string[] = []
  if ((dh.claims ?? 0) > 0) items.push(`使用了 ${dh.claims} 条历史观点作为参考`)
  if (dh.market) items.push('检查了当前市场价格和走势')
  if ((dh.events ?? 0) > 0) items.push(`检查了最近 ${dh.events} 条相关新闻`)
  if ((dh.flow ?? 0) > 0) items.push(`使用了 ${dh.flow} 条资金流信号`)
  if ((dh.web ?? 0) > 0) items.push(`进行了 ${dh.web} 次网络搜索`)
  if (dh.macro) items.push('参考了宏观经济指标')
  if (items.length === 0) items.push('本次未使用外部数据')
  // 负面项
  if ((dh.claims ?? 0) === 0 && data.mode !== 'chat') items.push('未获取到相关历史观点')
  if (!dh.market && data.mode !== 'chat') items.push('未获取到实时行情数据')
  if ((dh.flow ?? 0) === 0 && data.mode === 'debate') items.push('未获取到资金流数据')
  return items
}

// ── 辅助：构建"怎么判断"列表 ────────────────────────────────────

function buildHowList(data: ReceiptData): string[] {
  if (data.evidenceSufficiency === 'insufficient') {
    return ['识别到用户要求只根据选定资料回答', '检索选定资料并进行证据充分性检查', '未找到足够支撑结论的证据，返回保守提示']
  }
  if (data.processSteps && data.processSteps.length > 0) return data.processSteps
  const steps: string[] = []
  if (data.mode === 'chat') {
    steps.push('在知识库中搜索相关历史观点')
    steps.push('结合上下文生成回答')
  } else if (data.mode === 'briefing') {
    steps.push('分析问题，制定研究计划')
    steps.push('调取实时市场数据和相关新闻')
    steps.push('深度推演，生成结构化研究简报')
  } else {
    steps.push('多个独立分析角色分别研判')
    if (data.activatedSeats && data.activatedSeats.length > 0) {
      const labels = data.activatedSeats.map(s => SEAT_LABELS[s] ?? s).join('、')
      steps.push(`参与分析：${labels}`)
    }
    steps.push('独立裁决官综合各方观点得出结论')
    if (data.consensusLevel) {
      const ct = CONSENSUS_TEXT[data.consensusLevel]
      steps.push(ct ? `各方${ct.text}` : `共识水平：${data.consensusLevel}`)
    }
  }
  return steps
}

// ── 辅助：构建"哪里不确定"列表（翻译层核心） ────────────────────

function buildUncertaintyList(data: ReceiptData): { text: string; type: 'risk' | 'structure' | 'evidence' | 'data' | 'citation' }[] {
  const items: { text: string; type: 'risk' | 'structure' | 'evidence' | 'data' | 'citation' }[] = []
  if (data.evidenceSufficiency === 'insufficient') {
    items.push({
      text: insufficientEvidenceReasonText(data.insufficientEvidenceReason),
      type: 'evidence',
    })
  }
  data.warnings?.forEach(w => items.push({ text: w, type: 'risk' }))
  data.structureWarnings?.forEach(w => items.push({ text: w, type: 'structure' }))
  data.evidenceWarnings?.forEach(w => items.push({ text: w, type: 'evidence' }))
  // B4: 引用立场方向不一致（警告不改文本）
  const mismatches = data.stanceMismatches ?? []
  const stanceTxt: Record<string, string> = {
    bullish: '看涨', bearish: '看空', neutral: '中性', conditional: '有条件',
  }
  const dirTxt: Record<string, string> = {
    bullish: '看涨语气', bearish: '看空语气', neutral: '中性语气',
  }
  mismatches.slice(0, 3).forEach(m => {
    const cs = stanceTxt[m.claim_stance] ?? m.claim_stance
    const td = dirTxt[m.text_direction] ?? m.text_direction
    items.push({
      text: `引用 [${m.index}] 来源立场为 ${cs}，但回答中以 ${td} 使用`,
      type: 'citation',
    })
  })
  if (mismatches.length > 3) {
    items.push({
      text: `还有 ${mismatches.length - 3} 条引用立场方向不一致`,
      type: 'citation',
    })
  }
  // 数据覆盖不完整
  const dh = data.dataHits ?? {}
  if (data.mode !== 'chat') {
    if ((dh.claims ?? 0) === 0 && !dh.market && (dh.events ?? 0) === 0)
      items.push({ text: '本次数据覆盖不完整，结论可靠性可能受限', type: 'data' })
  }
  return items
}

// ── 数据类型 Badge ────────────────────────────────────────────────

function DataBadge({ label, active, count }: { label: string; active: boolean; count?: number }) {
  return (
    <span className={clsx(
      'inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] border transition-colors',
      active
        ? 'text-emerald-400 border-emerald-400/30 bg-emerald-400/5'
        : 'text-muted-foreground/40 border-border bg-transparent',
    )}>
      {active ? <CheckCircle2 size={8} className="shrink-0" /> : <XCircle size={8} className="shrink-0" />}
      {label}{count != null && count > 0 && active ? ` ×${count}` : ''}
    </span>
  )
}

// ── 查看过程：三段式面板 ─────────────────────────────────────────

function ProcessSection({ data }: { data: ReceiptData }) {
  const used = buildUsedList(data)
  const how = buildHowList(data)
  const uncertain = buildUncertaintyList(data)

  const typeIcon: Record<string, JSX.Element> = {
    risk: <AlertTriangle size={10} className="text-amber-400 mt-0.5 shrink-0" />,
    structure: <Activity size={10} className="text-cyan-400 mt-0.5 shrink-0" />,
    evidence: <ShieldCheck size={10} className="text-orange-400 mt-0.5 shrink-0" />,
    data: <Database size={10} className="text-slate-400 mt-0.5 shrink-0" />,
    citation: <AlertTriangle size={10} className="text-rose-400 mt-0.5 shrink-0" />,
  }

  return (
    <div className="space-y-2.5 pt-1.5 border-t border-border/40">
      {/* ① 这次用了什么 */}
      <div>
        <p className="text-[10px] font-semibold text-muted-foreground/60 flex items-center gap-1 mb-1">
          <Search size={9} />这次参考了
        </p>
        {used.map((item, i) => (
          <p key={i} className="text-[10px] text-foreground/60 flex items-start gap-1.5 leading-relaxed pl-0.5">
            <CheckCircle2 size={9} className={clsx('mt-0.5 shrink-0', item.startsWith('未') ? 'text-muted-foreground/30' : 'text-emerald-400/60')} />
            <span>{item}</span>
          </p>
        ))}
      </div>

      {/* ② 这次怎么判断 */}
      <div>
        <p className="text-[10px] font-semibold text-muted-foreground/60 flex items-center gap-1 mb-1">
          <Lightbulb size={9} />判断过程
        </p>
        {how.map((step, i) => (
          <p key={i} className="text-[10px] text-foreground/60 flex items-start gap-1.5 leading-relaxed pl-0.5">
            <span className="text-primary/40 mt-0.5 shrink-0 text-[9px] font-mono w-3 text-right">{i + 1}.</span>
            <span>{step}</span>
          </p>
        ))}
      </div>

      {/* ③ 哪里不确定 */}
      {uncertain.length > 0 && (
        <div>
          <p className="text-[10px] font-semibold text-amber-400/60 flex items-center gap-1 mb-1">
            <AlertTriangle size={9} />仍有不确定性
          </p>
          {uncertain.slice(0, 4).map((item, i) => (
            <p key={i} className="text-[10px] text-foreground/60 flex items-start gap-1.5 leading-relaxed pl-0.5">
              {typeIcon[item.type]}
              <span>{item.text}</span>
            </p>
          ))}
          {uncertain.length > 4 && (
            <p className="text-[10px] text-muted-foreground/40 pl-4">...还有 {uncertain.length - 4} 条</p>
          )}
        </div>
      )}
    </div>
  )
}

// ── 主组件 ────────────────────────────────────────────────────────

export default function AnswerReceipt({ data }: { data: ReceiptData }) {
  const [expanded, setExpanded] = useState(false)
  const [showTech, setShowTech] = useState(false)
  const modeInfo = MODE_LABELS[data.mode] ?? MODE_LABELS.chat
  const dh = data.dataHits ?? {}
  const hasAnyData = (dh.claims ?? 0) > 0 || dh.market || (dh.events ?? 0) > 0 || (dh.flow ?? 0) > 0 || (dh.web ?? 0) > 0
  const evidenceInsufficient = data.evidenceSufficiency === 'insufficient'
  const stanceMismatchCount = data.stanceMismatches?.length ?? 0
  const warningCount = (data.warnings?.length ?? 0) + (data.structureWarnings?.length ?? 0) + (data.evidenceWarnings?.length ?? 0) + (evidenceInsufficient ? 1 : 0)
  const dataSummary = evidenceInsufficient ? '选定资料不足' : buildDataSummary(dh)
  const consensus = data.consensusLevel ? CONSENSUS_TEXT[data.consensusLevel] : null

  return (
    <div className="mt-1.5 bg-secondary/20 border border-border/60 rounded-lg overflow-hidden">
      {/* 回执主行 — 用户语言摘要 */}
      <button
        onClick={() => setExpanded(v => !v)}
        className="w-full flex items-center gap-2 px-3 py-1.5 text-[10px] hover:bg-secondary/40 transition-colors"
      >
        <Eye size={10} className="text-muted-foreground/50 shrink-0" />

        {/* 模式标签 */}
        <span className={clsx('border rounded px-1.5 py-0.5 font-medium shrink-0', modeInfo.cls)}>
          {modeInfo.label}
        </span>

        {/* 数据源摘要（用户语言） */}
        <span className="flex items-center gap-1 text-muted-foreground/60">
          <Database size={9} />
          {evidenceInsufficient ? (
            <span className="text-amber-400/80">{dataSummary}</span>
          ) : hasAnyData ? (
            <span className="text-emerald-400/80">{dataSummary}</span>
          ) : (
            <span className="text-muted-foreground/40">{dataSummary}</span>
          )}
        </span>

        {/* 时间 */}
        {data.dataTime && (
          <span className="flex items-center gap-0.5 text-muted-foreground/50">
            <Clock size={8} />
            {data.dataTime}
          </span>
        )}

        {/* 模拟标识 */}
        {data.isMock && (
          <span className="text-amber-400/60 border border-amber-400/20 rounded px-1 py-0.5">模拟</span>
        )}

        {/* 共识/分歧指示（debate 模式） */}
        {consensus && (
          <span className={clsx('flex items-center gap-0.5', consensus.cls)}>
            <Layers size={8} />
            <span>{consensus.text}</span>
          </span>
        )}

        {/* 不确定性指示 */}
        {warningCount > 0 && (
          <span className="flex items-center gap-0.5 text-amber-400/60">
            <AlertTriangle size={8} />
            <span>{warningCount} 项注意</span>
          </span>
        )}

        {/* B4: 引用立场警告（醒目一点，用红色区分） */}
        {stanceMismatchCount > 0 && (
          <span
            className="flex items-center gap-0.5 text-rose-400/70"
            title="部分引用的观点立场与回答中的使用方向不一致（仅提示，不改文本）"
          >
            <AlertTriangle size={8} />
            <span>引用立场 {stanceMismatchCount} 条不一致</span>
          </span>
        )}

        <span className="ml-auto text-muted-foreground/30">
          {expanded ? <ChevronUp size={10} /> : <ChevronDown size={10} />}
        </span>
      </button>

      {/* 展开详情 */}
      {expanded && (
        <div className="px-3 pb-2.5 space-y-2 border-t border-border/40">
          {/* 数据类型 badge */}
          <div className="flex flex-wrap gap-1.5 pt-1.5">
            <DataBadge label="历史观点" active={(dh.claims ?? 0) > 0} count={dh.claims} />
            <DataBadge label="行情" active={!!dh.market} />
            <DataBadge label="新闻" active={(dh.events ?? 0) > 0} count={dh.events} />
            <DataBadge label="资金流" active={(dh.flow ?? 0) > 0} count={dh.flow} />
            <DataBadge label="网络搜索" active={(dh.web ?? 0) > 0} count={dh.web} />
            <DataBadge label="宏观" active={!!dh.macro} />
          </div>

          {/* 参与席位（用中文标签） */}
          {data.activatedSeats && data.activatedSeats.length > 0 && (
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className="text-[10px] text-muted-foreground/50">参与分析：</span>
              {data.activatedSeats.map(s => (
                <span key={s} className="text-[10px] font-medium text-foreground/60 bg-secondary/60 border border-border rounded px-1.5 py-0.5">
                  {SEAT_LABELS[s] ?? s}
                </span>
              ))}
            </div>
          )}

          {/* 查看过程：三段式 */}
          <ProcessSection data={data} />

          {/* 技术信息（默认折叠） */}
          {(data.model || data.costUsd != null || data.durationMs != null) && (
            <div className="pt-1 border-t border-border/30">
              <button
                onClick={(e) => { e.stopPropagation(); setShowTech(v => !v) }}
                className="text-[9px] text-muted-foreground/30 hover:text-muted-foreground/50 flex items-center gap-0.5 transition-colors"
              >
                <Info size={8} />{showTech ? '隐藏技术信息' : '技术信息'}
              </button>
              {showTech && (
                <div className="flex items-center gap-3 text-[10px] text-muted-foreground/40 font-mono mt-1">
                  {data.model && <span>{data.model}</span>}
                  {data.costUsd != null && <span>~${data.costUsd.toFixed(5)}</span>}
                  {data.durationMs != null && <span>{(data.durationMs / 1000).toFixed(1)}s</span>}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
