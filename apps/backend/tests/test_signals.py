"""
测试 /api/v1/signals
"""


def test_get_market_state_btc(client):
    """获取 BTC 市场状态"""
    resp = client.get("/api/v1/signals/market/BTC")
    assert resp.status_code == 200
    data = resp.json()
    assert data is not None
    assert data["asset"] == "BTC"
    assert "price" in data
    assert "trend_direction" in data
    assert "rsi_14" in data


def test_get_market_state_spx(client):
    """获取 SPX 市场状态"""
    resp = client.get("/api/v1/signals/market/SPX")
    assert resp.status_code == 200
    data = resp.json()
    assert data["asset"] == "SPX"


def test_get_market_state_unknown_asset(client):
    """未知资产应返回空或 null"""
    resp = client.get("/api/v1/signals/market/UNKNOWN_ASSET")
    assert resp.status_code == 200
    # Mock 模式下未知资产返回 null/empty dict


def test_get_multi_market_state(client):
    """批量获取市场状态"""
    resp = client.get("/api/v1/signals/market", params={"assets": "BTC,SPX,GOLD"})
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assets_returned = [d["asset"] for d in data]
    assert "BTC" in assets_returned


def test_get_flow_signals_btc(client):
    """获取 BTC 资金流信号"""
    resp = client.get("/api/v1/signals/flow/BTC")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) > 0
    for signal in data:
        assert "signal_type" in signal
        assert "direction" in signal
        assert "description" in signal


def test_get_flow_signals_filter_by_type(client):
    """按信号类型过滤资金流"""
    resp = client.get("/api/v1/signals/flow/BTC", params={"signal_type": "funding_rate"})
    assert resp.status_code == 200
    data = resp.json()
    for signal in data:
        assert signal["signal_type"] == "funding_rate"


def test_get_flow_summary_btc(client):
    """获取 BTC 资金连续性摘要"""
    resp = client.get("/api/v1/signals/flow/BTC/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert "overall_direction" in data
    assert "signal_count" in data
    assert data["signal_count"] > 0


def test_get_flow_summary_unknown_asset(client):
    """未知资产的摘要应返回 neutral"""
    resp = client.get("/api/v1/signals/flow/UNKNOWN_XYZ/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("overall_direction") == "neutral"
    assert data.get("signal_count") == 0
