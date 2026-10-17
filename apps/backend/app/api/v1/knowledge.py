"""Knowledge Base API — 知识库全局统计与浏览"""
from fastapi import APIRouter, Depends, Query, BackgroundTasks
from typing import Optional
from datetime import datetime, timezone
from pathlib import Path
import json
from sqlalchemy.orm import Session
from sqlalchemy import func
from loguru import logger

from app.database import safe_commit
from app.dependencies import get_db
from app.schemas.raw_source import RawSource
from app.schemas.claim_card import ClaimCard
from app.schemas.strategy_card import StrategyCard
from app.schemas.briefing_card import BriefingCard
from app.schemas.setup_card import SetupCard
from app.schemas.event_card import EventCard
from app.schemas.job_run import JobRun
from app.schemas.claim_card import ClaimRawSourceLink
from app.utils.record_classification import is_system_source_record
from app.utils.text_stats import build_source_status_payload

router = APIRouter()


def _is_system_source(source: RawSource) -> bool:
    return is_system_source_record(
        source_id=source.id,
        title=source.title,
        storage_path=source.storage_path,
    )


def _system_only_claim_ids(db: Session, claim_ids: list[str]) -> set[str]:
    if not claim_ids:
        return set()

    linked_sources: dict[str, list[RawSource]] = {}
    rows = (
        db.query(ClaimRawSourceLink.claim_id, RawSource)
        .join(RawSource, RawSource.id == ClaimRawSourceLink.source_id)
        .filter(ClaimRawSourceLink.claim_id.in_(claim_ids))
        .all()
    )
    for claim_id, source in rows:
        linked_sources.setdefault(claim_id, []).append(source)
    return {
        claim_id
        for claim_id, sources in linked_sources.items()
        if sources and all(_is_system_source(source) for source in sources)
    }


@router.get("/stats")
def get_kb_stats(db: Session = Depends(get_db)):
    """知识库全局统计"""
    # RawSource
    src_total = db.query(func.count(RawSource.id)).scalar()
    src_by_status = dict(db.query(RawSource.status, func.count(RawSource.id)).group_by(RawSource.status).all())
    src_by_type = dict(db.query(RawSource.source_type, func.count(RawSource.id)).group_by(RawSource.source_type).all())

    # ClaimCard
    claim_total = db.query(func.count(ClaimCard.id)).scalar()
    claim_by_status = dict(db.query(ClaimCard.status, func.count(ClaimCard.id)).group_by(ClaimCard.status).all())
    claim_by_stance = dict(db.query(ClaimCard.stance, func.count(ClaimCard.id)).group_by(ClaimCard.stance).all())
    claim_by_type = dict(db.query(ClaimCard.claim_type, func.count(ClaimCard.id)).group_by(ClaimCard.claim_type).all())

    # StrategyCard
    strat_total = db.query(func.count(StrategyCard.id)).scalar()
    strat_by_type = dict(db.query(StrategyCard.strategy_type, func.count(StrategyCard.id)).group_by(StrategyCard.strategy_type).all())

    # SetupCard
    setup_total = db.query(func.count(SetupCard.id)).scalar()
    setup_by_action = dict(db.query(SetupCard.recommended_action, func.count(SetupCard.id)).group_by(SetupCard.recommended_action).all())

    # BriefingCard
    brief_total = db.query(func.count(BriefingCard.id)).scalar()
    brief_by_dir = dict(db.query(BriefingCard.direction, func.count(BriefingCard.id)).group_by(BriefingCard.direction).all())

    # EventCard
    event_total = db.query(func.count(EventCard.id)).scalar()

    # JobRun
    job_total = db.query(func.count(JobRun.id)).scalar()
    job_by_status = dict(db.query(JobRun.status, func.count(JobRun.id)).group_by(JobRun.status).all())

    return {
        "raw_sources": {"total": src_total, "by_status": src_by_status, "by_type": src_by_type},
        "claims": {"total": claim_total, "by_status": claim_by_status, "by_stance": claim_by_stance, "by_type": claim_by_type},
        "strategies": {"total": strat_total, "by_type": strat_by_type},
        "setups": {"total": setup_total, "by_action": setup_by_action},
        "briefings": {"total": brief_total, "by_direction": brief_by_dir},
        "events": {"total": event_total},
        "job_runs": {"total": job_total, "by_status": job_by_status},
    }


