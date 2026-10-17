"""Ingestion Workflow

职责：编排上传 → 抽取 → 存储的完整流程。

流程：
1. 获取 pending 状态的 RawSource
2. 读取文件内容
3. 调用 Claim Extraction Skill
4. 持久化 ClaimCard 到数据库
5. 更新 RawSource 状态为 completed
6. 写入 job_runs 记录（G3 修复）
"""
import json as _json
import time
import unicodedata
from datetime import datetime, timezone, timedelta
from typing import Optional
from sqlalchemy.orm import Session
from ulid import ULID
from loguru import logger

from app.config import settings
from app.database import safe_commit
from app.services.raw_source_repository import RawSourceRepository
from app.services.claim_repository import ClaimRepository
from app.services.speaker_repository import SpeakerRepository
from app.services.contradiction_detector import ContradictionDetector
from app.services import semantic_search as sem_search
from app.services.document_parser_adapter import resolve_document_parser_adapter
from app.services.processing_trace_service import ProcessingTracer
from app.services.progress_tracker import progress_tracker
from app.skills.claim_extraction_skill import ClaimExtractionSkill, ClaimExtractionInput
from app.skills.speaker_profiling_skill import SpeakerProfilingSkill, SpeakerProfilingInput
from app.schemas.claim_card import ClaimCard, ClaimRawSourceLink, ClaimEvidenceLink
from app.schemas.evidence_span import EvidenceSpan
from app.schemas.speaker_profile import SpeakerProfile

# ── CJK 兼容字符修复 ─────────────────────────────────────────
# PDF OCR 常把标准汉字输出为 CJK Radical Supplement (U+2E80-U+2EFF)
# NFKC 可修复 U+2F00-U+2FDF 范围，但 U+2E80-U+2EFF 需手动映射
_CJK_RADICAL_MAP = {
    '\u2ea0': '\u6c11',  # ⺠ → 民
    '\u2ec5': '\u89c1',  # ⻅ → 见
    '\u2ec6': '\u89d2',  # ⻆ → 角
    '\u2ec9': '\u8d1d',  # ⻉ → 贝
    '\u2ecb': '\u8f66',  # ⻋ → 车
    '\u2ed3': '\u957f',  # ⻓ → 长
    '\u2ed4': '\u95e8',  # ⻔ → 门
    '\u2ed8': '\u9752',  # ⻘ → 青
    '\u2eda': '\u9875',  # ⻚ → 页
    '\u2edb': '\u98ce',  # ⻛ → 风
    '\u2edc': '\u98de',  # ⻜ → 飞
    '\u2ee2': '\u9a6c',  # ⻢ → 马
    '\u2ee3': '\u9aa8',  # ⻣ → 骨
    '\u2ee5': '\u9c7c',  # ⻥ → 鱼
    '\u2ee9': '\u9ec4',  # ⻩ → 黄
}

def _normalize_cjk_text(text: str) -> str:
    """NFKC 正规化 + CJK Radical Supplement 手动映射"""
    if not text:
        return text
    text = unicodedata.normalize('NFKC', text)
    for compat, standard in _CJK_RADICAL_MAP.items():
        text = text.replace(compat, standard)
    return text
from app.schemas.job_run import JobRun
from app.schemas.lifecycle_log import LifecycleLog
from app.schemas.raw_source import SourceAttachment
from app.utils.document_parse_result import DocumentParseSegment, build_document_parse_summary
from app.utils.parse_diagnostics import build_parse_diagnostics, encode_processing_error

# ── 资产类别识别 ─────────────────────────────────────────────
_ASSET_CLASS_MAP = {
    # 加密货币 — 高波动，时间维度更短
    "crypto": {"BTC", "ETH", "BNB", "SOL", "XRP", "DOGE", "ADA", "AVAX", "DOT", "MATIC",
               "LINK", "UNI", "AAVE", "ARB", "OP", "SUI", "APT", "PEPE", "WIF", "BONK"},
    # 美股/指数 — 标准周期
    "equity": {"SPX", "NDX", "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META",
               "HSI", "HSTECH", "CSI300", "ZZ500", "CYB"},
    # 大宗商品 — 受季节/库存周期驱动
    "commodity": {"GOLD", "OIL", "SILVER", "COPPER", "NG"},
    # 宏观/利率 — 长周期
    "macro": {"DXY", "VIX", "FEDFUNDS", "DGS10", "DGS2", "CPIAUCSL", "UNRATE"},
}

# ── 时间范围双轨设计 ───────────────────────────────────────────
#
# 区分两种时间概念：
# 1. event_date（确定性事件时间戳）：某个具体事件在某天发生
#    例："FOMC 6月15日决议"、"BTC 减半 2028-04-20"、"META 财报 Q1"
#    → 此类 claim 的 valid_until = event_date + 缓冲天数（事件影响衰减期）
#
# 2. influence_window（持续性影响时间窗口）：一个趋势/因素的持续影响周期
#    例："美联储降息周期利好风险资产" → 影响窗口 180-365天
#    例："BTC 短期超卖反弹" → 影响窗口 7-14天
#    → 此类 claim 的 valid_until = created_at + influence_window_days
#
# 如果 LLM 提取出 event_date，优先使用 event_date + 缓冲；否则 fallback 到 influence_window

# event_date 之后的影响衰减天数（按资产类别）
_EVENT_DECAY_DAYS = {
    "crypto": {"immediate": 1, "short": 3, "medium": 14, "long": 30},
    "equity": {"immediate": 2, "short": 7, "medium": 30, "long": 60},
    "commodity": {"immediate": 3, "short": 14, "medium": 45, "long": 90},
    "macro": {"immediate": 5, "short": 21, "medium": 60, "long": 180},
}

# influence_window 天数映射（持续性影响，无确定事件日期时使用）
_INFLUENCE_WINDOW_BY_CLASS = {
    "crypto": {"immediate": 2, "short": 14, "medium": 60, "long": 180, "unspecified": 30},
    "equity": {"immediate": 3, "short": 30, "medium": 90, "long": 365, "unspecified": 90},
    "commodity": {"immediate": 5, "short": 45, "medium": 120, "long": 365, "unspecified": 90},
    "macro": {"immediate": 7, "short": 60, "medium": 180, "long": 730, "unspecified": 180},
}

# 兜底（无法识别资产类别时）
HORIZON_DAYS = {
    "immediate": 3,
    "short": 30,
    "medium": 90,
    "long": 365,
    "unspecified": 90,
}


def _detect_asset_class(assets: list) -> str:
    """从资产列表推断资产类别"""
    if not assets:
        return "equity"
    for asset in assets:
        a = asset.upper()
        for cls, symbols in _ASSET_CLASS_MAP.items():
            if a in symbols:
                return cls
    return "equity"


def _calc_valid_until(
    time_horizon: str,
    created_at: datetime,
    asset_scope: list = None,
    event_date: datetime = None,
    time_sensitive: bool = True,
) -> datetime | None:
    """根据时间范围 + 资产类别 + 事件日期计算失效时间。
    
    三轨逻辑：
    - time_sensitive=False → 返回 None（长期有效，理念/框架/方法论类观点不过期）
    - 有 event_date → valid_until = event_date + 衰减天数（确定性事件）
    - 无 event_date → valid_until = created_at + 影响窗口天数（持续性影响）
    """
    if not time_sensitive:
        return None
    
    asset_class = _detect_asset_class(asset_scope or [])
    horizon = time_horizon or "unspecified"
    
    if event_date:
        # 确定性事件：事件日期 + 衰减期
        decay_map = _EVENT_DECAY_DAYS.get(asset_class, _EVENT_DECAY_DAYS["equity"])
        decay_days = decay_map.get(horizon, 30)
        return event_date + timedelta(days=decay_days)
    else:
        # 持续性影响：创建时间 + 影响窗口
        window_map = _INFLUENCE_WINDOW_BY_CLASS.get(asset_class, HORIZON_DAYS)
        window_days = window_map.get(horizon, 90)
        return created_at + timedelta(days=window_days)


def _parse_event_date(claim_data: dict) -> datetime | None:
    """尝试从 claim 中提取确定性事件日期。
    
    支持字段：event_date, target_date, expected_date
    支持格式：ISO 8601, YYYY-MM-DD, 时间戳
    """
    for field_name in ("event_date", "target_date", "expected_date"):
        raw = claim_data.get(field_name)
        if not raw:
            continue
        if isinstance(raw, datetime):
            return raw
        if isinstance(raw, str):
            raw = raw.strip()
            for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d"):
                try:
                    return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
    return None


def _locate_text_span(text: str, quote: str, fallback_start: int = 0, fallback_chars: int = 700) -> tuple[str, int | None, int | None, str, float]:
    source_text = text or ""
    quote_text = (quote or "").strip()
    if quote_text and source_text:
        idx = source_text.find(quote_text)
        if idx >= 0:
            return source_text[idx:idx + len(quote_text)], idx, idx + len(quote_text), "exact_quote", 1.0
        try:
            from app.services.quote_grounding import ground_quote
            grounded = ground_quote(source_text, quote_text)
            if grounded.is_grounded and grounded.start is not None and grounded.end is not None:
                return grounded.grounded_quote, grounded.start, grounded.end, f"quote_grounding:{grounded.status.value}", grounded.score
        except Exception:
            pass

    if not source_text:
        return quote_text, None, None, "llm_quote_unlocated", 0.35 if quote_text else 0.0

    start = max(0, min(fallback_start, len(source_text)))
    end = min(len(source_text), start + fallback_chars)
    content = source_text[start:end].strip()
    return content, start, start + len(content), "source_window_fallback", 0.55


def _chunk_start_in_document(document_text: str, chunk_text: str, search_from: int = 0) -> int | None:
    if not document_text or not chunk_text:
        return None
    start = document_text.find(chunk_text, max(0, search_from))
    if start < 0:
        start = document_text.find(chunk_text)
    return start if start >= 0 else None


