# 三阶段链路深度分析

> **历史分析参考**：问题与完成度停留在记录日期，当前结论见 `CURRENT_STATE.md`。

**更新时间**：2026-04-17
**范围**：从数据获取 → 处理存储 → 召回问答的端到端评估

> 本文档仿照工业界 RAG 系统深度审视方式，对当前设计的合理之处与优化方向进行剖析，
> 并对照学术界与同类产品的最佳实践给出可执行的改进路径。

---

## 一、数据获取与收集链路的深度分析

### 1.1 当前设计的数据源矩阵

| 维度                | 行情/资金                 | 宏观经济                   | 新闻事件                   | 社交媒体（5 平台）                  | 用户上传                |
| ----------------- | --------------------- | ---------------------- | ---------------------- | ---------------------------- | ------------------- |
| **入口**            | `snapshot_service`    | `macro_mcp`            | `event_feed_mcp`       | `social_subscription_service` | Inbox `/upload`     |
| **数据源**           | Binance / Yahoo / CoinGecko | FRED / Alpha Vantage     | RSS 多源                 | RSSHub + MediaCrawler + We-MP-RSS | 本地文件（文本/图片 OCR）  |
| **拉取方式**          | 主动轮询 HTTP             | 主动轮询                   | 主动轮询                   | 被动扫 JSONL（xhs/dy） + HTTP（其他）    | 事件驱动                |
| **频率**            | 15 min（开盘加密到 2 min）   | 按需                     | 按需                     | 6 h（可配） + staleness 预触发        | 实时                  |
| **去重**            | (symbol, timeframe, ts) 唯一键 | —                    | 内容 hash                | 4 层去重（本轮强化）                   | content_hash SHA-256 |
| **失败降级**          | Backoff + quota guard | Mock fallback          | Mock fallback          | 按平台独立失败                       | UI 反馈 + 重试队列        |
| **关键性（对下游影响）**    | 🔴 高                  | 🟡 中                   | 🟡 中                   | 🟡 中                         | 🟢 低                |

### 1.2 当前设计的合理之处

**✅ 分级分源、互为降级** — 行情 `Binance → Yahoo → CoinGecko`、新闻 RSS 多源冗余、社媒 3 个独立服务（RSSHub / MediaCrawler / We-MP-RSS），任一源挂掉都不会整条断。

**✅ 全部主动拉取 + content_hash 去重** — 不依赖 webhook，不依赖各平台的 ETag/Last-Modified。对 RSSHub / MediaCrawler 这类自建中间件友好。

**✅ scheduler 内嵌 FastAPI 进程** — 无 Celery / Redis 依赖，启停简单，对单用户研究系统足够。

**✅ MediaCrawler 数据读 JSONL 而非 HTTP**（本轮重写） — MediaCrawler 本身没有稳定的 REST 查询端点，旧版 `/api/crawl/xhs/user/{uid}` 是臆想的，从未拉到过数据。改为扫 `data/{platform}/jsonl/*_contents_*.jsonl` 后端到端跑通（12 条 pytest 覆盖）。

**✅ headless 自动抓取 + 状态可视化**（本轮新增） — scheduler 会在读 JSONL 前检查 staleness（>5h 陈旧即触发 `/api/crawler/start`，强制 `headless=true`），前端 Settings 页有"立即抓取 / 停止 / 实时状态"三按钮，与后端 `/mediacrawler/{trigger,stop,status}` 端点一一对应。

**✅ 4 层社媒去重架构**（沿用）：
1. `_last_sync_at` 增量游标
2. `_normalize_title()` + `_canonicalize_url()`（去除 UTM、规范前缀）
3. SHA-256(platform|author|title|date|url) 五元组
4. DB `UNIQUE(content_hash)` 约束兜底

### 1.3 存在的问题与优化方向

#### 问题 A：数据源健康度可观测性不足

当前 `/settings/datasource-health` 只 ping 3 个 meta-service（RSSHub / MediaCrawler / We-MP-RSS）。**缺失**：
- 单条外部 API 的滑动成功率（7 天窗口）
- 延迟 p50/p95/p99 分布
- `source_health_logs` 表已存在但**无可视化面板**（GAP_LIST.md G5 项）

