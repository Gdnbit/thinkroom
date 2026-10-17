import { expect, test } from '@playwright/test'

const baseUrl = process.env.UI_ACCEPTANCE_BASE_URL || 'http://127.0.0.1:5173'
const sessionId = process.env.UI_ACCEPTANCE_SESSION_ID
const marker = process.env.UI_ACCEPTANCE_MARKER_A

function trustReceiptSummary(url: URL, traceSourceIds?: string[]) {
  const expectedSourceIds = url.searchParams.getAll('expected_source_ids').filter(Boolean)
  const sources = traceSourceIds ?? (expectedSourceIds.length ? expectedSourceIds : ['source-ui-1'])
  const sourceSet = new Set(sources)
  const coveredExpectedSourceIds = expectedSourceIds.filter((sourceId) => sourceSet.has(sourceId))
  const missingExpectedSourceIds = expectedSourceIds.filter((sourceId) => !sourceSet.has(sourceId))

  return {
    related_object_type: url.searchParams.get('related_object_type') ?? 'chat_message',
    related_object_id: url.searchParams.get('related_object_id') ?? '',
    event_count: 4,
    event_types: ['scope', 'retrieval', 'quality', 'llm_call'],
    trace_source_ids: sources,
    expected_source_ids: expectedSourceIds,
    covered_expected_source_ids: coveredExpectedSourceIds,
    missing_expected_source_ids: missingExpectedSourceIds,
    source_coverage_ratio: expectedSourceIds.length ? coveredExpectedSourceIds.length / expectedSourceIds.length : null,
    coverage_status: missingExpectedSourceIds.length ? 'missing_sources' : expectedSourceIds.length ? 'covered' : 'no_expected_sources',
    has_llm_trace: true,
    llm_call_count: 1,
    llm_source_ids: sources,
    models: ['mock-ui-model'],
    warning_count: 0,
    warning_event_types: [],
    total_duration_ms: 1000,
    total_cost_usd: 0,
  }
}

function isChatIngestionContextPath(path: string) {
  return /^\/api\/v1\/chat\/sessions\/[^/]+\/ingestion-context$/.test(path)
}

function emptyIngestionContext() {
  return { source_ids: [], job_ids: [], upload_receipts: [] }
}

