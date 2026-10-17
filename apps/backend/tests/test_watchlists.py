"""
测试 /api/v1/watchlists
"""


def test_list_watchlists_empty(client):
    """空数据库应返回空列表"""
    resp = client.get("/api/v1/watchlists")
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_watchlist(client):
    """创建观察列表"""
    payload = {
        "name": "加密资产观察",
        "list_type": "asset",
        "description": "跟踪主要加密资产",
    }
    resp = client.post("/api/v1/watchlists", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "id" in data
    assert data["name"] == "加密资产观察"
    assert data["list_type"] == "asset"
    assert data["status"] == "active"


def test_create_watchlist_required_fields(client):
    """缺少必填字段应返回 422"""
    resp = client.post("/api/v1/watchlists", json={"description": "no name"})
    assert resp.status_code == 422


def test_list_watchlists_after_create(client):
    """创建后列表应有数据"""
    client.post("/api/v1/watchlists", json={"name": "WL1", "list_type": "asset"})
    client.post("/api/v1/watchlists", json={"name": "WL2", "list_type": "theme"})
    resp = client.get("/api/v1/watchlists")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_get_watchlist_by_id(client):
    """按 ID 获取观察列表"""
    create_resp = client.post(
        "/api/v1/watchlists",
        json={"name": "Test WL", "list_type": "speaker"}
    )
    wl_id = create_resp.json()["id"]
    resp = client.get(f"/api/v1/watchlists/{wl_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == wl_id


def test_get_watchlist_not_found(client):
    """不存在的 ID 应返回 404"""
    resp = client.get("/api/v1/watchlists/nonexistent_id")
    assert resp.status_code == 404


def test_delete_watchlist(client):
    """删除观察列表"""
    create_resp = client.post(
        "/api/v1/watchlists",
        json={"name": "To Delete", "list_type": "asset"}
    )
    wl_id = create_resp.json()["id"]

    del_resp = client.delete(f"/api/v1/watchlists/{wl_id}")
    assert del_resp.status_code in (200, 204)

    # 确认已删除
    get_resp = client.get(f"/api/v1/watchlists/{wl_id}")
    assert get_resp.status_code == 404


def test_watchlist_types(client):
    """四种列表类型都应支持"""
    for list_type in ["asset", "theme", "speaker", "strategy"]:
        resp = client.post(
            "/api/v1/watchlists",
            json={"name": f"WL {list_type}", "list_type": list_type}
        )
        assert resp.status_code == 200
        assert resp.json()["list_type"] == list_type
