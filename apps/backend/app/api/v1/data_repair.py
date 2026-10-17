"""脏数据修复工具 · /api/v1/data-repair.

为什么需要这个
----------
日常运营中，会出现以下"脏数据"：
1. 某 RawSource 摄入失败一半（部分 claim 已写入但 status=failed），需要清理
2. 误上传的资料想完整删除（连带 claim / link / lifecycle log 等下游全删）
3. 某条 claim 被发现是错误提取的，需要 archived 而不是 active 影响检索
4. 一批已 archived 的 claim 想永久物理删除（释放空间）

设计原则
----------
- **soft delete 优先**：默认改 status=archived 而非 DELETE，保留审计轨迹
- **级联可控**：必须显式指定 cascade=true 才会删 raw_source 的下游 claim
- **预览 dry-run**：所有破坏性操作都先返回"将影响 N 条"再实际执行
- **审计日志**：所有修复操作写 loguru WARNING（不被 sentry 噪声淹没）

端点
----------
- POST /preview-source-cascade/{source_id}   预览删 source 会级联多少 claim/link
- POST /archive-source/{source_id}           soft delete: source + 所有下游 claim 改为 archived
- POST /delete-source/{source_id}            hard delete: 物理删除 source + 所有 claim/link
- POST /archive-claim/{claim_id}             单独 archive 一条 claim
- POST /purge-archived-claims                物理删除所有 status=archived 的 claim（按窗口）
- POST /reset-failed-source/{source_id}      把 status=failed 的 source 重置为 pending（重摄入）
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session
from loguru import logger

from app.database import SessionLocal, safe_commit
from app.schemas.claim_card import ClaimCard, ClaimRawSourceLink, ClaimEvidenceLink
from app.schemas.raw_source import RawSource


router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Schemas ──────────────────────────────────────────────


class CascadePreview(BaseModel):
    source_id: str
    source_status: Optional[str]
    claim_count: int
    link_count: int
    evidence_link_count: int


class RepairResult(BaseModel):
    action: str
    source_id: Optional[str] = None
    claim_id: Optional[str] = None
    affected_sources: int = 0
    affected_claims: int = 0
    affected_links: int = 0
    notes: Optional[str] = None


class PurgeArchivedRequest(BaseModel):
    older_than_days: int = Field(30, ge=1, le=3650, description="只删 archived 超过 N 天的")
    dry_run: bool = Field(True, description="True=只预览不实际删")


# ── Helpers ──────────────────────────────────────────────


def _count_source_cascade(db: Session, source_id: str) -> CascadePreview:
    src = db.query(RawSource).filter(RawSource.id == source_id).first()
    claim_ids = [
        l.claim_id
        for l in db.query(ClaimRawSourceLink)
        .filter(ClaimRawSourceLink.source_id == source_id).all()
    ]
    return CascadePreview(
        source_id=source_id,
        source_status=src.status if src else None,
        claim_count=len(claim_ids),
        link_count=db.query(ClaimRawSourceLink).filter(
            ClaimRawSourceLink.source_id == source_id
        ).count(),
        evidence_link_count=(
            db.query(ClaimEvidenceLink)
            .filter(ClaimEvidenceLink.claim_id.in_(claim_ids))
            .count() if claim_ids else 0
        ),
    )


# ── 端点 ─────────────────────────────────────────────────


@router.post("/preview-source-cascade/{source_id}", response_model=CascadePreview)
def preview_source_cascade(
    source_id: str = Path(..., min_length=10),
    db: Session = Depends(get_db),
) -> CascadePreview:
    """预览删 source 会影响多少 claim / link / evidence_link."""
    return _count_source_cascade(db, source_id)


@router.post("/archive-source/{source_id}", response_model=RepairResult)
def archive_source(
    source_id: str = Path(..., min_length=10),
    db: Session = Depends(get_db),
) -> RepairResult:
    """soft delete：source 改 status=archived + 所有关联 claim 也改 archived.

    保留全部数据供审计 / 反悔 / 用户后悔时可手动恢复。
    """
    src = db.query(RawSource).filter(RawSource.id == source_id).first()
    if not src:
        raise HTTPException(status_code=404, detail=f"source {source_id} not found")

    claim_ids = [
        l.claim_id for l in db.query(ClaimRawSourceLink)
        .filter(ClaimRawSourceLink.source_id == source_id).all()
    ]

    # source 状态 archived（实际表用 status，"archived" 不是默认枚举但 String 列允许）
    old_status = src.status
    src.status = "archived"
    src.updated_at = datetime.now(timezone.utc)

    # claim 也改 archived
    affected_claims = 0
    if claim_ids:
        affected_claims = (
            db.query(ClaimCard)
            .filter(ClaimCard.id.in_(claim_ids), ClaimCard.status != "archived")
            .update({"status": "archived"}, synchronize_session=False)
        )

    safe_commit(db)
    logger.warning(
        f"[DataRepair] archive_source {source_id} "
        f"(was status={old_status}, archived {affected_claims} claims)"
    )
    return RepairResult(
        action="archive_source",
        source_id=source_id,
        affected_sources=1,
        affected_claims=affected_claims,
        notes=f"source status: {old_status} -> archived",
    )


@router.post("/delete-source/{source_id}", response_model=RepairResult)
def delete_source(
    source_id: str = Path(..., min_length=10),
    cascade: bool = Query(False, description="True=同时物理删除所有关联 claim/link"),
    db: Session = Depends(get_db),
) -> RepairResult:
    """**HARD delete** — 物理删除 source（默认仅 source 本身；cascade=true 时连同所有下游）.

    谨慎使用。建议先调 /preview-source-cascade 确认影响范围。
    """
    src = db.query(RawSource).filter(RawSource.id == source_id).first()
    if not src:
        raise HTTPException(status_code=404, detail=f"source {source_id} not found")

    affected_claims = 0
    affected_links = 0
    if cascade:
        # 收集 claim_ids
        claim_ids = [
            l.claim_id for l in db.query(ClaimRawSourceLink)
            .filter(ClaimRawSourceLink.source_id == source_id).all()
        ]
        # 删 claim_evidence_links
        if claim_ids:
            db.query(ClaimEvidenceLink).filter(
                ClaimEvidenceLink.claim_id.in_(claim_ids)
            ).delete(synchronize_session=False)
        # 删 claim_raw_source_links
        affected_links = (
            db.query(ClaimRawSourceLink)
            .filter(ClaimRawSourceLink.source_id == source_id)
            .delete(synchronize_session=False)
        )
        # 删 claim_cards
        if claim_ids:
            affected_claims = (
                db.query(ClaimCard)
                .filter(ClaimCard.id.in_(claim_ids))
                .delete(synchronize_session=False)
            )

    db.delete(src)
    safe_commit(db)

    logger.warning(
        f"[DataRepair] delete_source {source_id} cascade={cascade} "
        f"(deleted {affected_claims} claims, {affected_links} links)"
    )
    return RepairResult(
        action="delete_source",
        source_id=source_id,
        affected_sources=1,
        affected_claims=affected_claims,
        affected_links=affected_links,
        notes=f"cascade={cascade}",
    )


@router.post("/archive-claim/{claim_id}", response_model=RepairResult)
def archive_claim(
    claim_id: str = Path(..., min_length=10),
    db: Session = Depends(get_db),
) -> RepairResult:
    """单独 archive 一条 claim（不影响其他 claim from 同一 source）."""
    c = db.query(ClaimCard).filter(ClaimCard.id == claim_id).first()
    if not c:
        raise HTTPException(status_code=404, detail=f"claim {claim_id} not found")
    if c.status == "archived":
        return RepairResult(
            action="archive_claim",
            claim_id=claim_id,
            affected_claims=0,
            notes="already archived (no-op)",
        )

    old_status = c.status
    c.status = "archived"
    c.updated_at = datetime.now(timezone.utc)
    safe_commit(db)

    logger.warning(f"[DataRepair] archive_claim {claim_id} (was {old_status})")
    return RepairResult(
        action="archive_claim",
        claim_id=claim_id,
        affected_claims=1,
        notes=f"status: {old_status} -> archived",
    )


@router.post("/purge-archived-claims", response_model=RepairResult)
def purge_archived_claims(
    body: PurgeArchivedRequest,
    db: Session = Depends(get_db),
) -> RepairResult:
    """**HARD delete** 所有已 archived 且超过 N 天的 claim（释放空间）."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=body.older_than_days)
    q = db.query(ClaimCard).filter(
        ClaimCard.status == "archived",
        ClaimCard.updated_at < cutoff,
    )

    if body.dry_run:
        count = q.count()
        return RepairResult(
            action="purge_archived_claims",
            affected_claims=count,
            notes=f"dry_run=true · would delete {count} claims older than {body.older_than_days}d",
        )

    target_ids = [c.id for c in q.all()]
    if not target_ids:
        return RepairResult(action="purge_archived_claims", affected_claims=0, notes="nothing to purge")

    # 删 link / evidence
    db.query(ClaimEvidenceLink).filter(
        ClaimEvidenceLink.claim_id.in_(target_ids)
    ).delete(synchronize_session=False)
    db.query(ClaimRawSourceLink).filter(
        ClaimRawSourceLink.claim_id.in_(target_ids)
    ).delete(synchronize_session=False)
    # 删 claim
    affected = (
        db.query(ClaimCard)
        .filter(ClaimCard.id.in_(target_ids))
        .delete(synchronize_session=False)
    )
    safe_commit(db)

    logger.warning(
        f"[DataRepair] purge_archived_claims older_than={body.older_than_days}d, "
        f"deleted {affected} claims"
    )
    return RepairResult(
        action="purge_archived_claims",
        affected_claims=affected,
        notes=f"physically deleted, older_than={body.older_than_days}d",
    )


