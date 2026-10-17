"""P2-E 单元测试 · SQLite→Postgres 迁移管道（不依赖外部 PG）

策略：用两个临时 SQLite 库 round-trip：src（含真实数据）→ dst（空 ORM schema）。
覆盖：
- ORM 反射 + FK 拓扑序
- 跨方言转换（embedding TEXT JSON 维持）
- dst 列过滤（src 多出列时不炸）
- skip_tables（vec_*, sqlite_sequence, alembic_version）
- 行数对照 / PK 抽样 / FK 完整性
"""
from __future__ import annotations

import json
import os
import sys
import sqlite3
import tempfile
from pathlib import Path

import pytest

# infra/migration 入 sys.path
_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "infra" / "migration"))

from sqlalchemy import create_engine  # noqa: E402

# 触发 ORM 注册
import app.schemas  # noqa: F401, E402
import app.schemas.model_registry  # noqa: F401, E402
from app.database import Base  # noqa: E402

import sqlite_to_postgres as migrate_mod  # noqa: E402
import verify_migration as verify_mod  # noqa: E402


@pytest.fixture
def round_trip_envs(tmp_path: Path):
    """构造 src + dst 两个 SQLite 库.

    src：用 ORM Session 写测试数据（含 claim_cards.embedding JSON 字符串）
    dst：建表（空）
    """
    from sqlalchemy.orm import sessionmaker
    from app.schemas.raw_source import RawSource
    from app.schemas.speaker_profile import SpeakerProfile
    from app.schemas.claim_card import ClaimCard, ClaimRawSourceLink

    src_path = tmp_path / "src.db"
    dst_path = tmp_path / "dst.db"

    src_engine = create_engine(f"sqlite:///{src_path.as_posix()}")
    dst_engine = create_engine(f"sqlite:///{dst_path.as_posix()}")

    Base.metadata.create_all(bind=src_engine)
    Base.metadata.create_all(bind=dst_engine)

    # ── 用 ORM 写入（自动处理 default + JSON 序列化）────────
    SrcSession = sessionmaker(bind=src_engine)
    s = SrcSession()
    s.add(RawSource(
        id="01TESTRAWSOURCE0000000001",
        source_type="note",
        title="src1",
        original_url="http://example/1",
        raw_text="test content",
    ))
    s.add(SpeakerProfile(
        id="01TESTSPEAKER000000000001",
        name="alice",
    ))
    s.flush()
    # claim_cards.embedding 用 JSON 字符串（与 SQLite 实际存储一致）
    fake_emb = json.dumps([0.1] * 256)
    s.add(ClaimCard(
        id="01TESTCLAIM00000000000001",
        summary="test claim summary",
        claim_type="asset_view",
        stance="bullish",
        speaker_id="01TESTSPEAKER000000000001",
        embedding=fake_emb,
    ))
    s.flush()
    s.add(ClaimRawSourceLink(
        claim_id="01TESTCLAIM00000000000001",
        source_id="01TESTRAWSOURCE0000000001",
        link_type="extracted_from",
    ))
    s.commit()
    s.close()

    yield {
        "src_url": f"sqlite:///{src_path.as_posix()}",
        "dst_url": f"sqlite:///{dst_path.as_posix()}",
        "src_path": src_path,
        "dst_path": dst_path,
    }

    src_engine.dispose()
    dst_engine.dispose()


# ── 单测 ──────────────────────────────────────────────────────────


def test_is_skipped():
    assert migrate_mod._is_skipped("alembic_version") is True
    assert migrate_mod._is_skipped("sqlite_sequence") is True
    assert migrate_mod._is_skipped("vec_claims") is True
    assert migrate_mod._is_skipped("vec_claims_map") is True
    assert migrate_mod._is_skipped("claim_cards") is False
    assert migrate_mod._is_skipped("raw_sources") is False


def test_convert_row_for_dst_sqlite_keeps_json_string():
    row = {"embedding": json.dumps([0.1, 0.2, 0.3])}
    out = migrate_mod._convert_row_for_dst("claim_cards", dict(row), dst_is_pg=False)
    # SQLite dst：保持原字符串
    assert isinstance(out["embedding"], str)
    parsed = json.loads(out["embedding"])
    assert parsed == [0.1, 0.2, 0.3]


def test_convert_row_for_dst_pg_parses_to_list():
    row = {"embedding": json.dumps([0.1, 0.2, 0.3])}
    out = migrate_mod._convert_row_for_dst("claim_cards", dict(row), dst_is_pg=True)
    # PG dst：转 list[float]
    assert isinstance(out["embedding"], list)
    assert out["embedding"] == [0.1, 0.2, 0.3]


def test_convert_row_for_dst_handles_null():
    for dst_is_pg in (True, False):
        out = migrate_mod._convert_row_for_dst(
            "claim_cards", {"embedding": None}, dst_is_pg=dst_is_pg
        )
        assert out["embedding"] is None
        out = migrate_mod._convert_row_for_dst(
            "claim_cards", {"embedding": ""}, dst_is_pg=dst_is_pg
        )
        assert out["embedding"] is None


