"""外部市场指标抓取器（免费源友好）。

本模块集中放置"非传统行情"的指标数据源：
- Pizza 指数：基于 BTC 当前价 × 10,000（致敬 2010 年的比特币披萨日）
- Polymarket：预测市场，top-N 活跃市场的 Yes/No 概率
- 其他免费公开数据源可陆续扩展

所有函数都应该：
1. 幂等、无副作用（仅抓取，持久化由调用方负责）
2. 网络错误时返回 None（而不是抛异常），由调度器决定如何处理
3. 允许通过 HTTP_PROXY / HTTPS_PROXY 环境变量走代理
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from loguru import logger


# ── HTTP 代理 / 超时设置 ────────────────────────────────────────

def _get_proxies():
    from app.config import settings
    hp = settings.HTTP_PROXY
    hs = settings.HTTPS_PROXY
    if hp or hs:
        return {"http": hp or hs, "https": hs or hp}
    return None


# ── Pizza 指数（BTC 购买力指标）──────────────────────────────────

def fetch_pizza_index(btc_price_usd: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Bitcoin Pizza Index = BTC 当前价 × 10,000。

    参考：2010 年 5 月 22 日，Laszlo Hanyecz 用 10,000 BTC 换了两个披萨。
    该指数反映"当年那两个披萨今天值多少美元"，是流行的 BTC 购买力 meme 指标。

    参数:
        btc_price_usd: 如果调用方已经有 BTC 价格就传入，避免重复抓取。
                        若为 None 则从 CoinGecko public API 获取（无 key 免费）。

    返回:
        { symbol, price, btc_price, data_source, snapshot_at }
        失败时返回 None。
    """
    try:
        import requests as req
        if btc_price_usd is None or btc_price_usd <= 0:
            r = req.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "bitcoin", "vs_currencies": "usd"},
                proxies=_get_proxies(),
                timeout=8,
            )
            data = r.json()
            btc_price_usd = float(data.get("bitcoin", {}).get("usd") or 0)

        if not btc_price_usd or btc_price_usd <= 0:
            return None

        # 10,000 BTC 的当前市值（Pizza Index 的定义）
        pizza_value = btc_price_usd * 10_000.0

        return {
            "symbol": "PIZZA_INDEX",
            "asset": "PIZZA_INDEX",
            "timeframe": "1d",
            "price": round(pizza_value, 2),            # 10k BTC 美元价值
            "btc_price": round(btc_price_usd, 4),       # 参考 BTC 价
            "meta": {
                "description": "10,000 BTC 按当前 BTC 价格折算的美元价值；致敬 2010-05-22 Pizza Day",
                "multiplier": 10_000,
            },
            "data_source": "pizza_index_derived",
            "snapshot_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.debug(f"[IndicatorFetchers] pizza_index failed: {e}")
        return None


# ── Polymarket 热门预测市场 ──────────────────────────────────────

def fetch_polymarket_top_markets(limit: int = 10) -> Optional[List[Dict[str, Any]]]:
    """抓取 Polymarket 当前热门活跃市场的 Yes/No 概率。

    Polymarket Gamma API 免费、无需 key：
      https://gamma-api.polymarket.com/markets

    参数:
        limit: 返回前 N 个按成交量排序的活跃市场。

    返回:
        [{ symbol, question, yes_price, no_price, volume_usd, end_date, ... }, ...]
        失败返回 None。
    """
    try:
        import requests as req
        r = req.get(
            "https://gamma-api.polymarket.com/markets",
            params={
                "active": "true",
                "closed": "false",
                "order": "volumeNum",
                "ascending": "false",
                "limit": limit,
            },
            proxies=_get_proxies(),
            timeout=10,
        )
        r.raise_for_status()
        items = r.json() or []
        if not isinstance(items, list):
            return None

        out: List[Dict[str, Any]] = []
        for m in items[:limit]:
            try:
                question = m.get("question") or m.get("title") or "(untitled)"
                slug = m.get("slug") or m.get("conditionId") or ""
                # outcomePrices 通常是 ["0.62", "0.38"] 这种字符串数组
                op_raw = m.get("outcomePrices")
                if isinstance(op_raw, str):
                    import json
                    try:
                        op = json.loads(op_raw)
                    except Exception:
                        op = []
                else:
                    op = op_raw or []
                yes_price = None
                no_price = None
                if isinstance(op, list) and len(op) >= 2:
                    try:
                        yes_price = round(float(op[0]), 4)
                        no_price = round(float(op[1]), 4)
                    except Exception:
                        pass

                vol = m.get("volumeNum") or m.get("volume") or 0
                try:
                    volume_usd = float(vol)
                except Exception:
                    volume_usd = 0.0

                out.append({
                    "symbol": f"PM:{slug[:40]}" if slug else f"PM:market",
                    "question": question,
                    "yes_price": yes_price,
                    "no_price": no_price,
                    "volume_usd": round(volume_usd, 2),
                    "end_date": m.get("endDate") or m.get("end_date_iso"),
                    "url": f"https://polymarket.com/market/{slug}" if slug else None,
                    "snapshot_at": datetime.now(timezone.utc).isoformat(),
                })
            except Exception as e:
                logger.debug(f"[IndicatorFetchers] polymarket item parse failed: {e}")
                continue
        return out or None
    except Exception as e:
        logger.debug(f"[IndicatorFetchers] polymarket_top_markets failed: {e}")
        return None


# ── Fear & Greed Index（Alternative.me 免费 API）─────────────────

def fetch_fear_greed_index() -> Optional[Dict[str, Any]]:
    """Crypto Fear & Greed Index（Alternative.me 免费 API）。

    返回:
        { symbol, value (0-100), classification, snapshot_at }
        失败返回 None。
    """
    try:
        import requests as req
        r = req.get(
            "https://api.alternative.me/fng/",
            params={"limit": 1},
            proxies=_get_proxies(),
            timeout=8,
        )
        data = r.json()
        items = data.get("data") or []
        if not items:
            return None
        item = items[0]
        value = int(item.get("value", 0))
        classification = item.get("value_classification", "Neutral")
        return {
            "symbol": "FEAR_GREED",
            "asset": "FEAR_GREED",
            "timeframe": "1d",
            "price": float(value),                    # 0-100
            "meta": {
                "classification": classification,
                "description": "Crypto 恐惧贪婪指数（0=极度恐惧, 100=极度贪婪）",
            },
            "data_source": "alternative_me",
            "snapshot_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.debug(f"[IndicatorFetchers] fear_greed_index failed: {e}")
        return None


__all__ = [
    "fetch_pizza_index",
    "fetch_polymarket_top_markets",
    "fetch_fear_greed_index",
]
