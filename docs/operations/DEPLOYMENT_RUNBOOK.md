# Deployment Runbook

> **延后范围**：本文是生产部署参考，不表示当前版本已获生产发布批准。任何执行都
> 需要新的明确目标、目标环境证据和人工审批。

> 适用场景:把 ThinkRoom 从开发环境迁到**生产环境**（内网或公网）。
> 最近更新:2026-07-12（页级混合解析、受限接纳、Celery 摄取 Worker 与生产 fail-closed 门禁）
>
> 上线前必读：`PRE_LAUNCH_DRILL.md` (historical reference excluded from public source) — doctor pass 在隔离 PG 容器
> 实跑全链后修复的 12 处生产首跑会踩的坑（BOOLEAN DEFAULT 类型 / FK 孤儿 / docker exec
> pg_dump / JSONB autogen 误报等）以及生产上线 checklist。
> 配对文档:`@/docs/operations/SECURITY_CHECKLIST.md`、`@/docs/operations/BACKUP_RESTORE.md`

---

## 0. Pre-Launch Gate

2026-07-12 当前状态：

- [x] 本地 MVP 主流程、真实三模式回答、引用、持久化历史和页面验收已形成可重复证据
- [x] 文档摄取采用页级混合解析；102 页原生 PDF、混合页、扫描页失败边界和长图切片有回归测试
- [x] 本地摄取使用受限线程池；生产强制 Celery 5.6 + Redis + 独立 `ingestion_worker`
- [x] 生产 readiness 强制检查数据库、OpenRouter、Redis、对象存储和摄取 Worker
- [ ] 真实生产 `.env.prod`、生产 SOPS 值、TLS、PostgreSQL/MinIO、监控接收端、部署和恢复演练仍由发布负责人补齐

以下 2026-04-25 条目是历史基线，不代表当前放行证据版本：

- [x] `@/docs/operations/SECURITY_CHECKLIST.md` 🔴 P0 全部打勾
- [x] `pytest tests/ -m "not online"` 本地 **468/468 passed**（离线子集）；全量 497 tests
- [x] `npx tsc --noEmit` 前端零错误
- [x] Alembic **12 份迁移入库**（`0001..0012`），head = `0012_drill_pass_fixes`
- [x] **`alembic check` 双方言（SQLite + PG）零 drift**— 三次验证
- [x] P1-6..10 可观测性全部完成（commit `5530e1a`）
- [x] **P3 doctor pass**：隔离 PG 容器实跑全链 + CI 自动化拦回归（`df0f572` + `dc2d2bd`）
- [x] **CI**：4 个 job — backend / **doctor-pass** / frontend / docker-build
- [ ] P1-11 Secret Manager ⏳ 规模化时再做，当前 `.env.prod` 文件足够

---

## 1. 架构选择

| 规模 | 部署形态 | 数据库 | 调度 |
|---|---|---|---|
| 单用户/内网 | 本地启动脚本（SQLite）| dev.db | 受限进程内摄取线程池 + 进程内 scheduler |
| 小规模生产（当前支持边界） | `docker-compose.prod.yml` | Postgres + pgvector | 单 backend + Celery/Redis `ingestion_worker` + 进程内 scheduler |
| 多实例/高可用（尚未认证） | K8s / 托管容器 | Postgres + pgvector + PITR | 独立 API、Celery Worker、scheduler；补分布式接纳控制后再扩 backend |

本 runbook 以**小规模生产**（`docker-compose.prod.yml`）为主。当前生产合同限定单个 backend 容器和可配置并发的独立摄取 Worker；在完成 Redis/Postgres 分布式接纳原语及并发演练前，不支持水平扩展 backend。

---

## 2. 准备

### 2.1 生成 secrets

```powershell
# 后端 API Key（访问 /api/v1/** 用）
python -c "import secrets; print(secrets.token_urlsafe(32))"
# APP_SECRET_KEY（即使当前无 JWT 也为后续准备）
python -c "import secrets; print(secrets.token_urlsafe(64))"
# Postgres 密码
python -c "import secrets; print(secrets.token_urlsafe(24))"
```

### 2.2 准备 `apps/backend/.env.prod`

