"""Cost Tracker — LLM 调用成本追踪服务

功能：
1. 记录每次 LLM 调用的成本（模型、token 数、USD 费用）
2. 按会话（session）聚合成本
3. 按日聚合成本
4. 预算告警：当日/当月费用超过阈值时触发告警
5. 提供 API 查询接口数据

存储后端：
- SQLite 持久化（cost_log.db，进程重启不丢失）
- 内存字典缓存（快速查询当前会话数据）

设计原则：
- 异步写入，不阻塞主流程
- 即使追踪失败也不影响 LLM 调用
"""
from __future__ import annotations

import os
import time
import sqlite3
import threading
from collections import defaultdict
from datetime import datetime, timezone, date, timedelta
from typing import Optional, List, Dict
from dataclasses import dataclass, field
from pathlib import Path
from loguru import logger

# SQLite 持久化文件路径（与 dev.db 同目录）
_DB_DIR = Path(__file__).resolve().parent.parent.parent  # apps/backend/
_COST_DB_PATH = _DB_DIR / "cost_log.db"


@dataclass
class CostRecord:
    """单次 LLM 调用的成本记录"""
    timestamp: float
    model: str
    skill_role: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: float
    session_id: Optional[str] = None
    workflow: Optional[str] = None


@dataclass
class CostSummary:
    """成本汇总"""
    period: str             # "session:xxx" | "daily:2026-01-15" | "monthly:2026-01"
    total_cost_usd: float
    total_calls: int
    total_tokens: int
    by_model: Dict[str, float] = field(default_factory=dict)
    by_skill: Dict[str, float] = field(default_factory=dict)
    top_calls: List[dict] = field(default_factory=list)


@dataclass
class BudgetAlert:
    """预算告警"""
    level: str              # "warning" | "critical"
    message: str
    current_spend: float
    budget_limit: float
    period: str


