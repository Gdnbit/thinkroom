"""Market Data MCP — mock:DB / live:Binance+Yahoo+CoinGecko+FRED"""
from typing import Optional, List
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from loguru import logger
from app.mcps.base import MCPResponse
from app.schemas.market_state_card import MarketStateCard
from app.config import settings
from app.services import cache
from app.services import market_persist as persist

SOURCE_PACKAGE = "market_data_mcp"

BINANCE_SYMBOLS = {
    "BTC": "BTCUSDT", "ETH": "ETHUSDT", "BNB": "BNBUSDT",
    "SOL": "SOLUSDT", "XRP": "XRPUSDT", "DOGE": "DOGEUSDT",
}
YFINANCE_SYMBOLS = {
    "SPX": "^GSPC", "GOLD": "GC=F", "DXY": "DX-Y.NYB",
    "OIL": "CL=F", "SILVER": "SI=F", "NDX": "^NDX", "VIX": "^VIX",
    "HSI": "^HSI", "HSTECH": "^HSTECH",
    "CSI300": "000300.SS", "ZZ500": "000905.SS", "CYB": "399006.SZ",
}
COINGECKO_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum",
    "BNB": "binancecoin", "SOL": "solana",
}
FRED_SERIES = {
    "FEDFUNDS": "联邦基金利率",
    "DGS2": "2年期美债收益率",
    "DGS5": "5年期美债收益率",
    "DGS10": "10年期美债收益率",
    "CPIAUCSL": "美国CPI",
    "UNRATE": "美国失业率",
}
VALID_INTERVALS = ["1m", "5m", "15m", "1h", "4h", "1d", "1w"]

def _get_proxies():
    hp = settings.HTTP_PROXY
    hs = settings.HTTPS_PROXY
    if hp or hs:
        return {"http": hp or hs, "https": hs or hp}
    return None


