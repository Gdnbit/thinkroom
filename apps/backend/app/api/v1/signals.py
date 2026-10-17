"""新增市场状态和资金流 API 端点"""
from fastapi import APIRouter, Depends, Query
from typing import Optional, List
from sqlalchemy.orm import Session
import asyncio
from concurrent.futures import ThreadPoolExecutor

from app.dependencies import get_db
from app.mcps.market_data_mcp import MarketDataMCP
from app.mcps.flow_continuity_mcp import FlowContinuityMCP
from app.skills.indicator_interpretation_skill import IndicatorInterpretationSkill, IndicatorInput

from app.mcps.market_data_mcp import fetch_fred_macro

router = APIRouter()
_executor = ThreadPoolExecutor(max_workers=4)


async def _run_with_timeout(fn, *args, timeout: float = 6.0):
    """在线程池中运行同步函数，超时后返回 None"""
    from loguru import logger
    loop = asyncio.get_event_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(_executor, fn, *args),
            timeout=timeout
        )
    except asyncio.TimeoutError:
        logger.warning(f"[Signals] {fn.__name__ if hasattr(fn, '__name__') else fn} timed out after {timeout}s")
        return None
    except Exception as e:
        logger.warning(f"[Signals] {fn.__name__ if hasattr(fn, '__name__') else fn} failed: {e}")
        return None


@router.get("/macro")
async def get_macro_data(
    db: Session = Depends(get_db),
):
    """获取 FRED 宏观数据（利率/CPI/美债收益率），含12期历史趋势"""
    def _get():
        mcp = MarketDataMCP(db)
        return mcp.get_macro_data()
    resp = await _run_with_timeout(_get, timeout=15.0)
    if resp is None:
        return {}
    return resp.data or {}


@router.get("/market/{asset}")
async def get_market_state(
    asset: str,
    timeframe: str = Query("1d"),
    db: Session = Depends(get_db),
):
    """获取资产市场状态（live: Binance/Yahoo/CoinGecko，支持 1h/4h/1d/1w；mock: 数据库）"""
    def _get():
        mcp = MarketDataMCP(db)
        return mcp.get_market_state(asset=asset, timeframe=timeframe)
    resp = await _run_with_timeout(_get, timeout=15.0)
    if resp is None:
        return {}
    return resp.data or {}


@router.get("/market")
async def get_multi_market_state(
    assets: str = Query(..., description="逗号分隔的资产列表，如 BTC,SPX,GOLD"),
    timeframe: str = Query("1d"),
    db: Session = Depends(get_db),
):
    """批量获取多个资产市场状态"""
    asset_list = [a.strip() for a in assets.split(",")]
    def _get():
        mcp = MarketDataMCP(db)
        return mcp.get_multi_assets(assets=asset_list, timeframe=timeframe)
    resp = await _run_with_timeout(_get, timeout=15.0)
    if resp is None:
        return []
    return resp.data


@router.get("/flow/{asset}")
async def get_flow_signals(
    asset: str,
    signal_type: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """获取资产资金流信号"""
    def _get():
        mcp = FlowContinuityMCP(db)
        return mcp.get_flow_signals(asset=asset, signal_type=signal_type)
    resp = await _run_with_timeout(_get, timeout=12.0)
    if resp is None:
        return []
    return resp.data


@router.get("/flow/{asset}/summary")
async def get_flow_summary(
    asset: str,
    db: Session = Depends(get_db),
):
    """获取资产资金连续性摘要"""
    def _get():
        mcp = FlowContinuityMCP(db)
        return mcp.get_continuity_summary(asset=asset)
    resp = await _run_with_timeout(_get, timeout=12.0)
    if resp is None:
        return {}
    return resp.data


@router.get("/indicator/{asset}")
async def get_indicator_interpretation(
    asset: str,
    timeframe: str = Query("1d"),
    db: Session = Depends(get_db),
):
    """获取资产技术指标解读（纯规则，无 LLM 调用）"""
    def _get_market():
        mcp = MarketDataMCP(db)
        return mcp.get_market_state(asset=asset, timeframe=timeframe)
    market = await _run_with_timeout(_get_market, timeout=15.0)
    data = (market.data if market else None) or {}

    skill = IndicatorInterpretationSkill()
    result = skill.interpret(IndicatorInput(
        asset=asset.upper(),
        price=data.get("price"),
        price_change_1d=data.get("price_change_1d"),
        price_change_7d=data.get("price_change_7d"),
        rsi_14=data.get("rsi_14"),
        ma_20=data.get("ma_20"),
        ma_50=data.get("ma_50"),
        ma_200=data.get("ma_200"),
        volume_vs_avg=data.get("volume_vs_avg"),
        atr_14=data.get("atr_14"),
        trend_direction=data.get("trend_direction"),
        trend_strength=data.get("trend_strength"),
        volatility_state=data.get("volatility_state"),
        key_support_levels=data.get("key_support_levels") or [],
        key_resistance_levels=data.get("key_resistance_levels") or [],
    ))
    return {
        "asset": result.asset,
        "overall_signal": result.overall_signal,
        "signal_strength": result.signal_strength,
        "summary": result.summary,
        "interpretations": result.interpretations,
        "warnings": result.warnings,
        "suggested_bias": result.suggested_bias,
        "market_snapshot": data,
    }
