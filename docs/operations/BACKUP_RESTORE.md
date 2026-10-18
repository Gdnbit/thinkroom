# Backup & Restore

> **操作参考，未纳入本地 v0.1 验收**：执行前必须在目标环境重新验证工具、路径、
> 凭据、保留策略和真实恢复结果。

> 最近更新:2026-04-25（P2-C 自动化脚本就位 · P3 doctor pass 加入 docker exec 模式）
> 关联文档:`@/docs/operations/DEPLOYMENT_RUNBOOK.md` · `PRE_LAUNCH_DRILL.md` (historical reference excluded from public source)
>
> **生产集群常踩坑**：宿主机不装 `postgresql-client`（镜像足够），脚本默认走 PATH 找 `pg_dump`/`pg_restore`
> 会失败。P3 doctor pass 在隔离容器实测时打通了 docker exec 模式（§0.5 新增），生产唯一可行路径。

---

## 0. 自动化脚本（推荐入口，2026-04-25/26）

> ✨ **G6 一键安装**（2026-04-25）：30 min 设置消除 90% 个人 dev 模式数据丢失风险。
>
> **Windows**（管理员 PowerShell）：
>
> ```powershell
> # 每天 02:00 daily 备份；可选 -WeeklyAlso 加每周一 03:00 weekly
> .\infra\backup\install-windows-task.ps1 -WeeklyAlso
>
> # 卸载
> .\infra\backup\install-windows-task.ps1 -Uninstall
> ```
>
> **Linux/macOS**（用户 cron）：
>
> ```bash
> # 用 marker 注释幂等管理 user crontab
> infra/backup/install-cron.sh
> infra/backup/install-cron.sh --weekly      # 同时加 weekly
> infra/backup/install-cron.sh --uninstall   # 一键移除
> ```
>
> **特性**：StartWhenAvailable=true 关机错过自动重补；MultipleInstances=IgnoreNew 防重复；4h 执行时长上限；marker 注释让 cron 不污染其他任务。详见 `@/infra/backup/README.md`。

P2-C 落地了 3 个跨平台 Python 脚本，集中放在 `@/infra/backup/`：

| 脚本 | 用途 |
|---|---|
| `backup.py` | 备份 DB（PG `pg_dump --format=custom` 或 SQLite 在线 `.backup`）+ uploads tar.gz；按 type 自动 retention |
| `restore.py` | 恢复（pg_restore --clean 或替换 .db.gz）；带交互确认 + 应急回退副本 |
| `verify.py` | 备份完整性检查：存在性 / 新鲜度 / 文件大小 / 内容 magic |

### 0.1 一键日备份

```powershell
# Windows / PowerShell
$env:DATABASE_URL = "postgresql://agent_user:****@localhost:5432/agent_db"
$env:UPLOADS_DIR = "E:\path\to\uploads"
$env:BACKUP_DIR  = "E:\backups\ros"
python infra/backup/backup.py --type daily
python infra/backup/verify.py --quiet
```

```bash
# Linux / bash
export DATABASE_URL=postgresql://agent_user:****@localhost:5432/agent_db
export UPLOADS_DIR=/opt/ros/uploads
export BACKUP_DIR=/var/backups/ros
python3 infra/backup/backup.py --type daily
python3 infra/backup/verify.py --quiet
```

### 0.2 cron / 任务计划范例

**Linux cron**（`/etc/cron.d/ros-backup`）：

```cron
# m  h  dom  mon  dow  user  command
0    3  *    *    *    root  cd /opt/ros && DATABASE_URL=$ROS_DB_URL python3 infra/backup/backup.py --type daily   --quiet
0    4  *    *    1    root  cd /opt/ros && DATABASE_URL=$ROS_DB_URL python3 infra/backup/backup.py --type weekly  --quiet
0    5  1    *    *    root  cd /opt/ros && DATABASE_URL=$ROS_DB_URL python3 infra/backup/backup.py --type monthly --quiet
30   3  *    *    *    root  cd /opt/ros && python3 infra/backup/verify.py --quiet || curl -fsS -X POST $WEBHOOK
```

**Windows 任务计划**：

```powershell
# 每天 03:00 执行（管理员 PowerShell）
$action  = New-ScheduledTaskAction -Execute "python.exe" -Argument "C:\example\research-os\infra\backup\backup.py --type daily --quiet"
$trigger = New-ScheduledTaskTrigger -Daily -At 3am
Register-ScheduledTask -TaskName "ros-backup-daily" -Action $action -Trigger $trigger -RunLevel Highest
```

### 0.3 保留策略

由 `--type` 决定，环境变量可覆盖：

