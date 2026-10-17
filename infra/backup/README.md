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

- 备份 `dev.db` 用 SQLite 在线 `.backup` 命令（不阻塞写）
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

- **SQLite**：在线 `.backup` 命令，不阻塞写
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

```bash
# 从最近一份 daily 恢复
python infra/backup/restore.py --latest daily

# 从指定文件恢复
python infra/backup/restore.py --file backups/daily/db_20260101_020001.db.gz

# 校验某份备份是否完整（不实际恢复）
python infra/backup/verify.py --file backups/daily/db_20260101_020001.db.gz
```

恢复前 `restore.py` 会：

1. 把当前 db 备份到 `backups/_pre_restore_<timestamp>.db`（双保险）
2. 校验目标文件 magic bytes（不让损坏文件覆盖好数据）
3. 询问确认（除非加 `--yes`）

---

## 3. 测试覆盖

`apps/backend/tests/test_backup_scripts.py`（14 条）：完整 round-trip 测试 backup → restore → verify，每个 PR 都跑。

---

## 4. 进一步阅读

- `docs/operations/BACKUP_RESTORE.md` — 完整运维 runbook，含 cron 样例、Windows 任务计划详解、故障恢复
- `docs/operations/SECURITY_CHECKLIST.md` — 生产部署安全清单（含备份加密 / 异地复制 / 监控告警）
