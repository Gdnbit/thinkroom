"""G15 Parent-Child Retrieval 单元测试

覆盖：
- 单 source 命中 1 条 → 期望附 N 条邻居（不含 hit）
- 单 source 命中多条 → 邻居只附一次（不重复）
- 多 source 命中 → 各自附邻居，受 max_sources 限制
- 同 source 没有其他 claim → 邻居 0
- hit_claim 没有 source link → 不崩
- neighbors_per_source=0 / max_sources=0 → 短路返回原列表
- 状态过滤：邻居中的 expired/invalidated 被排除
- _parent_neighbor_of 属性正确挂载
"""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from ulid import ULID

from app.database import Base
from app.schemas.claim_card import ClaimCard, ClaimRawSourceLink
from app.schemas.raw_source import RawSource
from app.services.parent_context import attach_parent_neighbors


# ── 测试 fixtures ────────────────────────────────────────────────


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _make_source(db, title: str = "Test source") -> RawSource:
    src = RawSource(
        id=str(ULID()),
        source_type="article",
        title=title,
        language="zh",
        status="completed",
    )
    db.add(src)
    db.flush()
    return src


def _make_claim(
    db,
    source: RawSource,
    summary: str,
    *,
    created_at: datetime,
    status: str = "active",
) -> ClaimCard:
    claim = ClaimCard(
        id=str(ULID()),
        summary=summary,
        full_quote=summary,
        claim_type="asset_view",
        stance="neutral",
        time_horizon="short",
        asset_scope=["BTC"],
        theme_scope=[],
        geographic_scope=[],
        conditions=[],
        invalidation_conditions=[],
        reasoning_basis=[],
        confidence=0.7,
        source_trust_weight=0.5,
        is_contradicted=False,
        status=status,
        created_at=created_at,
        updated_at=created_at,
    )
    db.add(claim)
    db.flush()
    link = ClaimRawSourceLink(
        claim_id=claim.id,
        source_id=source.id,
        link_type="extracted_from",
        created_at=created_at,
        updated_at=created_at,
    )
    db.add(link)
    db.flush()
    return claim


# ── 1. 基本场景：单 source 5 条 claim，命中第 3 条 ─────────────


def test_single_source_hit_one_attaches_neighbors(db_session):
    src = _make_source(db_session)
    base = datetime.now(timezone.utc) - timedelta(hours=5)
    claims = [
        _make_claim(db_session, src, f"claim {i}", created_at=base + timedelta(hours=i))
        for i in range(5)
    ]
    db_session.commit()

    # 命中 c2 (中间那条)
    hit = [claims[2]]
    result = attach_parent_neighbors(
        db_session, hit, neighbors_per_source=2, max_sources=3
    )

    # 应该返回 1 hit + 2 邻居 = 3 条
    assert len(result) == 3
    assert result[0].id == claims[2].id
    neighbor_ids = {result[1].id, result[2].id}
    # 邻居应该是离 c2 时间最近的 c1 和 c3
    assert claims[1].id in neighbor_ids
    assert claims[3].id in neighbor_ids
    # 邻居挂 _parent_neighbor_of
    for nb in result[1:]:
        assert getattr(nb, "_parent_neighbor_of", None) == claims[2].id


# ── 2. 单 source 命中多条 → 邻居只附一次 ─────────────────────


def test_multiple_hits_same_source_no_duplicate_neighbors(db_session):
    src = _make_source(db_session)
    base = datetime.now(timezone.utc) - timedelta(hours=5)
    claims = [
        _make_claim(db_session, src, f"c{i}", created_at=base + timedelta(hours=i))
        for i in range(5)
    ]
    db_session.commit()

    # 命中 c0, c2 (同 source)
    hit = [claims[0], claims[2]]
    result = attach_parent_neighbors(
        db_session, hit, neighbors_per_source=2, max_sources=3
    )

    # 应该是 2 hit + 2 邻居（同 source 只附一次）
    assert len(result) == 4
    # 邻居必须是非命中的 claims
    hit_ids = {c.id for c in hit}
    neighbor_ids = {result[2].id, result[3].id}
    assert neighbor_ids.isdisjoint(hit_ids)


# ── 3. 多 source ───────────────────────────────────────────────


def test_multiple_sources_each_gets_neighbors(db_session):
    src1 = _make_source(db_session, "S1")
    src2 = _make_source(db_session, "S2")
    base = datetime.now(timezone.utc) - timedelta(hours=5)

    s1_claims = [
        _make_claim(db_session, src1, f"s1-{i}", created_at=base + timedelta(hours=i))
        for i in range(3)
    ]
    s2_claims = [
        _make_claim(db_session, src2, f"s2-{i}", created_at=base + timedelta(hours=i))
        for i in range(3)
    ]
    db_session.commit()

    # 各命中一条
    hit = [s1_claims[0], s2_claims[0]]
    result = attach_parent_neighbors(
        db_session, hit, neighbors_per_source=2, max_sources=3
    )

    # 2 hit + 2 邻居/source × 2 sources = 6
    assert len(result) == 6


