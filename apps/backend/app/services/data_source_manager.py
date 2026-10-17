"""Data Source Manager — 数据源管理 + API 限制提醒

功能：
1. **数据源注册表**：所有外部 API 的元数据、限制条件、健康状态
2. **API 限制提醒**：在调用前自动提示配额/频率/可用性
3. **健康监控**：记录每个数据源的最近调用时间、成功率、延迟
4. **Dashboard 数据**：供前端展示数据源状态面板

数据源分类：
- 行情类(market)：Binance, Yahoo Finance, CoinGecko
- 宏观类(macro)：FRED
- 新闻/事件类(event)：Alpha Vantage, RSS
- 资金流/链上类(flow)：Binance Futures, Fear & Greed Index, DeFiLlama, Glassnode
- 预测市场类(prediction)：Polymarket
- 社交媒体类(social)：Twitter/X, 微博, 小红书, 抖音, 微信公众号
- 内部类(internal)：Research Memory DB, Semantic Search
"""
from __future__ import annotations

import time
import threading
from datetime import datetime, timezone
from typing import Optional, List, Dict
from dataclasses import dataclass, field
from loguru import logger


@dataclass
class DataSourceMeta:
    """数据源元数据"""
    id: str                     # 唯一标识
    name: str                   # 显示名称
    category: str               # market | macro | event | flow | internal
    provider: str               # binance | yahoo | fred | ...
    api_type: str               # rest | websocket | db
    requires_key: bool          # 是否需要 API Key
    key_configured: bool        # Key 是否已配置
    rate_limit: str             # 频率限制描述
    cooldown_freq: str          # QuotaGuard 频率类别
    data_freshness: str         # 数据时效性描述
    coverage: List[str]         # 覆盖的资产/数据范围
    known_limitations: List[str]  # 已知限制
    fallback: Optional[str]     # 备用数据源 ID
    documentation_url: str      # 文档链接


@dataclass
class SourceHealthRecord:
    """数据源健康记录"""
    source_id: str
    last_call_at: Optional[float] = None
    last_success_at: Optional[float] = None
    last_error_at: Optional[float] = None
    last_error_msg: Optional[str] = None
    total_calls: int = 0
    total_successes: int = 0
    total_failures: int = 0
    avg_latency_ms: float = 0
    _latency_sum: float = 0


# ── 数据源注册表 ─────────────────────────────────────────────