def _calc_wilder_rsi(closes, period=14):
    """Wilder 平滑 RSI（指数移动平均），比简单 RSI 更稳定准确"""
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    # 初始值用简单平均
    gains = [max(d, 0) for d in deltas[:period]]
    losses = [max(-d, 0) for d in deltas[:period]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    # Wilder 指数平滑
    for d in deltas[period:]:
        avg_gain = (avg_gain * (period - 1) + max(d, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-d, 0)) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _calc_ema(values, period):
    """指数移动平均线 (EMA)"""
    if len(values) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = sum(values[:period]) / period  # SMA 初始化
    for v in values[period:]:
        ema = (v - ema) * multiplier + ema
    return ema


def _calc_ema_series(values, period):
    """返回完整 EMA 序列（用于 Bollinger 等依赖序列的指标）"""
    if len(values) < period:
        return []
    multiplier = 2 / (period + 1)
    ema = sum(values[:period]) / period
    result = [ema]
    for v in values[period:]:
        ema = (v - ema) * multiplier + ema
        result.append(ema)
    return result


def _calc_bollinger(closes, period=20, num_std=2):
    """布林带：中轨(SMA) + 上下轨(±N倍标准差)"""
    if len(closes) < period:
        return None, None, None, None
    window = closes[-period:]
    middle = sum(window) / period
    variance = sum((x - middle) ** 2 for x in window) / period
    std = variance ** 0.5
    upper = middle + num_std * std
    lower = middle - num_std * std
    # 带宽百分比（衡量波动收缩/扩张）
    bandwidth = (upper - lower) / middle if middle > 0 else 0
    return round(upper, 4), round(middle, 4), round(lower, 4), round(bandwidth, 4)


def _calc_atr(highs, lows, closes, period=14):
    """Wilder 平滑 ATR"""
    if len(closes) < period + 1:
        return None
    trs = [
        max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        for i in range(1, len(closes))
    ]
    # Wilder 平滑
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def _assess_volatility(atr, price, rsi, bb_bandwidth):
    """综合评估波动性状态"""
    if not price or price == 0:
        return "normal"
    # ATR 占价格比例
    atr_pct = (atr / price * 100) if atr else 0
    # RSI 极端度
    rsi_extreme = abs(rsi - 50) > 20 if rsi else False
    # Bollinger 带宽
    bb_wide = bb_bandwidth > 0.08 if bb_bandwidth else False

    score = 0
    if atr_pct > 3:
        score += 2
    elif atr_pct > 1.5:
        score += 1
    if rsi_extreme:
        score += 1
    if bb_wide:
        score += 1
    if score >= 3:
        return "high"
    elif score <= 0:
        return "low"
    return "normal"


def _build_kline_metrics(klines):
    """从 K 线数据计算完整技术指标集"""
    if not klines:
        return {}
    closes = [float(k[4]) for k in klines]
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    volumes = [float(k[5]) for k in klines]

    # Wilder RSI
    rsi = _calc_wilder_rsi(closes, 14)

    # Wilder ATR
    atr = _calc_atr(highs, lows, closes, 14)

    # SMA
    ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else None
    ma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else None
    ma200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else None

    # EMA
    ema12 = _calc_ema(closes, 12)
    ema26 = _calc_ema(closes, 26)
    ema50 = _calc_ema(closes, 50)

    # Bollinger Bands
    bb_upper, bb_middle, bb_lower, bb_bandwidth = _calc_bollinger(closes, 20, 2)

    # MACD（EMA12 - EMA26）
    macd_line = round(ema12 - ema26, 4) if ema12 and ema26 else None
    # MACD 信号线需要完整 EMA 序列
    macd_signal = None
    macd_histogram = None
    if ema12 and ema26 and len(closes) >= 26:
        ema12_series = _calc_ema_series(closes, 12)
        ema26_series = _calc_ema_series(closes, 26)
        min_len = min(len(ema12_series), len(ema26_series))
        if min_len > 0:
            offset = len(ema12_series) - min_len
            macd_series = [ema12_series[offset + i] - ema26_series[i] for i in range(min_len)]
            if len(macd_series) >= 9:
                macd_signal = _calc_ema(macd_series, 9)
                if macd_signal is not None and macd_line is not None:
                    macd_histogram = round(macd_line - macd_signal, 4)
                    macd_signal = round(macd_signal, 4)

    # 成交量分析
    av = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else None
    vr = round(volumes[-1] / av, 2) if av and volumes and volumes[-1] else None

    # 趋势判定（EMA 交叉优先，SMA 兜底）
    if ema12 and ema26:
        trend = "uptrend" if ema12 > ema26 else "downtrend"
    elif ma20 and ma50:
        trend = "uptrend" if ma20 > ma50 else ("downtrend" if ma20 < ma50 else "sideways")
    else:
        trend = "sideways"

    # 波动性综合评估
    vol_state = _assess_volatility(atr, closes[-1] if closes else None, rsi, bb_bandwidth)

    # 价格在布林带中的位置（%B）
    bb_position = None
    if bb_upper and bb_lower and bb_upper != bb_lower and closes:
        bb_position = round((closes[-1] - bb_lower) / (bb_upper - bb_lower), 3)

    return {
        "rsi_14": round(rsi, 2) if rsi else None,
        "atr_14": round(atr, 4) if atr else None,
        "ma_20": round(ma20, 4) if ma20 else None,
        "ma_50": round(ma50, 4) if ma50 else None,
        "ma_200": round(ma200, 4) if ma200 else None,
        "ema_12": round(ema12, 4) if ema12 else None,
        "ema_26": round(ema26, 4) if ema26 else None,
        "ema_50": round(ema50, 4) if ema50 else None,
        "macd_line": macd_line,
        "macd_signal": macd_signal,
        "macd_histogram": macd_histogram,
        "bb_upper": bb_upper,
        "bb_middle": bb_middle,
        "bb_lower": bb_lower,
        "bb_bandwidth": bb_bandwidth,
        "bb_position": bb_position,
        "trend_direction": trend,
        "volume_vs_avg": vr,
        "volatility_state": vol_state,
    }


def _fetch_binance(asset, interval="1d", limit=200):
    try:
        import requests as req
        sym = BINANCE_SYMBOLS.get(asset.upper())
        if not sym or interval not in VALID_INTERVALS:
            return None
        px = _get_proxies()
        t = req.get(f"https://api.binance.com/api/v3/ticker/24hr?symbol={sym}",
                    timeout=5, proxies=px).json()
        price = float(t.get("lastPrice", 0))
        c1d = float(t.get("priceChangePercent", 0)) / 100
        ks = req.get(
            f"https://api.binance.com/api/v3/klines?symbol={sym}&interval={interval}&limit={limit}",
            timeout=5, proxies=px).json()
        m = _build_kline_metrics(ks)
        c7d = None
        if interval == "1d" and len(ks) >= 7:
            c = [float(k[4]) for k in ks]
            c7d = (c[-1] - c[-7]) / c[-7] if c[-7] > 0 else None
        return {
            "asset": asset.upper(), "timeframe": interval,
            "price": round(price, 4), "price_change_1d": round(c1d, 4),
            "price_change_7d": round(c7d, 4) if c7d else None, "price_change_30d": None,
            "trend_direction": m.get("trend_direction", "sideways"), "trend_strength": None,
            "rsi_14": m.get("rsi_14"), "atr_14": m.get("atr_14"),
            "ma_20": m.get("ma_20"), "ma_50": m.get("ma_50"), "ma_200": m.get("ma_200"),
            "volume_vs_avg": m.get("volume_vs_avg"),
            "volatility_state": m.get("volatility_state", "normal"),
            "structure_notes": None, "key_support_levels": [], "key_resistance_levels": [],
            "data_source": "binance_public_api",
            "snapshot_at": datetime.now(timezone.utc).isoformat(),
            "klines_raw": [{"t": k[0], "o": k[1], "h": k[2], "l": k[3], "c": k[4], "v": k[5]}
                           for k in ks[-100:]],
        }
    except Exception:
        return None


def _fetch_yfinance(asset, interval="1d"):
    try:
        import requests as req
        sym = YFINANCE_SYMBOLS.get(asset.upper())
        if not sym:
            return None
        pm = {"1h": "5d", "4h": "60d", "1d": "200d", "1w": "5y"}
        im = {"1h": "1h", "4h": "1h", "1d": "1d", "1w": "1wk"}
        px = _get_proxies()
        r = req.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
            params={"interval": im.get(interval, "1d"), "range": pm.get(interval, "200d")},
            headers={"User-Agent": "Mozilla/5.0"}, proxies=px, timeout=10)
        res = r.json()["chart"]["result"][0]
        q = res["indicators"]["quote"][0]
        ts = res.get("timestamp", [])
        cl = q.get("close", [])
        hi = q.get("high", [])
        lo = q.get("low", [])
        vo = q.get("volume", [])
        op = q.get("open", [])
        closes = [c for c in cl if c is not None]
        highs = [h for h in hi if h is not None]
        lows = [l for l in lo if l is not None]
        if not closes:
            return None
        n = min(len(closes), len(highs), len(lows))
        pk = [[0, 0, highs[-n+i], lows[-n+i], closes[-n+i],
               (vo[-(n-i)] or 0) if vo and len(vo) > n-i else 0] for i in range(n)]
        m = _build_kline_metrics(pk)
        c1d = (closes[-1]-closes[-2])/closes[-2] if len(closes) >= 2 else 0
        c7d = (closes[-1]-closes[-7])/closes[-7] if len(closes) >= 7 else None
        raw = []
        for i in range(max(0, len(ts)-100), len(ts)):
            if i < len(cl) and cl[i] is not None:
                raw.append({"t": ts[i]*1000,
                            "o": (op[i] or cl[i]) if op and i < len(op) else cl[i],
                            "h": (hi[i] or cl[i]) if hi and i < len(hi) else cl[i],
                            "l": (lo[i] or cl[i]) if lo and i < len(lo) else cl[i],
                            "c": cl[i], "v": (vo[i] or 0) if vo and i < len(vo) else 0})
        return {
            "asset": asset.upper(), "timeframe": interval,
            "price": round(closes[-1], 4), "price_change_1d": round(c1d, 4),
            "price_change_7d": round(c7d, 4) if c7d else None, "price_change_30d": None,
            "trend_direction": m.get("trend_direction", "sideways"), "trend_strength": None,
            "rsi_14": m.get("rsi_14"), "atr_14": m.get("atr_14"),
            "ma_20": m.get("ma_20"), "ma_50": m.get("ma_50"), "ma_200": m.get("ma_200"),
            "volume_vs_avg": m.get("volume_vs_avg"),
            "volatility_state": m.get("volatility_state", "normal"),
            "structure_notes": None, "key_support_levels": [], "key_resistance_levels": [],
            "data_source": "yahoo_finance",
            "snapshot_at": datetime.now(timezone.utc).isoformat(),
            "klines_raw": raw,
        }
    except Exception as e:
        logger.warning(f"[MarketDataMCP] _fetch_yfinance({asset}) failed: {e}")
        return None


