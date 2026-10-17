# Research OS V2 — 升级设计文档

> **历史设计参考**：记录 V2 方案，不是当前范围或未完成任务清单。

## 〇、建议逐条评估

> 以下对收到的 21 条建议逐一评估，标注采纳程度、理由与落地方式。

### ✅ 完全采纳（高价值、可直接落地）

| # | 建议 | 评估 |
|---|------|------|
| 1 | 保留 V1 主干 | 完全同意。当前 21 张表、5 MCP、12 Skill、5 Workflow 结构扎实，在其上增量升级 |
| 2 | 产品重新定义为"Research OS for Decision" | 准确的定位升级。后续所有设计以此为锚 |
| 7 | EventCard 状态机（truth_status + outcome_status） | **本轮最高优先级**。当前 EventCard 仅 38 行，无真假/兑现追踪，是最大短板 |
| 8 | SpeakerProfile V2（研究员画像） | 高价值。当前仅 name/trust_weight/domain_tags，缺乏多维画像 |
| 10 | 新增 Committee Orchestrator | 高价值。当前委员会无条件全员出动，缺少动态编排层 |
| 11 | 证据供给分层（共享底稿 + 角色附件） | 高价值。当前 _gather_context 对所有角色喂同样的 context，token 浪费 |
| 13 | Decision Replay（决策回放） | 核心增量能力。需要 DecisionSnapshot 作为基础设施 |
| 16 | 人工 Override + 留痕 | 必要且实现简单。当前缺少用户修正能力 |
| 17 | OpenRouter 联网边界 | 已正确处理。系统通过 MCP 显式获取数据，不依赖模型联网 |
| 21 | 十条核心原则 | 作为设计约束全部采纳 |

### ⚠️ 部分采纳（方向正确，需简化或分阶段）

| # | 建议 | 评估 |
|---|------|------|
| 3 | 四层架构 | 方向正确，但是**逻辑分层**而非物理重构。当前代码已隐式对应这四层，无需重组目录结构。作为文档概念模型采纳，不做代码级强制拆分 |
| 4 | 公共数据定位 | 原则采纳。当前系统已以私域为核心，公共数据为辅。无需大改 |
| 5 | 数据缓存策略（热/温/冷） | **热层**和 **DecisionPacket** 高价值，立即实施。但 1m/5m OHLCV 全量缓存对个人系统存储压力过大（BTC 1m 一天 1440 条，一年 52 万条），降级为按需 + 快照模式 |
| 6 | yfinance/efinance/AKShare 角色 | yfinance 已用且定位清晰。efinance/AKShare 稳定性堪忧（接口频繁变动、无官方维护承诺），**暂不引入**，未来按需评估 |
| 9 | 动态七席委员会 | **席位 1-4 已有**。席位 5（Truth/Audit）和席位 6（PnL/Execution）有实际价值，但应设计为**可选激活**而非默认参与。席位 7（Actor/Motive）过于专化，投入产出比低，**降为 Phase 3 观察项** |
| 12 | ActionEconomics | 方向正确，但完整的滑点/执行成本需要实时订单簿数据（系统无此能力）。**简化版**聚焦 funding cost、波动率状态、等待价值窗口、方向判断，不做精确执行成本估算 |
| 14 | Opportunity Scout / Daily Radar | 已有 auto_briefing_job 作为雏形。升级为结构化的每日雷达输出，**Phase 2** 实施 |
| 15 | 评估与校准体系 | 需要足够数据积累才有意义。**Phase 3** 实施，先在 Phase 1 埋好数据采集点 |
| 19 | 三阶段路线图 | 采纳分阶段思路，但重新排列具体优先级 |

### ❌ 不采纳或大幅调整

| # | 建议 | 评估 |
|---|------|------|
| 6 (部分) | 引入 efinance/AKShare | 这两个库接口不稳定、文档差、Breaking change 频繁。当前 yfinance + Binance + FRED 已满足核心需求。等这些库成熟后再评估 |
| 9 (部分) | 席位 7（Actor & Motive） | 人物动机分析需要深度人物库建设，ROI 极低。当前 SpeakerProfile V2 + 来源画像已可覆盖大部分需求 |
| 5 (部分) | 1m/5m 全量 OHLCV 缓存 | 个人系统存储和采集频率均不支持。保留 MarketSnapshot（15 分钟快照）+ MarketStateCard（日度技术状态）已足够 |

