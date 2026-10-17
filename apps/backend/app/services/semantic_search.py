"""语义搜索服务 (G11)

职责：
1. 为新入库的 Claim 生成 embedding 并存储
2. 根据查询文本做语义相似搜索

搜索引擎降级链（自动选择最优可用方案）：
  ① pgvector (PostgreSQL)  — PGVECTOR_ENABLED=True 时
  ② sqlite-vec KNN 索引    — SQLite + pip install sqlite-vec 时
  ③ Python 暴力余弦相似度  — SQLite + JSON TEXT 列有 embedding 时
  ④ 关键词匹配 + 别名展开  — 无 embedding 时的最终退化
"""
import json as _json
import threading as _threading
from datetime import datetime, timezone
from typing import List, Optional, Any, Union
from loguru import logger
from sqlalchemy.orm import Session
from app.config import settings
from app.services.embedding import embed_text
from app.services.sqlite_vec_store import (
    get_store as _get_vec_store,
    cosine_similarity as _cosine_sim,
    _parse_json_embedding,
)
from app.utils.source_scope import clean_source_ids

# 上次搜索使用的引擎（供 API 层报告）
_last_search_tier: str = "keyword"

# ── sqlite-vec 延迟写入缓冲 ─────────────────────────────────────
# index_claim 在 SQLAlchemy 事务内部被调用，外层事务持有 RESERVED 写锁，
# 此时 vec_store（独立 sqlite3 连接）无法获取写锁 → 同线程死锁。
# 解决方案：将 upsert 缓冲到线程局部存储，调用方 db.commit() 之后再刷入。
_tls = _threading.local()


def _get_pending_vec() -> list:
    if not hasattr(_tls, 'pending_vec_upserts'):
        _tls.pending_vec_upserts = []
    return _tls.pending_vec_upserts


def flush_pending_vec_upserts():
    """将缓冲的 embedding 批量写入 sqlite-vec。应在 db.commit() 之后调用。"""
    pending = _get_pending_vec()
    if not pending:
        return
    vec_store = _get_vec_store()
    if vec_store:
        ok = 0
        for claim_id, vec in pending:
            if vec_store.upsert(claim_id, vec):
                ok += 1
        if ok:
            logger.debug(f"[SemanticSearch] flushed {ok}/{len(pending)} vec upserts")
    pending.clear()


def clear_pending_vec_upserts():
    """丢弃缓冲但未刷入 sqlite-vec 的 embedding（用于事务回滚场景）。

    当上游（如 ingestion_workflow）在失败路径上 expunge 掉脏 ClaimCard
    时，应配合调用本函数，避免把未落库的 claim_id 同步到向量索引，
    造成"索引里有但 DB 里没有"的不一致。
    """
    pending = _get_pending_vec()
    if pending:
        logger.debug(f"[SemanticSearch] dropped {len(pending)} pending vec upserts on rollback")
        pending.clear()


# ── 多语言别名展开表 ──────────────────────────────────────────
# 召回时将查询词展开为所有同义词，兼容中英文混用和历史数据
ALIAS_MAP: dict[str, list[str]] = {
    # 大宗商品
    "石油":   ["OIL", "原油", "crude", "WTI", "Brent", "petroleum"],
    "OIL":    ["石油", "原油", "crude", "WTI", "Brent", "petroleum"],
    "原油":   ["石油", "OIL", "crude", "WTI", "Brent"],
    "黄金":   ["GOLD", "Gold", "XAU", "贵金属"],
    "GOLD":   ["黄金", "Gold", "XAU", "贵金属"],
    "白银":   ["SILVER", "Silver", "XAG"],
    "SILVER": ["白银", "Silver", "XAG"],
    # 加密货币
    "比特币":  ["BTC", "Bitcoin", "bitcoin"],
    "BTC":    ["比特币", "Bitcoin", "bitcoin"],
    "bitcoin": ["BTC", "比特币", "Bitcoin"],
    "Bitcoin": ["BTC", "比特币", "bitcoin"],
    "以太坊":  ["ETH", "Ethereum", "ethereum"],
    "ETH":    ["以太坊", "Ethereum", "ethereum"],
    "SOL":    ["Solana", "solana", "索拉纳"],
    "BNB":    ["币安", "Binance"],
    "XRP":    ["瑞波", "Ripple", "ripple"],
    # 股指
    "SPX":    ["标普", "标普500", "S&P500", "S&P 500", "美股"],
    "NDX":    ["纳指", "纳斯达克", "Nasdaq", "nasdaq"],
    "DXY":    ["美元指数", "美元", "Dollar", "USD"],
    # 宏观
    "美联储":  ["Fed", "FED", "Federal Reserve", "FOMC"],
    "Fed":    ["美联储", "Federal Reserve", "FOMC"],
    "通胀":   ["inflation", "CPI", "PCE", "物价"],
    "inflation": ["通胀", "CPI", "PCE", "物价"],
    "流动性":  ["liquidity", "资金面"],
    "liquidity": ["流动性", "资金面"],
    "利率":   ["interest rate", "rate", "收益率", "yield"],
    "OPEC":   ["欧佩克", "欧佩克+", "OPEC+"],
}