def test_max_sources_limits_expansion(db_session):
    """命中 3 个 source 但 max_sources=1 → 只展开第一个."""
    sources = [_make_source(db_session, f"S{i}") for i in range(3)]
    base = datetime.now(timezone.utc) - timedelta(hours=5)
    hits = []
    for src in sources:
        cs = [
            _make_claim(db_session, src, f"{src.title}-{j}", created_at=base + timedelta(hours=j))
            for j in range(3)
        ]
        hits.append(cs[0])
    db_session.commit()

    result = attach_parent_neighbors(
        db_session, hits, neighbors_per_source=2, max_sources=1
    )
    # 3 hit + 2 邻居（仅第一个 source 展开）= 5
    assert len(result) == 5


# ── 4. 边界场景 ──────────────────────────────────────────────


def test_no_other_claims_in_source(db_session):
    """source 只有命中那一条 claim → 邻居 0."""
    src = _make_source(db_session)
    base = datetime.now(timezone.utc)
    only = _make_claim(db_session, src, "only", created_at=base)
    db_session.commit()

    result = attach_parent_neighbors(
        db_session, [only], neighbors_per_source=2, max_sources=3
    )
    assert len(result) == 1
    assert result[0].id == only.id


def test_hit_claim_with_no_source_link(db_session):
    """孤儿 claim（无 source link）→ 不崩，原样返回."""
    base = datetime.now(timezone.utc)
    orphan = ClaimCard(
        id=str(ULID()),
        summary="orphan",
        full_quote="orphan",
        claim_type="asset_view",
        stance="neutral",
        time_horizon="short",
        asset_scope=[],
        theme_scope=[],
        geographic_scope=[],
        conditions=[],
        invalidation_conditions=[],
        reasoning_basis=[],
        confidence=0.7,
        source_trust_weight=0.5,
        is_contradicted=False,
        status="active",
        created_at=base,
        updated_at=base,
    )
    db_session.add(orphan)
    db_session.commit()

    result = attach_parent_neighbors(
        db_session, [orphan], neighbors_per_source=2, max_sources=3
    )
    assert result == [orphan]


def test_disabled_short_circuits_zero_neighbors(db_session):
    """neighbors_per_source=0 → 短路返回原列表."""
    src = _make_source(db_session)
    c = _make_claim(db_session, src, "c", created_at=datetime.now(timezone.utc))
    db_session.commit()

    result = attach_parent_neighbors(
        db_session, [c], neighbors_per_source=0, max_sources=3
    )
    assert len(result) == 1


def test_disabled_short_circuits_zero_sources(db_session):
    """max_sources=0 → 短路返回原列表."""
    src = _make_source(db_session)
    c = _make_claim(db_session, src, "c", created_at=datetime.now(timezone.utc))
    db_session.commit()

    result = attach_parent_neighbors(
        db_session, [c], neighbors_per_source=2, max_sources=0
    )
    assert len(result) == 1


def test_empty_hit_list(db_session):
    result = attach_parent_neighbors(db_session, [], neighbors_per_source=2)
    assert result == []


# ── 5. 状态过滤 ────────────────────────────────────────────────


def test_status_filter_excludes_expired_neighbors(db_session):
    src = _make_source(db_session)
    base = datetime.now(timezone.utc) - timedelta(hours=5)
    active1 = _make_claim(db_session, src, "active 1", created_at=base, status="active")
    active2 = _make_claim(db_session, src, "active 2", created_at=base + timedelta(hours=1), status="active")
    expired = _make_claim(db_session, src, "expired", created_at=base + timedelta(hours=2), status="expired")
    db_session.commit()

    # 命中 active1，邻居应只取 active2（不取 expired）
    result = attach_parent_neighbors(
        db_session, [active1], neighbors_per_source=5, max_sources=3
    )
    neighbor_ids = {c.id for c in result if c.id != active1.id}
    assert active2.id in neighbor_ids
    assert expired.id not in neighbor_ids


# ── 6. 时间邻近性：取离锚点最近的 N 条 ──────────────────────────


def test_neighbors_picked_by_time_proximity(db_session):
    """命中点在时间轴中间 → 应优先取前后邻近的，而不是最远的."""
    src = _make_source(db_session)
    base = datetime.now(timezone.utc)
    claims = [
        _make_claim(db_session, src, f"c{i}", created_at=base + timedelta(hours=i))
        for i in range(10)
    ]
    db_session.commit()

    # 命中 c5
    result = attach_parent_neighbors(
        db_session, [claims[5]], neighbors_per_source=2, max_sources=1
    )
    assert len(result) == 3
    neighbor_ids = {c.id for c in result if c.id != claims[5].id}
    # 应是 c4 和 c6（距 c5 各 1 小时）
    assert claims[4].id in neighbor_ids
    assert claims[6].id in neighbor_ids
    # 而不是 c0 / c9（距 5 / 4 小时）
    assert claims[0].id not in neighbor_ids
    assert claims[9].id not in neighbor_ids
