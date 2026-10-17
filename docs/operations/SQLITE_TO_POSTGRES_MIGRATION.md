# SQLite → Postgres 迁移 Runbook（P2-E + P3 doctor pass 增强）

> **操作参考，未纳入本地 v0.1 验收**：当前接受的数据库是本地 SQLite。迁移前须
> 在副本上重新完成 schema、行数、外键、向量和回滚验证。

> 落地 2026-04-25 · P3 doctor pass 增强 2026-04-25·
> 关联脚本：`@/infra/migration/sqlite_to_postgres.py` · `@/infra/migration/verify_migration.py`
> 关联文档：`@/docs/operations/DEPLOYMENT_RUNBOOK.md` · `@/docs/operations/BACKUP_RESTORE.md`
> · `PRE_LAUNCH_DRILL.md` (historical reference excluded from public source) — dev.db 14 220 行 迁跱实报告
>
> **doctor pass 发现**：SQLite 默认 `PRAGMA foreign_keys=OFF`，长期开发库可能累积 FK 孤儿；PG 严格拒绝。
> dry-run 阶段必须先看孤儿报告，决定迁前清理还是用 `--skip-fk-orphans`（详 §3.5 新增）。

---

## 0. 适用场景

- **首次上云**：dev 用 SQLite 跑通业务，生产切 Postgres + pgvector
- **从单机扩到多实例**：SQLite 锁是单进程瓶颈，多 backend pod 必须 PG
- **要用 pgvector 加速语义搜索**：当前 dev SQLite 走 `sqlite-vec` Tier ②，PG 上走 Tier ① pgvector HNSW

不适用：
- **PG → SQLite 反向迁移**：本脚本不支持（开发场景没需求）
- **跨机房 PG → PG 迁移**：直接用 `pg_dump` / `pg_restore`（见 BACKUP_RESTORE.md §3）

---

## 1. 跨方言坑列表（已经全部处理）

| 项 | SQLite | Postgres | 处理方式 |
|---|---|---|---|
| `claim_cards.embedding` | TEXT (JSON 字符串) | `vector(2560)` (pgvector) 或 TEXT | 脚本解析 JSON → `list[float]`，psycopg+pgvector adapter 自动转 vector 字面量 |
| JSON 字段（`asset_scope` 等） | JSON (TEXT 内部) | JSONB | SQLAlchemy `JSON` 类型自动跨方言 |
| ULID 主键 | `String(26)` (TEXT) | `String(26)` (VARCHAR) | 无差异 |
| `DateTime(timezone=True)` | TEXT (ISO 字符串) | TIMESTAMPTZ | SQLAlchemy 自动 parse |
| Boolean | INTEGER 0/1 | BOOLEAN | SQLAlchemy 自动 |
| Bigint sequence | `INTEGER PRIMARY KEY AUTOINCREMENT` | `SERIAL` | **不存在** — 全部用 ULID 主键 |
| `vec_*` 虚拟表 | sqlite-vec 管理 | pgvector 列管理 | 反射时跳过；PG 端 alembic 0009 建 `claim_cards.embedding` 列 + HNSW 索引 |
| `alembic_version` | 1 行 | 1 行 | 不通过迁移；PG 上单独 `alembic upgrade head` |
| `sqlite_sequence` | SQLite 内部 | — | 跳过 |

---

## 2. 前置条件

### 2.1 Postgres 已起 + pgvector 扩展可用

```bash
# 用项目 docker-compose（推荐）
docker compose -f docker-compose.prod.yml up -d postgres

# 验证
docker compose exec postgres psql -U agent_user -d agent_db -c \
    "CREATE EXTENSION IF NOT EXISTS vector; SELECT extversion FROM pg_extension WHERE extname='vector';"
```

应输出版本号（如 `0.7.4`）。`infra/postgres/init.sql` 已包含 `CREATE EXTENSION IF NOT EXISTS vector`，新建容器时自动装。

### 2.2 PG 端 alembic 升至 head

必须升到 **`0012_drill_pass_fixes`**（P3 doctor pass 补上了 `model_registry` / `key_groups` 两张表）。