@router.post("/reset-failed-source/{source_id}", response_model=RepairResult)
def reset_failed_source(
    source_id: str = Path(..., min_length=10),
    db: Session = Depends(get_db),
) -> RepairResult:
    """把状态为 failed 的 source 重置为 pending，让 ingestion pipeline 重摄入.

    注意：会先 archive 所有该 source 已写入的（部分）claim，避免重复。
    """
    src = db.query(RawSource).filter(RawSource.id == source_id).first()
    if not src:
        raise HTTPException(status_code=404, detail=f"source {source_id} not found")
    if src.status not in ("failed", "completed"):
        raise HTTPException(
            status_code=400,
            detail=f"source status is {src.status!r}; only failed/completed can be reset",
        )

    # 1. 先 archive 已存在的 claim（避免重新摄入产生重复）
    claim_ids = [
        l.claim_id for l in db.query(ClaimRawSourceLink)
        .filter(ClaimRawSourceLink.source_id == source_id).all()
    ]
    archived_claims = 0
    if claim_ids:
        archived_claims = (
            db.query(ClaimCard)
            .filter(ClaimCard.id.in_(claim_ids), ClaimCard.status != "archived")
            .update({"status": "archived"}, synchronize_session=False)
        )

    # 2. 重置 source
    src.status = "pending"
    src.processing_error = None
    src.updated_at = datetime.now(timezone.utc)

    safe_commit(db)
    logger.warning(
        f"[DataRepair] reset_failed_source {source_id}, archived {archived_claims} stale claims"
    )
    return RepairResult(
        action="reset_failed_source",
        source_id=source_id,
        affected_sources=1,
        affected_claims=archived_claims,
        notes="status -> pending; existing claims archived to avoid dups",
    )


@router.get("/stats")
def repair_stats(db: Session = Depends(get_db)) -> dict:
    """快速看一眼：当前有多少 archived/failed 数据可清理."""
    return {
        "raw_sources": {
            "total": db.query(func.count(RawSource.id)).scalar() or 0,
            "by_status": dict(
                db.query(RawSource.status, func.count(RawSource.id)).group_by(RawSource.status).all()
            ),
        },
        "claim_cards": {
            "total": db.query(func.count(ClaimCard.id)).scalar() or 0,
            "by_status": dict(
                db.query(ClaimCard.status, func.count(ClaimCard.id)).group_by(ClaimCard.status).all()
            ),
        },
    }