def _fetch_coingecko(asset):
    try:
        import requests as req
        key = settings.COINGECKO_API_KEY
        if not key:
            return None
        cid = COINGECKO_IDS.get(asset.upper())
        if not cid:
            return None
        px = _get_proxies()
        r = req.get(f"https://api.coingecko.com/api/v3/coins/{cid}",
                    params={"localization": "false", "tickers": "false",
                            "community_data": "false", "developer_data": "false"},
                    headers={"x-cg-demo-api-key": key}, proxies=px, timeout=10)
        md = r.json().get("market_data", {})
        price = md.get("current_price", {}).get("usd")
        if not price:
            return None
        c1d = (md.get("price_change_percentage_24h") or 0) / 100
        c7d = (md.get("price_change_percentage_7d") or 0) / 100
        mc = md.get("market_cap", {}).get("usd")
        return {
            "asset": asset.upper(), "timeframe": "1d", "price": round(price, 4),
            "price_change_1d": round(c1d, 6), "price_change_7d": round(c7d, 6),
            "price_change_30d": None,
            "trend_direction": "uptrend" if c1d > 0 else "downtrend",
            "trend_strength": None, "rsi_14": None, "atr_14": None,
            "ma_20": None, "ma_50": None, "ma_200": None,
            "volume_vs_avg": None, "volatility_state": "normal",
            "structure_notes": f"market_cap:{mc}" if mc else None,
            "key_support_levels": [], "key_resistance_levels": [],
            "data_source": "coingecko",
            "snapshot_at": datetime.now(timezone.utc).isoformat(),
            "klines_raw": [],
        }
    except Exception:
        return None