def expand_aliases(term: str) -> list[str]:
    """将单个词展开为包含自身和所有别名的列表。"""
    aliases = ALIAS_MAP.get(term, [])
    result = [term]
    for a in aliases:
        if a not in result:
            result.append(a)
    return result


def expand_query_terms(query: str) -> list[str]:
    """从查询字符串中识别并展开所有已知别名词。"""
    expanded: list[str] = []
    for term in ALIAS_MAP:
        if term.lower() in query.lower() or term in query:
            for alias in expand_aliases(term):
                if alias not in expanded:
                    expanded.append(alias)
    return expanded if expanded else []


def build_embed_text(claim: Any) -> str:
    """G17 Contextual Embeddings: 拼接需要一起 embed 的文本。

    拼接顺序：contextual_text → summary → full_quote
    参考 Anthropic Contextual Retrieval (2024-09)：
    把 chunk 的“局部上下文”拼在原文前面后再 embed，
    可以明显提高检索召回准确率（实测失败率 −67%）。

    老数据无 contextual_text（NULL）时，退化为 summary+full_quote 的
    老路径，完全向后兼容。接受 ClaimCard ORM 对象、ExtractedClaim,
    或任何含同名属性的 dataclass / dict-like 。
    """
    parts: list[str] = []
    # 优先读 contextual_text（ClaimCard）；
    # 多一个 fallback：ExtractedClaim 用的是 contextual_summary
    ctx = (
        getattr(claim, "contextual_text", None)
        or getattr(claim, "contextual_summary", None)
    )
    if ctx:
        parts.append(str(ctx).strip())
    summary = getattr(claim, "summary", None)
    if summary:
        parts.append(str(summary).strip())
    fq = getattr(claim, "full_quote", None)
    if fq:
        parts.append(str(fq).strip())
    return "\n".join(p for p in parts if p)


def index_claim(db: Session, claim: Any) -> bool:
    """为 Claim 生成并存储 embedding。失败时静默跳过（使用 savepoint 保护）。

    PGVECTOR_ENABLED=True  → 直接写 vector 列（pgvector 搜索）
    PGVECTOR_ENABLED=False → JSON 序列化写 TEXT 列 + 同步写入 sqlite-vec vec0 索引
                              搜索时自动选择 sqlite-vec KNN → Python 余弦 → 关键词
    """
    if not settings.PGVECTOR_ENABLED:
        # TEXT 列模式：JSON 序列化存储
        try:
            if claim.embedding is not None:
                # 已有 embedding，缓冲到事务提交后再同步到 sqlite-vec
                parsed = _parse_json_embedding(claim.embedding)
                if parsed:
                    _get_pending_vec().append((claim.id, parsed))
                return True
            # G17: contextual_text + summary + full_quote，老数据退化为 summary+full_quote
            text = build_embed_text(claim)
            vec = embed_text(text)
            if vec is None:
                return False
            try:
                sp = db.begin_nested()
                claim.embedding = _json.dumps(vec)
                db.flush()
                sp.commit()
                # 缓冲到事务提交后再写入 sqlite-vec（避免同线程写锁死锁）
                _get_pending_vec().append((claim.id, vec))
                logger.debug(f"[SemanticSearch] Indexed claim {claim.id} (TEXT/JSON, dim={len(vec)})")
                return True
            except Exception as e:
                logger.warning(f"[SemanticSearch] index_claim TEXT failed: {e}")
                try:
                    sp.rollback()
                except Exception:
                    pass
                try:
                    claim.embedding = None
                    db.expire(claim)
                except Exception:
                    pass
                return False
        except Exception as e:
            logger.warning(f"[SemanticSearch] index_claim outer failed: {e}")
            return False

    # pgvector 模式
    try:
        from pgvector.sqlalchemy import Vector  # noqa
    except ImportError:
        return False

    try:
        if claim.embedding is not None:
            return True  # 已有 embedding，跳过

        # G17: contextual_text + summary + full_quote，老数据退化为 summary+full_quote
        text = build_embed_text(claim)

        vec = embed_text(text)
        if vec is None:
            return False

        # 使用 savepoint，失败时只回滚 embedding 更新，不影响外层事务
        try:
            sp = db.begin_nested()
            claim.embedding = vec
            db.flush()
            sp.commit()
            logger.debug(f"[SemanticSearch] Indexed claim {claim.id}")
            return True
        except Exception as e:
            logger.warning(f"[SemanticSearch] index_claim failed: {e}")
            try:
                sp.rollback()
            except Exception:
                pass
            # savepoint 回滚后，用 expire 让 ORM 丢弃内存中未持久化的 embedding，
            # 避免后续 flush 重试脏数据。不能用 expunge：expunge 会把对象从 session
            # 彻底移除，导致调用方后续访问该对象时抛出 "not bound to a Session" 错误。
            try:
                claim.embedding = None
                db.expire(claim)
            except Exception:
                pass
            return False
    except Exception as e:
        logger.warning(f"[SemanticSearch] index_claim outer failed: {e}")
        return False