```env
# ── App ─────────────────────────────────
APP_ENV=production
DEPLOYMENT_CONFIG_ID=prod-YYYYMMDDTHHMMSSZ   # 非敏感发布配置 ID；启动后端时必须与本文件一致
APP_DEBUG=false
APP_SECRET_KEY=<上一步生成>
APP_API_KEY=<上一步生成>
APP_CORS_ORIGINS=["https://yourdomain.com"]
# Secret provenance metadata; required by secret_inventory_validation.
# Use a controlled secret source such as sops:production, vault:research-os, or kubernetes-secret:research-os.
SECRETS_SOURCE=sops:production
# docker-compose.prod.yml overrides these runtime values, so keep per-key provenance explicit.
DATABASE_URL_SOURCE=sops:DATABASE_URL
REDIS_URL_SOURCE=sops:REDIS_URL
MINIO_ENDPOINT_SOURCE=sops:MINIO_ENDPOINT
MINIO_ACCESS_KEY_SOURCE=sops:MINIO_ACCESS_KEY
MINIO_SECRET_KEY_SOURCE=sops:MINIO_SECRET_KEY

# ── DB ──────────────────────────────────
DATABASE_URL=<POSTGRES_DSN_FROM_CONTROLLED_SECRET_SOURCE>
PGVECTOR_ENABLED=true
POSTGRES_PASSWORD=<REQUIRED_POSTGRES_PASSWORD>

# ── Cache ───────────────────────────────
REDIS_ENABLED=true
REDIS_URL=redis://redis:6379/0

# ── Durable ingestion queue ─────────────
INGESTION_DISPATCHER_MODE=celery
INGESTION_ASYNC_MAX_OUTSTANDING=100
INGESTION_MAX_ACTIVE_JOBS=100
CELERY_BROKER_URL=redis://redis:6379/2
CELERY_INGESTION_QUEUE=ingestion
CELERY_WORKER_CONCURRENCY=2
CELERY_WORKER_PREFETCH_MULTIPLIER=1
CELERY_TASK_SOFT_TIME_LIMIT_SECONDS=3300
CELERY_TASK_TIME_LIMIT_SECONDS=3600
CELERY_TASK_VISIBILITY_TIMEOUT_SECONDS=4200

# ── Object storage ──────────────────────
MINIO_ENDPOINT=minio:9000
MINIO_ACCESS_KEY=<prod-access-key>
MINIO_SECRET_KEY=<prod-secret-key>
MINIO_BUCKET=agent-files-prod
MINIO_ROOT_USER=<REQUIRED_MINIO_ROOT_USER>
MINIO_ROOT_PASSWORD=<REQUIRED_MINIO_ROOT_PASSWORD>

# ── LLM（OpenRouter 或私有路由） ────────
OPENROUTER_API_KEY=<你的 key>
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1

# ── 日志（P1-8）─────────────────────────
LOG_FORMAT=json                    # json | pretty; 生产 json 可接 ELK/Loki
LOG_LEVEL=INFO
LOG_DIR=/var/log/researchos        # 容器内路径，与 volume 对应
LOG_RETENTION_DAYS=30

# ── Sentry（P1-9，可选）────────────────
SENTRY_DSN=                        # 留空则自动跳过 init；填了则启用
SENTRY_ENVIRONMENT=production
SENTRY_TRACES_SAMPLE_RATE=0.1      # 10% performance trace
# SENTRY_RELEASE=v2.11             # 可选，用于按版本筛选

# ── 限流（P1-7）─────────────────────────
# slowapi 以 X-API-Key 前 16 位 或 IP 分桶
RATE_LIMIT_DEFAULT=60/minute
RATE_LIMIT_CHAT=30/minute
RATE_LIMIT_BRIEFING=20/minute
RATE_LIMIT_VERIFICATION=3/minute
# 生产部署必须走共享 Redis，跨节点共享计数
RATE_LIMIT_STORAGE_URI=redis://redis:6379/1

# ── 对象存储（资料原件 / 解析中间件 / 发布探针）────────────
OBJECT_STORAGE_PROVIDER=minio        # minio 或 s3_compatible
MINIO_ENDPOINT=minio:9000
MINIO_SECURE=true                    # 生产建议 true；本地 docker 可为 false
ALLOW_INSECURE_OBJECT_STORAGE_IN_PRODUCTION=false
MINIO_BUCKET=agent-files
MINIO_ACCESS_KEY=<from-secret-store>
MINIO_SECRET_KEY=<from-secret-store>

# ── 认证白名单（P0-1）──────────────────
# 默认已放行 /health/* /metrics /docs /openapi.json /redoc /favicon.ico
# 需要额外公开的路径在这里追加（逗号分隔）
APP_AUTH_WHITELIST=

# ── Mock 开关 ──────────────────────────
USE_MOCK_EVENT_FEED=false
USE_MOCK_MARKET_DATA=false
USE_MOCK_FLOW_DATA=false
```

> ⚠ **`apps/backend/.env.prod` 不要入库**。用 SOPS / docker secrets / K8s Secret / Vault 注入。

### 2.3 验证生产 env 文件

在把配置交给 Docker/K8s/SOPS 启动脚本前，先运行脱敏生产 env 检查器。该脚本只输出 key 级别状态，不回显任何 secret 值。

```powershell
python scripts/validate_production_env.py --env-file apps/backend/.env.prod --fail-on-error
```

期望结果：

- `status=pass`
- `release_allowed=true`
- `deployment_config_id` 与本次后端启动使用的 `DEPLOYMENT_CONFIG_ID` 一致
- `production_config_errors=[]`
- `checks.env_file_git_safety.status=pass`
- 真实 `.env.prod` 必须保持 untracked 且被 `.gitignore` 覆盖；若报告出现 `env_file_tracked_by_git` 或 `env_file_not_gitignored`，先迁移到 SOPS / deployment secret store 或修正 `.gitignore`，再继续发布
- `docs/verification/production-env-validation-latest.json` 中没有真实 secret 值、本机绝对路径或乱码

模板自身可以用来确认检查器是否工作：

```powershell
python scripts/validate_production_env.py --env-file apps/backend/.env.prod.example --fail-on-error
```

模板会因为 `<REQUIRED_...>` 占位符返回 `status=fail`，这是预期行为；填入部署密钥后再重新验证。

### 2.4 运行安全 operator preflight