def _build_registry() -> Dict[str, DataSourceMeta]:
    """构建数据源注册表"""
    from app.config import settings

    sources = [
        DataSourceMeta(
            id="binance_spot",
            name="Binance 现货行情",
            category="market",
            provider="binance",
            api_type="rest",
            requires_key=False,
            key_configured=True,
            rate_limit="1200 requests/min (IP), 无需 API Key",
            cooldown_freq="FAST",
            data_freshness="实时（5分钟冷却窗口）",
            coverage=["BTC", "ETH", "BNB", "SOL", "XRP", "DOGE"],
            known_limitations=[
                "中国大陆需代理访问",
                "仅支持 USDT 交易对",
                "历史 K 线最多 1000 条",
                "无基本面数据",
            ],
            fallback="coingecko",
            documentation_url="https://binance-docs.github.io/apidocs/spot/en/",
        ),
        DataSourceMeta(
            id="yahoo_finance",
            name="Yahoo Finance",
            category="market",
            provider="yahoo",
            api_type="rest",
            requires_key=False,
            key_configured=True,
            rate_limit="无官方公开限制，建议 < 2000/h",
            cooldown_freq="FAST",
            data_freshness="延迟约 15 分钟（非实时）",
            coverage=["SPX", "NDX", "GOLD", "OIL", "DXY", "VIX", "HSI", "CSI300"],
            known_limitations=[
                "非官方 API，可能随时变动",
                "美股数据延迟 15 分钟",
                "中国大陆需代理访问",
                "无盘前盘后数据",
                "A 股数据可能不完整",
            ],
            fallback=None,
            documentation_url="https://finance.yahoo.com/",
        ),
        DataSourceMeta(
            id="coingecko",
            name="CoinGecko",
            category="market",
            provider="coingecko",
            api_type="rest",
            requires_key=True,
            key_configured=bool(settings.COINGECKO_API_KEY),
            rate_limit="Demo Key: 30 calls/min, Pro: 500/min",
            cooldown_freq="FAST",
            data_freshness="1-2 分钟延迟",
            coverage=["BTC", "ETH", "BNB", "SOL"],
            known_limitations=[
                "Demo API Key 限制 30 次/分钟",
                "无 K 线数据（仅价格快照）",
                "价格为全球加权平均，非单一交易所",
                "中国大陆需代理访问",
            ],
            fallback=None,
            documentation_url="https://docs.coingecko.com/v3.0.1/reference/introduction",
        ),
        DataSourceMeta(
            id="fred",
            name="FRED 宏观数据",
            category="macro",
            provider="fred",
            api_type="rest",
            requires_key=True,
            key_configured=bool(settings.FRED_API_KEY),
            rate_limit="120 requests/min（需 API Key）",
            cooldown_freq="DAILY",
            data_freshness="经济指标更新周期不一（CPI 月度, 利率日度）",
            coverage=["FEDFUNDS", "DGS10", "DGS2", "CPIAUCSL", "UNRATE"],
            known_limitations=[
                "需要免费 API Key（https://fred.stlouisfed.org/docs/api/api_key.html）",
                "宏观数据天然滞后（CPI 滞后 1 个月）",
                "每天仅调用 1 次（系统限制，非 API 限制）",
                "仅覆盖美国宏观指标",
            ],
            fallback=None,
            documentation_url="https://fred.stlouisfed.org/docs/api/fred/",
        ),
        DataSourceMeta(
            id="binance_futures",
            name="Binance Futures 资金流",
            category="flow",
            provider="binance",
            api_type="rest",
            requires_key=False,
            key_configured=True,
            rate_limit="与 Binance 现货共享限制",
            cooldown_freq="MEDIUM",
            data_freshness="实时（3分钟冷却窗口）",
            coverage=["BTC", "ETH"],
            known_limitations=[
                "中国大陆需代理访问",
                "仅 USDT 永续合约",
                "资金费率每 8 小时结算一次",
                "多空比数据可能有延迟",
            ],
            fallback=None,
            documentation_url="https://binance-docs.github.io/apidocs/futures/en/",
        ),
        DataSourceMeta(
            id="alpha_vantage",
            name="Alpha Vantage 新闻情感",
            category="event",
            provider="alpha_vantage",
            api_type="rest",
            requires_key=True,
            key_configured=bool(getattr(settings, 'ALPHA_VANTAGE_API_KEY', None)),
            rate_limit="免费 Key: 25 requests/day, 5/min",
            cooldown_freq="DAILY",
            data_freshness="新闻延迟 1-2 小时",
            coverage=["全球股票", "加密货币", "外汇"],
            known_limitations=[
                "免费 Key 每天仅 25 次请求",
                "新闻覆盖以英文为主",
                "情感分析准确率约 70%",
                "每个资产每天仅调用 1 次",
            ],
            fallback=None,
            documentation_url="https://www.alphavantage.co/documentation/",
        ),
        DataSourceMeta(
            id="polymarket",
            name="Polymarket 预测市场",
            category="prediction",
            provider="polymarket",
            api_type="rest",
            requires_key=False,
            key_configured=True,
            rate_limit="无官方公开限制，建议 < 60 requests/min",
            cooldown_freq="MEDIUM",
            data_freshness="实时（基于链上结算）",
            coverage=["加密货币监管", "美国大选", "地缘政治", "宏观经济事件", "科技里程碑"],
            known_limitations=[
                "预测市场仅反映参与者的概率评估，非确定性预测",
                "低流动性市场的价格可能不准确",
                "中国大陆需代理访问",
                "部分市场可能有监管风险",
                "结果依赖 oracle 解析，存在争议仲裁延迟",
            ],
            fallback=None,
            documentation_url="https://docs.polymarket.com/",
        ),
        DataSourceMeta(
            id="fear_greed_index",
            name="Crypto Fear & Greed Index",
            category="flow",
            provider="alternative.me",
            api_type="rest",
            requires_key=False,
            key_configured=True,
            rate_limit="无限制（公开 API）",
            cooldown_freq="DAILY",
            data_freshness="每日更新",
            coverage=["BTC（加密市场整体情绪）"],
            known_limitations=[
                "仅覆盖加密市场整体情绪",
                "指标构成不透明（社交媒体、波动率、调查等加权）",
                "极端值参考意义大于绝对值",
                "更新频率仅日度",
            ],
            fallback=None,
            documentation_url="https://alternative.me/crypto/fear-and-greed-index/",
        ),
        DataSourceMeta(
            id="defi_llama",
            name="DeFiLlama TVL & Yield",
            category="flow",
            provider="defillama",
            api_type="rest",
            requires_key=False,
            key_configured=True,
            rate_limit="无限制（公开 API）",
            cooldown_freq="MEDIUM",
            data_freshness="实时 TVL，Yield 每小时更新",
            coverage=["DeFi 协议 TVL", "稳定币流通量", "跨链桥流量", "借贷利率"],
            known_limitations=[
                "TVL 数据依赖链上抓取，新协议可能遗漏",
                "稳定币数据不含中心化交易所",
                "Yield 数据可能存在短暂异常",
            ],
            fallback=None,
            documentation_url="https://defillama.com/docs/api",
        ),
        DataSourceMeta(
            id="glassnode_free",
            name="Glassnode 链上指标（免费层）",
            category="flow",
            provider="glassnode",
            api_type="rest",
            requires_key=False,
            key_configured=True,
            rate_limit="免费层：10 requests/min, 有限指标",
            cooldown_freq="DAILY",
            data_freshness="日度更新",
            coverage=["BTC 链上活跃地址", "交易所净流入/出", "矿工收入"],
            known_limitations=[
                "免费层仅提供有限指标和 24h 延迟",
                "高级指标需 Professional 订阅",
                "仅覆盖 BTC 和 ETH 的基础指标",
            ],
            fallback=None,
            documentation_url="https://docs.glassnode.com/",
        ),
        DataSourceMeta(
            id="research_memory",
            name="私域研究记忆库",
            category="internal",
            provider="local_db",
            api_type="db",
            requires_key=False,
            key_configured=True,
            rate_limit="无限制（本地数据库）",
            cooldown_freq="FAST",
            data_freshness="取决于用户手动更新频率",
            coverage=["所有已录入的观点和研究笔记"],
            known_limitations=[
                "数据质量取决于录入内容",
                "语义搜索依赖 embedding 质量",
                "无自动更新机制",
            ],
            fallback=None,
            documentation_url="",
        ),
        # ── 社交媒体信息源 ──────────────────────────────────────
        DataSourceMeta(
            id="social_twitter",
            name="Twitter/X（via RSSHub）",
            category="social",
            provider="rsshub",
            api_type="rest",
            requires_key=False,
            key_configured=bool(settings.RSSHUB_BASE_URL),
            rate_limit="取决于 RSSHub 实例配置",
            cooldown_freq="HOURLY",
            data_freshness="定时拉取（默认 30 分钟）",
            coverage=["指定 Twitter/X 博主的推文"],
            known_limitations=[
                "需要自部署 RSSHub 实例",
                "Twitter cookie 约 60 天过期需刷新",
                "依赖 RSSHub 的 Twitter 路由稳定性",
            ],
            fallback=None,
            documentation_url="https://docs.rsshub.app/routes/social-media#twitter",
        ),
        DataSourceMeta(
            id="social_weibo",
            name="微博（via RSSHub）",
            category="social",
            provider="rsshub",
            api_type="rest",
            requires_key=False,
            key_configured=bool(settings.RSSHUB_BASE_URL),
            rate_limit="取决于 RSSHub 实例配置",
            cooldown_freq="HOURLY",
            data_freshness="定时拉取（默认 30 分钟）",
            coverage=["指定微博博主的微博内容"],
            known_limitations=[
                "需要自部署 RSSHub 实例",
                "微博 cookie 约 120 天过期需刷新",
                "部分视频内容仅限中国大陆访问",
            ],
            fallback=None,
            documentation_url="https://docs.rsshub.app/routes/social-media#weibo",
        ),
        DataSourceMeta(
            id="social_xiaohongshu",
            name="小红书（via MediaCrawler）",
            category="social",
            provider="mediacrawler",
            api_type="rest",
            requires_key=False,
            key_configured=bool(settings.MEDIACRAWLER_API_URL),
            rate_limit="取决于 MediaCrawler 配置和平台反爬",
            cooldown_freq="HOURLY",
            data_freshness="定时拉取（默认 30 分钟）",
            coverage=["指定小红书博主的笔记内容"],
            known_limitations=[
                "需要部署 MediaCrawler + Playwright 环境",
                "小红书反爬严格，cookie 约 10 天过期",
                "频率过高可能触发封号",
            ],
            fallback=None,
            documentation_url="https://github.com/NanmiCoder/MediaCrawler",
        ),
        DataSourceMeta(
            id="social_douyin",
            name="抖音（via MediaCrawler + 视频转文字）",
            category="social",
            provider="mediacrawler",
            api_type="rest",
            requires_key=False,
            key_configured=bool(settings.MEDIACRAWLER_API_URL),
            rate_limit="取决于 MediaCrawler 配置；视频转写受 Gemini 免费层限制（1500次/天）",
            cooldown_freq="HOURLY",
            data_freshness="定时拉取（默认 30 分钟）",
            coverage=["指定抖音博主的视频内容（自动转文字稿）"],
            known_limitations=[
                "需要部署 MediaCrawler + Playwright 环境",
                "抖音 cookie 约 10 天过期",
                "视频转文字依赖 Gemini Flash（Google AI Studio 免费 或 OpenRouter 付费）",
                "长视频转写可能截断",
            ],
            fallback=None,
            documentation_url="https://github.com/NanmiCoder/MediaCrawler",
        ),
        DataSourceMeta(
            id="social_wechat_mp",
            name="微信公众号（via We-MP-RSS）",
            category="social",
            provider="wemprss",
            api_type="rest",
            requires_key=False,
            key_configured=bool(settings.WEMPRSS_BASE_URL),
            rate_limit="取决于 We-MP-RSS 实例配置",
            cooldown_freq="HOURLY",
            data_freshness="定时拉取（默认 30 分钟）",
            coverage=["指定微信公众号的文章内容"],
            known_limitations=[
                "需要自部署 We-MP-RSS 服务",
                "微信授权约 30 天过期需重新扫码",
                "腾讯可能封杀第三方抓取",
            ],
            fallback=None,
            documentation_url="https://github.com/rachelos/we-mp-rss",
        ),
    ]
    return {s.id: s for s in sources}


