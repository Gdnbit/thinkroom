"""Processing Trace Service — 数据处理全链路留痕服务

用法：
    tracer = ProcessingTracer(db, source_id="xxx", job_id="yyy")
    
    tracer.trace("raw_input", 0,
        input_snapshot={"raw_text_len": 5000, "source_type": "pdf"},
        output_snapshot={"text_extracted": True},
    )
    
    with tracer.step("claim_extraction", 4) as t:
        result = extraction_skill.execute(...)
        t.output = {"claims_count": 3, "is_mock": False}
    
    # 查询某个 source 的全链路
    traces = ProcessingTraceRepository.get_by_source(db, source_id)
"""
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from contextlib import contextmanager
from sqlalchemy.orm import Session
from ulid import ULID
from loguru import logger

from app.schemas.processing_trace import ProcessingTrace


class ProcessingTracer:
    """单次处理流水线的 trace 记录器"""

    def __init__(self, db: Session, source_id: str, job_id: str = None):
        self.db = db
        self.source_id = source_id
        self.job_id = job_id

    def trace(
        self,
        step_name: str,
        step_index: int,
        input_snapshot: Dict[str, Any] = None,
        output_snapshot: Dict[str, Any] = None,
        duration_ms: int = 0,
        status: str = "ok",
        error_message: str = None,
        meta: Dict[str, Any] = None,
    ) -> str:
        """记录一条处理步骤 trace，返回 trace_id"""
        now = datetime.now(timezone.utc)
        trace_id = str(ULID())
        try:
            t = ProcessingTrace(
                id=trace_id,
                source_id=self.source_id,
                job_id=self.job_id,
                step_index=step_index,
                step_name=step_name,
                status=status,
                input_snapshot=_safe_json(input_snapshot or {}),
                output_snapshot=_safe_json(output_snapshot or {}),
                duration_ms=duration_ms,
                error_message=error_message,
                meta=_safe_json(meta or {}),
                created_at=now,
                updated_at=now,
            )
            self.db.add(t)
            self.db.flush()
        except Exception as e:
            logger.warning(f"[Trace] Failed to record step {step_name}: {e}")
        return trace_id

    @contextmanager
    def step(self, step_name: str, step_index: int, input_snapshot: Dict[str, Any] = None):
        """上下文管理器：自动计时并记录"""
        ctx = _StepContext(input_snapshot)
        t0 = time.time()
        try:
            yield ctx
            duration = int((time.time() - t0) * 1000)
            self.trace(
                step_name=step_name,
                step_index=step_index,
                input_snapshot=ctx.input or {},
                output_snapshot=ctx.output or {},
                duration_ms=duration,
                status=ctx.status or "ok",
                meta=ctx.meta or {},
            )
        except Exception as e:
            duration = int((time.time() - t0) * 1000)
            self.trace(
                step_name=step_name,
                step_index=step_index,
                input_snapshot=ctx.input or {},
                output_snapshot=ctx.output or {},
                duration_ms=duration,
                status="error",
                error_message=str(e),
                meta=ctx.meta or {},
            )
            raise


class _StepContext:
    """step() 上下文，允许在 with 块内填充 output/meta"""
    def __init__(self, input_snapshot=None):
        self.input = input_snapshot or {}
        self.output: Dict[str, Any] = {}
        self.status: str = "ok"
        self.meta: Dict[str, Any] = {}


class ProcessingTraceRepository:
    """查询接口"""

    @staticmethod
    def get_by_source(db: Session, source_id: str, limit: int = 100):
        return (
            db.query(ProcessingTrace)
            .filter(ProcessingTrace.source_id == source_id)
            .order_by(ProcessingTrace.step_index)
            .limit(limit)
            .all()
        )

    @staticmethod
    def get_by_job(db: Session, job_id: str, limit: int = 100):
        return (
            db.query(ProcessingTrace)
            .filter(ProcessingTrace.job_id == job_id)
            .order_by(ProcessingTrace.step_index)
            .limit(limit)
            .all()
        )

    @staticmethod
    def get_recent(db: Session, limit: int = 50):
        return (
            db.query(ProcessingTrace)
            .order_by(ProcessingTrace.created_at.desc())
            .limit(limit)
            .all()
        )


def _safe_json(data: Any) -> dict:
    """确保数据可以 JSON 序列化（截断过长字段）"""
    if not isinstance(data, dict):
        return {"value": str(data)[:2000]}
    result = {}
    for k, v in data.items():
        if isinstance(v, str) and len(v) > 2000:
            result[k] = v[:2000] + f"...[truncated, total {len(v)} chars]"
        elif isinstance(v, (list, dict)):
            s = str(v)
            if len(s) > 3000:
                result[k] = f"[{type(v).__name__}, len={len(v) if isinstance(v, list) else len(str(v))}]"
            else:
                result[k] = v
        elif isinstance(v, datetime):
            result[k] = v.isoformat()
        else:
            result[k] = v
    return result