在正式 build / migrate / up 之前，推荐先运行 operator preflight。它会串联生产 env 校验和 Docker Compose 生产配置解析，并写入统一报告；默认不启动或停止任何容器，也不会访问 live backend。

```powershell
python scripts/run_production_operator_preflight.py --env-file apps/backend/.env.prod --fail-on-blocked
```

期望结果：

- `status=pass`
- `release_candidate_ready=true`
- `blocking_steps=[]`
- `steps.production_env_validation.exit_code=0`
- `steps.production_compose_config.exit_code=0`
- `docs/verification/production-operator-preflight-latest.json` 只包含脱敏状态、子报告路径和下一步动作，不包含真实 secret 值

后端启动并确认 `/health` 可访问后，再显式开启 live gate：

```powershell
python scripts/run_production_operator_preflight.py --env-file apps/backend/.env.prod --include-live-gate --backend-url http://127.0.0.1:8000 --fail-on-blocked
```

如果静态检查失败，`--include-live-gate` 会跳过 live gate 并报告 `live_gate_skipped_reason=static_checks_failed`，避免在已知配置错误时继续访问运行中后端。

### 2.5 使用生产栈发布包装器

需要把 preflight、build、migrate、up、健康检查和 live gate 串成一个可复核闭环时，使用生产栈发布包装器。默认只生成发布计划，不执行 Docker，不启动或停止任何容器：

```powershell
python scripts/run_production_stack_release.py --env-file apps/backend/.env.prod --fail-on-blocked
```

期望结果：

- `status=planned`
- `release_candidate_ready=true`
- `release_executed=false`
- `execution_skipped_reason=dry_run_plan_only`
- `docs/verification/production-stack-release-latest.json` 记录本次发布计划、静态 preflight 子报告路径和后续执行顺序

只有显式传入 `--execute` 才会执行 build / migrate / up，并在后端健康检查通过后自动运行 live operator preflight：

```powershell
python scripts/run_production_stack_release.py --env-file apps/backend/.env.prod --execute --backend-url http://127.0.0.1:8000 --fail-on-blocked
```

执行顺序固定为：

- `docker compose --env-file apps/backend/.env.prod -f docker-compose.prod.yml build`
- `docker compose --env-file apps/backend/.env.prod -f docker-compose.prod.yml run --rm migrate`
- `docker compose --env-file apps/backend/.env.prod -f docker-compose.prod.yml up -d`
- `/health/ready` 健康检查；必须为 `status=ready`，不能只依赖 HTTP 200
- `checks.ingestion_worker` 必须同时满足 `required=true`、`mode=celery`、`durable=true`、`worker_reachable=true`、`worker_count>=1`
- `run_production_operator_preflight.py --env-file apps/backend/.env.prod --include-live-gate`

后端生产启动本身只做 Alembic revision 只读校验，不再执行 `create_all()` 或 `_migrate_missing_columns()`。数据库 current heads 必须与镜像内 repository heads 完全一致，否则后端启动会失败并提示先运行 `alembic upgrade head`。这是一道独立于 Compose 启动顺序的防线，也覆盖绕过 Compose 直接启动镜像的部署方式。开发和测试环境仍保留本地 schema bootstrap 兼容路径。

如果静态 preflight 失败，即使传入 `--execute` 也不会执行 Docker；报告会返回 `execution_skipped_reason=static_preflight_failed`。如果 build、migrate、up、健康检查或 live gate 任一步失败，后续步骤会停止，报告会在 `blocking_steps` 和 `next_actions` 中给出需要人工处理的项。

### 2.6 一键刷新生产 readiness 证据

补齐真实 `apps/backend/.env.prod` 后，优先使用统一刷新入口生成/刷新 release gate、operator preflight、stack release 计划和 readiness summary。该入口默认只接受标准候选 env 文件 `apps/backend/.env.prod`；如果传入 `.env.prod.example`、历史临时 env 或其它模板文件，会先写出 `production-readiness-refresh-latest.json` 并直接返回 `status=blocked`，不会继续刷新子报告。

```powershell
python scripts/run_production_readiness_refresh.py --env-file apps/backend/.env.prod --backend-url http://127.0.0.1:8000 --fail-on-blocked
```

期望结果：

- `status=pass`
- `release_candidate_ready=true`
- `readiness_state=release_ready`
- `blocking_steps=[]`
- `docs/verification/production-readiness-refresh-latest.json` 记录本次刷新顺序、子报告 latest 路径、最终 digest 路径和仍需处理的下一步动作
- `docs/verification/frontend-release-checks-latest.json` 记录前端 npm audit、ESLint、TypeScript type-check 和 Vite production build 的真实执行结果
- `docs/verification/verification-report-hygiene-latest.json` 记录最终报告卫生门禁结果，确认公开发布证据中没有 secret 形态、本地后端 URL 或损坏 JSON
- `docs/verification/production-readiness-digest-latest.json` 中 `candidate_env_mismatch_count=0`

执行顺序固定为：

- `run_release_gate.py --require-env-file --env-file apps/backend/.env.prod`
- `run_production_operator_preflight.py --env-file apps/backend/.env.prod`
- `run_production_stack_release.py --env-file apps/backend/.env.prod`（默认 dry-run plan，不执行 Docker）
- `run_frontend_release_checks.py --fail-on-error`
- `summarize_production_readiness.py --report-dir docs/verification`
- `check_verification_reports.py docs/verification --fail-on-issue`（由 refresh 流程在末尾执行，并写出 `verification-report-hygiene-latest.json`）

