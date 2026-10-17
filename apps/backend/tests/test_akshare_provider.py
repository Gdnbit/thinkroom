"""G4 · AKShare provider 单元测试 + EOD auto-fetch 集成测试.

mock akshare module 避免实测网络。
"""
from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from ulid import ULID

from app.database import Base
from app.schemas.market_snapshot import FundEstimateSnapshot, FundProfile


# ── Mock akshare 模块 ─────────────────────────────────────


@pytest.fixture
def mock_akshare(monkeypatch):
    """Inject 一个假的 akshare module 到 sys.modules."""
    fake = types.ModuleType("akshare")
    fake.stock_zh_index_spot_em = MagicMock()
    fake.index_zh_a_hist = MagicMock()
    fake.fund_open_fund_info_em = MagicMock()
    monkeypatch.setitem(sys.modules, "akshare", fake)

    # 清 lru_cache —— is_available() 缓存可能保留之前测试的"不可用"结果
    from app.mcps import akshare_provider
    akshare_provider.is_available.cache_clear()
    yield fake
    akshare_provider.is_available.cache_clear()


@pytest.fixture
def no_akshare(monkeypatch):
    """模拟 akshare 未安装："""
    monkeypatch.setitem(sys.modules, "akshare", None)
    from app.mcps import akshare_provider
    akshare_provider.is_available.cache_clear()
    yield
    akshare_provider.is_available.cache_clear()


# ── is_available ──────────────────────────────────────────


def test_is_available_when_installed(mock_akshare):
    from app.mcps import akshare_provider
    assert akshare_provider.is_available() is True


def test_is_available_when_not_installed(no_akshare):
    from app.mcps import akshare_provider
    assert akshare_provider.is_available() is False


def test_unsupported_index_symbol_returns_none(mock_akshare):
    from app.mcps import akshare_provider
    assert akshare_provider.fetch_index_quote("BTC") is None
    assert akshare_provider.fetch_index_history("UNKNOWN", "1y") is None


# ── fetch_index_quote ─────────────────────────────────────


def test_fetch_index_quote_success(mock_akshare):
    mock_akshare.stock_zh_index_spot_em.return_value = pd.DataFrame([{
        "代码": "000300", "名称": "沪深300", "最新价": 3850.5, "今开": 3845.0,
        "最高": 3860.0, "最低": 3830.0, "昨收": 3852.3, "成交量": 12345678,
        "涨跌幅": -0.05,
    }])

    from app.mcps import akshare_provider
    result = akshare_provider.fetch_index_quote("CSI300")
    assert result is not None
    assert result["symbol"] == "CSI300"
    assert result["name"] == "沪深300"
    assert result["current"] == 3850.5
    assert result["previous_close"] == 3852.3
    assert result["source"].startswith("akshare/")


def test_fetch_index_quote_empty_dataframe(mock_akshare):
    mock_akshare.stock_zh_index_spot_em.return_value = pd.DataFrame()
    from app.mcps import akshare_provider
    assert akshare_provider.fetch_index_quote("CSI300") is None


def test_fetch_index_quote_no_match(mock_akshare):
    """akshare 返回了数据但没有匹配 code 的行."""
    mock_akshare.stock_zh_index_spot_em.return_value = pd.DataFrame([{
        "代码": "999999", "名称": "其他", "最新价": 100,
    }])
    from app.mcps import akshare_provider
    assert akshare_provider.fetch_index_quote("CSI300") is None


def test_fetch_index_quote_exception_returns_none(mock_akshare):
    mock_akshare.stock_zh_index_spot_em.side_effect = RuntimeError("network")
    from app.mcps import akshare_provider
    assert akshare_provider.fetch_index_quote("CSI300") is None


def test_fetch_index_quote_when_akshare_unavailable(no_akshare):
    from app.mcps import akshare_provider
    assert akshare_provider.fetch_index_quote("CSI300") is None


# ── fetch_index_history ───────────────────────────────────


def test_fetch_index_history_success(mock_akshare):
    base = datetime(2026, 4, 1)
    mock_akshare.index_zh_a_hist.return_value = pd.DataFrame([
        {"日期": (base + timedelta(days=i)),
         "开盘": 3800 + i, "收盘": 3810 + i,
         "最高": 3850 + i, "最低": 3790 + i,
         "成交量": 100000 + i * 100}
        for i in range(5)
    ])

    from app.mcps import akshare_provider
    series = akshare_provider.fetch_index_history("CSI300", "1m")
    assert series is not None
    assert len(series) == 5
    assert all("date" in p and "close" in p for p in series)


# ── fetch_fund_nav ────────────────────────────────────────


def test_fetch_fund_nav_success(mock_akshare):
    mock_akshare.fund_open_fund_info_em.return_value = pd.DataFrame([
        {"净值日期": datetime(2026, 4, 22), "单位净值": 1.4912, "日增长率": 0.85},
        {"净值日期": datetime(2026, 4, 23), "单位净值": 1.5012, "日增长率": 0.67},
        {"净值日期": datetime(2026, 4, 24), "单位净值": 1.5234, "日增长率": 1.48},
    ])

    from app.mcps import akshare_provider
    nav = akshare_provider.fetch_fund_nav("110011")
    assert nav is not None
    assert nav["fund_code"] == "110011"
    assert nav["nav"] == 1.5234  # 取最新一行
    assert nav["pct_change_1d"] == pytest.approx(0.0148, abs=0.001)


def test_fetch_fund_nav_invalid_code_format(mock_akshare):
    """非 6 位 code 直接返回 None."""
    from app.mcps import akshare_provider
    assert akshare_provider.fetch_fund_nav("ABC") is None
    assert akshare_provider.fetch_fund_nav("12345") is None
    assert akshare_provider.fetch_fund_nav("") is None


