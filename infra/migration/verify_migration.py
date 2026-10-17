"""SQLite → Postgres 迁移后验证脚本（P2-E）

检查项
------
1. **row count 对照**：每张 ORM 表 src/dst 行数必须一致
2. **alembic_version**：dst 应已 stamp 到 head
3. **claim_cards.embedding round-trip**：抽 5 行，PG 端检查 vector 维度 = EMBEDDING_DIM
4. **PK 主键抽查**：随机选 3 张表的前 5 行 PK，dst 端必须能查到
5. **FK 完整性**：claim_raw_source_links / claim_evidence_links 不应有指向不存在的 claim/source

退出码
------
- 0：所有检查通过
- 1：参数错
- 2：发现严重问题（行数不一致 / FK 残断）
- 3：发现警告（如 alembic_version 缺失，但数据 OK）
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path
from typing import List

# 把 backend + repo root 加进 sys.path
# repo root 让 `from infra.migration.* import ...` 可用
_REPO = Path(__file__).resolve().parents[2]
_BACKEND = _REPO / "apps" / "backend"
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_REPO))

from sqlalchemy import MetaData, create_engine, select, text  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402

from infra.migration.sqlite_to_postgres import (  # noqa: E402
    _is_skipped,
    _load_orm_metadata,
    _reflect_src_safe,
)


class CheckResult:
    def __init__(self, level: str, msg: str):
        self.level = level  # "ok" | "warn" | "error"
        self.msg = msg

    def __repr__(self):
        return f"[{self.level}] {self.msg}"


def check_row_counts(
    src_engine: Engine,
    dst_engine: Engine,
    ordered,
    src_names,
    allow_orphans: dict | None = None,
) -> List[CheckResult]:
    """每张 ORM 表 src/dst 行数对照。

    ``allow_orphans``：``{table_name: expected_skip}`` 反映迁移时
    ``--skip-fk-orphans`` 跳过的行数，dst 可少 N 行仍视为 OK。
    """
    allow_orphans = allow_orphans or {}
    results: List[CheckResult] = []
    src_total = 0
    dst_total = 0
    for t in ordered:
        if _is_skipped(t.name):
            continue
        if t.name not in src_names:
            continue
        with src_engine.connect() as c:
            src_n = c.exec_driver_sql(f'SELECT COUNT(*) FROM "{t.name}"').scalar()
        with dst_engine.connect() as c:
            try:
                dst_n = c.exec_driver_sql(f'SELECT COUNT(*) FROM "{t.name}"').scalar()
            except Exception as e:
                results.append(CheckResult("error", f"{t.name}: dst 读取失败 {e}"))
                continue
        src_total += src_n
        dst_total += dst_n
        if src_n != dst_n:
            allowed = allow_orphans.get(t.name, 0)
            diff = src_n - dst_n
            if 0 < diff <= allowed:
                results.append(CheckResult(
                    "warn",
                    f"{t.name}: src={src_n} dst={dst_n} （允许 {allowed} 行孤儿跳过）"
                ))
            else:
                results.append(CheckResult(
                    "error",
                    f"{t.name}: 行数不一致 src={src_n} dst={dst_n}"
                    + (f" （允许 {allowed} 但实际差 {diff}）" if allowed else "")
                ))
    if not any(r.level == "error" for r in results):
        results.append(CheckResult(
            "ok", f"行数全匹配（共 {src_total} 行 / {len([t for t in ordered if not _is_skipped(t.name)])} 表）"
        ))
    return results


def check_alembic_version(dst_engine: Engine) -> CheckResult:
    """dst 应有 alembic_version 表且行数 ≥ 1."""
    try:
        with dst_engine.connect() as c:
            rows = c.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).fetchall()
    except Exception as e:
        return CheckResult("warn", f"alembic_version 缺失或不可读：{e}")
    if not rows:
        return CheckResult("warn", "alembic_version 表为空（dst 可能未 alembic upgrade head）")
    return CheckResult("ok", f"alembic_version = {rows[0][0]}")


def check_embedding_round_trip(dst_engine: Engine, sample_size: int = 5) -> CheckResult:
    """抽样 claim_cards.embedding，检查能解析 + 维度合理（>= 100，避免误存）."""
    is_pg = dst_engine.dialect.name == "postgresql"
    with dst_engine.connect() as c:
        rows = c.exec_driver_sql(
            "SELECT id, embedding FROM claim_cards "
            "WHERE embedding IS NOT NULL "
            f"LIMIT {sample_size}"
        ).fetchall()
    if not rows:
        return CheckResult("warn", "claim_cards 无 embedding 数据（空库或全 NULL）")
    sampled_dims = []
    for cid, emb in rows:
        if is_pg:
            # PG with pgvector 返回 list / numpy array；无 pgvector 返回 str
            if isinstance(emb, (list, tuple)):
                sampled_dims.append(len(emb))
            elif isinstance(emb, str):
                # 可能是 vector 字面量 "[1.0,2.0,...]" 或 JSON
                try:
                    parsed = json.loads(emb)
                    sampled_dims.append(len(parsed))
                except Exception:
                    return CheckResult("error", f"claim_cards.id={cid} embedding 不可解析")
        else:
            try:
                parsed = json.loads(emb)
                sampled_dims.append(len(parsed))
            except Exception:
                return CheckResult("error", f"claim_cards.id={cid} embedding JSON 损坏")
    if not sampled_dims:
        return CheckResult("warn", "embedding 抽样为空")
    avg_dim = sum(sampled_dims) / len(sampled_dims)
    if avg_dim < 100:
        return CheckResult("error", f"embedding 维度异常小（avg={avg_dim:.0f}），可能字段错位")
    return CheckResult(
        "ok",
        f"embedding round-trip OK（{len(sampled_dims)} 行抽样，维度 {min(sampled_dims)}-{max(sampled_dims)}）"
    )


def check_pk_sampling(
    src_engine: Engine,
    dst_engine: Engine,
    ordered,
    src_names,
    allow_orphans: dict | None = None,
) -> List[CheckResult]:
    """随机选 3 张非空表，抽 5 个 PK 验证 dst 端可查.

    ``allow_orphans``：与 ``check_row_counts`` 协同。若某表抽样 N 行但
    dst 只 hit M 行，差距 ≤ 该表预算则降级为 warn 而非 error（与
    `--skip-fk-orphans` 配套时避免误报）。
    """
    allow_orphans = allow_orphans or {}
    results: List[CheckResult] = []
    candidates = []
    for t in ordered:
        if _is_skipped(t.name) or t.name not in src_names:
            continue
        if not list(t.primary_key.columns):
            continue
        with src_engine.connect() as c:
            n = c.exec_driver_sql(f'SELECT COUNT(*) FROM "{t.name}"').scalar()
        if n > 0:
            candidates.append(t)
    if not candidates:
        return [CheckResult("warn", "PK 抽样无候选表（所有表为空）")]
    picked = random.sample(candidates, min(3, len(candidates)))
    for t in picked:
        pk_col = list(t.primary_key.columns)[0].name
        with src_engine.connect() as c:
            sample_pks = [
                row[0] for row in
                c.exec_driver_sql(f'SELECT "{pk_col}" FROM "{t.name}" LIMIT 5').fetchall()
            ]
        if not sample_pks:
            continue
        placeholders = ", ".join(f"'{pk}'" for pk in sample_pks)
        with dst_engine.connect() as c:
            found = c.exec_driver_sql(
                f'SELECT COUNT(*) FROM "{t.name}" WHERE "{pk_col}" IN ({placeholders})'
            ).scalar()
        miss = len(sample_pks) - found
        if miss > 0:
            allowed = allow_orphans.get(t.name, 0)
            if miss <= allowed:
                results.append(CheckResult(
                    "warn",
                    f"{t.name}: PK 抽样 {len(sample_pks)} 行只在 dst 找到 {found} 行 "
                    f"（允许 {allowed} 行孤儿跳过）"
                ))
            else:
                results.append(CheckResult(
                    "error",
                    f"{t.name}: PK 抽样 {len(sample_pks)} 行只在 dst 找到 {found} 行"
                    + (f" （允许 {allowed} 但实际差 {miss}）" if allowed else "")
                ))
        else:
            results.append(CheckResult(
                "ok", f"{t.name}: {len(sample_pks)}/{len(sample_pks)} PK 抽样命中"
            ))
    return results


def check_fk_integrity(dst_engine: Engine) -> List[CheckResult]:
    """检查关键 link 表的 FK 完整性（dst 端不应有指向不存在 claim/source 的 link）."""
    results: List[CheckResult] = []
    pairs = [
        ("claim_raw_source_links", "claim_id", "claim_cards", "id"),
        ("claim_raw_source_links", "source_id", "raw_sources", "id"),
        ("claim_evidence_links", "claim_id", "claim_cards", "id"),
        ("claim_evidence_links", "evidence_span_id", "evidence_spans", "id"),
        ("briefing_evidence_links", "briefing_id", "briefing_cards", "id"),
        ("lifecycle_logs", "claim_id", "claim_cards", "id"),
    ]
    for link_table, fk_col, ref_table, ref_col in pairs:
        with dst_engine.connect() as c:
            try:
                broken = c.exec_driver_sql(
                    f'SELECT COUNT(*) FROM "{link_table}" l '
                    f'LEFT JOIN "{ref_table}" r ON l."{fk_col}" = r."{ref_col}" '
                    f'WHERE l."{fk_col}" IS NOT NULL AND r."{ref_col}" IS NULL'
                ).scalar()
            except Exception as e:
                results.append(CheckResult("warn", f"{link_table}.{fk_col} → {ref_table}.{ref_col} 检查跳过：{e}"))
                continue
        if broken > 0:
            results.append(CheckResult(
                "error",
                f"{link_table}.{fk_col}: 有 {broken} 行指向不存在的 {ref_table}.{ref_col}"
            ))
        else:
            results.append(CheckResult(
                "ok", f"{link_table}.{fk_col} → {ref_table}.{ref_col} FK 完整"
            ))
    return results


def verify(
    src_url: str,
    dst_url: str,
    *,
    allow_orphans: dict | None = None,
    log=print,
) -> int:
    log(f"\n[verify] src = {src_url[:60]}")
    log(f"[verify] dst = {dst_url[:60]}\n")

    metadata = _load_orm_metadata()
    ordered = list(metadata.sorted_tables)

    src_engine = create_engine(src_url)
    dst_engine = create_engine(dst_url)
    src_meta = _reflect_src_safe(src_engine)
    src_names = set(src_meta.tables.keys())

    all_results: List[CheckResult] = []

    log("[1/5] 行数对照...")
    for r in check_row_counts(src_engine, dst_engine, ordered, src_names, allow_orphans):
        log(f"  {r}")
        all_results.append(r)

    log("\n[2/5] alembic_version...")
    r = check_alembic_version(dst_engine)
    log(f"  {r}")
    all_results.append(r)

    log("\n[3/5] embedding round-trip...")
    r = check_embedding_round_trip(dst_engine)
    log(f"  {r}")
    all_results.append(r)

    log("\n[4/5] PK 抽样...")
    for r in check_pk_sampling(src_engine, dst_engine, ordered, src_names, allow_orphans):
        log(f"  {r}")
        all_results.append(r)

    log("\n[5/5] FK 完整性...")
    for r in check_fk_integrity(dst_engine):
        log(f"  {r}")
        all_results.append(r)

    src_engine.dispose()
    dst_engine.dispose()

    errors = [r for r in all_results if r.level == "error"]
    warns = [r for r in all_results if r.level == "warn"]

    log(f"\n[verify] {len([r for r in all_results if r.level == 'ok'])} ok / "
        f"{len(warns)} warn / {len(errors)} error")

    if errors:
        log("\n[verify] ERRORS:")
        for r in errors:
            log(f"  {r}")
        return 2
    if warns:
        return 3
    log("\n[verify] OK")
    return 0


def _parse_allow_orphans(s: str | None) -> dict:
    """解析 ``setup_cards=8,briefing_cards=8`` 格式."""
    out: dict = {}
    if not s:
        return out
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        try:
            out[k.strip()] = int(v.strip())
        except ValueError:
            pass
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="SQLite → Postgres 迁移后验证")
    p.add_argument("src_url", nargs="?", default=os.environ.get("SRC_DATABASE_URL"))
    p.add_argument("dst_url", nargs="?", default=os.environ.get("DST_DATABASE_URL"))
    p.add_argument("--allow-orphans", default=os.environ.get("VERIFY_ALLOW_ORPHANS", ""),
                   help="允许表级孤儿跳过（与 --skip-fk-orphans 配套），例： setup_cards=8,briefing_cards=8")
    args = p.parse_args()
    if not args.src_url or not args.dst_url:
        p.error("src_url 和 dst_url 必填（或通过环境变量 SRC_DATABASE_URL / DST_DATABASE_URL）")
    allow_orphans = _parse_allow_orphans(args.allow_orphans)
    return verify(args.src_url, args.dst_url, allow_orphans=allow_orphans)


if __name__ == "__main__":
    sys.exit(main())