如果后端尚未启动或 live 依赖不可用，该命令仍会写出脱敏报告并返回 blocked，维护页会显示具体阻断项。维护接口 `GET /api/v1/health/release-readiness` 的 `production_readiness_refresh.steps` 会暴露每个刷新步骤的 `status`、`exit_code`、`blocking_steps` 和安全的 `latest_path`，包括最终 `verification_report_hygiene -> docs/verification/verification-report-hygiene-latest.json`。同一接口的 `production_env_materialization` 会读取 `docs/verification/production-env-materialization-latest.json`，只展示缺失标准 env key、建议 SOPS 短名、来源标记和 dry-run/overwrite 命令，不展示密钥值、自由文本描述或本机绝对路径。排障时可以继续运行下面的单项命令；单项命令修复后，再回到一键刷新入口，避免 latest 证据来源不一致。

### 2.7 汇总生产就绪状态

当 env validation、compose validation、public API leak scan、frontend release checks、production preflight、operator preflight、stack release、release gate、secret inventory 分别生成 latest 报告后，使用统一汇总入口查看当前发布状态：

```powershell
python scripts/summarize_production_readiness.py --report-dir docs/verification --fail-on-blocked
```

该命令只读取 `docs/verification` 中的 latest 报告并生成汇总，不访问后端、不执行 Docker、不修改数据库。

默认 Markdown action plan 会先输出 `top_remediation`，只展示最高优先级的前 10 个 action groups。需要调整数量时：

```powershell
python scripts/summarize_production_readiness.py --report-dir docs/verification --top-remediation-limit 5 --fail-on-blocked
```

期望结果：

- `readiness_state=release_ready`
- `release_allowed=true`
- `blocking_reports=[]`
- `missing_reports=[]`
- `stale_reports=[]`
- `next_action_count=0`
- `top_remediation_limit=10`
- `action_groups=[]`
- `docs/verification/production-readiness-summary-latest.json` 只包含脱敏后的报告状态、阻断来源和下一步动作
- `docs/verification/production-readiness-action-plan-latest.md` 只包含脱敏后的 operator checklist、`action_groups`、source reports 和 `verification_commands`
- `docs/verification/production-readiness-digest-latest.json` 只包含 UI/自动化需要的精简发布阻断摘要：`status`、`readiness_state`、`release_allowed`、阻断报告计数、候选 env 文件不一致列表、最高优先级 `blockers`、上游报告缺失/过期列表和验证命令；不包含原始 `next_actions`、`required_actions` 或 `sample_actions`

如果任一 required report 缺失、超过默认 24 小时、状态失败、仍处于 blocked、latest report 不是基于标准候选 env 文件 `apps/backend/.env.prod` 生成，或者 release gate 未放行，汇总报告会返回 `status=blocked` 并在 `blocking_reports` / `missing_reports` / `stale_reports` / `candidate_env_mismatches` 中集中列出原因。重复的 `next_actions` 会按 config key 或 action key 合并为 `action_groups`，每组给出来源报告、是否需要 secret 更新，以及修复后应运行的 `verification_commands`。

维护页或外部自动化应优先读取 `production-readiness-digest-latest.json` 判断当前是否可发布、展示最高优先级 blocker；需要审计完整来源时再打开 `production-readiness-summary-latest.json` 和 `production-readiness-action-plan-latest.md`。

后端维护接口 `GET /api/v1/health/release-readiness` 会在 `production_readiness_digest` 字段中暴露同一份精简摘要，并通过 `evidence_sources.production_readiness_digest_path` 给出报告路径。该字段包含 `fresh`、`freshness_status`、`age_seconds`、`max_age_seconds`、`regenerate_command`、`candidate_env_mismatches` 和 `upstream_report_issues`；前端维护页只展示 digest 的发布状态、时效状态、阻断数量、前几项 blocker、候选 env 不一致、缺失/过期上游报告及只读的重新生成命令。若 digest 缺失或过期，应先运行：

```powershell
python scripts/summarize_production_readiness.py --report-dir docs/verification
```

如果维护页显示 `候选配置不一致`，先按对应报告项里的固定命令用 `apps/backend/.env.prod` 重新生成该 latest report；不要用 `.env.prod.example`、历史临时 env 或其它模板结果判断生产放行。如果维护页显示 `先补上游报告`，先按对应报告项里的固定命令补齐或刷新 `public-api-leak-scan-latest.json`、`production-preflight-latest.json`、`release-gate-latest.json`、`release-gate-secret-inventory-latest.json` 等来源报告，再运行上面的 digest 命令。生成新摘要后再判断是否放行；完整 action 仍以 summary/action plan 为准。需要把该命令作为 CI/release gate 使用时，再增加 `--fail-on-blocked`。

`action_groups` 会带上 `section_key` 与 `priority`，并按以下顺序渲染到 Markdown checklist：

