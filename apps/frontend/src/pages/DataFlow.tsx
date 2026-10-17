import { useState } from 'react'
import { GitBranch, Zap, ChevronRight, Info } from 'lucide-react'
import { clsx } from 'clsx'

// ── Data flow definition ─────────────────────────────────────────────────────
const PIPELINE_STAGES = [
  {
    id: 'ingest',
    label: '上传 & 解析',
    icon: '📥',
    color: 'from-violet-500/20 to-violet-500/5 border-violet-500/30',
    accent: 'text-violet-400',
    dot: 'bg-violet-500',
    nodes: [
      { id: 'upload',     label: '文件上传',       detail: 'PDF / 图片 / 文本 / URL',                         type: 'input'   },
      { id: 'ocr',        label: 'OCR / 文本提取', detail: 'file_parser.py · Vision LLM · pdfplumber',      type: 'process' },
      { id: 'raw_source', label: 'RawSource 入库', detail: 'storage_path + raw_text + article_summary',    type: 'store'   },
    ],
    edges: [['upload','ocr'], ['ocr','raw_source']],
  },
  {
    id: 'extraction',
    label: '知识提取',
    icon: '🧠',
    color: 'from-blue-500/20 to-blue-500/5 border-blue-500/30',
    accent: 'text-blue-400',
    dot: 'bg-blue-500',
    nodes: [
      { id: 'summary',      label: '文章摘要生成', detail: '三级兜底：L1直接/L2分段/L3采样',               type: 'process' },
      { id: 'chunks',       label: '文本分块',     detail: '_split_chunks() · 32000字/块',                 type: 'process' },
      { id: 'claim_extract',label: 'Claim 提取',   detail: 'ClaimExtractionSkill · deepseek-v3.1',          type: 'process' },
      { id: 'claim_store',  label: 'ClaimCard 入库',detail: 'asset_scope 中英文并列 · valid_until',         type: 'store'   },
      { id: 'embedding',    label: 'Embedding 索引',detail: 'text-embedding-v4 · dim=1024 · pgvector',      type: 'process' },
    ],
    edges: [['summary','chunks'], ['chunks','claim_extract'], ['claim_extract','claim_store'], ['claim_store','embedding']],
  },
  {
    id: 'retrieval',
    label: '召回 & 意图识别',
    icon: '🔍',
    color: 'from-cyan-500/20 to-cyan-500/5 border-cyan-500/30',
    accent: 'text-cyan-400',
    dot: 'bg-cyan-500',
    nodes: [
      { id: 'intent',   label: '意图识别',     detail: 'IntentRecognitionSkill · 规则层+LLM层',          type: 'process' },
      { id: 'alias',    label: '别名展开',     detail: 'ALIAS_MAP · 石油→OIL/原油/crude',               type: 'process' },
      { id: 'semantic', label: '语义搜索',     detail: 'pgvector L2距离 / 关键词降级',                   type: 'process' },
      { id: 'mcp_ctx',  label: 'MCP 上下文',   detail: 'ResearchMemory + EventFeed + MarketData',        type: 'process' },
    ],
    edges: [['intent','alias'], ['alias','semantic'], ['intent','mcp_ctx']],
  },
  {
    id: 'synthesis',
    label: '分析合成',
    icon: '⚗️',
    color: 'from-amber-500/20 to-amber-500/5 border-amber-500/30',
    accent: 'text-amber-400',
    dot: 'bg-amber-500',
    nodes: [
      { id: 'fusion',   label: 'FusionSkill',  detail: '多源融合 · 方向判断 · Setup质量',                type: 'process' },
      { id: 'briefing', label: 'BriefingSkill', detail: '简报生成 · 支撑/反驳/风险',                      type: 'process' },
      { id: 'debate',   label: '委员会辩论',   detail: 'Bull/Bear/Neutral · FinalAdjudication',           type: 'process' },
      { id: 'chat_llm', label: '研究问答',     detail: 'chat.py · 私域上下文注入 · 历史对话',            type: 'process' },
    ],
    edges: [['fusion','briefing'], ['fusion','debate'], ['fusion','chat_llm']],
  },
  {
    id: 'output',
    label: '输出 & 存储',
    icon: '📊',
    color: 'from-emerald-500/20 to-emerald-500/5 border-emerald-500/30',
    accent: 'text-emerald-400',
    dot: 'bg-emerald-500',
    nodes: [
      { id: 'briefing_card', label: 'BriefingCard',  detail: '简报存库 · job_run · cost_usd',                type: 'store'   },
      { id: 'debate_record', label: 'DebateRecord',   detail: '辩论记录 · 轮次 · 裁决',                       type: 'store'   },
      { id: 'chat_msg',      label: 'ChatMessage',    detail: 'payload · model_used · cost_usd',               type: 'store'   },
      { id: 'lifecycle',     label: 'Lifecycle 管理', detail: 'LifecycleSkill · expired/invalidated',           type: 'process' },
    ],
    edges: [['briefing_card','lifecycle'], ['chat_msg','lifecycle']],
  },
]

