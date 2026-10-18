# Security Checklist (公网上线安全清单)

> **延后范围**：清单记录已有安全脚手架和历史验证，不是当前生产发布结论。
> 未经新的目标环境验收，不得把勾选项解释为可直接公网部署。

> 适用范围:把 ThinkRoom 从单用户/内网部署迁往**公网多用户**生产环境。
> 最近更新:2026-07-12（P0 六项 + P1 五项核心完成；CI 离线全量测试、前端 release evidence、`@pytest.mark.online` 隔离和持久化摄取 Worker 门禁已同步）
> 来源:基于 2026-04-22 代码审计，见 `@/docs/handover/INFRASTRUCTURE.md §0`。

---

## 检查清单概览

| 级别 | 数量 | 总工作量 | 状态 |
|---|---|---|---|
| 🔴 P0 公网必做 | 6 | 10 人天 | ✅ 代码与离线验收完成；真实部署证据待发布负责人 |
| 🟡 P1 核心可观测性（P1-6..P1-10） | 5 | 3 人天 | ✅ 已完成（2026-04-24） |
| 🟡 P1 其他（待建） | 2 | 2 人天 | ⏳ 待办 |
| 🟢 P2 规模化前 | 5 | 6 人天 | ⏳ 待办 |

---

## 🔴 P0 - 公网必做

### P0-1 · API 认证中间件 · ✅ 已完成（commit `1c3d762`, 2026-04-24）

- **实现**：`@/apps/backend/app/middleware/auth.py` `APIKeyMiddleware`，注册在 `@/apps/backend/app/main.py`。
- 空 `APP_API_KEY` 自动关闭（dev/test 向后兼容）；CORS OPTIONS + `/health` `/docs` `/openapi.json` `/redoc` 白名单放行；`hmac.compare_digest` 常量时间比较。
- **验证**：`@/apps/backend/tests/test_auth_middleware.py` 10/10 passed。

---

#### 原始背景（已解决）

**现状**:30+ API 模块零认证。
- 搜索 `HTTPBearer` / `OAuth2` / `api_key` 在 `@/apps/backend/app/api/v1/` 零匹配
- `@/apps/backend/app/main.py` 未注册任何 auth dependency

**威胁模型**:
- 任何外部调用方可读/写/删所有 claims/briefings/debates
- LLM 调用费用会被白嫖（OpenRouter key 有效时）

**最小修复**（单用户系统）:
```python
# app/middleware/auth.py
from fastapi import Header, HTTPException
from app.config import settings

async def require_api_key(x_api_key: str = Header(None)):
    if settings.APP_ENV == "development":
        return  # dev 跳过
    if not x_api_key or x_api_key != settings.APP_API_KEY:
        raise HTTPException(401, "Invalid API key")
```

- 在 `main.py` 用 `app.include_router(api_v1_router, dependencies=[Depends(require_api_key)])`
- `/health`、`/docs`、`/openapi.json` 走白名单单独注册

**长期方向**:JWT + `users` 表 + session/refresh 流程。

---

### P0-2 · CORS 收紧 · ✅ 已完成（commit `1c3d762`, 2026-04-24）

- **实现**：`@/apps/backend/app/main.py` 的 `CORSMiddleware` 已改为显式白名单 methods + headers + `max_age=600`。

---

#### 原始背景（已解决）

**现状**:`@/apps/backend/app/main.py:94-100`
```python
allow_origins=settings.APP_CORS_ORIGINS,   # ✓ 已配置
allow_credentials=True,                     # ⚠ 同时 allow_methods=["*"] 放大 CSRF 面
allow_methods=["*"],                        # ❌
allow_headers=["*"],                        # ❌
```

**修复**:
```python
allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-Id"],
max_age=600,
```

生产环境还要确认 `APP_CORS_ORIGINS` 只含实际域名，不含 `*`、不含 `http://` 明文（除内网外）。

---

### P0-3 · Alembic 正式 migration 与生产启动边界 · ✅ 已完成（2026-07-12 复核）

- **实现**：
  - `@/apps/backend/alembic/env.py` 动态从 `settings.DATABASE_URL` 读取，dev/CI SQLite 与 prod Postgres 统一入口
  - 当前 migration chain 已覆盖到 `0020_job_run_scope_columns`；fresh SQLite `alembic upgrade head`、索引、回填和 ORM 全表/全列漂移门禁通过
  - `docker-compose.prod.yml` 由独立 `migrate` service 执行 `alembic upgrade head`，backend 依赖 `service_completed_successfully`
  - `app.services.schema_startup` 在生产启动时只读比较 database current heads 与 repository heads，任何缺失、落后、分叉或超前都会使启动失败
  - 生产路径不调用 `create_all()` 或 `_migrate_missing_columns()`；开发和测试继续保留本地兼容 bootstrap
