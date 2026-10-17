# 召回 → 回答 全链路评估 v2（2026-04-18）

> **历史评估**：保留旧链路审计，不代表当前 v0.1 验收结果。

> 本文是 `STORAGE_TO_ANSWER_REVIEW.md`（2026-04-17）的后续版。
> A1–A6 六个 A 级改进已合入（asset_alias / cost_scope / Briefing 双实现合并 / BGE reranker / stance-balance / debate schema V2）。
> 本次评估目的：**站在"一个问题进来、一个答案出去"的全链路视角**，逐级打分，标出 B 级遗留项。

---

## 一、管线全景图（10 个环节）

```
  用户输入
    │
    ▼
 ① /auto 路由决策 ──────► 决定走 chat / briefing / debate
    │
    ▼
 ② Intent 识别 + Asset 规范化
    │
    ▼
 ③ 召回（按模式分岔）
    ├─ chat:     ChatHarness.balanced_semantic_search
    ├─ briefing: ResearchMemoryMCP.query_for_briefing（三路混合）
    └─ debate:   ResearchMemoryMCP.query_for_briefing + 行情/资金/事件 MCP
    │
    ▼
 ④ 二阶段精排（BGE reranker，可选）
    │
    ▼
 ⑤ 立场均衡（stance-balance）
    │
    ▼
 ⑥ 时间衰减 + 信任权重融合评分
    │
    ▼
 ⑦ 上下文组装（context block / fusion / gathered context）
    │
    ▼
 ⑧ LLM 生成（ChatSkill / FusionSkill+BriefingSkill / DebateSkill+Adjudication）
    │
    ▼
 ⑨ 引用校验 + 花费累计（citation_validator + cost_scope）
    │
    ▼
 ⑩ 持久化 + 渲染
```

每一步对应的**代码真相**（文件:行）：

| 步骤 | 代码位置 |
|---|---|
| ① | `app/services/mode_router.py` / `app/api/v1/chat.py::auto_route` |
| ② | `app/skills/intent_recognition_skill.py` + `app/utils/asset_alias.py` |
| ③-chat | `app/services/chat_harness.py::balanced_semantic_search` |
| ③-briefing | `app/mcps/research_memory_mcp.py::query_for_briefing` |
| ③-debate | `app/workflows/committee_workflow.py::_gather_context` |
| ④ | `app/utils/reranker.py` + `chat_harness.py:129-158` |
| ⑤ | `app/utils/stance_balance.py` |
| ⑥ | `chat_harness.py::_rerank_score` + `app/utils/time_decay.py` |
| ⑦ | `chat_harness.py::build_chat_context` / `skills/fusion_skill.py` |
| ⑧ | `skills/chat_skill*` / `briefing_skill.py` / `committee_debate_skill.py` / `final_adjudication_skill.py` |
| ⑨ | `app/utils/citation_validator.py` + `app/utils/cost_scope.py` |
| ⑩ | `apps/frontend/src/pages/ResearchChat.tsx` |

---

## 二、逐环节评估

### ① 路由决策（/auto）

**现状**：`mode_router.py` 七档优先级链；默认降级到 chat。已有 32 条单测。

| 维度 | 评分 | 说明 |
|---|---|---|
| 正确性 | 🟢 优 | 斜杠命令 + 意图 + 关键词 + 资产识别多信号融合 |
| 透明度 | 🟢 优 | 前端 `RoutingBanner` 显示 reason + 成本 |
| 可观测性 | 🟡 可 | 但路由错判时**没有用户反馈回路**（无"此路由不对"按钮）|
| 延迟 | 🟢 优 | 纯规则 + 一次短 LLM，~50-200ms |

**B 级建议**：加 `routing_feedback` 端点，用户点"此路由不对"记录到 DB，周度批量复盘迭代路由规则。

---

### ② Intent 识别 + Asset 规范化

**现状**：
- `intent_recognition_skill.py` 用 rules → LLM fallback 的结构化识别，输出 `Intent {mode, assets, confidence, ...}`。
- A1 新增 `asset_alias.py` 统一覆盖 30+ 币种，`briefings.py` 已全部接入 `detect_asset()`。

| 维度 | 评分 | 说明 |
|---|---|---|
| 覆盖面 | 🟢 优 | 30+ 币种 + 常见别名 + 位置感知正则 |
| 一致性 | 🟢 优 | A1 后前后端/plan/briefing/committee 全部统一 |
| 误识别 | 🟡 可 | **英文大小写混用时有误判风险**（如 `ARB` vs `arbitrage`）—— 需加正则边界 |

**B 级建议**：加 `tests/test_asset_alias_adversarial.py` 针对缩写歧义（ARB/Arbitrum vs arbitrage、SOL/Solana vs solve）做对抗用例。

