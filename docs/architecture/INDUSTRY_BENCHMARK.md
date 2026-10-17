# 存储→回答链路 · 行业对标报告

> **历史研究参考**：行业信息和项目差距以文档编写日期为准，不代表当前市场状态。

**编写日期**：2026-04-17
**对标方法**：Web 检索 + 官方文档阅读 + 2025/2026 主流综述交叉验证
**对标维度**：需求/目标 → 权威机构做法 → 高热开源项目做法 → 我们的位置
**姊妹文档**：`STORAGE_TO_ANSWER_REVIEW.md`（代码级评估）

---

## 目录

1. 检索层（Retrieval）
2. 上下文组装层（Context Packing）
3. 多视角 / 立场平衡（Diversity）
4. 引用归因与幻觉控制（Grounding）
5. 多 Agent 辩论（Debate / Multi-Agent）
6. 意图路由与模式选择（Routing）
7. 时间衰减与数据新鲜度（Freshness）
8. 评估体系（Evaluation）
9. 综合对标矩阵 + 我们的位置
10. 优先采纳清单

---

## 1. 检索层（Retrieval）

### 需求与目标
从一个语义库里为问题精准召回 Top-K 相关证据。关键指标：Recall@k、Precision@k、MRR、NDCG、retrieval failure rate。

### 权威机构做法 —— **Anthropic Contextual Retrieval**（2024-09，行业当前标杆）
1. **Contextual Embeddings**：chunk 入库前由 LLM 加一段"它在原文中讲的是什么"的上下文前缀，再 embed
2. **Contextual BM25**：同样加过 context 的 chunk 进 BM25 索引
3. **Reciprocal Rank Fusion (RRF)** 合并 dense + sparse 结果
4. **Reranker**（Cohere Rerank 或 Voyage）二阶段精排

**权威结论**：
- 纯 Contextual Embedding → 检索失败率 **-49%**
- Contextual Embedding + Contextual BM25 + Reranker → **-67%**（5.7% → 1.9%）

### 高热项目做法
- **LangChain / LlamaIndex**：默认 hybrid search（vector + BM25 + RRF），reranker 作为可选二阶段
- **Weaviate / pgvector / Pinecone**：都原生支持 hybrid + RRF；Weaviate 有 `alpha` 参数调配比
- **RAG Stack Reddit 综述（2025）**：生产环境标配三段式 —— 混合召回（top-N）→ cross-encoder rerank（top-K）→ 截断喂给 LLM

### 我们现在做的
- ✅ 三级向量 fallback（pgvector → sqlite-vec → 关键词）—— 比业界更注重 resilience
- ✅ `query_for_briefing` 三路混合召回（语义 + 资产精确 + trust 保底）+ 融合评分 —— 类比 RRF 的 low-tech 手写版
- ⚠️ **无 BM25 索引** —— 关键词搜索只是 ilike，无 TF-IDF 加权
- ⚠️ **无 chunk-level 上下文增强** —— embedding 输入只是 `summary + full_quote`，没有 LLM 生成的 chunk context
- ❌ **无 Reranker** —— 纯语义余弦相似，中文金融语义歧义无法修正

### Gap 评估
我们的 `query_for_briefing` 设计思路（三路融合评分）其实已经**隐含了 hybrid search 的思想**，只是三路里没有 BM25，用的是"asset 精确匹配 + trust 排序"代替。这个替代在资产类问题上效果不差，但对于**跨资产 / 宏观 / 主题类问题效果显著下降**。

### 推荐动作
- **短期**（已在 GAP 列表 G11）：加 Cohere Rerank v3（或本地 BGE-Reranker-v2-m3）作为二阶段，top-20 → top-5。预计失败率降 30-50%。
- **中期**：实现 Anthropic Contextual Embeddings —— 入库时用 LLM 生成 "这条 claim 出自谁、针对哪个资产、讲什么时间窗"的 context 前缀，再 embed。我们已经有 `speaker_id` + `time_horizon` + `asset_scope`，可以模板化生成，不一定需要 LLM。
- **长期**：加 PostgreSQL 原生 full-text search 或独立 Elasticsearch，真正实现 dense + sparse hybrid。

---

## 2. 上下文组装层（Context Packing）

