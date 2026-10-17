from pydantic_settings import BaseSettings
from pathlib import Path
from typing import List, Optional


class ProductionConfigError(RuntimeError):
    """Raised when production starts with an unsafe default configuration."""


class Settings(BaseSettings):
    # 数据库
    DATABASE_URL: str = "sqlite:///./dev.db"
    DATA_PROFILE: str = "auto"  # auto -> test under APP_ENV=test, otherwise user
    REDIS_ENABLED: bool = False
    REDIS_URL: str = "redis://localhost:6379/0"
    REQUIRE_REDIS_IN_PRODUCTION: bool = True

    # ============================================================
    # OpenRouter 统一 API 配置（2026年4月最新优化）
    # ============================================================
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    OPENROUTER_HTTP_REFERER: str = "https://your-app.example.com"
    OPENROUTER_APP_TITLE: str = "Agent-Decision-Making-System"

    # ── 最终成文 / 研究简报（Claude Sonnet 4.6）──────────────────────
    BRIEFING_MODEL: str = "anthropic/claude-sonnet-4.6"
    BRIEFING_FALLBACK_MODEL: str = "google/gemini-3.1-pro-preview"
    BRIEFING_TEMPERATURE: float = 0.2
    BRIEFING_MAX_TOKENS: int = 2500
    BRIEFING_GENERAL_MAX_TOKENS: int = 1400
    BRIEFING_VERBOSITY: str = "max"
    BRIEFING_REASONING_ENABLED: bool = True
    SELECTED_SCOPE_DIRECT_CLAIM_LIMIT: int = 24

    # ── 辩论委员会四席（2026-04-05 选型）────────────────────────────────
    # A席（主席）：Claude Opus 4.6
    COMMITTEE_MODEL_A: str = "anthropic/claude-opus-4.7"
    COMMITTEE_MODEL_A_FALLBACK: str = "anthropic/claude-sonnet-4.6"
    COMMITTEE_MODEL_A_TEMPERATURE: float = 0.2
    COMMITTEE_MODEL_A_MAX_TOKENS: int = 1800
    COMMITTEE_MODEL_A_REASONING_EFFORT: str = "high"

    # B席（正方）：Gemini 3.1 Pro Preview
    COMMITTEE_MODEL_B: str = "google/gemini-3.1-pro-preview"
    COMMITTEE_MODEL_B_FALLBACK: str = "google/gemini-3-flash-preview"
    COMMITTEE_MODEL_B_TEMPERATURE: float = 0.2
    COMMITTEE_MODEL_B_MAX_TOKENS: int = 1400
    COMMITTEE_MODEL_B_REASONING_EFFORT: str = "medium"

    # C席（反方）：GPT-5.4
    COMMITTEE_MODEL_C: str = "openai/gpt-5.4"
    COMMITTEE_MODEL_C_FALLBACK: str = "openai/gpt-5.4-mini"
    COMMITTEE_MODEL_C_TEMPERATURE: float = 0.1
    COMMITTEE_MODEL_C_MAX_TOKENS: int = 1000

    # D席（市场结构）：GLM-5
    COMMITTEE_MODEL_D_OPTIONAL: str = "z-ai/glm-5"
    COMMITTEE_MODEL_D_TEMPERATURE: float = 0.05
    COMMITTEE_MODEL_D_MAX_TOKENS: int = 900

    # E席（V2 真伪审计）：Gemini Flash — Arena #11，审计需强推理能力
    COMMITTEE_MODEL_E: str = "google/gemini-3-flash-preview"  # Arena #11, 审计需强推理
    COMMITTEE_MODEL_E_FALLBACK: str = "z-ai/glm-5"
    COMMITTEE_MODEL_E_TEMPERATURE: float = 0.1
    COMMITTEE_MODEL_E_MAX_TOKENS: int = 1000

    # F席（V2 执行经济性）：Gemini Flash — 快速推理，成本低
    COMMITTEE_MODEL_F: str = "google/gemini-3-flash-preview"
    COMMITTEE_MODEL_F_FALLBACK: str = "xiaomi/mimo-v2-flash"  # 跨厂商容灾
    COMMITTEE_MODEL_F_TEMPERATURE: float = 0.15
    COMMITTEE_MODEL_F_MAX_TOKENS: int = 1000

    # ── 语义切片/高频重组（Gemini 3 Flash — Arena #11, 非 thinking 模型）──────────────────
    EXTRACTION_MODEL: str = "google/gemini-3-flash-preview"
    EXTRACTION_FALLBACK_MODEL: str = "z-ai/glm-5"           # BFCL #2, JSON 遵从度极高
    EXTRACTION_TEMPERATURE: float = 0.1
    EXTRACTION_MAX_TOKENS: int = 512

    SPEAKER_MODEL: str = "google/gemini-3-flash-preview"
    SPEAKER_FALLBACK_MODEL: str = "xiaomi/mimo-v2-flash"    # SWE 开源 #1, $0.09/$0.29
    SPEAKER_TEMPERATURE: float = 0.1
    SPEAKER_MAX_TOKENS: int = 256

    # ── 结构化字段抽取（MiMo-V2-Flash — 最简单任务用最便宜模型）──────────────────
    DATE_EXTRACTION_MODEL: str = "xiaomi/mimo-v2-flash"
    DATE_EXTRACTION_FALLBACK_MODEL: str = "z-ai/glm-5"      # 可靠备选
    DATE_EXTRACTION_TEMPERATURE: float = 0.0
    DATE_EXTRACTION_MAX_TOKENS: int = 256

    # ── 因果传播链推断（Gemini 3.1 Pro）/ 策略解释（Opus 4.6）────────────────────
    FUSION_MODEL: str = "z-ai/glm-5"
    FUSION_FALLBACK_MODEL: str = "google/gemini-3-flash-preview"
    FUSION_TEMPERATURE: float = 0.15
    FUSION_MAX_TOKENS: int = 1800

    STRATEGY_MODEL: str = "anthropic/claude-opus-4.7"
    STRATEGY_FALLBACK_MODEL: str = "anthropic/claude-sonnet-4.6"
    STRATEGY_TEMPERATURE: float = 0.1
    STRATEGY_MAX_TOKENS: int = 1200

    # ── 对话/规则迭代主模型（Claude Sonnet 4.6）────────────────────────────
    TOOL_ESCALATION_MODEL: str = "anthropic/claude-sonnet-4.6"
    TOOL_ESCALATION_TEMPERATURE: float = 0.2
    TOOL_ESCALATION_MAX_TOKENS: int = 1200

    # ── 测试监督与后处理────────────────────────────
    VERIFICATION_SUPERVISOR_MODEL: str = "openai/gpt-5.4-mini"
    VERIFICATION_SUPERVISOR_FALLBACK: str = "z-ai/glm-5"
    VERIFICATION_SUPERVISOR_MAX_TOKENS: int = 1000

    COMMITTEE_POSTPROCESS_MODEL: str = "openai/gpt-5.4-mini"
    COMMITTEE_POSTPROCESS_FALLBACK: str = "openai/gpt-5.4-nano"
    COMMITTEE_POSTPROCESS_TEMPERATURE: float = 0.2
    COMMITTEE_POSTPROCESS_MAX_TOKENS: int = 2000

    DRIFT_DETECTION_MODEL: str = "openai/gpt-5.4-nano"
    DRIFT_DETECTION_FALLBACK: str = "xiaomi/mimo-v2-flash"
    DRIFT_DETECTION_MAX_TOKENS: int = 400

    # ── 文档入口多模态解析（Gemini 3 Pro）────────────────────────────────────────────────
    DOCUMENT_PARSER_ADAPTER: str = "current"
    DOCUMENT_OCR_MAX_WORKERS: int = 3
    DOCUMENT_OCR_PAGE_DPI: int = 180
    DOCUMENT_OCR_PAGE_SLICE_THRESHOLD: int = 3600
    DOCUMENT_OCR_REQUEST_TIMEOUT_SECONDS: float = 120.0
    DOCUMENT_OCR_MAX_RETRIES: int = 2
    DOCUMENT_OCR_MAX_TOKENS: int = 8000
    DOCUMENT_OCR_REASONING_EFFORT: str = "none"
    DOCUMENT_OCR_FALLBACK_REASONING_EFFORT: str = "minimal"
    DOCUMENT_PDF_OCR_MODEL: str = "google/gemini-3.5-flash"
    DOCUMENT_PDF_OCR_FALLBACK_MODEL: str = "google/gemini-3.1-flash-lite"
    DOCUMENT_PDF_OCR_REASONING_EFFORT: str = "minimal"
    DOCUMENT_PDF_OCR_FALLBACK_REASONING_EFFORT: str = "none"
    DOCUMENT_HYBRID_MIN_VISUAL_COVERAGE: float = 0.15
    DOCUMENT_HYBRID_MIN_DRAWING_COUNT: int = 50
    DOCUMENT_HYBRID_SKIP_DECORATIVE_EDGE_PAIR: bool = True
    PRECHECK_VISION_MODEL: str = "google/gemini-3.1-flash-lite"
    PRECHECK_VISION_TEMPERATURE: float = 0.1
    PRECHECK_VISION_MAX_TOKENS: int = 800

    PRECHECK_VISION_FALLBACK: str = "google/gemini-3.5-flash"
    PRECHECK_VISION_FALLBACK_TEMPERATURE: float = 0.1
    PRECHECK_VISION_FALLBACK_MAX_TOKENS: int = 512

    # ── Embedding 模型────────────────────────────────────────────────
    EMBEDDING_MODEL: str = "qwen/qwen3-embedding-4b"
    EMBEDDING_API_KEY: str = ""
    EMBEDDING_BATCH_SIZE: int = 32
    EMBEDDING_FALLBACK_MODEL: str = "openai/text-embedding-3-small"
    EMBEDDING_QUALITY_MODEL: str = "google/gemini-embedding-001"
    EMBEDDING_DIM: int = 2560  # qwen/qwen3-embedding-4b 输出维度
    PGVECTOR_ENABLED: bool = False  # PostgreSQL 是否安装了 pgvector 扩展；False 时用 TEXT + JSON 序列化

    # ── 路由和可靠性配置────────────────────────────────────────────────
    OPENROUTER_SERVICE_TIER: str = "auto"
    OPENROUTER_PROVIDER_SORT: str = "throughput"
    OPENROUTER_ENABLE_MODEL_FALLBACKS: bool = True
    REGION_FALLBACK_MODEL: str = "z-ai/glm-5"  # Arena #23, BFCL #2, 万能兜底质量保障

    # ── P2-D Load Testing: LLM Mock 模式 ───────────────────────────
    # 设 True（或 .env 里 OPENROUTER_MOCK=1）后，llm_client 跳过实际 API 调用，
    # 返回按 skill_role 分类的确定性 mock 响应（briefing/extraction/fusion/...
    # 各有合理的 JSON 或文本结构）。专为 locust 压测设计：可在不烧 API 预算
    # 的情况下打开 chat / briefing / debate 写入路径，验证应用层（连接池、
    # 队列、JSON 解析、向量写入）在并发下的健康度。
    # 区别于「API key 缺失自动 mock 兜底」（_get_openai_client 返回 None 走的路径）：
    # 此开关是「**显式的**」mock，不依赖 key 是否配置。
    OPENROUTER_MOCK: bool = False
    # mock 模式下模拟的网络延迟（ms）— 让压测看到合理的端到端 P95，而不是 <1ms
    OPENROUTER_MOCK_LATENCY_MS: int = 50

    # ── S3: Prompt Caching（OpenRouter + Anthropic/OpenAI/DeepSeek） ──
    # 对 >= 2000 字符的 system prompt 自动注入 cache_control（Anthropic）或 prompt_cache_key（OpenAI）
    # 可节省 30-70% 重复 prompt 的 token 成本。调试时可关闭。
    ENABLE_PROMPT_CACHING: bool = True

    # ── A4/G11: BGE Cross-Encoder Reranker（二阶段精排） ──
    # 默认关闭，需先下载 ~278MB 模型。启用方法：
    #   1. 安装依赖：pip install FlagEmbedding
    #   2. 可选：设置 HF_HOME 为自己的模型缓存目录
    #   3. 首次运行时自动下载；或手动：
    #      python -c "from FlagEmbedding import FlagReranker; FlagReranker('BAAI/bge-reranker-v2-m3', use_fp16=True)"
    #   4. settings.ENABLE_RERANKER=True
    ENABLE_RERANKER: bool = False
    RERANKER_MODEL: str = "BAAI/bge-reranker-v2-m3"
    RERANKER_TOP_K: int = 8           # 精排后保留条数
    RERANKER_WEIGHT: float = 0.25      # 融合到综合评分中的权重（与语义秩并列）
    HF_HOME: str = ""  # 留空使用模型库默认缓存目录

    # ── G14: Query Rewrite（Step-Back Prompting） ──────────────────
    # 短 query / 含指代代词的问题在 intent 之后、semantic_search 之前先经 LLM 改写，
    # 大幅提升召回质量。LLM 失败 fallback 原 query 不阻塞链路。详见
    # `app/skills/query_rewrite_skill.py`。
    ENABLE_QUERY_REWRITE: bool = True
    QUERY_REWRITE_TRIGGER_MIN_LENGTH: int = 12   # 字符数 < 此值触发改写
    QUERY_REWRITE_HISTORY_TURNS: int = 4          # 参考最近 N 轮对话历史

    # ── G17: 自适应 top-K（按融合 score 阈值动态截断） ──────────────
    # 默认关闭（保守起见，先观察 score 分布再开）。开启后：
    #   - score >= ADAPTIVE_TOPK_MIN_SCORE 的 claim 才进入 primary
    #   - 数量被 clip 到 [ADAPTIVE_TOPK_MIN, top_k * ADAPTIVE_TOPK_MAX_FACTOR]
    #   - 且不超过 ADAPTIVE_TOPK_HARD_MAX 绝对上限
    # 详见 `app/utils/claim_post_processor.py`。
    ADAPTIVE_TOPK_ENABLED: bool = False
    ADAPTIVE_TOPK_MIN_SCORE: float = 0.45        # 融合 score 阈值（低于此视为"低相关"）
    ADAPTIVE_TOPK_MIN: int = 1                   # 最少返回 1 条（保证 LLM 有 context）
    ADAPTIVE_TOPK_MAX_FACTOR: float = 1.5        # 相对 top_k 的倍数上限（caller 给 8 → 最多 12）
    ADAPTIVE_TOPK_HARD_MAX: int = 15             # 绝对上限（防止 LLM token 爆炸)

    # ── G15: Parent-Child Retrieval（命中 claim 时附同源邻居） ──────
    # 默认关闭。开启后命中的每条 claim 通过 ClaimRawSourceLink 查同源邻居 claim
    # （按 created_at 邻近）作为额外 context 注入，提升答案的逻辑链完整性。
    # token 预算由下游 fit_blocks_to_budget 兜底，不会撑爆 context window。
    # 详见 `app/services/parent_context.py`。
    ENABLE_PARENT_CONTEXT: bool = False
    PARENT_CONTEXT_NEIGHBORS: int = 2           # 每个 source 最多附 N 条邻居
    PARENT_CONTEXT_MAX_SOURCES: int = 3         # 整次回答最多扩展 N 个 source

    # ── G16: RAGAS 自动评估（轻量本地实现） ───────────────────────────
    # judge LLM 模型（留空则用 OPENROUTER_MODEL 主模型；建议用便宜模型如 deepseek-v3.2）
    # 详见 `app/services/ragas_evaluator.py` + `app/api/v1/evaluate.py`。
    OPENROUTER_JUDGE_MODEL: str = ""

    # ── 重试策略────────────────────────────────────────────────────
    OPENROUTER_TIMEOUT_SECONDS: int = 30
    OPENROUTER_MAX_RETRIES: int = 4
    OPENROUTER_RETRY_BASE_MS: int = 500
    OPENROUTER_RETRY_MAX_MS: int = 8000

    # ── 结构化输出────────────────────────────────────────────────────
    OPENROUTER_USE_JSON_SCHEMA: bool = True
    OPENROUTER_JSON_SCHEMA_STRICT: bool = True

    # ── 模型调用参数 ─────────────────────────────────────────────
    MAX_TOKENS_PER_REQUEST: int = 4096
    LLM_TEMPERATURE: float = 0.2

    # ── 外部数据源 API Keys ──────────────────────────────────────
    COINGECKO_API_KEY: str = ""
    FRED_API_KEY: str = ""
    ALPHA_VANTAGE_API_KEY: str = ""
    TAVILY_API_KEY: str = ""

    # ── 网络代理（国内环境需要）──────────────────────────────────
    HTTP_PROXY: str = ""
    HTTPS_PROXY: str = ""

    # ── 对象存储（MinIO）──────────────────────────────────────────
    OBJECT_STORAGE_PROVIDER: str = "minio"
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_SECURE: bool = False
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET: str = "agent-files"
    REQUIRE_OBJECT_STORAGE_IN_PRODUCTION: bool = True
    ALLOW_INSECURE_OBJECT_STORAGE_IN_PRODUCTION: bool = False

    # ── 应用配置 ─────────────────────────────────────────────────
    APP_ENV: str = "development"
    DEPLOYMENT_CONFIG_ID: str = ""
    APP_DEBUG: bool = True
    APP_SECRET_KEY: str = "dev-secret-key"
    APP_CORS_ORIGINS: List[str] = ["http://localhost:5173"]
    # P2-B：多实例部署时，可在 backend-only pod 关闭调度器，让独立的 scheduler pod 跑
    # 单实例部署保持 true 即可（默认）
    APP_SCHEDULER_ENABLED: bool = True
    INGESTION_RECOVERY_ENABLED: bool = True
    INGESTION_RECOVERY_MAX_JOBS: int = 3
    INGESTION_RECOVERY_MAX_RETRIES: int = 3
    INGESTION_STALE_REAPER_INTERVAL_MINUTES: int = 5
    INGESTION_JOB_LEASE_SECONDS: int = 600
    INGESTION_DISPATCHER_MODE: str = "auto"
    INGESTION_ASYNC_MAX_WORKERS: int = 2
    INGESTION_ASYNC_MAX_OUTSTANDING: int = 50
    INGESTION_MAX_ACTIVE_JOBS: int = 100
    INGESTION_RESERVATION_TTL_SECONDS: int = 30
    INGESTION_ASYNC_RUN_IN_TESTS: bool = False
    CELERY_BROKER_URL: str = ""
    CELERY_INGESTION_QUEUE: str = "ingestion"
    CELERY_WORKER_CONCURRENCY: int = 2
    CELERY_WORKER_PREFETCH_MULTIPLIER: int = 1
    CELERY_TASK_VISIBILITY_TIMEOUT_SECONDS: int = 4200
    CELERY_TASK_SOFT_TIME_LIMIT_SECONDS: int = 3300
    CELERY_TASK_TIME_LIMIT_SECONDS: int = 3600
    CELERY_WORKER_MAX_TASKS_PER_CHILD: int = 50
    CELERY_WORKER_MAX_MEMORY_PER_CHILD_KB: int = 1500000
    CELERY_INSPECT_TIMEOUT_SECONDS: float = 1.0

    # ── 认证（P0-1，2026-04-22 新增）─────────────────────────────
    # APP_API_KEY: 为空则认证完全禁用（dev/test 默认行为）；
    #              非空则所有 /api/v1/** 请求必须带 `X-API-Key: <value>` header。
    # 生产环境必填，用 `python -c "import secrets; print(secrets.token_urlsafe(32))"` 生成。
    APP_API_KEY: str = ""
    # 认证白名单（不校验的 path 前缀）—— 健康检查、OpenAPI 文档、监控端点
    APP_AUTH_WHITELIST: List[str] = ["/health", "/docs", "/redoc", "/openapi.json", "/metrics"]

    # ── 可观测性（P1，2026-04-24 新增）────────────────────────────
    # 结构化日志
    # LOG_FORMAT: "json" (生产推荐，单行 JSON) | "pretty" (dev 彩色人类可读)
    LOG_FORMAT: str = "pretty"
    LOG_LEVEL: str = "INFO"
    # 日志文件目录（相对 backend 目录）；空字符串表示仅输出到 stdout
    LOG_DIR: str = "logs"
    LOG_RETENTION_DAYS: int = 30

    # Sentry 错误追踪；DSN 为空则完全跳过 init
    SENTRY_DSN: str = ""
    SENTRY_ENVIRONMENT: str = ""  # 空则取 APP_ENV
    SENTRY_TRACES_SAMPLE_RATE: float = 0.0  # 0 关闭 performance tracing；生产建议 0.05-0.1
    SENTRY_RELEASE: str = ""  # git sha 或版本号，CI 注入

    # 限流（slowapi）
    # 默认策略：未被显式装饰的端点不限流；关键端点在 api/v1 中加 @limiter.limit(...)
    # 全局默认（所有端点，IP 维度），可在单端点用 @limiter.limit("...") 覆盖
    RATE_LIMIT_DEFAULT: str = "300/minute"
    # 关键端点（chat / briefing / verification / claim search）
    RATE_LIMIT_CHAT: str = "30/minute"
    RATE_LIMIT_BRIEFING: str = "20/minute"
    RATE_LIMIT_VERIFICATION: str = "3/minute"
    RATE_LIMIT_SEARCH: str = "60/minute"
    RATE_LIMIT_STORAGE_URI: str = "memory://"

    # ── 功能开关（Mock 模式）──────────────────────────────────────
    USE_MOCK_EVENT_FEED: bool = False
    USE_MOCK_MARKET_DATA: bool = False
    USE_MOCK_FLOW_DATA: bool = False

    # ── Twitter KOL 账号列表────────────────────────────────────────
    TWITTER_ACCOUNTS: str = "CryptoCred,PeterLBrandt,glassnode,federalreserve"

    # ============================================================
    # 社交媒体信息源配置（RSSHub + MediaCrawler + We-MP-RSS）
    # ============================================================

    # ── RSSHub（用于 Twitter/X + 微博）─────────────────────────
    RSSHUB_BASE_URL: str = "http://localhost:1200"

    # Twitter/X via RSSHub
    TWITTER_COOKIE: str = ""  # Twitter auth_token cookie
    TWITTER_COOKIE_UPDATED_AT: str = ""  # ISO 时间戳
    SOCIAL_TWITTER_ACCOUNTS: str = ""  # 逗号分隔: elonmusk,CryptoCred,...

    # 微博 via RSSHub
    WEIBO_COOKIE: str = ""  # 微博登录后的 cookie
    WEIBO_COOKIE_UPDATED_AT: str = ""
    SOCIAL_WEIBO_UIDS: str = ""  # 逗号分隔微博 UID: 1234567890,9876543210,...

    # ── MediaCrawler（用于小红书 + 抖音）────────────────────────
    MEDIACRAWLER_API_URL: str = "http://localhost:8080"  # MediaCrawler WebUI API
    MEDIACRAWLER_ROOT: str = str(Path(__file__).resolve().parents[3] / "integrations" / "MediaCrawler")

    # 小红书
    XHS_COOKIE: str = ""
    XHS_COOKIE_UPDATED_AT: str = ""
    SOCIAL_XHS_USER_IDS: str = ""  # 逗号分隔小红书用户 ID

    # 抖音
    DOUYIN_COOKIE: str = ""
    DOUYIN_COOKIE_UPDATED_AT: str = ""
    SOCIAL_DOUYIN_USER_IDS: str = ""  # 逗号分隔抖音用户 sec_uid

    # ── We-MP-RSS（用于微信公众号）──────────────────────────────
    WEMPRSS_BASE_URL: str = "http://localhost:8090"
    WEMPRSS_API_TOKEN: str = ""
    WECHAT_AUTH_UPDATED_AT: str = ""
    SOCIAL_WECHAT_ACCOUNTS: str = ""  # 逗号分隔公众号名称/biz_id

    # ── 抖音视频转文字 ───────────────────────────────────────
    # 主选：Google AI Studio 直连（免费，需 GOOGLE_AI_API_KEY）
    # 备选：通过 OpenRouter（付费但极便宜）
    GOOGLE_AI_API_KEY: str = ""  # 在 https://aistudio.google.com 免费申请
    VIDEO_TRANSCRIBE_MODEL: str = "gemini-2.5-flash"  # Google AI Studio 模型名
    VIDEO_TRANSCRIBE_FALLBACK_MODEL: str = "google/gemini-2.5-flash-preview"  # OpenRouter 模型名（备选）
    VIDEO_TRANSCRIBE_MAX_TOKENS: int = 4096

    # ── 社媒拉取频率（分钟）──────────────────────────────────
    SOCIAL_FETCH_INTERVAL_MINUTES: int = 360  # 默认每 6 小时（每天 4 次）

    class Config:
        env_file = [".env", "apps/backend/.env"]
        env_file_encoding = "utf-8"


