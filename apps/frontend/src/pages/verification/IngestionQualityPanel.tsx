import { useState } from 'react'
import { escapeHtml } from '@/utils/escapeHtml'
import {
  BrainCircuit,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clipboard,
  FlaskConical,
  Loader2,
  XCircle,
  AlertTriangle,
  Clock,
} from 'lucide-react'
import { apiLong } from '@/services/api'
import { saveHistory, type TestHistoryRecord } from '@/services/testHistory'
import TestHistorySection from './TestHistorySection'

/* ── 小组件 ─────────────────────────────────────────── */

function CopyBtn({ getText, label = '复制' }: { getText: () => string; label?: string }) {
  const [copied, setCopied] = useState(false)
  const handle = () => {
    navigator.clipboard.writeText(getText()).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    })
  }
  return (
    <button
      onClick={handle}
      className={`flex items-center gap-1 px-2 py-1 rounded text-xs font-mono transition-colors border ${
        copied
          ? 'border-emerald-400/50 text-emerald-400 bg-emerald-400/10'
          : 'border-border text-muted-foreground hover:text-foreground hover:bg-secondary'
      }`}
    >
      {copied ? <CheckCircle2 size={11} /> : <Clipboard size={11} />}
      {copied ? '已复制' : label}
    </button>
  )
}

function StatusIcon({ status }: { status: string }) {
  if (status === 'ok') return <CheckCircle2 size={12} className="text-emerald-400" />
  if (status === 'warning') return <AlertTriangle size={12} className="text-yellow-400" />
  return <XCircle size={12} className="text-red-400" />
}

/* ── 单个样本结果卡片 ──────────────────────────────────── */