### 需求与目标
把 top-K 证据拼装成 LLM 能高效利用的结构化文本，同时控制 token 成本、保留可追溯性。

### 权威做法
- **OpenAI function calling / structured output**：用 JSON Schema 约束输出，降低解析失败率
- **Anthropic prompt caching**：Contextual Retrieval 为降本使用 cache，重复 context 部分被缓存，成本下降 90%
- **长上下文模型 vs RAG 的取舍**：Claude 200K、Gemini 1M 的出现没有消灭 RAG，反而让 context packing 的"结构化密度"更重要 —— token 多了不代表模型会认真读每一条

### 高热项目做法
- **LangChain**：`PromptTemplate` + `format_document()` 把 Document 对象格式化到 prompt
- **LlamaIndex**：`ResponseSynthesizer` 支持 `refine` / `tree_summarize` / `compact` 三种打包模式
- **DSPy**：声明式 prompt 编程，让框架自动优化 context 布局

### 我们现在做的
- ✅ **Chat Harness**（`chat_harness.py`）已经做得相当好：
  - `[N]` 编号制（类比 Anthropic 官方 prompt 示例）
  - `header + claims + market_snapshot` 分层结构
  - 每条 claim 都含 stance、信任、置信、时域、条件、失效条件 —— 这个密度比业界 RAG 强
- ✅ Briefing 的 `strategy_fit_summary` 要求概率矩阵 + 期望值加权 —— 领先于普通 RAG 项目
- ⚠️ **无 prompt caching** —— 相同 briefing system prompt 每次都发，浪费 token
- ⚠️ **无 token 预算守门** —— claims=10 每条 240 字符 + events=13 条 + market + flow + macro，容易悄悄超模型窗口

### Gap 评估
我们的 harness 设计**在 prompt 结构化密度上超过业界平均水平**，但在**成本控制**和**长度安全**上是弱项。业界已经把 prompt caching 当作标配（Anthropic / OpenAI 都支持 ephemeral cache_control），我们没用。

### 推荐动作
- **短期**：启用 Anthropic prompt caching（若用 Claude）或 OpenAI 系的 prompt_cache_key。Briefing 的 `BRIEFING_SYSTEM_PROMPT` 2200 字符全是静态内容，缓存后成本直降 50-70%。
- **中期**：加 `pack_context(claims, max_tokens=6000)` 函数，智能裁剪 full_quote / 排序优先级 / 降级为 summary。

---

## 3. 多视角 / 立场平衡（Diversity）

### 需求与目标
避免 RAG 的"回音室效应"——纯相似度召回会让相同立场的 chunk 聚在一起，让 LLM 以为是共识。

### 权威做法 —— **Maximum Marginal Relevance (MMR)** · Carbonell & Goldstein 1998（CMU）
经典公式：
```
MMR = arg max [ λ · Sim(d, q) - (1-λ) · max Sim(d, d_selected) ]
```
λ=1 纯相关性，λ=0 纯多样性。产业界已经把它作为 diversity reranking 的默认。

### 高热项目做法
- **Elastic Search Labs**（2024）：MMR 作为官方 blog 推荐的生产配置
- **Qdrant**（2025）：`mmr-diversity-aware-reranking` 内置
- **OpenSearch 3.3**（2025）：`vector-search-mmr` 作为一等 citizen
- **LangChain**：`as_retriever(search_type="mmr")` 一键启用

### 我们现在做的
- ✅ **Stance-balanced retrieval**（`chat_harness.balanced_semantic_search`）—— 80% 阈值触发对立注入
- 💡 这是一个**针对金融立场域特化的 MMR 变体** —— 我们选的是 binary stance（bull/bear）而不是 embedding 距离作为多样性度量，这个选择**针对金融领域其实更合理**

### Gap 评估
我们的方案比 MMR 更**领域对齐**：在金融场景，两个 bullish claim 哪怕 embedding 距离很远，用户要的也不是"一个涨价的逻辑 + 一个 DeFi 增长的逻辑"，而是"涨 vs 跌 vs 中性"的对立视角。stance-balance > 纯 MMR 在这个场景。

