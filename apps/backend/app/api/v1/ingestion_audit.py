"""Ingestion Audit API — 原始数据与处理结果的对比审计接口

供大模型或人工审查：
- 对比原始文件与数据库中存储的处理结果
- 检测信息丢失、重复、分类错误等问题
- 支持按 source_id 查看单条记录或批量审计所有记录
"""
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.schemas.raw_source import RawSource, SourceAttachment
from app.schemas.claim_card import ClaimCard, ClaimRawSourceLink
from app.services.file_parser import parse_wechat_chat

router = APIRouter()


# ── Response Models ──────────────────────────────────────

class AttachmentInfo(BaseModel):
    file_name: str
    attachment_type: str
    file_size_bytes: int
    file_exists: bool


class ClaimInfo(BaseModel):
    summary: str
    full_quote: Optional[str] = None
    full_quote_length: int = 0
    claim_type: str
    stance: str
    confidence: float
    asset_scope: list = []


class ChatAnalysis(BaseModel):
    is_chat: bool = False
    total_messages: int = 0
    speakers: dict = {}
    main_speaker: Optional[str] = None
    image_positions: list = []
    time_range: Optional[dict] = None
    unresolved_image_count: int = 0
    resolved_image_count: int = 0


class TextQuality(BaseModel):
    total_chars: int = 0
    total_lines: int = 0
    unique_lines: int = 0
    duplicate_lines: int = 0
    duplicate_examples: list = []
    has_ocr_preamble: bool = False


class AuditIssue(BaseModel):
    severity: str  # CRITICAL | WARNING | INFO
    message: str


class SourceAuditReport(BaseModel):
    source_id: str
    title: str
    source_type: str
    status: str
    processing_error: Optional[str] = None
    speaker_hint: Optional[str] = None
    theme_hint: Optional[str] = None
    word_count: int = 0
    created_at: Optional[str] = None

    raw_text_preview: str = ""
    raw_text_length: int = 0
    article_summary_preview: str = ""
    article_summary_length: int = 0

    attachments: List[AttachmentInfo] = []
    claims: List[ClaimInfo] = []
    claims_total: int = 0
    claims_with_full_quote: int = 0

    chat_analysis: Optional[ChatAnalysis] = None
    text_quality: TextQuality = TextQuality()

    issues: List[AuditIssue] = []


class BatchAuditReport(BaseModel):
    total_sources: int = 0
    total_claims: int = 0
    total_claims_with_quote: int = 0
    total_issues: int = 0
    critical_issues: int = 0
    sources: List[SourceAuditReport] = []
    summary: str = ""


# ── Helpers ──────────────────────────────────────────────