- **验收**：`test_schema_startup_policy.py` 覆盖生产无 DDL、revision mismatch fail-closed、安全错误信息和开发兼容；`test_alembic_sqlite_head.py` 覆盖完整 migration chain

---

#### 2026-04 原始背景（历史记录，现已解决）

当时 `@/apps/backend/alembic/versions/` 尚未形成完整 migration chain，启动时依赖 `_migrate_missing_columns()` 自动 ALTER TABLE。该描述不代表 2026-07-12 当前状态。

**问题**:
- 只能加列，不能改列、删列、加约束、迁数据
- 多实例启动会竞态（目前靠 try/except 容错）
- 无 downgrade 路径
- ORM 定义 ≠ 实际 schema 时，错误会被静默吞掉

**修复**:
```powershell
cd apps/backend
alembic revision --autogenerate -m "initial schema v2.10"
alembic upgrade head
# 生产由 migration job 执行 DDL；backend startup 只校验 current==head
```

当前 `docker-compose.prod.yml` 已将 backend 绑定到 `migrate: service_completed_successfully`，后端自身还会执行只读 revision 校验。

---

### P0-4 · 生产镜像（multi-stage）· ✅ 已完成（2026-04-24）

- **实现**：
  - `@/apps/backend/Dockerfile` 改为 builder (gcc/libpq-dev 编译 venv) → runtime (libpq5/libmagic1/tini/非 root UID 10001/HEALTHCHECK) 两阶段。Python baseline 固定 **3.11**
  - `@/apps/backend/Dockerfile.dev` 新增 —— dev compose bind mount 专用，保留 root + 热重载
  - `@/apps/frontend/Dockerfile` 改为 node:20-alpine build → nginx:1.27-alpine runtime；`@/apps/frontend/nginx.conf` 提供 SPA 回退、`/api` 反代、gzip、长缓存、安全头
  - `@/apps/frontend/Dockerfile.dev` 新增 —— dev compose 用
  - `@/apps/backend/.dockerignore` + `@/apps/frontend/.dockerignore` 新增
  - `@/docker-compose.prod.yml` 新增 —— 非 bind mount、无 --reload、仅内网端口、`.env.prod` 强制、资源上限、healthcheck depends_on
  - `@/apps/backend/.env.prod.example` 模板
- **验证**：`docker compose -f docker-compose.yml config` + `docker compose -f docker-compose.prod.yml config` 双双 exit 0

---

#### 原始背景（已解决）

**现状**:`Dockerfile` / `docker-compose.yml` 是 dev 形态。

**后端**:
```dockerfile
# apps/backend/Dockerfile.prod
FROM python:3.13-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip wheel --wheel-dir=/wheels -r requirements.txt

FROM python:3.13-slim AS runtime
RUN useradd --create-home --shell /bin/bash app
WORKDIR /app
COPY --from=builder /wheels /wheels
RUN pip install --no-index --find-links=/wheels /wheels/*
COPY . .
USER app
EXPOSE 8000
CMD ["gunicorn", "app.main:app", "-k", "uvicorn.workers.UvicornWorker", "-w", "4", "-b", "0.0.0.0:8000"]
```

> **Python 版本**:代码实际跑在 3.13（pytest 运行环境）；Dockerfile 此前固定 3.11 有版本错配风险。请确认哪个版本作 baseline。

**前端**:
```dockerfile
# apps/frontend/Dockerfile.prod
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
```

**compose**:新建 `docker-compose.prod.yml`，env 统一走 `.env.production`，关闭 `--reload` 和源码挂载。

---

### P0-5 · CI/CD · ✅ 已完成（2026-04-24）

- **实现**：`@/.github/workflows/ci.yml` 三个 job：
  1. **backend** — alembic upgrade head 验证 + `python -m pytest tests/ -m "not online"` 离线全量测试；`@pytest.mark.online` 用于隔离真实 LLM、外网 API 和慢速在线链路
  2. **frontend** — `python scripts/run_frontend_release_checks.py --fail-on-error`，统一执行 npm audit、ESLint、TypeScript type-check、Vite production build，并上传 `frontend-release-checks-latest.json` artifact
  3. **docker-build** — buildx 多阶段镜像 + GHA cache；启动 backend/frontend 容器冒烟测试 `/health`