def fetch_fred_macro():
    try:
        # FRED 数据：每日只调用一次（对齐 UTC 午夜）
        from app.services.api_quota import allow_fred, mark_fred_called
        cache_key = cache.make_key("fred_macro")
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
        # 配额检查：今天已调用过则直接返回 None（上层用 DB 存量）
        if not allow_fred():
            logger.debug("[MarketDataMCP] FRED quota exceeded today, using DB cache")
            return None
        import requests as req
        key = settings.FRED_API_KEY
        if not key:
            return None
        px = _get_proxies()
        out = {}
        for sid, label in FRED_SERIES.items():
            r = req.get("https://api.stlouisfed.org/fred/series/observations",
                        params={"series_id": sid, "api_key": key,
                                "file_type": "json", "sort_order": "desc", "limit": 12},
                        proxies=px, timeout=10)
            obs = r.json().get("observations", [])
            if obs:
                val = obs[0].get("value", ".")
                hist = [{"date": o["date"], "value": float(o["value"])}
                        for o in reversed(obs) if o.get("value", ".") != "."]
                out[sid] = {"label": label,
                            "value": float(val) if val != "." else None,
                            "date": obs[0].get("date"), "history": hist}
        result = out if out else None
        if result:
            cache.set(cache_key, result, ttl=3600)
            mark_fred_called()  # 标记今日已调用
        return result
    except Exception:
        return None


