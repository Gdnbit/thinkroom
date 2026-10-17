# 存储 → 回答 全链路深度评估（2026-04-17 快照）

> **历史评估**：已被后续评估和当前 v0.1 证据取代。

> ⚠️ **本文档已被 [RECALL_TO_ANSWER_REVIEW.md](RECALL_TO_ANSWER_REVIEW.md)（2026-04-18）取代。**
>
> 本文描述的所有"差距项"已由 A1-A6 + B1-B8 共 14 项改进全部合入 main（见 ACTION_PLAN.md (historical reference excluded from public source)）。保留此文档仅作历史评估记录。
> 当前真实链路状态与剩余工作请看 RECALL。

**评估日期**：2026-04-17
**评估范围**：从 PostgreSQL + pgvector 存储完成之后，到最终用户可见回答的完整链路
**评估方法**：代码走查 + 数据流追踪 + 架构对比
**评估对象**：三条并存的链路 —— Chat / Briefing / Debate

---

## 0. 评估结论速览

| 链路 | 召回质量 | 上下文质量 | 回答质量 | 可审计性 | 总分 |
|---|---|---|---|---|---|
| **Chat** | ★★★★☆ | ★★★★☆ | ★★★★☆ | ★★★★★ | **A-** |
| **Briefing** | ★★★★★ | ★★★★☆ | ★★★★★ | ★★★★☆ | **A-** |
| **Debate** | ★★★☆☆ | ★★★☆☆ | ★★★★☆ | ★★★★★ | **B+** |

**核心发现**：
1. **Chat 链路** 是三条里最完整的 —— 有 stance-balanced 召回、citation 归因、refusal 规则、三级向量 fallback。主要差距在**无 rerank / 无时间衰减 / 无 query rewrite**（已在 G11/G12/G14 列表）。
2. **Briefing 链路** 设计上最成熟 —— plan→fetch→deep 三阶段、`query_for_briefing` 三路混合召回、MCP 聚合、情景概率矩阵推演、证据链接持久化。主要差距在**双实现分裂**（`/plan-and-research` 端点与 `BriefingWorkflow` 并存）和**引用真实性无校验**。
3. **Debate 链路** 有一个**未被意识到的功能退化**：`committee._gather_context` 只用 `get_active_claims_by_asset(limit=8)`，没走混合召回，也没 stance balance。这意味着 bull/bear 席位可能看到的都是单边证据 —— 辩论的地基被削弱了。

---

## 1. Chat 链路（快速问答）

### 1.1 数据流

```
user question
    ↓
intent_skill.recognize()           → { mode, assets, themes, needs_private_knowledge, ... }
    ↓
build_chat_context(query, asset)   → 在 chat_harness.py
    │
    ├── balanced_semantic_search()
    │      ├── semantic_search()   → pgvector → sqlite-vec → 关键词（三级 fallback）
    │      ├── 取 3× candidates
    │      ├── 融合评分：sem(0.55) + trust(0.30) + conf(0.15)
    │      └── stance balance：若单一立场 >80% 且池内有对立，强制注入 ≥20% 对立
    │
    ├── MarketDataMCP.get_market_state()  → 仅在 asset_filter 命中时调
    │
    └── context_block（带 [N] 编号的结构化文本）+ citations
    ↓
llm_client.complete(CHAT_SYSTEM_PROMPT_V2 + history + context_block + question)
    ↓
ChatMessage(role=assistant, payload={citations, stance_distribution, market_snapshot, cost, ...})
```

### 1.2 做得好

