/**
 * G16 · RAGAS 质量评估面板
 *
 * 输入 (question, answer, contexts) 三元组 → POST /api/v1/evaluate
 *  → 显示三个核心指标 (faithfulness / answer_relevance / context_precision)
 *  → 自动写入 localStorage 历史 (最多 100 条)，下方画 trend
 *
 * 后端只暴露 single-shot 同步端点 (~3-5 秒一次，按 judge_model 计费)；
 * 历史持久化是 G16 后续改进，暂用 localStorage 满足个人使用调试需要.
 */
import { useState, useMemo } from 'react'
import { useMutation } from '@tanstack/react-query'
import {
  LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid, ResponsiveContainer, Legend,
} from 'recharts'
import {
  Gauge, Play, Trash2, AlertTriangle, CheckCircle2, Loader2, Copy, Upload,
  Target, Sparkles, ScrollText,
} from 'lucide-react'
import api from '@/services/api'

// ── 类型 ──────────────────────────────────────────────

interface EvaluateRequest {
  question: string
  answer: string
  contexts: string[]
}

interface EvaluateResponse {
  faithfulness: number | null
  answer_relevance: number | null
  context_precision: number | null
  judge_model: string
  latency_ms: number
  n_claims: number
  n_supported: number
  n_relevant_contexts: number
  n_total_contexts: number
  is_mock: boolean
  errors: string[]
}

interface HistoryEntry extends EvaluateResponse {
  ts: string                  // ISO
  question: string            // 截断 200 字
  answer_preview: string      // 截断 200 字
  contexts_count: number
}

// ── localStorage 历史 ────────────────────────────────

const STORAGE_KEY = 'ragas_eval_history_v1'
const MAX_HISTORY = 100

function loadHistory(): HistoryEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.slice(0, MAX_HISTORY) : []
  } catch { return [] }
}

function saveHistory(items: HistoryEntry[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(items.slice(0, MAX_HISTORY)))
  } catch { /* quota / private mode */ }
}

// ── Hooks ─────────────────────────────────────────────

function useEvaluate() {
  return useMutation({
    mutationFn: (req: EvaluateRequest) =>
      api.post<EvaluateResponse>('/api/v1/evaluate', req).then(r => r.data),
  })
}

// ── Helpers ───────────────────────────────────────────

function scoreColor(score: number | null): string {
  if (score === null) return 'text-muted-foreground'
  if (score >= 0.85) return 'text-emerald-500'
  if (score >= 0.65) return 'text-sky-500'
  if (score >= 0.45) return 'text-amber-500'
  return 'text-rose-500'
}

function scoreLabel(score: number | null): string {
  if (score === null) return '—'
  if (score >= 0.85) return '优秀'
  if (score >= 0.65) return '良好'
  if (score >= 0.45) return '一般'
  return '需改进'
}

function pct(x: number | null): string {
  return x === null ? '—' : `${(x * 100).toFixed(1)}%`
}

function avg(items: number[]): number | null {
  const valid = items.filter(n => Number.isFinite(n))
  if (valid.length === 0) return null
  return valid.reduce((a, b) => a + b, 0) / valid.length
}