settings = Settings()


def production_config_errors(cfg: Settings = settings) -> list[str]:
    """Return unsafe production settings without mutating the active configuration."""
    if (cfg.APP_ENV or "").lower() not in {"production", "prod"}:
        return []

    errors: list[str] = []
    if (cfg.DATABASE_URL or "").strip().lower().startswith("sqlite"):
        errors.append("DATABASE_URL must not use SQLite in production")
    if not (cfg.DEPLOYMENT_CONFIG_ID or "").strip():
        errors.append("DEPLOYMENT_CONFIG_ID must be set in production")
    if cfg.APP_DEBUG:
        errors.append("APP_DEBUG must be false in production")
    if not (cfg.APP_API_KEY or "").strip():
        errors.append("APP_API_KEY must be set in production")
    if (cfg.APP_SECRET_KEY or "") in {"", "dev-secret-key"} or len(cfg.APP_SECRET_KEY or "") < 32:
        errors.append("APP_SECRET_KEY must be a non-default secret with at least 32 characters")
    if cfg.OPENROUTER_MOCK:
        errors.append("OPENROUTER_MOCK must be false in production")
    if not (cfg.OPENROUTER_API_KEY or "").strip():
        errors.append("OPENROUTER_API_KEY must be set in production")
    if cfg.REQUIRE_REDIS_IN_PRODUCTION and not cfg.REDIS_ENABLED:
        errors.append("REDIS_ENABLED must be true in production when REQUIRE_REDIS_IN_PRODUCTION is true")
    dispatcher_mode = str(cfg.INGESTION_DISPATCHER_MODE or "auto").strip().lower()
    if dispatcher_mode == "auto":
        dispatcher_mode = "celery"
    if dispatcher_mode != "celery":
        errors.append("INGESTION_DISPATCHER_MODE must resolve to celery in production")
    broker_url = str(cfg.CELERY_BROKER_URL or cfg.REDIS_URL or "").strip().lower()
    if not broker_url.startswith(("redis://", "rediss://")):
        errors.append("CELERY_BROKER_URL must use Redis in production")
    if not str(cfg.CELERY_INGESTION_QUEUE or "").strip():
        errors.append("CELERY_INGESTION_QUEUE must be set in production")
    celery_concurrency = int(cfg.CELERY_WORKER_CONCURRENCY)
    if celery_concurrency < 1:
        errors.append("CELERY_WORKER_CONCURRENCY must be at least 1 in production")
    if int(cfg.CELERY_WORKER_PREFETCH_MULTIPLIER) != 1:
        errors.append("CELERY_WORKER_PREFETCH_MULTIPLIER must be 1 for long-running ingestion tasks")
    celery_soft_limit = int(cfg.CELERY_TASK_SOFT_TIME_LIMIT_SECONDS)
    celery_hard_limit = int(cfg.CELERY_TASK_TIME_LIMIT_SECONDS)
    celery_visibility_timeout = int(cfg.CELERY_TASK_VISIBILITY_TIMEOUT_SECONDS)
    if celery_soft_limit <= 0:
        errors.append("CELERY_TASK_SOFT_TIME_LIMIT_SECONDS must be positive in production")
    if celery_hard_limit <= 0:
        errors.append("CELERY_TASK_TIME_LIMIT_SECONDS must be positive in production")
    if celery_soft_limit >= celery_hard_limit:
        errors.append("CELERY_TASK_SOFT_TIME_LIMIT_SECONDS must be lower than CELERY_TASK_TIME_LIMIT_SECONDS")
    if celery_visibility_timeout < celery_hard_limit + 300:
        errors.append(
            "CELERY_TASK_VISIBILITY_TIMEOUT_SECONDS must exceed "
            "CELERY_TASK_TIME_LIMIT_SECONDS by at least 300 seconds"
        )
    if celery_concurrency > 0 and int(cfg.INGESTION_ASYNC_MAX_OUTSTANDING) < celery_concurrency:
        errors.append(
            "INGESTION_ASYNC_MAX_OUTSTANDING must be at least "
            "CELERY_WORKER_CONCURRENCY in production"
        )
    if celery_concurrency > 0 and int(cfg.INGESTION_MAX_ACTIVE_JOBS) < celery_concurrency:
        errors.append(
            "INGESTION_MAX_ACTIVE_JOBS must be at least "
            "CELERY_WORKER_CONCURRENCY in production"
        )
    if (cfg.RATE_LIMIT_STORAGE_URI or "").strip().lower() == "memory://":
        errors.append("RATE_LIMIT_STORAGE_URI must use shared storage such as Redis in production")
    if cfg.REQUIRE_OBJECT_STORAGE_IN_PRODUCTION:
        if not (cfg.MINIO_ENDPOINT or "").strip():
            errors.append("MINIO_ENDPOINT must be set in production when REQUIRE_OBJECT_STORAGE_IN_PRODUCTION is true")
        if not (cfg.MINIO_BUCKET or "").strip():
            errors.append("MINIO_BUCKET must be set in production when REQUIRE_OBJECT_STORAGE_IN_PRODUCTION is true")
        if not cfg.MINIO_SECURE and not cfg.ALLOW_INSECURE_OBJECT_STORAGE_IN_PRODUCTION:
            errors.append(
                "MINIO_SECURE must be true in production when object storage is required, "
                "unless ALLOW_INSECURE_OBJECT_STORAGE_IN_PRODUCTION is explicitly true"
            )
    if (cfg.MINIO_ACCESS_KEY, cfg.MINIO_SECRET_KEY) == ("minioadmin", "minioadmin"):
        errors.append("MinIO default credentials must not be used in production")
    return errors


def build_deployment_profile(cfg: Settings = settings) -> dict[str, object]:
    """Describe which readiness checks are required for this runtime profile."""
    is_production = (cfg.APP_ENV or "").lower() in {"production", "prod"}
    required_checks = ["database", "openrouter"]
    optional_checks: list[str] = []

    if is_production and cfg.REQUIRE_REDIS_IN_PRODUCTION:
        required_checks.append("redis")
    else:
        optional_checks.append("redis")

    if is_production and cfg.REQUIRE_OBJECT_STORAGE_IN_PRODUCTION:
        required_checks.append("minio")
    else:
        optional_checks.append("minio")

    if is_production:
        required_checks.append("ingestion_worker")

    return {
        "mode": "production_required" if is_production else "local_mvp",
        "label": "生产必配" if is_production else "本地 MVP",
        "allows_degraded_optional_dependencies": bool(optional_checks),
        "required_checks": required_checks,
        "optional_checks": optional_checks,
        "blocking_checks": [],
    }


def assert_production_safe(cfg: Settings = settings) -> None:
    errors = production_config_errors(cfg)
    if errors:
        raise ProductionConfigError("; ".join(errors))