- **立场平衡算法**（`@/apps/backend/app/services/chat_harness.py:82-166`）：80% 阈值触发对立注入，替换最低分同向观点。避免了"RAG 回音室效应"。
- **三级向量 fallback**（`@/apps/backend/app/services/semantic_search.py:240-295`）：pgvector 失败自动降级到 sqlite-vec，再不行用关键词。系统级 resilience。
- **引用归因**：每条 claim 分配 `[N]` 编号，前端可跳转到原文。`CHAT_SYSTEM_PROMPT_V2` 明确要求标注。
- **Refusal 规则**：prompt 里写"无证据时说不知道"—— 幻觉率的最后一道防线。
- **Payload 完整**：`context_block[:4000]`、`raw_input[:2000]`、`raw_output[:4000]`、stance_distribution、search_tier、candidates_scanned 全部入库，审计性极高。

### 1.3 差距与风险

**D-C1：立场平衡的边界问题**
*位置*：`chat_harness.py:137` `if dist.get(dominant, 0) / total_primary > 0.8`

8 条召回中 7 条 bull + 1 条 bear → 87.5% > 80%，触发对立注入。这可能**错杀合理共识**：如果市场确实普遍看多（BTC 牛市中段），强行注入 bear 反而降低回答质量。建议：加一个"候选池内对立比例"检查，若 bear 本就 <5%，不触发平衡。

**D-C2：多资产问题的 asset_filter 丢失**
*位置*：`chat.py:300` `asset_filter = intent.assets[0] if intent.assets else None`

用户问"BTC 和 ETH 的相对表现"时，只取第一个资产做过滤。第二资产的 claims 和市场数据都丢了。
**低优先级修复**：allow `asset_filter` 为 list，semantic_search 侧用 OR 过滤。

**D-C3：无引用真实性校验**
LLM 可能输出 `[3]` 但 citations 只有 `[1][2]`。前端照样渲染，用户无感。
**修复成本低**：response_text 中用正则 `\[(\d+)\]` 提取所有引用编号，若 > len(citations)，剔除或加警告标记。5 行代码。

**D-C4：固定 limit=8 的刚性**
*位置*：`chat.py:306`

简单事实类问题（"什么是 OI？"）8 条过多；复杂策略类问题（"BTC 宏观驱动框架"）8 条偏少。没有基于问题复杂度的自适应。**短期接受**，属于迭代优化。

### 1.4 推荐改进

| 改进项 | ROI | 工作量 |
|---|---|---|
| **引用真实性校验**（D-C3） | 高 | 0.5d |
| **立场平衡边界修复**（D-C1） | 中 | 0.5d |
| **自适应 limit**（D-C4） | 中 | 1d |
| Cross-Encoder rerank（G11） | 高 | 2d |
| 时间衰减（G12） | 中 | 0.5d |
| Query rewrite（G14） | 中 | 1-2d |

---

## 2. Briefing 链路（深度研究）

### 2.1 数据流（三阶段）

```
阶段一 · Plan
    ResearchPlanSkill.execute(question, history)
        → ResearchPlan{asset, is_open_ended, secondary_assets, data_needs, preliminary_direction, ...}

阶段二 · Fetch（按 data_needs 开关拉数据）
    ├── ResearchMemoryMCP.query_for_briefing()  ← 三路混合召回
    │      路径 A：semantic_search()          → 按 sem_rank 打分
    │      路径 B：get_active_by_asset()      → 资产精确匹配（最多 20 条兜底）
    │      路径 C：high confidence 5 条       → 高质量保底
    │      合并评分：sem(0.5) + trust(0.3) + conf(0.2) - 0.3*is_contradicted
    │
    ├── MarketDataMCP.get_market_state()      → 价格/RSI/MA/ATR/Volume
    ├── FlowContinuityMCP.get_flow_signals()  → 资金费率/OI/多空比
    ├── EventFeedMCP.fetch_recent_events()    → 近期新闻
    ├── WebSearchMCP.search_news/macro()      → 补充实时信息
    └── data_coverage = min(1.0, (claims/10 + events/5 + market) / 3)

阶段三 · Deep
    FusionSkill.execute() → signal_scorecard（规则评分）+ LLM 融合
        → FusionOutput{direction, conviction, setup_quality, synthesis_narrative, ...}
    BriefingSkill.execute() → LLM 生成最终简报
        → BriefingOutput{title, executive_summary, strategy_fit_summary(含概率矩阵), ...}
    持久化：BriefingCard + BriefingEvidenceLink（claim/event 证据链接）
```