```powershell
# Windows
$env:DATABASE_URL = "postgresql://agent_user:****@localhost:5432/agent_db"
$env:PGVECTOR_ENABLED = "true"
$env:EMBEDDING_DIM = "2560"
cd apps\backend
alembic upgrade head
```

```bash
# Linux
DATABASE_URL=postgresql://agent_user:****@localhost:5432/agent_db \
PGVECTOR_ENABLED=true EMBEDDING_DIM=2560 \
  alembic -c apps/backend/alembic.ini upgrade head
```

确认：

```bash
alembic current
# 0012_drill_pass_fixes (head)
alembic check
# No new upgrade operations detected.   （双方言 zero drift）
```

### 2.3 SQLite 端备份（**必做**）

迁移前用 P2-C 备份脚本快照一份，作为应急回滚源：

```powershell
$env:DATABASE_URL = "sqlite:///apps/backend/dev.db"
python infra/backup/backup.py --type manual
```

产物：`backups/manual/manual-sqlite-dev-<TIMESTAMP>.db.gz`。**手动备份 retention 永不清理**，可放心保留。

### 2.4 后端必须停服

迁移期间不应有写入：

```powershell
# 停 backend（如用 docker compose）
docker compose -f docker-compose.prod.yml stop backend

# 或停本地 uvicorn 进程
```

---

## 3. 执行迁移

### 3.1 dry-run（先跑这一步看行数对照）

```powershell
python infra/migration/sqlite_to_postgres.py `
    "sqlite:///apps/backend/dev.db" `
    "postgresql://agent_user:****@localhost:5432/agent_db" `
    --dry-run
```

输出形如：

```text
[migrate] ORM 共 34 表（按 FK 拓扑序）
[migrate] 行数对照表：
  raw_sources                                21 rows
  speaker_profiles                           13 rows
  claim_cards                               164 rows
  ...
```

确认你预期看到的所有表都在，行数也对得上。如果有意外的 0 行表，要先排查 dev.db 数据是否完整。

### 3.2 真跑（**干净路径**）

如果 §3.5 dry-run 报告 0 孤儿跳过次节，直接跑干净路径：

```powershell
python infra/migration/sqlite_to_postgres.py `
    "sqlite:///apps/backend/dev.db" `
    "postgresql://agent_user:****@localhost:5432/agent_db"
```

预期输出：

```text
[truncate] 反向清空 dest 34 表（PG TRUNCATE CASCADE）...
  [OK] TRUNCATE CASCADE 完成

[migrate] 开始迁移（FK 正序 INSERT）...
  [OK] raw_sources                              21 rows  (0.05s)
  [OK] speaker_profiles                         13 rows  (0.02s)
  [OK] claim_cards                             164 rows  (0.45s)   ← embedding 较慢（向量序列化）
  ...
[migrate] 完成：源 14056 行 → 目标 14056 行
[migrate] OK
```

**退出码**：

| Code | 含义 |
|---|---|
| 0 | 全部成功 + 行数一致 |
| 1 | 参数 / 连接错 |
| 2 | 至少一张表插入失败 |
| 3 | 迁移完成但 src/dst 行数不一致（异常情况，需排查） |

### 3.3 FK 孤儿处理（P3 doctor pass，**生产上线实战**）

**场景**：SQLite 不强制 FK，长期开发可能累积 “子表引用不存在的父表 PK” 的孤儿行。
迁到 PG 会 `ForeignKeyViolation`。

**检测**：dry-run 报告每张表每列的孤儿行数：

```powershell
python infra/migration/sqlite_to_postgres.py `
    "sqlite:///apps/backend/dev.db" $env:DATABASE_URL --dry-run
# 输出示例：
# [migrate] FK 孤儿扫描（SQLite 源默认不强制 FK，PG 会拒绝）：
#   [WARN] setup_cards.strategy_id->strategy_cards.id: 8 孤儿行
#   小计：8 行需迁前处理或用 --skip-fk-orphans 过滤
```

**三选一**：

