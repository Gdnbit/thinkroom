import { useMemo, useState, useCallback, useEffect } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  Play,
  CheckCircle2,
  XCircle,
  AlertCircle,
  ChevronDown,
  ChevronRight,
  Loader2,
  FlaskConical,
  Clock,
  Clipboard,
  GitBranch,
  Layers,
  TestTube2,
  Sparkles,
  Zap,
  Server,
  BrainCircuit,
  Users,
  History,
  Trash2,
  Eye,
} from 'lucide-react'
import { clsx } from 'clsx'
import api, { apiLong } from '@/services/api'
import ApiTestPanel from './verification/ApiTestPanel'
import SystemStatusPanel from './verification/SystemStatusPanel'
import IngestionQualityPanel from './verification/IngestionQualityPanel'
import CommitteeV2TestPanel from './verification/CommitteeV2TestPanel'
import { loadHistory, saveHistory, deleteHistory, clearHistory, getStorageInfo, type TestHistoryRecord, type TestCategory } from '@/services/testHistory'
import TestHistorySection from './verification/TestHistorySection'
import MasterTestPanel from './verification/MasterTestPanel'
import ModeRouterPanel from './verification/ModeRouterPanel'
import { Compass } from 'lucide-react'

interface ScenarioMeta {
  scenario_id: string
  name: string
  description: string
}

interface AssertionResult {
  name: string
  assertion_type: string
  status: 'passed' | 'failed' | 'skipped'
  reason?: string
  expected?: unknown
  actual?: unknown
}

interface StepResult {
  step_index: number
  action_type: string
  status: 'completed' | 'failed' | 'skipped'
  duration_ms: number
  input_refs: string[]
  output_refs: string[]
  error?: string
  details: Record<string, unknown>
}

interface VerificationReport {
  run_id: string
  scenario_id: string
  scenario_name: string
  status: 'passed' | 'failed' | 'error'
  started_at: string
  finished_at: string
  duration_ms: number
  steps: StepResult[]
  created_objects: Record<string, string[]>
  used_objects: Record<string, string[]>
  assertions: AssertionResult[]
  retrieval_snapshot: Record<string, unknown>
  summary: { passed: number; failed: number; skipped: number; warnings: number }
  warnings: string[]
  errors: string[]
}

interface SuiteScenarioResult {
  scenario_id: string
  scenario_name: string
  status: string
  duration_ms: number
  summary: { passed: number; failed: number; skipped: number; warnings: number }
  errors: string[]
  warnings: string[]
  assertions: AssertionResult[]
  steps: StepResult[]
}

interface VerificationSuiteResult {
  type: 'suite'
  summary: {
    started_at: string
    finished_at: string
    duration_ms: number
    total: number
    passed: number
    failed: number
    pass_rate: number
  }
  results: SuiteScenarioResult[]
  copy_text: string
}

function useScenarios() {
  return useQuery({
    queryKey: ['verification-scenarios'],
    queryFn: () => api.get<ScenarioMeta[]>('/api/v1/verification/scenarios').then((r) => r.data),
    staleTime: 5 * 60 * 1000,
    retry: 2,
    retryDelay: 1000,
  })
}

function useRunScenario() {
  return useMutation({
    mutationFn: (id: string) => apiLong.post<VerificationReport>(`/api/v1/verification/run/${id}`).then((r) => r.data),
  })
}

function useRunSuite() {
  return useMutation({
    mutationFn: (scenarioIds?: string[]) =>
      apiLong.post<VerificationSuiteResult>('/api/v1/verification/run-suite', scenarioIds ?? []).then((r) => r.data),
  })
}

function StatusBadge({ status }: { status: string }) {
  const cfg = {
    passed: { cls: 'text-emerald-400 bg-emerald-400/10 border-emerald-400/30', icon: <CheckCircle2 size={11} /> },
    failed: { cls: 'text-red-400 bg-red-400/10 border-red-400/30', icon: <XCircle size={11} /> },
    error: { cls: 'text-red-400 bg-red-400/10 border-red-400/30', icon: <AlertCircle size={11} /> },
    completed: { cls: 'text-emerald-400 bg-emerald-400/10 border-emerald-400/30', icon: <CheckCircle2 size={11} /> },
    skipped: { cls: 'text-muted-foreground bg-secondary border-border', icon: null },
  }[status] ?? { cls: 'text-muted-foreground bg-secondary border-border', icon: null }

  return (
    <span className={clsx('inline-flex items-center gap-1 text-xs border rounded-full px-2 py-0.5 font-medium', cfg.cls)}>
      {cfg.icon}
      {status}
    </span>
  )
}

function CopyButton({ getText, label = '复制' }: { getText: () => string; label?: string }) {
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

function StepRow({ step }: { step: StepResult }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="border-l-2 border-border pl-3 pb-3">
      <button onClick={() => setOpen((o) => !o)} className="flex items-center gap-2 w-full text-left hover:opacity-80 transition-opacity">
        <span className="text-muted-foreground">{open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}</span>
        <span className="text-xs text-foreground font-mono">
          {step.step_index + 1}. {step.action_type}
        </span>
        <StatusBadge status={step.status} />
        <span className="ml-auto text-xs text-muted-foreground flex items-center gap-1">
          <Clock size={10} />
          {step.duration_ms}ms
        </span>
      </button>
      {open && (
        <div className="mt-2 ml-4 space-y-1 text-xs text-muted-foreground">
          {step.input_refs.length > 0 && <p><span className="text-foreground">输入：</span>{step.input_refs.join(', ')}</p>}
          {step.output_refs.length > 0 && <p><span className="text-foreground">输出：</span>{step.output_refs.join(', ')}</p>}
          {step.error && <p className="text-red-400">错误：{step.error}</p>}
        </div>
      )}
    </div>
  )
}

