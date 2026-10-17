"""Research Memory MCP — 查询私域观点记忆库"""
from datetime import datetime, timezone
from typing import Optional, List, Any
from sqlalchemy.orm import Session
from app.mcps.base import MCPResponse
from app.services.claim_repository import ClaimRepository
from app.schemas.speaker_profile import SpeakerProfile
from app.utils.source_scope import clean_source_ids

SOURCE_PACKAGE = "research_memory_mcp"


# ── G19: query → 领域标签推断（轻量关键词匹配，零 LLM 调用）─────
# 用于 speaker_score.domain_match：让"擅长 macro 的分析师"在 macro 问题
# 中得分更高。这里的标签必须与 SpeakerProfile.applicable_domains / domain_tags
# 中常用的标签对齐（中英混用）。
_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "macro": ["宏观", "通胀", "通缩", "利率", "美联储", "Fed", "FOMC", "GDP",
              "macro", "recession", "货币政策", "monetary", "inflation",
              "deflation", "rate hike", "rate cut"],
    "crypto": ["BTC", "比特币", "Bitcoin", "ETH", "以太坊", "Ethereum", "SOL",
               "加密", "crypto", "区块链", "blockchain", "Web3"],
    "美股": ["美股", "SPX", "标普", "纳指", "纳斯达克", "Nasdaq", "Apple",
             "Tesla", "Nvidia", "S&P", "equity", "stock"],
    "大宗商品": ["黄金", "GOLD", "白银", "石油", "原油", "OIL", "WTI", "Brent",
                 "OPEC", "commodity", "贵金属"],
    "固定收益": ["债券", "国债", "yield", "treasury", "bond", "收益率"],
    "策略": ["策略", "交易策略", "趋势", "动量", "套利", "strategy"],
}


def _infer_query_domains(query: str, asset: Optional[str] = None) -> list[str]:
    """从 query + asset 提取领域标签（用于 speaker_score domain_match）。

    返回的是 SpeakerProfile.domain_tags / applicable_domains 风格的标签列表，
    例如 ["macro", "crypto"]。无命中返回 []，触发 speaker_score 跳过 domain_match。
    """
    if not query:
        query = ""
    text = (query or "").lower() + " " + (asset or "").lower()
    found: list[str] = []
    for domain, kws in _DOMAIN_KEYWORDS.items():
        for kw in kws:
            if kw.lower() in text:
                if domain not in found:
                    found.append(domain)
                break
    return found


def _as_aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _claim_is_decision_usable(claim: Any, now: datetime) -> bool:
    if getattr(claim, "is_contradicted", False):
        return False
    valid_until = _as_aware_utc(getattr(claim, "valid_until", None))
    if valid_until and valid_until < now:
        return False
    return True


