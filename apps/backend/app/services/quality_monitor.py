"""G5 · 数据源质量监控聚合服务.

基于持久化的 `SourceHealthLog` 表（`@/apps/backend/app/schemas/market_snapshot.py:61-72`）
做历史聚合，与 `data_source_manager` 的内存计数器互补：
- data_source_manager：进程内实时统计，重启即丢
- quality_monitor：DB 历史，跨重启可查；支持时间窗口、按 source/symbol 分组

主要场景
========
- 运营观察：「过去 7 天 binance 调用成功率多少？」
- 故障定位：「是哪一类错误最频繁？」
- 趋势分析：「延迟有没有明显变化？」

API 输出形态都是 dict（不是 SQLAlchemy 对象），供 FastAPI 直接序列化。
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.schemas.market_snapshot import SourceHealthLog


# ── 辅助函数 ────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _since(since_days: int) -> datetime:
    return _now() - timedelta(days=max(1, int(since_days)))


def _percentile(values: List[float], p: float) -> Optional[float]:
    """内联求百分位（不引 numpy）。p in [0, 100]."""
    if not values:
        return None
    sorted_v = sorted(values)
    k = (len(sorted_v) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_v) - 1)
    if f == c:
        return float(sorted_v[f])
    return float(sorted_v[f] + (sorted_v[c] - sorted_v[f]) * (k - f))


# ── 聚合函数 ────────────────────────────────────────────────────────


def get_quality_summary(db: Session, since_days: int = 7) -> Dict[str, Any]:
    """整体统计：调用总数 / 成功率 / 延迟分布."""
    since = _since(since_days)
    rows = (
        db.query(SourceHealthLog)
        .filter(SourceHealthLog.checked_at >= since)
        .all()
    )

    total = len(rows)
    if total == 0:
        return {
            "total_calls": 0,
            "success_count": 0,
            "failure_count": 0,
            "success_rate_pct": None,
            "latency_p50_ms": None,
            "latency_p95_ms": None,
            "latency_p99_ms": None,
            "latency_avg_ms": None,
            "since_days": since_days,
            "since_iso": since.isoformat(),
        }

    success_count = sum(1 for r in rows if r.success)
    failure_count = total - success_count
    latencies = [r.latency_ms for r in rows if r.latency_ms is not None]

    return {
        "total_calls": total,
        "success_count": success_count,
        "failure_count": failure_count,
        "success_rate_pct": round(success_count / total * 100, 2),
        "latency_p50_ms": _percentile(latencies, 50),
        "latency_p95_ms": _percentile(latencies, 95),
        "latency_p99_ms": _percentile(latencies, 99),
        "latency_avg_ms": round(sum(latencies) / len(latencies), 2) if latencies else None,
        "since_days": since_days,
        "since_iso": since.isoformat(),
    }


def get_quality_by_source(db: Session, since_days: int = 7) -> List[Dict[str, Any]]:
    """按 source_name 分组的统计；每行一个 source."""
    since = _since(since_days)
    rows = (
        db.query(SourceHealthLog)
        .filter(SourceHealthLog.checked_at >= since)
        .all()
    )
    if not rows:
        return []

    grouped: Dict[str, List[SourceHealthLog]] = defaultdict(list)
    for r in rows:
        grouped[r.source_name or "unknown"].append(r)

    out: List[Dict[str, Any]] = []
    for name, batch in grouped.items():
        total = len(batch)
        success_count = sum(1 for r in batch if r.success)
        latencies = [r.latency_ms for r in batch if r.latency_ms is not None]
        last = max(batch, key=lambda r: r.checked_at)
        out.append({
            "source_name": name,
            "total_calls": total,
            "success_count": success_count,
            "failure_count": total - success_count,
            "success_rate_pct": round(success_count / total * 100, 2),
            "latency_p50_ms": _percentile(latencies, 50),
            "latency_p95_ms": _percentile(latencies, 95),
            "latency_avg_ms": round(sum(latencies) / len(latencies), 2) if latencies else None,
            "last_checked_at": last.checked_at.isoformat() if last.checked_at else None,
            "last_success": last.success,
            "last_error_message": last.error_message if not last.success else None,
        })
    # 按调用次数倒序
    out.sort(key=lambda x: x["total_calls"], reverse=True)
    return out


def get_quality_by_symbol(db: Session, since_days: int = 7, limit: int = 50) -> List[Dict[str, Any]]:
    """按 symbol 分组（哪些资产抓取最不稳定）.

    用于发现「特定 symbol 一直挂」的问题（如某个港股代码格式不对）。
    """
    since = _since(since_days)
    rows = (
        db.query(SourceHealthLog)
        .filter(
            SourceHealthLog.checked_at >= since,
            SourceHealthLog.symbol.isnot(None),
        )
        .all()
    )
    if not rows:
        return []

    grouped: Dict[str, List[SourceHealthLog]] = defaultdict(list)
    for r in rows:
        grouped[r.symbol or "unknown"].append(r)

    out: List[Dict[str, Any]] = []
    for symbol, batch in grouped.items():
        total = len(batch)
        success = sum(1 for r in batch if r.success)
        out.append({
            "symbol": symbol,
            "total_calls": total,
            "success_count": success,
            "failure_count": total - success,
            "success_rate_pct": round(success / total * 100, 2),
        })
    # 优先返回失败率高的
    out.sort(key=lambda x: (x["success_rate_pct"], -x["total_calls"]))
    return out[:limit]


def get_quality_timeseries(
    db: Session,
    since_days: int = 7,
    bucket_hours: int = 4,
) -> List[Dict[str, Any]]:
    """按时间桶聚合 — 用于画时间趋势图.

    bucket_hours = 4 时一周窗口约 42 个桶；24 时是日级（适合 30 天窗口）。
    """
    since = _since(since_days)
    rows = (
        db.query(SourceHealthLog)
        .filter(SourceHealthLog.checked_at >= since)
        .all()
    )
    if not rows:
        return []

    # 桶起点：since 之后的第一个整 bucket_hours 小时
    bucket = max(1, int(bucket_hours))
    bucket_seconds = bucket * 3600

    def _bucket_start(ts: datetime) -> datetime:
        epoch = ts.timestamp()
        bucketed = (epoch // bucket_seconds) * bucket_seconds
        return datetime.fromtimestamp(bucketed, tz=timezone.utc)

    grouped: Dict[datetime, List[SourceHealthLog]] = defaultdict(list)
    for r in rows:
        if r.checked_at:
            grouped[_bucket_start(r.checked_at)].append(r)

    out: List[Dict[str, Any]] = []
    for bucket_ts, batch in sorted(grouped.items()):
        total = len(batch)
        success = sum(1 for r in batch if r.success)
        latencies = [r.latency_ms for r in batch if r.latency_ms is not None]
        out.append({
            "bucket_start_iso": bucket_ts.isoformat(),
            "total_calls": total,
            "success_count": success,
            "failure_count": total - success,
            "success_rate_pct": round(success / total * 100, 2),
            "latency_avg_ms": round(sum(latencies) / len(latencies), 2) if latencies else None,
            "latency_p95_ms": _percentile(latencies, 95),
        })
    return out


def get_recent_errors(db: Session, limit: int = 50) -> List[Dict[str, Any]]:
    """最近的失败记录（运营定位故障用）."""
    rows = (
        db.query(SourceHealthLog)
        .filter(SourceHealthLog.success.is_(False))
        .order_by(SourceHealthLog.checked_at.desc())
        .limit(max(1, min(int(limit), 500)))
        .all()
    )
    return [
        {
            "id": r.id,
            "source_name": r.source_name,
            "symbol": r.symbol,
            "checked_at": r.checked_at.isoformat() if r.checked_at else None,
            "error_message": r.error_message,
            "http_status": r.http_status,
        }
        for r in rows
    ]


def get_error_categories(db: Session, since_days: int = 7) -> List[Dict[str, Any]]:
    """异常类型分布 — error_message 简单分类（前缀 / 关键词聚合）.

    没用 LLM；用启发式规则把同一类错误折成一组。
    """
    since = _since(since_days)
    rows = (
        db.query(SourceHealthLog)
        .filter(
            SourceHealthLog.checked_at >= since,
            SourceHealthLog.success.is_(False),
            SourceHealthLog.error_message.isnot(None),
        )
        .all()
    )
    if not rows:
        return []

    def _categorize(msg: str) -> str:
        m = (msg or "").lower()
        if any(k in m for k in ("timeout", "timed out")):
            return "timeout"
        if any(k in m for k in ("connection", "refused", "reset", "unreachable")):
            return "connection"
        if any(k in m for k in ("dns", "resolution")):
            return "dns"
        if any(k in m for k in ("403", "forbidden", "unauthorized", "401", "auth")):
            return "auth"
        if any(k in m for k in ("429", "rate limit", "too many")):
            return "rate_limit"
        if any(k in m for k in ("500", "502", "503", "504", "internal server")):
            return "5xx"
        if any(k in m for k in ("empty response", "no data", "missing")):
            return "empty_response"
        if any(k in m for k in ("parse", "decode", "invalid json")):
            return "parse_error"
        return "other"

    counter: Counter[str] = Counter()
    examples: Dict[str, str] = {}
    for r in rows:
        cat = _categorize(r.error_message or "")
        counter[cat] += 1
        if cat not in examples and r.error_message:
            examples[cat] = (r.error_message or "")[:200]

    total = sum(counter.values()) or 1
    out = []
    for cat, n in counter.most_common():
        out.append({
            "category": cat,
            "count": n,
            "share_pct": round(n / total * 100, 2),
            "example": examples.get(cat),
        })
    return out


__all__ = [
    "get_quality_summary",
    "get_quality_by_source",
    "get_quality_by_symbol",
    "get_quality_timeseries",
    "get_recent_errors",
    "get_error_categories",
]