---

## 一、系统重新定义

**Research OS for Decision** — 以私域知识为核心、以公共证据为支撑、以动态委员会为推理中枢、以学习回放与执行经济性为闭环的个人研究与决策操作系统。

### 核心原则

1. **私域知识是核心资产**，公共数据是校准器，不可反客为主
2. **公共数据服务于证据校准与决策复现**，不做无边界囤积
3. **模型动态激活**，不无条件全员出动，成本控制核心是少喂无效信息
4. **社交源是精选研究员池**，不是噪音流量池
5. **真假消息与兑现状态独立建模**，区分"说过/是真的/实现了/市场按预期反应了"
6. **决策不只判方向，还判"现在做值不值"**
7. **Decision Replay 优先于传统回测**
8. **所有高确信输出必须附带反证、风险、失效条件与证据链**

---

## 二、V2 四层逻辑架构

```
┌─────────────────────────────────────────────────────────────────┐
│  第四层：发现与学习层（Discovery / Replay Layer）               │
│  Opportunity Scout · Daily Radar · Decision Replay · 后验校准   │
├─────────────────────────────────────────────────────────────────┤
│  第三层：决策层（Decision Layer）                                │
│  Committee Orchestrator · 动态席位 · SignalScorer               │
│  最终裁决 · ActionEconomics · 反证与失效条件                    │
├─────────────────────────────────────────────────────────────────┤
│  第二层：证据层（Evidence Layer）                                │
│  MarketStateCard · FlowSignalCard · EventCard V2                │
│  MacroSnapshot · SpeakerProfile V2 · 数据覆盖摘要              │
│  DecisionSnapshot（时点证据快照）                                │
├─────────────────────────────────────────────────────────────────┤
│  第一层：记忆层（Memory Layer）                                  │
│  ClaimCard · SpeakerProfile · EvidenceSpan · RawSource          │
│  语义索引（pgvector）· 召回缓存（Redis）· 生命周期管理         │
└─────────────────────────────────────────────────────────────────┘
```

**映射到现有代码**（无需物理目录重组）：

| 逻辑层 | 现有对应 | V2 新增 |
|--------|----------|---------|
| 记忆层 | ClaimCard, SpeakerProfile, EvidenceSpan, RawSource, LifecycleLog, Redis Cache | SpeakerProfile V2 字段 |
| 证据层 | EventCard, MarketStateCard, MarketSnapshot, FlowSignalCard, SourceHealthLog | EventCard V2 字段, DecisionSnapshot 新表, UserOverride 新表 |
| 决策层 | CommitteeWorkflow, SignalScorer, FinalAdjudication, BriefingCard | CommitteeOrchestrator 新服务, ActionEconomics 新模块, 席位 5/6 |
| 发现层 | scheduler.auto_briefing_job | OpportunityScout 新服务, DecisionReplay 新服务 |

---

## 三、数据对象设计

### 3.1 EventCard V2（在现有表上新增字段）

现有 EventCard 有 38 行 13 个字段。V2 新增以下字段：

```python
# ── V2 新增：真假状态轴 ──
truth_status: str          # unverified | partially_verified | confirmed | denied | retracted
truth_updated_at: datetime # 真假状态最后更新时间
truth_source: str          # 谁确认/否认的（来源说明）

# ── V2 新增：兑现状态轴 ──
outcome_status: str        # pending | no_follow_through | partially_materialized | fully_materialized | opposite_outcome
outcome_observed_at: datetime
outcome_notes: str         # 兑现情况说明

# ── V2 新增：信息质量与追踪 ──
source_tier: str           # tier1_official | tier2_major_media | tier3_kol | tier4_rumor
sentiment: float           # -1.0 ~ 1.0
novelty: float             # 0.0 ~ 1.0 新颖度
trust_weight: float        # 综合可信权重
claim_text: str            # 核心断言文本
invalidation_window: datetime  # 预期失效窗口
followup_event_ids: list   # 后续关联事件 ID

# ── V2 新增：用户修正 ──
user_override_truth: str   # 用户手动修正的真假状态
user_override_outcome: str # 用户手动修正的兑现状态
user_override_notes: str   # 修正原因
```