class MarketDataMCP:
    def __init__(self, db: Session):
        self.db = db
        self.use_mock = settings.USE_MOCK_MARKET_DATA

    def get_market_state(self, asset: str, timeframe: str = "1d"):
        try:
            if not self.use_mock:
                from app.services.api_quota import allow_market, mark_market_called
                # 1. Redis 缓存（TTL 5分钟）
                cache_key = cache.make_key("market", asset.upper(), timeframe)
                cached = cache.get(cache_key)
                if cached is not None:
                    return MCPResponse.ok(data=cached, source_package=SOURCE_PACKAGE)

                # 2. DB 新鲜数据（5分钟内）
                fresh = persist.get_fresh_market_state(self.db, asset, timeframe, max_age_minutes=5)
                if fresh:
                    cache.set(cache_key, fresh, ttl=300)
                    return MCPResponse.ok(data=fresh, source_package=SOURCE_PACKAGE)

                # 3. 配额检查：5分钟冷却内禁止重复调用外部 API
                if not allow_market(asset, timeframe):
                    logger.debug(f"[MarketDataMCP] quota cooldown for {asset}/{timeframe}, using DB latest")
                    row = (
                        self.db.query(MarketStateCard)
                        .filter(MarketStateCard.asset == asset.upper(),
                                MarketStateCard.timeframe == timeframe)
                        .order_by(MarketStateCard.snapshot_at.desc()).first()
                    )
                    if row:
                        data = persist._market_row_to_dict(row)
                        data["data_source"] = (row.data_source or "") + "[db_fallback]"
                        cache.set(cache_key, data, ttl=300)
                        return MCPResponse.ok(data=data, source_package=SOURCE_PACKAGE)
                    return MCPResponse.ok(data=None, source_package=SOURCE_PACKAGE)

                # 4. 外部 API 调用
                data = None
                au = asset.upper()
                if au in BINANCE_SYMBOLS:
                    data = _fetch_binance(au, interval=timeframe)
                    if not data:
                        data = _fetch_coingecko(au)
                elif au in YFINANCE_SYMBOLS:
                    data = _fetch_yfinance(au, interval=timeframe)
                elif au in COINGECKO_IDS:
                    data = _fetch_coingecko(au)
                if data:
                    # 5. 持久化到 DB + 写入缓存 + 标记配额
                    persist.persist_market_state(self.db, data)
                    cache.set(cache_key, data, ttl=300)
                    mark_market_called(asset, timeframe)
                    return MCPResponse.ok(data=data, source_package=SOURCE_PACKAGE)
            row = (
                self.db.query(MarketStateCard)
                .filter(MarketStateCard.asset == asset.upper(),
                        MarketStateCard.timeframe == timeframe,
                        MarketStateCard.status == "active")
                .order_by(MarketStateCard.snapshot_at.desc()).first()
            )
            data = None
            if row:
                data = {
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
                }
            if self.use_mock:
                return MCPResponse.mock(data=data, source_package=SOURCE_PACKAGE)
            return MCPResponse.ok(data=data, source_package=SOURCE_PACKAGE)
        except Exception as e:
            return MCPResponse.fail(str(e), SOURCE_PACKAGE)

    def get_multi_assets(self, assets, timeframe="1d"):
        results = []
        for asset in assets:
            resp = self.get_market_state(asset, timeframe)
            if resp.success and resp.data:
                results.append(resp.data)
        if self.use_mock:
            return MCPResponse.mock(data=results, source_package=SOURCE_PACKAGE)
        return MCPResponse.ok(data=results, source_package=SOURCE_PACKAGE)

    def get_multi_timeframe(self, asset: str, timeframes: List[str] = None):
        """多时间框架分析：同时获取多个时间周期的技术指标，识别趋势一致性。

        Args:
            asset: 资产代码
            timeframes: 时间周期列表，默认 ["1h", "4h", "1d"]

        Returns:
            MCPResponse 包含各时间框架数据 + MTF 综合判断
        """
        if not timeframes:
            timeframes = ["1h", "4h", "1d"]
        try:
            tf_data = {}
            for tf in timeframes:
                resp = self.get_market_state(asset, tf)
                if resp.success and resp.data:
                    tf_data[tf] = resp.data

            if not tf_data:
                return MCPResponse.ok(data=None, source_package=SOURCE_PACKAGE)

            # MTF 趋势一致性分析
            trends = {tf: d.get("trend_direction", "sideways") for tf, d in tf_data.items()}
            unique_trends = set(trends.values())

            if len(unique_trends) == 1 and "sideways" not in unique_trends:
                mtf_alignment = "strong"
                mtf_direction = unique_trends.pop()
            elif len(unique_trends) <= 2 and "sideways" in unique_trends:
                mtf_alignment = "moderate"
                mtf_direction = [t for t in unique_trends if t != "sideways"][0] if len(unique_trends) > 1 else "sideways"
            else:
                mtf_alignment = "weak"
                # 多数表决
                from collections import Counter
                cnt = Counter(trends.values())
                mtf_direction = cnt.most_common(1)[0][0]

            result = {
                "asset": asset.upper(),
                "timeframes": tf_data,
                "mtf_analysis": {
                    "trends_by_tf": trends,
                    "alignment": mtf_alignment,
                    "dominant_direction": mtf_direction,
                    "description": f"多周期趋势{'一致' if mtf_alignment == 'strong' else '分歧'}（{mtf_direction}），对齐强度={mtf_alignment}",
                },
            }
            return MCPResponse.ok(data=result, source_package=SOURCE_PACKAGE)
        except Exception as e:
            return MCPResponse.fail(str(e), SOURCE_PACKAGE)

    def get_supported_assets(self):
        return list(BINANCE_SYMBOLS.keys()) + list(YFINANCE_SYMBOLS.keys())

    def get_macro_data(self):
        try:
            if not self.use_mock and settings.FRED_API_KEY:
                data = fetch_fred_macro()
                if data:
                    # 知识库自动充实：FRED 宏观数据入库
                    try:
                        from app.services.knowledge_enrichment import enrich_from_api, format_macro_for_enrichment
                        text = format_macro_for_enrichment(data)
                        enrich_from_api(
                            db=self.db, source_name="fred_macro",
                            title="FRED 宏观数据", text_content=text,
                            theme_hint="宏观 利率 美联储 CPI",
                        )
                    except Exception:
                        pass
                    return MCPResponse.ok(data=data, source_package=SOURCE_PACKAGE)
            return MCPResponse.mock(
                data={
                    "FEDFUNDS": {"label": "Fed Funds Rate", "value": 5.33, "date": "2024-01-01", "history": []},
                    "DGS2":    {"label": "2Y Treasury",    "value": 4.60, "date": "2024-01-01", "history": []},
                    "DGS5":    {"label": "5Y Treasury",    "value": 4.35, "date": "2024-01-01", "history": []},
                    "DGS10":   {"label": "10Y Treasury",   "value": 4.20, "date": "2024-01-01", "history": []},
                    "CPIAUCSL":{"label": "CPI",            "value": 3.1,  "date": "2024-01-01", "history": []},
                },
                source_package=SOURCE_PACKAGE)
        except Exception as e:
            return MCPResponse.fail(str(e), SOURCE_PACKAGE)