| 选项 | 适用 | 函数 |
|------|------|------|
| **A. 迁前清理 SQLite** | 你愿意保证一部分数据丢失、且看得出是误误产生的孤儿 | `DELETE FROM 子表 WHERE FK NOT IN (SELECT pk FROM 父表)` 后重跑 dry-run |
| **B. `--skip-fk-orphans`（推荐）** | 你保留 src 不动，只让 dst 跳过这些孤儿；不变原始数据 | 下节 |
| **C. 临时关 FK 检查** | 生产不推荐。孤儿进 PG 会调 SET CONSTRAINTS，不可使用 `潮连` | 本 runbook 不推荐 |

**B. `--skip-fk-orphans` 详解**：

```powershell
python infra/migration/sqlite_to_postgres.py `
    "sqlite:///apps/backend/dev.db" $env:DATABASE_URL --skip-fk-orphans
# 输出示例：
#   [OK] setup_cards    67 rows  (-8 orphans)
#   ... briefing_cards 被同步跳过（3 doctor pass 坑 #6）
# [migrate] 完成：源 14220 行 → 目标 14204 行
```

**关键**：
- 过滤以 **dst 已迁移的父表 PK** 为准（不是 src）。举例说明：briefing.setup_id 指向一个被跳过的 setup，那 briefing 也永远进不了 PG。不会产生级联错误。
- **检查 stderr 的 “-N orphans” 后缀**：这是本表实际跳过的行数，记在迁移日志中。
- **`--allow-orphans` 与之配套**：verify 仅在预算内允许不一致，超则报 error。

**警告**：不要默认加 `--skip-fk-orphans`。dry-run 报告 0 孤儿的干净库不需要，
加了会护住未来 SQL/ORM bug 产生的合法 FK 错误。

### 3.4 环境变量解析陷阱（P3 doctor pass）

**不要**这么设环境变量期望关闭某个 flag：

```powershell
$env:MIGRATION_DRY_RUN = "false"  # 脚本会当作 true 处理
$env:MIGRATION_DRY_RUN = "0"      # 同上
```

现在脚本使用 `_env_bool()` （truthy 白名单： `1/true/yes/on/y`，不区分大小写）。
谭了环境变量请设 `"1"` 或 `"true"`，不设则不作为。`MIGRATION_SKIP_FK_ORPHANS` 同调。

### 3.5 大表性能参考

| 表 | 行数 | 耗时（本地 SQLite → 本地 PG） |
|---|---|---|
| `source_health_logs` | 4k | ~0.5s |
| `market_snapshots` | 3.5k | ~0.4s |
| `processing_traces` | 2k | ~0.3s |
| `claim_cards` | 200 | ~0.5s（embedding 序列化拖慢） |
| **合计** | **14k** | **~5-10s** |

迁移 10w 行级数据库预计 1-2 分钟。

---

## 4. 验证

```powershell
python infra/migration/verify_migration.py `
    "sqlite:///apps/backend/dev.db" `
    "postgresql://agent_user:****@localhost:5432/agent_db"
```

5 项检查：
1. **行数对照**：每张 ORM 表 src/dst 完全一致
2. **alembic_version**：dst 应为 `0009_pgvector_embedding`
3. **embedding round-trip**：抽 5 行检查向量维度合理（≥ 100）
4. **PK 抽样**：随机 3 张表抽 5 个 PK，dst 端必须能查到
5. **FK 完整性**：`claim_*_links` / `lifecycle_logs` 等不应指向不存在的父表

退出码：

| Code | 含义 |
|---|---|
| 0 | 全过 |
| 2 | 严重问题（行数 / FK 残断） |
| 3 | 仅 warning（如 alembic_version 缺失）但数据 OK |

---

## 5. 切流量到 PG

### 5.1 改 backend `DATABASE_URL`

`.env.production` 或 docker-compose 环境变量：

```bash
DATABASE_URL=postgresql://agent_user:****@postgres:5432/agent_db
PGVECTOR_ENABLED=true
EMBEDDING_DIM=2560
```

### 5.2 启 backend + smoke test

```powershell
docker compose -f docker-compose.prod.yml up -d backend
# 等 30s
curl http://localhost:8000/health/ready
# {"status":"ok","database":"ok",...}
```

走一遍核心 API：

```powershell
# 列 watchlists（验证 read 通路 + DB pool）
curl http://localhost:8000/api/v1/watchlists

# 抽一个 claim 详情（验证关联 query）
curl http://localhost:8000/api/v1/claims/?asset=BTC&limit=5

