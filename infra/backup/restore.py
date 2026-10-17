"""P2-C · 跨平台恢复脚本

支持的源：
- Postgres dump（pg_dump --format=custom 产物）→ pg_restore --clean
- SQLite .db 或 .db.gz → 替换 source 库
- uploads tar.gz → 解压到 uploads/

⚠️ 危险操作：恢复会**覆盖**当前数据。建议：
- 先停后端
- 先备份当前 DB（可作应急回退）
- 在 staging 跑过一遍再上生产

用法
----
    # 自动选择最新 daily 备份恢复
    python infra/backup/restore.py --auto-latest

    # 指定文件
    python infra/backup/restore.py --db-file backups/daily/daily-postgres-20260424T030000Z.dump
    python infra/backup/restore.py --uploads-file backups/daily/daily-uploads-20260424T030000Z.tar.gz

    # 仅恢复 DB / uploads
    python infra/backup/restore.py --auto-latest --skip-uploads
    python infra/backup/restore.py --auto-latest --skip-db

    # 恢复到指定（非 .env）DB
    DATABASE_URL=postgresql://... python infra/backup/restore.py --db-file <dump>

环境变量
--------
- `DATABASE_URL`：目标库（默认从 backend 配置读）
- `BACKUP_DIR`：备份根目录（默认 ./backups/）
- `UPLOADS_DIR`：上传文件目录（默认 apps/backend/uploads/）
- `RESTORE_CONFIRM=yes`：跳过交互确认（CI / cron 场景）

退出码
------
- 0：成功
- 1：参数错误
- 2：恢复失败
- 3：用户中断
"""
from __future__ import annotations

import argparse
import gzip
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Optional


def _env(name: str, default: str) -> str:
    v = os.environ.get(name, "").strip()
    return v if v else default


def _confirm(prompt: str) -> bool:
    if _env("RESTORE_CONFIRM", "") == "yes":
        return True
    print(f"\n⚠️  {prompt}")
    print("   输入 'yes' 继续，其他取消：", end="", flush=True)
    try:
        ans = input().strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        return False
    return ans == "yes"


