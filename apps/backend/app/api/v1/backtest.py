"""简单回测 API — 基于 Yahoo Finance 历史数据
支持策略：MA 交叉、RSI 超买超卖、布林带突破、双均线
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()


# ── 策略定义 ──────────────────────────────────────────────
STRATEGIES = [
    {
        "id": "ma_crossover",
        "name": "均线交叉",
        "description": "短期均线上穿长期均线买入，下穿卖出",
        "params": [
            {"key": "fast_period", "label": "短期均线", "type": "int", "default": 10, "min": 3, "max": 100},
            {"key": "slow_period", "label": "长期均线", "type": "int", "default": 30, "min": 10, "max": 200},
        ],
    },
    {
        "id": "rsi_reversal",
        "name": "RSI 超买超卖",
        "description": "RSI 低于超卖线买入，高于超买线卖出",
        "params": [
            {"key": "rsi_period", "label": "RSI 周期", "type": "int", "default": 14, "min": 5, "max": 50},
            {"key": "oversold", "label": "超卖阈值", "type": "int", "default": 30, "min": 10, "max": 45},
            {"key": "overbought", "label": "超买阈值", "type": "int", "default": 70, "min": 55, "max": 90},
        ],
    },
    {
        "id": "bollinger_breakout",
        "name": "布林带突破",
        "description": "价格触及下轨买入，触及上轨卖出",
        "params": [
            {"key": "bb_period", "label": "布林带周期", "type": "int", "default": 20, "min": 10, "max": 50},
            {"key": "bb_std", "label": "标准差倍数", "type": "float", "default": 2.0, "min": 1.0, "max": 3.0},
        ],
    },
    {
        "id": "dual_ma_trend",
        "name": "双均线趋势",
        "description": "价格在均线上方且短均线>长均线时持有，否则空仓",
        "params": [
            {"key": "fast_period", "label": "短期均线", "type": "int", "default": 20, "min": 5, "max": 100},
            {"key": "slow_period", "label": "长期均线", "type": "int", "default": 60, "min": 20, "max": 250},
        ],
    },
]

# ── Yahoo Finance 资产映射 ────────────────────────────────
YFINANCE_MAP = {
    "BTC": "BTC-USD", "ETH": "ETH-USD", "BNB": "BNB-USD", "SOL": "SOL-USD",
    "SPX": "^GSPC", "NDX": "^NDX", "GOLD": "GC=F", "SILVER": "SI=F",
    "OIL": "CL=F", "DXY": "DX-Y.NYB", "VIX": "^VIX",
    "HSI": "^HSI", "CSI300": "000300.SS",
}


class BacktestRequest(BaseModel):
    asset: str = "BTC"
    strategy_id: str = "ma_crossover"
    params: dict = {}
    period: str = "1y"        # 1m, 3m, 6m, 1y, 2y, 5y
    initial_capital: float = 100000.0


def _fetch_history(asset: str, period: str) -> Optional[list]:
    """通过 yfinance 获取历史 OHLCV"""
    try:
        import yfinance as yf
        from app.config import settings
        import os

        proxy = getattr(settings, "HTTP_PROXY", "") or os.environ.get("HTTP_PROXY", "")
        ticker = YFINANCE_MAP.get(asset.upper(), asset)
        kwargs = {"period": period, "interval": "1d"}
        if proxy:
            kwargs["proxy"] = proxy

        df = yf.Ticker(ticker).history(**kwargs)
        if df.empty:
            return None
        records = []
        for idx, row in df.iterrows():
            records.append({
                "date": idx.strftime("%Y-%m-%d"),
                "open": round(float(row["Open"]), 4),
                "high": round(float(row["High"]), 4),
                "low": round(float(row["Low"]), 4),
                "close": round(float(row["Close"]), 4),
                "volume": float(row.get("Volume", 0)),
            })
        return records
    except Exception as e:
        logger.warning(f"yfinance fetch failed for {asset}: {e}")
        return None


def _calc_ma(closes: list, period: int) -> list:
    result = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        result[i] = sum(closes[i - period + 1:i + 1]) / period
    return result


def _calc_rsi(closes: list, period: int) -> list:
    result = [None] * len(closes)
    if len(closes) < period + 1:
        return result
    gains, losses = [], []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(closes)):
        if i > period:
            avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        rs = avg_gain / avg_loss if avg_loss > 0 else 100
        result[i] = 100 - (100 / (1 + rs))
    return result


def _calc_bollinger(closes: list, period: int, std_mult: float):
    import math
    upper = [None] * len(closes)
    lower = [None] * len(closes)
    middle = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1:i + 1]
        avg = sum(window) / period
        variance = sum((x - avg) ** 2 for x in window) / period
        std = math.sqrt(variance)
        middle[i] = avg
        upper[i] = avg + std_mult * std
        lower[i] = avg - std_mult * std
    return upper, middle, lower


def _run_strategy(records: list, strategy_id: str, params: dict, initial_capital: float) -> dict:
    """执行回测逻辑"""
    closes = [r["close"] for r in records]
    dates = [r["date"] for r in records]
    n = len(closes)
    if n < 30:
        return {"error": "数据不足，至少需要30个交易日"}

    # Generate signals: 1 = buy, -1 = sell, 0 = hold
    signals = [0] * n

    if strategy_id == "ma_crossover":
        fast = _calc_ma(closes, int(params.get("fast_period", 10)))
        slow = _calc_ma(closes, int(params.get("slow_period", 30)))
        for i in range(1, n):
            if fast[i] and slow[i] and fast[i - 1] and slow[i - 1]:
                if fast[i] > slow[i] and fast[i - 1] <= slow[i - 1]:
                    signals[i] = 1
                elif fast[i] < slow[i] and fast[i - 1] >= slow[i - 1]:
                    signals[i] = -1

    elif strategy_id == "rsi_reversal":
        rsi = _calc_rsi(closes, int(params.get("rsi_period", 14)))
        oversold = float(params.get("oversold", 30))
        overbought = float(params.get("overbought", 70))
        for i in range(1, n):
            if rsi[i] is not None and rsi[i - 1] is not None:
                if rsi[i] > oversold and rsi[i - 1] <= oversold:
                    signals[i] = 1
                elif rsi[i] < overbought and rsi[i - 1] >= overbought:
                    signals[i] = -1

    elif strategy_id == "bollinger_breakout":
        upper, middle, lower = _calc_bollinger(
            closes, int(params.get("bb_period", 20)), float(params.get("bb_std", 2.0))
        )
        for i in range(1, n):
            if lower[i] is not None:
                if closes[i] <= lower[i] and closes[i - 1] > lower[i - 1]:
                    signals[i] = 1
                elif closes[i] >= upper[i] and closes[i - 1] < upper[i - 1]:
                    signals[i] = -1

    elif strategy_id == "dual_ma_trend":
        fast = _calc_ma(closes, int(params.get("fast_period", 20)))
        slow = _calc_ma(closes, int(params.get("slow_period", 60)))
        for i in range(1, n):
            if fast[i] and slow[i]:
                if closes[i] > fast[i] and fast[i] > slow[i]:
                    signals[i] = 1  # enter/hold
                elif closes[i] < fast[i] or fast[i] < slow[i]:
                    signals[i] = -1  # exit

    # Simulate trades
    capital = initial_capital
    position = 0.0  # units held
    entry_price = 0.0
    trades: list[dict] = []
    equity_curve = []
    in_position = False

    for i in range(n):
        if signals[i] == 1 and not in_position:
            # Buy
            position = capital / closes[i]
            entry_price = closes[i]
            capital = 0
            in_position = True
            trades.append({"type": "buy", "date": dates[i], "price": closes[i], "units": round(position, 6)})
        elif signals[i] == -1 and in_position:
            # Sell
            capital = position * closes[i]
            pnl_pct = (closes[i] - entry_price) / entry_price * 100
            trades.append({"type": "sell", "date": dates[i], "price": closes[i], "pnl_pct": round(pnl_pct, 2)})
            position = 0
            in_position = False

        # Equity
        eq = capital + position * closes[i]
        equity_curve.append({"date": dates[i], "equity": round(eq, 2), "price": closes[i]})

    # Final equity
    final_equity = capital + position * closes[-1]

    # Buy & Hold comparison
    bh_units = initial_capital / closes[0]
    bh_final = bh_units * closes[-1]

    # Metrics
    total_return = (final_equity - initial_capital) / initial_capital * 100
    bh_return = (bh_final - initial_capital) / initial_capital * 100
    sell_trades = [t for t in trades if t["type"] == "sell"]
    win_trades = [t for t in sell_trades if t.get("pnl_pct", 0) > 0]
    win_rate = len(win_trades) / len(sell_trades) * 100 if sell_trades else 0

    # Max drawdown
    peak = initial_capital
    max_dd = 0
    for pt in equity_curve:
        if pt["equity"] > peak:
            peak = pt["equity"]
        dd = (peak - pt["equity"]) / peak * 100
        if dd > max_dd:
            max_dd = dd

    # Downsample equity curve for frontend (max 200 points)
    step = max(1, len(equity_curve) // 200)
    sampled_equity = equity_curve[::step]
    if equity_curve and sampled_equity[-1] != equity_curve[-1]:
        sampled_equity.append(equity_curve[-1])

    return {
        "metrics": {
            "total_return_pct": round(total_return, 2),
            "buy_hold_return_pct": round(bh_return, 2),
            "alpha_pct": round(total_return - bh_return, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "total_trades": len(sell_trades),
            "win_rate_pct": round(win_rate, 1),
            "final_equity": round(final_equity, 2),
            "initial_capital": initial_capital,
        },
        "trades": trades[-50:],  # last 50 trades
        "equity_curve": sampled_equity,
        "period_start": dates[0],
        "period_end": dates[-1],
        "data_points": n,
    }


@router.get("/strategies")
def list_strategies():
    """获取可用回测策略列表"""
    return {"strategies": STRATEGIES}


@router.post("/run")
async def run_backtest(req: BacktestRequest):
    """运行回测"""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    _pool = ThreadPoolExecutor(max_workers=2)

    def _do():
        records = _fetch_history(req.asset, req.period)
        if not records:
            return {"error": f"无法获取 {req.asset} 的历史数据"}
        return _run_strategy(records, req.strategy_id, req.params, req.initial_capital)

    loop = asyncio.get_event_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(_pool, _do),
            timeout=30.0
        )
    except asyncio.TimeoutError:
        return {"error": "回测超时，请缩短回测周期"}

    return {
        "asset": req.asset,
        "strategy_id": req.strategy_id,
        "params": req.params,
        **result,
    }


@router.get("/assets")
def list_backtest_assets():
    """获取支持回测的资产列表"""
    assets = [
        {"symbol": k, "yf_symbol": v, "category": "加密货币" if k in ("BTC", "ETH", "BNB", "SOL") else
         "美股指数" if k in ("SPX", "NDX") else
         "大宗商品" if k in ("GOLD", "SILVER", "OIL") else
         "其他"}
        for k, v in YFINANCE_MAP.items()
    ]
    return {"assets": assets}