### 3.2 SpeakerProfile V2（在现有表上新增字段）

现有 SpeakerProfile 有 26 行 12 个字段。V2 新增：

```python
# ── V2 新增：多维画像 ──
perspective_value: float       # 角度价值 0~1（是否提供独特视角）
emotional_intensity: float     # 情绪强度 0~1（煽动性程度）
logic_robustness: float        # 逻辑稳健性 0~1
applicable_domains: list       # 擅长领域细分 ["宏观政策", "链上分析", "交易结构"]
time_half_life: str            # 观点适用时间跨度 immediate|short|medium|long
contrarian_value: float        # 反向指标价值 0~1

# ── V2 新增：兑现追踪 ──
hit_count: int                 # 命中次数
miss_count: int                # 失误次数
partial_hit_count: int         # 部分命中
avg_deviation_pct: float       # 平均偏差百分比
best_asset_classes: list       # 历史表现最佳的资产类别
worst_asset_classes: list      # 历史表现最差的资产类别

# ── V2 新增：使用建议 ──
usage_recommendation: str      # 使用建议（LLM 生成的简要说明）
# "适合作为宏观方向启发源，逻辑链不够稳健，不宜单独作为高置信证据"
last_calibrated_at: datetime   # 最后校准时间
```

### 3.3 DecisionSnapshot（全新表）

**核心目的**：冻结某次分析/委员会/裁决时刻系统"当时知道什么"，是 Decision Replay 的基础。

```python
class DecisionSnapshot(Base, ULIDPrimaryKey, TimestampMixin):
    __tablename__ = "decision_snapshots"

    # 关联对象
    trigger_type: str       # debate | briefing | chat | scout
    trigger_id: str         # 关联的 debate_id / briefing_id / session_id
    asset: str
    question: str

    # 时点快照
    snapshot_at: datetime   # 快照时间戳

    # 记忆层快照
    claims_snapshot: list   # 当时送入的私域观点（含 ID、摘要、立场、置信度、来源可信度）
    claims_count: int
    data_coverage: dict     # 数据覆盖摘要

    # 证据层快照
    market_snapshot: dict   # 当时的行情状态
    flow_snapshot: dict     # 当时的资金流信号
    events_snapshot: list   # 当时的事件列表（含 truth_status）
    macro_snapshot: dict    # 当时的宏观数据

    # 决策层快照
    signal_scorecard: dict  # SignalScorer 输出
    activated_seats: list   # 激活了哪些席位
    evidence_budget: dict   # token 预算分配

    # 输出快照
    final_direction: str
    final_conviction: float
    final_action: str
    synthesis_summary: str

    # 后验（Decision Replay 时填入）
    outcome_direction: str      # 事后的实际方向
    outcome_observed_at: datetime
    replay_notes: str           # 回放分析笔记
    replay_status: str          # pending | replayed | skipped
```

### 3.4 ActionEconomics（嵌入裁决输出，非独立表）

作为 FinalAdjudication 的扩展字段：

```python
# FinalAdjudication 新增字段
action_economics: dict  # JSON 存储
# {
#   "funding_cost_annualized": 0.15,     # 年化资金费率成本
#   "volatility_regime": "elevated",      # 波动率状态
#   "waiting_value_window": "2-3 days",   # 等待价值窗口
#   "hold_risk_description": "...",       # 持有风险描述
#   "action_timing": "wait_for_pullback", # 时机建议
#   "action_preference": "scale_in",      # enter_now|scale_in|hold_and_wait|reduce_now|exit_after_condition|avoid_for_now
#   "reasoning": "..."
# }
```

### 3.5 UserOverride（全新表）

