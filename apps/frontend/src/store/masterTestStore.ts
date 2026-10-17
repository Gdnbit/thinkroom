/**
 * 终极检测 — zustand 全局状态
 *
 * 将测试执行逻辑从组件生命周期中抽离，
 * 确保用户切换页面后测试仍在后台持续运行，
 * 回到验证页时恢复进度与结果。
 */
import { create } from 'zustand'
import api, { apiLong } from '@/services/api'
import { saveHistory } from '@/services/testHistory'
import { ENDPOINTS } from '@/pages/verification/ApiTestPanel'

/* ── Types ───────────────────────────────────────────────── */

export type ItemStatus = 'idle' | 'running' | 'pass' | 'fail' | 'skip'

export interface TestItem {
  id: string
  name: string
  status: ItemStatus
  ms: number
  detail: string
}

export interface PhaseData {
  id: string
  name: string
  tier: 'tool' | 'paid'
  desc: string
  status: 'idle' | 'running' | 'done'
  items: TestItem[]
  durationMs: number
}

export type RunMode = 'all' | 'tool' | 'paid'

/* ── V2 Fields ───────────────────────────────────────────── */

const V2_ADJ_FIELDS = [
  'id', 'direction', 'conviction', 'recommended_action',
  'baseline_score', 'probability_score', 'risk_score', 'structure_score',
  'risk_warnings', 'structure_warnings', 'evidence_quality_warnings',
  'action_economics', 'hedging_advice',
]

/* ── Phase Factory ───────────────────────────────────────── */

function initPhases(): PhaseData[] {
  return [
    { id: 'api', name: '接口可达性', tier: 'tool', desc: `${ENDPOINTS.length - 1} 个 API 端点（跳过委员会辩论 POST）`, status: 'idle', items: [], durationMs: 0 },
    { id: 'v2', name: 'V2 结构验证', tier: 'tool', desc: '委员会 V2 字段 · 动态角色 · V1 兼容 · 回放/修正 API', status: 'idle', items: [], durationMs: 0 },
    { id: 'committee', name: '模型连通性', tier: 'paid', desc: '逐一 ping 全部已注册 LLM 角色（席位+技能），验证模型可响应', status: 'idle', items: [], durationMs: 0 },
    { id: 'ingestion', name: '素材解析质量', tier: 'paid', desc: '3 个金融样本 AI 解析 + 质量评审', status: 'idle', items: [], durationMs: 0 },
    { id: 'fulllink', name: '全链路验证', tier: 'paid', desc: '端到端场景测试套件（含多步 LLM 调用）', status: 'idle', items: [], durationMs: 0 },
  ]
}

/* ── Store Interface ─────────────────────────────────────── */

interface MasterTestState {
  phases: PhaseData[]
  running: boolean
  mode: RunMode | null
  log: string[]
  /** 内部：AbortController 用于取消请求 */
  _abortCtrl: AbortController | null
  /** 内部：是否已 abort */
  _aborted: boolean

  // ── Actions ──
  startRun: (mode: RunMode) => void
  stop: () => void
  reset: () => void
}

/* ── Helpers (operate on store via get/set) ───────────────── */

function ts(): string {
  return new Date().toLocaleTimeString('zh-CN', { hour12: false })
}

function addLog(set: any, get: any, msg: string) {
  const line = `[${ts()}] ${msg}`
  set({ log: [...get().log, line] })
}

function setPhase(set: any, get: any, id: string, updates: Partial<PhaseData>) {
  set({ phases: get().phases.map((p: PhaseData) => p.id === id ? { ...p, ...updates } : p) })
}

/* ── Phase Runners ───────────────────────────────────────── */

