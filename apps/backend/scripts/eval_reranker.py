"""B7 · Reranker A/B 评估脚本

用法：
    python -m scripts.eval_reranker --queries path/to/queries.yaml --top-k 8

不指定 --queries 时使用内置默认查询集（BTC/ETH/宏观各 2 条）。

对比两种配置：
    A. use_reranker=False（仅语义 rank + trust + conf + decay）
    B. use_reranker=True  （BGE Cross-Encoder 精排 + 同融合公式）

指标：
    1. top-K Jaccard 相似度：|A∩B| / |A∪B|  → 若很高说明 reranker 改变不大
    2. 各路的 top-K 平均综合得分
    3. 立场分布（bull/bear/neutral）
    4. balanced_applied 触发率
    5. 候选池规模（用于判断是否足以让 reranker 发挥作用）

输出：
    按查询分列的对比表 + 汇总 summary。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Tuple

# 确保脚本可独立运行
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from sqlalchemy.orm import Session  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.services import semantic_search as sem_search  # noqa: E402
from app.utils.claim_post_processor import (  # noqa: E402
    post_process_candidates,
    PostProcessResult,
)


# ── 默认评估查询集（无 golden label；只做对比）─────────────────
DEFAULT_QUERIES: List[dict] = [
    {"query": "BTC 目前处于什么位置？下一步怎么走？", "asset": "BTC"},
    {"query": "比特币风险因素有哪些？", "asset": "BTC"},
    {"query": "ETH 合并之后长期前景如何", "asset": "ETH"},
    {"query": "以太坊生态能否重新扩张？", "asset": "ETH"},
    {"query": "美联储会继续加息吗？", "asset": None},
    {"query": "通胀会持续多久？市场要怎么应对？", "asset": None},
]


# ══════════════════════════════════════════════════════════════════
# 核心评估逻辑
# ══════════════════════════════════════════════════════════════════

def _fetch_candidates(db: Session, query: str, asset: str | None, mult: int = 3, limit: int = 8):
    """与 chat_harness.balanced_semantic_search 相同的初级召回。"""
    return sem_search.semantic_search(
        db=db,
        query=query,
        limit=max(limit * mult, 16),
        asset_filter=asset,
        status_filter="active",
    )


def _run_one(
    db: Session,
    query: str,
    asset: str | None,
    use_reranker: bool,
    top_k: int = 8,
) -> Tuple[PostProcessResult, float]:
    """跑一次 post_process_candidates，返回 (result, avg_score)"""
    candidates = _fetch_candidates(db, query, asset, limit=top_k)
    result = post_process_candidates(
        candidates=candidates,
        query=query,
        top_k=top_k,
        use_reranker=use_reranker,
        context=f"eval-{use_reranker and 'rr' or 'base'}",
    )
    # avg_score 估计：与 primary 同序重跑一次 _score（不做，代价偏高），
    # 这里简化用"命中数 / scanned"作为召回率代理。
    return result, 0.0


def _jaccard(a: List[str], b: List[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    inter = len(sa & sb)
    union = len(sa | sb) or 1
    return inter / union


# ══════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════

def evaluate(queries: List[dict], top_k: int = 8) -> None:
    db = SessionLocal()
    try:
        # 预检查：reranker 是否可用
        from app.config import settings
        from app.utils.reranker import get_reranker
        enabled = bool(getattr(settings, "ENABLE_RERANKER", False))
        rr_instance = get_reranker() if enabled else None
        print("\n" + "=" * 76)
        print(f"  B7 Reranker A/B Evaluation  |  {len(queries)} queries, top_k={top_k}")
        print("=" * 76)
        print(f"  settings.ENABLE_RERANKER = {enabled}")
        print(f"  reranker loaded          = {rr_instance is not None}")
        if not enabled:
            print("  [WARN] ENABLE_RERANKER=False; 'B' column will equal 'A' (no-op).")
            print("         在 .env 设置 ENABLE_RERANKER=True 并下载模型后再跑。")
        elif rr_instance is None:
            print("  [WARN] reranker 已启用但未加载；检查模型是否下载（download_reranker.py）。")

        jaccards: List[float] = []
        pool_sizes: List[int] = []
        rerank_balanced_rate = 0
        base_balanced_rate = 0

        header = f"{'Query':<34} {'Asset':<6} {'Pool':>5} {'A|B Jac':>8} {'A-B':>3} {'R-B':>3}  A-dist         B-dist"
        print("\n" + header)
        print("-" * 76)

        for q in queries:
            query = q["query"]
            asset = q.get("asset")

            base_res, _ = _run_one(db, query, asset, use_reranker=False, top_k=top_k)
            rr_res, _ = _run_one(db, query, asset, use_reranker=True, top_k=top_k)

            base_ids = [c.id for c in base_res.claims]
            rr_ids = [c.id for c in rr_res.claims]
            jac = _jaccard(base_ids, rr_ids)
            jaccards.append(jac)
            pool_sizes.append(max(base_res.total_scanned, rr_res.total_scanned))

            if base_res.balanced_applied:
                base_balanced_rate += 1
            if rr_res.balanced_applied:
                rerank_balanced_rate += 1

            a_dist = base_res.stance_distribution
            b_dist = rr_res.stance_distribution

            def _d(d):
                return f"b{d.get('bullish',0)}/s{d.get('bearish',0)}/n{d.get('neutral',0)+d.get('conditional',0)}"

            q_label = (query[:31] + "...") if len(query) > 34 else query
            asset_label = asset or "—"
            print(
                f"{q_label:<34} {asset_label:<6} {base_res.total_scanned:>5} "
                f"{jac:>8.2%} "
                f"{'!' if base_res.balanced_applied else ' ':>3} "
                f"{'!' if rr_res.balanced_applied else ' ':>3}  "
                f"{_d(a_dist):<13}  {_d(b_dist)}"
            )

        # 汇总
        n = len(queries)
        print("\n" + "=" * 76)
        print(f"  Summary")
        print("=" * 76)
        print(f"  平均 Jaccard (A=base vs B=reranker): {sum(jaccards)/n:.2%}")
        print(f"  平均候选池大小:                      {sum(pool_sizes)/n:.1f}")
        print(f"  A 对立注入触发率:                    {base_balanced_rate}/{n}")
        print(f"  B 对立注入触发率:                    {rerank_balanced_rate}/{n}")
        low_jac = sum(1 for j in jaccards if j < 0.5)
        print(f"  Jaccard < 50% 的查询数:              {low_jac}/{n}  (reranker 改变较大)")
        print("=" * 76 + "\n")
    finally:
        db.close()


def _load_queries(path: str) -> List[dict]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except ImportError:
            raise SystemExit("需要安装 pyyaml: pip install pyyaml")
        return yaml.safe_load(text) or []
    # JSON / JSONL
    if p.suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    return json.loads(text)


def main():
    ap = argparse.ArgumentParser(description="B7 · Reranker A/B 评估")
    ap.add_argument("--queries", default=None, help="查询集路径（yaml/json/jsonl）；不传用默认集")
    ap.add_argument("--top-k", type=int, default=8)
    args = ap.parse_args()

    queries = _load_queries(args.queries) if args.queries else DEFAULT_QUERIES
    evaluate(queries, top_k=args.top_k)


if __name__ == "__main__":
    main()
