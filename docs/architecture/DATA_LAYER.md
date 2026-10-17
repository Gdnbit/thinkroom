# Agent Decision Making System — 数据层架构设计文档

> **历史架构参考**：描述 2026-04 数据层，不是当前完成状态或 v0.1 验收依据。

**最近更新**：2026-04-18（scheduler 重构 + 衍生指标接入）

## 1. 文档目标

本文档用于定义系统数据层的完整方案，重点满足以下业务目标：

1. 尽可能使用免费数据源。
2. 构建自有历史数据库，支持“查询过去任意时刻的大致市场状态”。
3. 覆盖市场：美股、港股、A股、加密货币、全球大宗（黄金/原油等）。
4. 支持基金（尤其 A 股场外基金）日内估算，重点关注 14:00–15:00 区间走势。
5. 数据精度要求为“决策辅助级”，非高频交易级。

---

## 2. 总体架构

数据链路分为 6 层：

1. **数据源层**：免费主源 + 免费备源。
2. **采集层（MCP / Connector）**：按资产抓取行情与事件。
3. **标准化与计算层**：统一 symbol/时区/字段，并计算涨跌与估值。
4. **存储层**：事实表 + 业务卡片并存。
5. **任务调度层**：启动快照 + 每日 12:00 全量 + 工作日 14:00 A 股增强（10 min）+ 手动触发。
6. **输出层**：API、看板、时点回放、基金估值结果。

---

## 3. 数据来源设计（免费优先）

### 3.1 行情数据

- 主源：
  - 加密：Binance Public API（BTC/ETH/BNB/SOL/XRP/DOGE）
  - 传统市场：Yahoo Finance（美股指数 + 港股 + A 股指数 + 商品）
- 备源：
  - CoinGecko（加密备源）
  - 现有 DB 最新记录降级（若外部源暂不可用）

### 3.2 宏观与利率

- FRED（美联储公开数据）
- 配额策略：每日一次拉取主序列 + 缓存

### 3.3 事件/新闻

- Event Feed MCP + News Realtime MCP（现有）
- 事件保存字段：event_time / ingest_time / source / importance / asset_scope

### 3.5 衍生指标（2026-04-18 新增）

通过 `services/indicator_fetchers.py` 直接调用免费 HTTP API，结果写入 `market_snapshots`（`market=INDICATOR`）：

| 指标 | 来源 | symbol | 计算方式 |
|---|---|---|---|
| Pizza Index | CoinGecko simple/price | `PIZZA_INDEX` | BTC 价格 × 10,000 |
| Fear & Greed | alternative.me/fng | `FEAR_GREED` | 0-100 + classification |
| Polymarket Top 10 | Gamma API markets | `PM:<slug>` | 按 volume_24h 排序取前 10 |

### 3.4 基金估值参考数据

- A 股指数：CSI300 / ZZ500 / CYB（通过 Yahoo symbol 或后续 AKShare 扩展）
- 港股与海外参考：HSI / HSTECH / SPX / NDX
- 汇率可后续增强（QDII 场景）

---

## 4. 数据模型（核心新增）

本次在现有 `market_state_cards` 基础上新增事实表：

1. `market_snapshots`
   - 每次采样形成一行快照
   - 核心字段：`symbol, market, ts, price, pct_change_1d, source_name, quality_flag`

2. `fund_profiles`
   - 基金配置表
   - 核心字段：`fund_code, last_nav, basket(JSON), beta`

3. `fund_estimate_snapshots`
   - 基金估值时序表
   - 核心字段：`fund_code, ts, est_nav, est_pct_change_1d, confidence_level`

4. `source_health_logs`
   - 采集健康日志
   - 核心字段：`source_name, symbol, checked_at, success, latency_ms, error_message`

说明：
- `market_state_cards` 继续保留，作为技术指标视图。
- `market_snapshots` 作为“历史可追溯底座”。

---

## 5. 处理逻辑设计

### 5.1 市场快照采集

入口：`capture_market_snapshots(db, symbols, timeframe)`

流程：
1. 遍历 symbol，调用 `MarketDataMCP.get_market_state`。
2. 记录 `source_health_logs`。
3. 生成 `market_snapshots`。
4. 若返回缺失 `pct_change_1d`，尝试用前值计算近似。

### 5.2 基金估值（V1）

模型：

`est_nav_t = last_nav * (1 + beta * weighted_return)`

其中：
- `weighted_return = sum(weight_i * r_i) / sum(weight_i)`
- `r_i` 来自 `market_snapshots.pct_change_1d`