但我们的算法有一个**业界没遇到但我们会遇到**的问题：**过度纠偏**（见 STORAGE_TO_ANSWER_REVIEW.md 的 D-C1）。MMR 的 λ 参数天然提供了 dial；我们的"80% 触发"是硬阈值。

### 推荐动作
- 把 stance-balance 的"80% 阈值 + 20% 注入比例"提取为可调参数
- 长期考虑：stance-balance 和 MMR 可以并存 —— 先 stance 保证对立立场有代表，再用 MMR 在同一立场内部去冗余

---

## 4. 引用归因与幻觉控制（Grounding）

### 需求与目标
让 LLM 的回答**可溯源**，并能检测回答是否"不基于证据地胡说"。

### 权威做法 —— **TruLens RAG Triad**（行业标准评估框架）
三个独立维度：
1. **Context Relevance**：召回的 chunks 是否真与问题相关（衡量召回器）
2. **Groundedness**：回答的每一个 claim 是否能在召回的 context 中找到支持（衡量生成器是否编造）
3. **Answer Relevance**：最终回答是否回应了问题（衡量生成器是否跑题）

三个维度全绿 = "应用在其知识库范围内无幻觉"。

### 高热项目做法
- **RAGAS**（开源评估框架，事实标准）：`faithfulness / answer_relevancy / context_precision / context_recall`
- **LangSmith Evaluations**：内置 grounding check
- **Patronus AI, Galileo**：商业化的 grounding check 服务
- 实现思路共性：把回答拆成 atomic claims → 用另一个 LLM（或 NLI 模型）判断每个 claim 是否能被 context 支持

### 我们现在做的
- ✅ **`[N]` 编号归因**——prompt 层强制要求
- ✅ **citations payload 入库**——每条 [N] 对应哪个 claim_id、哪段 short_quote 完整保留
- ✅ **stance_distribution + balanced_applied 元数据** —— 业界罕见的审计粒度
- ❌ **无回答层 groundedness 检查** —— LLM 写了 `[3]` 但 citations 只有 `[1][2]`，前端照样渲染（见 STORAGE_TO_ANSWER_REVIEW.md D-C3）
- ❌ **无 atomic-claim-level 校验** —— Briefing 的 `supporting_points` 可能完全是 LLM 编造的"看起来合理"内容

### Gap 评估
我们**归因基建**优于业界平均（citations 入库 + 立场分布 + 市场快照），但**校验层是零**。
行业标准是"生成后 NLI 验证"，成本加 1-2 次 LLM 调用，是 production-grade 系统不能省的。

### 推荐动作
- **立即可做**（0.5 天）：正则校验 `\[(\d+)\]` 是否超出 citations 范围，剔除无效引用。保守版。
- **中期**：加 Groundedness 检查子系统 —— 用一个便宜模型（如 gpt-4o-mini 或 deepseek-v3.1）对每个 supporting_point 做一次 NLI 判断（entailment / contradiction / neutral）。与 RAGAS / TruLens 的 `faithfulness` 指标对齐。

---

## 5. 多 Agent 辩论（Debate / Multi-Agent）

### 需求与目标
通过多个独立视角 agent 并行分析 + 裁决官综合，抑制单一模型偏见、暴露盲点。

### 权威做法（2026 年 Q1 实况）
| 厂商 | 框架 | 定位 | 上市时间 |
|---|---|---|---|
| Microsoft | **AutoGen** | 多 Agent 对话，擅长群辩论、共识构建 | 2023+，持续迭代 |
| LangChain | **LangGraph** | 状态机驱动，生产级工作流 | 2024+，最成熟 |
| CrewAI | **CrewAI** | 角色化团队自动化，A2A 协议 | 2024+，热度最高 |
| OpenAI | **Agents SDK** | 官方多 Agent SDK | 2025-03 |
| Google | **ADK**（Agent Development Kit） | Google 官方 | 2025-04 |
| Anthropic | **Agent SDK** | 与 Claude 4.6 一起发布 | 2025-Q4 |

### 高热项目做法
- **Debate 模式共识**（AutoGen / LangGraph 都支持）：
  1. 多 Agent 并行各自分析
  2. 共享彼此的观点（第 2+ 轮）
  3. 可让一个 "critic" agent 专门挑刺
  4. 裁决官/Orchestrator 综合最终答案