def semantic_search(
    db: Session,
    query: str,
    limit: int = 10,
    asset_filter: "Optional[Union[str, List[str]]]" = None,
    status_filter: str = "active",
    source_ids: "Optional[List[str]]" = None,
    exclude_expired: bool = False,
    exclude_contradicted: bool = False,
    now: "Optional[datetime]" = None,
) -> List[Any]:
    """语义搜索 Claim。

    无 pgvector 或无 embedding 时退化为多语言关键词搜索（ilike + 别名展开）。

    asset_filter 可以是：
    - None：不做资产过滤
    - str（如 "BTC"）：单资产，展开别名后过滤
    - List[str]（如 ["BTC", "ETH"]）：多资产，合并所有别名用 OR 过滤
    """
    global _last_search_tier
    from app.schemas.claim_card import ClaimCard, ClaimRawSourceLink
    from sqlalchemy import cast, Text, or_

    _query_vec = None  # lazy-computed, shared across tiers

    def _get_query_vec():
        nonlocal _query_vec
        if _query_vec is None:
            _query_vec = embed_text(query)
        return _query_vec

    # P2-2: 规范化 asset_filter → 单一别名列表，所有 Tier 复用
    _asset_aliases: Optional[list] = None
    if asset_filter:
        _asset_aliases = []
        if isinstance(asset_filter, str):
            _raw_assets = [asset_filter]
        else:
            try:
                _raw_assets = list(asset_filter)
            except Exception:
                _raw_assets = [str(asset_filter)]
        for _a in _raw_assets:
            if not _a:
                continue
            for _alias in expand_aliases(str(_a).upper()):
                if _alias not in _asset_aliases:
                    _asset_aliases.append(_alias)
        if not _asset_aliases:
            _asset_aliases = None

    def _asset_filter_clause(aliases: list):
        """asset_scope 是 JSON/JSONB，用 cast→text ilike 匹配任一别名"""
        return or_(*[
            cast(ClaimCard.asset_scope, Text).ilike(f"%{a}%")
            for a in aliases
        ])

    _source_ids = clean_source_ids(source_ids)

    def _apply_source_scope(query_obj):
        if not _source_ids:
            return query_obj
        return query_obj.filter(
            ClaimCard.raw_source_links.any(ClaimRawSourceLink.source_id.in_(_source_ids))
        )

    _now = now or datetime.now(timezone.utc)

    def _apply_quality_filters(query_obj):
        if exclude_expired:
            query_obj = query_obj.filter(
                or_(ClaimCard.valid_from.is_(None), ClaimCard.valid_from <= _now)
            ).filter(
                or_(ClaimCard.valid_until.is_(None), ClaimCard.valid_until >= _now)
            )
        if exclude_contradicted:
            query_obj = query_obj.filter(ClaimCard.is_contradicted.is_(False))
        return query_obj

    # 尝试向量搜索（仅 PGVECTOR_ENABLED=True 时）
    if settings.PGVECTOR_ENABLED:
        try:
            from pgvector.sqlalchemy import Vector  # noqa
            query_vec = _get_query_vec()
            if query_vec is not None:
                q = db.query(ClaimCard).filter(ClaimCard.embedding.isnot(None))
                if status_filter:
                    q = q.filter(ClaimCard.status == status_filter)
                if _asset_aliases:
                    q = q.filter(_asset_filter_clause(_asset_aliases))
                q = _apply_source_scope(q)
                q = _apply_quality_filters(q)
                # 用 savepoint 隔离向量查询：若 pgvector SQL 执行失败（如列类型不兼容），
                # 只回滚此次查询，不破坏调用方的 session 事务状态
                sp = db.begin_nested()
                try:
                    results = (
                        q.order_by(ClaimCard.embedding.l2_distance(query_vec))
                        .limit(limit)
                        .all()
                    )
                    sp.commit()
                    logger.debug(f"[SemanticSearch] vector search: {len(results)} results")
                    _last_search_tier = "pgvector"
                    return results
                except Exception as e:
                    logger.debug(f"[SemanticSearch] vector query failed: {e}")
                    try:
                        sp.rollback()
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"[SemanticSearch] vector search unavailable: {e}")

    # ── Tier ②: sqlite-vec KNN 搜索 ────────────────────────────────
    vec_store = _get_vec_store()
    if vec_store and vec_store.count() > 0:
        query_vec = _get_query_vec()
        if query_vec is not None:
            # 搜索时多取一些，后续在 Python 层做 asset/status 过滤
            knn_results = vec_store.search(query_vec, limit=limit * 3)
            if knn_results:
                hit_ids = [cid for cid, _dist in knn_results]
                q = db.query(ClaimCard).filter(ClaimCard.id.in_(hit_ids))
                if status_filter:
                    q = q.filter(ClaimCard.status == status_filter)
                if _asset_aliases:
                    q = q.filter(_asset_filter_clause(_asset_aliases))
                q = _apply_source_scope(q)
                q = _apply_quality_filters(q)
                claims_by_id = {c.id: c for c in q.all()}
                # 按 KNN 距离排序
                results = [claims_by_id[cid] for cid, _ in knn_results if cid in claims_by_id]
                results = results[:limit]
                if results:
                    logger.debug(f"[SemanticSearch] sqlite-vec KNN: {len(results)} results")
                    _last_search_tier = "sqlite-vec"
                    return results

    # ── Tier ③: Python 暴力余弦相似度 ────────────────────────────
    if not settings.PGVECTOR_ENABLED:
        query_vec = _get_query_vec()
        if query_vec is not None:
            try:
                # 取出所有有 embedding 的 claim
                q = db.query(ClaimCard).filter(ClaimCard.embedding.isnot(None))
                if status_filter:
                    q = q.filter(ClaimCard.status == status_filter)
                if _asset_aliases:
                    q = q.filter(_asset_filter_clause(_asset_aliases))
                q = _apply_source_scope(q)
                q = _apply_quality_filters(q)
                candidates = q.all()
                if candidates:
                    scored = []
                    for c in candidates:
                        vec = _parse_json_embedding(c.embedding)
                        if vec:
                            sim = _cosine_sim(query_vec, vec)
                            scored.append((c, sim))
                    scored.sort(key=lambda x: x[1], reverse=True)
                    results = [c for c, sim in scored[:limit] if sim > 0.3]
                    if results:
                        logger.debug(
                            f"[SemanticSearch] brute-force cosine: {len(results)} results "
                            f"(top sim={scored[0][1]:.3f}, scanned {len(candidates)})"
                        )
                        _last_search_tier = "cosine"
                        return results
            except Exception as e:
                logger.debug(f"[SemanticSearch] brute-force search failed: {e}")

    # ── Tier ④: 多语言关键词搜索（最终退化） ─────────────────────
    logger.debug("[SemanticSearch] fallback to keyword search with alias expansion")
    q = db.query(ClaimCard)
    if status_filter:
        q = q.filter(ClaimCard.status == status_filter)
    if _asset_aliases:
        q = q.filter(_asset_filter_clause(_asset_aliases))
    q = _apply_source_scope(q)
    q = _apply_quality_filters(q)

    # 关键词匹配：原始查询词 + 别名展开
    import re
    all_terms: list[str] = []
    raw_words = [w for w in re.split(r"[\s\[\]()]+", query) if len(w) > 1]
    all_terms.extend(raw_words[:5])
    for alias in expand_query_terms(query):
        if alias not in all_terms:
            all_terms.append(alias)
    all_terms = all_terms[:10]

    if all_terms:
        conditions = [ClaimCard.summary.ilike(f"%{kw}%") for kw in all_terms]
        conditions += [ClaimCard.full_quote.ilike(f"%{kw}%") for kw in all_terms[:3]]
        q = q.filter(or_(*conditions))
    _last_search_tier = "keyword"
    return q.order_by(ClaimCard.created_at.desc()).limit(limit).all()


def get_last_search_tier() -> str:
    """返回上次 semantic_search 使用的搜索引擎层级。

    可能值: 'pgvector' | 'sqlite-vec' | 'cosine' | 'keyword'
    """
    return _last_search_tier