### 2.2 做得好

- **"先 plan 再 fetch"**：让 LLM 先基于训练知识给 data_needs，再按需取数。省钱 + 对齐问题。
- **`query_for_briefing` 三路混合召回**（`@/apps/backend/app/mcps/research_memory_mcp.py:100-213`）：单一召回方式（纯语义 / 纯资产）都会漏，三路合并去重是当前 RAG 最佳实践。
- **`_retrieval_paths` 可追溯字段**：每条返回的 claim 标注从哪条路径来，前端可做"这条观点命中了语义+资产匹配"这类可视化。
- **`data_coverage_summary`**：告知 LLM "本地 42 条 active claims，最老 2024-01，最新 2024-11" —— 让 LLM 知道"我能用什么"，降低编造欲望。
- **strategy_fit_summary 的结构化提示**（`@/apps/backend/app/skills/briefing_skill.py:52-94`）：强制因素拆分 + 概率矩阵 + 期望值加权决策，是 prompt engineering 的高级玩法。
- **证据链接持久化**：`BriefingEvidenceLink` 记录哪些 claim/event 被用到，配合 `source_claim_ids` 可以事后反查简报的每一块结论从哪来。

### 2.3 差距与风险

**D-B1：双实现分裂（严重）**
`/api/v1/briefings/plan-and-research`（`briefings.py:363-649`，287 行）与 `BriefingWorkflow.generate`（`briefing_workflow.py`，220 行）并存，**召回逻辑不一致**：
- 前者有 `is_open_ended` 宏观分支、WebSearchMCP 注入 events、macro_snapshots 聚合
- 后者只调 `query_for_briefing` + 四个 MCP，无 web 搜索、无宏观分支

scheduler 用 Workflow 版，前端用端点版 — 同一个问题通过"调度定时任务"和"聊天里问"得到的答案**可能明显不同**。
**建议**：把 `plan-and-research` 端点的逻辑抽成 `BriefingWorkflow.generate_v2()`，两处调用同一个函数。2 天工作量，消除长期歧义。

**D-B2：资产识别逻辑散落 4 处**
- `intent_skill.ASSET_KEYWORDS`（38 条）
- `briefings.py:377-384 _ASSET_KEYWORDS`（13 条）
- `research_plan_skill.py:122-144 _guess_asset()`（9 组别名）
- 前端 `ResearchChat.tsx:939` regex（19 个符号）

四处定义不一致，ETH 在某些地方没列，SOL 在某些地方没列。
**修复**：抽出 `app/utils/asset_alias.py::resolve_asset(text) -> Optional[str]`，4 处都调它。1 天工作量。

**D-B3：引用真实性无校验**
Briefing 的 `supporting_points` 要求"末尾注明（私域观点）"这种 source tag，但**后端不校验 LLM 是否真引用了 claims**。LLM 可以在 `supporting_points: ["美元指数有走弱迹象（私域观点）"]` 里编造 — 实际私域知识库里可能根本没这条。
**修复**：用 claims 的 summary 做向量相似度检查（prompted-claims vs supporting_points），低于阈值标红为"可能编造"。1-2 天。

**D-B4：Prompt 体量失控风险**
当前配置：`claims=10 条` + `events=8 条 + web_results=5 条` + `market + flow + macro` 全注入。
BriefingSkill 的 `_build_prompt` 把所有数据拼成单个 user_prompt，再加 `BRIEFING_SYSTEM_PROMPT`（2200+ 字符）。若每条 claim 有 full_quote（240 字符），10 条就 2400 字符 —— prompt 轻松超过 8K token。
`max_tokens=4096` 是响应上限，但 prompt+response 合计可能爆模型窗口。
**建议**：
- `claims` 裁剪：只保留 top-5 的 full_quote，其余只给 summary
- 给 `_build_prompt` 加 token 估算 + 截断逻辑

