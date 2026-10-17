"""News Realtime MCP - realtime news aggregator

Sources (all free):
1. CLS Telegraph (cls.cn) - Chinese financial news, no login needed
2. Nitter RSS - Twitter/X mirror, track KOL accounts, no API key
3. Jin10 RSS - Chinese flash news
4. FRED release calendar - macro events

Note on Twitter/X official API:
  Free tier only allows posting. Reading tweets requires Basic ($100/month).
  Nitter is the free alternative via RSS from open-source X mirrors.
"""
from typing import Optional, List
from datetime import datetime, timezone
from app.config import settings

SOURCE_PACKAGE = "news_realtime_mcp"

NITTER_INSTANCES = [
    "https://nitter.privacydev.net",
    "https://nitter.net",
    "https://nitter.cz",
]

DEFAULT_TWITTER_ACCOUNTS = [
    "CryptoCred",
    "PeterLBrandt",
    "100trillionUSD",
    "WClementeIII",
    "glassnode",
    "federalreserve",
]


def _get_proxies():
    hp = settings.HTTP_PROXY
    hs = settings.HTTPS_PROXY
    if hp or hs:
        return {"http": hp or hs, "https": hs or hp}
    return None


def _parse_importance(title: str, source: str = "") -> float:
    high_kw = ["fed", "rate", "etf", "sec", "crash", "ban", "btc", "bitcoin",
               "\u7f8e\u8054\u50a8", "\u5229\u7387", "\u66b4\u8dcc", "\u76d1\u7ba1", "\u6e05\u7b97", "\u7834\u4ea7", "\u7a81\u7834", "\u66b4\u6da8"]
    med_kw  = ["inflation", "cpi", "gdp", "employment", "\u901a\u8139", "\u5c31\u4e1a", "\u8d22\u62a5"]
    text = title.lower()
    hits_high = sum(1 for kw in high_kw if kw in text)
    hits_med  = sum(1 for kw in med_kw  if kw in text)
    base = 0.5 if source in ("\u8d22\u8054\u793e", "\u91d1\u5341\u6570\u636e") else 0.35
    return min(base + hits_high * 0.15 + hits_med * 0.08, 1.0)


def fetch_cls_telegraph(limit: int = 30) -> List[dict]:
    """CLS (cls.cn) telegraph - Chinese financial flash news (no login needed)"""
    try:
        import requests as req
        proxies = _get_proxies()
        url = "https://www.cls.cn/api/brief/pc/update"
        params = {"app": "CLS", "os": "web", "sv": "7.7.5", "type": 0, "rn": limit}
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.cls.cn/telegraph",
        }
        r = req.get(url, params=params, headers=headers, proxies=proxies, timeout=10)
        data = r.json()
        items = []
        if isinstance(data.get("data"), dict):
            items = data["data"].get("roll_data", [])
        results = []
        for item in items[:limit]:
            title = item.get("brief", "") or item.get("title", "")
            if not title:
                continue
            ts = item.get("ctime", 0)
            dt = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else datetime.now(timezone.utc).isoformat()
            importance = _parse_importance(title, "cls")
            bull_kw = ["\u4e0a\u6da8", "\u7a81\u7834", "\u5229\u597d", "\u5927\u6da8", "\u65b0\u9ad8"]
            bear_kw = ["\u4e0b\u8dcc", "\u66b4\u8dcc", "\u5229\u7a7a", "\u5d29\u76d8", "\u6e05\u7b97"]
            direction = ("bullish" if any(k in title for k in bull_kw) else
                         "bearish" if any(k in title for k in bear_kw) else "neutral")
            results.append({
                "id": None, "event_type": "news",
                "title": title[:200],
                "summary": item.get("content", "")[:500] if item.get("content") else None,
                "source_name": "\u8d22\u8054\u793e",
                "source_url": f"https://www.cls.cn/detail/{item.get(chr(105)+chr(100), '')}",
                "event_timestamp": dt,
                "asset_scope": ["CRYPTO", "SPX"],
                "theme_scope": [],
                "importance": round(importance, 3),
                "impact_direction": direction,
                "time_horizon": "immediate",
                "is_surprise": importance > 0.7,
                "status": "active",
                "data_source": "cls_telegraph",
                "sentiment_score": None,
            })
        return results
    except Exception:
        return []