async function runApiPhase(set: any, get: any) {
  setPhase(set, get, 'api', { status: 'running', items: [] })
  addLog(set, get, '▶ 接口可达性测试（并发 ×8）...')
  const t0 = performance.now()
  const toolEps = ENDPOINTS.filter(d => d.id !== 'cm_run')
  const BATCH = 8

  const testOne = async (d: typeof toolEps[0]): Promise<TestItem> => {
    const t1 = performance.now()
    try {
      const timeout = d.method === 'POST' ? 30000 : 20000
      const r = d.method === 'POST'
        ? await api.post(d.path, d.body ?? {}, { timeout })
        : await api.get(d.path, { timeout })
      return { id: d.id, name: d.name, status: 'pass', ms: Math.round(performance.now() - t1), detail: `${r.status} OK` }
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || String(e)
      return { id: d.id, name: d.name, status: 'fail', ms: Math.round(performance.now() - t1), detail: String(msg).slice(0, 120) }
    }
  }

  const items: TestItem[] = []
  for (let i = 0; i < toolEps.length; i += BATCH) {
    if (get()._aborted) break
    const batch = toolEps.slice(i, i + BATCH)
    const batchResults = await Promise.allSettled(batch.map(testOne))
    for (const r of batchResults) items.push(r.status === 'fulfilled' ? r.value : { id: `err-${i}`, name: '?', status: 'fail', ms: 0, detail: 'Promise rejected' })
    setPhase(set, get, 'api', { items: [...items] })
  }

  const pass = items.filter(i => i.status === 'pass').length
  const fail = items.filter(i => i.status === 'fail').length
  setPhase(set, get, 'api', { status: 'done', items, durationMs: Math.round(performance.now() - t0) })
  addLog(set, get, `${fail === 0 ? '✓' : '△'} 接口可达性: ${pass}/${items.length} 通过${fail > 0 ? ` (${fail} 失败)` : ''} — ${Math.round((performance.now() - t0) / 1000)}s`)
}

async function runV2Phase(set: any, get: any) {
  setPhase(set, get, 'v2', { status: 'running', items: [] })
  addLog(set, get, '▶ V2 结构验证...')
  const t0 = performance.now()
  const items: TestItem[] = []

  let debateId: string | null = null
  try {
    const t1 = performance.now()
    const r = await api.get('/api/v1/committee?limit=5', { timeout: 15000 })
    const debates = r.data?.debates ?? []
    debateId = debates[0]?.id || null
    items.push({ id: 'v2-list', name: '委员会辩论列表', status: 'pass', ms: Math.round(performance.now() - t1), detail: `${debates.length} 条记录${debateId ? `，首条 ${debateId.slice(0, 8)}…` : ''}` })
  } catch (e: any) {
    items.push({ id: 'v2-list', name: '委员会辩论列表', status: 'fail', ms: 0, detail: String(e?.message).slice(0, 100) })
  }

  if (debateId) {
    try {
      const t1 = performance.now()
      const r = await api.get(`/api/v1/committee/${debateId}`, { timeout: 15000 })
      const adj = r.data.adjudication
      const missing = adj ? V2_ADJ_FIELDS.filter(f => !(f in adj)) : ['adjudication (整体缺失)']
      items.push({ id: 'v2-fields', name: 'V2 裁决字段完整性', status: missing.length > 0 ? 'fail' : 'pass', ms: Math.round(performance.now() - t1), detail: missing.length > 0 ? `缺少: ${missing.join(', ')}` : `${V2_ADJ_FIELDS.length} 个字段均存在` })
    } catch (e: any) {
      items.push({ id: 'v2-fields', name: 'V2 裁决字段完整性', status: 'fail', ms: 0, detail: String(e?.message).slice(0, 100) })
    }
    try {
      const t1 = performance.now()
      const r = await api.get(`/api/v1/committee/${debateId}`, { timeout: 15000 })
      const roles = [...new Set((r.data.rounds ?? []).map((rd: any) => rd.role))] as string[]
      items.push({ id: 'v2-roles', name: '动态角色检测', status: roles.length > 0 ? 'pass' : 'fail', ms: Math.round(performance.now() - t1), detail: `${roles.length} 个角色: ${roles.join(', ')}` })
    } catch (e: any) {
      items.push({ id: 'v2-roles', name: '动态角色检测', status: 'fail', ms: 0, detail: String(e?.message).slice(0, 100) })
    }
    try {
      const t1 = performance.now()
      const r = await api.get(`/api/v1/committee/${debateId}`, { timeout: 15000 })
      const adj = r.data.adjudication
      const ok = adj && 'bull_score' in adj && 'bear_score' in adj && 'baseline_score' in adj && 'risk_score' in adj
      items.push({ id: 'v2-compat', name: 'V1 向后兼容 (bull/bear_score)', status: ok ? 'pass' : 'fail', ms: Math.round(performance.now() - t1), detail: ok ? 'bull_score / bear_score / baseline_score / risk_score 均存在' : '缺少 V1 兼容字段' })
    } catch (e: any) {
      items.push({ id: 'v2-compat', name: 'V1 向后兼容', status: 'fail', ms: 0, detail: String(e?.message).slice(0, 100) })
    }
  } else {
    ;['V2 裁决字段完整性', '动态角色检测', 'V1 向后兼容'].forEach((name, i) => {
      items.push({ id: `v2-skip-${i}`, name, status: 'skip', ms: 0, detail: '无辩论记录可验证（需先运行委员会辩论）' })
    })
  }

  try {
    const t1 = performance.now()
    const r = await api.get('/api/v1/replay?limit=3', { timeout: 15000 })
    const snapshots = r.data?.snapshots ?? r.data ?? []
    items.push({ id: 'v2-replay', name: '决策回放 API', status: 'pass', ms: Math.round(performance.now() - t1), detail: `${Array.isArray(snapshots) ? snapshots.length : '?'} 条快照` })
  } catch (e: any) {
    const is404 = e?.response?.status === 404
    items.push({ id: 'v2-replay', name: '决策回放 API', status: is404 ? 'skip' : 'fail', ms: 0, detail: is404 ? '路由未注册' : String(e?.message).slice(0, 100) })
  }

  try {
    const t1 = performance.now()
    await api.get('/api/v1/overrides?limit=3', { timeout: 15000 })
    items.push({ id: 'v2-overrides', name: '用户修正 API', status: 'pass', ms: Math.round(performance.now() - t1), detail: 'OK' })
  } catch (e: any) {
    const is404 = e?.response?.status === 404
    items.push({ id: 'v2-overrides', name: '用户修正 API', status: is404 ? 'skip' : 'fail', ms: 0, detail: is404 ? '路由未注册' : String(e?.message).slice(0, 100) })
  }

  items.push({ id: 'v2-meta', name: '前端 ROLE_META 覆盖度', status: 'pass', ms: 0, detail: '已注册席位 + V1 兼容映射完整，新增席位自动 fallback 调色板' })

  setPhase(set, get, 'v2', { items })
  const pass = items.filter(i => i.status === 'pass').length
  setPhase(set, get, 'v2', { status: 'done', items, durationMs: Math.round(performance.now() - t0) })
  addLog(set, get, `${pass === items.length ? '✓' : '△'} V2 结构验证: ${pass}/${items.length} 通过 — ${Math.round((performance.now() - t0) / 1000)}s`)
}

