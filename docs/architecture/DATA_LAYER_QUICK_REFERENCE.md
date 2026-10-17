# 数据层快速参考指南

> **历史架构参考**：字段、调度和数据源说明需按当前代码重新核对。

**最近更新**：2026-04-18（衍生指标 + scheduler 重构）

## 一句话总结

系统通过 **三个输入端**（市场数据、事件新闻、私域内容）→ **8 个 MCP**（处理层）→ **多张数据卡**（存储层）的架构，实现完整的信息摄入、处理和存储。
2026-04-18 新增 **衍生指标层**：Pizza Index / Fear&Greed / Polymarket 通过独立 `indicator_fetchers.py` 抓取，落入 `market_snapshots`（`market=INDICATOR`）。

---

## 快速查询表

### Q1: 我可以通过订阅源获取哪些数据？

**答**：通过 RSS/API 订阅源可以获取：

| 数据类型 | 来源 | 具体内容 | 更新频率 |
|---------|------|---------|---------|
| **加密新闻** | CoinDesk / CoinTelegraph | BTC/ETH 相关新闻 | 实时 |
| **传统金融新闻** | Reuters | SPX/GOLD/DXY 相关新闻 | 实时 |
| **中文快讯** | CLS Telegraph / Jin10 | 金融/加密快讯 | 实时 |
| **KOL 推文** | Nitter RSS（Twitter 镜像） | 自定义账号推文 | 实时 |
| **加密市价** | Binance API | BTC/ETH/BNB/SOL/XRP/DOGE 价格 | 实时 |
| **股票/指数** | Yahoo Finance | SPX/NDX/VIX/GOLD/DXY/OIL | 日度 |
| **加密市值** | CoinGecko | BTC/ETH 市值、流动性、涨跌 | 日度 |
| **宏观数据** | FRED | 联邦基金利率、美债收益率、CPI、失业率 | 周度/月度 |

---

### Q2: 除了订阅源，还能输入哪些内容？

**答**：除了订阅源，还可以输入：

| 输入方式 | 内容类型 | 处理方式 | 存储位置 |
|---------|---------|---------|---------|
| **用户上传** | PDF/Word/图片/视频 | 文本提取 + OCR + 转录 | MinIO + PostgreSQL |
| **URL 爬取** | 网页文章 | 自动爬取 + 文本提取 | MinIO + PostgreSQL |
| **粘贴文本** | 任意文本 | 直接存储 | PostgreSQL |
| **网络搜索** | 搜索结果 | 结果聚合 + 情感分析 | PostgreSQL |

---

### Q3: 这些数据是如何处理的？

**答**：分为五个 MCP 处理：

#### 1️⃣ market_data_mcp（市场数据处理）

```
Binance/Yahoo/CoinGecko/FRED API
    ↓
计算技术指标（RSI/ATR/MA/趋势/波动率）
    ↓
生成 MarketStateCard
    ↓
存储到 PostgreSQL + Redis 缓存
```

**输出数据**：
- 价格、涨跌幅、技术指标、趋势方向、波动率状态
- 支撑/阻力位、K线原始数据

#### 2️⃣ event_feed_mcp（RSS 新闻处理）

```
RSS 源（CoinDesk/Reuters/CoinTelegraph）
    ↓
关键词情感分析（bullish/bearish/neutral）
    ↓
计算重要性评分（0-1）
    ↓
生成 EventCard
    ↓
存储到 PostgreSQL + Redis 缓存
```

**输出数据**：
- 标题、摘要、情感方向、重要性、资产范围、主题范围

#### 3️⃣ news_realtime_mcp（中文快讯 + KOL 推文处理）

```
CLS Telegraph + Nitter RSS
    ↓
中文关键词分析
    ↓
重要性评分
    ↓
生成 EventCard
    ↓
存储到 PostgreSQL
```

**输出数据**：
- 快讯标题、摘要、情感方向、重要性、KOL 账号

#### 4️⃣ flow_continuity_mcp（资金流信号处理）

```
链上数据 / 交易所 API
    ↓
计算信号（funding_rate/open_interest/netflow 等）
    ↓
追踪连续性（持续天数、趋势变化）
    ↓
生成 FlowSignalCard
    ↓
存储到 PostgreSQL
```

**输出数据**：
- 信号类型、方向、强度、原始值、连续性追踪

#### 5️⃣ web_search_mcp（网络搜索处理）

```
搜索查询
    ↓
Tavily / SearXNG 搜索
    ↓
结果聚合 + 情感分析
    ↓
生成 EventCard
    ↓
存储到 PostgreSQL
```

**输出数据**：
- 搜索结果标题、摘要、来源、相关性评分

---

### Q4: 处理完之后怎样存储？

**答**：三层存储架构：

#### 📊 PostgreSQL（关系数据库）

存储所有结构化数据卡：