**D-B5：`data_needs` 未与成本控制挂钩**
*位置*：`briefings.py:468, 489, 513, 521`

ResearchPlan 输出了 `data_needs: {flow_signals: false}`，但代码里只是"`if not ...: skip`"。**没有成本反馈回路**：如果 plan 说不需要 flow，但 fusion 仍然编造了一个 flow-based 论点，系统不会报警。
**短期不急**，但长期需要"prompt 里的 supporting_points 不得超出 data_needs 允许的数据来源"这类约束。

### 2.4 推荐改进

| 改进项 | ROI | 工作量 |
|---|---|---|
| **双实现合并**（D-B1） | 很高 | 2d |
| **资产识别统一**（D-B2） | 高 | 1d |
| **引用真实性校验**（D-B3） | 高 | 1-2d |
| Prompt 体量控制（D-B4） | 中 | 0.5d |

---

## 3. Debate 链路（委员会辩论）

### 3.1 数据流

```
question + asset
    ↓
committee_workflow._gather_context(asset)  ← ⚠️ 召回退化点（见 D-D1）
    ├── ResearchMemoryMCP.get_active_claims_by_asset(asset, limit=8)
    ├── EventFeedMCP.fetch_recent_events(asset, limit=4)
    └── MarketDataMCP.get_market_state(asset)
    ↓
CommitteeOrchestrator.plan(task_mode, data_coverage, is_watchlist, user_force_full)
    → OrchestrationPlan{activated_seats, seat_budgets, token_budget}
      必选席位 + 可选席位（audit/pnl 按条件激活）
      超预算时逐步降级可选席位
    ↓
for round_num in rounds:
    for role in activated_seats:  # baseline/probability/risk/structure/audit/pnl
        CommitteeDebateSkill.debate_round()
            → DebateRoundOutput{role, position, key_arguments, challenges, direction, confidence}
        保存 DebateRound
    ↓
FinalAdjudicationSkill.adjudicate(all_rounds)
    → AdjudicationOutput{direction, conviction, synthesis, risk_warnings, structure_warnings,
                          action_economics, minority_view, invalidation_conditions, ...}
    ↓
持久化：FinalAdjudication + DecisionSnapshot（冻结决策时点证据）
```

### 3.2 做得好

- **CommitteeOrchestrator**（`@/apps/backend/app/services/committee_orchestrator.py:139-276`）：真正 production-grade 的席位编排：
  - 按 task_mode 选必选/可选席位
  - 数据覆盖不足时降级量化类席位（probability/structure/pnl）
  - 非关注资产限制 ≤2 席
  - Token 预算超限时逐步降级可选席位
  - 全程 notes 记录决策理由，可审计
- **V2 新席位（audit + pnl）**：真伪审计官、执行经济性顾问 —— 从"多空对立"升级到"多视角并存"。
- **DecisionSnapshot**：冻结当时的 claims + events + market + activated_seats + 最终结论，`replay` 功能的基石。是本系统最有创新性的设计之一。
- **FinalAdjudication 字段丰富**：synthesis + 4 个席位分数 + key_agreements/disagreements + risk_warnings + structure_warnings + action_economics + minority_view + invalidation_conditions。不只是"bull 赢还是 bear 赢"，而是完整决策档案。
- **robust_json_parse + 6 种字段 fallback**：`committee_debate_skill.py:283-327` 对不同 LLM 响应 schema 的兼容层 — 实战沉淀，不可或缺。

### 3.3 差距与风险

**D-D1：召回退化（严重，未被意识到）**
*位置*：`committee_workflow.py:373` `claims_resp = memory.get_active_claims_by_asset(asset, limit=8)`

