"""Settings API.

Non-secret runtime settings can be persisted to the local .env compatibility file.
API keys are read from the process environment injected by SOPS/secrets env and
are never written back through this API.
"""
import os
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter()

# Local compatibility config path. Do not persist API keys here.
ENV_PATH = Path(os.getcwd()) / ".env"


class SettingsResponse(BaseModel):
    openrouter_api_key_set: bool
    google_ai_api_key_set: bool
    openrouter_base_url: str
    # 模型配置
    extraction_model: str
    fusion_model: str
    strategy_model: str
    speaker_model: str
    briefing_model: str
    committee_model_a: str
    committee_model_b: str
    committee_model_c: str
    committee_model_d: str
    committee_model_e: str
    committee_model_f: str
    date_extraction_model: str
    embedding_model: str
    # 外部数据源
    coingecko_api_key_set: bool
    fred_api_key_set: bool
    alpha_vantage_api_key_set: bool
    tavily_api_key_set: bool
    # 功能开关
    use_mock_event_feed: bool
    use_mock_market_data: bool
    use_mock_flow_data: bool
    # 系统信息
    app_env: str
    database_url_type: str
    llm_temperature: float
    max_tokens: int


class SettingsUpdateRequest(BaseModel):
    openrouter_api_key: Optional[str] = None
    google_ai_api_key: Optional[str] = None
    openrouter_base_url: Optional[str] = None
    # 模型配置
    extraction_model: Optional[str] = None
    fusion_model: Optional[str] = None
    strategy_model: Optional[str] = None
    speaker_model: Optional[str] = None
    briefing_model: Optional[str] = None
    committee_model_a: Optional[str] = None
    committee_model_b: Optional[str] = None
    committee_model_c: Optional[str] = None
    committee_model_d: Optional[str] = None
    committee_model_e: Optional[str] = None
    committee_model_f: Optional[str] = None
    date_extraction_model: Optional[str] = None
    embedding_model: Optional[str] = None
    # 外部数据源
    coingecko_api_key: Optional[str] = None
    fred_api_key: Optional[str] = None
    alpha_vantage_api_key: Optional[str] = None
    tavily_api_key: Optional[str] = None
    # 功能开关
    use_mock_event_feed: Optional[bool] = None
    use_mock_market_data: Optional[bool] = None
    use_mock_flow_data: Optional[bool] = None
    llm_temperature: Optional[float] = None
    max_tokens: Optional[int] = None


SECRET_UPDATE_FIELDS = {
    "openrouter_api_key": "OPENROUTER_API_KEY",
    "google_ai_api_key": "GOOGLE_AI_API_KEY",
    "coingecko_api_key": "COINGECKO_API_KEY",
    "fred_api_key": "FRED_API_KEY",
    "alpha_vantage_api_key": "ALPHA_VANTAGE_API_KEY",
    "tavily_api_key": "TAVILY_API_KEY",
}


def _collect_secret_update_keys(body: SettingsUpdateRequest) -> list[str]:
    return [
        env_key
        for field_name, env_key in SECRET_UPDATE_FIELDS.items()
        if getattr(body, field_name) not in (None, "")
    ]


def _read_env() -> dict:
    """Read non-secret .env compatibility settings."""
    result = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                result[k.strip()] = v.strip()
    return result