```
market_state_cards
├─ asset, timeframe, snapshot_at
├─ price, price_change_1d/7d/30d
├─ trend_direction, rsi_14, ma_20/50/200
├─ volume_vs_avg, volatility_state
└─ data_source, extra_data

event_cards
├─ event_type, title, summary
├─ source_name, source_url, event_timestamp
├─ asset_scope, theme_scope, importance
├─ impact_direction, is_surprise
└─ data_source

flow_signal_cards
├─ asset, signal_type, timeframe
├─ direction, magnitude, raw_value
├─ continuity_days, is_trend_change
└─ data_source

raw_sources
├─ source_type, title, original_url
├─ publisher, published_at, raw_text
├─ language, status, word_count
├─ storage_path, article_summary
└─ speaker_hint, theme_hint

source_attachments
├─ source_id, attachment_type
├─ file_name, storage_path, mime_type
├─ file_size_bytes, ocr_text
└─ transcription_text
```

#### 📦 MinIO（对象存储）

存储大文件和二进制数据：

```
raw-files/
├─ {source_id}/original.pdf
├─ {source_id}/original.docx
└─ {source_id}/original.mp4

extracted-text/
├─ {source_id}/text.txt
└─ {source_id}/summary.txt

ocr-results/
├─ {source_id}/image_1_ocr.txt
└─ {source_id}/image_2_ocr.txt

transcriptions/
├─ {source_id}/audio_transcription.txt
└─ {source_id}/video_transcription.txt
```

#### ⚡ Redis（缓存）

存储热数据，加速查询：

```
market_state:BTC:1d → MarketStateCard JSON
market_state:ETH:4h → MarketStateCard JSON
event_feed:coindesk → [EventCard, EventCard, ...]
flow_signal:BTC → [FlowSignalCard, FlowSignalCard, ...]
```

---

## 数据流向图

```
┌─────────────────────────────────────────────────────────────┐
│                      信息输入端                              │
├─────────────────────────────────────────────────────────────┤
│ • Binance/Yahoo/CoinGecko/FRED（市场数据）                 │
│ • RSS 源（新闻）                                            │
│ • 用户上传/爬取（私域内容）                                 │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│                    MCP 处理层                                │
├─────────────────────────────────────────────────────────────┤
│ • market_data_mcp → 技术指标计算                            │
│ • event_feed_mcp → 关键词情感分析                           │
│ • news_realtime_mcp → 中文快讯处理                          │
│ • flow_continuity_mcp → 资金流信号                          │
│ • web_search_mcp → 搜索结果聚合                             │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│                    数据卡生成                                │
├─────────────────────────────────────────────────────────────┤
│ • MarketStateCard（市场状态）                               │
│ • EventCard（事件/新闻）                                    │
│ • FlowSignalCard（资金流）                                  │
│ • ClaimCard（观点提取）                                     │
│ • BriefingCard（最终简报）                                  │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│                    三层存储                                  │
├─────────────────────────────────────────────────────────────┤
│ PostgreSQL（结构化数据）→ MinIO（大文件）→ Redis（缓存）   │
└─────────────────────────────────────────────────────────────┘
```

---

## 配置清单

### 必需的 API Keys

```bash
# 市场数据
COINGECKO_API_KEY=...          # CoinGecko（加密市值）
FRED_API_KEY=...               # FRED（宏观数据）

# 新闻/搜索
TAVILY_API_KEY=...             # Tavily（网络搜索，免费 1000 次/月）
ALPHA_VANTAGE_API_KEY=...      # Alpha Vantage（新闻情感）

# Twitter KOL 追踪
TWITTER_ACCOUNTS=CryptoCred,PeterLBrandt,glassnode,federalreserve
```

### 功能开关

```bash
USE_MOCK_MARKET_DATA=false     # false=真实数据，true=Mock
USE_MOCK_EVENT_FEED=false      # false=真实 RSS，true=Mock
USE_MOCK_FLOW_DATA=false       # false=真实链上数据，true=Mock
```

### 网络配置

```bash
HTTP_PROXY=http://127.0.0.1:7890      # 国内需要代理
HTTPS_PROXY=http://127.0.0.1:7890
```

### 对象存储

```bash
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=agent-files
```

---

## 常见问题

### Q: 如何添加新的数据源？

**A**: 
1. 在对应的 MCP 中添加新的 API 调用或 RSS 源
2. 实现数据解析逻辑
3. 生成对应的数据卡
4. 存储到 PostgreSQL

### Q: 如何查询历史数据？

**A**:
1. 从 PostgreSQL 查询（结构化数据）
2. 从 MinIO 查询（原始文件）
3. 从 Redis 查询（热数据缓存）

### Q: 数据更新频率是多少？

**A**:
- 市场数据：实时（Binance）/ 日度（Yahoo/CoinGecko）
- 新闻：实时（RSS）
- 宏观数据：周度/月度（FRED）
- 资金流：实时/小时（链上数据）

### Q: 如何处理数据错误？

**A**:
- 所有 MCP 都有错误处理和重试机制
- 失败的请求会记录到 `errors` 字段
- 可以通过 `source_mode` 和 `data_freshness` 判断数据质量

---

## 下一步

详细的架构文档已保存到：
`DATA_LAYER.md`

包含：
- 完整的数据流向图
- 每个 MCP 的详细处理流程
- 数据卡的完整字段定义
- 存储结构和索引设计
- 配置和部署指南