输出：
- `est_nav`
- `est_pct_change_1d`
- `confidence_level`（按可用权重覆盖率）

### 5.3 旧数据回填

入口：`sync_market_state_into_snapshots`

作用：
- 将已有 `market_state_cards` 回填到 `market_snapshots`
- 用于历史数据平滑迁移

---

## 6. 调度策略（2026-04-18 重构）

Scheduler 采用 asyncio tick，原「每 15 分钟全量 + 14:00 的 2 min 高频」方案改为以下：

1. **启动后 5s** 执行一次全量快照（16 资产 + 3 类指标）
2. **每日 Asia/Shanghai 12:00** 执行一次全量快照（防长期在线）
3. **工作日 14:00-14:59 每 10 分钟**：仅抓 A 股 / 港股 5 个（CSI300/ZZ500/CYB/HSI/HSTECH），不重抓指标
4. 每 1 h 执行生命周期检查
5. 每 6 h 执行自动 briefing + 社媒同步
6. 每 12 h 执行 Daily Radar

**手动触发**：前端 Chat 页「刷新数据」按钮 → `POST /api/v1/snapshots/trigger`（同步阻塞，30-60s。防报 busy 错误通过 `_last_daily_snapshot_date` 并置限流）。

---

## 7. API 输出设计

### 7.1 快照历史查询（`/api/v1/market-history`）

1. `POST /snapshots/capture` — 底层抓取（不含指标）
2. `GET /snapshots/point?symbol=BTC&ts=...` — 某 symbol 在某时间点的快照
3. `GET /snapshots/window?symbol=BTC&days=7` — 时序回放
4. `POST /snapshots/backfill-from-market-state` — 从旧表回填
5. `GET /funds/estimate/latest` — 基金最新估值

### 7.2 手动刷新与状态（`/api/v1/snapshots`，2026-04-18 新增）

1. `POST /snapshots/trigger` — 同步阻塞抓取全量 16 资产 + 3 类指标，支持 `symbols[]` / `include_indicators` 参数
2. `GET /snapshots/status` — 最近一次快照元数据（`ts` / `triggered_by` / `saved` / `failed` / `indicators`）

---

## 8. 数据质量与降级

### 8.1 质量标签

- `normal`：正常
- `stale`：过旧
- `suspect`：疑似异常（后续可按阈值触发）

### 8.2 降级顺序

1. 主源实时/准实时
2. 备源
3. DB 最新记录

### 8.3 可观测性

- 每次采集写 `source_health_logs`
- 可用以统计成功率、延迟分布、异常频次

---

## 9. 当前实现对照（已完成）

### 9.1 已落地代码模块

- 新增 ORM：
  - `app/schemas/market_snapshot.py`
- 新增服务：
  - `app/services/snapshot_service.py`
- 新增 API：
  - `app/api/v1/market_history.py`
- 路由注册：
  - `app/api/v1/__init__.py`
- 调度增强：
  - `app/services/scheduler.py`
- 市场覆盖扩展：
  - `app/mcps/market_data_mcp.py` 新增 HSI/HSTECH/CSI300/ZZ500/CYB + GOLD/SILVER/OIL/DXY/BNB/SOL
- `app/services/indicator_fetchers.py` 新增 Pizza / Fear&Greed / Polymarket 抓取器（2026-04-18）
- `app/api/v1/snapshots.py` 新增手动刷新端点（2026-04-18）
- schema 汇总导出：
  - `app/schemas/__init__.py`

### 9.2 功能映射

1. 历史快照库：✅
2. 手动触发采集：✅
3. 时点查询与窗口回放：✅
4. 基金估值 V1：✅
5. 14:00–15:00 增强采样：✅
6. 采集健康日志：✅
7. 回填旧市场状态：✅

---

## 10. 后续迭代建议

1. A 股与基金数据源增强：引入 AKShare 作为 CN 专用增强源。
2. QDII 估值增强：加入汇率与海外市场时区修正。
3. 异常检测规则：加入跳变阈值与双源偏差检测。
4. 可视化看板：增加“14:00–15:00 基金估值热力图”。
5. EOD 对账：日终修正估值误差并输出日报。

---

## 11. 设计结论

该版本实现了“免费优先 + 自建时点历史 + 基金估值辅助 + 分时段增强采样”的目标，能够支持你当前最核心的使用方式：

- 平时靠自动采样沉淀历史
- 想看时可手动触发并立即落库
- 重点时段（14:00–15:00）自动提高采样密度
- 在自有数据库中直接查询过去任意时刻与走势，不依赖临时外部查询