```python
class UserOverride(Base, ULIDPrimaryKey, TimestampMixin):
    __tablename__ = "user_overrides"

    target_type: str        # speaker | event | claim | adjudication
    target_id: str          # 被修正对象的 ID
    field_name: str         # 被修正的字段名
    old_value: str          # 修正前的值（JSON 序列化）
    new_value: str          # 修正后的值
    reason: str             # 修正原因
    override_type: str      # correction | annotation | adoption_feedback
    # adoption_feedback: 用户标记"采纳/不采纳"及原因
```

---

## 四、Committee Orchestrator 设计

### 4.1 三层编排

```
用户输入
    ↓
第一层：任务模式识别（Task Mode）
    ↓
第二层：席位激活（Seat Activation）
    ↓
第三层：预算与证据约束（Budget & Evidence Gate）
    ↓
执行
```

### 4.2 任务模式（扩展现有 Intent）

| 模式 | 说明 | 触发条件 |
|------|------|----------|
| chat | 一般问答 | 默认 |
| research | 研究探索 | 用户提问含分析类关键词 |
| decision | 正式决策 | 用户明确要求"委员会"/"辩论"/"正式评估" |
| replay | 决策回放 | 用户引用历史决策 |
| scout | 主动发现 | 定时任务或用户请求"今日雷达" |
| audit | 真伪审计 | 用户质疑消息真假 |

### 4.3 席位激活规则

| 模式 | 必选席位 | 可选席位 | 预计 Token |
|------|----------|----------|-----------|
| chat | 无委员会 | — | ~2K |
| research | ① Baseline | ② Probability, ④ Structure | ~15K |
| decision | ① ② ③ ④ | ⑤ Truth/Audit, ⑥ PnL | ~40K~60K |
| replay | ② Probability | ⑤ Truth/Audit | ~10K |
| scout | 无委员会（规则引擎） | — | ~1K |
| audit | ⑤ Truth/Audit | ① Baseline | ~8K |

**席位定义**：

| # | 角色 | 模型 | 状态 |
|---|------|------|------|
| ① | Baseline Analyst | Claude Opus | V1 已有 |
| ② | Probability Analyst | Gemini Pro | V1 已有 |
| ③ | Risk Sentinel | GPT-5.4 | V1 已有 |
| ④ | Structure Analyst | GLM-5 | V1 已有 |
| ⑤ | Truth / Evidence Auditor | DeepSeek V3.2 | **V2 新增** |
| ⑥ | PnL / Execution Advisor | Gemini Flash | **V2 新增** |

**席位 ⑤ Truth/Audit 职责**：
- 检查事件 truth_status，标注未证实信息
- 检查来源 tier，标注低 tier 证据
- 检查证据冲突（同一事件不同来源说法矛盾）
- 输出：evidence_quality_score, unverified_warnings, conflict_flags

**席位 ⑥ PnL/Execution 职责**：
- 评估当前 funding rate / carry cost
- 评估波动率状态下的等待价值
- 评估方向判断的执行时机
- 输出：action_economics dict（见 3.4）

### 4.4 预算约束门

在激活席位前检查：

```python
def should_activate(mode, seat, context):
    # 1. Token 预算：单次总预算上限
    if estimated_total_tokens > MAX_BUDGET[mode]:
        return False  # 降级到更少席位

    # 2. 数据覆盖完整度
    if context.data_coverage_score < 0.3 and seat in [PROBABILITY, STRUCTURE]:
        return False  # 数据不足，量化分析无意义

    # 3. 资产重要性
    if asset not in watchlist and mode != "decision":
        return False  # 非关注资产，不启动高成本分析

    return True
```

### 4.5 证据供给分层

#### A. 共享共识包（所有席位共用，约 60%~70% token）

