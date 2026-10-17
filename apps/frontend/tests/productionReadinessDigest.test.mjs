import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import vm from 'node:vm'
import { createRequire } from 'node:module'
import ts from 'typescript'

const root = process.cwd()
const require = createRequire(import.meta.url)
const sourcePath = path.join(root, 'src', 'utils', 'productionReadinessDigest.ts')
const source = fs.readFileSync(sourcePath, 'utf8')
const transpiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2020,
    esModuleInterop: true,
  },
}).outputText

const sandbox = {
  exports: {},
  module: { exports: {} },
  require,
}
vm.runInNewContext(transpiled, sandbox, { filename: sourcePath })
const { buildProductionDigestView } = { ...sandbox.exports, ...sandbox.module.exports }

assert.equal(typeof buildProductionDigestView, 'function')

const blocked = buildProductionDigestView({
  available: true,
  status: 'blocked',
  readiness_state: 'blocked',
  release_allowed: false,
  blocker_count: 19,
  blocker_total_count: 19,
  blocker_shown_count: 10,
  top_remediation_count: 10,
  blockers: [
    {
      group_key: 'action:secrets',
      label: 'Secrets',
      section_label: 'Critical Secrets',
      referenced_config_keys: ['APP_API_KEY', 'APP_SECRET_KEY', 'OPENROUTER_API_KEY'],
      verification_commands: ['python scripts/validate_production_env.py --env-file apps/backend/.env.prod --fail-on-error'],
    },
    {
      group_key: 'action:rate_limit_storage',
      label: 'Rate limit storage',
      section_label: 'Production Profile',
      referenced_config_keys: ['RATE_LIMIT_STORAGE_URI', 'REDIS_ENABLED'],
      verification_commands: ['python scripts/run_release_gate.py --require-env-file --env-file apps/backend/.env.prod --backend-url http://127.0.0.1:8000'],
    },
    {
      group_key: 'config:MINIO_ENDPOINT',
      label: 'MINIO_ENDPOINT',
      section_label: 'Object Storage',
      config_keys: ['MINIO_ENDPOINT'],
      verification_commands: [],
    },
    { group_key: 'config:SENTRY_DSN', label: 'SENTRY_DSN', section_label: 'Observability' },
  ],
})

assert.equal(blocked.tone, 'danger')
assert.equal(blocked.title, '生产发布阻断')
assert.equal(blocked.subtitle, '19 个发布阻断项，优先处理前 10 项。')
assert.deepEqual(blocked.blockers.map((item) => item.label), ['Secrets', 'Rate limit storage', 'MINIO_ENDPOINT'])
assert.deepEqual(JSON.parse(JSON.stringify(blocked.blockers.map((item) => item.configKeys))), [
  ['APP_API_KEY', 'APP_SECRET_KEY', 'OPENROUTER_API_KEY'],
  ['RATE_LIMIT_STORAGE_URI', 'REDIS_ENABLED'],
  ['MINIO_ENDPOINT'],
])
assert.deepEqual(JSON.parse(JSON.stringify(blocked.blockers.map((item) => item.verificationCommands))), [
  ['python scripts/validate_production_env.py --env-file apps/backend/.env.prod --fail-on-error'],
  ['python scripts/run_release_gate.py --require-env-file --env-file apps/backend/.env.prod --backend-url http://127.0.0.1:8000'],
  [],
])
assert.equal(blocked.moreCount, 16)

const releaseReady = buildProductionDigestView({
  available: true,
  status: 'pass',
  readiness_state: 'release_ready',
  release_allowed: true,
  blocker_count: 0,
  top_remediation_count: 0,
  blockers: [],
})

assert.equal(releaseReady.tone, 'success')
assert.equal(releaseReady.title, '生产发布可放行')
assert.equal(releaseReady.subtitle, '最新发布摘要没有阻断项。')
assert.equal(releaseReady.moreCount, 0)

const missing = buildProductionDigestView({
  available: false,
  status: 'missing',
  readiness_state: 'unknown',
  release_allowed: false,
  blocker_count: null,
  top_remediation_count: 0,
  blockers: [],
  regenerate_command: 'python scripts/summarize_production_readiness.py --report-dir docs/verification',
})

assert.equal(missing.tone, 'warning')
assert.equal(missing.title, '发布摘要缺失')
assert.equal(missing.subtitle, '尚未生成 production-readiness-digest-latest.json。')
assert.equal(missing.operatorCommand, 'python scripts/summarize_production_readiness.py --report-dir docs/verification')

const stale = buildProductionDigestView({
  available: true,
  status: 'blocked',
  readiness_state: 'blocked',
  release_allowed: false,
  fresh: false,
  freshness_status: 'stale',
  blocker_count: 1,
  top_remediation_count: 1,
  regenerate_command: 'python scripts/summarize_production_readiness.py --report-dir docs/verification',
  candidate_env_mismatches: [
    {
      report_key: 'production_env_validation',
      report_label: 'Production env validation',
      field: 'env_path',
      expected_env_file: 'apps/backend/.env.prod',
      actual_env_file: 'apps/backend/.env.prod.example',
      regenerate_command: 'python scripts/validate_production_env.py --env-file apps/backend/.env.prod --fail-on-error',
    },
  ],
  upstream_report_issues: [
    {
      key: 'production_preflight',
      status: 'missing',
      regenerate_command: 'python scripts/export_production_preflight.py --backend-url http://127.0.0.1:8000 --fail-on-not-ready',
    },
    {
      key: 'public_api_leak_scan',
      status: 'stale',
      regenerate_command: 'python scripts/scan_public_api_leaks.py --backend-url http://127.0.0.1:8000 --fail-on-leak',
    },
  ],
  blockers: [
    { group_key: 'config:APP_API_KEY', label: 'APP_API_KEY', section_label: 'Critical Secrets' },
  ],
})

assert.equal(stale.tone, 'warning')
assert.equal(stale.title, '发布摘要已过期')
assert.equal(stale.subtitle, '请重新生成 production-readiness-digest-latest.json 后再判断发布状态。')
assert.equal(stale.operatorCommand, 'python scripts/summarize_production_readiness.py --report-dir docs/verification')
assert.deepEqual(stale.blockers.map((item) => item.label), ['APP_API_KEY'])
assert.deepEqual(JSON.parse(JSON.stringify(stale.candidateEnvMismatches)), [
  {
    key: 'production_env_validation:env_path',
    reportLabel: 'Production env validation',
    expectedEnvFile: 'apps/backend/.env.prod',
    actualEnvFile: 'apps/backend/.env.prod.example',
    operatorCommand: 'python scripts/validate_production_env.py --env-file apps/backend/.env.prod --fail-on-error',
  },
])
assert.deepEqual(JSON.parse(JSON.stringify(stale.upstreamReportIssues)), [
  {
    key: 'production_preflight',
    label: 'Production preflight',
    status: 'missing',
    statusLabel: '缺失',
    operatorCommand: 'python scripts/export_production_preflight.py --backend-url http://127.0.0.1:8000 --fail-on-not-ready',
  },
  {
    key: 'public_api_leak_scan',
    label: 'Public API leak scan',
    status: 'stale',
    statusLabel: '过期',
    operatorCommand: 'python scripts/scan_public_api_leaks.py --backend-url http://127.0.0.1:8000 --fail-on-leak',
  },
])
