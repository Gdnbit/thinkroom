"""Progress Tracker — 实时阶段式进度追踪

替代旧的预估倒计时方案，改为基于实际工作流阶段的实时进度。

每个工作流定义一组固定阶段（stages），每完成一个阶段就更新进度。
前端通过轮询 GET /progress/{job_id} 获取当前状态。

数据结构：
{
    "job_id": "xxx",
    "workflow": "ingestion",
    "status": "running",       // running | completed | failed
    "current_stage": "extraction",
    "current_stage_label": "正在提取观点...",
    "stages": [
        {"key": "parse", "label": "解析文件", "status": "completed", "elapsed_ms": 1200},
        {"key": "extraction", "label": "提取观点", "status": "running", "elapsed_ms": null},
        {"key": "embedding", "label": "生成向量", "status": "pending", "elapsed_ms": null},
    ],
    "progress_pct": 45,        // 0-100 基于已完成阶段数
    "started_at": "...",
    "elapsed_ms": 3200,
    "message": "正在提取第 2/5 条观点...",
}

线程安全，内存存储（进程重启后丢失，但 job 本身会在 DB 中）。
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any, Optional, List, Dict
from loguru import logger


def _clean_ids(values: Optional[List[str]]) -> List[str]:
    result: List[str] = []
    seen: set[str] = set()
    for value in values or []:
        if not isinstance(value, str):
            continue
        clean = value.strip()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


@dataclass
class Stage:
    """工作流的单个阶段"""
    key: str
    label: str
    status: str = "pending"    # pending | running | completed | skipped | failed
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    elapsed_ms: Optional[int] = None
    detail: Optional[str] = None


@dataclass
class JobProgress:
    """单个 Job 的完整进度"""
    job_id: str
    workflow: str
    stages: List[Stage]
    source_id: Optional[str] = None
    source_ids: List[str] = field(default_factory=list)
    session_id: Optional[str] = None
    object_scope: Optional[Dict[str, Any]] = None
    related_object_id: Optional[str] = None
    related_object_type: Optional[str] = None
    status: str = "running"
    started_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    message: str = ""
    error: Optional[str] = None


# ── 工作流阶段定义 ───────────────────────────────────────────

WORKFLOW_STAGES: Dict[str, List[dict]] = {
    "ingestion": [
        {"key": "load", "label": "加载文件"},
        {"key": "parse", "label": "解析内容"},
        {"key": "speaker", "label": "识别发言人"},
        {"key": "extraction", "label": "提取观点"},
        {"key": "quality_check", "label": "质量评估"},
        {"key": "contradiction", "label": "矛盾检测"},
        {"key": "embedding", "label": "生成向量索引"},
        {"key": "persist", "label": "保存入库"},
    ],
    "briefing": [
        {"key": "context", "label": "聚合上下文"},
        {"key": "claims", "label": "检索私域观点"},
        {"key": "market", "label": "获取市场数据"},
        {"key": "events", "label": "获取事件"},
        {"key": "flow", "label": "获取资金信号"},
        {"key": "scoring", "label": "量化信号评分"},
        {"key": "fusion", "label": "多源信号融合"},
        {"key": "generate", "label": "生成简报"},
        {"key": "persist", "label": "保存简报"},
    ],
    "committee_debate": [
        {"key": "context", "label": "聚合上下文"},
        {"key": "round_1_baseline", "label": "第1轮·基准分析师"},
        {"key": "round_1_probability", "label": "第1轮·概率分析师"},
        {"key": "round_1_risk", "label": "第1轮·风控哨兵"},
        {"key": "round_1_structure", "label": "第1轮·市场结构分析师"},
        {"key": "round_1_audit", "label": "第1轮·真伪审计官"},
        {"key": "round_1_pnl", "label": "第1轮·执行经济性顾问"},
        {"key": "round_2_baseline", "label": "第2轮·基准分析师"},
        {"key": "round_2_probability", "label": "第2轮·概率分析师"},
        {"key": "round_2_risk", "label": "第2轮·风控哨兵"},
        {"key": "round_2_structure", "label": "第2轮·市场结构分析师"},
        {"key": "round_2_audit", "label": "第2轮·真伪审计官"},
        {"key": "round_2_pnl", "label": "第2轮·执行经济性顾问"},
        {"key": "adjudication", "label": "最终裁决"},
        {"key": "persist", "label": "保存结果"},
    ],
    "strategy_distillation": [
        {"key": "load", "label": "加载来源"},
        {"key": "extract", "label": "提取策略"},
        {"key": "persist", "label": "保存策略卡"},
    ],
}


class ProgressTracker:
    """实时进度追踪器（线程安全）"""

    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: Dict[str, JobProgress] = {}

    def start_job(
        self,
        job_id: str,
        workflow: str,
        source_id: Optional[str] = None,
        source_ids: Optional[List[str]] = None,
        session_id: Optional[str] = None,
        object_scope: Optional[Dict[str, Any]] = None,
        related_object_id: Optional[str] = None,
        related_object_type: Optional[str] = None,
    ) -> None:
        """开始追踪一个 Job"""
        stage_defs = WORKFLOW_STAGES.get(workflow, [{"key": "processing", "label": "处理中"}])
        stages = [Stage(key=s["key"], label=s["label"]) for s in stage_defs]
        clean_source_ids = _clean_ids(([source_id] if source_id else []) + (source_ids or []))

        with self._lock:
            self._jobs[job_id] = JobProgress(
                job_id=job_id,
                workflow=workflow,
                stages=stages,
                source_id=source_id or (clean_source_ids[0] if clean_source_ids else None),
                source_ids=clean_source_ids,
                session_id=session_id,
                object_scope=object_scope,
                related_object_id=related_object_id,
                related_object_type=related_object_type,
                status="running",
                started_at=time.time(),
                message=f"开始 {workflow} 流程...",
            )
            # 限制内存：最多保留 200 个 job
            if len(self._jobs) > 200:
                oldest_keys = sorted(self._jobs.keys(), key=lambda k: self._jobs[k].started_at)[:50]
                for k in oldest_keys:
                    del self._jobs[k]

        logger.debug(f"[Progress] Job {job_id} started: {workflow}")

    def update_stage(
        self,
        job_id: str,
        stage_key: str,
        status: str = "running",
        message: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> None:
        """更新某个阶段的状态"""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return

            now = time.time()
            for stage in job.stages:
                if stage.key == stage_key:
                    if status == "running" and stage.started_at is None:
                        stage.started_at = now
                    stage.status = status
                    if detail:
                        stage.detail = detail
                    if status in ("completed", "skipped", "failed"):
                        stage.completed_at = now
                        if stage.started_at:
                            stage.elapsed_ms = int((now - stage.started_at) * 1000)
                        elif stage.elapsed_ms is None:
                            stage.elapsed_ms = 0
                    break

            if message:
                job.message = message

    def complete_job(self, job_id: str, message: str = "完成") -> None:
        """标记 Job 完成"""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.status = "completed"
            job.completed_at = time.time()
            job.message = message
            # 将所有 pending/running 阶段标记为 completed
            for s in job.stages:
                if s.status in ("pending", "running"):
                    was_running = s.status == "running"
                    s.status = "completed"
                    if not s.completed_at:
                        s.completed_at = job.completed_at
                    if was_running and s.started_at:
                        s.elapsed_ms = int((s.completed_at - s.started_at) * 1000)

    def fail_job(self, job_id: str, error: str) -> None:
        """标记 Job 失败"""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.status = "failed"
            job.completed_at = time.time()
            job.error = error
            job.message = f"失败: {error[:100]}"

    def cancel_job(self, job_id: str, reason: str = "用户取消") -> None:
        """标记 Job 已取消"""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.status = "cancelled"
            job.completed_at = time.time()
            job.error = reason
            job.message = f"已取消: {reason[:100]}"
            for s in job.stages:
                if s.status == "running":
                    s.status = "failed"
                    s.completed_at = job.completed_at
                    if s.started_at:
                        s.elapsed_ms = int((s.completed_at - s.started_at) * 1000)
                elif s.status == "pending":
                    s.status = "skipped"

    def get_progress(self, job_id: str) -> Optional[dict]:
        """获取 Job 的当前进度（供 API 返回）"""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None

            now = time.time()
            total = len(job.stages)
            completed = sum(1 for s in job.stages if s.status in ("completed", "skipped"))
            progress_pct = int(completed / total * 100) if total > 0 else 0

            # 找到当前正在运行的阶段
            current_stage = None
            current_label = ""
            current_stage_elapsed_ms = None
            for s in job.stages:
                if s.status == "running":
                    current_stage = s.key
                    current_label = s.label
                    stage_started_at = s.started_at or job.started_at
                    current_stage_elapsed_ms = max(0, int((now - stage_started_at) * 1000))
                    break

            # 如果没有 running 的，取第一个 pending 的
            if not current_stage:
                for s in job.stages:
                    if s.status == "pending":
                        current_stage = s.key
                        current_label = s.label
                        if job.status == "running":
                            current_stage_elapsed_ms = max(0, int((now - job.started_at) * 1000))
                        break

            stage_rows = []
            for s in job.stages:
                elapsed_ms = s.elapsed_ms
                status = s.status
                if s.status == "running":
                    stage_started_at = s.started_at or job.started_at
                    elapsed_ms = max(0, int((now - stage_started_at) * 1000))
                elif job.status == "running" and s.key == current_stage and s.status == "pending":
                    status = "running"
                    elapsed_ms = current_stage_elapsed_ms
                stage_rows.append(
                    {
                        "key": s.key,
                        "label": s.label,
                        "status": status,
                        "elapsed_ms": elapsed_ms,
                        "detail": s.detail,
                    }
                )

            return {
                "job_id": job.job_id,
                "source_id": job.source_id,
                "source_ids": job.source_ids,
                "session_id": job.session_id,
                "object_scope": job.object_scope,
                "related_object_id": job.related_object_id,
                "related_object_type": job.related_object_type,
                "workflow": job.workflow,
                "status": job.status,
                "current_stage": current_stage,
                "current_stage_label": current_label,
                "current_stage_elapsed_ms": current_stage_elapsed_ms,
                "stages": stage_rows,
                "progress_pct": progress_pct if job.status != "completed" else 100,
                "started_at": datetime.fromtimestamp(job.started_at, tz=timezone.utc).isoformat(),
                "elapsed_ms": int((now - job.started_at) * 1000),
                "message": job.message,
                "error": job.error,
            }

    def list_active(self) -> List[dict]:
        """列出所有活跃（running）的 Job 进度"""
        with self._lock:
            active = [j for j in self._jobs.values() if j.status == "running"]

        return [self.get_progress(j.job_id) for j in active if self.get_progress(j.job_id)]


# 全局单例
progress_tracker = ProgressTracker()