def fetch_nitter_rss(accounts: Optional[List[str]] = None, limit_per_account: int = 5) -> List[dict]:
    """Nitter RSS - free Twitter/X alternative (no API key needed)
    Official X API requires Basic plan ($100/month) to read tweets.
    Nitter provides open-source RSS mirrors of Twitter accounts.
    """
    try:
        import feedparser
    except ImportError:
        return []
    if not accounts:
        accounts = DEFAULT_TWITTER_ACCOUNTS
    proxies = _get_proxies()
    import os, requests as req
    if proxies:
        os.environ["HTTP_PROXY"]  = proxies["http"]
        os.environ["HTTPS_PROXY"] = proxies["https"]
    results = []
    for account in accounts:
        parsed = None
        for instance in NITTER_INSTANCES:
            try:
                r = req.get(f"{instance}/{account}/rss", timeout=8, proxies=proxies)
                if r.status_code == 200:
                    parsed = feedparser.parse(r.text)
                    if parsed.get("entries"):
                        break
            except Exception:
                continue
        if not parsed or not parsed.get("entries"):
            continue
        for entry in parsed["entries"][:limit_per_account]:
            title   = entry.get("title", "")[:200]
            summary = entry.get("summary", "")[:500]
            link    = entry.get("link", "")
            pub     = entry.get("published", "")
            importance = _parse_importance(title)
            tl = (title + " " + summary).lower()
            bull_kw = ["bullish", "buy", "long", "breakout", "rally", "surge"]
            bear_kw = ["bearish", "sell", "short", "crash", "dump", "drop"]
            direction = ("bullish" if any(k in tl for k in bull_kw) else
                         "bearish" if any(k in tl for k in bear_kw) else "neutral")
            results.append({
                "id": None, "event_type": "social",
                "title": f"@{account}: {title}",
                "summary": summary,
                "source_name": f"Twitter/@{account}",
                "source_url": link,
                "event_timestamp": pub[:16] if pub else datetime.now(timezone.utc).isoformat(),
                "asset_scope": ["BTC", "CRYPTO"],
                "theme_scope": ["social_signal"],
                "importance": round(importance, 3),
                "impact_direction": direction,
                "time_horizon": "immediate",
                "is_surprise": False,
                "status": "active",
                "data_source": "nitter_rss",
                "sentiment_score": None,
            })
    results.sort(key=lambda x: x["importance"], reverse=True)
    return results


def fetch_jin10_rss(limit: int = 20) -> List[dict]:
    """Jin10 (jin10.com) flash news RSS - Chinese financial news
    Note: Jin10 WebSocket is browser-only with no public API.
    RSS is the stable free alternative.
    """
    try:
        import feedparser, requests as req
        proxies = _get_proxies()
        import os
        if proxies:
            os.environ["HTTP_PROXY"]  = proxies["http"]
            os.environ["HTTPS_PROXY"] = proxies["https"]
        urls = [
            "https://flash.jin10.com/rss",
            "https://www.jin10.com/rss",
        ]
        for url in urls:
            try:
                r = req.get(url, timeout=8, proxies=proxies, headers={"User-Agent": "Mozilla/5.0"})
                if r.status_code != 200:
                    continue
                feed = feedparser.parse(r.text)
                entries = feed.get("entries", [])
                if not entries:
                    continue
                results = []
                for entry in entries[:limit]:
                    title = entry.get("title", "")[:200]
                    if not title:
                        continue
                    importance = _parse_importance(title)
                    bull_kw = ["\u4e0a\u6da8", "\u7a81\u7834", "\u5229\u597d", "\u5927\u6da8"]
                    bear_kw = ["\u4e0b\u8dcc", "\u66b4\u8dcc", "\u5229\u7a7a", "\u5d29\u76d8"]
                    direction = ("bullish" if any(k in title for k in bull_kw) else
                                 "bearish" if any(k in title for k in bear_kw) else "neutral")
                    results.append({
                        "id": None, "event_type": "news",
                        "title": title,
                        "summary": entry.get("summary", "")[:500],
                        "source_name": "\u91d1\u5341\u6570\u636e",
                        "source_url": entry.get("link", ""),
                        "event_timestamp": entry.get("published", "")[:16],
                        "asset_scope": ["CRYPTO", "SPX", "GOLD"],
                        "theme_scope": [],
                        "importance": round(importance, 3),
                        "impact_direction": direction,
                        "time_horizon": "immediate",
                        "is_surprise": importance > 0.7,
                        "status": "active",
                        "data_source": "jin10_rss",
                        "sentiment_score": None,
                    })
                return results
            except Exception:
                continue
        return []
    except Exception:
        return []