# 语义搜索（验证 pgvector）
curl -X POST http://localhost:8000/api/v1/knowledge/search `
    -H "Content-Type: application/json" `
    -d '{"query":"BTC outlook","limit":3}'
```

如果 search 输出含 `tier: "pgvector"`，说明 P2-A pgvector 链路正常工作。

### 5.3 跑 P2-D 负载测试 baseline

```powershell
# 见 tests/load/README.md
locust -f tests/load/locustfile.py --headless `
    -u 50 -r 5 -t 5m --host http://localhost:8000 `
    --csv reports/load-postgres-baseline --html reports/load-postgres.html
```

对比 SQLite 版本的 baseline，核心指标（特别是 `/knowledge/search` p95）应明显改善。

---

## 6. 回滚

> 万一切到 PG 后业务异常严重（数据错乱、p95 飙升、外部依赖兼容问题），可在 5 分钟内回到 SQLite。

```powershell
# 1. 停 backend
docker compose -f docker-compose.prod.yml stop backend

# 2. 改 DATABASE_URL 回 SQLite
# .env.production:
DATABASE_URL=sqlite:///app/dev.db
PGVECTOR_ENABLED=false

# 3. 如果迁移期间 SQLite 没动过，直接重启
docker compose -f docker-compose.prod.yml up -d backend

# 4. 如果不放心，从 §2.3 的备份恢复
python infra/backup/restore.py "sqlite:///apps/backend/dev.db" `
    backups/manual/manual-sqlite-dev-<TS>.db.gz
```

PG 端的数据可保留作"双写过渡期"参考；后续真要切回时再清理。

---

## 7. 数据漂移监控（迁移后双写期）

如果上云后想保留几天 SQLite 副本作为应急回退源（双写期），用 cron 每天对比一次：

```powershell
# /etc/cron.daily 或 Windows 任务计划
python infra/migration/verify_migration.py `
    "sqlite:///app/dev.db" `
    "postgresql://agent_user:****@postgres:5432/agent_db"
# exit code != 0 → webhook 送警
```

---

## 8. 已知限制

- **不迁移 sqlite-vec `vec_*` 虚拟表**：PG 端用 pgvector 重建 embedding 列即可，索引在迁移完后跑 `alembic upgrade head` 时自动建（`0009_pgvector_embedding.py`）
- **不迁移 `alembic_version`**：必须先在 PG 端 `alembic upgrade head`
- **embedding > 2000 维不建 HNSW 索引**：pgvector HNSW 上限 2000；当前 EMBEDDING_DIM=2560 走顺序扫描 + `<=>` cosine 距离算子，百万级 claim 内性能可接受
- **不支持 schema 漂移自适应**：src 多列 → dst 自动忽略；dst 多列 → 走 default / NULL；列类型不一致 → 失败（应在 `alembic upgrade head` 阶段保证 schema 一致）

---

## 9. 附：FK 拓扑顺序参考

按 FK 依赖正向（INSERT）：

```text
raw_sources, speaker_profiles, key_groups, model_registry, fund_profiles,
market_snapshots, source_health_logs, watchlists, decision_snapshots, ...
→ evidence_spans (depends raw_sources)
→ claim_cards (depends raw_sources, speaker_profiles)
→ claim_raw_source_links / claim_evidence_links / lifecycle_logs (depends claim_cards)
→ briefing_cards
→ briefing_evidence_links (depends briefing_cards, evidence_spans)
→ debate_records, outcome_records
→ debate_rounds, final_adjudications (depends debate_records)
```

脚本用 `Base.metadata.sorted_tables` 自动算这个顺序，runbook 里只列前 3 层供参考。

---

## 10. 测试覆盖

`@/apps/backend/tests/test_migration_scripts.py` 11 条单元测试（不依赖外部 PG）：
- `_is_skipped` / `_convert_row_for_dst` / `_reflect_src_safe` 各项行为
- 端到端 round-trip（SQLite → 临时 SQLite，验证行数 + embedding JSON 完整保留）
- dry-run 不写 dst
- verify 通过 / verify 检测出 row count mismatch

跑法：`pytest tests/test_migration_scripts.py -v`