- **Society of Minds**（Du et al. 2023, 引用数千）：多 Agent 辩论能显著降低单模型幻觉
- **Reflexion / Self-Refine / Constitutional AI**：同一 agent 的自我批评也属于"多视角"范畴

### 我们现在做的
- ✅ **CommitteeOrchestrator**（`committee_orchestrator.py`）—— 席位激活 + 预算降级 + 数据覆盖门控，已经是 production-grade 调度器
- ✅ **6 个席位角色差异化**（baseline/probability/risk/structure/audit/pnl）—— 比学术 Society of Minds 的"多 agent 同角色辩论"更有产品思维
- ✅ **多轮对话 + previous_rounds 注入** —— 对齐行业标准
- ✅ **FinalAdjudication 字段丰富**（synthesis + 4 分数 + agreements/disagreements/warnings/minority/invalidation/action_economics）—— 比业界 debate 框架记录更细
- ✅ **DecisionSnapshot 冻结决策时点** —— 这是**业界很少见的特性**，支持 replay，比 AutoGen / CrewAI 的对话历史更有审计价值
- ⚠️ **召回退化**（D-D1）—— 席位拿到的证据质量低于 chat/briefing
- ⚠️ **无 critic / devil's advocate 角色** —— 我们的 audit 席位接近这个功能，但 prompt 里没有明确"批评其他席位"的 directive

### Gap 评估
我们的 Debate 架构**在裁决记录深度、决策可追溯上领先业界**，在**证据供给**上落后自己的其他两条链路。
典型的"自己打自己"：committee 链路的执行层水平 > 辩论链路水平（按学术 Society of Minds 标准评估），但被召回质量拖累。

### 推荐动作
- **立即**（0.5 天，已在 STORAGE_TO_ANSWER_REVIEW.md D-D1）：召回换成 `query_for_briefing`
- **可选**：参考 LangGraph 的 `conditional_edges` 实现"若 audit 席位发现未验证事件则激活 skeptic 席位"的动态分支
- **长期观望**：是否值得迁移到 LangGraph —— 我们现有的 CommitteeOrchestrator 其实已经覆盖了 LangGraph 80% 的核心能力（状态机、预算、条件激活），除非需要可视化调试（LangGraph Studio），否则**不迁移是对的**

---

## 6. 意图路由与模式选择（Routing）

### 需求与目标
识别用户问题的类型，路由到合适的处理链路（快速问答 / 深度研究 / 辩论 / 拒答等）。

### 权威做法
- **OpenAI / Anthropic / Google** 的消费端产品都有"Deep Research" / "Research Mode" / "Extended Thinking" 切换
  - ChatGPT Deep Research：用户显式选择，或 LLM 自动判断复杂问题自动升级
  - Claude Extended Thinking：基于模型 reasoning budget
  - Grok Deep Search：约 3× 网页覆盖，10× 速度（营销数据）
- **官方 SDK 层面**：OpenAI 提供 `assistants.files.search`、Anthropic 提供 `computer_use` 等 tool use，通过 function calling 实现路由

### 高热项目做法
- **Hybrid LLM + Classifier**（主流方案）：
  - 规则层 / embedding 分类器（FAISS / BERT）快速匹配
  - 低置信时 fallback 到 LLM 做 zero/few-shot 分类
  - 与我们的 `intent_skill` 双层架构一致
- **Semantic Router**（开源库）：专门做意图路由，embedding + threshold
- **DSPy signatures + assertions**：声明式路由

### 我们现在做的（G20 刚完成）
- ✅ 双层 intent（规则 + LLM fallback）—— 与主流完全对齐
- ✅ `mode_router` 7 档决策 + 斜杠命令 + force route —— 比 ChatGPT 的"三档切换"更灵活
- ✅ 成本感知（每档 $/次显示）—— **业界罕见** —— OpenAI/Anthropic 都不给用户显示当次 query 的成本预估
- ✅ `/auto` 分派式架构（前端双阶段）—— 干净，对标 LangGraph 的 router node

### Gap 评估
**我们在这一层领先业界消费产品**。ChatGPT 的模式切换是黑盒，Claude 更甚。我们有：
- 显示决策理由
- 展示预估成本
- 支持斜杠显式 override
- 可审计（Verification 面板）
- 一键升级/降级

