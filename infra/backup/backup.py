"""P2-C · 跨平台备份脚本

支持的目标：
- **Postgres**：用 `pg_dump --format=custom`（生产推荐，可 pg_restore 增量恢复）
- **SQLite**：用在线 backup API 获取一致快照（可能短暂获取读锁）
- **uploads/ 目录**：tar.gz 打包

保留策略（默认）：
- daily：保留最近 7 份
- weekly：保留最近 4 份
- monthly：保留最近 12 份

用法
----
    # 默认 daily 备份到 ./backups/
    python infra/backup/backup.py

    # 指定类型 + 目标目录
    python infra/backup/backup.py --type weekly --target /var/backups/ros

    # 仅备份 DB（跳过 uploads）
    python infra/backup/backup.py --skip-uploads

    # 只备份 uploads（跳过 DB）
    python infra/backup/backup.py --skip-db

环境变量
--------
- `DATABASE_URL`：自动识别 sqlite:// 或 postgresql://（默认从 backend .env 读）
- `BACKUP_DIR`：备份根目录（默认 ./backups/）
- `BACKUP_RETENTION_DAILY` / `_WEEKLY` / `_MONTHLY`：保留份数（默认 7/4/12）
- `UPLOADS_DIR`：上传文件目录（默认 apps/backend/uploads/）
- `BACKUP_PG_DUMP`：pg_dump 可执行路径（默认 docker exec ros_postgres pg_dump）

退出码
------
- 0：成功
- 1：参数/配置错误
- 2：备份执行失败
- 3：保留策略清理失败（备份本身已成功）

cron 范例
---------
    # /etc/cron.d/ros-backup
    0 3 * * * root cd /opt/ros && python infra/backup/backup.py --type daily
    0 4 * * 1 root cd /opt/ros && python infra/backup/backup.py --type weekly
    0 5 1 * * root cd /opt/ros && python infra/backup/backup.py --type monthly

Windows 任务计划：见 docs/operations/BACKUP_RESTORE.md §3
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import time
import tempfile
from uuid import uuid4
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlite_support import connect_readonly, sqlite_path


# ── 配置（环境变量优先；脚本默认值次之） ───────────────────────────


def _env(name: str, default: str) -> str:
    """读取环境变量，空值视作未设置."""
    v = os.environ.get(name, "").strip()
    return v if v else default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


# ── 单一备份操作的封装 ────────────────────────────────────────────


class BackupResult:
    def __init__(self, name: str, path: Optional[Path], size: int, ok: bool, msg: str = ""):
        self.name = name
        self.path = path
        self.size = size
        self.ok = ok
        self.msg = msg

    def __repr__(self) -> str:
        if self.ok:
            return f"<BackupResult name={self.name} size={_human(self.size)} path={self.path}>"
        return f"<BackupResult name={self.name} FAILED msg={self.msg}>"


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# ── Postgres 备份 ────────────────────────────────────────────────


def backup_postgres(database_url: str, target_dir: Path, prefix: str) -> BackupResult:
    """``pg_dump --format=custom`` 把 PG 库 dump 成可被 pg_restore 读的二进制文件.

    PG 客户端解析顺序：
    1. ``BACKUP_PG_DUMP`` 环境变量（如 ``docker exec ros_postgres pg_dump``）— 推荐生产
    2. ``pg_dump`` 在 PATH（需手动装 postgresql-client）
    3. 默认尝试 ``docker exec ros_postgres pg_dump`` — 与 docker-compose.prod.yml 对齐
    """
    name = f"{prefix}-postgres-{_timestamp()}.dump"
    out = target_dir / name

    # 解析 DATABASE_URL → 取 host/port/db/user/password
    from urllib.parse import urlparse
    p = urlparse(database_url.replace("postgresql+psycopg2://", "postgresql://"))
    db = (p.path or "").lstrip("/")
    if not db:
        return BackupResult("postgres", None, 0, False, "DATABASE_URL 缺数据库名")

    env = os.environ.copy()
    if p.password:
        env["PGPASSWORD"] = p.password

    pg_dump_override = _env("BACKUP_PG_DUMP", "").strip()

    # ── 决定 cmd ──
    # 用户显式指定（如 "docker exec ros_postgres pg_dump"）→ shlex.split 拆分
    if pg_dump_override:
        import shlex
        prefix_cmd = shlex.split(pg_dump_override)
        is_docker = "docker" in pg_dump_override.split()[0]
        # 容器内 pg_dump：host 用 localhost，port 是容器内的 PG 端口（5432）
        # 而不是 DATABASE_URL 中的宿主映射端口（如 5433）
        host = "localhost" if is_docker else (p.hostname or "localhost")
        port = 5432 if is_docker else (p.port or 5432)
        # docker exec 模式：dump 到容器内 /tmp，然后 cp 出来
        if is_docker:
            container_path = f"/tmp/{name}"
            cmd = prefix_cmd + [
                "--format=custom",
                "--compress=6",
                "-h", host,
                "-p", str(port),
                "-U", p.username or "postgres",
                "-d", db,
                "-f", container_path,
            ]
            container = pg_dump_override.split()[2]  # docker exec <container> pg_dump
            try:
                subprocess.run(cmd, check=True, env=env, capture_output=True, text=True)
                subprocess.run(["docker", "cp", f"{container}:{container_path}", str(out)],
                               check=True, capture_output=True, text=True)
                subprocess.run(["docker", "exec", container, "rm", "-f", container_path],
                               check=False, capture_output=True, text=True)
            except subprocess.CalledProcessError as e:
                msg = (e.stderr or e.stdout or str(e))[:500]
                return BackupResult("postgres", None, 0, False,
                                    f"docker pg_dump exit={e.returncode}: {msg}")
            size = out.stat().st_size
            return BackupResult("postgres", out, size, True)

        # 自定义二进制：直接 -f 写宿主路径
        cmd = prefix_cmd + [
            "--format=custom",
            "--compress=6",
            "-h", host,
            "-p", str(p.port or 5432),
            "-U", p.username or "postgres",
            "-d", db,
            "-f", str(out),
        ]
        try:
            subprocess.run(cmd, check=True, env=env, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            msg = (e.stderr or e.stdout or str(e))[:500]
            return BackupResult("postgres", None, 0, False,
                                f"{pg_dump_override} exit={e.returncode}: {msg}")
        size = out.stat().st_size
        return BackupResult("postgres", out, size, True)

    # ── 默认路径：尝试 PATH 中的 pg_dump ──
    cmd = [
        "pg_dump",
        "--format=custom",
        "--compress=6",
        "-h", p.hostname or "localhost",
        "-p", str(p.port or 5432),
        "-U", p.username or "postgres",
        "-d", db,
        "-f", str(out),
    ]

    try:
        subprocess.run(cmd, check=True, env=env, capture_output=True, text=True)
    except FileNotFoundError:
        return BackupResult(
            "postgres", None, 0, False,
            "pg_dump 不在 PATH；安装 postgresql-client，或设置 "
            "BACKUP_PG_DUMP='docker exec ros_postgres pg_dump' 环境变量"
        )
    except subprocess.CalledProcessError as e:
        msg = (e.stderr or e.stdout or str(e))[:500]
        return BackupResult("postgres", None, 0, False, f"pg_dump exit={e.returncode}: {msg}")

    size = out.stat().st_size
    return BackupResult("postgres", out, size, True)


# ── SQLite 备份 ──────────────────────────────────────────────────


def backup_sqlite(database_url: str, target_dir: Path, prefix: str) -> BackupResult:
    """SQLite online backup API snapshot, compressed only after completion."""
    try:
        import gzip
        import sqlite3

        src = sqlite_path(database_url)
        if not src.is_file():
            raise ValueError(f"SQLite 文件不存在: {src}")
        name = f"{prefix}-sqlite-{src.stem}-{_timestamp()}-{uuid4().hex[:8]}.db.gz"
        out_gz = target_dir / name
        with tempfile.TemporaryDirectory(prefix=".sqlite-backup-", dir=target_dir) as staging:
            snapshot = Path(staging) / "snapshot.db"
            srcdb = connect_readonly(src)
            try:
                dstdb = sqlite3.connect(str(snapshot))
                try:
                    srcdb.backup(dstdb)
                finally:
                    dstdb.close()
            finally:
                srcdb.close()
            compressed = Path(staging) / "snapshot.db.gz"
            with snapshot.open("rb") as fi, gzip.open(compressed, "wb", compresslevel=6) as fo:
                shutil.copyfileobj(fi, fo)
            os.replace(compressed, out_gz)
        return BackupResult("sqlite", out_gz, out_gz.stat().st_size, True)
    except Exception as e:
        return BackupResult("sqlite", None, 0, False, f"sqlite3.backup 失败: {e}")


# ── uploads/ 目录备份 ────────────────────────────────────────────


def backup_uploads(uploads_dir: Path, target_dir: Path, prefix: str) -> BackupResult:
    """tar.gz 打包整个 uploads/ 目录."""
    if not uploads_dir.exists():
        return BackupResult("uploads", None, 0, True, f"uploads dir not found, skip: {uploads_dir}")

    name = f"{prefix}-uploads-{_timestamp()}.tar.gz"
    out = target_dir / name

    try:
        with tarfile.open(out, "w:gz", compresslevel=6) as tf:
            tf.add(uploads_dir, arcname=uploads_dir.name)
    except Exception as e:
        return BackupResult("uploads", None, 0, False, f"tar.gz failed: {e}")

    return BackupResult("uploads", out, out.stat().st_size, True)


# ── 保留策略：按 prefix 排序后保留前 N 份 ──────────────────────────


def apply_retention(target_dir: Path, prefix: str, keep: int) -> int:
    """删除超出保留份数的旧备份。返回删除的文件数."""
    files = sorted(
        [f for f in target_dir.iterdir() if f.is_file() and f.name.startswith(f"{prefix}-")],
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    removed = 0
    for f in files[keep:]:
        try:
            f.unlink()
            removed += 1
        except OSError:
            pass
    return removed


# ── 主流程 ───────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="ROS 备份脚本（P2-C）")
    parser.add_argument("--type", default="daily", choices=["daily", "weekly", "monthly", "manual"],
                        help="备份类型，决定保留策略 + 文件名前缀")
    parser.add_argument("--target", default=None, help="备份输出目录（默认 ./backups/）")
    parser.add_argument("--skip-db", action="store_true", help="跳过数据库备份")
    parser.add_argument("--skip-uploads", action="store_true", help="跳过 uploads 备份")
    parser.add_argument("--quiet", action="store_true", help="只打印失败")
    args = parser.parse_args()

    target_root = Path(args.target or _env("BACKUP_DIR", "./backups")).resolve()
    target = target_root / args.type
    target.mkdir(parents=True, exist_ok=True)

    db_url = _env("DATABASE_URL", "sqlite:///./apps/backend/dev.db")
    uploads = Path(_env("UPLOADS_DIR", "./apps/backend/uploads")).resolve()

    retention_map = {
        "daily":   _env_int("BACKUP_RETENTION_DAILY", 7),
        "weekly":  _env_int("BACKUP_RETENTION_WEEKLY", 4),
        "monthly": _env_int("BACKUP_RETENTION_MONTHLY", 12),
        "manual":  9999,  # manual 不清理
    }
    keep = retention_map[args.type]

    results: list[BackupResult] = []

    # 1. 数据库
    if not args.skip_db:
        if db_url.startswith("postgresql"):
            r = backup_postgres(db_url, target, args.type)
        elif db_url.startswith("sqlite"):
            r = backup_sqlite(db_url, target, args.type)
        else:
            r = BackupResult("db", None, 0, False, "未知 DATABASE_URL scheme")
        results.append(r)

    # 2. uploads
    if not args.skip_uploads:
        results.append(backup_uploads(uploads, target, args.type))

    # 3. 保留策略
    cleanup_failed = False
    for r in results:
        if r.ok and r.path:
            try:
                # 只清同 name 前缀（postgres / sqlite / uploads）
                # name 形如 "daily-postgres-..."，前缀提取为 "daily-postgres"
                prefix_full = "-".join(r.path.name.split("-")[:2])
                removed = apply_retention(target, prefix_full, keep)
                if removed and not args.quiet:
                    print(f"[Backup] retention: removed {removed} old {r.name} backups (keep={keep})")
            except Exception as e:
                cleanup_failed = True
                print(f"[Backup] retention cleanup failed for {r.name}: {e}", file=sys.stderr)

    # 4. 输出报告
    all_ok = all(r.ok for r in results)
    for r in results:
        flag = "✓" if r.ok else "✗"
        line = f"[Backup] {flag} {r.name:<10} size={_human(r.size):<8}"
        if r.path:
            line += f" → {r.path.name}"
        if r.msg:
            line += f"  ({r.msg})"
        if not args.quiet or not r.ok:
            print(line)

    if not all_ok:
        return 2
    if cleanup_failed:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