```python
shared_context = {
    "timestamp": "2026-04-08T17:00:00+08:00",
    "asset": "BTC",
    "question": "...",

    # 市场状态摘要（500 token 以内）
    "market_state": {
        "price": 84500, "change_24h": "-2.3%",
        "rsi_14": 42, "trend": "sideways",
        "volatility": "elevated", "volume_vs_avg": 1.2
    },

    # 宏观状态（200 token 以内）
    "macro_state": {
        "fed_rate": "5.25-5.50%", "dxy": 104.2,
        "us10y": "4.35%", "next_fomc": "2026-05-07"
    },

    # 高重要性事件（Top-5，500 token 以内）
    "key_events": [...],

    # 私域观点摘要（Top-8，800 token 以内）
    "claims_summary": [...],

    # 数据覆盖摘要
    "data_coverage": {...},

    # 主要矛盾点
    "key_tensions": ["私域多数看多 vs 技术面弱势", ...],

    # 强制反证包（所有席位必须看到）
    "mandatory_counter": {
        "counter_evidence": [...],   # 至少 2 条反向证据
        "failure_conditions": [...], # 至少 1 个失效条件
        "evidence_gaps": [...]       # 至少 1 个证据不足说明
    }
}
```

#### B. 角色附件（按席位追加，约 30%~40% token）

| 席位 | 附件内容 |
|------|----------|
| ② Probability | 历史同类事件样本、条件概率分布参考 |
| ③ Risk | 极端波动历史、尾部事件案例、当前杠杆水平 |
| ④ Structure | funding rate / OI / netflow / taker ratio / stablecoin supply |
| ⑤ Audit | 事件 truth_status 明细、来源冲突列表、低 tier 证据标注 |
| ⑥ PnL | 当前 funding 年化、波动率 percentile、近期 carry 成本 |

---

## 五、Decision Replay 方案

### 5.1 数据基础

每次委员会/Briefing 生成时，自动创建 DecisionSnapshot，冻结：
- 送入的 claims、events、market、flow 数据
- SignalScorer 输出
- 激活的席位列表
- 最终裁决结果

### 5.2 回放流程

```
1. 用户选择历史决策 → 加载 DecisionSnapshot
2. 展示"当时系统知道什么"
3. 对比"后来实际发生了什么"
   - 事件 truth_status 变化
   - 事件 outcome_status 变化
   - 价格走势（从 MarketSnapshot 重建）
4. 分析偏差来源
   - 哪些证据误导了系统
   - 哪些信号后来被证明更重要
   - 哪个席位的判断最接近实际结果
5. 生成学习笔记（可选 LLM 辅助）
```

### 5.3 后验标注

DecisionSnapshot 的后验字段由以下方式填入：
- **自动**：LifecycleWorkflow 扫描时，检查相关事件的 outcome_status 变化
- **手动**：用户通过 Override API 标注 outcome_direction 和 replay_notes

---

## 六、Opportunity Scout 方案

### 6.1 定位

不做自动买卖推荐。每日输出三类信息：
1. **异常资产**：波动率突变、量价背离、结构信号异常
2. **重点事件**：truth_status 变化、高重要性新事件、兑现窗口临近
3. **学习案例**：近期有后验结果的历史决策，适合复盘

### 6.2 实现方式

扩展现有 scheduler，新增 `daily_radar_job`：

```python
async def daily_radar_job():
    """每日 08:30 CST 生成雷达报告"""
    # 1. 扫描 MarketSnapshot：找到 24h 波动 > 2σ 的资产
    # 2. 扫描 FlowSignalCard：找到 is_trend_change=True 的信号
    # 3. 扫描 EventCard：找到 truth_status 变化 / importance >= 0.7 的新事件
    # 4. 扫描 DecisionSnapshot：找到 replay_status=pending 且已有后验数据的决策
    # 5. 汇总输出 DailyRadar JSON，存入 briefing_cards (topic="daily_radar")
```

---

## 七、评估指标体系（Phase 3 实施，Phase 1 埋点）

### 7.1 Phase 1 埋点

在 V2 数据对象中预埋以下可计算字段：
- EventCard: truth_status / outcome_status → 事件层评估基础
- SpeakerProfile: hit_count / miss_count → 来源层评估基础
- DecisionSnapshot: outcome_direction → 委员会层评估基础
- UserOverride: override_type=adoption_feedback → 用户层评估基础

### 7.2 Phase 3 计算

