export type ProductionDigestTone = 'success' | 'warning' | 'danger'

export interface ProductionDigestBlocker {
  group_key?: string
  label?: string
  section_label?: string
  config_keys?: string[]
  referenced_config_keys?: string[]
  verification_commands?: string[]
}

export interface ProductionUpstreamReportIssue {
  key?: string
  status?: string
  regenerate_command?: string
}

export interface ProductionCandidateEnvMismatch {
  report_key?: string
  report_label?: string
  field?: string
  expected_env_file?: string
  actual_env_file?: string
  regenerate_command?: string
}

export interface ProductionReadinessDigest {
  available?: boolean
  status?: string
  readiness_state?: string
  release_allowed?: boolean
  fresh?: boolean
  freshness_status?: string
  regenerate_command?: string
  blocker_count?: number | null
  blocker_total_count?: number | null
  blocker_shown_count?: number | null
  top_remediation_count?: number
  blockers?: ProductionDigestBlocker[]
  upstream_report_issues?: ProductionUpstreamReportIssue[]
  candidate_env_mismatches?: ProductionCandidateEnvMismatch[]
}

export interface ProductionDigestView {
  tone: ProductionDigestTone
  title: string
  subtitle: string
  blockers: Array<{
    key: string
    label: string
    sectionLabel: string
    configKeys: string[]
    verificationCommands: string[]
  }>
  moreCount: number
  operatorCommand?: string
  upstreamReportIssues: Array<{
    key: string
    label: string
    status: string
    statusLabel: string
    operatorCommand?: string
  }>
  candidateEnvMismatches: Array<{
    key: string
    reportLabel: string
    expectedEnvFile: string
    actualEnvFile: string
    operatorCommand?: string
  }>
}

function asCount(value: unknown, fallback = 0) {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function asStringList(value: unknown) {
  if (!Array.isArray(value)) return []
  return [...new Set(value.map(item => String(item).trim()).filter(Boolean))]
}

const UPSTREAM_REPORT_LABELS: Record<string, string> = {
  production_env_validation: 'Production env validation',
  production_compose_config: 'Production compose config',
  public_api_leak_scan: 'Public API leak scan',
  production_preflight: 'Production preflight',
  production_operator_preflight: 'Production operator preflight',
  production_stack_release: 'Production stack release',
  release_gate: 'Release gate',
  release_gate_secret_inventory: 'Release gate secret inventory',
  secret_inventory_validation: 'Secret inventory validation',
}

const UPSTREAM_REPORT_STATUS_LABELS: Record<string, string> = {
  missing: '缺失',
  stale: '过期',
}

function labelForReportKey(key: string) {
  return UPSTREAM_REPORT_LABELS[key] || key.replace(/_/g, ' ')
}

function buildUpstreamReportIssues(
  digest: ProductionReadinessDigest | null | undefined,
  maxItems: number,
) {
  return (digest?.upstream_report_issues ?? [])
    .map((item) => {
      const key = (item.key || '').trim()
      const status = (item.status || 'unknown').trim()
      return {
        key,
        label: labelForReportKey(key),
        status,
        statusLabel: UPSTREAM_REPORT_STATUS_LABELS[status] || status,
        operatorCommand: item.regenerate_command?.trim() || undefined,
      }
    })
    .filter((item) => item.key)
    .slice(0, Math.max(0, maxItems))
}

function buildCandidateEnvMismatches(
  digest: ProductionReadinessDigest | null | undefined,
  maxItems: number,
) {
  return (digest?.candidate_env_mismatches ?? [])
    .map((item) => {
      const reportKey = (item.report_key || '').trim()
      const field = (item.field || 'env_file').trim()
      const expectedEnvFile = (item.expected_env_file || '').trim()
      const actualEnvFile = (item.actual_env_file || '').trim()
      return {
        key: `${reportKey}:${field}`,
        reportLabel: (item.report_label || labelForReportKey(reportKey)).trim(),
        expectedEnvFile,
        actualEnvFile,
        operatorCommand: item.regenerate_command?.trim() || undefined,
      }
    })
    .filter((item) => item.key !== ':' && item.actualEnvFile && item.expectedEnvFile)
    .slice(0, Math.max(0, maxItems))
}

export function buildProductionDigestView(
  digest: ProductionReadinessDigest | null | undefined,
  options: { maxBlockers?: number } = {},
): ProductionDigestView {
  const maxBlockers = Math.max(0, options.maxBlockers ?? 3)
  const upstreamReportIssues = buildUpstreamReportIssues(digest, 3)
  const candidateEnvMismatches = buildCandidateEnvMismatches(digest, 3)
  if (!digest?.available) {
    return {
      tone: 'warning',
      title: '发布摘要缺失',
      subtitle: '尚未生成 production-readiness-digest-latest.json。',
      blockers: [],
      moreCount: 0,
      operatorCommand: digest?.regenerate_command,
      upstreamReportIssues,
      candidateEnvMismatches,
    }
  }

  const blockers = (digest.blockers ?? [])
    .map((item) => {
      const label = (item.label || item.group_key || 'unknown').trim()
      return {
        key: (item.group_key || label).trim(),
        label,
        sectionLabel: (item.section_label || 'Release Verification').trim(),
        configKeys: asStringList(item.referenced_config_keys?.length ? item.referenced_config_keys : item.config_keys),
        verificationCommands: asStringList(item.verification_commands),
      }
    })
    .filter((item) => item.label)
  const visibleBlockers = blockers.slice(0, maxBlockers)
  const blockerCount = asCount(digest.blocker_total_count, asCount(digest.blocker_count, blockers.length))
  const topRemediationCount = asCount(
    digest.blocker_shown_count,
    asCount(digest.top_remediation_count, blockers.length),
  )
  const moreCount = Math.max(0, blockerCount - visibleBlockers.length)

  if (digest.release_allowed) {
    return {
      tone: 'success',
      title: '生产发布可放行',
      subtitle: '最新发布摘要没有阻断项。',
      blockers: [],
      moreCount: 0,
      operatorCommand: digest.regenerate_command,
      upstreamReportIssues,
      candidateEnvMismatches,
    }
  }

  if (digest.fresh === false && digest.freshness_status !== 'missing') {
    return {
      tone: 'warning',
      title: '发布摘要已过期',
      subtitle: '请重新生成 production-readiness-digest-latest.json 后再判断发布状态。',
      blockers: visibleBlockers,
      moreCount,
      operatorCommand: digest.regenerate_command,
      upstreamReportIssues,
      candidateEnvMismatches,
    }
  }

  const blocked = digest.status === 'blocked' || digest.readiness_state === 'blocked'
  return {
    tone: blocked ? 'danger' : 'warning',
    title: blocked ? '生产发布阻断' : '生产发布需确认',
    subtitle: `${blockerCount} 个发布阻断项，优先处理前 ${topRemediationCount} 项。`,
    blockers: visibleBlockers,
    moreCount,
    operatorCommand: digest.regenerate_command,
    upstreamReportIssues,
    candidateEnvMismatches,
  }
}