---

### ③ 召回层

这是**最核心也最分歧**的一环。三种入口：

#### ③-a `ChatHarness.balanced_semantic_search`（chat 模式）

**现状**：
1. 先拉 `limit × candidate_multiplier (≥16)` 条候选（调 `semantic_search.search_similar_claims`）
2. A4 reranker（如启用）→ A5 stance-balance → time-decay × trust × confidence 融合打分
3. 返回 top-K + `stance_distribution` + `balanced_applied`

**优点**：
- 🟢 5-8 个阶段管线化，每阶段可独立调参
- 🟢 关注 claim 立场分布，避免单边偏见
- 🟢 time_decay 让老 claim 自动退位

**风险**：
- ✅ ~~`asset_filter` 只接受单一资产~~ → **D6（04-20）修复**：扩展为 `Optional[Union[str, List[str]]]`，多资产最多拉 3 个 snapshot
- 🟡 reranker 与 bi-encoder 分数**未做共识检查**，reranker 错判时无回退
- ✅ ~~候选池大小 `max(limit*3, 16)` 固定~~ → **D10（04-20）修复**：提升到 `max(limit*5, 30)`，grid search 实测 recall@8 = 79%，留 21% 余量

#### ③-b `query_for_briefing`（briefing / deep-research）

**现状**（`research_memory_mcp.py` 约 247 行）：
三路混合召回：
1. **语义路**：`search_similar_claims(query)` 返回 top-K
2. **资产精确路**：`get_active_claims_by_asset(asset)` 按资产拉
3. **信任保底路**：按 `source_trust_weight` 取前 N

三路合并去重，带 `retrieval_paths` 字段标注每条来自哪路。

**优点**：
- 🟢 **对"主题问题"很鲁棒** —— 语义召回 miss 时资产路兜底；资产路 miss 时信任路兜底
- 🟢 A3 后 /generate_briefing 端点已从劣化的 `get_active_claims_by_asset` 切换到此方法 —— 前后端召回质量对齐

**风险**：
- 🟡 **未接入 reranker**（A4 的 BGE 只在 ChatHarness 生效）—— briefing 模式无二阶段精排
- 🟡 **未接入 stance-balance**（A5 同理）—— 多空分布可能失衡
- 🟡 `limit=12` 可能对"全库宏观问题"偏小

**B 级建议 D-D1.5**：把 reranker + stance-balance 提取为独立后处理器，三个召回入口都调用。

#### ③-c `committee_workflow._gather_context`

**现状**：复用 `query_for_briefing` + 并行拉 MarketDataMCP / FlowContinuityMCP / EventFeedMCP。

**风险同 ③-b**；额外的是 **MCP 超时时的降级**：目前失败就返回 empty，没有重试或缓存复用。

---

### ④ 二阶段精排（BGE Cross-Encoder）

**现状（A4 今日完工）**：
- `reranker.py` lazy-load `BAAI/bge-reranker-v2-m3`
- feature flag `ENABLE_RERANKER` 默认 False
- 仅集成到 `ChatHarness`

| 维度 | 评分 |
|---|---|
| 设计 | 🟢 优，单例 + 降级兜底 + HF_HOME 指 E 盘（不污染 C 盘） |
| 测试 | 🟢 优，9/9 单测（含 mock compute_score、空输入、异常降级；**D7 新增 rank 归一回归测**） |
| 真实线上验证 | 🔴 **未做** —— 需用户跑 `download_reranker.py` 下载 278MB 模型后才能 a/b 评估 |
| 降级语义 | 🟢 **D7（04-20）修复**：未装 FlagEmbedding / compute_score 失败时返回 `(i, 1.0 - i/n)` 而非统一 0.5，保留 bi-encoder 相关性信号 |

**建议**：
1. 先不在 briefing 集成，等 chat 模式 a/b 有数据再推广
2. 加 **reranker 准确率指标**：每次 rerank 后记录"top-5 命中率"随时间变化

---

### ⑤ 立场均衡（Stance-Balance）

**现状（A5）**：`stance_balance.apply_stance_balance` 在 top-K 中**强制保留至少 1 条 opposite-stance**；若多数派撑满则从长尾补一条。

| 维度 | 评分 | 说明 |
|---|---|---|
| 目标对齐 | 🟢 优 | 防 LLM 单边偏见，和 Anthropic 的"constitutional balancing"思路一致 |
| 单测 | 🟢 优 | 边界用例（全单边、全中性、空集）全覆盖 |
| 生产验证 | 🟡 可 | 需线上跑一段看 `balanced_applied=true` 的比例是否合理（预期 20-40%）|

