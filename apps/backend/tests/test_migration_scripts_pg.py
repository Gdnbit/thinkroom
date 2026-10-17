"""P3 doctor pass · PG 集成测试（CI 自动化的核心）

把 doctor pass 手工实测自动化进 CI：
- 跑 alembic upgrade head against 真实 PG（pgvector），断言 current 到达当前唯一 head + alembic check 零 drift
- 跑 SQLite → PG 迁移（含 FK 孤儿）+ verify
- 验证 pg_typeof(embedding) = vector

这些测试要求宿主有可达的 Postgres + pgvector，由 ``DRILL_PG_URL`` 环境变量提供：

    export DRILL_PG_URL="postgresql://drill:drill_pass@localhost:5433/drill_db"
    pytest -m pg

CI 起 ``ankane/pgvector`` service container 后注入这个 URL。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# infra/migration 入 sys.path
_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "infra" / "migration"))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

# 触发 ORM 注册
import app.schemas  # noqa: F401, E402
from app.database import Base  # noqa: E402

import sqlite_to_postgres as migrate_mod  # noqa: E402
import verify_migration as verify_mod  # noqa: E402


pytestmark = pytest.mark.pg


# ── PG 可达性 + 自动跳过 ──────────────────────────────────────────


def _pg_url() -> str | None:
    """读 DRILL_PG_URL 环境变量；未设则跳过整个文件."""
    return os.environ.get("DRILL_PG_URL", "").strip() or None


@pytest.fixture(scope="module")
def pg_url():
    url = _pg_url()
    if not url:
        pytest.skip("DRILL_PG_URL not set; PG integration tests skipped")
    # 探活
    eng = create_engine(url)
    try:
        with eng.connect() as c:
            c.exec_driver_sql("SELECT 1")
    except Exception as e:
        eng.dispose()
        pytest.skip(f"PG not reachable at {url}: {e}")
    eng.dispose()
    return url


@pytest.fixture
def pg_engine(pg_url):
    """每个测试拿一个干净的 PG schema：DROP SCHEMA + CREATE EXTENSION vector."""
    eng = create_engine(pg_url)
    with eng.begin() as c:
        c.exec_driver_sql("DROP SCHEMA IF EXISTS public CASCADE")
        c.exec_driver_sql("CREATE SCHEMA public")
        c.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
    yield eng
    eng.dispose()


# ── alembic upgrade head against PG ─────────────────────────────


def test_alembic_upgrade_head_pg(pg_url, pg_engine):
    """坑 #1/#2/#4 整体回归：fresh PG alembic upgrade head 必须到当前唯一 head + 无 drift.

    这个测试复现 doctor pass 的核心断言。任何让 PG fresh 升级失败的回归
    （BOOLEAN DEFAULT 1、新表漏建等）都会在 CI 阶段被拦下。
    """
    import subprocess
    backend_dir = _REPO_ROOT / "apps" / "backend"
    env = os.environ.copy()
    env.update({
        "DATABASE_URL": pg_url,
        "PGVECTOR_ENABLED": "true",
        "EMBEDDING_DIM": "2560",
        "APP_ENV": "test",
        "LOG_DIR": "",
        "SENTRY_DSN": "",
        "OPENROUTER_API_KEY": "",
        "APP_API_KEY": "",
        "PYTHONIOENCODING": "utf-8",
    })

    # alembic upgrade head
    r = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=str(backend_dir), env=env,
        capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, f"alembic upgrade head failed:\n{r.stderr}\n{r.stdout}"

    # alembic current 必须包含当前唯一 head，避免新增迁移后测试仍硬编码旧 revision。
    r = subprocess.run(
        ["alembic", "heads"],
        cwd=str(backend_dir), env=env,
        capture_output=True, text=True, timeout=30,
    )
    heads_out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, f"alembic heads failed:\n{heads_out}"
    expected_heads = [line.split()[0] for line in heads_out.splitlines() if line.strip()]
    assert len(expected_heads) == 1, f"expected exactly one alembic head:\n{heads_out}"

    r = subprocess.run(
        ["alembic", "current"],
        cwd=str(backend_dir), env=env,
        capture_output=True, text=True, timeout=30,
    )
    out = (r.stdout or "") + (r.stderr or "")
    assert expected_heads[0] in out, f"current is not at head {expected_heads[0]}:\n{out}"

    # alembic check 必须无 drift
    r = subprocess.run(
        ["alembic", "check"],
        cwd=str(backend_dir), env=env,
        capture_output=True, text=True, timeout=30,
    )
    out = (r.stdout or "") + (r.stderr or "")
    assert "No new upgrade operations detected" in out, \
        f"alembic check reported drift on PG:\n{out}"


def test_decision_trace_source_links_backfill_pg(pg_url, pg_engine):
    """PG upgrade 0017 -> head must backfill decision trace source links.

    This protects the 0018 migration path for production databases that already
    contain decision_trace_events rows before the explicit source-link table
    exists.
    """
    import subprocess
    backend_dir = _REPO_ROOT / "apps" / "backend"
    env = os.environ.copy()
    env.update({
        "DATABASE_URL": pg_url,
        "PGVECTOR_ENABLED": "true",
        "EMBEDDING_DIM": "2560",
        "APP_ENV": "test",
        "LOG_DIR": "",
        "SENTRY_DSN": "",
        "OPENROUTER_API_KEY": "",
        "APP_API_KEY": "",
        "PYTHONIOENCODING": "utf-8",
    })

    r = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0017_decision_trace_query_indexes"],
        cwd=str(backend_dir), env=env,
        capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, f"alembic upgrade to 0017 failed:\n{r.stderr}\n{r.stdout}"

    with pg_engine.begin() as c:
        c.exec_driver_sql(
            """
            INSERT INTO decision_trace_events (
                related_object_type, related_object_id, run_id, session_id, job_id,
                event_type, step_name, status, model, skill_role,
                duration_ms, cost_usd, source_ids, payload,
                id, created_at, updated_at
            )
            VALUES (
                'chat_message', 'assistant-pg-source-backfill', 'run-pg-source-backfill',
                NULL, NULL, 'scope', 'source_scope', 'ok', NULL, NULL,
                NULL, NULL, '["source-a", "source-b", "source-a"]'::jsonb, '{}'::jsonb,
                '01KPGTRACESOURCEBACKFILL', now(), now()
            )
            """
        )

    r = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(backend_dir), env=env,
        capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, f"alembic upgrade head failed:\n{r.stderr}\n{r.stdout}"

    with pg_engine.connect() as c:
        rows = c.exec_driver_sql(
            """
            SELECT trace_event_id, source_id
            FROM decision_trace_source_links
            WHERE trace_event_id = '01KPGTRACESOURCEBACKFILL'
            ORDER BY source_id
            """
        ).fetchall()
        index_rows = c.exec_driver_sql(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE tablename = 'decision_trace_source_links'
              AND indexname = 'idx_decision_trace_source_links_source'
            """
        ).fetchall()

    assert rows == [
        ("01KPGTRACESOURCEBACKFILL", "source-a"),
        ("01KPGTRACESOURCEBACKFILL", "source-b"),
    ]
    assert index_rows, "idx_decision_trace_source_links_source missing on PG"