function classifyScenario(s: ScenarioMeta) {
  const txt = `${s.scenario_id} ${s.name} ${s.description}`.toLowerCase()
  if (txt.includes('ingest') || txt.includes('upload') || txt.includes('source')) return '上传识别与入库'
  if (txt.includes('knowledge') || txt.includes('graph') || txt.includes('timeline') || txt.includes('taxonomy')) return '知识看板与关系'
  if (txt.includes('retriev') || txt.includes('search') || txt.includes('query')) return '检索与召回'
  if (txt.includes('setup') || txt.includes('briefing') || txt.includes('decision')) return '决策与输出'
  return '基础链路与稳定性'
}

function aiSupervisorPromptTemplate(results: SuiteScenarioResult[]) {
  const payload = {
    date: '2026-04-05',
    objective: '验证 上传文件→识别处理→结构化入库→看板可见→关系传播链 是否成立',
    expected: {
      slicing: ['按语义切片', '关键时间点提取', '观察窗口提取'],
      recognition: ['节点类型 event/policy/outcome/market', '关系类型 causes/co_drives/follows/supports'],
      io: ['输入对象存在', '输出对象完整', '错误可追溯'],
      db_state: ['原始文档存档', '结构化对象入库', '关系边入库', '时间窗口入库'],
    },
    run_results: results,
    request: '请评估每项是否达标，并给出阻塞问题、误报风险、下一步修复建议。输出 JSON。',
  }
  return JSON.stringify(payload, null, 2)
}

type TabKey = 'master' | 'scenarios' | 'api' | 'ingestion' | 'status' | 'v2' | 'mode-router' | 'history'
const TABS: { key: TabKey; label: string; icon: any; desc: string }[] = [
  { key: 'master', label: '终极检测', icon: Layers, desc: '工具层 + 付费层 一键全检' },
  { key: 'scenarios', label: '全链路验证', icon: FlaskConical, desc: '端到端场景测试' },
  { key: 'api', label: '接口面板', icon: Zap, desc: '21 个 API 端点逐一测试' },
  { key: 'v2', label: 'V2 专项', icon: Users, desc: '委员会 V2 · 动态角色 · AnswerReceipt' },
  { key: 'mode-router', label: '路由审计', icon: Compass, desc: 'G20 · 三模式融合路由决策验证' },
  { key: 'ingestion', label: '解析质量', icon: BrainCircuit, desc: '素材解析 AI 评估' },
  { key: 'status', label: '系统状态', icon: Server, desc: '模型路由 · 数据源 · 成本' },
  { key: 'history', label: '历史记录', icon: History, desc: '查看往次测试结果' },
]