- **生产放行证据**：`@/scripts/run_frontend_release_checks.py` 会把前端 npm audit、ESLint、TypeScript type-check 和 Vite production build 写入 `@/docs/verification/frontend-release-checks-latest.json`；CI 和 `run_production_readiness_refresh.py` 使用同一入口，`summarize_production_readiness.py` 将该 latest 报告作为必需项。
- **触发**：push / PR to main / workflow_dispatch；concurrency cancel-in-progress
- **回归保护**：`@/apps/backend/pytest.ini` 注册 `online` marker；`@/apps/backend/tests/test_dependency_security_baseline.py` 校验 CI 仍执行离线全量策略，避免安全清单再次漂移。

#### 原始背景（已解决）

**现状**:无 `.github/workflows/`、无 `.gitlab-ci.yml`。

**最小 `.github/workflows/ci.yml`**:
```yaml
name: CI
on: [push, pull_request]
jobs:
  backend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.13' }
      - run: pip install -r apps/backend/requirements.txt
      - run: cd apps/backend && python -m pytest tests/ --tb=line -q
        # 注:LLM 在线用例在 CI 需提供 OPENROUTER_API_KEY secret 或按 marker 跳过
  frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: '20' }
      - run: cd apps/frontend && npm ci && npx tsc --noEmit
```

---

### P0-6 · 持久化摄取队列与 Worker 隔离 · ✅ 代码完成（2026-07-12）

- 本地/demo 使用有界线程池，生产 `INGESTION_DISPATCHER_MODE` 必须解析为 `celery`。
- Celery 5.6 + Redis 使用 JSON 标识符负载、`acks_late=true`、prefetch 1、软/硬时限和大于硬时限至少 300 秒的 visibility timeout。
- `JobRun` 是业务真相；完成态幂等、有效租约、行锁领取和“来源已完成但 JobRun 未关闭”的崩溃窗口均有回归测试。
- `docker-compose.prod.yml` 提供独立 `ingestion_worker`；backend 依赖 Worker 健康，`/health/ready` 和 release readiness 在生产强制 Worker 可达。
- 当前认证边界是单 backend + 独立 Worker。水平扩展 backend 前必须增加原子分布式接纳配额并完成并发演练。
- 真实 Linux 容器烟测已验证任务注册、Redis 投递、完成态租约、一次幂等重投和 claim 不重复；真实生产放行仍要求发布负责人提供 `.env.prod`、受控密钥来源、基础设施、TLS、监控接收端、部署与恢复演练证据。

---

## 🟡 P1 - 上线后 1 周内

### P1-6 · `/health` 深检 · ✅ 已完成（2026-04-24）

- **实现**：`@/apps/backend/app/api/v1/health_deep.py`
  - `GET /health` / `GET /health/live` —— liveness（不检查下游，进程活即 200）
  - `GET /health/ready` —— readiness：并行探测 DB / Redis / MinIO / OpenRouter / ingestion worker，每个检查独立超时
  - 生产状态机：DB、OpenRouter、Redis、MinIO 或 ingestion worker 任一失败 → 503 unhealthy；本地可选依赖失败 → 200 degraded；全通 → 200 ready
- **k8s probe 模板**：
  ```yaml
  livenessProbe:  { httpGet: { path: /health/live, port: 8000 }, periodSeconds: 10 }
  readinessProbe: { httpGet: { path: /health/ready, port: 8000 }, periodSeconds: 15 }
  ```
- **验证**：`@/apps/backend/tests/test_health_deep.py` 9/9 passed

### P1-7 · Rate Limiting · ✅ 已完成（2026-04-24）

- **实现**：`@/apps/backend/app/observability/rate_limit.py`
  - slowapi `Limiter(key_func=_key_func)` —— 按 `X-API-Key` 前 16 位分桶，无 key 回退 IP
  - 全局 `SlowAPIMiddleware` + 自定义 429 handler（响应含 `request_id` + `Retry-After` 头）
  - **不设 default_limits**（避免对非装饰端点强制要求 `response: Response` 参数）
- **关键端点**（显式 `@limiter.limit(...)` 装饰）：
  - `POST /api/v1/chat/sessions/{id}/chat` → `RATE_LIMIT_CHAT` = 30/min
  - `POST /api/v1/briefings` → `RATE_LIMIT_BRIEFING` = 20/min
  - `POST /api/v1/verification/run-all` → `RATE_LIMIT_VERIFICATION` = 3/min