test.describe('Decision chat main flow', () => {
  test('serves local shell assets without external startup font dependencies', async ({ request }) => {
    const htmlResponse = await request.get(`${baseUrl}/chat`)
    expect(htmlResponse.ok()).toBeTruthy()
    const html = await htmlResponse.text()
    expect(html).not.toContain('fonts.googleapis.com')
    expect(html).not.toContain('fonts.gstatic.com')

    const faviconResponse = await request.get(`${baseUrl}/favicon.svg`)
    expect(faviconResponse.ok()).toBeTruthy()
    expect(faviconResponse.headers()['content-type']).toContain('image/svg+xml')
    expect((await faviconResponse.text()).trimStart()).toMatch(/^<svg\b/)
  })

  test('visible navigation entries route to usable pages', async ({ page }) => {
    const consoleErrors: string[] = []
    page.on('console', (message) => {
      if (message.type() === 'error') consoleErrors.push(message.text())
    })
    page.on('pageerror', (error) => consoleErrors.push(error.message))

    await page.goto(`${baseUrl}/chat`)
    const nav = page.getByRole('navigation', { name: '主导航' })
    const main = page.locator('#main-content')

    const primaryRoutes = [
      { label: '问答', pattern: /\/chat/, title: '问答' },
      { label: '资料', pattern: /\/knowledge/, title: '资料' },
      { label: '复盘', pattern: /\/review/, title: '复盘' },
    ]

    for (const route of primaryRoutes) {
      await nav.getByRole('link', { name: route.label }).click()
      await expect(page).toHaveURL(route.pattern)
      await expect(main.getByRole('heading', { name: route.title, exact: true })).toBeVisible()
      await expect(main.getByText('Loading')).toHaveCount(0)
    }

    const expandSidebar = nav.getByRole('button', { name: '展开侧边栏' })
    if (await expandSidebar.isVisible()) await expandSidebar.click()
    await nav.getByRole('button', { name: '更多功能' }).click()
    const advancedGroups = [
      {
        group: '研究工具',
        routes: [
          { label: '观察池', pattern: /\/watchlist/, title: '观察池' },
          { label: '审议记录', pattern: /\/debate/, title: '审议记录' },
          { label: '来源画像', pattern: /\/speakers/, title: '来源画像' },
          { label: '策略候选库', pattern: /\/strategies/, title: '策略候选库' },
          { label: '策略回测', pattern: /\/backtest/, title: '策略回测' },
          { label: '基金配置', pattern: /\/fund-profiles/, title: '基金配置' },
          { label: '基金核对', pattern: /\/fund-reconciliation/, title: '基金核对' },
        ],
      },
      {
        group: '系统维护',
        routes: [
          { label: '证据审计', pattern: /\/audit/, title: '证据审计' },
          { label: '数据中心', pattern: /\/datahub/, title: '数据中心' },
          { label: '质量监控', pattern: /\/quality-monitor/, title: '质量监控' },
          { label: '质量评估', pattern: /\/quality-evaluator/, title: '质量评估' },
          { label: '验证中心', pattern: /\/verification$/, title: '验证中心' },
          { label: '数据流诊断', pattern: /\/verification\/dataflow/, title: '数据流诊断' },
          { label: '系统设置', pattern: /\/settings/, title: '系统设置' },
          { label: '旧工作台', pattern: /\/dashboard/, title: '旧工作台' },
          { label: '旧问答页', pattern: /\/legacy-chat/, title: '旧问答页' },
        ],
      },
    ]

    for (const group of advancedGroups) {
      await nav.getByRole('button', { name: group.group }).click()
      for (const route of group.routes) {
        await nav.getByRole('link', { name: route.label }).click()
        await expect(page).toHaveURL(route.pattern)
        await expect(main.getByRole('heading', { name: route.title, exact: true })).toBeVisible()
        await expect(main.getByText('Loading')).toHaveCount(0)
      }
    }

    expect(consoleErrors).toEqual([])
  })

  test('renders system status trace source-link consistency', async ({ page }) => {
    const consoleErrors: string[] = []
    const unhandledApiRequests: string[] = []
    let reaperCalls = 0
    page.on('console', (message) => {
      if (message.type() === 'error') consoleErrors.push(message.text())
    })
    page.on('pageerror', (error) => consoleErrors.push(error.message))

    await page.route('**/api/v1/**', async (route) => {
      const request = route.request()
      const url = new URL(request.url())
      const path = url.pathname
      const method = request.method()

      if (method === 'GET' && path === '/api/v1/verification/scenarios') {
        await route.fulfill({ json: [] })
        return
      }
      if (method === 'GET' && path === '/api/v1/alerts') {
        await route.fulfill({ json: { alerts: [], total: 0 } })
        return
      }
      if (method === 'GET' && path === '/api/v1/social-media/health') {
        await route.fulfill({ json: { success: true, data: { status: 'ok', enabled: false } } })
        return
      }
      if (method === 'GET' && path === '/api/v1/health') {
        await route.fulfill({
          json: {
            status: 'ok',
            version: '0.1.0',
            env: 'test',
            timestamp: '2026-07-04T00:00:00Z',
            capabilities: {
              document_parser: {
                requested_adapter: 'current',
                active_adapter: 'current',
                status: 'ready',
                input_modes: [
                  { kind: 'raw_text', status: 'supported' },
                  { kind: 'pdf', status: 'best_effort' },
                ],
                features: {
                  document_parse_summary: true,
                  progress_callback: true,
                  table_structure: false,
                  ocr_confidence: false,
                },
                notes: ['current parser adapter active'],
              },
            },
          },
        })
        return
      }
      if (method === 'GET' && path === '/api/v1/health/ready') {
        await route.fulfill({
          json: {
            status: 'degraded',
            checks: {
              database: { ok: true, latency_ms: 4, dialect: 'sqlite' },
              redis: { ok: false, latency_ms: 1, error: 'not configured' },
              minio: { ok: false, latency_ms: 2, error: 'not configured' },
              openrouter: { ok: true, latency_ms: 120, base_url: 'https://openrouter.ai/api/v1' },
            },
            deployment_profile: {
              mode: 'local_mvp',
              label: '本地 MVP',
              allows_degraded_optional_dependencies: true,
              required_checks: ['database', 'openrouter'],
              blocking_checks: [],
            },
            capabilities: {
              document_parser: {
                requested_adapter: 'current',
                active_adapter: 'current',
                status: 'ready',
                features: { document_parse_summary: true },
              },
            },
            timestamp: '2026-07-06T00:00:00Z',
          },
        })
        return
      }
      if (method === 'GET' && path === '/api/v1/health/release-readiness') {
        await route.fulfill({
          json: {
            status: 'warn',
            deployment_profile: {
              mode: 'local_mvp',
              label: '本地 MVP',
              required_checks: ['database', 'openrouter'],
              optional_checks: ['redis', 'minio'],
              blocking_checks: [],
            },
            summary: { pass: 2, warn: 7, fail: 0 },
            checks: [
              { key: 'runtime_readiness', label: '实时依赖', status: 'warn', detail: '实时依赖可用但存在降级项' },
              { key: 'runtime_profile', label: '部署 Profile', status: 'warn', detail: '本地 MVP 允许部分弱依赖降级' },
              {
                key: 'secrets',
                label: '密钥与认证',
                status: 'warn',
                detail: '存在本地空密钥或默认密钥',
                remediation: '将 APP_API_KEY 写入 SOPS/部署密钥并在启动脚本中注入；APP_SECRET_KEY 至少 32 字符且不能使用 dev-secret-key。',
                signals: {
                  secret_provenance: {
                    openrouter_api_key: { source: 'sops', set: true, default_like: false },
                    redis_url: { source: 'sops', set: true, default_like: false },
                    minio_access_key: { source: 'sops', set: true, default_like: false },
                    minio_secret_key: { source: 'sops', set: true, default_like: false },
                  },
                },
              },
              { key: 'shared_redis', label: '共享 Redis', status: 'warn', detail: '当前未启用 Redis，本地可降级，生产不可降级' },
              {
                key: 'object_storage',
                label: '对象存储',
                status: 'warn',
                detail: '对象存储仍为本地/默认配置',
                remediation: '本地演示先运行 scripts/start_local_dependencies.ps1，再用 start_backend_sops.ps1 -UseLocalDependencies 重启后端。',
                signals: {
                  provider: 'minio',
                  secure: true,
                  tls_required: true,
                  tls_policy_ok: true,
                  probe_ok: true,
                  production_posture_ok: false,
                  configuration_posture: 'local_default_credentials',
                  probe: { ok: true, provider: 'minio', secure: true, operation: 'write_read_delete' },
                },
              },
              { key: 'rate_limit_storage', label: '限流存储', status: 'warn', detail: '限流状态仍使用进程内存储' },
              {
                key: 'observability',
                label: '可观测性',
                status: 'warn',
                detail: '本地日志可用，生产建议启用 Sentry 与 JSON 日志',
                remediation: '生产环境设置 SENTRY_DSN 并设置 LOG_FORMAT=json；确认 /metrics 已暴露并纳入监控。',
                signals: {
                  production_posture_ok: false,
                  missing_for_production: ['structured_logs', 'error_tracking'],
                  deployment_sources: {
                    log_format: { source: 'default_or_missing', set: true, default_like: true },
                    sentry_dsn: { source: 'default_or_missing', set: false, default_like: true },
                  },
                  checks: {
                    metrics_endpoint: { status: 'pass', evidence: '/metrics', required_for_production: true },
                    request_correlation: { status: 'pass', evidence: 'X-Request-Id', required_for_production: true },
                    structured_logs: {
                      status: 'warn',
                      evidence: 'pretty',
                      source: 'default_or_missing',
                      required_for_production: true,
                    },
                    error_tracking: {
                      status: 'warn',
                      evidence: 'SENTRY_DSN empty',
                      source: 'default_or_missing',
                      required_for_production: true,
                    },
                  },
                },
              },
              { key: 'ingestion_queue', label: '入库队列', status: 'pass', detail: '当前没有活跃入库任务' },
              { key: 'document_parser', label: '资料解析能力', status: 'pass', detail: '当前解析适配器可用' },
            ],
            generated_at: '2026-07-06T00:00:00Z',
          },
        })
        return
      }
      if (method === 'GET' && path === '/api/v1/health/production-preflight') {
        await route.fulfill({
          json: {
            report_type: 'production_preflight',
            status: 'warn',
            acceptance: {
              local_demo_ready: true,
              production_ready: false,
              runtime_ready: true,
              release_status: 'warn',
            },
            summary: {
              release_readiness: { pass: 5, warn: 4, fail: 0 },
              blocker_count: 0,
              warning_count: 4,
              next_action_count: 3,
              public_api_leak_scan_status: 'pass',
              public_api_leak_scan_freshness: 'fresh',
            },
            decision_paths: {
              local_demo: {
                key: 'local_demo',
                label: '本地演示路径',
                status: 'ready',
                ready: true,
                required_action_count: 0,
                production_warning_count: 4,
                action_keys: [],
              },
              production_release: {
                key: 'production_release',
                label: '生产放行路径',
                status: 'needs_action',
                ready: false,
                blocker_count: 0,
                warning_count: 4,
                required_action_count: 3,
                action_keys: ['secrets', 'observability', 'object_storage'],
              },
            },
            next_actions: [
              {
                key: 'secrets',
                label: '密钥与认证',
                status: 'warn',
                action: '将 APP_API_KEY 写入 SOPS/部署密钥并在启动脚本中注入。',
                owner: 'deployment_operator',
                can_auto_fix: false,
                requires_secret_update: true,
                production_required: true,
                local_demo_blocking: false,
                config_keys: ['APP_API_KEY', 'APP_SECRET_KEY', 'OPENROUTER_API_KEY'],
                verification: [
                  { kind: 'endpoint', target: '/api/v1/health/production-secrets-tls', expectation: 'status=pass' },
                ],
              },
              {
                key: 'observability',
                label: '可观测性',
                status: 'warn',
                action: '生产环境设置 SENTRY_DSN 并设置 LOG_FORMAT=json。',
                owner: 'deployment_operator',
                can_auto_fix: false,
                requires_secret_update: true,
                production_required: true,
                local_demo_blocking: false,
                config_keys: ['LOG_FORMAT', 'SENTRY_DSN'],
                verification: [
                  { kind: 'endpoint', target: '/api/v1/health/release-readiness', expectation: 'checks.observability.status=pass' },
                ],
              },
            ],
            evidence_sources: {
              ready_endpoint: '/api/v1/health/ready',
              release_readiness_endpoint: '/api/v1/health/release-readiness',
              local_dependency_report_path: 'docs/verification/local-dependencies-latest.json',
              public_api_leak_scan_report_path: 'docs/verification/public-api-leak-scan-latest.json',
            },
            production_dry_run: {
              report_type: 'production_profile_dry_run',
              status: 'fail',
              mutates_active_settings: false,
              active_env: 'development',
              simulated_env: 'production',
              runtime_dependency_blockers: [],
              production_config_errors: ['APP_API_KEY must be set in production'],
            },
            production_secrets_tls: {
              report_type: 'production_secrets_tls_checklist',
              status: 'fail',
              summary: { pass: 2, warn: 0, fail: 4 },
              secret_items: [
                { key: 'APP_API_KEY', status: 'fail', set: false, source: 'default_or_missing', required_for_production: true },
                { key: 'APP_SECRET_KEY', status: 'fail', set: true, source: 'default_or_missing', required_for_production: true },
                { key: 'OPENROUTER_API_KEY', status: 'pass', set: true, source: 'sops', required_for_production: true },
                { key: 'MINIO_ACCESS_KEY', status: 'fail', set: true, source: 'default_or_missing', required_for_production: true },
                { key: 'MINIO_SECRET_KEY', status: 'fail', set: true, source: 'default_or_missing', required_for_production: true },
              ],
              tls_controls: [
                { key: 'MINIO_SECURE', status: 'fail', required_for_production: true },
                { key: 'ALLOW_INSECURE_OBJECT_STORAGE_IN_PRODUCTION', status: 'pass', required_for_production: false },
              ],
            },
            public_api_leak_scan: {
              available: true,
              status: 'pass',
              fresh: true,
              freshness_status: 'fresh',
              age_seconds: 120,
              max_age_seconds: 86400,
              leak_count: 0,
              endpoint_count: 6,
              summary: { pass: 6, warn: 0, fail: 0 },
            },
            local_dependencies: { status: 'passed' },
            generated_at: '2026-07-08T00:00:00Z',
          },
        })
        return
      }
      if (method === 'GET' && path === '/api/v1/decision-traces/consistency') {
        await route.fulfill({
          json: {
            status: 'ok',
            dialect: 'sqlite',
            trace_event_count: 12,
            event_source_pair_count: 10,
            link_count: 10,
            missing_link_count: 0,
            orphan_link_count: 0,
            sample_missing_links: [],
            sample_orphan_links: [],
          },
        })
        return
      }
      if (method === 'GET' && path === '/api/v1/decision-traces/eval-review-manifest') {
        await route.fulfill({
          json: {
            report_type: 'trace_eval_review_manifest',
            status: 'review_required',
            generated_at: '2026-07-05T00:00:00Z',
            source: 'decision_trace_events',
            non_destructive: true,
            main_manifest_mutated: false,
            total_candidates: 2,
            pending_candidates: 1,
            covered_candidates: 1,
            reviewed_candidates: 0,
            filtered_candidates: 2,
            returned_candidates: 2,
            limit: 200,
            offset: 0,
            candidate_scan_limit: 1000,
            filters: {
              review_status: null,
              workflow: null,
              query: null,
            },
            status_counts: {
              pending_review: 1,
              already_covered: 1,
            },
            workflow_counts: {
              chat: 1,
              committee: 1,
            },
            main_manifest_coverage_loaded: true,
            main_manifest_coverage_error: null,
            review_policy: [
              'do not auto-promote generated trace candidates',
              'verify fixture scope and evidence before adding to the main workflow eval manifest',
            ],
            candidates: [
              {
                review_status: 'pending_review',
                recommended_next_step: 'review_before_promoting_to_main_manifest',
                candidate: {
                  id: 'trace-chat-ui-system-status',
                  workflow: 'chat',
                  mode: 'deterministic',
                  coverage_status: 'generated_from_trace',
                  trigger_reasons: ['quality:available_citations_not_used'],
                  trace_event_count: 4,
                },
              },
              {
                review_status: 'already_covered',
                recommended_next_step: 'no_action_required_trace_already_in_main_manifest',
                main_manifest_case_id: 'trace-debate-ui-covered',
                candidate: {
                  id: 'trace-debate-ui-covered',
                  workflow: 'committee',
                  mode: 'deterministic',
                  coverage_status: 'generated_from_trace',
                  trigger_reasons: ['guardrail:blocked_output'],
                  trace_event_count: 6,
                },
              },
            ],
          },
        })
        return
      }
      if (method === 'GET' && path === '/api/v1/datasources/list') {
        await route.fulfill({ json: [] })
        return
      }
      if (method === 'GET' && path === '/api/v1/costs/overview') {
        await route.fulfill({
          json: {
            today_cost: 0,
            month_cost: 0,
            all_time_cost_usd: 0,
            today_calls: 0,
            month_calls: 0,
            all_time_calls: 0,
          },
        })
        return
      }
      if (method === 'GET' && path === '/api/v1/progress/active') {
        await route.fulfill({ json: [] })
        return
      }
      if (method === 'GET' && path === '/api/v1/ingestion/dispatcher/status') {
        await route.fulfill({
          json: {
            dispatcher: 'bounded_thread_pool',
            max_workers: 2,
            active_or_queued_count: 1,
            persisted_active_count: 1,
            persisted_active_job_ids: ['job-ui-stale'],
            persisted_active_jobs: [
              {
                job_id: 'job-ui-stale',
                source_id: 'source-ui-stale',
                status: 'running',
                stage: 'parse',
                pct: 40,
                message: 'parsing stale ui source',
                retry_count: 1,
                lease_state: 'running',
                lease_worker_id: 'worker-ui',
                lease_heartbeat_at: '2026-07-04T00:00:00Z',
                lease_expires_at: '2026-07-04T00:10:00Z',
                lease_seconds: 600,
                lease_expired: true,
              },
            ],
          },
        })
        return
      }
      if (method === 'POST' && path === '/api/v1/ingestion/dispatcher/reap-stale') {
        reaperCalls += 1
        await route.fulfill({
          json: {
            checked: 1,
            stale: 1,
            recoverable: 1,
            resumed: 1,
            closed: 0,
            failed: 0,
            dead_lettered: 0,
            jobs: [{ job_id: 'job-ui-stale', source_id: 'source-ui-stale', action: 'resumed' }],
          },
        })
        return
      }
      if (method === 'GET' && path === '/api/v1/datasources/reminders') {
        await route.fulfill({ json: [] })
        return
      }
      if (method === 'GET' && path === '/api/v1/datasources/quota') {
        await route.fulfill({ json: [] })
        return
      }
      if (method === 'GET' && path === '/api/v1/settings/model-routing') {
        await route.fulfill({ json: [] })
        return
      }
      if (method === 'GET' && path === '/api/v1/settings/features') {
        await route.fulfill({ json: [] })
        return
      }

      if (method === 'GET' && isChatIngestionContextPath(path)) {
        await route.fulfill({ json: emptyIngestionContext() })
        return
      }
      if (method === 'POST' && path === '/api/v1/ingestion/status/batch') {
        await route.fulfill({ json: [] })
        return
      }

      unhandledApiRequests.push(`${method} ${path}`)
      await route.fulfill({ status: 404, json: { detail: `Unhandled UI acceptance route: ${method} ${path}` } })
    })

    await page.goto(`${baseUrl}/verification`)
    await page.getByRole('button', { name: '系统状态' }).click()

    await expect(page.getByText('资料解析能力正常')).toBeVisible()
    await expect(page.getByText('核心可用，弱依赖降级')).toBeVisible()
    await expect(page.getByText('部署 Profile：本地 MVP')).toBeVisible()
    await expect(page.getByText('发布就绪需处理')).toBeVisible()
    await expect(page.getByText('/api/v1/health/release-readiness', { exact: true })).toBeVisible()
    await expect(page.getByText('实时依赖', { exact: true })).toBeVisible()
    await expect(page.getByText('密钥与认证', { exact: true })).toBeVisible()
    await expect(page.getByText('OpenRouter: sops')).toBeVisible()
    await expect(page.getByText('Redis: sops')).toBeVisible()
    await expect(page.getByText('MinIO: sops')).toBeVisible()
    await expect(page.getByText('限流存储')).toBeVisible()
    await expect(page.getByText('对象存储: minio')).toBeVisible()
    await expect(page.getByText('TLS: enabled')).toBeVisible()
    await expect(page.getByText('TLS 策略: ok')).toBeVisible()
    await expect(page.getByText('探针: ok')).toBeVisible()
    await expect(page.getByText('配置: local-defaults')).toBeVisible()
    await expect(page.getByText('下一步：本地演示先运行 scripts/start_local_dependencies.ps1')).toBeVisible()
    await expect(page.getByText('下一步：将 APP_API_KEY 写入 SOPS/部署密钥')).toBeVisible()
    await expect(page.locator('p[title*="APP_SECRET_KEY 至少 32 字符"]')).toHaveCount(1)
    await expect(page.getByText('下一步：生产环境设置 SENTRY_DSN 并设置 LOG_FORMAT=json')).toBeVisible()
    await expect(page.locator('p[title*="/metrics 已暴露"]')).toHaveCount(1)
    await expect(page.getByText('Metrics: pass')).toBeVisible()
    await expect(page.getByText('Request ID: pass')).toBeVisible()
    await expect(page.getByText('JSON 日志: warn')).toBeVisible()
    await expect(page.getByText('错误上报: warn')).toBeVisible()
    await expect(page.getByText('LOG_FORMAT 来源: default_or_missing')).toBeVisible()
    await expect(page.getByText('Sentry 来源: default_or_missing')).toBeVisible()
    await expect(page.getByText('观测缺口: structured_logs, error_tracking')).toBeVisible()
    await expect(page.getByText('读写探针: write_read_delete')).toBeVisible()
    await expect(page.getByText('Redis', { exact: true })).toBeVisible()
    await expect(page.getByText('MinIO', { exact: true })).toBeVisible()
    await expect(page.getByText('生产预检')).toBeVisible()
    await expect(page.getByText('本地演示可用')).toBeVisible()
    await expect(page.getByText('生产未放行')).toBeVisible()
    await expect(page.getByText('演示处理 0')).toBeVisible()
    await expect(page.getByText('生产待处理')).toBeVisible()
    await expect(page.getByText('生产处理 3')).toBeVisible()
    await expect(page.getByText('生产干跑: fail')).toBeVisible()
    await expect(page.getByText('密钥/TLS: fail')).toBeVisible()
    await expect(page.getByText('密钥/TLS 阻断 4')).toBeVisible()
    await expect(page.getByText('公开 API: pass')).toBeVisible()
    await expect(page.getByText('扫描时效: fresh')).toBeVisible()
    await expect(page.getByText('泄露 0')).toBeVisible()
    await expect(page.getByText('不改当前配置')).toBeVisible()
    await expect(page.getByText('/api/v1/health/production-preflight')).toBeVisible()
    await expect(page.getByText('密钥与认证：将 APP_API_KEY 写入 SOPS/部署密钥')).toBeVisible()
    await expect(page.getByText('deployment_operator · 需人工 · 验收 /api/v1/health/production-secrets-tls')).toBeVisible()
    await expect(page.getByText('APP_API_KEY, APP_SECRET_KEY, OPENROUTER_API_KEY')).toBeVisible()
    await expect(page.getByText('adapter current → current')).toBeVisible()
    await expect(page.getByText('解析摘要')).toBeVisible()
    await expect(page.getByText('表格结构')).toBeVisible()
    await expect(page.getByText('OCR 置信度')).toBeVisible()
    await expect(page.getByText('审议来源链完整')).toBeVisible()
    await expect(page.getByText('/api/v1/decision-traces/consistency')).toBeVisible()
    await expect(page.getByText('审议事件')).toBeVisible()
    await expect(page.getByText('来源对')).toBeVisible()
    await expect(page.getByText('关联行')).toBeVisible()
    await expect(page.getByText('缺失', { exact: true })).toBeVisible()
    await expect(page.getByText('孤儿')).toBeVisible()
    await expect(page.getByText('12', { exact: true })).toBeVisible()
    await expect(page.getByText('10', { exact: true })).toHaveCount(2)
    await expect(page.getByText('Trace 评测审查队列')).toBeVisible()
    await expect(page.getByText('trace-chat-ui-system-status')).toBeVisible()
    await expect(page.getByText('显示 2 / 2，队列 2')).toBeVisible()
    await expect(page.getByText('入库队列')).toHaveCount(2)
    await expect(page.getByText('job-ui-stale')).toBeVisible()
    await expect(page.getByText('source source-ui-stale')).toBeVisible()
    await expect(page.getByText('lease 过期')).toBeVisible()
    await page.getByRole('button', { name: '恢复过期' }).click()
    await expect(page.getByText('最近恢复：检查 1，过期 1，恢复 1，失败 0。')).toBeVisible()
    expect(reaperCalls).toBe(1)
    expect(unhandledApiRequests).toEqual([])
    expect(consoleErrors).toEqual([])
  })

  test('renders committee detail trust receipt and selected source scope', async ({ page }) => {
    const consoleErrors: string[] = []
    page.on('console', (message) => {
      if (message.type() === 'error') consoleErrors.push(message.text())
    })
    page.on('pageerror', (error) => consoleErrors.push(error.message))

    const debateId = 'debate-trust-1'
    await page.route('**/api/v1/committee**', async (route) => {
      const url = new URL(route.request().url())
      if (url.pathname.endsWith(`/api/v1/committee/${debateId}`)) {
        await route.fulfill({
          contentType: 'application/json',
          body: JSON.stringify({
            id: debateId,
            question: '只根据选定资料判断下周铜价会不会上涨。',
            asset: 'GENERAL',
            committee_level: 'triple',
            status: 'completed',
            final_direction: 'neutral',
            final_conviction: 0,
            final_action: 'watch',
            consensus_level: 'no_consensus',
            created_at: '2026-06-24T10:00:00Z',
            duration_ms: 812,
            rounds: [],
            adjudication: {
              id: 'adj-1',
              direction: 'neutral',
              conviction: 0,
              recommended_action: 'watch',
              consensus_level: 'no_consensus',
              synthesis: '选定资料不足以支持委员会作出方向性裁决，因此本次保持观察。',
              bull_score: 0.5,
              bear_score: 0.5,
              baseline_score: 0.5,
              probability_score: 0.5,
              risk_score: 0.5,
              structure_score: 0,
              structure_warnings: [],
              risk_warnings: ['选定资料缺少方向性证据'],
              hedging_advice: '等待补充资料后再审议。',
              action_economics: {
                decision_guardrail: {
                  status: 'blocked',
                  effective_action: 'watch',
                  reasons: ['insufficient_evidence'],
                },
              },
              evidence_quality_warnings: ['选定资料缺少方向性证据'],
              key_agreements: [],
              key_disagreements: [],
              invalidation_conditions: ['补充方向性资料后重新运行委员会。'],
              adjudicated_by: 'not_called_insufficient_evidence',
              is_mock: false,
            },
            decision_guardrail: {
              status: 'blocked',
              effective_action: 'watch',
              reasons: ['insufficient_evidence'],
            },
            quality_filters: {
              evidence_sufficiency: 'insufficient',
              insufficient_evidence_reason: 'selected_sources_actionable_question_requires_directional_evidence',
            },
            claims_used: 0,
            source_claim_ids: [],
            scope: {
              mode: 'selected_sources',
              source_ids: ['source-copper-background'],
              asset: 'GENERAL',
            },
            llm_trace: {
              call_count: 0,
              total_cost_usd: 0,
              calls: [],
              model: 'not_called_insufficient_evidence',
              error_type: 'insufficient_evidence',
            },
            cost_usd: 0,
            llm_call_count: 0,
          }),
        })
        return
      }
      if (url.pathname.endsWith('/api/v1/committee')) {
        await route.fulfill({
          contentType: 'application/json',
          body: JSON.stringify({
            debates: [{
              id: debateId,
              question: '只根据选定资料判断下周铜价会不会上涨。',
              asset: 'GENERAL',
              committee_level: 'triple',
              status: 'completed',
              final_direction: 'neutral',
              final_conviction: 0,
              final_action: 'watch',
              consensus_level: 'no_consensus',
              created_at: '2026-06-24T10:00:00Z',
            }],
            total: 1,
            has_more: false,
          }),
        })
        return
      }
      await route.continue()
    })
    await page.route('**/api/v1/decision-traces**', async (route) => {
      const url = new URL(route.request().url())
      if (url.pathname === '/api/v1/decision-traces/trust-receipt') {
        await route.fulfill({ json: trustReceiptSummary(url, ['source-copper-background']) })
        return
      }
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          count: 1,
          items: [
            {
              id: 'trace-debate-source-1',
              related_object_type: 'debate',
              related_object_id: debateId,
              run_id: 'run-debate-source-1',
              session_id: null,
              job_id: null,
              event_type: 'retrieval',
              step_name: 'selected_source_scope',
              status: 'ok',
              model: null,
              skill_role: null,
              error_type: null,
              duration_ms: 20,
              cost_usd: 0,
              source_ids: ['source-copper-background'],
              payload: {},
              created_at: '2026-06-24T10:00:00Z',
              updated_at: '2026-06-24T10:00:00Z',
            },
          ],
        }),
      })
    })

    await page.goto(`${baseUrl}/debate`)
    await page.getByRole('button', { name: /只根据选定资料判断下周铜价/ }).click()

    const receiptButton = page.getByRole('button', { name: /选定资料不足.*详情/ })
    await expect(receiptButton).toBeVisible()
    await expect(page.getByText('资料范围')).toBeVisible()
    await expect(page.getByText('限定 1 个资料源')).toBeVisible()
    await expect(page.getByText('引用观点 0 条')).toBeVisible()

    await receiptButton.click()
    await expect(page.getByText('模型调用', { exact: true })).toBeVisible()
    await expect(page.getByText('0 次调用')).toBeVisible()
    await expect(page.getByText('有效建议：watch')).toBeVisible()
    await expect(page.getByText('系统已停止模型生成方向性判断，并返回保守提示。')).toBeVisible()
    await expect(page.getByText('来源链')).toBeVisible()
    await expect(page.getByText('资料范围 1/1 已被 trace 覆盖')).toBeVisible()

    expect(consoleErrors).toEqual([])
  })

  test('keeps pending state scoped while creating sessions and rendering answer evidence', async ({ page }) => {
    const consoleErrors: string[] = []
    const unhandledApiRequests: string[] = []
    page.on('console', (message) => {
      if (message.type() === 'error') consoleErrors.push(message.text())
    })
    page.on('pageerror', (error) => consoleErrors.push(error.message))

    const now = '2026-06-22T04:00:00.000Z'
    const sessions = [
      {
        id: 'ui-session-a',
        title: '第一会话',
        mode: 'auto',
        asset: 'GENERAL',
        updated_at: now,
        message_count: 0,
      },
    ]
    const messagesBySession = new Map<string, any[]>([
      ['ui-session-a', []],
    ])
    let createdSessionCount = 0
    let releaseChatResponse: (() => void) | undefined
    let markChatRequestStarted: (() => void) | undefined
    const chatRequestStarted = new Promise<void>((resolve) => {
      markChatRequestStarted = resolve
    })
    const chatCanFinish = new Promise<void>((resolve) => {
      releaseChatResponse = resolve
    })

    const assistantPayload = {
      mode: 'chat',
      claims_used: 1,
      model_used: 'mock-ui-model',
      cost_usd: 0.001,
      total_cost_usd: 0.003,
      citations: [
        {
          index: 1,
          claim_id: 'claim-ui-1',
          source_id: 'source-ui-1',
          stance: 'neutral',
          summary: 'UI 主流程验收需要证据引用和 trace 展开。',
          short_quote: 'UI 主流程验收证据 quote。',
          trust_weight: 0.82,
          confidence: 0.91,
        },
      ],
      quality_report: {
        run_id: 'ui-run-1',
        generated_at: now,
        reference_time: '2026年06月22日 12:00:00 CST',
        duration_ms: 1234,
        decision_ready: false,
        severity: 'warning',
        warnings: [
          { code: 'uncited_material_claim', message: '有 1 个关键判断句未标注引用来源，需要补充引用或人工复核。', severity: 'warning' },
        ],
        evidence: {
            citation_count: 1,
            used_citation_count: 1,
            unused_citation_count: 0,
            uncited_material_claim_count: 1,
            uncited_material_claims: ['因此下周可以加仓原油多单'],
            field_evidence: {
              direction: { status: 'supported', claim_ids: ['claim-ui-1'], source_ids: ['source-ui-1'] },
              recommended_action: { status: 'insufficient', claim_ids: [], source_ids: ['source-ui-1'] },
            },
            evidence_span_count: 1,
            source_fallback_count: 0,
          invalid_citation_count: 0,
          stance_mismatch_count: 0,
        },
        scope: {
          mode: 'selected_sources',
          source_count: 1,
          matched_source_count: 1,
        },
      },
      decision_guardrail: {
        status: 'passed',
        reasons: [],
      },
      llm_trace: {
        call_count: 1,
        total_cost_usd: 0.003,
        calls: [
          {
            model: 'mock-ui-model',
            requested_model: 'primary-ui-model',
            configured_fallback_models: ['fallback-ui-model', 'emergency-ui-model'],
            fallback_chain: ['primary-ui-model', 'mock-ui-model'],
            skill_role: 'fusion',
            duration_ms: 1000,
            cost_usd: 0.001,
          },
        ],
      },
      scope: {
        mode: 'selected_sources',
        source_ids: ['source-ui-1'],
        resolved_from: 'ui_acceptance',
      },
      quality_filters: {
        exclude_expired: true,
        exclude_contradicted: true,
      },
    }

    await page.route('**/api/v1/**', async (route) => {
      const request = route.request()
      const url = new URL(request.url())
      const path = url.pathname
      const method = request.method()

      if (method === 'GET' && path === '/api/v1/chat/sessions') {
        await route.fulfill({ json: sessions })
        return
      }

      if (method === 'GET' && path === '/api/v1/alerts') {
        await route.fulfill({ json: { alerts: [], total: 0 } })
        return
      }

      if (method === 'GET' && path === '/api/v1/social-media/health') {
        await route.fulfill({ json: { success: true, data: { status: 'ok', enabled: false } } })
        return
      }

      if (method === 'GET' && isChatIngestionContextPath(path)) {
        await route.fulfill({ json: emptyIngestionContext() })
        return
      }

      if (method === 'POST' && path === '/api/v1/chat/sessions') {
        createdSessionCount += 1
        const created = {
          id: `ui-session-created-${createdSessionCount}`,
          title: '新的决策问答',
          mode: 'auto',
          asset: 'GENERAL',
          updated_at: now,
          message_count: 0,
        }
        sessions.unshift(created)
        messagesBySession.set(created.id, [])
        await route.fulfill({ json: created })
        return
      }

      const messageMatch = path.match(/^\/api\/v1\/chat\/sessions\/([^/]+)\/messages$/)
      if (method === 'GET' && messageMatch) {
        await route.fulfill({ json: messagesBySession.get(messageMatch[1]) ?? [] })
        return
      }

      const chatMatch = path.match(/^\/api\/v1\/chat\/sessions\/([^/]+)\/chat$/)
      if (method === 'POST' && chatMatch) {
        const sessionId = chatMatch[1]
        const body = request.postDataJSON() as { message?: string }
        markChatRequestStarted?.()
        await chatCanFinish
        const userMessage = {
          id: 'ui-user-message-1',
          role: 'user',
          content: body.message ?? '',
          payload: {},
          created_at: now,
        }
        const assistantMessage = {
          id: 'ui-assistant-message-1',
          role: 'assistant',
          content: JSON.stringify({
            rationale: ['引用证据显示 UI 主流程验收需要保留 trace 展开。'],
            final_answer: 'UI 主流程回答已经基于引用证据生成，并且可以展开 trace [1]。',
            risk_warnings: ['如果直接展示 JSON 字段，会降低用户对系统成熟度的信任。'],
          }),
          payload: assistantPayload,
          created_at: now,
        }
        messagesBySession.set(sessionId, [userMessage, assistantMessage])
        const session = sessions.find((item) => item.id === sessionId)
        if (session) {
          session.message_count = 2
          session.updated_at = now
        }
        await route.fulfill({
          json: {
            id: assistantMessage.id,
            content: assistantMessage.content,
            ...assistantPayload,
          },
        })
        return
      }

      if (method === 'GET' && path === '/api/v1/decision-traces/trust-receipt') {
        await route.fulfill({ json: trustReceiptSummary(url, ['source-ui-1']) })
        return
      }

      if (method === 'GET' && path === '/api/v1/decision-traces/trust-receipt') {
        await route.fulfill({ json: trustReceiptSummary(url, ['source-unrelated-1']) })
        return
      }

      if (method === 'GET' && path === '/api/v1/decision-traces/trust-receipt') {
        await route.fulfill({ json: trustReceiptSummary(url, ['source-unrelated-1']) })
        return
      }

      if (method === 'GET' && path === '/api/v1/decision-traces') {
        await route.fulfill({
          json: {
            count: 4,
            items: [
              {
                id: 'trace-scope',
                related_object_type: 'chat_message',
                related_object_id: 'ui-assistant-message-1',
                event_type: 'scope',
                step_name: 'source_scope',
                status: 'ok',
                source_ids: [],
                payload: {},
              },
              {
                id: 'trace-retrieval',
                related_object_type: 'chat_message',
                related_object_id: 'ui-assistant-message-1',
                event_type: 'retrieval',
                step_name: 'chat_context',
                status: 'ok',
                source_ids: [],
                payload: {},
              },
              {
                id: 'trace-quality',
                related_object_type: 'chat_message',
                related_object_id: 'ui-assistant-message-1',
                event_type: 'quality',
                step_name: 'quality_report',
                status: 'ok',
                source_ids: [],
                payload: {},
              },
              {
                id: 'trace-llm',
                related_object_type: 'chat_message',
                related_object_id: 'ui-assistant-message-1',
                event_type: 'llm_call',
                step_name: 'fusion',
                status: 'ok',
                model: 'mock-ui-model',
                skill_role: 'fusion',
                duration_ms: 1000,
                source_ids: [],
                payload: {},
              },
            ],
          },
        })
        return
      }

      if (method === 'GET' && isChatIngestionContextPath(path)) {
        await route.fulfill({ json: emptyIngestionContext() })
        return
      }
      if (method === 'POST' && path === '/api/v1/ingestion/status/batch') {
        await route.fulfill({ json: [] })
        return
      }

      unhandledApiRequests.push(`${method} ${path}`)
      await route.fulfill({ status: 404, json: { detail: `Unhandled UI acceptance route: ${method} ${path}` } })
    })

    await page.goto(`${baseUrl}/chat?session=ui-session-a`)

    await expect(page.getByRole('heading', { name: '问答', exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: /第一会话/ })).toBeVisible()
    await page.getByPlaceholder('输入问题，或把文件拖到这里后再提问...').fill('请验证主流程状态隔离')
    await page.getByRole('button', { name: '发送' }).click()
    await chatRequestStarted

    await expect(page.getByText('请验证主流程状态隔离')).toBeVisible()
    await expect(page.getByText(/正在生成普通问答/)).toBeVisible()
    await expect(page.getByText('接收问题')).toBeVisible()

    await page.getByRole('button', { name: '新建会话' }).click()
    await expect(page.getByText(/正在生成普通问答/)).toHaveCount(0)
    await expect(page.getByText('请验证主流程状态隔离')).toHaveCount(0)
    await expect(page.getByRole('button', { name: '简报生成' })).toBeEnabled()
    await expect(page.getByRole('button', { name: '委员会讨论' })).toBeEnabled()

    releaseChatResponse?.()
    await page.getByRole('button', { name: /第一会话/ }).click()
    await expect(page.getByRole('heading', { name: '结论' })).toBeVisible()
    await expect(page.getByText('UI 主流程回答已经基于引用证据生成')).toBeVisible()
    await expect(page.getByText('引用证据显示 UI 主流程验收需要保留 trace 展开。')).toBeVisible()
    await expect(page.getByText(/final_answer|rationale|risk_warnings/)).toHaveCount(0)
    const citationButton = page.getByRole('button', { name: /参考资料 1 条/ })
    await expect(citationButton).toBeVisible()
    await expect(page.getByText('需要复核')).toBeVisible()
    await expect(page.getByText('~$0.00300')).toHaveCount(0)
    await expect(page.getByText('mock-ui-model')).toHaveCount(0)
    await expect(page.getByText('持久化追踪')).toHaveCount(0)
    await expect(page.getByText('因此下周可以加仓原油多单')).toHaveCount(0)
    await citationButton.click()
    await expect(page.getByText('UI 主流程验收证据 quote')).toBeVisible()
    expect(unhandledApiRequests).toEqual([])
    expect(consoleErrors).toEqual([])
  })

  test('renders insufficient selected evidence as a cautious outcome', async ({ page }) => {
    const consoleErrors: string[] = []
    const unhandledApiRequests: string[] = []
    page.on('console', (message) => {
      if (message.type() === 'error') consoleErrors.push(message.text())
    })
    page.on('pageerror', (error) => consoleErrors.push(error.message))

    const now = '2026-06-22T04:00:00.000Z'
    const sessions = [
      {
        id: 'ui-insufficient-session',
        title: '证据不足会话',
        mode: 'auto',
        asset: 'GENERAL',
        updated_at: now,
        message_count: 0,
      },
    ]
    const messagesBySession = new Map<string, any[]>([
      ['ui-insufficient-session', []],
    ])

    await page.route('**/api/v1/**', async (route) => {
      const request = route.request()
      const url = new URL(request.url())
      const path = url.pathname
      const method = request.method()

      if (method === 'GET' && path === '/api/v1/chat/sessions') {
        await route.fulfill({ json: sessions })
        return
      }

      if (method === 'GET' && path === '/api/v1/alerts') {
        await route.fulfill({ json: { alerts: [], total: 0 } })
        return
      }

      if (method === 'GET' && path === '/api/v1/social-media/health') {
        await route.fulfill({ json: { success: true, data: { status: 'ok', enabled: false } } })
        return
      }

      if (method === 'GET' && path === '/api/v1/decision-traces/trust-receipt') {
        await route.fulfill({ json: trustReceiptSummary(url, ['source-unrelated-1']) })
        return
      }

      if (method === 'GET' && path === '/api/v1/decision-traces') {
        await route.fulfill({
          json: {
            count: 1,
            items: [
              {
                id: 'trace-insufficient-scope-1',
                related_object_type: 'chat_message',
                related_object_id: 'ui-insufficient-assistant',
                run_id: 'ui-insufficient-run',
                session_id: 'ui-insufficient-session',
                job_id: null,
                event_type: 'retrieval',
                step_name: 'selected_source_scope',
                status: 'ok',
                model: null,
                skill_role: null,
                error_type: null,
                duration_ms: 12,
                cost_usd: 0,
                source_ids: ['source-unrelated-1'],
                payload: {},
                created_at: now,
                updated_at: now,
              },
            ],
          },
        })
        return
      }

      const messageMatch = path.match(/^\/api\/v1\/chat\/sessions\/([^/]+)\/messages$/)
      if (method === 'GET' && messageMatch) {
        await route.fulfill({ json: messagesBySession.get(messageMatch[1]) ?? [] })
        return
      }

      const chatMatch = path.match(/^\/api\/v1\/chat\/sessions\/([^/]+)\/chat$/)
      if (method === 'POST' && chatMatch) {
        const sessionId = chatMatch[1]
        const body = request.postDataJSON() as { message?: string }
        const userMessage = {
          id: 'ui-insufficient-user',
          role: 'user',
          content: body.message ?? '',
          payload: {},
          created_at: now,
        }
        const assistantPayload = {
          mode: 'chat',
          claims_used: 0,
          model_used: 'not_called_insufficient_evidence',
          citations: [],
          quality_filters: {
            evidence_sufficiency: 'insufficient',
            insufficient_evidence_reason: 'selected_sources_query_mismatch',
          },
          quality_report: {
            run_id: 'ui-insufficient-run',
            generated_at: now,
            reference_time: '2026年06月22日 12:00:00 CST',
            duration_ms: 12,
            decision_ready: false,
            severity: 'warning',
            warnings: [{ message: '选定资料不足，不能给出方向性结论。' }],
            evidence: {
              citation_count: 0,
              evidence_span_count: 0,
              source_fallback_count: 0,
              invalid_citation_count: 0,
              stance_mismatch_count: 0,
            },
            scope: {
              mode: 'selected_sources',
              source_count: 1,
              matched_source_count: 1,
            },
          },
          llm_trace: {
            call_count: 0,
            model: 'not_called_insufficient_evidence',
            error_type: 'insufficient_evidence',
          },
          scope: {
            mode: 'selected_sources',
            source_ids: ['source-unrelated-1'],
            resolved_from: 'ui_acceptance',
          },
          reference_time: '2026年06月22日 12:00:00 CST',
        }
        const assistantMessage = {
          id: 'ui-insufficient-assistant',
          role: 'assistant',
          content: '截至 2026年06月22日 12:00:00 CST，选定资料不足以支持该问题的判断，因此不能仅凭这些资料给出方向性结论或交易建议。',
          payload: assistantPayload,
          created_at: now,
        }
        messagesBySession.set(sessionId, [userMessage, assistantMessage])
        await route.fulfill({
          json: {
            id: assistantMessage.id,
            content: assistantMessage.content,
            ...assistantPayload,
          },
        })
        return
      }

      if (method === 'GET' && isChatIngestionContextPath(path)) {
        await route.fulfill({ json: emptyIngestionContext() })
        return
      }
      if (method === 'POST' && path === '/api/v1/ingestion/status/batch') {
        await route.fulfill({ json: [] })
        return
      }

      unhandledApiRequests.push(`${method} ${path}`)
      await route.fulfill({ status: 404, json: { detail: `Unhandled UI acceptance route: ${method} ${path}` } })
    })

    await page.goto(`${baseUrl}/chat?session=ui-insufficient-session`)

    await page.getByPlaceholder('输入问题，或把文件拖到这里后再提问...').fill('请只根据选定资料判断下周铜价')
    await page.getByRole('button', { name: '发送' }).click()

    await expect(page.getByText('选定资料不足以支持该问题的判断')).toBeVisible()
    await expect(page.getByText('未找到可核验来源')).toBeVisible()
    await expect(page.getByText('需要复核')).toBeVisible()
    await expect(page.getByRole('button', { name: /参考资料/ })).toHaveCount(0)
    const saveDecisionButton = page.getByRole('button', { name: '暂不可保存' })
    await expect(saveDecisionButton).toBeVisible()
    await expect(saveDecisionButton).toBeDisabled()
    expect(unhandledApiRequests).toEqual([])
    expect(consoleErrors).toEqual([])
  })

  test('shows a clear message when backend rejects saving a decision record', async ({ page }) => {
    const consoleErrors: string[] = []
    const unhandledApiRequests: string[] = []
    page.on('console', (message) => {
      if (message.type() === 'error') consoleErrors.push(message.text())
    })
    page.on('pageerror', (error) => consoleErrors.push(error.message))

    const now = '2026-06-29T03:00:00.000Z'
    const sessions = [
      {
        id: 'ui-save-reject-session',
        title: '保存拒绝会话',
        mode: 'auto',
        asset: 'BTC',
        updated_at: now,
        message_count: 2,
      },
    ]
    const assistantPayload = {
      mode: 'chat',
      claims_used: 1,
      model_used: 'test-model',
      citations: [
        {
          index: 1,
          summary: 'BTC 资金面短期中性',
          short_quote: 'BTC 资金面短期中性，缺少明确方向。',
          confidence: 0.9,
          trust_weight: 0.8,
        },
      ],
      quality_report: {
        run_id: 'ui-save-reject-run',
        generated_at: now,
        reference_time: '2026年06月29日 11:00:00 CST',
        duration_ms: 250,
        decision_ready: true,
        severity: 'ok',
        warnings: [],
        evidence: {
          citation_count: 1,
          evidence_span_count: 1,
          source_fallback_count: 0,
          invalid_citation_count: 0,
          stance_mismatch_count: 0,
        },
      },
      decision_guardrail: { decision_ready: true, status: 'ok' },
      reference_time: '2026年06月29日 11:00:00 CST',
    }
    const messagesBySession = new Map<string, any[]>([
      [
        'ui-save-reject-session',
        [
          {
            id: 'ui-save-reject-user',
            role: 'user',
            content: '请判断 BTC 是否值得操作',
            payload: {},
            created_at: now,
          },
          {
            id: 'ui-save-reject-assistant',
            role: 'assistant',
            content: '截至 2026年06月29日 11:00:00 CST，当前资料显示 BTC 资金面短期中性。[1]',
            payload: assistantPayload,
            created_at: now,
          },
        ],
      ],
    ])

    await page.route('**/api/v1/**', async (route) => {
      const request = route.request()
      const url = new URL(request.url())
      const path = url.pathname
      const method = request.method()

      if (method === 'GET' && path === '/api/v1/chat/sessions') {
        await route.fulfill({ json: sessions })
        return
      }
      if (method === 'GET' && path === '/api/v1/alerts') {
        await route.fulfill({ json: { alerts: [], total: 0 } })
        return
      }
      if (method === 'GET' && path === '/api/v1/social-media/health') {
        await route.fulfill({ json: { success: true, data: { status: 'ok', enabled: false } } })
        return
      }
      if (method === 'GET' && path === '/api/v1/decision-traces') {
        await route.fulfill({ json: { count: 0, items: [] } })
        return
      }
      const messageMatch = path.match(/^\/api\/v1\/chat\/sessions\/([^/]+)\/messages$/)
      if (method === 'GET' && messageMatch) {
        await route.fulfill({ json: messagesBySession.get(messageMatch[1]) ?? [] })
        return
      }
      if (method === 'POST' && path === '/api/v1/replay/from-chat/ui-save-reject-assistant') {
        await route.fulfill({
          status: 409,
          json: {
            detail: 'quality_report.decision_ready must be true before saving a chat message as a DecisionSnapshot',
          },
        })
        return
      }

      if (method === 'GET' && isChatIngestionContextPath(path)) {
        await route.fulfill({ json: emptyIngestionContext() })
        return
      }

      unhandledApiRequests.push(`${method} ${path}`)
      await route.fulfill({ status: 404, json: { detail: `Unhandled UI acceptance route: ${method} ${path}` } })
    })

    await page.goto(`${baseUrl}/chat?session=ui-save-reject-session`)
    await page.getByRole('button', { name: '保存判断' }).click()

    await expect(page.getByText('当前回答的证据仍需复核，暂时无法保存。')).toBeVisible()
    await expect(page.getByRole('link', { name: '查看记录' })).toHaveCount(0)
    expect(unhandledApiRequests).toEqual([])
    expect(consoleErrors.filter((item) => !item.includes('409 (Conflict)'))).toEqual([])
  })

  test('keeps refreshed source scope isolated to the active session', async ({ page }) => {
    const consoleErrors: string[] = []
    const unhandledApiRequests: string[] = []
    let submittedSourceIds: string[] | undefined

    page.on('console', (message) => {
      if (message.type() === 'error') consoleErrors.push(message.text())
    })
    page.on('pageerror', (error) => consoleErrors.push(error.message))

    await page.addInitScript(() => {
      window.localStorage.setItem('decision_session_source_ids', JSON.stringify(['global-source']))
      window.localStorage.setItem('decision_session_source_ids:ui-source-a', JSON.stringify(['source-a']))
      window.localStorage.setItem('decision_session_source_ids:ui-source-b', JSON.stringify(['source-b']))
    })

    const now = '2026-07-01T02:00:00.000Z'
    const sessions = [
      {
        id: 'ui-source-a',
        title: 'A 会话',
        mode: 'auto',
        asset: 'GENERAL',
        updated_at: now,
        message_count: 0,
      },
      {
        id: 'ui-source-b',
        title: 'B 会话',
        mode: 'auto',
        asset: 'GENERAL',
        updated_at: now,
        message_count: 0,
      },
    ]
    const messagesBySession = new Map<string, any[]>([
      ['ui-source-a', []],
      ['ui-source-b', []],
    ])

    await page.route('**/api/v1/**', async (route) => {
      const request = route.request()
      const url = new URL(request.url())
      const path = url.pathname
      const method = request.method()

      if (method === 'GET' && path === '/api/v1/chat/sessions') {
        await route.fulfill({ json: sessions })
        return
      }
      if (method === 'GET' && path === '/api/v1/alerts') {
        await route.fulfill({ json: { alerts: [], total: 0 } })
        return
      }
      if (method === 'GET' && path === '/api/v1/social-media/health') {
        await route.fulfill({ json: { success: true, data: { status: 'ok', enabled: false } } })
        return
      }
      if (method === 'GET' && path === '/api/v1/decision-traces') {
        await route.fulfill({ json: { count: 0, items: [] } })
        return
      }

      const messageMatch = path.match(/^\/api\/v1\/chat\/sessions\/([^/]+)\/messages$/)
      if (method === 'GET' && messageMatch) {
        await route.fulfill({ json: messagesBySession.get(messageMatch[1]) ?? [] })
        return
      }

      const chatMatch = path.match(/^\/api\/v1\/chat\/sessions\/([^/]+)\/chat$/)
      if (method === 'POST' && chatMatch) {
        const sessionId = chatMatch[1]
        const body = request.postDataJSON() as { message?: string; source_ids?: string[] }
        submittedSourceIds = body.source_ids
        const userMessage = {
          id: 'ui-source-user',
          role: 'user',
          content: body.message ?? '',
          payload: {},
          created_at: now,
        }
        const assistantMessage = {
          id: 'ui-source-assistant',
          role: 'assistant',
          content: '截至 2026年07月01日 10:00:00 CST，本次回答只使用当前会话资料范围。',
          payload: {
            mode: 'chat',
            citations: [],
            scope: { mode: 'selected_sources', source_ids: submittedSourceIds ?? [] },
            reference_time: '2026年07月01日 10:00:00 CST',
          },
          created_at: now,
        }
        messagesBySession.set(sessionId, [userMessage, assistantMessage])
        await route.fulfill({
          json: {
            id: assistantMessage.id,
            content: assistantMessage.content,
            ...assistantMessage.payload,
          },
        })
        return
      }

      if (method === 'GET' && isChatIngestionContextPath(path)) {
        await route.fulfill({ json: emptyIngestionContext() })
        return
      }
      if (method === 'POST' && path === '/api/v1/ingestion/status/batch') {
        await route.fulfill({ json: [] })
        return
      }

      unhandledApiRequests.push(`${method} ${path}`)
      await route.fulfill({ status: 404, json: { detail: `Unhandled UI acceptance route: ${method} ${path}` } })
    })

    await page.goto(`${baseUrl}/chat?session=ui-source-b`)
    await expect(page.getByRole('button', { name: /B 会话/ })).toBeVisible()
    await page.getByTitle('刷新').click()
    await page.getByPlaceholder('输入问题，或把文件拖到这里后再提问...').fill('请只使用当前会话资料回答')
    await page.getByRole('button', { name: '发送' }).click()

    await expect(page.getByText('本次回答只使用当前会话资料范围')).toBeVisible()
    expect(submittedSourceIds).toEqual(['source-b'])
    expect(unhandledApiRequests).toEqual([])
    expect(consoleErrors).toEqual([])
  })

  test('does not request claim detail for synthetic briefing evidence citations', async ({ page }) => {
    const consoleErrors: string[] = []
    const unhandledApiRequests: string[] = []
    const claimDetailRequests: string[] = []

    page.on('console', (message) => {
      if (message.type() === 'error') consoleErrors.push(message.text())
    })
    page.on('pageerror', (error) => consoleErrors.push(error.message))

    const now = '2026-07-01T03:00:00.000Z'
    const sessions = [
      {
        id: 'ui-synthetic-citation-session',
        title: '合成引用会话',
        mode: 'auto',
        asset: 'GENERAL',
        updated_at: now,
        message_count: 2,
      },
    ]
    const messagesBySession = new Map<string, any[]>([
      [
        'ui-synthetic-citation-session',
        [
          {
            id: 'ui-synthetic-user',
            role: 'user',
            content: '生成一份简报',
            payload: {},
            created_at: now,
          },
          {
            id: 'ui-synthetic-assistant',
            role: 'assistant',
            content: '截至 2026年07月01日 11:00:00 CST，本简报参考了上传资料中的摘要来源 [1]。',
            payload: {
              mode: 'briefing',
              claims_used: 1,
              scope: { mode: 'selected_sources', source_ids: ['source-briefing-1'] },
              data_sources: {
                claims: {
                  count: 1,
                  items: [
                    {
                      summary: '上传资料摘要显示，演示系统需要优先突出证据来源与可信度回执。',
                      stance: 'neutral',
                      confidence: 0.8,
                    },
                  ],
                },
              },
            },
            created_at: now,
          },
        ],
      ],
    ])

    await page.route('**/api/v1/**', async (route) => {
      const request = route.request()
      const url = new URL(request.url())
      const path = url.pathname
      const method = request.method()

      if (method === 'GET' && path === '/api/v1/chat/sessions') {
        await route.fulfill({ json: sessions })
        return
      }
      if (method === 'GET' && path === '/api/v1/alerts') {
        await route.fulfill({ json: { alerts: [], total: 0 } })
        return
      }
      if (method === 'GET' && path === '/api/v1/social-media/health') {
        await route.fulfill({ json: { success: true, data: { status: 'ok', enabled: false } } })
        return
      }
      if (method === 'GET' && path === '/api/v1/decision-traces') {
        await route.fulfill({ json: { count: 0, items: [] } })
        return
      }
      if (method === 'GET' && path.startsWith('/api/v1/claims/') && path.endsWith('/citation')) {
        claimDetailRequests.push(path)
        await route.fulfill({ status: 404, json: { detail: 'synthetic citation should not be requested' } })
        return
      }

      const messageMatch = path.match(/^\/api\/v1\/chat\/sessions\/([^/]+)\/messages$/)
      if (method === 'GET' && messageMatch) {
        await route.fulfill({ json: messagesBySession.get(messageMatch[1]) ?? [] })
        return
      }

      if (method === 'GET' && isChatIngestionContextPath(path)) {
        await route.fulfill({ json: emptyIngestionContext() })
        return
      }

      unhandledApiRequests.push(`${method} ${path}`)
      await route.fulfill({ status: 404, json: { detail: `Unhandled UI acceptance route: ${method} ${path}` } })
    })

    await page.goto(`${baseUrl}/chat?session=ui-synthetic-citation-session`)
    await page.getByRole('button', { name: /参考资料/ }).click()

    await expect(page.getByText('摘要来源', { exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: '详情' })).toHaveCount(0)
    expect(claimDetailRequests).toEqual([])
    expect(unhandledApiRequests).toEqual([])
    expect(consoleErrors).toEqual([])
  })

  test('renders model provider outage as an actionable chat error', async ({ page }) => {
    const consoleErrors: string[] = []
    const unhandledApiRequests: string[] = []

    page.on('console', (message) => {
      if (message.type() === 'error') consoleErrors.push(message.text())
    })
    page.on('pageerror', (error) => consoleErrors.push(error.message))

    const now = '2026-07-01T04:00:00.000Z'
    const sessions = [
      {
        id: 'ui-model-outage-session',
        title: '模型不可用会话',
        mode: 'auto',
        asset: 'GENERAL',
        updated_at: now,
        message_count: 0,
      },
    ]

    await page.route('**/api/v1/**', async (route) => {
      const request = route.request()
      const url = new URL(request.url())
      const path = url.pathname
      const method = request.method()

      if (method === 'GET' && path === '/api/v1/chat/sessions') {
        await route.fulfill({ json: sessions })
        return
      }
      if (method === 'GET' && path === '/api/v1/alerts') {
        await route.fulfill({ json: { alerts: [], total: 0 } })
        return
      }
      if (method === 'GET' && path === '/api/v1/social-media/health') {
        await route.fulfill({ json: { success: true, data: { status: 'ok', enabled: false } } })
        return
      }
      if (method === 'GET' && path === '/api/v1/decision-traces') {
        await route.fulfill({ json: { count: 0, items: [] } })
        return
      }
      const messageMatch = path.match(/^\/api\/v1\/chat\/sessions\/([^/]+)\/messages$/)
      if (method === 'GET' && messageMatch) {
        await route.fulfill({ json: [] })
        return
      }
      if (method === 'POST' && path === '/api/v1/chat/sessions/ui-model-outage-session/chat') {
        await route.fulfill({
          status: 503,
          json: {
            detail: 'Model provider unavailable. Check OpenRouter configuration or retry later.',
            status_code: 503,
            error_code: 'llm_provider_unavailable',
            error: 'all_models_failed',
            error_type: 'upstream_unavailable',
            request_id: 'ui-model-outage-request',
          },
        })
        return
      }

      if (method === 'GET' && isChatIngestionContextPath(path)) {
        await route.fulfill({ json: emptyIngestionContext() })
        return
      }

      unhandledApiRequests.push(`${method} ${path}`)
      await route.fulfill({ status: 404, json: { detail: `Unhandled UI acceptance route: ${method} ${path}` } })
    })

    await page.goto(`${baseUrl}/chat?session=ui-model-outage-session`)
    const prompt = '请分析当前模型不可用时的提示'
    await page.getByPlaceholder('输入问题，或把文件拖到这里后再提问...').fill(prompt)
    await page.getByRole('button', { name: '发送' }).click()

    await expect(page.getByText('模型服务不可用：请检查 OpenRouter 密钥、余额、代理或稍后重试。')).toBeVisible()
    await expect(page.getByPlaceholder('输入问题，或把文件拖到这里后再提问...')).toHaveValue(prompt)
    await expect(page.getByText(/正在处理 普通问答/)).toHaveCount(0)
    expect(unhandledApiRequests).toEqual([])
    expect(consoleErrors.filter((item) => !item.includes('503 (Service Unavailable)'))).toEqual([])
  })

  test('keeps generated briefing result explicit when chat message persistence fails', async ({ page }) => {
    const consoleErrors: string[] = []
    const unhandledApiRequests: string[] = []
    let briefingCalls = 0
    let assistantWriteAttempts = 0

    page.on('console', (message) => {
      if (message.type() === 'error') consoleErrors.push(message.text())
    })
    page.on('pageerror', (error) => consoleErrors.push(error.message))

    const now = '2026-07-04T03:00:00.000Z'
    const session = {
      id: 'ui-briefing-persist-fail-session',
      title: '简报写入失败会话',
      mode: 'auto',
      asset: 'GENERAL',
      updated_at: now,
      message_count: 0,
    }
    let sessions: any[] = []
    const messagesBySession = new Map<string, any[]>([[session.id, []]])

    await page.route('**/api/v1/**', async (route) => {
      const request = route.request()
      const url = new URL(request.url())
      const path = url.pathname
      const method = request.method()

      if (method === 'GET' && path === '/api/v1/chat/sessions') {
        await route.fulfill({ json: sessions })
        return
      }
      if (method === 'POST' && path === '/api/v1/chat/sessions') {
        sessions = [session]
        await route.fulfill({ json: session })
        return
      }
      if (method === 'GET' && path === '/api/v1/alerts') {
        await route.fulfill({ json: { alerts: [], total: 0 } })
        return
      }
      if (method === 'GET' && path === '/api/v1/social-media/health') {
        await route.fulfill({ json: { success: true, data: { status: 'ok', enabled: false } } })
        return
      }
      if (method === 'GET' && path === '/api/v1/decision-traces') {
        await route.fulfill({ json: { count: 0, items: [] } })
        return
      }

      const messageMatch = path.match(/^\/api\/v1\/chat\/sessions\/([^/]+)\/messages$/)
      if (method === 'GET' && messageMatch) {
        await route.fulfill({ json: messagesBySession.get(messageMatch[1]) ?? [] })
        return
      }
      if (method === 'POST' && messageMatch) {
        const body = request.postDataJSON() as { role?: string; content?: string; payload?: Record<string, unknown> }
        if (body.role === 'assistant') {
          assistantWriteAttempts += 1
          await route.fulfill({
            status: 500,
            json: { detail: 'chat message persistence failed' },
          })
          return
        }
        const userMessage = {
          id: 'ui-briefing-user',
          role: 'user',
          content: body.content ?? '',
          payload: body.payload ?? {},
          created_at: now,
        }
        messagesBySession.set(messageMatch[1], [userMessage])
        await route.fulfill({ json: userMessage })
        return
      }

      if (method === 'POST' && path === '/api/v1/briefings') {
        briefingCalls += 1
        await route.fulfill({
          json: {
            id: 'briefing-persist-fail-1',
            title: '持久化失败简报',
            topic: '演示链路',
            direction: 'neutral',
            setup_quality: 'watch',
            executive_summary: '简报业务结果已经生成，但聊天消息写入失败。',
            supporting_points: ['业务接口已经成功返回。'],
            contrary_points: [],
            key_risks: ['聊天历史未持久化。'],
            recommended_action: 'watch',
            invalidation_conditions: ['重新确认消息写入能力。'],
            created_at: now,
            scope: { mode: 'global', source_ids: [] },
            llm_trace: { call_count: 1, model: 'ui-test-model' },
          },
        })
        return
      }

      if (method === 'GET' && isChatIngestionContextPath(path)) {
        await route.fulfill({ json: emptyIngestionContext() })
        return
      }

      unhandledApiRequests.push(`${method} ${path}`)
      await route.fulfill({ status: 404, json: { detail: `Unhandled UI acceptance route: ${method} ${path}` } })
    })

    await page.goto(`${baseUrl}/chat`)
    await page.getByRole('button', { name: /简报生成/ }).click()
    await page.getByPlaceholder('输入问题，或把文件拖到这里后再提问...').fill('生成一份系统演示简报')
    await page.getByRole('button', { name: '发送' }).click()

    await expect(page.getByText(/简报已生成，但暂未保存到当前会话/)).toBeVisible()
    await expect(page.getByPlaceholder('输入问题，或把文件拖到这里后再提问...')).toHaveValue('')
    expect(briefingCalls).toBe(1)
    expect(assistantWriteAttempts).toBe(1)
    expect(unhandledApiRequests).toEqual([])
    expect(consoleErrors.filter((item) => !item.includes('500 (Internal Server Error)'))).toEqual([])
  })

  test('persists briefing quality report into the chat trust receipt', async ({ page }) => {
    const consoleErrors: string[] = []
    const unhandledApiRequests: string[] = []
    let assistantPayload: Record<string, unknown> | undefined

    page.on('console', (message) => {
      if (message.type() === 'error') consoleErrors.push(message.text())
    })
    page.on('pageerror', (error) => consoleErrors.push(error.message))

    const now = '2026-07-04T04:30:00.000Z'
    const session = {
      id: 'ui-briefing-quality-session',
      title: '简报质量回执会话',
      mode: 'auto',
      asset: 'GENERAL',
      updated_at: now,
      message_count: 0,
    }
    let sessions: any[] = []
    const messagesBySession = new Map<string, any[]>([[session.id, []]])
    const briefingQualityReport = {
      decision_ready: false,
      reference_time: '2026-07-04T04:30:00Z',
      duration_ms: 2200,
      warnings: [{ code: 'field_evidence_insufficient', message: '部分字段证据不足', severity: 'warning' }],
      evidence: {
        citation_count: 0,
        used_citation_count: 0,
        field_evidence: {
          recommended_action: { status: 'supported', claim_ids: ['claim-briefing-1'], event_ids: [], source_ids: ['source-briefing-1'] },
          key_risks: { status: 'insufficient', claim_ids: [], event_ids: [], source_ids: ['source-briefing-1'] },
        },
      },
    }
    const briefingQualityFilters = {
      evidence_sufficiency: 'insufficient',
      insufficient_evidence_reason: 'selected_sources_actionable_question_requires_directional_evidence',
    }
    const briefingTimingTrace = {
      setup_ms: 100,
      selected_scope_check_ms: 100,
      retrieval_ms: 300,
      persistence_setup_ms: 100,
      model_calls_ms: 900,
      other_processing_ms: 700,
      total_ms: 2200,
    }

    await page.route('**/api/v1/**', async (route) => {
      const request = route.request()
      const url = new URL(request.url())
      const path = url.pathname
      const method = request.method()

      if (method === 'GET' && path === '/api/v1/chat/sessions') {
        await route.fulfill({ json: sessions })
        return
      }
      if (method === 'POST' && path === '/api/v1/chat/sessions') {
        sessions = [session]
        await route.fulfill({ json: session })
        return
      }
      if (method === 'GET' && path === '/api/v1/alerts') {
        await route.fulfill({ json: { alerts: [], total: 0 } })
        return
      }
      if (method === 'GET' && path === '/api/v1/social-media/health') {
        await route.fulfill({ json: { success: true, data: { status: 'ok', enabled: false } } })
        return
      }

      const messageMatch = path.match(/^\/api\/v1\/chat\/sessions\/([^/]+)\/messages$/)
      if (method === 'GET' && messageMatch) {
        await route.fulfill({ json: messagesBySession.get(messageMatch[1]) ?? [] })
        return
      }
      if (method === 'POST' && messageMatch) {
        const body = request.postDataJSON() as { role?: string; content?: string; payload?: Record<string, unknown> }
        const message = {
          id: body.role === 'assistant' ? 'ui-briefing-quality-assistant' : 'ui-briefing-quality-user',
          role: body.role ?? 'user',
          content: body.content ?? '',
          payload: body.payload ?? {},
          created_at: now,
        }
        if (body.role === 'assistant') assistantPayload = body.payload ?? {}
        messagesBySession.set(messageMatch[1], [...(messagesBySession.get(messageMatch[1]) ?? []), message])
        await route.fulfill({ json: message })
        return
      }

      if (method === 'POST' && path === '/api/v1/briefings') {
        await route.fulfill({
          json: {
            id: 'briefing-quality-1',
            title: '质量回执简报',
            topic: '演示链路',
            direction: 'neutral',
            setup_quality: 'watch',
            executive_summary: '简报已经生成，且返回字段级质量回执。',
            supporting_points: ['建议字段有证据支撑。'],
            contrary_points: [],
            key_risks: ['风险字段证据不足，需要人工复核。'],
            recommended_action: 'watch',
            invalidation_conditions: ['字段证据不足时不进入自动决策。'],
            created_at: now,
            scope: { mode: 'selected_sources', source_ids: ['source-briefing-1'] },
            quality_filters: briefingQualityFilters,
            llm_trace: { call_count: 1, model: 'ui-briefing-model', calls: [{ model: 'ui-briefing-model', duration_ms: 900 }] },
            timing_trace: briefingTimingTrace,
            quality_report: briefingQualityReport,
          },
        })
        return
      }

      if (method === 'GET' && path === '/api/v1/decision-traces/trust-receipt') {
        await route.fulfill({ json: trustReceiptSummary(url, ['source-briefing-1']) })
        return
      }
      if (method === 'GET' && path === '/api/v1/decision-traces') {
        await route.fulfill({ json: { count: 0, items: [] } })
        return
      }

      if (method === 'GET' && isChatIngestionContextPath(path)) {
        await route.fulfill({ json: emptyIngestionContext() })
        return
      }

      unhandledApiRequests.push(`${method} ${path}`)
      await route.fulfill({ status: 404, json: { detail: `Unhandled UI acceptance route: ${method} ${path}` } })
    })

    await page.goto(`${baseUrl}/chat`)
    await page.getByRole('button', { name: /简报生成/ }).click()
    await page.getByPlaceholder('输入问题，或把文件拖到这里后再提问...').fill('生成一份带质量回执的简报')
    await page.getByRole('button', { name: '发送' }).click()

    await expect(page.getByText('简报已经生成，且返回字段级质量回执。')).toBeVisible()
    expect(assistantPayload?.quality_report).toEqual(briefingQualityReport)
    expect(assistantPayload?.quality_filters).toEqual(briefingQualityFilters)
    expect(assistantPayload?.timing_trace).toEqual(briefingTimingTrace)

    await expect(page.getByText('未找到可核验来源')).toBeVisible()
    await expect(page.getByText('需要复核', { exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: '暂不可保存' })).toBeDisabled()
    expect(unhandledApiRequests).toEqual([])
    expect(consoleErrors).toEqual([])
  })

  test('persists committee quality report into the chat trust receipt', async ({ page }) => {
    const consoleErrors: string[] = []
    const unhandledApiRequests: string[] = []
    let assistantPayload: Record<string, unknown> | undefined

    page.on('console', (message) => {
      if (message.type() === 'error') consoleErrors.push(message.text())
    })
    page.on('pageerror', (error) => consoleErrors.push(error.message))

    const now = '2026-07-04T04:45:00.000Z'
    const session = {
      id: 'ui-committee-quality-session',
      title: '委员会质量回执会话',
      mode: 'auto',
      asset: 'GENERAL',
      updated_at: now,
      message_count: 0,
    }
    let sessions: any[] = []
    const messagesBySession = new Map<string, any[]>([[session.id, []]])
    const committeeQualityReport = {
      decision_ready: false,
      reference_time: '2026-07-04T04:45:00Z',
      duration_ms: 3200,
      warnings: [{ code: 'field_evidence_insufficient', message: '委员会字段证据不足', severity: 'warning' }],
      evidence: {
        field_evidence: {
          synthesis: { status: 'supported', claim_ids: ['claim-committee-1'], event_ids: [], source_ids: ['source-committee-1'] },
          risk_warnings: { status: 'insufficient', claim_ids: [], event_ids: [], source_ids: ['source-committee-1'] },
        },
      },
    }
    const committeeQualityFilters = {
      evidence_sufficiency: 'insufficient',
      insufficient_evidence_reason: 'selected_sources_temporal_horizon_mismatch',
    }

    await page.route('**/api/v1/**', async (route) => {
      const request = route.request()
      const url = new URL(request.url())
      const path = url.pathname
      const method = request.method()

      if (method === 'GET' && path === '/api/v1/chat/sessions') {
        await route.fulfill({ json: sessions })
        return
      }
      if (method === 'POST' && path === '/api/v1/chat/sessions') {
        sessions = [session]
        await route.fulfill({ json: session })
        return
      }
      if (method === 'GET' && path === '/api/v1/alerts') {
        await route.fulfill({ json: { alerts: [], total: 0 } })
        return
      }
      if (method === 'GET' && path === '/api/v1/social-media/health') {
        await route.fulfill({ json: { success: true, data: { status: 'ok', enabled: false } } })
        return
      }

      const messageMatch = path.match(/^\/api\/v1\/chat\/sessions\/([^/]+)\/messages$/)
      if (method === 'GET' && messageMatch) {
        await route.fulfill({ json: messagesBySession.get(messageMatch[1]) ?? [] })
        return
      }
      if (method === 'POST' && messageMatch) {
        const body = request.postDataJSON() as { role?: string; content?: string; payload?: Record<string, unknown> }
        const message = {
          id: body.role === 'assistant' ? 'ui-committee-quality-assistant' : 'ui-committee-quality-user',
          role: body.role ?? 'user',
          content: body.content ?? '',
          payload: body.payload ?? {},
          created_at: now,
        }
        if (body.role === 'assistant') assistantPayload = body.payload ?? {}
        messagesBySession.set(messageMatch[1], [...(messagesBySession.get(messageMatch[1]) ?? []), message])
        await route.fulfill({ json: message })
        return
      }

      if (method === 'POST' && path === '/api/v1/committee/run') {
        await route.fulfill({
          json: {
            debate_id: 'committee-quality-1',
            direction: 'neutral',
            conviction: 0.42,
            synthesis: '委员会已生成质量回执，但部分字段需要复核。',
            is_mock: false,
            recommended_action: 'watch',
            consensus_level: 'weak_consensus',
            duration_ms: 3200,
            activated_seats: ['baseline', 'risk'],
            risk_warnings: ['风险字段证据不足。'],
            scope: { mode: 'selected_sources', source_ids: ['source-committee-1'] },
            quality_filters: committeeQualityFilters,
            llm_trace: { call_count: 1, model: 'ui-committee-model', calls: [{ model: 'ui-committee-model', duration_ms: 1300 }] },
            quality_report: committeeQualityReport,
          },
        })
        return
      }

      if (method === 'GET' && path === '/api/v1/decision-traces/trust-receipt') {
        await route.fulfill({ json: trustReceiptSummary(url, ['source-committee-1']) })
        return
      }
      if (method === 'GET' && path === '/api/v1/decision-traces') {
        await route.fulfill({ json: { count: 0, items: [] } })
        return
      }

      if (method === 'GET' && isChatIngestionContextPath(path)) {
        await route.fulfill({ json: emptyIngestionContext() })
        return
      }

      unhandledApiRequests.push(`${method} ${path}`)
      await route.fulfill({ status: 404, json: { detail: `Unhandled UI acceptance route: ${method} ${path}` } })
    })

    await page.goto(`${baseUrl}/chat`)
    await page.getByRole('button', { name: /委员会讨论/ }).click()
    await page.getByPlaceholder('输入问题，或把文件拖到这里后再提问...').fill('组织一次带质量回执的委员会讨论')
    await page.getByRole('button', { name: '发送' }).click()

    await expect(page.getByText('委员会已生成质量回执，但部分字段需要复核。')).toBeVisible()
    expect(assistantPayload?.quality_report).toEqual(committeeQualityReport)
    expect(assistantPayload?.quality_filters).toEqual(committeeQualityFilters)

    await expect(page.getByText('未找到可核验来源')).toBeVisible()
    await expect(page.getByText('需要复核', { exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: '暂不可保存' })).toBeDisabled()
    expect(unhandledApiRequests).toEqual([])
    expect(consoleErrors).toEqual([])
  })

  test('creates a new session when the URL session is stale and no sessions exist', async ({ page }) => {
    const consoleErrors: string[] = []
    const unhandledApiRequests: string[] = []
    let createSessionCalls = 0
    let submittedChatSessionId: string | undefined

    page.on('console', (message) => {
      if (message.type() === 'error') consoleErrors.push(message.text())
    })
    page.on('pageerror', (error) => consoleErrors.push(error.message))

    const now = '2026-07-04T04:00:00.000Z'
    const newSession = {
      id: 'ui-created-from-stale-url',
      title: '陈旧 URL 首问',
      mode: 'auto',
      asset: 'GENERAL',
      updated_at: now,
      message_count: 0,
    }
    let sessions: any[] = []
    const messagesBySession = new Map<string, any[]>([
      ['missing-session', []],
      [newSession.id, []],
    ])

    await page.route('**/api/v1/**', async (route) => {
      const request = route.request()
      const url = new URL(request.url())
      const path = url.pathname
      const method = request.method()

      if (method === 'GET' && path === '/api/v1/chat/sessions') {
        await route.fulfill({ json: sessions })
        return
      }
      if (method === 'POST' && path === '/api/v1/chat/sessions') {
        createSessionCalls += 1
        sessions = [newSession]
        await route.fulfill({ json: newSession })
        return
      }
      if (method === 'GET' && path === '/api/v1/alerts') {
        await route.fulfill({ json: { alerts: [], total: 0 } })
        return
      }
      if (method === 'GET' && path === '/api/v1/social-media/health') {
        await route.fulfill({ json: { success: true, data: { status: 'ok', enabled: false } } })
        return
      }
      if (method === 'GET' && path === '/api/v1/decision-traces') {
        await route.fulfill({ json: { count: 0, items: [] } })
        return
      }

      const messageMatch = path.match(/^\/api\/v1\/chat\/sessions\/([^/]+)\/messages$/)
      if (method === 'GET' && messageMatch) {
        await route.fulfill({ json: messagesBySession.get(messageMatch[1]) ?? [] })
        return
      }

      const chatMatch = path.match(/^\/api\/v1\/chat\/sessions\/([^/]+)\/chat$/)
      if (method === 'POST' && chatMatch) {
        submittedChatSessionId = chatMatch[1]
        const body = request.postDataJSON() as { message?: string }
        const userMessage = {
          id: 'ui-stale-user',
          role: 'user',
          content: body.message ?? '',
          payload: {},
          created_at: now,
        }
        const assistantMessage = {
          id: 'ui-stale-assistant',
          role: 'assistant',
          content: '截至 2026年07月04日 12:00:00 CST，新会话已创建并正常回答。',
          payload: { mode: 'chat', citations: [], reference_time: '2026年07月04日 12:00:00 CST' },
          created_at: now,
        }
        messagesBySession.set(chatMatch[1], [userMessage, assistantMessage])
        await route.fulfill({ json: { id: assistantMessage.id, content: assistantMessage.content, ...assistantMessage.payload } })
        return
      }

      if (method === 'GET' && isChatIngestionContextPath(path)) {
        await route.fulfill({ json: emptyIngestionContext() })
        return
      }

      unhandledApiRequests.push(`${method} ${path}`)
      await route.fulfill({ status: 404, json: { detail: `Unhandled UI acceptance route: ${method} ${path}` } })
    })

    await page.goto(`${baseUrl}/chat?session=missing-session`)
    await page.getByPlaceholder('输入问题，或把文件拖到这里后再提问...').fill('这是陈旧 URL 下的第一问')
    await page.getByRole('button', { name: '发送' }).click()

    await expect(page.getByText('新会话已创建并正常回答')).toBeVisible()
    expect(createSessionCalls).toBe(1)
    expect(submittedChatSessionId).toBe(newSession.id)
    expect(unhandledApiRequests).toEqual([])
    expect(consoleErrors).toEqual([])
  })

  test('renders deterministic chat, briefing, debate evidence and trace surfaces', async ({ page }) => {
    expect(sessionId, 'UI_ACCEPTANCE_SESSION_ID must be provided by the acceptance runner').toBeTruthy()
    expect(marker, 'UI_ACCEPTANCE_MARKER_A must be provided by the acceptance runner').toBeTruthy()

    const consoleErrors: string[] = []
    page.on('console', (message) => {
      if (message.type() === 'error') consoleErrors.push(message.text())
    })
    page.on('pageerror', (error) => consoleErrors.push(error.message))

    await page.goto(`${baseUrl}/chat?session=${sessionId}`)

    await expect(page.getByRole('heading', { name: '问答', exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: '对话', exact: true })).toBeVisible()
    const chatMode = page.getByTestId('decision-chat-mode-chat')
    const briefingMode = page.getByTestId('decision-chat-mode-briefing')
    const debateMode = page.getByTestId('decision-chat-mode-debate')

    await expect(page.getByRole('group', { name: '回答模式' })).toBeVisible()
    await expect(chatMode).toHaveAttribute('aria-pressed', 'true')
    await expect(briefingMode).toHaveAttribute('aria-pressed', 'false')
    await expect(debateMode).toHaveAttribute('aria-pressed', 'false')

    await briefingMode.click()
    await expect(chatMode).toHaveAttribute('aria-pressed', 'false')
    await expect(briefingMode).toHaveAttribute('aria-pressed', 'true')

    await debateMode.click()
    await expect(briefingMode).toHaveAttribute('aria-pressed', 'false')
    await expect(debateMode).toHaveAttribute('aria-pressed', 'true')

    await chatMode.click()
    await expect(chatMode).toHaveAttribute('aria-pressed', 'true')
    await expect(debateMode).toHaveAttribute('aria-pressed', 'false')
    await expect(page.getByPlaceholder('输入问题，或把文件拖到这里后再提问...')).toBeVisible()

    const body = page.locator('body')
    await expect(body).toContainText(marker as string)
    await expect(body).toContainText('简报结论')
    await expect(body).toContainText('委员会裁决')
    await expect(body).toContainText(/参考资料|引用证据/)
    await expect(body).toContainText(/可作为参考|需要复核/)

    const evidenceButtons = page.getByRole('button', { name: /参考资料/ })
    expect(await evidenceButtons.count()).toBeGreaterThanOrEqual(3)
    await expect(evidenceButtons.first()).toHaveAttribute('aria-expanded', 'false')
    await expect(body).not.toContainText('mock-ui-model')
    await expect(body).not.toContainText('持久化追踪')
    await expect(body).not.toContainText('llm_call')

    expect(consoleErrors).toEqual([])
  })
})
