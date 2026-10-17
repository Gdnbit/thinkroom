"""Web Search MCP

Multi-backend web search, priority order:
  1. Tavily API     - AI-optimized, free 1000/month
  2. SearXNG        - self-hosted, free (needs SEARXNG_URL)
  3. DuckDuckGo     - no key needed, rate limited fallback
"""
from __future__ import annotations
from typing import List, Optional
from app.mcps.base import MCPResponse
from app.config import settings

SOURCE_PACKAGE = "web_search_mcp"


def _get_proxies():
    hp = settings.HTTP_PROXY
    hs = settings.HTTPS_PROXY
    if hp or hs:
        return {"http": hp or hs, "https": hs or hp}
    return None


def _search_tavily(query: str, max_results: int = 6) -> List[dict]:
    import requests as req
    key = settings.TAVILY_API_KEY
    if not key:
        raise ValueError("No TAVILY_API_KEY")
    resp = req.post(
        "https://api.tavily.com/search",
        json={"api_key": key, "query": query, "max_results": max_results,
              "search_depth": "basic", "include_answer": True, "include_raw_content": False},
        proxies=_get_proxies(), timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    results = []
    if data.get("answer"):
        results.append({"title": "AI Summary", "url": "", "snippet": data["answer"], "source": "tavily_answer", "score": 1.0})
    for r in data.get("results", [])[:max_results]:
        results.append({"title": r.get("title", ""), "url": r.get("url", ""),
                        "snippet": r.get("content", "")[:600], "source": "tavily", "score": r.get("score", 0.5)})
    return results


def _search_searxng(query: str, max_results: int = 6) -> List[dict]:
    import requests as req
    url = settings.SEARXNG_URL
    if not url:
        raise ValueError("No SEARXNG_URL")
    resp = req.get(f"{url.rstrip(chr(47))}/search",
                   params={"q": query, "format": "json", "categories": "general,news"},
                   proxies=_get_proxies(), timeout=12)
    resp.raise_for_status()
    data = resp.json()
    return [{"title": r.get("title", ""), "url": r.get("url", ""),
             "snippet": r.get("content", "")[:600], "source": "searxng", "score": r.get("score", 0.5)}
            for r in data.get("results", [])[:max_results]]


def _search_duckduckgo(query: str, max_results: int = 6) -> List[dict]:
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            raise ValueError("pip install ddgs")
    proxies = _get_proxies()
    proxy = proxies.get("https") or proxies.get("http") if proxies else None
    results = []
    with DDGS(proxy=proxy) as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            results.append({"title": r.get("title", ""), "url": r.get("href", ""),
                            "snippet": r.get("body", "")[:600], "source": "duckduckgo", "score": 0.5})
    return results


class WebSearchMCP:
    """Web search MCP for plan_and_research pipeline"""

    def search(self, query: str, max_results: int = 6, preferred_backend: Optional[str] = None) -> MCPResponse:
        from loguru import logger
        backends = [("tavily", _search_tavily), ("searxng", _search_searxng), ("duckduckgo", _search_duckduckgo)]
        if preferred_backend:
            backends = sorted(backends, key=lambda x: 0 if x[0] == preferred_backend else 1)
        last_error = ""
        for name, fn in backends:
            try:
                results = fn(query, max_results)
                logger.info(f"[WebSearch] backend={name} results={len(results)} query={query[:50]}")
                return MCPResponse.ok(data=results, source_package=SOURCE_PACKAGE)
            except Exception as e:
                last_error = f"{name}: {e}"
                logger.debug(f"[WebSearch] {name} failed: {e}")
        return MCPResponse.fail(error=f"All backends failed: {last_error}", source_package=SOURCE_PACKAGE)

    def search_news(self, topic: str, asset: Optional[str] = None, max_results: int = 5) -> MCPResponse:
        if asset and asset.upper() not in topic.upper():
            query = f"{asset} {topic} latest news 2025 2026"
        else:
            query = f"{topic} financial news latest"
        return self.search(query, max_results=max_results)

    def search_macro(self, question: str, max_results: int = 6) -> MCPResponse:
        query = f"{question} economic analysis 2025 2026"
        return self.search(query, max_results=max_results)