def _find_latest(target: Path, prefix: str) -> Optional[Path]:
    """target 目录下找前缀 prefix 的最新文件."""
    if not target.exists():
        return None
    files = sorted(
        [f for f in target.iterdir() if f.is_file() and f.name.startswith(prefix)],
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    return files[0] if files else None


# ── Postgres 恢复 ────────────────────────────────────────────────


def restore_postgres(database_url: str, dump_file: Path) -> bool:
    """``pg_restore --clean`` 把 dump 文件导入目标库.

    PG 客户端解析顺序（与 backup.py 对称）：
    1. ``RESTORE_PG_RESTORE`` 环境变量 — 推荐生产（如 ``docker exec ros_postgres pg_restore``）
    2. ``pg_restore`` 在 PATH（postgresql-client）
    """
    if not dump_file.exists():
        print(f"[Restore] dump file not found: {dump_file}", file=sys.stderr)
        return False

    from urllib.parse import urlparse
    p = urlparse(database_url.replace("postgresql+psycopg2://", "postgresql://"))
    db = (p.path or "").lstrip("/")
    if not db:
        print(f"[Restore] DATABASE_URL 缺数据库名: {database_url}", file=sys.stderr)
        return False

    env = os.environ.copy()
    if p.password:
        env["PGPASSWORD"] = p.password

    pg_restore_override = _env("RESTORE_PG_RESTORE", "").strip()

    # ── docker exec 模式：把 dump 复制到容器，再 pg_restore ──
    if pg_restore_override:
        import shlex
        prefix_cmd = shlex.split(pg_restore_override)
        is_docker = "docker" in pg_restore_override.split()[0]
        host = "localhost" if is_docker else (p.hostname or "localhost")
        port = 5432 if is_docker else (p.port or 5432)
        if is_docker:
            container = pg_restore_override.split()[2]
            container_path = f"/tmp/{dump_file.name}"
            try:
                # cp dump 到容器 /tmp
                subprocess.run(["docker", "cp", str(dump_file),
                                f"{container}:{container_path}"],
                               check=True, capture_output=True, text=True)
                cmd = prefix_cmd + [
                    "--clean", "--if-exists", "--no-owner",
                    "-h", host, "-p", str(port),
                    "-U", p.username or "postgres",
                    "-d", db,
                    container_path,
                ]
                result = subprocess.run(cmd, env=env, capture_output=True, text=True)
                if result.returncode != 0:
                    print(f"[Restore] docker pg_restore exit={result.returncode}",
                          file=sys.stderr)
                    print(result.stderr[:1000], file=sys.stderr)
                    return False
            except subprocess.CalledProcessError as e:
                msg = (e.stderr or e.stdout or str(e))[:500]
                print(f"[Restore] docker cp/pg_restore failed: {msg}", file=sys.stderr)
                return False
            finally:
                # 清理容器内 dump 文件
                subprocess.run(["docker", "exec", container, "rm", "-f", container_path],
                               check=False, capture_output=True, text=True)
            return True

        # 自定义二进制：直接 pg_restore
        cmd = prefix_cmd + [
            "--clean", "--if-exists", "--no-owner",
            "-h", host, "-p", str(port),
            "-U", p.username or "postgres",
            "-d", db,
            str(dump_file),
        ]
        result = subprocess.run(cmd, env=env, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"[Restore] {pg_restore_override} exit={result.returncode}", file=sys.stderr)
            print(result.stderr[:1000], file=sys.stderr)
            return False
        return True

    # ── 默认：PATH 中的 pg_restore ──
    cmd = [
        "pg_restore",
        "--clean",
        "--if-exists",
        "--no-owner",
        "-h", p.hostname or "localhost",
        "-p", str(p.port or 5432),
        "-U", p.username or "postgres",
        "-d", db,
        str(dump_file),
    ]

    try:
        result = subprocess.run(cmd, env=env, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"[Restore] pg_restore exit={result.returncode}", file=sys.stderr)
            print(result.stderr[:1000], file=sys.stderr)
            return False
    except FileNotFoundError:
        print("[Restore] pg_restore 不在 PATH；安装 postgresql-client，"
              "或设置 RESTORE_PG_RESTORE='docker exec ros_postgres pg_restore'",
              file=sys.stderr)
        return False

    return True


# ── SQLite 恢复 ──────────────────────────────────────────────────


def restore_sqlite(database_url: str, dump_file: Path) -> bool:
    """SQLite 恢复：替换源文件（如果是 .gz 先 gunzip）."""
    if "://" in database_url:
        dst_path = database_url.split("://", 1)[1].lstrip("/")
    else:
        dst_path = database_url
    dst = Path(dst_path)

    if not dump_file.exists():
        print(f"[Restore] dump file not found: {dump_file}", file=sys.stderr)
        return False

    # 先备份当前 DB 作应急回退（如果存在）
    if dst.exists():
        bak = dst.with_suffix(dst.suffix + ".before-restore")
        try:
            shutil.copy2(dst, bak)
            print(f"[Restore] saved current DB to {bak} (keep until verified)")
        except OSError as e:
            print(f"[Restore] could not back up current DB: {e}", file=sys.stderr)
            return False

    # gunzip if needed
    if dump_file.suffix == ".gz":
        tmp = dst.with_suffix(dst.suffix + ".restoring")
        with gzip.open(dump_file, "rb") as fi, open(tmp, "wb") as fo:
            shutil.copyfileobj(fi, fo)
        shutil.move(str(tmp), str(dst))
    else:
        shutil.copy2(dump_file, dst)

    # 完整性自检
    try:
        import sqlite3
        conn = sqlite3.connect(str(dst))
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            if not row or row[0] != "ok":
                print(f"[Restore] integrity_check failed: {row}", file=sys.stderr)
                return False
        finally:
            conn.close()
    except Exception as e:
        print(f"[Restore] integrity_check exception: {e}", file=sys.stderr)
        return False

    return True


# ── uploads 恢复 ─────────────────────────────────────────────────


def restore_uploads(tar_file: Path, target_parent: Path) -> bool:
    """tar.gz 解压到 target_parent（包内的 uploads/ 目录会被还原）."""
    if not tar_file.exists():
        print(f"[Restore] tar file not found: {tar_file}", file=sys.stderr)
        return False
    target_parent.mkdir(parents=True, exist_ok=True)

    try:
        with tarfile.open(tar_file, "r:gz") as tf:
            # Python 3.12+ 的 data filter 默认拦截危险路径
            try:
                tf.extractall(target_parent, filter="data")  # type: ignore[arg-type]
            except TypeError:
                # < 3.12 fallback：自己检查路径
                for m in tf.getmembers():
                    if m.name.startswith("/") or ".." in Path(m.name).parts:
                        print(f"[Restore] reject unsafe member: {m.name}", file=sys.stderr)
                        return False
                tf.extractall(target_parent)
    except Exception as e:
        print(f"[Restore] tar extract failed: {e}", file=sys.stderr)
        return False

    return True


# ── 主流程 ───────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="ROS 恢复脚本（P2-C）")
    parser.add_argument("--auto-latest", action="store_true",
                        help="自动选择 backups/daily/ 下的最新 dump + tar.gz")
    parser.add_argument("--db-file", default=None, help="指定 DB dump 文件路径")
    parser.add_argument("--uploads-file", default=None, help="指定 uploads tar.gz 路径")
    parser.add_argument("--skip-db", action="store_true", help="跳过 DB 恢复")
    parser.add_argument("--skip-uploads", action="store_true", help="跳过 uploads 恢复")
    parser.add_argument("--type", default="daily", help="auto-latest 时从哪个子目录找（daily/weekly/monthly）")
    args = parser.parse_args()

    backup_root = Path(_env("BACKUP_DIR", "./backups")).resolve()
    db_url = _env("DATABASE_URL", "sqlite:///./apps/backend/dev.db")
    uploads_dir = Path(_env("UPLOADS_DIR", "./apps/backend/uploads")).resolve()

    # 解析 db_file
    db_file: Optional[Path] = None
    if not args.skip_db:
        if args.db_file:
            db_file = Path(args.db_file).resolve()
        elif args.auto_latest:
            sub = backup_root / args.type
            if db_url.startswith("postgresql"):
                db_file = _find_latest(sub, f"{args.type}-postgres-")
            elif db_url.startswith("sqlite"):
                db_file = _find_latest(sub, f"{args.type}-sqlite-")
            if db_file is None:
                print(f"[Restore] no {args.type} DB backup found in {sub}", file=sys.stderr)
                return 1
        else:
            print("[Restore] need --auto-latest or --db-file", file=sys.stderr)
            return 1

    uploads_file: Optional[Path] = None
    if not args.skip_uploads:
        if args.uploads_file:
            uploads_file = Path(args.uploads_file).resolve()
        elif args.auto_latest:
            sub = backup_root / args.type
            uploads_file = _find_latest(sub, f"{args.type}-uploads-")
            # 找不到不报错，可能本来就没备份过 uploads

    # 用户确认
    plan_lines = ["即将恢复："]
    if db_file:
        plan_lines.append(f"  DB ({db_url[:50]}...) ← {db_file}")
    if uploads_file:
        plan_lines.append(f"  uploads ({uploads_dir}) ← {uploads_file}")
    plan_lines.append("\n⚠️  这会覆盖当前数据。请确保：")
    plan_lines.append("  1. 后端已停止")
    plan_lines.append("  2. 当前数据已应急备份")
    if not _confirm("\n".join(plan_lines) + "\n"):
        print("[Restore] aborted by user")
        return 3

    # 执行
    ok = True
    if db_file:
        if db_url.startswith("postgresql"):
            r = restore_postgres(db_url, db_file)
        elif db_url.startswith("sqlite"):
            r = restore_sqlite(db_url, db_file)
        else:
            print(f"[Restore] unknown DATABASE_URL scheme: {db_url[:30]}", file=sys.stderr)
            r = False
        ok = ok and r
        print(f"[Restore] DB {'OK' if r else 'FAIL'}")

    if uploads_file:
        r = restore_uploads(uploads_file, uploads_dir.parent)
        ok = ok and r
        print(f"[Restore] uploads {'OK' if r else 'FAIL'}")

    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