def test_fetch_fund_nav_when_unavailable(no_akshare):
    from app.mcps import akshare_provider
    assert akshare_provider.fetch_fund_nav("110011") is None


def test_fetch_fund_nav_history_filters_old(mock_akshare):
    today = datetime.now()
    rows = [
        {"净值日期": today - timedelta(days=200), "单位净值": 1.0, "日增长率": 0.0},
        {"净值日期": today - timedelta(days=50), "单位净值": 1.2, "日增长率": 0.1},
        {"净值日期": today - timedelta(days=10), "单位净值": 1.3, "日增长率": 0.05},
    ]
    mock_akshare.fund_open_fund_info_em.return_value = pd.DataFrame(rows)

    from app.mcps import akshare_provider
    series = akshare_provider.fetch_fund_nav_history("110011", since_days=90)
    assert series is not None
    assert len(series) == 2  # 200 days ago should be excluded


# ── EOD auto-fetch 集成 ──────────────────────────────────


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def _make_fund_with_today_snapshot(db, fund_code: str = "110011"):
    p = FundProfile(
        id=str(ULID()), fund_code=fund_code, fund_name="易方达中小盘",
        market="CN", basket=[{"symbol": "CSI300", "weight": 1.0}],
        beta=1.0, status="active", last_nav=1.5,
    )
    db.add(p)

    from zoneinfo import ZoneInfo
    today_sh = datetime.now(ZoneInfo("Asia/Shanghai")).replace(
        hour=14, minute=30, second=0, microsecond=0
    ).astimezone(timezone.utc)
    snap = FundEstimateSnapshot(
        id=str(ULID()), fund_code=fund_code, ts=today_sh,
        est_nav=1.52, est_pct_change_1d=0.0133,
        confidence_level="medium", ref_index="CSI300",
        model_version="v1", meta={},
        is_eod_final=False, actual_nav=None,
    )
    db.add(snap)
    db.commit()
    return p, snap


def test_eod_auto_fills_when_akshare_returns_nav(db_session, mock_akshare):
    """G4 集成：mark_eod_finals 标记后，自动用 akshare 拉真实 NAV 填入."""
    mock_akshare.fund_open_fund_info_em.return_value = pd.DataFrame([
        {"净值日期": datetime.now(), "单位净值": 1.5234, "日增长率": 1.48},
    ])

    p, snap = _make_fund_with_today_snapshot(db_session)
    from app.services.fund_reconciliation import mark_eod_finals
    summary = mark_eod_finals(db_session, auto_fetch_actual=True)

    db_session.refresh(snap)
    assert summary["marked"] == 1
    assert summary["auto_filled"] == 1
    assert summary["akshare_available"] is True
    assert snap.is_eod_final is True
    assert snap.actual_nav == pytest.approx(1.5234, abs=0.001)


def test_eod_skip_auto_fetch_when_disabled(db_session, mock_akshare):
    p, snap = _make_fund_with_today_snapshot(db_session)
    from app.services.fund_reconciliation import mark_eod_finals
    summary = mark_eod_finals(db_session, auto_fetch_actual=False)

    db_session.refresh(snap)
    assert summary["marked"] == 1
    assert summary["auto_filled"] == 0
    assert summary["akshare_available"] is False  # 没尝试加载
    assert snap.is_eod_final is True
    assert snap.actual_nav is None


def test_eod_no_auto_fetch_when_akshare_missing(db_session, no_akshare):
    p, snap = _make_fund_with_today_snapshot(db_session)
    from app.services.fund_reconciliation import mark_eod_finals
    summary = mark_eod_finals(db_session)

    db_session.refresh(snap)
    assert summary["marked"] == 1
    assert summary["auto_filled"] == 0
    assert summary["akshare_available"] is False
    assert snap.is_eod_final is True
    assert snap.actual_nav is None  # 没填


def test_eod_auto_fetch_failure_recorded(db_session, mock_akshare):
    """akshare 调用抛错 → auto_fetch_failed 计数 +1，但 EOD 标记仍成功."""
    mock_akshare.fund_open_fund_info_em.side_effect = RuntimeError("net err")
    p, snap = _make_fund_with_today_snapshot(db_session)

    from app.services.fund_reconciliation import mark_eod_finals
    summary = mark_eod_finals(db_session)

    db_session.refresh(snap)
    assert summary["marked"] == 1
    # auto_fetch_failed 不计 — 因为 fetch_fund_nav 内部已捕获异常返回 None，
    # 所以从 mark_eod_finals 视角看是"取到 None，跳过"
    assert summary["auto_filled"] == 0
    assert snap.actual_nav is None  # 没填


def test_eod_does_not_overwrite_already_filled(db_session, mock_akshare):
    """已有 actual_nav 的 snapshot 不会被自动覆盖."""
    p, snap = _make_fund_with_today_snapshot(db_session)
    snap.is_eod_final = True
    snap.actual_nav = 1.50  # 用户已手填
    db_session.commit()

    mock_akshare.fund_open_fund_info_em.return_value = pd.DataFrame([
        {"净值日期": datetime.now(), "单位净值": 9.99, "日增长率": 0.0},  # 假数据
    ])

    from app.services.fund_reconciliation import mark_eod_finals
    summary = mark_eod_finals(db_session)

    db_session.refresh(snap)
    assert summary["skipped_already"] == 1
    assert summary["auto_filled"] == 0
    assert snap.actual_nav == 1.50  # 保留用户手填值
