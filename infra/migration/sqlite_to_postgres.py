"""SQLite → Postgres 全量数据迁移脚本（P2-E）

设计
----
- **顺序**：用 `Base.metadata.sorted_tables` 拿到 FK 拓扑顺序；truncate 倒序、insert 正序
- **跨方言转换**：仅 `claim_cards.embedding` 需要特殊处理 — SQLite TEXT (JSON)
  → list[float] → PG `vector(2560)`（pgvector 适配器自动序列化）
- **跳过的表**：
  - `alembic_version` — 由 `alembic upgrade head` 单独管理
  - `sqlite_sequence` — SQLite 内部
  - `vec_*` — sqlite-vec 虚拟表，PG 端用原生 pgvector 替代
- **批量写入**：`BATCH_SIZE=1000`，避免单次 INSERT 过大
- **幂等**：默认 `--truncate` 模式先清空 dest 再 insert；`--append` 模式仅追加

环境变量
--------
- `SRC_DATABASE_URL` 或位置参数 1：源 SQLite URL，如 `sqlite:///apps/backend/dev.db`
- `DST_DATABASE_URL` 或位置参数 2：目标 PG URL
- `MIGRATION_DRY_RUN=1`：仅打印行数对照表，不写

退出码
------
- 0：迁移完成 + 行数匹配
- 1：参数 / 配置错
- 2：迁移失败（异常）
- 3：迁移成功但 row count 不匹配（仅 verify 阶段触发）

用法
----
    # 1. 备份 SQLite（用 P2-C 脚本）
    python infra/backup/backup.py --type manual

    # 2. PG 已起 + alembic 升至 head（在 PG 上）
    DATABASE_URL=$DST alembic upgrade head

    # 3. 跑迁移
    python infra/migration/sqlite_to_postgres.py \\
        sqlite:///apps/backend/dev.db \\
        postgresql://agent_user:****@localhost:5432/agent_db

    # 4. 验证
    python infra/migration/verify_migration.py \\
        sqlite:///apps/backend/dev.db \\
        postgresql://agent_user:****@localhost:5432/agent_db
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List

# 把 backend 加进 sys.path 以便 import app.*
_BACKEND = Path(__file__).resolve().parents[2] / "apps" / "backend"
sys.path.insert(0, str(_BACKEND))

from sqlalchemy import MetaData, Table, create_engine, select  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402


SKIP_TABLES = {"alembic_version", "sqlite_sequence"}
SKIP_PREFIX = ("vec_",)  # sqlite-vec 虚拟表
BATCH_SIZE = 1000


def _load_orm_metadata():
    """触发所有 ORM 模型注册，返回 Base.metadata（含 FK 拓扑信息）."""
    import app.schemas  # noqa: F401 — 触发主入口
    import app.schemas.model_registry  # noqa: F401 — KeyGroup/ModelRegistry
    from app.database import Base
    return Base.metadata


def _is_skipped(name: str) -> bool:
    if name in SKIP_TABLES:
        return True
    if any(name.startswith(p) for p in SKIP_PREFIX):
        return True
    return False


def _reflect_src_safe(src_engine: Engine) -> MetaData:
    """反射源库 schema，跳过 vec_*（sqlite-vec 虚拟表，无 vec0 模块时反射会炸）.

    `only=` 接收 (name, metadata) 回调，返回 True 表示纳入反射。
    """
    src_meta = MetaData()
    src_meta.reflect(
        bind=src_engine,
        only=lambda name, meta: not _is_skipped(name),
    )
    return src_meta


def _convert_row_for_dst(table_name: str, row: Dict[str, Any], dst_is_pg: bool) -> Dict[str, Any]:
    """跨方言转换 — 当前仅 claim_cards.embedding 需特殊处理.

    源始终是 SQLite TEXT (JSON 字符串)。dst 行为：
    - PG + pgvector → 解析为 list[float]，psycopg+pgvector adapter 自动转 vector 字面量
    - PG (无 pgvector) / SQLite → 保持原 JSON 字符串（dst 列也是 TEXT）
    """
    if table_name != "claim_cards":
        return row
    if "embedding" not in row:
        return row
    emb = row["embedding"]
    if emb is None or emb == "":
        row["embedding"] = None
        return row
    # SQLite dst：保持原始字符串（如已是 list，序列化回去）
    if not dst_is_pg:
        if isinstance(emb, (list, tuple)):
            row["embedding"] = json.dumps(list(emb))
        # 已是 str → 保留
        return row
    # PG dst：解析 JSON → list[float]
    if isinstance(emb, (list, tuple)):
        return row
    if isinstance(emb, str):
        try:
            parsed = json.loads(emb)
            if isinstance(parsed, list) and all(isinstance(x, (int, float)) for x in parsed):
                row["embedding"] = parsed
            else:
                row["embedding"] = None
        except (ValueError, TypeError):
            row["embedding"] = None
    return row


def _audit_fk_orphans(
    src_engine: Engine, src_meta: MetaData, ordered: List[Table], log
) -> Dict[str, Dict[str, int]]:
    """对每张表的每个 FK 列做孤儿扫描，返回 {table: {col: orphan_count}}.

    SQLite 默认 ``PRAGMA foreign_keys=OFF``，长期开发库可能积累 FK 孤儿
    （子表引用了不存在的父表 PK）。Postgres 默认强制 FK，迁移时这些孤儿
    会触发 ForeignKeyViolation，整张表回滚。

    本函数仅扫描，不修改；调用方决定打印告警还是用 ``--skip-fk-orphans``
    在迁移阶段过滤孤儿行。
    """
    report: Dict[str, Dict[str, int]] = {}
    src_names = set(src_meta.tables.keys())
    for t in ordered:
        if _is_skipped(t.name) or t.name not in src_names:
            continue
        src_t = src_meta.tables[t.name]
        per_col: Dict[str, int] = {}
        for fk in src_t.foreign_keys:
            child_col = fk.parent.name
            parent_table = fk.column.table.name
            parent_col = fk.column.name
            if parent_table not in src_names:
                continue
            sql = (
                f'SELECT COUNT(*) FROM "{t.name}" '
                f'WHERE "{child_col}" IS NOT NULL '
                f'AND "{child_col}" NOT IN (SELECT "{parent_col}" FROM "{parent_table}")'
            )
            try:
                with src_engine.connect() as c:
                    n = c.exec_driver_sql(sql).scalar() or 0
            except Exception:
                n = -1  # 反射不到 → 跳过
            if n and n > 0:
                per_col[f"{child_col}->{parent_table}.{parent_col}"] = n
        if per_col:
            report[t.name] = per_col
    if report:
        log("\n[migrate] FK 孤儿扫描（SQLite 源默认不强制 FK，PG 会拒绝）：")
        total = 0
        for tbl, cols in report.items():
            for col, n in cols.items():
                log(f"  [WARN] {tbl}.{col}: {n} 孤儿行")
                total += n
        log(f"  小计：{total} 行需迁前处理或用 --skip-fk-orphans 过滤")
    else:
        log("\n[migrate] FK 孤儿扫描通过（无孤儿）")
    return report


def _fetch_rows(src_engine: Engine, table: Table, batch_size: int = BATCH_SIZE) -> Iterable[List[Dict[str, Any]]]:
    """流式读取源表，按 batch_size 分批 yield list[dict]."""
    with src_engine.connect() as conn:
        # 服务端流式：execution_options(stream_results=True) 避免一次性载入内存
        result = conn.execution_options(stream_results=True).execute(select(table))
        batch: List[Dict[str, Any]] = []
        for row in result:
            batch.append(dict(row._mapping))
            if len(batch) >= batch_size:
                yield batch
                batch = []
        if batch:
            yield batch


def _truncate_in_reverse(dst_engine: Engine, ordered_tables: List[Table], log) -> None:
    """按 FK 反序清空 dest 表：PG 用 TRUNCATE CASCADE，SQLite 用逐表 DELETE FROM."""
    table_names = [t.name for t in reversed(ordered_tables) if not _is_skipped(t.name)]
    if not table_names:
        return
    is_pg = dst_engine.dialect.name == "postgresql"
    log(f"\n[truncate] 反向清空 dest {len(table_names)} 表（{'PG TRUNCATE CASCADE' if is_pg else 'SQLite DELETE FROM'}）...")
    with dst_engine.begin() as conn:
        if is_pg:
            joined = ", ".join(f'"{n}"' for n in table_names)
            try:
                conn.exec_driver_sql(f"TRUNCATE TABLE {joined} CASCADE")
                log(f"  [OK] TRUNCATE CASCADE 完成")
            except Exception as e:
                log(f"  [WARN] TRUNCATE 失败：{str(e)[:200]}")
        else:
            # SQLite: DELETE FROM 逐表（反向顺序避免 FK 冲突）
            for n in table_names:
                try:
                    conn.exec_driver_sql(f'DELETE FROM "{n}"')
                except Exception as e:
                    log(f"  [WARN] DELETE FROM {n}：{str(e)[:120]}")


def _insert_rows(dst_engine: Engine, table: Table, rows: List[Dict[str, Any]]) -> int:
    """批量插入。返回成功行数.

    - 按 dst 列名过滤 row dict（防 src 多出列、防 schema 漂移）
    - dst 多出的列依靠 default / NULL
    """
    if not rows:
        return 0
    dst_is_pg = dst_engine.dialect.name == "postgresql"
    dst_cols = {c.name for c in table.columns}
    cleaned = []
    for r in rows:
        filtered = {k: v for k, v in r.items() if k in dst_cols}
        cleaned.append(_convert_row_for_dst(table.name, filtered, dst_is_pg))
    with dst_engine.begin() as conn:
        conn.execute(table.insert(), cleaned)
    return len(cleaned)


def _migrate_table(
    src_engine: Engine,
    dst_engine: Engine,
    src_table: Table,
    dst_table: Table,
    log,
    *,
    skip_fk_orphans: bool = False,
    src_meta: MetaData | None = None,
) -> tuple[int, int]:
    """迁移单表，返回 (源行数, 目标插入行数)."""
    src_count = 0
    dst_inserted = 0
    dropped_orphans = 0
    name = src_table.name
    t0 = time.time()
    for batch in _fetch_rows(src_engine, src_table):
        src_count += len(batch)
        if skip_fk_orphans and src_meta is not None:
            # 关键：以 dst 已迁移的父表 PK 为准（级联跳过）
            batch, dropped = _filter_fk_orphans(dst_engine, src_meta, src_table, batch)
            dropped_orphans += dropped
        dst_inserted += _insert_rows(dst_engine, dst_table, batch)
    dur = time.time() - t0
    suffix = f"  (-{dropped_orphans} orphans)" if dropped_orphans else ""
    log(f"  [OK] {name:40s} {src_count:>6d} rows  ({dur:.2f}s){suffix}")
    return src_count, dst_inserted


def _filter_fk_orphans(
    dst_engine: Engine,
    src_meta: MetaData,
    src_table: Table,
    rows: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], int]:
    """从 rows 中过滤掉 FK 引用不存在父记录的孤儿行。

    **关键**：以 ``dst_engine`` 已写入的父表 PK 为准（不是 src），
    这样级联：父表跳过的孤儿在 dst 不存在 → 子表对应行自动级联跳过，
    避免连锁 ForeignKeyViolation。

    返回 (clean_rows, dropped_count)。同张子表的每一批都会重新查一次父表
    PK 集合（数据增量）；对同进程内多 batch，可优化为缓存（当前每张子表
    在第一个 batch 后才有完整父集，简单但正确）。
    """
    if not src_table.foreign_keys:
        return rows, 0
    src_names = set(src_meta.tables.keys())
    valid_pks: Dict[tuple[str, str], set] = {}
    for fk in src_table.foreign_keys:
        parent_table = fk.column.table.name
        parent_col = fk.column.name
        key = (parent_table, parent_col)
        if key in valid_pks or parent_table not in src_names:
            continue
        with dst_engine.connect() as c:
            sql = f'SELECT "{parent_col}" FROM "{parent_table}" WHERE "{parent_col}" IS NOT NULL'
            valid_pks[key] = {r[0] for r in c.exec_driver_sql(sql).fetchall()}

    clean: List[Dict[str, Any]] = []
    dropped = 0
    for row in rows:
        ok = True
        for fk in src_table.foreign_keys:
            child_col = fk.parent.name
            parent_table = fk.column.table.name
            parent_col = fk.column.name
            v = row.get(child_col)
            if v is None:
                continue
            if (parent_table, parent_col) not in valid_pks:
                continue
            if v not in valid_pks[(parent_table, parent_col)]:
                ok = False
                break
        if ok:
            clean.append(row)
        else:
            dropped += 1
    return clean, dropped


def migrate(
    src_url: str,
    dst_url: str,
    *,
    dry_run: bool = False,
    truncate: bool = True,
    skip_fk_orphans: bool = False,
    log=print,
) -> int:
    """主流程 — 返回退出码."""
    log(f"\n[migrate] src = {src_url[:60]}...")
    log(f"[migrate] dst = {dst_url[:60]}...")
    if dry_run:
        log("[migrate] DRY RUN: 仅打印行数对照表与 FK 孤儿扫描，不写")

    # ── 1. 加载 ORM metadata（拿 FK 拓扑）─────
    metadata = _load_orm_metadata()
    ordered = list(metadata.sorted_tables)  # FK-aware
    log(f"[migrate] ORM 共 {len(ordered)} 表（按 FK 拓扑序）")

    # ── 2. 检查源/目标可达 ─────────────────────
    src_engine = create_engine(src_url)
    dst_engine = create_engine(dst_url) if not dry_run else None
    try:
        with src_engine.connect() as c:
            c.exec_driver_sql("SELECT 1")
        if dst_engine is not None:
            with dst_engine.connect() as c:
                c.exec_driver_sql("SELECT 1")
    except Exception as e:
        log(f"[migrate] ERROR: 连接失败：{e}")
        return 1

    # ── 3. 反射源库（跳过 vec_* 等虚拟表）─────
    src_meta = _reflect_src_safe(src_engine)
    src_names = set(src_meta.tables.keys())
    log(f"[migrate] 源库实际 {len(src_names)} 表（已跳过 vec_*/sqlite_sequence）")

    if dry_run:
        log("\n[migrate] 行数对照表：")
        for t in ordered:
            if _is_skipped(t.name):
                continue
            if t.name not in src_names:
                log(f"  - {t.name:40s} (源库无此表)")
                continue
            with src_engine.connect() as c:
                n = c.exec_driver_sql(f'SELECT COUNT(*) FROM "{t.name}"').scalar()
            log(f"  {t.name:40s} {n:>6d} rows")
        # FK 孤儿扫描（仅 dry-run）
        _audit_fk_orphans(src_engine, src_meta, ordered, log)
        return 0

    # ── 4. truncate dest（默认行为）────────────
    if truncate:
        _truncate_in_reverse(dst_engine, ordered, log)

    # ── 5. 按拓扑序迁移 ────────────────────────
    log("\n[migrate] 开始迁移（FK 正序 INSERT）...")
    if skip_fk_orphans:
        log("[migrate] 启用 --skip-fk-orphans：自动跳过 FK 引用孤儿的行")
    total_src = 0
    total_dst = 0
    total_orphans = 0
    skipped: List[str] = []
    failed: List[tuple[str, str]] = []
    for t in ordered:
        if _is_skipped(t.name):
            skipped.append(t.name)
            continue
        if t.name not in src_names:
            skipped.append(f"{t.name} (源库无)")
            continue
        try:
            s, d = _migrate_table(
                src_engine,
                dst_engine,
                src_meta.tables[t.name],
                t,
                log,
                skip_fk_orphans=skip_fk_orphans,
                src_meta=src_meta,
            )
            total_src += s
            total_dst += d
            total_orphans += s - d if skip_fk_orphans else 0
        except Exception as e:
            log(f"  [FAIL] {t.name}: {str(e)[:200]}")
            failed.append((t.name, str(e)))

    # ── 6. 总结 ────────────────────────────────
    log(f"\n[migrate] 完成：源 {total_src} 行 → 目标 {total_dst} 行")
    if skipped:
        log(f"[migrate] 跳过 {len(skipped)} 表：{', '.join(skipped[:8])}{'...' if len(skipped) > 8 else ''}")
    if failed:
        log(f"\n[migrate] FAILED {len(failed)} 表：")
        for name, err in failed:
            log(f"  [FAIL] {name}: {err[:200]}")
        return 2

    if total_src != total_dst:
        log(f"\n[migrate] WARN: src/dst 行数不一致 ({total_src} vs {total_dst})")
        return 3
    log("\n[migrate] OK")
    return 0


def _env_bool(name: str, default: bool = False) -> bool:
    """环境变量布尔解析。

    `bool(os.environ.get(...))` 是常见陷阱：任何非空字符串（包括 "false"、"0"、"no"）
    都被视为 True。这里按业界惯例，只把 truthy 串视为 True。
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "y"}