| 维度 | 指标 | 数据来源 |
|------|------|----------|
| 来源 | 观点兑现率 | SpeakerProfile.hit_count / total_claims |
| 来源 | 失效率 | miss_count / total_claims |
| 事件 | 未证实消息误导率 | truth_status=denied 的事件被用于决策的次数 |
| 事件 | 兑现滞后分布 | outcome_observed_at - event_timestamp |
| 委员会 | 各席位偏差 | 对比 DebateRound.direction vs DecisionSnapshot.outcome_direction |
| 委员会 | 高 conviction 命中率 | conviction > 0.7 的裁决 vs 实际方向 |
| 用户 | 采纳率 | UserOverride.override_type=adoption_feedback 统计 |

---

## 八、分阶段实施路线图

### Phase 1：证据层做扎实（当前阶段）

**目标**：从"能跑"变成"可信"

| 序号 | 任务 | 涉及文件 | 工作量 |
|------|------|----------|--------|
| P1.1 | EventCard V2：新增 truth_status / outcome_status 等字段 | event_card.py | 小 |
| P1.2 | SpeakerProfile V2：新增多维画像字段 | speaker_profile.py | 小 |
| P1.3 | DecisionSnapshot 新表 | decision_snapshot.py (新) | 中 |
| P1.4 | UserOverride 新表 | user_override.py (新) | 小 |
| P1.5 | FinalAdjudication 新增 action_economics 字段 | debate.py | 小 |
| P1.6 | schemas/__init__.py 注册新模型 | __init__.py | 小 |
| P1.7 | CommitteeOrchestrator 服务 | committee_orchestrator.py (新) | 大 |
| P1.8 | 证据供给分层（共享包 + 角色附件） | committee_workflow.py 重构 | 大 |
| P1.9 | 人工 Override API | api/v1/overrides.py (新) | 中 |
| P1.10 | 委员会调用 DecisionSnapshot 自动保存 | committee_workflow.py | 中 |

### Phase 2：决策层做深

**目标**：从"分析系统"变成"决策辅助系统"

| 序号 | 任务 |
|------|------|
| P2.1 | 席位 ⑤ Truth/Audit：system prompt + skill |
| P2.2 | 席位 ⑥ PnL/Execution：system prompt + skill |
| P2.3 | ActionEconomics 完整实现（LLM 生成） |
| P2.4 | Decision Replay 基础版（后验标注 + 展示） |
| P2.5 | Daily Radar / Opportunity Scout |
| P2.6 | 意图识别扩展（新增 replay / scout / audit 模式） |
| P2.7 | 前端 Replay 页面 |

### Phase 3：发现与校准

**目标**：从"被动工具"变成"研究操作系统"

| 序号 | 任务 |
|------|------|
| P3.1 | 来源画像后验自动更新 |
| P3.2 | 席位历史表现追踪 |
| P3.3 | 事件兑现自动检测 |
| P3.4 | 评估仪表盘（前端） |
| P3.5 | 席位 ⑦ Actor/Motive（如验证有需求） |

---

## 九、风险清单与边界说明

### 9.1 技术风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| 席位增加导致 token 成本膨胀 | 高 | CommitteeOrchestrator 严格控制激活条件 + token 预算上限 |
| DecisionSnapshot 存储膨胀 | 中 | JSON 字段存储摘要而非完整数据，定期归档 |
| EventCard truth_status 需要人工维护 | 中 | 提供便捷 Override UI，未来探索自动检测 |
| efinance/AKShare 不稳定 | 低 | 暂不引入，保持现有数据源 |

### 9.2 产品边界

1. 本系统是**决策辅助**，不是自动交易系统
2. 不连接真实账户，不执行真实交易
3. ActionEconomics 提供**定性评估**，不提供精确执行成本
4. 所有输出**不构成投资建议**

### 9.3 对外展示边界

1. 展示架构、流程、设计思路、界面截图
2. **不**提供在线功能入口
3. **不**提供第三方原始数据下载
4. 页面包含免责声明：仅作学习研究展示、不构成投资建议、部分数据受第三方条款约束

---

## 十、数据分层缓存策略

### 热层（必须缓存，系统核心运行依赖）

