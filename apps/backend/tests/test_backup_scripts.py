"""P2-C 单元测试 · 备份/恢复/验证脚本

测试核心：
- backup.py 的 SQLite + uploads 备份完整路径（不依赖外部 pg_dump）
- restore.py 的 SQLite 恢复
- verify.py 的所有检查（存在性 / 新鲜度 / 大小 / magic / tar）
- 保留策略

不依赖 Postgres：用 SQLite 走完 backup→restore→verify 全链路。
"""
from __future__ import annotations

import gzip
import os
import sqlite3
import sys
import tarfile
import time
from pathlib import Path

import pytest


# 把 infra/backup 加入 sys.path（脚本不是包，单测临时引用）
_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "infra" / "backup"))

import backup as backup_mod  # noqa: E402
import restore as restore_mod  # noqa: E402
import verify as verify_mod  # noqa: E402


@pytest.fixture
def tmp_workspace(tmp_path: Path):
    """构造一个临时工作目录，含：
    - source.db（写入 1 张测试表 + 几行数据）
    - uploads/（含 1 个文件）
    - backups/（空）
    """
    src_db = tmp_path / "source.db"
    conn = sqlite3.connect(str(src_db))
    conn.executescript("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT, payload TEXT);")
    # 充充点数据，让压缩后 > MIN_BACKUP_SIZE (1KB)
    conn.executemany(
        "INSERT INTO t (name, payload) VALUES (?, ?)",
        [(f"name-{i}", "x" * 100) for i in range(200)],
    )
    conn.commit()
    conn.close()

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    # 复多个文件让 tar.gz > 1KB
    for i in range(50):
        (uploads / f"file-{i}.txt").write_text("hello " * 20, encoding="utf-8")

    backups = tmp_path / "backups"
    backups.mkdir()

    return {
        "root": tmp_path,
        "db": src_db,
        "uploads": uploads,
        "backups": backups,
        "db_url": f"sqlite:///{src_db.as_posix()}",
    }


# ── backup.py ────────────────────────────────────────────────────


def test_backup_sqlite_creates_compressed_dump(tmp_workspace):
    target = tmp_workspace["backups"] / "daily"
    target.mkdir()
    r = backup_mod.backup_sqlite(tmp_workspace["db_url"], target, "daily")
    assert r.ok, r.msg
    assert r.path.exists()
    assert r.path.suffix == ".gz"
    assert r.size > 0

    # gunzip 后是合法 SQLite
    with gzip.open(r.path, "rb") as f:
        head = f.read(16)
    assert head.startswith(b"SQLite format 3")


def test_backup_uploads_creates_tar_gz(tmp_workspace):
    target = tmp_workspace["backups"] / "daily"
    target.mkdir()
    r = backup_mod.backup_uploads(tmp_workspace["uploads"], target, "daily")
    assert r.ok
    assert r.path.suffix == ".gz"
    assert r.path.name.endswith(".tar.gz")

    # 能解出 fixture 写入的 file-{i}.txt
    with tarfile.open(r.path, "r:gz") as tf:
        names = tf.getnames()
    assert any(n.endswith("file-0.txt") for n in names)
    assert any(n.endswith("file-49.txt") for n in names)


def test_backup_uploads_skip_when_dir_missing(tmp_workspace):
    target = tmp_workspace["backups"] / "daily"
    target.mkdir()
    nonexistent = tmp_workspace["root"] / "nope"
    r = backup_mod.backup_uploads(nonexistent, target, "daily")
    assert r.ok  # missing dir is not an error
    assert r.path is None


def test_apply_retention_keeps_n_newest(tmp_workspace):
    target = tmp_workspace["backups"] / "daily"
    target.mkdir()
    # 创建 5 个名字带 prefix 的文件，mtime 递增
    for i in range(5):
        f = target / f"daily-sqlite-foo-{i}.db.gz"
        f.write_bytes(b"x")
        os.utime(f, (1_700_000_000 + i, 1_700_000_000 + i))

    removed = backup_mod.apply_retention(target, "daily-sqlite", keep=3)
    assert removed == 2
    # 只剩 3 份
    remaining = sorted(f.name for f in target.iterdir())
    assert len(remaining) == 3
    # 留的是 2/3/4（最新 3 个）
    assert remaining == [
        "daily-sqlite-foo-2.db.gz",
        "daily-sqlite-foo-3.db.gz",
        "daily-sqlite-foo-4.db.gz",
    ]


# ── restore.py ───────────────────────────────────────────────────


def test_restore_sqlite_from_gz(tmp_workspace):
    # 1. 先备份
    target = tmp_workspace["backups"] / "daily"
    target.mkdir()
    r = backup_mod.backup_sqlite(tmp_workspace["db_url"], target, "daily")
    assert r.ok

    # 2. 删源库
    src_db = tmp_workspace["db"]
    src_db.unlink()
    assert not src_db.exists()

    # 3. 恢复
    ok = restore_mod.restore_sqlite(tmp_workspace["db_url"], r.path)
    assert ok
    assert src_db.exists()

    # 4. 数据完整（fixture 写了 200 行 name-0..name-199）
    conn = sqlite3.connect(str(src_db))
    count = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    first = conn.execute("SELECT name FROM t WHERE id=1").fetchone()[0]
    last = conn.execute("SELECT name FROM t WHERE id=200").fetchone()[0]
    conn.close()
    assert count == 200
    assert first == "name-0"
    assert last == "name-199"