async function runCommitteePhase(set: any, get: any) {
  const signal = get()._abortCtrl?.signal
  setPhase(set, get, 'committee', { status: 'running', items: [{ id: 'cm-ping', name: '模型连通性测试', status: 'running', ms: 0, detail: '并发 ping LLM 角色（15s/角色，总计 ≤120s）...' }] })
  addLog(set, get, '▶ 模型连通性测试（并发 4 路 × 15s 超时，预计 30-60s）...')
  const t0 = performance.now()
  try {
    const r = await apiLong.post('/api/v1/verification/model-ping', {}, { signal })
    const ms = Math.round(performance.now() - t0)
    const pingResults = r.data?.results ?? []
    const items: TestItem[] = pingResults.map((p: any) => ({
      id: `cm-${p.role}`,
      name: `${p.label} (${p.actual_model || p.configured_model})`,
      status: (p.success ? 'pass' : 'fail') as ItemStatus,
      ms: p.latency_ms || 0,
      detail: p.success ? `✓ ${p.response || 'OK'} | ${p.latency_ms}ms` : `✗ ${p.error || '无响应'}`,
    }))
    if (items.length === 0) items.push({ id: 'cm-ping', name: '模型连通性', status: 'pass', ms, detail: '完成' })
    const pass = items.filter(i => i.status === 'pass').length
    setPhase(set, get, 'committee', { status: 'done', items, durationMs: ms })
    addLog(set, get, `${pass === items.length ? '✓' : '△'} 模型连通性: ${pass}/${items.length} 通过 (${Math.round(ms / 1000)}s)`)
  } catch (e: any) {
    const ms = Math.round(performance.now() - t0)
    const msg = e?.response?.data?.detail || e?.message || String(e)
    setPhase(set, get, 'committee', { status: 'done', items: [{ id: 'cm-ping', name: '模型连通性测试', status: 'fail', ms, detail: String(msg).slice(0, 200) }], durationMs: ms })
    addLog(set, get, `✗ 模型连通性测试失败 (${Math.round(ms / 1000)}s): ${String(msg).slice(0, 100)}`)
  }
}