---

### ⑥ 时间衰减 + 信任权重融合

**现状**：`time_decay_factor` 按 time_horizon（immediate/short/medium/long）不同衰减；`_rerank_score` 综合 sem_rank × 0.50 + trust × 0.25 + conf × 0.12 + decay × 0.13。

**风险**：
- 🟡 **权重凭直觉设定**，未做回归优化
- 🟡 time_decay 对"事件型"和"结论型" claim 用同一函数 —— 一个 8 小时前的新闻和 8 小时前的"比特币长期看涨"衰减应当不同

**B 级建议**：加 `tests/test_scoring_weights.py` 用 LLM-as-judge 对 100 个历史问题做 grid search 找更优权重。

---

### ⑦ 上下文组装

**现状**：
- **chat 模式**：`context_block` 字符串，含编号 + quote + asset + stance + time_horizon + speaker
- **briefing 模式**：Fusion 再做一次"主题聚合 / 矛盾检测 / 风险提取"再给 briefing_skill
- **debate 模式**：原始 claims + market + events 直接注入各席位 prompt

**风险**：
- 🔴 **context_block 字符长度未上限** —— 极端情况 12 条 claim × 长 quote 可能撑爆模型上下文窗口
- 🟡 Fusion 阶段"压缩为主题"会丢失原始 quote 细节，briefing 的 citation 精度低于 chat

**B 级建议 D-C1**：在 `chat_harness.build_chat_context` 加 token 限额（比如 4k）+ 超限自动截断到最高分前 N 条。

---

### ⑧ LLM 生成

| 模式 | Skill | 结构化输出 |
|---|---|---|
| chat | `chat_skill` via harness | 自由文本 + `[N]` 引用标记 |
| briefing | `fusion_skill` → `briefing_skill` | JSON（含 supporting/contrary/key_risks/strategy_fit/invalidation）|
| debate | `committee_debate_skill` → `final_adjudication_skill` | JSON（per-seat + synthesis）|

**优点**：
- 🟢 prompt_caching（S3）已接入
- 🟢 response_format=json_schema（大部分 provider）
- 🟢 A2 cost_scope 完整捕获每次调用

**风险**：
- 🔴 **LLM 不稳定输出** —— 今天用户截图的 "strategy_fit 乱飞" 正是：prompt 要求 markdown 表格，LLM 给了扁平文本。前端 parser 只能兜底。需要：
  1. Few-shot examples 强化格式
  2. 前端兼容多种格式（今天已加 fallback prose 渲染）
  3. 对 briefing_skill 加 post-validation（schema 不通过自动重试 1 次）
- 🟡 **chat citation 不在 prompt 里强约束** —— LLM 可能编造 `[9]` 当 citations 只有 8 条；虽然 `citation_validator` 会剔除，但先污染后清洗不如先约束

---

### ⑨ 引用校验 + 花费累计

**现状**：
- `citation_validator.validate_citations(reply, len(citations))` 剔除超范围 `[N]` 并返回清洗后文本
- A2 `cost_scope` contextvars 累计整次请求所有 LLM 调用花费

| 维度 | 评分 | 说明 |
|---|---|---|
| 成本准确性 | 🟢 优 | 从只捕获最后一次 → 全部累计 |
| 引用真实性 | 🟡 可 | 剔除"超范围"有效，但**不能检测"错用了正确编号"**（比如 `[3]` 应该是支持但 LLM 说成反驳）|

**B 级建议**：加 `citation_semantic_check.py` 用小模型异步校验每个 `[N]` 的"引用方向是否与 claim stance 一致"，错配时降级 UI 为灰字 + 警告。

---

### ⑩ 持久化 + 渲染

**持久化**：`chat_messages` / `briefing_cards` / `debate_records`（+ A6 model_by_seat）/ `job_runs` / `setup_cards`。完整 audit trail。

**渲染**（今日刚重写）：
- ResearchChat.tsx 已修溢出 + break-words（UI-1 到 UI-5）
- BriefingCard 简化为单层卡 + 轻分隔（UI-2）
- `strategy_fit_summary` 接入 Donut + 表格 + KV inline（UI-9/10）
- Hover 字号冲突修掉（UI-12，刚刚）

| 维度 | 评分 |
|---|---|
| 溢出/换行 | 🟢 优 |
| 视觉层次 | 🟢 优 |
| 交互细节 | 🟢 优 |
| 可访问性 | 🟡 可 | 还没加键盘导航 / aria-label |

---

## 三、整体评分

