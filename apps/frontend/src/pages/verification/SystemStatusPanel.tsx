import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Wifi, WifiOff, Loader2, RefreshCw, DollarSign, Activity,
  ArrowRight, Globe, AlertCircle, Shield, CheckCircle2, History, ChevronDown, ChevronUp, XCircle,
} from 'lucide-react'
import { clsx } from 'clsx'
import api from '@/services/api'
import {
  buildProductionDigestView,
  type ProductionReadinessDigest,
} from '@/utils/productionReadinessDigest'
import {
  buildProductionRefreshView,
  type ProductionReadinessRefreshReport,
} from '@/utils/productionReadinessRefresh'
import TraceEvalReviewPanel from './TraceEvalReviewPanel'

type CostGranularity = 'daily' | 'monthly' | 'yearly'

interface TraceConsistency {
  status: 'ok' | 'warning' | string
  dialect: string
  trace_event_count: number
  event_source_pair_count: number
  link_count: number
  missing_link_count: number
  orphan_link_count: number
  sample_missing_links: Array<{ trace_event_id: string; source_id: string }>
  sample_orphan_links: Array<{ trace_event_id: string; source_id: string }>
}

interface IngestionDispatcherJob {
  job_id: string
  source_id?: string | null
  status?: string
  stage?: string | null
  pct?: number | null
  message?: string | null
  retry_count?: number
  lease_state?: string | null
  lease_worker_id?: string | null
  lease_heartbeat_at?: string | null
  lease_expires_at?: string | null
  lease_seconds?: number | null
  lease_expired?: boolean
}

interface IngestionDispatcherStatus {
  dispatcher?: string
  max_workers?: number
  active_or_queued_count?: number
  persisted_active_count?: number
  persisted_active_job_ids?: string[]
  persisted_active_jobs?: IngestionDispatcherJob[]
}

interface IngestionReapResult {
  checked?: number
  stale?: number
  recoverable?: number
  resumed?: number
  closed?: number
  failed?: number
  dead_lettered?: number
  jobs?: Array<{ job_id?: string; source_id?: string; action?: string }>
}

interface DocumentParserCapabilities {
  requested_adapter?: string
  active_adapter?: string
  status?: string
  input_modes?: Array<{ kind?: string; status?: string; notes?: string[] }>
  features?: Record<string, boolean>
  notes?: string[]
}

interface ApiHealth {
  status?: string
  capabilities?: {
    document_parser?: DocumentParserCapabilities
  }
}

interface HealthCheck {
  ok?: boolean
  error?: string
  latency_ms?: number
  dialect?: string
  base_url?: string
}

interface DeploymentProfile {
  mode?: string
  label?: string
  allows_degraded_optional_dependencies?: boolean
  required_checks?: string[]
  optional_checks?: string[]
  blocking_checks?: string[]
}

interface ReleaseReadinessCheck {
  key?: string
  label?: string
  status?: 'pass' | 'warn' | 'fail' | string
  detail?: string
  remediation?: string
  signals?: Record<string, unknown>
}

interface ReleaseReadinessReport {
  status?: 'pass' | 'warn' | 'fail' | string
  deployment_profile?: DeploymentProfile
  summary?: {
    pass?: number
    warn?: number
    fail?: number
  }
  checks?: ReleaseReadinessCheck[]
  production_readiness_digest?: ProductionReadinessDigest
  production_readiness_refresh?: ProductionReadinessRefreshReport
  production_env_materialization?: ProductionEnvMaterializationReport
  main_flow_runtime_smoke?: MainFlowRuntimeSmokeReport
  evidence_sources?: {
    production_readiness_digest_path?: string
    production_readiness_refresh_path?: string
    production_env_materialization_path?: string
    main_flow_runtime_smoke_path?: string
  }
  generated_at?: string
}

interface ProductionEnvMaterializationRequest {
  config_key?: string
  suggested_sops_key?: string
  source_marker_key?: string
  sensitivity?: string
  can_be_derived_from?: string[]
  accepted_source_prefixes?: string[]
}

interface ProductionEnvMaterializationReport {
  available?: boolean
  path?: string
  status?: string
  fresh?: boolean
  freshness_status?: string
  dry_run?: boolean | null
  output_path?: string
  output_written?: boolean
  would_write_output?: boolean
  missing_required_key_count?: number | null
  missing_required_keys?: string[]
  missing_config_request_count?: number
  missing_config_requests?: ProductionEnvMaterializationRequest[]
  alias_resolution?: Record<string, string>
  operator_commands?: string[]
}

interface MainFlowRuntimeSmokeReport {
  available?: boolean
  path?: string
  status?: string
  fresh?: boolean
  freshness_status?: string
  age_seconds?: number | null
  max_age_seconds?: number
  generated_at?: string | null
  checked_at?: string | null
  session_id?: string | null
  include_committee?: boolean
  step_count?: number
  steps?: string[]
  errors?: string[]
  warnings?: string[]
  operator_command?: string
}

interface ProductionPreflightAction {
  key?: string
  label?: string
  status?: string
  action?: string
  owner?: string
  can_auto_fix?: boolean
  requires_secret_update?: boolean
  production_required?: boolean
  local_demo_blocking?: boolean
  config_keys?: string[]
  verification?: Array<{
    kind?: string
    target?: string
    expectation?: string
  }>
}

interface ProductionPreflightDecisionPath {
  key?: string
  label?: string
  status?: string
  ready?: boolean
  blocker_count?: number
  warning_count?: number
  production_warning_count?: number
  required_action_count?: number
  action_keys?: string[]
}

interface ProductionPreflightReport {
  status?: 'pass' | 'warn' | 'fail' | string
  acceptance?: {
    local_demo_ready?: boolean
    production_ready?: boolean
    runtime_ready?: boolean
    release_status?: string
  }
  summary?: {
    blocker_count?: number
    warning_count?: number
    next_action_count?: number
  }
  next_actions?: ProductionPreflightAction[]
  decision_paths?: {
    local_demo?: ProductionPreflightDecisionPath
    production_release?: ProductionPreflightDecisionPath
  }
  evidence_sources?: {
    ready_endpoint?: string
    release_readiness_endpoint?: string
    local_dependency_report_path?: string
    public_api_leak_scan_report_path?: string
  }
  production_dry_run?: {
    status?: string
    mutates_active_settings?: boolean
    production_config_errors?: string[]
    runtime_dependency_blockers?: string[]
  }
  production_secrets_tls?: {
    status?: string
    summary?: {
      pass?: number
      warn?: number
      fail?: number
    }
  }
  public_api_leak_scan?: {
    status?: string
    fresh?: boolean
    freshness_status?: string
    age_seconds?: number | null
    max_age_seconds?: number
    leak_count?: number | null
    endpoint_count?: number
    summary?: {
      pass?: number
      warn?: number
      fail?: number
    }
  }
  local_dependencies?: {
    status?: string
  }
}

interface ApiReadiness {
  status?: 'ready' | 'degraded' | 'unhealthy' | string
  checks?: Record<string, HealthCheck>
  capabilities?: {
    document_parser?: DocumentParserCapabilities
  }
  deployment_profile?: DeploymentProfile
  timestamp?: string
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}