**建议**：在 Dashboard 或 Verification 页加一个 Source Health 面板，消费 `source_health_logs`，展示 sparkline。

#### 问题 B：社媒 MediaCrawler 与主进程解耦但缺护栏

MediaCrawler 跑在独立端口 8080 的子进程。scheduler 的 `refresh_mediacrawler_data()` 假设它一直在跑。**实际情况**：
- 如果 MediaCrawler 崩溃，爬取静默失败（我们有 `log.warning` 但没 alert）
- 重复触发时依赖 MediaCrawler 自身的 "already_running" 拒绝逻辑
- 登录态过期时，MediaCrawler 会弹二维码（headless 模式下弹在无界面的 Playwright）

**建议**：
- 加一个 `mediacrawler_health` 阈值告警（前端红点 + 可选邮件）
- 登录态 < 2 天时推前端通知"即将需要重新登录"
- scheduler 收到 `already_running` 时等待而不是跳过

#### 问题 C：缺少数据质量 SLI/SLO

行业标准 RAG/数据系统会定义：
- **完整性 SLI**：每平台每 24h 新入库条数 ≥ 阈值
- **新鲜度 SLI**：最近入库时间 ≤ N 小时
- **错误率 SLO**：ingestion_workflow 失败率 < 5%

当前只有 `source_health_logs` 做被动记录，没有主动 SLO 检测 + 告警。

#### 问题 D：Twitter/微博依赖 RSSHub 单点

RSSHub 是第三方项目，不同运营商实例稳定性差异很大。当前只配一个 `RSSHUB_BASE_URL`，**没有多实例 fallback**。

**建议**：`RSSHUB_BASE_URLS`（复数）轮询，任一成功即返回。

### 1.4 业界对标

| 维度             | 我们              | Bloomberg Terminal | Benzinga Pro          | Koyfin                     |
| -------------- | --------------- | ------------------ | --------------------- | -------------------------- |
| 数据源数量          | 16 个            | 500+ (License)     | 50+ (License)         | 100+ (License)             |
| 社媒整合           | 5 平台（开源中间件）    | Twitter 原生 API     | Stocktwits + Twitter  | ⚠ 有限                    |
| 实时推流           | ❌（轮询）          | ✅ Push           | ✅ Push              | ✅ Push                   |
| SLA            | 无               | 99.9%              | 99.5%                 | 99.5%                      |
| 合规审计           | `source_health_logs` | SOC 2 / ISO 27001 | FINRA 合规            | SOC 2                      |

**定位判断**：我们是**研究辅助工具**不是交易终端，免 key 友好、可本地运行、数据可追溯，这条路是对的；但需要承认 SLA 差距，不适合做实时交易决策。

---

## 二、处理与存储链路的深度分析

### 2.1 当前设计的处理管道

```
raw_sources (content_hash UNIQUE)
    │
    ▼  ingestion_workflow.run()
    │   Step 1  _normalize_cjk_text()     NFKC + 15 个 CJK Radical Supplement 手动映射
    │   Step 2  claim_extraction_skill    LLM 提取结构化观点 + full_quote
    │   Step 3  quote_grounding           三级匹配（exact → normalized → fuzzy）89.6%
    │   Step 4  quality_gate              Grade A/B/C/D，D 丢弃
    │   Step 5  semantic_dedup            embedding 余弦 > 0.92 判重
    │   Step 6  speaker_alias_resolution  别名归一化 → speaker_id
    ▼
claim_cards (embedding TEXT[2560] + vec0 镜像)
    │
    ├─ briefings + briefing_evidence_links
    ├─ debates + debate_rounds + final_adjudications
    ├─ outcomes (复盘)
    └─ strategy_cards + setup_cards
```

### 2.2 合理之处

**✅ Grade D 丢弃而非纳入** — 低质量观点根本不进库，从源头防止污染。优于很多系统"全入库后查询时过滤"的做法。

**✅ Quote Grounding 三级匹配 + Double Check** — 89.6% grounding 率在中文金融领域是很高的水准。剩余 10.4% 经 double-check 确认为结构性限制（跨消息碎片），不是系统 bug。

**✅ 语义 dedup 先于入库** — 嵌入召回阶段不会有大量近似重复干扰。

