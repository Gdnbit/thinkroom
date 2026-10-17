"""G7 · auto_pnl_tracker 单元测试.

覆盖核心路径：
- _extract_price 容错多 key
- infer_horizon_days：claims 多数投票 / 默认值
- judge_direction：bullish/bearish/neutral 阈值边界
- auto_track_pnl 完整流程：too_early、no_entry、no_exit、成功 evaluate、dry_run、隔离 pending+null
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from ulid import ULID

from app.schemas.decision_snapshot import DecisionSnapshot
from app.schemas.market_snapshot import MarketSnapshot
from app.services.auto_pnl_tracker import (
    DEFAULT_HORIZON_DAYS,
    DIRECTION_THRESHOLD_PCT,
    _extract_price,
    auto_track_pnl,
    infer_horizon_days,
    judge_direction,
)


# ── 基础工具函数测试 ─────────────────────────────────────────────


def test_extract_price_priority_keys():
    """price > current_price > current > close > last_price."""
    assert _extract_price({"price": 100.0, "current_price": 999.0}) == 100.0
    assert _extract_price({"current_price": 200.0}) == 200.0
    assert _extract_price({"close": 300.0}) == 300.0
    assert _extract_price({"last_price": "400.5"}) == 400.5  # str 也能转


def test_extract_price_empty_or_invalid():
    """空 / None / 非数字 / 非正数都返回 None."""
    assert _extract_price(None) is None
    assert _extract_price({}) is None
    assert _extract_price({"price": None}) is None
    assert _extract_price({"price": "abc"}) is None
    assert _extract_price({"price": 0}) is None
    assert _extract_price({"price": -10}) is None


def test_judge_direction_thresholds():
    """边界：阈值内 neutral，超出按方向."""
    # 默认 ±2%
    assert judge_direction(100.0, 100.0)[0] == "neutral"
    assert judge_direction(100.0, 101.5)[0] == "neutral"  # +1.5% < 2
    assert judge_direction(100.0, 102.5)[0] == "bullish"  # +2.5%
    assert judge_direction(100.0, 98.5)[0] == "neutral"   # -1.5%
    assert judge_direction(100.0, 97.5)[0] == "bearish"   # -2.5%

    # PnL% 计算精度
    direction, pnl = judge_direction(100.0, 105.0)
    assert direction == "bullish"
    assert pnl == 5.0


def test_judge_direction_custom_threshold():
    """自定义阈值."""
    # threshold=5 时，+3% 仍算 neutral
    assert judge_direction(100.0, 103.0, threshold_pct=5.0)[0] == "neutral"
    assert judge_direction(100.0, 106.0, threshold_pct=5.0)[0] == "bullish"


def test_infer_horizon_days_majority_vote():
    """从 claims_snapshot 投票推断."""
    s = DecisionSnapshot(
        id=str(ULID()), trigger_type="briefing", trigger_id="x",
        asset="BTC", question="q",
        snapshot_at=datetime.now(timezone.utc),
        claims_snapshot=[
            {"id": "1", "time_horizon": "short"},
            {"id": "2", "time_horizon": "short"},
            {"id": "3", "time_horizon": "medium"},
        ],
    )
    assert infer_horizon_days(s) == 7  # short 多数


def test_infer_horizon_days_no_claims():
    """无 claims 用默认值."""
    s = DecisionSnapshot(
        id=str(ULID()), trigger_type="briefing", trigger_id="x",
        asset="BTC", question="q",
        snapshot_at=datetime.now(timezone.utc),
        claims_snapshot=[],
    )
    assert infer_horizon_days(s) == DEFAULT_HORIZON_DAYS


def test_infer_horizon_days_unknown_horizon():
    """time_horizon 是未知值时回退默认."""
    s = DecisionSnapshot(
        id=str(ULID()), trigger_type="briefing", trigger_id="x",
        asset="BTC", question="q",
        snapshot_at=datetime.now(timezone.utc),
        claims_snapshot=[{"id": "1", "time_horizon": "weird_value"}],
    )
    assert infer_horizon_days(s) == DEFAULT_HORIZON_DAYS


# ── auto_track_pnl 集成路径 ──────────────────────────────────────


def _make_snapshot(
    db, *, asset="BTC", days_ago=10, entry_price=100.0,
    horizon="short",  # → 7 天
    replay_status="pending", outcome_direction=None,
):
    """辅助：创建 DecisionSnapshot."""
    snapshot_at = datetime.now(timezone.utc) - timedelta(days=days_ago)
    s = DecisionSnapshot(
        id=str(ULID()),
        trigger_type="briefing",
        trigger_id=str(ULID()),
        asset=asset,
        question=f"{asset} 趋势研判",
        snapshot_at=snapshot_at,
        claims_snapshot=[{"id": str(ULID()), "time_horizon": horizon}],
        claims_count=1,
        market_snapshot={"price": entry_price, "rsi_14": 50.0},
        flow_snapshot={},
        events_snapshot=[],
        macro_snapshot={},
        signal_scorecard={},
        activated_seats=[],
        evidence_budget={},
        final_direction="bullish",
        final_conviction=0.7,
        final_action="watch",
        replay_status=replay_status,
        outcome_direction=outcome_direction,
    )
    db.add(s)
    db.flush()
    return s


def _make_market_snapshot(db, *, symbol, ts, price):
    """辅助：在 MarketSnapshot 表里写一条价格记录（供 exit_price 查询）."""
    ms = MarketSnapshot(
        id=str(ULID()),
        symbol=symbol.upper(),
        market="CRYPTO" if symbol.upper() in ("BTC", "ETH") else "US",
        ts=ts,
        price=price,
        source_name="test",
        is_fallback=False,
        quality_flag="normal",
    )
    db.add(ms)
    db.flush()
    return ms


def test_auto_track_pnl_evaluates_bullish(db):
    """涨幅 +5% → bullish + outcome 字段写入."""
    s = _make_snapshot(db, days_ago=10, entry_price=100.0, horizon="short")
    target_dt = s.snapshot_at + timedelta(days=7)
    _make_market_snapshot(db, symbol="BTC", ts=target_dt, price=105.0)
    db.commit()

    summary = auto_track_pnl(db)
    assert summary["evaluated"] == 1
    assert summary["skipped_too_early"] == 0
    assert summary["skipped_no_entry"] == 0
    assert summary["skipped_no_exit"] == 0

    db.refresh(s)
    assert s.outcome_direction == "bullish"
    assert s.outcome_pnl_pct == 5.0
    assert s.outcome_observed_at is not None
    assert "[Auto-PnL" in (s.replay_notes or "")
    # replay_status 不应被改变（让用户/LLM 还能继续做 replay 分析）
    assert s.replay_status == "pending"


def test_auto_track_pnl_evaluates_bearish(db):
    """跌幅 -8% → bearish."""
    s = _make_snapshot(db, days_ago=10, entry_price=200.0, horizon="short")
    target_dt = s.snapshot_at + timedelta(days=7)
    _make_market_snapshot(db, symbol="BTC", ts=target_dt, price=184.0)  # -8%
    db.commit()

    summary = auto_track_pnl(db)
    assert summary["evaluated"] == 1
    db.refresh(s)
    assert s.outcome_direction == "bearish"
    assert s.outcome_pnl_pct == -8.0


def test_auto_track_pnl_evaluates_neutral(db):
    """涨跌幅 ±2% 内 → neutral."""
    s = _make_snapshot(db, days_ago=10, entry_price=100.0, horizon="short")
    target_dt = s.snapshot_at + timedelta(days=7)
    _make_market_snapshot(db, symbol="BTC", ts=target_dt, price=101.0)  # +1%
    db.commit()

    summary = auto_track_pnl(db)
    assert summary["evaluated"] == 1
    db.refresh(s)
    assert s.outcome_direction == "neutral"
    assert s.outcome_pnl_pct == 1.0


def test_auto_track_pnl_skips_too_early(db):
    """horizon 末日还没到 → 跳过."""
    # snapshot 5 天前，horizon=short(7d)，target=2 天后 → 还没到
    s = _make_snapshot(db, days_ago=5, entry_price=100.0, horizon="short")
    db.commit()

    summary = auto_track_pnl(db)
    assert summary["evaluated"] == 0
    assert summary["skipped_too_early"] == 1
    db.refresh(s)
    assert s.outcome_direction is None


def test_auto_track_pnl_skips_no_entry_price(db):
    """market_snapshot 没 price → 跳过."""
    s = _make_snapshot(db, days_ago=10, horizon="short")
    s.market_snapshot = {"rsi_14": 60.0}  # 无 price
    db.flush()
    db.commit()

    summary = auto_track_pnl(db)
    assert summary["evaluated"] == 0
    assert summary["skipped_no_entry"] == 1


def test_auto_track_pnl_skips_no_exit_price(db):
    """MarketSnapshot 表没 horizon 末日附近的价格 → 跳过."""
    s = _make_snapshot(db, days_ago=10, entry_price=100.0, horizon="short")
    db.commit()  # 不创建任何 MarketSnapshot 记录

    summary = auto_track_pnl(db)
    assert summary["evaluated"] == 0
    assert summary["skipped_no_exit"] == 1


def test_auto_track_pnl_only_processes_pending_with_null_outcome(db):
    """已经标注过 outcome 的不再处理."""
    s = _make_snapshot(
        db, days_ago=10, entry_price=100.0,
        outcome_direction="bullish",  # 已经标过
    )
    target_dt = s.snapshot_at + timedelta(days=7)
    _make_market_snapshot(db, symbol="BTC", ts=target_dt, price=105.0)
    db.commit()

    summary = auto_track_pnl(db)
    assert summary["checked"] == 0
    assert summary["evaluated"] == 0


def test_auto_track_pnl_skips_replayed_status(db):
    """replay_status=replayed 的不处理."""
    s = _make_snapshot(
        db, days_ago=10, entry_price=100.0,
        replay_status="replayed",
    )
    target_dt = s.snapshot_at + timedelta(days=7)
    _make_market_snapshot(db, symbol="BTC", ts=target_dt, price=105.0)
    db.commit()

    summary = auto_track_pnl(db)
    assert summary["checked"] == 0


def test_auto_track_pnl_dry_run_does_not_write(db):
    """dry_run=True 不写入字段，但仍报告 evaluated."""
    s = _make_snapshot(db, days_ago=10, entry_price=100.0, horizon="short")
    target_dt = s.snapshot_at + timedelta(days=7)
    _make_market_snapshot(db, symbol="BTC", ts=target_dt, price=105.0)
    db.commit()

    summary = auto_track_pnl(db, dry_run=True)
    assert summary["evaluated"] == 1

    db.refresh(s)
    # 关键：dry_run 下字段保持 None
    assert s.outcome_direction is None
    assert s.outcome_pnl_pct is None
    assert s.outcome_observed_at is None


def test_auto_track_pnl_appends_to_existing_replay_notes(db):
    """如果 replay_notes 已存在内容，应 append 而不是覆盖."""
    s = _make_snapshot(db, days_ago=10, entry_price=100.0, horizon="short")
    s.replay_notes = "用户先前的复盘笔记"
    db.flush()
    target_dt = s.snapshot_at + timedelta(days=7)
    _make_market_snapshot(db, symbol="BTC", ts=target_dt, price=110.0)
    db.commit()

    auto_track_pnl(db)
    db.refresh(s)
    assert "用户先前的复盘笔记" in s.replay_notes
    assert "[Auto-PnL" in s.replay_notes


def test_auto_track_pnl_handles_multiple_assets(db):
    """多资产同时处理."""
    s1 = _make_snapshot(db, asset="BTC", days_ago=10, entry_price=100.0)
    s2 = _make_snapshot(db, asset="ETH", days_ago=10, entry_price=50.0)
    t1 = s1.snapshot_at + timedelta(days=7)
    t2 = s2.snapshot_at + timedelta(days=7)
    _make_market_snapshot(db, symbol="BTC", ts=t1, price=105.0)
    _make_market_snapshot(db, symbol="ETH", ts=t2, price=45.0)  # -10%
    db.commit()

    summary = auto_track_pnl(db)
    assert summary["evaluated"] == 2

    db.refresh(s1)
    db.refresh(s2)
    assert s1.outcome_direction == "bullish"
    assert s2.outcome_direction == "bearish"


# ── API endpoint ─────────────────────────────────────────────────


def test_endpoint_auto_track_pnl_dry_run(client, db):
    """POST /replay/auto-track-pnl?dry_run=true 报告但不写入."""
    s = _make_snapshot(db, days_ago=10, entry_price=100.0)
    t = s.snapshot_at + timedelta(days=7)
    _make_market_snapshot(db, symbol="BTC", ts=t, price=110.0)
    db.commit()

    resp = client.post("/api/v1/replay/auto-track-pnl?dry_run=true")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["summary"]["evaluated"] == 1
    assert data["summary"]["details"][0]["direction"] == "bullish"

    db.refresh(s)
    # dry_run 不写入
    assert s.outcome_direction is None


def test_endpoint_auto_track_pnl_writes(client, db):
    """POST /replay/auto-track-pnl 实际写入."""
    s = _make_snapshot(db, days_ago=10, entry_price=200.0)
    t = s.snapshot_at + timedelta(days=7)
    _make_market_snapshot(db, symbol="BTC", ts=t, price=180.0)  # -10%
    db.commit()

    resp = client.post("/api/v1/replay/auto-track-pnl")
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["evaluated"] == 1

    db.refresh(s)
    assert s.outcome_direction == "bearish"
    assert s.outcome_pnl_pct == -10.0


def test_endpoint_auto_track_pnl_empty_pending(client, db):
    """没有 pending snapshot 时不报错，返回 evaluated=0."""
    resp = client.post("/api/v1/replay/auto-track-pnl")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["summary"]["checked"] == 0
    assert data["summary"]["evaluated"] == 0
