# 备份与恢复（自用 + 生产共用）

> 状态：经过测试的运维参考，不属于 local v0.1 默认启动流程。安装计划任务、恢复以及
> retention 清理都会产生外部或删除型副作用；在“禁止删除/禁止改写数据”的任务中不得
> 执行，必须由操作者单独确认。

## 0. 快速开始（个人用户最重要的一步）

> **风险警告**：dev 模式下你的所有数据都在单文件 `dev.db` 里。磁盘故障、误删、git checkout、断电写损坏 —— 任何一件事都会让长期积累的判断 / 引用 / 思考全没。
>
> 设置自动备份是 **30 分钟内能避开 90% 数据风险** 的事。

### Windows（你的当前环境）

```powershell
# 在 PowerShell 里跑（不需要管理员就能注册到当前用户）
powershell -ExecutionPolicy Bypass -File infra\backup\install-windows-task.ps1

# 或同时设置 weekly 备份
powershell -ExecutionPolicy Bypass -File infra\backup\install-windows-task.ps1 -WeeklyAlso
```

任务会注册到 Windows 计划任务程序，每天 02:00 自动跑 `backup.py --type daily`：

- 备份 `dev.db` 用 SQLite 在线 backup API 获取一致快照（可能短暂获取读锁）
- 备份 `apps/backend/uploads/` 整个目录（tar.gz）
- 输出到项目根的 `./backups/` 目录
- 自动按 `daily=7 / weekly=4 / monthly=12` 保留策略清理过期文件

**验证**：

```powershell
# 立即跑一次（不等到 02:00）
Start-ScheduledTask -TaskName 'ROS_DailyBackup'

# 看任务状态
Get-ScheduledTask -TaskName 'ROS_DailyBackup*' | Format-Table TaskName, State, Triggers

# 看备份产物
Get-ChildItem ./backups/ | Sort-Object LastWriteTime -Descending | Select-Object -First 5
```

**卸载**：

```powershell
Unregister-ScheduledTask -TaskName 'ROS_DailyBackup' -Confirm:$false
Unregister-ScheduledTask -TaskName 'ROS_DailyBackup_Weekly' -Confirm:$false  # if installed
```

### Linux / macOS

```bash
# daily 02:00
bash infra/backup/install-cron.sh

# 同时加 weekly
bash infra/backup/install-cron.sh --weekly

# 卸载
bash infra/backup/install-cron.sh --uninstall
```

---

## 1. 备份脚本细节（高级）

直接调 `backup.py` 而不走计划任务：

```bash
# 默认 daily 备份到 ./backups/
python infra/backup/backup.py

# 指定目标目录
python infra/backup/backup.py --type weekly --target /var/backups/ros

# 仅 DB（跳过 uploads，速度更快）
python infra/backup/backup.py --skip-uploads

# 静默模式（cron 用）
python infra/backup/backup.py --quiet
```

支持的目标：

- **SQLite**：在线 backup API，一致快照后再 gzip 压缩
- **Postgres**：`pg_dump --format=custom`（可 pg_restore 增量恢复）
- **uploads/**：tar.gz 打包

环境变量（可选，覆盖默认行为）：

- `DATABASE_URL` — 自动识别 `sqlite://` / `postgresql://`
- `BACKUP_DIR` — 备份根目录（默认 `./backups/`）
- `BACKUP_RETENTION_DAILY` / `_WEEKLY` / `_MONTHLY` — 保留份数（默认 7 / 4 / 12）
- `UPLOADS_DIR` — uploads 目录（默认 `apps/backend/uploads/`）
- `BACKUP_PG_DUMP` — pg_dump 路径（默认 `docker exec ros_postgres pg_dump`）

退出码：`0` 成功 · `1` 参数错 · `2` 备份失败 · `3` 保留清理失败（备份本身已成功）

---

## 2. 恢复（restore）

**必须先停止后端、worker 和其他所有数据库使用者。此脚本不支持在线覆盖恢复。**
WAL 数据库应在停止写入后完成 checkpoint 并关闭连接；存在 `-wal`、`-shm` 或
`-journal` 时脚本拒绝覆盖。不要直接删除这些文件，它们可能包含尚未合入主文件的数据。

安装 backend requirements 后使用同一 Python 环境运行脚本（URL 解析使用 SQLAlchemy）。
`sqlite:///./dev.db` 相对于当前工作目录；`sqlite:////tmp/dev.db` 是 POSIX 绝对路径；
Windows 使用 `sqlite:///C:/path/to/dev.db`（替换为本机实际路径）。也支持
`sqlite+pysqlite:///...`。内存库、URI/query 参数、UNC 和非本机平台的绝对路径会被拒绝。

```bash
# 从最近一份 daily 恢复
python infra/backup/restore.py --auto-latest --type daily --skip-uploads

# 从指定文件恢复
python infra/backup/restore.py --db-file backups/daily/example.db.gz --skip-uploads

# 检查 daily 备份集合（不实际恢复）
python infra/backup/verify.py --types daily
```

`restore.py` 会：

1. 询问确认（自动化可使用 `RESTORE_CONFIRM=yes`，仍须先停止所有数据库使用者）。
2. 在目标目录暂存 `.db` 或解压 `.db.gz`，验证 magic 和完整 `PRAGMA integrity_check`。
3. 将当前数据库保留为 `<database>.before-restore`；已有副本则使用唯一后缀保留新副本。
   无法创建保护副本时停止恢复。
4. 在同一文件系统原子替换目标，清理暂存文件。损坏输入或替换失败不会覆盖原库。

恢复后先检查数据及 `PRAGMA integrity_check`，验证成功后再启动应用。保留保护副本直至
人工验收完成；文件系统故障和断电后的持久性仍取决于存储系统，需要独立异地备份。

---

## 3. 测试覆盖

`apps/backend/tests/test_backup_scripts.py`：真实临时数据库 round-trip、跨平台路径、WAL 在线备份、离线恢复要求、损坏输入和 I/O 失败保护，每个 PR 都跑。

---

## 4. 进一步阅读

- `docs/operations/BACKUP_RESTORE.md` — 完整运维 runbook，含 cron 样例、Windows 任务计划详解、故障恢复
- `docs/operations/SECURITY_CHECKLIST.md` — 生产部署安全清单（含备份加密 / 异地复制 / 监控告警）