def _audit_source(s: RawSource, db: Session) -> SourceAuditReport:
    """对单个 source 进行完整审计。"""
    text = s.raw_text or ""
    issues: List[AuditIssue] = []

    # ── Attachments ──
    atts = (
        db.query(SourceAttachment)
        .filter(SourceAttachment.source_id == s.id)
        .order_by(SourceAttachment.created_at.asc())
        .all()
    )
    att_infos = []
    for a in atts:
        exists = Path(a.storage_path).exists() if a.storage_path else False
        att_infos.append(AttachmentInfo(
            file_name=a.file_name or "",
            attachment_type=a.attachment_type or "",
            file_size_bytes=a.file_size_bytes or 0,
            file_exists=exists,
        ))
        if not exists:
            issues.append(AuditIssue(severity="WARNING", message=f"Attachment file missing: {a.file_name}"))

    # ── Claims ──
    links = db.query(ClaimRawSourceLink).filter(ClaimRawSourceLink.source_id == s.id).all()
    claim_infos = []
    has_quote_count = 0
    for link in links:
        c = db.query(ClaimCard).filter(ClaimCard.id == link.claim_id).first()
        if c:
            fq = c.full_quote or ""
            if fq.strip():
                has_quote_count += 1
            claim_infos.append(ClaimInfo(
                summary=c.summary or "",
                full_quote=fq[:300] if fq else None,
                full_quote_length=len(fq),
                claim_type=c.claim_type or "",
                stance=c.stance or "",
                confidence=c.confidence or 0,
                asset_scope=c.asset_scope or [],
            ))

    # ── Text Quality ──
    lines = text.split('\n')
    non_empty = [l.strip() for l in lines if l.strip() and len(l.strip()) > 5]
    counts = Counter(non_empty)
    dups = {k: v for k, v in counts.items() if v > 1}
    dup_count = sum(v - 1 for v in dups.values())

    dup_examples = []
    for line, count in sorted(dups.items(), key=lambda x: -x[1])[:5]:
        dup_examples.append(f"[{count}x] {line[:80]}")

    import re
    has_preamble = bool(re.search(r'以下是.*转录|根据您提供的图片', text[:200]))

    text_quality = TextQuality(
        total_chars=len(text),
        total_lines=len(non_empty),
        unique_lines=len(counts),
        duplicate_lines=dup_count,
        duplicate_examples=dup_examples,
        has_ocr_preamble=has_preamble,
    )

    # ── Chat Analysis ──
    chat_analysis = None
    parsed = parse_wechat_chat(text) if text else None
    if parsed and parsed.get("is_chat"):
        msgs = parsed.get("messages", [])
        unresolved = sum(1 for m in msgs if "[图片]" in m.get("content", ""))
        resolved = sum(1 for m in msgs if "[图片内容：" in m.get("content", ""))
        chat_analysis = ChatAnalysis(
            is_chat=True,
            total_messages=parsed["total_messages"],
            speakers=parsed["speakers"],
            main_speaker=parsed.get("main_speaker"),
            image_positions=parsed.get("image_positions", []),
            time_range=parsed.get("time_range"),
            unresolved_image_count=unresolved,
            resolved_image_count=resolved,
        )

    # ── Issues Detection ──
    if not text:
        if (s.status or "").lower() in {"pending", "processing", "failed"}:
            issues.append(AuditIssue(
                severity="WARNING",
                message=f"raw_text 尚未产出；当前处理状态为 {s.status or 'unknown'}，需要重试或人工复核"
            ))
        else:
            issues.append(AuditIssue(severity="CRITICAL", message="raw_text 为空，原始内容完全丢失"))
    elif len(text) < 50:
        issues.append(AuditIssue(severity="WARNING", message=f"raw_text 极短 ({len(text)} chars)"))

    if not claim_infos:
        issues.append(AuditIssue(severity="WARNING", message="0 claims extracted"))
    elif has_quote_count == 0 and len(claim_infos) > 0:
        issues.append(AuditIssue(severity="WARNING", message=f"0/{len(claim_infos)} claims have full_quote"))

    if dup_count > 5:
        issues.append(AuditIssue(severity="WARNING", message=f"{dup_count} duplicate lines in text"))

    if has_preamble:
        issues.append(AuditIssue(severity="INFO", message="Text contains OCR preamble (should be stripped)"))

    if chat_analysis and chat_analysis.is_chat and s.source_type != "chat":
        issues.append(AuditIssue(
            severity="WARNING",
            message=f"Detected as chat ({chat_analysis.total_messages} msgs) but source_type='{s.source_type}'"
        ))

    if not s.speaker_hint:
        issues.append(AuditIssue(severity="INFO", message="No speaker_hint"))

    if not s.theme_hint:
        issues.append(AuditIssue(severity="INFO", message="No theme_hint"))

    return SourceAuditReport(
        source_id=s.id,
        title=s.title or "",
        source_type=s.source_type or "",
        status=s.status or "",
        processing_error=s.processing_error,
        speaker_hint=s.speaker_hint,
        theme_hint=s.theme_hint,
        word_count=s.word_count or 0,
        created_at=s.created_at.isoformat() if s.created_at else None,
        raw_text_preview=text[:1000] if text else "",
        raw_text_length=len(text),
        article_summary_preview=(s.article_summary or "")[:500],
        article_summary_length=len(s.article_summary or ""),
        attachments=att_infos,
        claims=claim_infos,
        claims_total=len(claim_infos),
        claims_with_full_quote=has_quote_count,
        chat_analysis=chat_analysis,
        text_quality=text_quality,
        issues=issues,
    )


# ── Endpoints ────────────────────────────────────────────