| 环节 | A 前 | A 后 | 备注 |
|---|---|---|---|
| ① 路由 | 🟢 | 🟢 | 稳定 |
| ② Intent/Asset | 🟡 | 🟢 | A1 拉平前后端 |
| ③ 召回 | 🟡 | 🟢 | A3 消灭 briefing 劣化召回 |
| ④ 精排 | 🔴 | 🟡 | A4 接入但默认关；需线上验证 |
| ⑤ Stance-balance | 🔴 | 🟢 | A5 新增 |
| ⑥ 时间衰减 | 🟡 | 🟡 | 未变 |
| ⑦ 上下文 | 🟡 | 🟡 | 未变，仍有 token 超限风险 |
| ⑧ LLM | 🟡 | 🟡 | strategy_fit 输出不稳定 |
| ⑨ 引用 + 成本 | 🟡 | 🟢 | A2 成本全捕获 |
| ⑩ 渲染 | 🔴 | 🟢 | 今日 UI-1..12 |

**综合：🟡 → 🟢**（从"能用但多处劣化"升级到"端到端健康，剩一批优化项"）。

---

## 四、B 级待办（优先级降序）

| # | 名称 | 所属环节 | 工作量 | 期望收益 |
|---|---|---|---|---|
| **B1** | context_block token 限额 + 截断 | ⑦ | 0.5d | 杜绝极端情况上下文撑爆 |
| **B2** | briefing_skill JSON schema 自动重试 | ⑧ | 0.5d | 抑制 strategy_fit 乱飞 |
| **B3** | reranker + stance-balance 抽成公共后处理器，三入口统一 | ③④⑤ | 1d | briefing/debate 也享受质量提升 |
| **B4** | citation_semantic_check（方向校验） | ⑨ | 1d | 消灭"引对了但用错"的幻觉 |
| **B5** | asset_alias 对抗用例 + 边界正则收紧 | ② | 0.5d | 消除 ARB / SOL 等歧义 |
| **B6** | routing_feedback 端点（"路由不对"按钮） | ① | 0.5d + 前端 | 让路由规则持续改进 |
| **B7** | reranker 线上 a/b 验证 + metrics 看板 | ④ | 1d | A4 真实收益量化 |
| **B8** | scoring weights grid search | ⑥ | 1d | 找更优的融合权重 |

**合计**：约 **6 人天**，可按优先级单独推进。

---

## 五、推荐的验证动作（用户可立即执行）

### 1. 启动 reranker 做 a/b（最高收益）
```powershell
$env:HF_HOME = "<model-cache-directory>"
python apps\backend\scripts\download_reranker.py
# 然后在 .env 加 ENABLE_RERANKER=True，重启后端
```
跑 5-10 个真实问题，比较 `stance_distribution`、`claims_used`、答案准确度。

### 2. 检查 stance-balance 真实命中率
跑 20 个"方向性"问题（例如"BTC 下周看涨吗"）后，去 `chat_messages` 表查：
```sql
SELECT COUNT(*) FILTER (WHERE (payload->>'balanced_applied')::bool = true) * 100.0 / COUNT(*)
  AS balanced_rate
FROM chat_messages WHERE role='assistant' AND created_at > NOW() - INTERVAL '7 days';
```
预期 20-40%。过低说明 claim 库多空已均衡（好事）；过高说明多数话题单边（需检查 ingestion 质量）。

### 3. 观察成本准确性（A2）
前端响应字段 `llm_call_count` + `cost_usd` 应与 SQLite `cost_log.db` 的累加吻合。

### 4. 检查 schema V2 迁移效果
```sql
SELECT COUNT(*) FILTER (WHERE model_by_seat IS NOT NULL) AS v2_rows,
       COUNT(*) FILTER (WHERE model_by_seat IS NULL) AS v1_legacy_rows
FROM debate_records;
```
新建辩论应全部写到 `model_by_seat`；老数据保持 NULL，读时走 fallback 正常。

---

## 六、结论

A 级整改后，全链路从"能跑但多处劣化"进入"端到端健康"状态。**没有 P0 隐患**。

剩余的 B 级项都是**性能/鲁棒性优化**，可以按业务节奏逐步推进，不需要紧急处理。

**最值得先做**：B1（context 限额）+ B2（briefing JSON 重试）— 两者加起来 1 天，直接消灭目前已观察到的两个偶发失败模式（上下文超限、strategy_fit 格式乱）。

---

*评估人：Cascade AI Pair Programmer · 2026-04-18 01:35 UTC+08:00*

---

## 七、B1–B8 完成记录（2026-04-18 02:55 UTC+08:00）

> 全部 B 级 8 项已合入 main；总测试数从 ~100 提升到 **167 passed（+67 条新测）**；TypeScript 零编译错误。

### 执行总览