这个设计应该保留，不需要对标。

### 推荐动作
保持。

---

## 7. 时间衰减与数据新鲜度（Freshness）

### 需求与目标
金融领域的 claim 有保鲜期：3 天前的资金流 ≠ 3 周前的资金流。RAG 必须能"衰减陈旧信息"。

### 权威做法
- **ArXiv 2509.19376**（2025-09）《Solving Freshness in RAG: A Simple Recency Prior and the Limits》：
  - "as-of query"：按指定日期过滤
  - "recency-biased query"：top-K 语义召回后，用 `score = α · sem + (1-α) · recency` 重排
- **组织级 AI 系统**（Glean, Notion AI, Microsoft 365 Copilot）：**均无 epistemic typing**，都假设文档"中性且最新" —— 论文指出这是通病（ArXiv 2604.11759）

### 高热项目做法
- **LangChain**：`TimeWeightedVectorStoreRetriever`
  `score = sem_sim + (1 - decay_rate) ^ hours_passed`
- **LlamaIndex**：类似的 time-weighted retrieval
- **Langflow 博客**：推荐每日递增 decay_factor 作为生产配置

### 我们现在做的
- ✅ `valid_until` + `time_sensitive` 字段都有
- ✅ `get_expiring_claims()` 提醒即将过期
- ✅ `LifecycleLog` 有完整的过期轨迹
- ❌ **召回阶段无时间加权** —— 已在 GAP G12 记录，工作量 0.5 天

### Gap 评估
**数据模型已经到位，就差召回时的一行代码**。我们甚至比 LangChain 的默认实现（只基于 created_at）更精细，因为我们有 `time_sensitive=true` 标记和 `valid_until` 显式字段。

### 推荐动作
G12 直接做：`_rerank_score` 里加一项 `recency = exp(-age_days / τ)`，τ 按 `time_horizon` 分层（intraday=3、short=14、medium=60、long=180）。

---

## 8. 评估体系（Evaluation）

### 需求与目标
量化系统性能，持续监控退化。

### 权威做法 —— **TruLens RAG Triad** + **RAGAS**
两者是生产环境双标配：
| 工具 | 侧重 |
|---|---|
| TruLens | 3 轴 triad（relevance / groundedness / answer_relevance），侧重生产监控 |
| RAGAS | 更多指标（faithfulness / context_precision/recall / answer_relevancy），侧重 CI/CD |

两者都需要：
- 一个"真实答案"数据集（或 LLM-as-judge 替代）
- 在 CI 中跑 regression tests

### 我们现在做的
- ⚠️ 有 `Verification` 页 + scenarios，但**侧重功能性测试**（API 可用、端点返回格式），**不测生成质量**
- ⚠️ 有 `G20 路由审计面板` —— 刚做的，pass/fail 粒度
- ❌ 无 groundedness / faithfulness 评估流水线
- ❌ 无 context relevance 评估（召回是否相关，只有 `_retrieval_paths` 溯源，没有评分）

### Gap 评估
我们在**功能回归测试**上优秀（30+ API 场景覆盖），但在**生成质量评估**上几乎是空白。这是一个明显的缺口。

### 推荐动作
- **中期**：集成 RAGAS，在 Verification 页加一个"RAG 质量评估"tab，跑预设的 20 条 golden queries，报告 faithfulness / context_precision / answer_relevance
- 不需要自己实现 —— RAGAS 是 pip install，挂上 OpenAI/Deepseek 的 judge model 即可

---

## 9. 综合对标矩阵