- `critical_secrets`：`APP_API_KEY`、`APP_SECRET_KEY`、`OPENROUTER_API_KEY` 等关键密钥
- `database_runtime`：`DATABASE_URL`、`POSTGRES_PASSWORD`、`DEPLOYMENT_CONFIG_ID` 等数据库和运行身份
- `object_storage`：`MINIO_*` 对象存储配置
- `observability`：`SENTRY_DSN`、`LOG_FORMAT` 等观测性配置
- `production_profile`：`APP_ENV`、`APP_DEBUG`、profile safety 等环境画像配置
- `release_verification`：没有明确 config key 的最终发布验证动作

如果上游 report 把多个配置项合并在同一个 action 中，readiness summary 会在生成 `action_groups` 前先拆成逐配置项子动作。拆分后的子动作仍保留 `split_from_config_keys`，每个 group 会统计 `split_from_multi_key_action_count`，用于追溯这些单项检查来自哪个原始组合 action。

生产后端启动并可访问后，运行统一发布门禁。该命令会按顺序执行生产 env 验证、生产 compose 配置解析、公开 API 泄露扫描、生产预检，并把结果汇总到 `docs/verification/release-gate-latest.json`：

```powershell
python scripts/run_release_gate.py --require-env-file --env-file apps/backend/.env.prod --backend-url http://127.0.0.1:8000
```

期望结果：

- `release_allowed=true`
- `blocking_steps=[]`
- `steps.production_compose_config.status=pass`
- `production_compose_config_latest_path=docs/verification/production-compose-config-latest.json`，用于直接追溯 Docker Compose 配置解析证据
- `required_action_count=0`
- `production-preflight-latest.json` 中 `production_dry_run.status=pass`
- `steps.runtime_config_binding.status=pass`，证明 `--env-file` 中的 `DEPLOYMENT_CONFIG_ID` 与运行中后端暴露的 `runtime_config_identity.deployment_config_id` 一致
- `steps.secret_inventory_validation.status=pass`

如果需要单独复核密钥清单和来源证明，运行：

```powershell
python scripts/validate_secret_inventory.py --inventory docs/verification/release-gate-secret-inventory-latest.json --env-file apps/backend/.env.prod --fail-on-error
```

期望结果：

- `status=pass`
- `secret_inventory_validation.status=pass`
- 每个 secret check 均满足 `source_provenance_ok=true`
- 报告只展示 key 名、来源家族和验证状态，不展示任何 secret 原文

---

## 3. 第一次部署（冷启动）

### 3.1 起栈

```powershell
# 1. 拉取镜像 / 构建
docker compose --env-file apps/backend/.env.prod -f docker-compose.prod.yml build

# 2. 首次 migrate
docker compose --env-file apps/backend/.env.prod -f docker-compose.prod.yml run --rm migrate

# 3. 起服务
docker compose --env-file apps/backend/.env.prod -f docker-compose.prod.yml up -d

# 4. 看 log
docker compose --env-file apps/backend/.env.prod -f docker-compose.prod.yml logs -f backend
```

### 3.2 健康检查

```powershell
# 容器健康
docker compose --env-file apps/backend/.env.prod -f docker-compose.prod.yml ps

# liveness（无需 key，白名单放行）
curl http://localhost:8000/health/live
# 期望: {"status":"live","timestamp":"2026-04-24T..."}

# readiness（并行探测 DB / Redis / MinIO / OpenRouter）
curl http://localhost:8000/health/ready
# 期望 200 ready:
# {
#   "status": "ready",
#   "checks": {
#     "database":   {"status": "ok",  "latency_ms": 3},
#     "redis":      {"status": "ok",  "latency_ms": 1},
#     "minio":      {"status": "ok",  "latency_ms": 12},
#     "openrouter": {"status": "ok",  "latency_ms": 128}
#   },
#   "request_id": "abc123..."
# }
# DB 或 OpenRouter（key 非空时）失败 → 503 unhealthy
# Redis/MinIO 失败 → 本地 MVP 可 200 degraded；生产部署应视为配置未达标，先修复再接正式流量

# release readiness（维护页使用的发布就绪聚合报告，不作为 k8s probe）
curl http://localhost:8000/api/v1/health/release-readiness
# 期望生产上线前 status=pass 且 summary.fail=0
# 本地 MVP 通常为 warn：会列出密钥、Redis、对象存储、限流存储、可观测性等待处理项
# 生产上线前重点看:
# - secrets.secret_provenance: 模型和应用密钥必须来自 SOPS/受控环境注入，不应是 default_or_missing
# - secrets.secret_provenance: Redis / MinIO 的 source 也应可解释，但报告不得回显真实密钥或带密码 URI
# - object_storage.provider/minio secure=true 与目标环境一致
# - object_storage.tls_policy_ok=true；生产默认禁止非 TLS 对象存储，除非显式设置 ALLOW_INSECURE_OBJECT_STORAGE_IN_PRODUCTION=true
# - object_storage.probe.operation=write_read_delete 且 ok=true，证明 bucket 可写、可读、可删除

# Prometheus 指标（白名单放行）
curl http://localhost:8000/metrics | head -50

# 前端
curl -I http://localhost/
# 期望: 200 OK from Nginx
```

### 3.2.1 本地演示依赖