**✅ 嵌入向量 JSON + vec0 双写** — 兼容 SQLite 和 pgvector 两种部署，灵活性高。

**✅ 双轨时效性**：
- `event_date + decay`（新闻型）
- `influence_window`（策略/宏观型，持续生效）

### 2.3 存在的问题与优化方向

#### 问题 A：Claim 没有版本 / 时效衰减分

当前 `claim_cards` 一旦入库就永久 "live"（除非 `lifecycle` 标为 invalidated）。但金融观点天然有时效：
- 3 天前说"BTC 破 10 万是不可能的" → 现在应该降权
- 6 个月前的"牛市顶部信号" → 仍有参考但相关度应衰减

**建议**：召回时加时间衰减：
```
final_score = sem_sim × 0.45 + trust × 0.25 + conf × 0.15 + recency_decay × 0.15
recency_decay = exp(-age_days / tau)  # tau 按 time_horizon 不同：intraday=3, short_term=30, long_term=180
```

#### 问题 B：Chunk 粒度是单条 claim，丢失上下文

- 观点 A：「美联储可能在 Q3 降息」（trust 0.8）
- 观点 B：「但前提是核心 PCE 降到 2.5%」（同源同篇，trust 0.8）

当前会被切成两条独立 claim，召回时可能只拿到 A 丢失 B。LlamaIndex 的 Parent-Child retrieval（先召回小块，再展开到父文档）是公认的最佳实践。

**建议**：加 `parent_source_id` 字段；召回时如果命中 claim，自动附加同源邻近 2-3 条 claim 作为 "context neighbors"。

#### 问题 C：没有自动化 Faithfulness 评估

RAGAS / TruLens 的标准指标：
- **Faithfulness**：回答是否基于检索到的上下文
- **Answer Relevance**：回答是否回应了问题
- **Context Precision / Recall**：召回集合的精确度和召回率

当前只有人工 `IngestionAudit` 对比原文 vs 提取结果，没有运行时答案评估。

**建议**：可选加一个 `/evaluate` 端点，批量跑 RAGAS 对最近 N 条 briefing / chat 答案打分。

#### 问题 D：存储冗余风险（长期）

- `claim_cards.embedding` 是 2560 维 float 的 JSON TEXT（单条 ~30KB）
- `vec0` 虚表镜像了一份 binary（~10KB）
- 当前 ~164 条 claim → 总计 ~7MB，可接受
- 如果增长到 10 万条 → 4GB，SQLite 会开始吃力

**建议**：到 1 万条前切换到 pgvector 生产部署，或 prune 低 trust + 年龄 > 180d 的 claim。

### 2.4 业界对标

| 维度              | 我们             | LangChain 推荐         | LlamaIndex 推荐      | Perplexity     |
| --------------- | -------------- | -------------------- | ------------------ | -------------- |
| 切块策略            | 单条 claim       | Semantic / Recursive | Sentence Window    | 多级             |
| 去重              | ✅ 4 层 + 语义 | ⚠ 需用户自实现       | Node-level dedup   | ✅           |
| 父子文档检索          | ❌            | ✅ 官方 retrievers   | ✅ 原生支持        | ✅ 已知        |
| 时效衰减            | ❌            | ⚠ 需自实现          | ⚠ 需自实现        | ✅ 已知        |
| 自动评估            | ❌            | ✅ 集成 RAGAS       | ✅ 集成 RAGAS     | ✅ 自研       |
| Quote Grounding | ✅ 89.6%    | ❌ 无            | ⚠ 可选           | ✅ 强制       |

**定位判断**：我们在 Quote Grounding 和社媒去重两个点上**领先**公开做法；在切块策略、时效衰减、自动评估上**落后**，有明确改进路径。

---

## 三、召回 → 问答链路的深度分析

### 3.1 当前设计的两条路径对比

