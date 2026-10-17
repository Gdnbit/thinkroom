"""G2 FundProfile CRUD API 测试."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("app.config.settings.APP_API_KEY", "")
    from app.database import engine, Base as MainBase
    MainBase.metadata.drop_all(engine)
    MainBase.metadata.create_all(engine)
    from app.main import app
    return TestClient(app)


def _payload(**overrides):
    return {
        "fund_code": "110011",
        "fund_name": "易方达中小盘混合",
        "market": "CN",
        "last_nav": 1.5,
        "last_nav_date": "2026-04-25",
        "basket": [
            {"symbol": "CSI300", "weight": 0.6},
            {"symbol": "ZZ500", "weight": 0.3},
        ],
        "beta": 1.0,
        "status": "active",
        "notes": "示例",
        **overrides,
    }


# ── CRUD 流 ────────────────────────────────────────────────────


def test_create_then_list_then_get(client):
    resp = client.post("/api/v1/fund-profiles", json=_payload())
    assert resp.status_code == 201
    body = resp.json()
    assert body["fund_code"] == "110011"
    assert body["last_nav"] == 1.5
    assert len(body["basket"]) == 2

    r2 = client.get("/api/v1/fund-profiles")
    assert r2.status_code == 200
    assert len(r2.json()) == 1

    r3 = client.get("/api/v1/fund-profiles/110011")
    assert r3.status_code == 200
    assert r3.json()["fund_name"] == "易方达中小盘混合"


def test_create_duplicate_returns_409(client):
    client.post("/api/v1/fund-profiles", json=_payload())
    r2 = client.post("/api/v1/fund-profiles", json=_payload())
    assert r2.status_code == 409


def test_get_unknown_returns_404(client):
    resp = client.get("/api/v1/fund-profiles/UNKNOWN")
    assert resp.status_code == 404


def test_update_modifies_fields(client):
    client.post("/api/v1/fund-profiles", json=_payload())
    update = {
        "fund_name": "易方达中小盘混合（更名）",
        "market": "CN",
        "last_nav": 1.6,
        "last_nav_date": "2026-04-26",
        "basket": [{"symbol": "CSI300", "weight": 1.0}],
        "beta": 1.1,
        "status": "active",
        "notes": "updated",
    }
    r = client.put("/api/v1/fund-profiles/110011", json=update)
    assert r.status_code == 200
    body = r.json()
    assert body["fund_name"] == "易方达中小盘混合（更名）"
    assert body["last_nav"] == 1.6
    assert body["beta"] == 1.1
    assert len(body["basket"]) == 1


def test_update_unknown_returns_404(client):
    r = client.put("/api/v1/fund-profiles/UNKNOWN", json={
        "fund_name": "x", "market": "CN", "basket": [], "beta": 1.0, "status": "active"
    })
    assert r.status_code == 404


def test_delete_soft_archives(client):
    client.post("/api/v1/fund-profiles", json=_payload())
    r = client.delete("/api/v1/fund-profiles/110011")
    assert r.status_code == 204

    # 默认列表（只 active）应不包含
    r2 = client.get("/api/v1/fund-profiles")
    assert len(r2.json()) == 0

    # 显式查 archived 应返回
    r3 = client.get("/api/v1/fund-profiles?status=archived")
    assert len(r3.json()) == 1


# ── 校验 ──────────────────────────────────────────────────────


def test_basket_weight_sum_too_low_rejected(client):
    p = _payload(basket=[{"symbol": "CSI300", "weight": 0.1}])
    resp = client.post("/api/v1/fund-profiles", json=p)
    assert resp.status_code == 422
    body = resp.json()
    # 错误位置在 errors 字段里（exception_handler 统一格式）
    errors_text = str(body.get("errors", "")).lower()
    assert "basket" in errors_text or "权重" in str(body.get("errors", ""))


def test_basket_weight_sum_too_high_rejected(client):
    p = _payload(basket=[
        {"symbol": "CSI300", "weight": 0.9},
        {"symbol": "ZZ500", "weight": 0.9},
    ])
    resp = client.post("/api/v1/fund-profiles", json=p)
    assert resp.status_code == 422


def test_basket_weight_item_out_of_range(client):
    p = _payload(basket=[{"symbol": "CSI300", "weight": 1.5}])
    resp = client.post("/api/v1/fund-profiles", json=p)
    assert resp.status_code == 422


def test_invalid_status(client):
    p = _payload(status="weird")
    resp = client.post("/api/v1/fund-profiles", json=p)
    assert resp.status_code == 422


def test_invalid_beta_too_high(client):
    p = _payload(beta=10.0)
    resp = client.post("/api/v1/fund-profiles", json=p)
    assert resp.status_code == 422


def test_invalid_last_nav_zero(client):
    p = _payload(last_nav=0.0)
    resp = client.post("/api/v1/fund-profiles", json=p)
    assert resp.status_code == 422


def test_empty_basket_allowed(client):
    p = _payload(basket=[])
    resp = client.post("/api/v1/fund-profiles", json=p)
    assert resp.status_code == 201


def test_invalid_date_format(client):
    p = _payload(last_nav_date="2026/04/25")  # 非 ISO
    resp = client.post("/api/v1/fund-profiles", json=p)
    assert resp.status_code == 422


def test_list_by_status_filter(client):
    client.post("/api/v1/fund-profiles", json=_payload(fund_code="A", fund_name="A"))
    client.post("/api/v1/fund-profiles", json=_payload(fund_code="B", fund_name="B", status="archived"))

    r1 = client.get("/api/v1/fund-profiles?status=active")
    assert len(r1.json()) == 1
    assert r1.json()[0]["fund_code"] == "A"

    r2 = client.get("/api/v1/fund-profiles?status=archived")
    assert len(r2.json()) == 1
    assert r2.json()[0]["fund_code"] == "B"