本地演示时，如果希望 `/api/v1/health/release-readiness` 中 Redis / MinIO 从 `warn` 进入真实可用状态，可先启动最小依赖栈：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start_local_dependencies.ps1 -StartDockerDesktop
```

该脚本只启动 `docker-compose.yml` 中的 `redis` 与 `minio`，不执行 `docker compose down`，不删除容器、卷或文件。脚本会：

- 等待 Docker daemon；
- 执行 `docker compose -f docker-compose.yml up -d redis minio`；
- 等待 `agent_redis` 和 `agent_minio` healthcheck；
- 通过项目 `.venv` 内的 MinIO SDK 确保 `agent-files` bucket 存在；
- 写入 `docs/verification/local-dependencies-latest.json`。

后端如需使用这组本地依赖，启动环境至少应包含：

```env
REDIS_ENABLED=true
REDIS_URL=redis://127.0.0.1:6379/0
RATE_LIMIT_STORAGE_URI=redis://127.0.0.1:6379/1
MINIO_ENDPOINT=127.0.0.1:9000
MINIO_BUCKET=agent-files
MINIO_SECURE=false
```

本地 Docker MinIO 默认是非 TLS；生产环境仍以 `MINIO_SECURE=true` 为默认放行标准。

使用项目 SOPS/OpenRouter 密钥启动后端并接入本地 Redis/MinIO：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start_backend_sops.ps1 `
  -UseLocalDependencies `
  -LogPath logs/backend.log `
  -ErrorLogPath logs/backend.err.log
```

`-UseLocalDependencies` 只覆盖本地 Redis / MinIO / 限流存储连接，不修改 OpenRouter 密钥来源；OpenRouter 仍来自 SOPS。

### 3.3 首次种子数据（可选）

```powershell
# 仅 SQLite/dev 环境才跑;Postgres 生产不建议喂 mock
docker compose --env-file apps/backend/.env.prod -f docker-compose.prod.yml exec backend python scripts/init_dev_db.py
```

---

## 4. 日常运维

### 4.1 滚动发布

```powershell
# 拉新代码
git pull
# 重建 + 迁移
docker compose --env-file apps/backend/.env.prod -f docker-compose.prod.yml build backend ingestion_worker
docker compose --env-file apps/backend/.env.prod -f docker-compose.prod.yml run --rm migrate
# 先更新摄取 Worker 并确认健康，再更新 API
docker compose --env-file apps/backend/.env.prod -f docker-compose.prod.yml up -d --no-deps --force-recreate ingestion_worker
docker compose --env-file apps/backend/.env.prod -f docker-compose.prod.yml ps ingestion_worker
# 滚更 API（Nginx 前置 + 健康检查时）
docker compose --env-file apps/backend/.env.prod -f docker-compose.prod.yml up -d --no-deps --force-recreate backend
```

### 4.2 查看摄取队列和 Worker

```powershell
# 容器健康状态
docker compose --env-file apps/backend/.env.prod -f docker-compose.prod.yml ps ingestion_worker
# Celery 控制面探针；结果至少包含一个 pong
docker compose --env-file apps/backend/.env.prod -f docker-compose.prod.yml exec -T ingestion_worker celery -A app.celery_app:celery_app inspect ping --timeout 5
# 摄取 Worker 日志
docker compose --env-file apps/backend/.env.prod -f docker-compose.prod.yml logs --tail 200 ingestion_worker
```

运行时 API 验收：

- `GET /health/ready`：`status=ready` 且 `checks.ingestion_worker.worker_reachable=true`
- `GET /api/v1/ingestion/dispatcher/status`：`mode=celery`、`durable=true`，并显示容量、饱和状态和 Worker 数
- 任务的业务真相以 `JobRun` 为准；Celery 只运输 `source_id` 与 `job_id`
- Worker 丢失后允许至少一次重投；完成任务和有效租约必须阻止重复执行

### 4.3 查看 scheduler 任务

后端日志可过滤 `job_runs`:
```powershell
docker compose --env-file apps/backend/.env.prod -f docker-compose.prod.yml logs backend | Select-String "job_runs"
```

接口:`GET /api/v1/progress`（当前实时任务）、`GET /api/v1/costs`（成本汇总）。

### 4.4 回滚

不要在活动工作区执行 `git reset --hard`。生产回滚必须部署先前通过同一 release gate 的不可变镜像摘要，并同时回滚 `backend` 与 `ingestion_worker`；如果当前环境仍采用源码构建 Compose，先在独立、干净的发布工作区检出已批准版本，再构建和部署。

数据库默认采用向前修复。只有迁移文档明确给出可逆边界、完成备份与恢复演练并由发布负责人批准时，才允许执行 schema downgrade。

---

## 5. 多实例扩展（P2）

> 当前限制：`INGESTION_ASYNC_MAX_OUTSTANDING` 的快速接纳计数仍是 backend 进程内状态，`JobRun` 活跃上限提供数据库级保护，但尚未形成原子分布式配额。未完成 Redis/Postgres 原子接纳和多 backend 并发压测前，生产保持单 backend。

### 5.1 Scheduler 多实例去重（P2-B 落地，2026-04-24）

`@/apps/backend/app/services/scheduler.py` 嵌入 FastAPI 进程；多实例启动时会重复执行任务。**已用 Redis SETNX 分布式锁解决（默认开启，零配置）**：

**方案 A · Redis 分布式锁**（默认）

- `@/apps/backend/app/services/distributed_lock.py` 提供 `try_acquire / release / distributed_lock / with_distributed_lock`
- 每个 scheduler job 顶头加 `@with_distributed_lock("scheduler:<name>", ttl=N)`：竞争失败的实例跳过本轮
- 已装饰：auto_briefing / lifecycle_check / snapshot_capture / daily_radar / mediacrawler_refresh / social_subscription
- **Redis 不可达自动 degrade**：仅用于开发/本地 MVP；生产 `REDIS_ENABLED=true` 且 `REDIS_URL` 必须可达
- 生产启动前由 `production_config_errors()` 检查 Redis 与对象存储基础配置

```env
# .env.prod
REDIS_URL=redis://redis:6379/0
```

**方案 B · 拆独立 scheduler service**（可选）

如果要让 backend pod 完全不跳调度（明确分离 API / batch 责任）：

- 加新 service：`backend-scheduler`（同镜像，envs 不同）
- backend pod 设 `APP_SCHEDULER_ENABLED=false`，scheduler pod 保持 true（默认）
- 单 scheduler pod 即可正确（仍可享受锁的二次保护）

```yaml
# docker-compose.prod.yml 节选
services:
  backend:
    environment:
      APP_SCHEDULER_ENABLED: "false"  # 不跳调度，纯 API
    deploy:
      replicas: 3

  scheduler:
    image: ros-backend:prod  # 同 backend 镜像
    environment:
      APP_SCHEDULER_ENABLED: "true"
    command: ["python", "-c", "import app.main; ..."]  # 只起 scheduler，不开端口
    deploy:
      replicas: 1