| Type | 默认保留 | 环境变量 |
|---|---|---|
| daily   | 7 份 | `BACKUP_RETENTION_DAILY` |
| weekly  | 4 份 | `BACKUP_RETENTION_WEEKLY` |
| monthly | 12 份 | `BACKUP_RETENTION_MONTHLY` |
| manual  | ∞ 不清理 | — |

### 0.4 docker exec 模式（生产无 PG 客户端的唯一可行路径，P3 doctor pass）

生产宿主默认不装 `postgresql-client`（PG 容器内已自带），脚本默认走 PATH 找 `pg_dump` 会失败：

```text
[Backup] ✗ postgres   pg_dump 不在 PATH；安装 postgresql-client，
          或设置 BACKUP_PG_DUMP='docker exec ros_postgres pg_dump' 环境变量
```

**解法**：通过 `BACKUP_PG_DUMP` / `RESTORE_PG_RESTORE` env 让脚本走容器内的二进制：

```env
# .env.prod 补充
BACKUP_PG_DUMP=docker exec ros_postgres pg_dump
RESTORE_PG_RESTORE=docker exec ros_postgres pg_restore
```

**自动行为**（在 `infra/backup/backup.py` 第 113~214 行 + `restore.py` 89~191 行）：

- `is_docker = True` 时强制 host=`localhost` / port=`5432`（容器视角，**不是宿主映射端口**）
- 备份：`docker exec` 跑 `pg_dump -f /tmp/<name>.dump` → `docker cp` 到宿主 → `rm` 容器内文件
- 恢复：`docker cp` 宿主 dump 进容器 `/tmp` → `docker exec` 跑 `pg_restore` → `rm` 容器内文件

**用例**（doctor pass 实测）：

```powershell
$env:DATABASE_URL    = "postgresql://drill:****@localhost:5433/drill_db"
$env:BACKUP_PG_DUMP  = "docker exec ros_postgres pg_dump"
python infra/backup/backup.py --type manual
# → ✓ postgres   size=6.2MB    → manual-postgres-<ts>.dump
# → ✓ uploads    size=131.4MB  → manual-uploads-<ts>.tar.gz

$env:RESTORE_PG_RESTORE = "docker exec ros_postgres_restore pg_restore"
python infra/backup/restore.py --db-file backups/manual/manual-postgres-<ts>.dump --skip-uploads
# → [Restore] DB OK
```

**实测验证**：恢复后行数 + `pg_typeof(embedding)='vector'` + alembic_version 完全保留，
详见 `PRE_LAUNCH_DRILL.md` § P2-C 恢复演练 (historical reference excluded from public source)。

**回退**：宿主装 `postgresql-client` 后，留空这两个 env 即走 PATH（仍兼容）。

### 0.5 退出码（适合 cron 监控 / webhook 送警）

| 退出码 | 含义 |
|---|---|
| 0 | 全部成功 |
| 1 | 参数 / 配置错误 |
| 2 | 备份本身失败（必须告警） |
| 3 | 备份成功但 retention 清理失败（warn） |

---

## 1. 需要备份的内容

| 类别 | 位置 | 重要性 | 备份方式 |
|---|---|---|---|
| **主数据库** | SQLite `apps/backend/dev.db` / Postgres `agent_db` | 🔴 全部用户数据 | 每日快照 + 保留 30 天 |
| **成本日志** | `apps/backend/cost_log.db`（独立 SQLite） | 🟡 LLM 成本审计 | 每日快照 |
| **sqlite-vec 索引** | `apps/backend/vec_claims.db`（或 pgvector 表） | 🟢 可重建（重新 embedding） | 每周快照 |
| **上传文件** | `apps/backend/uploads/` volume | 🔴 raw_source 引用 | 每日 rsync / S3 同步 |
| **配置** | `.env.production` | 🔴 API keys + passwords | Vault 版本化，不进 git |
| **scheduler 状态** | 内嵌进程，无持久化 | 🟢 重启即恢复 | — |

---

## 2. SQLite 模式备份（小规模）

### 2.1 每日快照

```powershell
# 在 host 上执行（docker-compose 卷挂载）
$date = Get-Date -Format "yyyyMMdd"
$src = "apps\backend\dev.db"
$dst = "backups\dev-$date.db"

# SQLite .backup 是在线热备（不阻塞写）
sqlite3 $src ".backup $dst"

# 压缩 + 保留 30 天
Compress-Archive -Path $dst -DestinationPath "$dst.zip"
Remove-Item $dst
Get-ChildItem backups\*.zip | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } | Remove-Item
```