def test_restore_uploads_from_tar(tmp_workspace):
    target = tmp_workspace["backups"] / "daily"
    target.mkdir()
    r = backup_mod.backup_uploads(tmp_workspace["uploads"], target, "daily")
    assert r.ok

    # 删源 uploads
    import shutil as _sh
    _sh.rmtree(tmp_workspace["uploads"])
    assert not tmp_workspace["uploads"].exists()

    # 恢复（target_parent = root，tar 内含 uploads/）
    ok = restore_mod.restore_uploads(r.path, tmp_workspace["root"])
    assert ok
    # fixture 写了 50 个 file-{i}.txt
    assert (tmp_workspace["uploads"] / "file-0.txt").exists()
    assert (tmp_workspace["uploads"] / "file-49.txt").exists()


def test_restore_sqlite_keeps_before_restore_copy(tmp_workspace):
    # 备份
    target = tmp_workspace["backups"] / "daily"
    target.mkdir()
    r = backup_mod.backup_sqlite(tmp_workspace["db_url"], target, "daily")

    # 改源库（模拟"当前数据想保留作应急回退"）
    conn = sqlite3.connect(str(tmp_workspace["db"]))
    conn.execute("INSERT INTO t (name) VALUES ('zoe')")
    conn.commit()
    conn.close()

    # restore 会先把当前 DB 复制到 .before-restore
    ok = restore_mod.restore_sqlite(tmp_workspace["db_url"], r.path)
    assert ok

    bak = tmp_workspace["db"].with_suffix(tmp_workspace["db"].suffix + ".before-restore")
    assert bak.exists(), "应急回退副本应保留"


# ── verify.py ────────────────────────────────────────────────────


def test_verify_postgres_dump_magic_check(tmp_path):
    # 假 PG dump：前 5 字节 PGDMP
    fake = tmp_path / "fake.dump"
    fake.write_bytes(b"PGDMP" + b"\x00" * 100)
    assert verify_mod._check_postgres_dump_magic(fake) is True

    # 不合法 magic
    bad = tmp_path / "bad.dump"
    bad.write_bytes(b"NOTAPGDMP")
    assert verify_mod._check_postgres_dump_magic(bad) is False


def test_verify_sqlite_gz_check(tmp_workspace):
    target = tmp_workspace["backups"] / "daily"
    target.mkdir()
    r = backup_mod.backup_sqlite(tmp_workspace["db_url"], target, "daily")
    assert verify_mod._check_sqlite_gz(r.path) is True

    # gzip 可读但内容不是 SQLite (缺 magic) → False
    fake = target / "fake.db.gz"
    with gzip.open(fake, "wb") as f:
        f.write(b"not a sqlite")
    assert verify_mod._check_sqlite_gz(fake) is False

    # 不是合法 gzip → False
    fake.write_bytes(b"not even a gzip")
    assert verify_mod._check_sqlite_gz(fake) is False


def test_verify_tar_gz_check(tmp_workspace):
    target = tmp_workspace["backups"] / "daily"
    target.mkdir()
    r = backup_mod.backup_uploads(tmp_workspace["uploads"], target, "daily")
    assert verify_mod._check_tar_gz(r.path) is True

    bad = target / "bad.tar.gz"
    bad.write_bytes(b"not a tar")
    assert verify_mod._check_tar_gz(bad) is False


def test_verify_one_passes_for_fresh_complete_backup(tmp_workspace):
    target = tmp_workspace["backups"] / "daily"
    target.mkdir()
    backup_mod.backup_sqlite(tmp_workspace["db_url"], target, "daily")
    backup_mod.backup_uploads(tmp_workspace["uploads"], target, "daily")

    issues = verify_mod.verify_one(target, "daily", "sqlite")
    errors = [i for i in issues if i.level == "error"]
    assert errors == [], f"unexpected errors: {errors}"


def test_verify_one_fails_when_db_missing(tmp_workspace):
    target = tmp_workspace["backups"] / "daily"
    target.mkdir()
    # 只备份 uploads，缺 DB
    backup_mod.backup_uploads(tmp_workspace["uploads"], target, "daily")

    issues = verify_mod.verify_one(target, "daily", "sqlite")
    errors = [i for i in issues if i.level == "error"]
    assert any("缺 sqlite 备份" in i.msg for i in errors)


def test_verify_one_fails_when_db_too_old(tmp_workspace):
    target = tmp_workspace["backups"] / "daily"
    target.mkdir()
    r = backup_mod.backup_sqlite(tmp_workspace["db_url"], target, "daily")
    backup_mod.backup_uploads(tmp_workspace["uploads"], target, "daily")

    # 把 db 备份的 mtime 拉到 5 天前
    old_ts = time.time() - 5 * 86400
    os.utime(r.path, (old_ts, old_ts))

    issues = verify_mod.verify_one(target, "daily", "sqlite")
    errors = [i for i in issues if i.level == "error"]
    assert any("太旧" in i.msg for i in errors)


def test_verify_one_fails_when_db_too_small(tmp_workspace):
    target = tmp_workspace["backups"] / "daily"
    target.mkdir()
    # 写一个空 .db.gz（小于 1KB）
    tiny = target / "daily-sqlite-tiny-20260424T000000Z.db.gz"
    tiny.write_bytes(b"x" * 10)
    backup_mod.backup_uploads(tmp_workspace["uploads"], target, "daily")

    issues = verify_mod.verify_one(target, "daily", "sqlite")
    errors = [i for i in issues if i.level == "error"]
    assert any("太小" in i.msg for i in errors)