| 维度             | 普通对话 (Chat)              | 研究简报 (Briefing)             |
| -------------- | ------------------------ | --------------------------- |
| **入口**         | `POST /chat/sessions/{id}/chat` | `POST /briefings`           |
| **意图识别**       | ✅ `intent_skill.recognize()` 双层（规则 + LLM）  | ❌ 无（直接按参数路由）           |
| **召回来源**       | 仅语义搜索 1 路                   | 三路混合 RAG + 4 个 MCP（events/market/flow/memory） |
| **召回数量**       | 固定 5 条                   | 固定 12 条 claims + events/market/flow |
| **上下文注入格式**    | 扁平文本列表 `- [stance] summary（horizon）` | 分层 EvidencePack + 量化 Scorecard |
| **引用原文**       | ❌ 不注入 `full_quote`         | ✅ 短引用会注入                    |
| **实时数据**       | ❌ 无市场/事件/资金           | ✅ 4 个 MCP 并行                |
| **反证机制**       | ❌ 无                      | ✅ 强制反证包 + 矛盾检测            |
| **LLM 调用次数**   | 1 次（single complete）     | 2–8 次（Fusion + Briefing + 可选 6 席委员会） |
| **成本**         | ~$0.001                  | ~$0.05–0.30                 |
| **透明度**        | `claims_used` 数量           | 完整 `evidence_links` + 数据溯源   |
| **典型延迟**       | 2–8 s                    | 30–180 s                    |

### 3.2 当前设计的合理之处

**✅ 两条路径分层设计正确** — 轻量 Chat 应对日常问答、重量 Briefing 应对决策分析。符合成本-质量权衡原则。

**✅ 三路混合 RAG 召回策略专业** — `query_for_briefing` 合并了：
- 语义相似（embedding 余弦）
- 资产精确（`asset_scope` 包含目标）
- 高信任保底（`trust_weight` 降序 top-N）

综合评分：`sem_rank × 0.5 + trust × 0.3 + conf × 0.2`。比纯向量搜索稳健，不遗漏高质量私域观点。

**✅ EvidencePackBuilder 分层供给** — 共享共识包 + 角色附件 + 强制反证，按席位差异化供给。这是业界少有的精细做法（六席委员会 V2 架构的价值）。

**✅ 意图识别双层策略** — 规则层 0ms + LLM 层精细化，成本可控（参考 `intent_recognition_skill.py`）。

**✅ data_coverage_summary 抑制幻觉** — Briefing 会告知 LLM「本地有 N 条相关 claim / M 条新闻 / 是否有 flow 信号」，减少"我记得 Y 说过..."式的幻觉补充。这是被忽视但很有价值的设计。

**✅ 4 层语义搜索降级链** — `pgvector → sqlite-vec → Python cosine → keyword`，任何部署环境都能工作，且 `get_last_search_tier()` 可观测。

**✅ 意图识别在 Chat 路径有 asset_filter** — 识别到资产后自动过滤语义检索范围，避免跨资产污染。

### 3.3 存在的问题与优化方向

#### 问题 A：Chat 路径严重欠配（最严重）

Chat 是用户**最高频**的交互入口，但 RAG 能力**远低于** Briefing：

```python
# chat.py L190-198 — 当前实现
relevant_claims = semantic_search(db, body.message, limit=5, asset_filter=asset_filter)
# ...
context_parts.append(f"- [{stance}] {summary}" + (f"（{horizon}）" if horizon else ""))
```

具体缺失：
1. **只注入 `summary`，不注入 `full_quote`** — quote grounding 白做了，LLM 看不到原话
2. **不注入 `trust_weight` / `confidence`** — LLM 无法判断来源可信度
3. **不注入 `conditions` / `invalidation_conditions`** — 观点的适用边界丢失
4. **没有市场实时数据** — 用户问"BTC 怎么样"只能看到历史观点，当前价格未知
5. **没有矛盾观点对照** — 单边 top-5，看不到反证
6. **system prompt 允许无缝降级到模型自身知识** — 幻觉风险

#### 问题 B：召回数量固定，缺乏自适应

- Chat 固定 5 条、Briefing 固定 12 条
- 当库里只有 3 条相关 claim 时，凑数的低相关结果引入噪音
- 当库里有 50 条高度相关 claim 时，只取 5 条遗漏关键信息
- 没有相关性阈值过滤（`sim > 0.3` 阈值只在 Tier ③ 暴力余弦层生效，pgvector/sqlite-vec 层无阈值）

#### 问题 C：缺少 Re-ranking 环节

当前是 **single-stage retrieval**：
```
Query → Embedding → Top-K → 直接喂 LLM
```