# ── SQLite → PG 迁移 + FK 孤儿 ─────────────────────────────────


@pytest.fixture
def sqlite_src_with_orphans(tmp_path):
    """构造 SQLite 源库，含 1 行 FK 孤儿 link 模拟生产 SQLite 长期开发的脏数据."""
    from datetime import datetime, timezone
    from app.schemas.raw_source import RawSource
    from app.schemas.speaker_profile import SpeakerProfile
    from app.schemas.claim_card import ClaimCard, ClaimRawSourceLink

    src_path = tmp_path / "src.db"
    src_engine = create_engine(f"sqlite:///{src_path.as_posix()}")
    Base.metadata.create_all(bind=src_engine)

    Session = sessionmaker(bind=src_engine)
    s = Session()
    s.add(RawSource(
        id="01TESTRAWSOURCE0000000001",
        source_type="note",
        title="src1",
        original_url="http://example/1",
        raw_text="test content",
    ))
    s.add(SpeakerProfile(id="01TESTSPEAKER000000000001", name="alice"))
    s.flush()
    # 注意：测试不构造真实 embedding（避免维度耦合 EMBEDDING_DIM）；
    # _convert_row_for_dst 对 None 自然 pass-through。pgvector 类型由独立
    # test_pgvector_extension_available 守护
    s.add(ClaimCard(
        id="01TESTCLAIM00000000000001",
        summary="test claim summary",
        claim_type="asset_view",
        stance="bullish",
        speaker_id="01TESTSPEAKER000000000001",
        embedding=None,
    ))
    s.flush()
    # 合法 link
    s.add(ClaimRawSourceLink(
        claim_id="01TESTCLAIM00000000000001",
        source_id="01TESTRAWSOURCE0000000001",
        link_type="extracted_from",
    ))
    s.commit()
    s.close()

    # 旁路 ORM 插一行孤儿 link（引用不存在的 source）— 模拟生产 SQLite 默认 FK OFF 的脏数据
    now = datetime.now(timezone.utc).isoformat()
    with src_engine.begin() as c:
        c.exec_driver_sql(
            "INSERT INTO claim_raw_source_links "
            "(claim_id, source_id, link_type, created_at, updated_at) "
            f"VALUES ('01TESTCLAIM00000000000001', 'orphan_src', 'extracted_from', '{now}', '{now}')"
        )

    yield {
        "src_url": f"sqlite:///{src_path.as_posix()}",
        "src_path": src_path,
    }
    src_engine.dispose()


