"""A1 · 资产识别与别名统一（单一真源）

合并之前散落在以下位置的重复映射：
    - app/skills/intent_recognition_skill.py::ASSET_KEYWORDS
    - app/skills/research_plan_skill.py 内部 mapping
    - app/api/v1/briefings.py::_ASSET_KEYWORDS（两处）
    - app/services/semantic_search.py::_ASSET_ALIASES（检索扩展用，不同语义）

本模块为所有从自由文本中识别资产的调用点提供**单一入口**：
    canonical_symbol = detect_asset(text)
    symbols = detect_assets(text)  # 返回所有匹配
"""
from __future__ import annotations

from typing import List, Optional


# ── canonical 符号表：关键词（大小写不敏感） → 规范符号 ───────────
# 关键词 key 统一小写，输入会被 .lower() 后匹配
ASSET_ALIASES: dict[str, str] = {
    # 加密货币
    "btc": "BTC", "bitcoin": "BTC", "比特币": "BTC",
    "eth": "ETH", "ethereum": "ETH", "以太坊": "ETH", "以太": "ETH",
    "sol": "SOL", "solana": "SOL", "索拉纳": "SOL",
    "bnb": "BNB", "binance coin": "BNB", "币安币": "BNB",
    "xrp": "XRP", "ripple": "XRP", "瑞波": "XRP",
    # 贵金属
    "gold": "GOLD", "黄金": "GOLD", "xau": "GOLD",
    "silver": "SILVER", "白银": "SILVER", "xag": "SILVER",
    # 能源
    "oil": "OIL", "crude": "OIL", "wti": "OIL", "brent": "OIL",
    "石油": "OIL", "原油": "OIL",
    # 美股指数
    "spx": "SPX", "s&p": "SPX", "sp500": "SPX", "标普": "SPX", "标普500": "SPX", "美股": "SPX",
    "ndx": "NDX", "nasdaq": "NDX", "纳指": "NDX", "纳斯达克": "NDX",
    "dow": "DJI", "道琼斯": "DJI",
    # 宏观
    "dxy": "DXY", "美元指数": "DXY",
    "vix": "VIX", "恐慌指数": "VIX", "恐慌": "VIX",
}


# ── 规范符号 → 展示名（用于 UI 或 prompt）────────────────────────
SYMBOL_DISPLAY: dict[str, str] = {
    "BTC": "比特币 (BTC)",
    "ETH": "以太坊 (ETH)",
    "SOL": "Solana (SOL)",
    "BNB": "币安币 (BNB)",
    "XRP": "瑞波 (XRP)",
    "GOLD": "黄金 (GOLD)",
    "SILVER": "白银 (SILVER)",
    "OIL": "原油 (OIL)",
    "SPX": "标普 500 (SPX)",
    "NDX": "纳斯达克 (NDX)",
    "DJI": "道琼斯 (DJI)",
    "DXY": "美元指数 (DXY)",
    "VIX": "恐慌指数 (VIX)",
}


# B5: 判断字符是否属于 ASCII 单词内部（字母/数字/下划线）
def _is_word_char(ch: str) -> bool:
    return ch.isascii() and (ch.isalnum() or ch == "_")


def _is_ascii_key(kw: str) -> bool:
    """判断关键词是否全 ASCII（中文别名不需要词边界检查）"""
    return all(ord(c) < 128 for c in kw)


def _boundary_ok(text: str, start: int, end: int, kw: str) -> bool:
    """B5: ASCII 关键词必须处于单词边界，避免 solve→SOL / method→ETH 误判。

    CJK 关键词（中文/日文）跳过边界检查（CJK 不适用词边界概念）。
    """
    if not _is_ascii_key(kw):
        return True
    # 前一字符：越界或非字母数字 → 边界 OK
    left_ok = start == 0 or not _is_word_char(text[start - 1])
    # 后一字符：越界或非字母数字 → 边界 OK
    right_ok = end >= len(text) or not _is_word_char(text[end])
    return left_ok and right_ok


def detect_assets(text: Optional[str]) -> List[str]:
    """从自由文本中提取所有匹配的规范资产符号。

    顺序 = 关键词在文本中**首次出现的位置**，去重。
    长关键词优先判定（避免 "eth" 误吞 "ethereum" 导致 ETH 被重复记两次）。

    B5: ASCII 关键词（btc/eth/sol/oil/...）要求**位于单词边界**，
    例如 "solve/method/spoil" 不再误匹配为 SOL/ETH/OIL。
    CJK 关键词（比特币/以太坊/...）保持子串匹配语义。

    Args:
        text: 用户输入或 query
    Returns:
        规范符号列表，如 "比特币 然后 黄金" → ["BTC", "GOLD"]
    """
    if not text:
        return []
    q = text.lower()
    # 第一步：收集所有命中 (position, symbol)
    # 按长度降序，避免同位置短词抢占长词（如 "eth" 抢 "ethereum"）
    claimed: list[tuple[int, int, str]] = []  # (start, end, symbol)
    sorted_keys = sorted(ASSET_ALIASES.keys(), key=len, reverse=True)
    for kw in sorted_keys:
        start = 0
        while True:
            pos = q.find(kw, start)
            if pos < 0:
                break
            end = pos + len(kw)
            # B5: 词边界检查（CJK 豁免）
            if not _boundary_ok(q, pos, end, kw):
                start = pos + 1
                continue
            # 若与已认领区间重叠，跳过（被长词覆盖）
            if any(not (end <= s or pos >= e) for s, e, _ in claimed):
                start = pos + 1
                continue
            claimed.append((pos, end, ASSET_ALIASES[kw]))
            start = end
    # 按首次出现位置排序 + 去重
    claimed.sort(key=lambda x: x[0])
    seen: set[str] = set()
    result: List[str] = []
    for _, _, sym in claimed:
        if sym not in seen:
            seen.add(sym)
            result.append(sym)
    return result


def detect_asset(text: Optional[str], default: str = "BTC") -> str:
    """从自由文本中提取首个匹配（按文本位置）的规范资产符号。"""
    assets = detect_assets(text)
    return assets[0] if assets else default


def canonicalize(symbol_or_alias: Optional[str]) -> Optional[str]:
    """把用户输入的任意写法（如 "比特币"/"btc"/"BTC"）转为规范符号。

    Returns: 规范符号（如 "BTC"）；输入为 None/空时返回 None；
             无匹配时按大写返回原值（对未收录的 symbol 做保守处理）。
    """
    if not symbol_or_alias:
        return None
    s = symbol_or_alias.strip()
    if not s:
        return None
    # 命中别名表
    hit = ASSET_ALIASES.get(s.lower())
    if hit:
        return hit
    # 未命中 → 保守返回大写
    return s.upper()


__all__ = [
    "ASSET_ALIASES",
    "SYMBOL_DISPLAY",
    "detect_asset",
    "detect_assets",
    "canonicalize",
]