```

简单部署用方案 A 已足够；超大规模或需要独立资源限额时上方案 B。

### 5.2 PostgreSQL + pgvector（P2-A 落地，2026-04-24）

**已自动化**：

- `docker-compose.prod.yml` 默认起 `pgvector/pgvector:pg16` 镜像
- `infra/postgres/init.sql` 容器首启时自动 `CREATE EXTENSION vector`
- Alembic `0009_pgvector_embedding.py` 在 PG+pgvector 下建 `vector(EMBEDDING_DIM)` 列；幂等无损
- `claim_cards.embedding` ORM 自动用 `Vector` 类型（条件编译，见 `app/schemas/claim_card.py`）

**生产 env 必须**：

```env
DATABASE_URL=<POSTGRES_DSN_FROM_CONTROLLED_SECRET_SOURCE>
PGVECTOR_ENABLED=true
EMBEDDING_DIM=2560  # qwen3-embedding-4b 默认
```

**HNSW 索引行为**：

- `EMBEDDING_DIM ≤ 2000` → 0009 自动建 `ix_claim_cards_embedding_hnsw`（`vector_cosine_ops` + m=16 ef=64）
- `EMBEDDING_DIM > 2000`（如 2560） → 跳过 HNSW（pgvector 限制），走 `<=>` 顺序扫描。百万级以内 claim 性能可接受；超规模考虑切 1024 维 embedding

**SQLite → Postgres 数据迁移**：详见 §5.4（作为生产上线的标准路径，包含 doctor pass 发现的 `--skip-fk-orphans` / `--allow-orphans` 实战选项）。

**列漂移收尾**（G1 · G1.1 · G1.2 · P3）：ADD COLUMN 35+ + NOT NULL ALTER 69 + 索引 ORM 化 24 + `model_registry`/`key_groups` 补表 + `__init__.py` 注册全部完成；`alembic check` 在 SQLite + PG 两端必须保持零 drift。`_migrate_missing_columns()` 仅保留为开发/测试历史数据库兼容安全网，生产启动不会调用它。

### 5.3 Frontend 构建产物托管

把 `apps/frontend/dist/` 推到 CDN（Cloudflare/Vercel）或 S3+CloudFront；`VITE_API_URL` 在构建时注入目标后端域名。

### 5.4 SQLite → Postgres 数据迁移（P2-E + P3 doctor pass 增强）

dev 用 SQLite 跑通后切生产 PG 的标准路径。**doctor pass 发现**：生产 SQLite 默认 `PRAGMA foreign_keys=OFF`，可能累积 FK 孤儿；PG 严格拒绝。**dry-run 必须先看孤儿报告**。

```powershell
# 1. 备份 SQLite（用 P2-C 脚本，manual 永不清理）
$env:DATABASE_URL = "sqlite:///apps/backend/dev.db"
python infra/backup/backup.py --type manual

# 2. PG 已起 + alembic 升至 head；DSN 从 SOPS/secret store 注入，不在手册中写 URI 凭据形态
$env:DATABASE_URL = "<POSTGRES_DSN_FROM_CONTROLLED_SECRET_SOURCE>"
$env:PGVECTOR_ENABLED = "true"
cd apps\backend; alembic upgrade head; cd ..\..
# 验证到2月12 份 head + 零 drift
alembic current  # 期望：0012_drill_pass_fixes (head)
alembic check    # 期望：No new upgrade operations detected

# 3. 停 backend
docker compose --env-file apps/backend/.env.prod -f docker-compose.prod.yml stop backend

