"""脏数据修复工具单测."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from ulid import ULID


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("app.config.settings.APP_API_KEY", "")
    from app.database import engine, Base as MainBase
    MainBase.metadata.drop_all(engine)
    MainBase.metadata.create_all(engine)
    from app.main import app
    return TestClient(app)


@pytest.fixture
def db_with_data(client):
    """创建一组完整脏数据：1 source + 3 claim + links."""
    from app.database import SessionLocal
    from app.schemas.raw_source import RawSource
    from app.schemas.claim_card import ClaimCard, ClaimRawSourceLink

    db = SessionLocal()

    src = RawSource(
        id=str(ULID()), source_type="article", title="test source",
        raw_text="x", language="zh", status="completed",
    )
    db.add(src)

    claim_ids = []
    for i in range(3):
        c = ClaimCard(
            id=str(ULID()), summary=f"claim {i}", stance="neutral",
            claim_type="market_narrative", confidence=0.7,
            asset_scope=[], theme_scope=[], geographic_scope=[],
            conditions=[], invalidation_conditions=[], reasoning_basis=[],
            time_sensitive=False, status="active",
        )
        db.add(c)
        claim_ids.append(c.id)

    db.flush()

    for cid in claim_ids:
        db.add(ClaimRawSourceLink(claim_id=cid, source_id=src.id, link_type="extracted_from"))

    db.commit()
    yield {"source_id": src.id, "claim_ids": claim_ids}
    db.close()


# ── preview ─────────────────────────────────────────────


def test_preview_cascade(client, db_with_data):
    sid = db_with_data["source_id"]
    resp = client.post(f"/api/v1/data-repair/preview-source-cascade/{sid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["claim_count"] == 3
    assert body["link_count"] == 3
    assert body["source_status"] == "completed"


def test_preview_unknown_source(client):
    resp = client.post("/api/v1/data-repair/preview-source-cascade/01H0XBOGUS00000000000000000")
    # 不存在的 source 返回 0 计数（不抛 404 - 让 caller 自行判断）
    assert resp.status_code == 200
    assert resp.json()["claim_count"] == 0


# ── archive_source ──────────────────────────────────────


def test_archive_source_archives_claims(client, db_with_data):
    sid = db_with_data["source_id"]
    resp = client.post(f"/api/v1/data-repair/archive-source/{sid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["affected_sources"] == 1
    assert body["affected_claims"] == 3

    # source 与 claim 应该都变 archived
    from app.database import SessionLocal
    from app.schemas.raw_source import RawSource
    from app.schemas.claim_card import ClaimCard
    db = SessionLocal()
    src = db.query(RawSource).filter(RawSource.id == sid).first()
    assert src.status == "archived"
    archived_claims = db.query(ClaimCard).filter(
        ClaimCard.id.in_(db_with_data["claim_ids"]),
        ClaimCard.status == "archived",
    ).count()
    assert archived_claims == 3
    db.close()


def test_archive_unknown_source_404(client):
    resp = client.post("/api/v1/data-repair/archive-source/01H0XBOGUS00000000000000000")
    assert resp.status_code == 404


# ── delete_source ───────────────────────────────────────


def test_delete_source_no_cascade_keeps_claims(client, db_with_data):
    sid = db_with_data["source_id"]
    resp = client.post(f"/api/v1/data-repair/delete-source/{sid}")
    assert resp.status_code == 200

    from app.database import SessionLocal
    from app.schemas.raw_source import RawSource
    from app.schemas.claim_card import ClaimCard
    db = SessionLocal()
    assert db.query(RawSource).filter(RawSource.id == sid).first() is None
    # claim 仍存在
    surviving = db.query(ClaimCard).filter(
        ClaimCard.id.in_(db_with_data["claim_ids"])
    ).count()
    assert surviving == 3
    db.close()


def test_delete_source_with_cascade_removes_all(client, db_with_data):
    sid = db_with_data["source_id"]
    resp = client.post(f"/api/v1/data-repair/delete-source/{sid}?cascade=true")
    assert resp.status_code == 200
    body = resp.json()
    assert body["affected_claims"] == 3
    assert body["affected_links"] == 3

    from app.database import SessionLocal
    from app.schemas.raw_source import RawSource
    from app.schemas.claim_card import ClaimCard
    db = SessionLocal()
    assert db.query(RawSource).filter(RawSource.id == sid).first() is None
    assert db.query(ClaimCard).filter(
        ClaimCard.id.in_(db_with_data["claim_ids"])
    ).count() == 0
    db.close()


# ── archive_claim ───────────────────────────────────────


def test_archive_single_claim(client, db_with_data):
    cid = db_with_data["claim_ids"][0]
    resp = client.post(f"/api/v1/data-repair/archive-claim/{cid}")
    assert resp.status_code == 200
    assert resp.json()["affected_claims"] == 1


def test_archive_already_archived_claim_is_noop(client, db_with_data):
    cid = db_with_data["claim_ids"][0]
    client.post(f"/api/v1/data-repair/archive-claim/{cid}")  # first
    resp = client.post(f"/api/v1/data-repair/archive-claim/{cid}")  # second
    assert resp.status_code == 200
    assert resp.json()["affected_claims"] == 0
    assert "already archived" in resp.json()["notes"]


# ── purge_archived_claims ──────────────────────────────


def test_purge_dry_run_does_not_delete(client, db_with_data):
    # 先 archive 1 条 + 设置 updated_at 50 天前
    from app.database import SessionLocal
    from app.schemas.claim_card import ClaimCard
    db = SessionLocal()
    c = db.query(ClaimCard).filter(ClaimCard.id == db_with_data["claim_ids"][0]).first()
    c.status = "archived"
    c.updated_at = datetime.now(timezone.utc) - timedelta(days=50)
    db.commit()
    db.close()

    resp = client.post(
        "/api/v1/data-repair/purge-archived-claims",
        json={"older_than_days": 30, "dry_run": True}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["affected_claims"] == 1
    assert "dry_run=true" in body["notes"]

    # 实际 claim 仍存在
    from app.database import SessionLocal
    db = SessionLocal()
    assert db.query(ClaimCard).filter(
        ClaimCard.id == db_with_data["claim_ids"][0]
    ).first() is not None
    db.close()


def test_purge_actual_delete(client, db_with_data):
    from app.database import SessionLocal
    from app.schemas.claim_card import ClaimCard
    db = SessionLocal()
    for cid in db_with_data["claim_ids"][:2]:
        c = db.query(ClaimCard).filter(ClaimCard.id == cid).first()
        c.status = "archived"
        c.updated_at = datetime.now(timezone.utc) - timedelta(days=100)
    db.commit()
    db.close()

    resp = client.post(
        "/api/v1/data-repair/purge-archived-claims",
        json={"older_than_days": 30, "dry_run": False}
    )
    assert resp.status_code == 200
    assert resp.json()["affected_claims"] == 2

    from app.database import SessionLocal
    db = SessionLocal()
    remaining = db.query(ClaimCard).filter(
        ClaimCard.id.in_(db_with_data["claim_ids"])
    ).count()
    assert remaining == 1  # 只剩第 3 条 active 的
    db.close()


# ── reset_failed_source ────────────────────────────────


def test_reset_failed_source(client, db_with_data):
    # 把 source status 改为 failed
    from app.database import SessionLocal
    from app.schemas.raw_source import RawSource
    db = SessionLocal()
    src = db.query(RawSource).filter(RawSource.id == db_with_data["source_id"]).first()
    src.status = "failed"
    src.processing_error = "old error"
    db.commit()
    db.close()

    resp = client.post(f"/api/v1/data-repair/reset-failed-source/{db_with_data['source_id']}")
    assert resp.status_code == 200
    assert resp.json()["affected_claims"] == 3  # 3 个 claim 被 archived

    from app.database import SessionLocal
    db = SessionLocal()
    src2 = db.query(RawSource).filter(RawSource.id == db_with_data["source_id"]).first()
    assert src2.status == "pending"
    assert src2.processing_error is None
    db.close()


def test_reset_unknown_source_404(client):
    resp = client.post("/api/v1/data-repair/reset-failed-source/01H0XBOGUS00000000000000000")
    assert resp.status_code == 404


def test_reset_active_source_400(client, db_with_data):
    # source 是 completed 状态，可以 reset；改为 active（错误状态）应 400
    from app.database import SessionLocal
    from app.schemas.raw_source import RawSource
    db = SessionLocal()
    src = db.query(RawSource).filter(RawSource.id == db_with_data["source_id"]).first()
    src.status = "processing"  # 不允许 reset 的状态
    db.commit()
    db.close()

    resp = client.post(f"/api/v1/data-repair/reset-failed-source/{db_with_data['source_id']}")
    assert resp.status_code == 400


# ── stats ───────────────────────────────────────────────


def test_stats_endpoint(client, db_with_data):
    resp = client.get("/api/v1/data-repair/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["raw_sources"]["total"] == 1
    assert body["claim_cards"]["total"] == 3
    assert "completed" in body["raw_sources"]["by_status"]
    assert "active" in body["claim_cards"]["by_status"]