@router.get("/sources", response_model=BatchAuditReport)
def audit_all_sources(
    title_filter: Optional[str] = Query(None, description="Filter by title (LIKE match)"),
    source_type: Optional[str] = Query(None, description="Filter by source_type"),
    only_issues: bool = Query(False, description="Only return sources with issues"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """批量审计所有 source 的处理质量。

    返回结构化报告，包含每个 source 的：
    - 元数据（type, speaker, theme）
    - 文本质量指标（重复行、OCR 前言）
    - 聊天结构化分析（如适用）
    - Claims 完整性（full_quote 填充率）
    - 自动检测的问题列表
    """
    query = db.query(RawSource).order_by(RawSource.created_at.desc())
    if title_filter:
        query = query.filter(RawSource.title.like(f"%{title_filter}%"))
    if source_type:
        query = query.filter(RawSource.source_type == source_type)

    sources = query.limit(limit).all()
    reports = [_audit_source(s, db) for s in sources]

    if only_issues:
        reports = [r for r in reports if r.issues]

    total_claims = sum(r.claims_total for r in reports)
    total_quotes = sum(r.claims_with_full_quote for r in reports)
    total_issues = sum(len(r.issues) for r in reports)
    critical = sum(1 for r in reports for i in r.issues if i.severity == "CRITICAL")

    # Generate summary
    lines = [
        f"Audited {len(reports)} sources, {total_claims} claims.",
        f"full_quote coverage: {total_quotes}/{total_claims} ({total_quotes*100//max(total_claims,1)}%).",
        f"Issues: {total_issues} total, {critical} critical.",
    ]
    problem_sources = [r.title for r in reports if any(i.severity in ("CRITICAL", "WARNING") for i in r.issues)]
    if problem_sources:
        lines.append(f"Sources with problems: {', '.join(problem_sources[:10])}")

    return BatchAuditReport(
        total_sources=len(reports),
        total_claims=total_claims,
        total_claims_with_quote=total_quotes,
        total_issues=total_issues,
        critical_issues=critical,
        sources=reports,
        summary="\n".join(lines),
    )


@router.get("/source/{source_id}", response_model=SourceAuditReport)
def audit_single_source(
    source_id: str,
    db: Session = Depends(get_db),
):
    """审计单个 source 的处理质量，返回完整对比报告。"""
    s = db.query(RawSource).filter(RawSource.id == source_id).first()
    if not s:
        from fastapi import HTTPException
        raise HTTPException(404, f"Source {source_id} not found")
    return _audit_source(s, db)


@router.get("/source/{source_id}/full_text")
def get_source_full_text(
    source_id: str,
    db: Session = Depends(get_db),
):
    """获取 source 的完整 raw_text（用于 LLM 对比原始内容时使用）。"""
    s = db.query(RawSource).filter(RawSource.id == source_id).first()
    if not s:
        from fastapi import HTTPException
        raise HTTPException(404, f"Source {source_id} not found")
    return {
        "source_id": s.id,
        "title": s.title,
        "source_type": s.source_type,
        "raw_text": s.raw_text or "",
        "raw_text_length": len(s.raw_text or ""),
        "article_summary": s.article_summary or "",
    }


@router.get("/source/{source_id}/claims")
def get_source_claims_detail(
    source_id: str,
    db: Session = Depends(get_db),
):
    """获取 source 关联的所有 claims 的完整信息（包含 full_quote 全文）。"""
    s = db.query(RawSource).filter(RawSource.id == source_id).first()
    if not s:
        from fastapi import HTTPException
        raise HTTPException(404, f"Source {source_id} not found")

    links = db.query(ClaimRawSourceLink).filter(ClaimRawSourceLink.source_id == s.id).all()
    claims = []
    for link in links:
        c = db.query(ClaimCard).filter(ClaimCard.id == link.claim_id).first()
        if c:
            claims.append({
                "claim_id": c.id,
                "summary": c.summary,
                "full_quote": c.full_quote,
                "claim_type": c.claim_type,
                "stance": c.stance,
                "time_horizon": c.time_horizon,
                "confidence": c.confidence,
                "asset_scope": c.asset_scope or [],
                "theme_scope": c.theme_scope or [],
                "reasoning_basis": c.reasoning_basis or [],
                "conditions": c.conditions or [],
                "invalidation_conditions": c.invalidation_conditions or [],
            })

    return {
        "source_id": s.id,
        "title": s.title,
        "source_type": s.source_type,
        "claims_count": len(claims),
        "claims": claims,
    }