# 4. dry-run 主用途：以 _audit_fk_orphans 报告孤儿行数 （doctor pass 增强）
python infra/migration/sqlite_to_postgres.py `
    "sqlite:///apps/backend/dev.db" $env:DATABASE_URL --dry-run
# 输出例：[WARN] setup_cards.strategy_id->strategy_cards.id: 8 孤儿行
# 决策二选一：
#   (a) 迁前手工清理 SQLite 孤儿行（DELETE 子表 WHERE FK NOT IN 父表）
#   (b) 下一步加 --skip-fk-orphans 跳过（面向生产上线推荐）

# 5. 真跑（含孤儿过滤 + 父表跳过后子表级联跳过）
python infra/migration/sqlite_to_postgres.py `
    "sqlite:///apps/backend/dev.db" $env:DATABASE_URL --skip-fk-orphans

# 6. 验证（--allow-orphans 与上面记录的孤儿预算一致）
python infra/migration/verify_migration.py `
    "sqlite:///apps/backend/dev.db" $env:DATABASE_URL `
    --allow-orphans "setup_cards=8,briefing_cards=8"
# 期望："X ok / Y warn / 0 error"

# 7. 启 backend，跑 P2-D baseline 对比
docker compose --env-file apps/backend/.env.prod -f docker-compose.prod.yml up -d backend
```

**只能干净路径不要 `--skip-fk-orphans`**：若 dry-run 报告 0 孤儿（严谨的 SQLite 开发库），直接跑不加 flag，该表必须 src=dst 严格一致。

详细步骤、跨方言坑、回滚路径见 `@/docs/operations/SQLITE_TO_POSTGRES_MIGRATION.md`。
**迁移跱实体验报告**见 `PRE_LAUNCH_DRILL.md` (historical reference excluded from public source)（dev.db 14 220 行 → PG 14 204 行，8+8 级联孤儿）。

### 5.4.1 备份 / 恢复：生产集群无 PG 客户端的 docker exec 模式（P3 doctor pass）

生产默认宿主不装 `postgresql-client`（镜像足够）。`backup.py` / `restore.py` 现可走
容器内 `pg_dump` / `pg_restore`：

```env
# .env.prod 补充
BACKUP_PG_DUMP=docker exec ros_postgres pg_dump
RESTORE_PG_RESTORE=docker exec ros_postgres pg_restore
```

脚本会自动 `docker cp` 产物跳出容器、恢复时再 cp 进容器。宿主装 `postgresql-client`
后不设这两个 env 也可（脚本默认走 PATH）。实践例见
[`BACKUP_RESTORE.md` § docker exec 模式](BACKUP_RESTORE.md)。

---

## 5.5 负载测试（P2-D，2026-04-25）

`@/tests/load/` 提供 locust 脚本。**首次部署前必跑一次 baseline**，记录到 `reports/load-baseline-YYYYMMDD.html` 留档。后续每次大版本发布前对比 p95 退化是否 > 20%。

```powershell
# 1. 安装（独立 venv，避免污染生产 image）
python -m venv .venv-load
.venv-load\Scripts\activate
pip install -r tests/load/requirements.txt

# 2. 起后端（生产 build，多 worker）
$env:APP_ENV = "test"  # 关 scheduler 避免和压测打架
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4

# 3. 跑 locust（5min × 50 并发 → CSV + HTML 报告）
locust -f tests/load/locustfile.py --headless `
    -u 50 -r 5 -t 5m --host http://localhost:8000 `
    --csv reports/load --html reports/load.html
```

**SLO 阈值**（red lines 即生产不可用）：

| 端点 | 目标 p95 | 红线 p95 |
|---|---|---|
| `/health/live` | < 50ms | > 200ms |
| `/health/ready` | < 200ms | > 1s |
| `/metrics` | < 300ms | > 1s |
| `GET /watchlists` | < 500ms | > 2s |
| `GET /claims` | < 800ms | > 3s |
| `POST /knowledge/search` | < 2s | > 8s |
| 整体失败率 | < 1% | > 5% |

详细参数 / 排错 / CI 集成方案见 `@/tests/load/README.md`。

---

## 6. 故障诊断

| 症状 | 排查 |
|---|---|
| `/health/ready` 返回 503 db | `docker compose exec postgres pg_isready`；看 backend log 是否 `DATABASE_URL` 错 |
| 所有 LLM 调用 429 | OpenRouter rate limit；查 `GET /api/v1/costs` 看是否成本异常；等冷却或升档 |
| Scheduler 不跑 | `backend` log 搜 `scheduler`；确认 `APP_ENV` 不是 `test`（test 模式不启动调度） |
| `database is locked` | 生产应该用 Postgres，SQLite 只能单进程；若被迫用 SQLite 确认 `safe_commit` 全链路 |
| 前端白屏 | 浏览器 Console 看 CORS 报错；确认 `APP_CORS_ORIGINS` 含前端域名 |
| `/chat` 偶发 500 | 看 Sentry；常见是 LLM 超时 / JSON schema 未通过 `llm_retry` |

---

## 7. 监控告警建议（P1 完成后）

- **SLO**:
  - `/chat` p95 < 20s（含 LLM 往返）
  - `/briefings/generate` p95 < 120s
  - `/health/ready` 可用性 ≥ 99.5%
- **告警**:
  - 5 分钟内 5xx 率 > 5%
  - `llm_call_total{status="failed"}` 5 分钟增量 > 10
  - Scheduler job `failed` 连续两次
  - 成本 1 小时内超出预算阈值
