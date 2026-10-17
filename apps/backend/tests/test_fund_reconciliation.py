"""G3 基金估值 EOD 对账 — service + API 单测.

覆盖：
- mark_eod_finals: 幂等性 / 多 fund / 当天无数据 / active filter
- fill_actual_nav: 校验 EOD final 才能填 / actual_nav>0 / 不存在
- get_pending_reconciliation: 列待填 / fund_code 过滤
- get_reconciliation_summary: MAE/RMSE/MAPE/bias 计算 / 无样本
- get_reconciliation_timeseries: 顺序 / error_pct 计算
- /api/v1/fund-reconciliation/* 端点
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from ulid import ULID

from app.database import Base
from app.schemas.market_snapshot import FundEstimateSnapshot, FundProfile
from app.services import fund_reconciliation as fr


# ── Fixtures ──────────────────────────────────────────────────────


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


def _add_profile(db, fund_code: str = "000001", status: str = "active") -> FundProfile:
    p = FundProfile(
        id=str(ULID()),
        fund_code=fund_code,
        fund_name=f"Mock fund {fund_code}",
        market="CN",
        last_nav=1.0,
        basket=[{"symbol": "CSI300", "weight": 1.0}],
        beta=1.0,
        status=status,
    )
    db.add(p)
    db.flush()
    return p


def _add_snapshot(
    db,
    fund_code: str = "000001",
    *,
    ts: datetime | None = None,
    est_nav: float = 1.05,
    is_eod_final: bool = False,
    actual_nav: float | None = None,
) -> FundEstimateSnapshot:
    s = FundEstimateSnapshot(
        id=str(ULID()),
        fund_code=fund_code,
        ts=ts or datetime.now(timezone.utc),
        est_nav=est_nav,
        est_pct_change_1d=0.05,
        confidence_level="medium",
        ref_index="CSI300",
        model_version="v1",
        meta={},
        is_eod_final=is_eod_final,
        actual_nav=actual_nav,
    )
    db.add(s)
    db.flush()
    return s


# ── 1. mark_eod_finals ────────────────────────────────────────────


def test_mark_eod_finals_marks_last_today(db_session):
    _add_profile(db_session)
    today_sh = datetime.now(ZoneInfo("Asia/Shanghai")).replace(
        hour=10, minute=0, second=0, microsecond=0
    ).astimezone(timezone.utc)
    s1 = _add_snapshot(db_session, ts=today_sh, est_nav=1.05)
    s2 = _add_snapshot(db_session, ts=today_sh + timedelta(hours=2), est_nav=1.06)
    s3 = _add_snapshot(db_session, ts=today_sh + timedelta(hours=4), est_nav=1.07)
    db_session.commit()

    result = fr.mark_eod_finals(db_session)
    assert result["funds_total"] == 1
    assert result["marked"] == 1

    db_session.refresh(s1)
    db_session.refresh(s2)
    db_session.refresh(s3)
    # 只有最晚的一条被标记
    assert s1.is_eod_final is False
    assert s2.is_eod_final is False
    assert s3.is_eod_final is True


def test_mark_eod_finals_idempotent(db_session):
    """已标记的 fund 重复跑 → skipped_already 计数+1."""
    _add_profile(db_session)
    ts = datetime.now(ZoneInfo("Asia/Shanghai")).replace(
        hour=15, minute=30, second=0, microsecond=0
    ).astimezone(timezone.utc)
    _add_snapshot(db_session, ts=ts, est_nav=1.05, is_eod_final=True)
    db_session.commit()

    result = fr.mark_eod_finals(db_session)
    assert result["marked"] == 0
    assert result["skipped_already"] == 1


def test_mark_eod_finals_multiple_funds(db_session):
    _add_profile(db_session, fund_code="A")
    _add_profile(db_session, fund_code="B")
    today_sh = datetime.now(ZoneInfo("Asia/Shanghai")).replace(
        hour=10, minute=0, second=0, microsecond=0
    ).astimezone(timezone.utc)
    _add_snapshot(db_session, fund_code="A", ts=today_sh)
    _add_snapshot(db_session, fund_code="B", ts=today_sh + timedelta(hours=1))
    db_session.commit()

    result = fr.mark_eod_finals(db_session)
    assert result["marked"] == 2


def test_mark_eod_finals_no_today_snapshot_skipped(db_session):
    _add_profile(db_session)
    yesterday = datetime.now(ZoneInfo("Asia/Shanghai")).replace(
        hour=10, minute=0, second=0, microsecond=0
    ).astimezone(timezone.utc) - timedelta(days=2)
    _add_snapshot(db_session, ts=yesterday, est_nav=1.05)
    db_session.commit()

    result = fr.mark_eod_finals(db_session)
    assert result["marked"] == 0
    assert result["skipped_no_data"] == 1


def test_mark_eod_finals_inactive_fund_excluded(db_session):
    _add_profile(db_session, fund_code="A", status="active")
    _add_profile(db_session, fund_code="B", status="archived")  # 不应处理
    today_sh = datetime.now(ZoneInfo("Asia/Shanghai")).replace(
        hour=12, minute=0, second=0, microsecond=0
    ).astimezone(timezone.utc)
    _add_snapshot(db_session, fund_code="A", ts=today_sh)
    _add_snapshot(db_session, fund_code="B", ts=today_sh)
    db_session.commit()

    result = fr.mark_eod_finals(db_session)
    assert result["funds_total"] == 1  # 仅 active
    assert result["marked"] == 1


# ── 2. fill_actual_nav ─────────────────────────────────────────────


def test_fill_actual_nav_updates_snapshot(db_session):
    _add_profile(db_session)
    s = _add_snapshot(db_session, est_nav=1.05, is_eod_final=True)
    db_session.commit()

    updated = fr.fill_actual_nav(db_session, s.id, 1.04)
    assert updated.actual_nav == 1.04


def test_fill_actual_nav_rejects_non_eod(db_session):
    """非 EOD final 的快照不允许填."""
    _add_profile(db_session)
    s = _add_snapshot(db_session, is_eod_final=False)
    db_session.commit()

    with pytest.raises(ValueError, match="not EOD final"):
        fr.fill_actual_nav(db_session, s.id, 1.04)


def test_fill_actual_nav_rejects_zero_value(db_session):
    _add_profile(db_session)
    s = _add_snapshot(db_session, is_eod_final=True)
    db_session.commit()

    with pytest.raises(ValueError, match="must be > 0"):
        fr.fill_actual_nav(db_session, s.id, 0.0)


def test_fill_actual_nav_rejects_unknown_id(db_session):
    with pytest.raises(ValueError, match="not found"):
        fr.fill_actual_nav(db_session, "01H0XBOGUS00000000000000000", 1.0)


# ── 3. get_pending_reconciliation ─────────────────────────────────


def test_pending_lists_only_unfilled_eod_finals(db_session):
    _add_profile(db_session)
    base = datetime.now(timezone.utc) - timedelta(days=1)
    _add_snapshot(db_session, ts=base, is_eod_final=True, actual_nav=None)
    _add_snapshot(db_session, ts=base + timedelta(hours=1), is_eod_final=True, actual_nav=1.05)  # 已对账
    _add_snapshot(db_session, ts=base + timedelta(hours=2), is_eod_final=False, actual_nav=None)  # 非 EOD
    db_session.commit()

    pending = fr.get_pending_reconciliation(db_session)
    assert len(pending) == 1


def test_pending_filters_by_fund_code(db_session):
    _add_profile(db_session, fund_code="A")
    _add_profile(db_session, fund_code="B")
    _add_snapshot(db_session, fund_code="A", is_eod_final=True)
    _add_snapshot(db_session, fund_code="B", is_eod_final=True)
    db_session.commit()

    pending_a = fr.get_pending_reconciliation(db_session, fund_code="A")
    assert len(pending_a) == 1
    assert pending_a[0]["fund_code"] == "A"


# ── 4. get_reconciliation_summary ─────────────────────────────────


def test_summary_calculates_mae_rmse_bias(db_session):
    _add_profile(db_session)
    base = datetime.now(timezone.utc) - timedelta(days=10)
    # est=1.05 / actual=1.00 → +5%
    _add_snapshot(db_session, ts=base, est_nav=1.05, actual_nav=1.00, is_eod_final=True)
    # est=1.02 / actual=1.00 → +2%
    _add_snapshot(db_session, ts=base + timedelta(days=1), est_nav=1.02, actual_nav=1.00, is_eod_final=True)
    # est=0.97 / actual=1.00 → -3%
    _add_snapshot(db_session, ts=base + timedelta(days=2), est_nav=0.97, actual_nav=1.00, is_eod_final=True)
    db_session.commit()

    summaries = fr.get_reconciliation_summary(db_session, since_days=30)
    assert len(summaries) == 1
    s = summaries[0]
    assert s.sample_count == 3
    # 误差 = [+0.05, +0.02, -0.03]
    # MAE = (0.05 + 0.02 + 0.03) / 3 = 0.0333 → 3.33%
    assert s.mae_pct == pytest.approx(3.3333, abs=0.01)
    # bias = (0.05 + 0.02 - 0.03) / 3 = 0.0133 → 1.33%
    assert s.bias_pct == pytest.approx(1.3333, abs=0.01)
    # max_error = 0.05 → 5%
    assert s.max_error_pct == pytest.approx(5.0, abs=0.01)
    # MAPE = MAE because errors are already pct
    assert s.mape_pct == s.mae_pct


def test_summary_no_samples(db_session):
    _add_profile(db_session)
    summaries = fr.get_reconciliation_summary(db_session, since_days=30)
    assert len(summaries) == 1
    s = summaries[0]
    assert s.sample_count == 0
    assert s.mae_pct is None


def test_summary_includes_pending_count(db_session):
    _add_profile(db_session)
    base = datetime.now(timezone.utc) - timedelta(days=2)
    # 1 个待填，2 个已填
    _add_snapshot(db_session, ts=base, est_nav=1.05, actual_nav=None, is_eod_final=True)
    _add_snapshot(db_session, ts=base + timedelta(hours=1), est_nav=1.02, actual_nav=1.00, is_eod_final=True)
    _add_snapshot(db_session, ts=base + timedelta(hours=2), est_nav=0.97, actual_nav=1.00, is_eod_final=True)
    db_session.commit()

    summaries = fr.get_reconciliation_summary(db_session, since_days=30)
    s = summaries[0]
    assert s.pending_count == 1
    assert s.sample_count == 2


def test_summary_filters_by_fund_code(db_session):
    _add_profile(db_session, fund_code="A")
    _add_profile(db_session, fund_code="B")
    _add_snapshot(db_session, fund_code="A", est_nav=1.05, actual_nav=1.00, is_eod_final=True)
    _add_snapshot(db_session, fund_code="B", est_nav=1.10, actual_nav=1.00, is_eod_final=True)
    db_session.commit()

    summaries = fr.get_reconciliation_summary(db_session, fund_code="A", since_days=30)
    assert len(summaries) == 1
    assert summaries[0].fund_code == "A"


# ── 5. get_reconciliation_timeseries ──────────────────────────────


def test_timeseries_returns_in_order(db_session):
    _add_profile(db_session)
    base = datetime.now(timezone.utc) - timedelta(days=3)
    _add_snapshot(db_session, ts=base, est_nav=1.05, actual_nav=1.00, is_eod_final=True)
    _add_snapshot(db_session, ts=base + timedelta(days=1), est_nav=1.03, actual_nav=1.00, is_eod_final=True)
    _add_snapshot(db_session, ts=base + timedelta(days=2), est_nav=0.98, actual_nav=1.00, is_eod_final=True)
    db_session.commit()

    series = fr.get_reconciliation_timeseries(db_session, "000001", since_days=30)
    assert len(series) == 3
    # 升序
    timestamps = [p["ts"] for p in series]
    assert timestamps == sorted(timestamps)
    # error_pct 计算正确
    assert series[0]["error_pct"] == pytest.approx(5.0, abs=0.01)
    assert series[2]["error_pct"] == pytest.approx(-2.0, abs=0.01)


# ── 6. API 端点 ───────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("app.config.settings.APP_API_KEY", "")
    # 强制刷新 schema：删旧表 + 按 ORM 重建（解决 dev.db 残留旧 schema 导致缺列）
    from app.database import engine, Base as MainBase
    MainBase.metadata.drop_all(engine)
    MainBase.metadata.create_all(engine)
    from app.main import app
    return TestClient(app)


def test_endpoint_pending_returns_list(client):
    resp = client.get("/api/v1/fund-reconciliation/pending")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_endpoint_summary_returns_list(client):
    resp = client.get("/api/v1/fund-reconciliation/summary?since_days=30")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_endpoint_timeseries_requires_fund_code(client):
    resp = client.get("/api/v1/fund-reconciliation/timeseries")
    assert resp.status_code == 422  # missing required param


def test_endpoint_run_eod_works(client):
    resp = client.post("/api/v1/fund-reconciliation/run-eod")
    assert resp.status_code == 200
    body = resp.json()
    assert "marked" in body


def test_endpoint_fill_actual_validates_positive(client):
    resp = client.post(
        "/api/v1/fund-reconciliation/01H0XBOGUS00000000000000000/actual",
        json={"actual_nav": -1.0},
    )
    assert resp.status_code == 422  # pydantic gt=0


def test_endpoint_fill_actual_404_on_missing(client):
    resp = client.post(
        "/api/v1/fund-reconciliation/01H0XBOGUS00000000000000000/actual",
        json={"actual_nav": 1.0},
    )
    assert resp.status_code == 400
    assert "not found" in resp.json().get("detail", "")
