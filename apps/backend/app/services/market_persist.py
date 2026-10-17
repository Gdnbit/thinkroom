"""市场数据持久化服务

职责：将 live API 获取的数据写入数据库，实现：
1. 数据长期积累（历史行情/资金流/事件）
2. DB 优先策略：近期数据直接从 DB 读，超过新鲜度阈值才调外部 API
3. 节省外部 API 配额

新鲜度策略：
- 行情数据：5 分钟内的 DB 记录直接返回
- 资金流数据：3 分钟内的 DB 记录直接返回
- 事件数据：30 分钟内的 DB 记录直接返回
"""
from datetime import datetime, timezone, timedelta
from typing import Optional, Any
from ulid import ULID
from loguru import logger
from sqlalchemy.orm import Session
from app.schemas.market_state_card import MarketStateCard
from app.schemas.flow_signal_card import FlowSignalCard
from app.schemas.event_card import EventCard
from app.database import _write_lock


# ─── 行情数据 ──────────────────────────────────────────────

def get_fresh_market_state(db: Session, asset: str, timeframe: str, max_age_minutes: int = 5) -> Optional[dict]:
    """从 DB 读取足够新鲜的行情数据，不够新鲜返回 None（触发外部 API 调用）"""
    threshold = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)
    row = (
        db.query(MarketStateCard)
        .filter(
            MarketStateCard.asset == asset.upper(),
            MarketStateCard.timeframe == timeframe,
            MarketStateCard.status == "active",
            MarketStateCard.snapshot_at >= threshold,
        )
        .order_by(MarketStateCard.snapshot_at.desc())
        .first()
    )
    if not row:
        return None
    logger.debug(f"[Persist] DB hit: market {asset} {timeframe}")
    return _market_row_to_dict(row)


def persist_market_state(db: Session, data: dict) -> bool:
    """将 live 行情数据写入 market_state_cards"""
    with _write_lock:
        try:
            asset = data.get("asset", "").upper()
            timeframe = data.get("timeframe", "1d")
            if not asset:
                return False
            row = MarketStateCard(
                id=str(ULID()),
                asset=asset,
                timeframe=timeframe,
                snapshot_at=datetime.now(timezone.utc),
                price=data.get("price"),
                price_change_1d=data.get("price_change_1d"),
                price_change_7d=data.get("price_change_7d"),
                price_change_30d=data.get("price_change_30d"),
                trend_direction=data.get("trend_direction"),
                trend_strength=data.get("trend_strength"),
                key_support_levels=data.get("key_support_levels", []),
                key_resistance_levels=data.get("key_resistance_levels", []),
                rsi_14=data.get("rsi_14"),
                ma_20=data.get("ma_20"),
                ma_50=data.get("ma_50"),
                ma_200=data.get("ma_200"),
                volume_vs_avg=data.get("volume_vs_avg"),
                atr_14=data.get("atr_14"),
                structure_notes=data.get("structure_notes"),
                volatility_state=data.get("volatility_state"),
                data_source=data.get("data_source", "live_api"),
                extra_data={
                    k: v for k, v in data.items()
                    if k not in (
                        "id", "asset", "timeframe", "snapshot_at", "price",
                        "price_change_1d", "price_change_7d", "price_change_30d",
                        "trend_direction", "trend_strength", "key_support_levels",
                        "key_resistance_levels", "rsi_14", "ma_20", "ma_50", "ma_200",
                        "volume_vs_avg", "atr_14", "structure_notes",
                        "volatility_state", "data_source", "status",
                    )
                },
                status="active",
            )
            db.add(row)
            db.commit()
            logger.debug(f"[Persist] Saved market {asset} {timeframe} @ {row.snapshot_at}")
            return True
        except Exception as e:
            db.rollback()
            logger.warning(f"[Persist] persist_market_state failed: {e}")
            return False


def _market_row_to_dict(row: MarketStateCard) -> dict:
    return {
        "id": row.id, "asset": row.asset, "timeframe": row.timeframe,
        "snapshot_at": row.snapshot_at.isoformat() if row.snapshot_at else None,
        "price": row.price, "price_change_1d": row.price_change_1d,
        "price_change_7d": row.price_change_7d, "price_change_30d": row.price_change_30d,
        "trend_direction": row.trend_direction, "trend_strength": row.trend_strength,
        "key_support_levels": row.key_support_levels,
        "key_resistance_levels": row.key_resistance_levels,
        "rsi_14": row.rsi_14, "ma_20": row.ma_20,
        "ma_50": row.ma_50, "ma_200": row.ma_200,
        "volume_vs_avg": row.volume_vs_avg, "atr_14": row.atr_14,
        "structure_notes": row.structure_notes,
        "volatility_state": row.volatility_state,
        "data_source": row.data_source,
        "source_mode": "live",
        **(row.extra_data or {}),
    }


# ─── 资金流数据 ────────────────────────────────────────────

def get_fresh_flow_signals(db: Session, asset: str, max_age_minutes: int = 3) -> Optional[list]:
    """从 DB 读取足够新鲜的资金流数据"""
    threshold = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)
    rows = (
        db.query(FlowSignalCard)
        .filter(
            FlowSignalCard.asset == asset.upper(),
            FlowSignalCard.status == "active",
            FlowSignalCard.observed_at >= threshold,
        )
        .order_by(FlowSignalCard.observed_at.desc())
        .limit(10)
        .all()
    )
    if not rows:
        return None
    logger.debug(f"[Persist] DB hit: flow {asset} ({len(rows)} signals)")
    return [_flow_row_to_dict(r) for r in rows]