function asString(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function smokeTone(report?: MainFlowRuntimeSmokeReport): 'success' | 'warning' | 'danger' {
  if (!report?.available) return 'warning'
  if (report.status === 'passed' && report.fresh) return 'success'
  if (report.status === 'passed') return 'warning'
  return 'danger'
}

function smokeSubtitle(report?: MainFlowRuntimeSmokeReport): string {
  if (!report?.available) return '尚未生成主流程 smoke latest 证据'
  const status = report.status || 'unknown'
  const freshness = report.fresh ? 'fresh' : report.freshness_status || 'stale'
  const steps = typeof report.step_count === 'number' ? `${report.step_count} 步` : '步骤未知'
  return `${status} · ${freshness} · ${steps}`
}

function materializationTone(report?: ProductionEnvMaterializationReport): 'success' | 'warning' | 'danger' {
  if (!report?.available) return 'warning'
  if (report.status === 'pass' && (report.missing_required_key_count ?? 0) === 0) return 'success'
  if (report.status === 'fail') return 'danger'
  return 'warning'
}

function materializationTitle(report?: ProductionEnvMaterializationReport): string {
  if (!report?.available) return '生产配置物化未生成'
  const missingCount = report.missing_config_request_count ?? report.missing_required_key_count ?? 0
  if (report.status === 'pass' && missingCount === 0) return '生产配置物化可用'
  if (missingCount > 0) return `生产配置缺口 ${missingCount} 项`
  return '生产配置物化需确认'
}

function materializationSubtitle(report?: ProductionEnvMaterializationReport): string {
  if (!report?.available) return '先运行 dry-run 生成 production-env-materialization-latest.json'
  const freshness = report.fresh ? 'fresh' : report.freshness_status || 'unknown'
  const output = report.output_path || 'apps/backend/.env.prod'
  return `${report.status || 'unknown'} · ${freshness} · ${output}`
}

function objectStoragePostureLabel(value: string): string {
  const labels: Record<string, string> = {
    local_default_credentials: 'local-defaults',
    production_ready: 'production-ready',
    probe_failed: 'probe-failed',
    tls_policy_blocked: 'tls-blocked',
    not_configured: 'not-configured',
  }
  return labels[value] ?? value
}

function releaseSignalTags(item: ReleaseReadinessCheck): string[] {
  const signals = item.signals ?? {}
  const tags: string[] = []
  if (item.key === 'secrets') {
    const provenance = asRecord(signals.secret_provenance)
    const openrouter = asRecord(provenance?.openrouter_api_key)
    const redis = asRecord(provenance?.redis_url)
    const minioAccessKey = asRecord(provenance?.minio_access_key)
    const openrouterSource = asString(openrouter?.source)
    const redisSource = asString(redis?.source)
    const minioSource = asString(minioAccessKey?.source)
    if (openrouterSource) tags.push(`OpenRouter: ${openrouterSource}`)
    if (redisSource) tags.push(`Redis: ${redisSource}`)
    if (minioSource) tags.push(`MinIO: ${minioSource}`)
  }
  if (item.key === 'object_storage') {
    const probe = asRecord(signals.probe)
    const provider = asString(signals.provider) || asString(probe?.provider)
    const secure = typeof signals.secure === 'boolean' ? signals.secure : probe?.secure
    const tlsPolicyOk = signals.tls_policy_ok
    const probeOk = typeof signals.probe_ok === 'boolean' ? signals.probe_ok : probe?.ok
    const configurationPosture = asString(signals.configuration_posture)
    const operation = asString(probe?.operation)
    if (provider) tags.push(`对象存储: ${provider}`)
    if (typeof secure === 'boolean') tags.push(`TLS: ${secure ? 'enabled' : 'disabled'}`)
    if (typeof tlsPolicyOk === 'boolean') tags.push(`TLS 策略: ${tlsPolicyOk ? 'ok' : 'blocked'}`)
    if (typeof probeOk === 'boolean') tags.push(`探针: ${probeOk ? 'ok' : 'fail'}`)
    if (configurationPosture) tags.push(`配置: ${objectStoragePostureLabel(configurationPosture)}`)
    if (operation) tags.push(`读写探针: ${operation}`)
  }
  if (item.key === 'observability') {
    const checks = asRecord(signals.checks)
    const deploymentSources = asRecord(signals.deployment_sources)
    const logFormatSource = asRecord(deploymentSources?.log_format)
    const sentrySource = asRecord(deploymentSources?.sentry_dsn)
    const metricsEndpoint = asRecord(checks?.metrics_endpoint)
    const requestCorrelation = asRecord(checks?.request_correlation)
    const structuredLogs = asRecord(checks?.structured_logs)
    const errorTracking = asRecord(checks?.error_tracking)
    const missing = Array.isArray(signals.missing_for_production)
      ? signals.missing_for_production.map(item => String(item)).filter(Boolean)
      : []
    const metricsStatus = asString(metricsEndpoint?.status)
    const requestStatus = asString(requestCorrelation?.status)
    const logsStatus = asString(structuredLogs?.status)
    const errorsStatus = asString(errorTracking?.status)
    const logSource = asString(logFormatSource?.source) || asString(structuredLogs?.source)
    const errorSource = asString(sentrySource?.source) || asString(errorTracking?.source)
    if (metricsStatus) tags.push(`Metrics: ${metricsStatus}`)
    if (requestStatus) tags.push(`Request ID: ${requestStatus}`)
    if (logsStatus) tags.push(`JSON 日志: ${logsStatus}`)
    if (errorsStatus) tags.push(`错误上报: ${errorsStatus}`)
    if (logSource) tags.push(`LOG_FORMAT 来源: ${logSource}`)
    if (errorSource) tags.push(`Sentry 来源: ${errorSource}`)
    if (missing.length > 0) tags.push(`观测缺口: ${missing.join(', ')}`)
  }
  return tags
}

export default function SystemStatusPanel() {
  const healthQ = useQuery({ queryKey: ['sys-health'], queryFn: () => api.get<ApiHealth>('/api/v1/health').then(r => r.data), refetchInterval: 30000, retry: 1 })
  const readinessQ = useQuery({
    queryKey: ['sys-health-ready'],
    queryFn: () => api.get<ApiReadiness>('/api/v1/health/ready').then(r => r.data).catch(() => null),
    refetchInterval: 30000,
    retry: 1,
  })
  const releaseReadinessQ = useQuery({
    queryKey: ['sys-release-readiness'],
    queryFn: () => api.get<ReleaseReadinessReport>('/api/v1/health/release-readiness').then(r => r.data).catch(() => null),
    refetchInterval: 30000,
    retry: 1,
  })
  const productionPreflightQ = useQuery({
    queryKey: ['sys-production-preflight'],
    queryFn: () => api.get<ProductionPreflightReport>('/api/v1/health/production-preflight').then(r => r.data).catch(() => null),
    refetchInterval: 30000,
    retry: 1,
  })
  const traceConsistencyQ = useQuery({
    queryKey: ['sys-trace-consistency'],
    queryFn: () => api.get<TraceConsistency>('/api/v1/decision-traces/consistency', { params: { sample_limit: 5 } }).then(r => r.data).catch(() => null),
    refetchInterval: 60000,
    retry: 1,
  })
  const dsListQ = useQuery({ queryKey: ['sys-ds-list'], queryFn: () => api.get('/api/v1/datasources/list').then(r => r.data).catch(() => []), refetchInterval: 60000, retry: 1 })
  const costQ = useQuery({ queryKey: ['sys-cost'], queryFn: () => api.get('/api/v1/costs/overview').then(r => r.data).catch(() => null), refetchInterval: 60000, retry: 1 })
  const progressQ = useQuery({ queryKey: ['sys-prog'], queryFn: () => api.get('/api/v1/progress/active').then(r => r.data).catch(() => []), refetchInterval: 10000, retry: 1 })
  const remindersQ = useQuery({ queryKey: ['sys-rem'], queryFn: () => api.get('/api/v1/datasources/reminders').then(r => r.data).catch(() => []), refetchInterval: 60000, retry: 1 })
  const quotaQ = useQuery({ queryKey: ['sys-quota'], queryFn: () => api.get('/api/v1/datasources/quota').then(r => r.data).catch(() => []), refetchInterval: 60000, retry: 1 })
  const routingQ = useQuery({ queryKey: ['sys-routing'], queryFn: () => api.get('/api/v1/settings/model-routing').then(r => r.data).catch(() => []), refetchInterval: 60000, retry: 1 })
  const featuresQ = useQuery({ queryKey: ['sys-features'], queryFn: () => api.get('/api/v1/settings/features').then(r => r.data).catch(() => []), refetchInterval: 60000, retry: 1 })
  const dispatcherQ = useQuery({
    queryKey: ['sys-ingestion-dispatcher'],
    queryFn: () => api.get<IngestionDispatcherStatus>('/api/v1/ingestion/dispatcher/status').then(r => r.data).catch(() => null),
    refetchInterval: 10000,
    retry: 1,
  })

  const [costHistOpen, setCostHistOpen] = useState(false)
  const [costGranularity, setCostGranularity] = useState<CostGranularity>('daily')
  const [cancellingJobId, setCancellingJobId] = useState<string | null>(null)
  const [reapingStale, setReapingStale] = useState(false)
  const [reapResult, setReapResult] = useState<IngestionReapResult | null>(null)
  const costHistQ = useQuery({
    queryKey: ['sys-cost-hist', costGranularity],
    queryFn: () => api.get(`/api/v1/costs/history?granularity=${costGranularity}&limit=${costGranularity === 'daily' ? 90 : costGranularity === 'monthly' ? 24 : 10}`).then(r => r.data).catch(() => null),
    enabled: costHistOpen,
    retry: 1,
  })

  const backendOk = healthQ.data?.status === 'ok'
  const traceConsistency = traceConsistencyQ.data
  const traceConsistencyOk = traceConsistency?.status === 'ok'
  const baseUrl = api.defaults.baseURL || 'http://localhost:8000'
  const dispatcherStatus = dispatcherQ.data
  const dispatcherJobs = Array.isArray(dispatcherStatus?.persisted_active_jobs) ? dispatcherStatus.persisted_active_jobs : []
  const staleDispatcherJobs = dispatcherJobs.filter(job => job.lease_expired)
  const parserCapabilities = healthQ.data?.capabilities?.document_parser
  const parserFallback = parserCapabilities?.status === 'fallback'
  const parserFeatures = parserCapabilities?.features ?? {}
  const parserInputModes = parserCapabilities?.input_modes ?? []
  const readiness = readinessQ.data
  const readinessChecks = readiness?.checks ?? {}
  const coreReady = Boolean(readinessChecks.database?.ok && readinessChecks.openrouter?.ok)
  const optionalChecks = [readinessChecks.redis, readinessChecks.minio].filter(Boolean)
  const optionalDegraded = optionalChecks.some(check => check?.ok === false)
  const deploymentProfile = readiness?.deployment_profile
  const blockingChecks = deploymentProfile?.blocking_checks ?? []
  const releaseReadiness = releaseReadinessQ.data
  const releaseReadinessChecks = releaseReadiness?.checks ?? []
  const productionDigestView = buildProductionDigestView(releaseReadiness?.production_readiness_digest)
  const blockersWithVerificationCommands = productionDigestView.blockers
    .filter(blocker => blocker.verificationCommands.length > 0)
    .slice(0, 3)
  const productionRefreshView = buildProductionRefreshView(releaseReadiness?.production_readiness_refresh)
  const productionEnvMaterialization = releaseReadiness?.production_env_materialization
  const productionEnvMaterializationTone = materializationTone(productionEnvMaterialization)
  const productionEnvMaterializationRequests = productionEnvMaterialization?.missing_config_requests ?? []
  const productionEnvMaterializationCommands = productionEnvMaterialization?.operator_commands ?? []
  const mainFlowSmoke = releaseReadiness?.main_flow_runtime_smoke
  const mainFlowSmokeTone = smokeTone(mainFlowSmoke)
  const productionPreflight = productionPreflightQ.data
  const productionPreflightActions = productionPreflight?.next_actions ?? []
  const localDemoPath = productionPreflight?.decision_paths?.local_demo
  const productionReleasePath = productionPreflight?.decision_paths?.production_release
  const productionSecretsTls = productionPreflight?.production_secrets_tls
  const productionSecretsTlsFailCount = productionSecretsTls?.summary?.fail ?? 0
  const publicApiLeakScan = productionPreflight?.public_api_leak_scan
  const productionPreflightTone = productionPreflight?.status === 'pass'
    ? 'success'
    : productionPreflight?.status === 'fail'
      ? 'danger'
      : 'warn'
  const localDemoLabel = productionPreflight?.acceptance?.local_demo_ready ? '本地演示可用' : '本地演示需检查'
  const localDemoActionCount = localDemoPath?.required_action_count ?? (productionPreflight?.acceptance?.local_demo_ready ? 0 : productionPreflight?.summary?.blocker_count ?? 0)
  const productionReleaseStatus = productionReleasePath?.status
  const productionLabel = productionPreflight?.acceptance?.production_ready
    ? '生产可放行'
    : '生产未放行'
  const productionPathLabel = productionReleaseStatus === 'needs_action'
    ? '生产待处理'
    : productionReleaseStatus === 'blocked'
      ? '生产阻断'
      : productionPreflight?.acceptance?.production_ready
        ? '生产已就绪'
        : '生产待确认'
  const productionActionCount = productionReleasePath?.required_action_count ?? productionPreflight?.summary?.next_action_count ?? productionPreflightActions.length
  const releaseReadinessTone = releaseReadiness?.status === 'pass'
    ? 'success'
    : releaseReadiness?.status === 'fail'
      ? 'danger'
      : 'warn'
  const releaseReadinessTitle = releaseReadiness?.status === 'pass'
    ? '发布就绪通过'
    : releaseReadiness?.status === 'fail'
      ? '发布就绪阻断'
      : releaseReadinessQ.isLoading
        ? '发布就绪检查中'
        : '发布就绪需处理'
  const readinessTitle = readiness?.status === 'ready'
    ? '系统就绪'
    : coreReady && optionalDegraded
      ? '核心可用，弱依赖降级'
      : readiness?.status === 'unhealthy'
        ? '核心依赖异常'
        : readinessQ.isLoading
          ? '运行就绪检查中'
          : '运行状态需检查'
  const readinessTone = readiness?.status === 'ready'
    ? 'success'
    : coreReady
      ? 'warn'
      : 'danger'
  const readinessCheckRows = [
    { key: 'database', label: 'Database', check: readinessChecks.database, required: true },
    { key: 'openrouter', label: 'OpenRouter', check: readinessChecks.openrouter, required: true },
    { key: 'redis', label: 'Redis', check: readinessChecks.redis, required: false },
    { key: 'minio', label: 'MinIO', check: readinessChecks.minio, required: false },
  ]

  async function handleCancelJob(job: any) {
    const jobId = typeof job?.job_id === 'string' ? job.job_id : ''
    if (!jobId || job?.workflow !== 'ingestion') return
    setCancellingJobId(jobId)
    try {
      await api.post(`/api/v1/ingestion/jobs/${jobId}/cancel`, { reason: 'user_cancelled_from_system_status' })
      await progressQ.refetch()
    } finally {
      setCancellingJobId(null)
    }
  }

  async function handleReapStaleJobs() {
    setReapingStale(true)
    try {
      const response = await api.post<IngestionReapResult>('/api/v1/ingestion/dispatcher/reap-stale', null, {
        params: { limit: 50, resume: true },
      })
      setReapResult(response.data)
      await Promise.all([dispatcherQ.refetch(), progressQ.refetch()])
    } finally {
      setReapingStale(false)
    }
  }

  const DS_CATEGORIES: Record<string, { color: string; label: string }> = {
    market: { color: 'bg-sky-400', label: '行情' },
    macro: { color: 'bg-violet-400', label: '宏观' },
    event: { color: 'bg-amber-400', label: '事件' },
    flow: { color: 'bg-emerald-400', label: '资金/链上' },
    prediction: { color: 'bg-pink-400', label: '预测市场' },
    social: { color: 'bg-rose-400', label: '社交媒体' },
    internal: { color: 'bg-muted-foreground', label: '内部' },
  }

  const GRANULARITY_LABELS: Record<CostGranularity, string> = { daily: '按日', monthly: '按月', yearly: '按年' }

  return (
    <div className="space-y-4">
      {/* Connection */}
      <div className="rounded-2xl border border-border bg-card p-4">
        <div className="flex items-center gap-3">
          <div className={clsx('w-10 h-10 rounded-xl flex items-center justify-center', backendOk ? 'bg-emerald-400/10' : healthQ.isLoading ? 'bg-muted' : 'bg-red-400/10')}>
            {backendOk ? <Wifi size={18} className="text-emerald-400" /> : healthQ.isLoading ? <Loader2 size={18} className="text-muted-foreground animate-spin" /> : <WifiOff size={18} className="text-red-400" />}
          </div>
          <div className="flex-1">
            <p className="text-sm font-medium text-foreground">{backendOk ? '后端连接正常' : healthQ.isLoading ? '检测连接中...' : '后端连接失败'}</p>
            <p className="text-[11px] text-muted-foreground font-mono">{baseUrl}/api/v1/health</p>
          </div>
          <button onClick={() => { healthQ.refetch(); readinessQ.refetch(); releaseReadinessQ.refetch(); productionPreflightQ.refetch(); traceConsistencyQ.refetch(); costQ.refetch(); progressQ.refetch(); dispatcherQ.refetch(); dsListQ.refetch(); routingQ.refetch(); featuresQ.refetch() }}
            className="px-2 py-1 rounded bg-secondary text-xs hover:bg-border flex items-center gap-1">
            <RefreshCw size={10} /> 全部刷新
          </button>
        </div>
      </div>

      <div className="rounded-2xl border border-border bg-card p-4">
        <div className="flex items-start gap-3">
          <div className={clsx(
            'w-10 h-10 rounded-xl flex items-center justify-center shrink-0',
            productionPreflightTone === 'success' && 'bg-emerald-400/10',
            productionPreflightTone === 'warn' && 'bg-amber-400/10',
            productionPreflightTone === 'danger' && 'bg-red-400/10',
          )}>
            {productionPreflightQ.isLoading ? (
              <Loader2 size={18} className="text-muted-foreground animate-spin" />
            ) : productionPreflightTone === 'success' ? (
              <CheckCircle2 size={18} className="text-emerald-400" />
            ) : productionPreflightTone === 'danger' ? (
              <XCircle size={18} className="text-red-400" />
            ) : (
              <Shield size={18} className="text-amber-400" />
            )}
          </div>
          <div className="min-w-0 flex-1 space-y-3">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-sm font-medium text-foreground">生产预检</p>
                <p className="text-[11px] text-muted-foreground font-mono">/api/v1/health/production-preflight</p>
              </div>
              {productionPreflight?.status && (
                <span className={clsx(
                  'rounded-full px-2 py-1 text-[10px] shrink-0',
                  productionPreflightTone === 'success' && 'bg-emerald-400/10 text-emerald-400',
                  productionPreflightTone === 'warn' && 'bg-amber-400/10 text-amber-400',
                  productionPreflightTone === 'danger' && 'bg-red-400/10 text-red-400',
                )}>
                  {productionPreflight.status}
                </span>
              )}
            </div>

            <div className="grid grid-cols-2 gap-2 xl:grid-cols-4">
              <div className={clsx(
                'rounded-lg border p-2',
                productionPreflight?.acceptance?.local_demo_ready ? 'bg-emerald-400/5 border-emerald-400/30' : 'bg-amber-400/5 border-amber-400/30',
              )}>
                <p className="text-[10px] text-muted-foreground">本地演示</p>
                <p className={clsx('text-xs font-medium', productionPreflight?.acceptance?.local_demo_ready ? 'text-emerald-400' : 'text-amber-400')}>
                  {localDemoLabel}
                </p>
                <p className="mt-1 text-[10px] text-muted-foreground">演示处理 {localDemoActionCount}</p>
              </div>
              <div className={clsx(
                'rounded-lg border p-2',
                mainFlowSmokeTone === 'success' && 'bg-emerald-400/5 border-emerald-400/30',
                mainFlowSmokeTone === 'warning' && 'bg-amber-400/5 border-amber-400/30',
                mainFlowSmokeTone === 'danger' && 'bg-red-400/5 border-red-400/30',
              )}>
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="text-[10px] text-muted-foreground">主流程 Smoke</p>
                    <p className={clsx(
                      'truncate text-xs font-medium',
                      mainFlowSmokeTone === 'success' && 'text-emerald-400',
                      mainFlowSmokeTone === 'warning' && 'text-amber-400',
                      mainFlowSmokeTone === 'danger' && 'text-red-400',
                    )}>
                      {mainFlowSmoke?.available ? mainFlowSmoke.status || 'unknown' : '未生成'}
                    </p>
                  </div>
                  {mainFlowSmoke?.fresh && <CheckCircle2 size={14} className="mt-0.5 shrink-0 text-emerald-400" />}
                </div>
                <p className="mt-1 line-clamp-1 text-[10px] text-muted-foreground" title={smokeSubtitle(mainFlowSmoke)}>
                  {smokeSubtitle(mainFlowSmoke)}
                </p>
                {mainFlowSmoke?.operator_command && (
                  <p className="mt-1 line-clamp-1 font-mono text-[10px] text-muted-foreground" title={mainFlowSmoke.operator_command}>
                    {mainFlowSmoke.operator_command}
                  </p>
                )}
              </div>
              <div className={clsx(
                'rounded-lg border p-2',
                productionPreflight?.acceptance?.production_ready ? 'bg-emerald-400/5 border-emerald-400/30' : 'bg-amber-400/5 border-amber-400/30',
              )}>
                <p className="text-[10px] text-muted-foreground">生产放行</p>
                <p className={clsx('text-xs font-medium', productionPreflight?.acceptance?.production_ready ? 'text-emerald-400' : 'text-amber-400')}>
                  {productionLabel}
                </p>
                <p className="mt-1 text-[10px] text-muted-foreground">{productionPathLabel}</p>
              </div>
              <div className="rounded-lg bg-background border border-border p-2">
                <p className="text-[10px] text-muted-foreground">生产处理</p>
                <p className="text-xs font-medium text-amber-400">生产处理 {productionActionCount}</p>
              </div>
            </div>

            <div className={clsx(
              'rounded-lg border p-3',
              productionDigestView.tone === 'success' && 'border-emerald-400/30 bg-emerald-400/5',
              productionDigestView.tone === 'warning' && 'border-amber-400/30 bg-amber-400/5',
              productionDigestView.tone === 'danger' && 'border-red-400/30 bg-red-400/5',
            )}>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className={clsx(
                    'text-xs font-medium',
                    productionDigestView.tone === 'success' && 'text-emerald-400',
                    productionDigestView.tone === 'warning' && 'text-amber-400',
                    productionDigestView.tone === 'danger' && 'text-red-400',
                  )}>
                    {productionDigestView.title}
                  </p>
                  <p className="mt-1 text-[10px] text-muted-foreground">{productionDigestView.subtitle}</p>
                </div>
                {releaseReadiness?.evidence_sources?.production_readiness_digest_path && (
                  <span className="shrink-0 rounded-full border border-border bg-background px-2 py-0.5 text-[10px] text-muted-foreground">
                    digest
                  </span>
                )}
              </div>
              {productionDigestView.blockers.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {productionDigestView.blockers.map(blocker => (
                    <span
                      key={blocker.key}
                      className="inline-flex max-w-full items-center gap-1 rounded-full border border-border bg-background px-2 py-0.5 text-[10px] text-muted-foreground"
                      title={[
                        blocker.sectionLabel,
                        blocker.configKeys.length > 0 ? `Config: ${blocker.configKeys.join(', ')}` : '',
                        blocker.verificationCommands[0] ? `Verify: ${blocker.verificationCommands[0]}` : '',
                      ].filter(Boolean).join(' · ')}
                    >
                      <span className="truncate">{blocker.label}</span>
                      {blocker.configKeys.length > 0 && (
                        <span className="max-w-[160px] truncate font-mono text-[9px] text-muted-foreground/70">
                          {blocker.configKeys.slice(0, 3).join(', ')}
                        </span>
                      )}
                    </span>
                  ))}
                  {productionDigestView.moreCount > 0 && (
                    <span className="rounded-full border border-border bg-background px-2 py-0.5 text-[10px] text-muted-foreground">
                      +{productionDigestView.moreCount}
                    </span>
                  )}
                </div>
              )}
              {blockersWithVerificationCommands.length > 0 && (
                <div className="mt-2 space-y-1">
                  <p className="text-[10px] text-muted-foreground">阻断项验证</p>
                  {blockersWithVerificationCommands.map(blocker => (
                    <div
                      key={`${blocker.key}-verification`}
                      className="border-t border-border/60 pt-1 first:border-t-0 first:pt-0"
                    >
                      <p className="truncate text-[10px] font-medium text-foreground" title={blocker.label}>
                        {blocker.label}
                      </p>
                      <p className="mt-0.5 break-all font-mono text-[10px] text-muted-foreground">
                        {blocker.verificationCommands[0]}
                      </p>
                    </div>
                  ))}
                </div>
              )}
              {productionDigestView.candidateEnvMismatches.length > 0 && (
                <div className="mt-2 space-y-1">
                  <p className="text-[10px] text-muted-foreground">候选配置不一致</p>
                  {productionDigestView.candidateEnvMismatches.map(issue => (
                    <div
                      key={issue.key}
                      className="border-t border-border/60 pt-1 first:border-t-0 first:pt-0"
                    >
                      <p className="truncate text-[10px] font-medium text-foreground" title={issue.reportLabel}>
                        {issue.reportLabel}
                      </p>
                      <p className="mt-0.5 break-all text-[10px] text-muted-foreground">
                        {issue.actualEnvFile} → {issue.expectedEnvFile}
                      </p>
                      {issue.operatorCommand && (
                        <p className="mt-0.5 break-all font-mono text-[10px] text-muted-foreground">
                          {issue.operatorCommand}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              )}
              {productionDigestView.upstreamReportIssues.length > 0 && (
                <div className="mt-2 space-y-1">
                  <p className="text-[10px] text-muted-foreground">先补上游报告</p>
                  {productionDigestView.upstreamReportIssues.map(issue => (
                    <div
                      key={`${issue.key}-${issue.status}`}
                      className="border-t border-border/60 pt-1 first:border-t-0 first:pt-0"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <p className="truncate text-[10px] font-medium text-foreground" title={issue.label}>
                          {issue.label}
                        </p>
                        <span className="shrink-0 rounded-full border border-border bg-background px-1.5 py-0.5 text-[10px] text-muted-foreground">
                          {issue.statusLabel}
                        </span>
                      </div>
                      {issue.operatorCommand && (
                        <p className="mt-0.5 break-all font-mono text-[10px] text-muted-foreground">
                          {issue.operatorCommand}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              )}
              {productionDigestView.operatorCommand && (
                <div className="mt-2 rounded-md border border-border bg-background px-2 py-1">
                  <p className="text-[10px] text-muted-foreground">重新生成</p>
                  <p className="mt-0.5 break-all font-mono text-[10px] text-muted-foreground">
                    {productionDigestView.operatorCommand}
                  </p>
                </div>
              )}
            </div>

            <div className={clsx(
              'rounded-lg border p-3',
              productionEnvMaterializationTone === 'success' && 'border-emerald-400/30 bg-emerald-400/5',
              productionEnvMaterializationTone === 'warning' && 'border-amber-400/30 bg-amber-400/5',
              productionEnvMaterializationTone === 'danger' && 'border-red-400/30 bg-red-400/5',
            )}>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className={clsx(
                    'text-xs font-medium',
                    productionEnvMaterializationTone === 'success' && 'text-emerald-400',
                    productionEnvMaterializationTone === 'warning' && 'text-amber-400',
                    productionEnvMaterializationTone === 'danger' && 'text-red-400',
                  )}>
                    {materializationTitle(productionEnvMaterialization)}
                  </p>
                  <p className="mt-1 text-[10px] text-muted-foreground">
                    {materializationSubtitle(productionEnvMaterialization)}
                  </p>
                </div>
                {releaseReadiness?.evidence_sources?.production_env_materialization_path && (
                  <span className="shrink-0 rounded-full border border-border bg-background px-2 py-0.5 text-[10px] text-muted-foreground">
                    env
                  </span>
                )}
              </div>

              {productionEnvMaterializationRequests.length > 0 && (
                <div className="mt-2 space-y-1">
                  <p className="text-[10px] text-muted-foreground">待补生产配置</p>
                  {productionEnvMaterializationRequests.slice(0, 5).map(item => (
                    <div
                      key={item.config_key}
                      className="rounded-md border border-border bg-background px-2 py-1"
                    >
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="font-mono text-[10px] text-foreground">{item.config_key}</span>
                        {item.suggested_sops_key && (
                          <span className="rounded-full border border-border px-1.5 py-0.5 font-mono text-[9px] text-muted-foreground">
                            sops: {item.suggested_sops_key}
                          </span>
                        )}
                        {item.source_marker_key && (
                          <span className="rounded-full border border-border px-1.5 py-0.5 font-mono text-[9px] text-muted-foreground">
                            marker: {item.source_marker_key}
                          </span>
                        )}
                      </div>
                      {item.can_be_derived_from?.length ? (
                        <p className="mt-1 text-[10px] text-muted-foreground">
                          可由 {item.can_be_derived_from.join(', ')} 派生
                        </p>
                      ) : null}
                    </div>
                  ))}
                  {productionEnvMaterializationRequests.length > 5 && (
                    <p className="text-[10px] text-muted-foreground">
                      另有 {productionEnvMaterializationRequests.length - 5} 项，请查看报告文件。
                    </p>
                  )}
                </div>
              )}

              {productionEnvMaterializationCommands.length > 0 && (
                <div className="mt-2 rounded-md border border-border bg-background px-2 py-1">
                  <p className="text-[10px] text-muted-foreground">建议执行顺序</p>
                  {productionEnvMaterializationCommands.map(command => (
                    <p key={command} className="mt-0.5 break-all font-mono text-[10px] text-muted-foreground">
                      {command}
                    </p>
                  ))}
                </div>
              )}
            </div>

            <div className={clsx(
              'rounded-lg border p-3',
              productionRefreshView.tone === 'success' && 'border-emerald-400/30 bg-emerald-400/5',
              productionRefreshView.tone === 'warning' && 'border-amber-400/30 bg-amber-400/5',
              productionRefreshView.tone === 'danger' && 'border-red-400/30 bg-red-400/5',
            )}>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className={clsx(
                    'text-xs font-medium',
                    productionRefreshView.tone === 'success' && 'text-emerald-400',
                    productionRefreshView.tone === 'warning' && 'text-amber-400',
                    productionRefreshView.tone === 'danger' && 'text-red-400',
                  )}>
                    {productionRefreshView.title}
                  </p>
                  <p className="mt-1 text-[10px] text-muted-foreground">{productionRefreshView.subtitle}</p>
                </div>
                {releaseReadiness?.evidence_sources?.production_readiness_refresh_path && (
                  <span className="shrink-0 rounded-full border border-border bg-background px-2 py-0.5 text-[10px] text-muted-foreground">
                    refresh
                  </span>
                )}
              </div>
              {(productionRefreshView.envFile || productionRefreshView.actualEnvFile || productionRefreshView.expectedEnvFile) && (
                <div className="mt-2 rounded-md border border-border bg-background px-2 py-1">
                  <p className="text-[10px] text-muted-foreground">候选配置</p>
                  {productionRefreshView.actualEnvFile && productionRefreshView.expectedEnvFile ? (
                    <p className="mt-0.5 break-all text-[10px] text-muted-foreground">
                      {productionRefreshView.actualEnvFile} → {productionRefreshView.expectedEnvFile}
                    </p>
                  ) : (
                    <p className="mt-0.5 break-all text-[10px] text-muted-foreground">
                      {productionRefreshView.envFile}
                    </p>
                  )}
                </div>
              )}
              {productionRefreshView.blockingSteps.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {productionRefreshView.blockingSteps.map(step => (
                    <span
                      key={step}
                      className="rounded-full border border-border bg-background px-2 py-0.5 text-[10px] text-muted-foreground"
                    >
                      {step}
                    </span>
                  ))}
                </div>
              )}
              {productionRefreshView.steps.length > 0 && (
                <div className="mt-2 space-y-1">
                  {productionRefreshView.steps.map(step => {
                    const statusTone = step.status === 'pass'
                      ? 'text-emerald-400'
                      : step.status === 'blocked' || step.status === 'fail'
                        ? 'text-red-400'
                        : 'text-amber-400'
                    const meta = [
                      step.status,
                      step.exitCode !== null ? `exit ${step.exitCode}` : '',
                      step.blockingSteps.length > 0 ? step.blockingSteps.join(', ') : '',
                    ].filter(Boolean).join(' · ')
                    return (
                      <div key={step.key} className="rounded-md border border-border bg-background px-2 py-1">
                        <div className="flex items-center justify-between gap-2">
                          <p className="truncate text-[10px] font-medium text-foreground">{step.label || step.key}</p>
                          {meta && <span className={clsx('shrink-0 text-[10px]', statusTone)}>{meta}</span>}
                        </div>
                        {step.latestPath && (
                          <p className="mt-0.5 break-all font-mono text-[10px] text-muted-foreground">
                            {step.latestPath}
                          </p>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
              {productionRefreshView.recommendedActionGroups.length > 0 ? (
                <div className="mt-2 space-y-1">
                  {productionRefreshView.recommendedActionGroups.map(group => {
                    const meta = [
                      group.status,
                      `${group.actionCount} 项`,
                      group.sourceCount > 1 ? `${group.sourceCount} 处` : '',
                    ].filter(Boolean).join(' · ')
                    const primaryAction = group.actions[0]
                    const verifyCommand = primaryAction?.verificationCommands[0]
                    return (
                      <div key={group.key} className="rounded-md border border-border bg-background px-2 py-1">
                        <div className="flex items-center justify-between gap-2">
                          <p className="truncate text-[10px] font-medium text-foreground">{group.label || group.key}</p>
                          {meta && <span className="shrink-0 text-[10px] text-muted-foreground">{meta}</span>}
                        </div>
                        {group.actions.slice(0, 2).map(action => (
                          <p key={`${action.key}-${action.action}`} className="mt-0.5 line-clamp-2 text-[10px] text-muted-foreground">
                            {action.label || action.key}：{action.action}
                          </p>
                        ))}
                        {group.hasMoreActions && (
                          <p className="mt-0.5 text-[10px] text-muted-foreground">另有 {group.actionCount - group.actions.length} 项待处理</p>
                        )}
                        {verifyCommand && (
                          <p className="mt-0.5 break-all font-mono text-[10px] text-muted-foreground">
                            {verifyCommand}
                          </p>
                        )}
                      </div>
                    )
                  })}
                </div>
              ) : productionRefreshView.recommendedActions.length > 0 && (
                <div className="mt-2 space-y-1">
                  {productionRefreshView.recommendedActions.map(action => {
                    const verifyCommand = action.verificationCommands[0]
                    const meta = [
                      action.owner,
                      action.requiresSecretUpdate ? '密钥' : '',
                      action.sourceCount > 1 ? `${action.sourceCount} 处` : '',
                    ].filter(Boolean).join(' · ')
                    return (
                      <div key={`${action.key}-${action.action}`} className="rounded-md border border-border bg-background px-2 py-1">
                        <div className="flex items-center justify-between gap-2">
                          <p className="truncate text-[10px] font-medium text-foreground">{action.label || action.key}</p>
                          {meta && <span className="shrink-0 text-[10px] text-muted-foreground">{meta}</span>}
                        </div>
                        <p className="mt-0.5 line-clamp-2 text-[10px] text-muted-foreground">{action.action}</p>
                        {verifyCommand && (
                          <p className="mt-0.5 break-all font-mono text-[10px] text-muted-foreground">
                            {verifyCommand}
                          </p>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
              {productionRefreshView.operatorCommand && (
                <div className="mt-2 rounded-md border border-border bg-background px-2 py-1">
                  <p className="text-[10px] text-muted-foreground">刷新证据</p>
                  <p className="mt-0.5 break-all font-mono text-[10px] text-muted-foreground">
                    {productionRefreshView.operatorCommand}
                  </p>
                </div>
              )}
            </div>

            {productionPreflightActions.length > 0 && (
              <div className="space-y-1">
                {productionPreflightActions.slice(0, 2).map(action => {
                  const verificationTarget = action.verification?.[0]?.target
                  const actionMeta = [
                    action.owner,
                    action.can_auto_fix ? '可自动' : '需人工',
                    verificationTarget ? `验收 ${verificationTarget}` : '',
                  ].filter(Boolean).join(' · ')
                  const configKeys = Array.isArray(action.config_keys) ? action.config_keys.join(', ') : ''
                  return (
                    <div key={`${action.key}-${action.action}`} className="space-y-0.5">
                      <p
                        className="text-[10px] text-amber-400 line-clamp-2"
                        title={action.action}
                        aria-label={`${action.label || action.key}：${action.action}`}
                      >
                        {action.label || action.key}：{action.action}
                      </p>
                      {actionMeta && (
                        <p className="text-[10px] text-muted-foreground line-clamp-1" title={actionMeta}>
                          {actionMeta}
                        </p>
                      )}
                      {configKeys && (
                        <p className="text-[10px] text-muted-foreground line-clamp-1" title={configKeys}>
                          {configKeys}
                        </p>
                      )}
                    </div>
                  )
                })}
              </div>
            )}

            {productionPreflight?.evidence_sources && (
              <div className="flex flex-wrap gap-1">
                {productionPreflight.evidence_sources.ready_endpoint && (
                  <span className="rounded-full border border-border bg-background px-2 py-0.5 text-[10px] text-muted-foreground">
                    ready: {productionPreflight.evidence_sources.ready_endpoint}
                  </span>
                )}
                {productionPreflight.evidence_sources.release_readiness_endpoint && (
                  <span className="rounded-full border border-border bg-background px-2 py-0.5 text-[10px] text-muted-foreground">
                    release: {productionPreflight.evidence_sources.release_readiness_endpoint}
                  </span>
                )}
                {productionPreflight.local_dependencies?.status && (
                  <span className="rounded-full border border-border bg-background px-2 py-0.5 text-[10px] text-muted-foreground">
                    deps: {productionPreflight.local_dependencies.status}
                  </span>
                )}
                {productionPreflight.production_dry_run?.status && (
                  <span className="rounded-full border border-border bg-background px-2 py-0.5 text-[10px] text-muted-foreground">
                    生产干跑: {productionPreflight.production_dry_run.status}
                  </span>
                )}
                {productionSecretsTls?.status && (
                  <span className="rounded-full border border-border bg-background px-2 py-0.5 text-[10px] text-muted-foreground">
                    密钥/TLS: {productionSecretsTls.status}
                  </span>
                )}
                {productionSecretsTlsFailCount > 0 && (
                  <span className="rounded-full border border-red-400/30 bg-red-400/5 px-2 py-0.5 text-[10px] text-red-400">
                    密钥/TLS 阻断 {productionSecretsTlsFailCount}
                  </span>
                )}
                {publicApiLeakScan?.status && (
                  <span className="rounded-full border border-border bg-background px-2 py-0.5 text-[10px] text-muted-foreground">
                    公开 API: {publicApiLeakScan.status}
                  </span>
                )}
                {publicApiLeakScan?.freshness_status && (
                  <span className={clsx(
                    'rounded-full border px-2 py-0.5 text-[10px]',
                    publicApiLeakScan.fresh === false
                      ? 'border-amber-400/30 bg-amber-400/5 text-amber-400'
                      : 'border-border bg-background text-muted-foreground',
                  )}>
                    扫描时效: {publicApiLeakScan.freshness_status}
                  </span>
                )}
                {typeof publicApiLeakScan?.leak_count === 'number' && (
                  <span className={clsx(
                    'rounded-full border px-2 py-0.5 text-[10px]',
                    publicApiLeakScan.leak_count > 0
                      ? 'border-red-400/30 bg-red-400/5 text-red-400'
                      : 'border-emerald-400/30 bg-emerald-400/5 text-emerald-400',
                  )}>
                    泄露 {publicApiLeakScan.leak_count}
                  </span>
                )}
                {productionPreflight.production_dry_run?.mutates_active_settings === false && (
                  <span className="rounded-full border border-border bg-background px-2 py-0.5 text-[10px] text-muted-foreground">
                    不改当前配置
                  </span>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="rounded-2xl border border-border bg-card p-4">
        <div className="flex items-start gap-3">
          <div className={clsx(
            'w-10 h-10 rounded-xl flex items-center justify-center shrink-0',
            readinessTone === 'success' && 'bg-emerald-400/10',
            readinessTone === 'warn' && 'bg-amber-400/10',
            readinessTone === 'danger' && 'bg-red-400/10',
          )}>
            {readinessQ.isLoading ? (
              <Loader2 size={18} className="text-muted-foreground animate-spin" />
            ) : readinessTone === 'success' ? (
              <CheckCircle2 size={18} className="text-emerald-400" />
            ) : readinessTone === 'warn' ? (
              <AlertCircle size={18} className="text-amber-400" />
            ) : (
              <WifiOff size={18} className="text-red-400" />
            )}
          </div>
          <div className="min-w-0 flex-1 space-y-3">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-sm font-medium text-foreground">{readinessTitle}</p>
                <p className="text-[11px] text-muted-foreground font-mono">/api/v1/health/ready</p>
                {deploymentProfile?.label && (
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    部署 Profile：{deploymentProfile.label}
                    {deploymentProfile.required_checks?.length ? ` · 强依赖 ${deploymentProfile.required_checks.length}` : ''}
                    {blockingChecks.length ? ` · 阻断 ${blockingChecks.join(', ')}` : ''}
                  </p>
                )}
              </div>
              {readiness?.status && (
                <span className={clsx(
                  'rounded-full px-2 py-1 text-[10px] shrink-0',
                  readinessTone === 'success' && 'bg-emerald-400/10 text-emerald-400',
                  readinessTone === 'warn' && 'bg-amber-400/10 text-amber-400',
                  readinessTone === 'danger' && 'bg-red-400/10 text-red-400',
                )}>
                  {readiness.status}
                </span>
              )}
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
              {readinessCheckRows.map(({ key, label, check, required }) => (
                <div key={key} className={clsx(
                  'rounded-lg border p-2',
                  check?.ok ? 'bg-background border-border' : required ? 'bg-red-400/5 border-red-400/30' : 'bg-amber-400/5 border-amber-400/30',
                )}>
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-[10px] text-muted-foreground">{label}</p>
                    <span className={clsx('h-1.5 w-1.5 rounded-full', check?.ok ? 'bg-emerald-400' : required ? 'bg-red-400' : 'bg-amber-400')} />
                  </div>
                  <p className={clsx('mt-1 text-xs font-medium', check?.ok ? 'text-emerald-400' : required ? 'text-red-400' : 'text-amber-400')}>
                    {check?.ok ? '可用' : required ? '异常' : '降级'}
                  </p>
                  {typeof check?.latency_ms === 'number' && <p className="text-[10px] text-muted-foreground">{Math.round(check.latency_ms)}ms</p>}
                  {!check?.ok && check?.error && <p className="text-[10px] text-muted-foreground truncate">{check.error}</p>}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="rounded-2xl border border-border bg-card p-4">
        <div className="flex items-start gap-3">
          <div className={clsx(
            'w-10 h-10 rounded-xl flex items-center justify-center shrink-0',
            releaseReadinessTone === 'success' && 'bg-emerald-400/10',
            releaseReadinessTone === 'warn' && 'bg-amber-400/10',
            releaseReadinessTone === 'danger' && 'bg-red-400/10',
          )}>
            {releaseReadinessQ.isLoading ? (
              <Loader2 size={18} className="text-muted-foreground animate-spin" />
            ) : releaseReadinessTone === 'success' ? (
              <CheckCircle2 size={18} className="text-emerald-400" />
            ) : releaseReadinessTone === 'danger' ? (
              <XCircle size={18} className="text-red-400" />
            ) : (
              <AlertCircle size={18} className="text-amber-400" />
            )}
          </div>
          <div className="min-w-0 flex-1 space-y-3">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-sm font-medium text-foreground">{releaseReadinessTitle}</p>
                <p className="text-[11px] text-muted-foreground font-mono">/api/v1/health/release-readiness</p>
              </div>
              {releaseReadiness?.status && (
                <span className={clsx(
                  'rounded-full px-2 py-1 text-[10px] shrink-0',
                  releaseReadinessTone === 'success' && 'bg-emerald-400/10 text-emerald-400',
                  releaseReadinessTone === 'warn' && 'bg-amber-400/10 text-amber-400',
                  releaseReadinessTone === 'danger' && 'bg-red-400/10 text-red-400',
                )}>
                  {releaseReadiness.status}
                </span>
              )}
            </div>

            {releaseReadiness?.summary && (
              <div className="grid grid-cols-3 gap-2">
                <div className="rounded-lg bg-background border border-border p-2">
                  <p className="text-[10px] text-muted-foreground">通过</p>
                  <p className="text-sm font-mono text-emerald-400">{releaseReadiness.summary.pass ?? 0}</p>
                </div>
                <div className="rounded-lg bg-amber-400/5 border border-amber-400/30 p-2">
                  <p className="text-[10px] text-muted-foreground">需处理</p>
                  <p className="text-sm font-mono text-amber-400">{releaseReadiness.summary.warn ?? 0}</p>
                </div>
                <div className="rounded-lg bg-red-400/5 border border-red-400/30 p-2">
                  <p className="text-[10px] text-muted-foreground">阻断</p>
                  <p className="text-sm font-mono text-red-400">{releaseReadiness.summary.fail ?? 0}</p>
                </div>
              </div>
            )}

            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
              {releaseReadinessChecks.map((item) => {
                const itemTone = item.status === 'pass' ? 'success' : item.status === 'fail' ? 'danger' : 'warn'
                const signalTags = releaseSignalTags(item)
                return (
                  <div key={item.key || item.label} className={clsx(
                    'rounded-lg border p-2',
                    itemTone === 'success' && 'bg-background border-border',
                    itemTone === 'warn' && 'bg-amber-400/5 border-amber-400/30',
                    itemTone === 'danger' && 'bg-red-400/5 border-red-400/30',
                  )}>
                    <div className="flex items-center justify-between gap-2">
                      <p className="text-xs font-medium text-foreground">{item.label || item.key}</p>
                      <span className={clsx(
                        'h-1.5 w-1.5 rounded-full',
                        itemTone === 'success' && 'bg-emerald-400',
                        itemTone === 'warn' && 'bg-amber-400',
                        itemTone === 'danger' && 'bg-red-400',
                      )} />
                    </div>
                    {item.detail && <p className="mt-1 text-[10px] text-muted-foreground line-clamp-2">{item.detail}</p>}
                    {item.remediation && (
                      <p
                        className="mt-1 text-[10px] text-amber-400 line-clamp-2"
                        title={item.remediation}
                        aria-label={`下一步：${item.remediation}`}
                      >
                        下一步：{item.remediation}
                      </p>
                    )}
                    {signalTags.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1">
                        {signalTags.map(tag => (
                          <span key={tag} className="rounded-full border border-border bg-background px-2 py-0.5 text-[10px] text-muted-foreground">
                            {tag}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      </div>

      {parserCapabilities && (
        <div className="rounded-2xl border border-border bg-card p-4">
          <div className="flex items-start gap-3">
            <div className={clsx(
              'w-10 h-10 rounded-xl flex items-center justify-center shrink-0',
              parserFallback ? 'bg-amber-400/10' : 'bg-emerald-400/10',
            )}>
              {parserFallback ? <AlertCircle size={18} className="text-amber-400" /> : <CheckCircle2 size={18} className="text-emerald-400" />}
            </div>
            <div className="min-w-0 flex-1 space-y-3">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-sm font-medium text-foreground">
                    {parserFallback ? '资料解析能力降级' : '资料解析能力正常'}
                  </p>
                  <p className="text-[11px] text-muted-foreground font-mono">
                    adapter {parserCapabilities.requested_adapter || 'current'} → {parserCapabilities.active_adapter || 'current'}
                  </p>
                </div>
                <span className={clsx(
                  'rounded-full px-2 py-1 text-[10px] shrink-0',
                  parserFallback ? 'bg-amber-400/10 text-amber-400' : 'bg-emerald-400/10 text-emerald-400',
                )}>
                  {parserCapabilities.status || 'ready'}
                </span>
              </div>

              <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                <div className="rounded-lg bg-background border border-border p-2">
                  <p className="text-[10px] text-muted-foreground">解析摘要</p>
                  <p className={clsx('text-xs font-medium', parserFeatures.document_parse_summary ? 'text-emerald-400' : 'text-muted-foreground')}>
                    {parserFeatures.document_parse_summary ? '已启用' : '未启用'}
                  </p>
                </div>
                <div className="rounded-lg bg-background border border-border p-2">
                  <p className="text-[10px] text-muted-foreground">进度回调</p>
                  <p className={clsx('text-xs font-medium', parserFeatures.progress_callback ? 'text-emerald-400' : 'text-muted-foreground')}>
                    {parserFeatures.progress_callback ? '已启用' : '未启用'}
                  </p>
                </div>
                <div className="rounded-lg bg-background border border-border p-2">
                  <p className="text-[10px] text-muted-foreground">表格结构</p>
                  <p className={clsx('text-xs font-medium', parserFeatures.table_structure ? 'text-emerald-400' : 'text-amber-400')}>
                    {parserFeatures.table_structure ? '已启用' : '未启用'}
                  </p>
                </div>
                <div className="rounded-lg bg-background border border-border p-2">
                  <p className="text-[10px] text-muted-foreground">OCR 置信度</p>
                  <p className={clsx('text-xs font-medium', parserFeatures.ocr_confidence ? 'text-emerald-400' : 'text-amber-400')}>
                    {parserFeatures.ocr_confidence ? '已启用' : '未启用'}
                  </p>
                </div>
              </div>

              {parserInputModes.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {parserInputModes.map((mode) => (
                    <span
                      key={mode.kind}
                      className={clsx(
                        'rounded-full border px-2 py-1 text-[10px]',
                        mode.status === 'supported' ? 'border-emerald-400/30 bg-emerald-400/5 text-emerald-400' : 'border-amber-400/30 bg-amber-400/5 text-amber-400',
                      )}
                    >
                      {mode.kind} · {mode.status}
                    </span>
                  ))}
                </div>
              )}

              {parserCapabilities.notes?.[0] && (
                <p className="text-[11px] text-muted-foreground">{parserCapabilities.notes[0]}</p>
              )}
            </div>
          </div>
        </div>
      )}

      <div className="rounded-2xl border border-border bg-card p-4">
        <div className="flex items-start gap-3">
          <div className={clsx(
            'w-10 h-10 rounded-xl flex items-center justify-center shrink-0',
            traceConsistencyOk ? 'bg-emerald-400/10' : traceConsistencyQ.isLoading ? 'bg-muted' : 'bg-amber-400/10',
          )}>
            {traceConsistencyOk ? (
              <Shield size={18} className="text-emerald-400" />
            ) : traceConsistencyQ.isLoading ? (
              <Loader2 size={18} className="text-muted-foreground animate-spin" />
            ) : (
              <AlertCircle size={18} className="text-amber-400" />
            )}
          </div>
          <div className="min-w-0 flex-1 space-y-3">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-sm font-medium text-foreground">
                  {traceConsistencyOk ? '审议来源链完整' : traceConsistencyQ.isLoading ? '检查审议来源链...' : '审议来源链需检查'}
                </p>
                <p className="text-[11px] text-muted-foreground font-mono">/api/v1/decision-traces/consistency</p>
              </div>
              <button
                onClick={() => traceConsistencyQ.refetch()}
                className="px-2 py-1 rounded bg-secondary text-xs hover:bg-border flex items-center gap-1 shrink-0"
              >
                <RefreshCw size={10} /> 刷新
              </button>
            </div>

            {traceConsistency ? (
              <>
                <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
                  <div className="rounded-lg bg-background border border-border p-2">
                    <p className="text-[10px] text-muted-foreground">审议事件</p>
                    <p className="text-sm font-mono text-foreground">{traceConsistency.trace_event_count}</p>
                  </div>
                  <div className="rounded-lg bg-background border border-border p-2">
                    <p className="text-[10px] text-muted-foreground">来源对</p>
                    <p className="text-sm font-mono text-foreground">{traceConsistency.event_source_pair_count}</p>
                  </div>
                  <div className="rounded-lg bg-background border border-border p-2">
                    <p className="text-[10px] text-muted-foreground">关联行</p>
                    <p className="text-sm font-mono text-foreground">{traceConsistency.link_count}</p>
                  </div>
                  <div className={clsx('rounded-lg border p-2', traceConsistency.missing_link_count ? 'bg-amber-400/5 border-amber-400/30' : 'bg-background border-border')}>
                    <p className="text-[10px] text-muted-foreground">缺失</p>
                    <p className={clsx('text-sm font-mono', traceConsistency.missing_link_count ? 'text-amber-400' : 'text-foreground')}>{traceConsistency.missing_link_count}</p>
                  </div>
                  <div className={clsx('rounded-lg border p-2', traceConsistency.orphan_link_count ? 'bg-amber-400/5 border-amber-400/30' : 'bg-background border-border')}>
                    <p className="text-[10px] text-muted-foreground">孤儿</p>
                    <p className={clsx('text-sm font-mono', traceConsistency.orphan_link_count ? 'text-amber-400' : 'text-foreground')}>{traceConsistency.orphan_link_count}</p>
                  </div>
                </div>
                {(traceConsistency.sample_missing_links.length > 0 || traceConsistency.sample_orphan_links.length > 0) && (
                  <div className="rounded-lg border border-amber-400/30 bg-amber-400/5 p-2 space-y-1">
                    {[...traceConsistency.sample_missing_links, ...traceConsistency.sample_orphan_links].slice(0, 5).map((item, index) => (
                      <p key={`${item.trace_event_id}-${item.source_id}-${index}`} className="text-[10px] text-amber-300 font-mono truncate">
                        {item.trace_event_id} · {item.source_id}
                      </p>
                    ))}
                  </div>
                )}
              </>
            ) : (
              <p className="text-xs text-muted-foreground">{traceConsistencyQ.isError ? '加载失败' : '等待检查结果'}</p>
            )}
          </div>
        </div>
      </div>

      <TraceEvalReviewPanel />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Cost Overview */}
        <div className="rounded-2xl border border-border bg-card p-4 space-y-3">
          <div className="flex items-center gap-2 text-sm font-medium text-foreground">
            <DollarSign size={14} className="text-primary" /> 成本概览
            {costQ.isLoading && <Loader2 size={10} className="animate-spin text-muted-foreground" />}
          </div>
          {costQ.data ? (
            <div className="space-y-2">
              <div className="grid grid-cols-3 gap-2">
                <div className="rounded-lg bg-background border border-border p-3">
                  <p className="text-[10px] text-muted-foreground">今日花费</p>
                  <p className="text-lg font-mono font-semibold text-foreground">${(costQ.data.today_cost ?? 0).toFixed(4)}</p>
                </div>
                <div className="rounded-lg bg-background border border-border p-3">
                  <p className="text-[10px] text-muted-foreground">本月花费</p>
                  <p className="text-lg font-mono font-semibold text-foreground">${(costQ.data.month_cost ?? 0).toFixed(4)}</p>
                </div>
                <div className="rounded-lg bg-background border border-primary/30 p-3">
                  <p className="text-[10px] text-muted-foreground">总花费</p>
                  <p className="text-lg font-mono font-semibold text-primary">${(costQ.data.all_time_cost_usd ?? costQ.data.total_cost_usd ?? 0).toFixed(4)}</p>
                </div>
              </div>
              <div className="grid grid-cols-3 gap-2">
                <div className="rounded-lg bg-background border border-border p-2">
                  <p className="text-[10px] text-muted-foreground">今日调用</p>
                  <p className="text-sm font-mono text-foreground">{costQ.data.today_calls ?? 0} 次</p>
                </div>
                <div className="rounded-lg bg-background border border-border p-2">
                  <p className="text-[10px] text-muted-foreground">本月调用</p>
                  <p className="text-sm font-mono text-foreground">{costQ.data.month_calls ?? 0} 次</p>
                </div>
                <div className="rounded-lg bg-background border border-border p-2">
                  <p className="text-[10px] text-muted-foreground">总调用</p>
                  <p className="text-sm font-mono text-foreground">{costQ.data.all_time_calls ?? costQ.data.total_calls ?? 0} 次</p>
                </div>
              </div>
              {(costQ.data.budget_daily || costQ.data.budget_monthly) && (
                <div className="space-y-1.5">
                  <div className="text-[10px] text-muted-foreground flex items-center gap-2 flex-wrap">
                    <span>日预算 ${costQ.data.budget_daily ?? '–'}</span>
                    <span>月预算 ${costQ.data.budget_monthly ?? '–'}</span>
                    {(costQ.data.alerts?.length ?? 0) > 0 && <span className="text-amber-400">⚠ {costQ.data.alerts.length} 条告警</span>}
                  </div>
                  {costQ.data.today_usage_pct != null && (
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] text-muted-foreground w-6">日</span>
                      <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
                        <div className={clsx('h-full rounded-full', costQ.data.today_usage_pct > 80 ? 'bg-red-400' : costQ.data.today_usage_pct > 50 ? 'bg-amber-400' : 'bg-emerald-400')} style={{ width: `${Math.min(costQ.data.today_usage_pct, 100)}%` }} />
                      </div>
                      <span className="text-[10px] text-muted-foreground w-10 text-right">{costQ.data.today_usage_pct}%</span>
                    </div>
                  )}
                </div>
              )}
              {costQ.data.top_models && Object.keys(costQ.data.top_models).length > 0 && (
                <div className="space-y-1">
                  <p className="text-[10px] text-muted-foreground">模型花费 Top</p>
                  {Object.entries(costQ.data.top_models as Record<string, number>).map(([m, v]) => (
                    <div key={m} className="flex items-center gap-2 text-[10px]">
                      <span className="text-muted-foreground font-mono truncate flex-1">{m}</span>
                      <span className="text-foreground font-mono">${v.toFixed(4)}</span>
                    </div>
                  ))}
                </div>
              )}

              {/* Cost History Toggle */}
              <button
                onClick={() => setCostHistOpen(v => !v)}
                className="w-full flex items-center justify-center gap-1.5 text-[11px] text-muted-foreground hover:text-foreground py-1.5 rounded-lg hover:bg-secondary/50 transition-colors"
              >
                <History size={11} />
                <span>历史成本明细</span>
                {costHistOpen ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
              </button>

              {costHistOpen && (
                <div className="space-y-2 pt-1">
                  <div className="flex gap-1">
                    {(['daily', 'monthly', 'yearly'] as CostGranularity[]).map(g => (
                      <button key={g} onClick={() => setCostGranularity(g)}
                        className={clsx('px-2 py-1 rounded text-[10px] transition-colors',
                          g === costGranularity ? 'bg-primary text-primary-foreground' : 'bg-secondary text-muted-foreground hover:text-foreground')}>
                        {GRANULARITY_LABELS[g]}
                      </button>
                    ))}
                    {costHistQ.isLoading && <Loader2 size={10} className="animate-spin text-muted-foreground ml-1 mt-1" />}
                  </div>
                  {costHistQ.data?.items?.length > 0 ? (
                    <div className="max-h-60 overflow-auto rounded-lg border border-border">
                      <table className="w-full text-[11px]">
                        <thead>
                          <tr className="text-left text-muted-foreground border-b border-border bg-secondary/30 sticky top-0">
                            <th className="py-1.5 px-2 font-medium">{costGranularity === 'daily' ? '日期' : costGranularity === 'monthly' ? '月份' : '年份'}</th>
                            <th className="py-1.5 px-2 font-medium text-right">花费</th>
                            <th className="py-1.5 px-2 font-medium text-right">调用</th>
                            <th className="py-1.5 px-2 font-medium text-right">Tokens</th>
                          </tr>
                        </thead>
                        <tbody>
                          {(costHistQ.data.items as any[]).map((row: any) => (
                            <tr key={row.period} className="border-b border-border/30 hover:bg-secondary/20">
                              <td className="py-1 px-2 font-mono text-foreground">{row.period}</td>
                              <td className="py-1 px-2 font-mono text-right text-foreground">${row.cost_usd?.toFixed(4)}</td>
                              <td className="py-1 px-2 font-mono text-right text-muted-foreground">{row.calls}</td>
                              <td className="py-1 px-2 font-mono text-right text-muted-foreground">{row.tokens?.toLocaleString()}</td>
                            </tr>
                          ))}
                        </tbody>
                        <tfoot>
                          <tr className="border-t border-border bg-secondary/20 font-medium">
                            <td className="py-1.5 px-2 text-foreground">合计</td>
                            <td className="py-1.5 px-2 font-mono text-right text-primary">${costHistQ.data.total_cost_usd?.toFixed(4)}</td>
                            <td className="py-1.5 px-2 font-mono text-right text-muted-foreground">{costHistQ.data.total_calls}</td>
                            <td className="py-1.5 px-2"></td>
                          </tr>
                        </tfoot>
                      </table>
                    </div>
                  ) : (
                    <p className="text-[10px] text-muted-foreground text-center py-2">{costHistQ.isLoading ? '加载中...' : '暂无历史数据'}</p>
                  )}
                </div>
              )}
            </div>
          ) : (
            <p className="text-xs text-muted-foreground py-4 text-center">{costQ.isError ? '加载失败' : '加载中...'}</p>
          )}
        </div>

        {/* Active Jobs */}
        <div className="rounded-2xl border border-border bg-card p-4 space-y-3">
          <div className="flex items-center gap-2 text-sm font-medium text-foreground">
            <Activity size={14} className="text-primary" /> 活跃任务
            {progressQ.isLoading && <Loader2 size={10} className="animate-spin text-muted-foreground" />}
          </div>
          {Array.isArray(progressQ.data) && progressQ.data.length > 0 ? (
            <div className="space-y-2 max-h-48 overflow-auto">
              {(progressQ.data as any[]).map((job: any, i: number) => {
                const jobId = typeof job.job_id === 'string' ? job.job_id : ''
                const canCancel = job.workflow === 'ingestion' && ['pending', 'running'].includes(String(job.status))
                const cancelling = cancellingJobId === jobId
                return (
                  <div key={jobId || i} className="rounded-lg bg-background border border-border p-2 text-xs">
                    <div className="flex items-center gap-2">
                      <Loader2 size={10} className="text-primary animate-spin" />
                      <span className="text-foreground font-medium">{job.job_type || job.workflow || job.job_id}</span>
                      <span className="text-muted-foreground ml-auto">{job.current_stage_label || job.stage || job.status}</span>
                      {canCancel && (
                        <button
                          type="button"
                          onClick={() => void handleCancelJob(job)}
                          disabled={cancelling}
                          className="inline-flex items-center gap-1 rounded border border-border bg-secondary px-1.5 py-0.5 text-[10px] text-muted-foreground hover:border-red-400 hover:text-red-300 disabled:cursor-not-allowed disabled:opacity-50"
                          title="取消后台资料解析任务"
                        >
                          {cancelling ? <Loader2 size={10} className="animate-spin" /> : <XCircle size={10} />}
                          取消
                        </button>
                      )}
                    </div>
                    {job.progress_pct != null && (
                      <div className="mt-1.5 h-1 bg-muted rounded-full overflow-hidden">
                        <div className="h-full bg-primary rounded-full" style={{ width: `${Math.max(0, Math.min(100, job.progress_pct))}%` }} />
                      </div>
                    )}
                    {job.message && <p className="mt-1 truncate text-[10px] text-muted-foreground">{job.message}</p>}
                  </div>
                )
              })}
            </div>
          ) : (
            <p className="text-xs text-muted-foreground py-4 text-center">{progressQ.isError ? '加载失败' : '暂无活跃任务'}</p>
          )}
        </div>

        {/* Ingestion Dispatcher */}
        <div className="rounded-2xl border border-border bg-card p-4 space-y-3">
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-center gap-2 text-sm font-medium text-foreground">
              <Shield size={14} className={staleDispatcherJobs.length ? 'text-amber-400' : 'text-primary'} /> 入库队列
              {dispatcherQ.isLoading && <Loader2 size={10} className="animate-spin text-muted-foreground" />}
            </div>
            <button
              type="button"
              onClick={() => void handleReapStaleJobs()}
              disabled={reapingStale}
              className="inline-flex items-center gap-1 rounded border border-border bg-secondary px-2 py-1 text-[11px] text-muted-foreground hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
              title="恢复 lease 过期的资料入库任务"
            >
              {reapingStale ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}
              恢复过期
            </button>
          </div>

          {dispatcherStatus ? (
            <>
              <div className="grid grid-cols-4 gap-2">
                <div className="rounded-lg bg-background border border-border p-2">
                  <p className="text-[10px] text-muted-foreground">运行中</p>
                  <p className="text-sm font-mono text-foreground">{dispatcherStatus.active_or_queued_count ?? 0}</p>
                </div>
                <div className="rounded-lg bg-background border border-border p-2">
                  <p className="text-[10px] text-muted-foreground">持久化</p>
                  <p className="text-sm font-mono text-foreground">{dispatcherStatus.persisted_active_count ?? 0}</p>
                </div>
                <div className={clsx('rounded-lg border p-2', staleDispatcherJobs.length ? 'bg-amber-400/5 border-amber-400/30' : 'bg-background border-border')}>
                  <p className="text-[10px] text-muted-foreground">过期</p>
                  <p className={clsx('text-sm font-mono', staleDispatcherJobs.length ? 'text-amber-400' : 'text-foreground')}>{staleDispatcherJobs.length}</p>
                </div>
                <div className="rounded-lg bg-background border border-border p-2">
                  <p className="text-[10px] text-muted-foreground">并发</p>
                  <p className="text-sm font-mono text-foreground">{dispatcherStatus.max_workers ?? '-'}</p>
                </div>
              </div>

              {dispatcherJobs.length > 0 ? (
                <div className="space-y-1.5 max-h-48 overflow-auto">
                  {dispatcherJobs.slice(0, 6).map(job => (
                    <div key={job.job_id} className={clsx('rounded-lg border p-2 text-xs', job.lease_expired ? 'border-amber-400/30 bg-amber-400/5' : 'border-border bg-background')}>
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-foreground truncate">{job.job_id}</span>
                        <span className="ml-auto text-muted-foreground">{job.status || '-'}</span>
                        {job.lease_expired && <span className="rounded bg-amber-400/10 px-1.5 py-0.5 text-[10px] text-amber-400">lease 过期</span>}
                      </div>
                      <div className="mt-1 flex items-center gap-2 text-[10px] text-muted-foreground">
                        <span className="truncate">source {job.source_id || '-'}</span>
                        <span>retry {job.retry_count ?? 0}</span>
                        <span>{job.stage || job.lease_state || '等待状态'}</span>
                      </div>
                      {job.pct != null && (
                        <div className="mt-1.5 h-1 bg-muted rounded-full overflow-hidden">
                          <div className={clsx('h-full rounded-full', job.lease_expired ? 'bg-amber-400' : 'bg-primary')} style={{ width: `${Math.max(0, Math.min(100, job.pct))}%` }} />
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-muted-foreground py-3 text-center">暂无待维护的入库任务</p>
              )}

              {reapResult && (
                <p className="text-[10px] text-muted-foreground">
                  最近恢复：检查 {reapResult.checked ?? 0}，过期 {reapResult.stale ?? 0}，恢复 {reapResult.resumed ?? 0}，失败 {reapResult.failed ?? 0}。
                </p>
              )}
            </>
          ) : (
            <p className="text-xs text-muted-foreground py-4 text-center">{dispatcherQ.isError ? '加载失败' : '等待队列状态'}</p>
          )}
        </div>
      </div>

      {/* Model Routing Table — DYNAMIC from /settings/model-routing */}
      <div className="rounded-2xl border border-border bg-card p-4 space-y-3">
        <div className="flex items-center gap-2 text-sm font-medium text-foreground">
          <ArrowRight size={14} className="text-primary" /> 模型路由表（OpenRouter 统一网关）
          {routingQ.isLoading && <Loader2 size={10} className="animate-spin text-muted-foreground" />}
        </div>
        <p className="text-[11px] text-muted-foreground">实时读取当前配置，切换模型后自动更新；所有 LLM 调用通过 OpenRouter 路由，按角色分配不同模型，自动 fallback</p>
        {Array.isArray(routingQ.data) && routingQ.data.length > 0 ? (
          <div className="overflow-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-muted-foreground border-b border-border">
                  <th className="pb-2 pr-4 font-medium">角色 / 技能</th>
                  <th className="pb-2 pr-4 font-medium">主选模型</th>
                  <th className="pb-2 pr-4 font-medium">备选 (Fallback)</th>
                  <th className="pb-2 pr-4 font-medium">供应商</th>
                  <th className="pb-2 font-medium">层级</th>
                </tr>
              </thead>
              <tbody>
                {(routingQ.data as any[]).map((r: any, i: number) => (
                  <tr key={i} className="border-b border-border/50 hover:bg-secondary/30 transition-colors">
                    <td className="py-2 pr-4 text-foreground">{r.role}</td>
                    <td className="py-2 pr-4 font-mono text-muted-foreground">{r.model}</td>
                    <td className="py-2 pr-4 font-mono text-muted-foreground/60 text-[10px]">{r.fallback || '–'}</td>
                    <td className="py-2 pr-4 text-muted-foreground">{r.provider}</td>
                    <td className="py-2">
                      <span className={clsx('text-[10px] px-1.5 py-0.5 rounded',
                        r.tier === 'Tier-1' ? 'bg-primary/10 text-primary' :
                        r.tier === 'Embed' ? 'bg-sky-400/10 text-sky-400' : 'bg-muted text-muted-foreground'
                      )}>{r.tier}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-xs text-muted-foreground py-4 text-center">{routingQ.isError ? '加载失败' : '加载中...'}</p>
        )}
      </div>

      {/* Data Sources Grid */}
      <div className="rounded-2xl border border-border bg-card p-4 space-y-3">
        <div className="flex items-center gap-2 text-sm font-medium text-foreground">
          <Globe size={14} className="text-primary" /> 数据源状态
          {dsListQ.isLoading && <Loader2 size={10} className="animate-spin text-muted-foreground" />}
          {Array.isArray(dsListQ.data) && <span className="text-[10px] text-muted-foreground">（{dsListQ.data.length} 个）</span>}
        </div>
        {Array.isArray(dsListQ.data) && dsListQ.data.length > 0 ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-2">
            {(dsListQ.data as any[]).map((src: any) => {
              const catInfo = DS_CATEGORIES[src.category] || { color: 'bg-muted-foreground', label: src.category }
              return (
                <div key={src.id || src.name} className={clsx('rounded-xl border p-3 space-y-1.5',
                  src.key_configured === false ? 'border-amber-400/30 bg-amber-400/5' : 'border-border bg-background')}>
                  <div className="flex items-center gap-2">
                    <span className={clsx('w-2 h-2 rounded-full shrink-0', catInfo.color)} />
                    <span className="text-xs font-medium text-foreground truncate">{src.name || src.id}</span>
                  </div>
                  <div className="flex items-center gap-2 text-[10px]">
                    <span className="text-muted-foreground">{catInfo.label}</span>
                    {src.rate_limit && <span className="text-muted-foreground">| {src.rate_limit}</span>}
                  </div>
                  {src.key_configured === false && <p className="text-[10px] text-amber-400">⚠ 需要 API Key</p>}
                  {src.coverage && <p className="text-[10px] text-muted-foreground truncate">覆盖: {src.coverage}</p>}
                  {src.known_limitations && <p className="text-[10px] text-muted-foreground truncate">限制: {src.known_limitations}</p>}
                </div>
              )
            })}
          </div>
        ) : (
          <p className="text-xs text-muted-foreground py-4 text-center">{dsListQ.isError ? '加载失败' : '加载中...'}</p>
        )}
      </div>

      {/* Reminders */}
      {Array.isArray(remindersQ.data) && remindersQ.data.length > 0 && (
        <div className="rounded-2xl border border-amber-400/30 bg-amber-400/5 p-4 space-y-2">
          <div className="flex items-center gap-2 text-sm font-medium text-amber-400">
            <AlertCircle size={14} /> 数据源提醒 ({remindersQ.data.length})
          </div>
          <div className="space-y-1">
            {(remindersQ.data as any[]).map((r: any, i: number) => (
              <p key={i} className="text-xs text-amber-300/80">• {r.message || r.reminder || JSON.stringify(r)}</p>
            ))}
          </div>
        </div>
      )}

      {/* Quota */}
      {Array.isArray(quotaQ.data) && quotaQ.data.length > 0 && (
        <div className="rounded-2xl border border-border bg-card p-4 space-y-3">
          <div className="flex items-center gap-2 text-sm font-medium text-foreground">
            <Shield size={14} className="text-primary" /> API 配额状态
            {quotaQ.isLoading && <Loader2 size={10} className="animate-spin text-muted-foreground" />}
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {(quotaQ.data as any[]).map((q: any, i: number) => {
              const canCall = q.allowed ?? q.can_call ?? true
              const remaining = q.cooldown_remaining_sec ?? q.remaining ?? 0
              const name = q.resource || q.name || q.source_id || '未知'
              const freq = q.freq || ''
              return (
                <div key={i} className="rounded-lg border border-border bg-background p-2.5 text-xs space-y-1.5">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium text-foreground truncate">{name}</span>
                    <span className={clsx('px-1.5 py-0.5 rounded text-[10px] shrink-0', canCall ? 'bg-emerald-400/10 text-emerald-400' : 'bg-amber-400/10 text-amber-400')}>
                      {canCall ? '可用' : '冷却中'}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
                    {freq && <span className="px-1 py-0.5 rounded bg-muted">{freq}</span>}
                    {!canCall && remaining > 0 && <span>剩余 {remaining}s</span>}
                    {canCall && <span>就绪</span>}
                  </div>
                  {!canCall && remaining > 0 && q.cooldown_total_sec > 0 && (
                    <div className="h-1 bg-muted rounded-full overflow-hidden">
                      <div className="h-full bg-amber-400 rounded-full" style={{ width: `${Math.max(5, (remaining / q.cooldown_total_sec) * 100)}%` }} />
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Feature Status — DYNAMIC from /settings/features */}
      <div className="rounded-2xl border border-border bg-card p-4 space-y-3">
        <div className="flex items-center gap-2 text-sm font-medium text-foreground">
          <CheckCircle2 size={14} className="text-primary" /> 功能模块状态
          {featuresQ.isLoading && <Loader2 size={10} className="animate-spin text-muted-foreground" />}
        </div>
        {Array.isArray(featuresQ.data) && featuresQ.data.length > 0 ? (
          <div className="space-y-2">
            {(featuresQ.data as any[]).map((f: any, i: number) => (
              <div key={i} className="flex items-start gap-3 text-xs">
                <span className={clsx('mt-0.5 w-2 h-2 rounded-full shrink-0',
                  f.status === 'active' ? 'bg-emerald-400' : f.status === 'mock' ? 'bg-sky-400' : 'bg-amber-400')} />
                <div className="flex-1">
                  <span className="text-foreground font-medium">{f.label}</span>
                  {f.status === 'reserved' && <span className="text-[10px] ml-1.5 px-1.5 py-0.5 rounded bg-amber-400/10 text-amber-400">预留</span>}
                  {f.status === 'mock' && <span className="text-[10px] ml-1.5 px-1.5 py-0.5 rounded bg-sky-400/10 text-sky-400">Mock</span>}
                  <p className="text-[11px] text-muted-foreground mt-0.5">{f.detail}</p>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-xs text-muted-foreground py-4 text-center">{featuresQ.isError ? '加载失败' : '加载中...'}</p>
        )}
      </div>
    </div>
  )
}
