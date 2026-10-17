"""Snapshot and fund estimation service (free-source friendly)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional
import time

from sqlalchemy.orm import Session

from app.database import safe_commit
from app.mcps.market_data_mcp import MarketDataMCP
from app.schemas.market_snapshot import (
    MarketSnapshot,
    FundProfile,
    FundEstimateSnapshot,
    SourceHealthLog,
)
from app.schemas.market_state_card import MarketStateCard
from app.schemas.watchlist import WatchlistItem
from ulid import ULID


@dataclass
class SnapshotJobResult:
    requested: int
    saved: int
    failed: int
    estimated_funds: int


DEFAULT_SYMBOL_MARKET = {
    # US
    "SPX": "US", "NDX": "US", "VIX": "US",
    # HK
    "HSI": "HK", "HSTECH": "HK",
    # CN
    "CSI300": "CN", "ZZ500": "CN", "CYB": "CN",
    # crypto
    "BTC": "CRYPTO", "ETH": "CRYPTO", "BNB": "CRYPTO", "SOL": "CRYPTO",
    # global commodities
    "GOLD": "COMMODITY", "SILVER": "COMMODITY", "OIL": "COMMODITY",
    # rates/fx
    "DXY": "FX",
    # derived/sentiment indicators
    "PIZZA_INDEX": "INDICATOR",
    "FEAR_GREED": "INDICATOR",
    # polymarket prediction markets use PM:<slug> prefix
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _safe_pct(current: Optional[float], prev: Optional[float]) -> Optional[float]:
    if current is None or prev is None or prev == 0:
        return None
    return (current - prev) / prev


def get_default_symbols(db: Session) -> list[str]:
    base = [
        # 主流指数
        "SPX", "NDX", "VIX",
        # 港股/A股
        "HSI", "HSTECH", "CSI300", "ZZ500", "CYB",
        # 商品期货
        "GOLD", "SILVER", "OIL",
        # 汇率
        "DXY",
        # 加密主流
        "BTC", "ETH", "BNB", "SOL",
    ]
    wl = (
        db.query(WatchlistItem)
        .filter(WatchlistItem.status == "active", WatchlistItem.item_type == "asset")
        .limit(200)
        .all()
    )
    extra = [w.item_key.upper() for w in wl if w.item_key]
    merged = list(dict.fromkeys(base + extra))
    return merged


def infer_market(symbol: str) -> str:
    return DEFAULT_SYMBOL_MARKET.get(symbol.upper(), "US")


def capture_market_snapshots(db: Session, symbols: list[str], timeframe: str = "1d") -> SnapshotJobResult:
    mcp = MarketDataMCP(db)
    requested = len(symbols)
    saved = 0
    failed = 0

    for symbol in symbols:
        started = time.perf_counter()
        try:
            resp = mcp.get_market_state(symbol, timeframe=timeframe)
            elapsed_ms = (time.perf_counter() - started) * 1000
            ok = bool(resp and resp.success and resp.data)

            db.add(SourceHealthLog(
                id=str(ULID()),
                source_name="market_data_mcp",
                symbol=symbol.upper(),
                checked_at=_now_utc(),
                success=ok,
                latency_ms=round(elapsed_ms, 2),
                http_status=200 if ok else None,
                error_message=None if ok else "empty response",
                meta={"timeframe": timeframe},
            ))

            if not ok:
                failed += 1
                continue

            data = resp.data
            # previous snapshot for same symbol
            prev = (
                db.query(MarketSnapshot)
                .filter(MarketSnapshot.symbol == symbol.upper())
                .order_by(MarketSnapshot.ts.desc())
                .first()
            )
            pct_1d = data.get("price_change_1d")
            if pct_1d is None:
                pct_1d = _safe_pct(data.get("price"), prev.price if prev else None)

            db.add(MarketSnapshot(
                id=str(ULID()),
                symbol=symbol.upper(),
                market=infer_market(symbol),
                ts=_now_utc(),
                price=data.get("price"),
                pct_change_1d=pct_1d,
                source_name=data.get("data_source", "market_data_mcp"),
                source_symbol=symbol.upper(),
                is_fallback="fallback" in str(data.get("data_source", "")).lower(),
                quality_flag="normal",
                meta={
                    "timeframe": timeframe,
                    "trend_direction": data.get("trend_direction"),
                    "rsi_14": data.get("rsi_14"),
                },
            ))
            saved += 1
        except Exception as e:
            failed += 1
            db.add(SourceHealthLog(
                id=str(ULID()),
                source_name="market_data_mcp",
                symbol=symbol.upper(),
                checked_at=_now_utc(),
                success=False,
                latency_ms=None,
                http_status=None,
                error_message=str(e)[:500],
                meta={"timeframe": timeframe},
            ))

    # 先 commit symbol 数据 —— 避免 estimate_all_funds 抛异常时前面所有 SourceHealthLog
    # 和 MarketSnapshot 被一起回滚（B-commit 修复）
    safe_commit(db)
    try:
        estimated_funds = estimate_all_funds(db)
        safe_commit(db)
    except Exception as e:
        from loguru import logger as _log
        _log.warning(f"[Snapshot] estimate_all_funds failed (non-fatal): {e}")
        estimated_funds = 0
    return SnapshotJobResult(requested=requested, saved=saved, failed=failed, estimated_funds=estimated_funds)


def capture_indicator_snapshots(db: Session) -> dict:
    """抓取衍生/情绪类指标（Pizza / Fear&Greed / Polymarket 等）。

    独立于 capture_market_snapshots：
    - 使用 indicator_fetchers 模块的无 key 免费 API
    - 写入同一个 market_snapshots 表，便于 market-history 页面统一检索
    - 失败整条跳过（不影响其他指标）
    - QuotaGuard 保护：手动连点刷新时走冷却窗口内的 DB 存量，不打外部 API
    """
    from app.services import indicator_fetchers as ind
    from app.services.api_quota import guard as _quota_guard
    from loguru import logger

    saved = 0
    failed = 0
    details: dict = {"pizza": None, "fear_greed": None, "polymarket": 0}

    # ── Pizza Index：优先用 DB 中最新 BTC 价，没有再从 CoinGecko 公共 API 抓 ──
    # 冷却类别 FAST（5min），scheduler 12:00 一次不会受阻，手动连点会被节流
    if not _quota_guard.allow("indicator_pizza", "FAST"):
        logger.debug("[Snapshot] pizza_index quota cooldown, skip fetch")
        details["pizza_skipped_by_quota"] = True
    else:
        try:
            btc_row = (
                db.query(MarketSnapshot)
                .filter(MarketSnapshot.symbol == "BTC")
                .order_by(MarketSnapshot.ts.desc())
                .first()
            )
            btc_price = float(btc_row.price) if btc_row and btc_row.price else None
            pz = ind.fetch_pizza_index(btc_price_usd=btc_price)
            if pz:
                _quota_guard.mark_called("indicator_pizza", "FAST")
                db.add(MarketSnapshot(
                    id=str(ULID()),
                    symbol="PIZZA_INDEX",
                    market="INDICATOR",
                    ts=_now_utc(),
                    price=pz.get("price"),
                    pct_change_1d=None,
                    source_name=pz.get("data_source", "pizza_index_derived"),
                    source_symbol="PIZZA_INDEX",
                    is_fallback=False,
                    quality_flag="normal",
                    meta={"btc_price": pz.get("btc_price"), **(pz.get("meta") or {})},
                ))
                saved += 1
                details["pizza"] = pz.get("price")
            else:
                failed += 1
        except Exception as e:
            logger.debug(f"[Snapshot] pizza_index failed: {e}")
            failed += 1

    # ── Fear & Greed Index ──
    # 冷却类别 HOURLY（1h）：F&G 每日一次更新，手动连点无意义
    if not _quota_guard.allow("indicator_fear_greed", "HOURLY"):
        logger.debug("[Snapshot] fear_greed quota cooldown, skip fetch")
        details["fear_greed_skipped_by_quota"] = True
    else:
        try:
            fg = ind.fetch_fear_greed_index()
            if fg:
                _quota_guard.mark_called("indicator_fear_greed", "HOURLY")
                db.add(MarketSnapshot(
                    id=str(ULID()),
                    symbol="FEAR_GREED",
                    market="INDICATOR",
                    ts=_now_utc(),
                    price=fg.get("price"),
                    pct_change_1d=None,
                    source_name=fg.get("data_source", "alternative_me"),
                    source_symbol="FEAR_GREED",
                    is_fallback=False,
                    quality_flag="normal",
                    meta=fg.get("meta") or {},
                ))
                saved += 1
                details["fear_greed"] = fg.get("price")
            else:
                failed += 1
        except Exception as e:
            logger.debug(f"[Snapshot] fear_greed failed: {e}")
            failed += 1

    # ── Polymarket Top N ──
    # 冷却类别 FAST（5min）：预测市场变化较慢，5min 足够
    if not _quota_guard.allow("indicator_polymarket", "FAST"):
        logger.debug("[Snapshot] polymarket quota cooldown, skip fetch")
        details["polymarket_skipped_by_quota"] = True
    else:
        try:
            pm_items = ind.fetch_polymarket_top_markets(limit=10) or []
            if pm_items:
                _quota_guard.mark_called("indicator_polymarket", "FAST")
            for item in pm_items:
                try:
                    sym = item.get("symbol") or "PM:unknown"
                    price_val = item.get("yes_price")  # 用 YES 价作为 0-1 概率
                    db.add(MarketSnapshot(
                        id=str(ULID()),
                        symbol=sym[:64],
                        market="INDICATOR",
                        ts=_now_utc(),
                        price=float(price_val) if price_val is not None else None,
                        pct_change_1d=None,
                        source_name="polymarket",
                        source_symbol=sym[:64],
                        is_fallback=False,
                        quality_flag="normal",
                        meta={
                            "question": item.get("question"),
                            "yes_price": item.get("yes_price"),
                            "no_price": item.get("no_price"),
                            "volume_usd": item.get("volume_usd"),
                            "end_date": item.get("end_date"),
                            "url": item.get("url"),
                        },
                    ))
                    saved += 1
                    details["polymarket"] += 1
                except Exception as e:
                    logger.debug(f"[Snapshot] polymarket item save failed: {e}")
                    failed += 1
        except Exception as e:
            logger.debug(f"[Snapshot] polymarket batch failed: {e}")
            failed += 1

    safe_commit(db)
    return {"saved": saved, "failed": failed, "details": details}


def estimate_all_funds(db: Session) -> int:
    profiles = db.query(FundProfile).filter(FundProfile.status == "active").all()
    count = 0
    for p in profiles:
        est = estimate_one_fund(db, p)
        if est:
            count += 1
    return count


def estimate_one_fund(db: Session, profile: FundProfile) -> bool:
    if profile.last_nav is None or not profile.basket:
        return False

    weighted_ret = 0.0
    effective_weight = 0.0
    refs = []

    for item in profile.basket:
        symbol = str(item.get("symbol", "")).upper()
        weight = float(item.get("weight", 0.0) or 0.0)
        if not symbol or weight <= 0:
            continue
        row = (
            db.query(MarketSnapshot)
            .filter(MarketSnapshot.symbol == symbol)
            .order_by(MarketSnapshot.ts.desc())
            .first()
        )
        if not row or row.pct_change_1d is None:
            continue
        weighted_ret += weight * float(row.pct_change_1d)
        effective_weight += weight
        refs.append(symbol)

    if effective_weight <= 0:
        return False

    blended_ret = weighted_ret / effective_weight
    est_pct = blended_ret * float(profile.beta or 1.0)
    est_nav = float(profile.last_nav) * (1 + est_pct)

    db.add(FundEstimateSnapshot(
        id=str(ULID()),
        fund_code=profile.fund_code,
        ts=_now_utc(),
        est_nav=round(est_nav, 6),
        est_pct_change_1d=round(est_pct, 6),
        confidence_level="medium" if effective_weight >= 0.6 else "low",
        ref_index=",".join(refs[:5]),
        model_version="v1",
        meta={
            "effective_weight": round(effective_weight, 4),
            "basket_size": len(profile.basket),
        },
    ))
    return True


def get_intraday_window(db: Session, symbol: str, minutes: int = 60) -> list[dict]:
    threshold = _now_utc() - timedelta(minutes=minutes)
    rows = (
        db.query(MarketSnapshot)
        .filter(MarketSnapshot.symbol == symbol.upper(), MarketSnapshot.ts >= threshold)
        .order_by(MarketSnapshot.ts.asc())
        .all()
    )
    return [{
        "ts": r.ts.isoformat() if r.ts else None,
        "symbol": r.symbol,
        "market": r.market,
        "price": r.price,
        "pct_change_1d": r.pct_change_1d,
        "quality_flag": r.quality_flag,
        "source_name": r.source_name,
    } for r in rows]


def get_snapshot_at_or_before(db: Session, symbol: str, ts: datetime) -> Optional[dict]:
    row = (
        db.query(MarketSnapshot)
        .filter(MarketSnapshot.symbol == symbol.upper(), MarketSnapshot.ts <= ts)
        .order_by(MarketSnapshot.ts.desc())
        .first()
    )
    if not row:
        return None
    return {
        "ts": row.ts.isoformat() if row.ts else None,
        "symbol": row.symbol,
        "market": row.market,
        "price": row.price,
        "pct_change_1d": row.pct_change_1d,
        "source_name": row.source_name,
        "quality_flag": row.quality_flag,
        "meta": row.meta,
    }


def sync_market_state_into_snapshots(db: Session, limit: int = 300) -> int:
    """Backfill helper: move latest market_state_cards into market_snapshots if missing.
    Safe to run repeatedly.
    """
    rows = (
        db.query(MarketStateCard)
        .order_by(MarketStateCard.snapshot_at.desc())
        .limit(limit)
        .all()
    )
    inserted = 0
    for r in rows:
        exists = (
            db.query(MarketSnapshot)
            .filter(MarketSnapshot.symbol == r.asset, MarketSnapshot.ts == r.snapshot_at)
            .first()
        )
        if exists:
            continue
        db.add(MarketSnapshot(
            id=str(ULID()),
            symbol=r.asset,
            market=infer_market(r.asset),
            ts=r.snapshot_at,
            price=r.price,
            pct_change_1d=r.price_change_1d,
            source_name=r.data_source,
            source_symbol=r.asset,
            is_fallback=False,
            quality_flag="normal",
            meta={"timeframe": r.timeframe},
        ))
        inserted += 1
    if inserted:
        safe_commit(db)
    return inserted
