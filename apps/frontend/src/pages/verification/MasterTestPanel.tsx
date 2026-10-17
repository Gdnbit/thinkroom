/**
 * 终极检测面板 — 一键跑遍所有验证维度
 *
 * 测试执行逻辑已移至 zustand store（masterTestStore），
 * 切换页面后测试在后台持续运行，回来时自动恢复进度。
 *
 * 三种运行模式：
 *  1. 终极全检 — 工具层 + 付费层 全部执行
 *  2. 仅工具层 — 接口可达性 + V2 结构验证（免费，并发 ~5s）
 *  3. 仅付费层 — 模型连通 + 素材解析 + 全链路验证（需 LLM，并发 ~4-6min）
 */
import { useState, useMemo } from 'react'
import {
  CheckCircle2, XCircle, Loader2, Wrench, DollarSign,
  Zap, ChevronDown, ChevronRight, Clipboard, Shield,
  Users, BrainCircuit, FlaskConical, Radio,
} from 'lucide-react'
import { clsx } from 'clsx'
import { useMasterTestStore, type PhaseData } from '@/store/masterTestStore'
import TestHistorySection from './TestHistorySection'

/* ── Phase icon map (view-only concern) ──────────────────── */

const PHASE_ICON: Record<string, typeof Shield> = {
  api: Zap, v2: Users, committee: Users, ingestion: BrainCircuit, fulllink: FlaskConical,
}

/* ── Copy Button ────────────────────────────────────────── */

function CopyBtn({ getText, label = '复制报告' }: { getText: () => string; label?: string }) {
  const [ok, setOk] = useState(false)
  return (
    <button onClick={() => { navigator.clipboard.writeText(getText()); setOk(true); setTimeout(() => setOk(false), 1500) }}
      className={clsx('flex items-center gap-1 px-2 py-1.5 rounded text-xs font-mono border transition-colors',
        ok ? 'border-emerald-400/50 text-emerald-400 bg-emerald-400/10' : 'border-border text-muted-foreground hover:text-foreground hover:bg-secondary')}>
      {ok ? <CheckCircle2 size={11} /> : <Clipboard size={11} />}{ok ? '已复制' : label}
    </button>
  )
}

/* ── Main Component ─────────────────────────────────────── */

