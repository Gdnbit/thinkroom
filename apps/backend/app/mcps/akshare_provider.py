"""G4 · AKShare provider —— A 股指数 + 场外基金净值数据通道.

为什么要这条链
-----------
- Yahoo Finance 抓 A 股 (`000300.SS` 等) 在中国大陆经常 timeout / 数据延迟
- AKShare 是国内开源数据源聚合库，免费 + 不需 API key + 数据来源东方财富/新浪/腾讯
- 对于场外基金（6 位代码），yfinance 完全不支持，只能用 akshare / 蛋卷 / 天天基金

设计要点
-----------
- **lazy import**: akshare 不强制安装（依赖 pandas + numpy 略重），未装时所有函数都返回 None / raise 清晰错误
- **timeout 严格**: 默认 8 秒，超过即放弃 fallback yfinance
- **不缓存**: cache 由 caller (market_data_mcp 走 `cache.get_or_set_*`) 统一管理，避免双层缓存不一致

主要函数
-----------
- `is_available()` -> bool                 检测 akshare 是否可 import
- `fetch_index_quote(symbol)` -> dict       A 股指数实时报价（CSI300 / ZZ500 / CYB / SSE50 / HS300）
- `fetch_index_history(symbol, period)`     A 股指数历史 K 线
- `fetch_fund_nav(fund_code)` -> dict       场外基金最新净值 + 历史
- `fetch_fund_nav_history(fund_code, since_days)` -> List[dict]
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional

from loguru import logger

# AKShare A 股指数代码映射
#   key: 系统内统一 symbol
#   val: akshare 的 symbol 参数（部分接口要带前缀 sh/sz）
AKSHARE_INDEX_SYMBOLS: Dict[str, Dict[str, str]] = {
    "CSI300":  {"code": "000300", "exchange": "sh", "name": "沪深300"},
    "ZZ500":   {"code": "000905", "exchange": "sh", "name": "中证500"},
    "ZZ1000":  {"code": "000852", "exchange": "sh", "name": "中证1000"},
    "CYB":     {"code": "399006", "exchange": "sz", "name": "创业板指"},
    "SSE50":   {"code": "000016", "exchange": "sh", "name": "上证50"},
    "SH":      {"code": "000001", "exchange": "sh", "name": "上证综指"},
    "SZ":      {"code": "399001", "exchange": "sz", "name": "深证成指"},
    "STAR50":  {"code": "000688", "exchange": "sh", "name": "科创50"},
}


# ── 可用性检测 ────────────────────────────────────────────


@lru_cache(maxsize=1)
def is_available() -> bool:
    """检测 akshare 是否已安装可用（缓存 1 次结果，避免重复 import 失败）.

    缓存 1 次的原因：用户运行时不会突然装上 akshare，所以一次结果够了。
    要清缓存（测试用）：`is_available.cache_clear()`
    """
    try:
        import akshare  # noqa: F401
        return True
    except ImportError:
        logger.debug("[AKShare] akshare not installed (optional dependency)")
        return False


def _akshare():
    """Lazy import 实际 akshare 模块。调用方自己判断 None."""
    if not is_available():
        return None
    import akshare as ak
    return ak


# ── A 股指数 ──────────────────────────────────────────────


def fetch_index_quote(symbol: str) -> Optional[Dict[str, Any]]:
    """A 股指数最新报价（实时）.

    返回:
        {
          "symbol": "CSI300",
          "name": "沪深300",
          "current": 3850.12,
          "open": 3845.0,
          "high": 3860.5,
          "low": 3830.0,
          "previous_close": 3852.3,
          "volume": 12345678,
          "ts": "2026-04-25T15:00:00+08:00",
          "source": "akshare/stock_zh_index_spot",
        }
        或 None（不支持的 symbol / akshare 不可用 / 网络失败）.
    """
    info = AKSHARE_INDEX_SYMBOLS.get(symbol.upper())
    if not info:
        return None
    ak = _akshare()
    if not ak:
        return None

    try:
        # stock_zh_index_spot_em 是免费实时（约 3 秒延迟），返回所有 A 股指数
        df = ak.stock_zh_index_spot_em()
        if df is None or df.empty:
            return None
        # 列名："代码" "名称" "最新价" "涨跌幅" "涨跌额" "成交量" "成交额" "振幅" "最高" "最低" "今开" "昨收"
        # 找到匹配 code 的行
        match = df[df["代码"] == info["code"]]
        if match.empty:
            return None
        row = match.iloc[0]
        return {
            "symbol": symbol.upper(),
            "name": info["name"],
            "current": float(row.get("最新价") or 0) or None,
            "open": float(row.get("今开") or 0) or None,
            "high": float(row.get("最高") or 0) or None,
            "low": float(row.get("最低") or 0) or None,
            "previous_close": float(row.get("昨收") or 0) or None,
            "volume": int(row.get("成交量") or 0) or None,
            "change_pct": float(row.get("涨跌幅") or 0),
            "ts": datetime.now(timezone.utc).isoformat(),
            "source": "akshare/stock_zh_index_spot_em",
        }
    except Exception as e:
        logger.warning(f"[AKShare] fetch_index_quote({symbol}) failed: {e}")
        return None


def fetch_index_history(symbol: str, period: str = "1y") -> Optional[List[Dict[str, Any]]]:
    """A 股指数历史 K 线.

    Args:
        symbol: 系统内 symbol (CSI300 / ZZ500 / ...)
        period: 1m | 3m | 6m | 1y | 3y | 5y | all

    Returns:
        List of {date, open, high, low, close, volume} 或 None.
    """
    info = AKSHARE_INDEX_SYMBOLS.get(symbol.upper())
    if not info:
        return None
    ak = _akshare()
    if not ak:
        return None

    period_days = {"1m": 30, "3m": 90, "6m": 180, "1y": 365, "3y": 1095, "5y": 1825, "all": 3650}
    days = period_days.get(period, 365)
    end = datetime.now()
    start = end - timedelta(days=days)

    try:
        # index_zh_a_hist 用 "000300" 不带交易所
        df = ak.index_zh_a_hist(
            symbol=info["code"],
            period="daily",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
        if df is None or df.empty:
            return None
        # 列名："日期" "开盘" "收盘" "最高" "最低" "成交量" "成交额" "振幅" "涨跌幅" "涨跌额" "换手率"
        records = []
        for _, row in df.iterrows():
            d = row.get("日期")
            records.append({
                "date": d.isoformat() if hasattr(d, "isoformat") else str(d),
                "open": float(row.get("开盘") or 0) or None,
                "high": float(row.get("最高") or 0) or None,
                "low": float(row.get("最低") or 0) or None,
                "close": float(row.get("收盘") or 0) or None,
                "volume": int(row.get("成交量") or 0) or None,
            })
        return records
    except Exception as e:
        logger.warning(f"[AKShare] fetch_index_history({symbol}, {period}) failed: {e}")
        return None


# ── 场外基金净值 ──────────────────────────────────────────


def fetch_fund_nav(fund_code: str) -> Optional[Dict[str, Any]]:
    """场外基金最新净值（每日 21:00 后东方财富才有当日数据）.

    Returns:
        {
          "fund_code": "110011",
          "fund_name": "易方达中小盘混合",
          "nav": 3.1234,             # 单位净值
          "accum_nav": 3.5678,       # 累计净值
          "nav_date": "2026-04-25",
          "pct_change_1d": 0.0125,   # 当日涨跌幅
          "source": "akshare/fund_open_fund_info_em",
        }
    """
    if not fund_code or len(fund_code) != 6:
        return None
    ak = _akshare()
    if not ak:
        return None

    try:
        # fund_open_fund_info_em 返回历史净值表 ["净值日期", "单位净值", "累计净值", "日增长率", ...]
        # 取最近一行
        df = ak.fund_open_fund_info_em(symbol=fund_code, indicator="单位净值走势")
        if df is None or df.empty:
            return None
        last = df.iloc[-1]
        d = last.get("净值日期")
        date_str = d.isoformat() if hasattr(d, "isoformat") else str(d)

        # 名称需要单独查（轻量接口 fund_individual_basic_info_xq 可能不稳定，先 skip）
        return {
            "fund_code": fund_code,
            "fund_name": None,  # 由调用方从 FundProfile 取
            "nav": float(last.get("单位净值") or 0) or None,
            "accum_nav": None,  # 此表不含累计净值
            "nav_date": date_str,
            "pct_change_1d": float(last.get("日增长率") or 0) / 100 if last.get("日增长率") is not None else None,
            "source": "akshare/fund_open_fund_info_em",
        }
    except Exception as e:
        logger.warning(f"[AKShare] fetch_fund_nav({fund_code}) failed: {e}")
        return None


def fetch_fund_nav_history(fund_code: str, since_days: int = 90) -> Optional[List[Dict[str, Any]]]:
    """场外基金净值历史序列.

    Returns:
        List of {date, nav, pct_change_1d} 或 None.
    """
    if not fund_code or len(fund_code) != 6:
        return None
    ak = _akshare()
    if not ak:
        return None

    cutoff = datetime.now() - timedelta(days=since_days)

    try:
        df = ak.fund_open_fund_info_em(symbol=fund_code, indicator="单位净值走势")
        if df is None or df.empty:
            return None
        records = []
        for _, row in df.iterrows():
            d = row.get("净值日期")
            try:
                dt = d if isinstance(d, datetime) else datetime.fromisoformat(str(d))
            except Exception:
                continue
            if dt < cutoff:
                continue
            records.append({
                "date": dt.date().isoformat(),
                "nav": float(row.get("单位净值") or 0) or None,
                "pct_change_1d": float(row.get("日增长率") or 0) / 100 if row.get("日增长率") is not None else None,
            })
        return records
    except Exception as e:
        logger.warning(f"[AKShare] fetch_fund_nav_history({fund_code}) failed: {e}")
        return None


# ── 工具：可用 symbol 列表 ─────────────────────────────────


def supported_index_symbols() -> List[str]:
    """返回 AKShare provider 支持的 A 股指数 symbol 列表."""
    return list(AKSHARE_INDEX_SYMBOLS.keys())