def _write_env(data: dict):
    """Persist non-secret compatibility settings to .env."""
    lines = []
    if ENV_PATH.exists():
        existing_lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
        written_keys = set()
        for line in existing_lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k = stripped.split("=", 1)[0].strip()
                if k in data:
                    lines.append(f"{k}={data[k]}")
                    written_keys.add(k)
                else:
                    lines.append(line)
            else:
                lines.append(line)
        # 追加新 key
        for k, v in data.items():
            if k not in written_keys:
                lines.append(f"{k}={v}")
    else:
        for k, v in data.items():
            lines.append(f"{k}={v}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


@router.get("", response_model=SettingsResponse)
async def get_settings():
    """Return runtime configuration status; API keys are exposed only as booleans."""
    from app.config import settings
    db_url = settings.DATABASE_URL
    db_type = "sqlite" if db_url.startswith("sqlite") else "postgresql"
    return SettingsResponse(
        openrouter_api_key_set=bool(settings.OPENROUTER_API_KEY),
        google_ai_api_key_set=bool(settings.GOOGLE_AI_API_KEY),
        openrouter_base_url=settings.OPENROUTER_BASE_URL,
        extraction_model=settings.EXTRACTION_MODEL,
        fusion_model=settings.FUSION_MODEL,
        strategy_model=settings.STRATEGY_MODEL,
        speaker_model=settings.SPEAKER_MODEL,
        briefing_model=settings.BRIEFING_MODEL,
        committee_model_a=settings.COMMITTEE_MODEL_A,
        committee_model_b=settings.COMMITTEE_MODEL_B,
        committee_model_c=settings.COMMITTEE_MODEL_C,
        committee_model_d=settings.COMMITTEE_MODEL_D_OPTIONAL,
        committee_model_e=settings.COMMITTEE_MODEL_E,
        committee_model_f=settings.COMMITTEE_MODEL_F,
        date_extraction_model=settings.DATE_EXTRACTION_MODEL,
        embedding_model=settings.EMBEDDING_MODEL,
        coingecko_api_key_set=bool(settings.COINGECKO_API_KEY),
        fred_api_key_set=bool(settings.FRED_API_KEY),
        alpha_vantage_api_key_set=bool(settings.ALPHA_VANTAGE_API_KEY),
        tavily_api_key_set=bool(settings.TAVILY_API_KEY),
        use_mock_event_feed=settings.USE_MOCK_EVENT_FEED,
        use_mock_market_data=settings.USE_MOCK_MARKET_DATA,
        use_mock_flow_data=settings.USE_MOCK_FLOW_DATA,
        app_env=settings.APP_ENV,
        database_url_type=db_type,
        llm_temperature=settings.LLM_TEMPERATURE,
        max_tokens=settings.MAX_TOKENS_PER_REQUEST,
    )


@router.post("", response_model=dict)
async def update_settings(body: SettingsUpdateRequest):
    """Update non-secret runtime settings; API key fields are ignored by policy."""
    from app.config import settings
    env = _read_env()
    updates = {}
    ignored_secret_keys = _collect_secret_update_keys(body)
    empty_secret_update = {field_name: None for field_name in SECRET_UPDATE_FIELDS}
    if hasattr(body, "model_copy"):
        body = body.model_copy(update=empty_secret_update)
    else:
        body = body.copy(update=empty_secret_update)

    if body.openrouter_base_url is not None:
        updates["OPENROUTER_BASE_URL"] = body.openrouter_base_url
        settings.OPENROUTER_BASE_URL = body.openrouter_base_url

    # 模型配置
    if body.extraction_model is not None:
        updates["EXTRACTION_MODEL"] = body.extraction_model
        settings.EXTRACTION_MODEL = body.extraction_model
    if body.fusion_model is not None:
        updates["FUSION_MODEL"] = body.fusion_model
        settings.FUSION_MODEL = body.fusion_model
    if body.strategy_model is not None:
        updates["STRATEGY_MODEL"] = body.strategy_model
        settings.STRATEGY_MODEL = body.strategy_model
    if body.speaker_model is not None:
        updates["SPEAKER_MODEL"] = body.speaker_model
        settings.SPEAKER_MODEL = body.speaker_model
    if body.briefing_model is not None:
        updates["BRIEFING_MODEL"] = body.briefing_model
        settings.BRIEFING_MODEL = body.briefing_model
    if body.committee_model_a is not None:
        updates["COMMITTEE_MODEL_A"] = body.committee_model_a
        settings.COMMITTEE_MODEL_A = body.committee_model_a
    if body.committee_model_b is not None:
        updates["COMMITTEE_MODEL_B"] = body.committee_model_b
        settings.COMMITTEE_MODEL_B = body.committee_model_b
    if body.committee_model_c is not None:
        updates["COMMITTEE_MODEL_C"] = body.committee_model_c
        settings.COMMITTEE_MODEL_C = body.committee_model_c
    if body.committee_model_d is not None:
        updates["COMMITTEE_MODEL_D_OPTIONAL"] = body.committee_model_d
        settings.COMMITTEE_MODEL_D_OPTIONAL = body.committee_model_d
    if body.committee_model_e is not None:
        updates["COMMITTEE_MODEL_E"] = body.committee_model_e
        settings.COMMITTEE_MODEL_E = body.committee_model_e
    if body.committee_model_f is not None:
        updates["COMMITTEE_MODEL_F"] = body.committee_model_f
        settings.COMMITTEE_MODEL_F = body.committee_model_f
    if body.date_extraction_model is not None:
        updates["DATE_EXTRACTION_MODEL"] = body.date_extraction_model
        settings.DATE_EXTRACTION_MODEL = body.date_extraction_model
    if body.embedding_model is not None:
        updates["EMBEDDING_MODEL"] = body.embedding_model
        settings.EMBEDDING_MODEL = body.embedding_model

    # 功能开关
    if body.use_mock_event_feed is not None:
        updates["USE_MOCK_EVENT_FEED"] = str(body.use_mock_event_feed).lower()
        settings.USE_MOCK_EVENT_FEED = body.use_mock_event_feed
    if body.use_mock_market_data is not None:
        updates["USE_MOCK_MARKET_DATA"] = str(body.use_mock_market_data).lower()
        settings.USE_MOCK_MARKET_DATA = body.use_mock_market_data
    if body.use_mock_flow_data is not None:
        updates["USE_MOCK_FLOW_DATA"] = str(body.use_mock_flow_data).lower()
        settings.USE_MOCK_FLOW_DATA = body.use_mock_flow_data

    # 模型参数
    if body.llm_temperature is not None:
        if not 0.0 <= body.llm_temperature <= 2.0:
            raise HTTPException(status_code=422, detail="temperature must be 0.0-2.0")
        updates["LLM_TEMPERATURE"] = str(body.llm_temperature)
        settings.LLM_TEMPERATURE = body.llm_temperature
    if body.max_tokens is not None:
        if not 256 <= body.max_tokens <= 32768:
            raise HTTPException(status_code=422, detail="max_tokens must be 256-32768")
        updates["MAX_TOKENS_PER_REQUEST"] = str(body.max_tokens)
        settings.MAX_TOKENS_PER_REQUEST = body.max_tokens

    if updates:
        env.update(updates)
        _write_env(env)
        # 保存后清空 LLM 客户端缓存，确保下次调用使用新配置
        try:
            from app.utils.llm_client import llm_client
            llm_client._openai = None
            llm_client._openai_by_role = {}
        except Exception:
            pass
        # 刷新模型角色注册表缓存
        try:
            from app.utils.role_registry import refresh_cache
            refresh_cache()
        except Exception:
            pass

    return {
        "success": True,
        "updated_keys": list(updates.keys()),
        "ignored_secret_keys": ignored_secret_keys,
        "secret_policy": "API keys are loaded from SOPS/secrets env and are not written to .env.",
    }


@router.post("/test-connection", response_model=dict)
async def test_connection():
    """测试当前 OpenRouter API Key 是否可用"""
    import asyncio
    from app.config import settings
    from app.utils.llm_client import llm_client
    try:
        if not settings.OPENROUTER_API_KEY:
            return {"success": False, "error": "OPENROUTER_API_KEY not configured"}
        
        # 在线程池中执行同步 LLM 调用，避免阻塞事件循环
        result = await asyncio.to_thread(
            llm_client.embed, "test", model=settings.EMBEDDING_MODEL
        )
        if result:
            return {
                "success": True,
                "provider": "OpenRouter",
                "model": settings.EMBEDDING_MODEL,
                "embedding_dims": len(result[0]) if result[0] else 0,
            }
        else:
            return {"success": False, "error": "Embedding call returned empty"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/datasource-health", response_model=dict)
async def datasource_health():
    """Ping 各数据接口，返回在线/离线状态"""
    import asyncio
    import httpx
    from app.config import settings

    services = [
        {"id": "rsshub", "name": "RSSHub（Twitter/微博）", "url": settings.RSSHUB_BASE_URL},
        {"id": "mediacrawler", "name": "MediaCrawler（小红书/抖音）", "url": settings.MEDIACRAWLER_API_URL},
        {"id": "wemprss", "name": "We-MP-RSS（微信公众号）", "url": settings.WEMPRSS_BASE_URL},
    ]

    async def _ping(svc: dict) -> dict:
        url = svc["url"].rstrip("/")
        if not url:
            return {**svc, "online": False, "latency_ms": None, "error": "未配置"}
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(url)
                return {**svc, "online": r.status_code < 500, "latency_ms": round(r.elapsed.total_seconds() * 1000), "error": None}
        except Exception as e:
            return {**svc, "online": False, "latency_ms": None, "error": str(e)[:120]}

    results = await asyncio.gather(*[_ping(s) for s in services])

    # 外部 API（只检查 key 是否已配置 + 是否免 key）
    external_apis = [
        {"id": "binance", "name": "Binance 行情/资金费率", "requires_key": False, "key_set": True, "note": "免 Key 公开 API"},
        {"id": "yahoo", "name": "Yahoo Finance", "requires_key": False, "key_set": True, "note": "免 Key 公开 API"},
        {"id": "rss_news", "name": "RSS 新闻源（CoinDesk 等）", "requires_key": False, "key_set": True, "note": "免 Key RSS"},
        {"id": "coingecko", "name": "CoinGecko", "requires_key": True, "key_set": bool(settings.COINGECKO_API_KEY), "note": "Demo Key 可用"},
        {"id": "fred", "name": "FRED 宏观经济", "requires_key": True, "key_set": bool(settings.FRED_API_KEY), "note": "免费申请"},
        {"id": "alpha_vantage", "name": "Alpha Vantage", "requires_key": True, "key_set": bool(settings.ALPHA_VANTAGE_API_KEY), "note": "免费申请"},
        {"id": "tavily", "name": "Tavily 搜索", "requires_key": True, "key_set": bool(settings.TAVILY_API_KEY), "note": "搜索增强"},
        {"id": "fear_greed", "name": "Fear & Greed Index", "requires_key": False, "key_set": True, "note": "免 Key 公开 API"},
        {"id": "defillama", "name": "DeFiLlama TVL", "requires_key": False, "key_set": True, "note": "免 Key 公开 API"},
        {"id": "glassnode", "name": "Glassnode 链上（免费层）", "requires_key": False, "key_set": True, "note": "免 Key 免费层"},
    ]

    # Mock 状态
    mock_status = {
        "event_feed": settings.USE_MOCK_EVENT_FEED,
        "market_data": settings.USE_MOCK_MARKET_DATA,
        "flow_data": settings.USE_MOCK_FLOW_DATA,
    }

    return {"services": list(results), "external_apis": external_apis, "mock_status": mock_status}


@router.get("/quota-status", response_model=list)
async def get_quota_status():
    """获取各外部 API 的当前配额状态（冷却剩余时间）"""
    from app.services.api_quota import get_all_status
    return get_all_status()


@router.post("/quota-reset/{resource}", response_model=dict)
async def reset_quota(resource: str, freq: str = "FAST"):
    """强制重置某资源的配额冷却锁（用于调试或手动刷新）"""
    from app.services.api_quota import guard
    guard.reset(resource, freq)
    return {"success": True, "message": f"{resource} ({freq}) quota reset"}


@router.get("/model-routing", response_model=list)
def model_routing():
    """返回当前模型路由表（实时读取 settings，切换后即时反映）

    自动发现所有模型角色，新增席位/技能后无需改代码。
    """
    from app.utils.role_registry import get_routing_table
    return get_routing_table()


@router.get("/features", response_model=list)
def feature_status():
    """返回功能模块状态（实时读取配置与系统状态）"""
    from app.config import settings
    from app.utils.role_registry import get_committee_seats

    seats = get_committee_seats()
    seat_labels = " / ".join(s.label.split(" · ")[-1] if " · " in s.label else s.seat_id
                              for s in seats) if seats else "无"
    features = [
        {
            "label": "委员会辩论",
            "detail": f"{len(seats)}席动态激活：{seat_labels}",
            "status": "active",
        },
        {
            "label": "信号评分",
            "detail": "8种事件类型自适应权重（macro_policy/central_bank/earnings/crypto_*等）",
            "status": "active",
        },
        {
            "label": "观点质量",
            "detail": "宽容评分 A/B/C/D，D 仅限致命问题；支持语义合并 merge_claims",
            "status": "active",
        },
        {
            "label": "时间范围",
            "detail": "双轨设计：event_date+衰减 vs influence_window 持续影响",
            "status": "active",
        },
        {
            "label": "成本追踪",
            "detail": "自动记录每次 LLM 调用，日/月聚合，预算告警",
            "status": "active",
        },
        {
            "label": "数据源管理",
            "detail": "16 个源含社交媒体 / Polymarket / DeFiLlama / Glassnode 等",
            "status": "active",
        },
        {
            "label": "社交媒体集成",
            "detail": "Twitter/微博/小红书/抖音/微信公众号，Cookie 管理 + 视频转写",
            "status": "active",
        },
        {
            "label": "决策回放",
            "detail": "DecisionSnapshot 冻结决策时刻证据，事后 LLM 回放分析",
            "status": "active",
        },
        {
            "label": "意图识别",
            "detail": "8 种模式：briefing/debate/strategy/watchlist/replay/scout/audit/chat",
            "status": "active",
        },
        {
            "label": "风险引擎",
            "detail": "ATR 仓位计算+相关性矩阵（account/position 预留，纯计算）",
            "status": "reserved",
        },
    ]

    # 动态标注 mock 状态
    if settings.USE_MOCK_EVENT_FEED:
        features.append({"label": "Mock 事件源", "detail": "USE_MOCK_EVENT_FEED=true，使用模拟数据", "status": "mock"})
    if settings.USE_MOCK_MARKET_DATA:
        features.append({"label": "Mock 行情数据", "detail": "USE_MOCK_MARKET_DATA=true，使用模拟数据", "status": "mock"})
    if settings.USE_MOCK_FLOW_DATA:
        features.append({"label": "Mock 资金数据", "detail": "USE_MOCK_FLOW_DATA=true，使用模拟数据", "status": "mock"})

    return features