这是**本次评估最重要的发现**：
- Chat 用的是 `balanced_semantic_search`（立场平衡 + 语义融合）
- Briefing 用的是 `query_for_briefing`（三路混合召回 + trust 加权）
- **Debate 只用 `get_active_claims_by_asset` —— 拿该资产最新 8 条，按时间倒序**

这意味着：
1. **没有语义匹配**：问"BTC 的 ETF 资金流影响"，拿到的可能是 8 条讨论"BTC 减半"的旧观点
2. **没有立场平衡**：如果最近录入的 8 条都是看多，bull 席位顺势确认，bear 席位无料可怼，辩论变独角戏
3. **没有 trust 加权**：低信任来源和高信任来源等权
4. **没有 contradiction 降权**：已被打上 `is_contradicted=true` 的 claim 照样入席

**修复极简单**（单行改动）：
```python
# committee_workflow.py:373
claims_resp = memory.query_for_briefing(query=question, asset=asset, limit=10)
claims_data = claims_resp.data  # 已是 dict 列表
```
**ROI**：极高。这是三条链路中付费最贵的（~$0.50/次），证据地基却最弱，投入产出比严重倒挂。**0.5 天修复，立即升级辩论质量**。

**D-D2：flow / macro / web 证据缺失**
*位置*：`committee_workflow.py:366-406 _gather_context()`

只取了 claims + events + market 三路。相较之下：
- Briefing 取了 5 路（+ flow + web）
- DecisionSnapshot 保存时 `flow_snapshot={}`、`macro_snapshot={}` 留空

即 debate 的席位（尤其是 probability/structure/pnl）根本看不到资金费率、OI、宏观数据 —— 但 prompt 还在要求它们"基于实时数据判断"。
**建议**：让 debate 也复用 `plan_and_research` 的阶段二逻辑。等 D-B1（双实现合并）做完，这个自然解决。

**D-D3：DB 列名 vs 语义脱节**
`model_bull` / `model_bear` / `model_neutral` 是 V1 列名，V2 语义已升级为 baseline/probability/risk/structure/audit/pnl。代码里通过 `_ROLE_TO_DB_COLUMN` 字典映射兼容，但：
- 新加入的读者看 `DebateRecord.model_bull` 会误以为 "多方席位"，实际是基准分析师
- 新席位（audit/pnl）全部塞进 `model_neutral` 列
- 读代码成本高，迁移 DB schema 成本也高

**建议**：做一次 schema 迁移，把 3 列改为 `model_by_seat: JSON`（`{"baseline": "committee_a", "risk": "committee_c", ...}`）。1 天工作量，清掉 10 年技术债。

**D-D4：Parse 容错的深层信号**
`_parse` 方法有 4 次字段名 fallback（position/thesis/summary/trend_outlook/technical_observation）+ 2 次列表类型推断 + 1 次字典展平。这说明：
各委员会模型（6 个不同厂商）对 JSON schema 的遵守度差异很大。
**建议**：把 `response_format: json_schema` 传下去（`FusionSkill` 已这么做），强制 schema 遵守。支持结构化输出的模型直接解析，不支持的走当前 fallback。

### 3.4 推荐改进

| 改进项 | ROI | 工作量 |
|---|---|---|
| **召回升级为 query_for_briefing**（D-D1） | **极高** | **0.5d** ⭐ |
| flow/macro/web 补齐（D-D2） | 高 | 1d（配合 D-B1） |
| DB schema V2 迁移（D-D3） | 中 | 1d |
| response_format json_schema（D-D4） | 中 | 1d |

---

## 4. 跨链路共性问题

### C1. Claim 生命周期被忽略
所有召回都只过滤 `status="active"`。`expired / invalidated / superseded` 的 claim 直接不出现。但有些场景很有价值：
- 用户问"为什么之前说的 XXX 观点不对了？" —— 系统找不到任何线索
- 复盘：某 claim 刚被 `lifecycle_check` 标记为 invalidated，相关问题会"忘记"这条教训