| 能力 | 业界 SoTA | 高热项目 | 我们 | 位置 |
|---|---|---|---|---|
| 混合检索 | Dense+BM25+RRF | LangChain hybrid | 三路合并 + 融合评分 | 🟡 近似（缺 BM25） |
| Reranker | Cohere / BGE | 默认可选 | ❌ 无 | 🔴 落后 |
| Contextual Embedding | Anthropic 方案 | 少数项目采用 | ❌ 无 | 🔴 落后 |
| 三级 fallback | 罕见 | Weaviate/Pinecone 单层 | ✅ pgvector→sqlite-vec→kw | 🟢 领先 |
| Prompt Cache | OpenAI/Anthropic API | 部分 | ❌ 无 | 🟡 易补 |
| Diversity | MMR | LangChain/Qdrant 一键 | Stance-Balance（领域定制） | 🟢 等价 / 领先 |
| Grounding 校验 | RAGAS/TruLens | 事实标准 | ❌ 无 | 🔴 落后 |
| 引用归因 | `[N]` 编号 + citations | 主流 | ✅ citations + quote + meta | 🟢 等价 / 略领先 |
| 多 Agent 调度 | LangGraph/AutoGen | 多框架 | CommitteeOrchestrator | 🟢 等价 |
| 决策快照 (replay) | 罕见 | 基本没有 | ✅ DecisionSnapshot | 🟢 **显著领先** |
| 意图路由 | Semantic Router / LLM | 主流 | `mode_router` + 7 档 + slash | 🟢 等价 / 领先 |
| 路由透明度 | 黑盒 | 黑盒 | ✅ 决策理由+成本+审计面板 | 🟢 **显著领先** |
| 时间衰减 | TimeWeighted Retriever | LangChain 默认有 | ❌ 无（数据已齐） | 🔴 落后（易补） |
| RAG 评估 | RAGAS/TruLens | 标配 | ❌ 无 | 🔴 落后 |
| 功能回归测试 | 罕见 | 框架各异 | ✅ 30+ scenarios | 🟢 领先 |

### 加权总分
- **领先或显著领先**：7 项
- **等价**：2 项
- **易补（1-2 天工作量可追平）**：3 项（Prompt Cache / 时间衰减 / 引用校验）
- **需投入（2-5 天）**：3 项（Reranker / Contextual Embedding / RAG 评估流水线）
- **落后明显**：0 项（Reranker 虽然没有，但三路混合召回 + 立场平衡一定程度弥补）

**结论**：系统整体水平约相当于 **LangChain / LlamaIndex + 行业 2024 末 best practice**，在**审计性和决策可追溯**这两个维度**超过多数开源项目**。主要差距在 **质量评估基础设施** 和 **Reranker**。

---

## 10. 优先采纳清单（按 ROI 排序）

### S 级（1 天内可做完，ROI 极高）
1. **D-D1 修复** · Committee 召回换 `query_for_briefing` · 0.5d（前文已详述）
2. **引用 [N] 真实性正则校验** · 0.5d · 对齐业界 Grounding 的 baseline
3. **Prompt caching**（若走 Claude / OpenAI 付费路径）· 0.5d · 省 30-60% briefing 成本
4. **G12 时间衰减** · 0.5d · 对齐 LangChain `TimeWeightedVectorStoreRetriever`

### A 级（2-3 天）
5. **BGE-Reranker 二阶段**（G11）· 2d · 与 Anthropic SoTA 对齐
6. **RAGAS 集成 + 20 条 golden queries** · 2d · 补齐评估基建
7. **资产别名统一**（D-B2）· 1d · 4 处冗余消除

### B 级（视产品走向决定）
8. **Contextual Embedding**（Anthropic 方案）· 3d · 我们有 speaker/time/asset，可不用 LLM 生成 context，模板化就行
9. **Briefing 双实现合并**（D-B1）· 2d
10. **Groundedness / Faithfulness NLI 校验** · 2-3d · 对齐 RAGAS `faithfulness`
11. **Committee DB schema V2 迁移**（D-D3）· 1d

### C 级（长期，不紧急）
12. **独立 BM25 索引**（PostgreSQL full-text 或 Elasticsearch）
13. **Multi-vector embedding**
14. **UserProfile 跨会话偏好**

---

## 11. 一句话总结

> **我们的系统是一个"在审计性、决策可追溯、领域化立场平衡上领先业界，但在 Reranker 和质量评估基建上落后主流 1-2 代"的系统。**
>
> **建议：S 级 4 项本周做完（总工作量 ≤ 2 天），能把系统从"产品级 2024 末"拉到"业界主流 2025 末"**。
>
> **长期不建议迁移到 LangGraph/LlamaIndex —— 我们的自研组件（CommitteeOrchestrator / DecisionSnapshot / mode_router）在金融领域的领域性优势会在迁移后被抹平**。
