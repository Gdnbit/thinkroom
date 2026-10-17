import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import {
  BookOpen,
  CheckCircle2,
  ClipboardCheck,
  History,
  MessageSquare,
  Save,
  ShieldCheck,
} from 'lucide-react'
import { currentDecisionSession, type DecisionSession } from '@/data/decisionSession'

type StepKind = 'knowledge' | 'audit' | 'review'

const stepCopy: Record<StepKind, { eyebrow: string; title: string; summary: string; nextLabel: string; nextTo: string }> = {
  knowledge: {
    eyebrow: 'Current Decision Session',
    title: '资料确认',
    summary: '确认本次上传资料已经被接收、解析、切片，并进入可检索证据索引。',
    nextLabel: '查看证据审计',
    nextTo: '/audit',
  },
  audit: {
    eyebrow: 'Evidence Audit',
    title: '证据审计',
    summary: '检查当前回答依赖了哪些证据、哪些证据强、哪些风险仍需要补充材料。',
    nextLabel: '保存决策快照',
    nextTo: '/decision-snapshots',
  },
  review: {
    eyebrow: 'Outcome Review',
    title: '复盘中心',
    summary: '围绕已保存的决策版本记录结果、偏差来源和下一轮修正。',
    nextLabel: '回到问答修正',
    nextTo: '/chat',
  },
}

function Metric({ label, value, icon }: { label: string; value: ReactNode; icon: ReactNode }) {
  return (
    <div className="rounded-lg border border-border bg-secondary/25 p-3">
      <p className="mb-1 flex items-center gap-1.5 text-[11px] text-muted-foreground">
        {icon}
        {label}
      </p>
      <p className="text-sm font-semibold text-foreground">{value}</p>
    </div>
  )
}

export default function DecisionSessionStepPanel({ step, session = currentDecisionSession }: { step: StepKind; session?: DecisionSession }) {
  const copy = stepCopy[step]
  const doneStages = session.ingestionStages.filter((stage) => stage.state === 'done').length
  const strongEvidence = session.evidence.filter((item) => item.strength === 'strong').length

  return (
    <section className="rounded-xl border border-primary/20 bg-card p-4 shadow-sm">
      <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
        <div className="min-w-0">
          <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-primary">{copy.eyebrow}</p>
          <h2 className="mt-1 text-xl font-semibold text-foreground">{copy.title}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{copy.summary}</p>
          <p className="mt-3 max-w-3xl text-sm font-medium leading-relaxed text-foreground">{session.title}</p>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          <Link to="/chat" className="inline-flex h-9 items-center gap-2 rounded-lg border border-border bg-secondary px-3 text-xs font-semibold text-foreground hover:bg-surface-3">
            <MessageSquare className="h-3.5 w-3.5" />
            当前问答
          </Link>
          <Link to={copy.nextTo} className="inline-flex h-9 items-center gap-2 rounded-lg border border-primary bg-primary px-3 text-xs font-semibold text-primary-foreground">
            {step === 'knowledge' && <ClipboardCheck className="h-3.5 w-3.5" />}
            {step === 'audit' && <Save className="h-3.5 w-3.5" />}
            {step === 'review' && <History className="h-3.5 w-3.5" />}
            {copy.nextLabel}
          </Link>
        </div>
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-4">
        <Metric label="上传资料" value={`${session.files.length} 个文件`} icon={<BookOpen className="h-3.5 w-3.5 text-primary" />} />
        <Metric label="处理进度" value={`${doneStages}/${session.ingestionStages.length} 步完成`} icon={<CheckCircle2 className="h-3.5 w-3.5 text-emerald-300" />} />
        <Metric label="证据覆盖" value={`${session.evidence.length} 条证据 · ${strongEvidence} 条强支撑`} icon={<ShieldCheck className="h-3.5 w-3.5 text-primary" />} />
        <Metric label="待确认风险" value={`${session.risks.length} 项`} icon={<ClipboardCheck className="h-3.5 w-3.5 text-amber-300" />} />
      </div>
    </section>
  )
}