class CostTracker:
    """LLM 调用成本追踪器（线程安全，SQLite 持久化）"""

    def __init__(self):
        self._lock = threading.Lock()
        self._records: List[CostRecord] = []
        self._daily_totals: Dict[str, float] = defaultdict(float)
        self._monthly_totals: Dict[str, float] = defaultdict(float)
        self._session_totals: Dict[str, float] = defaultdict(float)
        self._session_calls: Dict[str, int] = defaultdict(int)
        self._model_totals: Dict[str, float] = defaultdict(float)
        self._skill_totals: Dict[str, float] = defaultdict(float)

        self.daily_budget_usd: float = 5.0
        self.monthly_budget_usd: float = 100.0
        self.alert_threshold: float = 0.8

        self._alerts: List[BudgetAlert] = []
        self._db_path = str(_COST_DB_PATH)
        self._init_db()
        self._load_from_db()

    # ── SQLite 持久化 ────────────────────────────────────────

    def _init_db(self) -> None:
        """创建 cost_log 表（幂等）"""
        try:
            conn = sqlite3.connect(self._db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cost_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    model TEXT NOT NULL,
                    skill_role TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    total_tokens INTEGER NOT NULL,
                    cost_usd REAL NOT NULL,
                    session_id TEXT,
                    workflow TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cost_ts ON cost_log(timestamp)")
            conn.commit()
            conn.close()
            logger.info(f"[CostTracker] SQLite DB ready: {self._db_path}")
        except Exception as e:
            logger.warning(f"[CostTracker] SQLite init failed (will use memory only): {e}")

    def _persist(self, record: CostRecord) -> None:
        """将单条记录写入 SQLite（在 lock 外调用，自身线程安全）"""
        try:
            conn = sqlite3.connect(self._db_path)
            conn.execute(
                "INSERT INTO cost_log (timestamp, model, skill_role, input_tokens, output_tokens, total_tokens, cost_usd, session_id, workflow) VALUES (?,?,?,?,?,?,?,?,?)",
                (record.timestamp, record.model, record.skill_role, record.input_tokens, record.output_tokens, record.total_tokens, record.cost_usd, record.session_id, record.workflow),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"[CostTracker] SQLite persist failed: {e}")

    def _load_from_db(self) -> None:
        """启动时从 SQLite 加载历史记录到内存缓存"""
        try:
            conn = sqlite3.connect(self._db_path)
            # 只加载最近 90 天的记录，避免内存膨胀
            cutoff = time.time() - 90 * 86400
            rows = conn.execute(
                "SELECT timestamp, model, skill_role, input_tokens, output_tokens, total_tokens, cost_usd, session_id, workflow FROM cost_log WHERE timestamp > ? ORDER BY timestamp",
                (cutoff,),
            ).fetchall()
            conn.close()

            for row in rows:
                r = CostRecord(
                    timestamp=row[0], model=row[1], skill_role=row[2],
                    input_tokens=row[3], output_tokens=row[4], total_tokens=row[5],
                    cost_usd=row[6], session_id=row[7], workflow=row[8],
                )
                self._records.append(r)
                ts = datetime.fromtimestamp(r.timestamp, tz=timezone.utc)
                day_key = ts.strftime("%Y-%m-%d")
                month_key = ts.strftime("%Y-%m")
                self._daily_totals[day_key] += r.cost_usd
                self._monthly_totals[month_key] += r.cost_usd
                self._model_totals[r.model] += r.cost_usd
                self._skill_totals[r.skill_role] += r.cost_usd
                if r.session_id:
                    self._session_totals[r.session_id] += r.cost_usd
                    self._session_calls[r.session_id] += 1

            logger.info(f"[CostTracker] Loaded {len(rows)} historical records from SQLite")
        except Exception as e:
            logger.warning(f"[CostTracker] SQLite load failed (starting fresh): {e}")

    def record(
        self,
        model: str,
        skill_role: str,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        cost_usd: float,
        session_id: Optional[str] = None,
        workflow: Optional[str] = None,
    ) -> None:
        """记录一次 LLM 调用成本（线程安全）"""
        try:
            record = CostRecord(
                timestamp=time.time(),
                model=model,
                skill_role=skill_role or "unknown",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                cost_usd=cost_usd,
                session_id=session_id,
                workflow=workflow,
            )

            now = datetime.now(timezone.utc)
            day_key = now.strftime("%Y-%m-%d")
            month_key = now.strftime("%Y-%m")

            with self._lock:
                self._records.append(record)

                # 限制内存中的记录数量（保留最近 10000 条）
                if len(self._records) > 10000:
                    self._records = self._records[-8000:]

                self._daily_totals[day_key] += cost_usd
                self._monthly_totals[month_key] += cost_usd
                self._model_totals[model] += cost_usd
                self._skill_totals[record.skill_role] += cost_usd

                if session_id:
                    self._session_totals[session_id] += cost_usd
                    self._session_calls[session_id] += 1

                # 检查预算
                self._check_budget(day_key, month_key)

            # 持久化到 SQLite（lock 外执行，避免阻塞）
            self._persist(record)

        except Exception as e:
            logger.warning(f"[CostTracker] Failed to record: {e}")

    def _check_budget(self, day_key: str, month_key: str) -> None:
        """检查是否触发预算告警（在 lock 内调用）"""
        daily = self._daily_totals[day_key]
        monthly = self._monthly_totals[month_key]

        # 每日预算
        if daily >= self.daily_budget_usd:
            alert = BudgetAlert(
                level="critical",
                message=f"每日预算已用尽：${daily:.4f} / ${self.daily_budget_usd:.2f}",
                current_spend=daily,
                budget_limit=self.daily_budget_usd,
                period=f"daily:{day_key}",
            )
            self._add_alert(alert)
        elif daily >= self.daily_budget_usd * self.alert_threshold:
            alert = BudgetAlert(
                level="warning",
                message=f"每日预算接近上限：${daily:.4f} / ${self.daily_budget_usd:.2f} ({daily/self.daily_budget_usd:.0%})",
                current_spend=daily,
                budget_limit=self.daily_budget_usd,
                period=f"daily:{day_key}",
            )
            self._add_alert(alert)

        # 每月预算
        if monthly >= self.monthly_budget_usd:
            alert = BudgetAlert(
                level="critical",
                message=f"每月预算已用尽：${monthly:.4f} / ${self.monthly_budget_usd:.2f}",
                current_spend=monthly,
                budget_limit=self.monthly_budget_usd,
                period=f"monthly:{month_key}",
            )
            self._add_alert(alert)
        elif monthly >= self.monthly_budget_usd * self.alert_threshold:
            alert = BudgetAlert(
                level="warning",
                message=f"每月预算接近上限：${monthly:.4f} / ${self.monthly_budget_usd:.2f} ({monthly/self.monthly_budget_usd:.0%})",
                current_spend=monthly,
                budget_limit=self.monthly_budget_usd,
                period=f"monthly:{month_key}",
            )
            self._add_alert(alert)

    def _add_alert(self, alert: BudgetAlert) -> None:
        """添加告警（去重：同一 period 只保留最新）"""
        self._alerts = [a for a in self._alerts if a.period != alert.period]
        self._alerts.append(alert)
        logger.warning(f"[CostTracker] ALERT [{alert.level}] {alert.message}")

    # ── 查询接口 ─────────────────────────────────────────────

    def get_daily_summary(self, day: Optional[str] = None) -> CostSummary:
        """获取某天的成本汇总"""
        day = day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._lock:
            records = [r for r in self._records
                       if datetime.fromtimestamp(r.timestamp, tz=timezone.utc).strftime("%Y-%m-%d") == day]

        return self._build_summary(f"daily:{day}", records)

    def get_monthly_summary(self, month: Optional[str] = None) -> CostSummary:
        """获取某月的成本汇总"""
        month = month or datetime.now(timezone.utc).strftime("%Y-%m")
        with self._lock:
            records = [r for r in self._records
                       if datetime.fromtimestamp(r.timestamp, tz=timezone.utc).strftime("%Y-%m") == month]

        return self._build_summary(f"monthly:{month}", records)

    def get_session_summary(self, session_id: str) -> CostSummary:
        """获取某个会话的成本汇总"""
        with self._lock:
            records = [r for r in self._records if r.session_id == session_id]

        return self._build_summary(f"session:{session_id}", records)

    def get_active_alerts(self) -> List[dict]:
        """获取当前活跃的告警"""
        with self._lock:
            return [
                {
                    "level": a.level,
                    "message": a.message,
                    "current_spend": round(a.current_spend, 4),
                    "budget_limit": a.budget_limit,
                    "period": a.period,
                }
                for a in self._alerts
            ]

    def get_overview(self) -> dict:
        """获取成本概览（Dashboard 用）"""
        now = datetime.now(timezone.utc)
        day_key = now.strftime("%Y-%m-%d")
        month_key = now.strftime("%Y-%m")

        with self._lock:
            daily = self._daily_totals.get(day_key, 0.0)
            monthly = self._monthly_totals.get(month_key, 0.0)
            total_calls = len(self._records)
            total_cost = sum(r.cost_usd for r in self._records)

        # 全历史总花费（从 SQLite 查，不受 90 天内存限制）
        all_time = self.get_all_time_cost()

        with self._lock:

            # 今日/本月调用次数
            today_calls = sum(
                1 for r in self._records
                if datetime.fromtimestamp(r.timestamp, tz=timezone.utc).strftime("%Y-%m-%d") == day_key
            )
            month_calls = sum(
                1 for r in self._records
                if datetime.fromtimestamp(r.timestamp, tz=timezone.utc).strftime("%Y-%m") == month_key
            )

            # 最近 7 天趋势
            trend = []
            for i in range(6, -1, -1):
                d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
                trend.append({"date": d, "cost_usd": round(self._daily_totals.get(d, 0.0), 4)})

            top_models = dict(sorted(
                {k: round(v, 4) for k, v in self._model_totals.items()}.items(),
                key=lambda x: x[1], reverse=True
            )[:5])
            top_skills = dict(sorted(
                {k: round(v, 4) for k, v in self._skill_totals.items()}.items(),
                key=lambda x: x[1], reverse=True
            )[:5])

        return {
            "today_cost": round(daily, 4),
            "today_cost_usd": round(daily, 4),
            "today_budget_usd": self.daily_budget_usd,
            "budget_daily": self.daily_budget_usd,
            "today_usage_pct": round(daily / self.daily_budget_usd * 100, 1) if self.daily_budget_usd > 0 else 0,
            "month_cost": round(monthly, 4),
            "month_cost_usd": round(monthly, 4),
            "month_budget_usd": self.monthly_budget_usd,
            "budget_monthly": self.monthly_budget_usd,
            "month_usage_pct": round(monthly / self.monthly_budget_usd * 100, 1) if self.monthly_budget_usd > 0 else 0,
            "today_calls": today_calls,
            "month_calls": month_calls,
            "total_calls": total_calls,
            "total_cost_usd": round(total_cost, 4),
            "all_time_cost_usd": all_time["total_cost_usd"],
            "all_time_calls": all_time["total_calls"],
            "daily_trend": trend,
            "alerts": self.get_active_alerts(),
            "top_models": top_models,
            "top_skills": top_skills,
        }

    def get_all_time_cost(self) -> float:
        """获取 SQLite 中所有记录的总花费（不受 90 天内存窗口限制）"""
        try:
            conn = sqlite3.connect(self._db_path)
            row = conn.execute("SELECT COALESCE(SUM(cost_usd), 0), COUNT(*) FROM cost_log").fetchone()
            conn.close()
            return {"total_cost_usd": round(row[0], 4), "total_calls": row[1]}
        except Exception:
            # 回退到内存数据
            with self._lock:
                return {
                    "total_cost_usd": round(sum(r.cost_usd for r in self._records), 4),
                    "total_calls": len(self._records),
                }

    def get_history(self, granularity: str = "daily", limit: int = 90) -> dict:
        """获取历史成本明细

        granularity: 'daily' | 'monthly' | 'yearly'
        limit: 最近 N 条（daily 默认 90 天，monthly 默认 24 月，yearly 不限）
        """
        try:
            conn = sqlite3.connect(self._db_path)
            if granularity == "yearly":
                rows = conn.execute("""
                    SELECT strftime('%Y', datetime(timestamp, 'unixepoch')) AS period,
                           COALESCE(SUM(cost_usd), 0) AS cost,
                           COUNT(*) AS calls,
                           COALESCE(SUM(total_tokens), 0) AS tokens
                    FROM cost_log GROUP BY period ORDER BY period DESC
                """).fetchall()
            elif granularity == "monthly":
                rows = conn.execute(f"""
                    SELECT strftime('%Y-%m', datetime(timestamp, 'unixepoch')) AS period,
                           COALESCE(SUM(cost_usd), 0) AS cost,
                           COUNT(*) AS calls,
                           COALESCE(SUM(total_tokens), 0) AS tokens
                    FROM cost_log GROUP BY period ORDER BY period DESC LIMIT {limit}
                """).fetchall()
            else:  # daily
                rows = conn.execute(f"""
                    SELECT strftime('%Y-%m-%d', datetime(timestamp, 'unixepoch')) AS period,
                           COALESCE(SUM(cost_usd), 0) AS cost,
                           COUNT(*) AS calls,
                           COALESCE(SUM(total_tokens), 0) AS tokens
                    FROM cost_log GROUP BY period ORDER BY period DESC LIMIT {limit}
                """).fetchall()
            conn.close()

            items = [
                {"period": r[0], "cost_usd": round(r[1], 4), "calls": r[2], "tokens": r[3]}
                for r in rows
            ]
            total_cost = sum(i["cost_usd"] for i in items)
            total_calls = sum(i["calls"] for i in items)
            return {
                "granularity": granularity,
                "items": items,
                "total_cost_usd": round(total_cost, 4),
                "total_calls": total_calls,
            }
        except Exception as e:
            logger.warning(f"[CostTracker] get_history failed: {e}")
            return {"granularity": granularity, "items": [], "total_cost_usd": 0, "total_calls": 0}

    def update_budget(self, daily: Optional[float] = None, monthly: Optional[float] = None) -> dict:
        """动态更新预算限制"""
        with self._lock:
            if daily is not None:
                self.daily_budget_usd = max(0.0, daily)
            if monthly is not None:
                self.monthly_budget_usd = max(0.0, monthly)
        return {
            "daily_budget_usd": self.daily_budget_usd,
            "monthly_budget_usd": self.monthly_budget_usd,
        }

    def _build_summary(self, period: str, records: List[CostRecord]) -> CostSummary:
        """从记录列表构建汇总"""
        if not records:
            return CostSummary(period=period, total_cost_usd=0, total_calls=0, total_tokens=0)

        by_model: Dict[str, float] = defaultdict(float)
        by_skill: Dict[str, float] = defaultdict(float)
        total_cost = 0.0
        total_tokens = 0

        for r in records:
            total_cost += r.cost_usd
            total_tokens += r.total_tokens
            by_model[r.model] += r.cost_usd
            by_skill[r.skill_role] += r.cost_usd

        # Top 5 最贵的调用
        top = sorted(records, key=lambda r: r.cost_usd, reverse=True)[:5]
        top_calls = [
            {
                "model": r.model,
                "skill_role": r.skill_role,
                "cost_usd": round(r.cost_usd, 6),
                "tokens": r.total_tokens,
                "timestamp": datetime.fromtimestamp(r.timestamp, tz=timezone.utc).isoformat(),
            }
            for r in top
        ]

        return CostSummary(
            period=period,
            total_cost_usd=round(total_cost, 4),
            total_calls=len(records),
            total_tokens=total_tokens,
            by_model={k: round(v, 4) for k, v in by_model.items()},
            by_skill={k: round(v, 4) for k, v in by_skill.items()},
            top_calls=top_calls,
        )


# 全局单例
cost_tracker = CostTracker()