export default function MasterTestPanel() {
  const { phases, running, mode, log, startRun, stop } = useMasterTestStore()
  const [expandedPhase, setExpandedPhase] = useState<string | null>(null)

  /* ─── Computed Stats ──────────────────────────────────── */

  const stats = useMemo(() => {
    const allItems = phases.flatMap(p => p.items)
    const pass = allItems.filter(i => i.status === 'pass').length
    const fail = allItems.filter(i => i.status === 'fail').length
    const skip = allItems.filter(i => i.status === 'skip').length
    const tested = allItems.length - skip
    const totalMs = phases.reduce((a, p) => a + p.durationMs, 0)
    return { pass, fail, skip, tested, total: allItems.length, totalMs }
  }, [phases])

  const copyText = useMemo(() => {
    const lines: string[] = []
    const now = new Date()
    const ts = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')} ${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`
    lines.push(`═══ 验证中心终极检测报告 ${ts} ═══`)
    lines.push(`总计: ${stats.pass}/${stats.tested} 通过 | ${stats.fail} 失败 | ${stats.skip} 跳过 | ${Math.round(stats.totalMs / 1000)}s`)
    lines.push('')
    phases.forEach(p => {
      if (p.items.length === 0) return
      const pp = p.items.filter(i => i.status === 'pass').length
      const ps = p.items.filter(i => i.status === 'skip').length
      const tierIcon = p.tier === 'paid' ? '[付费]' : '[免费]'
      lines.push(`--- ${tierIcon} ${p.name} (${pp}/${p.items.length - ps} 通过, ${Math.round(p.durationMs / 1000)}s) ---`)
      p.items.forEach(i => {
        const icon = i.status === 'pass' ? '✓' : i.status === 'fail' ? '✗' : '-'
        lines.push(`  ${icon} ${i.name} (${i.ms}ms) ${i.detail}`)
      })
      lines.push('')
    })
    if (log.length > 0) {
      lines.push('--- 运行日志 ---')
      lines.push(...log)
    }
    return lines.join('\n')
  }, [phases, stats, log])

  /* ─── Render ──────────────────────────────────────────── */

  const anyVisible = phases.some(p => p.items.length > 0 || p.status === 'running')

  return (
    <div className="space-y-4">
      {/* ── Header + Buttons ── */}
      <div className="rounded-2xl border border-border bg-card p-5 space-y-4">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <Shield size={16} className="text-primary" />
            终极检测
          </div>
          <p className="text-[11px] text-muted-foreground mt-1">
            一键跑遍全部测试维度：接口可达性 · V2 结构验证 · 模型连通性 · 素材解析 · 全链路场景。
            按 <span className="text-emerald-400 font-medium">工具层（免费）</span> 和
            <span className="text-violet-400 font-medium"> 付费层（LLM）</span> 双轨分流运行。
            {running && <span className="text-primary font-medium ml-1">切换页面后测试继续在后台运行。</span>}
          </p>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          <button onClick={() => startRun('all')} disabled={running}
            className="btn-primary flex items-center gap-1.5 disabled:opacity-50">
            {running && mode === 'all' ? <Loader2 size={12} className="animate-spin" /> : <Zap size={12} />}
            终极全检
          </button>
          <button onClick={() => startRun('tool')} disabled={running}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-emerald-400/40 text-emerald-400 hover:bg-emerald-400/10 disabled:opacity-50 transition-colors">
            {running && mode === 'tool' ? <Loader2 size={12} className="animate-spin" /> : <Wrench size={12} />}
            仅工具层（免费）
          </button>
          <button onClick={() => startRun('paid')} disabled={running}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-violet-400/40 text-violet-400 hover:bg-violet-400/10 disabled:opacity-50 transition-colors">
            {running && mode === 'paid' ? <Loader2 size={12} className="animate-spin" /> : <DollarSign size={12} />}
            仅付费层（LLM）
          </button>

          {running && (
            <button onClick={stop}
              className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-medium border border-red-400/40 text-red-400 hover:bg-red-400/10 transition-colors">
              <XCircle size={12} /> 中断
            </button>
          )}

          {stats.tested > 0 && (
            <CopyBtn getText={() => copyText} />
          )}
        </div>

        {/* Overall Stats Bar */}
        {stats.tested > 0 && (
          <div className="flex items-center gap-4 text-xs flex-wrap">
            <span className="text-foreground font-medium">已测 {stats.tested} 项</span>
            {stats.pass > 0 && <span className="text-emerald-400 font-medium">✓ {stats.pass} 通过</span>}
            {stats.fail > 0 && <span className="text-red-400 font-medium">✗ {stats.fail} 失败</span>}
            {stats.skip > 0 && <span className="text-muted-foreground">⊘ {stats.skip} 跳过</span>}
            {stats.totalMs > 0 && <span className="text-muted-foreground font-mono">{Math.round(stats.totalMs / 1000)}s</span>}
            <span className={clsx('font-mono font-medium', stats.fail === 0 && stats.tested > 0 ? 'text-emerald-400' : 'text-amber-400')}>
              {stats.tested > 0 ? Math.round((stats.pass / stats.tested) * 100) : 0}%
            </span>
            {/* Tier breakdown */}
            {(() => {
              const toolItems = phases.filter(p => p.tier === 'tool').flatMap(p => p.items).filter(i => i.status !== 'skip')
              const paidItems = phases.filter(p => p.tier === 'paid').flatMap(p => p.items).filter(i => i.status !== 'skip')
              const tp = toolItems.filter(i => i.status === 'pass').length
              const pp = paidItems.filter(i => i.status === 'pass').length
              return <>
                {toolItems.length > 0 && <span className="text-muted-foreground border-l border-border pl-3 ml-1">🔧 工具: {tp}/{toolItems.length}</span>}
                {paidItems.length > 0 && <span className="text-muted-foreground">💰 付费: {pp}/{paidItems.length}</span>}
              </>
            })()}
          </div>
        )}
      </div>

      {/* ── Phase Cards ── */}
      {anyVisible && (
        <div className="space-y-3">
          {/* Tool Tier */}
          {phases.filter(p => p.tier === 'tool' && (p.items.length > 0 || p.status === 'running')).length > 0 && (
            <div>
              <div className="flex items-center gap-2 text-xs font-medium text-emerald-400 mb-2">
                <Wrench size={12} /> 工具层（免费）
              </div>
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                {phases.filter(p => p.tier === 'tool' && (p.items.length > 0 || p.status === 'running')).map(p => (
                  <PhaseCard key={p.id} phase={p} expanded={expandedPhase === p.id} onToggle={() => setExpandedPhase(expandedPhase === p.id ? null : p.id)} />
                ))}
              </div>
            </div>
          )}

          {/* Paid Tier */}
          {phases.filter(p => p.tier === 'paid' && (p.items.length > 0 || p.status === 'running')).length > 0 && (
            <div>
              <div className="flex items-center gap-2 text-xs font-medium text-violet-400 mb-2">
                <DollarSign size={12} /> 付费层（LLM 调用）
              </div>
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                {phases.filter(p => p.tier === 'paid' && (p.items.length > 0 || p.status === 'running')).map(p => (
                  <PhaseCard key={p.id} phase={p} expanded={expandedPhase === p.id} onToggle={() => setExpandedPhase(expandedPhase === p.id ? null : p.id)} />
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── Progress Log ── */}
      {log.length > 0 && (
        <details open={running} className="rounded-xl border border-border bg-background overflow-hidden">
          <summary className="cursor-pointer px-4 py-2 text-xs font-medium text-muted-foreground hover:text-foreground flex items-center gap-2">
            <Radio size={11} /> 运行日志（{log.length} 条）
          </summary>
          <div className="px-4 py-2 max-h-52 overflow-auto font-mono border-t border-border">
            {log.map((line, i) => (
              <p key={i} className={clsx('text-[10px] leading-relaxed',
                line.includes('✗') || line.includes('失败') ? 'text-red-400' :
                line.includes('✓') || line.includes('完成') ? 'text-emerald-400' :
                line.includes('▶') ? 'text-primary' : 'text-muted-foreground')}>{line}</p>
            ))}
          </div>
        </details>
      )}

      {/* ── Empty State ── */}
      {!anyVisible && (
        <div className="border border-dashed border-border rounded-xl p-10 text-center space-y-3">
          <Shield size={32} className="mx-auto text-muted-foreground/20" />
          <p className="text-sm text-muted-foreground">选择上方按钮开始检测</p>
          <div className="flex items-center justify-center gap-8 text-[11px] text-muted-foreground">
            <div className="flex items-center gap-1.5">
              <Wrench size={11} className="text-emerald-400" />
              <span>工具层: 接口 + V2 结构（免费, 并发 ~5s）</span>
            </div>
            <div className="flex items-center gap-1.5">
              <DollarSign size={11} className="text-violet-400" />
              <span>付费层: 模型 ping + 解析 + 全链路（并发 ~4-6min）</span>
            </div>
          </div>
        </div>
      )}

      {/* ── History ── */}
      <TestHistorySection category="master" />
    </div>
  )
}

/* ── Phase Card Sub-component ───────────────────────────── */

function PhaseCard({ phase: p, expanded, onToggle }: { phase: PhaseData; expanded: boolean; onToggle: () => void }) {
  const pass = p.items.filter(i => i.status === 'pass').length
  const fail = p.items.filter(i => i.status === 'fail').length
  const skip = p.items.filter(i => i.status === 'skip').length
  const tested = p.items.length - skip
  const Icon = PHASE_ICON[p.id] || Shield

  const borderCls = p.status === 'running' ? 'border-primary/40 shadow-sm shadow-primary/5'
    : fail > 0 ? 'border-red-400/30'
    : pass > 0 && fail === 0 ? 'border-emerald-400/20'
    : 'border-border'

  const tierBadgeCls = p.tier === 'paid'
    ? 'bg-violet-400/10 text-violet-400 border-violet-400/30'
    : 'bg-emerald-400/10 text-emerald-400 border-emerald-400/30'

  return (
    <div className={clsx('rounded-xl border bg-card transition-all', borderCls)}>
      <button onClick={onToggle} className="w-full flex items-center gap-2.5 px-4 py-3 text-left hover:bg-secondary/20 transition-colors">
        {p.status === 'running'
          ? <Loader2 size={14} className="animate-spin text-primary shrink-0" />
          : fail > 0
          ? <XCircle size={14} className="text-red-400 shrink-0" />
          : tested > 0
          ? <CheckCircle2 size={14} className="text-emerald-400 shrink-0" />
          : <Icon size={14} className="text-muted-foreground shrink-0" />}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-foreground">{p.name}</span>
            <span className={clsx('text-[9px] px-1.5 py-0.5 rounded border font-medium', tierBadgeCls)}>
              {p.tier === 'paid' ? '💰 付费' : '🔧 免费'}
            </span>
          </div>
          <div className="flex items-center gap-2 text-[10px] text-muted-foreground mt-0.5">
            {p.status === 'running'
              ? <span className="text-primary">运行中...</span>
              : tested > 0
              ? <span>{pass}/{tested} 通过{fail > 0 ? <span className="text-red-400 ml-1">({fail} 失败)</span> : ''}</span>
              : skip > 0
              ? <span>已跳过</span>
              : <span>等待中</span>}
            {p.durationMs > 0 && <span className="font-mono">{Math.round(p.durationMs / 1000)}s</span>}
          </div>
        </div>
        {tested > 0 && (expanded ? <ChevronDown size={12} className="text-muted-foreground shrink-0" /> : <ChevronRight size={12} className="text-muted-foreground shrink-0" />)}
      </button>

      {expanded && p.items.length > 0 && (
        <div className="border-t border-border px-4 py-2 space-y-0.5 max-h-80 overflow-auto">
          {p.items.map(item => (
            <div key={item.id} className="flex items-center gap-2 text-[11px] py-0.5">
              {item.status === 'pass' ? <CheckCircle2 size={10} className="text-emerald-400 shrink-0" />
                : item.status === 'fail' ? <XCircle size={10} className="text-red-400 shrink-0" />
                : item.status === 'running' ? <Loader2 size={10} className="animate-spin text-primary shrink-0" />
                : <span className="w-2.5 h-2.5 rounded-full bg-muted-foreground/20 shrink-0" />}
              <span className="text-foreground shrink-0 max-w-[140px] truncate">{item.name}</span>
              {item.ms > 0 && <span className="text-muted-foreground font-mono text-[10px] shrink-0">{item.ms}ms</span>}
              <span className={clsx('text-[10px] truncate flex-1 min-w-0', item.status === 'fail' ? 'text-red-300' : 'text-muted-foreground')}>{item.detail}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