class ResearchMemoryMCP:
    def __init__(self, db: Session):
        self.db = db
        self.claim_repo = ClaimRepository(db)

    def search_claims(
        self,
        *,
        q: Optional[str] = None,
        asset: Optional[str] = None,
        theme: Optional[str] = None,
        status: str = "active",
        speaker_id: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> MCPResponse[dict]:
        """搜索观点卡片"""
        try:
            items, total = self.claim_repo.search(
                q=q, asset=asset, theme=theme,
                status=status, speaker_id=speaker_id,
                skip=offset, limit=limit,
            )
            return MCPResponse.ok(
                data={"claims": items, "total": total, "has_more": (offset + limit) < total},
                source_package=SOURCE_PACKAGE,
            )
        except Exception as e:
            return MCPResponse.fail(str(e), SOURCE_PACKAGE)

    def get_claim(self, claim_id: str) -> MCPResponse[Any]:
        """获取单个观点卡片"""
        try:
            claim = self.claim_repo.get(claim_id)
            return MCPResponse.ok(data=claim, source_package=SOURCE_PACKAGE)
        except Exception as e:
            return MCPResponse.fail(str(e), SOURCE_PACKAGE)

    def get_active_claims_by_asset(
        self, asset: str, limit: int = 20
    ) -> MCPResponse[List[Any]]:
        """获取某资产的所有活跃观点"""
        try:
            items = self.claim_repo.get_active_by_asset(asset)
            return MCPResponse.ok(data=items[:limit], source_package=SOURCE_PACKAGE)
        except Exception as e:
            return MCPResponse.fail(str(e), SOURCE_PACKAGE)

    def get_expiring_claims(self, days: int = 3) -> MCPResponse[List[Any]]:
        """获取即将过期的观点"""
        try:
            items = self.claim_repo.get_expiring_soon(days=days)
            return MCPResponse.ok(data=items, source_package=SOURCE_PACKAGE)
        except Exception as e:
            return MCPResponse.fail(str(e), SOURCE_PACKAGE)

    def search_speakers(
        self,
        *,
        name: Optional[str] = None,
        domain: Optional[str] = None,
        limit: int = 20,
    ) -> MCPResponse[List[Any]]:
        """搜索来源画像"""
        try:
            query = self.db.query(SpeakerProfile).filter(
                SpeakerProfile.status == "active"
            )
            if name:
                query = query.filter(SpeakerProfile.name.ilike(f"%{name}%"))
            if domain:
                query = query.filter(
                    SpeakerProfile.domain_tags.contains([domain])
                )
            items = query.order_by(SpeakerProfile.trust_weight.desc()).limit(limit).all()
            return MCPResponse.ok(data=items, source_package=SOURCE_PACKAGE)
        except Exception as e:
            return MCPResponse.fail(str(e), SOURCE_PACKAGE)

    def get_speaker(self, speaker_id: str) -> MCPResponse[Any]:
        """获取单个来源画像"""
        try:
            speaker = self.db.query(SpeakerProfile).filter(
                SpeakerProfile.id == speaker_id
            ).first()
            return MCPResponse.ok(data=speaker, source_package=SOURCE_PACKAGE)
        except Exception as e:
            return MCPResponse.fail(str(e), SOURCE_PACKAGE)

    def query_for_briefing(
        self,
        query: str,
        asset: Optional[str] = None,
        limit: int = 12,
        source_ids: Optional[List[str]] = None,
    ) -> MCPResponse[List[dict]]:
        """为简报生成精选相关观点（混合召回 RAG 核心入口）。

        三路混合召回策略，确保私域信息被完整利用：
          路径 A：语义相似搜索（pgvector）或关键词搜索（退化）
                  → 抓住与问题语义最接近的观点
          路径 B：资产精确匹配 → 不漏掉该资产所有活跃观点
          路径 C：高 trust_weight 优先 → 确保高质量来源不被排挤

        三路合并去重后，按综合评分排序：
          score = semantic_rank_score * 0.5 + trust_weight * 0.3 + confidence * 0.2
        取前 limit 条传给 LLM，并附带数据覆盖摘要。
        """
        try:
            from app.services import semantic_search as sem_search
            from app.schemas.claim_card import ClaimCard, ClaimRawSourceLink

            selected_source_ids = clean_source_ids(source_ids)

            # Small explicit scopes are faster and safer to enumerate locally.
            # Large scopes retain semantic retrieval so relevance does not regress.
            source_results = []
            direct_selected_scope = False
            if selected_source_ids:
                from app.config import settings

                direct_limit = max(0, int(settings.SELECTED_SCOPE_DIRECT_CLAIM_LIMIT))
                source_pool_limit = max(limit * 3, 30, direct_limit + 1)
                source_results = (
                    self.db.query(ClaimCard)
                    .join(ClaimRawSourceLink, ClaimRawSourceLink.claim_id == ClaimCard.id)
                    .filter(
                        ClaimCard.status == "active",
                        ClaimRawSourceLink.source_id.in_(selected_source_ids),
                    )
                    .distinct()
                    .order_by(ClaimCard.confidence.desc(), ClaimCard.created_at.desc())
                    .limit(source_pool_limit)
                    .all()
                )
                direct_selected_scope = direct_limit > 0 and len(source_results) <= direct_limit

            # ── 路径 A：语义 / 关键词召回 ────────────────────────
            sem_results = [] if direct_selected_scope else sem_search.semantic_search(
                db=self.db,
                query=query,
                limit=max(limit * 3, 30),  # Fix #3: 扩大语义召回池提高 recall；配合 post_process 二阶段精排
                # 如果用户已经限定资料源，source scope 的优先级高于资产默认值；
                # 否则非资产问题会被默认 BTC 过滤污染。
                asset_filter=None if selected_source_ids else asset,
                status_filter="active",
                source_ids=selected_source_ids or None,
                exclude_expired=True,
                exclude_contradicted=True,
            )
            # 记录语义排名（越靠前得分越高）
            sem_ids = {c.id: (len(sem_results) - i) / max(len(sem_results), 1)
                       for i, c in enumerate(sem_results)}

            # ── 路径 B：资产精确匹配全量召回 ─────────────────────
            asset_results = []
            if asset and not selected_source_ids:
                asset_items = self.claim_repo.get_active_by_asset(asset)
                asset_results = asset_items[:20]  # 最多 20 条兜底

            # ── 路径 C：高 trust_weight 来源保底 ─────────────────
            trust_results = []
            try:
                if selected_source_ids:
                    trust_query = (
                        self.db.query(ClaimCard)
                        .join(ClaimRawSourceLink, ClaimRawSourceLink.claim_id == ClaimCard.id)
                        .filter(
                            ClaimCard.status == "active",
                            ClaimRawSourceLink.source_id.in_(selected_source_ids),
                        )
                        .order_by(ClaimCard.confidence.desc())
                        .limit(5)
                    )
                elif asset:
                    trust_query = self.db.query(ClaimCard).filter(
                        ClaimCard.status == "active",
                        ClaimCard.asset_scope.contains([asset.upper()])
                    ).order_by(ClaimCard.confidence.desc()).limit(5)
                else:
                    trust_query = self.db.query(ClaimCard).filter(
                        ClaimCard.status == "active"
                    ).order_by(ClaimCard.confidence.desc()).limit(5)
                trust_results = trust_query.all()
            except Exception:
                pass

            # ── 合并去重（按语义排序位置优先）──────────────────
            seen_ids: set = set()
            all_candidates = []
            now = datetime.now(timezone.utc)
            # 重要：排序 sem_results 在前，保证 idx=0 的 claim 在 post_processor
            # 里能拿到最高的 sem_rank 归一分
            for c in sem_results + source_results + asset_results + trust_results:
                if c.id not in seen_ids and _claim_is_decision_usable(c, now):
                    seen_ids.add(c.id)
                    all_candidates.append(c)

            # ── 获取来源可信度，批量（供 post_processor 回调使用）──
            # G19: 缓存整个 SpeakerProfile 对象（而非只 trust_weight），供多维评分使用
            speaker_cache: dict = {}
            for c in all_candidates:
                sid = getattr(c, "speaker_id", None)
                if sid and sid not in speaker_cache:
                    sp = self.db.query(SpeakerProfile).filter(
                        SpeakerProfile.id == sid
                    ).first()
                    speaker_cache[sid] = sp  # SpeakerProfile 或 None

            # G19: 从 query 推断 query_domains，供 speaker_score domain_match 使用
            query_domains = _infer_query_domains(query, asset)

            # ── B3: 调用统一后处理器（reranker + stance-balance + 融合打分）──
            # briefing 对 is_contradicted 降权 0.3（chat 不降权）
            from app.utils.claim_post_processor import post_process_candidates
            # G19: 用 speaker_score 多维融合评分代替原单一 trust_weight
            from app.services.speaker_score import make_speaker_score_fn
            _trust_from_speaker = make_speaker_score_fn(speaker_cache, query_domains=query_domains)

            result = post_process_candidates(
                candidates=all_candidates,
                query=query,
                top_k=limit,
                use_reranker=None,  # 读 settings.ENABLE_RERANKER
                use_stance_balance=True,
                trust_weight_fn=_trust_from_speaker,
                contradiction_penalty=-0.3,
                context="briefing",
            )
            top_claims = result.claims
            source_ids_by_claim: dict[str, list[str]] = {}
            top_claim_ids = [str(c.id) for c in top_claims if getattr(c, "id", None)]
            if top_claim_ids:
                for claim_id, source_id in (
                    self.db.query(
                        ClaimRawSourceLink.claim_id,
                        ClaimRawSourceLink.source_id,
                    )
                    .filter(ClaimRawSourceLink.claim_id.in_(top_claim_ids))
                    .all()
                ):
                    source_ids_by_claim.setdefault(str(claim_id), []).append(str(source_id))
            # 保留诊断信息以便上层记录（目前仅日志，不改 MCPResponse 契约）
            if result.balanced_applied:
                from loguru import logger
                logger.info(
                    f"[query_for_briefing] stance-balance applied; "
                    f"dist={result.stance_distribution}"
                )

            # ── 序列化 ────────────────────────────────
            # G19: 输出序列化时使用多维 speaker_score（而非原 trust_weight）
            from app.services.speaker_score import compute_speaker_score as _spk_score
            claims = []
            for c in top_claims:
                sid = getattr(c, "speaker_id", None)
                sp = speaker_cache.get(sid)
                tw = _spk_score(sp, query_domains=query_domains) if sp else 0.5
                claims.append({
                    "id": c.id,
                    "source_ids": list(dict.fromkeys(source_ids_by_claim.get(str(c.id), []))),
                    "summary": c.summary,
                    "full_quote": getattr(c, "full_quote", None),
                    "stance": c.stance,
                    "confidence": c.confidence,
                    "trust_weight": round(tw, 2),
                    "speaker_name": getattr(c, "speaker_name", None),
                    "time_horizon": c.time_horizon,
                    "asset_scope": c.asset_scope,
                    "theme_scope": c.theme_scope,
                    "is_contradicted": getattr(c, "is_contradicted", False),
                    "valid_until": c.valid_until.isoformat() if getattr(c, "valid_until", None) else None,
                    "created_at": c.created_at.isoformat() if c.created_at else None,
                    "_retrieval_paths": (
                        (["semantic"] if c.id in sem_ids else []) +
                        (["source_scope"] if c in source_results else []) +
                        (["asset_match"] if c in asset_results else []) +
                        (["trust_boost"] if c in trust_results else [])
                    ),
                })

            return MCPResponse.ok(data=claims, source_package=SOURCE_PACKAGE)
        except Exception as e:
            return MCPResponse.fail(str(e), SOURCE_PACKAGE)

    def get_data_coverage_summary(
        self,
        asset: Optional[str] = None,
        source_ids: Optional[List[str]] = None,
    ) -> dict:
        """返回私域数据覆盖摘要，告知 LLM 本地有哪些数据，避免幻觉。"""
        try:
            from app.schemas.claim_card import ClaimCard, ClaimRawSourceLink
            from sqlalchemy import func

            selected_source_ids = clean_source_ids(source_ids)
            q = self.db.query(ClaimCard).filter(ClaimCard.status == "active")
            if selected_source_ids:
                q = q.join(ClaimRawSourceLink, ClaimRawSourceLink.claim_id == ClaimCard.id).filter(
                    ClaimRawSourceLink.source_id.in_(selected_source_ids)
                )
            elif asset:
                q = q.filter(ClaimCard.asset_scope.contains([asset.upper()]))

            total = q.count()
            latest = q.order_by(ClaimCard.created_at.desc()).first()
            oldest = q.order_by(ClaimCard.created_at.asc()).first()

            # 按 stance 统计
            stance_counts = {}
            stance_query = self.db.query(ClaimCard.stance, func.count()).filter(ClaimCard.status == "active")
            if selected_source_ids:
                stance_query = stance_query.join(
                    ClaimRawSourceLink,
                    ClaimRawSourceLink.claim_id == ClaimCard.id,
                ).filter(ClaimRawSourceLink.source_id.in_(selected_source_ids))
            elif asset:
                stance_query = stance_query.filter(ClaimCard.asset_scope.contains([asset.upper()]))
            for row in stance_query.group_by(ClaimCard.stance).all():
                stance_counts[row[0]] = row[1]

            return {
                "total_active_claims": total,
                "asset_filter": asset,
                "source_ids": selected_source_ids,
                "date_range": {
                    "oldest": oldest.created_at.strftime("%Y-%m-%d") if oldest else None,
                    "newest": latest.created_at.strftime("%Y-%m-%d") if latest else None,
                },
                "stance_distribution": stance_counts,
            }
        except Exception:
            return {"total_active_claims": 0, "asset_filter": asset}