| 数据 | 当前实现 | V2 调整 |
|------|----------|---------|
| Watchlist 资产行情快照 | MarketSnapshot 每 15 分钟 | 保持不变 |
| 技术状态卡片 | MarketStateCard 日度 | 保持不变 |
| 资金流信号 | FlowSignalCard | 保持不变 |
| 事件卡片 | EventCard | 升级为 V2 含状态追踪 |
| 私域观点 | ClaimCard + pgvector | 保持不变 |
| 决策快照 | 无 | **新增 DecisionSnapshot** |

### 温层（高价值但不是每次都用）

| 数据 | 实现 |
|------|------|
| 宏观时间序列 | FRED 数据通过 KnowledgeEnrichment 存入 RawSource |
| 历史事件链 | EventCard.followup_event_ids 追踪 |
| 来源画像历史 | SpeakerProfile V2 字段 |
| 社交媒体历史 | RawSource (source_type=tweet/weibo) |

### 冷层（按需获取，不主动存储）

| 数据 | 策略 |
|------|------|
| 非 Watchlist 资产行情 | 仅在用户查询时临时获取 |
| 深度历史 K 线 | 不缓存，yfinance 按需拉取最近 60 天 |
| 长尾社交媒体内容 | 不存储，仅在特定研究任务中拉取 |

---

## 十一、Ingestion Pipeline 优化记录（2026-04-16 更新）

### ✅ 已完成项

| # | 内容 | 涉及文件 | 完成日期 |
|---|------|----------|----------|
| I-1 | 用户标注接口：自由填写 + datalist 提示（非固定下拉框），LLM 自行解读 | upload.py, KnowledgeBase.tsx | 2026-04-15 |
| I-2 | 知识库自检/Lint：过期/矛盾/低质量/孤立/盲区/立场偏向 + 健康度评分 | knowledge_lint.py, knowledge.py | 2026-04-15 |
| I-3 | Speaker 校准字段已在 V2 schema + Lint 可检测待校准 speaker | speaker_profile.py, knowledge_lint.py | 2026-04-15 |
| I-4 | 端到端评估能力：3 个 API + evaluation_report 服务，支持 Markdown 一键复制 | evaluation_report.py, knowledge.py | 2026-04-15 |
| I-5 | 句子边界感知 Chunking：永不中断句子 + 400 字重叠 | ingestion_workflow.py `_split_chunks` | 2026-04-16 |
| I-6 | 来源级增量更新：SHA-256 content_hash 去重，重复文件自动跳过 | upload.py, raw_source.py | 2026-04-16 |
| I-7 | 聊天记录时间自动识别：regex 检测 + 自动设置 published_at | file_parser.py, ingestion_workflow.py | 2026-04-16 |
| I-8 | test_inbox/ 自动化测试入口：scan + process API，图片分组合并，去重 | knowledge.py, test_inbox/ | 2026-04-16 |
| I-9 | 评估报告原始文本完整内容：Markdown 10000 字 + JSON 全文 | evaluation_report.py | 2026-04-16 |
| I-10 | 幻觉模型选型验证：确认 Gemini Flash 系列幻觉率最优，DeepSeek 仅为兜底 | — (设计决策) | 2026-04-16 |

### 🔲 待办项

| # | 内容 | 优先级 | 备注 |
|---|------|--------|------|
| I-T1 | **信任度校准闭环**：基于 speaker 历史 hit_count/miss_count 自动更新 trust_weight | 高 | 对应 V2_DESIGN Phase 3 P3.1 来源画像后验自动更新 |
| I-T2 | **过期归档机制**：过期 claim 自动归档策略设计 | 低 | 需慎重——过期≠错误，归档可能丢失历史视角 |
| I-T3 | **管线评估报告实际测试**：用 test_inbox/ 投入真实素材，完整走一遍提取→评审流程 | 高 | 是验证以上所有优化成果的关键步骤 |

### 设计决策备忘

- **Speaker 验证**：不设独立验证环节，用户填写优先 → LLM 推断 fallback → Lint 检测缺失
- **幻觉控制**：grounded extraction（有原文对照）+ low temperature (0.1) + full_quote 引用 + 评估报告人工审阅
- **信任度「初始阶段」**：user_trust_level → claim.trust_weight 映射在入库时固化，未来 hit/miss 反馈校准