function formatTs(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

// ── 主组件 ────────────────────────────────────────────

export default function QualityEvaluator() {
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState('')
  const [contextsText, setContextsText] = useState('')
  const [history, setHistory] = useState<HistoryEntry[]>(() => loadHistory())
  const [latest, setLatest] = useState<HistoryEntry | null>(null)
  const evaluate = useEvaluate()

  const contexts = useMemo(
    () => contextsText.split('\n').map(s => s.trim()).filter(Boolean),
    [contextsText],
  )

  const canSubmit = question.trim().length > 0
    && answer.trim().length > 0
    && contexts.length > 0
    && !evaluate.isPending

  const handleSubmit = async () => {
    if (!canSubmit) return
    try {
      const result = await evaluate.mutateAsync({
        question: question.trim(),
        answer: answer.trim(),
        contexts,
      })
      const entry: HistoryEntry = {
        ...result,
        ts: new Date().toISOString(),
        question: question.trim().slice(0, 200),
        answer_preview: answer.trim().slice(0, 200),
        contexts_count: contexts.length,
      }
      setLatest(entry)
      const next = [entry, ...history].slice(0, MAX_HISTORY)
      setHistory(next)
      saveHistory(next)
    } catch {
      // useMutation 已记录 error
    }
  }

  const handleClearHistory = () => {
    if (!confirm(`确定清空 ${history.length} 条评估历史？`)) return
    setHistory([])
    saveHistory([])
  }

  const handleLoadFromHistory = (entry: HistoryEntry) => {
    setQuestion(entry.question)
    setAnswer(entry.answer_preview)
    // contexts 没有完整存储（preview 才有），提示用户
    setContextsText('# 该历史记录未保留完整 contexts，请重新粘贴\n')
  }

  // 趋势数据（按时间正序）
  const trendData = useMemo(() => {
    return [...history].reverse().map((h, i) => ({
      idx: i + 1,
      label: formatTs(h.ts),
      faithfulness: h.faithfulness !== null ? h.faithfulness * 100 : null,
      answer_relevance: h.answer_relevance !== null ? h.answer_relevance * 100 : null,
      context_precision: h.context_precision !== null ? h.context_precision * 100 : null,
    }))
  }, [history])

  // 7 日均值（统计口径不严，按"最近 N 条"近似）
  const recent7 = history.slice(0, 7)
  const avgs = {
    faithfulness: avg(recent7.map(h => h.faithfulness ?? NaN)),
    answer_relevance: avg(recent7.map(h => h.answer_relevance ?? NaN)),
    context_precision: avg(recent7.map(h => h.context_precision ?? NaN)),
  }

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <h1 className="text-2xl font-semibold flex items-center gap-2">
            <Gauge className="w-5 h-5 text-primary" />
            质量评估
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            对 (question, answer, contexts) 做 RAGAS 打分；历史保存在本浏览器 localStorage（最多 {MAX_HISTORY} 条）
          </p>
          <details className="mt-2 text-xs text-muted-foreground">
            <summary className="cursor-pointer select-none inline-flex items-center gap-2 hover:text-foreground">
              <ScrollText className="w-3.5 h-3.5" aria-hidden="true" />
              指标说明
            </summary>
            <div className="mt-1 leading-relaxed">
              faithfulness（答案被上下文支撑） / answer_relevance（回答是否对题） / context_precision（上下文命中率）
            </div>
          </details>
        </div>
        {history.length > 0 && (
          <button
            onClick={handleClearHistory}
            aria-label={`清空该浏览器 localStorage 里的 ${history.length} 条 RAGAS 评估历史`}
            title={`清空 ${history.length} 条历史`}
            className="px-3 py-1.5 rounded-md hover:bg-rose-500/10 hover:text-rose-400 text-sm flex items-center gap-1.5 text-muted-foreground transition-colors"
          >
            <Trash2 className="w-3.5 h-3.5" aria-hidden="true" />
            清空 {history.length} 条
          </button>
        )}
      </div>

      {/* 最近 7 次均值 */}
      {history.length >= 2 && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <AvgCard label="忠实度均值" subtitle={`最近 ${recent7.length} 次`} value={avgs.faithfulness} icon={<CheckCircle2 className="w-4 h-4" />} />
          <AvgCard label="答案相关性均值" subtitle={`最近 ${recent7.length} 次`} value={avgs.answer_relevance} icon={<Target className="w-4 h-4" />} />
          <AvgCard label="上下文精度均值" subtitle={`最近 ${recent7.length} 次`} value={avgs.context_precision} icon={<Sparkles className="w-4 h-4" />} />
        </div>
      )}

      {/* Input form */}
      <Card title="评估输入" subtitle="API 实际烧钱（每次 ~$0.005-$0.02 视 judge_model）">
        <div className="space-y-3">
          <div>
            <label className="text-xs text-muted-foreground mb-1 block">问题（question）</label>
            <textarea
              value={question}
              onChange={e => setQuestion(e.target.value)}
              placeholder="例：BTC 短期会上涨吗？"
              rows={2}
              className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm resize-y"
            />
          </div>
          <div>
            <label className="text-xs text-muted-foreground mb-1 block">答案（answer）</label>
            <textarea
              value={answer}
              onChange={e => setAnswer(e.target.value)}
              placeholder="把要评估的 chat / briefing 答案粘贴到这里"
              rows={5}
              className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm resize-y font-mono"
            />
          </div>
          <div>
            <label className="text-xs text-muted-foreground mb-1 block">
              上下文（contexts）— <span className="text-foreground/70">每行一条，{contexts.length} 条</span>
            </label>
            <textarea
              value={contextsText}
              onChange={e => setContextsText(e.target.value)}
              placeholder="一行一条 context（chat 引用的私域观点或 briefing 召回的 chunk）"
              rows={6}
              className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm resize-y font-mono"
            />
          </div>
          <div className="flex items-center justify-between gap-2 pt-1">
            <div className="text-[11px] text-muted-foreground">
              {!canSubmit && question.trim() && answer.trim() && contexts.length === 0 && '⚠ 至少填一条 context'}
              {!canSubmit && (!question.trim() || !answer.trim()) && '⚠ question / answer 不能为空'}
            </div>
            <button
              onClick={handleSubmit}
              disabled={!canSubmit}
              className="px-4 py-2 rounded-md bg-primary/15 hover:bg-primary/25 text-primary text-sm font-medium flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {evaluate.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
              {evaluate.isPending ? '评估中（3-5 秒）...' : '跑评估'}
            </button>
          </div>
        </div>
      </Card>

      {/* Latest result */}
      {evaluate.error && !latest && (
        <div className="rounded-lg bg-rose-500/10 border border-rose-500/30 p-3 text-sm flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 text-rose-500 shrink-0 mt-0.5" />
          <div>
            <div className="font-medium text-rose-400 mb-0.5">评估失败</div>
            <div className="text-xs text-rose-400/80">{evaluate.error instanceof Error ? evaluate.error.message : '未知错误'}</div>
          </div>
        </div>
      )}

      {latest && (
        <Card
          title="最近一次评估结果"
          subtitle={`${formatTs(latest.ts)} · ${latest.judge_model}${latest.is_mock ? ' · MOCK' : ''} · ${latest.latency_ms}ms`}
        >
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-4">
            <ScoreTile
              label="忠实度"
              hint="答案是否被 context 支撑"
              score={latest.faithfulness}
              detail={`${latest.n_supported}/${latest.n_claims} 支撑`}
            />
            <ScoreTile
              label="答案相关性"
              hint="答案是否真的回答 question"
              score={latest.answer_relevance}
              detail="LLM 反推 question + cosine"
            />
            <ScoreTile
              label="上下文精度"
              hint="context 里多少条真的相关"
              score={latest.context_precision}
              detail={`${latest.n_relevant_contexts}/${latest.n_total_contexts} 相关`}
            />
          </div>

          {latest.errors.length > 0 && (
            <div className="rounded-md bg-amber-500/10 border border-amber-500/30 p-2.5 mb-3">
              <div className="text-[11px] font-medium text-amber-400 mb-1 flex items-center gap-1.5">
                <AlertTriangle className="w-3 h-3" />评估时出现的子错误（不影响其他维度结果）
              </div>
              <ul className="text-[11px] text-amber-400/85 space-y-0.5">
                {latest.errors.map((e, i) => (
                  <li key={i} className="font-mono">· {e}</li>
                ))}
              </ul>
            </div>
          )}

          <div className="text-[10px] text-muted-foreground/70 italic">
            注：分数 ≥85% 优秀 / ≥65% 良好 / ≥45% 一般 / &lt;45% 需改进 ·
            single-shot 评估对随机问答会有 ±5% 抖动，看趋势更可靠
          </div>
        </Card>
      )}

      {/* Trend chart */}
      {trendData.length >= 2 && (
        <Card title="历史趋势" subtitle={`${history.length} 次评估`}>
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={trendData}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
              <XAxis dataKey="idx" tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} domain={[0, 100]} unit="%" />
              <Tooltip
                contentStyle={{ background: 'hsl(var(--card))', border: '1px solid hsl(var(--border))', fontSize: 12 }}
                labelFormatter={(idx: number) => trendData[idx - 1]?.label ?? ''}
                formatter={(v) => typeof v === 'number' ? `${v.toFixed(1)}%` : '—'}
              />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <Line
                type="monotone"
                dataKey="faithfulness"
                name="忠实度"
                stroke="#10b981"
                strokeWidth={2}
                dot={{ r: 3 }}
                connectNulls
              />
              <Line
                type="monotone"
                dataKey="answer_relevance"
                name="答案相关性"
                stroke="#3b82f6"
                strokeWidth={2}
                dot={{ r: 3 }}
                connectNulls
              />
              <Line
                type="monotone"
                dataKey="context_precision"
                name="上下文精度"
                stroke="#f59e0b"
                strokeWidth={2}
                dot={{ r: 3 }}
                connectNulls
              />
            </LineChart>
          </ResponsiveContainer>
        </Card>
      )}

      {/* History table */}
      {history.length > 0 && (
        <Card title={`历史记录（${history.length}）`}>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-muted-foreground border-b border-border bg-secondary/30">
                <tr>
                  <th className="text-left py-2 px-3">时间</th>
                  <th className="text-left py-2 px-3">问题</th>
                  <th className="text-right py-2 px-3">忠实</th>
                  <th className="text-right py-2 px-3">相关</th>
                  <th className="text-right py-2 px-3">精度</th>
                  <th className="text-right py-2 px-3">contexts</th>
                  <th className="text-right py-2 px-3">耗时</th>
                  <th className="text-center py-2 px-3 w-8"></th>
                </tr>
              </thead>
              <tbody>
                {history.slice(0, 20).map((h, i) => (
                  <tr key={`${h.ts}-${i}`} className="border-b border-border/40 hover:bg-secondary/30 transition-colors">
                    <td className="py-2 px-3 text-muted-foreground tabular-nums">{formatTs(h.ts)}</td>
                    <td className="py-2 px-3 max-w-[280px] truncate" title={h.question}>{h.question}</td>
                    <td className={`py-2 px-3 text-right tabular-nums ${scoreColor(h.faithfulness)}`}>{pct(h.faithfulness)}</td>
                    <td className={`py-2 px-3 text-right tabular-nums ${scoreColor(h.answer_relevance)}`}>{pct(h.answer_relevance)}</td>
                    <td className={`py-2 px-3 text-right tabular-nums ${scoreColor(h.context_precision)}`}>{pct(h.context_precision)}</td>
                    <td className="py-2 px-3 text-right tabular-nums text-muted-foreground">{h.contexts_count}</td>
                    <td className="py-2 px-3 text-right tabular-nums text-muted-foreground">{h.latency_ms}ms</td>
                    <td className="py-2 px-3 text-center">
                      <button
                        onClick={() => handleLoadFromHistory(h)}
                        aria-label="加载该条评估记录到上方输入表单，contexts 需重粘"
                        title="加载到输入框（contexts 需重粘）"
                        className="p-1 rounded hover:bg-secondary text-muted-foreground hover:text-foreground"
                      >
                        <Upload className="w-3 h-3" aria-hidden="true" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {history.length > 20 && (
              <p className="text-[10px] text-muted-foreground p-2 text-center">仅显示最近 20 条 / 共 {history.length} 条</p>
            )}
          </div>
        </Card>
      )}

      {/* 空状态指引 */}
      {history.length === 0 && !latest && (
        <Card title="使用提示">
          <details className="group">
            <summary className="cursor-pointer select-none text-xs text-muted-foreground hover:text-foreground">
              <span className="inline-flex items-center gap-1.5">
                <span className="rounded border border-border bg-secondary/40 px-2 py-0.5 text-[11px]">展开</span>
                <span>如何取数与解读指标</span>
              </span>
            </summary>
            <div className="mt-3 space-y-2 text-xs text-muted-foreground leading-relaxed">
            <div className="flex items-start gap-2">
              <ScrollText className="w-4 h-4 text-primary shrink-0 mt-0.5" />
              <div>
                <p className="text-foreground/80 font-medium mb-1">如何拿到 (question, answer, contexts)？</p>
                <ol className="list-decimal pl-4 space-y-1">
                  <li>从 chat 会话里复制刚才的问题和 LLM 回答（含 [N] 引用）</li>
                  <li>contexts 可以从 chat 消息的「来源详情」折叠区里复制每条 claim 的 summary（一行一条）</li>
                  <li>briefing 路径同理：summary 当 answer，每条引用 claim summary 当 contexts</li>
                </ol>
              </div>
            </div>
            <div className="flex items-start gap-2">
              <Copy className="w-4 h-4 text-primary shrink-0 mt-0.5" />
              <div>
                <p className="text-foreground/80 font-medium mb-1">每个指标分别看什么？</p>
                <ul className="list-disc pl-4 space-y-1">
                      <li><strong className="text-foreground/85">忠实度</strong>：LLM 把答案拆成原子 claim → 判断每条 claim 是否被 contexts 支撑 → score = supported / total。低：可能存在 hallucination</li>
                      <li><strong className="text-foreground/85">答案相关性</strong>：LLM 反推 N 个潜在 question → embed + cosine。低：答非所问 / 跑题</li>
                      <li><strong className="text-foreground/85">上下文精度</strong>：LLM 标注每条 context 是否相关 → average precision @ k。低：召回噪声多 / 排序差</li>
                </ul>
              </div>
            </div>
            <div className="flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 text-amber-500 shrink-0 mt-0.5" />
              <div>
                <p className="text-foreground/80 font-medium mb-1">注意</p>
                <ul className="list-disc pl-4 space-y-1">
                  <li>每次评估烧 LLM API 费（judge_model 调 3 次 + embedding 1 次）</li>
                  <li>历史只在浏览器 localStorage，不跨设备同步</li>
                  <li>contexts 只保留 preview 不保留完整列表，重跑历史需手动重粘</li>
                </ul>
              </div>
            </div>
            </div>
          </details>
        </Card>
      )}
    </div>
  )
}

// ── 子组件 ─────────────────────────────────────────────

function Card({ title, subtitle, children }: {
  title: string
  subtitle?: string
  children: React.ReactNode
}) {
  return (
    <div className="rounded-xl bg-card border border-border p-4">
      <div className="flex items-baseline justify-between mb-3 gap-2 flex-wrap">
        <h2 className="text-sm font-semibold">{title}</h2>
        {subtitle && <span className="text-xs text-muted-foreground">{subtitle}</span>}
      </div>
      {children}
    </div>
  )
}

function AvgCard({ label, subtitle, value, icon }: {
  label: string
  subtitle: string
  value: number | null
  icon: React.ReactNode
}) {
  return (
    <div className="rounded-xl bg-card border border-border p-3">
      <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground mb-1">
        {icon}
        <span>{label}</span>
      </div>
      <div className={`text-2xl font-semibold tabular-nums ${scoreColor(value)}`}>{pct(value)}</div>
      <div className="text-[10px] text-muted-foreground mt-0.5">{subtitle}</div>
    </div>
  )
}

function ScoreTile({ label, hint, score, detail }: {
  label: string
  hint: string
  score: number | null
  detail: string
}) {
  return (
    <div className="rounded-lg bg-secondary/30 border border-border p-4">
      <div className="text-[11px] text-muted-foreground mb-1">{label}</div>
      <div className={`text-3xl font-semibold tabular-nums ${scoreColor(score)} mb-1`}>
        {pct(score)}
      </div>
      <div className="text-[11px] text-muted-foreground italic mb-2">{hint}</div>
      <div className="flex items-center justify-between text-[10px]">
        <span className="text-muted-foreground/80">{detail}</span>
        <span className={`font-medium ${scoreColor(score)}`}>{scoreLabel(score)}</span>
      </div>
    </div>
  )
}