def test_convert_row_for_dst_skips_non_claim_card_table():
    row = {"summary": "foo", "asset_scope": "[]"}
    out = migrate_mod._convert_row_for_dst("event_cards", dict(row), dst_is_pg=False)
    # event_cards 不在跨方言列里，原样返回
    assert out == row


def test_convert_row_for_dst_invalid_json_to_none():
    out = migrate_mod._convert_row_for_dst(
        "claim_cards", {"embedding": "not json"}, dst_is_pg=True
    )
    assert out["embedding"] is None


def test_reflect_src_safe_skips_vec_tables(round_trip_envs):
    src_engine = create_engine(round_trip_envs["src_url"])
    src_meta = migrate_mod._reflect_src_safe(src_engine)
    names = set(src_meta.tables.keys())
    assert "claim_cards" in names
    assert not any(n.startswith("vec_") for n in names)
    src_engine.dispose()


def test_migrate_round_trip(round_trip_envs):
    """端到端：src SQLite → dst SQLite，行数应一致 + embedding JSON 保留."""
    rc = migrate_mod.migrate(
        round_trip_envs["src_url"],
        round_trip_envs["dst_url"],
        dry_run=False,
        truncate=True,
    )
    assert rc == 0

    # 验证 dst 数据
    dst_conn = sqlite3.connect(str(round_trip_envs["dst_path"]))
    n_claims = dst_conn.execute("SELECT COUNT(*) FROM claim_cards").fetchone()[0]
    assert n_claims == 1
    n_raws = dst_conn.execute("SELECT COUNT(*) FROM raw_sources").fetchone()[0]
    assert n_raws == 1
    n_links = dst_conn.execute("SELECT COUNT(*) FROM claim_raw_source_links").fetchone()[0]
    assert n_links == 1

    # embedding 完整保留（应该是 JSON string，可解析为 256 维 list）
    emb_str = dst_conn.execute(
        "SELECT embedding FROM claim_cards WHERE id = '01TESTCLAIM00000000000001'"
    ).fetchone()[0]
    parsed = json.loads(emb_str)
    assert isinstance(parsed, list)
    assert len(parsed) == 256
    dst_conn.close()


def test_dry_run_no_writes(round_trip_envs):
    """dry-run 不应写 dst."""
    rc = migrate_mod.migrate(
        round_trip_envs["src_url"],
        "postgresql://nope:nope@nope:5432/nope",  # 故意不可连
        dry_run=True,
    )
    assert rc == 0  # dry-run 不连 dst

    # dst 没写过
    dst_conn = sqlite3.connect(str(round_trip_envs["dst_path"]))
    n = dst_conn.execute("SELECT COUNT(*) FROM claim_cards").fetchone()[0]
    assert n == 0
    dst_conn.close()


def test_verify_passes_after_round_trip(round_trip_envs):
    """先 migrate 再 verify，应全过."""
    rc = migrate_mod.migrate(
        round_trip_envs["src_url"],
        round_trip_envs["dst_url"],
        dry_run=False,
        truncate=True,
    )
    assert rc == 0

    rc = verify_mod.verify(
        round_trip_envs["src_url"],
        round_trip_envs["dst_url"],
    )
    # 0=ok, 3=warn（如 alembic_version 缺失），都接受
    assert rc in (0, 3)


def test_verify_detects_row_count_mismatch(round_trip_envs):
    """人工破坏 dst → verify 应报 error."""
    migrate_mod.migrate(
        round_trip_envs["src_url"],
        round_trip_envs["dst_url"],
        dry_run=False,
    )

    # 删一行制造不一致
    dst_conn = sqlite3.connect(str(round_trip_envs["dst_path"]))
    dst_conn.execute("DELETE FROM claim_raw_source_links")
    dst_conn.commit()
    dst_conn.close()

    rc = verify_mod.verify(
        round_trip_envs["src_url"],
        round_trip_envs["dst_url"],
    )
    assert rc == 2  # error


# ── doctor pass 引入的回归测试 ────────────────────────────────────


def test_env_bool_handles_falsy_strings(monkeypatch):
    """坑 #3 回归：bool('false') / bool('0') 不应被误判为 True."""
    for falsy in ["false", "0", "no", "off", "n", "", "False", "  false  "]:
        monkeypatch.setenv("X_DRILL_BOOL", falsy)
        assert migrate_mod._env_bool("X_DRILL_BOOL") is False, f"{falsy!r} should be False"

    for truthy in ["1", "true", "yes", "on", "y", "TRUE", "  Yes  "]:
        monkeypatch.setenv("X_DRILL_BOOL", truthy)
        assert migrate_mod._env_bool("X_DRILL_BOOL") is True, f"{truthy!r} should be True"

    monkeypatch.delenv("X_DRILL_BOOL", raising=False)
    assert migrate_mod._env_bool("X_DRILL_BOOL") is False
    assert migrate_mod._env_bool("X_DRILL_BOOL", default=True) is True


