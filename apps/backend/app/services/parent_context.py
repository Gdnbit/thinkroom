"""G15 · Parent-Child Retrieval.

命中一条 claim 后，通过 `ClaimRawSourceLink` 查同源（同 raw_source）的其他
claim，作为「context neighbors」附加到主命中之后，提升答案的逻辑链完整性。

设计要点
=========
1. 退化前提：`ClaimCard` 没有 `chunk_index` 字段，只能用 `created_at` 排序
   （摄入时按 chunk 顺序生成，所以同 source 内 created_at 单调递增）。

2. 邻居选择：以"已命中的 claim"的 created_at 为锚，**前后各取 N//2 条**，
   避免只往一个方向偏。这样能同时覆盖"前文铺垫"和"后续推论"两个方向。

3. 去重：
   - 已经命中的 claim 不再作为邻居加入
   - 同一 source 在多个命中里只附一次邻居（避免噪声）
   - 全局只展开 `PARENT_CONTEXT_MAX_SOURCES` 个 source

4. 顺序：邻居列表按 (source_id, created_at) 排序，让逻辑链顺序合理；
   主命中 → 邻居 在 claim 列表中作为"额外 bonus"放在尾部，
   token 预算 (`fit_blocks_to_budget`) 兜底自然丢弃超量。

5. 邻居 claim 上挂 `_parent_neighbor_of` 属性，渲染层可以加视觉标签
   （目前 chat_harness 渲染不区分，先把 claim 喂进去就行；下版本可加 ↪ 标记）。

不主动做的事（YAGNI）
====================
- 不为 briefing 路径接入（briefing 自有三路混合召回 + score 融合，先观察 chat 效果）
- 不改 ClaimCard schema（不加 chunk_index 列，避免 alembic drift）
- 不在邻居 claim 上跑 reranker（成本不值）
- 不去重 chunk 内"重复观点"（quality gate 已在摄入时把关）
"""
from __future__ import annotations

from typing import List, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app.schemas.claim_card import ClaimCard


def attach_parent_neighbors(
    db: "Session",
    hit_claims: List["ClaimCard"],
    *,
    neighbors_per_source: int = 2,
    max_sources: int = 3,
    status_filter: str = "active",
) -> List["ClaimCard"]:
    """给已命中的 claims 附加同源邻居，返回扩充后的 claim 列表。

    Args:
        db: SQLAlchemy session
        hit_claims: balanced_semantic_search / 三路混合召回出的 claim 列表
                    （按相关性已排序）
        neighbors_per_source: 每个被扩展的 source 附上 N 条邻居
        max_sources: 整次最多扩展 N 个 source（防止 context 撑爆）
        status_filter: 邻居 claim 也走相同 status 过滤（默认 'active'）

    Returns:
        扩充后的 claim 列表 = `hit_claims + 邻居 claims`。邻居 claim 上挂
        `_parent_neighbor_of`（被它扩展的 hit claim id），方便上游展示。
        若 hit_claims 为空、参数禁用、或没有可关联 source，返回原样不变。
    """
    if not hit_claims or neighbors_per_source <= 0 or max_sources <= 0:
        return list(hit_claims)

    # 延迟导入避免顶层循环
    from app.schemas.claim_card import ClaimCard, ClaimRawSourceLink

    hit_ids = {c.id for c in hit_claims}

    # ── 1) 收集每个 hit claim 关联的 source_id（一对多）─────
    # 注意：一个 claim 可能 link 到多个 source（罕见，但 schema 允许）；
    # 我们用第一个 source 作为该 claim 的"主源"。
    links = (
        db.query(ClaimRawSourceLink)
        .filter(ClaimRawSourceLink.claim_id.in_(hit_ids))
        .all()
    )
    if not links:
        logger.debug("[ParentContext] no source links for hit claims, skip")
        return list(hit_claims)

    # claim_id -> first source_id
    claim_to_source: dict = {}
    # source_id -> 第一个命中该 source 的 hit claim（用于 _parent_neighbor_of）
    source_to_first_hit: dict = {}
    for lk in links:
        if lk.claim_id not in claim_to_source:
            claim_to_source[lk.claim_id] = lk.source_id
        if lk.source_id not in source_to_first_hit:
            # 找到 hit_claims 中 id == lk.claim_id 的那条
            source_to_first_hit[lk.source_id] = lk.claim_id

    # 按 hit_claims 的原始顺序（即排名）取出涉及的 unique source（保留排名顺序）
    seen_sources: list = []
    for c in hit_claims:
        sid = claim_to_source.get(c.id)
        if sid and sid not in seen_sources:
            seen_sources.append(sid)
        if len(seen_sources) >= max_sources:
            break

    if not seen_sources:
        return list(hit_claims)

    # ── 2) 对每个 source，查同 source 的 claim（排除已命中），按 created_at 排
    # 锚点：该 source 内已命中的最早 created_at
    extra: List[ClaimCard] = []
    for src_id in seen_sources:
        # 同 source 的 claim 通过 link 找
        same_source_links = (
            db.query(ClaimRawSourceLink)
            .filter(
                ClaimRawSourceLink.source_id == src_id,
                ClaimRawSourceLink.claim_id.notin_(hit_ids),
            )
            .all()
        )
        candidate_ids = [lk.claim_id for lk in same_source_links]
        if not candidate_ids:
            continue

        cq = db.query(ClaimCard).filter(ClaimCard.id.in_(candidate_ids))
        if status_filter:
            cq = cq.filter(ClaimCard.status == status_filter)
        candidates = cq.order_by(ClaimCard.created_at.asc()).all()
        if not candidates:
            continue

        # 锚点：在 hit_claims 中找到属于该 source 的第一个 hit
        anchor_id = source_to_first_hit.get(src_id)
        anchor = next((h for h in hit_claims if h.id == anchor_id), None)
        anchor_ts = getattr(anchor, "created_at", None)

        # 按时间距锚点的"绝对差"排序，取最近的 N 条
        if anchor_ts is not None:
            try:
                ranked = sorted(
                    candidates,
                    key=lambda c: abs(
                        (c.created_at - anchor_ts).total_seconds()
                        if c.created_at else 9e18
                    ),
                )
            except Exception:
                # 比较失败 → 退化按 created_at 升序（取头部）
                ranked = candidates
        else:
            ranked = candidates

        picked = ranked[:neighbors_per_source]
        for nb in picked:
            # 在 claim 上挂临时属性（不持久化到 DB）
            try:
                setattr(nb, "_parent_neighbor_of", anchor_id)
            except Exception:
                pass
            extra.append(nb)

    if not extra:
        return list(hit_claims)

    # 邻居整体按 source_id + created_at 排序，让逻辑链阅读自然
    try:
        extra.sort(
            key=lambda c: (
                claim_to_source.get(c.id, "") or
                getattr(c, "_parent_neighbor_of", "") or "",
                c.created_at,
            )
        )
    except Exception:
        pass

    logger.info(
        f"[ParentContext] attached {len(extra)} neighbors "
        f"across {len(seen_sources)} source(s) (limit "
        f"{neighbors_per_source}/source × {max_sources} sources)"
    )
    return list(hit_claims) + extra


__all__ = ["attach_parent_neighbors"]