function SampleCard({ item }: { item: any }) {
  const [open, setOpen] = useState(true)
  const act = item.actual || {}
  const exp = item.expect || {}

  return (
    <div className="border border-border rounded-xl overflow-hidden">
      <button
        onClick={() => setOpen((o: boolean) => !o)}
        className="w-full flex items-center gap-2 px-4 py-2.5 text-left bg-secondary/30 hover:bg-secondary/50 transition-colors"
      >
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        <span className="text-sm font-medium text-foreground">{item.sample_name}</span>
        <span className="ml-auto text-xs text-muted-foreground flex items-center gap-1">
          <Clock size={10} />
          {item.total_duration_ms}ms
        </span>
        <span className="text-xs text-muted-foreground">
          观点: {act.claims_count ?? '?'}条
        </span>
      </button>

      {open && (
        <div className="px-4 py-3 space-y-3 text-xs">
          {/* 1. 输入 */}
          <div>
            <div className="font-semibold text-foreground mb-1">1. 输入</div>
            <div className="text-muted-foreground">
              文本长度: {item.input?.text_length} 字符
            </div>
            <pre className="mt-1 p-2 bg-secondary/40 rounded-lg text-muted-foreground max-h-32 overflow-auto whitespace-pre-wrap text-[11px]">
              {item.input?.text_preview}
            </pre>
          </div>

          {/* 2. 预期 */}
          <div>
            <div className="font-semibold text-foreground mb-1">2. 预期</div>
            <div className="space-y-0.5 text-muted-foreground">
              <div>摘要: {exp.summary_level} — {exp.summary_desc}</div>
              <div>分块: {exp.chunks}</div>
              <div>观点: ≥{exp.claims_min}条 — {exp.claims_desc}</div>
              <div>资产覆盖: {(exp.asset_scope_should_contain || []).join(', ')}</div>
            </div>
          </div>

          {/* 3. 实际 */}
          <div>
            <div className="font-semibold text-foreground mb-1">3. 实际</div>
            {/* 步骤 */}
            <div className="space-y-1.5 mb-2">
              {(item.steps || []).map((step: any, i: number) => (
                <div key={i} className="flex items-center gap-2">
                  <StatusIcon status={step.status} />
                  <span className="text-foreground">{step.name}</span>
                  <span className="text-muted-foreground ml-auto">{step.duration_ms}ms</span>
                </div>
              ))}
            </div>

            {/* 摘要 */}
            {act.summary && (
              <div className="mb-2">
                <div className="text-muted-foreground mb-0.5">
                  摘要 ({act.summary_level}, {act.summary_length}字):
                </div>
                <div className="p-2 bg-secondary/40 rounded-lg text-foreground/80 whitespace-pre-wrap">
                  {act.summary}
                </div>
              </div>
            )}

            {/* 观点列表 */}
            {(act.claims || []).length > 0 && (
              <div className="mb-2">
                <div className="text-muted-foreground mb-1">
                  观点 ({act.claims_count}条), 资产: [{(act.all_assets || []).join(', ')}], stance: [{(act.all_stances || []).join(', ')}]
                </div>
                <div className="space-y-1.5">
                  {(act.claims || []).map((c: any, ci: number) => (
                    <div key={ci} className="p-2 bg-secondary/30 rounded-lg border border-border/50">
                      <div className="text-foreground font-medium">[{ci + 1}] {c.summary}</div>
                      <div className="mt-0.5 text-muted-foreground">
                        type={c.claim_type} | stance=<span className={
                          c.stance === 'bullish' ? 'text-emerald-400' :
                          c.stance === 'bearish' ? 'text-red-400' :
                          c.stance === 'conditional' ? 'text-yellow-400' : 'text-muted-foreground'
                        }>{c.stance}</span> | horizon={c.time_horizon} | conf={c.confidence}
                      </div>
                      <div className="mt-0.5 text-muted-foreground">
                        assets: {(c.asset_scope || []).join(', ') || '(空)'}
                      </div>
                      {(c.conditions || []).length > 0 && (
                        <div className="mt-0.5 text-muted-foreground">条件: {c.conditions.join('; ')}</div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* 质量评分 */}
            {act.grades_distribution && (
              <div className="text-muted-foreground">
                质量分布: {Object.entries(act.grades_distribution).map(([g, n]) => `${g}×${n}`).join(' ')}
              </div>
            )}
          </div>

          {/* 错误 */}
          {(item.errors || []).length > 0 && (
            <div>
              <div className="font-semibold text-red-400 mb-1">错误</div>
              {item.errors.map((e: string, i: number) => (
                <div key={i} className="text-red-300">{e}</div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/* ── 主面板 ──────────────────────────────────────────── */

export default function IngestionQualityPanel() {
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<any>(null)
  const [error, setError] = useState<string | null>(null)
  const [replayRecord, setReplayRecord] = useState<TestHistoryRecord | null>(null)

  const handleReplay = (rec: TestHistoryRecord | null) => {
    if (!rec) { setReplayRecord(null); return }
    setReplayRecord(rec)
    // 将历史记录的日志文本加载为 AI 评审显示
    setResult({ _replay: true, copyable_report: rec.logText, ai_evaluation: rec.logText, test_data: { results: rec.details || [], samples_count: rec.summary.total } })
  }

  const handleRun = async () => {
    setLoading(true)
    setResult(null)
    setError(null)
    const t0 = performance.now()
    try {
      const r = await apiLong.post('/api/v1/verification/ingestion-quality-test')
      setResult(r.data)
      // 保存到历史记录
      const data = r.data
      const results = data?.test_data?.results || []
      const ok = results.filter((r: any) => r.overall_status === 'ok').length
      const fail = results.filter((r: any) => r.overall_status === 'error').length
      const warn = results.length - ok - fail
      saveHistory({
        category: 'ingestion',
        label: '素材解析质量测试',
        timestamp: new Date().toISOString(),
        durationMs: Math.round(performance.now() - t0),
        summary: { total: results.length, passed: ok, failed: fail, warnings: warn },
        logText: data?.copyable_report?.slice(0, 3000) || '',
        details: results.map((r: any) => ({ label: r.sample_id, status: r.overall_status, detail: r.summary || '' })),
      })
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || String(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-4">
      {/* 头部 */}
      <div className="rounded-2xl border border-border bg-card p-4 space-y-3">
        <div className="flex items-center gap-2 text-sm font-medium text-foreground">
          <FlaskConical size={15} className="text-violet-400" />
          素材解析质量测试（AI 评估）
        </div>
        <div className="text-[11px] text-muted-foreground">
          使用 3 个不同类型的金融素材样本，测试 Ingestion Pipeline 的拆分、摘要、观点抽取、质量评分全流程，
          并由 AI 对非确定性结果进行质量评审。报告包含：输入 → 预期 → 实际 → 评估结果 → 评估原因 → 不足之处。
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          <button
            onClick={() => { setReplayRecord(null); handleRun() }}
            disabled={loading}
            className="btn-primary flex items-center gap-1.5 disabled:opacity-50 border-violet-500/40 bg-violet-600 hover:bg-violet-500"
          >
            {loading ? <Loader2 size={12} className="animate-spin" /> : <BrainCircuit size={12} />}
            {loading ? '测试中（含 LLM 调用，约 1-3 分钟）...' : '运行素材解析质量测试'}
          </button>

          {result?.copyable_report && (
            <CopyBtn getText={() => result.copyable_report} label="复制完整报告" />
          )}
        </div>
      </div>

      {/* 错误 */}
      {error && (
        <div className="border border-red-400/30 rounded-xl p-4 bg-red-400/5 text-xs text-red-300">
          <div className="flex items-center gap-2 mb-1"><XCircle size={13} className="text-red-400" />请求失败</div>
          <pre className="whitespace-pre-wrap">{error}</pre>
        </div>
      )}

      {/* 加载中 */}
      {loading && (
        <div className="rounded-2xl border border-violet-500/20 bg-card p-8 flex items-center justify-center gap-3">
          <Loader2 size={20} className="animate-spin text-violet-400" />
          <div className="text-sm text-muted-foreground">
            正在运行 3 个样本的全链路解析 + AI 质量评审，请稍候...
          </div>
        </div>
      )}

      {/* 结果：样本明细 */}
      {result?.test_data?.results && (
        <div className="space-y-3">
          <div className="text-sm font-medium text-foreground flex items-center gap-2">
            <FlaskConical size={14} className="text-primary" />
            样本测试明细（{result.test_data.samples_count} 个样本）
          </div>
          {result.test_data.results.map((item: any) => (
            <SampleCard key={item.sample_id} item={item} />
          ))}
        </div>
      )}

      {/* 结果：AI 评审报告 */}
      {result?.ai_evaluation && (
        <div className="rounded-2xl border border-violet-500/30 bg-card p-4 space-y-3">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2 text-sm font-medium text-violet-400">
              <BrainCircuit size={15} />
              AI 素材解析质量评审
              {result.ai_model && result.ai_model !== 'failed' && (
                <span className="text-[11px] text-muted-foreground font-normal">（{result.ai_model}）</span>
              )}
            </div>
            <CopyBtn
              getText={() => `=== AI 素材解析质量评审 ===\n模型: ${result.ai_model}\n\n${result.ai_evaluation}`}
              label="复制评审"
            />
          </div>

          <div className="prose prose-sm prose-invert max-w-none text-xs leading-relaxed">
            <div
              className="whitespace-pre-wrap text-foreground/90"
              dangerouslySetInnerHTML={{
                __html: escapeHtml(result.ai_evaluation as string)
                  .replace(/^### (.+)$/gm, '<h3 class="text-sm font-semibold text-foreground mt-3 mb-1">$1</h3>')
                  .replace(/^## (.+)$/gm, '<h2 class="text-sm font-bold text-foreground mt-4 mb-1">$1</h2>')
                  .replace(/^# (.+)$/gm, '<h1 class="text-base font-bold text-foreground mt-4 mb-2">$1</h1>')
                  .replace(/\*\*(.+?)\*\*/g, '<strong class="text-foreground">$1</strong>')
                  .replace(/🟢/g, '<span class="text-emerald-400">🟢</span>')
                  .replace(/🟡/g, '<span class="text-yellow-400">🟡</span>')
                  .replace(/🔴/g, '<span class="text-red-400">🔴</span>')
                  .replace(/\n/g, '<br/>'),
              }}
            />
          </div>
        </div>
      )}

      {replayRecord && (
        <div className="rounded-xl border border-amber-400/40 bg-amber-400/5 px-4 py-2 flex items-center gap-3">
          <FlaskConical size={14} className="text-amber-400" />
          <div className="flex-1">
            <span className="text-xs font-medium text-amber-400">沉浸式回放模式</span>
            <span className="text-[10px] text-muted-foreground ml-2">{new Date(replayRecord.timestamp).toLocaleString('zh-CN')}</span>
          </div>
          <button onClick={() => handleReplay(null)} className="px-2 py-1 rounded text-xs text-amber-400 hover:bg-amber-400/10 border border-amber-400/30">退出回放</button>
        </div>
      )}

      <TestHistorySection category="ingestion" onReplay={handleReplay} replayId={replayRecord?.id} />
    </div>
  )
}