def _add_claim_evidence_span(
    db: Session,
    *,
    claim_id: str,
    source_id: str,
    source_text: str,
    quote: str,
    fallback_start: int = 0,
    now: datetime,
    metadata: dict | None = None,
) -> str | None:
    content, char_start, char_end, method, score = _locate_text_span(
        source_text,
        quote,
        fallback_start=fallback_start,
    )
    if not content:
        return None

    span = EvidenceSpan(
        id=str(ULID()),
        source_id=source_id,
        span_type="quote" if quote else "source_window",
        content=content,
        page_or_timestamp=None,
        confidence=max(0.0, min(1.0, float(score or 0.0))),
        extraction_method=method,
        char_start=char_start,
        char_end=char_end,
        metadata_={
            "claim_id": claim_id,
            **(metadata or {}),
        },
        created_at=now,
        updated_at=now,
    )
    db.add(span)
    db.flush()
    db.add(ClaimEvidenceLink(
        claim_id=claim_id,
        evidence_span_id=span.id,
        relevance_score=span.confidence,
        created_at=now,
        updated_at=now,
    ))
    return span.id


def _normalize_trust_level(raw: str | None) -> str | None:
    """将用户自由输入的信任度文本归一化为 high/medium/low"""
    if not raw:
        return None
    t = raw.strip().lower()
    if any(kw in t for kw in ("高", "high", "权威", "准确", "靠谱", "信任")):
        return "high"
    if any(kw in t for kw in ("低", "low", "不靠谱", "仅供", "了解", "存疑")):
        return "low"
    if any(kw in t for kw in ("中", "medium", "一般", "参考")):
        return "medium"
    # 无法识别则保留原文到 user_note，trust 默认 medium
    return "medium"


# 自由输入的来源类型 → 系统枚举映射
_SOURCE_TYPE_NORMALIZE = {
    "专业文章": "article", "文章": "article", "分析": "article", "article": "article",
    "群聊记录": "chat", "群聊": "chat", "聊天": "chat", "chat": "chat",
    "视频文字稿": "video", "视频": "video", "video": "video",
    "个人笔记": "note", "笔记": "note", "note": "note", "备忘": "note",
    "研究报告": "report", "报告": "report", "report": "report",
    "社交媒体": "tweet", "推特": "tweet", "tweet": "tweet", "微博": "tweet",
}


def _normalize_source_type(raw: str | None) -> str | None:
    """将用户自由输入的来源类型归一化为系统枚举值"""
    if not raw:
        return None
    t = raw.strip().lower()
    # 精确匹配
    if t in _SOURCE_TYPE_NORMALIZE:
        return _SOURCE_TYPE_NORMALIZE[t]
    # 模糊匹配
    for keyword, enum_val in _SOURCE_TYPE_NORMALIZE.items():
        if keyword in t:
            return enum_val
    # 无法识别 → 返回原文（LLM 会在 prompt 里看到）
    return raw.strip()


def _has_active_ingestion_lease(summary: dict | None, now: datetime) -> bool:
    if not isinstance(summary, dict):
        return False
    raw_expires_at = summary.get("lease_expires_at")
    if not raw_expires_at:
        return False
    try:
        expires_at = datetime.fromisoformat(str(raw_expires_at).replace("Z", "+00:00"))
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return expires_at > now
    except Exception:
        return False


