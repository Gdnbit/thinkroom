"""MCP 工具描述注册表

为每个 MCP 提供结构化描述，用于：
1. LLM Tool Use / Function Calling（大模型自主决定调哪个工具）
2. 系统文档自动生成
3. 多 Agent 协作时的能力发现

格式兼容 OpenAI Function Calling 和 Anthropic Tool Use。
"""

MCP_TOOL_REGISTRY = [
    {
        "name": "research_memory_mcp",
        "display_name": "私域研究记忆库",
        "description": (
            "查询用户手动上传并结构化存储的私域观点(Claim)。"
            "来源：用户收集的研究报告、专家发言、截图、聊天记录等私域资料。"
            "每条观点含：来源可信度权重(trust_weight)、方向立场(bullish/bearish/neutral)、"
            "适用资产、时间视野(immediate/short/medium/long)、置信度(0-1)、"
            "是否被矛盾标记、观点有效期。"
            "支持语义搜索(pgvector)，按相关性精选，不全量返回。"
        ),
        "when_to_use": "了解某资产的历史研究背景、专家观点积累、私域信息时",
        "data_freshness": "用户手动更新，非实时",
        "parameters": {
            "type": "object",
            "properties": {
                "asset": {"type": "string", "description": "资产代码，如 BTC/ETH/SPX/GOLD"},
                "stance": {"type": "string", "enum": ["bullish","bearish","neutral","any"]},
                "limit": {"type": "integer", "default": 10}
            }
        },
        "returns": [
            "summary: 观点摘要",
            "stance: bullish/bearish/neutral/conditional",
            "confidence: 0-1",
            "speaker_name: 来源人物/机构",
            "trust_weight: 来源可信度权重",
            "time_horizon: 时间视野",
            "asset_scope: 适用资产",
            "is_contradicted: 是否已被矛盾标记",
            "valid_until: 观点有效期"
        ]
    },
    {
        "name": "market_data_mcp",
        "display_name": "市场行情与宏观数据",
        "description": (
            "获取资产实时行情快照和宏观数据。"
            "行情：Binance（加密）+ Yahoo Finance（传统金融），含价格、涨跌幅、"
            "RSI/MA20/MA50/MA200/ATR、趋势方向、最近50根K线。"
            "宏观：FRED，含联邦基金利率、10/2年美债收益率、CPI、失业率（12期历史）。"
            "5分钟缓存，优先从本地DB读取。"
        ),
        "when_to_use": "了解资产当前价格状态、技术指标、宏观利率环境时",
        "data_freshness": "实时（5分钟缓存）",
        "parameters": {
            "type": "object",
            "properties": {
                "asset": {"type": "string", "description": "BTC/ETH/SPX/GOLD/DXY/OIL/NDX/VIX"},
                "timeframe": {"type": "string", "enum": ["1m","5m","15m","1h","4h","1d","1w"], "default": "1d"}
            },
            "required": ["asset"]
        },
        "returns": [
            "price: 当前价格",
            "price_change_1d/7d: 涨跌幅",
            "rsi_14: RSI指标",
            "ma_20/50/200: 均线",
            "atr_14: 波动率",
            "trend_direction: uptrend/downtrend/sideways",
            "volatility_state: low/normal/elevated/extreme",
            "klines_raw: 最近50根K线OHLCV"
        ]
    },
    {
        "name": "flow_continuity_mcp",
        "display_name": "资金流与衍生品信号",
        "description": (
            "获取加密资产衍生品资金流信号，来自 Binance Futures 公开API。"
            "5个维度：资金费率(funding_rate)、未平仓合约量(open_interest)、"
            "多空比(long_short_ratio)、Taker买卖比(taker_ratio)、大户持仓比(top_trader_ratio)。"
            "每个信号含方向(bullish/bearish/neutral)和量化数值。"
            "3分钟缓存，优先从本地DB读取。仅适用于BTC/ETH/BNB/SOL。"
        ),
        "when_to_use": "判断市场多空情绪、资金费率是否过热、OI趋势、大户持仓方向时",
        "data_freshness": "实时（3分钟缓存）",
        "parameters": {
            "type": "object",
            "properties": {
                "asset": {"type": "string", "description": "BTC/ETH/BNB/SOL"},
                "signal_type": {"type": "string",
                    "enum": ["funding_rate","open_interest","long_short_ratio","taker_ratio","top_trader_ratio"]}
            },
            "required": ["asset"]
        },
        "returns": [
            "signal_type: 信号类型",
            "direction: bullish/bearish/neutral",
            "magnitude: 信号强度 0-1",
            "raw_value: 原始数值",
            "description: 信号含义解释"
        ]
    },
    {
        "name": "event_feed_mcp",
        "display_name": "市场事件与新闻",
        "description": (
            "获取最新市场事件和新闻，融合多源："
            "Alpha Vantage（英文+情感评分）、CoinDesk/Reuters RSS、财联社电报、金十数据。"
            "每条事件含重要性评分(0-1)、影响方向、资产范围。"
            "30分钟缓存，优先从本地DB读取（含去重）。"
        ),
        "when_to_use": "了解近期重大市场事件、新闻情感、监管动态时",
        "data_freshness": "实时（30分钟缓存）",
        "parameters": {
            "type": "object",
            "properties": {
                "asset": {"type": "string", "description": "过滤特定资产相关事件"},
                "importance_min": {"type": "number", "default": 0.5, "description": "重要性过滤阈值"},
                "limit": {"type": "integer", "default": 10}
            }
        },
        "returns": [
            "title: 事件标题",
            "summary: 摘要",
            "importance: 0-1 重要性",
            "impact_direction: bullish/bearish/neutral",
            "asset_scope: 影响资产",
            "source_name: 来源"
        ]
    },
    {
        "name": "semantic_search",
        "display_name": "语义检索（RAG）",
        "description": (
            "基于 pgvector 的语义相似性搜索私域 Claim 知识库。"
            "与 research_memory_mcp 不同：这里不是过滤而是'语义最相关'排序。"
            "无 embedding key 时自动退化为关键词搜索。"
            "核心用途：用户问某问题时，自动检索最相关的私域观点作为上下文，"
            "避免把所有观点塞进上下文导致大模型降智。"
        ),
        "when_to_use": "需要从知识库中精选与当前问题最相关的观点时（RAG入口）",
        "data_freshness": "实时检索DB",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "自然语言查询"},
                "asset": {"type": "string", "description": "限定资产范围"},
                "limit": {"type": "integer", "default": 5, "description": "返回最相关的N条"}
            },
            "required": ["query"]
        },
        "returns": [
            "Claim列表（按语义相关性排序）",
            "每条含 summary/stance/confidence/speaker/trust_weight"
        ]
    },
    {
        "name": "social_media_mcp",
        "display_name": "社交媒体信息源",
        "description": (
            "获取社交媒体平台上指定博主/公众号的最新内容。"
            "覆盖5个平台：Twitter/X（via RSSHub）、微博（via RSSHub）、"
            "小红书（via MediaCrawler）、抖音（via MediaCrawler + 视频转文字）、"
            "微信公众号（via We-MP-RSS）。"
            "抖音视频自动通过 Gemini 2.5 Flash 转化为文字稿。"
            "每条内容含：标题、正文/文字稿、平台来源、作者、发布时间。"
            "支持按平台筛选或全量拉取。"
        ),
        "when_to_use": "了解社交媒体上特定KOL的最新观点、行业动态、市场情绪时",
        "data_freshness": "定时拉取（默认30分钟）",
        "parameters": {
            "type": "object",
            "properties": {
                "platform": {
                    "type": "string",
                    "enum": ["twitter", "weibo", "xiaohongshu", "douyin", "wechat_mp", "all"],
                    "default": "all",
                    "description": "指定平台或 all 拉取全部"
                },
                "limit": {"type": "integer", "default": 20}
            }
        },
        "returns": [
            "title: 内容标题",
            "summary: 正文/文字稿",
            "platform: 平台标识",
            "author: 作者/博主",
            "source_url: 原文链接",
            "event_timestamp: 发布时间"
        ]
    },
]


def get_tool_descriptions_for_llm() -> list:
    """返回适合直接传给 LLM 的工具描述列表（OpenAI/Anthropic 格式）"""
    tools = []
    for mcp in MCP_TOOL_REGISTRY:
        tools.append({
            "name": mcp["name"],
            "description": f"{mcp['display_name']}\n{mcp['description']}\n适用场景：{mcp['when_to_use']}\n数据新鲜度：{mcp['data_freshness']}",
            "input_schema": mcp.get("parameters", {}),
        })
    return tools


def get_tool_summary_for_context() -> str:
    """返回适合作为系统提示词的简短工具清单"""
    lines = ["## 可用工具清单\n"]
    for mcp in MCP_TOOL_REGISTRY:
        lines.append(f"### {mcp['display_name']} (`{mcp['name']}`)")  
        lines.append(f"{mcp['description']}")
        lines.append(f"**适用场景**：{mcp['when_to_use']}")
        lines.append(f"**数据新鲜度**：{mcp['data_freshness']}")
        lines.append("")
    return "\n".join(lines)