def test_sqlite_to_pg_full_pipeline(pg_url, pg_engine, sqlite_src_with_orphans, tmp_path):
    """端到端：alembic upgrade PG → SQLite→PG 迁移（skip-fk-orphans）→ verify(allow-orphans).

    覆盖 doctor pass 坑 #3/#5/#6/#7 — env_bool / FK orphan audit / cascade /
    verify allow-orphans 全链。
    """
    import subprocess
    backend_dir = _REPO_ROOT / "apps" / "backend"

    env = os.environ.copy()
    env.update({
        "DATABASE_URL": pg_url,
        "PGVECTOR_ENABLED": "true",
        "EMBEDDING_DIM": "2560",
        "APP_ENV": "test",
        "LOG_DIR": "", "SENTRY_DSN": "",
        "OPENROUTER_API_KEY": "", "APP_API_KEY": "",
        "PYTHONIOENCODING": "utf-8",
    })

    # 1. alembic upgrade PG → 0012
    r = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=str(backend_dir), env=env,
        capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, f"alembic upgrade failed:\n{r.stderr}"

    # 2. dry-run 应识别孤儿
    rc = migrate_mod.migrate(
        sqlite_src_with_orphans["src_url"], pg_url,
        dry_run=True, log=lambda *_: None,
    )
    assert rc == 0

    # 3. 真跑迁移 + skip-fk-orphans
    rc = migrate_mod.migrate(
        sqlite_src_with_orphans["src_url"], pg_url,
        dry_run=False, skip_fk_orphans=True, log=lambda *_: None,
    )
    # 0=ok / 3=warn（行数不一致 因为 1 个孤儿被跳过）
    assert rc in (0, 3)

    # 4. PG 端断言 — 用 sqlalchemy 直接查
    eng = create_engine(pg_url)
    with eng.connect() as c:
        # claim_cards 1 行
        n = c.exec_driver_sql("SELECT count(*) FROM claim_cards").scalar()
        assert n == 1
        # raw_sources 1 行
        n = c.exec_driver_sql("SELECT count(*) FROM raw_sources").scalar()
        assert n == 1
        # claim_raw_source_links 应为 1 行（孤儿被跳过）
        n = c.exec_driver_sql("SELECT count(*) FROM claim_raw_source_links").scalar()
        assert n == 1
        # 没有指向 'orphan_src' 的 link
        n = c.exec_driver_sql(
            "SELECT count(*) FROM claim_raw_source_links WHERE source_id='orphan_src'"
        ).scalar()
        assert n == 0
        # embedding 列必须是 vector 类型（即使值为 NULL，列类型仍可断言）
        rows = c.exec_driver_sql(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name='claim_cards' AND column_name='embedding'"
        ).fetchall()
        assert rows, "claim_cards.embedding 列不存在"
        # PG 信息 schema 对 vector 类型显示为 'USER-DEFINED'；用 pg_type 直接确认
        rows = c.exec_driver_sql(
            "SELECT format_type(atttypid, atttypmod) "
            "FROM pg_attribute "
            "WHERE attrelid='claim_cards'::regclass AND attname='embedding'"
        ).fetchall()
        assert rows and "vector" in rows[0][0].lower(), \
            f"embedding 列类型不是 vector: {rows}"
    eng.dispose()

    # 5. verify with --allow-orphans 不应有 error
    captured: list[str] = []
    rc = verify_mod.verify(
        sqlite_src_with_orphans["src_url"], pg_url,
        allow_orphans={"claim_raw_source_links": 1},
        log=lambda msg: captured.append(str(msg)),
    )
    # 0=ok / 3=warn（孤儿被允许）
    assert rc in (0, 3), \
        f"verify 报 error: rc={rc}\n输出：\n" + "\n".join(captured[-20:])


# ── 备份 / 恢复（docker exec 模式可在 CI 跳，仅本地手工） ──────


def test_pgvector_extension_available(pg_engine):
    """确保 vector 扩展可见 — 任何 alembic 0009 路径依赖于此."""
    with pg_engine.connect() as c:
        rows = c.exec_driver_sql(
            "SELECT extversion FROM pg_extension WHERE extname='vector'"
        ).fetchall()
    assert rows, "vector 扩展未启用 (CREATE EXTENSION vector 失败?)"
    # 0.5.x 或更高
    version = rows[0][0]
    assert version, "extversion 为空"