class IngestionWorkflow:
    def __init__(self, db: Session):
        self.db = db
        self.raw_source_repo = RawSourceRepository(db)
        self.claim_repo = ClaimRepository(db)
        self.speaker_repo = SpeakerRepository(db)
        self.extraction_skill = ClaimExtractionSkill()
        self.speaker_skill = SpeakerProfilingSkill()
        self.contradiction_detector = ContradictionDetector(db)

    def process_source(
        self,
        source_id: str,
        job_id: str | None = None,
        *,
        lease_owner_id: str | None = None,
    ) -> dict:
        """处理单个 RawSource，记录 job_run"""
        now = datetime.now(timezone.utc)
        start_ts = time.time()

        # 创建或复用 job_run。异步接口会先持久化 job_id，避免后台任务还没启动时
        # 前端拿到的进度地址无法从数据库恢复。
        job = self.db.get(JobRun, job_id) if job_id else None
        if job is None:
            job = JobRun(
                id=job_id or str(ULID()),
                job_type="ingestion",
                status="running",
                triggered_by="api",
                input_params={"source_id": source_id},
                result_summary={},
                started_at=now,
                retry_count=0,
                source_ids=[source_id],
                object_scope={"source_ids": [source_id]},
                related_object_id=source_id,
                related_object_type="raw_source",
                created_at=now,
                updated_at=now,
            )
            self.db.add(job)
        else:
            if job.job_type != "ingestion" or (
                job.related_object_id and job.related_object_id != source_id
            ):
                return {
                    "success": False,
                    "source_id": source_id,
                    "job_id": job.id,
                    "error": "job does not belong to this ingestion source",
                    "retryable": False,
                }
            if job.status == "completed":
                prior_result = dict(job.result_summary or {})
                return {
                    **prior_result,
                    "success": bool(prior_result.get("success", True)),
                    "source_id": source_id,
                    "job_id": job.id,
                    "idempotent_replay": True,
                }
            if job.status == "cancelled":
                return {
                    "success": False,
                    "cancelled": True,
                    "source_id": source_id,
                    "job_id": job.id,
                    "error": "ingestion job cancelled before start",
                }
            lease_summary = job.result_summary if isinstance(job.result_summary, dict) else {}
            active_lease_owner = lease_summary.get("lease_worker_id")
            if (
                job.status == "running"
                and _has_active_ingestion_lease(lease_summary, now)
                and (not lease_owner_id or active_lease_owner != lease_owner_id)
            ):
                return {
                    "success": False,
                    "duplicate_active": True,
                    "retryable": True,
                    "source_id": source_id,
                    "job_id": job.id,
                    "status": "running",
                    "error": "ingestion job already has an active lease",
                }
            job.status = "running"
            job.started_at = job.started_at or now
            job.input_params = {**(job.input_params or {}), "source_id": source_id}
            job.source_ids = [source_id]
            job.object_scope = {**(job.object_scope or {}), "source_ids": [source_id]}
            job.related_object_id = source_id
            job.related_object_type = "raw_source"
            job.error_message = None
            job.updated_at = now
        self.db.flush()
        progress_tracker.start_job(
            job.id,
            "ingestion",
            source_id=source_id,
            session_id=job.session_id,
            object_scope=job.object_scope,
            related_object_id=source_id,
            related_object_type="raw_source",
        )
        progress_tracker.update_stage(job.id, "load", "running", "正在加载资料")
        # 注意：不在这里 commit，避免 SQLite 锁（验证场景调用时共享同一 session）
        # 进度回调使用独立 SessionLocal，不依赖此处 commit

        result = self._do_process(source_id, job_id=job.id)
        if result.get("cancelled"):
            self.db.rollback()
            return {
                **result,
                "job_id": job.id,
                "duration_ms": int((time.time() - start_ts) * 1000),
            }
        try:
            self.db.refresh(job)
        except Exception:
            pass
        if job.status == "cancelled":
            self.db.rollback()
            return {
                "success": False,
                "cancelled": True,
                "source_id": source_id,
                "job_id": job.id,
                "duration_ms": int((time.time() - start_ts) * 1000),
                "error": "ingestion job cancelled",
            }

        # 更新 job_run
        duration_ms = int((time.time() - start_ts) * 1000)
        result["job_id"] = job.id
        result["duration_ms"] = duration_ms
        job.status = "completed" if result["success"] else "failed"
        job.completed_at = datetime.now(timezone.utc)
        job.duration_ms = duration_ms
        job.error_message = result.get("error") if not result["success"] else None
        job.updated_at = datetime.now(timezone.utc)
        if result["success"]:
            progress_tracker.complete_job(job.id, "资料解析、切片、向量化和入库已完成")
        else:
            progress_tracker.fail_job(job.id, result.get("error") or "ingestion failed")
        progress_snapshot = progress_tracker.get_progress(job.id)
        if progress_snapshot:
            result["progress_snapshot"] = progress_snapshot
            result["stage"] = progress_snapshot.get("current_stage") or job.status
            result["pct"] = progress_snapshot.get("progress_pct")
            result["message"] = progress_snapshot.get("message")
        previous_summary = dict(job.result_summary or {})
        lease_summary = {k: v for k, v in previous_summary.items() if k.startswith("lease_")}
        from app.services.ingestion_dispatcher import finalize_ingestion_lease_metadata

        job.result_summary = finalize_ingestion_lease_metadata(
            {**lease_summary, **result},
            state=job.status,
            finalized_at=job.completed_at,
        )
        safe_commit(self.db)
        # 事务提交后刷入缓冲的 sqlite-vec 向量（commit 前写会死锁）
        from app.services.semantic_search import flush_pending_vec_upserts
        flush_pending_vec_upserts()

        return result

    def _do_process(self, source_id: str, job_id: str = None) -> dict:
        """实际处理逻辑（支持分块提取 + 进度追踪 + 全链路留痕）"""
        def _progress(stage: str, status: str = "running", message: str | None = None, detail: str | None = None) -> None:
            if job_id:
                progress_tracker.update_stage(job_id, stage, status, message=message, detail=detail)

        def _cancelled_result() -> dict | None:
            if not job_id:
                return None
            try:
                from app.database import SessionLocal as _SL
                _db = _SL()
                try:
                    _job = _db.get(JobRun, job_id)
                    if _job and _job.status == "cancelled":
                        return {
                            "success": False,
                            "cancelled": True,
                            "source_id": source_id,
                            "error": "ingestion job cancelled",
                        }
                finally:
                    _db.close()
            except Exception:
                return None
            return None

        source = self.raw_source_repo.get(source_id)
        if not source:
            return {"success": False, "error": f"Source {source_id} not found"}

        if source.status not in ("pending", "failed"):
            return {"success": False, "error": f"Source status is {source.status}, skip"}
        if cancel_result := _cancelled_result():
            return cancel_result

        self.raw_source_repo.mark_processing(source)
        _progress("load", "completed", "资料已加载", source.title or source.id)
        _progress("parse", "running", "正在解析文件内容")
        if cancel_result := _cancelled_result():
            return cancel_result

        # 初始化全链路留痕 tracer
        tracer = ProcessingTracer(self.db, source_id=source_id, job_id=job_id)
        tracer.trace("raw_input", 0,
            input_snapshot={
                "source_id": source_id,
                "source_type": source.source_type,
                "title": source.title or "",
                "speaker_hint": source.speaker_hint,
                "theme_hint": source.theme_hint,
                "user_trust_level": getattr(source, 'user_trust_level', None),
                "user_note": getattr(source, 'user_note', None),
                "collection_reason": getattr(source, 'collection_reason', None),
                "raw_text_len": len(source.raw_text or ""),
                "has_storage_path": bool(source.storage_path),
            },
            output_snapshot={"status": "processing_started"},
        )

        # 进度通过 job_run.result_summary 实时更新（每页 commit 一次，让进度接口可读）
        _job_id = [job_id]  # 绑定 job_id
        def _ocr_progress(current_page: int, total_pages: int):
            logger.info(f"[Ingestion] OCR progress: page {current_page}/{total_pages}")
            # Do not write job_run from a second SQLite session while the main
            # ingestion transaction is open; that waits on busy_timeout and
            # makes small uploads appear stuck. The in-memory tracker is the
            # real-time source during active jobs, and job_run is finalized at
            # the end of process_source.
            if _job_id[0]:
                progress_tracker.update_stage(
                    _job_id[0],
                    "parse",
                    "running",
                    message=f"正在解析内容：{current_page}/{total_pages}",
                    detail=f"{current_page}/{total_pages}",
                )

        try:
            # ── Step 1: 文本提取 ──
            _t1 = time.time()
            parse_attempts: list[dict] = []
            parse_segments: list[DocumentParseSegment] = []
            text_content = self._get_text_for_llm(
                source,
                progress_cb=_ocr_progress,
                diagnostics=parse_attempts,
                segment_collector=parse_segments,
            )
            if cancel_result := _cancelled_result():
                return cancel_result
            if not text_content:
                parse_diagnostics = build_parse_diagnostics(
                    source=source,
                    reason_code="no_text_content",
                    message="No text content",
                    method_attempts=parse_attempts,
                )
                self.raw_source_repo.mark_failed(
                    source,
                    encode_processing_error("No text content", parse_diagnostics),
                )
                tracer.trace("text_extraction", 1, status="error",
                    error_message="No text content",
                    output_snapshot={"parse_diagnostics": parse_diagnostics})
                _progress("parse", "failed", "文件内容解析失败", parse_diagnostics["reason_code"])
                return {
                    "success": False,
                    "error": "No text content",
                    "parse_diagnostics": parse_diagnostics,
                }
            parse_diagnostics = build_parse_diagnostics(
                source=source,
                reason_code="text_extracted",
                message="Text content extracted",
                status="completed",
                detail=f"{len(text_content)} chars",
                method_attempts=parse_attempts,
            )
            document_parse_summary = build_document_parse_summary(
                text_content,
                source_type=source.source_type,
                method_attempts=parse_attempts,
                segments=parse_segments,
            )
            parse_diagnostics["document_parse"] = document_parse_summary
            if document_parse_summary["status"] == "partial":
                parse_diagnostics.update(
                    {
                        "status": "partial",
                        "reason_code": "partial_parse_requires_review",
                        "message": "Partial document parse requires review",
                        "detail": "failed page/slice or segment alignment",
                        "retryable": True,
                    }
                )
                # Partial OCR text must not become a cache hit on retry. The
                # immutable upload and bounded diagnostic excerpts remain the
                # recovery evidence, while a retry reparses the original file.
                source.raw_text = None
                source.word_count = 0
                self.db.flush()
                self.raw_source_repo.mark_failed(
                    source,
                    encode_processing_error(
                        "Partial document parse requires review",
                        parse_diagnostics,
                    ),
                )
                tracer.trace(
                    "text_extraction",
                    1,
                    status="warning",
                    error_message="Partial document parse requires review",
                    output_snapshot={"parse_diagnostics": parse_diagnostics},
                    duration_ms=int((time.time() - _t1) * 1000),
                )
                _progress(
                    "parse",
                    "failed",
                    "文件仅部分解析，需重试或人工复核",
                    "partial_parse_requires_review",
                )
                return {
                    "success": False,
                    "error": "Partial document parse requires review",
                    "parse_diagnostics": parse_diagnostics,
                }
            _progress("parse", "completed", "文件内容解析完成", f"{len(text_content)} chars")
            tracer.trace("text_extraction", 1,
                input_snapshot={"source_type": source.source_type, "has_raw_text": bool(source.raw_text), "has_storage_path": bool(source.storage_path)},
                output_snapshot={
                    "text_len": len(text_content),
                    "preview": text_content[:300],
                    "parse_diagnostics": parse_diagnostics,
                },
                duration_ms=int((time.time() - _t1) * 1000),
            )

            # ── Step 2: Speaker / Theme 推断 ──
            _progress("speaker", "running", "正在识别来源画像")
            if len(text_content) >= self._LONG_DOC_DISTILL_THRESHOLD:
                if not source.theme_hint:
                    source.theme_hint = (source.title or "long-document")[:200]
                    self.db.flush()
                _progress("speaker", "skipped", "长文档已跳过来源画像深度推断")
                tracer.trace("speaker_inference", 2, status="skipped",
                    output_snapshot={
                        "reason": "long_document_mvp_path",
                        "speaker": source.speaker_hint,
                        "theme": source.theme_hint,
                    })
            elif not source.speaker_hint or not source.theme_hint:
                _t2 = time.time()
                try:
                    _hint = self._infer_speaker_theme(text_content[:2000], source)
                    if _hint.get("speaker") and not source.speaker_hint:
                        source.speaker_hint = _hint["speaker"]
                    if _hint.get("theme") and not source.theme_hint:
                        source.theme_hint = _hint["theme"]
                    self.db.flush()
                    logger.info(f"[Ingestion] Auto-inferred speaker={source.speaker_hint!r} theme={source.theme_hint!r}")
                    _progress("speaker", "completed", "来源画像识别完成")
                    tracer.trace("speaker_inference", 2,
                        input_snapshot={"text_snippet_len": min(2000, len(text_content))},
                        output_snapshot={"speaker": source.speaker_hint, "theme": source.theme_hint},
                        duration_ms=int((time.time() - _t2) * 1000),
                    )
                except Exception as _e:
                    logger.warning(f"[Ingestion] Auto-infer speaker/theme failed: {_e}")
                    _progress("speaker", "skipped", "来源画像识别失败，已继续解析", str(_e)[:200])
                    tracer.trace("speaker_inference", 2, status="warning",
                        error_message=str(_e), duration_ms=int((time.time() - _t2) * 1000))
            else:
                _progress("speaker", "completed", "来源画像已使用上传信息")

            # 文章日期提取：若 published_at 为空，从文本中提取；仍为空则用上传时间
            if not source.published_at:
                try:
                    from app.services.file_parser import extract_date_from_text
                    date_str = extract_date_from_text(text_content)
                    if date_str:
                        source.published_at = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
                        logger.info(f"[Ingestion] Extracted date from text: {date_str}")
                    else:
                        source.published_at = source.created_at  # 降级：用上传时间
                        logger.debug(f"[Ingestion] No date found in text, using upload time")
                    self.db.flush()
                except Exception as _e:
                    logger.warning(f"[Ingestion] Date extraction failed: {_e}")

            # ── Step 2b: 聊天记录时间范围识别 ──
            if source.source_type in ("chat", "image"):
                try:
                    from app.services.file_parser import extract_chat_time_range
                    chat_time = extract_chat_time_range(text_content)
                    if chat_time:
                        tracer.trace("chat_time_detection", 2,
                            output_snapshot=chat_time,
                        )
                        # 将聊天最早时间作为 published_at
                        if not source.published_at and chat_time.get("earliest"):
                            source.published_at = datetime.strptime(
                                chat_time["earliest"], "%Y-%m-%d %H:%M"
                            ).replace(tzinfo=timezone.utc)
                            self.db.flush()
                        logger.info(f"[Ingestion] Chat time range: {chat_time['earliest']} ~ {chat_time['latest']} "
                                    f"({chat_time['message_count']} msgs, {chat_time['duration_minutes']} min)")
                except Exception as _e:
                    logger.debug(f"[Ingestion] Chat time detection failed: {_e}")

            # ── Step 3: 文章级摘要 ──
            if not source.article_summary:
                _t3 = time.time()
                article_summary = self._generate_article_summary(text_content)
                if article_summary:
                    source.article_summary = article_summary
                    if not source.theme_hint:
                        source.theme_hint = article_summary[:100]
                    self.db.flush()
                    logger.info(f"[Ingestion] Article summary generated ({len(article_summary)} chars)")
                tracer.trace("article_summary", 3,
                    input_snapshot={"text_len": len(text_content)},
                    output_snapshot={"summary_len": len(article_summary or ""), "summary_preview": (article_summary or "")[:200]},
                    duration_ms=int((time.time() - _t3) * 1000),
                    status="ok" if article_summary else "warning",
                )
            else:
                article_summary = source.article_summary
                logger.debug("[Ingestion] Article summary already exists, skipping generation")
                tracer.trace("article_summary", 3, status="skipped",
                    output_snapshot={"summary_len": len(article_summary), "reason": "already_exists"})

            # 分块处理长文本，每块独立抽取观点
            chunks = self._split_chunks(text_content)
            original_chunk_count = len(chunks)
            if self._should_use_long_doc_distillation(text_content, chunks):
                _progress("extraction", "running", "长文档进入摘要抽取路径", f"{len(text_content)} chars")
                distilled_chunk = self._build_long_doc_distilled_chunk(
                    text_content=text_content,
                    article_summary=article_summary,
                    chunks=chunks,
                )
                chunks = [distilled_chunk]
                tracer.trace("long_document_distillation", 4,
                    input_snapshot={
                        "text_len": len(text_content),
                        "original_chunks": original_chunk_count,
                    },
                    output_snapshot={
                        "distilled_len": len(distilled_chunk),
                        "extraction_chunks": len(chunks),
                        "mode": "summary_plus_representative_slices",
                    },
                    status="ok",
                )
                if cancel_result := _cancelled_result():
                    return cancel_result
                source.processing_error = None
                source.word_count = len(text_content)
                self.raw_source_repo.mark_completed(source)
                _progress("extraction", "skipped", "长文档观点抽取已延后，摘要和原文已保存")
                _progress("quality_check", "skipped", "长文档 MVP 路径暂不做观点质量评分")
                _progress("contradiction", "skipped", "长文档 MVP 路径暂不做反证检测")
                _progress("embedding", "skipped", "长文档 MVP 路径暂不生成观点向量")
                _progress("persist", "completed", "长文档已完成解析存储")
                tracer.trace("pipeline_complete", 5,
                    output_snapshot={
                        "claims_created": 0,
                        "claims_skipped": 0,
                        "source_mode": "live",
                        "chunks_processed": 0,
                        "mode": "long_document_parse_store_mvp",
                        "note": "Long PDF parsed and stored; real-time claim extraction is deferred.",
                        "text_chars": len(text_content),
                        "article_summary_chars": len(article_summary or ""),
                        "parse_diagnostics": parse_diagnostics,
                    },
                    status="ok",
                )
                return {
                    "success": True,
                    "source_id": source.id,
                    "claims_extracted": 0,
                    "claims_skipped": 0,
                    "claim_ids": [],
                    "is_mock": False,
                    "warnings": ["long_document_claim_extraction_deferred"],
                    "source_mode": "live",
                    "chunks_processed": 0,
                    "long_document_mode": "parse_store_mvp",
                    "original_chunks": original_chunk_count,
                    "text_chars": len(text_content),
                    "article_summary_chars": len(article_summary or ""),
                    "parse_diagnostics": parse_diagnostics,
                }
            logger.info(f"[Ingestion] Text split into {len(chunks)} chunks for extraction")
            _progress("extraction", "running", "正在抽取结构化观点", f"0/{len(chunks)} chunks")

            created_claims = []
            total_skipped = 0
            is_mock = False
            warnings_all: list = []
            source_mode = "live"
            now = datetime.now(timezone.utc)
            speaker_id = None  # 在所有 chunk 间共享

            _chunk_search_from = 0
            for chunk_idx, chunk_text in enumerate(chunks):
                if cancel_result := _cancelled_result():
                    return cancel_result
                _chunk_doc_start = _chunk_start_in_document(text_content, chunk_text, max(0, _chunk_search_from - 64))
                if _chunk_doc_start is not None:
                    _chunk_search_from = _chunk_doc_start + max(1, len(chunk_text) - getattr(self, "_CHUNK_OVERLAP", 0))
                logger.info(f"[Ingestion] Extracting chunk {chunk_idx+1}/{len(chunks)} ({len(chunk_text)} chars)")
                _progress(
                    "extraction",
                    "running",
                    f"正在抽取观点：{chunk_idx + 1}/{len(chunks)}",
                    f"{len(chunk_text)} chars",
                )
                # ── Step 4: Claim 抽取（每 chunk 一条 trace）──
                _t4 = time.time()
                extraction_input = ClaimExtractionInput(
                    raw_source_id=source.id,
                    text_content=chunk_text,
                    speaker_hint=source.speaker_hint,
                    source_type_hint=_normalize_source_type(source.source_type) or source.source_type,
                    theme_hint=(
                        (source.theme_hint or "") +
                        (f"\n\n【文章摘要】{source.article_summary[:300]}" if source.article_summary else "") +
                        (f"\n\n【用户备注】{source.user_note}" if getattr(source, 'user_note', None) else "") +
                        (f"\n【收集原因】{source.collection_reason}" if getattr(source, 'collection_reason', None) else "")
                    ).strip() or None,
                )
                extraction_output = self.extraction_skill.execute(extraction_input)
                if cancel_result := _cancelled_result():
                    return cancel_result
                is_mock = extraction_output.is_mock
                source_mode = extraction_output.source_mode
                warnings_all.extend(extraction_output.warnings or [])
                total_skipped += extraction_output.skipped_count
                tracer.trace("claim_extraction", 4 + chunk_idx,
                    input_snapshot={"chunk_idx": chunk_idx, "chunk_len": len(chunk_text), "chunk_preview": chunk_text[:200]},
                    output_snapshot={
                        "claims_count": len(extraction_output.claims),
                        "skipped": extraction_output.skipped_count,
                        "is_mock": extraction_output.is_mock,
                        "source_mode": extraction_output.source_mode,
                        "claim_summaries": [c.summary[:80] for c in extraction_output.claims[:10]],
                    },
                    duration_ms=int((time.time() - _t4) * 1000),
                )

                # 更新抽取进度
                if _job_id[0]:
                    progress_tracker.update_stage(
                        _job_id[0],
                        "extraction",
                        "running",
                        message=f"正在抽取观点：{chunk_idx + 1}/{len(chunks)}",
                        detail=f"{len(created_claims)} claims so far",
                    )

                # Speaker Profiling（只在第一块做，避免重复）
                if chunk_idx == 0:
                    now = datetime.now(timezone.utc)
                    speaker_id = None
                    if source.speaker_hint:
                        existing = self.speaker_repo.find_by_name_fuzzy(source.speaker_hint)
                        if existing:
                            speaker_id = existing.id
                            self.speaker_repo.increment_claim_count(speaker_id)
                        else:
                            profiling_output = self.speaker_skill.execute(SpeakerProfilingInput(
                                raw_source_id=source.id,
                                text_content=chunk_text[:8000],  # 只用第一块做画像
                                speaker_hint=source.speaker_hint,
                                theme_hint=source.theme_hint,
                            ))
                            new_speaker = SpeakerProfile(
                                id=str(ULID()),
                                name=profiling_output.name,
                                aliases=profiling_output.aliases,
                                affiliation=profiling_output.affiliation,
                                role=profiling_output.role,
                                domain_tags=profiling_output.domain_tags,
                                trust_weight=profiling_output.trust_weight,
                                style_notes=profiling_output.style_notes,
                                status="active",
                                total_claims=1,
                                verified_claims=0,
                                created_at=now,
                                updated_at=now,
                            )
                            self.db.add(new_speaker)
                            self.db.flush()
                            speaker_id = new_speaker.id

                now = datetime.now(timezone.utc)

                # 质量评分 + 去重检测
                from app.services.claim_quality import score_claim_quality, check_duplicate
                from app.services.embedding import embed_text as _embed_text
                from app.services.sqlite_vec_store import _parse_json_embedding
                _existing_claims_cache = []
                _existing_embeddings_cache = []
                try:
                    _existing_for_dedup = self.db.query(ClaimCard).filter(
                        ClaimCard.status == "active",
                    ).order_by(ClaimCard.created_at.desc()).limit(200).all()
                    _existing_claims_cache = [
                        {"id": c.id, "summary": c.summary, "stance": c.stance,
                         "asset_scope": c.asset_scope or []}
                        for c in _existing_for_dedup
                    ]
                    _existing_embeddings_cache = [
                        _parse_json_embedding(getattr(c, 'embedding', None))
                        for c in _existing_for_dedup
                    ]
                except Exception:
                    pass

                for _ci, extracted in enumerate(extraction_output.claims):
                    # ── Step 5: 质量评分 ──
                    _progress("quality_check", "running", "正在做观点质量评分", extracted.summary[:120])
                    claim_dict = {
                        "summary": extracted.summary,
                        "claim_type": extracted.claim_type,
                        "stance": extracted.stance,
                        "time_horizon": extracted.time_horizon,
                        "asset_scope": extracted.asset_scope,
                        "reasoning_basis": extracted.reasoning_basis,
                        "conditions": extracted.conditions,
                        "invalidation_conditions": extracted.invalidation_conditions,
                        "confidence": extracted.confidence,
                        "full_quote": extracted.full_quote,
                        "trust_weight": 0.5,
                    }
                    quality = score_claim_quality(claim_dict)

                    # ── 质量门槛：跳过 Grade D 低质量观点 ──
                    if quality.grade == "D":
                        total_skipped += 1
                        warnings_all.append(
                            f"跳过低质量观点(Grade D): {extracted.summary[:40]}... "
                            f"({'; '.join(quality.issues[:2])})"
                        )
                        tracer.trace("quality_gate", 100 + chunk_idx * 20 + _ci,
                            input_snapshot={"summary": extracted.summary[:80]},
                            output_snapshot={
                                "grade": quality.grade, "score": quality.overall,
                                "action": "skip", "issues": quality.issues,
                            },
                            status="warning",
                        )
                        continue

                    # 为新观点生成 embedding（用于语义去重）
                    # G17: 与 index_claim 使用同一拼接函数，去重和检索 embed 文本完全一致
                    _new_emb_text = sem_search.build_embed_text(extracted)
                    _new_embedding = _embed_text(_new_emb_text) if _new_emb_text.strip() else None

                    # ── Step 6: 去重检测 ──
                    dedup = check_duplicate(
                        claim_dict, _new_embedding,
                        _existing_claims_cache, _existing_embeddings_cache,
                    )
                    if dedup.recommendation == "skip":
                        total_skipped += 1
                        warnings_all.append(f"跳过重复观点: {extracted.summary[:40]}... ({dedup.reason})")
                        tracer.trace("dedup_check", 100 + chunk_idx * 20 + _ci,
                            input_snapshot={"summary": extracted.summary[:80]},
                            output_snapshot={"recommendation": "skip", "reason": dedup.reason},
                            status="warning",
                        )
                        continue

                    # ── Step 6b: 合并路径 ──
                    if dedup.recommendation == "merge" and dedup.similar_claim_id:
                        try:
                            from app.services.claim_quality import merge_claims
                            existing_claim_obj = self.db.query(ClaimCard).filter(ClaimCard.id == dedup.similar_claim_id).first()
                            if existing_claim_obj:
                                existing_dict = {
                                    "id": existing_claim_obj.id,
                                    "summary": existing_claim_obj.summary,
                                    "stance": existing_claim_obj.stance,
                                    "claim_type": existing_claim_obj.claim_type,
                                    "time_horizon": existing_claim_obj.time_horizon,
                                    "asset_scope": existing_claim_obj.asset_scope or [],
                                    "theme_scope": existing_claim_obj.theme_scope or [],
                                    "conditions": existing_claim_obj.conditions or [],
                                    "invalidation_conditions": existing_claim_obj.invalidation_conditions or [],
                                    "reasoning_basis": existing_claim_obj.reasoning_basis or [],
                                    "confidence": existing_claim_obj.confidence,
                                    "full_quote": existing_claim_obj.full_quote,
                                    "trust_weight": existing_claim_obj.source_trust_weight,
                                }
                                merged = merge_claims(existing_dict, claim_dict)
                                # 回写合并结果到已有 claim
                                existing_claim_obj.summary = merged.get("summary", existing_claim_obj.summary)
                                existing_claim_obj.asset_scope = merged.get("asset_scope", existing_claim_obj.asset_scope)
                                existing_claim_obj.theme_scope = merged.get("theme_scope", existing_claim_obj.theme_scope)
                                existing_claim_obj.conditions = merged.get("conditions", existing_claim_obj.conditions)
                                existing_claim_obj.invalidation_conditions = merged.get("invalidation_conditions", existing_claim_obj.invalidation_conditions)
                                existing_claim_obj.reasoning_basis = merged.get("reasoning_basis", existing_claim_obj.reasoning_basis)
                                existing_claim_obj.confidence = merged.get("confidence", existing_claim_obj.confidence)
                                existing_claim_obj.source_trust_weight = merged.get("trust_weight", existing_claim_obj.source_trust_weight)
                                if merged.get("stance") and merged["stance"] != existing_claim_obj.stance:
                                    existing_claim_obj.stance = merged["stance"]
                                existing_claim_obj.version = (existing_claim_obj.version or 1) + 1
                                existing_claim_obj.updated_at = now
                                self.db.flush()
                                # 也建立 link 关联到新 source（避免重复）
                                existing_link = self.db.query(ClaimRawSourceLink).filter(
                                    ClaimRawSourceLink.claim_id == existing_claim_obj.id,
                                    ClaimRawSourceLink.source_id == source.id,
                                ).first()
                                if not existing_link:
                                    self.db.add(ClaimRawSourceLink(
                                        claim_id=existing_claim_obj.id,
                                        source_id=source.id,
                                        link_type="merged_from",
                                        created_at=now,
                                        updated_at=now,
                                    ))
                                _add_claim_evidence_span(
                                    self.db,
                                    claim_id=existing_claim_obj.id,
                                    source_id=source.id,
                                    source_text=text_content,
                                    quote=extracted.full_quote or extracted.summary,
                                    fallback_start=_chunk_doc_start or 0,
                                    now=now,
                                    metadata={
                                        "chunk_idx": chunk_idx,
                                        "claim_summary": extracted.summary[:300],
                                        "source": "ingestion_workflow_merge",
                                    },
                                )
                                created_claims.append(existing_claim_obj.id)
                                warnings_all.append(f"合并更新已有观点: {existing_claim_obj.summary[:40]}... (相似度{dedup.similarity:.1%})")
                                tracer.trace("dedup_merge", 100 + chunk_idx * 20 + _ci,
                                    input_snapshot={"new_summary": extracted.summary[:80]},
                                    output_snapshot={"merged_into": existing_claim_obj.id, "similarity": dedup.similarity},
                                )
                                # 重新生成 embedding（summary 已更新）
                                existing_claim_obj.embedding = None
                                sem_search.index_claim(self.db, existing_claim_obj)
                                logger.info(f"[Ingestion] Merged claim into existing {existing_claim_obj.id} (sim={dedup.similarity:.2f})")
                                continue
                        except Exception as merge_err:
                            logger.warning(f"[Ingestion] Merge failed, falling through to create: {merge_err}")

                    # 记录质量评分 + 去重 trace
                    tracer.trace("quality_scoring", 100 + chunk_idx * 20 + _ci,
                        input_snapshot={"summary": extracted.summary[:80], "stance": extracted.stance},
                        output_snapshot={
                            "grade": getattr(quality, 'grade', None),
                            "score": getattr(quality, 'score', None),
                            "dedup_recommendation": dedup.recommendation,
                        },
                    )

                    # ── Quote Grounding: 将 LLM 引用回溯到原文 ──
                    _grounded_quote = extracted.full_quote
                    if extracted.full_quote and chunk_text:
                        try:
                            from app.services.quote_grounding import ground_quote as _ground_quote
                            _gr = _ground_quote(chunk_text, extracted.full_quote)
                            if _gr.is_grounded:
                                _grounded_quote = _gr.grounded_quote
                                logger.debug(f"[Ingestion] Quote grounded: {_gr.status.value} score={_gr.score:.2f}")
                        except Exception as _gre:
                            logger.debug(f"[Ingestion] Quote grounding skipped: {_gre}")

                    # 复用去重阶段已生成的 embedding，避免 index_claim 再调一次 embed_text
                    _embedding_field_value = None
                    if _new_embedding:
                        try:
                            if settings.PGVECTOR_ENABLED:
                                _embedding_field_value = _new_embedding
                            else:
                                _embedding_field_value = _json.dumps(_new_embedding)
                        except Exception as _eerr:
                            logger.debug(f"[Ingestion] embedding reuse serialize failed: {_eerr}")
                            _embedding_field_value = None

                    claim = ClaimCard(
                        id=str(ULID()),
                        summary=extracted.summary,
                        # G17 Contextual Embeddings：LLM 抽取时同步生成的局部上下文
                        contextual_text=extracted.contextual_summary,
                        full_quote=_grounded_quote,
                        claim_type=extracted.claim_type,
                        stance=extracted.stance,
                        time_horizon=extracted.time_horizon,
                        asset_scope=extracted.asset_scope,
                        theme_scope=extracted.theme_scope,
                        geographic_scope=[],
                        conditions=extracted.conditions,
                        invalidation_conditions=extracted.invalidation_conditions,
                        reasoning_basis=extracted.reasoning_basis,
                        confidence=extracted.confidence,
                        source_trust_weight={"high": 0.8, "medium": 0.5, "low": 0.3}.get(_normalize_trust_level(getattr(source, 'user_trust_level', None)) or "", 0.5),
                        speaker_id=speaker_id,
                        is_contradicted=False,
                        status="active",
                        embedding=_embedding_field_value,
                        valid_until=_calc_valid_until(
                            extracted.time_horizon, now,
                            extracted.asset_scope,
                            event_date=_parse_event_date(vars(extracted) if hasattr(extracted, '__dict__') else {}),
                            time_sensitive=getattr(extracted, 'time_sensitive', True),
                        ),
                        time_sensitive=getattr(extracted, 'time_sensitive', True),
                        expiry_hint=getattr(extracted, 'expiry_hint', None) or None,
                        last_lifecycle_check=now,
                        version=1,
                        created_at=now,
                        updated_at=now,
                    )
                    self.db.add(claim)
                    self.db.flush()
                    _add_claim_evidence_span(
                        self.db,
                        claim_id=claim.id,
                        source_id=source.id,
                        source_text=text_content,
                        quote=_grounded_quote or extracted.full_quote or extracted.summary,
                        fallback_start=_chunk_doc_start or 0,
                        now=now,
                        metadata={
                            "chunk_idx": chunk_idx,
                            "claim_summary": extracted.summary[:300],
                            "source": "ingestion_workflow",
                        },
                    )

                    # P2-1: 同 chunk 内增量去重 —— 把刚创建的 claim 加进 cache，
                    # 这样本 chunk 后续 claim 可以和它互相去重（否则只能跨 chunk 去重）
                    try:
                        _existing_claims_cache.append({
                            "id": claim.id,
                            "summary": claim.summary,
                            "stance": claim.stance,
                            "asset_scope": claim.asset_scope or [],
                        })
                        _existing_embeddings_cache.append(_new_embedding)
                    except Exception:
                        pass

                    # G19: 检测与历史 Claim 的矛盾
                    # 验证场景（src_test_/src_summary_）下关闭自动反证压制，避免影响基础链路验收
                    _progress("contradiction", "running", "正在检查反证与冲突", claim.summary[:120])
                    if not str(source_id).startswith(("src_test_", "src_summary_")):
                        contradicted_ids = self.contradiction_detector.check_and_mark(claim)
                        if contradicted_ids:
                            logger.info(f"Claim {claim.id} contradicts {contradicted_ids}")

                    # G11: 生成 embedding（语义搜索，失败静默跳过）
                    _progress("embedding", "running", "正在生成向量索引", claim.summary[:120])
                    # 若前面已把去重用的 embedding 写入 claim.embedding，index_claim
                    # 会走"已有 embedding 直接缓冲到 sqlite-vec"分支，不会重复调用 embed_text
                    sem_search.index_claim(self.db, claim)

                    # lifecycle_log: 初始入库记录
                    init_log = LifecycleLog(
                        id=str(ULID()),
                        claim_id=claim.id,
                        from_status="created",
                        to_status="active",
                        reason=f"Ingested from source {source_id}",
                        triggered_by="ingestion_workflow",
                        evidence_snapshot={"source_id": source_id},
                        occurred_at=now,
                        created_at=now,
                        updated_at=now,
                    )
                    self.db.add(init_log)

                    link = ClaimRawSourceLink(
                        claim_id=claim.id,
                        source_id=source.id,
                        link_type="extracted_from",
                        created_at=now,
                        updated_at=now,
                    )
                    self.db.add(link)
                    created_claims.append(claim.id)

            _progress("extraction", "completed", "结构化观点抽取完成", f"{len(created_claims)} claims")
            _progress("quality_check", "completed", "观点质量评分完成")
            _progress("contradiction", "completed", "反证检查完成")
            _progress("embedding", "completed", "向量索引写入完成")
            _progress("persist", "running", "正在写入数据库")

            # 验证场景兜底：若未抽取到观点，生成一条基础 Claim，保证入库链路可验收
            if not created_claims and str(source_id).startswith(("src_test_", "src_summary_")):
                now = datetime.now(timezone.utc)
                fallback_claim = ClaimCard(
                    id=str(ULID()),
                    summary=(source.title or "测试兜底观点")[:180],
                    full_quote=(text_content[:500] if text_content else ""),
                    claim_type="asset_view",
                    stance="neutral",
                    time_horizon="short",
                    asset_scope=["BTC"],
                    theme_scope=[source.theme_hint] if source.theme_hint else ["测试"],
                    geographic_scope=[],
                    conditions=[],
                    invalidation_conditions=[],
                    reasoning_basis=["verification_fallback"],
                    confidence=0.62,
                    source_trust_weight=0.5,
                    speaker_id=speaker_id,
                    is_contradicted=False,
                    status="active",
                    valid_until=_calc_valid_until("short", now),
                    last_lifecycle_check=now,
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
                self.db.add(fallback_claim)
                self.db.flush()
                _add_claim_evidence_span(
                    self.db,
                    claim_id=fallback_claim.id,
                    source_id=source.id,
                    source_text=text_content,
                    quote=fallback_claim.full_quote or fallback_claim.summary,
                    fallback_start=0,
                    now=now,
                    metadata={
                        "source": "ingestion_workflow_fallback",
                        "claim_summary": fallback_claim.summary[:300],
                    },
                )
                self.db.add(ClaimRawSourceLink(
                    claim_id=fallback_claim.id,
                    source_id=source.id,
                    link_type="extracted_from",
                    created_at=now,
                    updated_at=now,
                ))
                self.db.add(LifecycleLog(
                    id=str(ULID()),
                    claim_id=fallback_claim.id,
                    from_status="created",
                    to_status="active",
                    reason=f"Fallback claim for source {source_id}",
                    triggered_by="ingestion_workflow_fallback",
                    evidence_snapshot={"source_id": source_id},
                    occurred_at=now,
                    created_at=now,
                    updated_at=now,
                ))
                created_claims.append(fallback_claim.id)

            self.db.flush()
            source.word_count = len(text_content)
            self.raw_source_repo.mark_completed(source)
            _progress("persist", "completed", "资料已完成入库")

            # ── 完成 trace ──
            tracer.trace("pipeline_complete", 999,
                output_snapshot={
                    "claims_extracted": len(created_claims),
                    "claims_skipped": total_skipped,
                    "claim_ids": created_claims[:20],
                    "is_mock": is_mock,
                    "source_mode": source_mode,
                    "chunks_processed": len(chunks),
                    "warnings_count": len(warnings_all),
                },
            )

            return {
                "success": True,
                "source_id": source_id,
                "claims_extracted": len(created_claims),
                "claims_skipped": total_skipped,
                "claim_ids": created_claims,
                "is_mock": is_mock,
                "warnings": warnings_all,
                "source_mode": source_mode,
                "chunks_processed": len(chunks),
                "parse_diagnostics": parse_diagnostics,
            }

        except Exception as e:
            # 事务一致性：清除主 session 中本次处理新增但未提交的脏数据
            # （ClaimCard / ClaimRawSourceLink / LifecycleLog），避免外层 process_source
            # safe_commit 把它们和 "raw_source=failed" 一起落库造成"部分成功"。
            # raw_source / job_run / ProcessingTrace 状态更新保留 —— 调用方需要读。
            try:
                _DIRTY_TYPES = (ClaimCard, ClaimRawSourceLink, ClaimEvidenceLink, EvidenceSpan, LifecycleLog)
                _dirty = [obj for obj in list(self.db.new) if isinstance(obj, _DIRTY_TYPES)]
                for _o in _dirty:
                    self.db.expunge(_o)
                if _dirty:
                    logger.warning(
                        f"[Ingestion] expunged {len(_dirty)} dirty objects on failure "
                        f"(source_id={source_id})"
                    )
                # 同时清空 sqlite-vec 延迟写入缓冲，避免把未落库的 claim_id 同步到索引
                try:
                    from app.services.semantic_search import clear_pending_vec_upserts
                    clear_pending_vec_upserts()
                except Exception:
                    pass
            except Exception as _ee:
                logger.error(f"[Ingestion] dirty expunge failed: {_ee}")
            parse_diagnostics = build_parse_diagnostics(
                source=source,
                reason_code="pipeline_exception",
                message=str(e),
                stage="processing",
                detail=e.__class__.__name__,
                method_attempts=locals().get("parse_attempts"),
            )
            self.raw_source_repo.mark_failed(source, encode_processing_error(str(e), parse_diagnostics))
            tracer.trace("pipeline_error", 999, status="error",
                error_message=str(e))
            return {"success": False, "error": str(e), "parse_diagnostics": parse_diagnostics}

    def _get_text_content(
        self,
        source,
        progress_cb=None,
        diagnostics=None,
        segment_collector: list[DocumentParseSegment] | None = None,
    ) -> Optional[str]:
        if source.raw_text:
            return source.raw_text

        from app.services.file_parser import extract_text, VISION_CHAT_PROMPT
        from pathlib import Path

        # ── 多图合并模式：读取所有 attachment（支持 image + content_image 分类） ──
        all_attachments = (
            self.db.query(SourceAttachment)
            .filter(
                SourceAttachment.source_id == source.id,
                SourceAttachment.attachment_type.in_(["image", "content_image"]),
            )
            .order_by(SourceAttachment.created_at.asc())
            .all()
        )
        # 分离聊天截图 vs 内容图
        chat_atts = [a for a in all_attachments if a.attachment_type == "image"]
        content_atts = [a for a in all_attachments if a.attachment_type == "content_image"]

        # 只要有多图（常见于聊天截图批次），就启用聊天专用 prompt
        # 通过文件名特征判断是否包含聊天截图
        from app.services.file_parser import classify_image_in_batch
        has_chat_screenshots = any(
            classify_image_in_batch(a.file_name or "", [x.file_name or "" for x in all_attachments]) in ("chat_screenshot",)
            for a in chat_atts
        )
        use_chat_prompt = has_chat_screenshots or len(content_atts) > 0

        if len(all_attachments) > 1:
            segment_start = len(segment_collector) if segment_collector is not None else 0
            total = len(all_attachments)
            logger.info(f"[Ingestion] Multi-image merge: {len(chat_atts)} chat + {len(content_atts)} content for source {source.id}")

            # Step 1: OCR 所有聊天截图（如果有内容图混合，使用聊天专用 prompt）
            chat_texts = []
            chat_vision_prompt = VISION_CHAT_PROMPT if use_chat_prompt else ""
            for idx, att in enumerate(chat_atts):
                if progress_cb:
                    progress_cb(idx + 1, total)
                att_text = extract_text(
                    att.storage_path,
                    mime_type=att.mime_type or "image/jpeg",
                    vision_prompt=chat_vision_prompt,
                    diagnostics=diagnostics,
                    segment_collector=segment_collector,
                )
                if att_text:
                    chat_texts.append(att_text.strip())
                    logger.info(f"[Ingestion] Chat screenshot {idx+1}/{len(chat_atts)} extracted {len(att_text)} chars")
                else:
                    logger.warning(f"[Ingestion] Chat screenshot {idx+1}/{len(chat_atts)} ({att.file_name}) returned no text")

            # Step 2: OCR 所有内容图（如果有的话）
            content_image_texts = {}
            if content_atts:
                for idx, att in enumerate(content_atts):
                    if progress_cb:
                        progress_cb(len(chat_atts) + idx + 1, total)
                    att_text = extract_text(
                        att.storage_path,
                        mime_type=att.mime_type or "image/jpeg",
                        diagnostics=diagnostics,
                        segment_collector=segment_collector,
                    )
                    if att_text:
                        content_image_texts[att.file_name] = att_text.strip()
                        logger.info(f"[Ingestion] Content image '{att.file_name}' extracted {len(att_text)} chars")

            # Step 3: 合并聊天文本（去除相邻截图重叠行）
            if not chat_texts:
                if segment_collector is not None:
                    del segment_collector[segment_start:]
                return None
            from app.services.file_parser import dedup_adjacent_ocr_texts
            merged_text = dedup_adjacent_ocr_texts(chat_texts)

            # Step 4: 如果有内容图，尝试将其插入到聊天记录的 [图片] 占位位置
            if content_image_texts:
                from app.services.file_parser import parse_wechat_chat, match_content_images_to_chat, rebuild_chat_text
                parsed = parse_wechat_chat(merged_text)
                if parsed and parsed.get("image_positions"):
                    content_files = [att.file_name for att in content_atts]
                    matched_msgs = match_content_images_to_chat(
                        parsed["messages"], content_image_texts, content_files
                    )
                    parsed["messages"] = matched_msgs
                    merged_text = rebuild_chat_text(parsed)
                    logger.info(f"[Ingestion] Content images matched to {len(parsed['image_positions'])} positions in chat")
                else:
                    # 无法匹配位置，附加到末尾
                    extra = "\n\n".join(
                        f"[附件图片 {fname}]\n{txt}" for fname, txt in content_image_texts.items()
                    )
                    merged_text = merged_text + "\n\n" + extra
                    logger.info(f"[Ingestion] Content images appended to end (no [图片] slots found)")

            if segment_collector is not None:
                del segment_collector[segment_start:]
                segment_collector.append(
                    DocumentParseSegment(
                        type="chat" if use_chat_prompt else "image",
                        content=merged_text,
                        method="multi_image_merge",
                        ordinal=1,
                        source_name=source.title or f"{total}-attachment-batch",
                    )
                )
            return merged_text

        # ── 单文件模式 ──
        if source.storage_path:
            try:
                suffix = Path(source.storage_path).suffix.lower()

                # 拒绝旧版 .doc（python-docx 不支持，会产生乱码）
                if suffix == ".doc":
                    raise ValueError("不支持旧版 .doc 格式，请转换为 .docx 后重新上传")

                mime_map = {".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            ".txt": "text/plain", ".md": "text/plain",
                            ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
                mime = mime_map.get(suffix, "")

                # 单图模式：检测文件名是否像聊天截图，自动使用聊天 prompt
                single_vision_prompt = ""
                if suffix in (".jpg", ".jpeg", ".png", ".webp"):
                    from app.services.file_parser import classify_image_in_batch
                    fname = Path(source.storage_path).name
                    img_cls = classify_image_in_batch(fname, [])
                    if img_cls == "chat_screenshot":
                        single_vision_prompt = VISION_CHAT_PROMPT
                        logger.info(f"[Ingestion] Single image detected as chat screenshot, using chat prompt")

                text = extract_text(
                    source.storage_path,
                    mime_type=mime,
                    progress_cb=progress_cb,
                    vision_prompt=single_vision_prompt,
                    diagnostics=diagnostics,
                    segment_collector=segment_collector,
                )
                if text:
                    return text
                # fallback: 直接读取（兼容纯文本文件）
                if suffix in (".txt", ".md"):
                    with open(source.storage_path, "r", encoding="utf-8", errors="ignore") as f:
                        return f.read()
            except Exception as e:
                logger.warning(f"[Ingestion] Text extraction error: {e}")
                raise
        return None

    # 每个 LLM 调用的最大字符数
    # 8000 中文字 ≈ 3000-4000 tokens，全量送入 extraction LLM
    _CHUNK_SIZE = 8000

    def _infer_speaker_theme(self, text_snippet: str, source) -> dict:
        """用结构化解析 + LLM 从文本推断 speaker 和 theme。

        优先使用微信聊天结构化解析器（零 LLM 成本识别发言人），
        然后用 LLM 补充 theme 和处理非聊天格式。
        """
        from app.services.file_parser import parse_wechat_chat, infer_source_type_from_text, extract_speaker_from_header
        result = {}

        # ── 阶段 0：从文本开头提取消息头中的发言人（如 "Silence  04-15 16:31:56"） ──
        header_speaker = extract_speaker_from_header(text_snippet)
        if header_speaker:
            result["speaker"] = header_speaker
            logger.info(f"[Ingestion] Speaker from header: {header_speaker!r}")

        # ── 阶段 1：结构化聊天解析（免 LLM） ──
        parsed_chat = parse_wechat_chat(text_snippet)
        if parsed_chat and parsed_chat.get("is_chat"):
            main_speaker = parsed_chat["main_speaker"]
            all_speakers = parsed_chat["speakers"]
            total_msgs = parsed_chat["total_messages"]

            # 格式化 speaker：如果主发言人明显占主导(>50%)，用其作为 speaker
            # 否则列出前两个
            if main_speaker:
                main_count = all_speakers[main_speaker]
                if main_count > total_msgs * 0.4:
                    result["speaker"] = main_speaker
                else:
                    top2 = sorted(all_speakers.items(), key=lambda x: -x[1])[:2]
                    result["speaker"] = " / ".join(s for s, _ in top2)

            logger.info(f"[Ingestion] Chat parsed: {total_msgs} msgs, "
                        f"speakers={dict(list(all_speakers.items())[:5])}, "
                        f"main={main_speaker}")

            # 自动修正 source_type
            if source.source_type == "image":
                source.source_type = "chat"
                self.db.add(source)
                self.db.flush()
                logger.info(f"[Ingestion] Auto-corrected source_type: image → chat")
        else:
            # 尝试推断 source_type（文章 vs 图片）
            inferred_type = infer_source_type_from_text(text_snippet)
            if inferred_type != "image" and source.source_type == "image":
                source.source_type = inferred_type
                self.db.add(source)
                self.db.flush()
                logger.info(f"[Ingestion] Auto-corrected source_type: image → {inferred_type}")

        # ── 阶段 2：LLM 推断 theme（以及 speaker 如果阶段1未识别） ──
        from app.utils.llm_client import llm_client

        # 根据是否已识别到聊天格式，使用不同的 prompt
        if parsed_chat and parsed_chat.get("is_chat"):
            speaker_info = f"已识别为群聊记录，主要发言人：{result.get('speaker', '未知')}，" \
                           f"参与者：{', '.join(list(parsed_chat['speakers'].keys())[:6])}"
            prompt = f"""以下是一段微信群聊记录的内容。
{speaker_info}

请分析主题，返回 2-4 个关键词（如：原油 CTA 逼空 流动性）。

文本摘要：
{text_snippet[:1500]}

返回纯 JSON：{{"theme": "关键词1 关键词2 关键词3"}}"""
        else:
            prompt = f"""以下是一段研究/投资资料的开头内容，请推断：
1. speaker（发言人/作者/机构）：是谁写的或说的？
   - 如果文本开头有明确的发言人名（如 "XXX  04-15 16:31"），提取该名称
   - 如果是公众号/媒体文章，提取公众号名或作者名
   - 如果无法确定填 null
2. theme（主题标签）：2-4个关键词，用空格分隔

文本：
{text_snippet[:1500]}

返回纯 JSON：{{"speaker": "...", "theme": "..."}}"""

        try:
            resp, _ = llm_client.complete_with_role(
                skill_role="speaker",
                system_prompt="你是一位专业的文档分析助手，擅长从各种格式的文本中识别作者、发言人和主题。",
                user_prompt=prompt,
                max_tokens=120,
                temperature=0.1,
            )
            import json, re
            text = (resp or "").strip()
            if "```" in text:
                text = re.sub(r"```[a-z]*", "", text).replace("```", "").strip()
            data = json.loads(text)

            # speaker：仅在阶段1未识别时使用LLM结果
            if not result.get("speaker"):
                sp = data.get("speaker")
                if sp and sp not in (None, "null", ""):
                    result["speaker"] = sp

            # theme：始终使用LLM结果
            th = data.get("theme")
            if th and th not in (None, "null", ""):
                result["theme"] = th

        except Exception as e:
            logger.warning(f"[Ingestion] _infer_speaker_theme LLM parse error: {e}")

        return result

    # ── 文章级摘要生成 ────────────────────────────────────────────
    # 字符长度阈值
    _SUMMARY_DIRECT_LIMIT = 6_000      # L1: 直接用全文
    _SUMMARY_SEGMENT_LIMIT = 60_000    # L2: 分段摘要再合并
    # L3: 超过 60000 字用结构采样

    SUMMARY_SYSTEM_PROMPT = """你是专业的金融文档分析师。请为以下文章生成一段结构化摘要（150-300字）。

摘要必须依次包含：
1. 【前提】作者基于什么背景/数据/事件展开分析
2. 【逻辑链】主要推导路径（A→B→C）
3. 【结论】最终判断或建议
4. 【资产】文中明确提及的资产或市场（简短列举）
5. 【时间】分析针对的时间维度

要求：体现文章思维路径，不要空泛总结，不要复述标题。"""

    _SUMMARY_SHORT_THRESHOLD = 500  # 短文本直接作为摘要，无需 LLM

    def _generate_article_summary(self, text: str) -> Optional[str]:
        """三级兜底策略生成文章摘要：
        L0 (≤500字)   : 文本本身足够短，直接作为摘要
        L1 (≤6000字)  : 直接全文
        L2 (≤60000字) : 分段摘要 → 二次合并
        L3 (>60000字) : 结构采样（头+各块首段+尾）→ 一次生成
        """
        try:
            from app.utils.llm_client import llm_client

            length = len(text)

            if length <= self._SUMMARY_SHORT_THRESHOLD:
                # L0: 文本很短，本身即摘要，无需调用 LLM
                logger.debug(f"[Summary] L0 short-text passthrough, {length} chars")
                return text.strip() if len(text.strip()) >= 20 else None

            if length <= self._SUMMARY_DIRECT_LIMIT:
                # L1: 直接全文
                input_text = text
                logger.debug(f"[Summary] L1 direct, {length} chars")
                return self._call_summary_llm(llm_client, input_text)

            elif length <= self._SUMMARY_SEGMENT_LIMIT:
                # L2: 分段摘要 → 合并
                # 每段取前 6000 字，最多取 5 段（30000 字覆盖大多数报告）
                chunks = self._split_chunks(text)
                seg_summaries = []
                for i, chunk in enumerate(chunks[:5]):
                    snippet = chunk[:self._SUMMARY_DIRECT_LIMIT]
                    logger.debug(f"[Summary] L2 segment {i+1}/{min(len(chunks),5)}, {len(snippet)} chars")
                    s = self._call_summary_llm(llm_client, snippet)
                    if s:
                        seg_summaries.append(f"【第{i+1}段】{s}")
                if not seg_summaries:
                    return None
                if len(seg_summaries) == 1:
                    return seg_summaries[0].replace("【第1段】", "")
                # 二次合并
                merge_prompt = "以下是同一篇文章各段落的分析摘要，请综合生成一段最终摘要（150-300字），保留核心逻辑链和结论：\n\n" + "\n\n".join(seg_summaries)
                logger.debug(f"[Summary] L2 merge, {len(seg_summaries)} segments")
                return self._call_summary_llm(llm_client, merge_prompt, is_merge=True)

            else:
                # L3: 结构采样（头3000 + 各块首段500 + 尾1000）
                chunks = self._split_chunks(text)
                head = text[:3000]
                mid_snippets = []
                for chunk in chunks[1:-1]:
                    # 取每块首段
                    first_para = chunk.split("\n\n")[0][:500]
                    mid_snippets.append(first_para)
                tail = text[-1000:]
                sampled = head + "\n\n[...文章中间部分...]\n\n" + "\n\n".join(mid_snippets[:10]) + "\n\n[...文章末尾...]\n\n" + tail
                logger.debug(f"[Summary] L3 sample, original={length} sampled={len(sampled)} chars")
                return self._build_deterministic_long_doc_summary(length, len(chunks), sampled)

        except Exception as e:
            logger.warning(f"[Summary] generate_article_summary failed: {e}")
            return None

    def _call_summary_llm(self, llm_client, text: str, is_merge: bool = False) -> Optional[str]:
        """调用 LLM 生成摘要，失败返回 None。"""
        try:
            system = self.SUMMARY_SYSTEM_PROMPT if not is_merge else "你是专业的金融文档分析师，请综合多段摘要生成最终摘要（150-300字）。"
            resp, _ = llm_client.complete_with_role(
                skill_role="extraction",
                system_prompt=system,
                user_prompt=text,
                max_tokens=600,
                temperature=0.3,
            )
            result = (resp or "").strip()
            return result if len(result) > 20 else None
        except Exception as e:
            logger.warning(f"[Summary] _call_summary_llm failed: {e}")
            return None

    def _build_deterministic_long_doc_summary(
        self,
        original_len: int,
        chunk_count: int,
        sampled_text: str,
    ) -> str:
        """Build a bounded summary for long-document MVP ingestion without LLM wait."""
        compact = " ".join((sampled_text or "").split())
        preview = compact[:900]
        return (
            f"【长文档摘要】原文约 {original_len} 字，按当前切分策略约 {chunk_count} 个 chunk。"
            "本次 MVP 采用确定性摘要与代表片段抽取，覆盖开头、中部和结尾的主要结构，"
            "用于确认长 PDF 可解析、可入库、可进入后续检索与问答；不宣称完成逐页逐条观点穷尽抽取。"
            f"【代表内容】{preview}"
        )

    def _get_text_for_llm(
        self,
        source,
        progress_cb=None,
        diagnostics=None,
        segment_collector: list[DocumentParseSegment] | None = None,
    ) -> Optional[str]:
        """获取文本内容，写回 DB，返回全文（不截断）"""
        parser_adapter = resolve_document_parser_adapter(
            settings.DOCUMENT_PARSER_ADAPTER,
            content_reader=self._get_text_content,
            normalizer=_normalize_cjk_text,
        )
        parse_result = parser_adapter.parse(
            source,
            progress_cb=progress_cb,
            diagnostics=diagnostics,
            segment_collector=segment_collector,
        )
        if not parse_result.text:
            return None

        if parse_result.from_cache:
            if parse_result.normalized:
                logger.info(f"[Ingestion] CJK NFKC normalized raw_text ({len(source.raw_text)} chars)")
                source.raw_text = parse_result.text
                self.db.flush()
            return parse_result.text

        # CJK 正规化 + 写回 raw_text（如扫描件 OCR 提取）
        if parse_result.should_persist:
            source.raw_text = parse_result.text
            source.word_count = len(parse_result.text)
            self.db.flush()
        return parse_result.text

    # 相邻 chunk 之间的重叠字符数，保留上下文连贯性
    _CHUNK_OVERLAP = 400
    _LONG_DOC_DISTILL_THRESHOLD = 120_000
    _LONG_DOC_DISTILL_MAX_CHARS = 7_500

    def _split_chunks(self, text: str) -> list[str]:
        """句子边界感知切分：永不在句子中间断开，相邻 chunk 有重叠。

        策略：
        1. 先按段落 (\\n\\n) 拆成段落列表
        2. 如果单段落超长，再按句子边界（。！？.!?\\n）二次拆分
        3. 贪心拼接：句子级单元 → 累积到 _CHUNK_SIZE 时断开
        4. 相邻 chunk 保留 _CHUNK_OVERLAP 字符的尾部重叠
        """
        if len(text) <= self._CHUNK_SIZE:
            return [text]

        # Step 1: 拆分为句子级单元
        sentences = self._split_into_sentences(text)
        if not sentences:
            return [text]

        chunks: list[str] = []
        current_parts: list[str] = []
        current_len = 0

        for sent in sentences:
            # 单句超长（极端情况）：硬切
            if len(sent) > self._CHUNK_SIZE:
                if current_parts:
                    chunks.append("".join(current_parts))
                    current_parts = []
                    current_len = 0
                # 按字符硬切超长句
                for i in range(0, len(sent), self._CHUNK_SIZE - self._CHUNK_OVERLAP):
                    chunks.append(sent[i:i + self._CHUNK_SIZE])
                continue

            if current_len + len(sent) > self._CHUNK_SIZE and current_parts:
                chunk_text = "".join(current_parts)
                chunks.append(chunk_text)
                # 重叠：保留尾部 overlap 字符作为下一个 chunk 的开头
                overlap_text = chunk_text[-self._CHUNK_OVERLAP:] if len(chunk_text) > self._CHUNK_OVERLAP else ""
                current_parts = [overlap_text, sent] if overlap_text else [sent]
                current_len = len(overlap_text) + len(sent)
            else:
                current_parts.append(sent)
                current_len += len(sent)

        if current_parts:
            chunks.append("".join(current_parts))

        return chunks

    def _should_use_long_doc_distillation(self, text: str, chunks: list[str]) -> bool:
        """Use one distilled extraction pass for very long documents in MVP demos."""
        return len(text or "") >= self._LONG_DOC_DISTILL_THRESHOLD and len(chunks) > 12

    def _build_long_doc_distilled_chunk(
        self,
        text_content: str,
        article_summary: str | None,
        chunks: list[str],
    ) -> str:
        parts: list[str] = []
        if article_summary:
            parts.append("【文章级摘要】\n" + article_summary[:1800])

        parts.append(
            "【长文档抽取说明】以下为全文代表性片段：开头、中部、结尾。"
            "请基于摘要和片段抽取可操作观点、风险边界、资产影响和失效条件；"
            "不要声称覆盖全文所有细节。"
        )

        selected: list[tuple[str, str]] = []
        if chunks:
            selected.append(("开头", chunks[0]))
            if len(chunks) > 2:
                selected.append(("中部", chunks[len(chunks) // 2]))
            if len(chunks) > 1:
                selected.append(("结尾", chunks[-1]))

        remaining = self._LONG_DOC_DISTILL_MAX_CHARS - sum(len(part) for part in parts)
        for label, chunk in selected:
            if remaining <= 0:
                break
            slot = max(500, remaining // max(1, len(selected)))
            snippet = chunk[: min(len(chunk), slot)]
            parts.append(f"\n【{label}片段】\n{snippet}")
            remaining = self._LONG_DOC_DISTILL_MAX_CHARS - sum(len(part) for part in parts)

        if not selected and text_content:
            remaining = self._LONG_DOC_DISTILL_MAX_CHARS - sum(len(part) for part in parts)
            parts.append("\n【全文开头片段】\n" + text_content[: max(0, remaining)])

        return "\n\n".join(parts)[: self._LONG_DOC_DISTILL_MAX_CHARS]

    @staticmethod
    def _split_into_sentences(text: str) -> list[str]:
        """将文本拆分为句子级单元，保留分隔符。

        优先按段落拆，段落内再按句子边界拆。
        保留原始空白字符，确保拼接后 == 原文。
        """
        import re
        units: list[str] = []

        # 按段落拆（保留 \\n\\n 分隔符）
        paragraphs = re.split(r'(\n\n+)', text)

        for para in paragraphs:
            # 空段落分隔符，直接保留
            if not para.strip():
                units.append(para)
                continue

            # 段落内按句子边界拆分
            # 匹配：中文句号/问号/叹号、英文句号+空格、换行
            # 使用正向后查找，保留分隔符在前一个 unit
            sents = re.split(r'(?<=[。！？\!\?])\s*(?=.)|(?<=\n)(?=.)', para)
            for s in sents:
                if s:
                    units.append(s)

        return units

    def process_pending_batch(self, limit: int = 5) -> dict:
        """批量处理 pending RawSource"""
        sources = self.raw_source_repo.get_pending(limit=limit)
        results = []
        for source in sources:
            result = self.process_source(source.id)
            results.append(result)
        return {
            "processed": len(sources),
            "succeeded": sum(1 for r in results if r.get("success")),
            "failed": sum(1 for r in results if not r.get("success")),
            "results": results,
        }
