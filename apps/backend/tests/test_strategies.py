"""
测试 /api/v1/strategies
"""
import pytest


def test_list_strategies_empty(client):
    """空数据库应返回空列表"""
    resp = client.get("/api/v1/strategies")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_strategies_with_data(client, sample_strategy):
    """有数据时应返回策略列表"""
    resp = client.get("/api/v1/strategies")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    s = data[0]
    assert s["strategy_name"] == "BTC 趋势跟踪策略"
    assert s["strategy_type"] == "trend_following"
    assert "entry_conditions" in s


def test_list_strategies_filter_by_asset(client, sample_strategy):
    """按资产过滤策略"""
    resp = client.get("/api/v1/strategies", params={"asset": "BTC"})
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    resp2 = client.get("/api/v1/strategies", params={"asset": "ETH"})
    assert resp2.status_code == 200
    assert len(resp2.json()) == 0


def test_list_strategies_filter_by_type(client, sample_strategy):
    """按类型过滤策略"""
    resp = client.get("/api/v1/strategies", params={"strategy_type": "trend_following"})
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    resp2 = client.get("/api/v1/strategies", params={"strategy_type": "mean_reversion"})
    assert resp2.status_code == 200
    assert len(resp2.json()) == 0


def test_get_strategy_by_id(client, sample_strategy):
    """按 ID 获取策略详情"""
    resp = client.get(f"/api/v1/strategies/{sample_strategy.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == sample_strategy.id
    assert data["core_logic"] is not None
    assert "entry_conditions" in data
    assert "disqualifying_conditions" in data
    assert "exit_rules" in data
    assert "risk_factors" in data


def test_get_strategy_not_found(client):
    """不存在的 ID 应返回 404"""
    resp = client.get("/api/v1/strategies/nonexistent_id")
    assert resp.status_code == 404


def test_distill_strategy_from_source(client, sample_raw_source):
    """从 RawSource 蒸馏策略"""
    resp = client.post(f"/api/v1/strategies/distill/{sample_raw_source.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "strategies_extracted" in data
    assert "job_id" in data
    assert "duration_ms" in data


def test_distill_strategy_nonexistent_source(client):
    """不存在的 source 应返回 400"""
    resp = client.post("/api/v1/strategies/distill/nonexistent_id")
    assert resp.status_code == 400


@pytest.mark.online  # LLM strategy-matching
def test_evaluate_strategy(client, sample_strategy):
    """评估策略匹配度，生成 SetupCard"""
    resp = client.post(
        f"/api/v1/strategies/{sample_strategy.id}/evaluate",
        json="BTC",
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "setup_id" in data
    assert "strategy_fit_score" in data
    assert "evidence_strength_score" in data
    assert "overall_score" in data
    assert "recommended_action" in data
    assert data["recommended_action"] in ("enter", "watch", "avoid")
    assert "matched_conditions" in data
    assert "unmatched_conditions" in data
    assert "source_mode" in data


@pytest.mark.online  # LLM strategy-matching
def test_evaluate_strategy_persists_setup_card(client, sample_strategy, db):
    """评估后应创建 SetupCard 记录"""
    from app.schemas.setup_card import SetupCard
    client.post(
        f"/api/v1/strategies/{sample_strategy.id}/evaluate",
        json="BTC",
    )
    setup = db.query(SetupCard).first()
    assert setup is not None
    assert setup.strategy_id == sample_strategy.id
    assert setup.asset == "BTC"
    assert setup.status == "active"