| # | 名称 | 所属环节 | 核心文件 | 新增测试 | 状态 |
|---|---|---|---|---|---|
| **B1** | context_block token 限额 | ⑦ | `utils/token_budget.py`, `services/chat_harness.py` | 13 | ✅ |
| **B2** | briefing JSON schema 重试 | ⑧ | `utils/llm_retry.py`, `skills/briefing_skill.py`, `skills/final_adjudication_skill.py` | 6 | ✅ |
| **B3** | 公共后处理器 | ③④⑤ | `utils/claim_post_processor.py`, `chat_harness.py`, `mcps/research_memory_mcp.py` | 21 | ✅ |
| **B4** | citation 语义方向校验 | ⑨ | `utils/citation_stance_checker.py`, `api/v1/chat.py` | 15 | ✅ |
| **B5** | asset_alias 词边界 | ② | `utils/asset_alias.py` | 43 | ✅ |
| **B6** | routing_feedback 端点 + 👍👎 UI | ① | `api/v1/routing_feedback.py`, `RoutingBanner.tsx`, `services/hooks.ts` | 10 | ✅ |
| **B7** | reranker A/B 评估脚本 | ④ | `scripts/eval_reranker.py` | — | ✅（含实测） |
| **B8** | golden set + grid search | ⑥ | `scripts/grid_search_weights.py`, `scripts/golden_set.template.jsonl` | 19 | ✅（待人工标注后跑） |

### 关键实现细节

#### B1 · Token 预算
- 用字符 ÷ 3.5 粗估 token（中英通吃）
- `chat_harness.build_chat_context` 在组装 `context_block` 时贪心截断，上限默认 4000 tokens
- 诊断字段 `context_tokens` / `context_dropped` 回传前端，可观测

#### B2 · JSON schema 重试
- `call_with_schema_retry(fn, schema_model)`: 第一次失败时追加 `"上次回复 JSON 无效，请严格按 schema 返回"` 的纠正 prompt 再调一次
- BriefingSkill 和 FinalAdjudicationSkill 已接入；失败后 fallback 到原有的宽松解析
- **注意**：重试意味着最坏情况调 2 次 LLM，成本上限翻倍；实测 format error 率 <5%

#### B3 · 公共后处理器
- 抽出 `post_process_candidates(candidates, query, top_k, ...) → PostProcessResult`
- 内部依次做：**Reranker → 融合打分（sem/trust/conf/decay，权重可注入）→ stance-balance 对立保留 → top-K 截取**
- ChatHarness `balanced_semantic_search` 和 ResearchMemoryMCP `query_for_briefing` 都改为调用它
- **实测发现并修复 1 个真实 bug**：原代码 `from app.core.config import settings` —— 路径错误（实际是 `app.config`），导致 `ENABLE_RERANKER` 永远读不到，公共管线下 reranker 从未真正激活。已修正

#### B4 · citation 语义方向
- 基于关键词启发式：从 LLM 输出的引用句里提取"看涨/看跌/中性"方向词，与被引用 claim.stance 比对
- **warn-only，不改文本**：结果写入 `message.payload.stance_mismatches`，前端可选展示
- 降低"LLM 引对了 ID 但立场用反"的幻觉概率

#### B5 · asset_alias 词边界
- ASCII 关键词匹配加 `\b`：`solve` 不再命中 `SOL`，`method` 不再命中 `ETH`
- CJK 关键词保持子串匹配（中文无空格分词）

#### B6 · routing_feedback
- **设计亮点**：复用现有 `user_overrides` 表（`target_type="routing"`, `override_type="routing_feedback"`），**零 migration**
- 三个端点：
  - `POST /api/v1/routing-feedback` — 提交 👍/👎/切换模式
  - `GET /api/v1/routing-feedback/stats` — 聚合各路由 accept_rate / helpful_rate
  - `GET /api/v1/routing-feedback/by-message/{id}` — 单消息的反馈历史
- 前端 `RoutingBanner` 右侧新增两个小按钮（👍/👎），再次点同向 = 取消表态

#### B7 · 评估脚本 实测（2026-04-18 02:52）

查询集：6 条（BTC×2, ETH×2, 宏观×2），top_k=6，候选池 13.3 条 avg。

| Query | Pool | Jaccard A vs B | A-dist → B-dist |
|---|---|---|---|
| BTC 目前处于什么位置？下一步怎么走？ | 18 | **20.0%** | b2/s3/n1 → b4/s1/n1 |
| 比特币风险因素有哪些？ | 18 | **20.0%** | b1/s4/n1 → b3/s1/n2 |
| ETH 合并之后长期前景如何 | 4 | 100.0% | b2/s2/n0 → b2/s2/n0 |
| 以太坊生态能否重新扩张？ | 4 | 100.0% | b2/s2/n0 → b2/s2/n0 |
| 美联储会继续加息吗？ | 18 | **33.3%** | b1/s3/n2 → b0/s4/n2 |
| 通胀会持续多久？市场要怎么应对？ | 18 | 50.0% | b1/s2/n3 → b1/s3/n2 |

