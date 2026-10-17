# 个人 Agent 协作与知识管理系统

> **愿景参考**：描述更广泛的 Agent 协作方向，不是当前运行架构。

> 最后更新：2026-04-13
> 整理来源：Windsurf 多轮对话

---

## 目录

- [一、系统愿景](#一系统愿景)
- [二、工具链与分工](#二工具链与分工)
- [三、个人知识管理系统：LLM Wiki 架构](#三个人知识管理系统llm-wiki-架构)
- [四、自动化资讯雷达](#四自动化资讯雷达)
- [五、语音备忘与日常记录系统](#五语音备忘与日常记录系统)
- [六、成本规划](#六成本规划)
- [七、落地优先级](#七落地优先级)

---

## 一、系统愿景

构建一个以 **Obsidian** 为知识浏览中心、以 **OpenClaw** 为信息入口与自动化引擎的个人 Agent 系统，实现：

1. **知识沉淀**：日常接收的视频、文章、语音想法自动进入结构化知识库
2. **资讯雷达**：每天自动获取科技、金融、生活、科学等多领域前沿信息，融合整理后统一推送
3. **语音锚点**：随时随地录制语音备忘，自动转文字、分类、提取待办
4. **多维记录**：日常记录、金融财务、思维模式等分层归档
5. **多端访问**：电脑（Obsidian/LibreChat）+ 手机（微信/Chatbox）无缝切换

---

## 二、工具链与分工

### 2.1 工具总览

```text
┌─ 编码（Windsurf Pro 额度）──────────────────────────────────┐
│ Windsurf IDE + Claude Opus 4.6                                │
│ → 主力编码、调试、测试                                          │
│ → 消耗 Windsurf 会员额度，不额外花钱                            │
│ → 封闭生态，无法被外部工具调用                                   │
└───────────────────────────────────────────────────────────────┘

┌─ 手机↔电脑桥梁（OpenRouter token）───────────────────────────┐
│ OpenClaw（7K+ stars，1612 贡献者，89 releases）                 │
│ → 微信接入（腾讯官方插件 @tencent-weixin/openclaw-weixin）      │
│ → 语音备忘录处理（Whisper/Deepgram STT）                        │
│ → 自动化 Cron 任务（资讯雷达、Wiki 日更）                        │
│ → 本地文件操作（写入 RAW 层）                                    │
└───────────────────────────────────────────────────────────────┘

┌─ 电脑端多模型对话（OpenRouter token）────────────────────────┐
│ LibreChat（25K+ stars，MIT 协议，自托管 Docker）                │
│ → 讨论需求、编写文档、业务分析                                   │
│ → 手机浏览器远程访问                                            │
└───────────────────────────────────────────────────────────────┘

┌─ 知识库浏览（免费）──────────────────────────────────────────┐
│ Obsidian                                                       │
│ → 浏览 LLM Wiki、查看知识图谱                                   │
│ → Web Clipper 浏览器插件（网页剪藏）                             │
│ → Graph View 查看关联关系                                       │
└───────────────────────────────────────────────────────────────┘
```

### 2.2 关键约束

- **Windsurf 是封闭生态**：Pro 会员额度只能在 IDE 内使用，无 API/CLI/远程接入
- **离开 Windsurf 的操作均消耗 OpenRouter token**
- **省钱策略**：Cron 任务和日常对话用 Sonnet 4.5 / Haiku，只在 Windsurf 中用 Opus

### 2.3 工具评估

| 工具 | 评分 | 推荐度 | 说明 |
|------|------|--------|------|
| **LibreChat** | ⭐⭐⭐⭐⭐ | 强烈推荐 | 25K+ stars，MIT，企业级用户，OpenRouter 原生支持 |
| **Chatbox** | ⭐⭐⭐⭐ | 推荐 | 23K+ stars，全平台客户端，轻量快捷，$9.9 终身授权 |
| **OpenClaw** | ⭐⭐⭐⭐ | 推荐 | 7K+ stars，1612 贡献者，微信官方插件，多渠道 |
| **Open Cowork** | ⭐⭐⭐ | 谨慎 | ~2K stars，Windows 上 bug 较多，功能想法好但不成熟 |
| **Claude Squad** | ⭐⭐ | 不适用 | 需要 WSL + tmux，且消耗 API token 非 Windsurf 额度 |

---

## 三、个人知识管理系统：LLM Wiki 架构

### 3.1 设计灵感

基于 **Andrej Karpathy**（OpenAI 创始团队、Tesla AI 前负责人）于 2026-04-11 发布的 **LLM Wiki** 模式。

核心理念：**不是 RAG 每次从头搜索，而是 LLM 将知识"编译"一次，持续维护一个结构化 Wiki。**

- RAW 层不可变，AI 只读——是你的真相源
- Wiki 层由 AI 写、你浏览——已编译好的知识
- 好的回答存回 Wiki——你的探索也会沉淀
- 定期 Lint——让 AI 检查矛盾、过时、孤立页面

### 3.2 三层架构

```text
RAW 层（原始数据，不可变，AI 只读）
        ↓ LLM 处理
Wiki 层（AI 生成与维护，你浏览）
        ↓ 消费
使用层（Obsidian 浏览、微信查询）
```

### 3.3 RAW 层目录结构

```text
knowledge-base/
├── raw/                              ← 原始素材，AI 只读不写
│   ├── daily/                        ← 日常记录
│   │   ├── voice/                    ← 语音转文字
│   │   ├── journal/                  ← 情感随笔
│   │   └── memo/                     ← 备忘与详细说明
│   │       ├── dev/                  ← 开发相关
│   │       ├── work/                 ← 工作安排
│   │       └── thinking/             ← 想法
│   ├── finance/                      ← 金融财务
│   │   ├── personal/                 ← 个人财务状况
│   │   └── market/                   ← 核心金融/市场信息
│   ├── thinking/                     ← 思维模式与私域思考
│   ├── feeds/                        ← 资讯订阅（自动抓取）
│   │   ├── YYYY-MM-DD/              ← 按日归档
│   │   ├── seen_hashes.json          ← 去重索引
│   │   └── last_briefing.md          ← 上次晨报（防重复）
│   ├── videos/                       ← 视频文字稿
│   ├── articles/                     ← 文章/网页剪藏
│   └── dev/                          ← 开发相关笔记/需求文档
```

### 3.4 Wiki 层目录结构

```text
├── wiki/                             ← AI 写，你读
│   ├── index.md                      ← 全局索引（AI 维护）
│   ├── log.md                        ← 操作日志（AI 追加）
│   ├── entities/                     ← 实体页面（人物/公司/项目）
│   ├── topics/                       ← 主题页面（金融/技术/健康）
│   ├── syntheses/                    ← 综合分析页面
│   ├── feeds/                        ← 资讯摘要与周报
│   └── dev/                          ← 开发项目知识编译
├── AGENTS.md                         ← Schema：指导 AI 如何维护 Wiki
└── .obsidian/                        ← Obsidian 配置
```

### 3.5 关键操作

| 操作 | 说明 |
|------|------|
| **Ingest（摄入）** | 新素材进入 raw/ → LLM 读取 → 写摘要 → 更新相关 wiki 页面 → 更新 index.md |
| **Query（查询）** | 向 AI 提问 → AI 搜索 wiki/ → 综合回答 → 好的回答存回 wiki/ |
| **Lint（健康检查）** | 定期让 AI 检查 wiki：矛盾、过时、孤立页面、缺失交叉引用 |

### 3.6 痛点解决：外部信息丝滑入库

**之前**：看到好视频 → 分享给豆包 → 等解析 → 复制文字稿 → 粘贴 → 信息丢失

**之后**：

```text
看到好视频 → 微信发链接给 OpenClaw → OpenClaw 自动：
  1. 调用 LLM 解析视频内容（或调用外部工具提取字幕）
  2. 生成文字稿存入 raw/videos/2026-04-12_视频标题.md
  3. LLM 读取文字稿，提取要点
  4. 更新 wiki/ 中相关的主题页面和实体页面
  5. 更新 index.md

你在 Obsidian 里刷新就能看到新内容。
```

---

## 四、自动化资讯雷达

### 4.1 设计目标

每天早上 10:00 收到**一份融合简报**，覆盖多领域前沿信息，内容经过串联整理，易读但严格保持原意。

### 4.2 关注范围（五大板块）

#### ① 科技与 AI

- **机构官方**：Anthropic、OpenAI、NVIDIA 等（官网博客 + GitHub + 社交媒体）
- **人物动态**：Elon Musk、Andrej Karpathy 等（通过 RSSHub 桥接 X/Twitter）
- **GitHub Trending**：每日最热/上涨最快的开源项目（All Languages、Python、TypeScript）
- **行业社区**：Hacker News、InfoQ AI/ML 等

> **注意**：关注的人和机构不止上述列表，后续持续扩展，此处仅为初始种子。

#### ② 全球热点 Top 10（跨领域）

类似"世界热搜榜"，覆盖科技、金融、政治、社会、生活等各领域：

- **综合热点源**：Google Trends（全球）、Twitter/X Trending、Reddit Popular
- **新闻聚合**：Reuters Top News、BBC World、AP News
- **中文视角**：微博热搜、知乎热榜（通过 RSSHub）

每日从上述源中提取**全球前十大热点事件**，不限领域。

#### ③ 顶级学术期刊

- **Nature** 最新发表 / News & Views
- **Science** 最新发表 / Perspectives
- **其他**（可选扩展）：Cell、The Lancet、PNAS 等

目标：了解顶刊当期在讲什么，不需要全文精读，但需要知道主题和核心发现。

#### ④ 金融与市场

- 全球主要市场动态（美股、A 股、加密货币大事件）
- 央行政策、利率决议等宏观事件
- 金融科技相关进展

#### ⑤ 生活与泛知识

- 重大社会事件、民生政策
- 健康/医疗突破（与顶刊板块互补）
- 文化/娱乐中的现象级事件

### 4.3 技术实现

| 组件 | 工具 | 说明 |
|------|------|------|
| RSS 源监控 | OpenClaw + rss-agent skill | 定时自动拉取 |
| GitHub Trending | GitHubTrendingRSS | 开源项目，每日生成 RSS |
| 社交媒体桥接 | RSSHub（自托管） | X/Twitter、微博、知乎等转 RSS |
| 全球热点 | Google Trends RSS + Reddit/.json | 跨领域热搜 |
| 学术期刊 | Nature/Science RSS Feed | 官方提供 RSS |
| 去重 | seen_hashes.json | 标题+URL 哈希去重 |
| 晨报生成 | OpenClaw Cron（每天 10:00） | LLM 融合整理 + 生成简报 |
| 推送 | 微信（OpenClaw 插件） | Alert Cron 到主会话 |

### 4.4 RSS 订阅源清单

**科技与 AI**

- Anthropic Engineering Blog / News
- OpenAI Blog
- NVIDIA Developer Blog
- GitHub Trending（All / Python / TypeScript）
- Hacker News (Best)
- InfoQ AI/ML

**人物动态（通过 RSSHub）**

- Elon Musk X Posts
- Andrej Karpathy X Posts
- （后续扩展更多人物）

**全球热点**

- Google Trends Daily（全球）
- Reuters Top News
- BBC World News
- AP News
- Reddit r/worldnews + r/popular
- 微博热搜 Top 50（RSSHub）
- 知乎热榜（RSSHub）

**顶级学术期刊**

- Nature Latest Research（RSS）
- Nature News & Views（RSS）
- Science Latest Articles（RSS）
- Science Perspectives（RSS）

**金融与市场**

- Bloomberg Markets（RSS）
- FT Markets（RSS）
- CoinDesk（RSS）

> 以上为初始种子源，后续根据使用体验持续增减。

### 4.5 去重与预警逻辑

```text
每天凌晨执行（Work Cron，后台静默）：
├── 1. 拉取所有 RSS 源的新条目
├── 2. 与 raw/feeds/seen_hashes.json 对比（标题+URL 的 hash 去重）
├── 3. 相同内容只记录一次；同一事件有新进展/分支 → 标记为"更新"
├── 4. 新内容 → 存入 raw/feeds/YYYY-MM-DD/
├── 5. 更新 seen_hashes.json
└── 6. 为 10:00 晨报准备素材池
```

去重确认正确的核心规则：

- **完全相同内容**：只入库一次，不重复提醒
- **同一事件新进展**：标记为"更新"，纳入下一次晨报
- **不同源报道同一事件**：合并为一条，注明多源

### 4.6 推送设置

**每天一次，统一早上 10:00 推送。** 不做实时推送、不做多次推送。

理由：减少打扰，集中阅读效率最高。所有内容（无论优先级）统一在晨报中呈现。

### 4.7 Cron 任务表

| 时间 | 类型 | 任务 |
|------|------|------|
| 每天 6:00 | Work（后台） | RSS 全量拉取 + 去重 + 存入 raw/feeds/ |
| **每天 10:00** | **Alert（推送）** | **每日融合简报（见 4.8 格式）** |
| 每天 22:00 | Work（后台） | LLM Wiki 日更：处理今天所有 raw/ 新内容 |
| 每周一 10:00 | Alert（推送） | 周报：本周新增知识、热点回顾、待办汇总 |

### 4.8 内容融合处理规则

这是本系统的**核心价值**——不是简单堆砌条目，而是 LLM 将多领域信息串联为一篇连贯的简报。

#### 处理原则

1. **串联融合**：将相关的热点、学术发现、行业动态等找到内在联系，串成一个叙事
2. **易读优先**：用你能听懂的语言表达，避免纯术语堆砌
3. **严格保持原意**：不为了易读而曲解、不过度简化、不添加主观臆断
4. **标注出处**：每条信息附带来源链接，方便深入阅读

#### LLM 处理流程

```text
raw/feeds/YYYY-MM-DD/ 中的所有今日素材
    ↓
Step 1：按领域分类（科技/热点/学术/金融/生活）
    ↓
Step 2：提取每条的核心信息（一句话摘要 + 重要度评分）
    ↓
Step 3：发现跨领域关联（例：Nature 发表 AI 蛋白质折叠论文 ↔ 科技圈讨论 AlphaFold 新版）
    ↓
Step 4：融合生成简报，相关内容串在一起讲，而非按来源分开罗列
    ↓
Step 5：生成最终推送文本 + 存入 raw/feeds/ 归档
```

### 4.9 每日简报示例

```text
OpenClaw（10:00 自动推送）：

📰 每日简报 2026-04-13

━━━ 🌍 今日全球热点 ━━━

1. 【AI + 学术】Anthropic 昨晚发布 Claude Opus 4.7 预览版，
   主打多步推理能力提升。同日 Nature 发表一篇关于 LLM 涌现
   能力的系统性研究，发现模型规模与推理能力之间存在阶梯式
   跃迁而非线性增长——这恰好解释了为何新一代模型感知上
   "突然变聪明了"。
   📎 Anthropic 官方 | Nature doi:10.1038/xxx

2. 【金融 + 政策】美联储理事暗示六月可能暂停加息，美股三大
   指数集体收涨。与此同时，SEC 批准了第二批现货以太坊 ETF，
   加密市场应声上涨 8%。
   � Reuters | Bloomberg

3. 【科技 + 开源】GitHub 热榜第一名：xxx 项目（+2.3K stars/天），
   一个轻量级 Agent 框架。Karpathy 发推评价其设计理念与他
   上周提出的 LLM Wiki 思路高度一致。
   📎 GitHub | @karpathy

4. 【Science 本期亮点】一项大规模临床试验表明，新型 GLP-1
   类药物在减重的同时显著降低心血管事件风险（-22%），该发现
   可能改变肥胖症的一线治疗方案。
   📎 Science doi:10.1126/xxx

5-10. ...（更多条目）

━━━ � 数据速览 ━━━
• BTC $68,420（+5.2%） | ETH $3,850（+8.1%）
• S&P 500 5,320（+0.8%） | 纳指 16,780（+1.2%）
• 微博热搜 #1: xxx

💡 今日关联洞察：本周 AI 领域出现密集发布（Anthropic 新模型 +
Nature LLM 研究 + 开源 Agent 框架爆火），可能标志着 Agent
架构正从实验阶段进入工程落地阶段。

📁 完整素材已归档 → raw/feeds/2026-04-13/
```

> **关键区别**：不是 5 个领域各列几条互不相干的新闻，而是找到它们之间的联系，用一个连贯的视角讲出来。同时每条都标注出处，你随时可以点进去看原文。

---

## 五、语音备忘与日常记录系统

### 5.1 需求详述

- 随时随地（手机微信）录制语音备忘录
- 作为开发、工作、日常学习中的"锚点"
- 减轻记忆负担，完整记录当前想法和工作安排
- 自动转文字、分类、提取 TODO、存入 RAW 层

### 5.2 处理链路

```text
手机发语音 → OpenClaw 收到 → Whisper/Deepgram 转文字 →
LLM 分析分类 → 存入 raw/daily/ 对应目录 →
提取 TODO → 回复确认消息 → 触发 Wiki 更新（如果涉及项目）
```

### 5.3 自动分类规则

| 内容类型 | 存储路径 | 示例 |
|---------|---------|------|
| 开发相关 | `raw/daily/memo/dev/` | 某个 API 接口的优化思路 |
| 工作安排 | `raw/daily/memo/work/` | 明天和 Boss 开会讨论需求 |
| 个人想法 | `raw/daily/memo/thinking/` | 对 AI Agent 架构的新思路 |
| 情感记录 | `raw/daily/journal/` | 今天很累但有成就感 |
| 财务相关 | `raw/finance/personal/` | 本月预算使用情况 |

### 5.4 文件格式示例

```markdown
---
date: 2026-04-12 22:30
source: wechat_voice
category: dev/memo
tags: [api, 优化, bug]
duration: 45s
---

# 某个接口的优化思路

## 原始转录
今天测试的时候发现有个接口响应偏慢，大概十次里面有一次超时。
我怀疑是冷启动的问题，明天需要看一下日志...

## AI 提取要点
- **问题**：某接口偶发超时（~10% 概率）
- **疑似原因**：冷启动
- **TODO**：明天检查日志，确认冷启动时间
```

### 5.5 STT 配置

| 优先级 | 引擎 | 特点 |
|--------|------|------|
| 首选 | Deepgram nova-3 | 云端，中文准确率高，有免费额度 |
| 备选 | Whisper large-v3（本地） | 完全免费，但需要 GPU |

### 5.6 使用场景

**场景 1：刷到好视频**

```text
你（微信）：[发送视频链接]
OpenClaw：🎬 正在解析视频...
OpenClaw：✅ 已提取并存储
  📁 raw/videos/2026-04-12_Karpathy谈LLM未来.md
  📝 要点：1. xxx 2. xxx 3. xxx
  🔗 已更新 Wiki 页面：[[karpathy]]、[[agent-architecture]]
```

**场景 2：突发灵感**

```text
你（微信）：[发送60秒语音]
OpenClaw：✅ 已记录：接口优化思路 → raw/daily/memo/dev/
  TODO 已提取：
  - [ ] 检查日志确认冷启动时间
  - [ ] 考虑预热方案
```

**场景 3：早上看简报**

```text
OpenClaw（10:00 自动推送）：
📰 每日简报 2026-04-13
1. 【AI + 学术】Anthropic 新模型 + Nature LLM 研究...
2. 【金融】美联储暂停加息信号 + 加密市场反弹...
3. 【Science】GLP-1 药物降低心血管风险...
� 今日关联洞察：Agent 架构进入工程落地阶段
```

---

## 六、成本规划

### 6.1 工具成本

| 项目 | 成本 | 说明 |
|------|------|------|
| Windsurf Pro | 已付费 | 主力编码，不额外花钱 |
| OpenRouter（Cron + 对话） | ~$3-5/月 | 用 Sonnet 4.5，不用 Opus |
| Chatbox（可选） | $9.9 终身 | 轻量手机客户端 |
| LibreChat | 免费 | 自托管 Docker |
| Obsidian | 免费 | 知识库浏览 |
| OpenClaw | 免费 | 开源自托管 |
| Deepgram STT | 免费额度 | 超出后按量计费 |
| RSSHub | 免费 | 自托管 |

### 6.2 OpenRouter Token 月度预算明细

| 任务 | 月成本 |
|------|--------|
| 每日简报生成 | ~$1.0 |
| RSS 巡检（扩展源） | ~$1.5 |
| 语音处理 | ~$0.8 |
| Wiki 日更 | ~$0.8 |
| 周报 | ~$0.1 |
| 日常对话 | ~$2-3 |
| **合计** | **~$5-8/月** |

**关键省钱点**：Cron 任务用 Sonnet 4.5 或更便宜的模型；语音转文字用 Deepgram 免费额度或本地 Whisper。

---

## 七、落地优先级

### 阶段一（本周）——基础设施

- [ ] 安装 Obsidian，创建 knowledge-base/ 目录结构
- [ ] 安装 Obsidian Web Clipper 浏览器插件
- [ ] Docker 部署 LibreChat + 配置 OpenRouter
- [ ] 开始手动向 raw/ 存素材

### 阶段二（下周）——通讯打通

- [ ] 安装 OpenClaw + 配置微信插件
- [ ] 配置 STT（Deepgram 或本地 Whisper）
- [ ] 实现"微信发链接/语音 → 存入 raw/"基本流程
- [ ] 测试语音备忘录端到端链路

### 阶段三（第 2-3 周）——自动化

- [ ] 配置 RSS 订阅源 + RSSHub 自托管
- [ ] 编写 AGENTS.md Schema（LLM Wiki 规则）
- [ ] 配置 Cron 任务（晨报、RSS 巡检、Wiki 日更）
- [ ] 编写 OpenClaw Skills（视频 ingest、语音分类）

---

> 本文档聚焦个人 Agent 协作与知识管理。核心项目开发请参见 `PROJECT_REQUIREMENTS.md`。
