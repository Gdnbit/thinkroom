"""Knowledge Lint Service — 知识库健康自检

定期或手动触发，扫描知识库中的潜在问题：
- 过期但仍标记为 active 的 claim
- 未解决的矛盾
- 低质量 claim（低置信度、无依据）
- 孤立 claim（无来源关联）
- 知识盲区（某些资产/主题覆盖不足）
- Speaker 权威度过期需校准
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from collections import Counter
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.schemas.claim_card import ClaimCard, ClaimRawSourceLink
from app.schemas.raw_source import RawSource
from app.schemas.speaker_profile import SpeakerProfile

logger = logging.getLogger(__name__)

# ── 阈值配置 ──────────────────────────────────────────────
_LOW_CONFIDENCE_THRESHOLD = 0.4
_STALE_DAYS = 90  # claim 超过此天数未被 lifecycle check 视为过期风险
_SPEAKER_CALIBRATION_DAYS = 60  # speaker 校准超过此天数需重新评估
_MIN_CLAIMS_PER_ASSET = 3  # 资产覆盖不足阈值


def _as_aware_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Normalize DB datetimes before comparing with timezone-aware `now`."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class LintIssue:
    """单条 lint 问题"""
    __slots__ = ("severity", "category", "message", "claim_id", "extra")

    def __init__(self, severity: str, category: str, message: str,
                 claim_id: Optional[str] = None, extra: Optional[dict] = None):
        self.severity = severity  # critical | warning | info
        self.category = category
        self.message = message
        self.claim_id = claim_id
        self.extra = extra or {}

    def to_dict(self) -> dict:
        d = {
            "severity": self.severity,
            "category": self.category,
            "message": self.message,
        }
        if self.claim_id:
            d["claim_id"] = self.claim_id
        if self.extra:
            d["extra"] = self.extra
        return d


def run_knowledge_lint(db: Session) -> dict:
    """执行全套知识库健康检查，返回结构化报告"""
    now = datetime.now(timezone.utc)
    issues: list[LintIssue] = []

    # ── 1. 过期风险检测 ──
    _check_expired_claims(db, now, issues)

    # ── 2. 未解决矛盾 ──
    _check_unresolved_contradictions(db, issues)

    # ── 3. 低质量 claim ──
    _check_low_quality_claims(db, issues)

    # ── 4. 孤立 claim（无来源关联）──
    _check_orphan_claims(db, issues)

    # ── 5. 知识覆盖盲区 ──
    coverage_stats = _check_coverage_gaps(db, issues)

    # ── 6. Speaker 校准检查 ──
    _check_speaker_calibration(db, now, issues)

    # ── 7. 数据质量统计 ──
    quality_stats = _compute_quality_stats(db, now)

    # 分类汇总
    by_severity = Counter(i.severity for i in issues)
    by_category = Counter(i.category for i in issues)

    report = {
        "run_at": now.isoformat(),
        "total_issues": len(issues),
        "by_severity": dict(by_severity),
        "by_category": dict(by_category),
        "issues": [i.to_dict() for i in issues],
        "coverage": coverage_stats,
        "quality_stats": quality_stats,
        "health_score": _compute_health_score(issues, quality_stats),
    }

    logger.info(f"[KnowledgeLint] Completed: {len(issues)} issues, "
                f"health_score={report['health_score']}")
    return report


def _check_expired_claims(db: Session, now: datetime, issues: list[LintIssue]):
    """检测 valid_until 已过但 status 仍为 active 的 claim"""
    expired = db.query(ClaimCard).filter(
        ClaimCard.status == "active",
        ClaimCard.valid_until.isnot(None),
        ClaimCard.valid_until < now,
    ).all()

    for c in expired:
        valid_until = _as_aware_utc(c.valid_until)
        days_past = (now - valid_until).days if valid_until else 0
        issues.append(LintIssue(
            severity="warning" if days_past < 30 else "critical",
            category="expired_active",
            message=f"Claim 已过期 {days_past} 天但仍为 active: {(c.summary or '')[:60]}",
            claim_id=c.id,
            extra={"valid_until": valid_until.isoformat() if valid_until else None, "days_past": days_past},
        ))

    # 长时间未做 lifecycle check 的 active claim
    stale_threshold = now - timedelta(days=_STALE_DAYS)
    stale = db.query(ClaimCard).filter(
        ClaimCard.status == "active",
        ClaimCard.valid_until.is_(None),
        ClaimCard.last_lifecycle_check < stale_threshold,
    ).count()
    if stale > 0:
        issues.append(LintIssue(
            severity="info",
            category="stale_lifecycle",
            message=f"{stale} 条 active claim 超过 {_STALE_DAYS} 天未做生命周期检查",
            extra={"count": stale},
        ))


def _check_unresolved_contradictions(db: Session, issues: list[LintIssue]):
    """检测标记了矛盾但未被取代/归档的 claim"""
    contradicted = db.query(ClaimCard).filter(
        ClaimCard.is_contradicted == True,
        ClaimCard.status == "active",
    ).all()

    for c in contradicted:
        issues.append(LintIssue(
            severity="warning",
            category="unresolved_contradiction",
            message=f"Claim 被标记矛盾但仍 active: {(c.summary or '')[:60]}",
            claim_id=c.id,
            extra={"contradiction_notes": (c.contradiction_notes or "")[:200]},
        ))


def _check_low_quality_claims(db: Session, issues: list[LintIssue]):
    """检测低置信度或无依据的 active claim"""
    low_conf = db.query(ClaimCard).filter(
        ClaimCard.status == "active",
        ClaimCard.confidence < _LOW_CONFIDENCE_THRESHOLD,
    ).all()

    for c in low_conf:
        issues.append(LintIssue(
            severity="info",
            category="low_confidence",
            message=f"低置信度 claim ({c.confidence:.0%}): {(c.summary or '')[:60]}",
            claim_id=c.id,
            extra={"confidence": c.confidence},
        ))

    # 无 reasoning_basis 的 claim
    no_basis = db.query(ClaimCard).filter(
        ClaimCard.status == "active",
    ).all()
    no_basis_count = sum(1 for c in no_basis if not c.reasoning_basis)
    if no_basis_count > 0:
        issues.append(LintIssue(
            severity="info",
            category="no_reasoning_basis",
            message=f"{no_basis_count} 条 active claim 没有推理依据 (reasoning_basis 为空)",
            extra={"count": no_basis_count},
        ))


def _check_orphan_claims(db: Session, issues: list[LintIssue]):
    """检测没有关联来源的 claim"""
    linked_ids = set(
        row[0] for row in db.query(ClaimRawSourceLink.claim_id).distinct().all()
    )
    all_active = db.query(ClaimCard).filter(ClaimCard.status == "active").all()
    orphans = [c for c in all_active if c.id not in linked_ids]

    if orphans:
        issues.append(LintIssue(
            severity="info",
            category="orphan_claims",
            message=f"{len(orphans)} 条 active claim 没有关联原始来源",
            extra={"count": len(orphans), "sample_ids": [c.id for c in orphans[:5]]},
        ))


def _check_coverage_gaps(db: Session, issues: list[LintIssue]) -> dict:
    """分析资产和主题的覆盖度"""
    active_claims = db.query(ClaimCard).filter(ClaimCard.status == "active").all()

    # 资产覆盖统计
    asset_counter: Counter = Counter()
    theme_counter: Counter = Counter()
    type_counter: Counter = Counter()
    stance_counter: Counter = Counter()

    for c in active_claims:
        for a in (c.asset_scope or []):
            asset_counter[a.upper()] += 1
        for t in (c.theme_scope or []):
            theme_counter[t.lower()] += 1
        type_counter[c.claim_type] += 1
        stance_counter[c.stance] += 1

    # 找出覆盖不足的资产
    thin_assets = [
        (asset, cnt) for asset, cnt in asset_counter.items()
        if cnt < _MIN_CLAIMS_PER_ASSET
    ]
    if thin_assets:
        issues.append(LintIssue(
            severity="info",
            category="thin_coverage",
            message=f"{len(thin_assets)} 个资产覆盖不足（少于 {_MIN_CLAIMS_PER_ASSET} 条 claim）",
            extra={"assets": dict(thin_assets[:10])},
        ))

    # 单一立场偏向检测
    total = len(active_claims)
    if total > 10:
        for stance, cnt in stance_counter.items():
            ratio = cnt / total
            if ratio > 0.7:
                issues.append(LintIssue(
                    severity="warning",
                    category="stance_imbalance",
                    message=f"立场偏向: {stance} 占比 {ratio:.0%} ({cnt}/{total})，缺乏对立面视角",
                    extra={"stance": stance, "ratio": round(ratio, 2)},
                ))

    return {
        "total_active_claims": total,
        "unique_assets": len(asset_counter),
        "top_assets": asset_counter.most_common(10),
        "unique_themes": len(theme_counter),
        "top_themes": theme_counter.most_common(10),
        "by_type": dict(type_counter),
        "by_stance": dict(stance_counter),
    }


def _check_speaker_calibration(db: Session, now: datetime, issues: list[LintIssue]):
    """检测需要重新校准的 speaker"""
    threshold = now - timedelta(days=_SPEAKER_CALIBRATION_DAYS)
    speakers = db.query(SpeakerProfile).all()

    for sp in speakers:
        total = (sp.hit_count or 0) + (sp.miss_count or 0) + (sp.partial_hit_count or 0)
        # 有足够数据但长时间未校准
        last_calibrated_at = _as_aware_utc(sp.last_calibrated_at)
        if total >= 5 and (not last_calibrated_at or last_calibrated_at < threshold):
            days_since = (now - last_calibrated_at).days if last_calibrated_at else 999
            issues.append(LintIssue(
                severity="info",
                category="speaker_needs_calibration",
                message=f"Speaker '{sp.name}' 有 {total} 次预测记录但 {days_since} 天未校准",
                extra={
                    "speaker_id": sp.id,
                    "speaker_name": sp.name,
                    "hit_count": sp.hit_count,
                    "miss_count": sp.miss_count,
                    "trust_weight": sp.trust_weight,
                },
            ))


def _compute_quality_stats(db: Session, now: datetime) -> dict:
    """计算知识库整体质量指标"""
    active = db.query(ClaimCard).filter(ClaimCard.status == "active").all()
    if not active:
        return {"total": 0}

    confidences = [c.confidence for c in active if c.confidence is not None]
    trust_weights = [c.source_trust_weight for c in active if c.source_trust_weight is not None]

    # 有 embedding 的比例
    has_embedding = sum(1 for c in active if c.embedding is not None)

    # 来源数据质量
    sources_total = db.query(func.count(RawSource.id)).scalar() or 0
    sources_completed = db.query(func.count(RawSource.id)).filter(
        RawSource.status == "completed"
    ).scalar() or 0
    sources_failed = db.query(func.count(RawSource.id)).filter(
        RawSource.status == "failed"
    ).scalar() or 0

    return {
        "total_active": len(active),
        "avg_confidence": round(sum(confidences) / len(confidences), 3) if confidences else 0,
        "avg_trust_weight": round(sum(trust_weights) / len(trust_weights), 3) if trust_weights else 0,
        "embedding_coverage": round(has_embedding / len(active), 3) if active else 0,
        "sources_total": sources_total,
        "sources_completed": sources_completed,
        "sources_failed": sources_failed,
        "ingestion_success_rate": round(sources_completed / sources_total, 3) if sources_total else 0,
    }


def _compute_health_score(issues: list[LintIssue], quality_stats: dict) -> float:
    """综合健康分 0-100"""
    score = 100.0

    # 按问题严重度扣分
    for i in issues:
        if i.severity == "critical":
            score -= 5
        elif i.severity == "warning":
            score -= 2
        elif i.severity == "info":
            score -= 0.5

    # embedding 覆盖率加权
    emb_cov = quality_stats.get("embedding_coverage", 0)
    if emb_cov < 0.8:
        score -= (0.8 - emb_cov) * 20

    # 入库成功率
    success_rate = quality_stats.get("ingestion_success_rate", 1)
    if success_rate < 0.9:
        score -= (0.9 - success_rate) * 30

    return round(max(0, min(100, score)), 1)