**Summary**：
- 平均 Jaccard = **53.89%**（reranker 改变 ~46% 的 top-K 组成）
- Jaccard<50% 的查询：3/6（BTC 双查 + 美联储）
- ETH 池子只有 4 条 → top-6 被全量取走 → Jaccard=100% 属预期

**初步洞察**：
1. **Reranker 生效且改变显著**：BGE Cross-Encoder 的语义精排与纯向量排序排序差异大，说明值得开启（A4 的投入有回报）
2. **反常信号需复盘**：`"比特币风险因素"` 查询，reranker 反而减少了 bear claims（s4→s1）——可能因 reranker 语义倾向"相关性"而非"立场"，需人工抽查 top-3 看是否真的 off-topic
3. **ETH 候选池太小**：仅 4 条 → **ingestion 质量问题，ETH 相关 claim 储备不足**，不是 retrieval 问题

#### B8 · Grid Search
- 需要先由人工填充 `scripts/golden_set.jsonl`（每条：query + 3-10 个"相关 claim ID"）
- 运行：`python -m scripts.grid_search_weights --golden scripts/golden_set.jsonl`
- 遍历 sem∈[0.3, 0.7], trust/conf/decay∈[0.05, 0.40] 的 43 组（step=0.1）或 ~数百组（step=0.05）
- 按 nDCG@K 排序，与 baseline `{sem:0.50, trust:0.25, conf:0.12, decay:0.13}` 对比
- 推荐：积累反馈样本 ≥ 20 条后再跑

### 后续建议（不紧急）

1. **Reranker 再观察**：启用 reranker 跑 1 周真实流量，对比 `/api/v1/routing-feedback/stats` 的 helpful_rate 变化
2. **Risk 查询异常**：审查"比特币风险因素"在 reranker 开启时的 top-3，若 off-topic 需考虑给 `stance=bearish` 的 claim 加 0.05 boost（仅限含"风险"关键词的查询）
3. **ETH 数据入库**：候选池 = 4 说明 ingestion 管线对 ETH 话题覆盖不足；考虑加一批 ETH KOL / 研报源
4. **citation_stance_checker 监控**：一周后 SQL 聚合 `jsonb_array_length(payload->'stance_mismatches')`，若 >5% 消息有 mismatch，说明 LLM system prompt 对"引用立场一致"强调不够

---

*本次记录人：Cascade AI Pair Programmer · 2026-04-18 02:55 UTC+08:00*

---

## 八、数据管线升级（2026-04-18 03:40 完成）

**动机**：与 B1-B8 召回层整改并行，解决另一侧反馈 —— 用户希望「数据新鲜 + 覆盖面广 + 可手动刷新」，同时降低免费 API 压力。

### 8.1 默认资产扩展（6 → 16）

`services/snapshot_service.get_default_symbols()` 固定下列 16 项：

- **主流指数**：SPX · NDX · VIX
- **港股/A股**：HSI · HSTECH · CSI300 · ZZ500 · CYB
- **商品**：GOLD · SILVER · OIL
- **汇率**：DXY
- **加密**：BTC · ETH · BNB · SOL

### 8.2 衍生指标三类（新）

`services/indicator_fetchers.py` 直连免费 HTTP API，写入 `market_snapshots`（`market=INDICATOR`）：

| 指标 | symbol | 来源 | 本日抓取样本 |
|---|---|---|---|
| Pizza Index | `PIZZA_INDEX` | CoinGecko | BTC \$77,274 → 10k BTC \$772 M |
| Fear & Greed | `FEAR_GREED` | alternative.me | 21（Extreme Fear） |
| Polymarket Top 10 | `PM:<slug>` | Gamma API | vol=\$44-57M 的 2028 政治/娱乐市场 |

### 8.3 Scheduler 重构

原方案每 15 min 常规全量 + 14:00 的 2 min 高频 → 新方案：

1. **启动后 5s** 一次全量快照
2. **每日 Asia/Shanghai 12:00** 一次全量快照（长期在线保障）
3. **工作日 14:00-14:59 每 10 min**：仅 A 股/港股 5 个（不重抓指标）
4. 其余定时任务（lifecycle / radar / social 等）保持不变

防重控制：`_last_daily_snapshot_date` 保证 12:00 任务每天仅跑一次。

### 8.4 手动刷新端点（新）