学术界与工业界最佳实践是 **two-stage retrieval**：
```
Query → Embedding → Top-K_large → Cross-Encoder Re-ranker → Top-K_small → LLM
```

缺 Re-ranker 意味着排序完全依赖 embedding 余弦相似度，对中文金融领域的细粒度语义匹配可能不够精确（例如"看多 BTC 但短期下跌"和"看空 BTC" embedding 可能很接近）。

#### 问题 D：没有 Query Expansion / Query Rewrite

用户输入"BTC 怎么看"，直接用原始 query 做语义搜索。问题：
- query 太短太模糊，embedding 质量差
- 没有利用对话历史做 query 补全（参考 Perplexity 的 follow-up query rewrite）
- 没有用 LLM 做 query decomposition（将"比较 BTC 和 ETH 的基本面"拆成两个子查询）

#### 问题 E：Hallucination Guard 不完整

当前 Chat 的 system prompt 说"如果知识库中没有相关内容，直接基于你的知识回答"，这意味着：
- LLM 可能**无缝切换**到训练知识，用户无法区分哪些是私域、哪些是模型记忆
- 没有 **attribution**（答案中哪些部分来自知识库、哪些来自模型）
- 没有 **refusal mechanism**（没证据时应明确说「私域库无数据，以下是通用背景」）

#### 问题 F：没有 Chat 的 AnswerReceipt

Briefing 模式下每个回答都带 `AnswerReceipt`（数据命中/席位/成本/不确定性可视化），但 Chat 模式下只有极简的 "claims_used N" 一个数字。

### 3.4 业界权威研究与最佳实践对比

#### 3.4.1 RAG 领域核心论文方向

| 技术 | 代表工作 | 我们的现状 | 差距 |
|---|---|---|---|
| **Adaptive Retrieval** | Self-RAG (Asai 2023), FLARE (Jiang 2023) | 固定召回 | 缺"是否需要检索"的动态判断 |
| **Query Rewriting** | RRR (Ma 2023), Step-Back Prompting | 无 | 直接用原始 query |
| **Cross-Encoder Re-ranking** | ColBERT, BGE-Reranker-v2 | 无 | 单阶段检索 |
| **Chunk Optimization** | Sentence Window, Parent-Child (LlamaIndex) | 单条 claim | 无 parent 展开 |
| **Faithfulness Evaluation** | RAGAS, TruLens | 无 | 只有人工 IngestionAudit |
| **Citation Generation** | ALCE (Gao 2023) | 无 | 答案不标注来源编号 |
| **Multi-Hop Retrieval** | IRCoT, DSP | 无 | 单次检索 |
| **Evidence Fusion** | FiD (Izacard & Grave 2021) | `FusionSkill` | ✅ 已有，可更精细 |
| **Contradiction Handling** | DisCoSense, DialFact | Briefing 有强制反证 | Chat 缺失 |

#### 3.4.2 与同类系统的架构对比

| 系统 | 召回架构 | 引用方式 | 成本级别 |
|---|---|---|---|
| **Perplexity AI** | Query Rewrite → Multi-source Search → Re-rank → Synthesize with citations | 内联 `[1][2]` + 可点击 | $$$ |
| **Notion AI Q&A** | Chunk retrieval + Parent document expansion | 链接回原文档 | $$ |
| **Bing Chat** | Search → Re-rank → Citation Generation | 内联标号 | $$$ |
| **Kimi Chat / DeepSeek** | 长上下文 + 搜索 | 内联 + 参考链接 | $$ |
| **我们 (Chat)** | Single embedding retrieval Top-5 | 仅 `claims_used` 数字 | $ |
| **我们 (Briefing)** | 三路混合 + EvidencePack + 可选委员会 | `evidence_links` 完整 | $$ |

#### 3.4.3 金融领域特殊考量

金融 RAG 系统比通用 RAG 更强调：

1. **时效性权重**
   3 天前的观点和 3 个月前的观点不应同等对待。当前 `query_for_briefing` **没有时间衰减因子**，`asset + sem_sim + trust` 三路合并时年龄隐式参与（通过 DESC 排序）但非加权。

2. **矛盾处理**
   金融观点天然存在多空分歧，**不能简单去重**。当前 `is_contradicted` 标记是好的开始，但召回时应**刻意保留对立观点**而非降权。Briefing 的"强制反证包"是正确方向；Chat 路径完全缺失。

