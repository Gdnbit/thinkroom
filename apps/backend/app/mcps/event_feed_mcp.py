"""Event Feed MCP — mock 模式从数据库读取，live 模式接入 RSS 新闻源（无需 Key，需代理）"""
from typing import Optional, List
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from loguru import logger
from app.mcps.base import MCPResponse
from app.services import market_persist as persist
from app.services.event_repository import EventRepository
from app.config import settings

SOURCE_PACKAGE = "event_feed_mcp"

# RSS 源配置（全部免费，无需注册）
RSS_FEEDS = [
    {"url": "https://www.coindesk.com/arc/outboundfeeds/rss/",    "source": "CoinDesk",      "asset_scope": ["BTC", "ETH", "CRYPTO"]},
    {"url": "https://cointelegraph.com/rss",                        "source": "CoinTelegraph",  "asset_scope": ["BTC", "ETH", "CRYPTO"]},
    {"url": "https://feeds.reuters.com/reuters/businessNews",       "source": "Reuters",       "asset_scope": ["SPX", "GOLD", "DXY"]},
]

# 关键词 → 情感映射（简单规则，无需 LLM）
BULLISH_KEYWORDS = [
    "surge", "rally", "bullish", "rise", "gain", "breakout", "all-time high", "ath",
    "adoption", "institutional", "inflow", "看涨", "突破", "上涨", "流入",
]
BEARISH_KEYWORDS = [
    "crash", "dump", "bearish", "fall", "drop", "selloff", "liquidation", "ban",
    "hack", "fraud", "recession", "inflation", "看跌", "下跌", "流出", "暴跌",
]


def _get_proxies() -> Optional[dict]:
    """从 settings 读取代理配置"""
    http_proxy  = settings.HTTP_PROXY
    https_proxy = settings.HTTPS_PROXY
    if http_proxy or https_proxy:
        return {"http": http_proxy or https_proxy, "https": https_proxy or http_proxy}
    return None


def _infer_direction(title: str, summary: str = "") -> str:
    """根据标题/摘要关键词简单推断情感方向"""
    text = (title + " " + summary).lower()
    bull_score = sum(1 for kw in BULLISH_KEYWORDS if kw.lower() in text)
    bear_score = sum(1 for kw in BEARISH_KEYWORDS if kw.lower() in text)
    if bull_score > bear_score:
        return "bullish"
    if bear_score > bull_score:
        return "bearish"
    return "neutral"


def _infer_importance(title: str) -> float:
    """根据关键词估算重要性 0-1"""
    high_kw = [
        # 宏观/政策
        "fed", "fomc", "rate", "cpi", "inflation", "gdp", "nonfarm", "tariff",
        "recession", "treasury", "powell", "yellen", "ecb", "pboc", "boj",
        # 监管/法律
        "sec", "etf", "ban", "regulation", "lawsuit", "sanction", "compliance",
        # 加密核心
        "btc", "bitcoin", "ethereum", "eth", "halving", "whale", "liquidat",
        # 行情
        "crash", "surge", "rally", "all-time", "ath", "breakout", "dump",
        # 中文关键词
        "美联储", "利率", "暴跌", "暴涨", "监管", "关税", "降息", "加息",
        "通胀", "衰退", "清算", "巨鲸", "突破",
    ]
    text = title.lower()
    hits = sum(1 for kw in high_kw if kw in text)
    return min(0.45 + hits * 0.15, 1.0)


def _fetch_alpha_vantage_news(asset: str = "BTC", limit: int = 20) -> List[dict]:
    """从 Alpha Vantage 获取新闻情感数据（需 API Key）"""
    try:
        import requests as req
        api_key = settings.ALPHA_VANTAGE_API_KEY
        if not api_key:
            return []
        proxies = _get_proxies()
        # ticker 映射
        ticker_map = {"BTC": "CRYPTO:BTC", "ETH": "CRYPTO:ETH", "SPX": "SPY", "GOLD": "GLD"}
        ticker = ticker_map.get(asset.upper(), f"CRYPTO:{asset.upper()}")
        url = "https://www.alphavantage.co/query"
        params = {
            "function": "NEWS_SENTIMENT",
            "tickers": ticker,
            "limit": limit,
            "apikey": api_key,
        }
        r = req.get(url, params=params, proxies=proxies, timeout=12)
        data = r.json()
        feed = data.get("feed", [])
        results = []
        for item in feed:
            # Alpha Vantage 已提供情感标签
            overall = item.get("overall_sentiment_label", "").lower()
            direction = "bullish" if "bullish" in overall else (
                        "bearish" if "bearish" in overall else "neutral")
            score = float(item.get("overall_sentiment_score", 0))
            importance = min(0.4 + abs(score) * 0.6, 1.0)
            results.append({
                "id": None,
                "event_type": "news",
                "title": item.get("title", "")[:200],
                "summary": item.get("summary", "")[:500],
                "source_name": item.get("source", "Alpha Vantage"),
                "source_url": item.get("url", ""),
                "event_timestamp": item.get("time_published", "")[:16],
                "asset_scope": [asset.upper()],
                "theme_scope": [],
                "importance": round(importance, 3),
                "impact_direction": direction,
                "time_horizon": "immediate",
                "is_surprise": False,
                "status": "active",
                "data_source": "alpha_vantage",
                "sentiment_score": score,
            })
        return results
    except Exception:
        return []