def persist_flow_signals(db: Session, signals: list) -> int:
    """批量保存资金流信号，返回成功写入数量"""
    if not signals:
        return 0
    with _write_lock:
        try:
            saved = 0
            for sig in signals:
                asset = sig.get("asset", "").upper()
                if not asset:
                    continue
                row = FlowSignalCard(
                    id=str(ULID()),
                    asset=asset,
                    signal_type=sig.get("signal_type", "unknown"),
                    timeframe=sig.get("timeframe", "1h"),
                    observed_at=datetime.now(timezone.utc),
                    direction=sig.get("direction"),
                    magnitude=sig.get("magnitude"),
                    raw_value=sig.get("raw_value"),
                    normalized_value=sig.get("normalized_value"),
                    description=sig.get("description"),
                    continuity_days=sig.get("continuity_days") or 1,
                    is_trend_change=sig.get("is_trend_change", False),
                    data_source=sig.get("data_source", "binance_futures"),
                    extra_data={k: v for k, v in sig.items()
                                if k not in ("id", "asset", "signal_type", "timeframe",
                                             "observed_at", "direction", "magnitude",
                                             "raw_value", "normalized_value", "description",
                                             "continuity_days", "is_trend_change", "data_source")},
                    status="active",
                )
                db.add(row)
                saved += 1
            if saved:
                db.commit()
                logger.debug(f"[Persist] Saved {saved} flow signals")
            return saved
        except Exception as e:
            db.rollback()
            logger.warning(f"[Persist] persist_flow_signals failed: {e}")
            return 0


def _flow_row_to_dict(r: FlowSignalCard) -> dict:
    return {
        "id": r.id, "asset": r.asset, "signal_type": r.signal_type,
        "timeframe": r.timeframe,
        "observed_at": r.observed_at.isoformat() if r.observed_at else None,
        "direction": r.direction, "magnitude": r.magnitude,
        "raw_value": r.raw_value, "normalized_value": r.normalized_value,
        "description": r.description, "continuity_days": r.continuity_days,
        "is_trend_change": r.is_trend_change, "data_source": r.data_source,
        "source_mode": "live",
    }


# ─── 事件数据 ──────────────────────────────────────────────

def get_fresh_events(db: Session, asset: Optional[str] = None, max_age_minutes: int = 30, limit: int = 10) -> Optional[list]:
    """从 DB 读取足够新鲜的事件数据"""
    threshold = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)
    q = (
        db.query(EventCard)
        .filter(
            EventCard.status == "active",
            EventCard.created_at >= threshold,
        )
    )
    if asset:
        q = q.filter(EventCard.asset_scope.contains([asset.upper()]))
    rows = q.order_by(EventCard.created_at.desc()).limit(limit).all()
    if not rows:
        return None
    logger.debug(f"[Persist] DB hit: events ({len(rows)})")
    return [_event_row_to_dict(r) for r in rows]


def persist_events(db: Session, events: list) -> int:
    """批量保存事件，去重（同 title + 同日期不重复写）"""
    if not events:
        return 0
    with _write_lock:
        try:
            saved = 0
            for evt in events:
                title = (evt.get("title") or "")[:200]
                if not title:
                    continue
                exists = (
                    db.query(EventCard)
                    .filter(
                        EventCard.title == title,
                        EventCard.created_at >= datetime.now(timezone.utc).replace(
                            hour=0, minute=0, second=0, microsecond=0
                        ),
                    )
                    .first()
                )
                if exists:
                    continue
                row = EventCard(
                    id=str(ULID()),
                    title=title,
                    summary=evt.get("summary") or evt.get("description") or "",
                    event_type=evt.get("event_type", "news"),
                    importance=evt.get("importance", 0.5),
                    impact_direction=evt.get("impact_direction", "neutral"),
                    asset_scope=evt.get("asset_scope", []),
                    theme_scope=evt.get("theme_scope", []),
                    geographic_scope=evt.get("geographic_scope", []),
                    source_name=evt.get("data_source") or evt.get("source_name", "live_feed"),
                    source_url=evt.get("source_url") or evt.get("url"),
                    event_timestamp=datetime.now(timezone.utc),
                    status="active",
                    extra_data={},
                )
                db.add(row)
                saved += 1
            if saved:
                db.commit()
                logger.debug(f"[Persist] Saved {saved} events")
            return saved
        except Exception as e:
            db.rollback()
            logger.warning(f"[Persist] persist_events failed: {e}")
            return 0


def _event_row_to_dict(r: EventCard) -> dict:
    return {
        "id": r.id, "title": r.title, "summary": r.summary,
        "event_type": r.event_type, "importance": r.importance,
        "impact_direction": r.impact_direction,
        "asset_scope": r.asset_scope, "theme_scope": r.theme_scope,
        "occurred_at": r.event_timestamp.isoformat() if r.event_timestamp else None,
        "data_source": r.source_name, "source_url": r.source_url,
        "source_mode": "live",
    }
