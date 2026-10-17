# 负载测试（P2-D）

> 状态：生产方向的参考测试，未纳入 local v0.1 验收。脚本仍可用于未来显式的负载
> 目标，但当前结果不能用于生产容量声明。

> Locust 脚本 · 验证后端在并发负载下的健康度（连接池、锁、event loop 不崩）
> 关联文档：`@/docs/operations/DEPLOYMENT_RUNBOOK.md` §6

---

## 1. 安装

负载测试依赖（locust + gevent）和生产 image 解耦，建议独立 venv：

```powershell
# Windows
python -m venv .venv-load
.venv-load\Scripts\activate
pip install -r tests/load/requirements.txt
```

```bash
# Linux / Mac
python3 -m venv .venv-load
source .venv-load/bin/activate
pip install -r tests/load/requirements.txt
```

---

## 2. 启动后端（被测目标）

负载测试需要后端实际运行。建议跑 **生产 build**（不要 dev reload，结果会失真）：

```powershell
# 在 backend 目录下
$env:APP_ENV = "test"  # 关掉 scheduler，避免和 load 流量打架
$env:DATABASE_URL = "sqlite:///./dev.db"  # 或生产 PG URL
$env:OPENROUTER_API_KEY = "fake"  # 不会真打 LLM（除非启用写入场景）
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

> **注意**：若后端启用了 `APP_API_KEY` 鉴权，给 locust 设置 `LOAD_API_KEY` 环境变量。

---

## 3. 运行 locust

### 3.1 WebUI 模式（推荐做交互调优）

```powershell
locust -f tests/load/locustfile.py --host http://localhost:8000
```

打开 <http://localhost:8089>：
- **Number of users**：并发用户峰值（建议从 50 开始）
- **Spawn rate**：每秒新增用户数（建议 5）
- **Host**：被测后端地址（已通过 -f --host 设了）

### 3.2 Headless 模式（CI / 跑 baseline 留档）

```powershell
# 50 并发持续 5 分钟，输出 CSV + HTML
locust -f tests/load/locustfile.py --headless `
    -u 50 -r 5 -t 5m --host http://localhost:8000 `
    --csv reports/load --html reports/load.html
```

输出文件：
- `reports/load_stats.csv` — 各端点 p50/p95/p99/RPS
- `reports/load_failures.csv` — 失败明细
- `reports/load_stats_history.csv` — 时序数据（画趋势图用）
- `reports/load.html` — 含图表的整合报告

---

## 4. 配置（环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `LOAD_HOST` | `http://localhost:8000` | 被测 host（也可用 `--host` 覆盖） |
| `LOAD_API_KEY` | `""` | `X-API-Key` header 值（生产开启鉴权时必填） |
| `LOAD_INCLUDE_WRITES` | `0` | `1`=启用写入场景（POST /observations） |
| `LOAD_LLM_MOCK` | `0` | `1`=启用 LLM 写入场景（chat / briefing），**前提：后端必须先开 `OPENROUTER_MOCK=1`** |
| `LOAD_FAIL_ON_5XX_ONLY` | `1` | `1`=只 5xx 算失败；`0`=4xx 也算 |

### 后端配套：LLM Mock 模式

启用 `LOAD_LLM_MOCK=1` 之前，必须给被测后端设 `OPENROUTER_MOCK=1`，否则 chat / briefing 路径会真实调用 OpenRouter，瞬间烧光 API quota。

启动被测后端时：