def _fetch_rss(limit: int = 20, asset: Optional[str] = None) -> List[dict]:
    """从 RSS 源抓取最新新闻（通过代理）"""
    try:
        import feedparser
    except ImportError:
        return []

    proxies = _get_proxies()
    # feedparser 通过环境变量读取代理
    import os
    if proxies:
        os.environ["HTTP_PROXY"]  = proxies["http"]
        os.environ["HTTPS_PROXY"] = proxies["https"]

    results = []
    for feed_cfg in RSS_FEEDS:
        # 资产过滤：如果指定资产且该 feed 不覆盖此资产则跳过
        if asset:
            asset_upper = asset.upper()
            covered = feed_cfg["asset_scope"]
            # CRYPTO 通吃加密
            if asset_upper in ("BTC", "ETH", "SOL", "BNB") and "CRYPTO" not in covered and asset_upper not in covered:
                continue
            if asset_upper not in ("BTC", "ETH", "SOL", "BNB") and asset_upper not in covered:
                continue
        try:
            feed = feedparser.parse(feed_cfg["url"])
            for entry in feed.entries[:10]:
                title   = entry.get("title", "")
                summary = entry.get("summary", "") or entry.get("description", "")
                link    = entry.get("link", "")
                # 解析发布时间
                published = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    try:
                        import calendar
                        ts = calendar.timegm(entry.published_parsed)
                        published = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                    except Exception:
                        pass
                published = published or datetime.now(timezone.utc).isoformat()

                direction  = _infer_direction(title, summary)
                importance = _infer_importance(title)

                results.append({
                    "id": None,
                    "event_type": "news",
                    "title": title[:200],
                    "summary": summary[:500] if summary else None,
                    "source_name": feed_cfg["source"],
                    "source_url": link,
                    "event_timestamp": published,
                    "asset_scope": feed_cfg["asset_scope"],
                    "theme_scope": [],
                    "importance": importance,
                    "impact_direction": direction,
                    "time_horizon": "immediate",
                    "is_surprise": False,
                    "status": "active",
                    "data_source": "rss",
                })
        except Exception:
            continue

    # 按 importance 降序排列，取前 limit 条
    results.sort(key=lambda x: x["importance"], reverse=True)
    return results[:limit]


