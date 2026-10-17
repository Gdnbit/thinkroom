"""G5 数据源质量监控聚合 + API 端点测试."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from ulid import ULID

from app.database import Base
from app.schemas.market_snapshot import SourceHealthLog
from app.services import quality_monitor as qm


# ── Fixtures ──────────────────────────────────────────────────


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


def _add_log(
    db,
    *,
    source: str = "binance",
    symbol: str | None = "BTC",
    success: bool = True,
    latency_ms: float | None = 200.0,
    error: str | None = None,
    when: datetime | None = None,
    http_status: int | None = 200,
):
    log = SourceHealthLog(
        id=str(ULID()),
        source_name=source,
        symbol=symbol,
        checked_at=when or datetime.now(timezone.utc),
        success=success,
        latency_ms=latency_ms,
        http_status=http_status if success else None,
        error_message=error,
        meta={},
    )
    db.add(log)
    db.flush()
    return log


# ── 1. summary ─────────────────────────────────────────────────


def test_summary_empty_returns_null_metrics(db_session):
    s = qm.get_quality_summary(db_session, since_days=7)
    assert s["total_calls"] == 0
    assert s["success_rate_pct"] is None
    assert s["latency_p95_ms"] is None


def test_summary_basic_aggregation(db_session):
    for _ in range(8):
        _add_log(db_session, success=True, latency_ms=100)
    for _ in range(2):
        _add_log(db_session, success=False, latency_ms=500, error="timeout")
    db_session.commit()

    s = qm.get_quality_summary(db_session, since_days=7)
    assert s["total_calls"] == 10
    assert s["success_count"] == 8
    assert s["failure_count"] == 2
    assert s["success_rate_pct"] == 80.0
    # 平均延迟应在合理范围
    assert s["latency_avg_ms"] == 180.0


def test_summary_excludes_old_logs(db_session):
    """超出窗口的日志应被排除."""
    old = datetime.now(timezone.utc) - timedelta(days=30)
    _add_log(db_session, when=old)
    _add_log(db_session)  # 现在
    db_session.commit()

    s = qm.get_quality_summary(db_session, since_days=7)
    assert s["total_calls"] == 1


# ── 2. by_source ───────────────────────────────────────────────


def test_by_source_groups_correctly(db_session):
    for _ in range(5):
        _add_log(db_session, source="binance", success=True)
    for _ in range(3):
        _add_log(db_session, source="yahoo", success=True)
    _add_log(db_session, source="yahoo", success=False, error="timeout")
    db_session.commit()

    out = qm.get_quality_by_source(db_session, since_days=7)
    by_name = {row["source_name"]: row for row in out}
    assert by_name["binance"]["total_calls"] == 5
    assert by_name["binance"]["success_rate_pct"] == 100.0
    assert by_name["yahoo"]["total_calls"] == 4
    assert by_name["yahoo"]["success_rate_pct"] == 75.0
    # 应按 total_calls 倒序
    assert out[0]["source_name"] == "binance"


def test_by_source_last_error_message_only_when_failed(db_session):
    """最后一条 ok 时不返回 error；最后一条 fail 时返回 error."""
    # 最后一条是失败
    _add_log(db_session, source="binance", success=True)
    _add_log(db_session, source="binance", success=False, error="connection refused")
    db_session.commit()

    out = qm.get_quality_by_source(db_session, since_days=7)
    assert out[0]["last_success"] is False
    assert "connection refused" in (out[0]["last_error_message"] or "")


# ── 3. by_symbol ───────────────────────────────────────────────


def test_by_symbol_prioritizes_failures(db_session):
    """失败率最高的应排在前面."""
    for _ in range(10):
        _add_log(db_session, symbol="BTC", success=True)
    for _ in range(5):
        _add_log(db_session, symbol="HSI", success=False, error="empty response")
    for _ in range(3):
        _add_log(db_session, symbol="HSI", success=True)
    db_session.commit()

    out = qm.get_quality_by_symbol(db_session, since_days=7)
    # HSI 失败率高 → 排第一
    assert out[0]["symbol"] == "HSI"
    assert out[0]["success_rate_pct"] == 37.5  # 3/8


def test_by_symbol_excludes_null_symbol(db_session):
    _add_log(db_session, symbol=None)
    _add_log(db_session, symbol="BTC")
    db_session.commit()

    out = qm.get_quality_by_symbol(db_session, since_days=7)
    assert all(s["symbol"] is not None for s in out)


# ── 4. timeseries ──────────────────────────────────────────────


def test_timeseries_buckets_by_hours(db_session):
    base = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    # 桶 1：3 条
    for _ in range(3):
        _add_log(db_session, when=base - timedelta(hours=1))
    # 桶 2 (4 小时前)：2 条
    for _ in range(2):
        _add_log(db_session, when=base - timedelta(hours=5))
    db_session.commit()

    out = qm.get_quality_timeseries(db_session, since_days=7, bucket_hours=4)
    # 应有至少 2 个桶
    assert len(out) >= 2
    total = sum(b["total_calls"] for b in out)
    assert total == 5


def test_timeseries_empty(db_session):
    assert qm.get_quality_timeseries(db_session, since_days=7) == []


# ── 5. recent errors ───────────────────────────────────────────


def test_recent_errors_descending(db_session):
    base = datetime.now(timezone.utc)
    _add_log(db_session, when=base - timedelta(hours=2), success=False, error="old")
    _add_log(db_session, when=base, success=False, error="new")
    db_session.commit()

    out = qm.get_recent_errors(db_session, limit=10)
    assert out[0]["error_message"] == "new"
    assert out[1]["error_message"] == "old"


def test_recent_errors_excludes_successes(db_session):
    _add_log(db_session, success=True)
    _add_log(db_session, success=False, error="x")
    db_session.commit()

    out = qm.get_recent_errors(db_session)
    assert len(out) == 1


# ── 6. error categories ────────────────────────────────────────


def test_error_categories_classifies_known_errors(db_session):
    _add_log(db_session, success=False, error="Connection timed out after 30s")
    _add_log(db_session, success=False, error="ConnectionRefusedError: [Errno 111]")
    _add_log(db_session, success=False, error="429 Too Many Requests")
    _add_log(db_session, success=False, error="Internal Server Error 502")
    _add_log(db_session, success=False, error="empty response")
    db_session.commit()

    out = qm.get_error_categories(db_session, since_days=7)
    cats = {row["category"]: row for row in out}
    assert "timeout" in cats
    assert "connection" in cats
    assert "rate_limit" in cats
    assert "5xx" in cats
    assert "empty_response" in cats
    # share_pct 总和 = 100
    assert sum(row["share_pct"] for row in out) == pytest.approx(100.0, abs=0.5)


def test_error_categories_excludes_successes(db_session):
    _add_log(db_session, success=True)
    db_session.commit()
    assert qm.get_error_categories(db_session, since_days=7) == []


# ── 7. percentile helper ───────────────────────────────────────


def test_percentile_basic():
    assert qm._percentile([1, 2, 3, 4, 5], 50) == pytest.approx(3.0)
    assert qm._percentile([1, 2, 3, 4, 5], 100) == 5.0
    assert qm._percentile([1, 2, 3, 4, 5], 0) == 1.0


def test_percentile_empty_returns_none():
    assert qm._percentile([], 50) is None


def test_percentile_single_value():
    assert qm._percentile([42.0], 95) == 42.0


# ── 8. API 端点 ────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    """构造 FastAPI test client（绕过 API key 中间件 + 确保表已建）."""
    monkeypatch.setattr("app.config.settings.APP_API_KEY", "")
    # 确保测试 db 已有表（fresh DATABASE_URL 时表不存在）
    from app.database import engine
    from app.database import Base
    Base.metadata.create_all(engine)
    from app.main import app
    return TestClient(app)


def test_endpoint_summary_returns_schema(client):
    resp = client.get("/api/v1/quality-monitor/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert "total_calls" in body
    assert "since_days" in body


def test_endpoint_by_source_returns_list(client):
    resp = client.get("/api/v1/quality-monitor/by-source?since_days=7")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_endpoint_validates_since_days_range(client):
    resp = client.get("/api/v1/quality-monitor/summary?since_days=999")
    assert resp.status_code == 422
    resp = client.get("/api/v1/quality-monitor/summary?since_days=0")
    assert resp.status_code == 422


def test_endpoint_timeseries_validates_bucket_hours(client):
    resp = client.get("/api/v1/quality-monitor/timeseries?bucket_hours=999")
    assert resp.status_code == 422
