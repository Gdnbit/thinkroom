export interface ProductionRefreshCandidateEnvMismatch {
  expected_env_file?: string
  actual_env_file?: string
}

export interface ProductionReadinessRefreshReport {
  available?: boolean
  status?: string
  readiness_state?: string
  release_candidate_ready?: boolean
  release_allowed?: boolean
  env_file?: string
  candidate_env_mismatch?: ProductionRefreshCandidateEnvMismatch | null
  blocking_steps?: string[]
  steps?: ProductionRefreshStepReport[]
  next_action_count?: number
  recommended_actions?: ProductionRefreshActionReport[]
  recommended_action_groups?: ProductionRefreshActionGroupReport[]
  operator_command?: string
  fresh?: boolean
  freshness_status?: string
}

export interface ProductionRefreshStepReport {
  key?: string
  label?: string
  status?: string
  exit_code?: number | null
  latest_path?: string
  blocking_steps?: string[]
}

export interface ProductionRefreshActionReport {
  key?: string
  label?: string
  status?: string
  action?: string
  owner?: string
  requires_secret_update?: boolean
  production_required?: boolean
  source_count?: number
  source_steps?: string[]
  source_reports?: string[]
  verification_commands?: string[]
}

export interface ProductionRefreshActionGroupReport {
  key?: string
  label?: string
  status?: string
  action_count?: number
  source_count?: number
  source_steps?: string[]
  source_reports?: string[]
  has_more_actions?: boolean
  actions?: ProductionRefreshActionReport[]
}

export interface ProductionRefreshActionView {
  key: string
  label: string
  status: string
  action: string
  owner: string
  requiresSecretUpdate: boolean
  productionRequired: boolean
  sourceCount: number
  sourceSteps: string[]
  sourceReports: string[]
  verificationCommands: string[]
}

export interface ProductionRefreshStepView {
  key: string
  label: string
  status: string
  exitCode: number | null
  latestPath: string
  blockingSteps: string[]
}

export interface ProductionRefreshActionGroupView {
  key: string
  label: string
  status: string
  actionCount: number
  sourceCount: number
  sourceSteps: string[]
  sourceReports: string[]
  hasMoreActions: boolean
  actions: ProductionRefreshActionView[]
}

export interface ProductionRefreshView {
  tone: 'success' | 'warning' | 'danger'
  title: string
  subtitle: string
  envFile: string
  expectedEnvFile: string
  actualEnvFile: string
  blockingSteps: string[]
  steps: ProductionRefreshStepView[]
  recommendedActions: ProductionRefreshActionView[]
  recommendedActionGroups: ProductionRefreshActionGroupView[]
  operatorCommand: string
}

const REFRESH_COMMAND = 'python scripts/run_production_readiness_refresh.py --env-file apps/backend/.env.prod --fail-on-blocked'
const STEP_LABELS: Record<string, string> = {
  release_gate: '发布门禁',
  production_operator_preflight: '生产操作预检',
  production_stack_release: '生产栈发布计划',
  frontend_release_checks: '前端发布检查',
  production_readiness_summary: '生产就绪汇总',
  verification_report_hygiene: '报告卫生检查',
  candidate_env_file: '候选配置文件',
}