const CROSS_STAGE_EDGES = [
  { from: 'raw_source',   to: 'summary',       label: 'raw_text'        },
  { from: 'claim_store',  to: 'semantic',      label: 'ClaimCard[]'     },
  { from: 'semantic',     to: 'fusion',        label: 'relevant claims' },
  { from: 'mcp_ctx',      to: 'fusion',        label: 'market+events'   },
  { from: 'briefing',     to: 'briefing_card', label: 'BriefingOutput'  },
  { from: 'debate',       to: 'debate_record', label: 'DebateRounds'    },
  { from: 'chat_llm',     to: 'chat_msg',      label: 'reply+usage'     },
]

const NODE_TYPE_STYLE: Record<string, string> = {
  input:   'border-dashed border-border/60 bg-background/40',
  process: 'border-border/80 bg-card/80',
  store:   'border-border bg-muted/40',
}
const NODE_TYPE_LABEL: Record<string, string> = {
  input: 'INPUT', process: 'PROCESS', store: 'STORE',
}

// ── Component ────────────────────────────────────────────────────────────────
export default function DataFlowPage() {
  const [activeNode, setActiveNode] = useState<string | null>(null)
  const [expandedStage, setExpandedStage] = useState<string | null>(null)

  const allNodes = PIPELINE_STAGES.flatMap(s =>
    s.nodes.map(n => ({ ...n, stageId: s.id, stageAccent: s.accent, stageDot: s.dot }))
  )
  const activeNodeData = allNodes.find(n => n.id === activeNode)

  return (
    <div className="p-6 2xl:p-8 max-w-[1400px] 2xl:max-w-[1760px] mx-auto space-y-5">
      <div className="page-header !mb-5 !pb-4">
        <span className="section-label">Data Flow</span>
        <h1 className="text-2xl font-semibold text-foreground flex items-center gap-2">
          <GitBranch size={20} className="text-primary" />
          数据流诊断
        </h1>
        <p className="text-sm text-muted-foreground mt-1">系统完整数据处理管道 — 从上传到输出的全链路节点与数据流转</p>
      </div>

      <div className="flex items-center gap-5 flex-wrap">
        {Object.entries(NODE_TYPE_LABEL).map(([type, label]) => (
          <div key={type} className="flex items-center gap-1.5">
            <div className={clsx('w-3 h-3 rounded border', NODE_TYPE_STYLE[type])} />
            <span className="text-[11px] font-mono text-muted-foreground">{label}</span>
          </div>
        ))}
        <div className="flex items-center gap-1.5">
          <div className="w-6 h-px border-t border-dashed border-muted-foreground/40" />
          <span className="text-[11px] font-mono text-muted-foreground">数据流向</span>
        </div>
      </div>

      <div className="space-y-2">
        {PIPELINE_STAGES.map((stage, si) => (
          <div key={stage.id} className={clsx('rounded-2xl border bg-gradient-to-r p-4 transition-all duration-200', stage.color)}>
            <button className="flex items-center gap-2 w-full text-left mb-3"
              onClick={() => setExpandedStage(s => s === stage.id ? null : stage.id)}>
              <span className="text-sm">{stage.icon}</span>
              <span className={clsx('text-xs font-semibold tracking-wide', stage.accent)}>
                {String(si + 1).padStart(2,'0')} — {stage.label}
              </span>
              <span className="text-[10px] font-mono text-muted-foreground/50 ml-1">{stage.nodes.length} 节点</span>
              <ChevronRight size={12} className={clsx('ml-auto text-muted-foreground transition-transform duration-150', expandedStage === stage.id ? 'rotate-90' : '')} />
            </button>
            <div className="flex items-stretch gap-2 flex-wrap">
              {stage.nodes.map((node) => (
                <div key={node.id} className="flex items-center gap-1">
                  <button
                    onClick={() => setActiveNode(n => n === node.id ? null : node.id)}
                    className={clsx(
                      'rounded-xl border px-3 py-2 text-left transition-all duration-150',
                      NODE_TYPE_STYLE[node.type],
                      activeNode === node.id ? 'ring-1 ring-primary scale-[1.03] shadow-lg' : 'hover:scale-[1.01] hover:border-border'
                    )}
                    style={{ minWidth: 130, maxWidth: 180 }}
                  >
                    <div className="flex items-center gap-1.5 mb-0.5">
                      <div className={clsx('w-1.5 h-1.5 rounded-full shrink-0', stage.dot)} />
                      <span className="text-xs font-medium text-foreground truncate">{node.label}</span>
                    </div>
                    <span className="text-[10px] text-muted-foreground leading-snug line-clamp-2 block">{node.detail}</span>
                    <span className={clsx('text-[9px] font-mono mt-1 block', stage.accent)}>{NODE_TYPE_LABEL[node.type]}</span>
                  </button>
                  {stage.edges.some(([f]) => f === node.id) && (
                    <div className="flex items-center gap-0">
                      <div className="w-4 h-px border-t border-dashed border-muted-foreground/40" />
                      <ChevronRight size={9} className="text-muted-foreground/40" />
                    </div>
                  )}
                </div>
              ))}
            </div>
            {expandedStage === stage.id && stage.edges.length > 0 && (
              <div className="mt-3 pt-3 border-t border-border/40 flex flex-wrap gap-2">
                {stage.edges.map(([f, t], i) => (
                  <div key={i} className="flex items-center gap-1 text-[11px] font-mono bg-background/40 rounded-lg px-2.5 py-1 border border-border/40">
                    <span className={stage.accent}>{f}</span>
                    <ChevronRight size={9} className="text-muted-foreground/50" />
                    <span className="text-foreground/70">{t}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="rounded-2xl border border-border bg-card p-4">
        <div className="flex items-center gap-2 mb-3">
          <Zap size={13} className="text-amber-400" />
          <span className="text-xs font-semibold text-foreground">跨阶段数据流</span>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
          {CROSS_STAGE_EDGES.map((e, i) => (
            <div key={i} className="flex items-center gap-1.5 text-[11px] font-mono bg-muted/30 rounded-lg px-2.5 py-1.5 border border-border/50">
              <span className="text-primary/80 truncate">{e.from}</span>
              <ChevronRight size={9} className="text-muted-foreground shrink-0" />
              <span className="text-foreground/70 truncate">{e.to}</span>
              <span className="ml-auto text-muted-foreground/50 text-[10px] truncate pl-1">{e.label}</span>
            </div>
          ))}
        </div>
      </div>

      {activeNode && activeNodeData && (
        <div className="rounded-2xl border border-primary/30 bg-primary/5 p-4 space-y-3">
          <div className="flex items-center gap-2">
            <Info size={13} className="text-primary" />
            <span className="text-xs font-semibold text-foreground">{activeNodeData.label}</span>
            <span className={clsx('text-[10px] font-mono border rounded px-1.5', activeNodeData.stageAccent)}>
              {NODE_TYPE_LABEL[activeNodeData.type]}
            </span>
            <button onClick={() => setActiveNode(null)}
              className="ml-auto text-[10px] text-muted-foreground hover:text-foreground px-2 py-0.5 rounded hover:bg-muted">
              关闭
            </button>
          </div>
          <p className="text-xs text-muted-foreground">{activeNodeData.detail}</p>
          {(() => {
            const up = CROSS_STAGE_EDGES.filter(e => e.to === activeNode)
            const dn = CROSS_STAGE_EDGES.filter(e => e.from === activeNode)
            if (!up.length && !dn.length) return null
            return (
              <div className="flex gap-8 flex-wrap">
                {up.length > 0 && (
                  <div>
                    <p className="text-[10px] font-mono text-muted-foreground mb-1 uppercase tracking-wider">接收自</p>
                    {up.map((e, i) => (
                      <div key={i} className="flex items-center gap-1 text-[11px] font-mono">
                        <span className="text-muted-foreground">{e.from}</span>
                        <ChevronRight size={9} className="text-primary" />
                        <span className="text-primary">{e.label}</span>
                      </div>
                    ))}
                  </div>
                )}
                {dn.length > 0 && (
                  <div>
                    <p className="text-[10px] font-mono text-muted-foreground mb-1 uppercase tracking-wider">输出至</p>
                    {dn.map((e, i) => (
                      <div key={i} className="flex items-center gap-1 text-[11px] font-mono">
                        <span className="text-primary">{e.label}</span>
                        <ChevronRight size={9} className="text-primary" />
                        <span className="text-muted-foreground">{e.to}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )
          })()}
        </div>
      )}
    </div>
  )
}