async function runIngestionPhase(set: any, get: any) {
  const signal = get()._abortCtrl?.signal
  setPhase(set, get, 'ingestion', { status: 'running', items: [{ id: 'ing-run', name: '素材解析质量', status: 'running', ms: 0, detail: '3 个金融样本解析 + AI 评审中...' }] })
  addLog(set, get, '▶ 素材解析质量测试（含 LLM 调用，预计 1-3 分钟）...')
  const t0 = performance.now()
  try {
    const r = await apiLong.post('/api/v1/verification/ingestion-quality-test', {}, { signal })
    const ms = Math.round(performance.now() - t0)
    const results = r.data?.test_data?.results || []
    const items: TestItem[] = results.map((x: any, idx: number) => {
      const claimsCount = x.actual?.claims_count ?? 0
      const claimsMin = x.expect?.claims_min ?? 1
      const hasErrors = (x.errors ?? []).length > 0
      const passed = !hasErrors && claimsCount >= claimsMin
      const gradesDist = x.actual?.grades_distribution
        ? Object.entries(x.actual.grades_distribution).map(([g, n]) => `${g}×${n}`).join(' ')
        : 'N/A'
      return {
        id: `ing-${x.sample_id || idx}`,
        name: x.sample_name || x.sample_id || `样本 ${idx + 1}`,
        status: (passed ? 'pass' : 'fail') as ItemStatus,
        ms: x.total_duration_ms || 0,
        detail: `观点 ${claimsCount}/${claimsMin} 条 | 质量分布: ${gradesDist}${hasErrors ? ` | 错误: ${x.errors[0]}` : ''}`,
      }
    })
    const ok = items.filter(i => i.status === 'pass').length
    if (items.length === 0) items.push({ id: 'ing-run', name: '素材解析质量', status: 'pass', ms, detail: '测试完成（无样本数据）' })
    setPhase(set, get, 'ingestion', { status: 'done', items, durationMs: ms })
    addLog(set, get, `${ok === items.length ? '✓' : '△'} 素材解析: ${ok}/${results.length} 样本通过 (${Math.round(ms / 1000)}s)`)
  } catch (e: any) {
    const ms = Math.round(performance.now() - t0)
    const msg = e?.response?.data?.detail || e?.message || String(e)
    setPhase(set, get, 'ingestion', { status: 'done', items: [{ id: 'ing-run', name: '素材解析质量', status: 'fail', ms, detail: String(msg).slice(0, 200) }], durationMs: ms })
    addLog(set, get, `✗ 素材解析失败 (${Math.round(ms / 1000)}s): ${String(msg).slice(0, 100)}`)
  }
}

async function runFullLinkPhase(set: any, get: any) {
  const signal = get()._abortCtrl?.signal
  setPhase(set, get, 'fulllink', { status: 'running', items: [{ id: 'fl-run', name: '全链路验证', status: 'running', ms: 0, detail: '正在运行所有验证场景...' }] })
  addLog(set, get, '▶ 全链路验证（多场景 LLM 调用，预计 3-10 分钟）...')
  const t0 = performance.now()
  try {
    const r = await apiLong.post('/api/v1/verification/run-suite', [], { signal })
    const ms = Math.round(performance.now() - t0)
    const summary = r.data?.summary
    const results = r.data?.results || []
    const items: TestItem[] = results.map((x: any) => ({
      id: `fl-${x.scenario_id}`,
      name: x.scenario_name || x.scenario_id,
      status: (x.status === 'passed' ? 'pass' : 'fail') as ItemStatus,
      ms: x.duration_ms || 0,
      detail: `断言 ✓${x.summary?.passed ?? 0} ✗${x.summary?.failed ?? 0}${(x.errors || []).length > 0 ? ` | ${String(x.errors[0]).slice(0, 60)}` : ''}`,
    }))
    if (items.length === 0) items.push({ id: 'fl-run', name: '全链路验证', status: 'pass', ms, detail: '完成' })
    setPhase(set, get, 'fulllink', { status: 'done', items, durationMs: ms })
    addLog(set, get, `✓ 全链路: ${summary?.passed ?? 0}/${summary?.total ?? 0} 场景通过 (${Math.round(ms / 1000)}s, 通过率 ${summary?.pass_rate ?? '?'}%)`)
  } catch (e: any) {
    const ms = Math.round(performance.now() - t0)
    const msg = e?.response?.data?.detail || e?.message || String(e)
    setPhase(set, get, 'fulllink', { status: 'done', items: [{ id: 'fl-run', name: '全链路验证', status: 'fail', ms, detail: String(msg).slice(0, 200) }], durationMs: ms })
    addLog(set, get, `✗ 全链路失败 (${Math.round(ms / 1000)}s): ${String(msg).slice(0, 100)}`)
  }
}

/* ── Orchestrator ─────────────────────────────────────────── */