function asText(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function asTextList(value: unknown): string[] {
  return Array.isArray(value) ? value.map(item => String(item).trim()).filter(Boolean) : []
}

function stepLabel(key: string, label: string): string {
  return STEP_LABELS[key] || label || key
}

function buildRecommendedActions(report?: ProductionReadinessRefreshReport | null): ProductionRefreshActionView[] {
  return Array.isArray(report?.recommended_actions)
    ? report.recommended_actions
      .map(action => ({
        key: asText(action.key),
        label: asText(action.label) || asText(action.key),
        status: asText(action.status),
        action: asText(action.action),
        owner: asText(action.owner),
        requiresSecretUpdate: action.requires_secret_update === true,
        productionRequired: action.production_required === true,
        sourceCount: typeof action.source_count === 'number' ? action.source_count : 1,
        sourceSteps: asTextList(action.source_steps),
        sourceReports: asTextList(action.source_reports),
        verificationCommands: asTextList(action.verification_commands),
      }))
      .filter(action => action.key && action.action)
    : []
}

function buildRefreshSteps(report?: ProductionReadinessRefreshReport | null): ProductionRefreshStepView[] {
  return Array.isArray(report?.steps)
    ? report.steps
      .map(step => ({
        key: asText(step.key),
        label: stepLabel(asText(step.key), asText(step.label)),
        status: asText(step.status),
        exitCode: typeof step.exit_code === 'number' ? step.exit_code : null,
        latestPath: asText(step.latest_path),
        blockingSteps: asTextList(step.blocking_steps),
      }))
      .filter(step => step.key)
    : []
}

function buildRecommendedActionGroups(report?: ProductionReadinessRefreshReport | null): ProductionRefreshActionGroupView[] {
  return Array.isArray(report?.recommended_action_groups)
    ? report.recommended_action_groups
      .map(group => ({
        key: asText(group.key),
        label: asText(group.label) || asText(group.key),
        status: asText(group.status),
        actionCount: typeof group.action_count === 'number' ? group.action_count : 0,
        sourceCount: typeof group.source_count === 'number' ? group.source_count : 0,
        sourceSteps: asTextList(group.source_steps),
        sourceReports: asTextList(group.source_reports),
        hasMoreActions: group.has_more_actions === true,
        actions: buildRecommendedActions({ recommended_actions: group.actions ?? [] }),
      }))
      .filter(group => group.key && group.actions.length > 0)
    : []
}

export function buildProductionRefreshView(report?: ProductionReadinessRefreshReport | null): ProductionRefreshView {
  const operatorCommand = asText(report?.operator_command) || REFRESH_COMMAND
  const mismatch = report?.candidate_env_mismatch ?? null
  const expectedEnvFile = asText(mismatch?.expected_env_file)
  const actualEnvFile = asText(mismatch?.actual_env_file)
  const envFile = asText(report?.env_file)
  const blockingSteps = Array.isArray(report?.blocking_steps)
    ? report.blocking_steps.map(item => String(item).trim()).filter(Boolean)
    : []
  const recommendedActions = buildRecommendedActions(report)
  const recommendedActionGroups = buildRecommendedActionGroups(report)
  const steps = buildRefreshSteps(report)

  if (!report?.available) {
    return {
      tone: 'warning',
      title: '证据刷新记录缺失',
      subtitle: '尚未生成 production-readiness-refresh-latest.json。',
      envFile,
      expectedEnvFile,
      actualEnvFile,
      blockingSteps,
      steps,
      recommendedActions,
      recommendedActionGroups,
      operatorCommand,
    }
  }

  if (report.release_candidate_ready && report.release_allowed) {
    return {
      tone: 'success',
      title: '证据刷新可用',
      subtitle: '最近一次刷新来自标准候选配置。',
      envFile,
      expectedEnvFile,
      actualEnvFile,
      blockingSteps,
      steps,
      recommendedActions,
      recommendedActionGroups,
      operatorCommand,
    }
  }

  if (mismatch) {
    return {
      tone: 'danger',
      title: '证据刷新被阻断',
      subtitle: `最近一次刷新使用了非标准候选配置，需改用 ${expectedEnvFile || 'apps/backend/.env.prod'}。`,
      envFile,
      expectedEnvFile,
      actualEnvFile,
      blockingSteps,
      steps,
      recommendedActions,
      recommendedActionGroups,
      operatorCommand,
    }
  }

  const stale = report.fresh === false && report.freshness_status !== 'missing'
  return {
    tone: stale ? 'warning' : 'danger',
    title: stale ? '证据刷新记录过期' : '证据刷新未放行',
    subtitle: stale ? '请重新刷新生产 readiness 证据。' : '最近一次刷新仍存在生产阻断项。',
    envFile,
    expectedEnvFile,
    actualEnvFile,
    blockingSteps,
    steps,
    recommendedActions,
    recommendedActionGroups,
    operatorCommand,
  }
}
