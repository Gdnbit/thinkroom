import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import {
  BookOpen,
  CheckCircle2,
  ClipboardCheck,
  History,
  MessageSquare,
  ShieldCheck,
} from 'lucide-react'
import { currentDecisionSession, type DecisionSession } from '@/data/decisionSession'

type StepKind = 'knowledge' | 'audit' | 'review'

const stepCopy: Record<StepKind, { title: string; nextLabel: string; nextTo: string }> = {
  knowledge: {
    title: '资料确认',
    nextLabel: '查看证据审计',
    nextTo: '/audit',
  },
  audit: {
    title: '审计范围',
    nextLabel: '查看复盘',
    nextTo: '/decision-snapshots',
  },
  review: {
    title: '复盘中心',
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
    <section className="rounded-xl border border-border bg-card p-4">
      <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-foreground">{copy.title}</h2>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">{session.title}</p>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          <Link to="/chat" className="btn-outline">
            <MessageSquare className="h-3.5 w-3.5" />
            当前问答
          </Link>
          <Link to={copy.nextTo} className="btn-outline">
            {step === 'knowledge' && <ClipboardCheck className="h-3.5 w-3.5" />}
            {step !== 'knowledge' && <History className="h-3.5 w-3.5" />}
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