class DataSourceManager:
    """数据源管理器"""

    def __init__(self):
        self._lock = threading.Lock()
        self._registry: Dict[str, DataSourceMeta] = _build_registry()
        self._health: Dict[str, SourceHealthRecord] = {
            sid: SourceHealthRecord(source_id=sid) for sid in self._registry
        }

    def get_source(self, source_id: str) -> Optional[DataSourceMeta]:
        return self._registry.get(source_id)

    def list_sources(self, category: Optional[str] = None) -> List[dict]:
        """列出所有数据源（可按类别过滤）"""
        sources = self._registry.values()
        if category:
            sources = [s for s in sources if s.category == category]

        from app.services.api_quota import guard
        result = []
        for s in sources:
            health = self._health.get(s.id, SourceHealthRecord(source_id=s.id))
            # 获取配额状态
            quota_status = guard.status(s.id, s.cooldown_freq)
            success_rate = (
                health.total_successes / health.total_calls * 100
                if health.total_calls > 0 else None
            )
            result.append({
                "id": s.id,
                "name": s.name,
                "category": s.category,
                "provider": s.provider,
                "requires_key": s.requires_key,
                "key_configured": s.key_configured,
                "rate_limit": s.rate_limit,
                "data_freshness": s.data_freshness,
                "coverage": s.coverage,
                "known_limitations": s.known_limitations,
                "fallback": s.fallback,
                "quota_allowed": quota_status["allowed"],
                "cooldown_remaining_sec": quota_status["cooldown_remaining_sec"],
                "health": {
                    "total_calls": health.total_calls,
                    "success_rate_pct": round(success_rate, 1) if success_rate is not None else None,
                    "avg_latency_ms": round(health.avg_latency_ms, 0) if health.avg_latency_ms else None,
                    "last_success": datetime.fromtimestamp(health.last_success_at, tz=timezone.utc).isoformat() if health.last_success_at else None,
                    "last_error": health.last_error_msg,
                },
                "status": self._compute_status(s, health),
            })
        return result

    def record_call(self, source_id: str, success: bool, latency_ms: float, error: Optional[str] = None):
        """记录一次数据源调用（供各 MCP 调用后上报）"""
        with self._lock:
            h = self._health.get(source_id)
            if not h:
                h = SourceHealthRecord(source_id=source_id)
                self._health[source_id] = h

            h.last_call_at = time.time()
            h.total_calls += 1
            h._latency_sum += latency_ms
            h.avg_latency_ms = h._latency_sum / h.total_calls

            if success:
                h.total_successes += 1
                h.last_success_at = time.time()
            else:
                h.total_failures += 1
                h.last_error_at = time.time()
                h.last_error_msg = (error or "Unknown error")[:200]

    def get_reminders(self) -> List[dict]:
        """获取当前需要注意的数据源提醒"""
        reminders = []

        for sid, meta in self._registry.items():
            # Key 未配置
            if meta.requires_key and not meta.key_configured:
                reminders.append({
                    "level": "warning",
                    "source": meta.name,
                    "message": f"{meta.name} 需要 API Key 但未配置，相关数据不可用",
                    "action": f"请在 .env 中配置对应的 API Key",
                })

            # 高失败率
            health = self._health.get(sid)
            if health and health.total_calls >= 5:
                fail_rate = health.total_failures / health.total_calls
                if fail_rate > 0.5:
                    reminders.append({
                        "level": "error",
                        "source": meta.name,
                        "message": f"{meta.name} 失败率 {fail_rate:.0%}，可能存在网络或配置问题",
                        "action": "检查代理设置和 API Key 是否有效",
                    })

            # 高延迟
            if health and health.avg_latency_ms > 10000:
                reminders.append({
                    "level": "warning",
                    "source": meta.name,
                    "message": f"{meta.name} 平均延迟 {health.avg_latency_ms:.0f}ms，响应偏慢",
                    "action": "检查网络连接和代理配置",
                })

        return reminders

    def get_dashboard(self) -> dict:
        """获取数据源管理面板数据"""
        sources = self.list_sources()
        reminders = self.get_reminders()

        # 按类别分组
        by_category = {}
        for s in sources:
            cat = s["category"]
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append(s)

        # 统计
        total = len(sources)
        healthy = sum(1 for s in sources if s["status"] == "healthy")
        degraded = sum(1 for s in sources if s["status"] == "degraded")
        unavailable = sum(1 for s in sources if s["status"] == "unavailable")

        return {
            "summary": {
                "total": total,
                "healthy": healthy,
                "degraded": degraded,
                "unavailable": unavailable,
            },
            "by_category": by_category,
            "reminders": reminders,
            "all_sources": sources,
        }

    def _compute_status(self, meta: DataSourceMeta, health: SourceHealthRecord) -> str:
        """计算数据源状态"""
        if meta.requires_key and not meta.key_configured:
            return "unavailable"
        if health.total_calls >= 3 and health.total_failures / health.total_calls > 0.5:
            return "degraded"
        if health.last_error_at and (not health.last_success_at or health.last_error_at > health.last_success_at):
            return "degraded"
        return "healthy"


# 全局单例
data_source_manager = DataSourceManager()
