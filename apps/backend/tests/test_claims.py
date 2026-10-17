"""
测试 /api/v1/claims
"""


def test_list_claims_empty(client):
    """空数据库应返回空列表"""
    resp = client.get("/api/v1/claims")
    assert resp.status_code == 200
    data = resp.json()
    assert "claims" in data
    assert data["claims"] == []
    assert data["total"] == 0


def test_list_claims_with_data(client, sample_claim):
    """有数据时应返回 Claim 列表"""
    resp = client.get("/api/v1/claims")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert len(data["claims"]) == 1
    claim = data["claims"][0]
    assert claim["summary"] == "BTC 处于强势上升趋势，看多"
    assert claim["stance"] == "bullish"
    assert claim["status"] == "active"


def test_search_claims_by_asset(client, sample_claim):
    """按资产过滤"""
    resp = client.get("/api/v1/claims", params={"asset": "BTC"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    resp2 = client.get("/api/v1/claims", params={"asset": "ETH"})
    assert resp2.status_code == 200
    assert resp2.json()["total"] == 0


def test_search_claims_by_status(client, sample_claim):
    """按状态过滤"""
    resp = client.get("/api/v1/claims", params={"status": "active"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    resp2 = client.get("/api/v1/claims", params={"status": "expired"})
    assert resp2.status_code == 200
    assert resp2.json()["total"] == 0


def test_search_claims_by_keyword(client, sample_claim):
    """关键词搜索"""
    resp = client.get("/api/v1/claims", params={"q": "BTC"})
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


def test_get_claim_by_id(client, sample_claim):
    """按 ID 获取单个 Claim"""
    resp = client.get(f"/api/v1/claims/{sample_claim.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == sample_claim.id
    assert data["claim_type"] == "asset_view"
    assert "BTC" in data["asset_scope"]


def test_get_claim_not_found(client):
    """不存在的 ID 应返回 404"""
    resp = client.get("/api/v1/claims/nonexistent_id_12345")
    assert resp.status_code == 404


def test_claims_pagination(client, db):
    """分页测试"""
    from app.schemas.claim_card import ClaimCard
    from ulid import ULID
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    for i in range(5):
        db.add(ClaimCard(
            id=str(ULID()),
            summary=f"测试观点 {i}",
            claim_type="macro_view",
            stance="neutral",
            time_horizon="medium",
            asset_scope=["BTC"],
            theme_scope=[],
            geographic_scope=[],
            conditions=[], invalidation_conditions=[],
            reasoning_basis=[],
            confidence=0.5,
            source_trust_weight=0.5,
            is_contradicted=False,
            status="active",
            version=1,
            created_at=now, updated_at=now,
        ))
    db.commit()

    resp = client.get("/api/v1/claims", params={"limit": 3, "offset": 0})
    assert resp.status_code == 200
    assert len(resp.json()["claims"]) == 3

    resp2 = client.get("/api/v1/claims", params={"limit": 3, "offset": 3})
    assert resp2.status_code == 200
    assert len(resp2.json()["claims"]) == 2