- **多实例部署**：改 `storage_uri="redis://redis:6379"` 即可跨节点共享

### P1-8 · 结构化日志 + 轮转 · ✅ 已完成（2026-04-24）

- **实现**：`@/apps/backend/app/logging_config.py`
  - `LOG_FORMAT=json`（生产）| `pretty`（dev，彩色）
  - 按日滚动 + `LOG_RETENTION_DAYS` 天保留 + gzip 压缩
  - `enqueue=True` 多进程安全
- **与 request_id 联动**：`RequestIDMiddleware` 用 `logger.contextualize(request_id=...)`，同请求所有日志都带 `request_id` 字段

### P1-9 · Sentry + 全局异常 · ✅ 已完成（2026-04-24）

- **实现**：
  - `@/apps/backend/app/observability/sentry.py` —— 空 DSN 自动跳过 init；集成 FastAPI/Starlette/Logging/SQLAlchemy
  - `@/apps/backend/app/observability/exception_handler.py` —— 三层处理器：
    - `StarletteHTTPException` → 保持原 status，body 加 `request_id`
    - `RequestValidationError` → 422 + 字段清单 + `request_id`
    - 任何其他 `Exception` → 500 + `request_id`（生产不透异常内部，dev 加 `exception_type`/`exception_message`）
  - 所有 500 响应头和 body 都带 `X-Request-Id`（配合 `@/apps/backend/app/observability/request_id.py` 纯 ASGI 中间件）
- **验证**：`@/apps/backend/tests/test_observability.py::TestExceptionHandler` 2/2 passed

### P1-10 · Prometheus `/metrics` · ✅ 已完成（2026-04-24）

- **实现**：`@/apps/backend/app/observability/metrics.py` 用 `prometheus-fastapi-instrumentator`
  - 自动采集 `http_request_duration_seconds{handler,method,status}` / `http_requests_total` / 载荷大小 / in-progress
  - `/metrics` 白名单放行，默认 gzip
  - 生产建议 ingress 限制内网 IP 访问
- **后续扩展**（未做）：`llm_call_total{model,module}` / `claim_ingest_total{status}` / `scheduler_job_status{job_name}` 业务指标
- **验证**：`@/apps/backend/tests/test_observability.py::TestMetrics` 2/2 passed

### P1-11 · Secret Manager · ⏳ 待办

生产部署前切换到以下之一：
- docker secrets
- K8s `Secret` + `envFrom`
- AWS Parameter Store / HashiCorp Vault

目前 `docker-compose.prod.yml` 走 `.env.prod` 文件，已足够单机部署。规模化再迁移。

### P1-12 · 依赖对齐 · ✅ 已完成（2026-04-24）

- `@/apps/backend/requirements.txt` 已追加：`sentry-sdk[fastapi]>=2.10.0` / `prometheus-fastapi-instrumentator>=7.0.0` / `slowapi>=0.1.9`
- Python baseline 固定 **3.11**（Dockerfile 两阶段镜像 + dev Dockerfile 一致；pytest 可在 3.13 验证）

---

## 🟢 P2 - 规模化前

- **Scheduler 独立进程**：现有 Redis 分布式锁保留；若水平扩展 backend，再把周期调度从 API 进程拆出
- **PostgreSQL + pgvector 生产切换**：`DATABASE_URL` + `PGVECTOR_ENABLED=True`
- **备份 / 恢复**：详见 `@/docs/operations/BACKUP_RESTORE.md`
- **负载测试**：locust / k6，至少覆盖 `/chat`、`/ingestion`、`/briefings`
- **API 版本迁移策略**:`v2` 路由冻结 `v1` 行为

---

## 最终验收 Gate

上线前必须全部 ✅：

- [ ] P0-1 ~ P0-6 全部完成并以当前代码指纹重新回归；不要依赖历史固定用例数
- [ ] `docker-compose -f docker-compose.prod.yml up` 可独立起栈
- [ ] 用非法 API Key 访问任一 `/api/v1/**` 端点返回 401
- [ ] Chrome DevTools → Network 看 `/chat` 响应头含 `Access-Control-Allow-Methods: GET, POST, ...`（不是 `*`）
- [ ] `alembic upgrade head` 在空库上成功，且迁移后表/列集合覆盖当前 ORM metadata
- [ ] `pytest tests/ -q` 在 CI 上自动跑（GitHub Actions 绿灯）