def main() -> int:
    p = argparse.ArgumentParser(description="SQLite → Postgres 全量数据迁移")
    p.add_argument("src_url", nargs="?", default=os.environ.get("SRC_DATABASE_URL"),
                   help="源 SQLite URL（或环境变量 SRC_DATABASE_URL）")
    p.add_argument("dst_url", nargs="?", default=os.environ.get("DST_DATABASE_URL"),
                   help="目标 PG URL（或环境变量 DST_DATABASE_URL）")
    p.add_argument("--dry-run", action="store_true",
                   default=_env_bool("MIGRATION_DRY_RUN"),
                   help="仅打印行数对照表，不写目标库")
    p.add_argument("--no-truncate", action="store_true",
                   help="不先 TRUNCATE dest（适合追加模式；可能违反 PK 唯一约束）")
    p.add_argument("--skip-fk-orphans", action="store_true",
                   default=_env_bool("MIGRATION_SKIP_FK_ORPHANS"),
                   help="跳过 FK 引用孤儿行（SQLite 默认不强制 FK 累积的脏数据）")
    args = p.parse_args()

    if not args.src_url or not args.dst_url:
        p.error("src_url 和 dst_url 必填（或通过环境变量 SRC_DATABASE_URL / DST_DATABASE_URL）")

    return migrate(
        args.src_url,
        args.dst_url,
        dry_run=args.dry_run,
        truncate=not args.no_truncate,
        skip_fk_orphans=args.skip_fk_orphans,
    )


if __name__ == "__main__":
    sys.exit(main())