```powershell
$env:OPENROUTER_MOCK = "1"
$env:OPENROUTER_MOCK_LATENCY_MS = "50"   # 模拟一段网络延迟（可选，让 P95 更接近真实）
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

然后压测：

```powershell
$env:LOAD_INCLUDE_WRITES = "1"
$env:LOAD_LLM_MOCK = "1"
locust -f tests/load/locustfile.py --host http://localhost:8000
```

`OPENROUTER_MOCK=1` 行为：
- `llm_client.complete*` 跳过 OpenRouter，按 `skill_role` 返回结构化 mock JSON / 文本
- `llm_client.embed()` 返回基于文本 hash 的确定性 L2-normalized 向量（dim=`EMBEDDING_DIM`）
- `llm_client.ping_model()` 返回 `success=True`，让 `/health/ready` / `system-doctor` 等运维端点也走通
- mock 调用同样填充 `_last_usage`（cost=0），保证 cost_scope / A2 累计逻辑不爆

详见 `apps/backend/app/utils/llm_client.py:_mock_response_skill_aware()`。

---

## 5. 场景与权重

| 场景 | weight | 端点 | 说明 |
|---|---|---|---|
| `HealthUser` | 35 | `/health/live` `/health/ready` `/metrics` | k8s probe + Prometheus scrape，必须极快 |
| `WatchlistUser` | 20 | `GET /api/v1/watchlists` `GET /watchlists/{id}` | 首页 list + detail |
| `ClaimsUser` | 20 | `GET /api/v1/claims/?asset=...` | 资产详情，多 filter 走索引 |
| `BriefingsUser` | 10 | `GET /api/v1/briefings/` | 已生成 briefing 列表（不调 LLM） |
| `SnapshotsUser` | 10 | `GET /api/v1/snapshots/latest` | market overview，可能命中缓存 |
| `SearchUser` | 5 | `POST /api/v1/knowledge/search` | 语义搜索（embedding 计算敏感） |
| `WriteUser` | 3（条件） | `POST /api/v1/observations/` | 仅 `LOAD_INCLUDE_WRITES=1` 时启用 |
| `ChatUser` | 2（条件） | `POST /chat/sessions/:id/chat` | 全链路 chat（intent/rewrite/search/fusion）；需 `LOAD_LLM_MOCK=1` |
| `BriefingUser` | 1（条件） | `POST /briefings` | 全链路 briefing 生成；需 `LOAD_LLM_MOCK=1` |

权重之和决定了用户分布。50 并发时大约：
- HealthUser ≈ 17（高频）
- WatchlistUser / ClaimsUser ≈ 各 10
- BriefingsUser / SnapshotsUser ≈ 各 5
- SearchUser ≈ 2-3
- WriteUser ≈ 1-2（如启用）
- ChatUser ≈ 1（如开启 LLM mock）
- BriefingUser ≈ 1（如开启 LLM mock）

---

## 6. 解读报表

### 6.1 健康基线（参考目标）

P2-D **建议阈值**（生产 SLO，dev 机器宽松些）：

| 指标 | 目标 | 红线 |
|---|---|---|
| `/health/live` p95 | < 50ms | > 200ms |
| `/health/ready` p95 | < 200ms | > 1s |
| `/metrics` p95 | < 300ms | > 1s |
| `GET /watchlists` p95 | < 500ms | > 2s |
| `GET /claims` p95 | < 800ms | > 3s |
| `POST /knowledge/search` p95 | < 2s | > 8s |
| 整体失败率 | < 1% | > 5% |

> 红线触发即视为生产不可用，需立即排查瓶颈（DB pool 满 / 锁竞争 / event loop 卡）。

### 6.2 排错

- **大量 5xx**：看 backend log 是否 DB pool exhausted（`SQLAlchemy QueuePool limit`）
- **p95 飙升但 p50 正常**：尾延迟大，常见是 LLM call / 长 query / GIL 竞争
- **/metrics 慢**：Prometheus instrumentator 在每次请求扫所有 collector，超大 cardinality 标签会拖累
- **整体 RPS 上不去**：单进程 event loop 已饱和，加 `--workers` 或上 gunicorn

### 6.3 锁验证（P2-B 专项）

跑 50+ 并发 5 分钟时，观察 backend log：
- `[Scheduler] auto_briefing` / `lifecycle_check` 不应出现并发重复跑（同时间戳同 job）
- `[DistLock] 'scheduler:xxx' held elsewhere, skip` 是健康信号

---

## 7. CI 集成（建议）

加到独立 workflow（不进 PR check，每周 1 次或手动触发）：

```yaml
# .github/workflows/load-test.yml
name: load-test
on:
  workflow_dispatch:
  schedule:
    - cron: "0 8 * * 1"  # 每周一 UTC 08:00
jobs:
  load:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker compose -f docker-compose.prod.yml up -d
      - run: pip install -r tests/load/requirements.txt
      - run: |
          locust -f tests/load/locustfile.py --headless \
                 -u 50 -r 5 -t 5m --host http://localhost:8000 \
                 --csv reports/load --html reports/load.html
      - uses: actions/upload-artifact@v4
        with: { name: load-report, path: reports/ }
```

退出码非 0（失败率超 5%）会导致 workflow fail。

---

## 8. 后续改进（不在 P2-D 范围内）

- **LLM mock 集成**：让 chat / briefing 写入场景可跑（需 backend 加 `OPENROUTER_MOCK=1` 开关）
- **历史趋势对比**：保存 baseline → 每次跑后比对 p95 退化 > 20% 报警
- **分布式压测**：单机 locust 受限于 CPU，需要时上 master + worker 多机
- **真实流量录制回放**：用 GoReplay 抓生产流量回放，比合成场景更准
