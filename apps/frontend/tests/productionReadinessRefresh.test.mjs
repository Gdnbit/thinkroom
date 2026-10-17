import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import vm from 'node:vm'
import { createRequire } from 'node:module'
import ts from 'typescript'

const root = process.cwd()
const require = createRequire(import.meta.url)
const sourcePath = path.join(root, 'src', 'utils', 'productionReadinessRefresh.ts')
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
const { buildProductionRefreshView } = { ...sandbox.exports, ...sandbox.module.exports }

assert.equal(typeof buildProductionRefreshView, 'function')

const blocked = buildProductionRefreshView({
  available: true,
  status: 'blocked',
  readiness_state: 'blocked',
  release_candidate_ready: false,
  release_allowed: false,
  env_file: 'apps/backend/.env.prod.example',
  candidate_env_mismatch: {
    expected_env_file: 'apps/backend/.env.prod',
    actual_env_file: 'apps/backend/.env.prod.example',
  },
  blocking_steps: ['candidate_env_file'],
  steps: [
    {
      key: 'release_gate',
      label: 'Release gate',
      status: 'blocked',
      exit_code: 2,
      latest_path: 'docs/verification/release-gate-latest.json',
      blocking_steps: ['production_env_validation'],
    },
    {
      key: 'frontend_release_checks',
      label: 'Frontend release checks',
      status: 'pass',
      exit_code: 0,
      latest_path: 'docs/verification/frontend-release-checks-latest.json',
      blocking_steps: [],
    },
  ],
  next_action_count: 1,
  recommended_actions: [
    {
      key: 'candidate_env_file',
      label: 'Candidate production env file',
      status: 'fail',
      action: 'Materialize apps/backend/.env.prod, validate it, then rerun readiness.',
      owner: 'release_operator',
      requires_secret_update: true,
      production_required: true,
      source_count: 2,
      source_steps: ['release_gate', 'production_readiness_summary'],
      source_reports: ['production_readiness_summary'],
      verification_commands: [
        'python scripts/materialize_production_env.py --source-label sops:production --fail-on-error',
      ],
    },
  ],
  recommended_action_groups: [
    {
      key: 'release_preparation',
      label: '发布准备',
      status: 'fail',
      action_count: 1,
      source_count: 2,
      source_steps: ['release_gate', 'production_readiness_summary'],
      source_reports: ['production_readiness_summary'],
      has_more_actions: false,
      actions: [
        {
          key: 'candidate_env_file',
          label: 'Candidate production env file',
          status: 'fail',
          action: 'Materialize apps/backend/.env.prod, validate it, then rerun readiness.',
          owner: 'release_operator',
          requires_secret_update: true,
          production_required: true,
          source_count: 2,
          source_steps: ['release_gate', 'production_readiness_summary'],
          source_reports: ['production_readiness_summary'],
          verification_commands: [
            'python scripts/materialize_production_env.py --source-label sops:production --fail-on-error',
          ],
        },
      ],
    },
  ],
  operator_command:
    'python scripts/run_production_readiness_refresh.py --env-file apps/backend/.env.prod --fail-on-blocked',
})

assert.equal(blocked.tone, 'danger')
assert.equal(blocked.title, '证据刷新被阻断')
assert.equal(blocked.subtitle, '最近一次刷新使用了非标准候选配置，需改用 apps/backend/.env.prod。')
assert.equal(blocked.envFile, 'apps/backend/.env.prod.example')
assert.equal(blocked.expectedEnvFile, 'apps/backend/.env.prod')
assert.equal(blocked.actualEnvFile, 'apps/backend/.env.prod.example')
assert.deepEqual(blocked.blockingSteps, ['candidate_env_file'])
assert.deepEqual(JSON.parse(JSON.stringify(blocked.steps)), [
  {
    key: 'release_gate',
    label: '发布门禁',
    status: 'blocked',
    exitCode: 2,
    latestPath: 'docs/verification/release-gate-latest.json',
    blockingSteps: ['production_env_validation'],
  },
  {
    key: 'frontend_release_checks',
    label: '前端发布检查',
    status: 'pass',
    exitCode: 0,
    latestPath: 'docs/verification/frontend-release-checks-latest.json',
    blockingSteps: [],
  },
])
assert.deepEqual(JSON.parse(JSON.stringify(blocked.recommendedActions)), [
  {
    key: 'candidate_env_file',
    label: 'Candidate production env file',
    status: 'fail',
    action: 'Materialize apps/backend/.env.prod, validate it, then rerun readiness.',
    owner: 'release_operator',
    requiresSecretUpdate: true,
    productionRequired: true,
    sourceCount: 2,
    sourceSteps: ['release_gate', 'production_readiness_summary'],
    sourceReports: ['production_readiness_summary'],
    verificationCommands: [
      'python scripts/materialize_production_env.py --source-label sops:production --fail-on-error',
    ],
  },
])
assert.deepEqual(JSON.parse(JSON.stringify(blocked.recommendedActionGroups)), [
  {
    key: 'release_preparation',
    label: '发布准备',
    status: 'fail',
    actionCount: 1,
    sourceCount: 2,
    sourceSteps: ['release_gate', 'production_readiness_summary'],
    sourceReports: ['production_readiness_summary'],
    hasMoreActions: false,
    actions: [
      {
        key: 'candidate_env_file',
        label: 'Candidate production env file',
        status: 'fail',
        action: 'Materialize apps/backend/.env.prod, validate it, then rerun readiness.',
        owner: 'release_operator',
        requiresSecretUpdate: true,
        productionRequired: true,
        sourceCount: 2,
        sourceSteps: ['release_gate', 'production_readiness_summary'],
        sourceReports: ['production_readiness_summary'],
        verificationCommands: [
          'python scripts/materialize_production_env.py --source-label sops:production --fail-on-error',
        ],
      },
    ],
  },
])
assert.equal(
  blocked.operatorCommand,
  'python scripts/run_production_readiness_refresh.py --env-file apps/backend/.env.prod --fail-on-blocked',
)

const missing = buildProductionRefreshView(undefined)

assert.equal(missing.tone, 'warning')
assert.equal(missing.title, '证据刷新记录缺失')
assert.deepEqual(JSON.parse(JSON.stringify(missing.recommendedActions)), [])
assert.deepEqual(JSON.parse(JSON.stringify(missing.recommendedActionGroups)), [])
assert.equal(missing.operatorCommand, 'python scripts/run_production_readiness_refresh.py --env-file apps/backend/.env.prod --fail-on-blocked')

const ready = buildProductionRefreshView({
  available: true,
  status: 'pass',
  readiness_state: 'release_ready',
  release_candidate_ready: true,
  release_allowed: true,
  env_file: 'apps/backend/.env.prod',
  blocking_steps: [],
  operator_command:
    'python scripts/run_production_readiness_refresh.py --env-file apps/backend/.env.prod --fail-on-blocked',
})

assert.equal(ready.tone, 'success')
assert.equal(ready.title, '证据刷新可用')
assert.equal(ready.subtitle, '最近一次刷新来自标准候选配置。')