export default function Verification() {
  const { data: scenarios, isLoading, isError, refetch } = useScenarios()
  const runMutation = useRunScenario()
  const runSuiteMutation = useRunSuite()
  const navigate = useNavigate()

  const [activeTab, setActiveTab] = useState<TabKey>('master')
  const [selected, setSelected] = useState<string | null>(null)
  const [report, setReport] = useState<VerificationReport | null>(null)
  const [suiteResult, setSuiteResult] = useState<VerificationSuiteResult | null>(null)
  const [runAllResult, setRunAllResult] = useState<any>(null)
  const [runAllDetails, setRunAllDetails] = useState<SuiteScenarioResult[]>([])
  const [globalError, setGlobalError] = useState<string | null>(null)
  const [progressLog, setProgressLog] = useState<string[]>([])
  const [progressCurrent, setProgressCurrent] = useState(0)
  const [progressTotal, setProgressTotal] = useState(0)
  const [runAllRunning, setRunAllRunning] = useState(false)
  const [search, setSearch] = useState('')
  const [categoryResult, setCategoryResult] = useState<Record<string, VerificationSuiteResult>>({})
  const [aiEvalResult, setAiEvalResult] = useState<string | null>(null)
  const [aiEvalModel, setAiEvalModel] = useState<string | null>(null)
  const [aiEvalLoading, setAiEvalLoading] = useState(false)
  const [aiEvalError, setAiEvalError] = useState<string | null>(null)
  const [historyRecords, setHistoryRecords] = useState<TestHistoryRecord[]>([])
  const [historyFilter, setHistoryFilter] = useState<TestCategory | ''>('')
  const [historyExpanded, setHistoryExpanded] = useState<string | null>(null)

  const refreshHistory = useCallback(() => {
    const all = loadHistory()
    setHistoryRecords(historyFilter ? all.filter(r => r.category === historyFilter) : all)
  }, [historyFilter])

  useEffect(() => { if (activeTab === 'history') refreshHistory() }, [activeTab, refreshHistory])

  const addLog = (msg: string) => {
    const time = new Date().toLocaleTimeString('zh-CN', { hour12: false })
    setProgressLog((prev) => [...prev, `[${time}] ${msg}`])
  }

  const handleRun = async (id: string) => {
    setSelected(id)
    setReport(null)
    setGlobalError(null)
    try {
      const r = await runMutation.mutateAsync(id)
      setReport(r)
      addLog(`单点完成：${r.scenario_name} → ${r.status}（${r.duration_ms}ms）`)
    } catch (e: any) {
      setGlobalError(e?.response?.data?.detail || e?.message || String(e))
    }
  }

  const handleRunSuite = async (ids?: string[], category?: string) => {
    setGlobalError(null)
    try {
      const suite = await runSuiteMutation.mutateAsync(ids ?? [])
      if (category) {
        setCategoryResult((prev) => ({ ...prev, [category]: suite }))
      } else {
        setSuiteResult(suite)
      }
      addLog(`套件完成：${suite.summary.passed}/${suite.summary.total} 通过`)
    } catch (e: any) {
      setGlobalError(e?.response?.data?.detail || e?.message || String(e))
    }
  }

  const handleRunAll = async () => {
    setReport(null)
    setSuiteResult(null)
    setRunAllResult(null)
    setRunAllDetails([])
    setGlobalError(null)
    setProgressLog([])

    const total = (scenarios || []).filter((s) => s.scenario_id !== 'scenario_010_intent_recognition').length
    setProgressTotal(total)
    setProgressCurrent(0)
    if (total === 0) {
      addLog('未加载到可执行场景，请先检查后端 /api/v1/verification/scenarios 是否可访问。')
      setGlobalError('场景列表为空，无法启动一键测试。请先确认验证场景接口可用，然后点击“重试加载场景”。')
      return
    }
    addLog(`开始一键测试，共 ${total} 个场景`)
    setRunAllRunning(true)

    try {
      const baseUrl = ((import.meta as any).env?.VITE_API_URL || '')
      const resp = await fetch(`${baseUrl}/api/v1/verification/run-all`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: AbortSignal.timeout(600000),
      })
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      const reader = resp.body!.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      let done = false

      while (!done) {
        const { value, done: d } = await reader.read()
        done = d
        if (value) buf += decoder.decode(value, { stream: !d })
        const parts = buf.split('\n\n')
        buf = parts.pop() || ''

        for (const part of parts) {
          const line = part.replace(/^data: /, '').trim()
          if (!line) continue
          try {
            const msg = JSON.parse(line)
            if (msg.type === 'progress') {
              addLog(`→ ${msg.message || `执行场景 ${msg.index}/${msg.total}`}`)
            } else if (msg.type === 'heartbeat') {
              // 心跳只维持连接，不刷日志，避免页面持续跳动
            } else if (msg.type === 'scenario') {
              setProgressCurrent((c) => c + 1)
              setRunAllDetails((prev) => [...prev, msg])
              const allAsserts = msg.assertions || []
              const failAsserts = allAsserts.filter((a: any) => a.status === 'failed')
              const passAsserts = allAsserts.filter((a: any) => a.status === 'passed')
              const allSteps = msg.steps || []
              const dur = msg.duration_ms != null ? `${msg.duration_ms}ms` : ''
              addLog(`${msg.status === 'passed' ? '✓' : '✗'} ${msg.scenario_name}  (${allSteps.length}步, 断言 ✓${passAsserts.length} ✗${failAsserts.length}, ${dur})`)
              // 展示每个步骤摘要
              allSteps.forEach((s: any) => {
                const sIcon = s.status === 'completed' ? '✓' : s.status === 'failed' ? '✗' : '-'
                const sOut = (s.output_refs || []).length ? ` → ${s.output_refs.join(', ')}` : ''
                addLog(`  ${sIcon} [${(s.step_index ?? 0) + 1}] ${s.action_type} (${s.duration_ms ?? 0}ms)${sOut}`)
                if (s.error) addLog(`    error: ${String(s.error).split('\n')[0]}`)
              })
              // 展示每个断言（通过的精简，失败的详细）
              allAsserts.forEach((a: any) => {
                const aIcon = a.status === 'passed' ? '✓' : a.status === 'failed' ? '✗' : '-'
                const aType = a.assertion_type || a.name || '?'
                const aName = a.name && a.name !== a.assertion_type ? a.name : ''
                const detail = a.status === 'failed' && (a.expected !== undefined || a.actual !== undefined)
                  ? ` | expected=${JSON.stringify(a.expected)} actual=${JSON.stringify(a.actual)}`
                  : ''
                addLog(`  ${aIcon} [${aType}]${aName ? ' ' + aName : ''}${a.reason ? ` :: ${a.reason}` : ''}${detail}`)
              })
              if ((msg.errors || []).length) {
                (msg.errors as string[]).slice(0, 3).forEach((e: string) => addLog(`  ERROR: ${String(e).split('\n')[0]}`))
              }
            } else if (msg.type === 'summary') {
              setRunAllResult(msg)
              setRunAllDetails(msg.results || [])
              addLog(`一键测试完成：${msg.passed}/${msg.total} 通过`)
              // 保存到历史记录
              saveHistory({
                category: 'suite',
                label: '全链路一键测试',
                timestamp: new Date().toISOString(),
                durationMs: msg.duration_ms ?? 0,
                summary: { total: msg.total ?? 0, passed: msg.passed ?? 0, failed: (msg.total ?? 0) - (msg.passed ?? 0) },
                logText: progressLog.join('\n'),
                details: msg.results,
              })
            }
          } catch {
            // ignore
          }
        }
      }
    } catch (e: any) {
      setGlobalError(e?.message || String(e))
    } finally {
      setRunAllRunning(false)
    }
  }

  const handleAiEvaluate = useCallback(async (scenarioResults: any[]) => {
    setAiEvalResult(null)
    setAiEvalModel(null)
    setAiEvalError(null)
    setAiEvalLoading(true)
    addLog('正在调用 AI 评估测试结果...')
    try {
      const passed = scenarioResults.filter((r: any) => r.status === 'passed').length
      const r = await apiLong.post('/api/v1/verification/ai-evaluate', {
        scenario_results: scenarioResults,
        total_passed: passed,
        total_count: scenarioResults.length,
      })
      setAiEvalResult(r.data.evaluation)
      setAiEvalModel(r.data.model)
      addLog(`AI 评估完成（模型: ${r.data.model}）`)
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || String(e)
      setAiEvalError(msg)
      addLog(`AI 评估失败: ${msg}`)
    } finally {
      setAiEvalLoading(false)
    }
  }, [])

  const grouped = useMemo(() => {
    const m = new Map<string, ScenarioMeta[]>()
    ;(scenarios || []).forEach((s) => {
      const c = classifyScenario(s)
      if (!m.has(c)) m.set(c, [])
      m.get(c)!.push(s)
    })
    return m
  }, [scenarios])

  const filteredScenarios = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return scenarios || []
    return (scenarios || []).filter((s) => `${s.scenario_id} ${s.name} ${s.description}`.toLowerCase().includes(q))
  }, [scenarios, search])

  const running = runMutation.isPending || runAllRunning || runSuiteMutation.isPending

  const oneClickCopy = useMemo(() => {
    const lines: string[] = []
    const now = new Date()
    const dateStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')} ${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`
    lines.push('=== 验证中心一键测试汇总 ===')
    lines.push(`日期: ${dateStr}`)
    lines.push('')
    if (runAllResult) {
      lines.push(`全量: ${runAllResult.passed}/${runAllResult.total} 通过`)
      lines.push('')
    }
    if (runAllDetails.length) {
      lines.push('--- 场景明细（完整调试信息） ---')
      runAllDetails.forEach((res: any) => {
        const dur = res.duration_ms != null ? ` [${res.duration_ms}ms]` : ''
        lines.push(`[${String(res.status || '').toUpperCase()}] ${res.scenario_name} (${res.scenario_id})${dur}`)
        lines.push(`  断言: ✓${res.summary?.passed ?? 0} ✗${res.summary?.failed ?? 0} -${res.summary?.skipped ?? 0}  warnings=${res.summary?.warnings ?? 0}`)

        // 所有步骤详情
        const steps = res.steps || []
        if (steps.length) {
          lines.push(`  步骤 (${steps.length}):`)
          steps.forEach((s: any) => {
            const sStatus = String(s.status || '').toUpperCase()
            const sDur = s.duration_ms != null ? `${s.duration_ms}ms` : '-'
            const sOut = (s.output_refs || []).length ? ` → [${s.output_refs.join(', ')}]` : ''
            lines.push(`    #${(s.step_index ?? 0) + 1} ${s.action_type} ${sStatus} (${sDur})${sOut}`)
            if (s.error) lines.push(`      error: ${String(s.error).split('\n')[0]}`)
            // 展示 details 中的关键字段
            if (s.details && Object.keys(s.details).length) {
              const detailStr = JSON.stringify(s.details)
              if (detailStr.length <= 300) {
                lines.push(`      details: ${detailStr}`)
              } else {
                lines.push(`      details: ${detailStr.slice(0, 300)}...`)
              }
            }
          })
        }

        // 所有断言详情（包括通过的）
        const asserts = res.assertions || []
        if (asserts.length) {
          lines.push(`  断言详情 (${asserts.length}):`)
          asserts.forEach((a: any) => {
            const icon = a.status === 'passed' ? '✓' : a.status === 'failed' ? '✗' : '-'
            const aType = a.assertion_type || a.name || '?'
            const aName = a.name && a.name !== a.assertion_type ? a.name : ''
            lines.push(`    ${icon} [${aType}]${aName ? ' ' + aName : ''}${a.reason ? ` :: ${a.reason}` : ''}`)
            if (a.expected !== undefined || a.actual !== undefined) {
              lines.push(`      expected=${JSON.stringify(a.expected)}  actual=${JSON.stringify(a.actual)}`)
            }
          })
        }

        // warnings
        ;(res.warnings || []).forEach((w: string) => lines.push(`  WARNING: ${w}`))
        // errors
        ;(res.errors || []).forEach((e: string) => {
          lines.push(`  ERROR: ${String(e).split('\n').slice(0, 3).join('\n    ')}`)
        })
        lines.push('')
      })
    }
    if (suiteResult) {
      lines.push(`套件: ${suiteResult.summary.passed}/${suiteResult.summary.total} 通过`)
      lines.push('')
    }
    if (progressLog.length) {
      lines.push('--- 运行日志 ---')
      lines.push(...progressLog)
      lines.push('')
    }
    if (globalError) {
      lines.push('--- 全局错误 ---')
      lines.push(globalError)
      lines.push('')
    }
    return lines.join('\n')
  }, [runAllResult, runAllDetails, suiteResult, progressLog, globalError])

  return (
    <div className="px-5 sm:px-8 2xl:px-10 py-10 2xl:py-14 max-w-[1400px] 2xl:max-w-[1760px] mx-auto space-y-6">
      {/* ── Page Header ── */}
      <div className="page-header !mb-5 !pb-4">
        <span className="section-label">Verification</span>
        <h1 className="text-2xl font-semibold text-foreground flex items-center gap-2">
          <FlaskConical size={20} className="text-primary" />
          验证中心
        </h1>
        <p className="text-sm text-muted-foreground mt-1">系统功能验证：全链路场景测试 · API 接口面板 · 系统运行状态</p>
      </div>

      {/* ── Tab Navigation ── */}
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-1 p-1 rounded-xl bg-muted/50 border border-border">
          {TABS.map((tab) => {
            const Icon = tab.icon
            const active = activeTab === tab.key
            return (
              <button key={tab.key} onClick={() => setActiveTab(tab.key)}
                className={clsx('flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all duration-150',
                  active ? 'bg-primary text-primary-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground hover:bg-secondary')}>
                <Icon size={12} />{tab.label}
              </button>
            )
          })}
        </div>
        <div className="flex items-center gap-2">
          {activeTab === 'scenarios' && <button onClick={() => refetch()} className="btn-outline flex items-center gap-1.5 text-xs">重试加载场景</button>}
          <button onClick={() => navigate('/verification/dataflow')} className="btn-outline flex items-center gap-1.5 text-xs"><GitBranch size={12} /> 数据流</button>
        </div>
      </div>

      {/* ── Tab: 终极检测 ── */}
      {activeTab === 'master' && <MasterTestPanel />}

      {/* ── Tab: 接口面板 ── */}
      {activeTab === 'api' && <ApiTestPanel />}

      {/* ── Tab: 解析质量 ── */}
      {activeTab === 'ingestion' && <IngestionQualityPanel />}

      {/* ── Tab: 系统状态 ── */}
      {activeTab === 'status' && <SystemStatusPanel />}

      {/* ── Tab: V2 专项测试 ── */}
      {activeTab === 'v2' && <CommitteeV2TestPanel />}

      {/* ── Tab: 路由审计（G20） ── */}
      {activeTab === 'mode-router' && <ModeRouterPanel />}

      {/* ── Tab: 历史记录 ── */}
      {activeTab === 'history' && (
        <div className="space-y-4">
          <div className="rounded-2xl border border-border bg-card p-4 space-y-3">
            <div className="flex items-center justify-between gap-2 flex-wrap">
              <div className="flex items-center gap-2 text-sm font-medium text-foreground">
                <History size={15} className="text-primary" />
                测试历史记录
                <span className="text-[11px] text-muted-foreground font-normal">（最多保留 200 条 / {getStorageInfo().sizeKB}KB，存于浏览器本地）</span>
              </div>
              <div className="flex items-center gap-2">
                <select value={historyFilter} onChange={e => { setHistoryFilter(e.target.value as TestCategory | ''); }}
                  className="bg-secondary border border-border rounded px-2 py-1 text-xs">
                  <option value="">全部类型</option>
                  <option value="master">终极检测</option>
                  <option value="suite">全链路</option>
                  <option value="api">接口面板</option>
                  <option value="v2">V2 专项</option>
                  <option value="ingestion">解析质量</option>
                  <option value="status">系统状态</option>
                </select>
                <button onClick={() => { if (confirm('确定清空所有历史记录？')) { clearHistory(); refreshHistory() } }}
                  className="flex items-center gap-1 px-2 py-1 rounded text-xs text-red-400 hover:bg-red-400/10 border border-red-400/30">
                  <Trash2 size={10} />清空
                </button>
              </div>
            </div>

            {historyRecords.length === 0 ? (
              <div className="py-10 text-center">
                <History size={28} className="mx-auto text-muted-foreground mb-2 opacity-40" />
                <p className="text-sm text-muted-foreground">暂无测试记录</p>
                <p className="text-xs text-muted-foreground mt-1">运行一键测试后，结果会自动保存到这里</p>
              </div>
            ) : (
              <div className="space-y-2">
                {historyRecords.map(rec => {
                  const isExpanded = historyExpanded === rec.id
                  const passRate = rec.summary.total > 0 ? Math.round((rec.summary.passed / rec.summary.total) * 100) : 0
                  const dt = new Date(rec.timestamp)
                  const timeStr = `${dt.getFullYear()}-${String(dt.getMonth()+1).padStart(2,'0')}-${String(dt.getDate()).padStart(2,'0')} ${String(dt.getHours()).padStart(2,'0')}:${String(dt.getMinutes()).padStart(2,'0')}:${String(dt.getSeconds()).padStart(2,'0')}`
                  const catLabel = { master: '终极检测', suite: '全链路', api: '接口面板', v2: 'V2 专项', ingestion: '解析质量', status: '系统状态' }[rec.category] || rec.category

                  return (
                    <div key={rec.id} className={clsx('rounded-xl border transition-colors', isExpanded ? 'border-primary/40 bg-primary/5' : 'border-border bg-background')}>
                      <div className="flex items-center gap-3 p-3 cursor-pointer hover:bg-secondary/30" onClick={() => setHistoryExpanded(isExpanded ? null : rec.id)}>
                        <div className={clsx('w-2 h-2 rounded-full shrink-0', rec.summary.failed === 0 ? 'bg-emerald-400' : rec.summary.passed === 0 ? 'bg-red-400' : 'bg-amber-400')} />
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 text-xs">
                            <span className="font-medium text-foreground">{rec.label}</span>
                            <span className="px-1.5 py-0.5 rounded bg-secondary text-muted-foreground text-[10px]">{catLabel}</span>
                          </div>
                          <div className="flex items-center gap-3 mt-0.5 text-[10px] text-muted-foreground">
                            <span>{timeStr}</span>
                            <span className="text-emerald-400">✓{rec.summary.passed}</span>
                            {rec.summary.failed > 0 && <span className="text-red-400">✗{rec.summary.failed}</span>}
                            <span>{passRate}%</span>
                            {rec.durationMs > 0 && <span>{Math.round(rec.durationMs/1000)}s</span>}
                          </div>
                        </div>
                        <div className="flex items-center gap-1">
                          <button onClick={e => { e.stopPropagation(); setHistoryExpanded(isExpanded ? null : rec.id) }}
                            className="p-1 rounded hover:bg-secondary text-muted-foreground hover:text-foreground">
                            <Eye size={12} />
                          </button>
                          <button onClick={e => { e.stopPropagation(); deleteHistory(rec.id); refreshHistory() }}
                            className="p-1 rounded hover:bg-red-400/10 text-muted-foreground hover:text-red-400">
                            <Trash2 size={12} />
                          </button>
                        </div>
                      </div>

                      {isExpanded && (
                        <div className="border-t border-border p-3 space-y-3">
                          {rec.details && Array.isArray(rec.details) && (
                            <div className="space-y-1.5">
                              <p className="text-xs font-medium text-foreground">场景明细</p>
                              {rec.details.map((res: any, idx: number) => (
                                <details key={idx} open={res.status !== 'passed'} className={clsx('rounded border p-2 bg-card', res.status === 'passed' ? 'border-emerald-400/20' : 'border-red-400/30')}>
                                  <summary className="cursor-pointer text-xs flex items-center gap-2">
                                    <StatusBadge status={res.status} />
                                    <span className="text-foreground">{res.scenario_name}</span>
                                    <span className="text-muted-foreground">✓{res.summary?.passed ?? 0} ✗{res.summary?.failed ?? 0}</span>
                                    <span className="text-muted-foreground ml-auto">{res.duration_ms ?? 0}ms</span>
                                  </summary>
                                  <div className="mt-2 pl-1 space-y-1 text-xs">
                                    {(res.assertions || []).map((a: any, ai: number) => (
                                      <div key={ai} className={a.status === 'passed' ? 'text-emerald-400/80' : 'text-red-300'}>
                                        <span>{a.status === 'passed' ? '✓' : '✗'} {a.name}</span>
                                        {a.expected !== undefined && a.expected !== null && <div className="pl-3 text-muted-foreground">预期: {JSON.stringify(a.expected)}</div>}
                                        {a.actual !== undefined && a.actual !== null && <div className="pl-3 text-muted-foreground">实际: {JSON.stringify(a.actual)}</div>}
                                      </div>
                                    ))}
                                    {(res.steps || []).map((s: any, si: number) => (
                                      <div key={si} className={s.status === 'completed' ? 'text-muted-foreground' : 'text-orange-300'}>
                                        <span>{s.status === 'completed' ? '✓' : '✗'} [{(s.step_index ?? 0)+1}] {s.action_type} ({s.duration_ms ?? 0}ms)</span>
                                        {s.error && <div className="pl-3 text-red-300">错误: {String(s.error).split('\n')[0]}</div>}
                                      </div>
                                    ))}
                                    {(res.errors || []).map((e: string, ei: number) => <div key={ei} className="text-red-400">ERROR: {String(e).split('\n')[0]}</div>)}
                                  </div>
                                </details>
                              ))}
                            </div>
                          )}

                          {rec.logText && (
                            <details className="rounded border border-border">
                              <summary className="cursor-pointer text-xs p-2 text-muted-foreground hover:text-foreground">
                                运行日志（点击展开）
                              </summary>
                              <pre className="p-2 text-[10px] font-mono text-muted-foreground max-h-64 overflow-auto whitespace-pre-wrap">
                                {rec.logText}
                              </pre>
                            </details>
                          )}

                          <div className="flex items-center gap-2">
                            <CopyButton getText={() => {
                              const lines = [`=== 历史记录: ${rec.label} ===`, `时间: ${timeStr}`, `通过率: ${rec.summary.passed}/${rec.summary.total}`, '']
                              if (rec.logText) lines.push(rec.logText)
                              return lines.join('\n')
                            }} label="复制此次结果" />
                          </div>
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Tab: 全链路验证 ── */}
      {activeTab === 'scenarios' && (
        <p className="text-[11px] text-muted-foreground">
          {isLoading ? '场景加载中...' : isError ? '场景加载失败' : `已加载 ${(scenarios || []).length} 个验证场景`}
        </p>
      )}

      {activeTab === 'scenarios' && globalError && (
        <div className="border border-red-400/30 rounded-xl p-4 bg-red-400/5 text-xs text-red-300">
          <div className="flex items-center gap-2 mb-1"><XCircle size={13} className="text-red-400" />请求失败</div>
          <pre className="whitespace-pre-wrap">{globalError}</pre>
        </div>
      )}

      {/* 顶层：一键测试 */}
      {activeTab === 'scenarios' && (<>
      <div className="rounded-2xl border border-border bg-card p-4 space-y-3">
        <div className="flex items-center gap-2 text-sm font-medium text-foreground">
          <Layers size={15} className="text-primary" />
          一键测试（全链路）
        </div>
        <div className="text-[11px] text-muted-foreground">提示：运行完毕后可点击"AI 评估"按钮，让 AI 对测试结果进行质量评审（含逐场景分析、健康度评分、改进建议）。</div>

        <div className="flex items-center gap-2 flex-wrap">
          <button
            onClick={handleRunAll}
            disabled={running}
            className="btn-primary flex items-center gap-1.5 disabled:opacity-50"
          >
            {runAllRunning ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
            一键运行全部场景
          </button>

          {runAllDetails.length > 0 && (
            <button
              onClick={() => handleAiEvaluate(runAllDetails)}
              disabled={aiEvalLoading || running}
              className="btn-outline flex items-center gap-1.5 disabled:opacity-50 border-violet-500/40 text-violet-400 hover:bg-violet-500/10"
            >
              {aiEvalLoading ? <Loader2 size={12} className="animate-spin" /> : <BrainCircuit size={12} />}
              AI 评估测试结果
            </button>
          )}

          <CopyButton getText={() => oneClickCopy} label="复制全部结果/报错" />
        </div>

        {progressTotal > 0 && (
          <div className="flex items-center gap-2">
            <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
              <div className="h-full bg-primary rounded-full transition-all duration-500" style={{ width: `${Math.min((progressCurrent / progressTotal) * 100, 100)}%` }} />
            </div>
            <span className="text-xs text-muted-foreground font-mono">{progressCurrent}/{progressTotal}</span>
          </div>
        )}

        {progressLog.length > 0 && (
          <div className="bg-background border border-border rounded-lg p-3 h-72 overflow-y-auto font-mono">
            {progressLog.slice(-500).map((line, i) => (
              <p key={i} className={`text-xs ${line.includes('✗') || line.includes('错误') ? 'text-red-400' : line.includes('✓') || line.includes('完成') ? 'text-emerald-400' : 'text-muted-foreground'}`}>
                {line}
              </p>
            ))}
          </div>
        )}
      </div>

      {runAllDetails.length > 0 && (
        <div className="rounded-2xl border border-border bg-card p-4 space-y-3">
          <div className="flex items-center justify-between gap-2">
            <div className="text-sm font-medium text-foreground">全量调试视图（输入→预期→实际）</div>
            <CopyButton
              label="复制全量明细"
              getText={() => {
                const lines: string[] = []
                lines.push('=== 一键测试全量明细（含输入→预期→实际） ===')
                lines.push('')
                runAllDetails.forEach((res: any) => {
                  lines.push(`[${String(res.status || '').toUpperCase()}] ${res.scenario_name} (${res.scenario_id}) ${res.duration_ms ?? 0}ms`)
                  lines.push(`  断言: ✓${res.summary?.passed ?? 0} ✗${res.summary?.failed ?? 0} -${res.summary?.skipped ?? 0}`)
                  ;(res.assertions || []).forEach((a: any) => {
                    const icon = a.status === 'passed' ? '✓' : a.status === 'failed' ? '✗' : '-'
                    lines.push(`  ${icon} ${a.name}`)
                    if (a.expected !== undefined && a.expected !== null) lines.push(`    预期: ${JSON.stringify(a.expected)}`)
                    if (a.actual !== undefined && a.actual !== null) lines.push(`    实际: ${JSON.stringify(a.actual)}`)
                    if (a.reason) lines.push(`    原因: ${a.reason}`)
                  })
                  ;(res.steps || []).forEach((s: any) => {
                    const icon = s.status === 'completed' ? '✓' : s.status === 'failed' ? '✗' : '-'
                    lines.push(`  ${icon} [${(s.step_index ?? 0) + 1}] ${s.action_type} (${s.duration_ms ?? 0}ms)`)
                    if (s.error) lines.push(`    错误: ${String(s.error).split('\n')[0]}`)
                    if (s.details && Object.keys(s.details).length > 0) {
                      const d = s.details
                      const preview = Object.entries(d).filter(([,v]) => v !== null && v !== undefined).map(([k,v]) => `${k}=${typeof v === 'string' ? v.slice(0,80) : JSON.stringify(v)}`).join(', ')
                      if (preview) lines.push(`    详情: ${preview.slice(0, 200)}`)
                    }
                  })
                  ;(res.errors || []).forEach((e: string) => lines.push(`  ERROR: ${String(e).split('\n')[0]}`))
                  lines.push('')
                })
                return lines.join('\n')
              }}
            />
          </div>

          <div className="space-y-2 max-h-[500px] overflow-auto">
            {runAllDetails.map((res: any) => (
              <details key={res.scenario_id} open={res.status !== 'passed'} className={`rounded border p-2 bg-background ${res.status === 'passed' ? 'border-emerald-400/20' : 'border-red-400/30'}`}>
                <summary className="cursor-pointer text-xs flex items-center gap-2">
                  <StatusBadge status={res.status} />
                  <span className="text-foreground">{res.scenario_name}</span>
                  <span className="text-muted-foreground">✓{res.summary?.passed ?? 0} ✗{res.summary?.failed ?? 0}</span>
                  <span className="text-muted-foreground ml-auto">{res.duration_ms ?? 0}ms</span>
                </summary>
                <div className="mt-2 pl-1 space-y-1.5 text-xs">
                  {(res.assertions || []).map((a: any, i: number) => (
                    <div key={`a-${i}`} className={a.status === 'passed' ? 'text-emerald-400/80' : 'text-red-300'}>
                      <span>{a.status === 'passed' ? '✓' : '✗'} {a.name}</span>
                      {(a.expected !== undefined && a.expected !== null) && <div className="pl-3 text-muted-foreground">预期: {JSON.stringify(a.expected)}</div>}
                      {(a.actual !== undefined && a.actual !== null) && <div className="pl-3 text-muted-foreground">实际: {JSON.stringify(a.actual)}</div>}
                      {a.reason && <div className="pl-3 text-orange-300">原因: {a.reason}</div>}
                    </div>
                  ))}
                  {(res.steps || []).map((s: any, i: number) => (
                    <div key={`s-${i}`} className={s.status === 'completed' ? 'text-muted-foreground' : 'text-orange-300'}>
                      <span>{s.status === 'completed' ? '✓' : '✗'} [{(s.step_index ?? 0) + 1}] {s.action_type} <span className="opacity-60">({s.duration_ms ?? 0}ms)</span></span>
                      {s.error && <div className="pl-3 text-red-300">错误: {String(s.error).split('\n')[0]}</div>}
                      {s.details && Object.keys(s.details).length > 0 && (
                        <div className="pl-3 text-muted-foreground/70 truncate max-w-xl">
                          {Object.entries(s.details).filter(([,v]) => v !== null && v !== undefined).map(([k,v]) =>
                            `${k}=${typeof v === 'string' ? v.slice(0,60) : JSON.stringify(v)}`
                          ).join(' | ').slice(0, 200)}
                        </div>
                      )}
                    </div>
                  ))}
                  {(res.errors || []).map((e: string, i: number) => (
                    <div key={`e-${i}`} className="text-red-400">错误：{String(e).split('\n')[0]}</div>
                  ))}
                </div>
              </details>
            ))}
          </div>
        </div>
      )}

      {/* AI 评估结果面板 */}
      {(aiEvalResult || aiEvalLoading || aiEvalError) && (
        <div className="rounded-2xl border border-violet-500/30 bg-card p-4 space-y-3">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2 text-sm font-medium text-violet-400">
              <BrainCircuit size={15} />
              AI 质量评审报告
              {aiEvalModel && <span className="text-[11px] text-muted-foreground font-normal">（{aiEvalModel}）</span>}
            </div>
            {aiEvalResult && (
              <CopyButton getText={() => `=== AI 质量评审报告 ===\n模型: ${aiEvalModel}\n\n${aiEvalResult}`} label="复制评审报告" />
            )}
          </div>

          {aiEvalLoading && (
            <div className="flex items-center gap-2 py-6 justify-center">
              <Loader2 size={16} className="animate-spin text-violet-400" />
              <span className="text-xs text-muted-foreground">AI 正在分析测试结果，请稍候...</span>
            </div>
          )}

          {aiEvalError && (
            <div className="border border-red-400/30 rounded-lg p-3 bg-red-400/5 text-xs text-red-300">
              <pre className="whitespace-pre-wrap">{aiEvalError}</pre>
            </div>
          )}

          {aiEvalResult && (
            <div className="prose prose-sm prose-invert max-w-none text-xs leading-relaxed">
              <div className="whitespace-pre-wrap text-foreground/90"
                dangerouslySetInnerHTML={{
                  __html: aiEvalResult
                    .replace(/^### (.+)$/gm, '<h3 class="text-sm font-semibold text-foreground mt-3 mb-1">$1</h3>')
                    .replace(/^## (.+)$/gm, '<h2 class="text-sm font-bold text-foreground mt-4 mb-1">$1</h2>')
                    .replace(/^# (.+)$/gm, '<h1 class="text-base font-bold text-foreground mt-4 mb-2">$1</h1>')
                    .replace(/\*\*(.+?)\*\*/g, '<strong class="text-foreground">$1</strong>')
                    .replace(/🟢/g, '<span class="text-emerald-400">🟢</span>')
                    .replace(/🟡/g, '<span class="text-yellow-400">🟡</span>')
                    .replace(/🔴/g, '<span class="text-red-400">🔴</span>')
                    .replace(/\n/g, '<br/>')
                }}
              />
            </div>
          )}
        </div>
      )}

      {/* 中层：分类测试 */}
      <div className="rounded-2xl border border-border bg-card p-4 space-y-3">
        <div className="flex items-center gap-2 text-sm font-medium text-foreground">
          <TestTube2 size={15} className="text-primary" />
          分类测试（按模块）
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {Array.from(grouped.entries()).map(([cat, list]) => {
            const cRes = categoryResult[cat]
            return (
              <div key={cat} className="rounded-xl border border-border bg-background p-3 space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <div>
                    <p className="text-xs font-semibold text-foreground">{cat}</p>
                    <p className="text-[11px] text-muted-foreground">场景数：{list.length}</p>
                  </div>
                  <button
                    onClick={() => handleRunSuite(list.map((x) => x.scenario_id), cat)}
                    disabled={running}
                    className="px-2 py-1 rounded bg-secondary text-xs hover:bg-border disabled:opacity-50"
                  >
                    运行本类
                  </button>
                </div>

                <div className="text-[11px] text-muted-foreground max-h-16 overflow-auto">
                  {list.map((s) => s.name).join(' / ')}
                </div>

                {cRes && (
                  <div className="rounded border border-border p-2 space-y-1">
                    <div className="text-xs text-foreground">通过 {cRes.summary.passed}/{cRes.summary.total}</div>
                    <div className="text-[11px] text-muted-foreground">通过率 {cRes.summary.pass_rate}%</div>
                    <CopyButton getText={() => cRes.copy_text} label="复制本类结果" />
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>

      {/* 下层：单点测试 */}
      <div className="rounded-2xl border border-border bg-card p-4 space-y-4">
        <div className="flex items-center gap-2 text-sm font-medium text-foreground">
          <Sparkles size={15} className="text-primary" />
          单点测试（功能级）
        </div>

        <div className="flex items-center gap-2">
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索场景：名称 / 描述 / ID"
            className="w-full bg-secondary border border-border rounded px-3 py-2 text-xs"
          />
          <CopyButton
            getText={() => aiSupervisorPromptTemplate((suiteResult?.results || []))}
            label="复制 AI 监督提示"
          />
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-[320px_1fr] gap-4">
          <div className="space-y-2 max-h-[520px] overflow-auto pr-1">
            {isLoading && <div className="text-xs text-muted-foreground">加载中...</div>}
            {filteredScenarios.map((s) => (
              <div key={s.scenario_id} className={clsx('border rounded-xl p-3', selected === s.scenario_id ? 'border-primary bg-primary/5' : 'border-border bg-background')}>
                <div className="flex items-start justify-between gap-2 mb-1">
                  <span className="text-xs font-medium text-foreground leading-tight">{s.name}</span>
                  <button
                    onClick={() => handleRun(s.scenario_id)}
                    disabled={running}
                    className="shrink-0 flex items-center gap-1 text-xs text-primary hover:opacity-70 disabled:opacity-40"
                  >
                    {running && selected === s.scenario_id ? <Loader2 size={11} className="animate-spin" /> : <Play size={11} />}运行
                  </button>
                </div>
                <p className="text-xs text-muted-foreground">{s.description}</p>
                <p className="text-[10px] text-muted-foreground mt-1">{classifyScenario(s)}</p>
              </div>
            ))}
          </div>

          <div>
            {!report && !running && (
              <div className="border border-dashed border-border rounded-xl p-10 text-center">
                <FlaskConical size={24} className="mx-auto text-muted-foreground mb-2" />
                <p className="text-sm text-muted-foreground">选择左侧场景并点击“运行”</p>
              </div>
            )}
            {running && !report && (
              <div className="border border-border rounded-xl p-10 text-center">
                <Loader2 size={24} className="mx-auto text-primary animate-spin mb-2" />
                <p className="text-sm text-muted-foreground">执行中...</p>
              </div>
            )}
            {report && (
              <div className="space-y-3">
                <div className="flex items-center gap-3 flex-wrap">
                  <StatusBadge status={report.status} />
                  <span className="text-xs text-muted-foreground">{report.scenario_name}</span>
                  <span className="text-xs text-muted-foreground">{report.duration_ms}ms</span>
                  <span className="text-xs text-emerald-400">✓ {report.summary.passed}</span>
                  {report.summary.failed > 0 && <span className="text-xs text-red-400">✗ {report.summary.failed}</span>}
                </div>

                <div className="rounded-xl border border-border bg-background p-3">
                  <p className="text-xs font-medium text-foreground mb-2">步骤时间线</p>
                  {report.steps.map((s) => <StepRow key={s.step_index} step={s} />)}
                </div>

                <div className="rounded-xl border border-border bg-background p-3">
                  <p className="text-xs font-medium text-foreground mb-2">断言结果</p>
                  <div className="space-y-1">
                    {report.assertions.map((a, idx) => (
                      <div key={idx} className="flex items-center gap-2 text-xs">
                        <StatusBadge status={a.status} />
                        <span className="text-foreground">{a.name}</span>
                        {a.reason && <span className="text-muted-foreground">— {a.reason}</span>}
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      <TestHistorySection category="suite" />

      {/* AI监督与预期指标区 */}
      <div className="rounded-2xl border border-border bg-card p-4 space-y-2 text-xs">
        <div className="font-medium text-foreground">AI 监督建议（全链路）</div>
        <div className="text-muted-foreground">
          建议将"复制 AI 监督提示"内容提交给联网模型，重点校验：
          1) 预期切片结果；2) 识别处理结果；3) 输入输出一致性；4) 数据库存储状态。
        </div>
      </div>
      </>)}
    </div>
  )
}
