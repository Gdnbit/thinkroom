"""P2-C · 备份完整性验证

定期跑（建议每天 / 备份后立即），保证备份"看起来"是有效的。

检查项
------
1. **存在性**：每个 backup type（daily / weekly / monthly）下至少有 1 份近期备份
2. **新鲜度**：daily 最新 < 36h；weekly < 8d；monthly < 35d
3. **大小合理**：> 1KB（防止空文件）
4. **可解压**：tar.gz 至少能列出条目；.dump / .db.gz 至少 magic 字节正确

不做的：完整 restore + row count 比对（成本高，单独 verify_restore.py 处理）。

退出码
------
- 0：全部 OK
- 1：参数错误
- 2：至少一项失败
- 3：仅 warning（如 weekly/monthly 缺失但 daily 在）

cron 范例
---------
    # 每天早上检查（备份后 1 小时）
    0 4 * * * root cd /opt/ros && python infra/backup/verify.py --quiet || \\
        curl -X POST $WEBHOOK_URL -d "ROS backup verify failed"
"""
from __future__ import annotations

import argparse
import gzip
import os
import sys
import tarfile
import time
from pathlib import Path
from typing import Optional


def _env(name: str, default: str) -> str:
    v = os.environ.get(name, "").strip()
    return v if v else default


# 各类型最大允许的"陈旧"秒数
FRESHNESS_MAX_AGE_SECS = {
    "daily":   36 * 3600,           # 1.5 天
    "weekly":  8 * 24 * 3600,       # 8 天
    "monthly": 35 * 24 * 3600,      # 35 天
}

MIN_BACKUP_SIZE = 1024  # 1KB，小于此大小视为失败


class Issue:
    def __init__(self, level: str, msg: str):
        self.level = level  # "error" | "warning"
        self.msg = msg

    def __repr__(self) -> str:
        return f"[{self.level}] {self.msg}"


def _find_latest(target: Path, prefix: str) -> Optional[Path]:
    if not target.exists():
        return None
    files = sorted(
        [f for f in target.iterdir() if f.is_file() and f.name.startswith(prefix)],
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    return files[0] if files else None


def _check_postgres_dump_magic(path: Path) -> bool:
    """pg_dump --format=custom 的文件以 magic `PGDMP` 开头."""
    try:
        with open(path, "rb") as f:
            magic = f.read(5)
        return magic == b"PGDMP"
    except OSError:
        return False


def _check_sqlite_gz(path: Path) -> bool:
    """gunzip 后前 16 字节是 SQLite magic `SQLite format 3\\x00`."""
    try:
        with gzip.open(path, "rb") as f:
            data = f.read(16)
        return data.startswith(b"SQLite format 3")
    except OSError:
        return False


def _check_tar_gz(path: Path) -> bool:
    """tarfile 能打开 + 至少有 1 个 member."""
    try:
        with tarfile.open(path, "r:gz") as tf:
            for _ in tf:
                return True  # 有至少 1 个
        return False
    except (tarfile.TarError, OSError):
        return False


def verify_one(target_dir: Path, btype: str, db_kind: str) -> list[Issue]:
    """对某个 backup type 子目录做检查，返回 issue 列表（空表示全过）."""
    issues: list[Issue] = []
    max_age = FRESHNESS_MAX_AGE_SECS.get(btype, 36 * 3600)
    now = time.time()

    # DB
    db_prefix = f"{btype}-{db_kind}-"
    db = _find_latest(target_dir, db_prefix)
    if db is None:
        issues.append(Issue("error", f"{btype}: 缺 {db_kind} 备份"))
    else:
        size = db.stat().st_size
        age = now - db.stat().st_mtime
        if size < MIN_BACKUP_SIZE:
            issues.append(Issue("error", f"{btype}/{db.name}: 太小 ({size} bytes)"))
        if age > max_age:
            issues.append(Issue("error", f"{btype}/{db.name}: 太旧 ({age/3600:.1f}h > {max_age/3600:.0f}h)"))
        # 内容自检
        if db_kind == "postgres":
            if not _check_postgres_dump_magic(db):
                issues.append(Issue("error", f"{btype}/{db.name}: 不是合法 pg_dump 文件"))
        elif db_kind == "sqlite":
            if not _check_sqlite_gz(db):
                issues.append(Issue("error", f"{btype}/{db.name}: 不是合法 sqlite.gz 文件"))

    # uploads
    up = _find_latest(target_dir, f"{btype}-uploads-")
    if up is None:
        # uploads 缺失只 warning（可能本来就没上传文件）
        issues.append(Issue("warning", f"{btype}: 缺 uploads 备份（如果不需要可忽略）"))
    else:
        size = up.stat().st_size
        age = now - up.stat().st_mtime
        if size < MIN_BACKUP_SIZE:
            issues.append(Issue("error", f"{btype}/{up.name}: 太小 ({size} bytes)"))
        if age > max_age:
            issues.append(Issue("error", f"{btype}/{up.name}: 太旧 ({age/3600:.1f}h)"))
        if not _check_tar_gz(up):
            issues.append(Issue("error", f"{btype}/{up.name}: tar.gz 损坏或为空"))

    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="ROS 备份验证（P2-C）")
    parser.add_argument("--target", default=None, help="备份根目录（默认 ./backups/）")
    parser.add_argument("--types", default="daily,weekly,monthly",
                        help="检查的 backup types（逗号分隔）")
    parser.add_argument("--quiet", action="store_true", help="只打印失败")
    args = parser.parse_args()

    backup_root = Path(args.target or _env("BACKUP_DIR", "./backups")).resolve()
    db_url = _env("DATABASE_URL", "sqlite:///./apps/backend/dev.db")
    db_kind = "postgres" if db_url.startswith("postgresql") else "sqlite"

    if not backup_root.exists():
        print(f"[Verify] backup root not found: {backup_root}", file=sys.stderr)
        return 2

    types = [t.strip() for t in args.types.split(",") if t.strip()]
    all_issues: list[Issue] = []

    for btype in types:
        sub = backup_root / btype
        issues = verify_one(sub, btype, db_kind)
        all_issues.extend(issues)
        if not args.quiet:
            if not issues:
                print(f"[Verify] {btype:<8} ✓")
            else:
                for iss in issues:
                    print(f"[Verify] {btype:<8} {iss}")

    errors = [i for i in all_issues if i.level == "error"]
    warnings = [i for i in all_issues if i.level == "warning"]

    print(f"\n[Verify] summary: {len(errors)} errors, {len(warnings)} warnings")
    if errors:
        return 2
    if warnings:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
