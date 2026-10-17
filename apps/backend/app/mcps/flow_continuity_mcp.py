"""Flow Continuity MCP — mock 模式从数据库读取，live 模式接入 Binance Futures API（无需 Key，需代理）"""
import json
from typing import Optional, List
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from loguru import logger
from app.mcps.base import MCPResponse
from app.schemas.flow_signal_card import FlowSignalCard
from app.config import settings
from app.services import cache
from app.services import market_persist as persist

SOURCE_PACKAGE = "flow_continuity_mcp"

BINANCE_FUTURES_SYMBOLS = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "BNB": "BNBUSDT",
    "SOL": "SOLUSDT",
}


def _get_proxies() -> Optional[dict]:
    http_proxy  = settings.HTTP_PROXY
    https_proxy = settings.HTTPS_PROXY
    if http_proxy or https_proxy:
        return {"http": http_proxy or https_proxy, "https": https_proxy or http_proxy}
    return None


def _fetch_funding_rate(asset: str) -> Optional[dict]:
    """从 Binance Futures 获取资金费率（无需 Key，需代理）"""
    try:
        import requests as req
        symbol = BINANCE_FUTURES_SYMBOLS.get(asset.upper())
        if not symbol:
            return None
        url = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}&limit=3"
        data = req.get(url, timeout=8, proxies=_get_proxies()).json()
        if not data:
            return None
        latest = data[-1]
        rate = float(latest.get("fundingRate", 0))
        direction = "bullish" if rate > 0.0001 else ("bearish" if rate < -0.0001 else "neutral")
        return {
            "signal_type": "funding_rate",
            "asset": asset.upper(),
            "direction": direction,
            "magnitude": round(abs(rate) * 100, 4),
            "raw_value": rate,
            "normalized_value": round(rate * 1000, 4),
            "description": f"资金费率 {rate*100:.4f}%，{'多头付费（看涨偏向）' if rate > 0 else ('空头付费（看跌偏向）' if rate < 0 else '中性')}",
            "continuity_days": None,
            "is_trend_change": False,
            "data_source": "binance_futures_public_api",
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception:
        return None


def _fetch_open_interest(asset: str) -> Optional[dict]:
    """从 Binance Futures 获取未平仓合约量及历史趋势（无需 Key，需代理）"""
    try:
        import requests as req
        symbol = BINANCE_FUTURES_SYMBOLS.get(asset.upper())
        if not symbol:
            return None
        px = _get_proxies()
        # 当前 OI
        cur = req.get(f"https://fapi.binance.com/fapi/v1/openInterest?symbol={symbol}",
                      timeout=8, proxies=px).json()
        oi = float(cur.get("openInterest", 0))
        # OI 历史（1h, 最近48条）
        hist_url = ("https://fapi.binance.com/futures/data/openInterestHist"
                    f"?symbol={symbol}&period=1h&limit=48")
        hist = req.get(hist_url, timeout=8, proxies=px).json()
        oi_vals = [float(h["sumOpenInterest"]) for h in hist] if isinstance(hist, list) else []
        oi_trend = "neutral"
        if len(oi_vals) >= 6:
            recent = sum(oi_vals[-3:]) / 3
            older  = sum(oi_vals[-6:-3]) / 3
            oi_trend = "rising" if recent > older * 1.01 else ("falling" if recent < older * 0.99 else "neutral")
        direction = "bullish" if oi_trend == "rising" else ("bearish" if oi_trend == "falling" else "neutral")
        return {
            "signal_type": "open_interest",
            "asset": asset.upper(),
            "direction": direction,
            "magnitude": round(oi / 1e6, 2),
            "raw_value": oi,
            "normalized_value": None,
            "description": f"未平仓合约 {oi/1e6:.1f}M，趋势: {oi_trend}",
            "continuity_days": None,
            "is_trend_change": False,
            "data_source": "binance_futures_public_api",
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception:
        return None


def _fetch_taker_ratio(asset: str) -> Optional[dict]:
    """从 Binance Futures 获取主动买卖比 Taker Buy/Sell（无需 Key，需代理）"""
    try:
        import requests as req
        symbol = BINANCE_FUTURES_SYMBOLS.get(asset.upper())
        if not symbol:
            return None
        px = _get_proxies()
        url = ("https://fapi.binance.com/futures/data/takerlongshortRatio"
               f"?symbol={symbol}&period=1h&limit=24")
        data = req.get(url, timeout=8, proxies=px).json()
        if not isinstance(data, list) or not data:
            return None
        latest = data[-1]
        ratio = float(latest.get("buySellRatio", 1.0))
        buy_vol = float(latest.get("buyVol", 0))
        sell_vol = float(latest.get("sellVol", 0))
        direction = "bullish" if ratio > 1.05 else ("bearish" if ratio < 0.95 else "neutral")
        return {
            "signal_type": "taker_buy_sell_ratio",
            "asset": asset.upper(),
            "direction": direction,
            "magnitude": round(abs(ratio - 1.0), 4),
            "raw_value": ratio,
            "normalized_value": round(buy_vol / (buy_vol + sell_vol), 4) if (buy_vol + sell_vol) > 0 else None,
            "description": f"主动买卖比 {ratio:.3f}（买量占比 {buy_vol/(buy_vol+sell_vol)*100:.1f}%）" if (buy_vol+sell_vol) > 0 else f"主动买卖比 {ratio:.3f}",
            "continuity_days": None,
            "is_trend_change": False,
            "data_source": "binance_futures_public_api",
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception:
        return None


def _fetch_top_trader_ratio(asset: str) -> Optional[dict]:
    """从 Binance Futures 获取大户持仓多空比（无需 Key，需代理）"""
    try:
        import requests as req
        symbol = BINANCE_FUTURES_SYMBOLS.get(asset.upper())
        if not symbol:
            return None
        px = _get_proxies()
        url = ("https://fapi.binance.com/futures/data/topLongShortAccountRatio"
               f"?symbol={symbol}&period=1h&limit=1")
        data = req.get(url, timeout=8, proxies=px).json()
        if not isinstance(data, list) or not data:
            return None
        latest = data[-1]
        ratio = float(latest.get("longShortRatio", 1.0))
        long_pct = float(latest.get("longAccount", 0.5))
        direction = "bullish" if ratio > 1.2 else ("bearish" if ratio < 0.8 else "neutral")
        return {
            "signal_type": "top_trader_long_short",
            "asset": asset.upper(),
            "direction": direction,
            "magnitude": round(abs(ratio - 1.0), 4),
            "raw_value": ratio,
            "normalized_value": round(long_pct, 4),
            "description": f"大户多空比 {ratio:.2f}（多头占比 {long_pct*100:.1f}%）",
            "continuity_days": None,
            "is_trend_change": False,
            "data_source": "binance_futures_public_api",
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception:
        return None


def _fetch_long_short_ratio(asset: str) -> Optional[dict]:
    """从 Binance Futures 获取多空比（无需 Key，需代理）"""
    try:
        import requests as req
        symbol = BINANCE_FUTURES_SYMBOLS.get(asset.upper())
        if not symbol:
            return None
        url = f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={symbol}&period=4h&limit=1"
        data = req.get(url, timeout=8, proxies=_get_proxies()).json()
        if not data:
            return None
        latest = data[-1]
        ratio = float(latest.get("longShortRatio", 1.0))
        long_pct = float(latest.get("longAccount", 0.5))
        direction = "bullish" if ratio > 1.2 else ("bearish" if ratio < 0.8 else "neutral")
        return {
            "signal_type": "long_short_ratio",
            "asset": asset.upper(),
            "direction": direction,
            "magnitude": round(abs(ratio - 1.0), 4),
            "raw_value": ratio,
            "normalized_value": round(long_pct, 4),
            "description": f"多空比 {ratio:.2f}（多头占比 {long_pct*100:.1f}%）",
            "continuity_days": None,
            "is_trend_change": False,
            "data_source": "binance_futures_public_api",
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception:
        return None


class FlowContinuityMCP:
    def __init__(self, db: Session):
        self.db = db
        self.use_mock = settings.USE_MOCK_FLOW_DATA

    def get_flow_signals(
        self,
        asset: str,
        signal_type: Optional[str] = None,
        limit: int = 10,
    ) -> MCPResponse[List[dict]]:
        """获取资产的资金流信号"""
        try:
            if not self.use_mock and asset.upper() in BINANCE_FUTURES_SYMBOLS:
                from app.services.api_quota import allow_flow, mark_flow_called
                # 1. Redis 缓存（TTL 3分钟）
                cache_key = cache.make_key("flow", asset.upper(), signal_type)
                cached = cache.get(cache_key)
                if cached is not None:
                    return MCPResponse.ok(data=cached[:limit], source_package=SOURCE_PACKAGE)

                # 2. DB 新鲜数据（3分钟内）
                fresh = persist.get_fresh_flow_signals(self.db, asset, max_age_minutes=3)
                if fresh:
                    if signal_type:
                        fresh = [s for s in fresh if s["signal_type"] == signal_type]
                    cache.set(cache_key, fresh, ttl=180)
                    return MCPResponse.ok(data=fresh[:limit], source_package=SOURCE_PACKAGE)

                # 3. 配额检查：3分钟冷却内禁止重复调用
                if not allow_flow(asset):
                    logger.debug(f"[FlowMCP] quota cooldown for {asset}, using DB latest")
                    q = self.db.query(FlowSignalCard).filter(
                        FlowSignalCard.asset == asset.upper(),
                        FlowSignalCard.status == "active",
                    )
                    if signal_type:
                        q = q.filter(FlowSignalCard.signal_type == signal_type)
                    rows = q.order_by(FlowSignalCard.observed_at.desc()).limit(limit).all()
                    data = [{
                        "id": r.id, "asset": r.asset, "signal_type": r.signal_type,
                        "direction": r.direction, "magnitude": r.magnitude,
                        "raw_value": r.raw_value, "description": r.description,
                        "data_source": r.data_source + "[db_fallback]",
                        "observed_at": r.observed_at.isoformat() if r.observed_at else None,
                    } for r in rows]
                    return MCPResponse.ok(data=data, source_package=SOURCE_PACKAGE)

                # 4. 外部 API 调用
                live_signals = []
                for fetcher in [_fetch_funding_rate, _fetch_long_short_ratio,
                                 _fetch_open_interest, _fetch_taker_ratio, _fetch_top_trader_ratio]:
                    result = fetcher(asset)
                    if result:
                        live_signals.append(result)
                if live_signals:
                    if signal_type:
                        live_signals = [s for s in live_signals if s["signal_type"] == signal_type]
                    persist.persist_flow_signals(self.db, live_signals)
                    cache.set(cache_key, live_signals, ttl=180)
                    mark_flow_called(asset)  # 标记配额
                    return MCPResponse.ok(data=live_signals[:limit], source_package=SOURCE_PACKAGE)

            # mock 模式或 API 失败：从数据库读取
            q = self.db.query(FlowSignalCard).filter(
                FlowSignalCard.asset == asset.upper(),
                FlowSignalCard.status == "active",
            )
            if signal_type:
                q = q.filter(FlowSignalCard.signal_type == signal_type)
            rows = q.order_by(FlowSignalCard.observed_at.desc()).limit(limit).all()
            data = [
                {
                    "id": r.id,
                    "asset": r.asset,
                    "signal_type": r.signal_type,
                    "timeframe": r.timeframe,
                    "observed_at": r.observed_at.isoformat() if r.observed_at else None,
                    "direction": r.direction,
                    "magnitude": r.magnitude,
                    "raw_value": r.raw_value,
                    "normalized_value": r.normalized_value,
                    "description": r.description,
                    "continuity_days": r.continuity_days,
                    "is_trend_change": r.is_trend_change,
                    "data_source": r.data_source,
                }
                for r in rows
            ]
            if self.use_mock:
                return MCPResponse.mock(data=data, source_package=SOURCE_PACKAGE)
            return MCPResponse.ok(data=data, source_package=SOURCE_PACKAGE)
        except Exception as e:
            return MCPResponse.fail(str(e), SOURCE_PACKAGE)

    def get_continuity_summary(self, asset: str) -> MCPResponse[dict]:
        """获取资产资金流综合摘要"""
        signals_resp = self.get_flow_signals(asset, limit=20)
        signals = signals_resp.data or []

        if not signals:
            summary = {
                "asset": asset,
                "overall_direction": "neutral",
                "signal_count": 0,
                "bullish_signals": 0,
                "bearish_signals": 0,
                "avg_magnitude": 0.0,
                "signals": [],
            }
        else:
            bullish = sum(1 for s in signals if s.get("direction") == "bullish")
            bearish = sum(1 for s in signals if s.get("direction") == "bearish")
            avg_mag = sum(s.get("magnitude") or 0 for s in signals) / len(signals)
            direction = "bullish" if bullish > bearish else (
                "bearish" if bearish > bullish else "neutral"
            )
            summary = {
                "asset": asset,
                "overall_direction": direction,
                "signal_count": len(signals),
                "bullish_signals": bullish,
                "bearish_signals": bearish,
                "avg_magnitude": round(avg_mag, 3),
                "signals": signals,
            }

        if self.use_mock:
            return MCPResponse.mock(data=summary, source_package=SOURCE_PACKAGE)
        return MCPResponse.ok(data=summary, source_package=SOURCE_PACKAGE)
