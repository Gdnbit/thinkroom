"""G7 · 决策快照自动 PnL 追踪

在 DecisionSnapshot 上自动计算 outcome：
- 扫所有 replay_status=pending 且 outcome_direction=None 的 snapshot
- 推断每条 snapshot 的 horizon（依据 claims_snapshot 里 time_horizon 的多数投票）
- 仅处理 snapshot_at + horizon 已经过去的（避免「事还没发生就评估」）
- entry_price 来自 snapshot.market_snapshot
- exit_price 来自系统现有的 MarketSnapshot 表（启动 / 12:00 / 14:00-15:00 收集）
  通过 get_snapshot_at_or_before 找 horizon 末日（或之前 max_lag_days 内）的最近一笔
- pnl_pct = (exit - entry) / entry * 100
- 自动判 outcome_direction：±2% 内为 neutral，超过为 bullish/bearish
- 写回字段不改 replay_status（让用户/LLM 还能做 replay 分析）

不接外部数据源：避免 backend 进程在没网时 hang。
不存在 MarketSnapshot 数据时跳过该条（保留 pending），等下次再试。
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional

from loguru import logger
from sqlalchemy.orm import Session

from app.database import safe_commit
from app.schemas.decision_snapshot import DecisionSnapshot

# Horizon 字符串 → 天数
_HORIZON_TO_DAYS = {
    "immediate": 1,
    "short": 7,
    "medium": 30,
    "long": 90,
    "unspecified": 7,
}
DEFAULT_HORIZON_DAYS = 7
# ±N% 内视为 neutral，避免噪声触发方向判断
DIRECTION_THRESHOLD_PCT = 2.0
# get_snapshot_at_or_before 找到的快照与 target_dt 最大允许间隔
EXIT_PRICE_MAX_LAG_DAYS = 3


def _extract_price(market_snap: Optional[dict]) -> Optional[float]:
    """容错从 market_snapshot dict 提取价格.

    不同写入路径用过的 key：price / current_price / current / close。
    """
    if not market_snap:
        return None
    for k in ("price", "current_price", "current", "close", "last_price"):
        v = market_snap.get(k)
        if v is None:
            continue
        try:
            f = float(v)
            if f > 0:
                return f
        except (TypeError, ValueError):
            continue
    return None


def infer_horizon_days(snapshot: DecisionSnapshot) -> int:
    """从 snapshot 推断 horizon 天数.

    优先级：
    1. claims_snapshot 里所有 claim 的 time_horizon 多数投票
    2. 默认 7 天
    """
    claims = snapshot.claims_snapshot or []
    horizons = [c.get("time_horizon") for c in claims if isinstance(c, dict) and c.get("time_horizon")]
    if horizons:
        most_common = Counter(horizons).most_common(1)[0][0]
        return _HORIZON_TO_DAYS.get(most_common, DEFAULT_HORIZON_DAYS)
    return DEFAULT_HORIZON_DAYS


def judge_direction(entry: float, exit_price: float, threshold_pct: float = DIRECTION_THRESHOLD_PCT) -> tuple[str, float]:
    """根据 entry / exit 算 PnL%，返回 (direction, pnl_pct).

    - pnl_pct > +threshold: bullish
    - pnl_pct < -threshold: bearish
    - 否则 neutral
    """
    pnl_pct = (exit_price - entry) / entry * 100.0
    if pnl_pct > threshold_pct:
        direction = "bullish"
    elif pnl_pct < -threshold_pct:
        direction = "bearish"
    else:
        direction = "neutral"
    return direction, round(pnl_pct, 4)


def _fetch_exit_price(db: Session, asset: str, target_dt: datetime) -> Optional[tuple[float, datetime]]:
    """从系统已收集的 MarketSnapshot 表查 horizon 末日附近的价格.

    Returns (price, observed_ts)；找不到或超过 max_lag_days 时返回 None。
    """
    from app.services.snapshot_service import get_snapshot_at_or_before

    snap = get_snapshot_at_or_before(db, asset, target_dt)
    if not snap or snap.get("price") is None:
        return None
    try:
        price = float(snap["price"])
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None

    # 解析 ts (ISO string) → datetime
    ts_raw = snap.get("ts")
    if isinstance(ts_raw, str):
        try:
            observed_ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except ValueError:
            observed_ts = target_dt
    elif isinstance(ts_raw, datetime):
        observed_ts = ts_raw
    else:
        observed_ts = target_dt

    # 时区对齐
    if observed_ts.tzinfo is None:
        observed_ts = observed_ts.replace(tzinfo=timezone.utc)
    if target_dt.tzinfo is None:
        target_dt = target_dt.replace(tzinfo=timezone.utc)

    lag_days = abs((target_dt - observed_ts).days)
    if lag_days > EXIT_PRICE_MAX_LAG_DAYS:
        return None
    return price, observed_ts


def auto_track_pnl(db: Session, *, dry_run: bool = False, limit: int = 500) -> dict:
    """主入口：扫到期的 pending snapshot 自动算 outcome.

    Args:
        dry_run: True 时只统计不写入
        limit: 一次最多处理多少条（防止单次过长）

    Returns:
        {
            "checked": int,
            "evaluated": int,           # 实际评估并(可能)写入的
            "skipped_too_early": int,   # horizon 末日还没到
            "skipped_no_entry": int,    # snapshot 里没 entry price
            "skipped_no_exit": int,     # MarketSnapshot 表里没找到 exit price
            "errors": int,
            "details": [{snapshot_id, direction, pnl_pct, horizon_days}, ...],
        }
    """
    now = datetime.now(timezone.utc)
    pending = (
        db.query(DecisionSnapshot)
        .filter(DecisionSnapshot.replay_status == "pending")
        .filter(DecisionSnapshot.outcome_direction.is_(None))
        .order_by(DecisionSnapshot.snapshot_at.asc())
        .limit(limit)
        .all()
    )

    summary = {
        "checked": 0,
        "evaluated": 0,
        "skipped_too_early": 0,
        "skipped_no_entry": 0,
        "skipped_no_exit": 0,
        "errors": 0,
        "details": [],
    }

    any_written = False
    for s in pending:
        summary["checked"] += 1
        try:
            horizon_days = infer_horizon_days(s)
            snap_at = s.snapshot_at
            if snap_at.tzinfo is None:
                snap_at = snap_at.replace(tzinfo=timezone.utc)
            target_dt = snap_at + timedelta(days=horizon_days)

            if target_dt > now:
                summary["skipped_too_early"] += 1
                continue

            entry = _extract_price(s.market_snapshot)
            if entry is None:
                summary["skipped_no_entry"] += 1
                continue

            exit_result = _fetch_exit_price(db, s.asset, target_dt)
            if exit_result is None:
                summary["skipped_no_exit"] += 1
                continue
            exit_price, observed_ts = exit_result

            direction, pnl_pct = judge_direction(entry, exit_price)

            if not dry_run:
                s.outcome_direction = direction
                s.outcome_pnl_pct = pnl_pct
                s.outcome_observed_at = observed_ts
                # 不改 replay_status：让用户/LLM 还能做后续 replay 分析
                # 在 replay_notes 里加一行 auto 标记（不覆盖已有）
                auto_line = (
                    f"[Auto-PnL @ {now.isoformat(timespec='seconds')}] "
                    f"horizon={horizon_days}d entry={entry:.6g} exit={exit_price:.6g} "
                    f"pnl={pnl_pct:+.2f}% direction={direction}"
                )
                if s.replay_notes:
                    s.replay_notes = s.replay_notes + "\n" + auto_line
                else:
                    s.replay_notes = auto_line
                s.updated_at = now
                db.flush()
                any_written = True

            summary["evaluated"] += 1
            summary["details"].append({
                "snapshot_id": s.id,
                "asset": s.asset,
                "horizon_days": horizon_days,
                "entry_price": entry,
                "exit_price": exit_price,
                "pnl_pct": pnl_pct,
                "direction": direction,
                "snapshot_at": snap_at.isoformat(),
                "observed_at": observed_ts.isoformat(),
            })
        except Exception as e:
            logger.warning(f"[AutoPnL] {s.id} ({s.asset}) failed: {e}")
            summary["errors"] += 1

    if any_written:
        try:
            safe_commit(db)
        except Exception as commit_err:
            logger.error(f"[AutoPnL] commit failed: {commit_err}")
            summary["errors"] += 1

    return summary