@router.get("/sources")
def list_sources(
    status: Optional[str] = Query(None),
    source_type: Optional[str] = Query(None),
    include_system: bool = Query(False),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Browse source records; automated verification records are hidden by default."""
    q = db.query(RawSource)
    if status: q = q.filter(RawSource.status == status)
    if source_type: q = q.filter(RawSource.source_type == source_type)
    candidates = q.order_by(RawSource.created_at.desc()).all()
    if not include_system:
        candidates = [source for source in candidates if not _is_system_source(source)]
    total = len(candidates)
    items = candidates[offset: offset + limit]
    item_ids = [s.id for s in items]
    claim_counts = dict(
        db.query(ClaimRawSourceLink.source_id, func.count(ClaimRawSourceLink.claim_id))
        .filter(ClaimRawSourceLink.source_id.in_(item_ids))
        .group_by(ClaimRawSourceLink.source_id)
        .all()
    ) if item_ids else {}
    return {
        "total": total,
        "items": [{
            "id": s.id, "title": s.title or "(untitled)", "source_type": s.source_type,
            "status": s.status, "word_count": s.word_count,
            **build_source_status_payload(s, int(claim_counts.get(s.id, 0))),
            "speaker_hint": s.speaker_hint, "theme_hint": s.theme_hint,
            "article_summary": s.article_summary,
            "user_trust_level": getattr(s, 'user_trust_level', None),
            "collection_reason": getattr(s, 'collection_reason', None),
            "user_note": getattr(s, 'user_note', None),
            "created_at": s.created_at.isoformat() if s.created_at else None,
        } for s in items]
    }


@router.get("/sources/{source_id}")
async def get_source(source_id: str, db: Session = Depends(get_db)):
    """获取单个来源详情（含关联 claims）"""
    from fastapi import HTTPException
    s = db.query(RawSource).filter(RawSource.id == source_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Source not found")
    links = db.query(ClaimRawSourceLink).filter(ClaimRawSourceLink.source_id == source_id).all()
    claim_ids = [lk.claim_id for lk in links]
    claims = db.query(ClaimCard).filter(ClaimCard.id.in_(claim_ids)).all() if claim_ids else []
    return {
        "id": s.id, "title": s.title, "source_type": s.source_type,
        "status": s.status, "word_count": s.word_count,
        **build_source_status_payload(s, len(claims)),
        "speaker_hint": s.speaker_hint, "theme_hint": s.theme_hint,
        "article_summary": s.article_summary,
        "user_trust_level": getattr(s, 'user_trust_level', None),
        "collection_reason": getattr(s, 'collection_reason', None),
        "user_note": getattr(s, 'user_note', None),
        "raw_text": (s.raw_text or "")[:2000],
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "claims": [{
            "id": c.id, "summary": c.summary, "stance": c.stance,
            "status": c.status, "confidence": c.confidence,
        } for c in claims]
    }


@router.delete("/sources/{source_id}")
async def delete_source(source_id: str, db: Session = Depends(get_db)):
    """删除来源及其关联的 claims、links、job_runs"""
    from fastapi import HTTPException
    from app.schemas.claim_card import ClaimRawSourceLink
    s = db.query(RawSource).filter(RawSource.id == source_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Source not found")
    # 删除关联 links
    links = db.query(ClaimRawSourceLink).filter(ClaimRawSourceLink.source_id == source_id).all()
    claim_ids = [lk.claim_id for lk in links]
    for lk in links:
        db.delete(lk)
    # 删除孤立 claims（只关联此 source 的）
    for cid in claim_ids:
        remaining = db.query(ClaimRawSourceLink).filter(ClaimRawSourceLink.claim_id == cid).count()
        if remaining == 0:
            claim = db.query(ClaimCard).filter(ClaimCard.id == cid).first()
            if claim:
                db.delete(claim)
    # 删除关联 job_runs
    db.query(JobRun).filter(JobRun.related_object_id == source_id).delete()
    # 删除 source
    db.delete(s)
    safe_commit(db)
    return {"deleted": source_id, "claims_affected": len(claim_ids)}


@router.get("/claims")
def list_claims_kb(
    status: Optional[str] = Query(None),
    stance: Optional[str] = Query(None),
    asset: Optional[str] = Query(None),
    include_system: bool = Query(False),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Browse claims; claims backed only by system records are hidden by default."""
    q = db.query(ClaimCard)
    if status: q = q.filter(ClaimCard.status == status)
    if stance: q = q.filter(ClaimCard.stance == stance)
    if asset: q = q.filter(ClaimCard.asset_scope.contains([asset]))
    candidates = q.order_by(ClaimCard.created_at.desc()).all()
    if not include_system:
        system_claim_ids = _system_only_claim_ids(db, [claim.id for claim in candidates])
        candidates = [claim for claim in candidates if claim.id not in system_claim_ids]
    total = len(candidates)
    items = candidates[offset: offset + limit]
    return {
        "total": total,
        "items": [{
            "id": c.id, "summary": c.summary, "claim_type": c.claim_type,
            "stance": c.stance, "status": c.status, "confidence": c.confidence,
            "asset_scope": c.asset_scope, "theme_scope": c.theme_scope,
            "time_horizon": c.time_horizon,
            "valid_until": c.valid_until.isoformat() if c.valid_until else None,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        } for c in items]
    }


@router.get("/briefings")
def list_briefings_kb(
    direction: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """知识库视图：浏览所有 BriefingCard"""
    q = db.query(BriefingCard)
    if direction: q = q.filter(BriefingCard.direction == direction)
    total = q.count()
    items = q.order_by(BriefingCard.created_at.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "items": [{
            "id": b.id, "title": b.title, "topic": b.topic,
            "direction": b.direction, "setup_quality": b.setup_quality,
            "recommended_action": b.recommended_action,
            "asset_scope": b.asset_scope, "strategy_fit_summary": b.strategy_fit_summary,
            "generated_at": b.generated_at.isoformat() if b.generated_at else None,
        } for b in items]
    }


@router.get("/setups")
def list_setups_kb(
    asset: Optional[str] = Query(None),
    recommended_action: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """知识库视图：浏览所有 SetupCard"""
    q = db.query(SetupCard)
    if asset: q = q.filter(SetupCard.asset == asset)
    if recommended_action: q = q.filter(SetupCard.recommended_action == recommended_action)
    total = q.count()
    items = q.order_by(SetupCard.created_at.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "items": [{
            "id": s.id, "asset": s.asset, "direction": s.direction,
            "overall_score": s.overall_score, "strategy_fit_score": s.strategy_fit_score,
            "recommended_action": s.recommended_action,
            "status": s.status, "strategy_id": s.strategy_id,
            "evaluated_at": s.evaluated_at.isoformat() if s.evaluated_at else None,
        } for s in items]
    }


@router.patch("/sources/{source_id}")
async def update_source(
    source_id: str,
    body: dict,
    db: Session = Depends(get_db),
):
    """编辑来源的 speaker_hint / theme_hint / title"""
    from datetime import datetime, timezone
    src = db.query(RawSource).filter(RawSource.id == source_id).first()
    if not src:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Source not found")
    allowed = {"title", "speaker_hint", "theme_hint", "language",
                "user_trust_level", "collection_reason", "user_note"}
    for k, v in body.items():
        if k in allowed:
            setattr(src, k, v)
    src.updated_at = datetime.now(timezone.utc)
    safe_commit(db)
    return {"success": True, "id": source_id}


@router.get("/sources/{source_id}/progress")
async def get_source_progress(
    source_id: str,
    db: Session = Depends(get_db),
):
    """获取来源处理进度"""
    from app.schemas.job_run import JobRun
    src = db.query(RawSource).filter(RawSource.id == source_id).first()
    if not src:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Source not found")
    # 获取最新 job_run
    job = db.query(JobRun).filter(
        JobRun.related_object_id == source_id,
        JobRun.job_type == "ingestion"
    ).order_by(JobRun.created_at.desc()).first()
    summary = job.result_summary if job else {}
    claim_count = db.query(ClaimRawSourceLink).filter(ClaimRawSourceLink.source_id == source_id).count()
    status_payload = build_source_status_payload(src, claim_count)
    # 计算进度百分比
    pct = summary.get("pct", 0) if summary else 0
    if src.status == "completed":
        pct = 100
    elif src.status == "pending":
        pct = 0
    stage = summary.get("stage", "") if summary else ""
    # 生成进度描述文字
    if src.status == "completed":
        extracted = summary.get('claims_extracted', claim_count)
        if summary.get("long_document_mode") == "parse_store_mvp":
            msg = "长文档已解析入库，观点抽取后台延后"
        elif status_payload["claim_extraction_status"] == "zero_claims":
            msg = "已解析并生成摘要，但未抽取到结构化观点"
        else:
            msg = f"完成：提取 {extracted} 条观点"
    elif src.status == "failed":
        msg = f"失败：{summary.get('error', '未知错误')}"
    elif stage == "ocr":
        ocr_page = summary.get('ocr_page', 0)
        ocr_total = summary.get('ocr_total', '?')
        msg = f"OCR 识别中：第 {ocr_page}/{ocr_total} 页"
    elif stage == "extraction":
        chunk = summary.get('chunk', 0)
        total_chunks = summary.get('total_chunks', 1)
        claims = summary.get('claims_so_far', 0)
        msg = f"AI 抽取中：第 {chunk}/{total_chunks} 块，已得 {claims} 条观点"
    else:
        msg = "等待处理…"
    return {
        "source_id": source_id,
        "status": src.status,
        "word_count": src.word_count or 0,
        **status_payload,
        "pct": pct,
        "msg": msg,
        "job_status": job.status if job else None,
        "job_started_at": job.started_at.isoformat() if job and job.started_at else None,
        "job_completed_at": job.completed_at.isoformat() if job and job.completed_at else None,
        "duration_ms": job.duration_ms if job else None,
        "result_summary": summary,
    }


@router.get("/sources/{source_id}/traces")
async def get_source_traces(
    source_id: str,
    db: Session = Depends(get_db),
):
    """获取来源的全链路处理留痕（每一步的输入/输出/耗时）"""
    from app.services.processing_trace_service import ProcessingTraceRepository
    traces = ProcessingTraceRepository.get_by_source(db, source_id)
    return [
        {
            "id": t.id,
            "step_index": t.step_index,
            "step_name": t.step_name,
            "status": t.status,
            "input_snapshot": t.input_snapshot,
            "output_snapshot": t.output_snapshot,
            "duration_ms": t.duration_ms,
            "error_message": t.error_message,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in traces
    ]


@router.post("/sources/{source_id}/reprocess")
async def reprocess_source(
    source_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """重新触发来源抽取"""
    from app.api.v1.ingestion import (
        _create_queued_ingestion_job,
        _dispatch_queued_ingestion_jobs,
        _persist_dispatch_failures,
        _reserve_async_capacity_or_raise,
    )
    from app.services.ingestion_dispatcher import release_ingestion_capacity
    from app.services.progress_tracker import progress_tracker
    from fastapi import HTTPException
    src = db.query(RawSource).filter(RawSource.id == source_id).first()
    if not src:
        raise HTTPException(status_code=404, detail="Source not found")
    admission = _reserve_async_capacity_or_raise(db, 1)
    reservation_id = admission.get("reservation_id")
    original_state = {
        "status": src.status,
        "raw_text": src.raw_text,
        "word_count": src.word_count,
        "processing_error": src.processing_error,
    }
    try:
        src.status = "pending"
        src.raw_text = None
        src.word_count = 0
        src.processing_error = None
        src.updated_at = datetime.now(timezone.utc)
        job = _create_queued_ingestion_job(db, source_id, commit=False)
        safe_commit(db)
        db.refresh(job)
    except Exception:
        db.rollback()
        release_ingestion_capacity(reservation_id)
        raise
    progress_tracker.start_job(
        job.id,
        "ingestion",
        source_id=source_id,
        related_object_id=source_id,
        related_object_type="raw_source",
    )
    jobs = _dispatch_queued_ingestion_jobs(
        [{"source_id": source_id, "job_id": job.id}],
        reservation_id=reservation_id,
    )
    dispatch_status = jobs[0]["dispatch_status"]
    _persist_dispatch_failures(db, jobs)
    if not dispatch_status.get("submitted") and dispatch_status.get("reason") != "already_submitted":
        src.status = original_state["status"]
        src.raw_text = original_state["raw_text"]
        src.word_count = original_state["word_count"]
        src.processing_error = original_state["processing_error"]
        src.updated_at = datetime.now(timezone.utc)
        safe_commit(db)
        raise HTTPException(
            status_code=503,
            detail={
                "reason": dispatch_status.get("reason") or "dispatch_failed",
                "retryable": bool(dispatch_status.get("retryable")),
                "source_id": source_id,
                "job_id": job.id,
            },
        )
    return {
        "queued": True,
        "source_id": source_id,
        "job_id": job.id,
        "progress_url": f"/api/v1/progress/{job.id}",
        "dispatch_status": dispatch_status,
        "admission": {key: value for key, value in admission.items() if key != "reservation_id"},
    }


_TAXONOMY_FILE = Path(__file__).resolve().parents[2] / "configs" / "financial_taxonomy.json"


def _load_fin_taxonomy() -> dict:
    default = {
        "macro": {"label": "宏观", "children": {"growth": "增长", "inflation": "通胀"}},
        "market_structure": {"label": "市场结构", "children": {"equity": "权益市场", "crypto": "加密市场"}},
        "micro": {"label": "微观", "children": {"valuation": "估值", "earnings": "盈利"}},
        "style_factor": {"label": "风格因子", "children": {"value": "价值", "momentum": "动量"}},
        "strategy_execution": {"label": "策略与执行", "children": {"allocation": "资产配置", "risk_control": "风险控制"}},
    }
    try:
        if _TAXONOMY_FILE.exists():
            loaded = json.loads(_TAXONOMY_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and loaded:
                return loaded
    except Exception:
        pass
    return default


FIN_TAXONOMY = _load_fin_taxonomy()


def _safe_text(v: Optional[str]) -> str:
    return (v or "").strip().lower()


def _classify_claim_multi(claim: ClaimCard) -> list[dict]:
    summary = _safe_text(claim.summary)
    themes = [(_safe_text(t), t) for t in (claim.theme_scope or [])]
    assets = [(_safe_text(a), a) for a in (claim.asset_scope or [])]

    results: list[dict] = []

    def add(cat: str, sub: str, reason: str):
        item = {
            "category": cat,
            "category_label": FIN_TAXONOMY[cat]["label"],
            "subcategory": sub,
            "subcategory_label": FIN_TAXONOMY[cat]["children"][sub],
            "reason": reason,
        }
        results.append(item)

    macro_keywords = {
        "monetary_policy": ["fed", "fomc", "rate", "加息", "降息", "央行", "利率"],
        "fiscal_policy": ["财政", "预算", "赤字", "税", "刺激"],
        "inflation": ["通胀", "cpi", "ppi", "inflation"],
        "growth": ["gdp", "增长", "衰退", "经济"],
        "employment": ["就业", "失业", "nonfarm", "payroll"],
        "liquidity": ["liquidity", "流动性", "m2", "信用"],
        "global_risk": ["systemic", "risk-off", "避险"],
        "geopolitics": ["地缘", "战争", "制裁", "冲突"],
    }
    market_keywords = {
        "equity": ["spx", "ndx", "hsi", "a股", "股市", "equity", "stock"],
        "fixed_income": ["债", "yield", "10y", "2y", "treasury"],
        "fx": ["dxy", "usd", "汇率", "fx"],
        "commodity": ["gold", "silver", "oil", "原油", "黄金", "商品"],
        "crypto": ["btc", "eth", "crypto", "加密", "比特币", "以太坊"],
        "derivatives": ["futures", "options", "期权", "期货", "iv", "open interest"],
        "volatility": ["vix", "volatility", "波动", "波动率"],
    }
    micro_keywords = {
        "company_fundamental": ["基本面", "fundamental", "业务", "竞争力"],
        "valuation": ["估值", "pe", "pb", "ev/ebitda", "折现"],
        "earnings": ["财报", "业绩", "earnings", "利润"],
        "cashflow": ["现金流", "free cash flow", "fcf"],
        "balance_sheet": ["负债", "杠杆", "资产负债"],
        "corporate_action": ["回购", "分红", "增发", "并购", "重组"],
    }
    style_keywords = {
        "value": ["value", "价值"],
        "growth": ["growth", "成长"],
        "quality": ["quality", "质量"],
        "momentum": ["momentum", "动量", "趋势"],
        "size": ["small cap", "large cap", "市值"],
        "dividend": ["dividend", "股息", "分红"],
        "sentiment": ["情绪", "sentiment", "恐慌", "贪婪"],
    }
    strategy_keywords = {
        "timing": ["择时", "timing", "窗口"],
        "allocation": ["配置", "allocation", "再平衡"],
        "risk_control": ["风控", "止损", "drawdown", "回撤"],
        "positioning": ["仓位", "position", "杠杆"],
        "entry_exit": ["入场", "出场", "止盈", "交易计划"],
        "hedging": ["对冲", "hedge", "保护"],
        "event_driven": ["事件", "event", "催化", "公告"],
    }

    def match_set(cat: str, mp: dict[str, list[str]]):
        for sub, keys in mp.items():
            for kw in keys:
                if kw in summary or any(kw in t for t, _ in themes) or any(kw in a for a, _ in assets):
                    add(cat, sub, f"keyword:{kw}")
                    break

    match_set("macro", macro_keywords)
    match_set("market_structure", market_keywords)
    match_set("micro", micro_keywords)
    match_set("style_factor", style_keywords)
    match_set("strategy_execution", strategy_keywords)

    if not results:
        add("strategy_execution", "event_driven", "fallback:unclassified")

    # 去重，但允许跨类别重复
    seen = set()
    deduped = []
    for x in results:
        k = (x["category"], x["subcategory"])
        if k in seen:
            continue
        seen.add(k)
        deduped.append(x)
    return deduped


@router.get("/timeline")
def get_knowledge_timeline(
    days: int = Query(90, ge=7, le=365),
    limit: int = Query(400, ge=50, le=3000),
    db: Session = Depends(get_db),
):
    """统一时间轴数据：来源/观点/简报/机会，支持日历与时间轴板块复用"""
    start_ts = datetime.now(timezone.utc).timestamp() - days * 86400
    start_dt = datetime.fromtimestamp(start_ts, timezone.utc)

    events: list[dict] = []

    sources = (
        db.query(RawSource)
        .filter(RawSource.created_at >= start_dt)
        .order_by(RawSource.created_at.desc())
        .limit(limit)
        .all()
    )
    for s in sources:
        events.append({
            "id": s.id,
            "type": "source_created",
            "title": s.title or "Untitled Source",
            "ts": s.created_at.isoformat() if s.created_at else None,
            "date": (s.created_at.date().isoformat() if s.created_at else None),
            "status": s.status,
            "asset_scope": [],
            "theme_scope": [s.theme_hint] if s.theme_hint else [],
            "meta": {"source_type": s.source_type, "word_count": s.word_count},
        })

    claims = (
        db.query(ClaimCard)
        .filter(ClaimCard.created_at >= start_dt)
        .order_by(ClaimCard.created_at.desc())
        .limit(limit)
        .all()
    )
    for c in claims:
        events.append({
            "id": c.id,
            "type": "claim_created",
            "title": c.summary or "Claim",
            "ts": c.created_at.isoformat() if c.created_at else None,
            "date": (c.created_at.date().isoformat() if c.created_at else None),
            "status": c.status,
            "asset_scope": c.asset_scope or [],
            "theme_scope": c.theme_scope or [],
            "meta": {
                "stance": c.stance,
                "claim_type": c.claim_type,
                "confidence": c.confidence,
                "valid_until": c.valid_until.isoformat() if c.valid_until else None,
            },
        })
        if c.valid_until:
            events.append({
                "id": f"{c.id}:expiry",
                "type": "claim_expiry",
                "title": f"Claim Expiry: {(c.summary or 'Claim')[:60]}",
                "ts": c.valid_until.isoformat(),
                "date": c.valid_until.date().isoformat(),
                "status": c.status,
                "asset_scope": c.asset_scope or [],
                "theme_scope": c.theme_scope or [],
                "meta": {"claim_id": c.id},
            })

    briefings = (
        db.query(BriefingCard)
        .filter(BriefingCard.created_at >= start_dt)
        .order_by(BriefingCard.created_at.desc())
        .limit(limit)
        .all()
    )
    for b in briefings:
        ts = b.generated_at or b.created_at
        events.append({
            "id": b.id,
            "type": "briefing_generated",
            "title": b.title or b.topic or "Briefing",
            "ts": ts.isoformat() if ts else None,
            "date": (ts.date().isoformat() if ts else None),
            "status": b.status,
            "asset_scope": b.asset_scope or [],
            "theme_scope": [],
            "meta": {
                "direction": b.direction,
                "setup_quality": b.setup_quality,
                "recommended_action": b.recommended_action,
            },
        })

    setups = (
        db.query(SetupCard)
        .filter(SetupCard.created_at >= start_dt)
        .order_by(SetupCard.created_at.desc())
        .limit(limit)
        .all()
    )
    for s in setups:
        ts = s.evaluated_at or s.created_at
        events.append({
            "id": s.id,
            "type": "setup_evaluated",
            "title": f"Setup {s.asset} {s.recommended_action}",
            "ts": ts.isoformat() if ts else None,
            "date": (ts.date().isoformat() if ts else None),
            "status": s.status,
            "asset_scope": [s.asset] if s.asset else [],
            "theme_scope": [],
            "meta": {
                "direction": s.direction,
                "overall_score": s.overall_score,
                "strategy_fit_score": s.strategy_fit_score,
                "recommended_action": s.recommended_action,
            },
        })

    events = [e for e in events if e.get("ts")]
    events.sort(key=lambda x: x["ts"], reverse=True)

    by_date: dict[str, list[dict]] = {}
    for e in events:
        d = e["date"]
        by_date.setdefault(d, []).append(e)

    return {
        "days": days,
        "total": len(events),
        "events": events,
        "by_date": by_date,
    }


@router.get("/taxonomy")
def get_knowledge_taxonomy(
    limit: int = Query(800, ge=100, le=3000),
    db: Session = Depends(get_db),
):
    """金融知识分类树与映射结果（允许一条知识映射多个分类）"""
    claims = db.query(ClaimCard).order_by(ClaimCard.created_at.desc()).limit(limit).all()

    mapped: list[dict] = []
    category_count: dict[str, int] = {}
    subcategory_count: dict[str, int] = {}

    for c in claims:
        cls = _classify_claim_multi(c)
        for one in cls:
            item = {
                "claim_id": c.id,
                "summary": c.summary,
                "stance": c.stance,
                "status": c.status,
                "asset_scope": c.asset_scope or [],
                "theme_scope": c.theme_scope or [],
                "category": one["category"],
                "category_label": one["category_label"],
                "subcategory": one["subcategory"],
                "subcategory_label": one["subcategory_label"],
                "reason": one["reason"],
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            mapped.append(item)
            category_count[one["category"]] = category_count.get(one["category"], 0) + 1
            sk = f"{one['category']}.{one['subcategory']}"
            subcategory_count[sk] = subcategory_count.get(sk, 0) + 1

    return {
        "taxonomy": FIN_TAXONOMY,
        "total_claims": len(claims),
        "total_mapped": len(mapped),
        "category_count": category_count,
        "subcategory_count": subcategory_count,
        "mapped_items": mapped,
    }


@router.get("/graph")
def get_knowledge_graph(
    limit: int = Query(300, ge=50, le=1000),
    db: Session = Depends(get_db),
):
    """知识图谱（有向）：事件/政策/结果/市场节点 + 传播关系"""
    import re
    raw_claims = db.query(ClaimCard).order_by(ClaimCard.created_at.asc()).limit(limit).all()

    # ── Dedup: merge claims with near-identical summaries + same scope ──
    def _norm(s: str) -> str:
        """Normalize summary for dedup comparison."""
        s = re.sub(r'\[.*?\]', '', s).strip().lower()
        s = re.sub(r'[\s·\-—:：,，。.、]+', '', s)
        return s[:60]  # compare first 60 chars after normalization

    def _scope_key(c: ClaimCard) -> str:
        themes = tuple(sorted(c.theme_scope or []))
        assets = tuple(sorted(c.asset_scope or []))
        return f"{themes}|{assets}"

    seen: dict[str, ClaimCard] = {}  # dedup_key -> best claim
    merge_count: dict[str, int] = {}  # dedup_key -> how many merged
    for c in raw_claims:
        key = f"{_scope_key(c)}||{_norm(c.summary or '')}"
        existing = seen.get(key)
        if existing is None:
            seen[key] = c
            merge_count[key] = 1
        else:
            merge_count[key] += 1
            # Keep the one with higher confidence, or newer if tied
            if (c.confidence or 0) > (existing.confidence or 0):
                seen[key] = c
    claims = list(seen.values())
    claim_merge = {c.id: merge_count[k] for k, c in seen.items()}

    def infer_node_type(c: ClaimCard) -> str:
        ct = (c.claim_type or "").lower()
        summary = (c.summary or "").lower()
        if "policy" in ct or "政策" in summary or "央行" in summary or "监管" in summary:
            return "policy"
        if "result" in ct or "outcome" in ct or "结果" in summary or "影响" in summary:
            return "outcome"
        if (c.asset_scope or []):
            return "market"
        return "event"

    nodes: list[dict] = []
    links: list[dict] = []

    for c in claims:
        mc = claim_merge.get(c.id, 1)
        nodes.append({
            "id": c.id,
            "type": infer_node_type(c),
            "label": (c.summary or "Claim")[:120],
            "status": c.status,
            "stance": c.stance,
            "asset_scope": c.asset_scope or [],
            "theme_scope": c.theme_scope or [],
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "merge_count": mc,
        })

    # 有向构图规则：
    # - 同资产/主题且时间先后 => follows
    # - policy -> outcome/market => causes
    # - 多事件共同指向结果 => co_drives（后续前端按入度识别）
    for i in range(len(claims)):
        ci = claims[i]
        ai = set((ci.asset_scope or []))
        ti = set((ci.theme_scope or []))
        for j in range(i + 1, len(claims)):
            cj = claims[j]
            aj = set((cj.asset_scope or []))
            tj = set((cj.theme_scope or []))

            common_assets = sorted(list(ai.intersection(aj)))
            common_themes = sorted(list(ti.intersection(tj)))
            if not common_assets and not common_themes:
                continue

            type_i = infer_node_type(ci)
            type_j = infer_node_type(cj)

            relation_type = "follows"
            if type_i == "policy" and type_j in {"outcome", "market"}:
                relation_type = "causes"
            elif type_i in {"event", "policy"} and type_j == "outcome":
                relation_type = "co_drives"
            elif type_i == "event" and type_j == "event":
                relation_type = "follows"
            else:
                relation_type = "supports"

            weight = len(common_assets) * 2 + len(common_themes)
            relation_detail = []
            if common_assets:
                relation_detail.append(f"影响标的:{','.join(common_assets[:3])}")
            if common_themes:
                relation_detail.append(f"事件主题:{','.join(common_themes[:3])}")

            links.append({
                "source": ci.id,
                "target": cj.id,
                "weight": weight,
                "relation_type": relation_type,
                "relation": " | ".join(relation_detail) if relation_detail else relation_type,
                "directed": True,
            })

    return {
        "nodes": nodes,
        "links": links,
        "meta": {
            "node_count": len(nodes),
            "link_count": len(links),
            "directed": True,
            "raw_claim_count": len(raw_claims),
            "deduped_count": len(raw_claims) - len(claims),
        },
    }


@router.get("/lint")
def run_lint(db: Session = Depends(get_db)):
    """知识库健康自检 — 扫描过期、矛盾、低质量、盲区等问题"""
    from app.services.knowledge_lint import run_knowledge_lint
    return run_knowledge_lint(db)


# ── Test Inbox: 自动化测试入口 ────────────────────────────────────
# 支持子文件夹批次模式：
#   - 每个子文件夹 = 一个来源（文件夹内图片按文件名排序后合并）
#   - 子文件夹内如有 PDF/TXT 等非图片文件，也独立处理
#   - 根目录文件 = 各自独立处理
#   - 支持嵌套文件夹（如 xxx.pdf/ 目录内有 xxx.pdf 文件）

_SUPPORTED_EXTS = {".txt", ".md", ".pdf", ".docx", ".jpg", ".jpeg", ".png", ".webp"}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _collect_files_recursive(folder):
    """递归收集文件夹内所有支持格式的文件"""
    from pathlib import Path
    results = []
    for item in sorted(folder.iterdir(), key=lambda x: x.name):
        if item.is_file() and item.suffix.lower() in _SUPPORTED_EXTS:
            results.append(item)
        elif item.is_dir():
            results.extend(_collect_files_recursive(item))
    return results


@router.get("/test-inbox/scan")
def scan_test_inbox():
    """扫描 test_inbox/ 文件夹，列出待处理内容（支持子文件夹批次）"""
    from pathlib import Path
    inbox = Path(__file__).resolve().parents[5] / "test_inbox"
    if not inbox.exists():
        return {"error": "test_inbox/ 文件夹不存在", "path": str(inbox)}

    # 扫描子文件夹批次
    batches = []
    root_files = []

    for item in sorted(inbox.iterdir(), key=lambda x: x.name):
        if item.name == "README.md":
            continue
        if item.is_dir():
            files_in_batch = _collect_files_recursive(item)
            if files_in_batch:
                images = [f for f in files_in_batch if f.suffix.lower() in _IMAGE_EXTS]
                others = [f for f in files_in_batch if f.suffix.lower() not in _IMAGE_EXTS]
                batches.append({
                    "folder": item.name,
                    "images": [f.name for f in images],
                    "other_files": [f.name for f in others],
                    "total_size_mb": round(sum(f.stat().st_size for f in files_in_batch) / 1024 / 1024, 2),
                })
        elif item.is_file() and item.suffix.lower() in _SUPPORTED_EXTS:
            root_files.append({
                "name": item.name, "size_mb": round(item.stat().st_size / 1024 / 1024, 2),
                "ext": item.suffix.lower(),
            })

    return {
        "inbox_path": str(inbox),
        "batches": batches,
        "root_files": root_files,
        "total_batches": len(batches),
        "total_root_files": len(root_files),
    }


@router.post("/test-inbox/process")
def process_test_inbox(
    user_trust_level: str = Query("", description="可选信任度标注"),
    user_note: str = Query("", description="可选备注"),
    db: Session = Depends(get_db),
):
    """扫描 test_inbox/ 并自动上传处理所有文件（支持子文件夹批次）

    处理规则：
    - 每个子文件夹 = 一组素材，文件夹内图片按文件名排序后合并为一个 source
    - 子文件夹内非图片文件（pdf/txt/md）独立创建 source
    - 根目录文件各自独立处理
    - SHA-256 content_hash 自动去重
    """
    from pathlib import Path
    import hashlib
    import shutil
    from datetime import datetime, timezone
    from ulid import ULID

    inbox = Path(__file__).resolve().parents[5] / "test_inbox"
    if not inbox.exists():
        from fastapi import HTTPException
        raise HTTPException(404, "test_inbox/ folder not found")

    from app.services.raw_source_repository import RawSourceRepository
    from app.schemas.raw_source import RawSource as RawSourceModel, SourceAttachment
    from app.services.storage_service import storage, UPLOAD_DIR

    repo = RawSourceRepository(db)
    now = datetime.now(timezone.utc)
    source_ids = []
    skipped = []
    manifest = []  # 处理清单

    def _compute_hash(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _check_dup(content_hash: str):
        return db.query(RawSourceModel).filter(
            RawSourceModel.content_hash == content_hash
        ).first()

    def _copy_to_storage(src_path, suffix=""):
        dest = UPLOAD_DIR / "raw_sources" / f"{str(ULID())}{suffix or src_path.suffix}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_path, dest)
        return str(dest)

    def _create_single_file_source(f, label=""):
        """处理单个非图片文件（txt/md/pdf/docx）"""
        content = f.read_bytes()
        file_hash = _compute_hash(content)
        existing = _check_dup(file_hash)
        if existing:
            source_ids.append(existing.id)
            skipped.append(f"[dup] {label or f.name}")
            return

        ext = f.suffix.lower()
        source_type = {".txt": "note", ".md": "note", ".pdf": "report", ".docx": "report"}.get(ext, "note")
        raw_text = None
        if ext in (".txt", ".md"):
            raw_text = content.decode("utf-8", errors="ignore")

        dest = _copy_to_storage(f)
        src = RawSourceModel(
            id=str(ULID()),
            source_type=source_type,
            title=f.name if not label else f"{label}: {f.name}",
            storage_path=dest,
            raw_text=raw_text,
            content_hash=file_hash,
            word_count=len(raw_text) if raw_text else 0,
            user_trust_level=user_trust_level or None,
            user_note=user_note or None,
            status="pending", language="zh",
            created_at=now, updated_at=now,
        )
        created = repo.create(src)
        source_ids.append(created.id)
        manifest.append({"type": source_type, "title": src.title, "id": created.id})

    def _create_image_batch_source(image_files, batch_label):
        """将多张图片合并为一个 source（多图拼接模式）

        新增：图片分类 — 区分聊天截图 vs 内容图片，
        内容图片标记为 attachment_type="content_image" 便于下游位置匹配。
        """
        from app.services.file_parser import classify_image_in_batch

        combined_bytes = b"".join(f.read_bytes() for f in image_files)
        combined_hash = _compute_hash(combined_bytes)

        existing = _check_dup(combined_hash)
        if existing:
            source_ids.append(existing.id)
            skipped.append(f"[dup] {batch_label} ({len(image_files)} images)")
            return

        # ── 图片分类 ──
        all_paths = [str(f) for f in image_files]
        classifications = {}
        for f in image_files:
            cls = classify_image_in_batch(str(f), all_paths)
            classifications[f.name] = cls

        chat_screenshots = [f for f in image_files if classifications[f.name] != "content_image"]
        content_images = [f for f in image_files if classifications[f.name] == "content_image"]
        n_chat = len(chat_screenshots)
        n_content = len(content_images)

        logger.info(f"[TestInbox] {batch_label}: {n_chat} chat screenshots + {n_content} content images")

        src = RawSourceModel(
            id=str(ULID()),
            source_type="image",
            title=f"{batch_label} ({len(image_files)} images)",
            content_hash=combined_hash,
            user_trust_level=user_trust_level or None,
            user_note=user_note or None,
            status="pending", language="zh",
            created_at=now, updated_at=now,
        )
        created = repo.create(src)

        # 先存聊天截图（按文件名排序），再存内容图
        ordered_files = sorted(chat_screenshots, key=lambda x: x.name) + \
                        sorted(content_images, key=lambda x: x.name)

        for idx, img_file in enumerate(ordered_files):
            dest = UPLOAD_DIR / "raw_sources" / f"{created.id}_{idx}{img_file.suffix}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(img_file, dest)
            # 内容图使用特殊 attachment_type 标记
            att_type = "content_image" if classifications[img_file.name] == "content_image" else "image"
            att = SourceAttachment(
                id=str(ULID()),
                source_id=created.id,
                attachment_type=att_type,
                file_name=img_file.name,
                storage_path=str(dest),
                mime_type=f"image/{img_file.suffix.lstrip('.').replace('jpg', 'jpeg')}",
                file_size_bytes=img_file.stat().st_size,
                created_at=now, updated_at=now,
            )
            db.add(att)
        # 第一张图片路径作为 source 的 storage_path
        first_dest = UPLOAD_DIR / "raw_sources" / f"{created.id}_0{ordered_files[0].suffix}"
        created.storage_path = str(first_dest)
        db.flush()
        source_ids.append(created.id)
        manifest.append({
            "type": "image_batch", "title": src.title, "id": created.id,
            "files": [f.name for f in ordered_files],
            "chat_screenshots": n_chat, "content_images": n_content,
        })

    def _create_single_image_source(img_file, label=""):
        """处理单张图片"""
        content = img_file.read_bytes()
        file_hash = _compute_hash(content)
        existing = _check_dup(file_hash)
        if existing:
            source_ids.append(existing.id)
            skipped.append(f"[dup] {label or img_file.name}")
            return

        dest = _copy_to_storage(img_file)
        src = RawSourceModel(
            id=str(ULID()),
            source_type="image",
            title=label or img_file.name,
            storage_path=dest,
            content_hash=file_hash,
            user_trust_level=user_trust_level or None,
            user_note=user_note or None,
            status="pending", language="zh",
            created_at=now, updated_at=now,
        )
        created = repo.create(src)
        source_ids.append(created.id)
        manifest.append({"type": "image_single", "title": src.title, "id": created.id})

    # ── 1. 处理子文件夹批次 ──
    for item in sorted(inbox.iterdir(), key=lambda x: x.name):
        if not item.is_dir() or item.name == "README.md":
            continue

        batch_files = _collect_files_recursive(item)
        if not batch_files:
            continue

        batch_label = f"batch_{item.name}"
        images = sorted([f for f in batch_files if f.suffix.lower() in _IMAGE_EXTS],
                        key=lambda x: x.name)
        others = [f for f in batch_files if f.suffix.lower() not in _IMAGE_EXTS]

        # 图片合并为一个 source
        if len(images) > 1:
            _create_image_batch_source(images, batch_label)
        elif len(images) == 1:
            _create_single_image_source(images[0], batch_label)

        # 非图片文件各自独立
        for f in others:
            _create_single_file_source(f, label=batch_label)

    # ── 2. 处理根目录文件 ──
    for item in sorted(inbox.iterdir(), key=lambda x: x.name):
        if not item.is_file() or item.suffix.lower() not in _SUPPORTED_EXTS:
            continue
        if item.name == "README.md":
            continue

        if item.suffix.lower() in _IMAGE_EXTS:
            _create_single_image_source(item, f"root: {item.name}")
        else:
            _create_single_file_source(item, label="root")

    safe_commit(db)

    # ── 3. 触发 ingestion 处理 ──
    pending_ids = [
        sid for sid in source_ids
        if db.query(RawSourceModel).filter(
            RawSourceModel.id == sid, RawSourceModel.status == "pending"
        ).first()
    ]
    from app.workflows.ingestion_workflow import IngestionWorkflow
    wf = IngestionWorkflow(db)
    results = []
    for sid in pending_ids:
        r = wf.process_source(sid)
        results.append(r)

    return {
        "total_sources": len(source_ids),
        "processed": len(pending_ids),
        "skipped_duplicates": skipped,
        "source_ids": source_ids,
        "manifest": manifest,
        "results": results,
    }


# ── R4: 端到端评估数据收集 ────────────────────────────────────────
# 注意路由顺序：/evaluate/recent 和 /evaluate/batch 必须在 /evaluate/{source_id} 之前

@router.get("/evaluate/recent")
def get_recent_evaluation(
    limit: int = Query(5, ge=1, le=20),
    format: str = Query("markdown", enum=["markdown", "json"]),
    db: Session = Depends(get_db),
):
    """获取最近 N 个已完成来源的评估报告"""
    recent = (
        db.query(RawSource)
        .filter(RawSource.status == "completed")
        .order_by(RawSource.created_at.desc())
        .limit(limit)
        .all()
    )
    from app.services.evaluation_report import build_source_evaluation
    reports = [build_source_evaluation(db, s.id) for s in recent]
    reports = [r for r in reports if r]
    if format == "markdown":
        from fastapi.responses import PlainTextResponse
        combined = "\n\n---\n\n".join(r["markdown"] for r in reports)
        return PlainTextResponse(combined, media_type="text/markdown; charset=utf-8")
    return [r["data"] for r in reports]


@router.post("/evaluate/batch")
def get_batch_evaluation(
    source_ids: list[str],
    format: str = Query("markdown", enum=["markdown", "json"]),
    db: Session = Depends(get_db),
):
    """批量组装多个来源的评估报告"""
    from app.services.evaluation_report import build_source_evaluation
    reports = []
    for sid in source_ids[:10]:  # 限制最多 10 个
        r = build_source_evaluation(db, sid)
        if r:
            reports.append(r)
    if format == "markdown":
        from fastapi.responses import PlainTextResponse
        combined = "\n\n---\n\n".join(r["markdown"] for r in reports)
        return PlainTextResponse(combined, media_type="text/markdown; charset=utf-8")
    return [r["data"] for r in reports]


@router.get("/evaluate/{source_id}")
def get_evaluation_report(
    source_id: str,
    format: str = Query("markdown", enum=["markdown", "json"]),
    db: Session = Depends(get_db),
):
    """组装单个来源的完整链路报告，便于一键复制给 LLM 审阅。

    返回 markdown 格式时，输出可直接粘贴到对话中。
    返回 json 格式时，输出结构化数据。
    """
    from app.services.evaluation_report import build_source_evaluation
    report = build_source_evaluation(db, source_id)
    if not report:
        from fastapi import HTTPException
        raise HTTPException(404, "Source not found")
    if format == "markdown":
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(report["markdown"], media_type="text/markdown; charset=utf-8")
    return report["data"]
