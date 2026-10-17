"""基金实时估值 API — 数据源：天天基金网（fundgz.1234567.com.cn）
交易时段（9:30-15:00 北京时间）每 1-3 分钟更新估算净值
"""
from __future__ import annotations

import re
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from fastapi import APIRouter, Query
import httpx

logger = logging.getLogger(__name__)
router = APIRouter()

FUND_GZ_URL = "https://fundgz.1234567.com.cn/js/{code}.js"
FUND_DETAIL_URL = "https://fund.eastmoney.com/pingzhongdata/{code}.js"

# 常用基金预设列表（用户也可以自己输入代码）
POPULAR_FUNDS = [
    {"code": "110011", "name": "易方达中小盘混合"},
    {"code": "161725", "name": "招商中证白酒指数"},
    {"code": "003834", "name": "华夏能源革新股票"},
    {"code": "005827", "name": "易方达蓝筹精选混合"},
    {"code": "320007", "name": "诺安成长混合"},
    {"code": "012414", "name": "华夏中证动漫游戏ETF联接A"},
    {"code": "007994", "name": "广发科技先锋混合"},
    {"code": "260108", "name": "景顺长城新兴成长混合"},
    {"code": "001938", "name": "中欧时代先锋股票A"},
    {"code": "519772", "name": "交银定期支付双息平衡混合"},
    {"code": "000961", "name": "天弘沪深300ETF联接A"},
    {"code": "001549", "name": "天弘中证500指数A"},
    {"code": "000051", "name": "华夏沪深300ETF联接A"},
]

BJ_TZ = timedelta(hours=8)


def _parse_jsonp(text: str) -> Optional[dict]:
    """解析天天基金 JSONP 回调: jsonpgz({...});"""
    m = re.search(r'jsonpgz\((\{.*?\})\)', text, re.S)
    if not m:
        return None
    import json
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


async def _fetch_fund_estimate(code: str) -> Optional[dict]:
    """获取单只基金的实时估值"""
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                FUND_GZ_URL.format(code=code),
                headers={"Referer": "https://fund.eastmoney.com/"},
            )
            resp.raise_for_status()
            data = _parse_jsonp(resp.text)
            if not data:
                return None
            return {
                "code": data.get("fundcode", code),
                "name": data.get("name", ""),
                "nav_yesterday": float(data.get("dwjz", 0)),     # 昨日净值
                "nav_estimated": float(data.get("gsz", 0)),       # 估算净值
                "change_pct": float(data.get("gszzl", 0)),        # 估算涨跌幅 %
                "estimate_time": data.get("gztime", ""),          # 估值时间
            }
    except Exception as e:
        logger.warning(f"Fund estimate fetch failed for {code}: {e}")
        return None


@router.get("/estimate")
async def get_fund_estimates(
    codes: str = Query(
        default="110011,161725,003834,005827,320007",
        description="逗号分隔的基金代码列表"
    ),
):
    """批量获取基金实时估值"""
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    if not code_list:
        return {"funds": [], "fetch_time": datetime.now(timezone.utc).isoformat()}

    import asyncio
    tasks = [_fetch_fund_estimate(c) for c in code_list[:20]]  # 限制最多20只
    results = await asyncio.gather(*tasks)
    funds = [r for r in results if r is not None]

    return {
        "funds": funds,
        "fetch_time": datetime.now(timezone.utc).isoformat(),
        "count": len(funds),
    }


@router.get("/popular")
async def get_popular_funds():
    """获取常用基金列表"""
    return {"funds": POPULAR_FUNDS}


@router.get("/search")
async def search_fund(
    keyword: str = Query(..., description="基金名称或代码关键字"),
):
    """搜索基金（通过天天基金搜索接口）"""
    try:
        url = f"https://fundsuggest.eastmoney.com/FundSearch/api/FundSearchAPI.ashx?callback=cb&m=1&key={keyword}"
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(url, headers={"Referer": "https://fund.eastmoney.com/"})
            text = resp.text
            # Parse JSONP: cb({...})
            m = re.search(r'cb\((\{.*\})\)', text, re.S)
            if not m:
                return {"results": []}
            import json
            data = json.loads(m.group(1))
            datas = data.get("Datas", [])
            results = []
            for item in datas[:15]:
                results.append({
                    "code": item.get("CODE", ""),
                    "name": item.get("NAME", ""),
                    "type": item.get("FundBaseInfo", {}).get("FTYPE", "") if isinstance(item.get("FundBaseInfo"), dict) else "",
                })
            return {"results": results}
    except Exception as e:
        logger.warning(f"Fund search failed: {e}")
        return {"results": []}


@router.get("/history/{code}")
async def get_fund_history(
    code: str,
    period: str = Query("1y", description="时间范围: 1m/3m/6m/1y/3y/all"),
):
    """获取基金历史净值 + 对比曲线（同类平均 / 沪深300）"""
    import json as _json
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.get(
                FUND_DETAIL_URL.format(code=code),
                headers={"Referer": "https://fund.eastmoney.com/"},
            )
            resp.raise_for_status()
            text = resp.text

        # 基金名称
        m_name = re.search(r'var fS_name\s*=\s*"(.*?)"', text)
        fund_name = m_name.group(1) if m_name else code

        # 历史净值: [{x: timestamp_ms, y: nav, equityReturn: pct}, ...]
        m_nav = re.search(r'var Data_netWorthTrend\s*=\s*(\[.*?\])\s*;', text, re.S)
        nav_points = _json.loads(m_nav.group(1)) if m_nav else []

        # 对比曲线: [{name: "基金", data: [[ts, pct], ...]}, {name: "同类平均", ...}, ...]
        m_gt = re.search(r'var Data_grandTotal\s*=\s*(\[.*?\])\s*;', text, re.S)
        compare_series = _json.loads(m_gt.group(1)) if m_gt else []

        # 收益率
        returns = {}
        for var, label in [('syl_1n', '近1年'), ('syl_6y', '近6月'),
                           ('syl_3y', '近3月'), ('syl_1y', '近1月')]:
            m_r = re.search(r'var ' + var + r'\s*=\s*"(.*?)"', text)
            if m_r:
                try:
                    returns[label] = float(m_r.group(1))
                except ValueError:
                    pass

        # 按 period 裁剪 nav_points
        if nav_points and period != "all":
            now_ms = nav_points[-1]["x"]
            period_map = {"1m": 30, "3m": 90, "6m": 180, "1y": 365, "3y": 1095}
            days = period_map.get(period, 365)
            cutoff = now_ms - days * 86400_000
            nav_points = [p for p in nav_points if p["x"] >= cutoff]

        # 精简输出：只保留 {t, nav} 减少传输量
        nav_data = [{"t": p["x"], "nav": p["y"]} for p in nav_points]

        return {
            "code": code,
            "name": fund_name,
            "nav": nav_data,
            "compare": [
                {"name": s.get("name", ""), "data": s.get("data", [])}
                for s in compare_series
            ],
            "returns": returns,
            "total_points": len(nav_data),
        }
    except Exception as e:
        logger.warning(f"Fund history fetch failed for {code}: {e}")
        return {"code": code, "name": "", "nav": [], "compare": [], "returns": {}}