class EventFeedMCP:
    def __init__(self, db: Session):
        self.db = db
        self.event_repo = EventRepository(db)
        self.use_mock = settings.USE_MOCK_EVENT_FEED

    def fetch_recent_events(
        self,
        *,
        asset: Optional[str] = None,
        theme: Optional[str] = None,
        importance_min: float = 0.0,
        limit: int = 20,
    ) -> MCPResponse[List[dict]]:
        """获取最新事件 — live 模式优先 Alpha Vantage 情感新闻，补充 RSS + 中文快讯，mock 模式从数据库读取"""
        try:
            if not self.use_mock:
                from app.services.api_quota import (
                    allow_events, mark_events_called,
                    allow_alpha_vantage, mark_alpha_vantage_called,
                )
                # DB 优先：1小时内已有数据且配额已锁定，直接返回存量
                fresh = persist.get_fresh_events(self.db, asset=asset, max_age_minutes=60, limit=limit)
                if fresh and not allow_events(asset):
                    filtered = [e for e in fresh if e["importance"] >= importance_min]
                    if filtered:
                        logger.debug(f"[EventFeedMCP] quota locked, returning DB cache ({len(filtered)} events)")
                        return MCPResponse.ok(data=filtered[:limit], source_package=SOURCE_PACKAGE)

                # 配额允许：拉取最新数据
                live_events: List[dict] = []

                # 1. Alpha Vantage（每日限额，每资产每天只调用一次）
                av_asset = asset or "BTC"
                if settings.ALPHA_VANTAGE_API_KEY and allow_alpha_vantage(av_asset):
                    av_events = _fetch_alpha_vantage_news(av_asset, limit=limit)
                    if av_events:
                        live_events.extend(av_events)
                        mark_alpha_vantage_called(av_asset)
                elif fresh:  # AV 配额已用，补充 DB 存量
                    live_events.extend(fresh)

                existing_titles = {e["title"] for e in live_events}

                # 2. 中文快讯（财联社 + 金十）
                try:
                    from app.mcps.news_realtime_mcp import fetch_cls_telegraph, fetch_jin10_rss
                    for item in fetch_cls_telegraph(20) + fetch_jin10_rss(20):
                        if item["title"] not in existing_titles:
                            live_events.append(item)
                            existing_titles.add(item["title"])
                except Exception:
                    pass

                # 3. RSS（每小时冷却，同一 asset 共享配额）
                if allow_events(asset):
                    rss_events = _fetch_rss(limit=limit * 2, asset=asset)
                    for e in rss_events:
                        if e["title"] not in existing_titles:
                            live_events.append(e)
                            existing_titles.add(e["title"])
                    if rss_events:
                        mark_events_called(asset)

                # 4. 社交媒体（RSSHub + MediaCrawler + We-MP-RSS）
                try:
                    from app.mcps.social_media_mcp import SocialMediaMCP
                    social_mcp = SocialMediaMCP()
                    social_resp = social_mcp.fetch_all(limit=limit)
                    if social_resp.success:
                        for item in social_resp.data:
                            if item.get("title") and item["title"] not in existing_titles:
                                # 用关键词推断情感和重要性
                                item["impact_direction"] = _infer_direction(
                                    item.get("title", ""), item.get("summary", ""))
                                item["importance"] = max(
                                    item.get("importance", 0.4),
                                    _infer_importance(item.get("title", "")))
                                live_events.append(item)
                                existing_titles.add(item["title"])
                except Exception as e:
                    logger.debug(f"[EventFeedMCP] Social media fetch skipped: {e}")

                if importance_min > 0:
                    live_events = [e for e in live_events if e["importance"] >= importance_min]
                live_events.sort(key=lambda x: x["importance"], reverse=True)

                # 持久化到 DB
                persist.persist_events(self.db, live_events)

                # 知识库自动充实：高重要性事件入库
                try:
                    from app.services.knowledge_enrichment import enrich_from_api
                    important = [e for e in live_events if e.get("importance", 0) >= 0.6]
                    if important:
                        lines = [f"# 重要市场事件摘要 ({len(important)} 条)\n"]
                        for ev in important[:10]:
                            lines.append(f"## {ev.get('title', '')}")
                            if ev.get("summary"):
                                lines.append(ev["summary"][:300])
                            lines.append(f"- 来源: {ev.get('source_name', '?')} | 方向: {ev.get('impact_direction', '?')} | 重要性: {ev.get('importance', 0):.2f}")
                            lines.append("")
                        enrich_from_api(
                            db=self.db,
                            source_name=f"event_feed_{asset or 'global'}",
                            title="市场事件汇总",
                            text_content="\n".join(lines),
                            theme_hint=" ".join(set(a for ev in important for a in (ev.get("asset_scope") or []))),
                        )
                except Exception:
                    pass

                return MCPResponse.ok(data=live_events[:limit], source_package=SOURCE_PACKAGE)

            # mock 模式：从数据库读取
            items = self.event_repo.get_recent(
                asset=asset,
                theme=theme,
                importance_min=importance_min,
                limit=limit,
            )
            data = [
                {
                    "id": e.id,
                    "event_type": e.event_type,
                    "title": e.title,
                    "summary": e.summary,
                    "source_name": e.source_name,
                    "event_timestamp": e.event_timestamp.isoformat() if e.event_timestamp else None,
                    "asset_scope": e.asset_scope or [],
                    "theme_scope": e.theme_scope or [],
                    "importance": e.importance,
                    "impact_direction": e.impact_direction,
                    "time_horizon": e.time_horizon,
                    "is_surprise": e.is_surprise,
                    "status": e.status,
                    "truth_status": getattr(e, "truth_status", "unverified"),
                    "outcome_status": getattr(e, "outcome_status", None),
                    "source_tier": getattr(e, "source_tier", None),
                }
                for e in items
            ]
            return MCPResponse.mock(data=data, source_package=SOURCE_PACKAGE)
        except Exception as e:
            return MCPResponse.fail(str(e), SOURCE_PACKAGE)