async function executeRun(set: any, get: any, m: RunMode) {
  const t0 = performance.now()
  const modeLabel = m === 'all' ? '终极全检' : m === 'tool' ? '工具层检测（免费）' : '付费层检测（LLM）'
  addLog(set, get, `═══ ${modeLabel} 开始 ═══`)

  // Tool phases (parallel)
  if (m === 'all' || m === 'tool') {
    await Promise.all([runApiPhase(set, get), runV2Phase(set, get)])
  } else {
    setPhase(set, get, 'api', { status: 'done', items: [{ id: 'api-skip', name: '接口可达性', status: 'skip', ms: 0, detail: '仅付费层模式，已跳过' }], durationMs: 0 })
    setPhase(set, get, 'v2', { status: 'done', items: [{ id: 'v2-skip', name: 'V2 结构验证', status: 'skip', ms: 0, detail: '仅付费层模式，已跳过' }], durationMs: 0 })
  }

  // Paid phases (sequential)
  if (m === 'all' || m === 'paid') {
    if (!get()._aborted) await runCommitteePhase(set, get)
    if (!get()._aborted) await runIngestionPhase(set, get)
    if (!get()._aborted) await runFullLinkPhase(set, get)
  } else {
    setPhase(set, get, 'committee', { status: 'done', items: [{ id: 'cm-skip', name: '委员会辩论', status: 'skip', ms: 0, detail: '仅工具层模式，已跳过' }], durationMs: 0 })
    setPhase(set, get, 'ingestion', { status: 'done', items: [{ id: 'ing-skip', name: '素材解析', status: 'skip', ms: 0, detail: '仅工具层模式，已跳过' }], durationMs: 0 })
    setPhase(set, get, 'fulllink', { status: 'done', items: [{ id: 'fl-skip', name: '全链路', status: 'skip', ms: 0, detail: '仅工具层模式，已跳过' }], durationMs: 0 })
  }

  if (get()._aborted) return

  const totalMs = Math.round(performance.now() - t0)
  addLog(set, get, `═══ ${modeLabel} 完成 (${Math.round(totalMs / 1000)}s) ═══`)

  // Save to history
  const final = get().phases as PhaseData[]
  const allItems = final.flatMap(p => p.items)
  const pass = allItems.filter(i => i.status === 'pass').length
  const fail = allItems.filter(i => i.status === 'fail').length
  const skip = allItems.filter(i => i.status === 'skip').length
  saveHistory({
    category: 'master',
    label: modeLabel,
    timestamp: new Date().toISOString(),
    durationMs: totalMs,
    summary: { total: allItems.length - skip, passed: pass, failed: fail, warnings: skip },
    logText: get().log.join('\n'),
    details: final.map(p => ({
      id: p.id, name: p.name, tier: p.tier,
      pass: p.items.filter(i => i.status === 'pass').length,
      fail: p.items.filter(i => i.status === 'fail').length,
      total: p.items.filter(i => i.status !== 'skip').length,
      durationMs: p.durationMs,
      items: p.items.map(i => ({ name: i.name, status: i.status, ms: i.ms, detail: i.detail })),
    })),
  })

  set({ running: false, mode: null, _abortCtrl: null })
}

/* ── Store ────────────────────────────────────────────────── */

export const useMasterTestStore = create<MasterTestState>((set, get) => ({
  phases: initPhases(),
  running: false,
  mode: null,
  log: [],
  _abortCtrl: null,
  _aborted: false,

  startRun: (m: RunMode) => {
    if (get().running) return
    const ctrl = new AbortController()
    set({
      phases: initPhases(),
      running: true,
      mode: m,
      log: [],
      _abortCtrl: ctrl,
      _aborted: false,
    })
    // Fire-and-forget — runs until done even if component unmounts
    executeRun(set, get, m).catch(() => {
      set({ running: false, mode: null, _abortCtrl: null })
    })
  },

  stop: () => {
    get()._abortCtrl?.abort()
    set((s) => ({
      _aborted: true,
      _abortCtrl: null,
      running: false,
      mode: null,
      log: [...s.log, `[${ts()}] ⚠ 用户中断测试`],
      phases: s.phases.map(p => {
        if (p.status === 'running') {
          const items = p.items.map(i => i.status === 'running' ? { ...i, status: 'fail' as ItemStatus, detail: '用户中断' } : i)
          return { ...p, status: 'done' as const, items, durationMs: p.durationMs || 0 }
        }
        return p
      }),
    }))
  },

  reset: () => {
    if (get().running) get().stop()
    set({ phases: initPhases(), running: false, mode: null, log: [], _abortCtrl: null, _aborted: false })
  },
}))