def _insert_orphan_link(src_engine, claim_id: str, source_id: str) -> None:
    """工具：往 claim_raw_source_links 插一行，填齐 NOT NULL 时间戳.

    ORM 真实列：claim_id, source_id, link_type, created_at, updated_at（无 relevance_score）。
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with src_engine.begin() as c:
        c.exec_driver_sql(
            "INSERT INTO claim_raw_source_links "
            "(claim_id, source_id, link_type, created_at, updated_at) "
            f"VALUES ('{claim_id}', '{source_id}', 'extracted_from', '{now}', '{now}')"
        )


def test_audit_fk_orphans_finds_dangling_refs(round_trip_envs):
    """坑 #5 回归：dry-run audit 应找到 FK 孤儿."""
    from sqlalchemy import MetaData

    src_engine = create_engine(round_trip_envs["src_url"])

    # 在 src 制造孤儿：插一行 claim_raw_source_links 引用不存在的 source
    _insert_orphan_link(src_engine, "01CLAIMORPHAN", "nonexistent_src")

    src_meta = MetaData()
    src_meta.reflect(bind=src_engine)
    ordered = list(Base.metadata.sorted_tables)

    captured: list[str] = []
    report = migrate_mod._audit_fk_orphans(
        src_engine, src_meta, ordered, log=lambda msg: captured.append(str(msg))
    )

    # 至少 claim_raw_source_links 报了孤儿
    assert "claim_raw_source_links" in report
    src_engine.dispose()


def test_skip_fk_orphans_cascades_via_dst(round_trip_envs):
    """坑 #6 回归：父表跳过的孤儿，子表也应级联跳过."""
    src_engine = create_engine(round_trip_envs["src_url"])

    # 在 src 制造孤儿 link：link 指向不存在的 raw_source
    with src_engine.begin() as c:
        cid = c.exec_driver_sql("SELECT id FROM claim_cards LIMIT 1").scalar()
    _insert_orphan_link(src_engine, cid, "orphan_src_id")
    src_engine.dispose()

    # 跑迁移 with skip-fk-orphans
    rc = migrate_mod.migrate(
        round_trip_envs["src_url"],
        round_trip_envs["dst_url"],
        dry_run=False,
        skip_fk_orphans=True,
    )
    # rc 可能是 0（全部 OK）或 3（行数 warn 因为孤儿被跳过）
    assert rc in (0, 3)

    # dst 不应有 orphan_src_id 的链接
    dst_conn = sqlite3.connect(str(round_trip_envs["dst_path"]))
    n = dst_conn.execute(
        "SELECT count(*) FROM claim_raw_source_links WHERE source_id='orphan_src_id'"
    ).fetchone()[0]
    dst_conn.close()
    assert n == 0, "孤儿 link 应被 skip-fk-orphans 过滤"


def test_verify_allow_orphans_downgrades_error_to_warn(round_trip_envs):
    """坑 #7 回归：check_row_counts 接受 allow_orphans 应把行数差从 error 降到 warn.

    单测 ``check_row_counts``（避开 PK sampling 的随机性干扰）。
    """
    from sqlalchemy import MetaData

    migrate_mod.migrate(
        round_trip_envs["src_url"],
        round_trip_envs["dst_url"],
        dry_run=False,
    )

    # 删 1 行 dst.claim_raw_source_links 制造 dst < src
    dst_conn = sqlite3.connect(str(round_trip_envs["dst_path"]))
    dst_conn.execute(
        "DELETE FROM claim_raw_source_links WHERE rowid=(SELECT rowid FROM claim_raw_source_links LIMIT 1)"
    )
    dst_conn.commit()
    dst_conn.close()

    src_engine = create_engine(round_trip_envs["src_url"])
    dst_engine = create_engine(round_trip_envs["dst_url"])
    src_meta = MetaData()
    src_meta.reflect(bind=src_engine)
    ordered = list(Base.metadata.sorted_tables)

    # 不带 allow_orphans → 至少 1 个 error
    results = verify_mod.check_row_counts(src_engine, dst_engine, ordered, set(src_meta.tables.keys()))
    errors = [r for r in results if r.level == "error"]
    assert any("claim_raw_source_links" in r.msg for r in errors)

    # 带 allow_orphans 容忍 1 行 → 该表只 warn，不 error
    results = verify_mod.check_row_counts(
        src_engine, dst_engine, ordered, set(src_meta.tables.keys()),
        allow_orphans={"claim_raw_source_links": 1},
    )
    errors = [r for r in results if r.level == "error" and "claim_raw_source_links" in r.msg]
    warns = [r for r in results if r.level == "warn" and "claim_raw_source_links" in r.msg]
    assert not errors, f"应无 error，实际：{errors}"
    assert warns, f"应有 warn，实际 results={results}"

    src_engine.dispose()
    dst_engine.dispose()