3. **Source Attribution（来源归因）**
   金融合规要求回答必须可追溯。当前 Briefing 有 `evidence_links`，但 Chat 模式**完全没有**。

4. **Confidence Calibration**
   金融决策对不确定性敏感。应该向用户传达"我基于 3 条高信任观点和 2 条低信任观点得出此结论，整体置信度 65%"，而非单一判断。

### 3.5 优先级改进路线图

| 优先级 | 改进项 | 预期收益 | 工作量 |
|---|---|---|---|
| 🔴 P0 | **升级 Chat 路径的 context 质量**：注入 `full_quote` / `trust` / `conditions` + Scorecard 样式 | 降低幻觉、提高回答质量 | 1 天 |
| 🔴 P0 | **加入对立观点保留机制**：召回时按 stance 平衡（bull / bear / neutral 各 ≥1） | 避免 Chat 一边倒 | 0.5 天 |
| 🟡 P1 | **Query Rewrite + 对话历史融合**：短 query 自动补全上下文 | 提升短 query 召回质量 | 1 天 |
| 🟡 P1 | **Cross-Encoder Re-ranking**：BGE-Reranker 二阶段排序 | 提升 top-K 精度 ~10-15% | 1-2 天 |
| 🟡 P1 | **时间衰减评分**：召回加 recency_decay 因子 | 过期观点自动降权 | 0.5 天 |
| 🟢 P2 | **Parent-Child Retrieval**：命中 claim 自动附同源邻近 | 补全 claim 切片丢失的上下文 | 1-2 天 |
| 🟢 P2 | **Chat 加 AnswerReceipt** + 内联 citation `[1][2]` | 与 Briefing UX 对齐 | 1 天 |
| 🟢 P2 | **RAGAS 自动评估端点** | 量化回答质量 | 1-2 天 |
| 🔵 P3 | **自适应 top-K**：基于相关度阈值动态裁剪 | 噪音观点少、高密度召回多 | 0.5 天 |

---

## 四、总结与定位判断

### 4.1 综合评估

| 阶段 | 成熟度 | 相对业界定位 |
|---|---|---|
| **1. 数据获取** | 🟢 生产级 | 免 key 路线 + 本地中间件组合在个人/研究系统里是最佳实践 |
| **2. 处理存储** | 🟢 生产级 | Quote Grounding + Grade-D drop 等某些点领先公开做法 |
| **3. 召回问答**（Briefing） | 🟢 生产级 | 六席委员会 + EvidencePack 属专业级 |
| **3. 召回问答**（Chat） | 🟡 可用但粗糙 | 欠配，有明确提升空间 |

### 4.2 本轮（2026-04-17）完成的关键修复

1. **xhs / douyin 数据链路重写**：JSONL 磁盘扫描替代伪 HTTP 端点
2. **scheduler 自动 headless 触发**：陈旧数据自动刷新，非必要不弹浏览器
3. **Settings 页 UX**：独立保存 / 登录态徽标 / 立即抓取按钮 / 爬虫状态横幅
4. **ResearchChat 白屏修复**：Rules of Hooks 违规拆分为 4 个子组件
5. **Cookie 状态源对齐**：`get_cookie_status()` 识别 MediaCrawler 浏览器登录态
6. **12 条 pytest 回归**：保护 JSONL 解析、过滤、时间戳、去重逻辑

### 4.3 下一轮建议聚焦

基于 3.5 的路线图，如果只做一件事 → 做 **P0：Chat context 升级**。这能以 1 天工作量把用户最高频入口从"粗糙"拉到"专业级"。

如果再做一件 → 做 **P1：Cross-Encoder Re-ranker**。这是 RAG 领域 ROI 最高的单点升级（预期召回精度 +10-15%）。

---

**交叉引用**：
- 实现细节：CURRENT_STATE.md (historical reference excluded from public source)
- 数据层：[DATA_LAYER.md](./DATA_LAYER.md)
- V2 架构：[V2_DESIGN.md](./V2_DESIGN.md)
- Quote Grounding：../optimization/QUOTE_GROUNDING.md (historical reference excluded from public source)
- 未完成差距：GAP_LIST.md (historical reference excluded from public source)
