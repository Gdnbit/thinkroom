"""
测试 /api/v1/events
"""


def test_list_events(client):
    """获取事件列表（P0 Mock 数据）"""
    resp = client.get("/api/v1/events")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) > 0  # Mock 数据应有内容


def test_events_have_required_fields(client):
    """每个事件应包含必要字段"""
    resp = client.get("/api/v1/events")
    assert resp.status_code == 200
    events = resp.json()
    for event in events:
        assert "id" in event
        assert "title" in event
        assert "importance" in event
        assert "asset_scope" in event
        assert "event_timestamp" in event


def test_events_filter_by_asset(client):
    """按资产过滤事件"""
    resp = client.get("/api/v1/events", params={"asset": "BTC"})
    assert resp.status_code == 200
    events = resp.json()
    for event in events:
        assert "BTC" in event["asset_scope"]


def test_events_filter_by_importance(client):
    """按重要性过滤事件"""
    resp = client.get("/api/v1/events", params={"importance_min": 0.85})
    assert resp.status_code == 200
    events = resp.json()
    for event in events:
        assert event["importance"] >= 0.85


def test_events_limit(client):
    """limit 参数生效"""
    resp = client.get("/api/v1/events", params={"limit": 2})
    assert resp.status_code == 200
    assert len(resp.json()) <= 2


def test_events_nonexistent_asset(client):
    """不存在的资产应返回空列表"""
    resp = client.get("/api/v1/events", params={"asset": "NONEXISTENT_XYZ"})
    assert resp.status_code == 200
    assert resp.json() == []