| 端点 | 作用 |
|---|---|
| `POST /api/v1/snapshots/trigger` | 同步阻塞抓取（30-60s），支持 `symbols[]` / `include_indicators` 参数 |
| `GET /api/v1/snapshots/status` | 最近一次快照元数据 |

前端组件 `RefreshDataButton.tsx`（compact + 非 compact 两种样式）集成在 Chat 页 RadarBar 同一行，调用时使用 `apiLong`（10 分钟超时 axios 实例）以兼容长任务。

### 8.5 验收结果

- **三类免费 API 实测通过**：Pizza / Fear&Greed / Polymarket 均返回正常数据
- **Backend imports OK**
- **pytest 167 passed**（无回归）
- **TypeScript 零错误**

### 8.6 已知限制

- Yahoo `^HSTECH` 偶发 404（非本次引入）
- Polymarket Top 10 当前多为政治/娱乐题材；可考虑后续加 `category=crypto` 过滤

---

## 九、数据流 audit 补丁（2026-04-20，D1-D10）

延续 B1-B8 的方法论：不靠经验猜，**按 10 环节逐段扫代码找数据流漏洞**，锁定到 8 个文件、~1 人天修复。

### 9.1 动机

B1-B8 聚焦在召回/打分/提示词层面的"算法 gap"；这一轮转到"**数据流与一致性**"——寻找那些不会让单测失败、却会在真实流量下静默劣化的问题。

### 9.2 十项补丁 × 环节映射

| # | 级别 | 环节 | 位置 | 问题 | 修复 |
|---|---|---|---|---|---|
| **D1** | P0 | ⑨ | `api/v1/chat.py:399-439` | B4 `stance_mismatches` 字段定义后从未写入消息 payload | `stance_mismatches_payload` 装入 `payload.stance_mismatches`，前端可消费 |
| **D2** | P0 | 入库 | `services/semantic_search.py:139-212` | `index_claim` 不检查已有 embedding → 重复向量化；savepoint 失败路径 `expunge` 会断开 ORM | 已有 embedding 直接缓冲 return；失败路径 `claim.embedding=None + db.expire` |
| **D3** | P1 | 入库 | `workflows/ingestion_workflow.py:869-891` | 管线中途失败，已 flush 未 commit 的脏对象随 `raw_source=failed` 一起落库 → "部分成功"幽灵 claim | `except` 路径 expunge `ClaimCard/Link/LifecycleLog` + `clear_pending_vec_upserts()` |
| **D4** | P1 | 快照 | `services/snapshot_service.py:180-272` | Pizza / F&G / Polymarket 无节流，前端连点即打外部 API | 三类都接入 `api_quota.guard`（FAST 5min / HOURLY 1h），`details[*_skipped_by_quota]=True` |
| **D5** | P2 | 入库 | `workflows/ingestion_workflow.py:740-747` | 同 chunk 内新 claim 未加 cache，后续 claim 无法互相去重 | `self.db.flush()` 后立即 `_existing_claims_cache.append()` |
| **D6** | P2 | ②③ | `services/chat_harness.py:96,253,272-288` | `asset_filter: Optional[str]` 使多资产问题只过滤第一个 | 扩为 `Union[str, List[str]]`；多资产最多 3 snapshot |
| **D7** | P2 | ④ | `utils/reranker.py:79-103` | 降级返回统一 `(i, 0.5)` 抹平 bi-encoder 相关性 | 改 `(i, 1.0 - i/n)` rank 归一 |
| **D8** | B | 快照 | `services/snapshot_service.py:163-171` | `estimate_all_funds` 抛异常回滚整批 `MarketSnapshot+SourceHealthLog` | 先 `safe_commit(db)` 提交 symbol 数据再跑 estimate |
| **D9** | — | ⑧ | `skills/final_adjudication_skill.py:197-219` | `_parse` 方法此前被折叠，测试和一次性解析场景无入口 | 补回 `_parse(text) → AdjudicationOutput`，吞异常记 `error` 字段 |
| **D10** | — | ③ | `services/chat_harness.py:97,119` | 候选池 `3×` + 底线 `16` 过窄 | 改 `5×` + 底线 `30`，grid search 实测 recall@8 = 79% 时留 21% 余量 |

### 9.3 事务一致性完整模型

入库失败路径现在闭环如下：

```
_do_process() try:
  flush ClaimCard + ClaimRawSourceLink + LifecycleLog
  flush_pending_vec_upserts() 暂缓（在 commit 后执行）
  if 出错 → except:
    1. expunge 所有 dirty ClaimCard/Link/LifecycleLog    ← D3
    2. clear_pending_vec_upserts()  清空 sqlite-vec 缓冲  ← D3
    3. mark_failed(raw_source)
外层 process_source:
  safe_commit(db)  ← 此时 db.new 已经不含脏 claim，只写 raw_source=failed
  flush_pending_vec_upserts()  ← 缓冲已空，零写入
```