**建议**：新增 `retrieve_mode="include_historical"` 开关，`replay` / `audit` 场景下允许召回 invalidated claims。

### C2. 时间衰减缺失
`valid_until` 字段存在，但**无召回时的时间加权**。3 天前 vs 3 个月前的观点等权参与评分。
在金融场景尤其有害（资金流向信号 1 周后就过期了）。
**见 G12（已列 GAP）**。

### C3. 成本追踪不一致
- Committee 用 `_total_cost += llm_client.get_last_usage().get("cost_usd")` 累加（正确）
- Briefing 的 `/plan-and-research` 返回 `cost_usd: llm_client.get_last_usage()` —— 这只是**最后一次**调用的 cost，不是本轮全部 LLM 的累计
- Chat 类似问题（只记录最后一次）

**影响**：cost dashboards 里 briefing 实际花 $0.15 但显示 $0.04（只是 BriefingSkill 的 cost，不含 ResearchPlan + Fusion 的）。**1 天修复**，给每个 workflow 加一个 cost_accumulator。

### C4. ChatMessage payload 膨胀
`chat` 路径的 assistant payload：
- `context_block[:4000]`
- `raw_input[:2000]`
- `raw_output[:4000]`
- `citations`（最多 8 条 × ~200 字节）
- `market_snapshot`

单条 ~12KB，一个 10 轮对话 ~120KB。10 个 session 后 > 1MB。长期累积会成为 DB IO 瓶颈。
**短期不急**，但建议：context_block 和 raw_input/output 改为"写到外部存储（如 S3）+ DB 只存 URL"。或者截断更狠（各 1000 字符）。

### C5. 无跨会话用户偏好
用户说 "我是波段交易者" "我不做杠杆" 这类偏好不会跨 session 记忆。每次重新解释。
**短期不急**，但一旦加上会显著提升 UX。可以做成一个 `UserProfile` 表 + intent 层注入。

### C6. Embedding 单一
所有向量都是同一个模型 + 同一维度。对于多语言混合（中英）、技术指标 vs 新闻 vs 策略规则这类不同文本类型，单一 embedding 精度有限。
**长期投入**：multi-vector 或 ColBERT-style late-interaction。不紧急。

---

## 5. 建议执行顺序（按 ROI 排序）

### 本周（高 ROI + 低工作量）
1. **D-D1：Committee 召回升级** — 0.5d，三条链路中最严重的功能退化。1 行改动。
2. **D-C3 + D-B3：引用真实性校验** — 1d，chat/briefing 共同获益，降低幻觉率。
3. **D-B2：资产识别统一到单一工具函数** — 1d，清除 4 处代码冗余。

### 两周内
4. **D-B1：Briefing 双实现合并** — 2d，前端/scheduler 行为一致。
5. **C3：成本追踪统一** — 1d，修正 briefing / chat 的 cost 低估。
6. **G12：时间衰减评分** — 0.5d（已在 GAP 列表）。

### 一个月内
7. **G11：Cross-Encoder rerank** — 2d（已在 GAP 列表）。
8. **G14：Query Rewrite + 对话历史融合** — 1-2d。
9. **D-D3：委员会 DB schema V2 迁移** — 1d。

### 长期投入
10. **C1：Claim 生命周期 include_historical 模式** — 2d。
11. **C4：ChatMessage payload 外部化存储** — 3d。
12. **C5：UserProfile 跨会话偏好** — 3d。
13. **C6：Multi-vector embedding** — 1 周+。

---

## 6. 一句话总结

> **"存储层是 A+，召回融合层是 A-，回答生成层是 A，审计层是 A+。但委员会链路的召回有一个未被意识到的退化点，修它只要 0.5 天 —— 应该立即做。"**

其余差距都是"已知优化项"，属于产品自然迭代范畴，不改也能跑。