建议配到 Windows 任务计划/cron 每天凌晨 3 点。

### 2.2 恢复

优先使用 `infra/backup/restore.py --db-file <backup.db.gz> --skip-uploads`；支持 `.db`
和 `.db.gz`，完整步骤见 [备份脚本说明](../../infra/backup/README.md#2-恢复restore)。
恢复前必须停止所有数据库使用者并完成 WAL checkpoint/关闭连接，不能直接删除侧文件。
脚本会先在目标目录验证数据库完整性，保留 `.before-restore` 副本后再原子替换。
以下为历史 ZIP 备份的手动流程，不具备脚本的暂存与完整性保护，优先在独立目录恢复验证。

```powershell
# 1. 停后端
docker compose -f docker-compose.prod.yml stop backend
# 2. 替换 db（保留当前作应急回退）
Copy-Item apps\backend\dev.db apps\backend\dev.db.before-restore
Expand-Archive backups\dev-20260422.db.zip -DestinationPath apps\backend\
Move-Item apps\backend\dev-20260422.db apps\backend\dev.db -Force
# 3. 启后端
docker compose -f docker-compose.prod.yml start backend
# 4. 健康检查
curl http://localhost:8000/health/ready
```

---

## 3. PostgreSQL 模式备份（生产推荐）

### 3.1 每日逻辑备份

```powershell
$date = Get-Date -Format "yyyyMMdd"
docker compose -f docker-compose.prod.yml exec -T postgres `
  pg_dump -U agent_user -Fc agent_db | `
  Out-File -Encoding byte "backups\agent_db-$date.dump"

# 保留 30 天
Get-ChildItem backups\agent_db-*.dump | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } | Remove-Item
```

### 3.2 PITR（Point-in-Time Recovery）

如果业务 RPO < 24h，需要开启 WAL archiving:

```ini
# postgresql.conf
wal_level = replica
archive_mode = on
archive_command = 'copy "%p" "/archive/%f"'
```

然后定期 `pg_basebackup` + 持续归档 WAL 到 S3 或远端存储。

### 3.3 恢复

```powershell
# 逻辑恢复（简单场景）
docker compose -f docker-compose.prod.yml run --rm migrate  # 确保 schema 存在
Get-Content backups\agent_db-20260422.dump -Raw | `
  docker compose -f docker-compose.prod.yml exec -T postgres `
  pg_restore -U agent_user -d agent_db --clean --if-exists

# PITR 恢复:参照 PostgreSQL 官方文档 https://www.postgresql.org/docs/current/continuous-archiving.html
```

---

## 4. 上传文件备份

```powershell
# 源:docker volume "uploads_data" 映射到 host
# 目标:每日 rsync 到 NAS 或上 S3

# 方案 A — rsync 到本地磁盘
$date = Get-Date -Format "yyyyMMdd"
robocopy "<volume-path>" "backups\uploads-$date" /E /R:3

# 方案 B — 直接同步到 S3
aws s3 sync <volume-path> s3://your-bucket/uploads/ --delete
```

---

## 5. 备份完整性校验

每月至少跑一次「冷恢复演练」:

1. 在独立环境（staging / 临时 VM）把最近备份恢复为新库
2. `pytest tests/ --tb=line -q`（注:LLM 在线用例会用 OpenRouter，额度计算）
3. 手工跑一遍:`POST /chat/sessions` + 一条 briefing + 一条 debate，看数据链路完整
4. 记录 RTO（Recovery Time Objective，从决定恢复到服务就绪的总时长）

---

## 6. 灾难恢复（DR）矩阵

| 场景 | RPO 目标 | RTO 目标 | 动作 |
|---|---|---|---|
| Backend pod crash | 0 | < 1 min | k8s 自动重启 / docker restart policy |
| DB 损坏（可重建 schema） | 24h | < 30 min | 逻辑备份恢复 |
| Host 存储丢失 | 24h | < 2h | 新机 + 备份恢复 + DNS 切换 |
| 整区域故障 | 24h | < 8h | 异地备份 + 在备 region 起栈 |
| 误删数据（可识别） | 0 | < 15 min | PITR 到误删前 5 分钟 |

---

## 7. 不建议备份的内容

- `apps/backend/.pytest_cache/` — 临时
- `apps/frontend/node_modules/` — 包
- `apps/backend/venv/` — 虚拟环境
- `apps/backend/test*.db` / `apps/backend/demo.db` — 开发期临时库
- `logs/*.log` — 通过日志基础设施（如 ELK / Loki）管理，不随应用备份

这些都已在 `.gitignore` 中，备份脚本应同步排除。