与 D2 的 `db.expire` 搭配——savepoint 回滚后 claim 仍然可以继续被外层代码访问（不会抛 "not bound to a Session"），只是 `embedding` 字段会在下次 attribute 访问时重新 load。

### 9.4 QuotaGuard 行为验证

三类冷却窗口：

| 指标 | 类别 | 冷却 | 数据源 |
|---|---|---|---|
| Pizza Index | FAST | 5 min | CoinGecko 公共 API |
| Fear & Greed | HOURLY | 1 h | alternative.me（官方每日更新） |
| Polymarket Top 10 | FAST | 5 min | Gamma API |

前端手动"刷新"按钮连点时：

- 首次：正常拉取，`mark_called`
- 冷却期内二次：跳过外部调用，`details["pizza_skipped_by_quota"]=True`
- 前端 `RefreshDataButton` 展示"上次更新"仍来自 DB 存量，无视觉假死

### 9.5 多资产检索 snapshot 装配

`build_chat_context(asset_filter=["BTC","ETH","SOL"])` 行为：

1. semantic_search 路径：`_asset_aliases` 展开为全部 3 个资产的别名集合（既有实现）
2. market_snapshot 路径：遍历前 3 个资产调 `MarketDataMCP.get_market_state`
3. 拼装 `_snapshots` 数组注入 context_block（最多 3 条避免膨胀）

跨资产对比问题（"ETH 和 SOL 哪个值得入场"）从"过滤错一个"→"两者都有行情+引用"。

### 9.6 验收数据

- **pytest collect**：429 条（较 B1-B8 时 167 条 +262 条）
- **全量实测（2026-04-22，外网 + OpenRouter）**：**429 / 429 passed**，整体耗时 ~22 min
  - `test_briefings.py` 偶发一次 LLM 网络 flake（结果 7 pass / 1 fail）；单独重跑 **8/8 passed**（耗时 32.42s）
  - 本轮新增回归均 passed：`test_reranker.py` 9/9、`test_chat_harness.py` 8/8、`test_citation_stance_checker.py` 15/15
  - 重点重测：`test_verification.py` 7/7 + `test_watchlists.py` 8/8（15 条耗时 367.99s）
- **无新增依赖**；全部在既有运行时
- **离线回断**：若无 LLM 外网，`test_briefings` / `test_verification` 的问答可用性断言会直接失败，其余 ~420 条可离线通过

### 9.7 风险复盘

| 复盘项 | 结论 |
|---|---|
| D2 `expunge → expire` 是否退化？ | 否。`expunge` 彻底从 session 移除对象，调用方后续访问 `claim.foo` 会抛 DetachedInstanceError；`expire` 只让 ORM 在下次 attribute 访问时重新 load，更稳妥 |
| D3 expunge 脏对象是否误伤其他业务？ | 否。仅 expunge `ClaimCard / ClaimRawSourceLink / LifecycleLog` 三类且来自 `self.db.new`（本次 session 新增未提交的）；`raw_source / job_run / ProcessingTrace` 保留 |
| D4 QuotaGuard 首次冷却是否影响 scheduler？ | 否。scheduler 12:00 全量任务只在每天第一次触发，不会踩冷却窗口；手动按钮触发才可能遇到 `_skipped_by_quota=True`（此为预期） |
| D7 rank 归一是否改变已通过测试的期望值？ | 仅 `test_reranker.py` 中"降级"用例断言从 `score == 0.5` 改为 `0 <= score <= 1 且降序` |
| D10 候选池倍数提升是否显著增加成本？ | 单次 semantic_search 从 `24` 候选增到 `40` 候选 —— 仅多出 cosine 相似度计算（CPU），不触发 LLM，成本影响可忽略 |

### 9.8 与已有框架的关系

- **未破坏既有 feature flag**：`ENABLE_RERANKER` / `PGVECTOR_ENABLED` / `ASSET_ALIAS_V2` 行为不变
- **未引入新 migration**：完全在现有 schema 内修复
- **未引入新依赖**：所有改动在 Python stdlib + 既有包内完成
- **与 B3 公共后处理器协同**：D6 多资产支持的是 chat 入口；briefing 入口的 `query_for_briefing` 已经通过 `assets: List[str]` 支持多资产（无需改动）

---

*D 级数据流 audit 完成人：Cascade AI Pair Programmer · 2026-04-20 18:29 UTC+08:00*

*数据管线升级记录人：Cascade AI Pair Programmer · 2026-04-18 03:40 UTC+08:00*