def fetch_macro_calendar(days_ahead: int = 7) -> List[dict]:
    """Macro economic calendar via Investing.com RSS (no key needed)"""
    try:
        import feedparser, requests as req
        proxies = _get_proxies()
        import os
        if proxies:
            os.environ["HTTP_PROXY"]  = proxies["http"]
            os.environ["HTTPS_PROXY"] = proxies["https"]
        url = "https://www.investing.com/rss/news_14.rss"
        r = req.get(url, timeout=10, proxies=proxies,
                    headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return []
        feed = feedparser.parse(r.text)
        results = []
        for entry in feed.get("entries", [])[:20]:
            title = entry.get("title", "")[:200]
            results.append({
                "id": None, "event_type": "macro_event",
                "title": title,
                "summary": entry.get("summary", "")[:500],
                "source_name": "Investing.com",
                "source_url": entry.get("link", ""),
                "event_timestamp": entry.get("published", "")[:16],
                "asset_scope": ["SPX", "DXY", "GOLD"],
                "theme_scope": ["macro"],
                "importance": _parse_importance(title),
                "impact_direction": "neutral",
                "time_horizon": "short_term",
                "is_surprise": False,
                "status": "active",
                "data_source": "investing_com_rss",
                "sentiment_score": None,
            })
        return results
    except Exception:
        return []


class NewsRealtimeMCP:
    """Aggregates realtime news from multiple free sources"""

    def __init__(self):
        self.use_mock = getattr(settings, "USE_MOCK_EVENT_FEED", True)

    def fetch_all(self, limit: int = 50) -> List[dict]:
        """Fetch and merge from all sources, deduplicate by title"""
        if self.use_mock:
            return []
        all_items: List[dict] = []
        seen_titles: set = set()

        for fetcher, name in [
            (lambda: fetch_cls_telegraph(30), "cls"),
            (lambda: fetch_jin10_rss(20), "jin10"),
            (lambda: fetch_nitter_rss(limit_per_account=3), "nitter"),
            (lambda: fetch_macro_calendar(), "macro"),
        ]:
            try:
                items = fetcher()
                for item in items:
                    t = item.get("title", "")
                    if t and t not in seen_titles:
                        seen_titles.add(t)
                        all_items.append(item)
            except Exception:
                continue

        all_items.sort(key=lambda x: x.get("importance", 0), reverse=True)
        return all_items[:limit]

    def fetch_chinese_news(self, limit: int = 30) -> List[dict]:
        """Only Chinese sources: CLS + Jin10"""
        if self.use_mock:
            return []
        items = fetch_cls_telegraph(limit) + fetch_jin10_rss(limit)
        seen, out = set(), []
        for item in items:
            t = item.get("title", "")
            if t and t not in seen:
                seen.add(t)
                out.append(item)
        out.sort(key=lambda x: x.get("importance", 0), reverse=True)
        return out[:limit]

    def fetch_twitter_kol(self, accounts: Optional[List[str]] = None, limit: int = 20) -> List[dict]:
        """Twitter KOL feed via Nitter RSS"""
        if self.use_mock:
            return []
        return fetch_nitter_rss(accounts=accounts, limit_per_account=5)[:limit]
