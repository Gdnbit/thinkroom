"""测试 /api/v1/lifecycle"""
from datetime import datetime, timezone, timedelta
from ulid import ULID


def _make_claim(db, status="active", valid_until_offset_days=None, is_contradicted=False):
    from app.schemas.claim_card import ClaimCard
    now = datetime.now(timezone.utc)
    if valid_until_offset_days is not None:
        vu = now + timedelta(days=valid_until_offset_days)
    else:
        vu = now + timedelta(days=90)
    claim = ClaimCard(
        id=str(ULID()), summary="测试观点",
        claim_type="asset_view", stance="bullish", time_horizon="medium",
        asset_scope=["BTC"], theme_scope=[],
        geographic_scope=[], conditions=[],
        invalidation_conditions=[], reasoning_basis=[],
        confidence=0.8, source_trust_weight=0.5,
        is_contradicted=is_contradicted,
        status=status, valid_until=vu,
        last_lifecycle_check=now, version=1,
        created_at=now, updated_at=now,
    )
    db.add(claim)
    db.commit()
    db.refresh(claim)
    return claim


def test_lifecycle_scan_no_claims(client):
    """无 claim 时 scan 应正常返回"""
    resp = client.post("/api/v1/lifecycle/scan")
    assert resp.status_code == 200
    data = resp.json()
    assert data["scanned"] == 0
    assert data["expired"] == 0
    assert data["updated"] == 0


def test_lifecycle_scan_active_claims_not_expired(client, db):
    """未过期的 claim 不应被标记"""
    _make_claim(db, valid_until_offset_days=30)
    resp = client.post("/api/v1/lifecycle/scan")
    assert resp.status_code == 200
    data = resp.json()
    assert data["scanned"] >= 1
    assert data["expired"] == 0
    assert data["updated"] == 0


def test_lifecycle_scan_expired_claim(client, db):
    """已过期的 claim 应被标记为 expired"""
    from app.schemas.claim_card import ClaimCard
    claim = _make_claim(db, valid_until_offset_days=-5)  # 5天前过期
    resp = client.post("/api/v1/lifecycle/scan")
    assert resp.status_code == 200
    data = resp.json()
    assert data["expired"] >= 1
    assert data["updated"] >= 1
    db.refresh(claim)
    assert claim.status == "expired"


def test_lifecycle_scan_dry_run(client, db):
    """dry_run 不应真正修改数据库"""
    from app.schemas.claim_card import ClaimCard
    claim = _make_claim(db, valid_until_offset_days=-5)
    resp = client.post("/api/v1/lifecycle/scan", params={"dry_run": "true"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["dry_run"] is True
    db.refresh(claim)
    assert claim.status == "active"  # 未被修改


def test_lifecycle_invalidate_claim(client, db):
    """手动失效 claim"""
    claim = _make_claim(db)
    payload = {"reason": "发现矛盾证据"}
    resp = client.post(f"/api/v1/lifecycle/invalidate/{claim.id}", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    db.refresh(claim)
    assert claim.status == "invalidated"
    assert claim.is_contradicted is True


def test_lifecycle_invalidate_nonexistent(client):
    """不存在的 claim 应返回 404"""
    resp = client.post("/api/v1/lifecycle/invalidate/nonexistent_id", json={"reason": "test"})
    assert resp.status_code == 404


def test_lifecycle_logs_for_claim(client, db):
    """失效操作后应有 lifecycle log"""
    claim = _make_claim(db)
    client.post(f"/api/v1/lifecycle/invalidate/{claim.id}", json={"reason": "矛盾"})
    resp = client.get(f"/api/v1/lifecycle/logs/{claim.id}")
    assert resp.status_code == 200
    logs = resp.json()
    assert isinstance(logs, list)
    assert len(logs) >= 1
    log = logs[0]
    assert log["claim_id"] == claim.id
    assert log["to_status"] == "invalidated"
    assert "reason" in log


def test_lifecycle_scan_writes_lifecycle_log(client, db):
    """scan 过期后应写入 lifecycle log"""
    claim = _make_claim(db, valid_until_offset_days=-5)
    client.post("/api/v1/lifecycle/scan")
    resp = client.get(f"/api/v1/lifecycle/logs/{claim.id}")
    assert resp.status_code == 200
    logs = resp.json()
    assert len(logs) >= 1
    assert logs[0]["to_status"] == "expired"
