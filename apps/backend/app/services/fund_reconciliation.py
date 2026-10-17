"""G3 · 基金估值 EOD 对账服务.

背景
====
`estimate_one_fund` 每次跑 snapshot_capture_job 都会写一条 FundEstimateSnapshot，
但从来没有跟"真实日终 NAV"对账。长期估值可信度无法量化。

设计取舍
========
个人投资工具 + 免费数据源场景下，**不强求**自动从基金公司 API 拉真实 NAV。
而是：

1. **Scheduler EOD job**（每天 17:00 北京时间，A 股收盘 1.5 小时后）
   - 对每个 active fund，把当天最近一条 FundEstimateSnapshot 标记 `is_eod_final=True`
   - 不抢已经标记过的（幂等）

2. **用户手动填写实际 NAV**（API endpoint `POST /api/v1/fund-reconciliation/{snapshot_id}/actual`）
   - 用户在天眼查 / 基金公司官网 / 蛋卷基金等查询真实当日净值后填入

3. **API 端点**
   - `GET /pending` — 列待填实际值的 EOD final
   - `GET /summary?fund_code=X&since_days=30` — 误差统计（MAE/RMSE/MAPE/偏差方向）
   - `POST /{snapshot_id}/actual` — 填入实际 NAV

关键指标
========
- **MAE**（mean absolute error）：误差绝对值的平均
- **RMSE**（root mean square error）：误差平方和的根号 — 大误差被放大
- **MAPE**（mean absolute percentage error）：相对误差均值，跨 fund 可比
- **bias_pct**（系统性偏差）：(est - actual) / actual 的均值；正值=系统性高估
- **max_error_abs / max_error_pct**：最大单日误差

不主动做的事（YAGNI）
====================
- 不引入第三方实时净值 API（个人用先用手动；公司化部署再说）
- 不持久化对账结果到独立表（够用就直接在 FundEstimateSnapshot 上算）
- 不做误差告警通知（先看 dashboard）
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.database import safe_commit
from app.schemas.market_snapshot import FundEstimateSnapshot, FundProfile


# ── 数据类 ──────────────────────────────────────────────────────────


@dataclass
class ReconciliationSummary:
    fund_code: str
    sample_count: int                # 已对账的快照条数
    pending_count: int               # 等待填实际值的 EOD final 条数
    mae_pct: Optional[float]         # 平均绝对误差（百分比）
    rmse_pct: Optional[float]        # 均方根误差（百分比）
    mape_pct: Optional[float]        # 平均绝对百分比误差
    bias_pct: Optional[float]        # 系统性偏差（正=系统性高估）
    max_error_pct: Optional[float]   # 最大单日误差（百分比，绝对值）
    last_eod_at: Optional[str]       # 最近一次 EOD final 时间
    since_days: int


# ── 辅助 ────────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _today_window_utc() -> tuple[datetime, datetime]:
    """以 Asia/Shanghai 「今天」对应的 UTC 时间窗口."""
    try:
        from zoneinfo import ZoneInfo
        sh = datetime.now(ZoneInfo("Asia/Shanghai"))
        sh_start = sh.replace(hour=0, minute=0, second=0, microsecond=0)
        sh_end = sh_start + timedelta(days=1)
        return sh_start.astimezone(timezone.utc), sh_end.astimezone(timezone.utc)
    except Exception:
        # 退化：UTC 当天
        u_start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
        return u_start, u_start + timedelta(days=1)


# ── EOD 标记（scheduler 调用） ─────────────────────────────────────


def mark_eod_finals(db: Session, *, auto_fetch_actual: bool = True) -> Dict[str, Any]:
    """把每个 active fund 当天最后一条 FundEstimateSnapshot 标记为 is_eod_final=True.

    幂等：已标记的不动；如果 fund 当天没有快照，跳过。

    Args:
        auto_fetch_actual: G4 增强 — 标记 EOD 后自动尝试用 AKShare 拉真实 NAV
                           并填入 actual_nav。akshare 不可用 / 失败时静默跳过，
                           用户可手动填。

    返回 summary 用于日志。
    """
    today_start, today_end = _today_window_utc()

    profiles = db.query(FundProfile).filter(FundProfile.status == "active").all()
    if not profiles:
        return {
            "funds_total": 0, "marked": 0, "skipped_already": 0,
            "skipped_no_data": 0, "auto_filled": 0, "auto_fetch_failed": 0,
        }

    marked = 0
    skipped_already = 0
    skipped_no_data = 0
    auto_filled = 0
    auto_fetch_failed = 0

    # 检测 akshare 可用性（一次）
    akshare_ok = False
    if auto_fetch_actual:
        try:
            from app.mcps import akshare_provider
            akshare_ok = akshare_provider.is_available()
        except ImportError:
            akshare_ok = False

    for p in profiles:
        # 当日已有 final 标记？
        existing_final = (
            db.query(FundEstimateSnapshot)
            .filter(
                FundEstimateSnapshot.fund_code == p.fund_code,
                FundEstimateSnapshot.ts >= today_start,
                FundEstimateSnapshot.ts < today_end,
                FundEstimateSnapshot.is_eod_final.is_(True),
            )
            .first()
        )
        target_snap = existing_final
        if existing_final:
            skipped_already += 1
        else:
            # 找当天最后一条
            last_today = (
                db.query(FundEstimateSnapshot)
                .filter(
                    FundEstimateSnapshot.fund_code == p.fund_code,
                    FundEstimateSnapshot.ts >= today_start,
                    FundEstimateSnapshot.ts < today_end,
                )
                .order_by(FundEstimateSnapshot.ts.desc())
                .first()
            )
            if not last_today:
                skipped_no_data += 1
                continue
            last_today.is_eod_final = True
            marked += 1
            target_snap = last_today

        # G4: 尝试自动 fetch 真实 NAV（仅当未填且 akshare 可用）
        if (
            akshare_ok
            and target_snap is not None
            and target_snap.actual_nav is None
            and len(p.fund_code) == 6  # 仅场外基金
        ):
            try:
                from app.mcps import akshare_provider
                nav_data = akshare_provider.fetch_fund_nav(p.fund_code)
                if nav_data and nav_data.get("nav"):
                    target_snap.actual_nav = float(nav_data["nav"])
                    auto_filled += 1
                    logger.info(
                        f"[FundReconciliation] auto-filled {p.fund_code} "
                        f"actual_nav={nav_data['nav']} (date={nav_data.get('nav_date')})"
                    )
            except Exception as e:
                auto_fetch_failed += 1
                logger.warning(
                    f"[FundReconciliation] auto-fetch failed for {p.fund_code}: {e}"
                )

    safe_commit(db)
    summary = {
        "funds_total": len(profiles),
        "marked": marked,
        "skipped_already": skipped_already,
        "skipped_no_data": skipped_no_data,
        "auto_filled": auto_filled,
        "auto_fetch_failed": auto_fetch_failed,
        "akshare_available": akshare_ok,
    }
    logger.info(f"[FundReconciliation] EOD mark: {summary}")
    return summary


# ── 用户手动填实际 NAV ─────────────────────────────────────────────


def fill_actual_nav(
    db: Session,
    snapshot_id: str,
    actual_nav: float,
) -> FundEstimateSnapshot:
    """把用户提供的实际 NAV 填入快照（仅允许 is_eod_final=True 的快照）.

    Raises:
        ValueError: 快照不存在 / 非 EOD final / actual_nav <= 0
    """
    if not actual_nav or actual_nav <= 0:
        raise ValueError(f"actual_nav must be > 0, got {actual_nav!r}")

    snap = (
        db.query(FundEstimateSnapshot)
        .filter(FundEstimateSnapshot.id == snapshot_id)
        .first()
    )
    if not snap:
        raise ValueError(f"snapshot {snapshot_id} not found")
    if not snap.is_eod_final:
        raise ValueError(
            f"snapshot {snapshot_id} is not EOD final (is_eod_final=False); "
            f"only EOD final snapshots can be reconciled"
        )

    snap.actual_nav = float(actual_nav)
    safe_commit(db)
    logger.info(
        f"[FundReconciliation] {snap.fund_code}@{snap.ts}: "
        f"est_nav={snap.est_nav} actual_nav={actual_nav}"
    )
    return snap


# ── 误差统计 ────────────────────────────────────────────────────────


def get_pending_reconciliation(
    db: Session,
    *,
    fund_code: Optional[str] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """列待填实际 NAV 的 EOD final 快照（最新优先）.

    用于让用户/前端展示"还有多少天的对账没补齐"。
    """
    q = db.query(FundEstimateSnapshot).filter(
        FundEstimateSnapshot.is_eod_final.is_(True),
        FundEstimateSnapshot.actual_nav.is_(None),
    )
    if fund_code:
        q = q.filter(FundEstimateSnapshot.fund_code == fund_code)
    rows = q.order_by(FundEstimateSnapshot.ts.desc()).limit(max(1, min(limit, 500))).all()

    return [
        {
            "id": r.id,
            "fund_code": r.fund_code,
            "ts": r.ts.isoformat() if r.ts else None,
            "est_nav": r.est_nav,
            "est_pct_change_1d": r.est_pct_change_1d,
            "confidence_level": r.confidence_level,
            "ref_index": r.ref_index,
        }
        for r in rows
    ]


def get_reconciliation_summary(
    db: Session,
    *,
    fund_code: Optional[str] = None,
    since_days: int = 30,
) -> List[ReconciliationSummary]:
    """计算误差统计（按 fund_code 分组）.

    若 fund_code 给定 → 只统计该一个 fund；否则覆盖所有 active fund。
    """
    since = _now() - timedelta(days=max(1, int(since_days)))

    fund_codes: List[str]
    if fund_code:
        fund_codes = [fund_code]
    else:
        profiles = db.query(FundProfile).filter(FundProfile.status == "active").all()
        fund_codes = [p.fund_code for p in profiles]

    out: List[ReconciliationSummary] = []
    for fc in fund_codes:
        # 已对账（actual_nav != null）的 EOD final
        rec_rows = (
            db.query(FundEstimateSnapshot)
            .filter(
                FundEstimateSnapshot.fund_code == fc,
                FundEstimateSnapshot.is_eod_final.is_(True),
                FundEstimateSnapshot.actual_nav.isnot(None),
                FundEstimateSnapshot.ts >= since,
            )
            .order_by(FundEstimateSnapshot.ts.desc())
            .all()
        )

        # pending：has is_eod_final but no actual_nav
        pending_count = (
            db.query(FundEstimateSnapshot)
            .filter(
                FundEstimateSnapshot.fund_code == fc,
                FundEstimateSnapshot.is_eod_final.is_(True),
                FundEstimateSnapshot.actual_nav.is_(None),
                FundEstimateSnapshot.ts >= since,
            )
            .count()
        )

        if not rec_rows:
            out.append(ReconciliationSummary(
                fund_code=fc,
                sample_count=0,
                pending_count=pending_count,
                mae_pct=None,
                rmse_pct=None,
                mape_pct=None,
                bias_pct=None,
                max_error_pct=None,
                last_eod_at=None,
                since_days=since_days,
            ))
            continue

        # 误差 = (est_nav - actual_nav) / actual_nav
        errors_pct: List[float] = []
        for r in rec_rows:
            if r.est_nav is None or r.actual_nav is None or r.actual_nav <= 0:
                continue
            pct = (float(r.est_nav) - float(r.actual_nav)) / float(r.actual_nav)
            errors_pct.append(pct)

        if not errors_pct:
            out.append(ReconciliationSummary(
                fund_code=fc,
                sample_count=0,
                pending_count=pending_count,
                mae_pct=None,
                rmse_pct=None,
                mape_pct=None,
                bias_pct=None,
                max_error_pct=None,
                last_eod_at=rec_rows[0].ts.isoformat() if rec_rows[0].ts else None,
                since_days=since_days,
            ))
            continue

        n = len(errors_pct)
        bias = sum(errors_pct) / n
        abs_errors = [abs(e) for e in errors_pct]
        mae = sum(abs_errors) / n
        rmse = math.sqrt(sum(e * e for e in errors_pct) / n)
        mape = mae  # 误差已经是百分比，所以 MAPE = MAE
        max_err = max(abs_errors)

        out.append(ReconciliationSummary(
            fund_code=fc,
            sample_count=n,
            pending_count=pending_count,
            mae_pct=round(mae * 100, 4),
            rmse_pct=round(rmse * 100, 4),
            mape_pct=round(mape * 100, 4),
            bias_pct=round(bias * 100, 4),
            max_error_pct=round(max_err * 100, 4),
            last_eod_at=rec_rows[0].ts.isoformat() if rec_rows[0].ts else None,
            since_days=since_days,
        ))

    return out


def get_reconciliation_timeseries(
    db: Session,
    fund_code: str,
    since_days: int = 30,
) -> List[Dict[str, Any]]:
    """按时间顺序返回某 fund 的对账明细（用于前端画曲线图）.

    每个点：(date, est_nav, actual_nav, error_pct)
    """
    since = _now() - timedelta(days=max(1, int(since_days)))
    rows = (
        db.query(FundEstimateSnapshot)
        .filter(
            FundEstimateSnapshot.fund_code == fund_code,
            FundEstimateSnapshot.is_eod_final.is_(True),
            FundEstimateSnapshot.actual_nav.isnot(None),
            FundEstimateSnapshot.ts >= since,
        )
        .order_by(FundEstimateSnapshot.ts.asc())
        .all()
    )

    out = []
    for r in rows:
        if r.est_nav is None or r.actual_nav is None or r.actual_nav <= 0:
            continue
        err_pct = (float(r.est_nav) - float(r.actual_nav)) / float(r.actual_nav) * 100
        out.append({
            "id": r.id,
            "ts": r.ts.isoformat() if r.ts else None,
            "est_nav": r.est_nav,
            "actual_nav": r.actual_nav,
            "error_pct": round(err_pct, 4),
            "abs_error_pct": round(abs(err_pct), 4),
            "ref_index": r.ref_index,
            "confidence_level": r.confidence_level,
        })
    return out


__all__ = [
    "ReconciliationSummary",
    "mark_eod_finals",
    "fill_actual_nav",
    "get_pending_reconciliation",
    "get_reconciliation_summary",
    "get_reconciliation_timeseries",
]
